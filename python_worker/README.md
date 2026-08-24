# Python Vision Worker

This worker lives inside `bs/esp32-glass-all` now. It exposes the vision-navigation APIs used by the Go backend:

- `GET /api/vision/status`
- `POST /api/vision/control`
- `POST /api/vision/process`

Run it from the `bs/esp32-glass-all` root:

```powershell
.\start_worker.ps1
```

Or directly:

```powershell
cd python_worker
..\..\.venv_nav\Scripts\python.exe app_main.py
```

Default address: `http://127.0.0.1:18082`.
