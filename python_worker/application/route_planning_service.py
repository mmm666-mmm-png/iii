"""
应用服务 —— 路线规划编排服务

RoutePlanningService: 核心用例编排
1. 接收导航请求
2. 调用高德API获取多条备选路线
3. 加载静态盲道矢量数据
4. 获取实时障碍物热点
5. 调用打分引擎对路线重排序
6. 返回盲道友好度最高的路线 + 语音播报文本

这是DDD中的应用层，负责协调领域对象和基础设施，不包含业务规则。
"""
from __future__ import annotations

import logging
from typing import List, Optional
from urllib.parse import quote, urlencode

from domain.model.route import Route
from domain.model.navigation_request import NavigationRequest
from domain.model.tactile_paving import TactilePaving
from domain.model.obstacle import ObstacleHotspot
from domain.repositories.route_repository import IRouteRepository
from domain.repositories.tactile_paving_repository import ITactilePavingRepository
from domain.repositories.obstacle_repository import IObstacleRepository
from domain.services.route_scoring_service import RouteScoringService, ScoringConfig

from infrastructure.amap_client import AmapRouteClient
from infrastructure.geojson_tactile_repository import GeoJsonTactilePavingRepository
from infrastructure.in_memory_obstacle_repository import InMemoryObstacleRepository
from infrastructure.qwen_voice_client import QwenVoiceClient
from infrastructure.config import AppConfig

from .dtos.route_dtos import (
    RoutePlanningRequest,
    RoutePlanningResult,
    RouteSummary,
)

logger = logging.getLogger(__name__)


