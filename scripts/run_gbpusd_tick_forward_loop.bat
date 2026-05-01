@echo off
REM GBPUSD Tick-Native Forward-Shadow Polling Loop
REM Runs every 30 seconds, collects live ticks, simulates penetration lattice
REM Output logged to: reports/gbpusd_tick_forward_loop.log

set SCRIPT_DIR=%~dp0
cd /d "%SCRIPT_DIR%\.."

echo [%DATE% %TIME%] Starting GBPUSD tick-forward shadow polling loop...
echo [%DATE% %TIME%] Configuration: GBPUSD sell=0.5/buy=1.0 gap=1/3 alpha=0.5
echo [%DATE% %TIME%] Poll interval: 30 seconds

:loop
echo.
echo [%DATE% %TIME%] Running shadow_gbpusd_tick_forward_poll.py...
python scripts\shadow_gbpusd_tick_forward_poll.py >> reports\gbpusd_tick_forward_loop.log 2>&1
if %ERRORLEVEL% NEQ 0 (
    echo [%DATE% %TIME%] ERROR: Script exited with code %ERRORLEVEL%
)

timeout /t 5 /nobreak >nul
goto loop
