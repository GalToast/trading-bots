#!/usr/bin/env python3
"""Launch FX M15 Warp shadow lanes for GBPUSD and USDJPY.

Rationale: FX M5 produced 0 closes in 60+ min. M15 bars have 3x the range,
providing enough volatility for mean-reversion closes (same reason ETH M15
at $17.97/c dominates ETH M5).

Steps computed from M15 ATR:
- GBPUSD M15 ATR ~0.00070, step at 1.0x = 0.00070
- USDJPY M15 ATR ~0.080, step at 1.0x = 0.080

Usage: python scripts/launch_fx_m15_warp_shadows.py
Registry entries added to: configs/penetration_lattice_runner_registry.json
"""

import json
import os
import sys

REPO = os.path.dirname(os.path.dirname(__file__))
REGISTRY = os.path.join(REPO, "configs", "penetration_lattice_runner_registry.json")

# FX M15 Warp lane definitions
FX_M15_LANES = [
    {
        "name": "shadow_gbpusd_m15_warp",
        "kind": "shadow_fx",
        "symbol": "GBPUSD",
        "timeframe": "M15",
        "step": 0.00070,  # 1.0x M15 ATR
        "max_open_per_side": 12,
        "max_floating_loss_usd": -15.0,
    },
    {
        "name": "shadow_usdjpy_m15_warp",
        "kind": "shadow_fx",
        "symbol": "USDJPY",
        "timeframe": "M15",
        "step": 0.080,  # 1.0x M15 ATR
        "max_open_per_side": 12,
        "max_floating_loss_usd": -15.0,
    },
]


def add_lane(registry, lane_def):
    """Add a lane to the registry if it doesn't exist, update if it does."""
    name = lane_def["name"]
    
    # Check for existing
    for i, entry in enumerate(registry):
        if entry.get("name") == name:
            print(f"  {name}: already exists, updating...")
            entry["restart_args"] = build_restart_args(lane_def)
            entry["enabled"] = True
            return False  # Updated, not added
    
    # New entry
    entry = {
        "name": name,
        "kind": lane_def["kind"],
        "state_path": f"reports/penetration_lattice_{name}_state.json",
        "event_path": f"reports/penetration_lattice_{name}_events.jsonl",
        "poll_seconds": 30,
        "stale_after_seconds": 240,
        "process_match_substrings": [
            "scripts/live_penetration_lattice_tick_crypto_shadow.py",
            f"reports/penetration_lattice_{name}_state.json"
        ],
        "restart_args": build_restart_args(lane_def),
        "enabled": True,
    }
    registry.append(entry)
    return True


def build_restart_args(lane_def):
    name = lane_def["name"]
    return [
        "scripts/live_penetration_lattice_tick_crypto_shadow.py",
        "--symbol", lane_def["symbol"],
        "--fresh-start",
        "--timeframe", lane_def["timeframe"],
        "--step", str(lane_def["step"]),
        "--max-open-per-side", str(lane_def["max_open_per_side"]),
        "--raw-close-alpha", "1.0",
        "--raw-rearm-variant", "rearm_lvl2_exc2",
        "--raw-sell-gap", "1",
        "--raw-buy-gap", "1",
        "--poll-seconds", "30",
        "--max-floating-loss-usd", str(lane_def["max_floating_loss_usd"]),
        "--max-lattice-window-bars", "240",
        "--state-path", f"reports/penetration_lattice_{name}_state.json",
        "--event-path", f"reports/penetration_lattice_{name}_events.jsonl",
    ]


def main():
    with open(REGISTRY, encoding="utf-8") as f:
        data = json.load(f)
    
    registry = data.get("registry", data.get("lanes", data.get("entries", [])))
    
    print("FX M15 Warp Shadow Lanes:")
    added = 0
    updated = 0
    
    for lane_def in FX_M15_LANES:
        is_new = add_lane(registry, lane_def)
        if is_new:
            print(f"  ✅ {lane_def['name']}: ADDED (step={lane_def['step']}, {lane_def['timeframe']})")
            added += 1
        else:
            print(f"  🔄 {lane_def['name']}: UPDATED")
            updated += 1
    
    # Save
    with open(REGISTRY, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    
    print(f"\nRegistry updated: {added} added, {updated} updated")
    print(f"Saved: {REGISTRY}")
    print("\nNext steps:")
    print("1. Add lanes to fx_watchdog group in configs/watchdog_groups.json")
    print("2. Restart fx_watchdog loop to pick up new lanes")
    print("3. Monitor for first closes (expect within 20-30 min)")
    
    # Also update watchdog groups
    watchdog_path = os.path.join(REPO, "configs", "watchdog_groups.json")
    if os.path.exists(watchdog_path):
        with open(watchdog_path, encoding="utf-8") as f:
            wd = json.load(f)
        
        groups = wd.get("groups", wd)
        fx_group = groups.get("fx_watchdog", groups.get("fx_watchdog", {}))
        lanes = fx_group.get("lanes", [])
        
        new_lanes = []
        for lane_def in FX_M15_LANES:
            if lane_def["name"] not in lanes:
                lanes.append(lane_def["name"])
                new_lanes.append(lane_def["name"])
        
        if new_lanes:
            with open(watchdog_path, "w", encoding="utf-8") as f:
                json.dump(wd, f, indent=2, ensure_ascii=False)
            print(f"\nWatchdog groups updated: {', '.join(new_lanes)} added to fx_watchdog")
        else:
            print("\nWatchdog groups: lanes already present")
    
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
