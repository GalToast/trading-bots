#!/usr/bin/env python3
"""
Launch expanded FX M15 Warp shadow lanes.

Steps computed at 0.5x ATR(M15) for ~2.0x range/step headroom:
- AUDUSD: step=0.00025 (ATR15=0.00050)
- EURUSD: step=0.00028 (ATR15=0.00055)
- NZDUSD: step=0.00021 (ATR15=0.00042)
- USDCAD: step=0.00023 (ATR15=0.00047)
- XAUUSD: step=4.80   (ATR15=9.61)

Also adds registry entries for watchdog supervision.
"""
import subprocess
import sys
import os
import json
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
os.chdir(REPO)

LANES = [
    {
        "name": "shadow_audusd_m15_warp",
        "symbol": "AUDUSD",
        "step": "0.00025",
        "state_path": "reports/penetration_lattice_shadow_audusd_m15_warp_state.json",
        "event_path": "reports/penetration_lattice_shadow_audusd_m15_warp_events.jsonl",
    },
    {
        "name": "shadow_eurusd_m15_warp",
        "symbol": "EURUSD",
        "step": "0.00028",
        "state_path": "reports/penetration_lattice_shadow_eurusd_m15_warp_state.json",
        "event_path": "reports/penetration_lattice_shadow_eurusd_m15_warp_events.jsonl",
    },
    {
        "name": "shadow_nzdusd_m15_warp",
        "symbol": "NZDUSD",
        "step": "0.00021",
        "state_path": "reports/penetration_lattice_shadow_nzdusd_m15_warp_state.json",
        "event_path": "reports/penetration_lattice_shadow_nzdusd_m15_warp_events.jsonl",
    },
    {
        "name": "shadow_usdcad_m15_warp",
        "symbol": "USDCAD",
        "step": "0.00023",
        "state_path": "reports/penetration_lattice_shadow_usdcad_m15_warp_state.json",
        "event_path": "reports/penetration_lattice_shadow_usdcad_m15_warp_events.jsonl",
    },
    {
        "name": "shadow_xauusd_m15_warp",
        "symbol": "XAUUSD",
        "step": "4.80",
        "state_path": "reports/penetration_lattice_shadow_xauusd_m15_warp_state.json",
        "event_path": "reports/penetration_lattice_shadow_xauusd_m15_warp_events.jsonl",
    },
]

# Register BEFORE launching — eliminates window where children run unsupervised
registry_path = REPO / "configs" / "penetration_lattice_runner_registry.json"
r = json.load(open(registry_path))
existing_names = {lane["name"] for lane in r["lanes"]}
registry_added = 0

for lane in LANES:
    if lane["name"] in existing_names:
        print(f"  {lane['name']} already in registry — skipping")
        continue
    entry = {
        "name": lane["name"],
        "kind": "shadow_fx",
        "state_path": lane["state_path"],
        "event_path": lane["event_path"],
        "poll_seconds": 30,
        "stale_after_seconds": 240,
        "process_match_substrings": [
            "scripts/live_penetration_lattice_tick_crypto_shadow.py",
            lane["state_path"]
        ],
        "restart_args": [
            "scripts/live_penetration_lattice_tick_crypto_shadow.py",
            "--symbol", lane["symbol"], "--timeframe", "M15", "--step", lane["step"],
            "--max-open-per-side", "12", "--raw-close-alpha", "1.0",
            "--raw-rearm-variant", "rearm_lvl2_exc2", "--raw-sell-gap", "1", "--raw-buy-gap", "1",
            "--poll-seconds", "30", "--max-floating-loss-usd", "-15.0",
            "--max-lattice-window-bars", "240",
            "--state-path", lane["state_path"],
            "--event-path", lane["event_path"]
        ],
        "enabled": True
    }
    r["lanes"].append(entry)
    registry_added += 1
    print(f"  Registered: {lane['name']}")

if registry_added > 0:
    with open(registry_path, "w") as f:
        json.dump(r, f, indent=2)
    print(f"  Registry saved ({registry_added} new lanes)")

print(f"\nRegistry now has {len(r['lanes'])} lanes")

# Launch AFTER registration — watchdog can immediately supervise
print("\nLaunching lanes...")
for lane in LANES:
    cmd = [
        sys.executable, "scripts/live_penetration_lattice_tick_crypto_shadow.py",
        "--symbol", lane["symbol"],
        "--timeframe", "M15",
        "--step", lane["step"],
        "--max-open-per-side", "12",
        "--raw-close-alpha", "1.0",
        "--raw-rearm-variant", "rearm_lvl2_exc2",
        "--raw-sell-gap", "1",
        "--raw-buy-gap", "1",
        "--poll-seconds", "30",
        "--max-floating-loss-usd", "-15.0",
        "--max-lattice-window-bars", "240",
        "--state-path", lane["state_path"],
        "--event-path", lane["event_path"],
    ]
    print(f"  Launching {lane['name']}: {' '.join(cmd[:3])}...")
    proc = subprocess.Popen(cmd, cwd=str(REPO))
    print(f"    PID: {proc.pid}")
