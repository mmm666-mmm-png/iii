"""
接口层 —— 障碍物实时上报WebSocket处理器

支持眼镜端通过WebSocket实时上报障碍物检测结果，
比HTTP REST更适合高频实时数据流。
"""
from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import Dict, Optional

from fastapi import WebSocket, WebSocketDisconnect

from domain.model.obstacle import Obstacle
from domain.model.route import GeoPoint
from infrastructure.in_memory_obstacle_repository import InMemoryObstacleRepository

logger = logging.getLogger(__name__)


class ObstacleReportWebSocketHandler:
    """障碍物上报WebSocket处理器。"""

    def __init__(self, repository: Optional[InMemoryObstacleRepository] = None):
        self.repository = repository or InMemoryObstacleRepository()
        self._active_connections: Dict[str, WebSocket] = {}

    async def handle(self, websocket: WebSocket, device_id: str = "unknown"):
        """
        处理WebSocket连接。

        接收的消息格式:
        {
          "type": "obstacle_report",
          "data": {
            "lng": 117.327,
            "lat": 34.812,
            "obstacle_type": "pole",
            "severity": "medium",
            "confidence": 0.85,
            "description": "电线杆"
          }
        }

        或心跳消息:
        {"type": "ping"}
        """
        await websocket.accept()
        self._active_connections[device_id] = websocket
        logger.info(f"设备 {device_id} 已连接障碍物上报WebSocket")

        try:
            while True:
                raw = await websocket.receive_text()
                try:
                    message = json.loads(raw)
                except json.JSONDecodeError:
                    await websocket.send_json({"type": "error", "message": "无效的JSON"})
                    continue

                msg_type = message.get("type", "")

                if msg_type == "ping":
                    await websocket.send_json({"type": "pong", "timestamp": datetime.utcnow().isoformat()})

                elif msg_type == "obstacle_report":
                    result = self._process_report(message.get("data", {}), device_id)
                    await websocket.send_json({
                        "type": "obstacle_ack",
                        "data": result,
                    })

                elif msg_type == "get_hotspots":
                    hotspots = self.repository.get_hotspots()
                    await websocket.send_json({
                        "type": "hotspots",
                        "data": {
                            "count": len(hotspots),
                            "hotspots": [
                                {
                                    "id": hs.hotspot_id,
                                    "lng": hs.location.lng,
                                    "lat": hs.location.lat,
                                    "type": hs.obstacle_type,
                                    "severity": hs.severity,
                                    "count": hs.report_count,
                                }
                                for hs in hotspots
                            ],
                        },
                    })

                else:
                    await websocket.send_json({"type": "error", "message": f"未知消息类型: {msg_type}"})

        except WebSocketDisconnect:
            logger.info(f"设备 {device_id} 断开连接")
        except Exception as e:
            logger.error(f"WebSocket处理异常: {e}", exc_info=True)
        finally:
            self._active_connections.pop(device_id, None)

    def _process_report(self, data: dict, device_id: str) -> dict:
        """处理单条障碍物上报。"""
        try:
            raw_lng = data.get("lng")
            raw_lat = data.get("lat")
            location = (
                GeoPoint(float(raw_lng), float(raw_lat))
                if raw_lng is not None and raw_lat is not None
                else None
            )
            obstacle = Obstacle(
                device_id=device_id,
                location=location,
                obstacle_type=data.get("obstacle_type", "unknown"),
                severity=data.get("severity", "medium"),
                confidence=float(data.get("confidence", 0.8)),
                description=data.get("description", ""),
            )
            self.repository.add(obstacle)
            return {
                "obstacle_id": obstacle.obstacle_id,
                "success": True,
                "total_reports": self.repository.get_report_count(),
            }
        except Exception as e:
            return {"success": False, "error": str(e)}

    def get_active_connections(self) -> Dict[str, WebSocket]:
        return dict(self._active_connections)
