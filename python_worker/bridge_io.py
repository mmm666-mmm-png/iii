# bridge_io.py
# -*- coding: utf-8 -*-
"""
视觉线程桥接模块。

app_main.py 在主事件循环中接收摄像头 JPEG；yolomedia 等传统同步算法在线程里运行。
这个模块用 Condition + 回调把两边解耦：
- push_raw_jpeg：主线程放入最新 JPEG；
- wait_raw_bgr：算法线程等待并取 BGR；
- send_vis_bgr：算法线程把标注图回传给前端。
"""
import threading
from collections import deque
import time
import cv2
import numpy as np

# 原始JPEG帧缓冲（只保留最新 N 帧）
_MAX_BUF = 4
_frames = deque(maxlen=_MAX_BUF)
_cond = threading.Condition()

# 向前端发送JPEG的回调，由 app_main.py 在启动时注册
_sender_lock = threading.Lock()
_sender_cb = None

# 向前端发送UI文本的回调（由 app_main.py 在启动时注册）
_ui_sender_lock = threading.Lock()
_ui_sender_cb = None

def set_sender(cb):
    """注册 JPEG 广播回调。cb 可能会从非 asyncio 线程调用。"""
    global _sender_cb
    with _sender_lock:
        _sender_cb = cb

def set_ui_sender(cb):
    """注册文本广播回调，供算法线程把提示推给 UI。"""
    global _ui_sender_cb
    with _ui_sender_lock:
        _ui_sender_cb = cb

def push_raw_jpeg(jpeg_bytes: bytes):
    """主线程收到摄像头帧后调用，只保留最近几帧，避免算法线程处理旧画面。"""
    if not jpeg_bytes:
        return
    with _cond:
        _frames.append((time.time(), jpeg_bytes))
        _cond.notify_all()

def wait_raw_bgr(timeout_sec: float = 0.5):
    """算法线程调用：等待并解码最新 JPEG 为 BGR；超时返回 None。"""
    t_end = time.time() + timeout_sec
    last = None
    while time.time() < t_end:
        with _cond:
            if _frames:
                last = _frames[-1]
        if last is None:
            time.sleep(0.01)
            continue
        # 解码JPEG为BGR
        ts, jpeg = last
        arr = np.frombuffer(jpeg, dtype=np.uint8)
        bgr = cv2.imdecode(arr, cv2.IMREAD_COLOR)
        if bgr is not None:
            # 在最源头进行镜像处理
            #bgr = cv2.flip(bgr, 1)
            return bgr
        # 解码失败，稍等重试
        time.sleep(0.01)
    return None

def send_vis_bgr(bgr, quality: int = 80):
    """算法线程调用：把处理后 BGR 编码成 JPEG 并推给前端 viewer。"""
    if bgr is None:
        return
    
    # 直接编码，不做任何增强处理
    ok, enc = cv2.imencode(".jpg", bgr, [int(cv2.IMWRITE_JPEG_QUALITY), int(quality)])
    if not ok:
        return
    with _sender_lock:
        cb = _sender_cb
    if cb:
        try:
            cb(enc.tobytes())
        except Exception:
            pass

def send_ui_final(text: str):
    """算法线程调用：把一条 UI 文案作为 final 文本推给前端。"""
    if not text:
        return
    with _ui_sender_lock:
        cb = _ui_sender_cb
    if cb:
        try:
            cb(str(text))
        except Exception:
            pass
