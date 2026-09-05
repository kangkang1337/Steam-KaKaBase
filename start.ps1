Set-Location -Path $PSScriptRoot

$port = 8765
if ($env:STEAMKB_PORT) {
    $port = [int]$env:STEAMKB_PORT
}

$env:STEAMKB_PLAYER_REFRESH_MINUTES = "30"
$env:STEAMKB_PRICE_REFRESH_HOURS = "24"
$env:STEAMKB_TRACKED_REFRESH_BATCH_LIMIT = "1"
$env:STEAMKB_HOTLIST_TARGET = "100"
$env:STEAMKB_CATALOG_LIMIT = "20000"
$env:STEAMKB_CATALOG_ENRICH_DAILY_LIMIT = "1500"
$env:STEAMKB_CATALOG_ENRICH_BATCH_LIMIT = "50"
$env:STEAMKB_NICHE_POOL_LIMIT = "500"
# Default to direct Steam access. Set STEAMKB_PROXY_URL in .env only when a
# local proxy is deliberately required.
Remove-Item Env:STEAMKB_PROXY_URL -ErrorAction SilentlyContinue
# Do not inherit tooling/system proxy variables such as 127.0.0.1:9. The
# backend uses only the deliberate STEAMKB_PROXY_URL value from .env.
foreach ($proxyVariable in @('HTTP_PROXY', 'HTTPS_PROXY', 'ALL_PROXY', 'http_proxy', 'https_proxy', 'all_proxy')) {
    Remove-Item "Env:$proxyVariable" -ErrorAction SilentlyContinue
}

$envPath = Join-Path $PSScriptRoot ".env"
if (Test-Path $envPath) {
    $envLines = Get-Content -LiteralPath $envPath -ErrorAction SilentlyContinue
    $hasItadKey = $false
    foreach ($line in $envLines) {
        $cleanLine = $line.Trim().TrimStart([char]0xFEFF)
        if ($cleanLine -match '^ITAD_API_KEY\s*=\s*.+') {
            $hasItadKey = $true
            break
        }
    }
    if ($hasItadKey) {
        Write-Host "ITAD API key detected in .env."
    } else {
        Write-Host ".env exists, but ITAD_API_KEY was not found."
    }
    $hasSteamKey = $false
    foreach ($line in $envLines) {
        $cleanLine = $line.Trim().TrimStart([char]0xFEFF)
        if ($cleanLine -match '^STEAM_API_KEY\s*=\s*.+') {
            $hasSteamKey = $true
            break
        }
    }
    if ($hasSteamKey) {
        Write-Host "Steam Web API key detected; full catalog sync enabled."
    } else {
        Write-Host "STEAM_API_KEY not found; IStoreService catalog sync will wait for a key."
    }
} else {
    Write-Host ".env not found at $envPath. ITAD historical lows will be disabled."
}

$httpxCheck = python -c "import importlib.util; raise SystemExit(0 if importlib.util.find_spec('httpx') else 1)" 2>$null
if ($LASTEXITCODE -ne 0) {
    Write-Host "Optional async hotlist crawler needs httpx. Install it with: python -m pip install -r requirements.txt"
}

function Test-PortInUse($candidatePort) {
    $connection = Get-NetTCPConnection -LocalAddress 127.0.0.1 -LocalPort $candidatePort -State Listen -ErrorAction SilentlyContinue
    return $null -ne $connection
}

$existing = Get-NetTCPConnection -LocalAddress 127.0.0.1 -LocalPort $port -State Listen -ErrorAction SilentlyContinue |
    Select-Object -ExpandProperty OwningProcess -Unique
foreach ($processId in $existing) {
    try {
        Write-Host "Stopping old backend process $processId on port $port"
        Stop-Process -Id $processId -Force -ErrorAction Stop
    } catch {
        Write-Host "Could not stop process $processId. If the page still shows old data, close the old backend manually."
    }
}

while (Test-PortInUse $port) {
    Write-Host "Port $port is still occupied; trying next port."
    $port += 1
}

$env:STEAMKB_PORT = "$port"
$url = "http://127.0.0.1:$port/"
Write-Host "Starting Steam-KaKaBase at $url"

$server = Start-Process `
    -FilePath "python" `
    -ArgumentList @("-u", "-m", "backend.main") `
    -WorkingDirectory $PSScriptRoot `
    -WindowStyle Hidden `
    -PassThru

try {
    $ready = $false
    for ($i = 0; $i -lt 50; $i++) {
        try {
            $client = [Net.Sockets.TcpClient]::new()
            $client.Connect("127.0.0.1", $port)
            $client.Close()
            $ready = $true
            break
        } catch {
            Start-Sleep -Milliseconds 200
        }
    }

    if ($ready) {
        Start-Process $url
        Write-Host "Steam-KaKaBase is running. Close this window to stop the backend."
        Write-Host "Auto collection: players every $env:STEAMKB_PLAYER_REFRESH_MINUTES minutes; prices/reviews every $env:STEAMKB_PRICE_REFRESH_HOURS hours."
        Wait-Process -Id $server.Id
    } else {
        Write-Host "Backend did not start on port $port. Run python -m backend.main to see details."
    }
} finally {
    if ($server -and -not $server.HasExited) {
        Stop-Process -Id $server.Id -Force
    }
}
