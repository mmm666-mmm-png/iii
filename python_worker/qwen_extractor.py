# qwen_extractor.py
# -*- coding: utf-8 -*-
"""
中文寻物目标到英文视觉类别的归一化。

优先使用 LOCAL_CN2EN 本地映射；映射无法覆盖时调用 Qwen Turbo，
要求只返回短英文类名，作为 yolomedia/YOLOE 的目标提示词。
"""
from typing import List, Tuple
import os
from openai import OpenAI

# —— 本地优先映射（可随时扩充/改名）——
LOCAL_CN2EN = {
    "红牛": "Red_Bull",
    "ad钙奶": "AD_milk",
    "ad 钙奶": "AD_milk",
    "ad": "AD_milk",
    "钙奶": "AD_milk",
    "矿泉水": "bottle",
    "水瓶": "bottle",
    "可乐": "coke",
    "雪碧": "sprite",
}

def _make_client() -> OpenAI:
    """创建 DashScope OpenAI 兼容客户端。"""
    # 复用你百炼兼容端点；支持从环境变量读取
    base_url = os.getenv("DASHSCOPE_COMPAT_BASE", "https://dashscope.aliyuncs.com/compatible-mode/v1")
    api_key  = "sk-a9440db694924559ae4ebdc2023d2b9a"
    return OpenAI(api_key=api_key, base_url=base_url)

PROMPT_SYS = (
    "You are a label normalizer. Convert the given Chinese object "
    "description into a short, lowercase English YOLO/vision class name "
    "(1~3 words). If multiple are given, return the single most likely one. "
    "Output ONLY the label, no punctuation."
)

def extract_english_label(query_cn: str) -> Tuple[str, str]:
    """
    返回 (label_en, source)；source ∈ {'local', 'qwen', 'fallback'}
    """
    q = (query_cn or "").strip().lower()
    if q in LOCAL_CN2EN:
        return LOCAL_CN2EN[q], "local"

    # 简单规则：去掉前缀修饰词
    # 例如“帮我找一瓶矿泉水”中包含“矿泉水”，即可直接映射到 bottle。
    for k, v in LOCAL_CN2EN.items():
        if k in q:
            return v, "local"

    # 调用 Qwen Turbo（兼容 Chat Completions）
    try:
        client = _make_client()
        msgs = [
            {"role": "system", "content": PROMPT_SYS},
            {"role": "user",   "content": query_cn.strip()},
        ]
        rsp = client.chat.completions.create(
            model=os.getenv("QWEN_MODEL", "qwen-turbo"),
            messages=msgs,
            stream=False
        )
        label = (rsp.choices[0].message.content or "").strip()
        # 清洗一下
        label = label.replace(".", "").replace(",", "").replace("  ", " ").strip()
        # 兜底：空就回 'bottle'
        return (label or "bottle"), "qwen"
    except Exception:
        return "bottle", "fallback"
