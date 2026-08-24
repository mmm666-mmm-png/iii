# 服务器部署说明（不用启动脚本）

本文以 `esp32-glass-all` 为项目根目录，说明如何不用 `start_worker.ps1`、`start_server.ps1`、`start_frontend.ps1` 手动启动。

## 1. 目录和端口

```text
esp32-glass-all/
├─ python_worker/              # Python worker，默认 18082
├─ server/                     # Go 后端，HTTP/WS 和 UDP 默认 8888
├─ esp32-glass-front/          # Vue 前端，构建后是 dist/
├─ AIGlasses_for_navigation/   # 视觉模型文件和导航资产
└─ firmware/                   # ESP32 固件
```

服务器需要放行：

```text
TCP 8888   Go 后端 HTTP / WebSocket
UDP 8888   ESP32 设备上传视频和音频
TCP 5174   仅开发预览时需要；正式部署可不用
TCP 80/443 如果用 Nginx 对外提供前端
```

## 2. 安装依赖

### Python worker

在 `esp32-glass-all` 目录执行：

```bash
python3.11 -m venv .venv_nav
./.venv_nav/bin/python -m pip install --upgrade pip setuptools wheel
./.venv_nav/bin/python -m pip install -r ./python_worker/requirements.txt
```

Windows 服务器可把 Python 路径换成：

```powershell
py -3.11 -m venv .venv_nav
.\.venv_nav\Scripts\python.exe -m pip install --upgrade pip setuptools wheel
.\.venv_nav\Scripts\python.exe -m pip install -r .\python_worker\requirements.txt
```

### 国内/弱网安装加速

普通 PyPI 包可以走国内镜像：

```bash
./.venv_nav/bin/python -m pip config set global.index-url https://pypi.tuna.tsinghua.edu.cn/simple
./.venv_nav/bin/python -m pip config set global.timeout 120
./.venv_nav/bin/python -m pip install --upgrade pip setuptools wheel
./.venv_nav/bin/python -m pip install --no-cache-dir --retries 10 -r ./python_worker/requirements.txt
```

如果你是在 `python_worker` 目录里执行，路径改成：

```bash
../.venv_nav/bin/python -m pip config set global.index-url https://pypi.tuna.tsinghua.edu.cn/simple
../.venv_nav/bin/python -m pip config set global.timeout 120
../.venv_nav/bin/python -m pip install --upgrade pip setuptools wheel
../.venv_nav/bin/python -m pip install --no-cache-dir --retries 10 -r ./requirements.txt
```

`clip` 是 GitHub 源码依赖，pip 镜像不会加速它。先让 Git 避开 HTTP/2 断流，再重试：

```bash
git config --global http.version HTTP/1.1
git config --global http.postBuffer 524288000
../.venv_nav/bin/python -m pip install --no-cache-dir --retries 10 --timeout 120 \
  "git+https://github.com/ultralytics/CLIP.git@81ff68ed7ffcac3b40484c914f104f816757308d"
```

如果服务器访问 GitHub 仍然很慢，可以把 CLIP 仓库在网络更好的机器下载好，上传到服务器后本地安装：

```bash
../.venv_nav/bin/python -m pip install /path/to/CLIP
../.venv_nav/bin/python -m pip install --no-cache-dir --retries 10 -r ./requirements.txt
```

Linux 如果安装 `pyaudio` 或 OpenCV 报错，先装系统依赖：

```bash
sudo apt-get update
sudo apt-get install -y portaudio19-dev python3-pyaudio libgl1 libglib2.0-0
```

### Go 后端

```bash
cd server
go mod download
go build -o xiao-stream ./cmd/xiao-stream
cd ..
```

### 前端

```bash
cd esp32-glass-front
npm install
npm run build
cd ..
```

## 3. 配置环境变量

### Go 后端配置

复制模板：

```bash
cp ./server/.env.example ./server/.env
```

编辑 `server/.env`：

```dotenv
SERVER_HTTP_ADDR=:8888
CORS_ALLOW_ORIGIN=*

SERVER_UDP_ADDR=:8888
DEVICE_TOKEN=123456

VISION_WORKER_URL=http://127.0.0.1:18082
VISION_FRAME_INTERVAL_MS=450
NAVIGATION_VOICE_DIR=../python_worker/voice

DASHSCOPE_API_KEY=
DASHSCOPE_REGION=cn
DASHSCOPE_MODEL=qwen3-omni-flash-realtime
DASHSCOPE_VOICE=Ethan
DASHSCOPE_ENABLE_SEARCH=1
```

`DEVICE_TOKEN` 必须和固件里的 `AIGLASS_DEVICE_TOKEN` 一致。

### Python worker 环境变量

模型路径使用项目内相对路径，视觉模型在 `AIGlasses_for_navigation/`。启动 worker 前只需要设置运行参数：

```bash
export WORKER_HOST=127.0.0.1
export WORKER_PORT=18082
export ENABLE_SYNC_RECORDER=0
export VISION_INPUT_ROTATION=cw90
```

Windows PowerShell 对应写法：

