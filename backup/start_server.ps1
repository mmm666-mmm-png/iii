$ErrorActionPreference = "Stop"

$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$ServerDir = Join-Path $Root "server"

if (-not $env:VISION_WORKER_URL) { $env:VISION_WORKER_URL = "http://127.0.0.1:18082" }
$env:GOTELEMETRY = "off"

Set-Location $ServerDir
go run ./cmd/xiao-stream
