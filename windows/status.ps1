# Lumina Gallery Server - status (Start Menu shortcut). ASCII-only.
# Shows addresses, ports, the full certificate fingerprint and the recent log.
# The access code is shown only if you ask for it.
$ScriptDir = $PSScriptRoot
if (-not $ScriptDir) { $ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path }
. (Join-Path $ScriptDir 'common.ps1')

try {
    if (-not (Test-Path -LiteralPath $VenvPython)) { throw 'The server is not installed. Run install_windows.bat.' }
    $info = Invoke-Admin -Command 'info'
    $http = [int]$info.port
    Write-Host ''
    Write-Host '================================================================' -ForegroundColor Cyan
    Write-Host '  Lumina Gallery Server - status' -ForegroundColor Cyan
    Write-Host '================================================================' -ForegroundColor Cyan
    if (Test-ServerUp $http) {
        Write-Ok ('Running (HTTP port ' + $http + ', HTTPS port ' + $info.https_port + ')')
    } else {
        Write-Bad ('Not answering on port ' + $http + '. Use Start Menu > Lumina Server - Start.')
    }
    $task = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    if ($task) {
        $ti = Get-ScheduledTaskInfo -TaskName $TaskName -ErrorAction SilentlyContinue
        Write-Host ('  Auto-start task: ' + $task.State + $(if ($ti) { ' (last result ' + $ti.LastTaskResult + ')' } else { '' }))
    } else {
        Write-Host '  Auto-start task: not registered'
    }
    Write-Host ''
    Write-Host '  Server address for the app:'
    $ips = @(Get-LanAddresses)
    if ($ips.Count -eq 0) { $ips = @($info.lan_ips) }
    if ($ips.Count -eq 0) { Write-Host '      (no network address found - are you connected to Wi-Fi/Ethernet?)' }
    foreach ($ip in $ips) { Write-Host ('      http://' + $ip + ':' + $http) -ForegroundColor Cyan }
    Write-Host ''
    Write-Host '  Shared folders:'
    foreach ($r in @($info.roots)) {
        $state = 'ok'
        if (-not $r.accessible) { $state = 'NOT ACCESSIBLE' }
        Write-Host ('      ' + $r.name + ': ' + $r.path + '  [' + $state + ']')
    }
    Write-Host ('  Auto-organize uploads: ' + $info.auto_organize + '; existing files: ' + $info.organize_existing)
    Write-Host ''
    Write-Host '  Certificate fingerprint (SHA-256) - the app pins this:'
    Write-Host ('      ' + (Format-Fingerprint $info.fingerprint))
    $profiles = @()
    try { $profiles = @(Get-NetConnectionProfile -ErrorAction Stop | Where-Object { $_.NetworkCategory -eq 'Public' }) } catch { }
    foreach ($p in $profiles) {
        Write-Warn ('Network "' + $p.Name + '" is Public - phones cannot connect. Switch it to Private in Settings > Network & internet.')
    }
    Write-Host ''
    Show-LogTail 20
    Write-Host ''
    if (Read-YesNo 'Show the access code on screen?' $false) {
        $c = Invoke-Admin -Command 'show-code'
        Write-Host ('  Access code: ' + $c.code) -ForegroundColor Green
    }
} catch {
    Write-Bad $_.Exception.Message
}
Wait-ForEnter
