"""
领域服务 —— 障碍物时空聚合器

ObstacleAggregator: 将眼镜端实时上报的单个障碍物聚合为时空热点
- 空间聚类: 距离在阈值内的障碍物归为同一热点
- 时间衰减: 旧上报权重随时间衰减
- 严重程度加权: high/medium/low 不同权重
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Dict, List, Optional

from ..model.obstacle import Obstacle, ObstacleHotspot, ObstacleType, Severity
from ..model.route import GeoPoint


@dataclass
class AggregationConfig:
    """聚合配置。"""
    spatial_cluster_radius_meters: float = 10.0   # 空间聚类半径
    time_window_seconds: int = 300                 # 时间窗口（5分钟）
    decay_half_life_seconds: int = 120             # 衰减半衰期（2分钟）
    min_reports_for_hotspot: int = 1               # 形成热点的最小上报数


class ObstacleAggregator:
    """
    障碍物聚合服务。

    维护内部热点列表，接收单条上报后更新热点状态。
    纯领域逻辑，不依赖存储。
    """

    def __init__(self, config: Optional[AggregationConfig] = None):
        self.config = config or AggregationConfig()
        self._hotspots: Dict[str, ObstacleHotspot] = {}

    def add_report(self, obstacle: Obstacle) -> ObstacleHotspot:
        """
        处理一条障碍物上报，返回更新后的热点。

        逻辑:
        1. 查找空间范围内同类型的已有热点
        2. 找到则合并（更新位置、计数、置信度、时间）
        3. 没找到则创建新热点
        """
        # 先清理过期热点
        self._purge_expired(obstacle.timestamp)

        # 查找匹配的热点
        matched = self._find_matching_hotspot(obstacle)

        if matched:
            self._merge_into_hotspot(matched, obstacle)
            return matched
        else:
            new_hotspot = self._create_hotspot(obstacle)
            self._hotspots[new_hotspot.hotspot_id] = new_hotspot
            return new_hotspot

    def get_active_hotspots(
        self, now: Optional[datetime] = None
    ) -> List[ObstacleHotspot]:
        """获取当前所有活跃热点（已清理过期）。"""
        if now is None:
            now = datetime.utcnow()
        self._purge_expired(now)
        # 重新计算权重（含时间衰减）
        for hs in self._hotspots.values():
            self._update_hotspot_weight(hs, now)
        return list(self._hotspots.values())

    def get_hotspots_near(
        self, center: GeoPoint, radius_meters: float, now: Optional[datetime] = None
    ) -> List[ObstacleHotspot]:
        """获取指定坐标半径内的热点。"""
        active = self.get_active_hotspots(now)
        return [
            hs for hs in active
            if hs.location.distance_to(center) <= radius_meters
        ]

    def clear(self):
        """清空所有热点。"""
        self._hotspots.clear()

    def _find_matching_hotspot(self, obstacle: Obstacle) -> Optional[ObstacleHotspot]:
        """查找空间和类型都匹配的热点。"""
        radius = self.config.spatial_cluster_radius_meters
        for hs in self._hotspots.values():
            if hs.obstacle_type != obstacle.obstacle_type:
                continue
            if hs.location.distance_to(obstacle.location) <= radius:
                return hs
        return None

    def _merge_into_hotspot(self, hotspot: ObstacleHotspot, obstacle: Obstacle):
        """将上报合并到已有热点（位置加权平均、更新计数和时间）。"""
        n = hotspot.report_count
        # 位置加权平均（新报告权重为1，历史权重为n）
        total_weight = n + 1
        hotspot.location = GeoPoint(
            (hotspot.location.lng * n + obstacle.location.lng) / total_weight,
            (hotspot.location.lat * n + obstacle.location.lat) / total_weight,
        )
        hotspot.report_count += 1
        hotspot.last_seen = obstacle.timestamp
        if obstacle.timestamp < hotspot.first_seen:
            hotspot.first_seen = obstacle.timestamp
        # 置信度加权平均
        hotspot.avg_confidence = (
            (hotspot.avg_confidence * n + obstacle.confidence) / total_weight
        )
        # 严重程度取最高
        if Severity.weight(obstacle.severity) > Severity.weight(hotspot.severity):
            hotspot.severity = obstacle.severity
        self._update_hotspot_weight(hotspot, obstacle.timestamp)

    def _create_hotspot(self, obstacle: Obstacle) -> ObstacleHotspot:
        """从单条上报创建新热点。"""
        hs = ObstacleHotspot(
            location=obstacle.location,
            obstacle_type=obstacle.obstacle_type,
            severity=obstacle.severity,
            report_count=1,
            first_seen=obstacle.timestamp,
            last_seen=obstacle.timestamp,
            avg_confidence=obstacle.confidence,
        )
        self._update_hotspot_weight(hs, obstacle.timestamp)
        return hs

    def _update_hotspot_weight(self, hotspot: ObstacleHotspot, now: datetime):
        """计算热点的聚合权重（含时间衰减）。"""
        age_seconds = (now - hotspot.last_seen).total_seconds()
        half_life = self.config.decay_half_life_seconds
        # 指数衰减: weight = base * 0.5^(age/half_life)
        decay = math.pow(0.5, age_seconds / half_life) if half_life > 0 else 1.0
        base_weight = (
            Severity.weight(hotspot.severity)
            * hotspot.report_count
            * hotspot.avg_confidence
        )
        hotspot.aggregate_weight = base_weight * decay

    def _purge_expired(self, now: datetime):
        """移除超过时间窗口的热点。"""
        cutoff = now - timedelta(seconds=self.config.time_window_seconds)
        expired = [
            hid for hid, hs in self._hotspots.items()
            if hs.last_seen < cutoff
        ]
        for hid in expired:
            del self._hotspots[hid]
