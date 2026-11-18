# Registers the local "Cenu Mednieks" tracker: salidzini.lv + pigu.lt run from your
# home IP 3x/day (07:15 / 15:15 / 23:15) and write to the same state gist as GitHub
# Actions (which covers amazon + hinnavaatlus). Requires: Python on PATH, .env filled.
#
# Run from PowerShell:  powershell -ExecutionPolicy Bypass -File .\setup-local-task.ps1

$repo = Split-Path -Parent $MyInvocation.MyCommand.Path

$action = New-ScheduledTaskAction -Execute "python.exe" `
    -Argument "scraper.py --stores salidzini.lv,pigu.lt --no-digest" `
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
    -Description "Price tracker: salidzini.lv + pigu.lt from home IP (GitHub Actions covers the rest)"

Write-Host ""
Write-Host "Registered 'Cenu Mednieks local tracker' (07:15 / 15:15 / 23:15 daily)."
Write-Host "Test it now with:"
Write-Host "  Start-ScheduledTask -TaskName 'Cenu Mednieks local tracker'"
