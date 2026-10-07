# The Windows installer now lives in windows\setup.ps1.
# Double-click install_windows.bat (recommended), or run:
#   powershell -NoProfile -ExecutionPolicy Bypass -File windows\setup.ps1
$here = $PSScriptRoot
if (-not $here) { $here = Split-Path -Parent $MyInvocation.MyCommand.Path }
& (Join-Path (Join-Path $here 'windows') 'setup.ps1') @args
exit $LASTEXITCODE
