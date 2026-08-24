"""
领域模型 —— 导航请求值对象

NavigationRequest: 用户发起的一次路线规划请求
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

from .route import GeoPoint


@dataclass(frozen=True)
class NavigationRequest:
    """
    导航请求值对象（不可变）。

    属性:
      request_id: 请求唯一ID
      origin: 起点坐标
      destination: 终点坐标
      user_id: 用户ID（可选）
      device_id: 眼镜设备ID（可选）
      created_at: 请求时间
      preferences: 用户偏好（如避免天桥、优先电梯等）
    """
    request_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    origin: GeoPoint = field(default_factory=lambda: GeoPoint(0, 0))
    destination: GeoPoint = field(default_factory=lambda: GeoPoint(0, 0))
    user_id: str = ""
    device_id: str = ""
    created_at: datetime = field(default_factory=datetime.utcnow)
    preferences: dict = field(default_factory=dict)

    def is_valid(self) -> bool:
        """校验请求是否有效（起终点不能相同且不能为零坐标）。"""
        if self.origin.lng == 0 and self.origin.lat == 0:
            return False
        if self.destination.lng == 0 and self.destination.lat == 0:
            return False
        if self.origin.distance_to(self.destination) < 1.0:
            return False
        return True

    def to_dict(self) -> dict:
        return {
            "request_id": self.request_id,
            "origin": {"lng": self.origin.lng, "lat": self.origin.lat},
            "destination": {"lng": self.destination.lng, "lat": self.destination.lat},
            "user_id": self.user_id,
            "device_id": self.device_id,
            "created_at": self.created_at.isoformat(),
            "preferences": self.preferences,
        }
