# ESP32 Glass Frontend

这个目录现在是独立前端项目，后端不再内嵌页面。

## 开发启动

1. 启动 Go 后端：

```powershell
cd ..\server
go run ./cmd/xiao-stream
```

后端地址、设备 token、数据库和 DashScope 都在 `../server/.env` 里配置。

2. 启动前端：

```powershell
npm install
npm run dev
```

默认开发环境会把 `/api`、`/snapshot.jpg`、`/healthz` 和 `/ws` 代理到 `http://127.0.0.1:8888`。

## 环境变量

复制 `.env.example` 为 `.env.local` 后可按需修改：

- `VITE_DEV_PROXY_TARGET`：Vite 开发代理目标，默认 `http://127.0.0.1:8888`
- `VITE_BACKEND_ORIGIN`：当前端部署到其他域名时，显式指定后端地址，例如 `http://192.168.1.244:8888`

## 生产构建

```powershell
npm run build
```

构建产物在 `dist/`，可以单独部署到 Nginx、Vercel 或任意静态文件服务。
