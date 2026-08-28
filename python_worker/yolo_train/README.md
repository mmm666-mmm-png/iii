# 人行道两轮车 YOLO 检测：共享电动车 / 自行车 / 家用电动车

> 目标：在人行道/盲道导航场景里，识别 **共享电动车、自行车、家用电动车**，并给出物体的**边界框标注**，
> 便于为视障用户语音提示避让。

## 已就绪的部件

| 文件 | 作用 |
|---|---|
| `../ebike_detector.py`（python_worker 下） | 检测器：**有训练模型自动用**，没有则回退 YOLOE 开放词汇，直接能识别并画框 |
| `../interfaces/api/ebike_endpoints.py` | HTTP 接口：`POST /api/ebike/detect`（传 JPEG 返回物体+标注图）、`GET /api/ebike/status` |
| `data.yaml` | 3 类数据集配置（`shared_ebike` / `bicycle` / `home_ebike`） |
| `train.py` | 训练脚本（YOLO11） |
| `auto_label.py` | 用 YOLOE 自动打标，生成 YOLO txt 伪标签，省去手工标注 |

## 一、先跑通（不用训练）
在 `python_worker` 目录下（用 conda esp32 环境）：
```powershell
cd D:\glass-esp32\esp32-glass-all-zhu\esp32-glass-all-zhu\python_worker
D:\conda\envs\esp32\python.exe ebike_detector.py --image 某张图.jpg --out 标注结果.jpg
```
检测器会自动用 `AIGlasses_for_navigation/yoloe-11l-seg.pt` 识别 3 类并画框。
也可以通过接口：
```powershell
curl.exe -X POST http://127.0.0.1:18082/api/ebike/detect --data-binary "@图.jpg" -H "Content-Type: image/jpeg"
```

## 二、准备数据集（从零开始）
1. **采图**：尽量覆盖不同场景（晴天/阴天/逆光、近/远、停着/骑行、多个同框）。
   - 建议每类 ≥ 300 张，3 类共 ≥ 900 张。
2. **自动打标**（伪标签，省 80% 手工）：
   ```powershell
   D:\conda\envs\esp32\python.exe yolo_train\auto_label.py --input D:\raw_photos --out yolo_train\datasets\ebike\images\train --labels yolo_train\datasets\ebike\labels\train
   ```
3. **人工修正**：用 [X-AnyLabeling](https://github.com/CVHub520/X-AnyLabeling) 打开 `images/train`，
   修正/补画框后导出（YOLO 格式）覆盖 `labels/train`。再留 15~20% 做 `images/val` + `labels/val`。

> ⚠️ **难点提醒**：`共享电动车` 和 `家用电动车` 视觉上非常相似（都是电动两轮车），
> 纯靠外观区分很难，通常只能靠**品牌/涂装/颜色**（共享电动车往往是特定配色）。
> 采集时请多拍带明显涂装的共享单车；若实在难分，可把两类合并训练，界面再按颜色提示。

## 三、训练
```powershell
cd D:\glass-esp32\esp32-glass-all-zhu\esp32-glass-all-zhu\python_worker\yolo_train
D:\conda\envs\esp32\python.exe train.py --data data.yaml --model yolo11s.pt --epochs 100 --batch 8
```
- RTX4050(6G)：`yolo11s` + `batch 8` + `imgsz 640` 比较稳；OOM 就把 batch 降到 4。
- 训练完权重在 `runs/ebike/train/weights/best.pt`。

## 四、部署
把 `best.pt` 复制为：
```
AIGlasses_for_navigation\ebike_detect.pt
```
`ebike_detector.py` 启动时会**优先加载**它（比 YOLOE 快很多），重启 python worker 即生效。
需要 ONNX（CPU/边缘更快）：
```powershell
D:\conda\envs\esp32\python.exe train.py --data data.yaml --export
```

## 五、已接入盲道导航（本次已接好）
`BlindPathNavigator`（`workflow_blindpath.py`）已在每帧流程里接入两轮车检测：
- 节流检测（默认每 30 帧一次，用最近一次结果持续画框），识别到 共享电动车/自行车/家用电动车 画**橙色框**。
- 近距离时语音提示「**前方/左前方/右前方有XXX，注意避让**」，与障碍物同级最高优先级。
- 环境变量：
  - `AIGLASS_EBIKE_ENABLED=0` 关闭；`AIGLASS_EBIKE_INTERVAL=30` 检测频率；`AIGLASS_EBIKE_COOLDOWN=8` 语音冷却。
- 检测器优先用训练好的 `AIGlasses_for_navigation/ebike_detect.pt`（快）；没有则回退 YOLOE（重，首次加载约 10s，注意 6G 显存）。

`POST /api/ebike/detect` 接口（独立于导航）返回：
```json
{ "ok": true, "count": 2, "objects": [
   {"class":"bicycle","label_cn":"自行车","box":[x1,y1,x2,y2],"box_norm":[...],"conf":0.91}
], "annotatedImageBase64":"..." }
```
前端/Go 可把标注图叠加到预览画面。
