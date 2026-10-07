# Shared helpers for the Lumina Gallery Server Windows scripts.
# Dot-sourced by setup.ps1, status.ps1, start.ps1, stop.ps1, uninstall.ps1 and
# add_folder.ps1. Works on Windows PowerShell 5.1 and PowerShell 7.
# Keep this file ASCII-only (PowerShell 5.1 reads BOM-less files as ANSI).

$ErrorActionPreference = 'Stop'
try { $OutputEncoding = New-Object System.Text.UTF8Encoding $false } catch { }
try { [Console]::OutputEncoding = New-Object System.Text.UTF8Encoding $false } catch { }
$env:PYTHONUTF8 = '1'
$env:PYTHONIOENCODING = 'utf-8'
$env:PIP_DISABLE_PIP_VERSION_CHECK = '1'

$LuminaBase   = Join-Path $env:LOCALAPPDATA 'LuminaServer'
$AppDir       = Join-Path $LuminaBase 'app'
$VenvDir      = Join-Path $LuminaBase 'venv'
$ConfigDir    = Join-Path $LuminaBase 'config'
$ConfigFile   = Join-Path $ConfigDir 'config.json'
$CertDir      = Join-Path $ConfigDir 'certs'
$LogDir       = Join-Path $LuminaBase 'logs'
$ThumbDir     = Join-Path (Join-Path $LuminaBase 'cache') 'thumbnails'
$LogFile      = Join-Path $LogDir 'server.log'
$VenvPython   = Join-Path (Join-Path $VenvDir 'Scripts') 'python.exe'
$VenvPythonW  = Join-Path (Join-Path $VenvDir 'Scripts') 'pythonw.exe'
$RunPy        = Join-Path $AppDir 'run.py'
$AdminPy      = Join-Path $AppDir 'lumina_admin.py'
$TaskName     = 'Lumina Gallery Server'
$FirewallName = 'Lumina Gallery Server'
$StartMenuDir = Join-Path ([Environment]::GetFolderPath('Programs')) 'Lumina Server'

function Write-Step([string]$Text) {
    Write-Host ''
    Write-Host ('== ' + $Text) -ForegroundColor Cyan
}

function Write-Ok([string]$Text)   { Write-Host ('  OK   ' + $Text) -ForegroundColor Green }
function Write-Warn([string]$Text) { Write-Host ('  NOTE ' + $Text) -ForegroundColor Yellow }
function Write-Bad([string]$Text)  { Write-Host ('  ERROR ' + $Text) -ForegroundColor Red }

function Read-YesNo([string]$Question, [bool]$Default = $true) {
    if ($Default) { $suffix = '[Y/n]' } else { $suffix = '[y/N]' }
    while ($true) {
        $a = Read-Host ('  ' + $Question + ' ' + $suffix)
        if ([string]::IsNullOrWhiteSpace($a)) { return $Default }
        $a = $a.Trim().ToLowerInvariant()
        if ($a -eq 'y' -or $a -eq 'yes') { return $true }
        if ($a -eq 'n' -or $a -eq 'no') { return $false }
        Write-Host '  Please answer y or n.'
    }
}

function Wait-ForEnter([string]$Text = 'Press Enter to close this window') {
    try { [void](Read-Host ('  ' + $Text)) } catch { }
}

# Run a native program safely: never throws because the program wrote to
# stderr (the Windows PowerShell 5.1 + ErrorActionPreference=Stop pitfall),
# and always reports the real exit code. Values are passed on STDIN only.
function Invoke-Native {
    param(
        [Parameter(Mandatory = $true)][string]$Exe,
        [string[]]$Arguments = @(),
        [string]$InputText = $null
    )
    $oldEap = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    $PSNativeCommandUseErrorActionPreference = $false
    $out = @()
    $code = -1
    try {
        $global:LASTEXITCODE = 0
        if ($null -ne $InputText -and $InputText.Length -gt 0) {
            $out = $InputText | & $Exe @Arguments 2>&1
        } else {
            $out = & $Exe @Arguments 2>&1
        }
        $code = $LASTEXITCODE
        if ($null -eq $code) { $code = 0 }
    } catch {
        $out = @($_.ToString())
        $code = -1
    } finally {
        $ErrorActionPreference = $oldEap
    }
    $lines = @()
    foreach ($o in @($out)) { $lines += [string]$o }
    return [pscustomobject]@{ ExitCode = [int]$code; Output = $lines }
}

# Call lumina_admin.py: payload as base64(UTF-8 JSON) on stdin, one
# "@@JSON {...}" line back. Returns the parsed object.
function Invoke-Admin {
    param(
        [Parameter(Mandatory = $true)][string]$Command,
        [hashtable]$Payload = @{},
        [string]$Python = $VenvPython,
        [string]$Script = $AdminPy,
        [string]$Config = $ConfigFile
    )
    $json = ConvertTo-Json -InputObject $Payload -Depth 6 -Compress
    $b64 = [Convert]::ToBase64String([System.Text.Encoding]::UTF8.GetBytes($json))
    $hadConfig = Test-Path Env:PICTUREVIEWER_CONFIG
    $oldConfig = $env:PICTUREVIEWER_CONFIG
    if ($Config) { $env:PICTUREVIEWER_CONFIG = $Config } else { Remove-Item Env:PICTUREVIEWER_CONFIG -ErrorAction SilentlyContinue }
    try {
        $r = Invoke-Native -Exe $Python -Arguments @($Script, $Command) -InputText $b64
    } finally {
        if ($hadConfig) { $env:PICTUREVIEWER_CONFIG = $oldConfig } else { Remove-Item Env:PICTUREVIEWER_CONFIG -ErrorAction SilentlyContinue }
    }
    $line = $r.Output | Where-Object { $_ -like '@@JSON *' } | Select-Object -Last 1
    if (-not $line) {
        throw ("Helper command '" + $Command + "' failed (exit " + $r.ExitCode + "):" + [Environment]::NewLine + ($r.Output -join [Environment]::NewLine))
    }
    return ($line.Substring(7) | ConvertFrom-Json)
}

