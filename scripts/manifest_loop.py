#!/usr/bin/env python3
"""
Structural Alpha Manifest Loop
Periodically refreshes the cross-venue heat scores to drive the Generative Shadow Fleet.
"""

import os
import sys
import time
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parent.parent

def run_script(name):
    print(f"[*] Running {name}...")
    try:
        subprocess.run([sys.executable, str(ROOT / "scripts" / name)], check=True)
    except Exception as e:
        print(f"[!] Error running {name}: {e}")

def main():
    print("STRUCTURAL ALPHA MANIFEST LOOP ONLINE")
    while True:
        # Refresh inputs
        run_script("build_kraken_maker_opportunity_board.py")
        run_script("build_venue_handoff_bridge.py")
        # Run performance feedback audit
        run_script("audit_performance_feedback.py")
        # Build manifest
        run_script("build_structural_alpha_manifest.py")
        # Update executive dashboard
        run_script("build_unified_dashboard.py")
        
        print("[*] Manifest refreshed. Sleeping 60s...")
        time.sleep(60)

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        pass
