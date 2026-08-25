"""
基础设施 —— 语音合成客户端

调用 DashScope TTS 把中文文本合成为 8kHz mono PCM16，供 audio_player
的下行音频链路播放。主要用于高德路线逐段播报等动态文本场景（这类文本
无法命中 voice/ 目录下的静态 wav）。
"""
from __future__ import annotations

import io
import logging
import os
import wave
from typing import Optional

logger = logging.getLogger(__name__)


class TtsClient:
    """DashScope 语音合成客户端。"""

    MODEL = "sambert-zhichu-v1"

    def __init__(self, api_key: str = ""):
        self.api_key = api_key or os.getenv("DASHSCOPE_API_KEY", "")

    def synthesize_pcm16_8k(self, text: str) -> Optional[bytes]:
        """把文本合成为 8kHz mono PCM16；失败返回 None。"""
        text = (text or "").strip()
        if not text or not self.api_key:
            return None
        try:
            from dashscope.audio.tts import SpeechSynthesizer

            result = SpeechSynthesizer.call(
                model=self.MODEL,
                text=text,
                format="wav",
                sample_rate=8000,
            )
            wav_bytes = result.get_audio_data() if result else None
            if not wav_bytes:
                logger.warning("TTS 返回空音频: %s", text[:20])
                return None
            return self._wav_to_pcm16_8k(wav_bytes)
        except Exception as exc:
            logger.error("TTS 合成失败: %s", exc)
            return None

    @staticmethod
    def _wav_to_pcm16_8k(wav_bytes: bytes) -> Optional[bytes]:
        """解析 WAV 字节，返回 8kHz mono PCM16。"""
        try:
            import audioop

            with wave.open(io.BytesIO(wav_bytes), "rb") as wf:
                channels = wf.getnchannels()
                sampwidth = wf.getsampwidth()
                framerate = wf.getframerate()
                frames = wf.readframes(wf.getnframes())

            if channels == 2:
                frames = audioop.tomono(frames, sampwidth, 1, 0)
            if sampwidth != 2:
                frames = audioop.lin2lin(frames, sampwidth, 2)
            if framerate != 8000:
                frames, _ = audioop.ratecv(frames, 2, 1, framerate, 8000, None)
            return frames
        except Exception as exc:
            logger.error("TTS 音频解码失败: %s", exc)
            return None
