"""Navigation route endpoints."""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Dict, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from audio_player import play_voice_text
from application.dtos.route_dtos import RoutePlanningRequest, VoiceCommandRequest
from application.route_planning_service import RoutePlanningService
from application.voice_interaction_service import VoiceInteractionService
from domain.model.obstacle import Obstacle
from domain.model.route import GeoPoint
from infrastructure.config import AppConfig
from infrastructure.in_memory_obstacle_repository import InMemoryObstacleRepository

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["navigation"])

_route_service: Optional[RoutePlanningService] = None
_voice_service: Optional[VoiceInteractionService] = None
_obstacle_repo: Optional[InMemoryObstacleRepository] = None


def get_route_service() -> RoutePlanningService:
    global _route_service
    if _route_service is None:
        _route_service = RoutePlanningService()
    return _route_service


def get_voice_service() -> VoiceInteractionService:
    global _voice_service
    if _voice_service is None:
        _voice_service = VoiceInteractionService()
    return _voice_service


def get_obstacle_repo() -> InMemoryObstacleRepository:
    global _obstacle_repo
    if _obstacle_repo is None:
        _obstacle_repo = InMemoryObstacleRepository()
    return _obstacle_repo


class PlanRouteBody(BaseModel):
    origin_lng: Optional[float] = Field(default=None, description="起点经度")
    origin_lat: Optional[float] = Field(default=None, description="起点纬度")
    destination_lng: Optional[float] = Field(default=None, description="终点经度")
    destination_lat: Optional[float] = Field(default=None, description="终点纬度")
    origin_text: str = Field(default="", description="起点名称")
    destination_text: str = Field(default="", description="终点名称")
    user_id: str = ""
    device_id: str = ""
    preferences: Dict = Field(default_factory=dict)


class VoiceCommandBody(BaseModel):
    text: str = Field(..., description="语音识别文本")
    device_id: str = ""
    user_id: str = ""


class ObstacleReportBody(BaseModel):
    device_id: str = ""
    lng: float = Field(..., description="障碍物经度")
    lat: float = Field(..., description="障碍物纬度")
    obstacle_type: str = Field(default="unknown", description="障碍物类型")
    severity: str = Field(default="medium", description="严重程度: low/medium/high")
    confidence: float = Field(default=0.8, ge=0, le=1)
    description: str = ""


@router.post("/navigation/plan")
def plan_route(body: PlanRouteBody):
    """Plan a route from either text places or raw coordinates.

    注意：这里使用同步 def 而非 async def——内部会调用高德 geocode / 路线规划
    等阻塞式网络 IO，若放在 async 路由里会卡死单 worker 的事件循环，
    导致 /api/vision/* 等其他接口一起超时。
    """
    try:
        route_service = get_route_service()
        route_service.obstacle_repo = get_obstacle_repo()

        request_context = {
            "origin_desc": "",
            "destination_desc": "",
            "response_text": "",
        }

        if body.origin_text.strip() or body.destination_text.strip():
            voice_service = get_voice_service()
            request_info = voice_service.create_navigation_request(
                origin_desc=body.origin_text,
                destination_desc=body.destination_text,
                user_id=body.user_id,
                device_id=body.device_id,
                preferences=body.preferences,
            )
            if not request_info.get("need_planning"):
                _announce_navigation_voice(request_info.get("response_text", ""))
                return {"success": True, "data": request_info}

            request_context.update(
                {
                    "origin_desc": request_info.get("origin_desc", ""),
                    "destination_desc": request_info.get("destination_desc", ""),
                    "response_text": request_info.get("response_text", ""),
                }
            )
            request = request_info["navigation_request"]
        else:
            missing = [
                name
                for name, value in {
                    "origin_lng": body.origin_lng,
                    "origin_lat": body.origin_lat,
                    "destination_lng": body.destination_lng,
                    "destination_lat": body.destination_lat,
                }.items()
                if value is None
            ]
            if missing:
                raise HTTPException(
                    status_code=400,
                    detail=(
                        "origin_lng, origin_lat, destination_lng and destination_lat "
                        "are required when no place names are provided"
                    ),
                )

            request = RoutePlanningRequest(
                origin_lng=body.origin_lng,
                origin_lat=body.origin_lat,
                destination_lng=body.destination_lng,
                destination_lat=body.destination_lat,
                user_id=body.user_id,
                device_id=body.device_id,
                preferences=body.preferences,
            )

        planning_result = route_service.plan_route(request)
        _announce_navigation_voice(planning_result.broadcast_text)

        if not request_context["response_text"]:
            request_context["response_text"] = planning_result.broadcast_text

        return {
            "success": True,
            "data": {
                **request_context,
                "planning_result": planning_result.to_dict(),
            },
        }
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except HTTPException:
        raise
    except Exception as exc:
        logger.error("route planning failed: %s", exc, exc_info=True)
        raise HTTPException(status_code=500, detail=f"路线规划失败: {exc}")


