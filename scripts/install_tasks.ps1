$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
$run1445 = Join-Path $projectRoot "run_1445.bat"
$runClose = Join-Path $projectRoot "run_close.bat"

if (-not (Test-Path -LiteralPath $run1445) -or -not (Test-Path -LiteralPath $runClose)) {
    throw "未找到 run_1445.bat 或 run_close.bat"
}

$cmd = Join-Path $env:SystemRoot "System32\cmd.exe"
$action1445 = New-ScheduledTaskAction -Execute $cmd -Argument "/c `"$run1445`"" -WorkingDirectory $projectRoot
$trigger1445 = New-ScheduledTaskTrigger -Weekly -DaysOfWeek Monday,Tuesday,Wednesday,Thursday,Friday -At "14:45"
$actionClose = New-ScheduledTaskAction -Execute $cmd -Argument "/c `"$runClose`"" -WorkingDirectory $projectRoot
$triggerClose = New-ScheduledTaskTrigger -Weekly -DaysOfWeek Monday,Tuesday,Wednesday,Thursday,Friday -At "15:10"
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -ExecutionTimeLimit (New-TimeSpan -Hours 2)

Register-ScheduledTask -TaskName "AStock-Monitor-1445" -Action $action1445 -Trigger $trigger1445 -Settings $settings -Description "A股14:45量化监测" -Force
Register-ScheduledTask -TaskName "AStock-Monitor-Close" -Action $actionClose -Trigger $triggerClose -Settings $settings -Description "A股盘后复盘" -Force

Write-Output "已安装 AStock-Monitor-1445 与 AStock-Monitor-Close"
