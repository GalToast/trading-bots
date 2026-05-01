#!/usr/bin/env python3
"""Add XAUUSD, NAS100, US30 M5 Warp shadow lanes to the runner registry."""
import json

REGISTRY_PATH = "configs/penetration_lattice_runner_registry.json"

NEW_LANES = [
    {
        "name": "shadow_xauusd_m5_warp",
        "kind": "shadow_crypto",
        "state_path": "reports/penetration_lattice_shadow_xauusd_m5_warp_state.json",
        "event_path": "reports/penetration_lattice_shadow_xauusd_m5_warp_events.jsonl",
        "poll_seconds": 30,
        "stale_after_seconds": 240,
        "process_match_substrings": [
            "scripts/live_penetration_lattice_tick_crypto_shadow.py",
            "reports/penetration_lattice_shadow_xauusd_m5_warp_state.json",
        ],
        "restart_args": [
            "scripts/live_penetration_lattice_tick_crypto_shadow.py",
            "--symbol", "XAUUSD",
            "--fresh-start",
            "--timeframe", "M5",
            "--step", "9.19",
            "--max-open-per-side", "12",
            "--raw-close-alpha", "1.0",
            "--raw-rearm-variant", "rearm_lvl2_exc2",
            "--raw-sell-gap", "1",
            "--raw-buy-gap", "1",
            "--poll-seconds", "30",
            "--max-floating-loss-usd", "-15.0",
            "--max-lattice-window-bars", "240",
            "--state-path", "reports/penetration_lattice_shadow_xauusd_m5_warp_state.json",
            "--event-path", "reports/penetration_lattice_shadow_xauusd_m5_warp_events.jsonl",
        ],
    },
    {
        "name": "shadow_nas100_m5_warp",
        "kind": "shadow_crypto",
        "state_path": "reports/penetration_lattice_shadow_nas100_m5_warp_state.json",
        "event_path": "reports/penetration_lattice_shadow_nas100_m5_warp_events.jsonl",
        "poll_seconds": 30,
        "stale_after_seconds": 240,
        "process_match_substrings": [
            "scripts/live_penetration_lattice_tick_crypto_shadow.py",
            "reports/penetration_lattice_shadow_nas100_m5_warp_state.json",
        ],
        "restart_args": [
            "scripts/live_penetration_lattice_tick_crypto_shadow.py",
            "--symbol", "NAS100",
            "--fresh-start",
            "--timeframe", "M5",
            "--step", "24.77",
            "--max-open-per-side", "12",
            "--raw-close-alpha", "1.0",
            "--raw-rearm-variant", "rearm_lvl2_exc2",
            "--raw-sell-gap", "1",
            "--raw-buy-gap", "1",
            "--poll-seconds", "30",
            "--max-floating-loss-usd", "-15.0",
            "--max-lattice-window-bars", "240",
            "--state-path", "reports/penetration_lattice_shadow_nas100_m5_warp_state.json",
            "--event-path", "reports/penetration_lattice_shadow_nas100_m5_warp_events.jsonl",
        ],
    },
    {
        "name": "shadow_us30_m5_warp",
        "kind": "shadow_crypto",
        "state_path": "reports/penetration_lattice_shadow_us30_m5_warp_state.json",
        "event_path": "reports/penetration_lattice_shadow_us30_m5_warp_events.jsonl",
        "poll_seconds": 30,
        "stale_after_seconds": 240,
        "process_match_substrings": [
            "scripts/live_penetration_lattice_tick_crypto_shadow.py",
            "reports/penetration_lattice_shadow_us30_m5_warp_state.json",
        ],
        "restart_args": [
            "scripts/live_penetration_lattice_tick_crypto_shadow.py",
            "--symbol", "US30",
            "--fresh-start",
            "--timeframe", "M5",
            "--step", "40.69",
            "--max-open-per-side", "12",
            "--raw-close-alpha", "1.0",
            "--raw-rearm-variant", "rearm_lvl2_exc2",
            "--raw-sell-gap", "1",
            "--raw-buy-gap", "1",
            "--poll-seconds", "30",
            "--max-floating-loss-usd", "-15.0",
            "--max-lattice-window-bars", "240",
            "--state-path", "reports/penetration_lattice_shadow_us30_m5_warp_state.json",
            "--event-path", "reports/penetration_lattice_shadow_us30_m5_warp_events.jsonl",
        ],
    },
]


def main():
    with open(REGISTRY_PATH, "r", encoding="utf-8") as f:
        registry = json.load(f)

    existing_names = {lane["name"] for lane in registry["lanes"]}
    added = []

    for lane in NEW_LANES:
        if lane["name"] in existing_names:
            print(f"  SKIP (already exists): {lane['name']}")
            continue
        registry["lanes"].append(lane)
        added.append(lane["name"])
        print(f"  ADDED: {lane['name']}")

    with open(REGISTRY_PATH, "w", encoding="utf-8", newline="\n") as f:
        json.dump(registry, f, indent=2)
        f.write("\n")

    total = len(registry["lanes"])
    print(f"\nRegistry now has {total} lanes. Added {len(added)} new lanes.")


if __name__ == "__main__":
    main()
