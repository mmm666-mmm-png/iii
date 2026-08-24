"""
领域模型 —— 盲道（触感铺装）值对象

TactilePaving: 一条盲道，由多个线段组成
TactileSegment: 盲道线段
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import List, Tuple

from .route import GeoPoint


@dataclass(frozen=True)
class TactileSegment:
    """
    盲道线段（值对象，不可变）。

    属性:
      start, end: 起终点坐标
      segment_type: continuous(连续砖) / warning(提示砖)
      width_meters: 盲道宽度
    """
    start: GeoPoint
    end: GeoPoint
    segment_type: str = "continuous"
    width_meters: float = 0.6

    @property
    def length_meters(self) -> float:
        return self.start.distance_to(self.end)

    def to_tuple(self) -> Tuple[GeoPoint, GeoPoint]:
        return (self.start, self.end)


@dataclass
class TactilePaving:
    """
    盲道实体：一条完整的盲道（由多个线段组成）。

    属性:
      paving_id: 唯一标识
      name: 名称
      segments: 线段列表
      source: 数据来源(geojson/csv/采集)
    """
    paving_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    name: str = ""
    segments: List[TactileSegment] = field(default_factory=list)
    source: str = "unknown"

    @property
    def total_length_meters(self) -> float:
        return sum(s.length_meters for s in self.segments)

    def get_all_segments(self) -> List[Tuple[GeoPoint, GeoPoint]]:
        return [s.to_tuple() for s in self.segments]

    def get_all_points(self) -> List[GeoPoint]:
        pts: List[GeoPoint] = []
        for s in self.segments:
            if not pts or pts[-1] != s.start:
                pts.append(s.start)
            pts.append(s.end)
        return pts
