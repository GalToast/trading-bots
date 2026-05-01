#!/usr/bin/env python3
"""
Draft script to launch the XRP HH breakout shadow lane.
DO NOT RUN until team sign-off is confirmed on switchboard.

Usage (when approved):
  python scripts/operators/launch_xrp_hh_shadow.py --apply

Without --apply: dry-run that prints exactly what would change.
"""
import json
import sys
import os

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
CONFIG = os.path.join(ROOT, "configs", "hungry_hippo_xrpusd_m15_breakout_shadow.json")

def main():
    apply = "--apply" in sys.argv
    
    with open(CONFIG) as f:
        cfg = json.load(f)
    
    print(f"XRP HH Breakout Shadow Launch {'DRY RUN' if not apply else 'APPLY'}")
    print("=" * 60)
    print(f"Config: {CONFIG}")
    print(f"Current enabled: {cfg.get('enabled', False)}")
    print(f"Current pause_note: {cfg.get('pause_note', 'N/A')}")
    print(f"Watchdog group: {cfg.get('watchdog_group', 'N/A')}")
    print(f"Symbol: XRPUSD")
    print(f"Timeframe: M15")
    print(f"Step: 0.00655179")
    print(f"Max open per side: 10")
    print(f"Alpha: 0.7")
    print(f"Escape hatch: enabled (max 12 bars, $2.00 loss)")
    print(f"Floating loss guard: -$15.00")
    print()
    
    if cfg.get('enabled', False):
        print("⚠️  Already enabled! No action needed.")
        return
    
    print("Proposed change:")
    print(f"  enabled: false → true")
    print(f"  pause_note: '{cfg.get('pause_note', '')}' → ''")
    print()
    
    if apply:
        cfg['enabled'] = True
        cfg['pause_note'] = ''
        with open(CONFIG, 'w') as f:
            json.dump(cfg, f, indent=2)
        print("✅ Config updated. Now reload crypto_watchdog:")
        print(f"   powershell -ExecutionPolicy Bypass -File scripts/operators/ensure_watchdog_group.ps1 -GroupName crypto_watchdog -ReloadIfDrift")
    else:
        print("Dry run complete. Run with --apply to apply changes.")

if __name__ == "__main__":
    main()
