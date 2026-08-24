# Linux 部署文档

本文以 Ubuntu 22.04/24.04 为例，项目目录假设为：

```text
/opt/ai-glass/esp32-glass-all
```

实际路径不同，把文档里的 `/opt/ai-glass/esp32-glass-all` 替换成你的路径即可。

## 1. 服务组成

```text
esp32-glass-all/
├─ python_worker/              # Python 视觉 worker，默认 127.0.0.1:18082
├─ server/                     # Go 后端，HTTP/WS + UDP，默认 :8888
├─ esp32-glass-front/          # Vue 前端，构建产物 dist/
├─ AIGlasses_for_navigation/   # 模型文件
└─ firmware/                   # ESP32 固件
```

端口：

```text
TCP 80/443   Nginx 前端入口
TCP 8888     Go 后端 HTTP/WebSocket，若只走 Nginx 可不对公网开放
UDP 8888     ESP32 上传音视频，也用于设备端语音下发
TCP 18082    Python worker，只建议监听 127.0.0.1
```

云服务器安全组至少放行：

```text
TCP 80
TCP 443      如果配置 HTTPS
UDP 8888
```

## 2. 安装系统依赖

```bash
sudo apt-get update
sudo apt-get install -y \
  git curl ca-certificates build-essential pkg-config \
  python3.11 python3.11-venv python3.11-dev \
  portaudio19-dev python3-pyaudio \
  libgl1 libglib2.0-0 libsm6 libxext6 libxrender1 \
  ffmpeg nginx
```

安装 Go 1.23。若系统源版本过低，建议用官方包：

```bash
cd /tmp
curl -LO https://go.dev/dl/go1.23.5.linux-amd64.tar.gz
sudo rm -rf /usr/local/go
sudo tar -C /usr/local -xzf go1.23.5.linux-amd64.tar.gz
echo 'export PATH=/usr/local/go/bin:$PATH' >> ~/.bashrc
export PATH=/usr/local/go/bin:$PATH
go version
```

安装 Node.js 20：

```bash
curl -fsSL https://deb.nodesource.com/setup_20.x | sudo -E bash -
sudo apt-get install -y nodejs
node -v
npm -v
```

## 3. 放置项目和模型

示例：

```bash
sudo mkdir -p /opt/ai-glass
sudo chown -R "$USER:$USER" /opt/ai-glass
cd /opt/ai-glass
```

把项目上传或克隆到：

```text
/opt/ai-glass/esp32-glass-all
```

模型文件放到：

```text
/opt/ai-glass/esp32-glass-all/AIGlasses_for_navigation/
```

至少需要：

```text
yolo-seg.pt
yoloe-11l-seg.pt
trafficlight.pt
shoppingbest5.pt
```

## 4. Python Worker

```bash
cd /opt/ai-glass/esp32-glass-all
python3.11 -m venv .venv_nav
python3 -m venv .venv_nav
./.venv_nav/bin/python -m pip install --upgrade pip setuptools wheel
./.venv_nav/bin/python -m pip install -r ./python_worker/requirements.txt
```

国内或弱网可先设置 pip 镜像：

```bash
./.venv_nav/bin/python -m pip config set global.index-url https://pypi.tuna.tsinghua.edu.cn/simple
./.venv_nav/bin/python -m pip config set global.timeout 120
```

`requirements.txt` 里的 `clip` 来自 GitHub，镜像不会加速。网络不稳定时先设置 Git：

```bash
git config --global http.version HTTP/1.1
git config --global http.postBuffer 524288000
```

如果服务器没有 GPU，也可以先按当前依赖跑 CPU 版本；只是视觉模型推理会慢。

手动启动 worker：

```bash
cd /opt/ai-glass/esp32-glass-all
export WORKER_HOST=127.0.0.1
export WORKER_PORT=18082
export BLIND_PATH_MODEL="$PWD/AIGlasses_for_navigation/yolo-seg.pt"
export OBSTACLE_MODEL="$PWD/AIGlasses_for_navigation/yoloe-11l-seg.pt"
export AIGLASS_OBS_MODEL="$PWD/AIGlasses_for_navigation/yoloe-11l-seg.pt"
export TRAFFIC_LIGHT_MODEL="$PWD/AIGlasses_for_navigation/trafficlight.pt"
export YOLOE_MODEL_PATH="$PWD/AIGlasses_for_navigation/yoloe-11l-seg.pt"
export SHOPPING_MODEL="$PWD/AIGlasses_for_navigation/shoppingbest5.pt"
export ENABLE_SYNC_RECORDER=0
export VISION_INPUT_ROTATION=cw90
cd python_worker
../.venv_nav/bin/python app_main.py
```

