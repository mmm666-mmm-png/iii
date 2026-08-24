# 设置目标文件夹路径（请根据实际情况修改）
$basePath = ".\"

# 1. 进入子文件夹 esp32-glass-front
$serverPath = Join-Path $basePath "esp32-glass-front"
Set-Location $serverPath

Write-Host "已进入目录: $serverPath" -ForegroundColor Green

# 2. 运行 npm 程序
npm run dev