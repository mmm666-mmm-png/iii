# -*- coding: utf-8 -*-
"""
ebike_endpoints.py —— 人行道两轮车检测接口

- POST /api/ebike/detect   接收一张 JPEG 图，返回检测到的两轮车 + 带框标注图(base64)
- GET  /api/ebike/status   返回类别与模型可用状态

检测推理较重，放到线程池执行（asyncio.to_thread），避免阻塞事件循环。
"""
from __future__ import annotations

import asyncio
import base64
import logging

import cv2
import numpy as np
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/ebike", tags=["ebike"])


def _get_detector():
    from ebike_detector import get_ebike_detector

    return get_ebike_detector()


@router.post("/detect")
async def detect_ebike(request: Request):
    """接收一张 JPEG 图，返回检测到的两轮车与带框标注图。"""
    try:
        body = await request.body()
        if not body:
            return JSONResponse({"ok": False, "error": "empty body"})
        arr = np.frombuffer(body, dtype=np.uint8)
        img = cv2.imdecode(arr, cv2.IMREAD_COLOR)
        if img is None:
            return JSONResponse({"ok": False, "error": "invalid jpeg"})

        det = _get_detector()
        objs, ann = await asyncio.to_thread(det.detect_and_annotate, img)

        ok, enc = cv2.imencode(".jpg", ann, [int(cv2.IMWRITE_JPEG_QUALITY), 82])
        ann_b64 = base64.b64encode(enc.tobytes()).decode("ascii") if ok else ""
        return JSONResponse(
            {
                "ok": True,
                "count": len(objs),
                "objects": objs,
                "annotatedImageBase64": ann_b64,
                "imageFormat": "jpeg",
            }
        )
    except Exception as exc:  # noqa: BLE001
        logger.error("ebike detect failed: %s", exc, exc_info=True)
        return JSONResponse({"ok": False, "error": str(exc)})


@router.get("/status")
async def ebike_status():
    import os

    from ebike_detector import CLASSES, TRAINED_MODEL_PATH, YOLOE_MODEL_PATH

    return {
        "ok": True,
        "classes": [c["cls"] for c in CLASSES],
        "trained_model_exists": os.path.exists(TRAINED_MODEL_PATH),
        "yoloe_model_exists": os.path.exists(YOLOE_MODEL_PATH),
    }