也可以直接使用项目里的启动脚本：

```bash
cd /opt/ai-glass/esp32-glass-all
chmod +x start_worker.sh
./start_worker.sh
```

验证：

```bash
curl http://127.0.0.1:18082/api/vision/status
```

说明：

```text
VISION_INPUT_ROTATION=cw90
```

表示先把设备画面顺时针旋转 90 度，再做导盲检测和标注。可选值：`cw90`、`ccw90`、`180`、`none`。

## 5. Go 后端

创建配置：

```bash
cd /opt/ai-glass/esp32-glass-all
cp ./server/.env.example ./server/.env
nano ./server/.env
```

推荐内容：

```dotenv
SERVER_HTTP_ADDR=:8888
SERVER_UDP_ADDR=:8888
CORS_ALLOW_ORIGIN=*

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

`NAVIGATION_VOICE_DIR` 是预生成导盲语音 wav 目录。导盲模式下，后端会读取这里的 wav，然后通过 UDP 推给 ESP32 设备端扬声器。

编译并启动：

```bash
cd /opt/ai-glass/esp32-glass-all/server
go mod download
go build -o xiao-stream ./cmd/xiao-stream
./xiao-stream
```

验证：

```bash
curl http://127.0.0.1:8888/healthz
curl http://127.0.0.1:8888/api/status
```

## 6. 前端构建

如果前端通过 Nginx 同源访问 Go 后端，不需要设置 `VITE_BACKEND_ORIGIN`：

```bash
cd /opt/ai-glass/esp32-glass-all/esp32-glass-front
npm install
npm run build
```

开发预览或临时启动 Web 页面可以用：

```bash
cd /opt/ai-glass/esp32-glass-all
chmod +x start_frontend.sh
./start_frontend.sh
```

默认监听：

```text
0.0.0.0:5174
```

如果你不用 Nginx 代理，而是直接访问前端静态站点，需要在构建前写后端地址：

```bash
cat > .env.production <<'EOF'
VITE_BACKEND_ORIGIN=http://服务器IP:8888
EOF
npm run build
```

## 7. Nginx 配置

创建站点配置：

```bash
sudo nano /etc/nginx/sites-available/ai-glass.conf
```

写入：

```nginx
server {
    listen 80;
    server_name 你的域名或服务器IP;

    root /opt/ai-glass/esp32-glass-all/esp32-glass-front/dist;
    index index.html;

    client_max_body_size 20m;

    location / {
        try_files $uri $uri/ /index.html;
    }

    location /api/ {
        proxy_pass http://127.0.0.1:8888;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    }

    location = /healthz {
        proxy_pass http://127.0.0.1:8888;
    }

    location = /snapshot.jpg {
        proxy_pass http://127.0.0.1:8888;
        proxy_buffering off;
    }

    location = /vision-snapshot.jpg {
        proxy_pass http://127.0.0.1:8888;
        proxy_buffering off;
    }

    location /ws/ {
        proxy_pass http://127.0.0.1:8888;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_set_header Host $host;
        proxy_read_timeout 3600s;
        proxy_send_timeout 3600s;
    }
}
```

启用：

```bash
sudo ln -sf /etc/nginx/sites-available/ai-glass.conf /etc/nginx/sites-enabled/ai-glass.conf
sudo nginx -t
sudo systemctl reload nginx
```

访问：

```text
http://服务器IP/#live
```

## 8. systemd 常驻运行

创建 worker 服务：

```bash
sudo nano /etc/systemd/system/ai-glass-worker.service
```

写入：

```ini
[Unit]
Description=AI Glass Python Worker
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
WorkingDirectory=/opt/ai-glass/esp32-glass-all/python_worker
Environment=WORKER_HOST=127.0.0.1
Environment=WORKER_PORT=18082
Environment=BLIND_PATH_MODEL=/opt/ai-glass/esp32-glass-all/AIGlasses_for_navigation/yolo-seg.pt
Environment=OBSTACLE_MODEL=/opt/ai-glass/esp32-glass-all/AIGlasses_for_navigation/yoloe-11l-seg.pt
Environment=AIGLASS_OBS_MODEL=/opt/ai-glass/esp32-glass-all/AIGlasses_for_navigation/yoloe-11l-seg.pt
Environment=TRAFFIC_LIGHT_MODEL=/opt/ai-glass/esp32-glass-all/AIGlasses_for_navigation/trafficlight.pt
Environment=YOLOE_MODEL_PATH=/opt/ai-glass/esp32-glass-all/AIGlasses_for_navigation/yoloe-11l-seg.pt
Environment=SHOPPING_MODEL=/opt/ai-glass/esp32-glass-all/AIGlasses_for_navigation/shoppingbest5.pt
Environment=ENABLE_SYNC_RECORDER=0
Environment=VISION_INPUT_ROTATION=cw90
ExecStart=/opt/ai-glass/esp32-glass-all/.venv_nav/bin/python app_main.py
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
```

创建 Go 后端服务：

```bash
sudo nano /etc/systemd/system/ai-glass-server.service
```

写入：

```ini
[Unit]
Description=AI Glass Go Backend
After=network-online.target ai-glass-worker.service
Wants=network-online.target

[Service]
Type=simple
WorkingDirectory=/opt/ai-glass/esp32-glass-all/server
ExecStart=/opt/ai-glass/esp32-glass-all/server/xiao-stream
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
```

启动：

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now ai-glass-worker
sudo systemctl enable --now ai-glass-server
sudo systemctl status ai-glass-worker --no-pager
sudo systemctl status ai-glass-server --no-pager
```

看日志：

```bash
journalctl -u ai-glass-worker -f
journalctl -u ai-glass-server -f
```

## 9. ESP32 固件配置

编辑：

```text
firmware/xiao_sense_ws_stream/local_config.h
```

关键项：

```cpp
#define AIGLASS_UDP_HOST "服务器公网IP或局域网IP"
#define AIGLASS_DEVICE_TOKEN "123456"
```

`AIGLASS_DEVICE_TOKEN` 要和 `server/.env` 的 `DEVICE_TOKEN` 一致。

设备必须能访问服务器 UDP 8888。云服务器要同时检查：

```text
Linux 防火墙
云厂商安全组
路由/NAT
```

## 10. 防火墙

如果启用了 UFW：

```bash
sudo ufw allow 80/tcp
sudo ufw allow 443/tcp
sudo ufw allow 8888/udp
sudo ufw status
```

如果临时直接访问 Go 后端：

```bash
sudo ufw allow 8888/tcp
```

## 11. 快速验收

本机检查：

```bash
curl http://127.0.0.1:18082/api/vision/status
curl http://127.0.0.1:8888/healthz
curl http://127.0.0.1:8888/api/status
```

Nginx 检查：

```bash
curl http://127.0.0.1/healthz
```

浏览器打开：

```text
http://服务器IP/#live
```

设备上线后，页面应看到：

```text
设备在线
实时画面
导盲/问答模式切换
导航事件
```

## 12. 常见问题

### worker 启动时报 ALSA 或 pygame 声卡错误

云服务器没有声卡时常见。只要 `/api/vision/status` 正常返回，通常不影响视觉处理。导盲语音由 Go 后端读取预生成 wav 并通过 ESP32 扬声器播放。

### 页面能打开，但没有设备画面

检查：

```bash
sudo ss -lunpt | grep 8888
journalctl -u ai-glass-server -f
```

确认云安全组和 Linux 防火墙都放行 UDP 8888，固件里的服务器 IP 和 token 正确。

### 导盲图方向不对

调整 worker 的：

```text
VISION_INPUT_ROTATION
```

可选：

```text
cw90
ccw90
180
none
```

修改 systemd 文件后执行：

```bash
sudo systemctl daemon-reload
sudo systemctl restart ai-glass-worker
```

### 导盲没有设备端语音

检查 Go 后端日志是否加载语音目录：

```bash
journalctl -u ai-glass-server -f
```

确认：

```text
server/.env 里的 NAVIGATION_VOICE_DIR=../python_worker/voice
python_worker/voice/map.zh-CN.json 存在
ESP32 hello 中 speaker enabled
UDP 8888 可达
```

也可以用后端扬声器测试接口：

```bash
curl "http://127.0.0.1:8888/api/debug/speaker-test?durationMs=700&freq=880"
```

### Go 后端连不上 worker

检查：

```bash
curl http://127.0.0.1:18082/api/vision/status
grep VISION_WORKER_URL /opt/ai-glass/esp32-glass-all/server/.env
```

### 前端请求跨域失败

推荐用 Nginx 同源代理。如果直接访问 `5174` 或静态站点，构建前设置：

```dotenv
VITE_BACKEND_ORIGIN=http://服务器IP:8888
```

然后重新：

```bash
npm run build
```
