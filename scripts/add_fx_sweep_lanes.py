#!/usr/bin/env python3
"""Add 1.0x ATR coefficient sweep parallel lanes for GBPUSD and USDJPY."""
import json

REGISTRY_PATH = "configs/penetration_lattice_runner_registry.json"

NEW_LANES = [
    {
        "name": "shadow_gbpusd_m5_warp_1x",
        "kind": "shadow_fx",
        "state_path": "reports/penetration_lattice_shadow_gbpusd_m5_warp_1x_state.json",
        "event_path": "reports/penetration_lattice_shadow_gbpusd_m5_warp_1x_events.jsonl",
        "poll_seconds": 30,
        "stale_after_seconds": 240,
        "process_match_substrings": [
            "scripts/live_penetration_lattice_tick_crypto_shadow.py",
            "reports/penetration_lattice_shadow_gbpusd_m5_warp_1x_state.json",
        ],
        "restart_args": [
            "scripts/live_penetration_lattice_tick_crypto_shadow.py",
            "--symbol", "GBPUSD",
            "--fresh-start",
            "--timeframe", "M5",
            "--step", "0.00028",
            "--max-open-per-side", "12",
            "--raw-close-alpha", "1.0",
            "--raw-rearm-variant", "rearm_lvl2_exc2",
            "--raw-sell-gap", "1",
            "--raw-buy-gap", "1",
            "--poll-seconds", "30",
            "--max-floating-loss-usd", "-15.0",
            "--max-lattice-window-bars", "240",
            "--state-path", "reports/penetration_lattice_shadow_gbpusd_m5_warp_1x_state.json",
            "--event-path", "reports/penetration_lattice_shadow_gbpusd_m5_warp_1x_events.jsonl",
        ]
    },
    {
        "name": "shadow_usdjpy_m5_warp_1x",
        "kind": "shadow_fx",
        "state_path": "reports/penetration_lattice_shadow_usdjpy_m5_warp_1x_state.json",
        "event_path": "reports/penetration_lattice_shadow_usdjpy_m5_warp_1x_events.jsonl",
        "poll_seconds": 30,
        "stale_after_seconds": 240,
        "process_match_substrings": [
            "scripts/live_penetration_lattice_tick_crypto_shadow.py",
            "reports/penetration_lattice_shadow_usdjpy_m5_warp_1x_state.json",
        ],
        "restart_args": [
            "scripts/live_penetration_lattice_tick_crypto_shadow.py",
            "--symbol", "USDJPY",
            "--fresh-start",
            "--timeframe", "M5",
            "--step", "0.0338",
            "--max-open-per-side", "12",
            "--raw-close-alpha", "1.0",
            "--raw-rearm-variant", "rearm_lvl2_exc2",
            "--raw-sell-gap", "1",
            "--raw-buy-gap", "1",
            "--poll-seconds", "30",
            "--max-floating-loss-usd", "-15.0",
            "--max-lattice-window-bars", "240",
            "--state-path", "reports/penetration_lattice_shadow_usdjpy_m5_warp_1x_state.json",
            "--event-path", "reports/penetration_lattice_shadow_usdjpy_m5_warp_1x_events.jsonl",
        ]
    }
]

def main():
    with open(REGISTRY_PATH, "r", encoding="utf-8") as f:
        registry = json.load(f)

    existing_names = {lane["name"] for lane in registry["lanes"]}
    added = 0

    for lane in NEW_LANES:
        if lane["name"] in existing_names:
            print(f"  SKIP: {lane['name']}")
        else:
            registry["lanes"].append(lane)
            added += 1
            print(f"  ADDED: {lane['name']}")

    if added > 0:
        with open(REGISTRY_PATH, "w", encoding="utf-8", newline="\n") as f:
            json.dump(registry, f, indent=2)
            f.write("\n")
        print("Registry updated.")

if __name__ == "__main__":
    main()
