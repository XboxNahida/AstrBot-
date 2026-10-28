param([string]$BaseUrl = '', [switch]$RequestBase64)
$ErrorActionPreference = 'Stop'
$workspace = Split-Path $PSScriptRoot -Parent
$config = Get-Content -Raw -Encoding UTF8 (Join-Path $workspace 'AstrBot-master/data/config/astrbot_plugin_private_companion_config.json') | ConvertFrom-Json -AsHashtable
$testApiKey = if ($env:ASTRBOT_TEST_IMAGE_KEY) { $env:ASTRBOT_TEST_IMAGE_KEY } else { $config.EXTERNAL_IMAGE_API_KEY }
if (!$BaseUrl) { $BaseUrl = $config.EXTERNAL_IMAGE_API_BASE_URL }
$endpoint = $BaseUrl.TrimEnd('/') + '/images/generations'
$runDirectory = Join-Path $workspace ('artifacts/image-api-test-' + (Get-Date -Format 'yyyyMMdd-HHmmss'))
New-Item -ItemType Directory -Path $runDirectory | Out-Null
$summary = [ordered]@{ endpoint = $endpoint; model = $config.EXTERNAL_IMAGE_API_MODEL; generation_completed = $false; download_completed = $false }
$handler = [System.Net.Http.HttpClientHandler]::new()
$handler.UseProxy = $false
$handler.AllowAutoRedirect = $false
$client = [System.Net.Http.HttpClient]::new($handler)
$client.Timeout = [TimeSpan]::FromSeconds(300)
$request = [System.Net.Http.HttpRequestMessage]::new([System.Net.Http.HttpMethod]::Post, $endpoint)
$request.Headers.Authorization = [System.Net.Http.Headers.AuthenticationHeaderValue]::new('Bearer', $testApiKey)
$request.Headers.Add('Idempotency-Key', [Guid]::NewGuid().ToString())
$payloadObject = @{ model = $config.EXTERNAL_IMAGE_API_MODEL; prompt = 'A clean anime illustration of a small green tree beside a blue lake in daylight, no people, no text, no watermark.'; size = '1024x1024'; n = 1; async = $false }
if ($RequestBase64) { $payloadObject.response_format = 'b64_json' }
$payload = $payloadObject | ConvertTo-Json -Compress
$request.Content = [System.Net.Http.StringContent]::new($payload, [System.Text.Encoding]::UTF8, 'application/json')
$watch = [System.Diagnostics.Stopwatch]::StartNew()
try {
    Write-Output 'Submitting exactly one image generation request...'
    $response = $client.SendAsync($request).GetAwaiter().GetResult()
    $body = $response.Content.ReadAsStringAsync().GetAwaiter().GetResult()
    $summary.generation_seconds = [Math]::Round($watch.Elapsed.TotalSeconds, 2)
    $summary.http_status = [int]$response.StatusCode
    Write-Output ('Generation HTTP {0}, {1}s' -f $summary.http_status, $summary.generation_seconds)
    $data = $body | ConvertFrom-Json -AsHashtable
    $responseRecord = $data | ConvertTo-Json -Depth 30
    if ($testApiKey) { $responseRecord = $responseRecord.Replace($testApiKey, '[REDACTED]') }
    $responseRecord | Set-Content -Encoding UTF8 (Join-Path $runDirectory 'response.json')
    $summary.response_fields = @($data.Keys)
    $summary.task_id = $data.task_id
    if (!$response.IsSuccessStatusCode -or $data.error) {
        $summary.error = if ($data.error.message) { $data.error.message } elseif ($data.message) { $data.message } else { 'API request failed' }
    } elseif (!$data.data -or (!$data.data[0].url -and !$data.data[0].b64_json)) {
        $summary.error = 'No image URL or base64 in API response'
    } else {
        $summary.generation_completed = $true
        $item = $data.data[0]
        $imagePath = Join-Path $runDirectory 'test-image.png'
        if ($item.b64_json) {
            $summary.return_type = 'b64_json'
            $bytes = [Convert]::FromBase64String($item.b64_json)
        } else {
            $summary.return_type = 'url'
            $summary.image_url = $item.url
            Write-Output ('Downloading returned image with 35s timeout: ' + $item.url)
            $downloadHandler = [System.Net.Http.HttpClientHandler]::new()
            $downloadHandler.UseProxy = $false
            $downloadClient = [System.Net.Http.HttpClient]::new($downloadHandler)
            $downloadClient.Timeout = [TimeSpan]::FromSeconds(35)
            $downloadWatch = [System.Diagnostics.Stopwatch]::StartNew()
            try {
                $downloadResponse = $downloadClient.GetAsync([string]$item.url).GetAwaiter().GetResult()
                $summary.download_http_status = [int]$downloadResponse.StatusCode
                $downloadResponse.EnsureSuccessStatusCode() | Out-Null
                $bytes = $downloadResponse.Content.ReadAsByteArrayAsync().GetAwaiter().GetResult()
            } finally {
                $summary.download_seconds = [Math]::Round($downloadWatch.Elapsed.TotalSeconds, 2)
                $downloadClient.Dispose()
            }
        }
        [System.IO.File]::WriteAllBytes($imagePath, $bytes)
        $summary.download_completed = $true
        $summary.image_path = $imagePath
        $summary.image_bytes = $bytes.Length
    }
} catch {
    $summary.error = $_.Exception.GetBaseException().Message
    $summary.elapsed_seconds = [Math]::Round($watch.Elapsed.TotalSeconds, 2)
} finally {
    $client.Dispose()
    $request.Dispose()
    $json = $summary | ConvertTo-Json -Depth 6
    if ($testApiKey) { $json = $json.Replace($testApiKey, '[REDACTED]') }
    $json | Set-Content -Encoding UTF8 (Join-Path $runDirectory 'result.json')
    Write-Output $json
}
