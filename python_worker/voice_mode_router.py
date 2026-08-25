# -*- coding: utf-8 -*-
"""
voice_mode_router.py — 语音模式路由器（千问聊天 ⇄ 高德导航）

输入：ASR 语音转文字结果（字符串）。
两个模式：
    qwen_chat —— 交给千问聊天问答。
    amap_nav  —— 高德导航，解析目的地并开启导航播报。

特性：
    1. 调用大模型做意图解析，输出 JSON，区分 nav/chat 意图，提取目的地。
    2. 全局状态 CURRENT_MODE 保存当前模式。
    3. 语音口语切换（“导航去XX”、“退出导航”…）自动切换模式。
    4. 导航模式后台不中断：导航途中闲聊仍走千问。
    5. 音频冲突：千问说话时暂停高德导航播报，说完恢复。
    6. JSON 解析异常捕获容错（含关键词 fallback）。

接入方式（示例）：
    from voice_mode_router import handle_voice

    # 在你的 ASR final 回调里调用：
    handle_voice(asr_text)

本模块只写新增逻辑，不重写 ASR / TTS / 高德底层接口。
所有需要接入你项目的位置都用 `# TODO(替换)` 标注。
"""

import json
import os
import re
import threading
from typing import Dict, Optional

# ==================== 全局状态 ====================
CURRENT_MODE = "qwen_chat"  # "qwen_chat" | "amap_nav"
_MODE_LOCK = threading.Lock()

# 最近一次动作/目的地，供测试与调试观察。
last_action: Optional[str] = None
last_destination: Optional[str] = None

# 是否启用大模型做意图解析（生产建议 True；测试可关闭走关键词 fallback）。
_USE_LLM = True


# ==================== 音频冲突互斥 ====================
class AudioGuard:
    """千问说话时暂停高德导航播报，说完恢复。"""

    def __init__(self):
        self._cond = threading.Condition()
        self._qwen_speaking = False

    @property
    def qwen_speaking(self) -> bool:
        with self._cond:
            return self._qwen_speaking

    def qwen_begin(self) -> None:
        with self._cond:
            self._qwen_speaking = True
            pause_amap_broadcast()  # TODO(替换)

    def qwen_end(self) -> None:
        with self._cond:
            self._qwen_speaking = False
            resume_amap_broadcast()  # TODO(替换)
            self._cond.notify_all()

    def wait_if_qwen_speaking(self) -> None:
        """高德导航播报前调用：若千问正在说话，等待其说完。"""
        with self._cond:
            while self._qwen_speaking:
                self._cond.wait()


audio_guard = AudioGuard()


# ==================== 底层项目函数（TODO 替换） ====================
def qwen_chat_reply(text: str) -> None:
    """TODO(替换)：调用你的千问聊天（Qwen Omni / QwenVoiceClient）并 TTS 播报回答。"""
    print(f"    [千问回答] {text}")


def start_amap_nav(destination: str) -> None:
    """TODO(替换)：调用你的高德导航——解析目的地并开启导航播报。"""
    global last_destination
    last_destination = destination
    print(f"    [高德导航] 开始导航 -> {destination}")


def stop_amap_nav() -> None:
    """TODO(替换)：停止高德导航与导航播报。"""
    print("    [高德导航] 停止导航")


def pause_amap_broadcast() -> None:
    """TODO(替换)：暂停高德导航播报（千问说话时调用）。"""
    print("    [音频] 暂停高德导航播报")


def resume_amap_broadcast() -> None:
    """TODO(替换)：恢复高德导航播报（千问说完时调用）。"""
    print("    [音频] 恢复高德导航播报")


def announce_amap_voice(text: str) -> None:
    """TODO(替换)：高德导航单句播报入口（带音频互斥的包装示例）。"""
    audio_guard.wait_if_qwen_speaking()
    print(f"    [高德播报] {text}")


# ==================== 意图解析 ====================
INTENT_PROMPT = """你是语音助手的意图路由器。请把用户语音转写文本分类为导航(nav)或聊天(chat)，并提取目的地。

用户说: "{text}"

请严格按以下 JSON 格式返回（不要输出任何其他内容）:
{{"intent": "nav" 或 "chat", "action": "start_nav" 或 "stop_nav" 或 "ask_qwen", "destination": "目的地名称" 或 null}}

规则:
1. "导航到XX / 带我去XX / 去XX" → intent=nav, action=start_nav, destination=XX。
2. "退出导航 / 停止导航 / 结束导航" → intent=chat, action=stop_nav, destination=null。
3. 单纯闲聊或提问（天气、时间等）→ intent=chat, action=ask_qwen, destination=null。
4. "切换导航模式"但没目的地 → intent=nav, action=start_nav, destination=null。
"""


