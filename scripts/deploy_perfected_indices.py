#!/usr/bin/env python3
import json
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
REGISTRY_PATH = ROOT / "configs" / "penetration_lattice_runner_registry.json"

PERFECTION_SPECS = {
    "shadow_nas100_m5_warp": {
        "step": "5.36",
        "alpha": "0.5",
        "symbol": "NAS100"
    },
    "shadow_us30_m5_warp": {
        "step": "7.86",
        "alpha": "0.5",
        "symbol": "US30"
    },
    "shadow_xauusd_m5_warp": { # Newly defined God-Tier lane
        "step": "1.13",
        "alpha": "0.5",
        "symbol": "XAUUSD"
    }
}

def patch_lane(lane, spec):
    """Update restart_args with perfected coefficients."""
    args = lane.get("restart_args", [])
    new_args = []
    
    # We strip --fresh-start to preserve history if it exists
    # unless it's a new lane definition.
    preserve_history = os.path.exists(ROOT / lane["state_path"])
    
    idx = 0
    while idx < len(args):
        arg = args[idx]
        if arg == "--step":
            new_args.append("--step")
            new_args.append(spec["step"])
            idx += 2
            continue
        if arg == "--raw-close-alpha":
            new_args.append("--raw-close-alpha")
            new_args.append(spec["alpha"])
            idx += 2
            continue
        if arg == "--fresh-start":
            if preserve_history:
                # Remove it so we don't wipe state on this pivot
                print(f"  [Preserve] Stripping --fresh-start from {lane['name']} to keep history.")
            else:
                new_args.append("--fresh-start")
            idx += 1
            continue
        new_args.append(arg)
        idx += 1
    
    lane["restart_args"] = new_args
    return lane

def main():
    print(f"Loading registry from {REGISTRY_PATH}")
    with open(REGISTRY_PATH, "r", encoding="utf-8") as f:
        registry = json.load(f)
    
    lanes = registry.get("lanes", [])
    updated_count = 0
    
    # Define needed lanes
    existing_names = {l["name"] for l in lanes}
    
    for name, spec in PERFECTION_SPECS.items():
        if name in existing_names:
            print(f"Updating {name}...")
            for i, lane in enumerate(lanes):
                if lane["name"] == name:
                    lanes[i] = patch_lane(lane, spec)
                    updated_count += 1
        else:
            print(f"Creating new perfected lane: {name}...")
            # Template from NAS100
            new_lane = {
                "name": name,
                "kind": "shadow_indices" if "nas" in name or "us30" in name else "shadow_commodity",
                "state_path": f"reports/penetration_lattice_{name}_state.json",
                "event_path": f"reports/penetration_lattice_{name}_events.jsonl",
                "poll_seconds": 30,
                "stale_after_seconds": 240,
                "process_match_substrings": [
                    "scripts/live_penetration_lattice_tick_crypto_shadow.py",
                    f"reports/penetration_lattice_{name}_state.json"
                ],
                "restart_args": [
                    "scripts/live_penetration_lattice_tick_crypto_shadow.py",
                    "--symbol", spec["symbol"],
                    "--fresh-start", # Initial creation needs it
                    "--timeframe", "M5",
                    "--step", spec["step"],
                    "--raw-close-alpha", spec["alpha"],
                    "--max-open-per-side", "25",
                    "--raw-rearm-variant", "rearm_lvl2_exc2",
                    "--raw-sell-gap", "1",
                    "--raw-buy-gap", "1",
                    "--poll-seconds", "30",
                    "--max-floating-loss-usd", "-50.0",
                    "--state-path", f"reports/penetration_lattice_{name}_state.json",
                    "--event-path", f"reports/penetration_lattice_{name}_events.jsonl"
                ]
            }
            lanes.append(new_lane)
            updated_count += 1
            
    registry["lanes"] = lanes
    
    # Save a backup
    with open(str(REGISTRY_PATH) + ".bak", "w", encoding="utf-8") as f:
        json.dump(registry, f, indent=2)
        
    with open(REGISTRY_PATH, "w", encoding="utf-8") as f:
        json.dump(registry, f, indent=2)
        
    print(f"Success. Updated {updated_count} lanes. Registry backed up to .bak")

if __name__ == "__main__":
    main()
