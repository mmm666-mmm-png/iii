"""
集成测试 —— 路线规划服务

验证完整流程:
1. 路线规划服务能正常初始化
2. Mock模式下能返回多条路线
3. 最优路线有合理的分数
4. 结果包含所有必要字段
5. 障碍物上报后影响路线打分
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from application.route_planning_service import RoutePlanningService
from application.dtos.route_dtos import RoutePlanningRequest
from infrastructure.in_memory_obstacle_repository import InMemoryObstacleRepository
from domain.model.obstacle import Obstacle, ObstacleType, Severity
from domain.model.route import GeoPoint, Route, RouteSegment, RoadType


class TestRoutePlanningService:
    """路线规划服务集成测试。"""

    def setup_method(self):
        self.obstacle_repo = InMemoryObstacleRepository()
        self.service = RoutePlanningService(obstacle_repo=self.obstacle_repo)

    def test_service_initialization(self):
        """服务应能正常初始化。"""
        assert self.service.route_repo is not None
        assert self.service.tactile_repo is not None
        assert self.service.obstacle_repo is not None
        assert self.service.scoring_service is not None
        assert self.service.voice_client is not None

    def test_plan_route_returns_result(self):
        """路线规划应返回有效结果。"""
        request = RoutePlanningRequest(
            origin_lng=117.327, origin_lat=34.812,
            destination_lng=117.335, destination_lat=34.820,
        )
        result = self.service.plan_route(request)

        assert result is not None
        assert result.best_route is not None
        assert len(result.all_routes) >= 1
        assert result.broadcast_text != ""
        assert result.request_id != ""

    def test_best_route_has_score(self):
        """最优路线应有分数。"""
        request = RoutePlanningRequest(
            origin_lng=117.327, origin_lat=34.812,
            destination_lng=117.335, destination_lat=34.820,
        )
        result = self.service.plan_route(request)

        assert result.best_route.score > 0
        assert result.best_route.score <= 100

    def test_routes_sorted_by_score(self):
        """所有路线应按分数降序排列。"""
        request = RoutePlanningRequest(
            origin_lng=117.327, origin_lat=34.812,
            destination_lng=117.335, destination_lat=34.820,
        )
        result = self.service.plan_route(request)

        scores = [r.score for r in result.all_routes]
        assert scores == sorted(scores, reverse=True), "路线应按分数降序排列"

    def test_result_contains_required_fields(self):
        """结果应包含所有必要字段。"""
        request = RoutePlanningRequest(
            origin_lng=117.327, origin_lat=34.812,
            destination_lng=117.335, destination_lat=34.820,
        )
        result = self.service.plan_route(request)
        d = result.to_dict()

        assert "request_id" in d
        assert "best_route" in d
        assert "all_routes" in d
        assert "broadcast_text" in d
        assert "score_breakdown" in d
        assert "data_sources" in d
        assert "obstacle_hotspot_count" in d

    def test_used_amap_flag_reflects_real_amap_routes(self):
        class FakeAmapRepo:
            def fetch_routes(self, request):
                route = Route(name="Amap Route", source="amap")
                route.segments = [
                    RouteSegment(
                        request.origin,
                        request.destination,
                        "向前行驶",
                        RoadType.SIDEWALK,
                        120,
                        90,
                    )
                ]
                route.polyline = [request.origin, request.destination]
                route.compute_total_distance()
                route.count_turns()
                return [route]

        service = RoutePlanningService(
            route_repo=FakeAmapRepo(),
            obstacle_repo=self.obstacle_repo,
        )
        request = RoutePlanningRequest(
            origin_lng=117.327, origin_lat=34.812,
            destination_lng=117.335, destination_lat=34.820,
        )
        result = service.plan_route(request)

        assert result.used_amap is True
        assert result.to_dict()["data_sources"]["amap"] is True

    def test_mock_routes_do_not_report_amap(self):
        class FakeMockRepo:
            def fetch_routes(self, request):
                route = Route(name="Mock Route", source="mock")
                route.segments = [
                    RouteSegment(
                        request.origin,
                        request.destination,
                        "向前行驶",
                        RoadType.SIDEWALK,
                        120,
                        90,
                    )
                ]
                route.polyline = [request.origin, request.destination]
                route.compute_total_distance()
                route.count_turns()
                return [route]

        service = RoutePlanningService(
            route_repo=FakeMockRepo(),
            obstacle_repo=self.obstacle_repo,
        )
        request = RoutePlanningRequest(
            origin_lng=117.327, origin_lat=34.812,
            destination_lng=117.335, destination_lat=34.820,
        )
        result = service.plan_route(request)

        assert result.used_amap is False
        assert result.to_dict()["data_sources"]["amap"] is False

    def test_obstacle_report_affects_planning(self):
        """上报障碍物后应影响规划结果。"""
        # 先规划一次（无障碍物）
        request = RoutePlanningRequest(
            origin_lng=117.327, origin_lat=34.812,
            destination_lng=117.335, destination_lat=34.820,
        )
        result_no_obs = self.service.plan_route(request)
        best_score_no_obs = result_no_obs.best_route.score

        # 上报大量障碍物
        for i in range(10):
            obs = Obstacle(
                device_id="test",
                location=GeoPoint(117.327 + i * 0.0005, 34.812 + i * 0.0005),
                obstacle_type=ObstacleType.POLE,
                severity=Severity.HIGH,
                confidence=0.9,
            )
            self.obstacle_repo.add(obs)

        # 再次规划
        result_with_obs = self.service.plan_route(request)

        assert result_with_obs.obstacle_hotspot_count > 0, "应有障碍物热点"
        # 有障碍物时最优路线的障碍物密度应大于0
        assert result_with_obs.best_route.obstacle_density > 0 or \
               result_with_obs.score_breakdown["raw_metrics"]["obstacle_density_per_km"] > 0, \
               "上报障碍物后路线的障碍物密度应大于0"

    def test_invalid_request_raises(self):
        """无效请求应抛出异常。"""
        # 起终点相同
        request = RoutePlanningRequest(
            origin_lng=117.327, origin_lat=34.812,
            destination_lng=117.327, destination_lat=34.812,
        )
        try:
            self.service.plan_route(request)
            assert False, "应抛出ValueError"
        except ValueError:
            pass

    def test_zero_coordinates_rejected(self):
        """零坐标请求应被拒绝。"""
        request = RoutePlanningRequest(
            origin_lng=0, origin_lat=0,
            destination_lng=117.335, destination_lat=34.820,
        )
        try:
            self.service.plan_route(request)
            assert False, "应抛出ValueError"
        except ValueError:
            pass


if __name__ == "__main__":
    test = TestRoutePlanningService()
    test.setup_method(); test.test_service_initialization()
    print("✓ 服务初始化测试通过")
    test.setup_method(); test.test_plan_route_returns_result()
    print("✓ 路线规划返回结果测试通过")
    test.setup_method(); test.test_best_route_has_score()
    print("✓ 最优路线分数测试通过")
    test.setup_method(); test.test_routes_sorted_by_score()
    print("✓ 路线排序测试通过")
    test.setup_method(); test.test_result_contains_required_fields()
    print("✓ 结果字段完整性测试通过")
    test.setup_method(); test.test_obstacle_report_affects_planning()
    print("✓ 障碍物影响规划测试通过")
    test.setup_method(); test.test_invalid_request_raises()
    print("✓ 无效请求异常测试通过")
    test.setup_method(); test.test_zero_coordinates_rejected()
    print("✓ 零坐标拒绝测试通过")
    print("\n所有路线规划服务测试通过！")
