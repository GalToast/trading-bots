@echo off
REM GBPUSD Tick-Forward Polling Loop — polls every 5 seconds
cd /d "%~dp0.."
:loop
python scripts\shadow_gbpusd_tick_forward_poll.py >> reports\gbpusd_tick_forward_poll.log 2>&1
timeout /t 5 /nobreak >nul
goto loop
