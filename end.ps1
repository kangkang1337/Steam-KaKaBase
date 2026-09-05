param(
    [int]$Port = 0
)

Set-Location -Path $PSScriptRoot

if ($Port -le 0) {
    if ($env:STEAMKB_PORT) {
        $Port = [int]$env:STEAMKB_PORT
    } else {
        $Port = 8765
    }
}

Write-Host "Stopping Steam-KaKaBase backend on port $Port"

$connections = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
if ($connections) {
    $processIds = $connections | Select-Object -ExpandProperty OwningProcess -Unique
} else {
    $processIds = @(netstat -ano | Select-String ":$Port\s+.*LISTENING" | ForEach-Object {
        $parts = ($_.Line.Trim() -split "\s+")
        [int]$parts[-1]
    } | Sort-Object -Unique)
}

if (-not $processIds) {
    Write-Host "No backend is listening on port $Port"
    exit 0
}
foreach ($processId in $processIds) {
    try {
        $process = Get-Process -Id $processId -ErrorAction Stop
        Write-Host "Stopping process $processId ($($process.ProcessName))"
        Stop-Process -Id $processId -Force -ErrorAction Stop
    } catch {
        Write-Host "Could not stop process ${processId}: $($_.Exception.Message)"
    }
}

Start-Sleep -Milliseconds 300
$remaining = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
if ($remaining) {
    Write-Host "Port $Port is still occupied. You may need to close the process manually or run PowerShell as administrator."
    exit 1
}

Write-Host "Port $Port is clear."
