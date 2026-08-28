# -*- coding: utf-8 -*-
"""
auto_label.py —— 用 YOLOE 对图片自动打标，生成 YOLO 检测格式标注（txt）

在没有标注数据时，先用它给一批图片自动生成伪标签，再人工抽查/修正后训练。

用法（在 python_worker 目录下运行，以便复用 ebike_detector）：
    D:\\conda\\envs\\esp32\\python.exe yolo_train/auto_label.py \
        --input  D:\\raw_photos \
        --out    yolo_train/datasets/ebike/images/train \
        --labels yolo_train/datasets/ebike/labels/train

说明：
1. 遍历 input 下所有图片；
2. 用 YOLOE 检测 共享电动车/自行车/家用电动车；
3. 为每张图写一个同名 .txt（YOLO 格式: cls cx cy w h 归一化）；
4. 把有目标的图片复制到 out 目录（与 labels 一一对应）。
⚠️ 自动标注可能不准，训练前务必人工抽查/修正（可用 X-AnyLabeling / LabelImg）。
"""
from __future__ import annotations

import argparse
import os
import shutil

import cv2


def main() -> None:
    ap = argparse.ArgumentParser(description="YOLOE 自动打标")
    ap.add_argument("--input", required=True, help="原始图片目录")
    ap.add_argument("--out", default="yolo_train/datasets/ebike/images/train")
    ap.add_argument("--labels", default="yolo_train/datasets/ebike/labels/train")
    ap.add_argument("--conf", type=float, default=0.25)
    args = ap.parse_args()

    # 复用检测器（会自动优先训练模型，否则 YOLOE）
    from ebike_detector import CLS_INDEX, EbikeDetector

    os.makedirs(args.out, exist_ok=True)
    os.makedirs(args.labels, exist_ok=True)

    det = EbikeDetector(conf=args.conf)
    exts = (".jpg", ".jpeg", ".png", ".bmp", ".webp")
    images = [f for f in sorted(os.listdir(args.input)) if f.lower().endswith(exts)]
    if not images:
        raise SystemExit(f"目录里没有图片: {args.input}")
    print(f"共 {len(images)} 张图片，开始自动标注...")

    n_annotated = 0
    n_obj = 0
    for name in images:
        path = os.path.join(args.input, name)
        img = cv2.imread(path)
        if img is None:
            print("跳过无法读取:", path)
            continue
        H, W = img.shape[:2]
        objs = det.detect(img)
        stem = os.path.splitext(name)[0]
        lines: list[str] = []
        for o in objs:
            cls_id = CLS_INDEX.get(o["class"])
            if cls_id is None:
                continue
            x1, y1, x2, y2 = o["box"]
            cx = (x1 + x2) / 2 / W
            cy = (y1 + y2) / 2 / H
            bw = max(0.0, (x2 - x1) / W)
            bh = max(0.0, (y2 - y1) / H)
            lines.append(f"{cls_id} {cx:.6f} {cy:.6f} {bw:.6f} {bh:.6f}")
        if not lines:
            print(f"{name}: 0 个目标（跳过，未标注）")
            continue
        txt_path = os.path.join(args.labels, stem + ".txt")
        with open(txt_path, "w", encoding="utf-8") as f:
            f.write("\n".join(lines))
        shutil.copy(path, os.path.join(args.out, name))
        n_annotated += 1
        n_obj += len(lines)
        print(f"{name}: {len(lines)} 个目标 -> {os.path.basename(txt_path)}")

    print("=" * 60)
    print(f"完成：标注 {n_annotated} 张，共 {n_obj} 个目标。")
    print(f"图片目录: {args.out}")
    print(f"标签目录: {args.labels}")
    print("⚠️ 提示：自动标注可能不准，训练前请人工抽查/修正。")


if __name__ == "__main__":
    main()
