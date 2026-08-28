# -*- coding: utf-8 -*-
"""
train.py —— 训练人行道两轮车检测模型（共享电动车 / 自行车 / 家用电动车）

用 Ultralytics YOLO11，RTX4050(6G) 建议:
    python train.py --data data.yaml --model yolo11s.pt --epochs 100 --batch 8

训练完会在 runs/ebike/train/weights/ 下生成 best.pt / last.pt，
把它复制为 AIGlasses_for_navigation/ebike_detect.pt 即可被 ebike_detector.py 自动使用。
加 --export 会顺便导出 ONNX（供更快的 CPU/边缘部署）。
"""
from __future__ import annotations

import argparse
import os

import torch
from ultralytics import YOLO


def main() -> None:
    ap = argparse.ArgumentParser(description="训练 共享电动车/自行车/家用电动车 检测模型")
    ap.add_argument("--data", default="data.yaml", help="数据集 yaml（默认 data.yaml）")
    ap.add_argument("--model", default="yolo11s.pt", help="基础模型，6G 显存建议 yolo11n/s")
    ap.add_argument("--epochs", type=int, default=100)
    ap.add_argument("--imgsz", type=int, default=640)
    ap.add_argument("--batch", type=int, default=8, help="RTX4050 6G 建议 8；OOM 就降到 4")
    ap.add_argument("--project", default="runs/ebike")
    ap.add_argument("--name", default="train")
    ap.add_argument("--export", action="store_true", help="训练完导出 ONNX")
    args = ap.parse_args()

    if not os.path.exists(args.data):
        raise SystemExit(
            f"找不到数据集配置: {args.data}\n"
            "请先在 yolo_train/ 下准备数据（见 data.yaml 与 README.md），"
            "或用 auto_label.py 自动打标。"
        )

    device = 0 if torch.cuda.is_available() else "cpu"
    print(f"使用设备: {device}")

    model = YOLO(args.model)
    model.train(
        data=args.data,
        epochs=args.epochs,
        imgsz=args.imgsz,
        batch=args.batch,
        project=args.project,
        name=args.name,
        device=device,
        patience=30,
        workers=0,  # Windows 下避免 DataLoader worker 报错
    )

    best = os.path.join(args.project, args.name, "weights", "best.pt")
    print("=" * 60)
    print("最佳权重:", best)
    print("部署：把它复制为  AIGlasses_for_navigation/ebike_detect.pt")
    print("        (python_worker/ebike_detector.py 会自动加载它)")
    if args.export:
        m = YOLO(best)
        m.export(format="onnx", imgsz=args.imgsz)
        print("ONNX 已导出: ", os.path.join(args.project, args.name, "weights", "best.onnx"))


if __name__ == "__main__":
    main()