class RoutePlanningService:
    """
    路线规划应用服务。

    编排流程:
      请求 → 获取备选路线 → 加载盲道数据 → 获取障碍物热点 → 打分排序 → 生成播报 → 返回
    """

    def __init__(
        self,
        route_repo: Optional[IRouteRepository] = None,
        tactile_repo: Optional[ITactilePavingRepository] = None,
        obstacle_repo: Optional[IObstacleRepository] = None,
        scoring_service: Optional[RouteScoringService] = None,
        voice_client: Optional[QwenVoiceClient] = None,
        config: Optional[AppConfig] = None,
    ):
        self.config = config or AppConfig.from_env()
        self.route_repo = route_repo or AmapRouteClient(self.config.amap)
        self.tactile_repo = tactile_repo or GeoJsonTactilePavingRepository(self.config.tactile)
        self.obstacle_repo = obstacle_repo or InMemoryObstacleRepository()
        self.scoring_service = scoring_service or RouteScoringService()
        self.voice_client = voice_client or QwenVoiceClient(self.config.qwen)

    def plan_route(self, request: RoutePlanningRequest) -> RoutePlanningResult:
        """
        执行完整的路线规划流程。

        Args:
          request: 路线规划请求

        Returns:
          路线规划结果（含最优路线、所有备选、播报文本）
        """
        domain_request = request.to_domain()

        if not domain_request.is_valid():
            raise ValueError("无效的导航请求：起终点不能相同且不能为零坐标")

        logger.info(f"开始路线规划: {domain_request.request_id}")

        # 1. 获取多条备选路线
        routes = self._fetch_routes(domain_request)
        logger.info(f"获取到 {len(routes)} 条备选路线")

        # 2. 加载静态盲道数据
        tactile_pavings = self._load_tactile_pavings(domain_request)
        logger.info(f"加载 {len(tactile_pavings)} 条盲道数据")

        # 3. 获取实时障碍物热点
        obstacle_hotspots = self._get_obstacle_hotspots(domain_request)
        logger.info(f"获取 {len(obstacle_hotspots)} 个障碍物热点")

        # 4. 打分重排序
        ranked_routes = self.scoring_service.rank_routes(
            routes, tactile_pavings, obstacle_hotspots
        )
        best_route = ranked_routes[0]
        used_amap = any(str(route.source).startswith("amap") for route in ranked_routes)
        logger.info(
            f"最优路线: {best_route.name}, 得分={best_route.score}, "
            f"盲道覆盖={best_route.blind_path_coverage:.1%}, "
            f"障碍密度={best_route.obstacle_density:.1f}/km"
        )

        # 5. 生成语音播报
        broadcast_text = self.voice_client.generate_route_broadcast(
            best_route, ranked_routes, domain_request
        )
        broadcast_text_en = self.voice_client.generate_route_broadcast_en(
            best_route,
            ranked_routes,
            domain_request,
            request.origin_name,
            request.destination_name,
        )

        # 6. 组装结果
        return RoutePlanningResult(
            request_id=domain_request.request_id,
            best_route=RouteSummary.from_route(best_route),
            all_routes=[RouteSummary.from_route(r) for r in ranked_routes],
            broadcast_text=broadcast_text,
            broadcast_text_en=broadcast_text_en,
            score_breakdown=best_route.score_breakdown_dict(),
            used_amap=used_amap,
            used_blind_path_data=len(tactile_pavings) > 0,
            used_obstacle_data=len(obstacle_hotspots) > 0,
            obstacle_hotspot_count=len(obstacle_hotspots),
            origin_name=request.origin_name,
            destination_name=request.destination_name,
            navigation_uri=self._build_navigation_uri(
                domain_request,
                request.origin_name,
                request.destination_name,
            ),
        )

    def get_route_detail(self, route_id: str, request: RoutePlanningRequest) -> Optional[dict]:
        """获取指定路线的详细信息（含路段和打分明细）。"""
        domain_request = request.to_domain()
        routes = self._fetch_routes(domain_request)
        for route in routes:
            if route.route_id == route_id:
                tactile = self._load_tactile_pavings(domain_request)
                hotspots = self._get_obstacle_hotspots(domain_request)
                self.scoring_service.score_route(route, tactile, hotspots)
                return self._route_to_detail_dict(route)
        return None

    def _fetch_routes(self, request: NavigationRequest) -> List[Route]:
        """获取备选路线，带异常处理。"""
        try:
            routes = self.route_repo.fetch_routes(request)
            if routes:
                return routes
        except Exception as e:
            logger.error(f"获取路线失败: {e}")

        # 回退到Mock
        from infrastructure.amap_client import AmapRouteClient
        mock_client = AmapRouteClient()
        return mock_client._mock_routes(request)

    def _load_tactile_pavings(self, request: NavigationRequest) -> List[TactilePaving]:
        """加载路线附近的盲道数据。"""
        try:
            # 计算路线大致边界
            all_points = [request.origin, request.destination]
            min_lng = min(p.lng for p in all_points) - 0.01
            max_lng = max(p.lng for p in all_points) + 0.01
            min_lat = min(p.lat for p in all_points) - 0.01
            max_lat = max(p.lat for p in all_points) + 0.01
            return self.tactile_repo.get_in_bbox(min_lng, min_lat, max_lng, max_lat)
        except Exception as e:
            logger.error(f"加载盲道数据失败: {e}")
            return []

    def _get_obstacle_hotspots(self, request: NavigationRequest) -> List[ObstacleHotspot]:
        """获取路线附近的实时障碍物热点。"""
        try:
            # 以起点为中心搜索较大范围
            center = request.origin
            radius = max(
                500.0,
                request.origin.distance_to(request.destination) * 1.5
            )
            return self.obstacle_repo.get_hotspots(center, radius)
        except Exception as e:
            logger.error(f"获取障碍物热点失败: {e}")
            return []

    def _build_navigation_uri(
        self,
        request: NavigationRequest,
        origin_name: str = "",
        destination_name: str = "",
    ) -> str:
        """Build an Amap URI that can be opened on mobile or web."""
        def format_point(point, name: str = "") -> str:
            value = f"{point.lng:.6f},{point.lat:.6f}"
            if name.strip():
                return f"{value},{name.strip()}"
            return value

        params = {
            "from": format_point(request.origin, origin_name),
            "to": format_point(request.destination, destination_name),
            "mode": "walk",
            "policy": "0",
            "src": "ai-glass",
            "coordinate": "gaode",
            "callnative": "1",
        }
        return f"https://uri.amap.com/navigation?{urlencode(params, quote_via=quote, safe=',')}"

    def _route_to_detail_dict(self, route: Route) -> dict:
        """将路线转换为详细字典。"""
        return {
            "route_id": route.route_id,
            "name": route.name,
            "source": route.source,
            "total_distance_meters": round(route.total_distance_meters, 1),
            "total_duration_seconds": route.total_duration_seconds,
            "turn_count": route.turn_count,
            "traffic_light_count": route.traffic_light_count,
            "score": route.score_breakdown_dict(),
            "segments": [
                {
                    "start": {"lng": s.start.lng, "lat": s.start.lat},
                    "end": {"lng": s.end.lng, "lat": s.end.lat},
                    "instruction": s.instruction,
                    "road_type": s.road_type,
                    "distance_meters": round(s.distance_meters, 1),
                    "duration_seconds": s.duration_seconds,
                }
                for s in route.segments
            ],
            "polyline": [
                {"lng": p.lng, "lat": p.lat}
                for p in route.get_all_points()
            ],
        }
