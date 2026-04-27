# =============================================================================
# RumoWave - One-shot installer for Windows 10/11
# Run from an Administrator PowerShell:
#   powershell -ExecutionPolicy Bypass -File install.ps1
# =============================================================================

param(
    [string]$ServiceName = "RumoWave",
    [switch]$Uninstall
)

$ErrorActionPreference = "Stop"
$Root = $PSScriptRoot

# ---- helpers ----------------------------------------------------------------
function Write-Step { param($msg) Write-Host "`n>> $msg" -ForegroundColor Cyan }
function Write-OK   { param($msg) Write-Host "   OK  $msg" -ForegroundColor Green }
function Write-Warn { param($msg) Write-Host "   !!  $msg" -ForegroundColor Yellow }
function Write-Fail { param($msg) Write-Host "   XX  $msg" -ForegroundColor Red; exit 1 }

# ---- uninstall path ---------------------------------------------------------
if ($Uninstall) {
    Write-Step "Removing service $ServiceName"
    if (Get-Command nssm -ErrorAction SilentlyContinue) {
        nssm stop    $ServiceName 2>$null
        nssm remove  $ServiceName confirm
        Write-OK "Service removed."
    } else {
        Write-Warn "NSSM not found - nothing to remove."
    }
    exit 0
}

# =============================================================================
# STEP 1 — Python version check
# =============================================================================
Write-Step "Checking Python"
try {
    $pyver = python --version 2>&1
    Write-OK $pyver
} catch {
    Write-Fail "Python not found. Install Python 3.11+ from https://python.org"
}

# =============================================================================
# STEP 2 — Install Python dependencies
# =============================================================================
Write-Step "Installing Python dependencies"
python -m pip install --upgrade pip --quiet
python -m pip install -r "$Root\requirements.txt" --quiet
Write-OK "Dependencies installed."

# =============================================================================
# STEP 3 — Auto-generate HMAC secret if still default
# =============================================================================
Write-Step "Checking HMAC secret"
$yaml = Get-Content "$Root\config\settings.yaml" -Raw
if ($yaml -match "CHANGE_ME") {
    $secret = python -c "import secrets; print(secrets.token_hex(32))"
    $yaml = $yaml -replace "CHANGE_ME[^\`"]*", $secret
    Set-Content "$Root\config\settings.yaml" $yaml -Encoding UTF8
    Write-OK "Generated new HMAC secret: $($secret.Substring(0,16))..."
} else {
    Write-OK "HMAC secret already configured."
}

# =============================================================================
# STEP 4 — Detect MIDI ports and update settings.yaml
# =============================================================================
Write-Step "Detecting MIDI output ports"
$midiPorts = python -c @'
import winrt.windows.devices.midi as midi
import winrt.windows.devices.enumeration as enum
import asyncio, json

async def get_ports():
    devs = await type(enum.DeviceInformation).find_all_async_aqs_filter(
        enum.DeviceInformation, midi.MidiOutPort.get_device_selector()
    )
    return [d.name for d in devs]

print(json.dumps(asyncio.run(get_ports())))
'@

if ($LASTEXITCODE -ne 0) {
    Write-Warn "Could not enumerate MIDI ports (WinRT error). Will use first available port."
    $ports = @()
} else {
    $ports = $midiPorts | ConvertFrom-Json
    Write-OK "Found ports: $($ports -join ', ')"
}

# Pick best port: prefer loopMIDI explicitly, then any non-GS port, then GS Wavetable
$gsKeywords   = @("gs", "wavetable", "sintetizador", "microsoft", "sw synth")
$loopMidi     = $ports | Where-Object { $_ -match "loopmidi" } | Select-Object -First 1
$nonGs        = $ports | Where-Object { $_ -notmatch ($gsKeywords -join "|") } | Select-Object -First 1
$preferred    = if ($loopMidi) { $loopMidi } else { $nonGs }
$fallback     = $ports | Select-Object -First 1

