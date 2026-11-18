# Registers the local "Cenu Mednieks" tracker: salidzini.lv runs from your
# home IP 3x/day (07:15 / 15:15 / 23:15) and writes to the same state gist as
# GitHub Actions (which covers amazon + hinnavaatlus). Requires: .env filled.
# (pigu.lt is paused - Cloudflare-flagged after heavy retries; re-add via /add.)
#
# Run from PowerShell:  powershell -ExecutionPolicy Bypass -File .\setup-local-task.ps1
# ASCII only - Windows PowerShell 5.1 misparses BOM-less UTF-8 with non-ASCII chars.

$repo = Split-Path -Parent $MyInvocation.MyCommand.Path

# Find python: PATH first, then the default per-user install locations
$python = (Get-Command python.exe -ErrorAction SilentlyContinue).Source
if (-not $python) {
    foreach ($p in @("$env:LOCALAPPDATA\Programs\Python\Python312\python.exe",
                     "$env:LOCALAPPDATA\Programs\Python\Python311\python.exe")) {
        if (Test-Path $p) { $python = $p; break }
    }
}
if (-not $python) { throw "python.exe not found - install Python 3.11+ or add it to PATH" }
Write-Host "Using python: $python"

$action = New-ScheduledTaskAction -Execute $python `
    -Argument "scraper.py --stores salidzini.lv,kurpirkt.lv,ceno.lv --no-digest" `
    -WorkingDirectory $repo

$triggers = @(
    (New-ScheduledTaskTrigger -Daily -At 07:15),
    (New-ScheduledTaskTrigger -Daily -At 15:15),
    (New-ScheduledTaskTrigger -Daily -At 23:15)
)

$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable `
    -ExecutionTimeLimit (New-TimeSpan -Minutes 15) -MultipleInstances IgnoreNew

Register-ScheduledTask -TaskName "Cenu Mednieks local tracker" `
    -Action $action -Trigger $triggers -Settings $settings -Force `
    -Description "Price tracker: salidzini.lv from home IP (GitHub Actions covers the rest)"

Write-Host ""
Write-Host "Registered 'Cenu Mednieks local tracker' (07:15 / 15:15 / 23:15 daily)."
Write-Host "Test it now with:"
Write-Host "  Start-ScheduledTask -TaskName 'Cenu Mednieks local tracker'"
