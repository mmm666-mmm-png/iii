# -*- coding: utf-8 -*-
"""
voice_session_manager.py —— 双模式异步语音会话管理器（千问聊天 ⇄ 高德导航）

设计目标
========
在 ESP32 眼镜项目里统一管理「千问聊天(qwen_chat)」与「高德导航(amap_nav)」两种
语音模式的自由切换，并支持聊天模式下的语音打断（barge-in）。

核心能力
--------
1. 聊天模式 AI 播报时，用户开口即可打断（VAD 人声触发），打断后可以问新问题，
   也可以直接说「导航去 XX」切到导航模式。
2. 导航模式后台持续运行：用户说「切换聊天模式」→ 停止导航切回聊天；
   导航途中打断播报问「今天天气怎么样」→ 导航后台继续，千问回答，说完恢复导航播报。
3. VAD 检测到人声触发打断，下发 stop_tts 指令给 ESP32，并用 SESSION_ID 解决
   音频分片乱序/延迟（旧会话的分片到达后直接丢弃）。
4. 大模型意图解析输出固定 JSON，区分 nav/chat 意图并提取目的地。
5. 导航只「暂停播报」，不销毁导航实例；AI 播报结束后恢复导航播报。
6. 全链路 JSON 解析异常捕获容错（含关键词 fallback）。

不重写底层
==========
本模块【不重写】ASR / TTS / 高德底层接口，所有需要接入你项目的位置都用
`# TODO(替换)` 标注，并在模块底部给出占位函数清单。

接入方式
========
    from voice_session_manager import VoiceSessionManager

    mgr = VoiceSessionManager()

    # 1) ASR 识别出 final 文本后调用（ASR 由你现有代码驱动）：
    #    await mgr.handle_asr_final(asr_text)

    # 2) VAD 检测到人声时调用（也可用 start_vad_monitor() 让内部轮询）：
    #    await mgr.on_vad_speech_start()

    # 3) 关闭时：
    #    await mgr.close()

ESP32 硬件/固件需要修改的点见文件底部 `ESP32_CHANGES`。
"""

from __future__ import annotations

import asyncio
import json
import re
import time
from typing import Any, Callable, Coroutine, Dict, List, Optional

# ==================== 模式常量 ====================
MODE_QWEN_CHAT = "qwen_chat"
MODE_AMAP_NAV = "amap_nav"
VALID_MODES = (MODE_QWEN_CHAT, MODE_AMAP_NAV)

# 下行音频：8kHz mono PCM16，20ms 一包 = 320 字节（与 audio_stream.py 一致）
STREAM_SR = 8000
STREAM_CH = 1
STREAM_SW = 2
BYTES_PER_20MS = STREAM_SR * STREAM_SW * 20 // 1000  # 320


# ==================== 占位函数（TODO 替换，不重写底层） ====================
async def send_to_esp32(payload: Dict[str, Any]) -> None:
    """TODO(替换)：把一条 JSON 消息下发到 ESP32（走你的 UDP/WebSocket 通道）。"""
    print(f"    [ESP32↓] {json.dumps(payload, ensure_ascii=False)}")


async def synthesize_pcm16_8k(text: str) -> bytes:
    """TODO(替换)：调用你的 TTS，把文本合成 8kHz mono PCM16；失败返回 b""。"""
    print(f"    [TTS] 合成语音: {text[:30]}")
    return b""


async def qwen_chat_reply(text: str) -> str:
    """TODO(替换)：调用千问聊天（Qwen Omni / QwenVoiceClient），返回回答文本。"""
    print(f"    [千问回答] {text}")
    return f"关于「{text}」的回答。"


async def amap_start_nav(destination: str) -> None:
    """TODO(替换)：启动高德导航（创建/复用导航实例，不销毁）。"""
    print(f"    [高德导航] 开始导航 -> {destination}")


async def amap_stop_nav() -> None:
    """TODO(替换)：停止高德导航并销毁导航实例。"""
    print("    [高德导航] 停止导航")


