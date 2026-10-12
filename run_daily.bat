@echo off
setlocal
REM ============================================================
REM  aihot daily runner - called by Windows Task Scheduler (17:00)
REM  Usage: run_daily.bat [extra args passed to "aihot run"]
REM         e.g.  run_daily.bat --dry-run
REM  NOTE: keep this file ASCII-only (cmd.exe mangles non-ASCII args)
REM ============================================================

chcp 65001 >nul
set PYTHONUTF8=1
set PYTHONIOENCODING=utf-8
cd /d "%~dp0"

if not exist logs mkdir logs
set LOGFILE=logs\run_daily.log

echo. >> "%LOGFILE%"
echo ===== %date% %time% preflight ===== >> "%LOGFILE%"
".venv\Scripts\python.exe" -m aihot check --quick >> "%LOGFILE%" 2>&1
if errorlevel 1 (
    echo [%date% %time%] PREFLIGHT FAILED - skip today >> "%LOGFILE%"
    ".venv\Scripts\python.exe" -m aihot skip --reason "preflight failed: LLM or WeCom unreachable. Today's digest was skipped. See logs\run_daily.log" >> "%LOGFILE%" 2>&1
    exit /b 2
)

echo ===== %date% %time% run ===== >> "%LOGFILE%"
".venv\Scripts\python.exe" -m aihot run %* >> "%LOGFILE%" 2>&1
set RC=%errorlevel%
echo [%date% %time%] exit=%RC% >> "%LOGFILE%"
exit /b %RC%
