# XIAO Stream Backend

## Directory Layout

- `cmd/xiao-stream`: backend process entrypoint.
- `internal/serverapp/config.go`: `.env` loading, defaults, and flag overrides.
- `internal/serverapp/server.go`: HTTP routes, WebSocket viewers, and shared stream state.
- `internal/serverapp/udp_ingest.go`: ESP32 UDP packet ingest and video fragment assembly.
- `internal/serverapp/dashscope_bridge.go`: DashScope realtime bridge.
- `internal/serverapp/vision_worker.go`: Python vision-navigation worker bridge.
- `internal/serverapp/navigation_voice.go`: pregenerated navigation wav loading and device playback.
- `internal/serverapp/stream_stats.go`: UDP stream quality and packet statistics.
- `internal/serverapp/skills.go`: server-side skill registry and built-in skills.
- `internal/serverapp/device_playback.go`: AI audio playback back to the device.

## Configuration

Create local config from the template:

```powershell
Copy-Item .env.example .env
```

Run the backend:

```powershell
go run ./cmd/xiao-stream
```

Optional vision worker integration. From the `bs/esp32-glass-all` root, the worker is in `python_worker`:

```dotenv
VISION_WORKER_URL=http://127.0.0.1:18082
VISION_FRAME_INTERVAL_MS=450
NAVIGATION_VOICE_DIR=../python_worker/voice
```

The worker is expected to expose `/api/vision/status`, `/api/vision/control`, and `/api/vision/process`.
When navigation guidance changes, the backend reads the matching pregenerated wav from `NAVIGATION_VOICE_DIR` and sends PCM16 audio to the ESP32 speaker over the existing UDP playback channel.

Command-line flags can still override `.env` when needed:

```powershell
go run ./cmd/xiao-stream -http-addr :8888 -udp-addr :8888
```
