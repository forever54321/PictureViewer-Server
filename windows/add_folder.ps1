# Lumina Gallery Server - add another backup folder (e.g. for a second phone).
# Values go to Python on STDIN, never through cmd.exe. ASCII-only.
$ScriptDir = $PSScriptRoot
if (-not $ScriptDir) { $ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path }
. (Join-Path $ScriptDir 'common.ps1')

try {
    # Prefer the installed server; fall back to an old-style install that
    # lives in the folder this script came from (venv + .env next to server.py).
    $py = $VenvPython; $admin = $AdminPy; $cfg = $ConfigFile
    if (-not (Test-Path -LiteralPath $VenvPython)) {
        $src = Split-Path -Parent $ScriptDir
        $oldPy = Join-Path $src 'venv\Scripts\python.exe'
        if (-not (Test-Path -LiteralPath $oldPy)) { throw 'The server is not installed. Run install_windows.bat first.' }
        $py = $oldPy; $admin = Join-Path $src 'lumina_admin.py'; $cfg = ''
    }
    Write-Host ''
    Write-Host '  Add a backup folder' -ForegroundColor Cyan
    Write-Host '  Each phone can pick its own folder in the app (same access code).'
    $name = Read-Host '  Name to show in the app (e.g. Wife''s iPhone)'
    if ([string]::IsNullOrWhiteSpace($name)) { throw 'No name entered - nothing was added.' }
    $path = $null
    if ([System.Threading.Thread]::CurrentThread.GetApartmentState() -eq 'STA') {
        try {
            Add-Type -AssemblyName System.Windows.Forms
            $dlg = New-Object System.Windows.Forms.FolderBrowserDialog
            $dlg.Description = 'Choose the backup folder for "' + $name + '"'
            $dlg.ShowNewFolderButton = $true
            $owner = New-Object System.Windows.Forms.Form
            $owner.TopMost = $true
            Write-Host '  A folder picker window opened (it may be behind this window).'
            if ($dlg.ShowDialog($owner) -eq [System.Windows.Forms.DialogResult]::OK) { $path = $dlg.SelectedPath }
            $owner.Dispose()
        } catch { $path = $null }
    }
    if (-not $path) { $path = Read-Host '  Full folder path (e.g. D:\Backups\Wife)' }
    if ([string]::IsNullOrWhiteSpace($path)) { throw 'No folder entered - nothing was added.' }
    $r = Invoke-Admin -Command 'add-folder' -Payload @{ name = $name.Trim(); path = $path.Trim().Trim('"') } -Python $py -Script $admin -Config $cfg
    if ($r.ok) {
        Write-Ok $r.message
        Write-Host '  It appears in the app shortly (no restart needed).'
    } else {
        Write-Bad $r.reason
    }
} catch {
    Write-Bad $_.Exception.Message
}
Wait-ForEnter
