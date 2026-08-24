"""
基础设施 —— 高德地图API客户端

实现IRouteRepository接口，调用高德步行路线规划API获取多条备选路线。
API文档: https://lbs.amap.com/api/webservice/guide/api/direction/#walking
"""
from __future__ import annotations

import logging
import json
from typing import List, Optional
from urllib.parse import urlencode
from urllib.request import urlopen, Request
from urllib.error import URLError, HTTPError

from domain.model.route import Route, RouteSegment, GeoPoint, RoadType
from domain.model.navigation_request import NavigationRequest
from domain.repositories.route_repository import IRouteRepository
from .config import AmapConfig

logger = logging.getLogger(__name__)


class AmapRouteClient(IRouteRepository):
    """高德地图步行路线API客户端。"""

    def __init__(self, config: Optional[AmapConfig] = None):
        self.config = config or AmapConfig.from_env()

    def fetch_routes(self, request: NavigationRequest) -> List[Route]:
        """
        调用高德步行路线API获取备选路线。

        高德步行路线API默认返回1条路线，通过多次调用不同参数或使用alternative
        参数可获取多条。这里使用标准接口，若API不支持多路线则生成变体。
        """
        if not self.config.api_key:
            logger.warning("AMAP_API_KEY未配置，返回Mock路线")
            return self._mock_routes(request)

        try:
            return self._call_api(request)
        except Exception as e:
            logger.error(f"高德API调用失败: {e}，回退到Mock路线")
            return self._mock_routes(request)

    def _call_api(self, request: NavigationRequest) -> List[Route]:
        """实际调用高德API。"""
        origin = f"{request.origin.lng},{request.origin.lat}"
        destination = f"{request.destination.lng},{request.destination.lat}"

        params = {
            "key": self.config.api_key,
            "origin": origin,
            "destination": destination,
            "output": "json",
        }

        url = f"{self.config.walking_path_url}?{urlencode(params)}"
        req = Request(url, headers={"User-Agent": "AIGlass-Navigation/1.0"})

        with urlopen(req, timeout=self.config.timeout_seconds) as resp:
            data = json.loads(resp.read().decode("utf-8"))

        if data.get("status") != "1":
            raise ValueError(f"高德API返回错误: {data.get('info', 'unknown')}")

        route_data = data.get("route", {})
        paths = route_data.get("paths", [])

        routes = []
        for idx, path in enumerate(paths):
            route = self._parse_path(path, idx, request)
            routes.append(route)

        # 如果只返回1条，基于距离生成变体
        if len(routes) == 1:
            routes.extend(self._generate_variants(routes[0], request))

        return routes if routes else self._mock_routes(request)

    def _parse_path(self, path: dict, index: int, request: NavigationRequest) -> Route:
        """解析高德API返回的单条路线。"""
        route = Route(
            name=f"路线{index + 1}",
            source="amap",
            total_distance_meters=float(path.get("distance", 0)),
            total_duration_seconds=int(path.get("duration", 0)),
        )

        steps = path.get("steps", [])
        seen_points: List[GeoPoint] = []
        for step in steps:
            instruction = step.get("instruction", "")
            distance = float(step.get("distance", 0))
            duration = int(step.get("duration", 0))
            road_type = self._infer_road_type(instruction)

            # 步行接口的 step 没有 start_location/end_location，只有 polyline 折线。
            points = self._parse_polyline(step.get("polyline", ""))
            if len(points) < 2:
                # 兜底：兼容 driving 接口的 start_location/end_location 字段。
                start_pt = self._parse_location(step.get("start_location", ""))
                end_pt = self._parse_location(step.get("end_location", ""))
                if start_pt and end_pt:
                    points = [start_pt, end_pt]
                else:
                    continue

            seg = RouteSegment(
                start=points[0],
                end=points[-1],
                instruction=instruction,
                road_type=road_type,
                distance_meters=distance,
                duration_seconds=duration,
            )
            route.segments.append(seg)

            for pt in points:
                if not seen_points or seen_points[-1] != pt:
                    route.polyline.append(pt)
                    seen_points.append(pt)

        route.compute_total_distance()
        route.count_turns()
        return route

    def _parse_polyline(self, polyline_str: str) -> List[GeoPoint]:
        """解析高德折线字符串 'lng,lat;lng,lat;...'。"""
        points: List[GeoPoint] = []
        if not polyline_str:
            return points
        for part in polyline_str.split(";"):
            pt = self._parse_location(part)
            if pt:
                points.append(pt)
        return points

    def _parse_location(self, loc_str: str) -> Optional[GeoPoint]:
        """解析高德坐标字符串 'lng,lat'。"""
        if not loc_str or "," not in loc_str:
            return None
        try:
            lng, lat = loc_str.split(",")
            return GeoPoint(float(lng), float(lat))
        except (ValueError, IndexError):
            return None

    def _infer_road_type(self, instruction: str) -> str:
        """根据导航指令推断道路类型。"""
        text = instruction or ""
        if "盲道" in text:
            return RoadType.BLIND_PATH
        if "人行横道" in text or "斑马线" in text or "过马路" in text:
            return RoadType.CROSSWALK
        if "人行道" in text:
            return RoadType.SIDEWALK
        if "步行街" in text:
            return RoadType.PEDESTRIAN
        if "地下通道" in text:
            return RoadType.UNDERPASS
        if "天桥" in text:
            return RoadType.FOOTBRIDGE
        return RoadType.UNKNOWN

    def _generate_variants(self, base_route: Route, request: NavigationRequest) -> List[Route]:
        """
        当API只返回1条路线时，生成模拟变体用于演示排序功能。
        实际生产环境应使用支持多备选路线的API。
        """
        variants = []
        # 变体1: 稍长但可能经过更多人行道
        v1 = Route(
            name="路线2(备选)",
            source="amap_variant",
            segments=list(base_route.segments),
            polyline=list(base_route.polyline),
        )
        v1.total_distance_meters = base_route.total_distance_meters * 1.15
        v1.total_duration_seconds = int(base_route.total_duration_seconds * 1.1)
        for seg in v1.segments:
            if seg.road_type == RoadType.UNKNOWN:
                seg.road_type = RoadType.SIDEWALK
        variants.append(v1)

        # 变体2: 更短但经过更多红绿灯
        v2 = Route(
            name="路线3(快捷)",
            source="amap_variant",
            segments=list(base_route.segments),
            polyline=list(base_route.polyline),
        )
        v2.total_distance_meters = base_route.total_distance_meters * 0.9
        v2.total_duration_seconds = int(base_route.total_duration_seconds * 0.95)
        v2.traffic_light_count = max(2, base_route.traffic_light_count + 2)
        variants.append(v2)

        return variants

    def _mock_routes(self, request: NavigationRequest) -> List[Route]:
        """生成Mock路线用于无API Key或测试场景。"""
        origin = request.origin
        dest = request.destination
        mid = GeoPoint(
            (origin.lng + dest.lng) / 2,
            (origin.lat + dest.lat) / 2,
        )
        # 偏移点生成不同路线
        offset = 0.001
        mid_north = GeoPoint(mid.lng, mid.lat + offset)
        mid_south = GeoPoint(mid.lng, mid.lat - offset)
        mid_east = GeoPoint(mid.lng + offset, mid.lat)

        routes = []

        # 路线1: 经过盲道（最优）
        r1 = Route(name="路线1(盲道优先)", source="mock")
        r1.segments = [
            RouteSegment(origin, mid_north, "沿盲道向北直行", RoadType.BLIND_PATH, 150, 120),
            RouteSegment(mid_north, dest, "沿盲道向东到达目的地", RoadType.BLIND_PATH, 180, 144),
        ]
        r1.polyline = [origin, mid_north, dest]
        r1.compute_total_distance()
        routes.append(r1)

        # 路线2: 经过人行道
        r2 = Route(name="路线2(人行道)", source="mock")
        r2.segments = [
            RouteSegment(origin, mid_east, "沿人行道向东直行", RoadType.SIDEWALK, 200, 160),
            RouteSegment(mid_east, dest, "沿人行道向南到达目的地", RoadType.SIDEWALK, 160, 128),
        ]
        r2.polyline = [origin, mid_east, dest]
        r2.traffic_light_count = 1
        r2.compute_total_distance()
        routes.append(r2)

        # 路线3: 经过斑马线和未知道路
        r3 = Route(name="路线3(最短)", source="mock")
        r3.segments = [
            RouteSegment(origin, mid_south, "向南直行", RoadType.UNKNOWN, 120, 96),
            RouteSegment(mid_south, dest, "过斑马线后向东到达", RoadType.CROSSWALK, 140, 180),
        ]
        r3.polyline = [origin, mid_south, dest]
        r3.traffic_light_count = 2
        r3.compute_total_distance()
        routes.append(r3)

        return routes
