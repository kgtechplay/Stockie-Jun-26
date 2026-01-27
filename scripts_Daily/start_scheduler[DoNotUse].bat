@echo off
REM Batch file to start the daily snapshot scheduler
REM This can be added to Windows Startup folder for automatic startup

cd /d "%~dp0\.."
python scripts\schedule_daily_snapshots.py

pause

