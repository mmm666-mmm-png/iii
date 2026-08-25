"""
应用层 DTO —— 路线规划相关数据传输对象
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional

from domain.model.route import Route
from domain.model.navigation_request import NavigationRequest


@dataclass
class RoutePlanningRequest:
    """路线规划请求DTO。"""
    origin_lng: float
    origin_lat: float
    destination_lng: float
    destination_lat: float
    origin_name: str = ""
    destination_name: str = ""
    user_id: str = ""
    device_id: str = ""
    preferences: Dict = field(default_factory=dict)

    def to_domain(self) -> NavigationRequest:
        from domain.model.route import GeoPoint
        return NavigationRequest(
            origin=GeoPoint(self.origin_lng, self.origin_lat),
            destination=GeoPoint(self.destination_lng, self.destination_lat),
            user_id=self.user_id,
            device_id=self.device_id,
            preferences=self.preferences,
        )


@dataclass
class RouteSummary:
    """路线摘要DTO（用于列表展示）。"""
    route_id: str
    name: str
    source: str
    total_distance_meters: float
    total_duration_seconds: int
    score: float
    blind_path_coverage: float
    obstacle_density: float
    turn_count: int
    traffic_light_count: int

    @classmethod
    def from_route(cls, route: Route) -> "RouteSummary":
        return cls(
            route_id=route.route_id,
            name=route.name,
            source=route.source,
            total_distance_meters=route.total_distance_meters,
            total_duration_seconds=route.total_duration_seconds,
            score=route.score,
            blind_path_coverage=route.blind_path_coverage,
            obstacle_density=route.obstacle_density,
            turn_count=route.turn_count,
            traffic_light_count=route.traffic_light_count,
        )

    def to_dict(self) -> dict:
        return {
            "route_id": self.route_id,
            "name": self.name,
            "source": self.source,
            "total_distance_meters": round(self.total_distance_meters, 1),
            "total_duration_seconds": self.total_duration_seconds,
            "total_duration_minutes": self.total_duration_seconds // 60,
            "score": self.score,
            "blind_path_coverage": round(self.blind_path_coverage, 4),
            "blind_path_coverage_pct": round(self.blind_path_coverage * 100, 1),
            "obstacle_density_per_km": round(self.obstacle_density, 3),
            "turn_count": self.turn_count,
            "traffic_light_count": self.traffic_light_count,
        }


@dataclass
class RoutePlanningResult:
    """路线规划结果DTO。"""
    request_id: str
    best_route: RouteSummary
    all_routes: List[RouteSummary]
    broadcast_text: str
    score_breakdown: Dict
    used_amap: bool
    used_blind_path_data: bool
    used_obstacle_data: bool
    obstacle_hotspot_count: int
    origin_name: str = ""
    destination_name: str = ""
    navigation_uri: str = ""
    broadcast_text_en: str = ""
    turn_by_turn: List[Dict] = field(default_factory=list)
    route_guide_text: str = ""

    def to_dict(self) -> dict:
        return {
            "request_id": self.request_id,
            "best_route": self.best_route.to_dict(),
            "all_routes": [r.to_dict() for r in self.all_routes],
            "broadcast_text": self.broadcast_text,
            "score_breakdown": self.score_breakdown,
            "data_sources": {
                "amap": self.used_amap,
                "blind_path": self.used_blind_path_data,
                "obstacle_realtime": self.used_obstacle_data,
            },
            "obstacle_hotspot_count": self.obstacle_hotspot_count,
            "origin_name": self.origin_name,
            "destination_name": self.destination_name,
            "navigation": {
                "uri": self.navigation_uri,
                "mode": "walk",
                "origin_name": self.origin_name,
                "destination_name": self.destination_name,
            },
            "broadcast_text_en": self.broadcast_text_en,
            "turn_by_turn": self.turn_by_turn,
            "route_guide_text": self.route_guide_text,
        }


@dataclass
class ObstacleReportRequest:
    """障碍物上报DTO。"""
    device_id: str
    lng: float
    lat: float
    obstacle_type: str
    severity: str = "medium"
    confidence: float = 0.8
    description: str = ""


@dataclass
class VoiceCommandRequest:
    """语音命令DTO。"""
    text: str
    device_id: str = ""
    user_id: str = ""
