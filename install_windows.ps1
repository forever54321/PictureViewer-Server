# ============================================================
# PictureViewer Server - Windows PowerShell Installer
# ============================================================
# Run: Right-click → "Run with PowerShell"
# Or:  powershell -ExecutionPolicy Bypass -File install_windows.ps1
# ============================================================

$ErrorActionPreference = "Stop"

# Reliably get the folder this script lives in
if ($PSScriptRoot) {
    $ScriptDir = $PSScriptRoot
} elseif ($MyInvocation.MyCommand.Path) {
    $ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
} else {
    $ScriptDir = (Get-Location).Path
}

# Ensure we're working from the script's directory
Set-Location $ScriptDir
$VenvDir = Join-Path $ScriptDir "venv"

Write-Host "  Script directory: $ScriptDir" -ForegroundColor Gray

Write-Host ""
Write-Host "============================================================" -ForegroundColor Cyan
Write-Host "  PictureViewer Server - Windows Installer" -ForegroundColor Cyan
Write-Host "============================================================" -ForegroundColor Cyan
Write-Host ""

# --- Check Python ---
$pythonCmd = $null
foreach ($cmd in @("python3", "python")) {
    try {
        $version = & $cmd --version 2>&1
        if ($version -match "Python 3") {
            $pythonCmd = $cmd
            Write-Host "  Found: $version" -ForegroundColor Green
            break
        }
    } catch {}
}

if (-not $pythonCmd) {
    Write-Host "  ERROR: Python 3 is required but not found." -ForegroundColor Red
    Write-Host "  Download from https://www.python.org/downloads/" -ForegroundColor Yellow
    Write-Host "  Make sure to check 'Add Python to PATH' during installation." -ForegroundColor Yellow
    Write-Host ""
    Read-Host "Press Enter to exit"
    exit 1
}

# --- Create virtual environment ---
Write-Host ""
Write-Host "  Creating virtual environment..." -ForegroundColor Yellow
& $pythonCmd -m venv $VenvDir

# --- Verify venv and install dependencies ---
$venvPython = Join-Path $VenvDir "Scripts\python.exe"
$venvPip = Join-Path $VenvDir "Scripts\pip.exe"

if (-not (Test-Path $venvPython)) {
    Write-Host "  ERROR: Virtual environment creation failed." -ForegroundColor Red
    Read-Host "Press Enter to exit"
    exit 1
}

Write-Host "  Installing dependencies..." -ForegroundColor Yellow
$requirementsFile = Join-Path $ScriptDir "requirements.txt"

# Create requirements.txt if missing (e.g. only the script was copied)
if (-not (Test-Path $requirementsFile)) {
    Write-Host "  requirements.txt not found - creating it..." -ForegroundColor Yellow
    @"
flask==3.1.0
flask-cors==5.0.1
Pillow==11.1.0
python-dotenv==1.0.1
werkzeug==3.1.3
PyJWT==2.10.1
"@ | Set-Content -Path $requirementsFile -Encoding UTF8
}

# Also ensure server.py and config.py exist
$serverPyPath = Join-Path $ScriptDir "server.py"
$configPyPath = Join-Path $ScriptDir "config.py"
if (-not (Test-Path $serverPyPath) -or -not (Test-Path $configPyPath)) {
    Write-Host ""
    Write-Host "  WARNING: server.py and/or config.py not found in $ScriptDir" -ForegroundColor Red
    Write-Host "  Make sure ALL Server files are copied to the same folder:" -ForegroundColor Yellow
    Write-Host "    - install_windows.ps1" -ForegroundColor Yellow
    Write-Host "    - server.py" -ForegroundColor Yellow
    Write-Host "    - config.py" -ForegroundColor Yellow
    Write-Host "    - requirements.txt" -ForegroundColor Yellow
    Write-Host ""
    Read-Host "Press Enter to exit"
    exit 1
}

Write-Host "  Requirements file: $requirementsFile" -ForegroundColor Gray

# Use venv python -m pip (most reliable on Windows)
& $venvPython -m pip install --upgrade pip -q 2>$null
& $venvPython -m pip install -r "$requirementsFile"

