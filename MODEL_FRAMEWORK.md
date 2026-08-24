# 模型与框架说明

本文档说明本项目使用的主要 AI 模型、算法框架和工程框架。项目整体不是单一模型应用，而是一个端云协同的智能眼镜导航系统：ESP32 负责采集音视频和播放语音，Go 后端负责设备通信与转发，Python Worker 负责视觉导航和语音/大模型能力，Vue 前端负责可视化监控与控制。

## 1. 总体架构

系统采用“四层结构”：

| 层级 | 目录 | 主要作用 | 使用框架/技术 |
| --- | --- | --- | --- |
| 设备端 | `firmware/xiao_sense_ws_stream` | 摄像头、麦克风、扬声器、Wi-Fi/UDP 通信 | Arduino ESP32、FreeRTOS、ESP32 Camera、I2S/PDM |
| 后端中转层 | `server` | 接收 ESP32 音视频流、维护设备状态、转发视觉任务、对接 DashScope Realtime | Go、Gin、Gorilla WebSocket |
| AI/视觉 Worker | `python_worker` | 盲道导航、斑马线/红绿灯检测、障碍物检测、寻物引导、ASR 和多模态问答 | Python、FastAPI、OpenCV、PyTorch、Ultralytics、MediaPipe、DashScope |
| 前端展示层 | `esp32-glass-front` | 实时画面预览、设备状态、AI 状态、导航控制 | Vue 3、Vite、Ant Design Vue |

核心数据流如下：

1. ESP32 采集摄像头 JPEG 帧和麦克风 PCM 音频，通过 UDP 发给 Go 后端。
2. Go 后端重组音视频流，提供 `/api/status`、`/snapshot.jpg`、WebSocket 等接口给前端。
3. Go 后端按配置把视频帧转发给 Python Worker 的 `/api/vision/process`，把控制命令转发给 `/api/vision/control`。
4. Python Worker 调用视觉模型生成导航状态、避障提示和标注画面。
5. 导航语音通过预生成 wav 或 AI 语音回传到 Go 后端，再由 Go 后端通过 UDP 下发给 ESP32 扬声器播放。

## 2. 使用的 AI 模型

### 2.1 视觉导航模型

项目主要使用 Ultralytics 系列模型，底层依赖 PyTorch 推理。

| 模型文件 | 使用位置 | 作用 |
| --- | --- | --- |
| `AIGlasses_for_navigation/yolo-seg.pt` | `python_worker/app_main.py`、`workflow_blindpath.py`、`workflow_crossstreet.py` | 盲道、斑马线等地面目标分割，用于路径方向判断和过马路流程 |
| `AIGlasses_for_navigation/yoloe-11l-seg.pt` | `obstacle_detector_client.py`、`yoloe_backend.py`、`yolomedia.py` | YOLOE 开放词汇分割，用文本提示词识别障碍物或寻物目标 |
| `AIGlasses_for_navigation/trafficlight.pt` | `trafficlight_detection.py` | 红绿灯状态检测，识别红灯、绿灯、倒计时等类别 |
| `AIGlasses_for_navigation/shoppingbest5.pt` | 资产保留，`yolomedia.py` 中有历史路径 | 原固定类别寻物模型，目前寻物主流程优先使用 YOLOE 文本提示后端 |
| `python_worker/hand_landmarker.task` / `AIGlasses_for_navigation/hand_landmarker.task` | `yolomedia.py` | MediaPipe 手部关键点检测，用于判断手和目标物体的相对位置 |

视觉部分的主要框架：

- `ultralytics==8.3.200`：加载 YOLO、YOLOE 分割/检测模型。
- `torch==2.0.1`、`torchvision==0.15.2`：模型推理后端，支持 CUDA 加速。
- `opencv-python` / `opencv-contrib-python`：图像解码、旋转、绘制、掩码处理、几何计算。
- `mediapipe==0.10.8`：手部关键点检测。

### 2.2 语音识别模型

Python Worker 中实时 ASR 使用阿里云 DashScope：

| 模型 | 使用位置 | 作用 |
| --- | --- | --- |
| `paraformer-realtime-v2` | `python_worker/app_main.py` | 实时中文语音识别，把 ESP32 麦克风音频转成文字命令 |

相关框架：

- `dashscope==1.14.1`：调用 DashScope 实时语音识别。
- `asr_core.py`：封装 ASR 回调，处理 partial/final 文本、热词中断、避免 AI 播报重叠。

### 2.3 大语言/多模态模型

项目中有两条 Qwen/DashScope 调用路径：

