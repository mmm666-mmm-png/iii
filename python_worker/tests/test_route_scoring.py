"""
单元测试 —— 路线打分重排序引擎

验证:
1. 单条路线打分各维度计算正确
2. 多条路线排序正确（盲道友好度高的排前面）
3. 盲道覆盖率计算正确
4. 障碍物密度计算正确
5. 权重归一化正确
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import math
from datetime import datetime

from domain.model.route import Route, RouteSegment, GeoPoint, RoadType
from domain.model.obstacle import ObstacleHotspot, ObstacleType, Severity
from domain.model.tactile_paving import TactilePaving, TactileSegment
from domain.services.route_scoring_service import RouteScoringService, ScoringConfig


def make_route(name, segments, traffic_lights=0):
    """辅助函数：创建路线。"""
    r = Route(name=name, source="test")
    r.segments = segments
    r.polyline = [s.start for s in segments] + [segments[-1].end] if segments else []
    r.traffic_light_count = traffic_lights
    r.compute_total_distance()
    r.count_turns()
    return r


def make_segment(lng1, lat1, lng2, lat2, road_type=RoadType.SIDEWALK, instruction=""):
    return RouteSegment(
        start=GeoPoint(lng1, lat1),
        end=GeoPoint(lng2, lat2),
        instruction=instruction,
        road_type=road_type,
    )


def make_tactile_paving(lng, lat, length_segments=5):
    """创建一条南北向的盲道。"""
    segments = []
    for i in range(length_segments):
        start = GeoPoint(lng, lat + i * 0.0008)
        end = GeoPoint(lng, lat + (i + 1) * 0.0008)
        segments.append(TactileSegment(start, end, "continuous", 0.6))
    return TactilePaving(name="test_blind_path", segments=segments, source="test")


def make_hotspot(lng, lat, otype=ObstacleType.POLE, severity=Severity.MEDIUM, count=3):
    hs = ObstacleHotspot(
        location=GeoPoint(lng, lat),
        obstacle_type=otype,
        severity=severity,
        report_count=count,
        avg_confidence=0.85,
    )
    hs.compute_weight()
    return hs


class TestRouteScoringService:
    """路线打分服务测试。"""

    def setup_method(self):
        self.service = RouteScoringService()

    def test_weight_normalization(self):
        """测试权重归一化。"""
        config = ScoringConfig(
            weight_length=10,
            weight_turn=10,
            weight_traffic_light=10,
            weight_road_type=10,
            weight_blind_path=10,
            weight_obstacle=10,
        )
        svc = RouteScoringService(config)
        total = (
            svc.config.weight_length + svc.config.weight_turn
            + svc.config.weight_traffic_light + svc.config.weight_road_type
            + svc.config.weight_blind_path + svc.config.weight_obstacle
        )
        assert abs(total - 1.0) < 0.001, f"权重和应为1，实际{total}"

    def test_single_route_scoring(self):
        """测试单条路线打分。"""
        route = make_route("测试路线", [
            make_segment(117.327, 34.812, 117.328, 34.813, RoadType.BLIND_PATH),
            make_segment(117.328, 34.813, 117.329, 34.814, RoadType.SIDEWALK),
        ])
        scored = self.service.score_route(route, [], [])

        assert scored.score > 0, "分数应大于0"
        assert scored.score <= 100, "分数应不超过100"
        assert scored.score_length >= 0
        assert scored.score_turn >= 0
        assert scored.score_road_type >= 0

    def test_blind_path_route_scores_higher(self):
        """测试盲道路线得分高于普通路线。"""
        # 盲道路线
        blind_route = make_route("盲道路线", [
            make_segment(117.327, 34.812, 117.328, 34.813, RoadType.BLIND_PATH),
            make_segment(117.328, 34.813, 117.329, 34.814, RoadType.BLIND_PATH),
        ])
        # 普通路线
        normal_route = make_route("普通路线", [
            make_segment(117.327, 34.812, 117.328, 34.813, RoadType.UNKNOWN),
            make_segment(117.328, 34.813, 117.329, 34.814, RoadType.UNKNOWN),
        ])

        pavings = [make_tactile_paving(117.3275, 34.812, 5)]

        scored_blind = self.service.score_route(blind_route, pavings, [])
        scored_normal = self.service.score_route(normal_route, pavings, [])

        # 盲道路线的road_type分数应该更高
        assert scored_blind.score_road_type > scored_normal.score_road_type, \
            f"盲道道路类型分({scored_blind.score_road_type})应高于普通({scored_normal.score_road_type})"

    def test_route_ranking(self):
        """测试多条路线排序。"""
        routes = [
            make_route("路线C(差)", [
                make_segment(117.327, 34.812, 117.330, 34.815, RoadType.UNKNOWN),
            ], traffic_lights=5),
            make_route("路线A(好)", [
                make_segment(117.327, 34.812, 117.328, 34.813, RoadType.BLIND_PATH),
                make_segment(117.328, 34.813, 117.329, 34.814, RoadType.BLIND_PATH),
            ], traffic_lights=0),
            make_route("路线B(中)", [
                make_segment(117.327, 34.812, 117.3285, 34.8135, RoadType.SIDEWALK),
            ], traffic_lights=2),
        ]

        pavings = [make_tactile_paving(117.3275, 34.812, 5)]
        ranked = self.service.rank_routes(routes, pavings, [])

        assert ranked[0].name == "路线A(好)", f"最优路线应为路线A，实际{ranked[0].name}"
        assert ranked[0].score >= ranked[1].score >= ranked[2].score

    def test_obstacle_density_affects_score(self):
        """测试障碍物密度影响分数。"""
        # 使用两个独立的route对象（score_route会修改传入对象）
        route1 = make_route("测试路线", [
            make_segment(117.327, 34.812, 117.329, 34.814, RoadType.SIDEWALK),
        ])
        route2 = make_route("测试路线", [
            make_segment(117.327, 34.812, 117.329, 34.814, RoadType.SIDEWALK),
        ])

        # 无障碍物
        no_obstacle = self.service.score_route(route1, [], [])

        # 有障碍物（在路线中点附近）
        hotspots = [make_hotspot(117.328, 34.813, count=5)]
        with_obstacle = self.service.score_route(route2, [], hotspots)

        assert with_obstacle.obstacle_density > 0, "障碍物密度应大于0"
        assert with_obstacle.score_obstacle < no_obstacle.score_obstacle, \
            f"有障碍物时障碍物维度分数({with_obstacle.score_obstacle})应低于无障碍物({no_obstacle.score_obstacle})"

    def test_blind_path_coverage_calculation(self):
        """测试盲道覆盖率计算。"""
        # 路线与盲道重合
        route_on_blind = make_route("盲道上的路线", [
            make_segment(117.327, 34.812, 117.327, 34.816, RoadType.SIDEWALK),
        ])
        pavings = [make_tactile_paving(117.327, 34.812, 6)]

        scored = self.service.score_route(route_on_blind, pavings, [])
        assert scored.blind_path_coverage > 0.5, \
            f"盲道上的路线覆盖率应>50%，实际{scored.blind_path_coverage:.1%}"

    def test_score_breakdown_dict(self):
        """测试打分明细字典输出。"""
        route = make_route("测试", [
            make_segment(117.327, 34.812, 117.328, 34.813),
        ])
        scored = self.service.score_route(route, [], [])
        breakdown = scored.score_breakdown_dict()

        assert "total_score" in breakdown
        assert "dimensions" in breakdown
        assert "raw_metrics" in breakdown
        assert all(k in breakdown["dimensions"]
                   for k in ["length", "turn", "traffic_light", "road_type", "blind_path", "obstacle"])


if __name__ == "__main__":
    # 简单运行测试
    test = TestRouteScoringService()
    test.setup_method()
    test.test_weight_normalization()
    print("✓ 权重归一化测试通过")
    test.test_single_route_scoring()
    print("✓ 单条路线打分测试通过")
    test.test_blind_path_route_scores_higher()
    print("✓ 盲道路线高分测试通过")
    test.test_route_ranking()
    print("✓ 路线排序测试通过")
    test.test_obstacle_density_affects_score()
    print("✓ 障碍物密度影响测试通过")
    test.test_blind_path_coverage_calculation()
    print("✓ 盲道覆盖率计算测试通过")
    test.test_score_breakdown_dict()
    print("✓ 打分明细输出测试通过")
    print("\n所有打分引擎测试通过！")
