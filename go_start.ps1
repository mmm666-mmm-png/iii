# Set the target folder path (adjust as needed)
$basePath = ".\"

# 1. Enter the server subfolder
$serverPath = Join-Path $basePath "server"
Set-Location $serverPath

Write-Host "Entered directory: $serverPath" -ForegroundColor Green

# 2. Free port 8888: stop any old xiao-stream process to avoid bind conflict
$listeners = Get-NetTCPConnection -LocalPort 8888 -State Listen -ErrorAction SilentlyContinue
$ownerPids = $listeners | Select-Object -ExpandProperty OwningProcess -Unique
foreach ($ownerPid in $ownerPids) {
    if ($ownerPid -and $ownerPid -ne $PID) {
        $proc = Get-Process -Id $ownerPid -ErrorAction SilentlyContinue
        if ($proc -and $proc.ProcessName -like "*xiao-stream*") {
            Write-Host "Stopping old xiao-stream process (PID $ownerPid)..." -ForegroundColor Yellow
            Stop-Process -Id $ownerPid -Force
            Start-Sleep -Milliseconds 500
        }
    }
}

# 3. Run the Go program
go run ./cmd/xiao-stream