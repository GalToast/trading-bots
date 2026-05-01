#!/usr/bin/env python3
"""Launch BTC $15 tight-step equivalent across ALL major FX pairs on M15.

BTC $15 = 0.0200% of price. Scaled to each FX pair:
- EURUSD: 0.000236 (2.4 pips)
- GBPUSD: 0.000271 (2.7 pips) — ALREADY LAUNCHED
- USDJPY: 0.0319 (3.2 pips)
- AUDUSD: 0.000143 (1.4 pips)
- NZDUSD: 0.000118 (1.2 pips)
- USDCAD: 0.000275 (2.8 pips)

Spread/step ratios are 0.2-1.0× — dramatically better than BTC's 11.8×.
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

# BTC $15 = 0.0200% of price at $74,877
BTC_PCT = 0.000200

FX_SYMBOLS = [
    ("EURUSD", 0.000236),   # 2.4 pips
    ("GBPUSD", 0.000271),   # 2.7 pips — already launched, skip
    ("USDJPY", 0.0319),     # 3.2 pips
    ("AUDUSD", 0.000143),   # 1.4 pips
    ("NZDUSD", 0.000118),   # 1.2 pips
    ("USDCAD", 0.000275),   # 2.8 pips
]

SHADOWS = []
for sym, step in FX_SYMBOLS:
    if sym == "GBPUSD":
        continue  # Already launched
    SHADOWS.append({
        "name": f"shadow_{sym.lower()}_m15_btc_tight15",
        "kind": "shadow_fx",
        "symbol": sym,
        "timeframe": "M15",
        "step": step,
        "max_open_per_side": 60,
        "close_alpha": 1.0,
        "rearm_variant": "rearm_lvl2_exc1",
        "sell_gap": 1,
        "buy_gap": 1,
        "state_path": f"reports/penetration_lattice_shadow_{sym.lower()}_m15_btc_tight15_state.json",
        "event_path": f"reports/penetration_lattice_shadow_{sym.lower()}_m15_btc_tight15_events.jsonl",
        "exec_state_path": f"reports/penetration_lattice_shadow_{sym.lower()}_m15_btc_tight15_exec_state.json",
        "exec_event_path": f"reports/penetration_lattice_shadow_{sym.lower()}_m15_btc_tight15_exec_events.jsonl",
        "magic": 941788 + SHADOWS.__len__(),
        "prefix": f"PLSHADOW-{sym[:3]}T15",
    })

# Fix magic numbers (need unique)
for i, s in enumerate(SHADOWS):
    s["magic"] = 941788 + i


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
                print(f"  Refreshed registry: {s['name']}")
            else:
                print(f"  Already in registry: {s['name']}")
            return False
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
    return True


def add_to_watchdog(s):
    wd = json.loads(WATCHDOG.read_text())
    ensure_watchdog_group(wd, "fx_watchdog", "FX", s["name"])
    WATCHDOG.write_text(json.dumps(wd, indent=4) + "\n")
    print(f"  Added {s['name']} to fx_watchdog")


def launch(s):
    args = build_args(s)
    print(f"  Launching {s['symbol']} step={s['step']}...")
    
    proc = subprocess.Popen(
        args,
        creationflags=8,
        stdout=open(f"{s['name']}.out.log", "w", encoding="utf-8"),
        stderr=open(f"{s['name']}.err.log", "w", encoding="utf-8"),
        cwd=str(ROOT),
    )
    
    time.sleep(2)
    
    state_path = ROOT / s["state_path"]
    if state_path.exists():
        try:
            state = json.loads(state_path.read_text())
            sym_data = state.get("symbols", {}).get(s["symbol"], {})
            runner = state.get("runner", {})
            print(f"    PID={proc.pid}, anchor={sym_data.get('anchor')}, step={sym_data.get('base_step_px')}, hb={runner.get('heartbeat_at')}")
        except:
            print(f"    PID={proc.pid} (state file locked)")
    else:
        print(f"    PID={proc.pid} (no state yet)")
    
    return proc.pid


def main():
    print("=" * 70)
    print("FX BTC $15 Tight-Step Multi-Symbol Launch")
    print(f"BTC $15 = {BTC_PCT*100:.4f}% of price")
    print(f"Spread/step on FX: 0.2-1.0× vs BTC 11.8×")
    print("=" * 70)
    
    launched = 0
    for s in SHADOWS:
        print(f"\n--- {s['symbol']} ({s['step']} = {s['step']/0.0001:.1f} pips) ---")
        added = add_to_registry(s)
        if added:
            add_to_watchdog(s)
            launch(s)
            launched += 1
        else:
            print(f"  Skipping launch (already registered)")
    
    print(f"\n{'=' * 70}")
    print(f"Launched {launched} FX shadows. Total running: {launched + 1} (including GBPUSD)")
    print(f"{'=' * 70}")


if __name__ == "__main__":
    main()