```powershell
$env:WORKER_HOST="127.0.0.1"
$env:WORKER_PORT="18082"
$env:ENABLE_SYNC_RECORDER="0"
$env:VISION_INPUT_ROTATION="cw90"
```

`VISION_INPUT_ROTATION` 用于让导盲检测和标注坐标匹配前端正向预览，默认是 `cw90`。可选值：`cw90`、`ccw90`、`180`、`none`。

## 4. 手动启动

### 启动 Python worker

在 `esp32-glass-all` 目录：

```bash
./start_worker.sh
```

Windows：

```powershell
.\start_worker.ps1
```

底层等价于进入 `python_worker` 后运行：

```bash
cd python_worker
../.venv_nav/bin/python app_main.py
```

Windows：

```powershell
cd python_worker
..\.venv_nav\Scripts\python.exe app_main.py
```

验证：

```bash
curl http://127.0.0.1:18082/api/vision/status
```

云服务器没有声卡时，启动日志里可能出现 ALSA 或 pygame 音频设备警告。只要 worker 没退出、接口能访问，就不影响视觉处理；导盲提示会由 Go 后端读取 `NAVIGATION_VOICE_DIR` 里的预生成 wav，再通过设备端扬声器播放。

### 启动 Go 后端

新开一个终端，在 `esp32-glass-all` 目录：

```bash
cd server
./xiao-stream
```

也可以不提前编译，直接运行：

```bash
cd server
go run ./cmd/xiao-stream
```

Windows PowerShell 可以用：

```powershell
.\start_server.ps1
```

验证：

```bash
curl http://127.0.0.1:8888/healthz
curl http://127.0.0.1:8888/api/status
```

### 启动前端（开发预览）

只做测试时可以这样：

```bash
./start_frontend.sh
```

访问：

```text
http://服务器IP:5174/#live
```

如果浏览器不是从 Go 后端同源访问，前端需要知道后端地址。构建前创建 `esp32-glass-front/.env.production`：

```dotenv
VITE_BACKEND_ORIGIN=http://服务器IP:8888
```

然后重新构建：

```bash
cd esp32-glass-front
npm run build
```

## 5. 正式部署前端（Nginx 推荐）

前端构建产物在：

```text
esp32-glass-front/dist/
```

可以用 Nginx 托管静态文件，并把 `/api`、`/snapshot.jpg`、`/vision-snapshot.jpg`、`/healthz`、`/ws` 反向代理到 Go 后端。

示例配置：

```nginx
server {
    listen 80;
    server_name 你的域名或服务器IP;

    root /path/to/esp32-glass-all/esp32-glass-front/dist;
    index index.html;

    location / {
        try_files $uri $uri/ /index.html;
    }

    location /api/ {
        proxy_pass http://127.0.0.1:8888;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
    }

    location = /snapshot.jpg {
        proxy_pass http://127.0.0.1:8888;
    }

    location = /vision-snapshot.jpg {
        proxy_pass http://127.0.0.1:8888;
    }

    location = /healthz {
        proxy_pass http://127.0.0.1:8888;
    }

    location /ws/ {
        proxy_pass http://127.0.0.1:8888;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_set_header Host $host;
    }
}
```

这种方式下，前端和后端同源，通常不需要设置 `VITE_BACKEND_ORIGIN`。`server/.env` 里的 `CORS_ALLOW_ORIGIN` 可以保留 `*`，或改成你的域名。

## 6. ESP32 固件需要改的地方

编辑：

```text
firmware/xiao_sense_ws_stream/local_config.h
```

关键配置：

```cpp
#define AIGLASS_UDP_HOST "服务器公网IP或局域网IP"
#define AIGLASS_DEVICE_TOKEN "123456"
```

当前固件里的 UDP 目标端口固定为 `8888`，定义在 `app_config.h` 的 `UDP_PORT` 常量里。

如果设备和服务器不在同一个局域网，设备必须能访问服务器的 `UDP 8888`。云服务器还要在安全组里放行 UDP 8888。

## 7. 后台常驻运行

简单测试可以用 `tmux` 或 `screen` 分两个窗口分别跑 worker 和后端。

生产环境建议用 systemd。示例：

```ini
[Unit]
Description=AI Glass Python Worker
After=network.target

[Service]
WorkingDirectory=/path/to/esp32-glass-all/python_worker
ExecStart=/path/to/esp32-glass-all/.venv_nav/bin/python app_main.py
Restart=always

[Install]
WantedBy=multi-user.target
```

```ini
[Unit]
Description=AI Glass Go Backend
After=network.target

[Service]
WorkingDirectory=/path/to/esp32-glass-all/server
ExecStart=/path/to/esp32-glass-all/server/xiao-stream
Restart=always

[Install]
WantedBy=multi-user.target
```

## 8. 快速检查

```bash
curl http://127.0.0.1:18082/api/vision/status
curl http://127.0.0.1:8888/healthz
curl http://127.0.0.1:8888/api/status
```

浏览器打开：

```text
http://服务器IP/#live
```

如果不用 Nginx，而是 `npm run preview`：

```text
http://服务器IP:5174/#live
```
