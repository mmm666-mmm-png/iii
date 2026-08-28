# -*- coding: utf-8 -*-
"""
ebike_detector.py —— 人行道两轮车检测（共享电动车 / 自行车 / 家用电动车）

两种模式：
1. 专用检测模型：若存在 `AIGlasses_for_navigation/ebike_detect.pt`（用 yolo_train/train.py 训练），
   直接用 ultralytics YOLO 推理，又快又稳。
2. 回退开放词汇：用现有 `yoloe-11l-seg.pt`（YOLOE）按文本提示词检测，无需训练即可先用。

输出：物体列表（类别 / 中文名 / 像素框 / 归一化框 / 置信度）+ 带框标注图。

用法（命令行对单张图做标注）：
    python ebike_detector.py --image 路径.jpg --out 输出.jpg
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import threading
from typing import Any, Dict, List, Optional, Tuple

import cv2
import numpy as np

logger = logging.getLogger(__name__)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
TRAINED_MODEL_PATH = os.getenv(
    "EBIKE_MODEL_PATH",
    os.path.join(BASE_DIR, "..", "AIGlasses_for_navigation", "ebike_detect.pt"),
)
YOLOE_MODEL_PATH = os.getenv(
    "YOLOE_MODEL_PATH",
    os.path.join(BASE_DIR, "..", "AIGlasses_for_navigation", "yoloe-11l-seg.pt"),
)
CONF_THR = float(os.getenv("EBIKE_CONF", "0.25"))

# 类别定义：cls 是模型/训练用的英文名，cn 是界面显示的中文名，
# prompts 是 YOLOE 开放词汇用的英文提示词（多提示词可提升召回）。
CLASSES: List[Dict[str, Any]] = [
    {"cls": "shared_ebike", "cn": "共享电动车",
     "prompts": ["shared electric scooter", "shared e-bike", "dockless electric bike"]},
    {"cls": "bicycle", "cn": "自行车",
     "prompts": ["bicycle", "bike"]},
    {"cls": "home_ebike", "cn": "家用电动车",
     "prompts": ["electric moped", "electric scooter", "electric bicycle", "e-bike"]},
]
# 训练/标注时用的类别 id（与 yolo_train/data.yaml 一致）
CLS_INDEX: Dict[str, int] = {c["cls"]: i for i, c in enumerate(CLASSES)}
# YOLOE 提示词 -> 类别名
PROMPT_TO_CLS: Dict[str, str] = {}
for _c in CLASSES:
    for _p in _c["prompts"]:
        PROMPT_TO_CLS[_p] = _c["cls"]

# 画框颜色（BGR）
CLS_COLORS: Dict[str, Tuple[int, int, int]] = {
    "shared_ebike": (0, 165, 255),   # 橙
    "bicycle": (0, 255, 0),          # 绿
    "home_ebike": (255, 0, 255),     # 紫
}

_CJK_FONT: Any = None
_CJK_FONT_LOCK = threading.Lock()


def _get_cjk_font():
    """惰性加载一个中文字体（Windows 微软雅黑/黑体，Linux Noto），失败返回 None。"""
    global _CJK_FONT
    if _CJK_FONT is not None:
        return _CJK_FONT
    with _CJK_FONT_LOCK:
        if _CJK_FONT is not None:
            return _CJK_FONT
        try:
            from PIL import ImageFont
        except Exception:
            _CJK_FONT = False
            return _CJK_FONT
        for path in (
            "C:/Windows/Fonts/msyh.ttc",
            "C:/Windows/Fonts/simhei.ttf",
            "C:/Windows/Fonts/msyhbd.ttc",
            "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
            "/usr/share/fonts/truetype/wqy/wqy-microhei.ttc",
        ):
            if os.path.exists(path):
                try:
                    _CJK_FONT = ImageFont.truetype(path, 24)
                    return _CJK_FONT
                except Exception:
                    continue
        _CJK_FONT = False
        return _CJK_FONT


class EbikeDetector:
    """人行道两轮车检测器：优先专用训练模型，否则回退 YOLOE 开放词汇。"""

    def __init__(
        self,
        trained_model_path: Optional[str] = None,
        yoloe_model_path: Optional[str] = None,
        conf: Optional[float] = None,
    ):
        self._lock = threading.Lock()
        self._model: Any = None
        self._mode: Optional[str] = None  # "yolo" | "yoloe"
        self._names: Optional[Dict[int, str]] = None
        self._trained_path = trained_model_path or TRAINED_MODEL_PATH
        self._yoloe_path = yoloe_model_path or YOLOE_MODEL_PATH
        self._conf = conf if conf is not None else CONF_THR

    # ----- 模型加载 -----
    def _ensure_model(self) -> None:
        if self._model is not None:
            return
        import torch
        from ultralytics import YOLO, YOLOE

        device = "cuda:0" if torch.cuda.is_available() else "cpu"
        if os.path.exists(self._trained_path):
            logger.info("使用专用训练模型: %s", self._trained_path)
            self._model = YOLO(self._trained_path)
            self._mode = "yolo"
            self._names = dict(self._model.names)
        elif os.path.exists(self._yoloe_path):
            logger.info("未找到训练模型，回退 YOLOE: %s", self._yoloe_path)
            model = YOLOE(self._yoloe_path)
            model.to(device)
            model.fuse()
            prompts = [p for c in CLASSES for p in c["prompts"]]
            try:
                model.set_classes(prompts, model.get_text_pe(prompts))
            except TypeError:
                model.set_classes(prompts)
            self._model = model
            self._mode = "yoloe"
        else:
            raise FileNotFoundError(
                f"既无专用训练模型 {self._trained_path} 也无 YOLOE {self._yoloe_path}"
            )

    # ----- 检测 -----
    def detect(self, image: np.ndarray) -> List[Dict[str, Any]]:
        """检测一帧 BGR 图，返回物体列表。"""
        if image is None or image.size == 0:
            return []
        with self._lock:
            self._ensure_model()
            if self._mode == "yolo":
                return self._detect_yolo(image)
            return self._detect_yoloe(image)

    def _detect_yolo(self, image: np.ndarray) -> List[Dict[str, Any]]:
        H, W = image.shape[:2]
        res = self._model.predict(image, conf=self._conf, verbose=False)
        objs: List[Dict[str, Any]] = []
        if not (res and res[0].boxes is not None and len(res[0].boxes)):
            return objs
        b = res[0].boxes
        xyxy = b.xyxy.cpu().numpy()
        cls_ids = b.cls.cpu().tolist()
        confs = b.conf.cpu().tolist()
        for i in range(len(xyxy)):
            cid = int(cls_ids[i])
            name = str((self._names or {}).get(cid, "unknown"))
            objs.append(self._make_obj(name, xyxy[i], confs[i], W, H))
        return objs

    def _detect_yoloe(self, image: np.ndarray) -> List[Dict[str, Any]]:
        H, W = image.shape[:2]
        try:
            res = self._model.predict(image, conf=self._conf, verbose=False)
        except Exception as exc:  # noqa: BLE001
            logger.error("YOLOE 推理异常: %s", exc)
            return []
        objs: List[Dict[str, Any]] = []
        if not (res and res[0].boxes is not None and len(res[0].boxes)):
            return objs
        b = res[0].boxes
        xyxy = b.xyxy.cpu().numpy()
        cls_ids = b.cls.cpu().tolist()
        confs = b.conf.cpu().tolist()
        names_map = getattr(res[0], "names", {})
        for i in range(len(xyxy)):
            cid = int(cls_ids[i])
            prompt = ""
            if isinstance(names_map, dict):
                prompt = str(names_map.get(cid, ""))
            elif isinstance(names_map, list) and 0 <= cid < len(names_map):
                prompt = str(names_map[cid])
            cls_name = PROMPT_TO_CLS.get(prompt, "unknown")
            objs.append(self._make_obj(cls_name, xyxy[i], confs[i], W, H))
        return objs

    @staticmethod
    def _make_obj(cls_name: str, box, conf: float, W: int, H: int) -> Dict[str, Any]:
        x1, y1, x2, y2 = (float(v) for v in box)
        return {
            "class": cls_name,
            "label_cn": next((c["cn"] for c in CLASSES if c["cls"] == cls_name), cls_name),
            "box": [round(x1, 1), round(y1, 1), round(x2, 1), round(y2, 1)],
            "box_norm": [round(x1 / W, 4), round(y1 / H, 4), round(x2 / W, 4), round(y2 / H, 4)],
            "conf": round(float(conf), 3),
        }

    # ----- 标注 -----
    def detect_and_annotate(self, image: np.ndarray) -> Tuple[List[Dict[str, Any]], np.ndarray]:
        objs = self.detect(image)
        return objs, self.annotate(image, objs)

    def annotate(self, image: np.ndarray, objects: Optional[List[Dict[str, Any]]] = None) -> np.ndarray:
        """把检测结果画成带框标注图（返回拷贝，不修改原图）。"""
        objs = objects if objects is not None else self.detect(image)
        img = image.copy()
        for o in objs:
            x1, y1, x2, y2 = (int(v) for v in o["box"])
            color = CLS_COLORS.get(o.get("class"), (255, 255, 255))
            cv2.rectangle(img, (x1, y1), (x2, y2), color, 2)
            label = f"{o.get('label_cn', o.get('class', ''))} {o.get('conf', 0):.2f}"
            self._draw_label(img, label, (x1, y1 - 6), color)
        return img

    @staticmethod
    def _draw_label(img: np.ndarray, text: str, org: Tuple[int, int], color) -> None:
        x, y = org
        font_obj = _get_cjk_font()
        if font_obj is False or font_obj is None:
            # 无中文字体 → 英文回退
            cv2.rectangle(img, (x, y - 22), (x + 130, y + 2), color, -1)
            cv2.putText(img, text, (x + 4, y - 6), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 0, 0), 1, cv2.LINE_AA)
            return
        try:
            from PIL import Image, ImageDraw
        except Exception:
            cv2.rectangle(img, (x, y - 22), (x + 130, y + 2), color, -1)
            cv2.putText(img, text, (x + 4, y - 6), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 0, 0), 1, cv2.LINE_AA)
            return
        pil = Image.fromarray(cv2.cvtColor(img, cv2.COLOR_BGR2RGB))
        draw = ImageDraw.Draw(pil)
        bbox = draw.textbbox((0, 0), text, font=font_obj)
        tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
        draw.rectangle((x, y - th - 8, x + tw + 10, y + 2), fill=tuple(reversed(color)))
        draw.text((x + 5, y - th - 6), text, font=font_obj, fill=(0, 0, 0))
        img[:] = cv2.cvtColor(np.asarray(pil), cv2.COLOR_RGB2BGR)


# ----- 单例 -----
_detector: Optional[EbikeDetector] = None
_detector_lock = threading.Lock()


def get_ebike_detector() -> EbikeDetector:
    global _detector
    with _detector_lock:
        if _detector is None:
            _detector = EbikeDetector()
        return _detector


def main() -> None:
    ap = argparse.ArgumentParser(description="人行道两轮车检测/标注")
    ap.add_argument("--image", required=True, help="输入图片路径")
    ap.add_argument("--out", default=None, help="输出标注图路径（默认同目录 _annotated.jpg）")
    args = ap.parse_args()

    logging.basicConfig(level=logging.INFO)
    img = cv2.imread(args.image)
    if img is None:
        raise SystemExit(f"无法读取图片: {args.image}")

    det = EbikeDetector()
    objs, ann = det.detect_and_annotate(img)
    print(json.dumps(objs, ensure_ascii=False, indent=2))
    out = args.out or os.path.splitext(args.image)[0] + "_annotated.jpg"
    cv2.imwrite(out, ann)
    print("检测到:", len(objs), "个目标；已保存标注图:", out)


if __name__ == "__main__":
    main()
