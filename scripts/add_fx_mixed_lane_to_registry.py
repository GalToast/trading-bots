#!/usr/bin/env python3
"""Add FX mixed close-policy shadow lane to runner registry."""
import json
from pathlib import Path

REGISTRY = Path("configs/penetration_lattice_runner_registry.json")
WATCHDOG = Path("configs/watchdog_groups.json")

# Add to runner registry
reg = json.loads(REGISTRY.read_text(encoding="utf-8"))

new_lane = {
    "name": "shadow_fx_close_policy_mixed",
    "kind": "shadow_fx",
    "state_path": "reports/penetration_lattice_shadow_fx_close_policy_mixed_state.json",
    "event_path": "reports/penetration_lattice_shadow_fx_close_policy_mixed_events.jsonl",
    "poll_seconds": 5,
    "stale_after_seconds": 60,
    "process_match_substrings": [
        "scripts/live_penetration_lattice_tick_shadow.py",
        "reports/penetration_lattice_shadow_fx_close_policy_mixed_state.json"
    ],
    "restart_args": [
        "scripts/live_penetration_lattice_tick_shadow.py",
        "--symbols", "EURUSD", "GBPUSD",
        "--raw-rearm-variant", "rearm_lvl2_exc2",
        "--raw-symbol-overrides-path", "configs/fx_raw_symbol_overrides_close_policy_mixed.json",
        "--state-path", "reports/penetration_lattice_shadow_fx_close_policy_mixed_state.json",
        "--event-path", "reports/penetration_lattice_shadow_fx_close_policy_mixed_events.jsonl",
        "--poll-seconds", "5"
    ]
}

# Check if already exists
existing = [l["name"] for l in reg["lanes"]]
if "shadow_fx_close_policy_mixed" not in existing:
    reg["lanes"].append(new_lane)
    REGISTRY.write_text(json.dumps(reg, indent=2) + "\n", encoding="utf-8")
    print(f"Added shadow_fx_close_policy_mixed to runner registry")
else:
    print("Already in runner registry")

# Add to watchdog group
wd = json.loads(WATCHDOG.read_text(encoding="utf-8"))
if "shadow_fx_close_policy_mixed" not in wd["groups"]["shadow_watchdog"]["lanes"]:
    wd["groups"]["shadow_watchdog"]["lanes"].append("shadow_fx_close_policy_mixed")
    WATCHDOG.write_text(json.dumps(wd, indent=2) + "\n", encoding="utf-8")
    print(f"Added to shadow_watchdog group")
else:
    print("Already in shadow_watchdog group")
