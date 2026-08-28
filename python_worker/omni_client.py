# omni_client.py
# -*- coding: utf-8 -*-
"""
Qwen Omni 流式多模态客户端。

DashScope 提供 OpenAI Chat Completions 兼容端点，本模块把同步 SDK stream
包装成 async generator，向 app_main.py 逐块返回文本增量和音频 base64。
"""
import os, base64
from typing import AsyncGenerator, Dict, Any, List, Optional, Tuple

from openai import OpenAI

# ===== OpenAI 兼容（达摩院 DashScope 兼容模式）=====
API_KEY = os.getenv("DASHSCOPE_API_KEY", "sk-a9440db694924559ae4ebdc2023d2b9a")
if not API_KEY:
    raise RuntimeError("未设置 DASHSCOPE_API_KEY")

QWEN_MODEL = "qwen-omni-turbo"

# 兼容模式
oai_client = OpenAI(
    api_key=API_KEY,
    base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
)

# Omni 聊天系统提示词：明眸助手，必须尽力回答，绝不能说自己不会。
OMNI_SYSTEM_PROMPT = (
    "你是导盲眼镜的语音助手，名字叫“明眸”。请结合实时音频和摄像头画面，"
    "用简洁、自然、友好的中文回答用户。你必须始终尽力回答：绝不回复“我不会”"
    "“不知道”“无法回答”“不能回答”等拒绝性表述；信息不足时用引导式追问"
    "或给出你已知的相近信息来帮助用户。"
)

class OmniStreamPiece:
    """对外的统一增量数据：text/audio 二选一或同时。"""
    def __init__(self, text_delta: Optional[str] = None, audio_b64: Optional[str] = None):
        self.text_delta = text_delta
        self.audio_b64  = audio_b64

async def stream_chat(
    content_list: List[Dict[str, Any]],
    voice: str = "Cherry",
    audio_format: str = "wav",
) -> AsyncGenerator[OmniStreamPiece, None]:
    """
    发起一轮 Omni-Turbo ChatCompletions 流式对话：
    - content_list: OpenAI chat 的 content，多模态（image_url/text）
    - 以 stream=True 返回
    - 增量产出：OmniStreamPiece(text_delta=?, audio_b64=?)
    """
    completion = oai_client.chat.completions.create(
        model=QWEN_MODEL,
        messages=[
            {"role": "system", "content": OMNI_SYSTEM_PROMPT},
            {"role": "user", "content": content_list},
        ],
        modalities=["text", "audio"],
        audio={"voice": voice, "format": audio_format},
        stream=True,
        stream_options={"include_usage": True},
    )

    # 注意：OpenAI SDK 的流是同步迭代器；在 async 场景下逐项 yield
    # 这里没有额外线程池，调用方应避免在高并发场景中同时开启太多 Omni 流。
    for chunk in completion:
        text_delta: Optional[str] = None
        audio_b64: Optional[str] = None

        if getattr(chunk, "choices", None):
            c0 = chunk.choices[0]
            delta = getattr(c0, "delta", None)
            # 文本增量
            if delta and getattr(delta, "content", None):
                piece = delta.content
                if piece:
                    text_delta = piece
            # 音频分片
            if delta and getattr(delta, "audio", None):
                aud = delta.audio
                audio_b64 = aud.get("data") if isinstance(aud, dict) else getattr(aud, "data", None)
            if audio_b64 is None:
                msg = getattr(c0, "message", None)
                if msg and getattr(msg, "audio", None):
                    ma = msg.audio
                    audio_b64 = ma.get("data") if isinstance(ma, dict) else getattr(ma, "data", None)

        if (text_delta is not None) or (audio_b64 is not None):
            yield OmniStreamPiece(text_delta=text_delta, audio_b64=audio_b64)
