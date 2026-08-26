# app_main.py
# -*- coding: utf-8 -*-
"""
本地 Python Worker 入口。

它负责把 ESP32/Go 服务端送来的音视频流接入本机算法：
1. FastAPI 提供健康检查、视觉状态查询、视觉控制和单帧处理接口；
2. WebSocket 接收 ESP32 相机 JPEG 和麦克风 PCM，推送浏览器预览；
3. DashScope ASR 将上行语音转文字，并识别导航/寻物/问答命令；
4. Qwen Omni 负责普通多模态问答，返回文本和语音；
5. NavigationMaster 托管盲道、斑马线、红绿灯和找物品等视觉状态机。
"""
import os, sys, time, json, asyncio, base64, importlib

# ``audioop`` was removed from the standard library in Python 3.13.  Import it
# dynamically so type checkers do not report a missing import; install the
# ``audioop-lts`` package when running on Python 3.13+.
try:
    audioop = importlib.import_module("audioop")
except ModuleNotFoundError as exc:
    raise ModuleNotFoundError(
        "audioop is unavailable; install the compatible package with "
        "'python -m pip install audioop-lts'"
    ) from exc
from typing import Any, Dict, Optional, Tuple, List, Callable, Set, Deque
from collections import deque
from dataclasses import dataclass
import re
from contextlib import asynccontextmanager
# 在其它 import 之后加：
from qwen_extractor import extract_english_label
from navigation_master import NavigationMaster, OrchestratorResult 
# 新增：导入盲道导航器
from workflow_blindpath import BlindPathNavigator
# 新增：导入过马路导航器
from workflow_crossstreet import CrossStreetNavigator
import torch
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Request
from fastapi.responses import HTMLResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from starlette.websockets import WebSocketState
import uvicorn
import cv2
import numpy as np
from ultralytics import YOLO
from obstacle_detector_client import ObstacleDetectorClient

import torch  # 添加这行


import mediapipe as mp
import bridge_io
import threading
import yolomedia  # 确保和 app_main.py 同目录，文件名就是 yolomedia.py

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
# 摄像头实物安装方向与浏览器正向画面不同，因此视觉算法统一先旋转到“人眼正向”坐标。
VISION_INPUT_ROTATION = "cw90"

def local_path(*parts: str) -> str:
    """以 python_worker 目录为基准拼路径，避免启动目录不同导致找不到模型/资源。"""
    return os.path.join(BASE_DIR, *parts)

def orient_frame_for_vision(bgr):
    """把输入帧调整到视觉算法和浏览器预览一致的方向。"""
    if bgr is None:
        return bgr
    if VISION_INPUT_ROTATION in ("", "none", "0", "off", "disabled"):
        return bgr
    if VISION_INPUT_ROTATION in ("cw90", "clockwise90", "clockwise_90", "90", "right"):
        return cv2.rotate(bgr, cv2.ROTATE_90_CLOCKWISE)
    if VISION_INPUT_ROTATION in ("ccw90", "counterclockwise90", "counterclockwise_90", "-90", "left"):
        return cv2.rotate(bgr, cv2.ROTATE_90_COUNTERCLOCKWISE)
    if VISION_INPUT_ROTATION in ("180", "flip"):
        return cv2.rotate(bgr, cv2.ROTATE_180)
    return bgr

def encode_frame_jpeg(bgr, quality=80):
    """把 OpenCV BGR 图像编码成 JPEG ndarray。"""
    return cv2.imencode(".jpg", bgr, [int(cv2.IMWRITE_JPEG_QUALITY), quality])

def encode_frame_jpeg_bytes(bgr, quality=80):
    """把 BGR 图像编码成 JPEG bytes；失败返回空 bytes。"""
    ok, enc = encode_frame_jpeg(bgr, quality)
    return enc.tobytes() if ok else b""
# ---- Windows 事件循环策略 ----
if sys.platform.startswith("win"):
    try:
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    except Exception:
        pass

# ---- .env ----
for env_path in (
    os.path.join(BASE_DIR, ".env"),
    os.path.join(BASE_DIR, ".env.local"),
    os.path.join(BASE_DIR, "..", ".env"),
    os.path.join(BASE_DIR, "..", ".env.local"),
    os.path.join(BASE_DIR, "..", "server", ".env"),
    os.path.join(BASE_DIR, "..", "server", ".env.local"),
):
    if not os.path.exists(env_path):
        continue
    try:
        from dotenv import load_dotenv

        load_dotenv(env_path, override=False)
    except Exception:
        try:
            with open(env_path, "r", encoding="utf-8-sig") as handle:
                for raw_line in handle:
                    line = raw_line.strip()
                    if not line or line.startswith("#"):
                        continue
                    if line.startswith("export "):
                        line = line[7:].strip()
                    if "=" not in line:
                        continue
                    key, value = line.split("=", 1)
                    key = key.strip()
                    if not key or key in os.environ:
                        continue
                    value = value.strip().strip('"').strip("'")
                    if " #" in value:
                        value = value.split(" #", 1)[0].rstrip()
                    os.environ.setdefault(key, value)
        except Exception:
            pass

# ---- DashScope ASR 基础 ----
from dashscope import audio as dash_audio  # 若未安装，会在原项目里抛错提示

API_KEY = os.getenv("DASHSCOPE_API_KEY", "sk-a9440db694924559ae4ebdc2023d2b9a")
if not API_KEY:
    raise RuntimeError("未设置 DASHSCOPE_API_KEY")

MODEL        = "paraformer-realtime-v2"
SAMPLE_RATE  = 16000
AUDIO_FMT    = "pcm"
CHUNK_MS     = 20
BYTES_CHUNK  = SAMPLE_RATE * CHUNK_MS // 1000 * 2
SILENCE_20MS = bytes(BYTES_CHUNK)

# ---- 引入我们的模块 ----
from audio_stream import (
    register_stream_route,         # 挂 /stream.wav
    broadcast_pcm16_realtime,      # 实时向连接分发 16k PCM
    hard_reset_audio,              # 音频+AI 播放总闸
    BYTES_PER_20MS_16K,
    is_playing_now,
    current_ai_task,
)
from omni_client import stream_chat, OmniStreamPiece
from asr_core import (
    ASRCallback,
    set_current_recognition,
    stop_current_recognition,
)
from audio_player import initialize_audio_system, play_voice_text
from interfaces.api.route_endpoints import router as navigation_router

# ---- 同步录制器 ----
import sync_recorder
import signal
import atexit

