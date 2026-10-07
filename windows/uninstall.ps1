# Lumina Gallery Server - uninstall (Start Menu shortcut). ASCII-only.
# Removes the program, its Python environment, auto-start task, shortcuts and
# (if you agree) the firewall rule and settings. Your photo folders are NEVER
# touched.
$ScriptDir = $PSScriptRoot
if (-not $ScriptDir) { $ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path }
. (Join-Path $ScriptDir 'common.ps1')
Set-Location -LiteralPath $env:TEMP

function Remove-Safely([string]$Path) {
    if (-not (Test-Path -LiteralPath $Path)) { return }
    $full = [System.IO.Path]::GetFullPath($Path)
    if (-not $full.StartsWith($LuminaBase, [StringComparison]::OrdinalIgnoreCase)) { throw ('Refusing to delete ' + $full) }
    Remove-Item -LiteralPath $full -Recurse -Force
}

try {
    Write-Host ''
    Write-Host '  This removes the Lumina Gallery Server from this PC.' -ForegroundColor Cyan
    Write-Host '  Your photos and videos are NOT deleted.' -ForegroundColor Cyan
    if (-not (Read-YesNo 'Uninstall now?' $false)) { Write-Host '  Nothing was changed.'; Wait-ForEnter; exit 0 }

    [void](Stop-LuminaServer)
    $task = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    if ($task) {
        Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
        Write-Ok 'Auto-start task removed.'
    }
    $startupLnk = Join-Path ([Environment]::GetFolderPath('Startup')) 'Lumina Gallery Server.lnk'
    if (Test-Path -LiteralPath $startupLnk) { Remove-Item -LiteralPath $startupLnk -Force }

    $rule = $null
    try { $rule = Get-NetFirewallRule -DisplayName $FirewallName -ErrorAction SilentlyContinue } catch { }
    if ($rule) {
        if (Read-YesNo 'Remove the firewall rule? (asks for administrator permission)' $true) {
            $cmd = "Get-NetFirewallRule -DisplayName '" + $FirewallName + "' | Remove-NetFirewallRule"
            try {
                $p = Start-Process -FilePath 'powershell.exe' -Verb RunAs -Wait -PassThru -WindowStyle Hidden `
                    -ArgumentList @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-Command', $cmd)
                if ($p.ExitCode -eq 0) { Write-Ok 'Firewall rule removed.' } else { Write-Warn 'Could not remove the firewall rule.' }
            } catch {
                Write-Warn ('Not removed. Later, in an Administrator PowerShell: ' + $cmd)
            }
        }
    }

    if (Test-Path -LiteralPath $StartMenuDir) { Remove-Item -LiteralPath $StartMenuDir -Recurse -Force; Write-Ok 'Start Menu shortcuts removed.' }
    Remove-Safely $VenvDir
    Remove-Safely (Join-Path $LuminaBase 'cache')
    Remove-Safely $AppDir
    Write-Ok 'Program files removed.'

    Write-Host ''
    Write-Host '  Your settings (access code, certificate, extra folders list) are in:'
    Write-Host ('    ' + $ConfigDir)
    Write-Host '  Keep them if you plan to reinstall - phones then stay connected.'
    if (Read-YesNo 'Delete the settings and logs too?' $false) {
        Remove-Safely $ConfigDir
        Remove-Safely $LogDir
        if (@(Get-ChildItem -LiteralPath $LuminaBase -Force -ErrorAction SilentlyContinue).Count -eq 0) {
            Remove-Item -LiteralPath $LuminaBase -Force
        }
        Write-Ok 'Settings and logs deleted.'
    }
    Write-Host ''
    Write-Ok 'Uninstalled. Your photo folders were not touched.'
} catch {
    Write-Bad $_.Exception.Message
}
Wait-ForEnter
