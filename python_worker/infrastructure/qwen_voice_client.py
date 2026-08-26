"""
基础设施 —— 通义千问大模型语音交互客户端

负责:
1. 语音交互: 理解用户导航意图（起点、终点、偏好）
2. 结果播报: 将路线规划结果转换为自然语言
3. 文本转语音: 调用DashScope TTS生成语音

基于DashScope OpenAI兼容接口实现。
"""
from __future__ import annotations

import json
import logging
import os
from typing import Dict, List, Optional, Tuple

from domain.model.route import Route
from domain.model.navigation_request import NavigationRequest
from .config import QwenConfig

logger = logging.getLogger(__name__)


# 道路类型 → 中文播报名称（用于路线状况描述）
_ROAD_TYPE_LABELS = {
    "blind_path": "盲道",
    "sidewalk": "人行道",
    "pedestrian": "步行街",
    "crosswalk": "斑马线",
    "underpass": "地下通道",
    "footbridge": "人行天桥",
    "service": "服务道路",
    "unknown": "普通道路",
}


class QwenVoiceClient:
    """通义千问语音交互客户端。"""

    def __init__(self, config: Optional[QwenConfig] = None):
        self.config = config or QwenConfig.from_env()
        self._client = None

    def _get_client(self):
        """懒加载OpenAI兼容客户端。"""
        if self._client is None:
            try:
                from openai import OpenAI
                self._client = OpenAI(
                    api_key=self.config.api_key,
                    base_url=self.config.base_url,
                    timeout=self.config.timeout_seconds,
                )
            except ImportError:
                logger.warning("openai库未安装，大模型功能不可用")
                self._client = False
        return self._client if self._client is not False else None

    def parse_navigation_intent(self, user_text: str) -> Dict:
        """
        从用户语音文本中解析导航意图。

        返回:
          {
            "intent": "navigation" | "query" | "other",
            "origin": str or None,  # 起点描述
            "destination": str or None,  # 终点描述
            "preferences": dict,
            "raw_text": str
          }
        """
        client = self._get_client()
        if client is None or not self.config.api_key:
            return self._fallback_parse_intent(user_text)

        try:
            prompt = f"""你是一个盲人导航助手。请从用户话语中提取导航意图。
用户说: "{user_text}"

请严格按以下JSON格式返回（不要输出其他内容）:
{{
  "intent": "navigation" 或 "query" 或 "other",
  "origin": 起点地名或null,
  "destination": 终点地名或null,
  "preferences": {{"avoid_stairs": bool, "prefer_elevator": bool, "notes": "备注"}}
}}"""

            resp = client.chat.completions.create(
                model=self.config.model,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.1,
                response_format={"type": "json_object"},
            )
            content = resp.choices[0].message.content
            result = json.loads(content)
            result["raw_text"] = user_text
            return result
        except Exception as e:
            logger.error(f"大模型意图解析失败: {e}")
            return self._fallback_parse_intent(user_text)

    def generate_route_broadcast(
        self,
        best_route: Route,
        all_routes: List[Route],
        request: NavigationRequest,
    ) -> str:
        """
        将路线规划结果转换为自然语言播报文本。

        Args:
          best_route: 最优路线
          all_routes: 所有备选路线
          request: 原始导航请求

        Returns:
          适合语音播报的中文文本
        """
        client = self._get_client()
        if client is None or not self.config.api_key:
            return self._fallback_broadcast(best_route, all_routes)

        try:
            condition = self._route_condition_summary(best_route)
            route_info = {
                "总距离": f"{best_route.total_distance_meters:.0f}米",
                "预计时间": f"{best_route.total_duration_seconds // 60}分钟",
                "路线状况": {
                    "道路类型构成": condition["road_type_distribution"],
                    "转弯次数": condition["turn_count"],
                    "红绿灯数": condition["traffic_light_count"],
                },
                "盲道覆盖率": f"{best_route.blind_path_coverage * 100:.1f}%",
                "备选路线数": len(all_routes),
            }

            prompt = f"""你是一个盲人导航助手，请用温暖、清晰、简洁的语言播报导航路线。
路线信息: {json.dumps(route_info, ensure_ascii=False)}

要求:
1. 开头说明已为您规划好路线
2. 重点播报路线状况：沿途道路类型构成（盲道、人行道、斑马线、天桥等分别有多长）、转弯次数和红绿灯情况
3. 重点播报盲道覆盖情况：覆盖率高不高、是否适合盲杖行走
4. 结尾给出出发建议
5. 总字数控制在150字以内，适合语音播报
6. 不要使用markdown格式，直接输出纯文本"""

            resp = client.chat.completions.create(
                model=self.config.model,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.7,
                max_tokens=300,
            )
            return self._complete_route_broadcast(
                resp.choices[0].message.content.strip(), best_route, condition
            )
        except Exception as e:
            logger.error(f"大模型播报生成失败: {e}")
            return self._fallback_broadcast(best_route, all_routes)

    @staticmethod
    def _complete_route_broadcast(text: str, route: Route, condition: Dict) -> str:
        """补齐模型可能遗漏的高德和盲道关键信息，供两端共用同一段播报。"""
        parts = [str(text or "").strip().rstrip("。！？!?.")]
        blind_pct = route.blind_path_coverage * 100
        if "盲道覆盖" not in parts[0]:
            if blind_pct >= 60:
                parts.append(f"盲道覆盖率{blind_pct:.1f}%，盲道连续性较好，适合沿盲道行走")
            elif blind_pct >= 30:
                parts.append(f"盲道覆盖率{blind_pct:.1f}%，部分路段请借助人行道")
            else:
                parts.append(f"盲道覆盖率{blind_pct:.1f}%，覆盖不足，建议使用盲杖辅助行走")
        if "红绿灯" not in parts[0]:
            parts.append(
                f"全程{'无' if condition['traffic_light_count'] == 0 else '有'}红绿灯"
                + ("" if condition["traffic_light_count"] == 0 else f"{condition['traffic_light_count']}处")
            )
        if "转弯" not in parts[0]:
            parts.append(f"转弯{condition['turn_count']}次")
        if "建议" not in parts[0]:
            parts.append("建议白天出行，确保安全")
        return "。".join(part for part in parts if part) + "。"

    @staticmethod
    def _route_condition_summary(route: Route) -> Dict:
        """统计路线各道路类型的里程构成，生成路线状况摘要。"""
        distribution: Dict[str, float] = {}
        for seg in route.segments:
            label = _ROAD_TYPE_LABELS.get(seg.road_type, "普通道路")
            distribution[label] = distribution.get(label, 0.0) + seg.compute_distance()

        sorted_items = sorted(
            distribution.items(), key=lambda kv: kv[1], reverse=True
        )
        road_parts = [
            f"{label}约{dist:.0f}米" for label, dist in sorted_items if dist > 0
        ]
        return {
            "road_type_distribution": road_parts,
            "turn_count": route.turn_count,
            "traffic_light_count": route.traffic_light_count,
        }

    def generate_route_broadcast_en(
        self,
        best_route: Route,
        all_routes: List[Route],
        request: NavigationRequest,
        origin_name: str = "",
        destination_name: str = "",
    ) -> str:
        """Generate a concise English navigation broadcast."""
        client = self._get_client()
        if client is None or not self.config.api_key:
            return self._fallback_broadcast_en(best_route, all_routes, origin_name, destination_name)

        try:
            route_info = {
                "best_route": best_route.score_breakdown_dict(),
                "distance_m": f"{best_route.total_distance_meters:.0f}",
                "duration_min": f"{best_route.total_duration_seconds // 60}",
                "blind_coverage_pct": f"{best_route.blind_path_coverage * 100:.1f}",
                "obstacle_density_per_km": f"{best_route.obstacle_density:.1f}",
                "alternative_routes": len(all_routes),
                "origin_name": origin_name or "",
                "destination_name": destination_name or "",
            }

            prompt = f"""You are a navigation assistant for a blind traveler.
Write a short English voice broadcast for the selected walking route.
Route data: {json.dumps(route_info, ensure_ascii=False)}

Requirements:
1. Start by confirming the route is ready.
2. Mention distance and estimated duration.
3. Mention whether the route has good blind-path coverage.
4. Warn briefly about obstacle density if needed.
5. Keep it under 120 words.
6. Plain text only. No markdown.
7. Use natural English that sounds good in speech."""

            resp = client.chat.completions.create(
                model=self.config.model,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.5,
                max_tokens=220,
            )
            return resp.choices[0].message.content.strip()
        except Exception as e:
            logger.error(f"english route broadcast failed: {e}")
            return self._fallback_broadcast_en(best_route, all_routes, origin_name, destination_name)

    def generate_obstacle_warning(self, obstacle_type: str, distance: str) -> str:
        """生成障碍物预警语音文本。"""
        client = self._get_client()
        if client is None or not self.config.api_key:
            return f"前方{distance}处发现{obstacle_type}，请注意避让。"

        try:
            prompt = f"""你是盲人导航助手。检测到前方{distance}处有{obstacle_type}。
请生成一句简洁的语音预警（20字以内），语气紧迫但不恐慌。"""
            resp = client.chat.completions.create(
                model=self.config.model,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.5,
                max_tokens=50,
            )
            return resp.choices[0].message.content.strip()
        except Exception:
            return f"前方{distance}处发现{obstacle_type}，请注意避让。"

    def _fallback_broadcast_en(
        self,
        best_route: Route,
        all_routes: List[Route],
        origin_name: str = "",
        destination_name: str = "",
    ) -> str:
        """Fallback English navigation broadcast."""
        dist = best_route.total_distance_meters
        minutes = best_route.total_duration_seconds // 60
        blind_pct = best_route.blind_path_coverage * 100
        obs_density = best_route.obstacle_density
        origin_text = origin_name.strip() or "the starting point"
        destination_text = destination_name.strip() or "the destination"

        parts = [
            f"Navigation is ready from {origin_text} to {destination_text}.",
            f"The selected route is about {dist:.0f} meters and takes roughly {minutes} minutes.",
        ]

        if blind_pct >= 60:
            parts.append(f"Blind-path coverage is strong at about {blind_pct:.0f} percent.")
        elif blind_pct >= 30:
            parts.append(f"Blind-path coverage is moderate at about {blind_pct:.0f} percent.")
        else:
            parts.append("Blind-path coverage is limited, so please stay attentive.")

        if obs_density > 5:
            parts.append(f"Obstacle density is relatively high at about {obs_density:.0f} per kilometer.")
        elif obs_density > 2:
            parts.append("There are a few obstacles along the route.")
        else:
            parts.append("Obstacle density is low.")

        parts.append("Please follow the guidance carefully and stay safe.")
        return " ".join(parts)

    def _fallback_parse_intent(self, text: str) -> Dict:
        """无大模型时的简单意图解析。"""
        result = {
            "intent": "other",
            "origin": None,
            "destination": None,
            "preferences": {},
            "raw_text": text,
        }

        if any(kw in text for kw in ["导航", "去", "到", "怎么走", "路线", "带路"]):
            result["intent"] = "navigation"
            # 简单提取"从X到Y"或"去X"
            if "到" in text:
                parts = text.split("到", 1)
                result["destination"] = parts[1].strip("，。！？、 ")
            elif "去" in text:
                parts = text.split("去", 1)
                result["destination"] = parts[1].strip("，。！？、 ")

        return result

    def _fallback_broadcast(self, best_route: Route, all_routes: List[Route]) -> str:
        """无大模型时的模板播报（重点：路线状况 + 盲道覆盖情况）。"""
        dist = best_route.total_distance_meters
        minutes = best_route.total_duration_seconds // 60
        blind_pct = best_route.blind_path_coverage * 100

        condition = self._route_condition_summary(best_route)
        road_parts = condition["road_type_distribution"]

        parts = [f"已为您规划好最优路线，全程约{dist:.0f}米，预计{minutes}分钟。"]

        # 路线状况
        if road_parts:
            parts.append("沿途路况：" + "，".join(road_parts[:4]) + "。")
        if condition["turn_count"]:
            parts.append(f"全程需转弯{condition['turn_count']}次。")
        if condition["traffic_light_count"]:
            parts.append(f"途经{condition['traffic_light_count']}个红绿灯。")

        # 盲道覆盖情况
        if blind_pct >= 60:
            parts.append(f"这条路线的盲道覆盖率达{blind_pct:.0f}%，盲道连续性好，非常适合盲杖行走。")
        elif blind_pct >= 30:
            parts.append(f"路线约有{blind_pct:.0f}%的路段铺有盲道，部分路段需借助人行道。")
        else:
            parts.append("该路线盲道覆盖较少，建议借助盲杖和路人协助。")

        parts.append("祝您出行顺利，出发吧。")
        return "".join(parts)