def _extract_json(content: str) -> Optional[Dict]:
    """从大模型输出中容错提取 JSON（剥离 markdown 代码块、截取首对花括号）。"""
    if not content:
        return None
    content = content.strip()

    fence = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", content, re.DOTALL)
    if fence:
        content = fence.group(1)

    try:
        return json.loads(content)
    except (json.JSONDecodeError, TypeError):
        pass

    brace = re.search(r"\{.*\}", content, re.DOTALL)
    if brace:
        try:
            return json.loads(brace.group(0))
        except (json.JSONDecodeError, TypeError):
            pass
    return None


def _call_llm_json(prompt: str) -> Optional[Dict]:
    """调用大模型返回 JSON dict；任何异常都返回 None（由调用方回退关键词解析）。

    TODO(替换)：如果你项目里已有 QwenVoiceClient / 千问客户端，直接复用它即可，
    下面是基于 OpenAI 兼容接口（DashScope）的参考实现。
    """
    try:
        from openai import OpenAI

        client = OpenAI(
            api_key=os.getenv("DASHSCOPE_API_KEY", ""),
            base_url=os.getenv(
                "QWEN_BASE_URL", "https://dashscope.aliyuncs.com/compatible-mode/v1"
            ),
        )
        resp = client.chat.completions.create(
            model=os.getenv("QWEN_MODEL", "qwen-turbo"),
            messages=[{"role": "user", "content": prompt}],
            temperature=0.1,
            response_format={"type": "json_object"},
        )
        content = resp.choices[0].message.content
        return _extract_json(content)
    except Exception as exc:  # 网络/鉴权/解析等一切异常都走容错
        print(f"[VOICE_ROUTER] 大模型意图解析失败，回退关键词: {exc}")
        return None


def _clean_dest(s: str) -> str:
    s = re.sub(r"[，。！？!?、~～\s]+", "", s).strip()
    s = re.sub(r"(附近|那边|这附近|这里|那边儿)$", "", s)
    return s


def _fallback_intent(text: str) -> Dict:
    """无大模型或 JSON 异常时的关键词兜底。"""
    t = (text or "").strip()

    # 1) 退出/停止导航 → 切回聊天 + 停止导航
    if any(k in t for k in ("退出导航", "停止导航", "结束导航", "不导航", "关掉导航", "关闭导航")):
        return {"intent": "chat", "action": "stop_nav", "destination": None}

    # 2) 带目的地的导航意图
    m = re.search(r"(?:导航到|导航去|带我去|前往|去)\s*(.+)", t)
    if m:
        dest = _clean_dest(m.group(1))
        if dest:
            return {"intent": "nav", "action": "start_nav", "destination": dest}

    # 3) 切换导航模式（无目的地）
    if any(k in t for k in ("导航", "高德导航", "切换导航")):
        return {"intent": "nav", "action": "start_nav", "destination": None}

    # 4) 默认聊天
    return {"intent": "chat", "action": "ask_qwen", "destination": None}


def parse_intent(text: str) -> Dict:
    """意图解析：优先大模型，异常/关闭时回退关键词。"""
    if _USE_LLM:
        try:
            result = _call_llm_json(INTENT_PROMPT.format(text=text))
            if result is not None:
                intent = str(result.get("intent", "")).lower()
                if intent in ("nav", "chat"):
                    action = str(result.get("action", "")).lower()
                    destination = result.get("destination")
                    if not isinstance(destination, str) or not destination.strip():
                        destination = None
                    return {
                        "intent": "nav" if intent == "nav" else "chat",
                        "action": action,
                        "destination": destination,
                    }
        except Exception as exc:
            print(f"[VOICE_ROUTER] parse_intent 异常: {exc}")
    return _fallback_intent(text)


# ==================== 模式切换 ====================
def switch_mode(mode: str) -> str:
    global CURRENT_MODE
    if mode not in ("qwen_chat", "amap_nav"):
        return CURRENT_MODE
    with _MODE_LOCK:
        if CURRENT_MODE != mode:
            CURRENT_MODE = mode
            print(f"[VOICE_ROUTER] 模式切换 -> {mode}")
    return CURRENT_MODE


