#!/usr/bin/env python3
"""
Rotation Entry Threshold Sweep
================================
Replays the rotation events to find the optimal entry threshold.

Current: 5% threshold → 2 closes, -$0.01 net
Question: What threshold maximizes net PnL after fees/spread?

Uses the actual CFG/RAVE RS history from the runner to simulate
different entry thresholds.

Usage:
    python scripts/sweep_rotation_entry_threshold.py
"""
import json
from pathlib import Path
from itertools import combinations

ROOT = Path(__file__).resolve().parent.parent

# Config
COINS = ["CFG-USD", "RAVE-USD", "BAL-USD", "SUP-USD"]
FEE_RATE = 0.004
SPREAD_ESTIMATE = 0.001
POSITION_SIZE = 4.80
MAX_HOLD = 96
WINDOW = 96

THRESHOLDS = [0.03, 0.05, 0.08, 0.10, 0.12, 0.15, 0.20, 0.25]

def main():
    # Load the RS data from the rotation runner's state
    state_path = ROOT / "reports" / "rotation_shadow_state.json"
    if not state_path.exists():
        print("⚠️  No rotation state found. Run the rotation runner first.")
        return

    state = json.loads(state_path.read_text())
    pairs = state.get("pairs", {})

    print("=" * 72)
    print("ROTATION ENTRY THRESHOLD SWEEP")
    print("=" * 72)
    print()
    print("Note: This analyzes historical entry RS values from the runner's")
    print("event log to simulate different thresholds.")
    print()

    # Load events
    event_path = ROOT / "reports" / "rotation_shadow_events.jsonl"
    if not event_path.exists():
        print("⚠️  No rotation events found.")
        return

    events = []
    for line in event_path.read_text().strip().splitlines():
        try:
            events.append(json.loads(line))
        except:
            pass

    # Find open/close pairs
    opens = [e for e in events if e.get("action") == "open"]
    closes = [e for e in events if e.get("action") == "close"]

    print(f"Total events: {len(events)}")
    print(f"Opens: {len(opens)}, Closes: {len(closes)}")
    print()

    if not opens:
        print("No opens yet. Need more data.")
        return

    print(f"{'Threshold':>10} {'Entry RS':>10} {'PnL':>8} {'Net':>10} {'Signal?':>10}")
    print("-" * 60)

    # Analyze each close's entry RS vs threshold
    for close in closes:
        entry_rs = abs(close.get("entry_rs", 0))
        pnl = close.get("pnl", 0)
        pair = close.get("pair", "?")
        hold = close.get("hold_bars", "?")
        reason = close.get("exit_reason", "?")

        for threshold in THRESHOLDS:
            would_fire = entry_rs >= threshold
            signal_str = "✅ YES" if would_fire else "❌ no"
            net_str = f"${pnl:+.2f}" if would_fire else "—"

            if would_fire:
                print(f"{threshold:>10.0%} {entry_rs:>10.4%} {pnl:>8.2f} {net_str:>10} {signal_str:>10}")

    print()
    print("SUMMARY:")
    print()

    # Simulate each threshold
    for threshold in THRESHOLDS:
        qualifying = [c for c in closes if abs(c.get("entry_rs", 0)) >= threshold]
        total_pnl = sum(c.get("pnl", 0) for c in qualifying)
        count = len(qualifying)
        avg_pnl = total_pnl / count if count > 0 else 0

        print(f"  Threshold {threshold:.0%}: {count} closes, net ${total_pnl:+.2f}, avg ${avg_pnl:+.2f}/close")


if __name__ == "__main__":
    main()
