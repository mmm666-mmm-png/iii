# 设置目标文件夹路径（请根据实际情况修改）
$basePath = ".\"

# 1. 进入子文件夹 python_worker
$workerPath = Join-Path $basePath "python_worker"
Set-Location $workerPath

Write-Host "Already entry: $workerPath" -ForegroundColor Green

# 2. 定位 Python 解释器（使用 VS Code 创建的 .venv_nav 环境）。
# 注意：在 .ps1 脚本里 `conda activate` 经常因 conda 未初始化而失效，
# 导致误用 base 环境的 python（缺少 cv2/torch 等依赖），因此这里直接用绝对路径更可靠。
$NavPython = "D:\qq download\429500506\FileRecv\esp32-glass-all-zhu\esp32-glass-all-zhu\.venv_nav\Scripts\python.exe"
$CondaPython = "D:\conda\envs\esp32\python.exe"
if (Test-Path $NavPython) {
    $Python = $NavPython
} elseif (-not (Test-Path $CondaPython)) {
    # 兜底：用 conda run 解析 esp32 环境，仍失败则退回系统 python
    try {
        $CondaPython = (conda run -n esp32 where python 2>$null | Select-Object -Last 1).Trim()
    } catch {
        $CondaPython = "python"
    }
    $Python = $CondaPython
} else {
    $Python = $CondaPython
}

Write-Host "Using Python: $Python" -ForegroundColor Green

# 3. 端口占用检测：若 worker 端口已被占用，自动结束旧的 worker 进程，避免重复启动报错
$WorkerPort = 18082
if ($env:WORKER_PORT) { $WorkerPort = [int]$env:WORKER_PORT }
$existing = Get-NetTCPConnection -State Listen -LocalPort $WorkerPort -ErrorAction SilentlyContinue | Select-Object -First 1
if ($existing) {
    $oldPid = $existing.OwningProcess
    $oldProc = Get-CimInstance Win32_Process -Filter "ProcessId=$oldPid" -ErrorAction SilentlyContinue
    $cmdline = "$($oldProc.CommandLine)"
    Write-Host "端口 $WorkerPort 已被 PID $oldPid 占用: $cmdline" -ForegroundColor Yellow
    if ($cmdline -match 'app_main\.py') {
        Write-Host "检测到旧的 worker 进程，正在结束 PID $oldPid 后重启 ..." -ForegroundColor Yellow
        Stop-Process -Id $oldPid -Force -ErrorAction SilentlyContinue
        Start-Sleep -Seconds 2
    } else {
        Write-Host "端口 $WorkerPort 被其他程序占用，无法启动。请手动释放端口后重试。" -ForegroundColor Red
        exit 1
    }
}

# 4. 运行 app_main.py
& $Python app_main.py
