"""
run_all_tests.py —— 统一测试运行入口

运行所有DDD架构相关的单元测试和集成测试。
用法: python run_all_tests.py
"""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

def run_test_module(module_name, test_class_name, tests):
    """运行一个测试模块的所有测试。"""
    print(f"\n{'='*60}")
    print(f"  运行: {module_name}")
    print(f"{'='*60}")
    try:
        module = __import__(module_name, fromlist=[test_class_name])
        test_class = getattr(module, test_class_name)
        test = test_class()
        passed = 0
        failed = 0
        for test_name in tests:
            try:
                test.setup_method()
                getattr(test, test_name)()
                print(f"  ✓ {test_name}")
                passed += 1
            except Exception as e:
                print(f"  ✗ {test_name}: {e}")
                failed += 1
        print(f"\n  结果: {passed} 通过, {failed} 失败")
        return failed == 0
    except Exception as e:
        print(f"  模块导入失败: {e}")
        return False


def main():
    all_passed = True

    # 1. 路线打分引擎测试
    all_passed &= run_test_module(
        "tests.test_route_scoring",
        "TestRouteScoringService",
        [
            "test_weight_normalization",
            "test_single_route_scoring",
            "test_blind_path_route_scores_higher",
            "test_route_ranking",
            "test_obstacle_density_affects_score",
            "test_blind_path_coverage_calculation",
            "test_score_breakdown_dict",
        ]
    )

    # 2. 障碍物聚合测试
    all_passed &= run_test_module(
        "tests.test_obstacle_aggregator",
        "TestObstacleAggregator",
        [
            "test_single_report_creates_hotspot",
            "test_nearby_same_type_merges",
            "test_different_type_no_merge",
            "test_distant_same_type_no_merge",
            "test_time_decay",
            "test_expired_hotspots_purged",
            "test_severity_weight",
            "test_get_hotspots_near",
            "test_clear",
        ]
    )

    # 3. 路线规划服务集成测试
    all_passed &= run_test_module(
        "tests.test_route_planning_service",
        "TestRoutePlanningService",
        [
            "test_service_initialization",
            "test_plan_route_returns_result",
            "test_best_route_has_score",
            "test_routes_sorted_by_score",
            "test_result_contains_required_fields",
            "test_obstacle_report_affects_planning",
            "test_invalid_request_raises",
            "test_zero_coordinates_rejected",
        ]
    )

    print(f"\n{'='*60}")
    if all_passed:
        print("  所有测试通过！")
    else:
        print("  部分测试失败，请检查上方输出。")
    print(f"{'='*60}")

    return 0 if all_passed else 1


if __name__ == "__main__":
    sys.exit(main())
