$ErrorActionPreference = "Stop"

$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$FrontendDir = Join-Path $Root "esp32-glass-front"

if (-not $env:VITE_DEV_PROXY_TARGET) { $env:VITE_DEV_PROXY_TARGET = "http://127.0.0.1:8888" }

Set-Location $FrontendDir
npm run dev -- --host 127.0.0.1 --port 5174
