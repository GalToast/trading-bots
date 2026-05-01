#!/usr/bin/env python3
"""Add remaining 4 FX shadow lanes to the runner registry to complete FX scaling."""
import json
import subprocess

REGISTRY_PATH = "configs/penetration_lattice_runner_registry.json"

CONFIGS = [
    ("AUDUSD", "0.00035"),
    ("EURUSD", "0.00036"),
    ("NZDUSD", "0.00029"),
    ("USDCAD", "0.00041"),
]

def create_lane(sym, step):
    sym_lower = sym.lower()
    return {
        "name": f"shadow_{sym_lower}_m5_warp",
        "kind": "shadow_fx",
        "state_path": f"reports/penetration_lattice_shadow_{sym_lower}_m5_warp_state.json",
        "event_path": f"reports/penetration_lattice_shadow_{sym_lower}_m5_warp_events.jsonl",
        "poll_seconds": 30,
        "stale_after_seconds": 240,
        "process_match_substrings": [
            "scripts/live_penetration_lattice_tick_crypto_shadow.py",
            f"reports/penetration_lattice_shadow_{sym_lower}_m5_warp_state.json",
        ],
        "restart_args": [
            "scripts/live_penetration_lattice_tick_crypto_shadow.py",
            "--symbol", sym,
            "--fresh-start",
            "--timeframe", "M5",
            "--step", step,
            "--max-open-per-side", "12",
            "--raw-close-alpha", "1.0",
            "--raw-rearm-variant", "rearm_lvl2_exc2",
            "--raw-sell-gap", "1",
            "--raw-buy-gap", "1",
            "--poll-seconds", "30",
            "--max-floating-loss-usd", "-15.0",
            "--max-lattice-window-bars", "240",
            "--state-path", f"reports/penetration_lattice_shadow_{sym_lower}_m5_warp_state.json",
            "--event-path", f"reports/penetration_lattice_shadow_{sym_lower}_m5_warp_events.jsonl",
        ]
    }

def main():
    with open(REGISTRY_PATH, "r", encoding="utf-8") as f:
        registry = json.load(f)

    existing_names = {lane["name"] for lane in registry["lanes"]}
    added = []

    for sym, step in CONFIGS:
        lane = create_lane(sym, step)
        if lane["name"] in existing_names:
            print(f"  SKIP: {lane['name']}")
        else:
            registry["lanes"].append(lane)
            added.append(lane)
            print(f"  ADDED: {lane['name']}")

    if added:
        with open(REGISTRY_PATH, "w", encoding="utf-8", newline="\n") as f:
            json.dump(registry, f, indent=2)
            f.write("\n")
        print("Registry updated.")
        
        # update watchdog
        with open('configs/watchdog_groups.json', 'r', encoding='utf-8') as f:
            groups = json.load(f)
        for added_lane in added:
            if added_lane["name"] not in groups['groups']['fx_watchdog']['lanes']:
                groups['groups']['fx_watchdog']['lanes'].append(added_lane["name"])
        with open('configs/watchdog_groups.json', 'w', encoding='utf-8', newline='\n') as f:
            json.dump(groups, f, indent=2)
            f.write('\n')
        print("Watchdog updated.")

if __name__ == "__main__":
    main()