async def amap_pause_broadcast() -> None:
    """TODO(替换)：暂停高德导航播报（保留导航实例，仅暂停语音输出）。"""
    print("    [高德导航] 暂停播报")


async def amap_resume_broadcast() -> None:
    """TODO(替换)：恢复高德导航播报。"""
    print("    [高德导航] 恢复播报")


async def amap_next_step() -> Optional[str]:
    """TODO(替换)：返回下一段导航提示文本；导航走完/结束返回 None。

    你的高德导航内部可能已经是一个独立循环，此时可以不使用本函数，
    而改为在每段播报前调用 mgr.nav_broadcast_begin()，播报后调用 mgr.nav_broadcast_end()。
    """
    return None


async def vad_is_speech() -> bool:
    """TODO(替换)：返回当前是否检测到人声（VAD 实时结果）。

    - 若你的 VAD 在 ESP32 端做，则把 voice_activity 标志随音频上行带过来；
    - 若在 Python 端做，则这里对接你的 VAD 推理结果。
    """
    return False


async def llm_intent(text: str) -> Optional[Dict[str, Any]]:
    """TODO(替换)：调用大模型做意图解析，要求输出固定 JSON。

    参考实现见 `_llm_intent_json`，异常时返回 None 由调用方回退关键词解析。
    """
    return await _llm_intent_json(text)


# ==================== 意图解析：固定 JSON ====================
INTENT_PROMPT = """你是语音助手意图路由器。请把用户语音转写文本分类为导航(nav)或聊天(chat)，并提取目的地。

用户说: "{text}"

严格按以下 JSON 格式返回，不要输出任何其它内容:
{{"intent": "nav" 或 "chat", "action": "start_nav" 或 "stop_nav" 或 "ask_qwen", "destination": "目的地名称" 或 null}}

规则:
1. "导航到XX / 导航去XX / 带我去XX / 去XX" → intent=nav, action=start_nav, destination=XX。
2. "退出导航 / 停止导航 / 结束导航 / 切换聊天模式 / 切回聊天" → intent=chat, action=stop_nav, destination=null。
3. 单纯闲聊或提问（天气、时间、讲故事等）→ intent=chat, action=ask_qwen, destination=null。
4. "切换导航模式 / 高德导航" 但没目的地 → intent=nav, action=start_nav, destination=null。
"""


def _extract_json(content: str) -> Optional[Dict[str, Any]]:
    """从大模型输出里容错提取 JSON（剥离 markdown 代码块 / 截取首对花括号）。"""
    if not content:
        return None
    content = content.strip()

    # 1) 剥离 ```json ... ``` 或 ``` ... ```
    fence = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", content, re.DOTALL)
    if fence:
        content = fence.group(1).strip()

    # 2) 直接解析
    try:
        obj = json.loads(content)
        if isinstance(obj, dict):
            return obj
    except (json.JSONDecodeError, TypeError):
        pass

    # 3) 截取第一对花括号再解析
    brace = re.search(r"\{.*\}", content, re.DOTALL)
    if brace:
        try:
            obj = json.loads(brace.group(0))
            if isinstance(obj, dict):
                return obj
        except (json.JSONDecodeError, TypeError):
            pass
    return None


async def _llm_intent_json(text: str) -> Optional[Dict[str, Any]]:
    """大模型意图解析参考实现（OpenAI 兼容接口 / DashScope）。

    任何异常（网络、鉴权、JSON 坏格式）都返回 None，由上层回退关键词解析。
    TODO(替换)：若项目已有千问客户端，直接复用并把结果过一遍 `_normalize_intent`。
    """
    try:
        import os

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
            messages=[{"role": "user", "content": INTENT_PROMPT.format(text=text)}],
            temperature=0.1,
            response_format={"type": "json_object"},
        )
        content = (resp.choices[0].message.content or "") if resp.choices else ""
        return _extract_json(content)
    except Exception as exc:  # noqa: BLE001 —— 全链路 JSON/网络容错
        print(f"[SESSION] 大模型意图解析失败，回退关键词: {exc}")
        return None


