param([string]$ServiceName = "RumoWave")

$Root = Split-Path $PSScriptRoot

function Write-Step { param($m) Write-Host "`n>> $m" -ForegroundColor Cyan }
function Write-OK   { param($m) Write-Host "   [PASS] $m" -ForegroundColor Green }
function Write-Fail { param($m) Write-Host "   [FAIL] $m" -ForegroundColor Red }
function Write-Info { param($m) Write-Host "   [INFO] $m" -ForegroundColor Gray }

$allPassed = $true

# TEST 1: Service exists and is RUNNING
Write-Step "Test 1 - Service status"
if (-not (Get-Command nssm -ErrorAction SilentlyContinue)) {
    Write-Fail "NSSM not found in PATH."
    $allPassed = $false
} else {
    $status = nssm status $ServiceName 2>&1
    if ($status -match "RUNNING") {
        Write-OK "Service $ServiceName is RUNNING."
    } else {
        Write-Fail "Service status: $status"
        Write-Info "Try: nssm start $ServiceName"
        $allPassed = $false
    }
}

# TEST 2: UDP port 9000 is listening
Write-Step "Test 2 - UDP port 9000 is open"
$udpListener = Get-NetUDPEndpoint -LocalPort 9000 -ErrorAction SilentlyContinue
if ($udpListener) {
    $pid2 = $udpListener.OwningProcess
    $proc = Get-Process -Id $pid2 -ErrorAction SilentlyContinue
    Write-OK "Port 9000/udp is open (PID $pid2 - $($proc.ProcessName))"
} else {
    Write-Fail "Nothing is listening on UDP 9000."
    Write-Info "The gateway process may have crashed. Check logs\service_stderr.log"
    $allPassed = $false
}

# TEST 3: gateway.log was written recently
Write-Step "Test 3 - Log file is being written"
$logPath = "$Root\logs\gateway.log"
if (Test-Path $logPath) {
    $lastWrite = (Get-Item $logPath).LastWriteTime
    $ageSeconds = [int]((Get-Date) - $lastWrite).TotalSeconds
    if ($ageSeconds -lt 60) {
        Write-OK "gateway.log updated ${ageSeconds}s ago."
    } else {
        Write-Fail "gateway.log last updated ${ageSeconds}s ago (expected under 60s)."
        $allPassed = $false
    }
} else {
    Write-Fail "gateway.log not found at $logPath"
    $allPassed = $false
}

# TEST 4: Send 3 OSC messages and check received count increases
Write-Step "Test 4 - End-to-end message processing"

function Get-ReceivedCount {
    $log = Get-Content "$Root\logs\gateway.log" -Tail 50 -ErrorAction SilentlyContinue
    $last = $log | Where-Object { $_ -match "received=(\d+)" } | Select-Object -Last 1
    if ($last -match "received=(\d+)") { return [int]$Matches[1] }
    return -1
}

$countBefore = Get-ReceivedCount
Write-Info "Packets received before test: $countBefore"

python "$Root\tests\osc_client_sim.py" --count 3 --delay 0.1 --address /fader/1 --value 0.5 2>&1 | Out-Null

$deadline = (Get-Date).AddSeconds(15)
$countAfter = $countBefore
while ((Get-Date) -lt $deadline) {
    Start-Sleep -Seconds 2
    $countAfter = Get-ReceivedCount
    if ($countAfter -gt $countBefore) { break }
}

if ($countAfter -gt $countBefore) {
    $diff = $countAfter - $countBefore
    Write-OK "Received count went from $countBefore to $countAfter (+$diff packets)."
} else {
    Write-Fail "Received count did not increase after sending 3 packets (still $countAfter)."
    Write-Info "Check if HMAC secret in settings.yaml matches the one the simulator uses."
    $allPassed = $false
}

# TEST 5: Telemetry CSV exists and has data
Write-Step "Test 5 - Telemetry CSV has data"
$csvFiles = Get-ChildItem "$Root\logs\latency_*.csv" -ErrorAction SilentlyContinue |
    Where-Object { $_.Name -notmatch "demo" } |
    Sort-Object LastWriteTime -Descending
if ($csvFiles) {
    $latest = $csvFiles[0]
    $rows = (Import-Csv $latest.FullName).Count
    if ($rows -gt 0) {
        Write-OK "$($latest.Name) has $rows data row(s)."
    } else {
        Write-Fail "$($latest.Name) exists but has no data rows yet."
        $allPassed = $false
    }
} else {
    Write-Fail "No latency CSV files found. Messages may not be reaching the MIDI writer."
    $allPassed = $false
}

# Summary
Write-Host ""
Write-Host "============================================================" -ForegroundColor Cyan
if ($allPassed) {
    Write-Host "  ALL TESTS PASSED - Gateway is operational." -ForegroundColor Green
} else {
    Write-Host "  SOME TESTS FAILED - See details above." -ForegroundColor Red
    Write-Host ""
    Write-Host "  Useful commands:"
    Write-Host "    nssm status $ServiceName"
    Write-Host "    nssm restart $ServiceName"
    Write-Host "    Get-Content logs\service_stderr.log -Tail 30"
    Write-Host "    Get-Content logs\gateway.log -Tail 50"
}
Write-Host "============================================================" -ForegroundColor Cyan
Write-Host ""
Write-Host "Press Enter to close..."
Read-Host
