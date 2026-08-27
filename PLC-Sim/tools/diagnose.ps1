param(
    [string]$HostName = "127.0.0.1",
    [int]$Port = 18765
)

$ErrorActionPreference = "Stop"
$baseUrl = "http://${HostName}:${Port}"

Write-Host "PLC-Sim diagnostics: $baseUrl" -ForegroundColor Cyan

try {
    $health = Invoke-RestMethod -Uri "$baseUrl/api/health" -TimeoutSec 3
    Write-Host "[OK] Backend health" -ForegroundColor Green
    $health | ConvertTo-Json -Depth 5
} catch {
    Write-Host "[FAIL] Backend is unavailable: $($_.Exception.Message)" -ForegroundColor Red
    Write-Host "Start it with .\start_gui.bat and retry."
    exit 1
}

try {
    $version = Invoke-RestMethod -Uri "$baseUrl/api/version" -TimeoutSec 3
    Write-Host "[OK] Backend/static version" -ForegroundColor Green
    $version | ConvertTo-Json -Depth 5
} catch {
    Write-Host "[FAIL] Version endpoint: $($_.Exception.Message)" -ForegroundColor Red
    exit 2
}

try {
    $response = Invoke-WebRequest -UseBasicParsing -Uri "$baseUrl/static/app.js" -TimeoutSec 3
    Write-Host "[OK] Frontend asset app.js ($($response.RawContentLength) bytes)" -ForegroundColor Green
} catch {
    Write-Host "[FAIL] Frontend asset: $($_.Exception.Message)" -ForegroundColor Red
    exit 3
}

Write-Host "Diagnostics passed." -ForegroundColor Green