def _clean_destination(value: str) -> str:
    """清理目的地文本：去标点、去尾缀。"""
    value = re.sub(r"[，。！？!?、~～\s]+", "", value or "").strip()
    value = re.sub(r"(附近|那边|这附近|这里|那边儿)$", "", value)
    return value


def _fallback_intent(text: str) -> Dict[str, Any]:
    """无大模型或 JSON 异常时的关键词兜底（覆盖测试用例）。"""
    t = (text or "").strip()

    # 1) 退出/停止导航，或切换回聊天模式 → stop_nav
    if any(
        k in t
        for k in (
            "退出导航",
            "停止导航",
            "结束导航",
            "关掉导航",
            "关闭导航",
            "不导航",
            "切换聊天",
            "切回聊天",
            "切到聊天",
            "聊天模式",
        )
    ):
        return {"intent": "chat", "action": "stop_nav", "destination": None}

    # 2) 带目的地的导航意图
    m = re.search(r"(?:导航到|导航去|带我去|前往|去)\s*(.+)", t)
    if m:
        dest = _clean_destination(m.group(1))
        if dest:
            return {"intent": "nav", "action": "start_nav", "destination": dest}

    # 3) 切换导航模式（无目的地）
    if any(k in t for k in ("导航", "高德导航", "切换导航")):
        return {"intent": "nav", "action": "start_nav", "destination": None}

    # 4) 默认聊天
    return {"intent": "chat", "action": "ask_qwen", "destination": None}


