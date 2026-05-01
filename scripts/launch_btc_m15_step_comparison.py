#!/usr/bin/env python3
"""Launch BTC M15 step comparison shadows: $15 vs $20.

Both use the same tick-native crypto shadow runner as the live $75 lane.
Gate: 25+ closes, positive net, resets < 50% of closes.
"""
import json
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CONFIGS = ROOT / "configs"
REPORTS = ROOT / "reports"
REGISTRY = CONFIGS / "penetration_lattice_runner_registry.json"
WATCHDOG = CONFIGS / "watchdog_groups.json"

SHADOWS = [
    {
        "name": "shadow_btcusd_m15_step15",
        "step": 15.0,
        "state_path": "reports/penetration_lattice_shadow_btcusd_m15_step15_state.json",
        "event_path": "reports/penetration_lattice_shadow_btcusd_m15_step15_events.jsonl",
        "exec_state_path": "reports/penetration_lattice_shadow_btcusd_m15_step15_exec_state.json",
        "exec_event_path": "reports/penetration_lattice_shadow_btcusd_m15_step15_exec_events.jsonl",
        "magic": 941785,
    },
    {
        "name": "shadow_btcusd_m15_step20",
        "step": 20.0,
        "state_path": "reports/penetration_lattice_shadow_btcusd_m15_step20_state.json",
        "event_path": "reports/penetration_lattice_shadow_btcusd_m15_step20_events.jsonl",
        "exec_state_path": "reports/penetration_lattice_shadow_btcusd_m15_step20_exec_state.json",
        "exec_event_path": "reports/penetration_lattice_shadow_btcusd_m15_step20_exec_events.jsonl",
        "magic": 941786,
    },
]


def build_process_match_substrings(s):
    return [
        "scripts/live_penetration_lattice_tick_crypto_shadow.py",
        s["state_path"],
    ]


def ensure_watchdog_group(wd, group_name, label, lane_name):
    groups = wd.setdefault("groups", {})
    group = groups.setdefault(group_name, {"label": label, "lanes": []})
    group.setdefault("label", label)
    lanes = group.setdefault("lanes", [])
    if lane_name not in lanes:
        lanes.append(lane_name)
    wd[group_name] = {"lanes": list(lanes)}


def build_args(s):
    return [
        "python",
        "scripts/live_penetration_lattice_tick_crypto_shadow.py",
        "--symbol", "BTCUSD",
        "--timeframe", "M15",
        "--step", str(s["step"]),
        "--max-open-per-side", "60",
        "--raw-close-alpha", "1.0",
        "--raw-rearm-variant", "rearm_lvl2_exc1",
        "--raw-sell-gap", "1",
        "--raw-buy-gap", "1",
        "--state-path", s["state_path"],
        "--event-path", s["event_path"],
        "--direct-live",
        "--direct-exec-state-path", s["exec_state_path"],
        "--direct-exec-log-path", s["exec_event_path"],
        "--live-magic", str(s["magic"]),
        "--live-comment-prefix", f"PLSHADOW-S{s['step']:.0f}",
        "--live-volume", "0.01",
        "--max-floating-loss-usd", "-3500.0",
        "--poll-seconds", "1",
        "--fresh-start",
    ]


def add_to_registry(s):
    reg = json.loads(REGISTRY.read_text())
    for entry in reg["lanes"]:
        if entry["name"] == s["name"]:
            updated = False
            process_match = build_process_match_substrings(s)
            if entry.get("process_match_substrings") != process_match:
                entry["process_match_substrings"] = process_match
                updated = True
            if updated:
                REGISTRY.write_text(json.dumps(reg, indent=4) + "\n")
                print(f"  Refreshed registry entry for {s['name']}")
            else:
                print(f"  Registry entry already exists for {s['name']}")
            return
    new_entry = {
        "name": s["name"],
        "kind": "shadow_crypto",
        "state_path": s["state_path"],
        "event_path": s["event_path"],
        "poll_seconds": 1,
        "stale_after_seconds": 120,
        "enabled": True,
        "max_floating_loss_usd": -3500.0,
        "process_match_substrings": build_process_match_substrings(s),
        "restart_args": build_args(s),
    }
    reg["lanes"].append(new_entry)
    REGISTRY.write_text(json.dumps(reg, indent=4) + "\n")
    print(f"  Added {s['name']} to registry")


def add_to_watchdog(s):
    wd = json.loads(WATCHDOG.read_text())
    ensure_watchdog_group(wd, "crypto_watchdog", "Crypto", s["name"])
    WATCHDOG.write_text(json.dumps(wd, indent=4) + "\n")
    print(f"  Added {s['name']} to crypto_watchdog")


def launch(s):
    args = build_args(s)
    print(f"\n  Launching {s['name']} (step=${s['step']})...")
    print(f"  Command: {' '.join(args[:5])} ...")
    
    # Use subprocess to launch detached
    kwargs = {
        "cwd": str(ROOT),
        "creationflags": 8 if sys.platform == "win32" else 0,  # DETACHED_PROCESS
    }
    proc = subprocess.Popen(
        args,
        stdout=open(s["name"] + ".out.log", "w", encoding="utf-8"),
        stderr=open(s["name"] + ".err.log", "w", encoding="utf-8"),
        **kwargs,
    )
    print(f"  PID: {proc.pid}")
    
    # Wait a moment for startup
    time.sleep(3)
    
    # Check state file
    state_path = ROOT / s["state_path"]
    if state_path.exists():
        state = json.loads(state_path.read_text())
        btc = state.get("symbols", {}).get("BTCUSD", {})
        runner = state.get("runner", {})
        print(f"  State: anchor={btc.get('anchor')}, heartbeat={runner.get('heartbeat_at')}")
    else:
        print(f"  State file not yet created (may take a moment)")
    
    return proc.pid


def main():
    print("=" * 60)
    print("BTC M15 Step Comparison Shadow Launch")
    print("=" * 60)
    
    for s in SHADOWS:
        print(f"\n--- {s['name']} (step=${s['step']}) ---")
        add_to_registry(s)
        add_to_watchdog(s)
        launch(s)
    
    print("\n" + "=" * 60)
    print("Both shadows launched. Monitor with:")
    print("  python scripts/quick_lane_audit.py")
    print("  python scripts/read_live_states.py")
    print("=" * 60)


if __name__ == "__main__":
    main()
