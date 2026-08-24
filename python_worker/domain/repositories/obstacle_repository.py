"""
仓储接口 —— 障碍物上报仓储

定义眼镜端实时障碍物上报的存储抽象。基础设施层可实现为内存、Redis或数据库。
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import List, Optional

from ..model.obstacle import Obstacle, ObstacleHotspot
from ..model.route import GeoPoint


class IObstacleRepository(ABC):
    """障碍物上报仓储接口。"""

    @abstractmethod
    def add(self, obstacle: Obstacle) -> None:
        """保存一条障碍物上报。"""
        ...

    @abstractmethod
    def get_recent(self, seconds: int = 300) -> List[Obstacle]:
        """获取最近N秒内的所有上报。"""
        ...

    @abstractmethod
    def get_hotspots(
        self, center: Optional[GeoPoint] = None, radius_meters: float = 1000.0
    ) -> List[ObstacleHotspot]:
        """
        获取障碍物热点（时空聚合后）。

        Args:
          center: 可选，指定中心点
          radius_meters: 中心半径（仅当center提供时有效）
        """
        ...

    @abstractmethod
    def clear(self) -> None:
        """清空所有上报。"""
        ...
