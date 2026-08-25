"""
领域模型 —— 路线聚合根

Route: 聚合根，包含多条 RouteSegment（路段）和 Waypoint（途经点）
RouteSegment: 路段实体
Waypoint: 途经点值对象

零外部依赖。
"""
from __future__ import annotations

import math
import uuid
from dataclasses import dataclass, field
from typing import List, Optional, Tuple


# ============================================================
# 值对象：地理坐标点
# ============================================================
@dataclass(frozen=True)
class GeoPoint:
    """地理坐标点（经度, 纬度），GCJ-02坐标系。"""
    lng: float
    lat: float

    def distance_to(self, other: "GeoPoint") -> float:
        """Haversine公式计算两点间距离（米）。纯函数。"""
        R = 6371000.0
        phi1 = math.radians(self.lat)
        phi2 = math.radians(other.lat)
        dphi = math.radians(other.lat - self.lat)
        dlmb = math.radians(other.lng - self.lng)
        a = (math.sin(dphi / 2) ** 2
             + math.cos(phi1) * math.cos(phi2) * math.sin(dlmb / 2) ** 2)
        return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))

    def to_tuple(self) -> Tuple[float, float]:
        return (self.lng, self.lat)

    def __str__(self) -> str:
        return f"{self.lng:.6f},{self.lat:.6f}"


def point_to_segment_distance(
    p: GeoPoint, a: GeoPoint, b: GeoPoint
) -> Tuple[float, float]:
    """
    点p到线段ab的最短距离（米）和投影比例t∈[0,1]。纯函数。
    """
    if a.lng == b.lng and a.lat == b.lat:
        return p.distance_to(a), 0.0
    dx = b.lng - a.lng
    dy = b.lat - a.lat
    px = p.lng - a.lng
    py = p.lat - a.lat
    denom = dx * dx + dy * dy
    t = max(0.0, min(1.0, (px * dx + py * dy) / denom))
    proj = GeoPoint(a.lng + t * dx, a.lat + t * dy)
    return p.distance_to(proj), t


# GCJ-02 椭球参数（火星坐标加密）
_GCJ_A = 6378245.0
_GCJ_EE = 0.00669342162296594323


def _gcj_out_of_china(lng: float, lat: float) -> bool:
    """境外坐标不参与加密。"""
    return not (72.004 <= lng <= 137.8347 and 0.8293 <= lat <= 55.8271)


def _gcj_transform_lat(x: float, y: float) -> float:
    ret = -100.0 + 2.0 * x + 3.0 * y + 0.2 * y * y + 0.1 * x * y + 0.2 * math.sqrt(abs(x))
    ret += (20.0 * math.sin(6.0 * x * math.pi) + 20.0 * math.sin(2.0 * x * math.pi)) * 2.0 / 3.0
    ret += (20.0 * math.sin(y * math.pi) + 40.0 * math.sin(y / 3.0 * math.pi)) * 2.0 / 3.0
    ret += (160.0 * math.sin(y / 12.0 * math.pi) + 320.0 * math.sin(y * math.pi / 30.0)) * 2.0 / 3.0
    return ret


def _gcj_transform_lng(x: float, y: float) -> float:
    ret = 300.0 + x + 2.0 * y + 0.1 * x * x + 0.1 * x * y + 0.1 * math.sqrt(abs(x))
    ret += (20.0 * math.sin(6.0 * x * math.pi) + 20.0 * math.sin(2.0 * x * math.pi)) * 2.0 / 3.0
    ret += (20.0 * math.sin(x * math.pi) + 40.0 * math.sin(x / 3.0 * math.pi)) * 2.0 / 3.0
    ret += (150.0 * math.sin(x / 12.0 * math.pi) + 300.0 * math.sin(x / 30.0 * math.pi)) * 2.0 / 3.0
    return ret


def wgs84_to_gcj02(lng: float, lat: float) -> Tuple[float, float]:
    """WGS-84 转 GCJ-02（火星坐标）。纯函数，零外部依赖。

    手机 navigator.geolocation 返回 WGS-84，高德路线为 GCJ-02，二者在中国
    境内偏移可达几百米。按标准 GCJ-02 加密算法把 WGS-84 转成 GCJ-02，消除
    坐标系差异；境外坐标原样返回。
    """
    lng = float(lng)
    lat = float(lat)
    if _gcj_out_of_china(lng, lat):
        return lng, lat

    d_lat = _gcj_transform_lat(lng - 105.0, lat - 35.0)
    d_lng = _gcj_transform_lng(lng - 105.0, lat - 35.0)
    rad_lat = lat / 180.0 * math.pi
    magic = math.sin(rad_lat)
    magic = 1 - _GCJ_EE * magic * magic
    sqrt_magic = math.sqrt(magic)
    d_lat = (d_lat * 180.0) / ((_GCJ_A * (1 - _GCJ_EE)) / (magic * sqrt_magic) * math.pi)
    d_lng = (d_lng * 180.0) / (_GCJ_A / sqrt_magic * math.cos(rad_lat) * math.pi)
    return lng + d_lng, lat + d_lat


# ============================================================
# 值对象：途经点
# ============================================================
@dataclass(frozen=True)
class Waypoint:
    """途经点。"""
    point: GeoPoint
    name: str = ""


