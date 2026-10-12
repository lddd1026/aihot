# 注册 aihot 每日任务（Windows 任务计划程序）
#
# 用法（在项目根目录用 PowerShell 执行）：
#   powershell -ExecutionPolicy Bypass -File scripts\register_task.ps1
#   powershell -ExecutionPolicy Bypass -File scripts\register_task.ps1 -Time 07:00
#
# 关键设置（与 PLAN.md M4 一致）：
#   - 仅用户登录时运行（代理软件依赖用户会话）
#   - 唤醒计算机运行
#   - 错过后尽快补跑（StartWhenAvailable）
#   - 单次执行限时 30 分钟、不重复启动

param(
    [string]$TaskName = "aihot-daily",
    [string]$Time = "07:30"
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$bat = Join-Path $root "run_daily.bat"

if (-not (Test-Path $bat)) {
    throw "找不到 run_daily.bat：$bat"
}

$action = New-ScheduledTaskAction -Execute $bat -WorkingDirectory $root
$trigger = New-ScheduledTaskTrigger -Daily -At $Time
$settings = New-ScheduledTaskSettingsSet `
    -StartWhenAvailable `
    -WakeToRun `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -MultipleInstances IgnoreNew `
    -ExecutionTimeLimit (New-TimeSpan -Minutes 30)
$principal = New-ScheduledTaskPrincipal `
    -UserId "$env:USERDOMAIN\$env:USERNAME" `
    -LogonType Interactive `
    -RunLevel Limited

Register-ScheduledTask `
    -TaskName $TaskName `
    -Action $action `
    -Trigger $trigger `
    -Settings $settings `
    -Principal $principal `
    -Description "aihot 每日 AI 热点日报（需保持代理软件开启；每天 $Time 运行）" `
    -Force | Out-Null

Write-Host "已注册任务：$TaskName"
Write-Host "  运行时间：每天 $Time（仅用户登录时运行 / 唤醒计算机 / 错过后尽快补跑）"
Write-Host "  执行入口：$bat"
Write-Host ""
Write-Host "常用操作："
Write-Host "  立即手动跑一次：  Start-ScheduledTask -TaskName $TaskName"
Write-Host "  查看状态：        Get-ScheduledTask -TaskName $TaskName | Get-ScheduledTaskInfo"
Write-Host "  撤销注册：        powershell -ExecutionPolicy Bypass -File scripts\unregister_task.ps1"
