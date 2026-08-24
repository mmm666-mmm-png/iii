"""
仓储接口 —— 盲道矢量数据仓储

定义静态盲道数据的获取抽象。基础设施层可实现为GeoJSON文件、数据库或API。
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import List, Optional

from ..model.route import GeoPoint
from ..model.tactile_paving import TactilePaving


class ITactilePavingRepository(ABC):
    """盲道矢量数据仓储接口。"""

    @abstractmethod
    def get_all(self) -> List[TactilePaving]:
        """获取所有盲道数据。"""
        ...

    @abstractmethod
    def get_in_bbox(
        self, min_lng: float, min_lat: float, max_lng: float, max_lat: float
    ) -> List[TactilePaving]:
        """获取指定边界框内的盲道数据。"""
        ...

    @abstractmethod
    def get_near(
        self, center: GeoPoint, radius_meters: float
    ) -> List[TactilePaving]:
        """获取指定坐标半径内的盲道数据。"""
        ...
