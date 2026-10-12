# 撤销 aihot 每日任务
#
# 用法：powershell -ExecutionPolicy Bypass -File scripts\unregister_task.ps1

param(
    [string]$TaskName = "aihot-daily"
)

$ErrorActionPreference = "Stop"
$task = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
if ($null -eq $task) {
    Write-Host "任务不存在：$TaskName"
    exit 0
}

Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
Write-Host "已撤销任务：$TaskName"