function ConvertFrom-SecureToPlain([System.Security.SecureString]$Secure) {
    $bstr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($Secure)
    try { return [Runtime.InteropServices.Marshal]::PtrToStringBSTR($bstr) }
    finally { [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($bstr) }
}

function Get-Setting($Object, [string]$Name, $Default) {
    if ($null -ne $Object -and ($Object.PSObject.Properties.Name -contains $Name) -and $null -ne $Object.$Name -and "$($Object.$Name)" -ne '') {
        return $Object.$Name
    }
    return $Default
}

# Processes belonging to this install (the venv launcher AND the base
# interpreter it spawns both carry run.py on their command line).
function Get-LuminaProcesses {
    $result = @()
    try {
        $procs = Get-CimInstance Win32_Process -Filter "Name LIKE 'python%'" -ErrorAction Stop
    } catch {
        return $result
    }
    foreach ($p in $procs) {
        $cl = [string]$p.CommandLine
        if ($cl -and $cl.IndexOf($RunPy, [StringComparison]::OrdinalIgnoreCase) -ge 0) { $result += $p }
    }
    return $result
}

function Stop-LuminaServer {
    $task = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    if ($task) {
        try { Stop-ScheduledTask -TaskName $TaskName -ErrorAction Stop } catch { }
    }
    $deadline = (Get-Date).AddSeconds(10)
    while ((Get-Date) -lt $deadline) {
        $procs = @(Get-LuminaProcesses)
        if ($procs.Count -eq 0) { return $true }
        foreach ($p in $procs) {
            try { Stop-Process -Id $p.ProcessId -Force -ErrorAction Stop } catch { }
        }
        Start-Sleep -Milliseconds 500
    }
    return (@(Get-LuminaProcesses).Count -eq 0)
}

function Get-ConfiguredPorts {
    $http = 8500; $https = 8543
    if (Test-Path -LiteralPath $ConfigFile) {
        try {
            $raw = [System.IO.File]::ReadAllText($ConfigFile, [System.Text.Encoding]::UTF8)
            $c = $raw | ConvertFrom-Json
            $http = [int](Get-Setting $c 'port' 8500)
            $https = [int](Get-Setting $c 'https_port' 8543)
        } catch { }
    }
    return @($http, $https)
}

function Test-ServerUp([int]$Port, [int]$TimeoutSec = 2) {
    try {
        $r = Invoke-WebRequest -Uri ('http://127.0.0.1:' + $Port + '/api/status') -UseBasicParsing -TimeoutSec $TimeoutSec
        return ($r.StatusCode -eq 200)
    } catch {
        return $false
    }
}

function Wait-ServerUp([int]$Port, [int]$Seconds = 30) {
    $deadline = (Get-Date).AddSeconds($Seconds)
    while ((Get-Date) -lt $deadline) {
        if (Test-ServerUp $Port) { return $true }
        Start-Sleep -Seconds 1
    }
    return $false
}

# IPv4 addresses of adapters that are up AND have a default gateway (i.e. the
# real Wi-Fi / Ethernet LAN, not Hyper-V/WSL/VPN virtual switches).
function Get-LanAddresses {
    $ips = @()
    try {
        $cfgs = Get-NetIPConfiguration -ErrorAction Stop | Where-Object {
            $_.IPv4DefaultGateway -ne $null -and $_.NetAdapter -ne $null -and $_.NetAdapter.Status -eq 'Up'
        }
        foreach ($c in $cfgs) {
            foreach ($a in @($c.IPv4Address)) {
                if ($a.IPAddress -and $a.IPAddress -notlike '169.254.*') { $ips += $a.IPAddress }
            }
        }
    } catch { }
    return $ips
}

function Show-LogTail([int]$Lines = 20) {
    if (Test-Path -LiteralPath $LogFile) {
        Write-Host ('  Last ' + $Lines + ' log lines (' + $LogFile + '):') -ForegroundColor Gray
        Get-Content -LiteralPath $LogFile -Tail $Lines -Encoding UTF8 | ForEach-Object { Write-Host ('    ' + $_) }
    } else {
        Write-Host ('  No log file yet at ' + $LogFile) -ForegroundColor Gray
    }
}

function Format-Fingerprint([string]$Fp) {
    if (-not $Fp) { return '(no certificate yet - it is created on first start)' }
    $pairs = @()
    for ($i = 0; $i -lt $Fp.Length; $i += 2) { $pairs += $Fp.Substring($i, [Math]::Min(2, $Fp.Length - $i)) }
    return (($pairs -join ':').ToUpperInvariant())
}