if ($LASTEXITCODE -ne 0) {
    Write-Host "  ERROR: Failed to install dependencies." -ForegroundColor Red
    Read-Host "Press Enter to exit"
    exit 1
}
Write-Host "  Dependencies installed successfully." -ForegroundColor Green

# --- Configuration ---
Write-Host ""
Write-Host "  --- Configuration ---" -ForegroundColor Cyan
Write-Host ""

$defaultFolder = Join-Path $env:USERPROFILE "Pictures"
$mediaFolder = Read-Host "  Media folder path [$defaultFolder]"
if ([string]::IsNullOrWhiteSpace($mediaFolder)) { $mediaFolder = $defaultFolder }

# Expand path and validate
$mediaFolder = [System.IO.Path]::GetFullPath($mediaFolder)
if (-not (Test-Path $mediaFolder)) {
    Write-Host "  Folder does not exist. Creating it..." -ForegroundColor Yellow
    New-Item -ItemType Directory -Path $mediaFolder -Force | Out-Null
}

$port = Read-Host "  Server port [8500]"
if ([string]::IsNullOrWhiteSpace($port)) { $port = "8500" }

$accessCode = Read-Host "  Access code [picture123]"
if ([string]::IsNullOrWhiteSpace($accessCode)) { $accessCode = "picture123" }

# Generate secret key
$secretKey = & $venvPython -c "import secrets; print(secrets.token_hex(32))"

# --- Save .env file ---
$envFile = Join-Path $ScriptDir ".env"
@"
PICTUREVIEWER_MEDIA_FOLDER=$mediaFolder
PICTUREVIEWER_ACCESS_CODE=$accessCode
PICTUREVIEWER_SECRET_KEY=$secretKey
"@ | Set-Content -Path $envFile -Encoding UTF8

# --- Update port in config if changed ---
if ($port -ne "8500") {
    $configFile = Join-Path $ScriptDir "config.py"
    (Get-Content $configFile) -replace 'PORT = 8500', "PORT = $port" | Set-Content $configFile
}

# --- Create start_server.ps1 ---
$startScript = Join-Path $ScriptDir "start_server.ps1"
@"
# PictureViewer Server - Start Script
`$ScriptDir = Split-Path -Parent `$MyInvocation.MyCommand.Path
`$VenvPython = Join-Path `$ScriptDir "venv\Scripts\python.exe"

# Load environment variables
Get-Content (Join-Path `$ScriptDir ".env") | ForEach-Object {
    if (`$_ -match '^([^=]+)=(.*)$') {
        [System.Environment]::SetEnvironmentVariable(`$Matches[1], `$Matches[2], "Process")
    }
}

# Start server
& `$VenvPython (Join-Path `$ScriptDir "server.py")
"@ | Set-Content -Path $startScript -Encoding UTF8

# --- Create start_server.bat (convenience wrapper) ---
$startBat = Join-Path $ScriptDir "start_server.bat"
@"
@echo off
powershell -ExecutionPolicy Bypass -File "%~dp0start_server.ps1"
pause
"@ | Set-Content -Path $startBat -Encoding UTF8

