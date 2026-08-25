"""
应用服务 —— GPS 实时定位导航触发

前端手机浏览器通过「开启定位」开关上报实时经纬度（navigator.geolocation），
Go 后端转发到 python worker 的 /api/gps/update，本服务负责：

1. 保存最近一次规划的路线路段（每段含 start/end 坐标与 instruction）；
2. 用实时坐标匹配当前所在路段；
3. 进入下一路段时触发 RouteBroadcastService 把该段指令合成语音播报。

坐标说明：手机 navigator.geolocation 返回 WGS-84 坐标，高德路线为 GCJ-02，
两者在中国境内存在几百米级偏移。update_position 会先把 WGS-84 转为 GCJ-02
（见 domain.model.route.wgs84_to_gcj02）再与路段匹配；匹配阈值只用于吸收
手机定位本身的精度误差，若定位明显偏离路线则忽略，不触发播报。
"""
from __future__ import annotations

import logging
import threading
import time
from typing import Any, Dict, List, Optional, Tuple

from domain.model.route import GeoPoint, point_to_segment_distance, wgs84_to_gcj02
from application.route_broadcast_service import RouteBroadcastService

logger = logging.getLogger(__name__)


class GpsNavigationTrigger:
    """把实时 GPS 坐标匹配到已规划路线路段，进入新路段时触发语音播报。"""

    # 偏离路线超过该距离（米）时视为偏离，不推进路段，避免误播报。
    MATCH_MAX_DISTANCE_M = 80.0

    # —— GPS 异常/断连容错参数 ——
    GPS_STALE_TIMEOUT_S = 15.0    # 超过该秒数未收到手机定位视为断连
    GPS_MAX_ACCURACY_M = 100.0    # 定位精度超过该值视为低可信（仍尝试匹配，仅告警）
    GPS_JUMP_MAX_M = 300.0        # 相邻两次定位跳变超过该距离视为异常漂移（仅告警）
    GPS_OFFROUTE_WARN_COUNT = 5   # 连续偏离路线达到该次数打印一次警告

    def __init__(self, broadcast_service: Optional[RouteBroadcastService] = None):
        self._broadcast = broadcast_service or RouteBroadcastService()
        self._lock = threading.Lock()
        self._segments: List[Dict[str, Any]] = []
        self._announced_index = -1
        self._last_position: Optional[Dict[str, Any]] = None
        self._paused = False
        self._blind_path_coverage: Optional[float] = None

        # —— GPS 连接/容错状态 ——
        self._last_gps_ts: Optional[float] = None
        self._gps_connected: bool = False
        self._offroute_count: int = 0
        self._watchdog_thread: Optional[threading.Thread] = None
        self._watchdog_stop = threading.Event()

    # ----- 路线注册 -----
    def set_route(
        self,
        segments: List[Dict[str, Any]],
        blind_path_coverage: Optional[float] = None,
    ) -> None:
        """注册最新路线路段，并重置已播报进度。

        segments 每项需含 start/end 坐标与 instruction，例如::

            {
                "index": 1,
                "instruction": "从起点向东北方向出发",
                "distance_meters": 120.0,
                "start": {"lng": 117.5, "lat": 34.8},
                "end": {"lng": 117.51, "lat": 34.81},
            }

        blind_path_coverage: 本条路线的盲道覆盖率（0~1），用于首次进入路线时播报。
        """
        cleaned: List[Dict[str, Any]] = []
        for seg in segments or []:
            start = seg.get("start") or {}
            end = seg.get("end") or {}
            if not isinstance(start, dict) or not isinstance(end, dict):
                continue
            if "lng" not in start or "lat" not in start or "lng" not in end or "lat" not in end:
                continue
            cleaned.append(
                {
                    "index": seg.get("index", len(cleaned) + 1),
                    "instruction": str(seg.get("instruction") or "请继续直行").strip(),
                    "start": {"lng": float(start["lng"]), "lat": float(start["lat"])},
                    "end": {"lng": float(end["lng"]), "lat": float(end["lat"])},
                    "distance_meters": float(seg.get("distance_meters") or 0.0),
                    "road_type": str(seg.get("road_type") or ""),
                }
            )
        with self._lock:
            self._segments = cleaned
            self._announced_index = -1
            self._last_position = None
            self._blind_path_coverage = blind_path_coverage
        logger.info("GPS 导航触发已注册路线，共 %d 段", len(cleaned))

    def clear(self) -> None:
        """清空当前路线，停止 GPS 触发播报。

        注意：只清路线与偏离计数，不清 GPS 连接状态（手机仍在持续上传定位）。
        """
        with self._lock:
            self._segments = []
            self._announced_index = -1
            self._last_position = None
            self._paused = False
            self._offroute_count = 0
            self._blind_path_coverage = None

    def set_paused(self, paused: bool) -> None:
        """暂停/恢复导航播报（只暂停语音输出，不销毁路线与进度追踪）。

        暂停期间继续用 GPS 匹配、追踪位置，但不推进已播报路段、不触发播报；
        恢复后由下一次定位补齐当前所在路段。
        """
        with self._lock:
            self._paused = bool(paused)

    def is_paused(self) -> bool:
        with self._lock:
            return self._paused

    # ----- 位置更新 -----
    def update_position(
        self, lng: float, lat: float, accuracy: Optional[float] = None
    ) -> Dict[str, Any]:
        """处理一次实时定位，返回匹配与触发信息。

        手机 geolocation 上报的是 WGS-84，高德路线为 GCJ-02，这里先做
        WGS-84→GCJ-02 转换，消除坐标系差异带来的几百米级偏移，再做路段匹配。

        容错：非法/越界坐标直接拒绝；精度过低、跳变过大只告警不中断；
        连续偏离路线累计告警；断连由 check_gps_stale()/watchdog 判定。
        """
        # 1) 坐标合法性校验
        try:
            lng = float(lng)
            lat = float(lat)
        except (TypeError, ValueError):
            return {"matched": None, "reason": "invalid_coord", "off_route": False, "triggered": []}
        if not (-180.0 <= lng <= 180.0 and -90.0 <= lat <= 90.0):
            logger.warning("GPS 坐标越界，已忽略: lng=%s, lat=%s", lng, lat)
            return {"matched": None, "reason": "out_of_range", "off_route": False, "triggered": []}

        now = time.time()

        # 2) 断连恢复检测：距上次更新过久，提示信号恢复
        with self._lock:
            last_ts = self._last_gps_ts
        if last_ts is not None and (now - last_ts) > self.GPS_STALE_TIMEOUT_S:
            logger.warning("GPS 信号恢复：距上次定位已 %.1f 秒", now - last_ts)
        with self._lock:
            self._last_gps_ts = now
            self._gps_connected = True

        # 3) 精度告警（仍尝试匹配，避免漏报）
        if accuracy is not None and accuracy > self.GPS_MAX_ACCURACY_M:
            logger.warning("GPS 精度过低：%.0f 米（阈值 %.0f 米）", accuracy, self.GPS_MAX_ACCURACY_M)

        try:
            gcj_lng, gcj_lat = wgs84_to_gcj02(lng, lat)
            point = GeoPoint(gcj_lng, gcj_lat)
        except (TypeError, ValueError):
            return {"matched": None, "reason": "invalid_coord", "off_route": False, "triggered": []}

        # 4) 跳变/漂移告警（与上一次定位距离对比，不影响本次匹配）
        prev = None
        with self._lock:
            prev = dict(self._last_position) if self._last_position else None
            self._last_position = {"lng": point.lng, "lat": point.lat, "accuracy": accuracy}
        jumped = False
        if prev and "lng" in prev and "lat" in prev:
            try:
                jump_m = point.distance_to(GeoPoint(float(prev["lng"]), float(prev["lat"])))
                if jump_m > self.GPS_JUMP_MAX_M:
                    jumped = True
                    logger.warning("GPS 异常跳变：本次与上次相距 %.0f 米", jump_m)
            except (TypeError, ValueError):
                pass

        with self._lock:
            segments = list(self._segments)

        if not segments:
            return {
                "matched": None,
                "reason": "no_route",
                "distance_m": None,
                "off_route": False,
                "triggered": [],
            }

        best_index, best_distance, _ = self._match_segment(point, segments)

        result: Dict[str, Any] = {
            "matched": best_index,
            "distance_m": round(best_distance, 1) if best_distance is not None else None,
            "off_route": best_distance is None or best_distance > self.MATCH_MAX_DISTANCE_M,
            "triggered": [],
            "jumped": jumped,
        }

        # 5) 偏离路线累计告警
        if result["off_route"]:
            with self._lock:
                self._offroute_count += 1
                count = self._offroute_count
            if count == self.GPS_OFFROUTE_WARN_COUNT:
                logger.warning("GPS 已连续 %d 次偏离规划路线，请确认是否走错或定位漂移", count)
        else:
            with self._lock:
                self._offroute_count = 0

        if best_index is None or result["off_route"]:
            return result

        to_announce: List[int] = []
        first_announce = False
        with self._lock:
            if best_index > self._announced_index:
                if self._paused:
                    # 暂停期间不推进、不播报，恢复后由下一次定位补齐当前路段。
                    result["paused"] = True
                    return result
                first_announce = self._announced_index < 0
                start_index = self._announced_index + 1
                self._announced_index = best_index
                to_announce = list(range(start_index, best_index + 1))

        if to_announce:
            steps = [
                {
                    "index": segments[i]["index"],
                    "instruction": segments[i]["instruction"],
                    "road_type": segments[i].get("road_type", ""),
                }
                for i in to_announce
                if 0 <= i < len(segments)
            ]
            result["triggered"] = steps
            # 首次进入路线时，先播报本条路线的盲道覆盖情况
            if first_announce:
                self._broadcast_blind_coverage()
            for step in steps:
                self._broadcast_step(step)

        return result

    # ----- 内部工具 -----
    def _match_segment(
        self, point: GeoPoint, segments: List[Dict[str, Any]]
    ) -> Tuple[Optional[int], Optional[float], float]:
        """返回 (最近路段索引, 到该路段最短距离, 投影比例 t)。"""
        best_index: Optional[int] = None
        best_distance = float("inf")
        best_t = 0.0
        for i, seg in enumerate(segments):
            start = seg.get("start") or {}
            end = seg.get("end") or {}
            try:
                a = GeoPoint(float(start["lng"]), float(start["lat"]))
                b = GeoPoint(float(end["lng"]), float(end["lat"]))
            except (KeyError, TypeError, ValueError):
                continue
            distance, t = point_to_segment_distance(point, a, b)
            if distance < best_distance:
                best_index, best_distance, best_t = i, distance, t
        if best_index is None:
            return None, None, 0.0
        return best_index, best_distance, best_t

    def _broadcast_step(self, step: Dict[str, Any]) -> None:
        """把单个路段指令合成语音并播报（后台线程，不阻塞定位处理）。"""
        try:
            self._broadcast.broadcast_steps([step])
        except Exception as exc:  # noqa: BLE001
            logger.error("GPS 导航触发播报失败: %s", exc)

    def _broadcast_blind_coverage(self) -> None:
        """首次进入路线时播报本条路线的盲道覆盖情况（后台线程）。"""
        with self._lock:
            coverage = self._blind_path_coverage
        if coverage is None:
            return
        pct = coverage * 100
        if pct >= 60:
            text = f"本条路线盲道覆盖率达{pct:.0f}%，盲道连续性好，请沿盲道行走。"
        elif pct >= 30:
            text = f"本条路线约有{pct:.0f}%的路段铺有盲道，部分路段请借助人行道。"
        else:
            text = "本条路线盲道覆盖较少，请借助盲杖和路人协助。"
        try:
            self._broadcast.broadcast_text(text)
        except Exception as exc:  # noqa: BLE001
            logger.error("盲道覆盖情况播报失败: %s", exc)

    # ----- GPS 断连/健康检查 -----
    def is_gps_connected(self) -> bool:
        """当前是否处于 GPS 已连接状态（最近是否收到过定位）。"""
        with self._lock:
            return self._gps_connected

    def last_position(self) -> Optional[Dict[str, Any]]:
        """返回最近一次手机定位（已转 GCJ-02），无定位时返回 None。

        规划路线时用它作为起点；返回 dict 含 lng/lat/accuracy。
        """
        with self._lock:
            if not self._last_position:
                return None
            return dict(self._last_position)

    def check_gps_stale(
        self, now: Optional[float] = None, timeout_s: Optional[float] = None
    ) -> bool:
        """检查 GPS 是否超时断连，返回 True 表示当前断连。

        断连/恢复的状态变化会打印日志；从未收到过定位时返回 False（不判定断连）。
        """
        timeout_s = timeout_s if timeout_s is not None else self.GPS_STALE_TIMEOUT_S
        now = now if now is not None else time.time()
        with self._lock:
            last = self._last_gps_ts
            connected = self._gps_connected
        if last is None:
            return False
        stale = (now - last) > timeout_s
        if stale and connected:
            with self._lock:
                self._gps_connected = False
            logger.warning("GPS 信号丢失：已 %.1f 秒未收到手机定位（阈值 %.0f 秒）", now - last, timeout_s)
        elif not stale and not connected:
            with self._lock:
                self._gps_connected = True
            logger.info("GPS 信号恢复")
        return stale

    def gps_health(self) -> Dict[str, Any]:
        """返回 GPS 连接/容错状态摘要，供接口或日志使用。"""
        with self._lock:
            last_ts = self._last_gps_ts
            connected = self._gps_connected
            offroute = self._offroute_count
        return {
            "connected": connected,
            "last_update_at": last_ts,
            "seconds_since_update": round(time.time() - last_ts, 1) if last_ts else None,
            "off_route_streak": offroute,
            "stale_timeout_s": self.GPS_STALE_TIMEOUT_S,
        }

    def start_watchdog(self, interval_s: float = 5.0) -> None:
        """启动后台线程，周期性检查 GPS 断连并在日志中告警。"""
        if self._watchdog_thread and self._watchdog_thread.is_alive():
            return
        self._watchdog_stop.clear()
        self._watchdog_thread = threading.Thread(
            target=self._watchdog_loop, args=(interval_s,), daemon=True, name="gps-watchdog"
        )
        self._watchdog_thread.start()

    def _watchdog_loop(self, interval_s: float) -> None:
        while not self._watchdog_stop.is_set():
            self._watchdog_stop.wait(interval_s)
            if self._watchdog_stop.is_set():
                break
            try:
                self.check_gps_stale()
            except Exception as exc:  # noqa: BLE001
                logger.error("GPS 断连检测异常: %s", exc)

    def stop_watchdog(self) -> None:
        """停止断连检测后台线程。"""
        self._watchdog_stop.set()


# 进程级单例，供路由端点与 navigation_app 共享。
_trigger: Optional[GpsNavigationTrigger] = None


def get_gps_trigger() -> GpsNavigationTrigger:
    """返回进程级 GPS 触发服务单例。"""
    global _trigger
    if _trigger is None:
        _trigger = GpsNavigationTrigger()
        _trigger.start_watchdog()
    return _trigger
