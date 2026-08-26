"""
领域模型 —— 障碍物实体

Obstacle: 眼镜端上报的单个障碍物
ObstacleHotspot: 时空聚合后的障碍物热点
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

from .route import GeoPoint


class ObstacleType:
    """障碍物类型常量。"""
    POLE = "pole"
    TREE = "tree"
    BICYCLE_PARKED = "bicycle_parked"
    CONSTRUCTION = "construction"
    BENCH = "bench"
    DUSTBIN = "dustbin"
    MANHOLE = "manhole"
    STAIRS = "stairs"
    PERSON = "person"
    BICYCLE_MOVING = "bicycle_moving"
    VEHICLE = "vehicle"
    ANIMAL = "animal"
    UNKNOWN = "unknown"

    @staticmethod
    def default_severity(otype: str) -> str:
        high = {ObstacleType.CONSTRUCTION, ObstacleType.STAIRS,
                ObstacleType.MANHOLE, ObstacleType.VEHICLE}
        medium = {ObstacleType.POLE, ObstacleType.TREE,
                  ObstacleType.BICYCLE_PARKED, ObstacleType.BICYCLE_MOVING,
                  ObstacleType.PERSON, ObstacleType.ANIMAL}
        if otype in high:
            return Severity.HIGH
        if otype in medium:
            return Severity.MEDIUM
        return Severity.LOW


class Severity:
    """严重程度常量。"""
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"

    @staticmethod
    def weight(sev: str) -> float:
        return {"low": 0.5, "medium": 1.0, "high": 2.0}.get(sev, 1.0)


@dataclass
class Obstacle:
    """
    障碍物实体 —— 眼镜端单次视觉上报。

    上报协议字段:
      - obstacle_id: 唯一ID
      - device_id: 眼镜设备ID
      - location: 位置(经纬度)
      - obstacle_type: 类型
      - severity: 严重程度(low/medium/high)
      - timestamp: 时间戳
      - confidence: 检测置信度0~1
      - description: 描述
    """
    obstacle_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    device_id: str = ""
    # 眼镜端无 GPS 时 location 为 None：上报仍会留存并可计数，但不参与空间聚类。
    location: Optional[GeoPoint] = None
    obstacle_type: str = ObstacleType.UNKNOWN
    severity: str = Severity.MEDIUM
    timestamp: datetime = field(default_factory=datetime.utcnow)
    confidence: float = 0.8
    description: str = ""

    def to_dict(self) -> dict:
        location = (
            {"lng": self.location.lng, "lat": self.location.lat}
            if self.location is not None
            else None
        )
        return {
            "obstacle_id": self.obstacle_id,
            "device_id": self.device_id,
            "location": location,
            "obstacle_type": self.obstacle_type,
            "severity": self.severity,
            "timestamp": self.timestamp.isoformat(),
            "confidence": self.confidence,
            "description": self.description,
        }


@dataclass
class ObstacleHotspot:
    """
    障碍物热点 —— 时空聚合后的结果。

    多个相近位置、相近时间的上报聚合为一个热点。
    """
    hotspot_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    location: GeoPoint = field(default_factory=lambda: GeoPoint(0, 0))
    obstacle_type: str = ObstacleType.UNKNOWN
    severity: str = Severity.MEDIUM
    report_count: int = 0
    first_seen: datetime = field(default_factory=datetime.utcnow)
    last_seen: datetime = field(default_factory=datetime.utcnow)
    avg_confidence: float = 0.0
    aggregate_weight: float = 0.0

    def compute_weight(self) -> float:
        self.aggregate_weight = (
            Severity.weight(self.severity) * self.report_count * self.avg_confidence
        )
        return self.aggregate_weight
