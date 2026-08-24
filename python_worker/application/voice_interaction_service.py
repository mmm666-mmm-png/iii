"""Application service for voice interaction and navigation intent parsing."""

from __future__ import annotations

import logging
from typing import Dict, Optional, Tuple

from infrastructure.config import AppConfig
from infrastructure.qwen_voice_client import QwenVoiceClient

from .dtos.route_dtos import RoutePlanningRequest, VoiceCommandRequest

logger = logging.getLogger(__name__)


class VoiceInteractionService:
    """Parse voice text and build route-planning requests."""

    def __init__(
        self,
        voice_client: Optional[QwenVoiceClient] = None,
        config: Optional[AppConfig] = None,
    ):
        self.config = config or AppConfig.from_env()
        self.voice_client = voice_client or QwenVoiceClient(self.config.qwen)
        self._location_cache: Dict[str, Tuple[float, float]] = {
            "枣庄学院": (117.327, 34.812),
            "枣庄站": (117.286, 34.798),
            "枣庄西站": (117.235, 34.856),
            "万达广场": (117.315, 34.805),
            "市立医院": (117.308, 34.818),
            "高铁站": (117.286, 34.798),
        }

    def process_command(self, request: VoiceCommandRequest) -> Dict:
        """Parse ASR text and either chat or build a navigation request."""
        logger.info("processing voice command: %s", request.text)

        intent_result = self.voice_client.parse_navigation_intent(request.text)
        intent = intent_result.get("intent", "other")

        if intent != "navigation":
            return {
                "intent": intent,
                "response_text": self._generate_chat_response(request.text, intent_result),
                "navigation_request": None,
                "need_planning": False,
            }

        return self.create_navigation_request(
            origin_desc=intent_result.get("origin"),
            destination_desc=intent_result.get("destination"),
            user_id=request.user_id,
            device_id=request.device_id,
            preferences=intent_result.get("preferences", {}),
        )

    def create_navigation_request(
        self,
        origin_desc: Optional[str],
        destination_desc: Optional[str],
        user_id: str = "",
        device_id: str = "",
        preferences: Optional[Dict] = None,
    ) -> Dict:
        """Resolve place names into a route-planning request."""
        preferences = preferences or {}
        origin_desc = (origin_desc or "").strip()
        destination_desc = (destination_desc or "").strip()

        if not destination_desc:
            return {
                "intent": "navigation",
                "response_text": "请告诉我目的地名称。",
                "navigation_request": None,
                "need_planning": False,
                "origin_desc": origin_desc,
                "destination_desc": "",
            }

        origin_is_current = not origin_desc or origin_desc in {
            "当前位置",
            "我当前位置",
            "我现在的位置",
            "当前地点",
            "现在位置",
        }

        # 并行解析起终点坐标，减少高德 geocode 的串行网络等待
        from concurrent.futures import ThreadPoolExecutor

        with ThreadPoolExecutor(max_workers=2) as executor:
            dest_future = executor.submit(self._geocode, destination_desc)
            origin_future = (
                None if origin_is_current else executor.submit(self._geocode, origin_desc)
            )
            dest_lng, dest_lat = dest_future.result(timeout=20)
            if origin_future is not None:
                origin_lng, origin_lat = origin_future.result(timeout=20)
            else:
                origin_desc = "当前位置"
                origin_lng, origin_lat = 117.327, 34.812

        if not origin_is_current and origin_lng == 0 and origin_lat == 0:
            return {
                "intent": "navigation",
                "response_text": f"抱歉，我没有找到「{origin_desc}」的位置，请换一种起点说法。",
                "navigation_request": None,
                "need_planning": False,
                "origin_desc": origin_desc,
                "destination_desc": destination_desc,
            }

        if dest_lng == 0 and dest_lat == 0:
            return {
                "intent": "navigation",
                "response_text": f"抱歉，我没有找到「{destination_desc}」的位置，请换一种说法。",
                "navigation_request": None,
                "need_planning": False,
                "origin_desc": origin_desc,
                "destination_desc": destination_desc,
            }

        nav_request = RoutePlanningRequest(
            origin_lng=origin_lng,
            origin_lat=origin_lat,
            destination_lng=dest_lng,
            destination_lat=dest_lat,
            origin_name=origin_desc,
            destination_name=destination_desc,
            user_id=user_id,
            device_id=device_id,
            preferences=preferences,
        )

        return {
            "intent": "navigation",
            "response_text": f"好的，正在为您规划从{origin_desc}到{destination_desc}的盲道友好路线，请稍候。",
            "navigation_request": nav_request,
            "need_planning": True,
            "origin_desc": origin_desc,
            "destination_desc": destination_desc,
        }

    def _geocode(self, location_name: str) -> Tuple[float, float]:
        """Geocode a location name with local cache and Amap fallback."""
        if not location_name:
            return 0.0, 0.0

        for name, coords in self._location_cache.items():
            if name in location_name or location_name in name:
                return coords

        try:
            from infrastructure.config import AmapConfig

            config = AmapConfig.from_env()
            if config.api_key:
                return self._call_amap_geocode(location_name, config)
        except Exception as exc:
            logger.warning("geocode lookup failed: %s", exc)

        return 0.0, 0.0

    def _call_amap_geocode(self, address: str, config) -> Tuple[float, float]:
        """Call Amap geocode API (with city hint to avoid cross-city matches)."""
        import json
        from urllib.parse import urlencode
        from urllib.request import Request, urlopen

        params = {
            "key": config.api_key,
            "address": address,
            "output": "json",
        }
        # 无城市限定会让“东湖公园”这类常见地名解析到外省（如南昌东湖区），
        # 导致步行路线距离超限返回 OVER_DIRECTION_RANGE。加上城市限定后落到本地。
        city = getattr(config, "city", "") or ""
        if city:
            params["city"] = city

        url = f"https://restapi.amap.com/v3/geocode/geo?{urlencode(params)}"
        req = Request(url, headers={"User-Agent": "AIGlass-Navigation/1.0"})

        with urlopen(req, timeout=config.timeout_seconds) as resp:
            data = json.loads(resp.read().decode("utf-8"))

        if data.get("status") == "1" and data.get("geocodes"):
            location = data["geocodes"][0].get("location", "")
            if "," in location:
                lng, lat = location.split(",")
                return float(lng), float(lat)

        # 带城市限定失败时，退回不带城市的解析，保证至少能拿到一个坐标。
        if city:
            return self._call_amap_geocode_no_city(address, config)

        return 0.0, 0.0

    def _call_amap_geocode_no_city(self, address: str, config) -> Tuple[float, float]:
        """Geocode without a city hint, used as a fallback."""
        import json
        from urllib.parse import urlencode
        from urllib.request import Request, urlopen

        params = {
            "key": config.api_key,
            "address": address,
            "output": "json",
        }
        url = f"https://restapi.amap.com/v3/geocode/geo?{urlencode(params)}"
        req = Request(url, headers={"User-Agent": "AIGlass-Navigation/1.0"})

        with urlopen(req, timeout=config.timeout_seconds) as resp:
            data = json.loads(resp.read().decode("utf-8"))

        if data.get("status") == "1" and data.get("geocodes"):
            location = data["geocodes"][0].get("location", "")
            if "," in location:
                lng, lat = location.split(",")
                return float(lng), float(lat)

        return 0.0, 0.0

    def _generate_chat_response(self, text: str, intent_result: Dict) -> str:
        """Generate a normal chat response when the text is not navigation."""
        intent = intent_result.get("intent", "other")
        if intent == "query":
            return "我可以帮您规划盲道友好的步行路线，您可以说「导航到XX」或「去XX」。"
        return "我是您的盲道导航助手，您可以说「导航到XX」来开始路线规划。"
