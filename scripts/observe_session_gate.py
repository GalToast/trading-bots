#!/usr/bin/env python3
"""
07:00 UTC Session Gate Observation.

Snapshots all FX lanes at session gate opening, reports closes, resets, PnL.
Usage:  python scripts/observe_session_gate.py [--baseline] [--snapshot] [--compare BASELINE_JSON]

--baseline:  Save current state as baseline (run BEFORE 07:00)
--snapshot:  Save current state as post-session snapshot (run at 07:15, 07:30, etc.)
--compare:   Compare snapshot to baseline, print diff report
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
REPORTS = ROOT / "reports"


FX_LANES = {
    "live_rearm_941777": "reports/penetration_lattice_live_rearm_941777_state.json",
    "live_momentum_alpha50_941778": "reports/penetration_lattice_live_momentum_alpha50_941778_state.json",
    "session_gated": "reports/penetration_lattice_shadow_fx_close_policy_mixed_session_gated_state.json",
    "fx_m15_micro_gbpusd": "reports/shadow_fx_m15_micro_gbpusd_bar_state.json",
    "fx_m15_micro_eurusd": "reports/shadow_fx_m15_micro_eurusd_bar_state.json",
    "fx_m15_micro_nzdusd": "reports/shadow_fx_m15_micro_nzdusd_bar_state.json",
    "gbpusd_tick_forward": "reports/shadow_gbpusd_tick_forward_state.json",
}


def extract_lane(name: str, path: str) -> dict | None:
    p = ROOT / path
    if not p.exists():
        return None
    state = json.loads(p.read_text(encoding="utf-8"))
    symbols = state.get("symbols", {})
    runner = state.get("runner", {})
    result = {"name": name, "heartbeat": runner.get("heartbeat_at"), "pid": runner.get("pid")}
    for sym_key, sym in symbols.items():
        closes = sym.get("realized_closes", 0)
        if isinstance(closes, list):
            closes = len(closes)
        net = sym.get("realized_net_usd", 0)
        opens = len(sym.get("open_tickets", []))
        resets = sym.get("anchor_resets", 0)
        per_close = net / closes if closes > 0 else 0
        result[sym_key] = {
            "closes": closes,
            "net": round(net, 2),
            "per_close": round(per_close, 4),
            "open": opens,
            "resets": resets,
        }
    return result


def save_snapshot(label: str) -> str:
    data = {"label": label, "time": datetime.now(timezone.utc).isoformat(), "lanes": {}}
    for name, path in FX_LANES.items():
        lane = extract_lane(name, path)
        if lane:
            data["lanes"][name] = lane
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M")
    out = REPORTS / f"session_gate_{label}_{ts}.json"
    out.write_text(json.dumps(data, indent=2), encoding="utf-8")
    print(f"Saved {out}")
    return str(out)


def compare(baseline_path: str) -> None:
    baseline = json.loads(Path(ROOT / baseline_path).read_text(encoding="utf-8"))
    print(f"=== Session Gate Observation ===")
    print(f"Baseline: {baseline['label']} at {baseline['time']}")
    print(f"")
    for lane_name, lane_data in baseline["lanes"].items():
        print(f"--- {lane_name} ---")
        for sym_key, sym in lane_data.items():
            if sym_key in ("name", "heartbeat", "pid"):
                continue
            print(f"  {sym_key}: {sym['closes']}c, ${sym['net']:.2f}, ${sym['per_close']:.4f}/c, {sym['open']} open, {sym['resets']} resets")
        print()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--baseline", action="store_true", help="Save pre-session baseline")
    parser.add_argument("--snapshot", action="store_true", help="Save post-session snapshot")
    parser.add_argument("--compare", metavar="PATH", help="Compare snapshot to baseline")
    args = parser.parse_args()

    if args.baseline:
        save_snapshot("baseline")
    elif args.snapshot:
        save_snapshot("snapshot")
    elif args.compare:
        compare(args.compare)
    else:
        # Default: print current state
        print(f"=== Current FX Lane State ({datetime.now(timezone.utc).strftime('%H:%M UTC')}) ===\n")
        for name, path in FX_LANES.items():
            lane = extract_lane(name, path)
            if lane:
                print(f"--- {name} ---")
                for sym_key, sym in lane.items():
                    if sym_key in ("name", "heartbeat", "pid"):
                        continue
                    print(f"  {sym_key}: {sym['closes']}c, ${sym['net']:.2f}, ${sym['per_close']:.4f}/c, {sym['open']} open, {sym['resets']} resets")
                print()


if __name__ == "__main__":
    main()
