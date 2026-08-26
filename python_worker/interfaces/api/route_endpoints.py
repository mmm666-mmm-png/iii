"""Navigation route endpoints."""

from __future__ import annotations

import base64
import logging
import threading
from datetime import datetime
from typing import Dict, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from audio_player import play_pcm_bytes, play_voice_text
from application.dtos.route_dtos import RoutePlanningRequest, VoiceCommandRequest
from application.route_broadcast_service import RouteBroadcastService
from application.route_planning_service import RoutePlanningService
from application.voice_interaction_service import VoiceInteractionService
from application.gps_navigation_trigger import get_gps_trigger
from domain.model.obstacle import Obstacle
from domain.model.route import GeoPoint
from infrastructure.config import AppConfig
from infrastructure.in_memory_obstacle_repository import InMemoryObstacleRepository
from infrastructure.tts_client import TtsClient

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["navigation"])

_route_service: Optional[RoutePlanningService] = None
_voice_service: Optional[VoiceInteractionService] = None
_obstacle_repo: Optional[InMemoryObstacleRepository] = None
_broadcast_service: Optional[RouteBroadcastService] = None


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


def get_broadcast_service() -> RouteBroadcastService:
    global _broadcast_service
    if _broadcast_service is None:
        _broadcast_service = RouteBroadcastService()
    return _broadcast_service


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
    lng: Optional[float] = Field(default=None, description="障碍物经度（眼镜无 GPS 时可缺省）")
    lat: Optional[float] = Field(default=None, description="障碍物纬度（眼镜无 GPS 时可缺省）")
    obstacle_type: str = Field(default="unknown", description="障碍物类型")
    severity: str = Field(default="medium", description="严重程度: low/medium/high")
    confidence: float = Field(default=0.8, ge=0, le=1)
    description: str = ""


class GpsUpdateBody(BaseModel):
    lat: float = Field(..., description="纬度 (WGS-84)")
    lng: float = Field(..., description="经度 (WGS-84)")
    accuracy: Optional[float] = Field(default=None, description="定位精度（米）")


