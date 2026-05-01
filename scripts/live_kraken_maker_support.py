import subprocess
import time
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent

def run_step(cmd):
    print(f"--- RUNNING: {' '.join(cmd)} ---")
    try:
        subprocess.run(cmd, check=True, cwd=REPO)
    except Exception as e:
        print(f"ERROR in {cmd[1]}: {e}")

def main():
    print("KRAKEN MAKER SUPPORT ORCHESTRATOR STARTED")
    while True:
        # 1. Update Radar (One-off since build_once is called in radar.py if --loop not used, 
        # but radar.py --loop is better. Actually I'll just run it once per loop here.)
        run_step([sys.executable, "scripts/build_kraken_spot_live_radar.py"])
        
        # 2. Update Foundry (Bridge)
        run_step([sys.executable, "scripts/build_kraken_live_foundry_bridge.py"])
        
        # 3. Update Bear Velocity (Veto list for dumping assets)
        run_step([sys.executable, "scripts/build_kraken_spot_bear_velocity_board.py"])
        
        # 4. Update Pulse Board (Trend/Volatility)
        run_step([sys.executable, "scripts/build_kraken_spot_pulse_board.py", "--refresh-cache", "--max-candle-fetches", "50"])
        
        # 5. Update Handoff Bridge (Lead-Lag from Coinbase)
        run_step([sys.executable, "scripts/build_venue_handoff_bridge.py"])
        
        # 6. Update Opportunity Board (Maker Efficiency)
        run_step([sys.executable, "scripts/build_kraken_maker_opportunity_board.py"])
        
        # 7. Update Performance Feedback (Audit real MFE capture)
        run_step([sys.executable, "scripts/audit_performance_feedback.py"])
        
        # 8. Update Structural Alpha Manifest (Final Synthesis)
        run_step([sys.executable, "scripts/build_structural_alpha_manifest.py"])
        
        # 9. Hindsight Audit (Measure execution efficiency)
        run_step([sys.executable, "scripts/kraken_maker_hindsight_audit.py"])
        
        print("--- LOOP COMPLETE. SLEEPING 120s ---")
        time.sleep(120)

if __name__ == "__main__":
    main()
