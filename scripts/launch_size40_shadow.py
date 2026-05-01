#!/usr/bin/env python3
"""One-click Size40 shadow launcher."""
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "scripts"

def main():
    print("=" * 60)
    print("SIZE40 SHADOW LANE — ONE-CLICK LAUNCHER")
    print("=" * 60)
    print()
    
    # Check files exist
    state_path = ROOT / "reports" / "kraken_spot_maker_machinegun_size40_shadow_state.json"
    events_path = ROOT / "reports" / "kraken_spot_maker_machinegun_size40_events.jsonl"
    
    if not state_path.exists():
        print(f"[ERROR] State file not found: {state_path}")
        return 1
    
    if not events_path.exists():
        print(f"[ERROR] Events file not found: {events_path}")
        return 1
    
    print("[READY] Size40 shadow lane files found!")
    print(f"  State: {state_path}")
    print(f"  Events: {events_path}")
    print()
    
    # Build launch command
    cmd = [
        "python",
        str(SCRIPTS / "live_kraken_spot_frontier_maker_machinegun_shadow.py"),
        "--state-path", str(state_path),
        "--events-path", str(events_path),
        "--max-quote-usd", "40.0",
        "--min-in-position-spread-bps", "50",
        "--max-loss-pct", "1.5",
        "--no-mfe-stop-min-age-seconds", "30",
        "--systemic-max-positions", "3",
        "--reentry-cooldown-polls", "10",
    ]
    
    print("[LAUNCH] Size40 shadow lane command:")
    print(f"  {' '.join(cmd)}")
    print()
    print("[INFO] Expected impact:")
    print("  Size12: $0.43/close -> $9.01/hr")
    print("  Size40: $1.43/close -> $30.04/hr -> $21,625/month")
    print()
    print("[ACTION] Copy and run the command above in a new terminal!")
    print("=" * 60)
    
    return 0

if __name__ == "__main__":
    sys.exit(main())