if ($preferred) {
    $chosenPort = $preferred
    Write-OK "Will use: $chosenPort"
} elseif ($fallback) {
    $chosenPort = $fallback
    Write-Warn "No virtual/hardware port found. Using built-in: $chosenPort"
    Write-Warn "For better results install loopMIDI: https://www.tobias-erichsen.de/software/loopmidi.html"
} else {
    $chosenPort = ""
    Write-Warn "No MIDI ports detected. Gateway will pick the first available at runtime."
}

# Patch device_name in settings.yaml
if ($chosenPort) {
    $yaml = Get-Content "$Root\config\settings.yaml" -Raw
    $yaml = $yaml -replace '(device_name:\s*")[^"]*(")', "`${1}$chosenPort`${2}"
    Set-Content "$Root\config\settings.yaml" $yaml -Encoding UTF8
    Write-OK "settings.yaml updated with device_name: $chosenPort"
}

# =============================================================================
# STEP 5 — NSSM service registration (optional, skip if not admin or no NSSM)
# =============================================================================
Write-Step "Setting up Windows service (NSSM)"

$isAdmin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole(
    [Security.Principal.WindowsBuiltInRole]::Administrator
)

if (-not $isAdmin) {
    Write-Warn "Not running as Administrator - skipping service registration."
    Write-Warn "Re-run as Administrator to install the Windows service."
} elseif (-not (Get-Command nssm -ErrorAction SilentlyContinue)) {
    Write-Warn "NSSM not found in PATH - skipping service registration."
    Write-Warn "Install NSSM: winget install NSSM.NSSM  (then re-run this script)"
} else {
    $python  = (Get-Command python).Source
    $logDir  = "$Root\logs"
    New-Item -ItemType Directory -Force -Path $logDir | Out-Null

    # Remove existing service if present
    try {
        $existing = nssm status $ServiceName 2>$null
    } catch {
        $existing = $null
    }
    if ($existing -and $existing -notmatch "Can't open") {
        try { nssm stop   $ServiceName 2>$null } catch {}
        try { nssm remove $ServiceName confirm 2>$null } catch {}
    }

    nssm install     $ServiceName $python "-m src.main"
    nssm set         $ServiceName AppDirectory  $Root
    nssm set         $ServiceName DisplayName   "RumoWave"
    nssm set         $ServiceName Description   "RumoWave - Phone-to-MIDI wireless gateway (Python + WinRT)"
    nssm set         $ServiceName Start         SERVICE_AUTO_START
    nssm set         $ServiceName AppStdout     "$logDir\service_stdout.log"
    nssm set         $ServiceName AppStderr     "$logDir\service_stderr.log"
    nssm set         $ServiceName AppRotateFiles 1
    nssm set         $ServiceName AppRotateBytes 10485760
    nssm set         $ServiceName AppRestartDelay 3000

    nssm start $ServiceName
    Start-Sleep -Seconds 2
    $status = nssm status $ServiceName
    if ($status -match "RUNNING") {
        Write-OK "Service is RUNNING."
    } else {
        Write-Warn "Service status: $status  (check logs\service_stderr.log)"
    }
}

# =============================================================================
# STEP 6 — Print summary
# =============================================================================
Write-Host ""
Write-Host "============================================================" -ForegroundColor Cyan
Write-Host "  Installation complete" -ForegroundColor Cyan
Write-Host "============================================================" -ForegroundColor Cyan
Write-Host ""
Write-Host "  To test without the service (manual mode):"
Write-Host "    python -m src.main"
Write-Host ""
Write-Host "  To send a test message:"
Write-Host "    python tests/osc_client_sim.py --count 5 --delay 0.2"
Write-Host ""
Write-Host "  To generate a latency chart:"
Write-Host "    python src/analyzer.py --show"
Write-Host ""
Write-Host "  To check service status:"
Write-Host "    nssm status $ServiceName"
Write-Host ""
Write-Host "  MIDI port in use: $chosenPort" -ForegroundColor Yellow
Write-Host "  UDP port        : 9000" -ForegroundColor Yellow
Write-Host ""
