# Lumina Gallery Server - Windows installer / updater.
#
# Start it by double-clicking install_windows.bat (which runs this with a
# process-scoped execution-policy bypass). Re-running it updates the server
# and keeps your settings.
#
# What it does (no admin rights needed except for the optional firewall step):
#   1. copies the server to %LOCALAPPDATA%\LuminaServer\app
#   2. finds a supported Python (3.10 - 3.14, 64-bit)
#   3. builds a private Python environment in %LOCALAPPDATA%\LuminaServer\venv
#   4. keeps / migrates existing settings (config.json, or an old .env)
#   5-7. asks for your photo folder, access code and ports
#   8. (optional, asks for admin) adds a firewall rule for your home network
#   9. registers a per-user auto-start task
#  10. adds Start Menu shortcuts (Start / Stop / Status / Add Folder / Uninstall)
#  11. starts the server and checks it answers
#
# Keep this file ASCII-only; it must run on Windows PowerShell 5.1 and 7.

[CmdletBinding()]
param()

$ScriptDir = $PSScriptRoot
if (-not $ScriptDir) { $ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path }
$SourceDir = Split-Path -Parent $ScriptDir
. (Join-Path $ScriptDir 'common.ps1')

$script:GeneratedCode = $null
$MinPy = [version]'3.10'
$MaxPy = [version]'3.14'

