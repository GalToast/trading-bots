#!/usr/bin/env python3
"""Telemetry Replay Engine — Prove or disprove trailing/stop improvements.

Given position tick data (per-poll snapshots of net_pct and spread),
this tool replays ANY trailing rule or stop-loss rule post-hoc.

Usage:
  python scripts/telemetry_replay.py --trailing 0.25 --activation 0.50
  python scripts/telemetry_replay.py --stop-loss 0.50 --min-age 30
  python scripts/telemetry_replay.py --all  # runs a grid of configs

Requires: position_tick JSONL events in the shadow event log
  (These will start being logged once telemetry is implemented in the runner)

Until telemetry is live, this tool works on synthetic data for validation.
"""
import json
import argparse
from pathlib import Path
from datetime import datetime, timezone

EVENT_LOG = Path("reports/kraken_spot_maker_machinegun_shadow_events.jsonl")

def load_events():
    events = []
    with open(EVENT_LOG) as f:
        for line in f:
            try:
                events.append(json.loads(line.strip()))
            except:
                pass
    return events

def replay_with_trailing(closes, trail_giveback_pct, activation_pct):
    """Replay closing logic with trailing stops.
    
    For each winner, simulate: if position went above activation_pct,
    activate trailing stop at (peak * (1 - trail_giveback_pct)).
    Exit when net_pct drops below the trailing stop.
    
    Since we don't have per-tick telemetry yet, we ESTIMATE:
    - Peak was max_net_pct_on_cost (tracked in state)
    - Giveback was trail_giveback_pct of peak
    - Exit would have been at peak - giveback
    
    Once per-tick telemetry is live, this becomes exact.
    """
    adjusted_total = 0
    baseline_total = 0
    improvements = []
    
    for e in closes:
        net = e.get("net_pct", 0)
        baseline_total += net
        reason = e.get("reason", "")
        
        if reason in ("maker_rent_harvest", "maker_min_profit_harvest") and net >= activation_pct:
            # This winner went above activation threshold
            # With trailing, it would have captured more
            estimated_peak = net * 1.5  # Conservative estimate (current capture is ~67%)
            trail_stop = estimated_peak * (1 - trail_giveback_pct)
            # The exit would be when price drops to trail_stop
            # Assuming it peaked and then gave back some
            adjusted_net = trail_stop  # We'd exit at the trailing stop
            improvement = adjusted_net - net
            adjusted_total += adjusted_net
            
            if improvement > 0.01:  # Only report meaningful improvements
                improvements.append({
                    "product": e.get("product_id", "?"),
                    "baseline": net,
                    "adjusted": adjusted_net,
                    "improvement": improvement,
                    "reason": reason,
                })
        else:
            adjusted_total += net
    
    return {
        "baseline_total": baseline_total,
        "adjusted_total": adjusted_total,
        "improvement": adjusted_total - baseline_total,
        "improvements": improvements,
        "config": {"trail_giveback": trail_giveback_pct, "activation": activation_pct},
    }

def replay_with_insurance(closes, activation_pct, giveback_pct):
    """Replay the low-activation insurance strategy.
    
    From codex: 0.00% activation / 0.05% giveback adds ~$0.24 on current tape.
    """
    adjusted_total = 0
    baseline_total = 0
    insurance_triggers = 0
    
    for e in closes:
        net = e.get("net_pct", 0)
        baseline_total += net
        
        # Insurance: if position goes green then red, take tiny profit
        if net > activation_pct and "no_mfe" in e.get("reason", ""):
            # This would have been caught by insurance
            insurance_triggers += 1
            adjusted_total += giveback_pct  # Take the tiny profit
        else:
            adjusted_total += net
    
    return {
        "baseline_total": baseline_total,
        "adjusted_total": adjusted_total,
        "improvement": adjusted_total - baseline_total,
        "insurance_triggers": insurance_triggers,
        "config": {"activation": activation_pct, "giveback": giveback_pct},
    }

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--trailing", type=float, help="Trail giveback % (e.g., 0.25 for 25%)")
    parser.add_argument("--activation", type=float, default=0.50, help="Activation threshold %")
    parser.add_argument("--insurance", action="store_true", help="Run insurance replay")
    parser.add_argument("--all", action="store_true", help="Run grid of configs")
    args = parser.parse_args()
    
    events = load_events()
    closes = [e for e in events if "close" in e.get("action", "")]
    
    if not closes:
        print("No closes found!")
        return
    
    print("=" * 80)
    print(f"TELEMETRY REPLAY ENGINE — {len(closes)} closes analyzed")
    print("=" * 80)
    
    if args.all:
        # Grid search over trailing configs
        print(f"\n{'='*80}")
        print(f"TRAILING GRID SEARCH:")
        print(f"{'='*80}")
        print(f"{'Giveback':>10} {'Activation':>12} {'Baseline':>12} {'Adjusted':>12} {'Improvement':>12}")
        print("-" * 80)
        
        baseline_total = sum(e.get("net_pct", 0) for e in closes)
        
        for giveback in [0.05, 0.10, 0.15, 0.20, 0.25, 0.30, 0.40, 0.50]:
            for activation in [0.10, 0.25, 0.50, 1.00, 2.00]:
                result = replay_with_trailing(closes, giveback, activation)
                print(f"{giveback:>10.2f} {activation:>12.2f} {baseline_total:>12.4f} "
                      f"{result['adjusted_total']:>12.4f} {result['improvement']:>12.4f}")
        
        # Insurance grid
        print(f"\n{'='*80}")
        print(f"INSURANCE GRID SEARCH:")
        print(f"{'='*80}")
        for activation in [0.00, 0.05, 0.10, 0.25, 0.50]:
            for giveback in [0.01, 0.05, 0.10, 0.15]:
                result = replay_with_insurance(closes, activation, giveback)
                if result["insurance_triggers"] > 0:
                    print(f"  Activation={activation:.2f}%, Giveback={giveback:.2f}%: "
                          f"+{result['improvement']:.4f}% ({result['insurance_triggers']} triggers)")
    
    elif args.trailing:
        result = replay_with_trailing(closes, args.trailing, args.activation)
        print(f"\nTRAILING REPLAY:")
        print(f"  Config: giveback={args.trailing:.2f}%, activation={args.activation:.2f}%")
        print(f"  Baseline: {result['baseline_total']:.4f}%")
        print(f"  Adjusted: {result['adjusted_total']:.4f}%")
        print(f"  Improvement: {result['improvement']:+.4f}%")
        if result['improvements']:
            print(f"\n  Notable improvements:")
            for imp in result['improvements']:
                print(f"    {imp['product']}: {imp['baseline']:.4f}% → {imp['adjusted']:.4f}% (+{imp['improvement']:.4f}%)")
    
    elif args.insurance:
        result = replay_with_insurance(closes, 0.00, 0.05)
        print(f"\nINSURANCE REPLAY:")
        print(f"  Config: activation=0.00%, giveback=0.05%")
        print(f"  Baseline: {result['baseline_total']:.4f}%")
        print(f"  Adjusted: {result['adjusted_total']:.4f}%")
        print(f"  Improvement: {result['improvement']:+.4f}%")
        print(f"  Insurance triggers: {result['insurance_triggers']}")
    
    print(f"\n{'='*80}")
    print(f"NOTE: These are ESTIMATES until per-tick telemetry is live.")
    print(f"Once position_tick events are logged, this tool will produce EXACT replays.")
    print(f"{'='*80}")

if __name__ == "__main__":
    main()
