#!/usr/bin/env python3
"""Launch GBPUSD M15 at BTC $15 scaled equivalent (2.7 pips).
Spread on GBPUSD is 0.58 pips vs 2.7 pips step = 0.21× spread/step.
Compare to BTC: $177 spread / $15 step = 11.8× spread/step.
FX tight steps should work FAR better than BTC.
"""
import subprocess
import sys
import time
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CONFIGS = ROOT / "configs"
REPORTS = ROOT / "reports"
REGISTRY = CONFIGS / "penetration_lattice_runner_registry.json"
WATCHDOG = CONFIGS / "watchdog_groups.json"

# BTC $15 = 0.0200% of BTC price → GBPUSD at same % = 2.7 pips
FX_STEP = 0.00027  # 2.7 pips

SHADOW = {
    "name": "shadow_gbpusd_m15_btc_tight15",
    "kind": "shadow_fx",
    "symbol": "GBPUSD",
    "timeframe": "M15",
    "step": FX_STEP,
    "max_open_per_side": 60,
    "close_alpha": 1.0,
    "rearm_variant": "rearm_lvl2_exc1",
    "sell_gap": 1,
    "buy_gap": 1,
    "state_path": "reports/penetration_lattice_shadow_gbpusd_m15_btc_tight15_state.json",
    "event_path": "reports/penetration_lattice_shadow_gbpusd_m15_btc_tight15_events.jsonl",
    "exec_state_path": "reports/penetration_lattice_shadow_gbpusd_m15_btc_tight15_exec_state.json",
    "exec_event_path": "reports/penetration_lattice_shadow_gbpusd_m15_btc_tight15_exec_events.jsonl",
    "magic": 941787,
    "prefix": "PLSHADOW-GBPT15",
}


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
        "python", "scripts/live_penetration_lattice_tick_crypto_shadow.py",
        "--symbol", s["symbol"],
        "--timeframe", s["timeframe"],
        "--step", str(s["step"]),
        "--max-open-per-side", str(s["max_open_per_side"]),
        "--raw-close-alpha", str(s["close_alpha"]),
        "--raw-rearm-variant", s["rearm_variant"],
        "--raw-sell-gap", str(s["sell_gap"]),
        "--raw-buy-gap", str(s["buy_gap"]),
        "--state-path", s["state_path"],
        "--event-path", s["event_path"],
        "--direct-live",
        "--direct-exec-state-path", s["exec_state_path"],
        "--direct-exec-log-path", s["exec_event_path"],
        "--live-magic", str(s["magic"]),
        "--live-comment-prefix", s["prefix"],
        "--live-volume", "0.01",
        "--max-floating-loss-usd", "-15.0",
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
        "kind": s["kind"],
        "state_path": s["state_path"],
        "event_path": s["event_path"],
        "poll_seconds": 1,
        "stale_after_seconds": 120,
        "enabled": True,
        "max_floating_loss_usd": -15.0,
        "process_match_substrings": build_process_match_substrings(s),
        "restart_args": build_args(s),
    }
    reg["lanes"].append(new_entry)
    REGISTRY.write_text(json.dumps(reg, indent=4) + "\n")
    print(f"  Added {s['name']} to registry")


def add_to_watchdog(s):
    wd = json.loads(WATCHDOG.read_text())
    ensure_watchdog_group(wd, "fx_watchdog", "FX", s["name"])
    WATCHDOG.write_text(json.dumps(wd, indent=4) + "\n")
    print(f"  Added {s['name']} to fx_watchdog")


def launch(s):
    args = build_args(s)
    print(f"\n  Launching {s['name']}...")
    print(f"  Step: {s['step']} ({s['step']/0.0001:.1f} pips)")
    
    proc = subprocess.Popen(
        args,
        creationflags=8,
        stdout=open(f"{s['name']}.out.log", "w", encoding="utf-8"),
        stderr=open(f"{s['name']}.err.log", "w", encoding="utf-8"),
        cwd=str(ROOT),
    )
    print(f"  PID: {proc.pid}")
    
    time.sleep(3)
    
    state_path = ROOT / s["state_path"]
    if state_path.exists():
        state = json.loads(state_path.read_text())
        sym = state.get("symbols", {}).get(s["symbol"], {})
        runner = state.get("runner", {})
        print(f"  State: anchor={sym.get('anchor')}, step={sym.get('base_step_px')}, hb={runner.get('heartbeat_at')}")
    else:
        print(f"  State file not yet created")
    
    return proc.pid


def main():
    print("=" * 60)
    print("GBPUSD M15 BTC $15 Tight-Step Shadow Launch")
    print(f"BTC $15 equivalent: {FX_STEP} ({FX_STEP/0.0001:.1f} pips)")
    print(f"GBPUSD spread: ~0.58 pips → spread/step = {0.58/(FX_STEP/0.0001):.2f}×")
    print(f"Compare to BTC: spread/step = 11.8×")
    print("=" * 60)
    
    add_to_registry(SHADOW)
    add_to_watchdog(SHADOW)
    pid = launch(SHADOW)
    
    print(f"\n{'=' * 60}")
    print("Launched. Monitor with: python scripts/diagnostics/check_btcusd_m15_warp_state.py")
    print(f"{'=' * 60}")


if __name__ == "__main__":
    main()
