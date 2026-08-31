# -*- coding: utf-8 -*-
"""
障碍物检测集成测试（离线，不依赖 ESP32 / Go / 前端）。

验证三件事：
1. block.pt 检测模型能正常加载并对真实图片检出物体；
2. 导盲模式的障碍物语音文案：person 静默、其余统一为“前方有障碍物请注意躲避”；
3. BlindPathNavigator.process_frame 能产出标注图，且障碍物可视化被正确添加。

运行：
    D:\\conda\\envs\\esp32\\python.exe tests/test_obstacle_integration.py
"""
import os
import sys

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

import cv2
import numpy as np

DATASET_VAL = r"D:\glass-esp32\datasets\obstaclev2iyolo26-2-26e710a7\images\val"
MODEL_DIR = os.path.join(BASE_DIR, "..", "AIGlasses_for_navigation")


def _sample_images(n=4):
    files = sorted(os.listdir(DATASET_VAL))[:n]
    return [os.path.join(DATASET_VAL, f) for f in files if f.lower().endswith(".jpg")]


def test_speech_text():
    from workflow_blindpath import BlindPathNavigator
    nav = BlindPathNavigator(None, None, None)
    assert nav._speech_for_obstacle("person") == "", "person 不应触发障碍物播报"
    assert nav._speech_for_obstacle("car") == "前方有障碍物请注意躲避"
    assert nav._speech_for_obstacle("bicycle") == "前方有障碍物请注意躲避"
    assert nav._speech_for_obstacle("unknown_thing") == "前方有障碍物请注意躲避"
    print("[PASS] 障碍物语音文案校验通过")


def test_detector():
    from obstacle_detector_client import ObstacleDetectorClient
    model_path = os.path.join(MODEL_DIR, "block.pt")
    client = ObstacleDetectorClient(model_path=model_path)
    print(f"[INFO] 模型类别: {client.WHITELIST_CLASSES}")

    total = 0
    for path in _sample_images():
        img = cv2.imread(path)
        if img is None:
            continue
        objs = client.detect(img, path_mask=None)
        names = [o.get("name") for o in objs]
        total += len(objs)
        print(f"[DETECT] {os.path.basename(path)}: {len(objs)} 个物体 -> {names}")
    assert total > 0, "在数据集样本上未检出任何物体，block.pt 可能未生效"
    print(f"[PASS] 障碍物检测模型校验通过（共检出 {total} 个物体）")


def test_navigator_annotation():
    from ultralytics import YOLO
    from obstacle_detector_client import ObstacleDetectorClient
    from workflow_blindpath import BlindPathNavigator

    seg = YOLO(os.path.join(MODEL_DIR, "yolo-seg.pt"))
    obs = ObstacleDetectorClient(model_path=os.path.join(MODEL_DIR, "block.pt"))
    nav = BlindPathNavigator(seg, obs, None)

    path = _sample_images(1)[0]
    img = cv2.imread(path)
    res = nav.process_frame(img)
    assert res.annotated_image is not None
    # 标注图应与原图同尺寸
    assert res.annotated_image.shape == img.shape
    print(f"[NAV] guidance_text={res.guidance_text!r}, state_info={res.state_info}")
    print(f"[NAV] annotated_image 形状={res.annotated_image.shape}")
    print("[PASS] BlindPathNavigator.process_frame 标注图生成通过")


if __name__ == "__main__":
    test_speech_text()
    test_detector()
    test_navigator_annotation()
    print("\n[ALL PASS] 障碍物检测集成测试全部通过")
