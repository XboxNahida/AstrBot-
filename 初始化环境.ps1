$ErrorActionPreference = 'Stop'
if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
    throw '请先安装 uv：https://docs.astral.sh/uv/getting-started/installation/'
}
Push-Location (Join-Path $PSScriptRoot 'AstrBot-master')
try {
    uv sync
    if ($LASTEXITCODE -ne 0) { throw '依赖安装失败，请根据上方错误处理后重试。' }
    & '.\.venv\Scripts\python.exe' (Join-Path $PSScriptRoot '初始化配置.py')
    if ($LASTEXITCODE -ne 0) { throw '配置初始化失败。' }
} finally {
    Pop-Location
}
Write-Host '环境准备完成。安装 .NET 8 桌面运行时后，可双击 AstrBot启动器.exe。'