# ==================== 聊天（带音频冲突处理） ====================
def ask_qwen(text: str) -> None:
    """交给千问聊天问答：千问说话期间暂停高德导航播报，说完恢复。"""
    audio_guard.qwen_begin()
    try:
        qwen_chat_reply(text)  # TODO(替换)
    finally:
        audio_guard.qwen_end()


# ==================== 路由主入口 ====================
def handle_voice(text: str) -> Dict:
    """ASR final 文本入口：解析意图并按模式路由。

    返回 dict，便于测试断言：
      {"mode": 当前模式, "action": 动作, "destination": 目的地或 None}
    """
    global last_action, last_destination
    text = (text or "").strip()
    if not text:
        return {"mode": CURRENT_MODE, "action": "none", "destination": None}

    intent = parse_intent(text)
    intent_name = intent.get("intent", "chat")
    action = intent.get("action", "")
    destination = intent.get("destination")

    # 1) 退出导航 → 停止导航 + 切回聊天
    if action == "stop_nav":
        last_action = "stop_nav"
        stop_amap_nav()  # TODO(替换)
        switch_mode("qwen_chat")
        return {"mode": CURRENT_MODE, "action": "stop_nav", "destination": None}

    # 2) 开始导航（带目的地则直接开导航；无目的地仅切模式）
    if intent_name == "nav":
        last_action = "start_nav"
        switch_mode("amap_nav")
        if destination:
            last_destination = destination
            start_amap_nav(destination)  # TODO(替换)
        return {"mode": CURRENT_MODE, "action": "start_nav", "destination": destination}

    # 3) 聊天：交给千问。导航模式下不停止导航，导航后台继续。
    last_action = "ask_qwen"
    ask_qwen(text)  # 内部已处理音频冲突
    return {"mode": CURRENT_MODE, "action": "ask_qwen", "destination": None}


# ==================== 测试 ====================
def run_tests(use_llm: bool = False) -> None:
    """运行 4 条给定测试语句（use_llm=False 走关键词 fallback，便于离线验证）。"""
    global _USE_LLM, CURRENT_MODE, last_destination, last_action
    _USE_LLM = use_llm

    def reset():
        global CURRENT_MODE, last_destination, last_action
        CURRENT_MODE = "qwen_chat"
        last_destination = None
        last_action = None

    def check(name, cond):
        print(("  ✅ PASS: " if cond else "  ❌ FAIL: ") + name)

    print("=" * 60)
    print("测试 1: 导航到西湖 → 切换导航模式，目的地=西湖")
    reset()
    r = handle_voice("导航到西湖")
    check("模式切到 amap_nav", r["mode"] == "amap_nav")
    check("目的地=西湖", last_destination == "西湖")
    print(f"  返回: {r}, 目的地记录: {last_destination}")

    print("=" * 60)
    print("测试 2: 退出导航，和我聊天 → 切回聊天模式，停止导航")
    r = handle_voice("退出导航，和我聊天")
    check("模式切回 qwen_chat", r["mode"] == "qwen_chat")
    check("动作=stop_nav", r["action"] == "stop_nav")
    print(f"  返回: {r}")

    print("=" * 60)
    print("测试 3: （导航途中）今天天气怎么样 → 导航继续，千问回答天气")
    # 模拟导航途中：先进入导航模式
    reset()
    switch_mode("amap_nav")
    r = handle_voice("今天天气怎么样")
    check("导航模式不中断(仍 amap_nav)", r["mode"] == "amap_nav")
    check("动作=ask_qwen(走千问)", r["action"] == "ask_qwen")
    print(f"  返回: {r}")

    print("=" * 60)
    print("测试 4: 切换导航模式带我去高铁站 → 切换导航模式，目的地高铁站")
    reset()
    r = handle_voice("切换导航模式带我去高铁站")
    check("模式切到 amap_nav", r["mode"] == "amap_nav")
    check("目的地=高铁站", last_destination == "高铁站")
    print(f"  返回: {r}, 目的地记录: {last_destination}")

    print("=" * 60)
    print("全部测试完成。")
    _USE_LLM = True  # 恢复默认


if __name__ == "__main__":
    run_tests(use_llm=False)
