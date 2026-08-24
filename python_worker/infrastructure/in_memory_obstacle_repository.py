"""
基础设施 —— 内存障碍物上报仓储

实现IObstacleRepository接口，使用内存存储+时空聚合。
适用于单实例部署；多实例部署应替换为Redis实现。
"""
from __future__ import annotations

import logging
import threading
from collections import deque
from datetime import datetime, timedelta
from typing import Deque, List, Optional

from domain.model.obstacle import Obstacle, ObstacleHotspot
from domain.model.route import GeoPoint
from domain.repositories.obstacle_repository import IObstacleRepository
from domain.services.obstacle_aggregator import ObstacleAggregator, AggregationConfig

logger = logging.getLogger(__name__)


class InMemoryObstacleRepository(IObstacleRepository):
    """基于内存的障碍物上报仓储。"""

    def __init__(
        self,
        max_history: int = 10000,
        retention_seconds: int = 600,
        aggregation_config: Optional[AggregationConfig] = None,
    ):
        self._lock = threading.Lock()
        self._reports: Deque[Obstacle] = deque(maxlen=max_history)
        self._retention = retention_seconds
        self._aggregator = ObstacleAggregator(aggregation_config)

    def add(self, obstacle: Obstacle) -> None:
        """保存一条障碍物上报并更新聚合。"""
        with self._lock:
            self._reports.append(obstacle)
            self._aggregator.add_report(obstacle)
            self._purge_old(obstacle.timestamp)

    def get_recent(self, seconds: int = 300) -> List[Obstacle]:
        """获取最近N秒内的上报。"""
        cutoff = datetime.utcnow() - timedelta(seconds=seconds)
        with self._lock:
            return [r for r in self._reports if r.timestamp >= cutoff]

    def get_hotspots(
        self, center: Optional[GeoPoint] = None, radius_meters: float = 1000.0
    ) -> List[ObstacleHotspot]:
        """获取障碍物热点。"""
        with self._lock:
            if center is not None:
                return self._aggregator.get_hotspots_near(center, radius_meters)
            return self._aggregator.get_active_hotspots()

    def clear(self) -> None:
        """清空所有上报。"""
        with self._lock:
            self._reports.clear()
            self._aggregator.clear()

    def get_report_count(self) -> int:
        """获取当前存储的上报总数。"""
        with self._lock:
            return len(self._reports)

    def _purge_old(self, now: datetime):
        """清理超过保留期的上报。"""
        cutoff = now - timedelta(seconds=self._retention)
        while self._reports and self._reports[0].timestamp < cutoff:
            self._reports.popleft()