def _normalize_intent(raw: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """把大模型返回的 dict 规范化成内部固定结构，非法则返回 None。"""
    if not isinstance(raw, dict):
        return None
    intent = str(raw.get("intent", "")).strip().lower()
    if intent not in ("nav", "chat"):
        return None
    action = str(raw.get("action", "")).strip().lower()
    destination = raw.get("destination")
    if not isinstance(destination, str) or not destination.strip():
        destination = None
    else:
        destination = _clean_destination(destination)
    return {"intent": intent, "action": action, "destination": destination}


async def parse_intent(text: str, llm_fn: Optional[Callable[..., Any]] = None) -> Dict[str, Any]:
    """意图解析总入口：优先大模型，异常/关闭时回退关键词，永不抛异常。

    兼容两种情况：llm_fn 返回 coroutine（异步调用）或直接返回 dict（同步调用）。
    """
    try:
        fn = llm_fn or llm_intent
        result = fn(text)
        if asyncio.iscoroutine(result):
            result = await result
        norm = _normalize_intent(result)
        if norm is not None:
            return norm
    except Exception as exc:  # noqa: BLE001
        print(f"[SESSION] parse_intent 异常: {exc}")
    return _fallback_intent(text)


# ==================== 会话管理器 ====================
class VoiceSessionManager:
    """双模式异步语音会话管理器。

    用一个 `asyncio.Lock` 串行化所有“模式/输出状态”变更，避免 VAD 打断、
    ASR final、聊天任务完成等并发事件互相踩踏。
    """

    def __init__(self, deps: Optional[Dict[str, Any]] = None) -> None:
        deps = deps or {}

        # —— 依赖注入（可替换为用户自己的实现，默认走模块占位函数）——
        self.send_to_esp32 = deps.get("send_to_esp32", send_to_esp32)
        self.synthesize_pcm16_8k = deps.get("synthesize_pcm16_8k", synthesize_pcm16_8k)
        self.qwen_chat_reply = deps.get("qwen_chat_reply", qwen_chat_reply)
        self.amap_start_nav = deps.get("amap_start_nav", amap_start_nav)
        self.amap_stop_nav = deps.get("amap_stop_nav", amap_stop_nav)
        self.amap_pause_broadcast = deps.get("amap_pause_broadcast", amap_pause_broadcast)
        self.amap_resume_broadcast = deps.get("amap_resume_broadcast", amap_resume_broadcast)
        self.amap_next_step = deps.get("amap_next_step", amap_next_step)
        self.vad_is_speech = deps.get("vad_is_speech", vad_is_speech)
        self.llm_intent = deps.get("llm_intent", llm_intent)
        # 允许测试/用户覆盖内置 tts_speak（默认 None = 用内置分片实现）
        self._tts_speak_override = deps.get("tts_speak")

        # —— 会话状态 ——
        self._lock = asyncio.Lock()
        self.mode: str = MODE_QWEN_CHAT
        self._session_seq = 0
        self._active_playback_session: Optional[str] = None  # 当前正在播报的会话

        # —— 聊天任务 ——
        self._chat_task: Optional[asyncio.Task] = None

        # —— 导航状态（暂停 ≠ 销毁）——
        self._nav_running = False
        self._nav_paused = False
        self._nav_broadcasting = False
        self._nav_resume_event = asyncio.Event()
        self._nav_resume_event.set()  # 初始允许播报
        self._nav_task: Optional[asyncio.Task] = None

        # —— VAD ——
        self._vad_task: Optional[asyncio.Task] = None
        self._vad_was_speaking = False
        self.vad_poll_interval = deps.get("vad_poll_interval", 0.05)

    # ==================== SESSION_ID ====================
    def _new_session_id(self) -> str:
        self._session_seq += 1
        return f"tts-{int(time.time() * 1000)}-{self._session_seq}"

    def _is_current_session(self, session_id: str) -> bool:
        return bool(session_id) and session_id == self._active_playback_session

    # ==================== 音频输出（统一入口，带 SESSION_ID） ====================
    async def tts_speak(self, text: str, session_id: str) -> None:
        """把文本合成为 PCM16 并以 20ms 分片下发 ESP32。

        每片携带 session_id + seq；发送前检查会话是否仍然有效，被打断则立即中止，
        从而让 ESP32 能按 SESSION_ID 丢弃乱序/延迟的旧分片。
        """
        if self._tts_speak_override is not None:
            await self._tts_speak_override(text, session_id)
            return

        if not text:
            return
        try:
            pcm = await self.synthesize_pcm16_8k(text)
        except Exception as exc:  # noqa: BLE001
            print(f"[SESSION] TTS 合成异常: {exc}")
            return
        if not pcm:
            return

        seq = 0
        offset = 0
        while offset < len(pcm):
            # 被打断（会话已被替换/置空）→ 停止发送本会话剩余分片
            if not self._is_current_session(session_id):
                return
            piece = pcm[offset : offset + BYTES_PER_20MS]
            try:
                await self.send_to_esp32(
                    {
                        "type": "tts_chunk",
                        "session_id": session_id,
                        "seq": seq,
                        "sample_rate": STREAM_SR,
                        "audio_base64": _b64(piece),
                    }
                )
            except Exception as exc:  # noqa: BLE001
                print(f"[SESSION] 下发音频分片失败: {exc}")
            seq += 1
            offset += BYTES_PER_20MS
            await asyncio.sleep(0)  # 让出事件循环，保证打断能及时被调度

    async def _send_stop_tts(self, session_id: str) -> None:
        """下发 stop_tts 给 ESP32，令其立即停止该会话的音频播放。"""
        try:
            await self.send_to_esp32({"type": "stop_tts", "session_id": session_id})
        except Exception as exc:  # noqa: BLE001
            print(f"[SESSION] 下发 stop_tts 失败: {exc}")

    # ==================== 模式切换 ====================
    async def _set_mode(self, mode: str) -> None:
        if mode not in VALID_MODES:
            return
        if self.mode != mode:
            self.mode = mode
            print(f"[SESSION] 模式切换 -> {mode}")
            try:
                await self.send_to_esp32({"type": "mode", "mode": mode})
            except Exception:  # noqa: BLE001
                pass

    # ==================== 打断（VAD） ====================
    def _is_output_active(self) -> bool:
        """当前是否有音频在输出（聊天 AI 播报 或 导航播报）。"""
        return bool(self._active_playback_session) or self._nav_broadcasting

    async def on_vad_speech_start(self) -> None:
        """VAD 检测到人声时调用：打断当前输出。"""
        async with self._lock:
            if not self._is_output_active():
                return
            await self._interrupt_current_output()

    async def _interrupt_current_output(self) -> None:
        """打断当前所有输出：停 TTS、取消聊天任务、暂停导航播报（不销毁导航）。"""
        sid = self._active_playback_session
        if sid:
            await self._send_stop_tts(sid)
            self._active_playback_session = None
        await self._cancel_chat_task()
        await self._pause_nav_broadcast()

    async def start_vad_monitor(self) -> None:
        """启动后台 VAD 轮询：检测到人声上升沿即触发打断。

        若你的 VAD 是事件驱动（例如 ESP32 上报 voice_activity 标志），
        不需要本循环，直接在事件回调里 await mgr.on_vad_speech_start() 即可。
        """
        if self._vad_task and not self._vad_task.done():
            return
        self._vad_task = asyncio.create_task(self._vad_monitor_loop())

    async def _vad_monitor_loop(self) -> None:
        while True:
            try:
                speaking = bool(await self.vad_is_speech())
            except Exception:  # noqa: BLE001
                speaking = False
            # 上升沿触发一次，避免每个轮询周期重复打断
            if speaking and not self._vad_was_speaking:
                await self.on_vad_speech_start()
            self._vad_was_speaking = speaking
            await asyncio.sleep(self.vad_poll_interval)

    # ==================== 聊天 ====================
    async def _start_chat(self, text: str) -> None:
        await self._cancel_chat_task()
        session_id = self._new_session_id()
        self._active_playback_session = session_id
        self._chat_task = asyncio.create_task(self._chat_worker(text, session_id))

    async def _chat_worker(self, text: str, session_id: str) -> None:
        try:
            # 千问说话期间暂停导航播报（导航实例保留）
            await self._pause_nav_broadcast()
            answer = await self.qwen_chat_reply(text)
            if answer and self._is_current_session(session_id):
                await self.tts_speak(answer, session_id)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            print(f"[SESSION] 聊天处理异常: {exc}")
        finally:
            current = asyncio.current_task()
            if current is not None and self._chat_task is current:
                self._chat_task = None
            if self._is_current_session(session_id):
                self._active_playback_session = None
            # 千问说完恢复导航播报
            await self._resume_nav_broadcast()

    async def _cancel_chat_task(self) -> None:
        task = self._chat_task
        self._chat_task = None
        if task and not task.done():
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass

    # ==================== 导航（暂停 ≠ 销毁） ====================
    async def _start_nav(self, destination: str) -> None:
        if not self._nav_running:
            self._nav_running = True
            self._nav_resume_event.set()
            self._nav_task = asyncio.create_task(self._nav_worker())
        self._nav_paused = False
        try:
            await self.amap_start_nav(destination)
        except Exception as exc:  # noqa: BLE001
            print(f"[SESSION] 启动导航异常: {exc}")

    async def _stop_nav(self) -> None:
        self._nav_running = False
        self._nav_paused = False
        self._nav_broadcasting = False
        self._nav_resume_event.set()
        nav_task = self._nav_task
        self._nav_task = None
        if nav_task and not nav_task.done():
            nav_task.cancel()
            try:
                await nav_task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass
        try:
            await self.amap_stop_nav()
        except Exception as exc:  # noqa: BLE001
            print(f"[SESSION] 停止导航异常: {exc}")

    async def _pause_nav_broadcast(self) -> None:
        if self.mode == MODE_AMAP_NAV and self._nav_running and not self._nav_paused:
            self._nav_paused = True
            self._nav_resume_event.clear()  # 阻塞导航播报循环
            try:
                await self.amap_pause_broadcast()
            except Exception as exc:  # noqa: BLE001
                print(f"[SESSION] 暂停导航播报异常: {exc}")

    async def _resume_nav_broadcast(self) -> None:
        if self.mode == MODE_AMAP_NAV and self._nav_running and self._nav_paused:
            self._nav_paused = False
            self._nav_resume_event.set()  # 放行导航播报循环
            try:
                await self.amap_resume_broadcast()
            except Exception as exc:  # noqa: BLE001
                print(f"[SESSION] 恢复导航播报异常: {exc}")

    async def _nav_worker(self) -> None:
        """导航播报循环：每段播报分配独立 SESSION_ID，可被 VAD 打断后恢复。"""
        try:
            while self._nav_running:
                await self._nav_resume_event.wait()  # 暂停时在此阻塞
                try:
                    step = await self.amap_next_step()
                except Exception as exc:  # noqa: BLE001
                    print(f"[SESSION] 获取导航步骤异常: {exc}")
                    step = None
                if not step:
                    break

                session_id = self._new_session_id()
                self._active_playback_session = session_id
                self._nav_broadcasting = True
                await self._amap_broadcast_step(step, session_id)
                if self._is_current_session(session_id):
                    self._active_playback_session = None
                self._nav_broadcasting = False
        except asyncio.CancelledError:
            raise
        finally:
            self._nav_broadcasting = False
            self._nav_running = False

    async def _amap_broadcast_step(self, step_text: str, session_id: str) -> None:
        """导航单步播报：复用统一 tts_speak，保证 stop_tts 对导航播报同样生效。"""
        await self.tts_speak(step_text, session_id)

    # ==================== 路由主入口 ====================
    async def handle_asr_final(self, text: str) -> Dict[str, Any]:
        """ASR final 文本入口：先打断当前输出，再解析意图并路由。

        返回 dict 便于测试断言：{"mode", "action", "destination"}。
        """
        text = (text or "").strip()
        if not text:
            return {"mode": self.mode, "action": "none", "destination": None}

        async with self._lock:
            # 用户说完一句话，先打断旧输出（含聊天播报/导航播报）
            await self._interrupt_current_output()

            intent = await parse_intent(text, self.llm_intent)
            action = intent.get("action", "")
            destination = intent.get("destination")
            is_nav = intent.get("intent") == "nav"

            # 1) 停止导航 / 切回聊天
            if action == "stop_nav":
                await self._stop_nav()
                await self._set_mode(MODE_QWEN_CHAT)
                return {"mode": self.mode, "action": "stop_nav", "destination": None}

            # 2) 导航意图
            if is_nav:
                await self._set_mode(MODE_AMAP_NAV)
                if destination:
                    await self._start_nav(destination)
                return {
                    "mode": self.mode,
                    "action": "start_nav",
                    "destination": destination,
                }

            # 3) 聊天意图：导航模式下不切模式、不销毁导航，仅千问回答
            await self._start_chat(text)
            return {"mode": self.mode, "action": "ask_qwen", "destination": None}

    # ==================== 关闭 ====================
    async def close(self) -> None:
        """关闭管理器：停 VAD 轮询、取消聊天/导航任务。"""
        if self._vad_task and not self._vad_task.done():
            self._vad_task.cancel()
        await self._cancel_chat_task()
        await self._stop_nav()


def _b64(data: bytes) -> str:
    import base64

    return base64.b64encode(data).decode("ascii")


# ==================== ESP32 硬件/固件需要修改的点 ====================
ESP32_CHANGES = """
ESP32 硬件/固件需要修改的点
===========================
1. 音频下行协议升级（新增 session_id + seq）：
   - 固件接收 JSON 消息 `{"type":"tts_chunk","session_id","seq","sample_rate","audio_base64"}`；
   - 维护一个「当前播放会话 current_session_id」；
   - 收到 tts_chunk 时，若 session_id != current_session_id → 直接丢弃（解决乱序/延迟旧包）。

2. 新增 stop_tts 指令（最高优先级）：
   - 收到 `{"type":"stop_tts","session_id"}` 后：立即停止 I2S 播放、清空音频 DMA/环形缓冲
     （例如 i2s_stop() / i2s_zero_dma_buffer() 后再 i2s_start()），并置 current_session_id 无效。

3. 人声检测（VAD）来源二选一：
   - 方案A（推荐）：ESP32 端用 I2S 麦克风数据做轻量 VAD，检测到人声时把
     `voice_activity`（0/1）随上行音频包带上行，Python 端据此调用 on_vad_speech_start()。
   - 方案B：Python 端对上行 PCM 做 VAD（替换 vad_is_speech()），不依赖 ESP32 端 VAD。

4. 模式指示（可选，便于盲人感知）：
   - 增加一个 GPIO 指示灯/震动，收到 `{"type":"mode","mode":...}` 时切换
     （qwen_chat / amap_nav 两种状态用不同闪烁或震感区分）。
"""


# ==================== 测试（覆盖 4 个场景） ====================
async def _run_tests() -> None:
    """离线测试：用假依赖驱动，不依赖网络/硬件，验证 4 个场景的预期行为。

    假 tts_speak 会在一个 asyncio.Event 上阻塞，测试通过控制 Event 来模拟
    “播报中 / 播报完成”，用任务取消来模拟“被打断”，完全确定性、无时序抖动。
    """

    class Recorder:
        def __init__(self) -> None:
            self.log: List[str] = []
            self.gates: List[asyncio.Event] = []

    rec = Recorder()

    async def fake_send(payload: Dict[str, Any]) -> None:
        rec.log.append(f"send:{payload.get('type')}")

    async def fake_qwen(text: str) -> str:
        return f"答：{text}"

    async def fake_tts_override(text: str, session_id: str) -> None:
        # 记录开始，并在 gate 上阻塞；测试 set() 放行=播报完成，cancel=被打断。
        rec.log.append(f"tts_start:{session_id}")
        gate = asyncio.Event()
        rec.gates.append(gate)
        try:
            await gate.wait()
            rec.log.append(f"tts_done:{session_id}")
        except asyncio.CancelledError:
            rec.log.append("tts_cancelled")
            raise

    async def fake_start_nav(dest: str) -> None:
        rec.log.append(f"nav_start:{dest}")

    async def fake_stop_nav() -> None:
        rec.log.append("nav_stop")

    async def fake_pause() -> None:
        rec.log.append("nav_pause")

    async def fake_resume() -> None:
        rec.log.append("nav_resume")

    async def fake_next_step() -> Optional[str]:
        # 第一次返回一步导航提示，之后阻塞（模拟导航持续运行、等待下一段）。
        if fake_next_step.called:  # type: ignore[attr-defined]
            await asyncio.Event().wait()
            return None
        fake_next_step.called = True  # type: ignore[attr-defined]
        return "前方路口右转"

    fake_next_step.called = False  # type: ignore[attr-defined]

    deps = {
        "send_to_esp32": fake_send,
        "qwen_chat_reply": fake_qwen,
        "tts_speak": fake_tts_override,
        "amap_start_nav": fake_start_nav,
        "amap_stop_nav": fake_stop_nav,
        "amap_pause_broadcast": fake_pause,
        "amap_resume_broadcast": fake_resume,
        "amap_next_step": fake_next_step,
        "llm_intent": lambda t: None,  # 走关键词 fallback
    }

    def check(name: str, cond: bool) -> None:
        print(("  ✅ PASS: " if cond else "  ❌ FAIL: ") + name)

    async def wait_gate(count: int) -> asyncio.Event:
        """等待第 count 个 tts 播报开始（返回其 gate）。"""
        for _ in range(500):
            if len(rec.gates) >= count:
                return rec.gates[count - 1]
            await asyncio.sleep(0.002)
        raise AssertionError(f"等待第 {count} 个播报超时")

    print("=" * 64)
    print("场景 1: 聊天播报中，用户说「导航去人民公园」→ 打断 + 切导航")
    mgr = VoiceSessionManager(deps=dict(deps))
    await mgr.handle_asr_final("你好")
    await wait_gate(1)                       # 聊天进入播报
    first_task = mgr._chat_task
    await mgr.on_vad_speech_start()          # VAD 打断
    r = await mgr.handle_asr_final("导航去人民公园")
    check("模式=amap_nav", r["mode"] == MODE_AMAP_NAV)
    check("动作=start_nav", r["action"] == "start_nav")
    check("目的地=人民公园", r["destination"] == "人民公园")
    check("下发过 stop_tts", "send:stop_tts" in rec.log)
    check("旧聊天被取消", "tts_cancelled" in rec.log)
    check("旧聊天任务已结束", first_task is not None and first_task.done())
    check("导航已启动", "nav_start:人民公园" in rec.log)
    await mgr.close()

    print("=" * 64)
    print("场景 2: 导航运行中，用户说「切换聊天模式」→ 停止导航 + 切回聊天")
    rec.log.clear()
    rec.gates.clear()
    fake_next_step.called = False  # type: ignore[attr-defined]
    mgr = VoiceSessionManager(deps=dict(deps))
    await mgr.handle_asr_final("导航去人民公园")  # 进入导航
    r = await mgr.handle_asr_final("切换聊天模式")
    check("模式=qwen_chat", r["mode"] == MODE_QWEN_CHAT)
    check("动作=stop_nav", r["action"] == "stop_nav")
    check("导航已停止", "nav_stop" in rec.log)
    await mgr.close()

    print("=" * 64)
    print("场景 3: 导航途中打断问「今天天气怎么样」→ 导航后台继续 + 千问回答")
    rec.log.clear()
    rec.gates.clear()
    fake_next_step.called = False  # type: ignore[attr-defined]
    mgr = VoiceSessionManager(deps=dict(deps))
    await mgr.handle_asr_final("导航去人民公园")  # 进入导航，模式=amap_nav
    await wait_gate(1)                       # 导航开始播报（gate1 是导航）
    r = await mgr.handle_asr_final("今天天气怎么样")  # 打断导航播报并提问
    check("模式仍=amap_nav(导航不销毁)", r["mode"] == MODE_AMAP_NAV)
    check("动作=ask_qwen", r["action"] == "ask_qwen")
    check("未调用 nav_stop", "nav_stop" not in rec.log)
    check("导航播报被暂停", "nav_pause" in rec.log)
    chat_gate = await wait_gate(2)           # 聊天回答开始播报
    chat_gate.set()                          # 聊天回答播报完成
    for _ in range(500):                     # 等待 chat worker 收尾
        if "nav_resume" in rec.log:
            break
        await asyncio.sleep(0.002)
    check("恢复过导航播报", "nav_resume" in rec.log)
    await mgr.close()

    print("=" * 64)
    print("场景 4: 聊天播报中打断问「讲个小故事」→ 终止上一轮，直接回复新问题")
    rec.log.clear()
    rec.gates.clear()
    mgr = VoiceSessionManager(deps=dict(deps))
    await mgr.handle_asr_final("你好")
    await wait_gate(1)                       # 第一轮聊天播报开始
    first_task = mgr._chat_task
    await mgr.on_vad_speech_start()          # 打断第一轮
    r = await mgr.handle_asr_final("讲个小故事")
    check("动作=ask_qwen", r["action"] == "ask_qwen")
    check("旧任务已终止", first_task is not None and first_task.done())
    await wait_gate(2)                       # 第二轮聊天播报开始
    check("新任务已启动", mgr._chat_task is not None and mgr._chat_task is not first_task)
    await mgr.close()

    print("=" * 64)
    print("全部测试完成。")


if __name__ == "__main__":
    # 运行离线测试，验证 4 个场景
    try:
        asyncio.run(_run_tests())
    except KeyboardInterrupt:
        pass