def _build_plan_request(body: PlanRouteBody):
    """把 PlanRouteBody 解析成 RoutePlanningRequest。

    返回 (request, request_context)；request 为 None 表示无需规划（例如缺
    少目的地），此时 request_context 里会带 response_text。
    """
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
            return None, request_info

        request_context.update(
            {
                "origin_desc": request_info.get("origin_desc", ""),
                "destination_desc": request_info.get("destination_desc", ""),
                "response_text": request_info.get("response_text", ""),
            }
        )
        return request_info["navigation_request"], request_context

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
    return request, request_context


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

        request, request_context = _build_plan_request(body)
        if request is None:
            return {"success": True, "data": request_context}

        planning_result = route_service.plan_route(request)
        get_gps_trigger().set_route(
            planning_result.segments,
            blind_path_coverage=planning_result.best_route.blind_path_coverage,
        )

        if not request_context["response_text"]:
            request_context["response_text"] = planning_result.broadcast_text

        return {
            "success": True,
            "data": {
                **request_context,
                **_navigation_audio_payload(planning_result.broadcast_text),
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
            result.update(_navigation_audio_payload(planning_result.broadcast_text))
            get_gps_trigger().set_route(
                planning_result.segments,
                blind_path_coverage=planning_result.best_route.blind_path_coverage,
            )
        result.pop("navigation_request", None)
        return {"success": True, "data": result}
    except Exception as exc:
        logger.error("voice command failed: %s", exc, exc_info=True)
        raise HTTPException(status_code=500, detail=f"语音命令处理失败: {exc}")


@router.post("/navigation/broadcast")
def broadcast_route(body: PlanRouteBody):
    """Plan a route and broadcast its Amap steps one by one via TTS.

    规划流程阻塞执行，但语音合成与逐段播报放到后台线程，避免长时间占用
    事件循环；响应会立即返回逐段步骤文本，供前端展示。
    """
    try:
        route_service = get_route_service()
        route_service.obstacle_repo = get_obstacle_repo()

        request, request_context = _build_plan_request(body)
        if request is None:
            return {"success": True, "data": request_context}

        planning_result = route_service.plan_route(request)
        get_gps_trigger().set_route(
            planning_result.segments,
            blind_path_coverage=planning_result.best_route.blind_path_coverage,
        )

        steps = planning_result.turn_by_turn

        if not request_context["response_text"]:
            request_context["response_text"] = planning_result.broadcast_text

        return {
            "success": True,
            "data": {
                **request_context,
                **_navigation_audio_payload(planning_result.broadcast_text),
                "turn_by_turn": steps,
                "route_guide_text": planning_result.route_guide_text,
                "planning_result": planning_result.to_dict(),
            },
        }
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except HTTPException:
        raise
    except Exception as exc:
        logger.error("route broadcast failed: %s", exc, exc_info=True)
        raise HTTPException(status_code=500, detail=f"路线播报失败: {exc}")


@router.post("/obstacle/report")
async def report_obstacle(body: ObstacleReportBody):
    """Report a detected obstacle to the shared repository."""
    try:
        location = (
            GeoPoint(body.lng, body.lat)
            if body.lng is not None and body.lat is not None
            else None
        )
        obstacle = Obstacle(
            device_id=body.device_id,
            location=location,
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


@router.post("/gps/update")
def gps_update(body: GpsUpdateBody):
    """接收手机实时 GPS 坐标（WGS-84），匹配当前导航路线并触发逐段播报。

    Go 后端把手机浏览器上报的 GPS 转发到此端点；内部先把 WGS-84 转成 GCJ-02
    再与路线路段匹配，进入新路段即触发该段 instruction 的 TTS 播报。

    始终返回 200 + success 字段，避免偶发异常导致手机/Go 转发端断开。
    """
    try:
        result = get_gps_trigger().update_position(
            lng=body.lng, lat=body.lat, accuracy=body.accuracy
        )
        return {"success": True, "data": result}
    except Exception as exc:
        logger.error("gps update failed: %s", exc, exc_info=True)
        return {"success": False, "data": {"error": str(exc)}}


def _announce_navigation_voice(text: str) -> None:
    message = (text or "").strip()
    if not message:
        return

    def _speak() -> None:
        # 优先用 TTS 合成完整路线信息（路线状况/盲道覆盖情况/总距离/预计时间等），
        # 避免静态 wav 兜底时丢失路线详情。
        try:
            pcm = TtsClient().synthesize_pcm16_8k(message)
            if pcm:
                play_pcm_bytes(pcm)
                return
        except Exception:
            logger.debug("navigation voice TTS failed, fallback to static wav", exc_info=True)

        # TTS 不可用（无 API key 或网络失败）时，回退到静态 wav 匹配。
        try:
            play_voice_text(message)
        except Exception:
            logger.debug("navigation voice playback skipped", exc_info=True)

    # 后台线程合成，避免阻塞规划接口响应；播放走音频队列，不阻塞线程。
    try:
        threading.Thread(target=_speak, daemon=True).start()
    except Exception:
        logger.debug("navigation voice thread start failed", exc_info=True)


def _navigation_audio_payload(text: str) -> dict:
    """把完整路线摘要合成为设备端可播放的 PCM 音频。"""
    message = (text or "").strip()
    if not message:
        return {}
    try:
        pcm = TtsClient().synthesize_pcm16_8k(message)
        if pcm:
            return {
                "audio_base64": base64.b64encode(pcm).decode("ascii"),
                "audio_sample_rate": 8000,
                "audio_channels": 1,
                "audio_bits_per_sample": 16,
            }
    except Exception:
        logger.debug("navigation response TTS failed", exc_info=True)
    return {}
