#!/usr/bin/env python3
"""Add USDJPY M5 Warp shadow lane to the runner registry."""
import json

REGISTRY_PATH = "configs/penetration_lattice_runner_registry.json"

USDJPY_LANE = {
    "name": "shadow_usdjpy_m5_warp",
    "kind": "shadow_fx",
    "state_path": "reports/penetration_lattice_shadow_usdjpy_m5_warp_state.json",
    "event_path": "reports/penetration_lattice_shadow_usdjpy_m5_warp_events.jsonl",
    "poll_seconds": 30,
    "stale_after_seconds": 240,
    "process_match_substrings": [
        "scripts/live_penetration_lattice_tick_shadow.py",
        "reports/penetration_lattice_shadow_usdjpy_m5_warp_state.json",
    ],
    "restart_args": [
        "scripts/live_penetration_lattice_tick_shadow.py",
        "--symbols", "USDJPY",
        "--timeframe", "M5",
        "--step", "0.0519",
        "--max-open-per-side", "12",
        "--raw-close-alpha", "1.0",
        "--raw-rearm-variant", "rearm_lvl2_exc2",
        "--raw-sell-gap", "1",
        "--raw-buy-gap", "1",
        "--poll-seconds", "30",
        "--max-floating-loss-usd", "-15.0",
        "--max-lattice-window-bars", "240",
        "--state-path", "reports/penetration_lattice_shadow_usdjpy_m5_warp_state.json",
        "--event-path", "reports/penetration_lattice_shadow_usdjpy_m5_warp_events.jsonl",
    ]
}

def main():
    with open(REGISTRY_PATH, "r", encoding="utf-8") as f:
        registry = json.load(f)

    existing_names = {lane["name"] for lane in registry["lanes"]}

    if USDJPY_LANE["name"] in existing_names:
        print(f"  SKIP (already exists): {USDJPY_LANE['name']}")
    else:
        registry["lanes"].append(USDJPY_LANE)
        print(f"  ADDED: {USDJPY_LANE['name']}")

    with open(REGISTRY_PATH, "w", encoding="utf-8", newline="\n") as f:
        json.dump(registry, f, indent=2)
        f.write("\n")

    print(f"\nRegistry now has {len(registry['lanes'])} lanes.")

if __name__ == "__main__":
    main()
