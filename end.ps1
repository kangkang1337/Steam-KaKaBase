param(
    [int]$Port = 0
)

Set-Location -Path $PSScriptRoot

$envPath = Join-Path $PSScriptRoot ".env"
$runtimeDir = Join-Path $PSScriptRoot "data\runtime"
$webPidPath = Join-Path $runtimeDir "web.pid"
$crawlerPidPath = Join-Path $runtimeDir "crawler.pid"

if ($Port -le 0) {
    if ($env:STEAMKB_PORT) {
        $Port = [int]$env:STEAMKB_PORT
    } elseif (Test-Path $envPath) {
        $portLine = Get-Content -LiteralPath $envPath -ErrorAction SilentlyContinue |
            ForEach-Object { $_.Trim().TrimStart([char]0xFEFF) } |
            Where-Object { $_ -match '^STEAMKB_PORT\s*=\s*\d+\s*$' } |
            Select-Object -First 1
        $Port = if ($portLine) { [int](($portLine -split '=', 2)[1].Trim()) } else { 8765 }
    } else {
        $Port = 8765
    }
}

$failed = $false
$stopped = [System.Collections.Generic.HashSet[int]]::new()

function Stop-ManagedPid([string]$PidPath, [string]$ExpectedModule, [string]$Label) {
    if (-not (Test-Path -LiteralPath $PidPath)) { return }
    $savedPid = 0
    [void][int]::TryParse((Get-Content -LiteralPath $PidPath -ErrorAction SilentlyContinue | Select-Object -First 1), [ref]$savedPid)
    if ($savedPid -le 0) {
        Remove-Item -LiteralPath $PidPath -Force -ErrorAction SilentlyContinue
        return
    }
    $process = Get-Process -Id $savedPid -ErrorAction SilentlyContinue
    if (-not $process) {
        Remove-Item -LiteralPath $PidPath -Force -ErrorAction SilentlyContinue
        return
    }
    $command = Get-CimInstance Win32_Process -Filter "ProcessId = $savedPid" -ErrorAction SilentlyContinue
    if (-not $command -or $command.CommandLine -notlike "*$ExpectedModule*") {
        Write-Host "Ignoring stale $Label PID file; process $savedPid is not $ExpectedModule."
        Remove-Item -LiteralPath $PidPath -Force -ErrorAction SilentlyContinue
        return
    }
    try {
        Write-Host "Stopping $Label process $savedPid"
        Stop-Process -Id $savedPid -Force -ErrorAction Stop
        [void]$stopped.Add($savedPid)
        if ($ExpectedModule -eq "backend.crawler_main") {
            python -c "from backend.db import release_process_lease_by_pid; release_process_lease_by_pid('crawler', $savedPid)"
            if ($LASTEXITCODE -ne 0) {
                Write-Host "Crawler stopped, but its SQLite lease could not be cleared."
                $script:failed = $true
            }
        }
    } catch {
        Write-Host "Could not stop $Label process ${savedPid}: $($_.Exception.Message)"
        $script:failed = $true
    }
    Remove-Item -LiteralPath $PidPath -Force -ErrorAction SilentlyContinue
}

Stop-ManagedPid $crawlerPidPath "backend.crawler_main" "crawler"
Stop-ManagedPid $webPidPath "backend.main" "Web"

# Also handle Web processes started manually without a PID file.
$connections = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
$processIds = @($connections | Select-Object -ExpandProperty OwningProcess -Unique)
foreach ($processId in $processIds) {
    if ($stopped.Contains([int]$processId)) { continue }
    try {
        $process = Get-Process -Id $processId -ErrorAction Stop
        Write-Host "Stopping process $processId ($($process.ProcessName)) on port $Port"
        Stop-Process -Id $processId -Force -ErrorAction Stop
    } catch {
        Write-Host "Could not stop process ${processId}: $($_.Exception.Message)"
        $failed = $true
    }
}

Start-Sleep -Milliseconds 300
if (Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue) {
    Write-Host "Port $Port is still occupied."
    $failed = $true
} else {
    Write-Host "Steam-KaKaBase Web port $Port is clear; managed crawler is stopped."
}

if ($failed) { exit 1 }
exit 0
