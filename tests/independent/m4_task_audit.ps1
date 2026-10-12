# M4 独立验证：已注册计划任务 aihot-daily 配置审计
# 用法: powershell -NoProfile -ExecutionPolicy Bypass -File tests\independent\m4_task_audit.ps1
param(
    [string]$TaskName = "aihot-daily",
    [string]$OutFile = "reports\evidence\m4\raw\01-task-config.txt"
)

$ErrorActionPreference = "Stop"
$t = Get-ScheduledTask -TaskName $TaskName -ErrorAction Stop
$lines = @()
$lines += "TaskName=" + $t.TaskName
$lines += "State=" + $t.State
$lines += "TaskPath=" + ($t.TaskPath -replace '\s+$','')
$a = $t.Actions | Select-Object -First 1
$lines += "Action.Execute=" + $a.Execute
$lines += "Action.WorkingDirectory=" + $a.WorkingDirectory
$lines += "Action.Arguments=[" + $a.Arguments + "]"
foreach ($tr in $t.Triggers) {
    $lines += "Trigger.Type=" + $tr.CimClass.CimClassName
    $lines += "Trigger.StartBoundary=" + $tr.StartBoundary
    if ($null -ne $tr.DaysInterval) { $lines += "Trigger.DaysInterval=" + $tr.DaysInterval }
    if ($null -ne $tr.Enabled) { $lines += "Trigger.Enabled=" + $tr.Enabled }
    if ($null -ne $tr.RandomDelay) { $lines += "Trigger.RandomDelay=" + $tr.RandomDelay }
}
$s = $t.Settings
$lines += "Settings.Enabled=" + $s.Enabled
$lines += "Settings.WakeToRun=" + $s.WakeToRun
$lines += "Settings.StartWhenAvailable=" + $s.StartWhenAvailable
$lines += "Settings.MultipleInstances=" + $s.MultipleInstances
$lines += "Settings.ExecutionTimeLimit=" + $s.ExecutionTimeLimit
$lines += "Settings.DisallowStartIfOnBatteries=" + $s.DisallowStartIfOnBatteries
$lines += "Settings.StopIfGoingOnBatteries=" + $s.StopIfGoingOnBatteries
$lines += "Settings.AllowHardTerminate=" + $s.AllowHardTerminate
$lines += "Settings.RestartCount=" + $s.RestartCount
$lines += "Principal.UserId=" + $t.Principal.UserId
$lines += "Principal.LogonType=" + $t.Principal.LogonType
$lines += "Principal.RunLevel=" + $t.Principal.RunLevel
$info = Get-ScheduledTaskInfo -TaskName $TaskName
$lines += "Info.LastRunTime=" + $info.LastRunTime
$lines += "Info.LastTaskResult=" + $info.LastTaskResult
$lines += "Info.NextRunTime=" + $info.NextRunTime
$lines += "Info.NumberOfMissedRuns=" + $info.NumberOfMissedRuns

New-Item -ItemType Directory -Force -Path (Split-Path -Parent $OutFile) | Out-Null
$lines | Set-Content -Path $OutFile -Encoding UTF8
$lines | Write-Output
