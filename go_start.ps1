# 设置目标文件夹路径（请根据实际情况修改）
$basePath = ".\"

# 1. 进入子文件夹 server
$serverPath = Join-Path $basePath "server"
Set-Location $serverPath

Write-Host "已进入目录: $serverPath" -ForegroundColor Green

# 2. 运行 Go 程序
go run ./cmd/xiao-stream