# ============================================================
# 道路类型枚举
# ============================================================
class RoadType:
    """道路类型（字符串常量，避免enum依赖）。"""
    BLIND_PATH = "blind_path"       # 盲道
    SIDEWALK = "sidewalk"           # 人行道
    PEDESTRIAN = "pedestrian"       # 步行街
    CROSSWALK = "crosswalk"         # 斑马线
    UNDERPASS = "underpass"         # 地下通道
    FOOTBRIDGE = "footbridge"       # 人行天桥
    SERVICE = "service"             # 服务道路
    UNKNOWN = "unknown"

    @staticmethod
    def friendliness(road_type: str) -> float:
        """步行友好度权重 0~1。"""
        return {
            RoadType.BLIND_PATH: 1.0,
            RoadType.SIDEWALK: 0.85,
            RoadType.PEDESTRIAN: 0.8,
            RoadType.CROSSWALK: 0.6,
            RoadType.SERVICE: 0.5,
            RoadType.UNDERPASS: 0.45,
            RoadType.FOOTBRIDGE: 0.4,
            RoadType.UNKNOWN: 0.3,
        }.get(road_type, 0.3)

    @staticmethod
    def label(road_type: str) -> str:
        """道路类型的中文播报名称。"""
        return {
            RoadType.BLIND_PATH: "盲道",
            RoadType.SIDEWALK: "人行道",
            RoadType.PEDESTRIAN: "步行街",
            RoadType.CROSSWALK: "斑马线",
            RoadType.UNDERPASS: "地下通道",
            RoadType.FOOTBRIDGE: "人行天桥",
            RoadType.SERVICE: "服务道路",
            RoadType.UNKNOWN: "普通道路",
        }.get(road_type, "普通道路")


# ============================================================
# 实体：路段
# ============================================================
@dataclass
class RouteSegment:
    """路线的一个路段。"""
    start: GeoPoint
    end: GeoPoint
    instruction: str = ""
    road_type: str = RoadType.UNKNOWN
    distance_meters: float = 0.0
    duration_seconds: int = 0

    def compute_distance(self) -> float:
        if self.distance_meters == 0:
            self.distance_meters = self.start.distance_to(self.end)
        return self.distance_meters


# ============================================================
# 聚合根：路线
# ============================================================
@dataclass
class Route:
    """
    路线聚合根。

    封装一条完整步行路线的所有信息：
    - segments: 路段列表
    - polyline: 坐标点序列（用于地图绘制和盲道匹配）
    - 总距离、总时长、转弯数、红绿灯数
    - 打分明细（由打分服务填充）
    """
    route_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    name: str = ""
    source: str = "unknown"  # amap / mock
    segments: List[RouteSegment] = field(default_factory=list)
    polyline: List[GeoPoint] = field(default_factory=list)
    total_distance_meters: float = 0.0
    total_duration_seconds: int = 0
    turn_count: int = 0
    traffic_light_count: int = 0

    # 打分明细（由RouteScoringService填充）
    score: float = 0.0
    score_length: float = 0.0
    score_turn: float = 0.0
    score_traffic_light: float = 0.0
    score_road_type: float = 0.0
    score_blind_path: float = 0.0
    score_obstacle: float = 0.0
    blind_path_coverage: float = 0.0
    obstacle_density: float = 0.0

    def compute_total_distance(self) -> float:
        total = sum(s.compute_distance() for s in self.segments)
        self.total_distance_meters = total
        return total

    def get_all_points(self) -> List[GeoPoint]:
        if self.polyline:
            return self.polyline
        pts = [s.start for s in self.segments]
        if self.segments:
            pts.append(self.segments[-1].end)
        return pts

    def count_turns(self, angle_threshold_deg: float = 30.0) -> int:
        """根据路段间方向变化估算转弯次数。纯函数。"""
        if len(self.segments) < 2:
            self.turn_count = 0
            return 0
        count = 0
        prev_angle = None
        for s in self.segments:
            angle = math.degrees(math.atan2(
                s.end.lat - s.start.lat, s.end.lng - s.start.lng))
            if prev_angle is not None:
                diff = abs(angle - prev_angle)
                if diff > 180:
                    diff = 360 - diff
                if diff > angle_threshold_deg:
                    count += 1
            prev_angle = angle
        self.turn_count = count
        return count

    def score_breakdown_dict(self) -> dict:
        """可解释的打分明细。"""
        return {
            "total_score": round(self.score, 2),
            "dimensions": {
                "length": round(self.score_length, 2),
                "turn": round(self.score_turn, 2),
                "traffic_light": round(self.score_traffic_light, 2),
                "road_type": round(self.score_road_type, 2),
                "blind_path": round(self.score_blind_path, 2),
                "obstacle": round(self.score_obstacle, 2),
            },
            "raw_metrics": {
                "distance_meters": round(self.total_distance_meters, 1),
                "turn_count": self.turn_count,
                "traffic_light_count": self.traffic_light_count,
                "blind_path_coverage": round(self.blind_path_coverage, 4),
                "obstacle_density_per_km": round(self.obstacle_density, 3),
            },
        }
