Set-Location -Path $PSScriptRoot

$envPath = Join-Path $PSScriptRoot ".env"
$runtimeDir = Join-Path $PSScriptRoot "data\runtime"
$webPidPath = Join-Path $runtimeDir "web.pid"
$crawlerPidPath = Join-Path $runtimeDir "crawler.pid"
$port = 8765

if ($env:STEAMKB_PORT) {
    $port = [int]$env:STEAMKB_PORT
} elseif (Test-Path $envPath) {
    $portLine = Get-Content -LiteralPath $envPath -ErrorAction SilentlyContinue |
        ForEach-Object { $_.Trim().TrimStart([char]0xFEFF) } |
        Where-Object { $_ -match '^STEAMKB_PORT\s*=\s*\d+\s*$' } |
        Select-Object -First 1
    if ($portLine) {
        $port = [int](($portLine -split '=', 2)[1].Trim())
    }
}

# Ignore ambient tooling proxies. The backend reads only explicit settings
# from .env and tries direct access before an enabled proxy fallback.
Remove-Item Env:STEAMKB_PROXY_URL -ErrorAction SilentlyContinue
foreach ($proxyVariable in @('HTTP_PROXY', 'HTTPS_PROXY', 'ALL_PROXY', 'http_proxy', 'https_proxy', 'all_proxy')) {
    Remove-Item "Env:$proxyVariable" -ErrorAction SilentlyContinue
}

if (Test-Path $envPath) {
    $envLines = Get-Content -LiteralPath $envPath -ErrorAction SilentlyContinue
    if ($envLines | Where-Object { $_.Trim().TrimStart([char]0xFEFF) -match '^ITAD_API_KEY\s*=\s*.+' }) {
        Write-Host "ITAD API key detected in .env."
    } else {
        Write-Host ".env exists, but ITAD_API_KEY was not found."
    }
    if ($envLines | Where-Object { $_.Trim().TrimStart([char]0xFEFF) -match '^STEAM_API_KEY\s*=\s*.+' }) {
        Write-Host "Steam Web API key detected; catalog sync enabled."
    } else {
        Write-Host "STEAM_API_KEY not found; catalog sync will wait for a key."
    }
} else {
    Write-Host ".env not found. API-key-dependent collection is disabled."
}

python -c "import fastapi, httpx, uvicorn" 2>$null
if ($LASTEXITCODE -ne 0) {
    Write-Host "Backend dependencies are missing. Install them with: python -m pip install -r requirements.txt"
    exit 1
}

New-Item -ItemType Directory -Path $runtimeDir -Force | Out-Null

# Stop prior managed processes before starting a fresh pair.
& (Join-Path $PSScriptRoot "end.ps1") -Port $port
if ($LASTEXITCODE -ne 0) {
    Write-Host "Could not stop the previous Steam-KaKaBase processes."
    exit 1
}

$portOwner = Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue
if ($portOwner) {
    Write-Host "Port $port is occupied by another process. Set STEAMKB_PORT to a free port."
    exit 1
}

$env:STEAMKB_PORT = "$port"
$url = "http://127.0.0.1:$port/"
Write-Host "Starting Steam-KaKaBase Web at $url"
$web = Start-Process `
    -FilePath "python" `
    -ArgumentList @("-u", "-m", "backend.main") `
    -WorkingDirectory $PSScriptRoot `
    -WindowStyle Hidden `
    -PassThru
Set-Content -LiteralPath $webPidPath -Value $web.Id -Encoding ascii

try {
    $ready = $false
    for ($i = 0; $i -lt 75; $i++) {
        if ($web.HasExited) { break }
        try {
            $response = Invoke-WebRequest -UseBasicParsing -Uri "http://127.0.0.1:$port/ready" -TimeoutSec 1
            if ($response.StatusCode -eq 200) {
                $ready = $true
                break
            }
        } catch {
            Start-Sleep -Milliseconds 200
        }
    }

    if (-not $ready) {
        Write-Host "Web process did not become ready. Run python -m backend.main to inspect the error."
        exit 1
    }

    Write-Host "Starting independent Steam-KaKaBase crawler"
    $crawler = Start-Process `
        -FilePath "python" `
        -ArgumentList @("-u", "-m", "backend.crawler_main") `
        -WorkingDirectory $PSScriptRoot `
        -WindowStyle Hidden `
        -PassThru
    Set-Content -LiteralPath $crawlerPidPath -Value $crawler.Id -Encoding ascii

    Start-Sleep -Milliseconds 500
    if ($crawler.HasExited) {
        Write-Host "Crawler exited during startup (exit code $($crawler.ExitCode)). Another crawler may own the lease."
        exit 1
    }

    Start-Process $url
    Write-Host "Web PID: $($web.Id); crawler PID: $($crawler.Id)"
    Write-Host "Both processes are running. Close this window or run .\end.ps1 to stop them."

    while (-not $web.HasExited -and -not $crawler.HasExited) {
        Start-Sleep -Seconds 1
    }
    # Give end.ps1 time to stop the companion process and remove both PID files.
    Start-Sleep -Seconds 1
    $managedStop = -not (Test-Path -LiteralPath $webPidPath) -and
        -not (Test-Path -LiteralPath $crawlerPidPath)
    if ($managedStop) {
        Write-Host "Steam-KaKaBase Web and crawler stopped."
    } else {
        if ($web.HasExited) {
            Write-Host "Web process exited unexpectedly (exit code $($web.ExitCode))."
        }
        if ($crawler.HasExited) {
            Write-Host "Crawler process exited unexpectedly (exit code $($crawler.ExitCode))."
        }
    }
} finally {
    foreach ($process in @($crawler, $web)) {
        if ($process -and -not $process.HasExited) {
            Stop-Process -Id $process.Id -Force -ErrorAction SilentlyContinue
        }
    }
    Remove-Item -LiteralPath $webPidPath, $crawlerPidPath -Force -ErrorAction SilentlyContinue
}
