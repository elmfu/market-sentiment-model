@echo off
REM run_weekly.bat — Task Scheduler 入口（處理路徑空格與 venv 啟動）
REM Task Scheduler 設定:
REM   程式:   "C:\Users\hp\Side Project\reddit-sentiment\run_weekly.bat"
REM   起始於: C:\Users\hp\Side Project\reddit-sentiment
cd /d "%~dp0"
if exist ".venv\Scripts\python.exe" (
    ".venv\Scripts\python.exe" weekly_update.py %*
) else (
    python weekly_update.py %*
)
exit /b %errorlevel%
