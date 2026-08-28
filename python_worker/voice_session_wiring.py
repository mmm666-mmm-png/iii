# -*- coding: utf-8 -*-
"""
voice_session_wiring.py —— 把 VoiceSessionManager 接入项目真实底层

这个模块把 `voice_session_manager.VoiceSessionManager` 的占位函数接上现有的
Python 服务，产出一个“开箱即用”的会话管理器实例：

    from voice_session_wiring import build_voice_session_manager

    mgr = build_voice_session_manager()
    # ASR final 回调里：await mgr.handle_asr_final(asr_text)
    # VAD 回调里：    await mgr.on_vad_speech_start()

底层连接关系
------------
- synthesize_pcm16_8k -> infrastructure.tts_client.TtsClient（DashScope TTS）
- tts_chunk            -> audio_player.play_pcm_bytes（现有设备/预览音频链路）
- stop_tts             -> audio_stream.hard_reset_audio（清 /stream.wav）
                           + 通知 Go 后端清空设备 UDP 播放队列（POST /api/voice/interrupt）
- qwen_chat_reply      -> OpenAI 兼容 qwen-turbo 文本问答
- llm_intent           -> voice_session_manager._llm_intent_json（已有实现）
- amap_start_nav       -> VoiceInteractionService + RoutePlanningService 规划路线，
                           注册到 GpsNavigationTrigger 并播报路线摘要
- amap_stop_nav        -> GpsNavigationTrigger.clear()
- amap_pause/resume    -> GpsNavigationTrigger.set_paused()
- amap_next_step       -> 阻塞占位（导航播报由 GPS 触发器驱动，不走会话管理器内部循环）
- vad_is_speech        -> 默认 False，由你注入真实 VAD 结果

注意
----
- 真正打到 ESP32 扬声器的下行音频由 Go 后端经 UDP 发送（server/internal/serverapp/
  device_playback.go）。因此 stop_tts 除了清 Python 侧 /stream.wav，还会 POST 到
  Go 后端的 /api/voice/interrupt 清空设备播放队列。
- 若你的部署里 Python worker 无法直连 Go 后端，可把环境变量 GO_BACKEND_URL 设成
  Go 后端地址（默认 http://127.0.0.1:8888）。
"""

from __future__ import annotations

import asyncio
import base64
import logging
import os
from typing import Any, Dict, Optional

from voice_session_manager import VoiceSessionManager

logger = logging.getLogger(__name__)

GO_BACKEND_URL = os.getenv("GO_BACKEND_URL", "http://127.0.0.1:8888").rstrip("/")

# 千问聊天系统提示词：明眸助手，必须尽力回答，绝不能说自己不会。
QWEN_CHAT_SYSTEM_PROMPT = (
    "你是导盲眼镜的语音助手，名字叫“明眸”。请用简洁、自然、友好的中文回答用户。"
    "你必须始终尽力回答：绝不回复“我不会”“不知道”“无法回答”“不能回答”等拒绝性表述；"
    "信息不足时用引导式追问或给出你已知的相近信息来帮助用户。"
)


# ==================== 底层实现 ====================
async def _synthesize_pcm16_8k(text: str) -> bytes:
    """DashScope TTS 合成 8kHz PCM16（阻塞调用放线程池）。"""
    try:
        from infrastructure.tts_client import TtsClient

        return await asyncio.to_thread(TtsClient().synthesize_pcm16_8k, text) or b""
    except Exception as exc:  # noqa: BLE001
        logger.error("TTS 合成失败: %s", exc)
        return b""


