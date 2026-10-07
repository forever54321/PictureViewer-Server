# Lumina Gallery Server - start (Start Menu shortcut). ASCII-only.
$ScriptDir = $PSScriptRoot
if (-not $ScriptDir) { $ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path }
. (Join-Path $ScriptDir 'common.ps1')

$ports = Get-ConfiguredPorts
$http = $ports[0]
try {
    if (Test-ServerUp $http) {
        Write-Ok ('The server is already running (port ' + $http + ').')
    } else {
        if (-not (Test-Path -LiteralPath $VenvPythonW)) { throw 'The server is not installed. Run install_windows.bat.' }
        $task = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
        if ($task) {
            Start-ScheduledTask -TaskName $TaskName
        } else {
            Start-Process -FilePath $VenvPythonW -ArgumentList ('"' + $RunPy + '"') -WorkingDirectory $AppDir -WindowStyle Hidden
        }
        Write-Host '  Starting... (up to 30 seconds)'
        if (Wait-ServerUp $http 30) {
            Write-Ok 'The server is running.'
        } else {
            Write-Bad 'The server did not start.'
            Show-LogTail 20
        }
    }
} catch {
    Write-Bad $_.Exception.Message
}
Wait-ForEnter
