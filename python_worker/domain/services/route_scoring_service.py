"""
领域服务 —— 路线打分重排序引擎

RouteScoringService: 对多条备选路线进行多维度打分并排序
核心原则: 盲道友好度优先，融合静态盲道覆盖 + 实时障碍物密度

打分维度（权重可配置）:
  1. 距离长度      (length)        - 越短越好
  2. 转弯次数      (turn)          - 越少越好
  3. 红绿灯数量    (traffic_light) - 越少越好
  4. 道路类型友好度(road_type)     - 盲道>人行道>步行街>...
  5. 盲道覆盖率    (blind_path)    - 越高越好（核心维度）
  6. 障碍物密度    (obstacle)      - 越低越好（核心维度，实时）

总分 = Σ(weight_i * normalized_score_i)，范围 0~100
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from ..model.route import Route, RouteSegment, GeoPoint, RoadType, point_to_segment_distance
from ..model.obstacle import ObstacleHotspot, Severity
from ..model.tactile_paving import TactilePaving


@dataclass
class ScoringConfig:
    """打分权重配置。所有权重之和不强制为1，内部会归一化。"""
    weight_length: float = 0.10
    weight_turn: float = 0.10
    weight_traffic_light: float = 0.10
    weight_road_type: float = 0.15
    weight_blind_path: float = 0.30       # 核心：盲道覆盖
    weight_obstacle: float = 0.25         # 核心：障碍物密度

    # 归一化参数
    max_distance_meters: float = 5000.0   # 用于距离归一化的参考值
    max_turns: int = 20
    max_traffic_lights: int = 15

    # 盲道匹配阈值（米）
    blind_path_match_threshold: float = 3.0

    # 障碍物影响半径（米）
    obstacle_influence_radius: float = 15.0


class RouteScoringService:
    """
    路线打分服务。

    纯领域逻辑，零外部依赖。输入路线列表 + 盲道数据 + 障碍物热点，
    输出打分排序后的路线列表。
    """

    def __init__(self, config: Optional[ScoringConfig] = None):
        self.config = config or ScoringConfig()
        self._normalize_weights()

    def _normalize_weights(self):
        """权重归一化，确保总和为1。"""
        c = self.config
        total = (c.weight_length + c.weight_turn + c.weight_traffic_light
                 + c.weight_road_type + c.weight_blind_path + c.weight_obstacle)
        if total > 0:
            c.weight_length /= total
            c.weight_turn /= total
            c.weight_traffic_light /= total
            c.weight_road_type /= total
            c.weight_blind_path /= total
            c.weight_obstacle /= total

    def score_route(
        self,
        route: Route,
        tactile_pavings: List[TactilePaving],
        obstacle_hotspots: List[ObstacleHotspot],
    ) -> Route:
        """
        对单条路线打分，填充route的score字段和各维度分数字段。

        Args:
          route: 待打分的路线
          tactile_pavings: 静态盲道矢量数据列表
          obstacle_hotspots: 实时障碍物热点列表（已时空聚合）

        Returns:
          同一route对象，分数已填充
        """
        c = self.config

        # 确保基础指标已计算
        route.compute_total_distance()
        route.count_turns()

        # 1. 距离分数 (越短越高，0~1)
        dist = route.total_distance_meters
        score_length = max(0.0, 1.0 - dist / c.max_distance_meters)

        # 2. 转弯分数 (越少越高)
        score_turn = max(0.0, 1.0 - route.turn_count / c.max_turns)

        # 3. 红绿灯分数 (越少越高)
        score_tl = max(0.0, 1.0 - route.traffic_light_count / c.max_traffic_lights)

        # 4. 道路类型友好度 (按路段距离加权平均)
        score_road_type = self._compute_road_type_score(route)

        # 5. 盲道覆盖率 (核心维度)
        blind_coverage = self._compute_blind_path_coverage(route, tactile_pavings)
        route.blind_path_coverage = blind_coverage
        score_blind_path = blind_coverage  # 已经是0~1

        # 6. 障碍物密度 (核心维度，越低分越高)
        obstacle_density = self._compute_obstacle_density(route, obstacle_hotspots)
        route.obstacle_density = obstacle_density  # 个/公里
        # 归一化：假设每公里0个=1分，每公里20个=0分
        score_obstacle = max(0.0, 1.0 - obstacle_density / 20.0)

        # 加权总分 (0~100)
        total = (
            c.weight_length * score_length
            + c.weight_turn * score_turn
            + c.weight_traffic_light * score_tl
            + c.weight_road_type * score_road_type
            + c.weight_blind_path * score_blind_path
            + c.weight_obstacle * score_obstacle
        ) * 100.0

        route.score = round(total, 2)
        route.score_length = round(score_length * 100, 2)
        route.score_turn = round(score_turn * 100, 2)
        route.score_traffic_light = round(score_tl * 100, 2)
        route.score_road_type = round(score_road_type * 100, 2)
        route.score_blind_path = round(score_blind_path * 100, 2)
        route.score_obstacle = round(score_obstacle * 100, 2)

        return route

    def rank_routes(
        self,
        routes: List[Route],
        tactile_pavings: List[TactilePaving],
        obstacle_hotspots: List[ObstacleHotspot],
    ) -> List[Route]:
        """
        对多条路线打分并按总分降序排列。

        Returns:
          排序后的路线列表，第一条为最优路线。
        """
        scored = [
            self.score_route(r, tactile_pavings, obstacle_hotspots)
            for r in routes
        ]
        scored.sort(key=lambda r: r.score, reverse=True)
        return scored

    def _compute_road_type_score(self, route: Route) -> float:
        """按路段距离加权计算道路类型友好度。"""
        if not route.segments:
            return 0.3  # 默认低分
        total_dist = 0.0
        weighted_sum = 0.0
        for seg in route.segments:
            d = seg.compute_distance()
            if d <= 0:
                continue
            total_dist += d
            weighted_sum += d * RoadType.friendliness(seg.road_type)
        if total_dist == 0:
            return 0.3
        return weighted_sum / total_dist

    def _compute_blind_path_coverage(
        self, route: Route, pavings: List[TactilePaving]
    ) -> float:
        """
        计算路线的盲道覆盖率。

        方法: 将路线polyline等距采样，统计落在盲道匹配阈值内的采样点比例。
        返回 0~1。
        """
        points = route.get_all_points()
        if len(points) < 2 or not pavings:
            return 0.0

        # 收集所有盲道线段
        all_segments: List[Tuple[GeoPoint, GeoPoint]] = []
        for paving in pavings:
            all_segments.extend(paving.get_all_segments())
        if not all_segments:
            return 0.0

        # 路线采样：每隔约5米一个采样点
        sample_points = self._sample_route_points(points, interval_meters=5.0)
        if not sample_points:
            return 0.0

        threshold = self.config.blind_path_match_threshold
        match_count = 0
        for p in sample_points:
            if self._is_near_any_segment(p, all_segments, threshold):
                match_count += 1

        return match_count / len(sample_points)

    def _compute_obstacle_density(
        self, route: Route, hotspots: List[ObstacleHotspot]
    ) -> float:
        """
        计算路线沿途障碍物密度（个/公里）。

        方法: 统计影响半径内的障碍物热点加权数量，除以路线长度(公里)。
        每个热点按严重程度加权: high=2, medium=1, low=0.5
        """
        if not hotspots or route.total_distance_meters <= 0:
            return 0.0

        points = route.get_all_points()
        if len(points) < 2:
            return 0.0

        radius = self.config.obstacle_influence_radius
        weighted_count = 0.0

        for hotspot in hotspots:
            # 检查热点是否在路线附近
            min_dist = self._point_to_polyline_distance(hotspot.location, points)
            if min_dist <= radius:
                # 距离衰减: 越近影响越大
                decay = 1.0 - (min_dist / radius) * 0.5
                weighted_count += hotspot.aggregate_weight * decay

        # 转换为个/公里
        distance_km = route.total_distance_meters / 1000.0
        if distance_km <= 0:
            return 0.0
        return weighted_count / distance_km

    def _sample_route_points(
        self, points: List[GeoPoint], interval_meters: float = 5.0
    ) -> List[GeoPoint]:
        """沿路线按固定间隔采样。"""
        if len(points) < 2:
            return list(points)
        samples = [points[0]]
        acc_dist = 0.0
        for i in range(len(points) - 1):
            a, b = points[i], points[i + 1]
            seg_dist = a.distance_to(b)
            if seg_dist == 0:
                continue
            remaining = interval_meters - acc_dist
            while remaining <= seg_dist:
                # 插值
                t = remaining / seg_dist
                samples.append(GeoPoint(
                    a.lng + t * (b.lng - a.lng),
                    a.lat + t * (b.lat - a.lat),
                ))
                remaining += interval_meters
            acc_dist = seg_dist - (remaining - interval_meters)
        samples.append(points[-1])
        return samples

    def _is_near_any_segment(
        self, p: GeoPoint, segments: List[Tuple[GeoPoint, GeoPoint]], threshold: float
    ) -> bool:
        """判断点是否在任意线段的阈值距离内。"""
        for a, b in segments:
            dist, _ = point_to_segment_distance(p, a, b)
            if dist <= threshold:
                return True
        return False

    def _point_to_polyline_distance(self, p: GeoPoint, points: List[GeoPoint]) -> float:
        """点到折线的最短距离。"""
        if len(points) < 2:
            return p.distance_to(points[0]) if points else float("inf")
        min_dist = float("inf")
        for i in range(len(points) - 1):
            dist, _ = point_to_segment_distance(p, points[i], points[i + 1])
            if dist < min_dist:
                min_dist = dist
        return min_dist
