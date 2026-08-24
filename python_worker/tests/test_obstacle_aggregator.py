"""
单元测试 —— 障碍物时空聚合器

验证:
1. 单条上报创建热点
2. 相近位置同类型上报合并为同一热点
3. 不同类型上报不合并
4. 时间衰减正确
5. 过期热点被清理
6. 热点权重计算正确
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from datetime import datetime, timedelta

from domain.model.obstacle import Obstacle, ObstacleType, Severity
from domain.model.route import GeoPoint
from domain.services.obstacle_aggregator import ObstacleAggregator, AggregationConfig


def make_obstacle(lng, lat, otype=ObstacleType.POLE, severity=Severity.MEDIUM,
                  confidence=0.8, timestamp=None):
    return Obstacle(
        device_id="test_device",
        location=GeoPoint(lng, lat),
        obstacle_type=otype,
        severity=severity,
        confidence=confidence,
        timestamp=timestamp or datetime.utcnow(),
    )


class TestObstacleAggregator:
    """障碍物聚合器测试。"""

    def setup_method(self):
        self.aggregator = ObstacleAggregator()

    def test_single_report_creates_hotspot(self):
        """单条上报应创建一个热点。"""
        obs = make_obstacle(117.327, 34.812)
        hotspot = self.aggregator.add_report(obs)

        assert hotspot.report_count == 1
        assert hotspot.obstacle_type == ObstacleType.POLE
        assert hotspot.location.lng == 117.327

    def test_nearby_same_type_merges(self):
        """相近位置同类型上报应合并。"""
        now = datetime.utcnow()
        obs1 = make_obstacle(117.327, 34.812, timestamp=now)
        obs2 = make_obstacle(117.32705, 34.81205, timestamp=now)  # 约5米外

        h1 = self.aggregator.add_report(obs1)
        h2 = self.aggregator.add_report(obs2)

        assert h1.hotspot_id == h2.hotspot_id, "相近同类型应合并为同一热点"
        assert h2.report_count == 2

    def test_different_type_no_merge(self):
        """不同类型上报不应合并。"""
        now = datetime.utcnow()
        obs1 = make_obstacle(117.327, 34.812, otype=ObstacleType.POLE, timestamp=now)
        obs2 = make_obstacle(117.327, 34.812, otype=ObstacleType.TREE, timestamp=now)

        self.aggregator.add_report(obs1)
        self.aggregator.add_report(obs2)

        hotspots = self.aggregator.get_active_hotspots(now)
        assert len(hotspots) == 2, "不同类型应是两个热点"

    def test_distant_same_type_no_merge(self):
        """远距离同类型上报不应合并。"""
        now = datetime.utcnow()
        obs1 = make_obstacle(117.327, 34.812, timestamp=now)
        obs2 = make_obstacle(117.340, 34.820, timestamp=now)  # 很远

        self.aggregator.add_report(obs1)
        self.aggregator.add_report(obs2)

        hotspots = self.aggregator.get_active_hotspots(now)
        assert len(hotspots) == 2, "远距离应是两个热点"

    def test_time_decay(self):
        """旧热点权重应随时间衰减。"""
        old_time = datetime.utcnow() - timedelta(seconds=120)  # 2分钟前（半衰期）
        obs = make_obstacle(117.327, 34.812, timestamp=old_time)
        self.aggregator.add_report(obs)

        now = datetime.utcnow()
        hotspots = self.aggregator.get_active_hotspots(now)
        assert len(hotspots) == 1
        # 经过一个半衰期，权重应减半左右
        base_weight = Severity.weight(Severity.MEDIUM) * 1 * 0.8
        assert hotspots[0].aggregate_weight < base_weight, \
            f"衰减后权重({hotspots[0].aggregate_weight})应小于基础权重({base_weight})"

    def test_expired_hotspots_purged(self):
        """超过时间窗口的热点应被清理。"""
        config = AggregationConfig(time_window_seconds=60)  # 1分钟窗口
        agg = ObstacleAggregator(config)

        old_time = datetime.utcnow() - timedelta(seconds=120)  # 2分钟前，已过期
        obs = make_obstacle(117.327, 34.812, timestamp=old_time)
        agg.add_report(obs)

        now = datetime.utcnow()
        hotspots = agg.get_active_hotspots(now)
        assert len(hotspots) == 0, "过期热点应被清理"

    def test_severity_weight(self):
        """高严重程度热点权重应更高。"""
        now = datetime.utcnow()
        high_obs = make_obstacle(117.327, 34.812, severity=Severity.HIGH, timestamp=now)
        low_obs = make_obstacle(117.328, 34.813, severity=Severity.LOW, timestamp=now)

        h_high = self.aggregator.add_report(high_obs)
        h_low = self.aggregator.add_report(low_obs)

        assert h_high.aggregate_weight > h_low.aggregate_weight, \
            "高严重程度权重应更高"

    def test_get_hotspots_near(self):
        """测试按坐标范围查询热点。"""
        now = datetime.utcnow()
        self.aggregator.add_report(make_obstacle(117.327, 34.812, timestamp=now))
        self.aggregator.add_report(make_obstacle(117.340, 34.820, timestamp=now))

        # 查询第一个点附近
        near = self.aggregator.get_hotspots_near(GeoPoint(117.327, 34.812), 100.0)
        assert len(near) == 1

        # 查询大范围
        all_near = self.aggregator.get_hotspots_near(GeoPoint(117.327, 34.812), 5000.0)
        assert len(all_near) == 2

    def test_clear(self):
        """测试清空。"""
        self.aggregator.add_report(make_obstacle(117.327, 34.812))
        assert len(self.aggregator.get_active_hotspots()) == 1

        self.aggregator.clear()
        assert len(self.aggregator.get_active_hotspots()) == 0


if __name__ == "__main__":
    test = TestObstacleAggregator()
    test.setup_method(); test.test_single_report_creates_hotspot()
    print("✓ 单条上报创建热点测试通过")
    test.setup_method(); test.test_nearby_same_type_merges()
    print("✓ 相近同类型合并测试通过")
    test.setup_method(); test.test_different_type_no_merge()
    print("✓ 不同类型不合并测试通过")
    test.setup_method(); test.test_distant_same_type_no_merge()
    print("✓ 远距离不合并测试通过")
    test.setup_method(); test.test_time_decay()
    print("✓ 时间衰减测试通过")
    test.setup_method(); test.test_expired_hotspots_purged()
    print("✓ 过期热点清理测试通过")
    test.setup_method(); test.test_severity_weight()
    print("✓ 严重程度权重测试通过")
    test.setup_method(); test.test_get_hotspots_near()
    print("✓ 范围查询测试通过")
    test.setup_method(); test.test_clear()
    print("✓ 清空测试通过")
    print("\n所有障碍物聚合测试通过！")