# --- Auto-start option ---
Write-Host ""
$autoStart = Read-Host "  Start server automatically on login? [y/N]"
if ($autoStart -match '^[Yy]') {
    $pythonExe = Join-Path $VenvDir "Scripts\python.exe"
    $serverPy = Join-Path $ScriptDir "server.py"

    $action = New-ScheduledTaskAction -Execute $pythonExe -Argument "`"$serverPy`"" -WorkingDirectory $ScriptDir
    $trigger = New-ScheduledTaskTrigger -AtLogOn
    $settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable

    try {
        Register-ScheduledTask -TaskName "PictureViewerServer" -Action $action -Trigger $trigger -Settings $settings -Force | Out-Null
        Write-Host "  Server registered as startup task." -ForegroundColor Green
    } catch {
        Write-Host "  Could not register startup task (try running as Administrator)." -ForegroundColor Yellow
    }
}

# --- Add firewall rule (required for connection) ---
Write-Host ""
$addFirewall = Read-Host "  Add Windows Firewall rule for port $port? (RECOMMENDED) [Y/n]"
if ($addFirewall -notmatch '^[Nn]') {
    try {
        # Remove old rule if exists
        Remove-NetFirewallRule -DisplayName "PictureViewer Server" -ErrorAction SilentlyContinue
        # Add for both Private and Public profiles
        New-NetFirewallRule -DisplayName "PictureViewer Server" -Direction Inbound -Protocol TCP -LocalPort $port -Action Allow -Profile Private,Public -ErrorAction Stop | Out-Null
        Write-Host "  Firewall rule added successfully." -ForegroundColor Green
    } catch {
        Write-Host "  Could not add firewall rule automatically." -ForegroundColor Yellow
        Write-Host "  Please run this script as Administrator, or manually allow port $port in Windows Firewall." -ForegroundColor Yellow
    }
}

# --- Get ALL local IPs (Wi-Fi, Ethernet, etc.) ---
$localIPs = @()

# Method 1: Find the IP used for default route (most reliable)
try {
    $defaultIP = (Test-Connection -ComputerName 8.8.8.8 -Count 1 -ErrorAction Stop).IPV4Address.IPAddressToString
    if ($defaultIP) { $localIPs += $defaultIP }
} catch {
    try {
        # Fallback: use .NET socket
        $socket = New-Object System.Net.Sockets.UdpClient
        $socket.Connect("8.8.8.8", 53)
        $defaultIP = ($socket.Client.LocalEndPoint).Address.ToString()
        $socket.Close()
        if ($defaultIP -and $defaultIP -ne "0.0.0.0") { $localIPs += $defaultIP }
    } catch {}
}

# Method 2: Get all non-loopback IPv4 addresses
try {
    Get-NetIPAddress -AddressFamily IPv4 -ErrorAction Stop | Where-Object {
        $_.IPAddress -ne "127.0.0.1" -and
        $_.InterfaceAlias -notmatch "Loopback|vEthernet|Docker|WSL|Hyper-V|VirtualBox|VMware"
    } | ForEach-Object {
        if ($_.IPAddress -notin $localIPs) { $localIPs += $_.IPAddress }
    }
} catch {}

if ($localIPs.Count -eq 0) { $localIPs = @("Could not detect - run 'ipconfig' to find your IP") }

# --- Done ---
Write-Host ""
Write-Host "============================================================" -ForegroundColor Green
Write-Host "  Installation Complete!" -ForegroundColor Green
Write-Host "============================================================" -ForegroundColor Green
Write-Host ""
Write-Host "  Media Folder  : $mediaFolder" -ForegroundColor White
Write-Host ""
Write-Host "  Your server addresses (try any that works):" -ForegroundColor White
foreach ($ip in $localIPs) {
    Write-Host "    -> http://${ip}:${port}" -ForegroundColor Cyan
}
Write-Host ""
Write-Host "  Access Code   : $accessCode" -ForegroundColor White
Write-Host ""
Write-Host "  To start the server:" -ForegroundColor Yellow
Write-Host "    Double-click : start_server.bat" -ForegroundColor Yellow
Write-Host "    Or run       : .\start_server.ps1" -ForegroundColor Yellow
Write-Host ""
Write-Host "  IMPORTANT:" -ForegroundColor Yellow
Write-Host "   - Phone and PC must be on the SAME Wi-Fi network" -ForegroundColor Yellow
Write-Host "   - Allow Python through Windows Firewall when prompted" -ForegroundColor Yellow
Write-Host "   - Try each IP address listed above" -ForegroundColor Yellow
Write-Host "============================================================" -ForegroundColor Green
Write-Host ""

# --- Start now? ---
$startNow = Read-Host "  Start the server now? [Y/n]"
if ($startNow -notmatch '^[Nn]') {
    # Load env vars
    Get-Content $envFile | ForEach-Object {
        if ($_ -match '^([^=]+)=(.*)$') {
            [System.Environment]::SetEnvironmentVariable($Matches[1], $Matches[2], "Process")
        }
    }
    $serverPy = Join-Path $ScriptDir "server.py"
    & $venvPython $serverPy
}