@router.post("/navigation/voice")
def process_voice_command(body: VoiceCommandBody):
    """Parse a navigation voice command and plan a route when needed.

    注意：同步 def 路由，内部含阻塞式大模型/高德调用，避免卡死事件循环。
    """
    try:
        request = VoiceCommandRequest(
            text=body.text,
            device_id=body.device_id,
            user_id=body.user_id,
        )
        service = get_voice_service()
        result = service.process_command(request)

        if result.get("need_planning") and result.get("navigation_request"):
            route_service = get_route_service()
            route_service.obstacle_repo = get_obstacle_repo()
            planning_result = route_service.plan_route(result["navigation_request"])
            result["planning_result"] = planning_result.to_dict()
            _announce_navigation_voice(planning_result.broadcast_text)
        else:
            _announce_navigation_voice(result.get("response_text", ""))

        result.pop("navigation_request", None)
        return {"success": True, "data": result}
    except Exception as exc:
        logger.error("voice command failed: %s", exc, exc_info=True)
        raise HTTPException(status_code=500, detail=f"语音命令处理失败: {exc}")


@router.post("/obstacle/report")
async def report_obstacle(body: ObstacleReportBody):
    """Report a detected obstacle to the shared repository."""
    try:
        obstacle = Obstacle(
            device_id=body.device_id,
            location=GeoPoint(body.lng, body.lat),
            obstacle_type=body.obstacle_type,
            severity=body.severity,
            confidence=body.confidence,
            description=body.description,
        )
        repo = get_obstacle_repo()
        repo.add(obstacle)
        return {
            "success": True,
            "data": {
                "obstacle_id": obstacle.obstacle_id,
                "total_reports": repo.get_report_count(),
                "hotspot_count": len(repo.get_hotspots()),
            },
        }
    except Exception as exc:
        logger.error("obstacle report failed: %s", exc, exc_info=True)
        raise HTTPException(status_code=500, detail=f"障碍物上报失败: {exc}")


@router.get("/obstacle/hotspots")
async def get_obstacle_hotspots(
    lng: Optional[float] = None,
    lat: Optional[float] = None,
    radius: float = 1000.0,
):
    """Return shared obstacle hotspots."""
    try:
        repo = get_obstacle_repo()
        center = GeoPoint(lng, lat) if lng is not None and lat is not None else None
        hotspots = repo.get_hotspots(center, radius)
        return {
            "success": True,
            "data": {
                "count": len(hotspots),
                "hotspots": [
                    {
                        "hotspot_id": hs.hotspot_id,
                        "location": {"lng": hs.location.lng, "lat": hs.location.lat},
                        "obstacle_type": hs.obstacle_type,
                        "severity": hs.severity,
                        "report_count": hs.report_count,
                        "avg_confidence": round(hs.avg_confidence, 2),
                        "aggregate_weight": round(hs.aggregate_weight, 2),
                        "last_seen": hs.last_seen.isoformat(),
                    }
                    for hs in hotspots
                ],
            },
        }
    except Exception as exc:
        logger.error("get obstacle hotspots failed: %s", exc, exc_info=True)
        raise HTTPException(status_code=500, detail=str(exc))


@router.get("/navigation/status")
async def navigation_status():
    """Return a minimal navigation worker status payload."""
    config = AppConfig.from_env()
    repo = get_obstacle_repo()
    return {
        "success": True,
        "data": {
            "status": "running",
            "amap_configured": bool(config.amap.api_key),
            "obstacle_reports": repo.get_report_count(),
            "obstacle_hotspots": len(repo.get_hotspots()),
            "timestamp": datetime.utcnow().isoformat(),
        },
    }


def _announce_navigation_voice(text: str) -> None:
    message = (text or "").strip()
    if not message:
        return
    try:
        play_voice_text(message)
    except Exception:
        logger.debug("navigation voice playback skipped", exc_info=True)
