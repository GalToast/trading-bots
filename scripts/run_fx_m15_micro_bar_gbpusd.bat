@echo off
REM FX M15 Micro Bar Shadow - GBPUSD
REM Processes M15 bars using bar-level simulation (matches backtest methodology)
cd /d "%~dp0.."
echo [%DATE% %TIME%] Starting FX M15 Micro Bar Shadow - GBPUSD
echo Step=0.0001, MaxOpen=80, Alpha=1.0, Momentum=True
:loop
python scripts\shadow_fx_m15_micro_bar.py --symbol GBPUSD --step 0.0001 --max-open 80 --poll-seconds 30 >> reports\fx_m15_micro_bar_gbpusd.log 2>&1
timeout /t 5 /nobreak >nul
goto loop