@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用生命周期：启动时注册 bridge 回调并预加载语音，关闭时清理资源。"""
    # --- startup ---
    register_bridge_sender()
    start_audio_preload()
    yield
    # --- shutdown ---
    await shutdown_cleanup()


app = FastAPI(lifespan=lifespan)
app.include_router(navigation_router)

# ====== 状态与容器 ======
app.mount("/static", StaticFiles(directory="static"), name="static")

# 浏览器 WebUI 文本通道：用于推送 ASR partial/final、AI 文本和系统提示。
ui_clients: Dict[int, WebSocket] = {}
current_partial: str = ""
recent_finals: List[str] = []
RECENT_MAX = 50
# 最近相机 JPEG 帧缓存：Omni 问答时会拿最后一帧作为图像上下文。
last_frames: Deque[Tuple[float, bytes]] = deque(maxlen=10)

# 摄像头预览订阅者，以及当前连接的 ESP32 摄像头/音频 WebSocket。
camera_viewers: Set[WebSocket] = set()
esp32_camera_ws: Optional[WebSocket] = None
esp32_audio_ws: Optional[WebSocket] = None

# 【新增】盲道导航相关全局变量
blind_path_navigator = None
navigation_active = False
yolo_seg_model = None
obstacle_detector = None
_auto_obstacle_report_lock = threading.Lock()
_auto_obstacle_report_times: Dict[Tuple[str, int, int], float] = {}
AUTO_OBSTACLE_REPORT_COOLDOWN = float(os.getenv("AIGLASS_OBS_REPORT_COOLDOWN", "15"))


def report_detected_obstacle(obstacle: Dict[str, Any]) -> None:
    """保存视觉检测结果；无 GPS 时只保留上报记录，不伪造坐标。"""
    name = str(obstacle.get("name") or "unknown").strip().lower()
    center_x = float(obstacle.get("center_x", 0))
    center_y = float(obstacle.get("center_y", 0))
    key = (name, round(center_x / 80), round(center_y / 80))
    now = time.monotonic()
    with _auto_obstacle_report_lock:
        previous = _auto_obstacle_report_times.get(key, 0.0)
        if now - previous < AUTO_OBSTACLE_REPORT_COOLDOWN:
            return
        _auto_obstacle_report_times[key] = now

    try:
        from domain.model.obstacle import Obstacle
        from interfaces.api.route_endpoints import get_obstacle_repo

        confidence = float(obstacle.get("confidence", 0.8))
        get_obstacle_repo().add(Obstacle(
            device_id="esp32-glasses",
            location=None,
            obstacle_type=name,
            severity="medium",
            confidence=max(0.0, min(1.0, confidence)),
            description="视觉自动检测（无 GPS）",
        ))
        print(f"[OBSTACLE_REPORT] 自动上报: {name}（无 GPS）")
    except Exception as exc:
        print(f"[OBSTACLE_REPORT] 自动上报失败: {exc}")

# 【新增】过马路导航相关全局变量
cross_street_navigator = None
cross_street_active = False
orchestrator = None  # 新增
# 视觉状态机会被 HTTP 接口和摄像头 WebSocket 同时触发，使用普通线程锁保护。
vision_api_lock = threading.Lock()
# 提供给 Go 服务端和前端轮询的最近一次视觉状态摘要。
last_vision_result: Dict[str, Any] = {
    "ready": False,
    "state": "IDLE",
    "guidanceText": "",
    "lastError": "",
    "updatedAt": None,
}

# 【新增】omni对话状态标志
omni_conversation_active = False  # 标记omni对话是否正在进行
omni_previous_nav_state = None  # 保存omni激活前的导航状态，用于恢复

# 【新增】模型后台预热：把首次推理放到后台线程，避免 CPU 上 warmup 阻塞启动。
def _start_background_warmup():
    """后台预热模型；可用环境变量 AIGLASS_SKIP_MODEL_WARMUP=1 关闭。"""
    if os.getenv("AIGLASS_SKIP_MODEL_WARMUP", "0") == "1":
        return

    def _warmup():
        if yolo_seg_model is not None:
            try:
                test_img = np.zeros((640, 640, 3), dtype=np.uint8)
                _ = yolo_seg_model.predict(
                    test_img,
                    device="cuda" if torch.cuda.is_available() else "cpu",
                    verbose=False,
                )
                print("[NAVIGATION] 盲道分割模型后台预热完成")
            except Exception as e:
                print(f"[NAVIGATION] 盲道模型预热失败: {e}")
        if obstacle_detector is not None:
            try:
                test_img = np.zeros((640, 640, 3), dtype=np.uint8)
                cv2.rectangle(test_img, (200, 200), (400, 400), (255, 255, 255), -1)
                _ = obstacle_detector.detect(test_img)
                print("[NAVIGATION] YOLO-E 障碍物模型后台预热完成")
            except Exception as e:
                print(f"[NAVIGATION] YOLO-E 障碍物模型预热失败: {e}")

    threading.Thread(target=_warmup, daemon=True).start()


# 【新增】模型加载函数
def load_navigation_models():
    """
    加载导航相关模型。

    yolo_seg_model 用于盲道/斑马线分割；
    obstacle_detector 用 YOLOE 白名单提示词检测路径上的动态/静态障碍物。
    这里在应用启动阶段预加载，避免第一次进入导航时卡顿。
    """
    global yolo_seg_model, obstacle_detector

    try:
        seg_model_path = local_path("..", "AIGlasses_for_navigation", "yolo-seg.pt")
        #print(f"[NAVIGATION] 尝试加载模型: {seg_model_path}")

        if os.path.exists(seg_model_path):
            # 盲道与斑马线共用一个分割模型，类别 ID 在各工作流中通过环境变量/默认值绑定。
            print(f"[NAVIGATION] 模型文件存在，开始加载...")
            yolo_seg_model = YOLO(seg_model_path)

            # 强制放到 GPU
            if torch.cuda.is_available():
                yolo_seg_model.to("cuda")
                print(f"[NAVIGATION] 盲道分割模型加载成功并放到GPU: {yolo_seg_model.device}")
            else:
                print("[NAVIGATION] CUDA不可用，模型仍在CPU")

            # 首次推理已移至后台预热，避免 CPU 上 warmup 阻塞启动。
        else:
            print(f"[NAVIGATION] 错误：找不到模型文件: {seg_model_path}")
            print(f"[NAVIGATION] 当前工作目录: {os.getcwd()}")
            print(f"[NAVIGATION] 请检查文件路径是否正确")
            
        # 【修改开始】使用 ObstacleDetectorClient 替代直接的 YOLO
        obstacle_model_path = local_path("..", "AIGlasses_for_navigation", "yoloe-11l-seg.pt")
        print(f"[NAVIGATION] 尝试加载障碍物检测模型: {obstacle_model_path}")
        
        if os.path.exists(obstacle_model_path):
            print(f"[NAVIGATION] 障碍物检测模型文件存在，开始加载...")
            try:
                # 使用 ObstacleDetectorClient 封装的 YOLO-E
                obstacle_detector = ObstacleDetectorClient(model_path=obstacle_model_path)
                print(f"[NAVIGATION] ========== YOLO-E 障碍物检测器加载成功 ==========")
                
                # 检查模型是否成功加载
                if hasattr(obstacle_detector, 'model') and obstacle_detector.model is not None:
                    print(f"[NAVIGATION] YOLO-E 模型已初始化")
                    print(f"[NAVIGATION] 模型设备: {next(obstacle_detector.model.parameters()).device}")
                else:
                    print(f"[NAVIGATION] 警告：YOLO-E 模型初始化异常")
                
                # 检查白名单是否成功加载
                if hasattr(obstacle_detector, 'WHITELIST_CLASSES'):
                    print(f"[NAVIGATION] 白名单类别数: {len(obstacle_detector.WHITELIST_CLASSES)}")
                    print(f"[NAVIGATION] 白名单前10个类别: {', '.join(obstacle_detector.WHITELIST_CLASSES[:10])}")
                else:
                    print(f"[NAVIGATION] 警告：白名单类别未定义")
                
                # 检查文本特征是否成功预计算
                if hasattr(obstacle_detector, 'whitelist_embeddings') and obstacle_detector.whitelist_embeddings is not None:
                    print(f"[NAVIGATION] YOLO-E 文本特征已预计算")
                    print(f"[NAVIGATION] 文本特征张量形状: {obstacle_detector.whitelist_embeddings.shape if hasattr(obstacle_detector.whitelist_embeddings, 'shape') else '未知'}")
                else:
                    print(f"[NAVIGATION] 警告：YOLO-E 文本特征未预计算")
                
                # 首次推理已移至后台预热，避免 CPU 上 warmup 阻塞启动。
                print(f"[NAVIGATION] ========== YOLO-E 障碍物检测器加载完成 ==========")
                
            except Exception as e:
                print(f"[NAVIGATION] 障碍物检测器加载失败: {e}")
                import traceback
                traceback.print_exc()
                obstacle_detector = None
        else:
            print(f"[NAVIGATION] 警告：找不到障碍物检测模型文件: {obstacle_model_path}")
        
    except Exception as e:
        print(f"[NAVIGATION] 模型加载失败: {e}")
        import traceback
        traceback.print_exc()

    # 模型加载完成后，在后台线程做首次推理预热，不阻塞服务启动。
    _start_background_warmup()

# 在程序启动时加载模型
print("[NAVIGATION] 开始加载导航模型...")
load_navigation_models()
print(f"[NAVIGATION] 模型加载完成 - yolo_seg_model: {yolo_seg_model is not None}")

# 【新增】启动同步录制
print("[RECORDER] 启动同步录制系统...")
if sync_recorder.start_recording():
    print("[RECORDER] 录制系统已启动，将自动保存视频和音频")
else:
    print("[RECORDER] 录制系统未开启")

# 【新增】注册退出处理器，确保Ctrl+C时保存录制文件
def cleanup_on_exit():
    """程序退出时停止录制器，确保已缓存的视频/音频片段落盘。"""
    try:
        sync_recorder.stop_recording()
    except Exception as e:
        print(f"[SYSTEM] 关闭录制器时出错: {e}")

def signal_handler(sig, frame):
    """处理 Ctrl+C / 终止信号，走统一清理逻辑。"""
    print("\n[SYSTEM] 收到中断信号，正在安全退出...")
    cleanup_on_exit()
    import sys
    sys.exit(0)

# 注册信号处理器
signal.signal(signal.SIGINT, signal_handler)  # Ctrl+C
signal.signal(signal.SIGTERM, signal_handler)  # 终止信号
atexit.register(cleanup_on_exit)  # 正常退出时也调用

print("[RECORDER] 已注册退出处理器")



# 【新增】预加载红绿灯检测模型（避免进入WAIT_TRAFFIC_LIGHT状态时卡顿）
try:
    import trafficlight_detection
    print("[TRAFFIC_LIGHT] 开始预加载红绿灯检测模型...")
    if trafficlight_detection.init_model():
        print("[TRAFFIC_LIGHT] 红绿灯检测模型预加载成功")
        # 执行一次测试推理，完全预热模型
        try:
            test_img = np.zeros((640, 640, 3), dtype=np.uint8)
            _ = trafficlight_detection.process_single_frame(test_img)
            print("[TRAFFIC_LIGHT] 模型预热完成")
        except Exception as e:
            print(f"[TRAFFIC_LIGHT] 模型预热失败: {e}")
    else:
        print("[TRAFFIC_LIGHT] 红绿灯检测模型预加载失败")
except Exception as e:
    print(f"[TRAFFIC_LIGHT] 红绿灯模型预加载出错: {e}")

# ============== 关键：系统级"硬重置"总闸 =================
# 导航和问答都可能触发音频输出；中断/新一轮对话必须串行执行，避免旧音频继续播放。
interrupt_lock = asyncio.Lock()

# ============== YOLO媒体线程管理 =================
yolomedia_thread: Optional[threading.Thread] = None
yolomedia_stop_event = threading.Event()
yolomedia_running = False
yolomedia_sending_frames = False  # 新增：标记YOLO是否已经开始发送处理后的帧

# 物品名称到YOLO类别的映射
ITEM_TO_CLASS_MAP = {
    "红牛": "Red_Bull",
    "AD钙奶": "AD_milk",
    "ad钙奶": "AD_milk",
    "钙奶": "AD_milk",
}

async def ui_broadcast_raw(msg: str):
    """向所有 WebUI 文本客户端广播原始字符串，失败连接会被清理。"""
    dead = []
    for k, ws in list(ui_clients.items()):
        try:
            await ws.send_text(msg)
        except Exception:
            dead.append(k)
    for k in dead:
        ui_clients.pop(k, None)


async def ui_broadcast_partial(text: str):
    """广播 ASR/AI 临时文本。partial 只用于页面展示，不写入历史 final。"""
    global current_partial
    current_partial = text
    await ui_broadcast_raw("PARTIAL:" + text)

async def ui_broadcast_final(text: str):
    """广播最终文本，并保留最近若干条供新连接初始化。"""
    global current_partial, recent_finals
    current_partial = ""
    recent_finals.append(text)
    if len(recent_finals) > RECENT_MAX:
        recent_finals = recent_finals[-RECENT_MAX:]
    await ui_broadcast_raw("FINAL:" + text)
    print(f"[ASR/AI FINAL] {text}", flush=True)

async def full_system_reset(reason: str = ""):
    """
    回到刚启动后的状态：
    1) 停播 + 取消AI任务 + 切断所有/stream.wav（hard_reset_audio）
    2) 停止 ASR 实时识别流（关键）
    3) 清 UI 状态
    4) 清最近相机帧（避免把旧帧又拼进下一轮）
    5) 告知 ESP32：RESET（可选）
    """
    # 1) 音频&AI
    await hard_reset_audio(reason or "full_system_reset")

    # 2) ASR
    await stop_current_recognition()

    # 3) UI
    global current_partial, recent_finals
    current_partial = ""
    recent_finals = []

    # 4) 相机帧
    try:
        last_frames.clear()
    except Exception:
        pass

    # 5) 通知 ESP32
    try:
        if esp32_audio_ws and (esp32_audio_ws.client_state == WebSocketState.CONNECTED):
            await esp32_audio_ws.send_text("RESET")
    except Exception:
        pass

    print("[SYSTEM] full reset done.", flush=True)

# ========= 启动/停止 YOLO 媒体处理 =========
def start_yolomedia_with_target(target_name: str):
    """启动 yolomedia 后台线程，按目标类别做寻物/抓取引导。"""
    global yolomedia_thread, yolomedia_stop_event, yolomedia_running, yolomedia_sending_frames
    
    # 如果已经在运行，先停止
    if yolomedia_running:
        stop_yolomedia()
    
    # 查找对应的YOLO类别
    yolo_class = ITEM_TO_CLASS_MAP.get(target_name, target_name)
    print(f"[YOLOMEDIA] Starting with target: {target_name} -> YOLO class: {yolo_class}", flush=True)
    print(f"[YOLOMEDIA] Available mappings: {ITEM_TO_CLASS_MAP}", flush=True)  # 添加这行调试
    
    yolomedia_stop_event.clear()
    yolomedia_running = True
    yolomedia_sending_frames = False  # 重置发送帧状态
    
    def _run():
        # yolomedia 内部会从 bridge_io.wait_raw_bgr 取最新帧，
        # 处理后的画面再通过 bridge_io.send_vis_bgr 推回浏览器。
        try:
            # 传递目标类别名和停止事件
            yolomedia.main(headless=True, prompt_name=yolo_class, stop_event=yolomedia_stop_event)
        except Exception as e:
            print(f"[YOLOMEDIA] worker stopped: {e}", flush=True)
        finally:
            global yolomedia_running, yolomedia_sending_frames
            yolomedia_running = False
            yolomedia_sending_frames = False
    
    yolomedia_thread = threading.Thread(target=_run, daemon=True)
    yolomedia_thread.start()
    print(f"[YOLOMEDIA] background worker started for: {yolo_class}（正在初始化，暂时显示原始画面）", flush=True)

def stop_yolomedia():
    """停止寻物线程，并清空“寻物正在接管画面”的标志。"""
    global yolomedia_thread, yolomedia_stop_event, yolomedia_running, yolomedia_sending_frames
    
    if yolomedia_running:
        print("[YOLOMEDIA] Stopping worker...", flush=True)
        yolomedia_stop_event.set()
        
        # 等待线程结束（最多等5秒）
        if yolomedia_thread and yolomedia_thread.is_alive():
            yolomedia_thread.join(timeout=5.0)
        
        yolomedia_running = False
        yolomedia_sending_frames = False
        
        # 【新增】如果orchestrator在找物品模式，结束时不自动恢复（由命令控制）
        # 只清理标志位即可
        print("[YOLOMEDIA] Worker stopped, 等待状态切换.", flush=True)


def ensure_vision_stack() -> Tuple[bool, str]:
    """
    懒初始化视觉处理链路。

    Go 服务端可能先调用 /api/vision/status 或 /api/vision/process；
    因此这里按需创建 BlindPathNavigator、CrossStreetNavigator 和 NavigationMaster。
    """
    global blind_path_navigator, cross_street_navigator, orchestrator

    if yolo_seg_model is None:
        return False, "盲道模型未加载，无法进行导航推理"

    if blind_path_navigator is None:
        blind_path_navigator = BlindPathNavigator(
            yolo_seg_model, obstacle_detector, report_detected_obstacle
        )
        print("[VISION_API] 盲道导航器已初始化")

    if cross_street_navigator is None:
        cross_street_navigator = CrossStreetNavigator(
            seg_model=yolo_seg_model,
            coco_model=None,
            obs_model=None,
            obstacle_reporter=report_detected_obstacle,
        )
        print("[VISION_API] 过马路导航器已初始化")

    if orchestrator is None:
        orchestrator = NavigationMaster(blind_path_navigator, cross_street_navigator)
        print("[VISION_API] 统领状态机已初始化")

    return True, ""


def _vision_status_payload() -> Dict[str, Any]:
    """组装对外可序列化的视觉状态，供前端和 Go 服务端展示。"""
    state = orchestrator.get_state() if orchestrator else "IDLE"
    payload = dict(last_vision_result)
    payload.update({
        "ready": orchestrator is not None and yolo_seg_model is not None,
        "state": state,
        "yolomediaRunning": yolomedia_running,
        "models": {
            "blindPath": yolo_seg_model is not None,
            "obstacle": obstacle_detector is not None,
        },
    })
    return payload


def _set_vision_result(**values):
    """更新最近一次视觉处理结果，并写入 UTC 时间戳。"""
    last_vision_result.update(values)
    last_vision_result["updatedAt"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def is_any_audio_playing() -> bool:
    """当前是否有任何系统语音在播报（Omni 问答 或 本地导航/TTS/预录语音）。

    播报期间 ASR final 不触发千问，避免导航播报回声被麦克风拾取后
    又被识别成输入、引发「自问自答」。
    """
    if is_playing_now():
        return True
    try:
        from audio_player import is_audio_playing

        return is_audio_playing()
    except Exception:
        return False


# ========= 高德导航语音会话路由 =========
_AMAP_NAV_START_WORDS = ("导航到", "导航去", "带我去", "前往")
_AMAP_NAV_STOP_WORDS = ("退出导航", "关闭导航", "关掉导航", "切换聊天", "切回聊天", "切到聊天")
_voice_session_mgr = None


def get_voice_session_manager():
    """懒加载 VoiceSessionManager 单例（已接好真实底层）。"""
    global _voice_session_mgr
    if _voice_session_mgr is None:
        from voice_session_wiring import build_voice_session_manager
        _voice_session_mgr = build_voice_session_manager()
    return _voice_session_mgr


async def _try_route_amap_session(user_text: str) -> bool:
    """把高德导航相关语音路由到 VoiceSessionManager，返回是否已拦截。

    拦截规则：
    - 「导航到/导航去/带我去/前往 XX」→ 启动高德导航；
    - 「退出导航/关闭导航/切换聊天…」→ 退出高德导航；
    - 当前已在高德导航会话(mode==amap_nav)时，其余语音也交给它
      （暂停导航 → 千问回答 → 恢复导航）。
    """
    text = (user_text or "").strip()
    if not text:
        return False
    try:
        from voice_session_manager import MODE_AMAP_NAV
    except Exception as exc:
        print(f"[AMAP] 会话管理器导入失败: {exc}")
        return False

    mgr = get_voice_session_manager()
    is_start = any(w in text for w in _AMAP_NAV_START_WORDS)
    is_stop = any(w in text for w in _AMAP_NAV_STOP_WORDS)
    if not (is_start or is_stop or mgr.mode == MODE_AMAP_NAV):
        return False

    try:
        result = await mgr.handle_asr_final(text)
        action = result.get("action")
        if action == "start_nav":
            print(f"[AMAP] 高德导航 -> {result.get('destination') or ''}")
        elif action == "stop_nav":
            print("[AMAP] 退出高德导航")
        else:
            print("[AMAP] 导航中语音 -> 千问")
        return True
    except Exception as exc:
        print(f"[AMAP] 会话处理异常: {exc}")
        return False

# ========= 自定义的 start_ai_with_text，支持识别特殊命令 =========
async def start_ai_with_text_custom(user_text: str):
    """
    ASR final 文本的总入口。

    先识别导航控制、红绿灯检测、寻物等本地命令；
    只有无法命中特殊命令时，才进入 Qwen Omni 普通多模态问答。
    """
    global navigation_active, blind_path_navigator, cross_street_active, cross_street_navigator, orchestrator
    
    # 【新增】高德导航会话路由（导航到XX / 退出导航 / 导航中语音）
    if await _try_route_amap_session(user_text):
        return

    # 【修改】在导航模式和红绿灯检测模式下，只有特定词才进入omni对话
    if orchestrator:
        current_state = orchestrator.get_state()
        # 如果在导航模式或红绿灯检测模式（非CHAT模式）
        if current_state not in ["CHAT", "IDLE"]:
            # 导航过程中防止路人语音或环境噪声频繁打断，
            # 只允许明确问答触发词和导航控制词进入后续处理。
            # 检查是否是允许的对话触发词
            allowed_keywords = ["帮我看", "帮我看下", "帮我找", "找一下", "看看", "识别一下"]
            is_allowed_query = any(keyword in user_text for keyword in allowed_keywords)
            
            # 检查是否是导航控制命令
            nav_control_keywords = ["开始过马路", "过马路结束", "开始导航", "盲道导航", "停止导航", "结束导航", 
                                   "检测红绿灯", "看红绿灯", "停止检测", "停止红绿灯"]
            is_nav_control = any(keyword in user_text for keyword in nav_control_keywords)
            
            # 如果既不是允许的查询，也不是导航控制命令，则丢弃
            if not is_allowed_query and not is_nav_control:
                mode_name = "红绿灯检测" if current_state == "TRAFFIC_LIGHT_DETECTION" else "导航"
                print(f"[{mode_name}模式] 丢弃非对话语音: {user_text}")
                return  # 直接丢弃，不进入omni
    
    # 【修改】检查是否是过马路相关命令 - 使用orchestrator控制
    if "开始过马路" in user_text or "帮我过马路" in user_text:
        # 【新增】如果正在找物品，先停止
        if yolomedia_running:
            stop_yolomedia()
            print("[ITEM_SEARCH] 从找物品模式切换到过马路")
        
        if orchestrator:
            orchestrator.start_crossing()
            print(f"[CROSS_STREET] 过马路模式已启动，状态: {orchestrator.get_state()}")
            # 播放启动语音并广播到UI
            play_voice_text("过马路模式已启动。")
            await ui_broadcast_final("[系统] 过马路模式已启动")
        else:
            print("[CROSS_STREET] 警告：导航统领器未初始化！")
            play_voice_text("启动过马路模式失败，请稍后重试。")
            await ui_broadcast_final("[系统] 导航系统未就绪")
        return
    
    if "过马路结束" in user_text or "结束过马路" in user_text:
        if orchestrator:
            orchestrator.stop_navigation()
            print(f"[CROSS_STREET] 导航已停止，状态: {orchestrator.get_state()}")
            # 播放停止语音并广播到UI
            play_voice_text("已停止导航。")
            await ui_broadcast_final("[系统] 过马路模式已停止")
        else:
            await ui_broadcast_final("[系统] 导航系统未运行")
        return
    
    # 【修改】检查是否是红绿灯检测命令 - 实现与盲道导航互斥
    if "检测红绿灯" in user_text or "看红绿灯" in user_text:
        try:
            import trafficlight_detection
            
            # 切换orchestrator到红绿灯检测模式（暂停盲道导航）
            if orchestrator:
                orchestrator.start_traffic_light_detection()
                print(f"[TRAFFIC] 切换到红绿灯检测模式，状态: {orchestrator.get_state()}")
            
            # 【改进】使用主线程模式而不是独立线程，避免掉帧
            success = trafficlight_detection.init_model()  # 只初始化模型，不启动线程
            trafficlight_detection.reset_detection_state()  # 重置状态
            
            if success:
                await ui_broadcast_final("[系统] 红绿灯检测已启动")
            else:
                await ui_broadcast_final("[系统] 红绿灯模型加载失败")
        except Exception as e:
            print(f"[TRAFFIC] 启动红绿灯检测失败: {e}")
            await ui_broadcast_final(f"[系统] 启动失败: {e}")
        return
    
    if "停止检测" in user_text or "停止红绿灯" in user_text:
        try:
            # 恢复到对话模式
            if orchestrator:
                orchestrator.stop_navigation()  # 回到CHAT模式
                print(f"[TRAFFIC] 红绿灯检测停止，恢复到{orchestrator.get_state()}模式")
            
            await ui_broadcast_final("[系统] 红绿灯检测已停止")
        except Exception as e:
            print(f"[TRAFFIC] 停止红绿灯检测失败: {e}")
            await ui_broadcast_final(f"[系统] 停止失败: {e}")
        return
    
    # 【修改】检查是否是导航相关命令 - 使用orchestrator控制
    if "开始导航" in user_text or "盲道导航" in user_text or "帮我导航" in user_text:
        # 【新增】如果正在找物品，先停止
        if yolomedia_running:
            stop_yolomedia()
            print("[ITEM_SEARCH] 从找物品模式切换到盲道导航")
        
        if orchestrator:
            orchestrator.start_blind_path_navigation()
            print(f"[NAVIGATION] 盲道导航已启动，状态: {orchestrator.get_state()}")
            await ui_broadcast_final("[系统] 盲道导航已启动")
        else:
            print("[NAVIGATION] 警告：导航统领器未初始化！")
            await ui_broadcast_final("[系统] 导航系统未就绪")
        return
    
    if "停止导航" in user_text or "结束导航" in user_text:
        if orchestrator:
            orchestrator.stop_navigation()
            print(f"[NAVIGATION] 导航已停止，状态: {orchestrator.get_state()}")
            await ui_broadcast_final("[系统] 盲道导航已停止")
        else:
            await ui_broadcast_final("[系统] 导航系统未运行")
        return

    nav_cmd_keywords = ["开始过马路", "过马路结束", "开始导航", "盲道导航", "停止导航", "结束导航", "立即通过", "现在通过", "继续"]
    if any(k in user_text for k in nav_cmd_keywords):
        if orchestrator:
            orchestrator.on_voice_command(user_text)
            await ui_broadcast_final("[系统] 导航模式已更新")
        else:
            await ui_broadcast_final("[系统] 导航统领器未初始化")
        return    

    # 检查是否是"帮我找/识别一下xxx"的命令
    # 扩展正则表达式，支持更多关键词
    find_pattern = r"(?:^\s*帮我)?\s*找一下\s*(.+?)(?:。|！|？|$)"
    match = re.search(find_pattern, user_text)
        
    if match:
        # 提取中文物品名称
        item_cn = match.group(1).strip()
        if item_cn:
            # 【新增】用本地映射 + Qwen 提取英文类名
            label_en, src = extract_english_label(item_cn)
            print(f"[COMMAND] Finder request: '{item_cn}' -> '{label_en}' (src={src})", flush=True)

            # 【新增】切换到找物品模式（暂停导航）
            if orchestrator:
                orchestrator.start_item_search()
                print(f"[ITEM_SEARCH] 已切换到找物品模式，状态: {orchestrator.get_state()}")
            
            # 【关键】把英文类名传给 yolomedia（它会在找不到类时自动切 YOLOE）
            start_yolomedia_with_target(label_en)

            # 给前端/语音来个确认反馈
            try:
                await ui_broadcast_final(f"[找物品] 正在寻找 {item_cn}...")
            except Exception:
                pass

            return
    
    # 检查是否是"找到了"的命令
    if "找到了" in user_text or "拿到了" in user_text:
        print("[COMMAND] Found command detected", flush=True)
        # 停止yolomedia
        stop_yolomedia()
        
        # 【新增】停止找物品模式，恢复之前的导航状态
        if orchestrator:
            orchestrator.stop_item_search(restore_nav=True)
            current_state = orchestrator.get_state()
            print(f"[ITEM_SEARCH] 找物品结束，当前状态: {current_state}")
            
            # 根据恢复的状态给出反馈
            if current_state in ["BLINDPATH_NAV", "SEEKING_CROSSWALK", "WAIT_TRAFFIC_LIGHT", "CROSSING", "SEEKING_NEXT_BLINDPATH"]:
                await ui_broadcast_final("[找物品] 已找到物品，继续导航。")
            else:
                await ui_broadcast_final("[找物品] 已找到物品。")
        else:
            await ui_broadcast_final("[找物品] 已找到物品。")
        
        return
    
    # 【修改】omni对话开始时，切换到CHAT模式
    global omni_conversation_active, omni_previous_nav_state
    omni_conversation_active = True
    
    # 保存当前导航状态并切换到CHAT模式
    if orchestrator:
        current_state = orchestrator.get_state()
        # 只有在导航模式下才需要保存和切换
        if current_state not in ["CHAT", "IDLE"]:
            omni_previous_nav_state = current_state
            orchestrator.force_state("CHAT")
            print(f"[OMNI] 对话开始，从{current_state}切换到CHAT模式")
        else:
            omni_previous_nav_state = None
            print(f"[OMNI] 对话开始（当前已在{current_state}模式）")
    
    # 如果不是特殊命令，执行原有的AI对话逻辑
    # 但如果yolomedia正在运行，暂时不处理普通对话
    if yolomedia_running:
        print("[AI] YOLO media is running, skipping normal AI response", flush=True)
        return
    
    # 原有的AI对话逻辑
    await start_ai_with_text(user_text)

# ========= Omni 播放启动 =========
async def start_ai_with_text(user_text: str):
    """
    发起普通 Omni 多模态问答。

    会把最近一帧图像和用户文本一起发给模型；模型返回的 24k 音频会重采样成 8k PCM，
    再通过 /stream.wav 下行给浏览器或设备播放链路。
    """
    async def _runner():
        txt_buf: List[str] = []
        rate_state = None

        # 组装（图像+文本）
        content_list = []
        if last_frames:
            try:
                # 只取最后一帧，降低请求体大小，保证问答关注当前场景。
                _, jpeg_bytes = last_frames[-1]
                img_b64 = base64.b64encode(jpeg_bytes).decode("ascii")
                content_list.append({
                    "type": "image_url",
                    "image_url": {"url": f"data:image/jpeg;base64,{img_b64}"}
                })
            except Exception:
                pass
        content_list.append({"type": "text", "text": user_text})

        try:
            async for piece in stream_chat(content_list, voice="Cherry", audio_format="wav"):
                # 文本增量（仅 UI）
                if piece.text_delta:
                    txt_buf.append(piece.text_delta)
                    try:
                        await ui_broadcast_partial("[AI] " + "".join(txt_buf))
                    except Exception:
                        pass

                # 音频分片：Omni 返回 24k (PCM16) 的 wav audio.data（Base64）；下行需要 8k PCM16
                if piece.audio_b64:
                    try:
                        pcm24 = base64.b64decode(piece.audio_b64)
                    except Exception:
                        pcm24 = b""
                    if pcm24:
                        # 24k → 8k (使用ratecv保证音调和速度不变)
                        pcm8k, rate_state = audioop.ratecv(pcm24, 2, 1, 24000, 8000, rate_state)
                        pcm8k = audioop.mul(pcm8k, 2, 0.60)
                        if pcm8k:
                            await broadcast_pcm16_realtime(pcm8k)

        except asyncio.CancelledError:
            # 被新一轮打断
            raise
        except Exception as e:
            try:
                await ui_broadcast_final(f"[AI] 发生错误：{e}")
            except Exception:
                pass
        finally:
            # 【修改】标记omni对话结束，恢复之前的导航模式
            global omni_conversation_active, omni_previous_nav_state
            omni_conversation_active = False
            
            # 恢复之前的导航状态
            if orchestrator and omni_previous_nav_state:
                orchestrator.force_state(omni_previous_nav_state)
                print(f"[OMNI] 对话结束，恢复到{omni_previous_nav_state}模式")
                omni_previous_nav_state = None
            else:
                print(f"[OMNI] 对话结束（无需恢复导航状态）")
            
            # 自然结束时，给当前连接一个 "完结" 信号
            from audio_stream import stream_clients  # 局部导入，避免环依赖
            for sc in list(stream_clients):
                if not sc.abort_event.is_set():
                    try: sc.q.put_nowait(b"\x00"*BYTES_PER_20MS_16K)  # 一帧静音
                    except Exception: pass
                    try: sc.q.put_nowait(None)
                    except Exception: pass

            final_text = ("".join(txt_buf)).strip() or "（空响应）"
            try:
                await ui_broadcast_final("[AI] " + final_text)
            except Exception:
                pass

    # 真正启动前先硬重置，保证**绝无**旧音频残留
    await hard_reset_audio("start_ai_with_text")
    loop = asyncio.get_running_loop()
    from audio_stream import current_ai_task as _task_holder  # 读写模块内全局
    from audio_stream import __dict__ as _as_dict
    # 设置模块内的 current_ai_task
    task = loop.create_task(_runner())
    _as_dict["current_ai_task"] = task

# ---------- 页面 / 健康 ----------
@app.get("/", response_class=HTMLResponse)
def root():
    """返回内置调试页面。生产前端通常走 esp32-glass-front 或 Go 静态页。"""
    with open(os.path.join("templates", "index.html"), "r", encoding="utf-8") as f:
        return HTMLResponse(f.read())

@app.get("/api/health", response_class=PlainTextResponse)
def health():
    return "OK"

@app.get("/api/vision/status")
def vision_status():
    """查询视觉 worker 是否就绪，以及当前导航/寻物状态。"""
    ok, message = ensure_vision_stack()
    if not ok:
        _set_vision_result(ready=False, lastError=message)
    return _vision_status_payload()

@app.post("/api/vision/control")
async def vision_control(request: Request):
    """外部控制视觉状态机：开始盲道、过马路、红绿灯、找物品、停止或重置。"""
    global orchestrator
    try:
        payload = await request.json()
    except Exception:
        payload = {}

    command = str(payload.get("command") or payload.get("mode") or "").strip()
    target = str(payload.get("target") or "").strip()
    ok, message = ensure_vision_stack()
    if not ok:
        _set_vision_result(ready=False, lastError=message)
        return {"ok": False, "error": message, **_vision_status_payload()}

    with vision_api_lock:
        normalized = command.lower().replace("-", "_")
        try:
            # 前端和 Go 服务端可能使用不同命名，这里统一归一化成少量内部命令。
            if normalized in ("start_blind_navigation", "blind_navigation", "blind", "start_blind"):
                if yolomedia_running:
                    stop_yolomedia()
                orchestrator.start_blind_path_navigation()
                guidance = "盲道导航已启动"
            elif normalized in ("start_crossing", "crossing", "crosswalk"):
                if yolomedia_running:
                    stop_yolomedia()
                orchestrator.start_crossing()
                guidance = "过马路模式已启动"
            elif normalized in ("detect_traffic_light", "traffic_light"):
                if yolomedia_running:
                    stop_yolomedia()
                orchestrator.start_traffic_light_detection()
                guidance = "红绿灯检测已启动"
            elif normalized in ("find_object", "item_search", "search_item"):
                if not target:
                    return {"ok": False, "error": "target is required", **_vision_status_payload()}
                orchestrator.start_item_search()
                start_yolomedia_with_target(target)
                guidance = f"正在寻找 {target}"
            elif normalized in ("stop", "stop_navigation", "idle", "chat"):
                if yolomedia_running:
                    stop_yolomedia()
                orchestrator.stop_navigation()
                guidance = "导航已停止"
            elif normalized in ("reset", "clear"):
                if yolomedia_running:
                    stop_yolomedia()
                orchestrator.reset()
                guidance = "视觉状态已重置"
            else:
                return {"ok": False, "error": f"unknown command: {command}", **_vision_status_payload()}

            _set_vision_result(ready=True, state=orchestrator.get_state(), guidanceText=guidance, lastError="")
            return {"ok": True, "guidanceText": guidance, **_vision_status_payload()}
        except Exception as e:
            _set_vision_result(lastError=str(e))
            return {"ok": False, "error": str(e), **_vision_status_payload()}

@app.post("/api/vision/process")
async def vision_process(request: Request):
    """
    Go 服务端调用的单帧视觉处理接口。

    请求体是 JPEG bytes，响应包含状态、语音引导文本和标注图 base64。
    """
    ok, message = ensure_vision_stack()
    if not ok:
        _set_vision_result(ready=False, lastError=message)
        return {"ok": False, "error": message, **_vision_status_payload()}

    jpeg_data = await request.body()
    if not jpeg_data:
        return {"ok": False, "error": "empty image payload", **_vision_status_payload()}

    with vision_api_lock:
        try:
            arr = np.frombuffer(jpeg_data, dtype=np.uint8)
            bgr = cv2.imdecode(arr, cv2.IMREAD_COLOR)
            if bgr is None or bgr.size == 0:
                return {"ok": False, "error": "invalid jpeg payload", **_vision_status_payload()}
            bgr = orient_frame_for_vision(bgr)
            oriented_jpeg = encode_frame_jpeg_bytes(bgr)
            bridge_io.push_raw_jpeg(oriented_jpeg or jpeg_data)

            current_state = orchestrator.get_state() if orchestrator else "IDLE"
            guidance_text = ""
            out_img = bgr

            if current_state == "ITEM_SEARCH":
                # 寻物线程独立消费 bridge_io 中的原始帧，本接口只维护状态和兜底画面。
                # yolomedia 仍在后台消费 bridge_io；这里保持最近原始帧作为兜底预览。
                guidance_text = last_vision_result.get("guidanceText", "")
            elif current_state == "TRAFFIC_LIGHT_DETECTION":
                import trafficlight_detection
                result = trafficlight_detection.process_single_frame(bgr)
                out_img = result.get("vis_image")
                if out_img is None:
                    out_img = bgr
                stable_light = result.get("stable_light")
                if stable_light:
                    guidance_text = {"red": "红灯", "green": "绿灯", "yellow": "黄灯"}.get(stable_light, str(stable_light))
            elif orchestrator is not None:
                res = orchestrator.process_frame(bgr)
                out_img = res.annotated_image if res.annotated_image is not None else bgr
                guidance_text = res.guidance_text or ""

            ok_enc, enc = cv2.imencode(".jpg", out_img, [int(cv2.IMWRITE_JPEG_QUALITY), 80])
            image_b64 = base64.b64encode(enc.tobytes()).decode("ascii") if ok_enc else ""
            _set_vision_result(
                ready=True,
                state=orchestrator.get_state() if orchestrator else current_state,
                guidanceText=guidance_text,
                lastError="",
            )
            return {
                "ok": True,
                "state": orchestrator.get_state() if orchestrator else current_state,
                "guidanceText": guidance_text,
                "annotatedImageBase64": image_b64,
                "imageFormat": "jpeg",
                "yolomediaRunning": yolomedia_running,
            }
        except Exception as e:
            _set_vision_result(lastError=str(e))
            return {"ok": False, "error": str(e), **_vision_status_payload()}

# 注册 /stream.wav
register_stream_route(app)

# ---------- WebSocket：WebUI 文本（ASR/AI 状态推送） ----------
@app.websocket("/ws_ui")
async def ws_ui(ws: WebSocket):
    """浏览器文本 WebSocket：接收 ASR、AI、导航提示等文本事件。"""
    await ws.accept()
    ui_clients[id(ws)] = ws
    try:
        init = {"partial": current_partial, "finals": recent_finals[-10:]}
        await ws.send_text("INIT:" + json.dumps(init, ensure_ascii=False))
        while True:
            await asyncio.sleep(60)
    except WebSocketDisconnect:
        pass
    finally:
        ui_clients.pop(id(ws), None)

# ---------- WebSocket：ESP32 音频入口（ASR 上行） ----------
@app.websocket("/ws_audio")
async def ws_audio(ws: WebSocket):
    """
    ESP32/浏览器音频入口。

    文本 START/STOP 控制 DashScope 实时 ASR 生命周期；
    二进制消息是 PCM 音频帧，直接送给 Recognition.send_audio_frame。
    """
    global esp32_audio_ws
    esp32_audio_ws = ws
    await ws.accept()
    print("\n[AUDIO] client connected")
    recognition = None
    streaming = False
    last_ts = time.monotonic()
    keepalive_task: Optional[asyncio.Task] = None

    async def stop_rec(send_notice: Optional[str] = None):
        """停止当前 ASR 识别流，并可选向设备返回状态文本。"""
        nonlocal recognition, streaming, keepalive_task
        if keepalive_task and not keepalive_task.done():
            keepalive_task.cancel()
            try: await keepalive_task
            except Exception: pass
        keepalive_task = None
        if recognition:
            try: recognition.stop()
            except Exception: pass
            recognition = None
        await set_current_recognition(None)
        streaming = False
        if send_notice:
            try: await ws.send_text(send_notice)
            except Exception: pass

    async def on_sdk_error(_msg: str):
        await stop_rec(send_notice="RESTART")

    async def keepalive_loop():
        """在麦克风短暂无数据时补静音，防止 ASR 服务端过早断流。"""
        nonlocal last_ts, recognition, streaming
        try:
            while streaming and recognition is not None:
                idle = time.monotonic() - last_ts
                if idle > 0.35:
                    try:
                        for _ in range(30):  # ~600ms 静音
                            recognition.send_audio_frame(SILENCE_20MS)
                        last_ts = time.monotonic()
                    except Exception:
                        await on_sdk_error("keepalive send failed")
                        return
                await asyncio.sleep(0.10)
        except asyncio.CancelledError:
            return

    try:
        while True:
            if WebSocketState and ws.client_state != WebSocketState.CONNECTED:
                break
            try:
                msg = await ws.receive()
            except WebSocketDisconnect:
                break
            except RuntimeError as e:
                if "Cannot call \"receive\"" in str(e):
                    break
                raise

            if "text" in msg and msg["text"] is not None:
                raw = (msg["text"] or "").strip()
                cmd = raw.upper()

                if cmd == "START":
                    print("[AUDIO] START received")
                    await stop_rec()
                    loop = asyncio.get_running_loop()
                    def post(coro):
                        asyncio.run_coroutine_threadsafe(coro, loop)

                    # 组装 ASR 回调（把依赖都注入）
                    cb = ASRCallback(
                        on_sdk_error=lambda s: post(on_sdk_error(s)),
                        post=post,
                        ui_broadcast_partial=ui_broadcast_partial,
                        ui_broadcast_final=ui_broadcast_final,
                        is_playing_now_fn=is_any_audio_playing,
                        start_ai_with_text_fn=start_ai_with_text_custom,  # 使用自定义版本
                        full_system_reset_fn=full_system_reset,
                        interrupt_lock=interrupt_lock,
                    )

                    recognition = dash_audio.asr.Recognition(
                        api_key=API_KEY, model=MODEL, format=AUDIO_FMT,
                        sample_rate=SAMPLE_RATE, callback=cb
                    )
                    recognition.start()
                    await set_current_recognition(recognition)
                    streaming = True
                    last_ts = time.monotonic()
                    keepalive_task = asyncio.create_task(keepalive_loop())
                    await ui_broadcast_partial("（已开始接收音频…）")
                    await ws.send_text("OK:STARTED")

                elif cmd == "STOP":
                    if recognition:
                        for _ in range(15):  # ~300ms 静音
                            try: recognition.send_audio_frame(SILENCE_20MS)
                            except Exception: break
                    await stop_rec(send_notice="OK:STOPPED")

                elif raw.startswith("PROMPT:"):
                    # 设备端主动发起一轮：同样使用“先硬重置后播放”的强语义
                    text = raw[len("PROMPT:"):].strip()
                    if text:
                        async with interrupt_lock:
                            await start_ai_with_text_custom(text) # 使用自定义的启动函数
                        await ws.send_text("OK:PROMPT_ACCEPTED")
                    else:
                        await ws.send_text("ERR:EMPTY_PROMPT")

            elif "bytes" in msg and msg["bytes"] is not None:
                if streaming and recognition:
                    try:
                        recognition.send_audio_frame(msg["bytes"])
                        last_ts = time.monotonic()
                    except Exception:
                        await on_sdk_error("send_audio_frame failed")

    except Exception as e:
        print(f"\n[WS ERROR] {e}")
    finally:
        await stop_rec()
        try:
            if WebSocketState is None or ws.client_state == WebSocketState.CONNECTED:
                await ws.close(code=1000)
        except Exception:
            pass
        if esp32_audio_ws is ws:
            esp32_audio_ws = None
        print("[WS] connection closed")

# ---------- WebSocket：ESP32 相机入口（JPEG 二进制） ----------
@app.websocket("/ws/camera")
async def ws_camera_esp(ws: WebSocket):
    """
    ESP32 相机 JPEG 输入。

    每收到一帧都会：
    1. 记录原始帧；
    2. 旋转到视觉算法坐标；
    3. 推给 bridge_io 供寻物线程消费；
    4. 根据当前 NavigationMaster 状态执行导航/红绿灯/回退预览；
    5. 把处理后的 JPEG 广播给浏览器 viewer。
    """
    global esp32_camera_ws, blind_path_navigator, cross_street_navigator, cross_street_active, navigation_active, orchestrator
    if esp32_camera_ws is not None:
        await ws.close(code=1013)
        return
    esp32_camera_ws = ws
    await ws.accept()
    print("[CAMERA] ESP32 connected")
    
    # 【新增】初始化盲道导航器
    if blind_path_navigator is None and yolo_seg_model is not None:
        blind_path_navigator = BlindPathNavigator(
            yolo_seg_model, obstacle_detector, report_detected_obstacle
        )
        print("[NAVIGATION] 盲道导航器已初始化")
    else:
        if blind_path_navigator is not None:
            print("[NAVIGATION] 导航器已存在，无需重新初始化")
        elif yolo_seg_model is None:
            print("[NAVIGATION] 警告：YOLO模型未加载，无法初始化导航器")
    
    # 【新增】初始化过马路导航器
    if cross_street_navigator is None:
        if yolo_seg_model:
            cross_street_navigator = CrossStreetNavigator(
                seg_model=yolo_seg_model,
                coco_model=None,  # 不使用交通灯检测
                obs_model=None,    # 暂时也不用障碍物检测，让它更快
                obstacle_reporter=report_detected_obstacle,
            )
            print("[CROSS_STREET] 过马路导航器已初始化（简化版 - 仅斑马线检测）")
        else:
            print("[CROSS_STREET] 错误：缺少分割模型，无法初始化过马路导航器")
            
            if not yolo_seg_model:
                print("[CROSS_STREET] - 缺少分割模型 (yolo_seg_model)")
            if not obstacle_detector:
                print("[CROSS_STREET] - 缺少障碍物检测器 (obstacle_detector)")
    
    if orchestrator is None and blind_path_navigator is not None and cross_street_navigator is not None:
        orchestrator = NavigationMaster(blind_path_navigator, cross_street_navigator)
        print("[NAV MASTER] 统领状态机已初始化（托管模式）")
    frame_counter = 0  # 添加帧计数器
    
    try:
        while True:
            msg = await ws.receive()
            if "bytes" in msg and msg["bytes"] is not None:
                data = msg["bytes"]
                frame_counter += 1
                
                # 【新增】录制原始帧
                try:
                    sync_recorder.record_frame(data)
                except Exception as e:
                    if frame_counter % 100 == 0:  # 避免日志刷屏
                        print(f"[RECORDER] 录制帧失败: {e}")
                
                try:
                    last_frames.append((time.time(), data))
                except Exception:
                    pass
                
                # 【调试】检查导航条件
                if frame_counter % 30 == 0:  # 每30帧输出一次
                    state_dbg = orchestrator.get_state() if orchestrator else "N/A"
                    print(f"[NAVIGATION DEBUG] 帧:{frame_counter}, state={state_dbg}, yolomedia_running={yolomedia_running}")
                
                # 统一解码（添加更严格的异常处理）
                try:
                    arr = np.frombuffer(data, dtype=np.uint8)
                    bgr = cv2.imdecode(arr, cv2.IMREAD_COLOR)
                    # 验证解码结果
                    if bgr is None or bgr.size == 0:
                        if frame_counter % 30 == 0:
                            print(f"[JPEG] 解码失败：数据长度={len(data)}")
                        bgr = None
                    else:
                        bgr = orient_frame_for_vision(bgr)
                except Exception as e:
                    if frame_counter % 30 == 0:
                        print(f"[JPEG] 解码异常: {e}")
                    bgr = None

                oriented_data = encode_frame_jpeg_bytes(bgr) if bgr is not None else b""
                # 推送到bridge_io（供yolomedia使用），保持和导航处理相同的正向坐标。
                bridge_io.push_raw_jpeg(oriented_data or data)

                # 【托管】优先交给统领状态机（寻物未占用画面时）
                # 【修改】找物品模式时不执行导航处理，让yolomedia接管画面
                if orchestrator and not yolomedia_running and bgr is not None:
                    current_state = orchestrator.get_state()
                    
                    # 【新增】找物品模式：不处理画面，等待yolomedia发送处理后的帧
                    if current_state == "ITEM_SEARCH":
                        # 找物品模式下，如果yolomedia还没开始发送帧，先显示原始画面
                        if not yolomedia_sending_frames and camera_viewers:
                            jpeg_data = oriented_data or encode_frame_jpeg_bytes(bgr)
                            if jpeg_data:
                                dead = []
                                for viewer_ws in list(camera_viewers):
                                    try:
                                        await viewer_ws.send_bytes(jpeg_data)
                                    except Exception:
                                        dead.append(viewer_ws)
                                for d in dead:
                                    camera_viewers.discard(d)
                        continue  # 跳过后续的导航处理
                    
                    out_img = bgr
                    try:
                        # 【新增】检查是否在红绿灯检测模式
                        if current_state == "TRAFFIC_LIGHT_DETECTION":
                            # 红绿灯检测模式：在主线程中直接处理，避免掉帧
                            import trafficlight_detection
                            result = trafficlight_detection.process_single_frame(bgr, ui_broadcast_callback=ui_broadcast_final)
                            out_img = result['vis_image'] if result['vis_image'] is not None else bgr
                        else:
                            # 其他模式：正常的导航处理
                            res = orchestrator.process_frame(bgr)

                            # 语音引导（内部已节流）
                            # 注：omni对话时已切换到CHAT模式，不会生成导航语音
                            if res.guidance_text:
                                try:
                                    # 先播放语音，再广播到UI
                                    play_voice_text(res.guidance_text)
                                    await ui_broadcast_final(f"[导航] {res.guidance_text}")
                                except Exception:
                                    pass

                            # 输出图像
                            out_img = res.annotated_image if res.annotated_image is not None else bgr
                    except Exception as e:
                        if frame_counter % 100 == 0:
                            print(f"[NAV MASTER] 处理帧时出错: {e}")

                    # 广播图像
                    if camera_viewers and out_img is not None:
                        ok, enc = cv2.imencode(".jpg", out_img, [int(cv2.IMWRITE_JPEG_QUALITY), 80])
                        if ok:
                            jpeg_data = enc.tobytes()
                            dead = []
                            for viewer_ws in list(camera_viewers):
                                try:
                                    await viewer_ws.send_bytes(jpeg_data)
                                except Exception:
                                    dead.append(viewer_ws)
                            for d in dead:
                                camera_viewers.discard(d)
                    # 已托管，进入下一帧
                    continue

                # 【回退】寻物占用或者未解码成功，按原始画面回传
                if not yolomedia_sending_frames and camera_viewers:
                    try:
                        if bgr is None:
                            arr = np.frombuffer(data, dtype=np.uint8)
                            bgr = cv2.imdecode(arr, cv2.IMREAD_COLOR)
                            if bgr is not None and bgr.size > 0:
                                bgr = orient_frame_for_vision(bgr)
                        if bgr is not None:
                            jpeg_data = oriented_data or encode_frame_jpeg_bytes(bgr)
                            if jpeg_data:
                                dead = []
                                for viewer_ws in list(camera_viewers):
                                    try:
                                        await viewer_ws.send_bytes(jpeg_data)
                                    except Exception:
                                        dead.append(viewer_ws)
                                for ws in dead:
                                    camera_viewers.discard(ws)
                    except Exception as e:
                        print(f"[CAMERA] Broadcast error: {e}")

            elif "type" in msg and msg["type"] in ("websocket.close", "websocket.disconnect"):
                break
    except WebSocketDisconnect:
        pass
    except Exception as e:
        print(f"[CAMERA ERROR] {e}")
    finally:
        try:
            if WebSocketState is None or ws.client_state == WebSocketState.CONNECTED:
                await ws.close(code=1000)
        except Exception:
            pass
        esp32_camera_ws = None
        print("[CAMERA] ESP32 disconnected")
        
        # 【新增】清理导航状态
        if blind_path_navigator:
            blind_path_navigator.reset()
        if cross_street_navigator:
            cross_street_navigator.reset()
        if orchestrator:
            orchestrator.reset()
            print("[NAV MASTER] 统领器已重置")

# ---------- WebSocket：浏览器订阅相机帧 ----------
@app.websocket("/ws/viewer")
async def ws_viewer(ws: WebSocket):
    """浏览器相机预览 WebSocket，只接收 JPEG 二进制帧。"""
    await ws.accept()
    camera_viewers.add(ws)
    print(f"[VIEWER] Browser connected. Total viewers: {len(camera_viewers)}", flush=True)
    try:
        while True:
            # 保持连接活跃
            await asyncio.sleep(60)
    except WebSocketDisconnect:
        print("[VIEWER] Browser disconnected", flush=True)
    finally:
        try: 
            camera_viewers.remove(ws)
        except Exception: 
            pass
        print(f"[VIEWER] Removed. Total viewers: {len(camera_viewers)}", flush=True)

# === 注册给 bridge_io 的发送回调（把 JPEG 广播给 /ws/viewer） ===
def register_bridge_sender():
    """注册 bridge_io 回调，让后台寻物线程能把处理后 JPEG 切回主事件循环广播。"""
    # 保存主线程的事件循环
    main_loop = asyncio.get_event_loop()
    
    def _sender(jpeg_bytes: bytes):
        # 注意：这个函数可能在非协程线程里被调用，需要切回主事件循环
        try:
            # 检查事件循环状态，避免在关闭时发送
            if main_loop.is_closed():
                return
            
            # 标记YOLO已经开始发送处理后的帧
            global yolomedia_sending_frames
            if not yolomedia_sending_frames:
                yolomedia_sending_frames = True
                print("[YOLOMEDIA] 开始发送处理后的帧，切换到YOLO画面", flush=True)
            
            async def _broadcast():
                if not camera_viewers:
                    return
                dead = []
                for ws in list(camera_viewers):
                    try:
                        await ws.send_bytes(jpeg_bytes)
                    except Exception as e:
                        dead.append(ws)
                for ws in dead:
                    try:
                        camera_viewers.remove(ws)
                    except Exception:
                        pass
            
            # 使用保存的主线程事件循环
            future = asyncio.run_coroutine_threadsafe(_broadcast(), main_loop)
            # 不等待结果，避免阻塞生产线程
        except Exception as e:
            # 只在非预期错误时打印日志
            if "Event loop is closed" not in str(e):
                print(f"[DEBUG] _sender error: {e}", flush=True)

    bridge_io.set_sender(_sender)

def start_audio_preload():
    """启动时后台预加载预录语音，避免第一次导航播报卡顿。"""
    # 在后台线程中初始化，避免阻塞启动
    def _init():
        try:
            initialize_audio_system()
        except Exception as e:
            print(f"[AUDIO] 初始化失败: {e}")
    
    threading.Thread(target=_init, daemon=True).start()

async def shutdown_cleanup():
    """应用关闭时的清理工作"""
    print("[SHUTDOWN] 开始清理资源...")
    
    # 停止YOLO媒体处理
    stop_yolomedia()
    
    # 停止音频和AI任务
    await hard_reset_audio("shutdown")
    
    print("[SHUTDOWN] 资源清理完成")

# app_main.py —— lifespan 已统一到顶部 lifespan 函数


# --- 导出接口（可选） ---
def get_last_frames():
    """调试/测试用：暴露最近 JPEG 帧缓存。"""
    return last_frames

def get_camera_ws():
    """调试/测试用：返回当前 ESP32 相机 WebSocket。"""
    return esp32_camera_ws

if __name__ == "__main__":
    worker_host = os.getenv("WORKER_HOST", os.getenv("HOST", "127.0.0.1"))
    worker_port = int(os.getenv("WORKER_PORT", os.getenv("PORT", "18082")))
    uvicorn.run(
        app, host=worker_host, port=worker_port,
        log_level="warning", access_log=False,
        loop="asyncio", workers=1, reload=False
    )