def _notify_go_interrupt() -> None:
    """通知 Go 后端清空设备 UDP 播放队列（best-effort，失败不影响主流程）。"""
    try:
        from urllib.request import Request, urlopen

        req = Request(
            f"{GO_BACKEND_URL}/api/voice/interrupt",
            data=b"{}",
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urlopen(req, timeout=1.5) as resp:
            resp.read()
    except Exception as exc:  # noqa: BLE001
        logger.debug("通知 Go 后端中断播放失败: %s", exc)


async def _send_to_esp32(payload: Dict[str, Any]) -> None:
    """把会话管理器的下行消息接到真实音频/设备链路。"""
    ptype = payload.get("type")

    if ptype == "tts_chunk":
        audio_b64 = payload.get("audio_base64") or ""
        if not audio_b64:
            return
        try:
            pcm = base64.b64decode(audio_b64)
            from audio_player import play_pcm_bytes

            # 每个新会话的第一片先清掉旧音频队列，后续分片顺序入队不丢段。
            clear_stale = int(payload.get("seq", 0)) == 0
            play_pcm_bytes(pcm, clear_stale=clear_stale)
        except Exception as exc:  # noqa: BLE001
            logger.error("下发音频分片失败: %s", exc)

    elif ptype == "stop_tts":
        try:
            from audio_stream import hard_reset_audio

            await hard_reset_audio(f"stop_tts {payload.get('session_id')}")
        except Exception as exc:  # noqa: BLE001
            logger.error("本地中断播放失败: %s", exc)
        await asyncio.to_thread(_notify_go_interrupt)

    elif ptype == "mode":
        # 可选：把模式同步到 Go/前端做状态展示。
        logger.info("语音模式 -> %s", payload.get("mode"))


async def _qwen_chat_reply(text: str) -> str:
    """OpenAI 兼容 qwen-turbo 文本问答（阻塞调用放线程池）。"""
    def _call() -> str:
        try:
            from openai import OpenAI

            client = OpenAI(
                api_key=os.getenv("DASHSCOPE_API_KEY", ""),
                base_url=os.getenv(
                    "QWEN_BASE_URL",
                    "https://dashscope.aliyuncs.com/compatible-mode/v1",
                ),
            )
            resp = client.chat.completions.create(
                model=os.getenv("QWEN_MODEL", "qwen-turbo"),
                messages=[
                    {"role": "system", "content": QWEN_CHAT_SYSTEM_PROMPT},
                    {"role": "user", "content": text},
                ],
                stream=False,
            )
            return (resp.choices[0].message.content or "").strip()
        except Exception as exc:  # noqa: BLE001
            logger.error("千问聊天失败: %s", exc)
            return "抱歉，我暂时没听清，请再说一次。"

    return await asyncio.to_thread(_call)


# ==================== 导航底层（复用现有规划 + GPS 触发） ====================
def _start_blind_path_navigation_if_available() -> None:
    """best-effort 启动视觉导盲（orchestrator 切 BLINDPATH_NAV），失败不影响高德导航。"""
    try:
        import app_main

        if app_main.orchestrator is not None:
            app_main.orchestrator.start_blind_path_navigation()
    except Exception as exc:  # noqa: BLE001
        logger.debug("启动视觉导盲失败: %s", exc)


def _plan_and_start_nav_sync(destination: str) -> str:
    """规划导航并注册到 GPS 触发器，返回路线摘要播报文本（在线程中执行）。"""
    from application.voice_interaction_service import VoiceInteractionService
    from interfaces.api.route_endpoints import (
        _announce_navigation_voice,
        get_gps_trigger,
        get_obstacle_repo,
        get_route_service,
    )

    info = VoiceInteractionService().create_navigation_request(
        origin_desc=None, destination_desc=destination
    )
    if not info.get("need_planning"):
        text = info.get("response_text") or "请告诉我目的地名称。"
        _announce_navigation_voice(text)
        return text

    nav_request = info["navigation_request"]
    route_service = get_route_service()
    route_service.obstacle_repo = get_obstacle_repo()
    result = route_service.plan_route(nav_request)

    # 注册到 GPS 触发器：手机定位进入下一路段时自动播报。
    get_gps_trigger().set_route(result.segments)
    # 高德导航同时开启视觉导盲模式（盲道跟随）
    _start_blind_path_navigation_if_available()
    text = result.broadcast_text or info.get("response_text", "")
    if text:
        _announce_navigation_voice(text)
    return text


async def _amap_start_nav(destination: str) -> Optional[str]:
    return await asyncio.to_thread(_plan_and_start_nav_sync, destination)


async def _amap_stop_nav() -> None:
    from application.gps_navigation_trigger import get_gps_trigger

    get_gps_trigger().clear()


async def _amap_pause_broadcast() -> None:
    from application.gps_navigation_trigger import get_gps_trigger

    get_gps_trigger().set_paused(True)


async def _amap_resume_broadcast() -> None:
    from application.gps_navigation_trigger import get_gps_trigger

    get_gps_trigger().set_paused(False)


async def _amap_next_step() -> Optional[str]:
    """导航播报由 GPS 触发器驱动，不使用会话管理器内部循环。

    这里永久阻塞，仅保持“导航运行中”状态，直到 _stop_nav 取消导航任务。
    """
    await asyncio.Event().wait()
    return None


async def _vad_is_speech() -> bool:
    """默认不检测人声；接入你的 VAD（替换本函数或注入 overrides）。"""
    return False


def _nav_audio_active() -> bool:
    """当前是否有导航语音正在排队/播放（聊天播报据此让路）。"""
    try:
        from audio_player import is_navigation_audio_active

        return is_navigation_audio_active()
    except Exception:  # noqa: BLE001
        return False


# ==================== 工厂 ====================
def build_voice_session_manager(
    overrides: Optional[Dict[str, Any]] = None,
) -> VoiceSessionManager:
    """构建接好真实底层的 VoiceSessionManager。

    overrides 可覆盖任意依赖，例如注入真实 VAD：
        mgr = build_voice_session_manager(overrides={"vad_is_speech": my_vad_fn})
    """
    deps: Dict[str, Any] = {
        "synthesize_pcm16_8k": _synthesize_pcm16_8k,
        "send_to_esp32": _send_to_esp32,
        "qwen_chat_reply": _qwen_chat_reply,
        "amap_start_nav": _amap_start_nav,
        "amap_stop_nav": _amap_stop_nav,
        "amap_pause_broadcast": _amap_pause_broadcast,
        "amap_resume_broadcast": _amap_resume_broadcast,
        "amap_next_step": _amap_next_step,
        "vad_is_speech": _vad_is_speech,
        "nav_audio_active": _nav_audio_active,
    }
    if overrides:
        deps.update(overrides)

    return VoiceSessionManager(deps=deps)
