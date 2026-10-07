# Lumina Gallery Server - stop (Start Menu shortcut). ASCII-only.
$ScriptDir = $PSScriptRoot
if (-not $ScriptDir) { $ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path }
. (Join-Path $ScriptDir 'common.ps1')

try {
    if (Stop-LuminaServer) {
        Write-Ok 'The server is stopped. It starts again automatically the next time you sign in'
        Write-Host '  (or use Start Menu > Lumina Server - Start).'
    } else {
        Write-Bad 'Some server processes could not be stopped. Try again, or restart Windows.'
    }
} catch {
    Write-Bad $_.Exception.Message
}
Wait-ForEnter