# ---------------------------------------------------------------------------
function Install-AppFiles {
    Write-Step 'Step 1/11: Copying the server program'
    $srcFull = [System.IO.Path]::GetFullPath($SourceDir).TrimEnd('\')
    $dstFull = [System.IO.Path]::GetFullPath($AppDir).TrimEnd('\')
    if ($srcFull -ieq $dstFull) {
        Write-Ok 'Running from the installed copy - files already in place.'
        return
    }
    $files = @('server.py', 'config.py', 'safety.py', 'lumina_admin.py', 'add_folder.py',
               'run.py', 'requirements.txt', 'requirements-heic.txt', 'README.md', 'VERSION')
    foreach ($f in $files) {
        if (-not (Test-Path -LiteralPath (Join-Path $SourceDir $f))) {
            throw ("'" + $f + "' is missing next to this installer. Download the whole ZIP from GitHub, extract ALL files, and run install_windows.bat again.")
        }
    }
    foreach ($d in @($LuminaBase, $LogDir, $ConfigDir)) {
        New-Item -ItemType Directory -Force -Path $d | Out-Null
    }
    $staging = Join-Path $LuminaBase 'app.new'
    if (Test-Path -LiteralPath $staging) { Remove-Item -LiteralPath $staging -Recurse -Force }
    New-Item -ItemType Directory -Force -Path (Join-Path $staging 'windows') | Out-Null
    foreach ($f in $files) {
        Copy-Item -LiteralPath (Join-Path $SourceDir $f) -Destination (Join-Path $staging $f) -Force
    }
    Get-ChildItem -LiteralPath $ScriptDir -Filter '*.ps1' | ForEach-Object {
        Copy-Item -LiteralPath $_.FullName -Destination (Join-Path (Join-Path $staging 'windows') $_.Name) -Force
    }
    if (Test-Path -LiteralPath $AppDir) { Remove-Item -LiteralPath $AppDir -Recurse -Force }
    Rename-Item -LiteralPath $staging -NewName 'app'
    # Drop the "downloaded from the internet" mark so the shortcuts run cleanly.
    Get-ChildItem -LiteralPath $AppDir -Recurse -File | ForEach-Object {
        try { Unblock-File -LiteralPath $_.FullName -ErrorAction Stop } catch { }
    }
    Write-Ok ('Installed to ' + $AppDir)
}

# ---------------------------------------------------------------------------
function Get-PythonCandidates {
    $c = @()
    $pyPaths = @()
    $cmd = Get-Command py.exe -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($cmd) { $pyPaths += $cmd.Source }
    $userPy = Join-Path $env:LOCALAPPDATA 'Programs\Python\Launcher\py.exe'
    if ((Test-Path -LiteralPath $userPy) -and ($pyPaths -notcontains $userPy)) { $pyPaths += $userPy }
    foreach ($py in $pyPaths) {
        foreach ($v in @('-3.13', '-3.12', '-3.14', '-3.11', '-3.10', '-3')) {
            $c += , @($py, $v)
        }
    }
    $direct = @()
    foreach ($g in @(Get-Command python.exe -CommandType Application -All -ErrorAction SilentlyContinue)) { $direct += $g.Source }
    foreach ($ver in @('313', '312', '314', '311', '310')) {
        $direct += (Join-Path $env:LOCALAPPDATA ('Programs\Python\Python' + $ver + '\python.exe'))
    }
    foreach ($p in $direct) {
        if (-not $p) { continue }
        if ($p -match '\\WindowsApps\\') { continue }   # Microsoft Store alias stub
        if (Test-Path -LiteralPath $p) { $c += , @($p, $null) }
    }
    return , $c   # leading comma: keep the list of pairs intact even with one entry
}

function Find-Python {
    Write-Step 'Step 2/11: Finding Python'
    $tried = @{}
    foreach ($cand in (Get-PythonCandidates)) {
        $exe = $cand[0]; $flag = $cand[1]
        $key = ($exe + '|' + $flag)
        if ($tried.ContainsKey($key)) { continue }
        $tried[$key] = $true
        $pre = @()
        if ($flag) { $pre += $flag }
        $r = Invoke-Native -Exe $exe -Arguments ($pre + @($AdminPy, 'env-info'))
        $line = $r.Output | Where-Object { $_ -like '@@JSON *' } | Select-Object -Last 1
        if (-not $line) { continue }
        $info = $line.Substring(7) | ConvertFrom-Json
        $v = [version]$info.version
        $short = New-Object version $v.Major, $v.Minor
        if ($short -lt $MinPy -or $short -gt $MaxPy) {
            Write-Warn ('Skipping Python ' + $info.version + ' at ' + $info.base_executable + ' (need 3.10 - 3.14).')
            continue
        }
        if ($info.bits -ne 64) {
            Write-Warn ('Skipping 32-bit Python ' + $info.version + ' - please use 64-bit Python.')
            continue
        }
        if ($info.machine -eq 'ARM64') {
            Write-Warn 'This is an ARM64 PC. Packages for ARM64 are less common; installing anyway.'
        }
        Write-Ok ('Using Python ' + $info.version + ' (' + $info.machine + ') - ' + $info.base_executable)
        return [pscustomobject]@{ Exe = $exe; Pre = $pre; Info = $info }
    }
    return $null
}

function Install-PythonIfMissing {
    Write-Warn 'No supported Python (3.10 - 3.14, 64-bit) was found.'
    $winget = Get-Command winget.exe -ErrorAction SilentlyContinue
    if ($winget) {
        if (Read-YesNo 'Install Python 3.13 for your user account now with winget (no admin needed)?' $true) {
            $r = Invoke-Native -Exe $winget.Source -Arguments @('install', '-e', '--id', 'Python.Python.3.13',
                '--scope', 'user', '--accept-package-agreements', '--accept-source-agreements')
            $r.Output | Select-Object -Last 5 | ForEach-Object { Write-Host ('    ' + $_) }
            if ($r.ExitCode -ne 0) { Write-Warn ('winget exited with code ' + $r.ExitCode) }
            $found = Find-Python
            if ($found) { return $found }
        }
    }
    Write-Host ''
    Write-Host '  Please install Python yourself:' -ForegroundColor Yellow
    Write-Host '    1. On the page that opens, download "Python 3.13" (Windows installer, 64-bit).'
    Write-Host '    2. Run it and tick "Add python.exe to PATH".'
    Write-Host '    3. Double-click install_windows.bat again.'
    try { Start-Process 'https://www.python.org/downloads/windows/' } catch { }
    throw 'Python is required.'
}

# ---------------------------------------------------------------------------
function Initialize-Venv($Py) {
    Write-Step 'Step 3/11: Preparing the Python environment'
    $healthy = $false
    if (Test-Path -LiteralPath $VenvPython) {
        $r = Invoke-Native -Exe $VenvPython -Arguments @($AdminPy, 'env-info')
        $line = $r.Output | Where-Object { $_ -like '@@JSON *' } | Select-Object -Last 1
        if ($line) {
            $vi = $line.Substring(7) | ConvertFrom-Json
            $vv = [version]$vi.version
            $short = New-Object version $vv.Major, $vv.Minor
            if ($short -ge $MinPy -and $short -le $MaxPy -and $vi.bits -eq 64) { $healthy = $true }
        }
    }
    if ($healthy) {
        Write-Ok 'Reusing the existing environment.'
    } else {
        if (Test-Path -LiteralPath $VenvDir) {
            Write-Warn 'Existing environment is broken or outdated - recreating it.'
            Remove-Item -LiteralPath $VenvDir -Recurse -Force
        }
        $r = Invoke-Native -Exe $Py.Exe -Arguments ($Py.Pre + @('-m', 'venv', $VenvDir))
        if ($r.ExitCode -ne 0 -or -not (Test-Path -LiteralPath $VenvPython)) {
            $r.Output | Select-Object -Last 15 | ForEach-Object { Write-Host ('    ' + $_) }
            throw 'Could not create the Python environment (python -m venv failed).'
        }
        Write-Ok ('Created ' + $VenvDir)
    }

    Write-Host '  Installing packages (this can take a minute)...'
    $null = Invoke-Native -Exe $VenvPython -Arguments @('-m', 'pip', 'install', '--upgrade', '--only-binary=:all:', 'pip')
    $req = Join-Path $AppDir 'requirements.txt'
    $r = Invoke-Native -Exe $VenvPython -Arguments @('-m', 'pip', 'install', '--only-binary=:all:', '-r', $req)
    if ($r.ExitCode -ne 0) {
        $r.Output | Select-Object -Last 25 | ForEach-Object { Write-Host ('    ' + $_) }
        throw ('Installing the required packages failed (pip exit code ' + $r.ExitCode + '). Check your internet connection and run the installer again.')
    }
    Write-Ok 'Required packages installed.'
    $heic = Join-Path $AppDir 'requirements-heic.txt'
    $r = Invoke-Native -Exe $VenvPython -Arguments @('-m', 'pip', 'install', '--only-binary=:all:', '-r', $heic)
    if ($r.ExitCode -ne 0) {
        Write-Warn 'Optional HEIC support (pillow-heif) could not be installed - the server works without it.'
    }
    $t = Invoke-Admin -Command 'selftest'
    if (-not $t.ok) { throw ('Self-test failed: ' + $t.reason) }
    $extras = @()
    if ($t.heif) { $extras += 'HEIC thumbnails' }
    if ($t.ffmpeg) { $extras += 'video thumbnails (ffmpeg)' }
    if (-not $t.https) { Write-Warn 'HTTPS support is unavailable (cryptography missing) - the server will run HTTP-only.' }
    Write-Ok ('Self-test passed (Pillow ' + $t.pillow + $(if ($extras.Count) { '; ' + ($extras -join ', ') } else { '' }) + ').')
    if (-not $t.ffmpeg) {
        Write-Host '  Tip: install ffmpeg (winget install Gyan.FFmpeg) to get video thumbnails.' -ForegroundColor Gray
    }
}

# ---------------------------------------------------------------------------
function Show-CurrentSettings($cur) {
    Write-Host ('    Photo folder : ' + $cur.media_folder)
    Write-Host ('    Ports        : HTTP ' + $cur.port + ', HTTPS ' + $cur.https_port)
    Write-Host ('    Organize existing files: ' + $cur.organize_existing)
}

function Invoke-Migration {
    # Old installs kept their settings in a .env file next to server.py.
    $candidates = @()
    $own = Join-Path $SourceDir '.env'
    if (Test-Path -LiteralPath $own) { $candidates += $SourceDir }
    if ($candidates.Count -eq 0) {
        Write-Host ''
        Write-Host '  If you used an older version of this server, its settings are in a file' -ForegroundColor Gray
        Write-Host '  called ".env" in the old server folder. Point me to that folder to keep' -ForegroundColor Gray
        Write-Host '  your access code and certificate (so phones stay connected).' -ForegroundColor Gray
        $p = Read-Host '  Old server folder (or press Enter to skip)'
        if (-not [string]::IsNullOrWhiteSpace($p)) { $candidates += $p.Trim().Trim('"') }
    }
    foreach ($dir in $candidates) {
        $r = Invoke-Admin -Command 'migrate-env' -Payload @{ env_path = $dir; cert_dir = $CertDir }
        if ($r.ok) {
            Write-Ok ('Imported settings from ' + $r.old_dir + ' (' + (@($r.migrated) -join ', ') + ').')
            Write-Host '  The old folder was not changed. Once everything works you can delete it' -ForegroundColor Gray
            Write-Host '  (after removing its old "PictureViewerServer" startup task, see below).' -ForegroundColor Gray
            return $true
        } else {
            Write-Warn ('Could not import from ' + $dir + ': ' + $r.reason)
        }
    }
    return $false
}

function Select-Folder([string]$Default) {
    $chosen = $null
    $sta = [System.Threading.Thread]::CurrentThread.GetApartmentState() -eq 'STA'
    if ($sta) {
        try {
            Add-Type -AssemblyName System.Windows.Forms
            $dlg = New-Object System.Windows.Forms.FolderBrowserDialog
            $dlg.Description = 'Choose the folder with your photos and videos (phones back up here)'
            $dlg.ShowNewFolderButton = $true
            if ($Default -and (Test-Path -LiteralPath $Default)) { $dlg.SelectedPath = $Default }
            $owner = New-Object System.Windows.Forms.Form
            $owner.TopMost = $true
            Write-Host '  A folder picker window opened (it may be behind this window).'
            if ($dlg.ShowDialog($owner) -eq [System.Windows.Forms.DialogResult]::OK) { $chosen = $dlg.SelectedPath }
            $owner.Dispose()
        } catch {
            $chosen = $null
        }
    }
    if (-not $chosen) {
        $p = Read-Host ('  Photo folder path [' + $Default + ']')
        if ([string]::IsNullOrWhiteSpace($p)) { $chosen = $Default } else { $chosen = $p.Trim().Trim('"') }
    }
    return $chosen
}

function Read-MediaFolder([string]$Default) {
    Write-Step 'Step 5/11: Photo folder'
    Write-Host '  Choose a folder that holds ONLY your photos/videos (for example'
    Write-Host '  Pictures, or D:\Photos). Not a whole drive, not your user folder.'
    while ($true) {
        $folder = Select-Folder $Default
        $v = Invoke-Admin -Command 'validate-folder' -Payload @{ path = $folder }
        if (-not $v.ok) {
            Write-Bad ('That folder can''t be used: ' + $v.reason)
            continue
        }
        if (-not $v.exists) {
            if (Read-YesNo ('"' + $v.path + '" does not exist. Create it?') $true) {
                New-Item -ItemType Directory -Force -Path $v.path | Out-Null
            } else { continue }
        }
        if ($v.cloud_synced) {
            Write-Warn 'This folder is synced by OneDrive/iCloud. Files that are "online-only"'
            Write-Warn 'are skipped by auto-organize, and every upload is also uploaded to the cloud.'
            if (-not (Read-YesNo 'Use it anyway?' $false)) { continue }
        }
        Write-Ok ('Photo folder: ' + $v.path)
        return $v.path
    }
}

function Read-AccessCode {
    Write-Step 'Step 6/11: Access code (the password phones use to connect)'
    Write-Host '  1) Generate a strong code for me (recommended)'
    Write-Host '  2) Type my own (12+ characters with lower case, UPPER case, a number and a symbol)'
    while ($true) {
        $choice = Read-Host '  Choose 1 or 2 [1]'
        if ([string]::IsNullOrWhiteSpace($choice) -or $choice.Trim() -eq '1') {
            $g = Invoke-Admin -Command 'gen-code'
            $script:GeneratedCode = $g.code
            Write-Host ''
            Write-Host ('  Your access code:  ' + $g.code) -ForegroundColor Green
            Write-Host '  Write it down - you type it into the app on each phone.' -ForegroundColor Green
            Write-Host '  (You can show it again later with Start Menu > Lumina Server - Status.)' -ForegroundColor Gray
            return $g.code
        }
        if ($choice.Trim() -eq '2') {
            while ($true) {
                $a = ConvertFrom-SecureToPlain (Read-Host '  Access code' -AsSecureString)
                $v = Invoke-Admin -Command 'validate-code' -Payload @{ code = $a }
                if (-not $v.ok) { Write-Bad ('The code must contain ' + $v.reason + '.'); continue }
                $b = ConvertFrom-SecureToPlain (Read-Host '  Type it again' -AsSecureString)
                if ($a -cne $b) { Write-Bad 'The two entries do not match.'; continue }
                Write-Ok 'Access code accepted.'
                return $a
            }
        }
    }
}

function Get-PortOwner([int]$Port) {
    try {
        $c = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction Stop | Select-Object -First 1
        if ($c) {
            $p = Get-Process -Id $c.OwningProcess -ErrorAction SilentlyContinue
            if ($p) { return ($p.ProcessName + ' (PID ' + $p.Id + ')') }
            return ('PID ' + $c.OwningProcess)
        }
    } catch { }
    return $null
}

function Read-Port([string]$Label, [int]$Default, [int]$Other) {
    while ($true) {
        $s = Read-Host ('  ' + $Label + ' port [' + $Default + ']')
        if ([string]::IsNullOrWhiteSpace($s)) { $s = [string]$Default }
        $n = 0
        if (-not [int]::TryParse($s.Trim(), [ref]$n) -or $n -lt 1024 -or $n -gt 65535) {
            Write-Bad 'Enter a number from 1024 to 65535.'; continue
        }
        if ($n -eq $Other) { Write-Bad 'HTTP and HTTPS need different ports.'; continue }
        $owner = Get-PortOwner $n
        if ($owner) {
            Write-Bad ('Port ' + $n + ' is in use by ' + $owner + '.')
            Write-Host '  If that is an OLD copy of this server, close it (or end that task) and press Enter;' -ForegroundColor Gray
            Write-Host '  otherwise type a different port.' -ForegroundColor Gray
            continue
        }
        return $n
    }
}

function Read-Ports([int]$Http, [int]$Https) {
    Write-Step 'Step 7/11: Network ports'
    Write-Host '  Press Enter to keep the defaults unless another program uses them.'
    $h = Read-Port 'HTTP' $Http -1
    $s = Read-Port 'HTTPS' $Https $h
    return @($h, $s)
}

function Protect-ConfigDir {
    # Config + certificate: current user and SYSTEM only.
    New-Item -ItemType Directory -Force -Path $ConfigDir | Out-Null
    $sid = [System.Security.Principal.WindowsIdentity]::GetCurrent().User.Value
    $r = Invoke-Native -Exe 'icacls.exe' -Arguments @($ConfigDir, '/inheritance:r', '/grant:r',
        ('*' + $sid + ':(OI)(CI)F'), '*S-1-5-18:(OI)(CI)F', '/T', '/C', '/Q')
    if ($r.ExitCode -ne 0) {
        Write-Warn ('Could not restrict permissions on ' + $ConfigDir + ' (icacls exit ' + $r.ExitCode + ').')
    } else {
        Write-Ok 'Settings folder restricted to your account.'
    }
}

# ---------------------------------------------------------------------------
function Quote-PS([string]$s) { return "'" + $s.Replace("'", "''") + "'" }

function Set-Firewall([int]$Http, [int]$Https, $PyInfo) {
    Write-Step 'Step 8/11: Windows Firewall'
    $profiles = @()
    try { $profiles = @(Get-NetConnectionProfile -ErrorAction Stop) } catch { }
    $public = @($profiles | Where-Object { $_.NetworkCategory -eq 'Public' })
    $switchIdx = @()
    if ($public.Count -gt 0) {
        foreach ($p in $public) {
            Write-Warn ('Your network "' + $p.Name + '" is set to Public. Phones can only connect on a Private network.')
        }
        Write-Host '  To change it yourself: Settings > Network & internet > Wi-Fi (or Ethernet) >' -ForegroundColor Gray
        Write-Host '  your network > Network profile type > Private. Only do this for your HOME network.' -ForegroundColor Gray
        if (Read-YesNo 'Is this your home network, and should I switch it to Private now?' $false) {
            foreach ($p in $public) { $switchIdx += [int]$p.InterfaceIndex }
        }
    }
    Write-Host '  The server needs an inbound firewall rule (Private networks, local subnet only).'
    if (-not (Read-YesNo 'Add it now? Windows will ask for administrator permission once.' $true)) {
        Write-Warn 'Skipped. Phones will not be able to connect until the ports are allowed.'
        return
    }
    $programs = @()
    foreach ($p in @($PyInfo.base_executable, $PyInfo.base_pythonw)) {
        if ($p -and (Test-Path -LiteralPath $p) -and ($programs -notcontains $p)) { $programs += $p }
    }
    $progList = ($programs | ForEach-Object { Quote-PS $_ }) -join ', '
    $idxList = ($switchIdx | ForEach-Object { [string]$_ }) -join ', '
    $errFile = Join-Path $env:TEMP ('lumina_fw_' + [guid]::NewGuid().ToString('N') + '.txt')
    $script = @"
`$ErrorActionPreference = 'Stop'
try {
    Get-NetFirewallRule -DisplayName $(Quote-PS $FirewallName) -ErrorAction SilentlyContinue | Remove-NetFirewallRule
    Get-NetFirewallRule -DisplayName 'PictureViewer Server' -ErrorAction SilentlyContinue | Remove-NetFirewallRule
    foreach (`$prog in @($progList)) {
        New-NetFirewallRule -DisplayName $(Quote-PS $FirewallName) -Description 'Lumina Gallery Server (photo backup from your phone)' -Direction Inbound -Action Allow -Protocol TCP -LocalPort @($Http, $Https) -Program `$prog -Profile Private -RemoteAddress LocalSubnet | Out-Null
    }
    foreach (`$i in @($idxList)) { Set-NetConnectionProfile -InterfaceIndex `$i -NetworkCategory Private }
    exit 0
} catch {
    `$_.ToString() | Out-File -FilePath $(Quote-PS $errFile) -Encoding UTF8
    exit 1
}
"@
    $tmp = Join-Path $env:TEMP ('lumina_fw_' + [guid]::NewGuid().ToString('N') + '.ps1')
    [System.IO.File]::WriteAllText($tmp, $script, (New-Object System.Text.UTF8Encoding $true))
    try {
        $proc = Start-Process -FilePath 'powershell.exe' -Verb RunAs -Wait -PassThru -WindowStyle Hidden `
            -ArgumentList @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', ('"' + $tmp + '"'))
        if ($proc.ExitCode -eq 0) {
            Write-Ok ('Firewall rule "' + $FirewallName + '" added for ports ' + $Http + ' and ' + $Https + '.')
            if ($switchIdx.Count) { Write-Ok 'Network switched to Private.' }
        } else {
            $msg = ''
            if (Test-Path -LiteralPath $errFile) { $msg = Get-Content -LiteralPath $errFile -Raw }
            Write-Bad ('Firewall step failed: ' + $msg)
            Show-ManualFirewall $Http $Https $programs
        }
    } catch {
        Write-Warn 'Administrator permission was not granted.'
        Show-ManualFirewall $Http $Https $programs
    } finally {
        Remove-Item -LiteralPath $tmp -ErrorAction SilentlyContinue
        Remove-Item -LiteralPath $errFile -ErrorAction SilentlyContinue
    }
}

function Show-ManualFirewall([int]$Http, [int]$Https, $Programs) {
    Write-Host '  To allow it later, open PowerShell as Administrator and run:' -ForegroundColor Yellow
    foreach ($p in $Programs) {
        Write-Host ("    New-NetFirewallRule -DisplayName '" + $FirewallName + "' -Direction Inbound -Action Allow -Protocol TCP -LocalPort " + $Http + ',' + $Https + " -Program '" + $p.Replace("'", "''") + "' -Profile Private -RemoteAddress LocalSubnet") -ForegroundColor Yellow
    }
    Write-Host '  (or just run install_windows.bat again and accept the prompt).' -ForegroundColor Yellow
}

# ---------------------------------------------------------------------------
function Register-AutoStart {
    Write-Step 'Step 9/11: Start automatically when you sign in'
    $old = Get-ScheduledTask -TaskName 'PictureViewerServer' -ErrorAction SilentlyContinue
    if ($old) {
        Write-Warn 'Found the OLD startup task "PictureViewerServer" (previous version of this server).'
        if (Read-YesNo 'Remove it so the old and new servers do not fight over the ports?' $true) {
            try { Stop-ScheduledTask -TaskName 'PictureViewerServer' -ErrorAction SilentlyContinue } catch { }
            try {
                Unregister-ScheduledTask -TaskName 'PictureViewerServer' -Confirm:$false -ErrorAction Stop
                Write-Ok 'Old task removed.'
            } catch {
                Write-Warn ('Could not remove it (' + $_.Exception.Message + '). Remove "PictureViewerServer" in Task Scheduler yourself.')
            }
        }
    }
    $user = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name
    try {
        $action = New-ScheduledTaskAction -Execute $VenvPythonW -Argument ('"' + $RunPy + '"') -WorkingDirectory $AppDir
        $trigger = New-ScheduledTaskTrigger -AtLogOn -User $user
        $principal = New-ScheduledTaskPrincipal -UserId $user -LogonType Interactive -RunLevel Limited
        $settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
            -StartWhenAvailable -ExecutionTimeLimit ([TimeSpan]::Zero) -RestartCount 3 `
            -RestartInterval (New-TimeSpan -Minutes 1) -MultipleInstances IgnoreNew
        Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Principal $principal `
            -Settings $settings -Description 'Lumina Gallery Server - shares your photo folder with the Lumina Gallery iPhone app.' `
            -Force | Out-Null
        $check = Get-ScheduledTask -TaskName $TaskName -ErrorAction Stop
        Write-Ok ('Auto-start task "' + $TaskName + '" registered (state: ' + $check.State + ').')
        return $true
    } catch {
        Write-Bad ('Could not register the auto-start task: ' + $_.Exception.Message)
        # Fallback: a shortcut in the per-user Startup folder (no admin needed).
        try {
            $startup = [Environment]::GetFolderPath('Startup')
            $shell = New-Object -ComObject WScript.Shell
            $lnk = $shell.CreateShortcut((Join-Path $startup 'Lumina Gallery Server.lnk'))
            $lnk.TargetPath = $VenvPythonW
            $lnk.Arguments = '"' + $RunPy + '"'
            $lnk.WorkingDirectory = $AppDir
            $lnk.Save()
            Write-Ok 'Added a Startup-folder shortcut instead (starts when you sign in).'
        } catch {
            Write-Warn 'Auto-start is not set up. Use Start Menu > Lumina Server - Start after signing in.'
        }
        return $false
    }
}

function New-Shortcut([string]$Name, [string]$ScriptName, [string]$Description) {
    $shell = New-Object -ComObject WScript.Shell
    $lnk = $shell.CreateShortcut((Join-Path $StartMenuDir ($Name + '.lnk')))
    $lnk.TargetPath = Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe'
    $lnk.Arguments = '-NoProfile -ExecutionPolicy Bypass -File "' + (Join-Path (Join-Path $AppDir 'windows') $ScriptName) + '"'
    $lnk.WorkingDirectory = $AppDir
    $lnk.Description = $Description
    $lnk.Save()
}

function Add-Shortcuts {
    Write-Step 'Step 10/11: Start Menu shortcuts'
    try {
        New-Item -ItemType Directory -Force -Path $StartMenuDir | Out-Null
        New-Shortcut 'Lumina Server - Start' 'start.ps1' 'Start the Lumina Gallery Server'
        New-Shortcut 'Lumina Server - Stop' 'stop.ps1' 'Stop the Lumina Gallery Server'
        New-Shortcut 'Lumina Server - Status' 'status.ps1' 'Addresses, ports, certificate fingerprint, recent log'
        New-Shortcut 'Lumina Server - Add Folder' 'add_folder.ps1' 'Add another backup folder (e.g. for a second phone)'
        New-Shortcut 'Lumina Server - Uninstall' 'uninstall.ps1' 'Remove the Lumina Gallery Server (your photos are never touched)'
        Write-Ok ('Shortcuts are in Start Menu > Lumina Server')
    } catch {
        Write-Warn ('Could not create Start Menu shortcuts: ' + $_.Exception.Message)
    }
}

function Start-AndVerify([int]$Http, [bool]$TaskOk) {
    Write-Step 'Step 11/11: Starting the server'
    $started = $false
    if ($TaskOk) {
        try { Start-ScheduledTask -TaskName $TaskName -ErrorAction Stop; $started = $true } catch {
            Write-Warn ('Could not start the task: ' + $_.Exception.Message)
        }
    }
    if (-not $started) {
        Start-Process -FilePath $VenvPythonW -ArgumentList ('"' + $RunPy + '"') -WorkingDirectory $AppDir -WindowStyle Hidden
    }
    Write-Host '  Waiting for the server to answer (up to 30 seconds)...'
    if (Wait-ServerUp $Http 30) {
        Write-Ok 'The server is running.'
        return $true
    }
    Write-Bad 'The server did not answer within 30 seconds.'
    Show-LogTail 30
    return $false
}

function Show-Summary([int]$Http) {
    $info = $null
    try { $info = Invoke-Admin -Command 'info' } catch { }
    $ips = @(Get-LanAddresses)
    if ($ips.Count -eq 0 -and $info) { $ips = @($info.lan_ips) }
    Write-Host ''
    Write-Host '================================================================' -ForegroundColor Green
    Write-Host '  Lumina Gallery Server is installed' -ForegroundColor Green
    Write-Host '================================================================' -ForegroundColor Green
    Write-Host '  In the Lumina Gallery app, enter this server address:'
    if ($ips.Count) {
        foreach ($ip in $ips) { Write-Host ('      http://' + $ip + ':' + $Http) -ForegroundColor Cyan }
    } else {
        Write-Host '      (could not detect - run ipconfig and use your Wi-Fi/Ethernet IPv4 address)'
    }
    if ($script:GeneratedCode) {
        Write-Host ('  Access code: ' + $script:GeneratedCode) -ForegroundColor Green
    } else {
        Write-Host '  Access code: the one you chose (Status shortcut can show it).'
    }
    if ($info -and $info.fingerprint) {
        Write-Host '  The app switches to HTTPS automatically. Certificate fingerprint (SHA-256):'
        Write-Host ('      ' + (Format-Fingerprint $info.fingerprint)) -ForegroundColor Gray
    }
    Write-Host ''
    Write-Host '  Start Menu > Lumina Server: Start / Stop / Status / Add Folder / Uninstall'
    Write-Host ('  Logs: ' + $LogFile)
    Write-Host '  To update later: download the new ZIP and run install_windows.bat again'
    Write-Host '  (your settings are kept).'
    Write-Host '================================================================' -ForegroundColor Green
}

# ---------------------------------------------------------------------------
function Invoke-Setup {
    Write-Host ''
    Write-Host '================================================================' -ForegroundColor Cyan
    Write-Host '  Lumina Gallery Server - Windows setup' -ForegroundColor Cyan
    Write-Host '================================================================' -ForegroundColor Cyan
    Write-Host ('  Program files: ' + $LuminaBase)

    if (Test-Path -LiteralPath $AppDir) {
        Write-Host '  Stopping the running server (if any) for the update...'
        if (-not (Stop-LuminaServer)) { Write-Warn 'Some server processes could not be stopped.' }
    }

    Install-AppFiles
    $py = Find-Python
    if (-not $py) { $py = Install-PythonIfMissing }
    Initialize-Venv $py

    Write-Step 'Step 4/11: Settings'
    $cur = Invoke-Admin -Command 'read-config'
    $keep = $false
    if (-not $cur.exists) {
        if (Invoke-Migration) { $cur = Invoke-Admin -Command 'read-config' }
    }
    if ($cur.exists -and $cur.has_code -and $cur.media_folder) {
        Write-Host '  Current settings:'
        Show-CurrentSettings $cur
        $folderCheck = Invoke-Admin -Command 'validate-folder' -Payload @{ path = $cur.media_folder }
        if (-not $folderCheck.ok) {
            Write-Bad ('The current photo folder can no longer be used: ' + $folderCheck.reason)
        } else {
            $keep = Read-YesNo 'Keep current settings?' $true
        }
    } elseif ($cur.exists) {
        Write-Warn 'Existing settings are incomplete - please answer a few questions.'
    }

    New-Item -ItemType Directory -Force -Path $ConfigDir | Out-Null
    Protect-ConfigDir
    $dirs = @{ log_dir = $LogDir; cert_dir = $CertDir; thumbnail_dir = $ThumbDir }
    if ($keep) {
        $w = Invoke-Admin -Command 'write-config' -Payload @{ values = $dirs }
        if (-not $w.ok) { throw ('Could not update settings: ' + $w.reason) }
        Write-Ok 'Keeping your settings.'
        $httpPort = [int]$cur.port; $httpsPort = [int]$cur.https_port
    } else {
        $defFolder = Get-Setting $cur 'media_folder' (Join-Path $env:USERPROFILE 'Pictures')
        $folder = Read-MediaFolder $defFolder
        Write-Host ''
        Write-Host '  New uploads from your phone are always sorted into Photos\Year\Month'
        Write-Host '  and Videos\Year\Month. The server can also tidy what is ALREADY here.'
        $orgDefault = [bool](Get-Setting $cur 'organize_existing' $true)
        $organize = Read-YesNo 'Organize photos already in this folder into Photos/Videos/Year/Month? This MOVES files (originals kept, nothing deleted)' $orgDefault
        $values = @{ media_folder = $folder; auto_organize = $true; organize_existing = $organize }
        $code = $null
        if ($cur.has_code) {
            if (-not (Read-YesNo 'Keep your current access code?' $true)) { $code = Read-AccessCode }
        } else {
            $code = Read-AccessCode
        }
        if ($code) { $values['access_code'] = $code }
        $ports = Read-Ports ([int](Get-Setting $cur 'port' 8500)) ([int](Get-Setting $cur 'https_port' 8543))
        $values['port'] = $ports[0]; $values['https_port'] = $ports[1]
        foreach ($k in $dirs.Keys) { $values[$k] = $dirs[$k] }
        $regen = $false
        if ($cur.has_secret) {
            $regen = Read-YesNo 'Sign out all phones (they will need the access code again)?' $false
        }
        $w = Invoke-Admin -Command 'write-config' -Payload @{ values = $values; regen_secret = $regen }
        if (-not $w.ok) { throw ('Could not save settings: ' + $w.reason) }
        Write-Ok ('Settings saved to ' + $ConfigFile)
        $httpPort = $ports[0]; $httpsPort = $ports[1]
    }
    Protect-ConfigDir

    $venvInfo = Invoke-Admin -Command 'env-info'
    Set-Firewall $httpPort $httpsPort $venvInfo
    $taskOk = Register-AutoStart
    Add-Shortcuts
    $up = Start-AndVerify $httpPort $taskOk
    if ($up) { Show-Summary $httpPort }
    return $up
}

$exitCode = 0
try {
    if (-not (Invoke-Setup)) { $exitCode = 1 }
} catch {
    Write-Host ''
    Write-Bad $_.Exception.Message
    Write-Host '  Setup did not finish. Fix the problem above and run install_windows.bat again.' -ForegroundColor Yellow
    $exitCode = 1
}
Write-Host ''
exit $exitCode
