# 备份并同步生图配置，密钥仅从本次进程环境变量读取。
$ErrorActionPreference = 'Stop'
if (!$env:ASTRBOT_TEST_IMAGE_KEY) { throw '缺少本次配置所需的临时密钥。' }
$workspace = Split-Path $PSScriptRoot -Parent
$path = Join-Path $workspace 'AstrBot-master/data/config/astrbot_plugin_private_companion_config.json'
$raw = Get-Content -Raw -Encoding UTF8 $path
$config = $raw | ConvertFrom-Json -AsHashtable
if ($config.photo_action_config -isnot [System.Collections.IDictionary]) {
    throw '未找到当前版本的 photo_action_config，停止更新。'
}
$backup = "$path.before-heiheiai-$(Get-Date -Format 'yyyyMMdd-HHmmss').bak"
Copy-Item -LiteralPath $path -Destination $backup -ErrorAction Stop
foreach ($section in @($config, $config.photo_action_config)) {
    $section.EXTERNAL_IMAGE_API_BASE_URL = 'https://www.heiheiai.shop/v1'
    $section.EXTERNAL_IMAGE_API_KEY = $env:ASTRBOT_TEST_IMAGE_KEY
    $section.EXTERNAL_IMAGE_API_MODEL = 'gpt-image-2'
    $section.external_image_api_platform = 'openai'
    $section.external_image_download_proxy = ''
    $section.external_image_download_use_environment_proxy = $false
}
if ((Get-Content -Raw -Encoding UTF8 $path) -cne $raw) {
    throw '配置在读取后发生变化，停止覆盖。'
}
$config | ConvertTo-Json -Depth 100 | Set-Content -LiteralPath $path -Encoding UTF8
$verified = Get-Content -Raw -Encoding UTF8 $path | ConvertFrom-Json -AsHashtable
foreach ($section in @($verified, $verified.photo_action_config)) {
    if ($section.EXTERNAL_IMAGE_API_BASE_URL -ne 'https://www.heiheiai.shop/v1' -or
        $section.EXTERNAL_IMAGE_API_KEY -cne $env:ASTRBOT_TEST_IMAGE_KEY -or
        $section.external_image_download_proxy -or $section.external_image_download_use_environment_proxy) {
        throw '配置验证失败，请保留备份。'
    }
}
[pscustomobject]@{ verified = $true; base_url = $verified.EXTERNAL_IMAGE_API_BASE_URL; proxy = $false; backup = $backup } | ConvertTo-Json
