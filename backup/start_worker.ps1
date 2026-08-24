$ErrorActionPreference = "Stop"

$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$WorkerDir = Join-Path $Root "python_worker"
$VenvPython = Join-Path $Root ".venv_nav\Scripts\python.exe"
if (-not (Test-Path $VenvPython)) {
    $VenvPython = Join-Path (Split-Path -Parent $Root) ".venv_nav\Scripts\python.exe"
}
if (-not (Test-Path $VenvPython)) {
    throw "找不到 Python 虚拟环境，请先创建 .venv_nav 并安装 python_worker\requirements.txt"
}

if (-not $env:WORKER_HOST) { $env:WORKER_HOST = "127.0.0.1" }
if (-not $env:WORKER_PORT) { $env:WORKER_PORT = "18082" }
if (-not $env:ENABLE_SYNC_RECORDER) { $env:ENABLE_SYNC_RECORDER = "0" }
if (-not $env:VISION_INPUT_ROTATION) { $env:VISION_INPUT_ROTATION = "cw90" }

Set-Location $WorkerDir
& $VenvPython app_main.py
