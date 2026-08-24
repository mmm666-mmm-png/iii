"""
基础设施 —— GeoJSON盲道矢量数据仓储

实现ITactilePavingRepository接口，从GeoJSON文件加载盲道数据。
GeoJSON格式: FeatureCollection, 每个Feature为LineString
属性可包含: name, type(continuous/warning), width
"""
from __future__ import annotations

import json
import logging
import os
from typing import List, Optional

from domain.model.route import GeoPoint
from domain.model.tactile_paving import TactilePaving, TactileSegment
from domain.repositories.tactile_paving_repository import ITactilePavingRepository
from .config import TactilePavingConfig

logger = logging.getLogger(__name__)


class GeoJsonTactilePavingRepository(ITactilePavingRepository):
    """基于GeoJSON文件的盲道数据仓储。"""

    def __init__(self, config: Optional[TactilePavingConfig] = None):
        self.config = config or TactilePavingConfig.from_env()
        self._cache: Optional[List[TactilePaving]] = None

    def get_all(self) -> List[TactilePaving]:
        """获取所有盲道数据（带缓存）。"""
        if self._cache is None:
            self._cache = self._load_from_file()
        return self._cache

    def get_in_bbox(
        self, min_lng: float, min_lat: float, max_lng: float, max_lat: float
    ) -> List[TactilePaving]:
        """获取边界框内的盲道。"""
        all_pavings = self.get_all()
        result = []
        for paving in all_pavings:
            for seg in paving.segments:
                if (min_lng <= seg.start.lng <= max_lng and
                    min_lat <= seg.start.lat <= max_lat):
                    result.append(paving)
                    break
        return result

    def get_near(
        self, center: GeoPoint, radius_meters: float
    ) -> List[TactilePaving]:
        """获取指定坐标半径内的盲道。"""
        all_pavings = self.get_all()
        result = []
        for paving in all_pavings:
            for seg in paving.segments:
                if (center.distance_to(seg.start) <= radius_meters or
                    center.distance_to(seg.end) <= radius_meters):
                    result.append(paving)
                    break
        return result

    def reload(self):
        """强制重新加载数据。"""
        self._cache = None
        self._cache = self._load_from_file()

    def _load_from_file(self) -> List[TactilePaving]:
        """从GeoJSON文件加载盲道数据。"""
        path = self.config.geojson_path
        # 相对路径统一按 python_worker 目录解析，避免工作目录不同导致找不到文件。
        if path and not os.path.isabs(path):
            worker_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            candidate = os.path.join(worker_dir, path)
            if os.path.exists(candidate):
                path = candidate

        if not path or not os.path.exists(path):
            logger.warning(f"盲道GeoJSON文件不存在: {path}，使用内置示例数据")
            return self._sample_data()

        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            return self._parse_geojson(data)
        except Exception as e:
            logger.error(f"加载盲道GeoJSON失败: {e}，使用示例数据")
            return self._sample_data()

    def _parse_geojson(self, data: dict) -> List[TactilePaving]:
        """解析GeoJSON FeatureCollection。"""
        pavings = []
        features = data.get("features", [])

        for idx, feature in enumerate(features):
            geom = feature.get("geometry", {})
            if geom.get("type") != "LineString":
                continue

            coords = geom.get("coordinates", [])
            if len(coords) < 2:
                continue

            props = feature.get("properties", {})
            name = props.get("name", f"盲道_{idx}")
            seg_type = props.get("type", "continuous")
            width = float(props.get("width", 0.6))

            segments = []
            for i in range(len(coords) - 1):
                start = GeoPoint(coords[i][0], coords[i][1])
                end = GeoPoint(coords[i + 1][0], coords[i + 1][1])
                segments.append(TactileSegment(
                    start=start,
                    end=end,
                    segment_type=seg_type,
                    width_meters=width,
                ))

            pavings.append(TactilePaving(
                name=name,
                segments=segments,
                source="geojson",
            ))

        logger.info(f"从GeoJSON加载了 {len(pavings)} 条盲道")
        return pavings

    def _sample_data(self) -> List[TactilePaving]:
        """
        内置示例盲道数据（枣庄学院附近）。
        用于无GeoJSON文件时的演示和测试。
        """
        # 枣庄学院附近坐标 (GCJ-02)
        base_lng, base_lat = 117.327, 34.812

        pavings = []

        # 盲道1: 南北向主干道
        segments1 = []
        for i in range(10):
            start = GeoPoint(base_lng, base_lat + i * 0.0008)
            end = GeoPoint(base_lng, base_lat + (i + 1) * 0.0008)
            segments1.append(TactileSegment(start, end, "continuous", 0.6))
        pavings.append(TactilePaving(name="学院路盲道", segments=segments1, source="sample"))

        # 盲道2: 东西向
        segments2 = []
        for i in range(8):
            start = GeoPoint(base_lng + i * 0.001, base_lat + 0.004)
            end = GeoPoint(base_lng + (i + 1) * 0.001, base_lat + 0.004)
            segments2.append(TactileSegment(start, end, "continuous", 0.6))
        pavings.append(TactilePaving(name="文化路盲道", segments=segments2, source="sample"))

        # 盲道3: 连接支路
        segments3 = []
        for i in range(5):
            start = GeoPoint(base_lng + 0.005 + i * 0.0006, base_lat + i * 0.0006)
            end = GeoPoint(base_lng + 0.005 + (i + 1) * 0.0006, base_lat + (i + 1) * 0.0006)
            segments3.append(TactileSegment(start, end, "continuous", 0.6))
        pavings.append(TactilePaving(name="支路盲道", segments=segments3, source="sample"))

        logger.info(f"使用内置示例盲道数据，共 {len(pavings)} 条")
        return pavings
