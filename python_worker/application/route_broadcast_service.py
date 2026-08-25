"""
应用服务 —— 高德路线逐段语音播报

把已规划路线的高德导航步骤（Route.segments 中每条 instruction）组织成
自然语言，并借助 TTS 合成为语音逐段播报到设备扬声器。

播报在独立线程中进行，避免阻塞规划接口的 HTTP 响应。
"""
from __future__ import annotations

import logging
import threading
from typing import Dict, List, Optional

from domain.model.route import Route
from infrastructure.tts_client import TtsClient

logger = logging.getLogger(__name__)


class RouteBroadcastService:
    """路线播报服务：构建逐段引导文本并在后台语音播报。"""

    def __init__(self, tts_client: Optional[TtsClient] = None):
        self.tts_client = tts_client or TtsClient()

    @staticmethod
    def build_turn_by_turn(route: Route) -> List[Dict]:
        """把路线路段转成逐段播报条目（供接口返回与语音合成）。"""
        steps: List[Dict] = []
        for index, seg in enumerate(route.segments, start=1):
            instruction = (seg.instruction or "").strip()
            if not instruction:
                instruction = "请继续直行"
            steps.append(
                {
                    "index": index,
                    "instruction": instruction,
                    "distance_meters": round(seg.distance_meters, 1),
                    "duration_seconds": seg.duration_seconds,
                    "road_type": seg.road_type,
                }
            )
        return steps

    @staticmethod
    def build_guide_text(route: Route) -> str:
        """构建整段播报文本（供前端展示 / 日志记录）。"""
        lines = []
        for step in RouteBroadcastService.build_turn_by_turn(route):
            lines.append(f"第{step['index']}步，{step['instruction']}")
        return "。".join(lines) + ("。" if lines else "")

    @staticmethod
    def step_to_speech(step: Dict) -> str:
        """把单个步骤转为适合语音播报的一句话。"""
        instruction = str(step.get("instruction") or "").strip()
        return f"第{step.get('index')}步，{instruction}"

    def broadcast_steps(self, steps: List[Dict]) -> None:
        """在后台线程中把每个步骤合成为语音并顺序播放。"""
        if not steps:
            return
        steps_copy = list(steps)

        def _worker() -> None:
            try:
                pcm_list = []
                for step in steps_copy:
                    pcm = self.tts_client.synthesize_pcm16_8k(
                        self.step_to_speech(step)
                    )
                    if pcm:
                        pcm_list.append(pcm)
                if not pcm_list:
                    logger.warning("路线播报未生成任何语音片段，可能 TTS 不可用")
                    return
                from audio_player import play_pcm_sequence

                play_pcm_sequence(pcm_list)
            except Exception as exc:
                logger.error("路线语音播报失败: %s", exc)

        thread = threading.Thread(target=_worker, daemon=True)
        thread.start()
