# audio_stream.py
# -*- coding: utf-8 -*-
"""
AI 语音下行的 HTTP WAV 流。

Qwen Omni 或预录语音都会先变成 8kHz PCM16，然后通过 broadcast_pcm16_realtime
按 20ms 节拍写入当前 /stream.wav 连接。hard_reset_audio 是总闸，用于打断旧音频。
"""
import asyncio
import os
import time
from dataclasses import dataclass
from typing import Optional, Set, List, Tuple, Any, Dict
from fastapi import Request
from fastapi.responses import StreamingResponse

# ===== 下行 WAV 流基础参数 =====
# 当前设备/浏览器链路使用 8kHz mono PCM16，20ms 一包是 ASR/TTS 常见实时粒度。
STREAM_SR = 8000  # 改为8kHz，ESP32支持
STREAM_CH = 1
STREAM_SW = 2
BYTES_PER_20MS_16K = STREAM_SR * STREAM_SW * 20 // 1000  # 320B (8kHz)

# ===== AI 播放任务总闸 =====
current_ai_task: Optional[asyncio.Task] = None

# 整段播报结束后的语音识别延后：播报结束后再延后 POST_PLAY_GRACE_SECONDS 秒
# 才放行 ASR 结果触发新一轮，避免扬声器播报回声被麦克风拾取后又被识别成输入
# （自问自答）。默认 3 秒，可用环境变量 AIGLASS_POST_BROADCAST_DELAY_SECONDS 覆盖。
POST_PLAY_GRACE_SECONDS = float(os.getenv("AIGLASS_POST_BROADCAST_DELAY_SECONDS", "3.0"))
_last_ai_play_end_ts = 0.0  # 最近一次 AI 播报结束时刻（monotonic）


def mark_ai_play_end() -> None:
    """记录一次 AI 播报结束时刻，供 is_playing_now 判断延后期。"""
    global _last_ai_play_end_ts
    _last_ai_play_end_ts = time.monotonic()

async def cancel_current_ai():
    """取消当前大模型语音任务，并等待其退出。"""
    global current_ai_task
    task = current_ai_task
    current_ai_task = None
    if task and not task.done():
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        except Exception:
            pass
    # 播报被中断也视为一次播报结束，进入延后期
    mark_ai_play_end()

def is_playing_now() -> bool:
    """判断普通 AI 问答任务是否仍在产出音频/文本（含播报结束后的延后期）。"""
    t = current_ai_task
    if (t is not None) and (not t.done()):
        return True
    # 播报刚结束：延后 POST_PLAY_GRACE_SECONDS 秒再放行语音识别
    return (time.monotonic() - _last_ai_play_end_ts) < POST_PLAY_GRACE_SECONDS

# ===== /stream.wav 连接管理 =====
@dataclass(frozen=True)
class StreamClient:
    """一个 /stream.wav 播放连接及其音频队列。"""
    q: asyncio.Queue
    abort_event: asyncio.Event

stream_clients: "Set[StreamClient]" = set()
STREAM_QUEUE_MAX = 96  # 小缓冲，避免积压

def _wav_header_unknown_size(sr=16000, ch=1, sw=2) -> bytes:
    """生成近似无限长度的 WAV 头，适合 HTTP streaming 持续写 PCM。"""
    import struct
    byte_rate = sr * ch * sw
    block_align = ch * sw
    data_size = 0x7FFFFFF0
    riff_size = 36 + data_size
    return struct.pack(
        "<4sI4s4sIHHIIHH4sI",
        b"RIFF", riff_size, b"WAVE",
        b"fmt ", 16,
        1, ch, sr, byte_rate, block_align, sw * 8,
        b"data", data_size
    )

async def hard_reset_audio(reason: str = ""):
    """
    **一键清场**：丢弃所有播放器连接（abort_event置位）+ 取消当前AI任务。
    这样旧的音频不会再有任何去处，也没有任何任务继续产出。
    """
    # 1) 断开所有正在播放的 HTTP 连接
    for sc in list(stream_clients):
        try:
            sc.abort_event.set()
        except Exception:
            pass
    stream_clients.clear()

    # 2) 取消当前AI任务
    await cancel_current_ai()

    # 3) 日志
    if reason:
        print(f"[HARD-RESET] {reason}")

async def broadcast_pcm16_realtime(pcm16: bytes):
    """以 20ms 节拍把 pcm16 发送给所有仍存活的连接；队列满丢尾，保持实时。"""
    # 【新增】录制音频（在分发之前整体录制，避免分片）
    try:
        import sync_recorder
        sync_recorder.record_audio(pcm16, text="[Omni对话]")
    except Exception:
        pass  # 静默失败，不影响播放
    
    loop = asyncio.get_event_loop()
    next_tick = loop.time()
    off = 0
    while off < len(pcm16):
        # 按 20ms 切片，队列满时丢最旧片段，优先保证实时性而不是完整回放。
        take = min(BYTES_PER_20MS_16K, len(pcm16) - off)
        piece = pcm16[off:off + take]

        dead: List[StreamClient] = []
        for sc in list(stream_clients):
            if sc.abort_event.is_set():
                dead.append(sc)
                continue
            try:
                if sc.q.full():
                    try: sc.q.get_nowait()
                    except Exception: pass
                sc.q.put_nowait(piece)
            except Exception:
                dead.append(sc)
        for sc in dead:
            try: stream_clients.discard(sc)
            except Exception: pass

        next_tick += 0.020
        now = loop.time()
        if now < next_tick:
            await asyncio.sleep(next_tick - now)
        else:
            next_tick = now
        off += take

# ===== FastAPI 路由注册器 =====
def register_stream_route(app):
    """把 /stream.wav 注册到 FastAPI app 上。"""
    @app.get("/stream.wav")
    async def stream_wav(_: Request):
        # —— 强制单连接（或少数连接），先拉闸所有旧连接 ——
        for sc in list(stream_clients):
            try: sc.abort_event.set()
            except Exception: pass
        stream_clients.clear()

        q: asyncio.Queue[bytes | None] = asyncio.Queue(maxsize=STREAM_QUEUE_MAX)
        abort_event = asyncio.Event()
        sc = StreamClient(q=q, abort_event=abort_event)
        stream_clients.add(sc)

        async def gen():
            # 先写 WAV 头，再持续输出 PCM；连接被 hard_reset_audio 标记后立即结束。
            yield _wav_header_unknown_size(STREAM_SR, STREAM_CH, STREAM_SW)
            try:
                while True:
                    if abort_event.is_set():
                        break
                    try:
                        chunk = await asyncio.wait_for(q.get(), timeout=0.5)
                    except asyncio.TimeoutError:
                        continue
                    if abort_event.is_set():
                        break
                    if chunk is None:
                        break
                    if chunk:
                        yield chunk
            finally:
                stream_clients.discard(sc)
        return StreamingResponse(gen(), media_type="audio/wav")
