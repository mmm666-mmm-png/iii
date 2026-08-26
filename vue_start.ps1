# 设置目标文件夹路径（请根据实际情况修改）
$basePath = ".\"

# 便携版 Node 20：vite 8 要求 Node ^20.19.0，系统自带的是 v16，版本过旧会报
# "'vite' 不是内部或外部命令" 或运行期引擎错误。优先使用仓库内自带的 node-v20。
# 解析仓库根目录：不同终端里 $PSScriptRoot 可能为空，按优先级回退。
$repoRoot = $PSScriptRoot
if (-not $repoRoot) {
    $scriptPath = $MyInvocation.MyCommand.Path
    if ($scriptPath) { $repoRoot = Split-Path -Parent $scriptPath }
}
if (-not $repoRoot) {
    $repoRoot = (Resolve-Path $basePath -ErrorAction SilentlyContinue).Path
}
if (-not $repoRoot) {
    Write-Host "错误：无法定位仓库根目录。" -ForegroundColor Red
    exit 1
}

$nodeDir = Join-Path $repoRoot ".tools\node-v20.19.4-win-x64"
if (Test-Path (Join-Path $nodeDir "node.exe")) {
    $env:Path = "$nodeDir;$env:Path"
    Write-Host "Using portable Node: $(& (Join-Path $nodeDir 'node.exe') -v)" -ForegroundColor Green
} else {
    Write-Host "错误：找不到便携 Node：$nodeDir" -ForegroundColor Red
    Write-Host "系统 Node 版本过旧（v16），无法运行 vite 8，请确认上述 node.exe 存在。" -ForegroundColor Red
    exit 1
}

# 1. 进入子文件夹 esp32-glass-front
$serverPath = Join-Path $basePath "esp32-glass-front"
Set-Location $serverPath

Write-Host "已进入目录: $serverPath" -ForegroundColor Green

# 2. 运行 npm 程序
npm run dev