| 模型 | 使用位置 | 作用 |
| --- | --- | --- |
| `qwen3-omni-flash-realtime` | `server/.env.example`、`server/internal/serverapp/dashscope_bridge.go` | Go 后端实时多模态桥接模型，可接收音频和周期性图像帧，返回文本和语音 |
| `qwen-omni-turbo` | `python_worker/omni_client.py` | Python Worker 中的多模态问答模型，支持文本 + 图像输入，输出文本和音频 |
| `qwen-turbo` | `python_worker/qwen_extractor.py` | 寻物目标归一化，例如把中文“矿泉水”转换为英文视觉类别 `bottle` |

相关框架：

- `openai==1.3.5`：通过 DashScope 的 OpenAI 兼容接口调用 Qwen 系列模型。
- DashScope Realtime WebSocket：Go 后端在 `dashscope_bridge.go` 中直接连接实时多模态服务。

## 3. 主要业务工作流

### 3.1 盲道导航

盲道导航由 `NavigationMaster` 和 `BlindPathNavigator` 协同完成。系统用 `yolo-seg.pt` 对盲道区域做分割，再根据掩码位置、中心线、路径偏移量生成“直行、左移、右移、停下”等提示。障碍物检测由 `yoloe-11l-seg.pt` 辅助完成，只保留出现在路径区域内的危险目标。

### 3.2 过马路导航

过马路流程由 `CrossStreetNavigator` 管理。它使用 `yolo-seg.pt` 检测斑马线和通行区域，使用 `trafficlight.pt` 判断红绿灯状态，并结合状态机输出“发现斑马线、等待绿灯、开始通行、过马路结束”等导航语音。

### 3.3 寻物/抓取引导

寻物流程由 `yolomedia.py` 执行。用户说出目标物体后，`qwen_extractor.py` 先把中文目标转换成英文类别；随后 YOLOE 使用该文本类别作为开放词汇提示词进行分割。MediaPipe Hand Landmarker 检测手部关键点，系统根据“手的位置、目标中心、目标面积/手面积比例”给出“向左、向右、向前、后退、已对中”等提示。

### 3.4 语音问答

普通问答使用 Qwen Omni。系统会取最近一帧摄像头画面和用户语音识别文本作为上下文，调用 `qwen-omni-turbo` 输出文本和音频。Go 后端也保留了 `qwen3-omni-flash-realtime` 的实时多模态桥接能力，可直接把设备音频和周期性图像帧送入模型。

## 4. 工程框架说明

### 4.1 Python Worker

Python Worker 使用 FastAPI 提供视觉与语音服务：

- `GET /api/vision/status`：返回视觉模型和导航状态。
- `POST /api/vision/control`：接收开始导航、停止导航、开始寻物等控制命令。
- `POST /api/vision/process`：接收单帧图像，返回导航结果。
- WebSocket：接收或推送摄像头预览、ASR 文本、AI 文本等实时数据。

主要依赖：

- FastAPI + Uvicorn：本地 AI 服务接口。
- OpenCV + NumPy：图像处理。
- PyTorch + Ultralytics：YOLO/YOLOE 推理。
- MediaPipe：手部关键点。
- DashScope + OpenAI SDK：ASR 和 Qwen 模型调用。

### 4.2 Go 后端

Go 后端是设备和 AI 服务之间的中转层，使用 Gin 和 Gorilla WebSocket：

- 接收 ESP32 UDP hello、音频包、视频分片。
- 维护最新帧、设备在线状态、码流统计。
- 转发视觉控制和图像帧给 Python Worker。
- 把导航 wav 或 AI 语音分片下发给 ESP32。
- 可选连接 DashScope Realtime，实现实时多模态问答。

### 4.3 前端

前端是独立 Vue 3 项目，使用 Vite 构建，Ant Design Vue 提供 UI 组件。开发环境通过 Vite proxy 把 `/api`、`/snapshot.jpg`、`/healthz` 和 `/ws` 转发到 Go 后端。

### 4.4 ESP32 固件

固件面向 XIAO ESP32S3 Sense：

- 摄像头采集 VGA JPEG。
- 麦克风采集 PCM 音频。
- 扬声器播放后端下发的 PCM16 音频。
- 使用 FreeRTOS 队列拆分视频、音频、播放任务。
- 使用 UDP 自定义协议传输 hello、音频、视频分片和 AI 下行音频。

## 5. 一句话概括

本项目采用“ESP32 端侧采集 + Go 实时中转 + Python 多模型视觉导航 + DashScope/Qwen 语音多模态能力 + Vue 前端监控”的框架；核心视觉模型是 YOLO/YOLOE，核心语音和大模型能力来自 DashScope 的 Paraformer 与 Qwen Omni 系列。
