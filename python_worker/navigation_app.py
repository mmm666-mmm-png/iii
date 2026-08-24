"""
navigation_app.py —— 盲道友好路线规划服务独立入口

基于DDD架构的路线规划服务，提供:
- 高德多路线获取 + 打分重排序
- 静态盲道矢量数据融合
- 眼镜端实时障碍物上报融合
- 大模型语音交互与结果播报

可独立运行，也可被app_main.py导入集成。

运行方式:
  python navigation_app.py
  或
  uvicorn navigation_app:app --host 0.0.0.0 --port 18082
"""
from __future__ import annotations

import logging
import os
import sys
import threading
from datetime import datetime
from typing import Dict

# 确保当前目录在sys.path中
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from interfaces.api.route_endpoints import router as navigation_router
from interfaces.websocket.obstacle_report_handler import ObstacleReportWebSocketHandler
from infrastructure.config import AppConfig

# 配置日志
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

# 创建FastAPI应用
config = AppConfig.from_env()
app = FastAPI(
    title="盲道友好路线规划服务",
    description="基于DDD架构的视障导航路线规划引擎，融合高德路线、盲道矢量数据和实时障碍物",
    version="1.0.0",
)

# CORS配置
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 注册路由
app.include_router(navigation_router)

# WebSocket处理器实例
ws_handler = ObstacleReportWebSocketHandler()
vision_lock = threading.Lock()
vision_state: Dict[str, object] = {
    "enabled": True,
    "connected": True,
    "ready": True,
    "state": "IDLE",
    "guidance_text": "",
    "last_error": "",
    "last_processed_at": None,
    "frames_processed": 0,
    "frames_failed": 0,
    "latest_frame_bytes": 0,
    "annotated_available": False,
    "worker_url": f"http://{config.host}:{config.port}",
    "mode": "navigation_bridge",
}


class VisionControlBody(BaseModel):
    command: str = Field(..., description="vision control command")
    target: str = Field(default="", description="vision target")


def _normalize_command(command: str) -> str:
    return str(command or "").strip().lower().replace("-", "_")


def _vision_response_payload() -> Dict[str, object]:
    return {
        "ok": True,
        "ready": bool(vision_state["ready"]),
        "state": str(vision_state["state"]),
        "guidanceText": str(vision_state["guidance_text"]),
        "error": str(vision_state["last_error"]),
        "annotatedImageBase64": "",
        "imageFormat": "jpeg",
    }


def _vision_status_payload() -> Dict[str, object]:
    return {
        "enabled": bool(vision_state["enabled"]),
        "connected": bool(vision_state["connected"]),
        "ready": bool(vision_state["ready"]),
        "workerUrl": str(vision_state["worker_url"]),
        "state": str(vision_state["state"]),
        "lastGuidance": str(vision_state["guidance_text"]),
        "lastError": str(vision_state["last_error"]),
        "lastProcessedAt": vision_state["last_processed_at"],
        "framesProcessed": int(vision_state["frames_processed"]),
        "framesFailed": int(vision_state["frames_failed"]),
        "latestFrameBytes": int(vision_state["latest_frame_bytes"]),
        "annotatedAvailable": bool(vision_state["annotated_available"]),
    }


def _apply_vision_command(command: str, target: str = "") -> str:
    normalized = _normalize_command(command)
    target_text = str(target or "").strip()

    if normalized in {"start_blind_navigation", "blind_navigation", "blind", "start_blind"}:
        vision_state["state"] = "BLIND_NAVIGATION"
        vision_state["guidance_text"] = "已切换到导盲导航模式。"
    elif normalized in {"start_crossing", "crossing", "crosswalk"}:
        vision_state["state"] = "CROSSING"
        vision_state["guidance_text"] = "已切换到过马路模式。"
    elif normalized in {"detect_traffic_light", "traffic_light"}:
        vision_state["state"] = "TRAFFIC_LIGHT"
        vision_state["guidance_text"] = "已切换到红绿灯识别模式。"
    elif normalized in {"find_object", "item_search", "search_item"}:
        vision_state["state"] = "OBJECT_SEARCH"
        vision_state["guidance_text"] = f"正在查找{target_text}" if target_text else "正在查找目标物品。"
    elif normalized in {"stop", "stop_navigation", "idle", "chat", "reset", "clear"}:
        vision_state["state"] = "IDLE"
        vision_state["guidance_text"] = "已停止视觉导航。"
    else:
        vision_state["state"] = normalized.upper() if normalized else "IDLE"
        vision_state["guidance_text"] = f"已收到控制命令：{command}"

    vision_state["connected"] = True
    vision_state["ready"] = True
    vision_state["last_error"] = ""
    vision_state["last_processed_at"] = datetime.utcnow().isoformat()
    return vision_state["guidance_text"]


@app.websocket("/ws/obstacle")
async def obstacle_websocket(websocket):
    """障碍物实时上报WebSocket端点。"""
    await ws_handler.handle(websocket, device_id="websocket_client")


@app.get("/api/vision/status")
async def vision_status():
    """Compatibility status endpoint for the Go vision worker bridge."""
    with vision_lock:
        vision_state["connected"] = True
        vision_state["ready"] = True
        vision_state["last_processed_at"] = datetime.utcnow().isoformat()
        payload = _vision_status_payload()
    payload.update(_vision_response_payload())
    return payload


@app.post("/api/vision/control")
async def vision_control(body: VisionControlBody):
    """Compatibility control endpoint for the Go vision worker bridge."""
    with vision_lock:
        guidance = _apply_vision_command(body.command, body.target)
        payload = _vision_response_payload()
        payload["guidanceText"] = guidance
        payload["state"] = str(vision_state["state"])
        payload["ready"] = True
        payload["error"] = ""
        payload["ok"] = True
    return payload


@app.post("/api/vision/process")
async def vision_process(request: Request):
    """Accept JPEG frames from Go backend without rejecting the request."""
    frame = await request.body()
    with vision_lock:
        vision_state["connected"] = True
        vision_state["ready"] = True
        vision_state["frames_processed"] = int(vision_state["frames_processed"]) + 1
        vision_state["latest_frame_bytes"] = len(frame)
        vision_state["last_processed_at"] = datetime.utcnow().isoformat()
        payload = _vision_response_payload()
        payload["state"] = str(vision_state["state"])
        payload["guidanceText"] = str(vision_state["guidance_text"])
        payload["ok"] = True
    return payload


@app.get("/")
async def root():
    """服务根路径。"""
    return {
        "service": "盲道友好路线规划服务",
        "version": "1.0.0",
        "architecture": "DDD (Domain-Driven Design)",
        "layers": ["domain", "application", "infrastructure", "interfaces"],
        "endpoints": {
            "POST /api/navigation/plan": "路线规划（核心）",
            "POST /api/navigation/voice": "语音命令处理",
            "POST /api/obstacle/report": "障碍物上报",
            "GET /api/obstacle/hotspots": "获取障碍物热点",
            "GET /api/navigation/status": "服务状态",
            "WS /ws/obstacle": "障碍物实时上报",
        },
    }


@app.get("/healthz")
async def health():
    """健康检查。"""
    return {"status": "healthy"}


def main():
    """独立运行入口。"""
    import uvicorn
    logger.info(f"启动盲道路线规划服务: {config.host}:{config.port}")
    logger.info("API文档: http://localhost:{}/docs".format(config.port))
    uvicorn.run(app, host=config.host, port=config.port)


if __name__ == "__main__":
    main()
