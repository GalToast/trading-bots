#!/usr/bin/env python3
"""
Live Proof Run — First Signal Analyzer

Watches the live proof events file and provides immediate analysis when trades close.
Compares live performance against 30d backtest predictions in real-time.

Usage:
    python scripts/first_signal_analyzer.py [--watch] [--interval 10]
"""

import json
import os
import sys
import time
import argparse
from datetime import datetime, timezone
from pathlib import Path
from collections import defaultdict

REPORTS_DIR = Path(__file__).parent.parent / "reports"

# Backtest predictions for the 3 proof run coins
BACKTEST_PREDICTIONS = {
    "RAVE-USD": {
        "strategy": "supertrend",
        "predicted_wr": 56.6,
        "predicted_monthly_pnl": 1095,
        "predicted_trades_per_month": 242,
        "predicted_max_dd": 41.7,
    },
    "NOM-USD": {
        "strategy": "fibonacci",
        "predicted_wr": 46.0,
        "predicted_monthly_pnl": 766,
        "predicted_trades_per_month": 200,
        "predicted_max_dd": 30.2,
    },
    "GHST-USD": {
        "strategy": "fibonacci",
        "predicted_wr": 49.3,
        "predicted_monthly_pnl": 370,
        "predicted_trades_per_month": 136,
        "predicted_max_dd": 37.2,
    },
}


def analyze_live_state():
    """Analyze current live proof state."""
    state_path = REPORTS_DIR / "live_proof_state.json"
    if not state_path.exists():
        return None, "No live proof state file found"

    try:
        with open(state_path) as f:
            state = json.load(f)
    except (json.JSONDecodeError, FileNotFoundError):
        return None, "State file corrupted or empty"

    return state, "OK"


def analyze_live_events():
    """Analyze live proof events for trade activity."""
    events_path = REPORTS_DIR / "live_proof_events.jsonl"
    if not events_path.exists():
        return [], "No events file found"

    events = []
    try:
        with open(events_path) as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        events.append(json.loads(line))
                    except json.JSONDecodeError:
                        pass
    except Exception as e:
        return [], f"Error reading events: {e}"

    return events, f"OK — {len(events)} events"


def print_analysis():
    """Print live proof analysis."""
    # State analysis
    state, state_msg = analyze_live_state()
    events, events_msg = analyze_live_events()

    print(f"\n{'='*70}")
    print(f"  📊 LIVE PROOF RUN — FIRST SIGNAL ANALYSIS")
    print(f"  Updated: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}")
    print(f"{'='*70}\n")

    print(f"  State: {state_msg}")
    print(f"  Events: {events_msg}\n")

    if state:
        cash = state.get("total_equity", state.get("cash", 0))
        cycle = state.get("cycle", 0)
        ledgers = state.get("ledgers", state.get("coins", {}))

        print(f"  Cycle: {cycle}")
        print(f"  Total Equity: ${cash:.2f}")
        print(f"  Coins tracked: {len(ledgers)}\n")

        for coin, coin_data in ledgers.items():
            predicted = BACKTEST_PREDICTIONS.get(coin, {})
            strategy = coin_data.get("strategy", predicted.get("strategy", "unknown"))

            signals = coin_data.get("signals", 0)
            closes = coin_data.get("closes", 0)
            wins = coin_data.get("wins", 0)
            losses = coin_data.get("losses", 0)
            wr = wins / closes * 100 if closes > 0 else 0

            position = coin_data.get("position", "flat")
            is_active = position == "active"

            status = "🟢 ACTIVE" if is_active else "⚪ FLAT"
            if signals > 0 and closes == 0:
                status = "🟡 SIGNAL FIRED"
            elif signals == 0:
                status = "⏳ NO SIGNALS YET"

            print(f"  {coin} ({strategy}) — {status}")
            print(f"    Signals: {signals} | Closes: {closes} | Wins: {wins} | Losses: {losses}")
            print(f"    Live WR: {wr:.1f}% | Predicted WR: {predicted.get('predicted_wr', 'N/A')}%")
            print(f"    Cash: ${coin_data.get('cash', 0):.2f} | PnL: ${coin_data.get('pnl', 0):.2f}")
            if is_active:
                entry = coin_data.get("position_entry", 0)
                tp = coin_data.get("position_tp", 0)
                sl = coin_data.get("position_sl", 0)
                hold = coin_data.get("position_hold", 0)
                print(f"    Entry: ${entry:.6f} | TP: ${tp:.6f} | SL: ${sl:.6f} | Hold: {hold} bars")
            print()

    # Event analysis
    if events:
        entries = [e for e in events if "entry" in str(e.get("type", e.get("event", ""))).lower()]
        exits = [e for e in events if "exit" in str(e.get("type", e.get("event", ""))).lower() or
                 "close" in str(e.get("type", e.get("event", ""))).lower()]

        print(f"  Event Breakdown:")
        print(f"    Total events: {len(events)}")
        print(f"    Entries: {len(entries)}")
        print(f"    Exits: {len(exits)}")
        print()

        if exits:
            print(f"  Recent Closes:")
            for exit_event in exits[-5:]:
                coin = exit_event.get("coin", exit_event.get("product_id", "unknown"))
                pnl = exit_event.get("pnl", exit_event.get("net_pnl", 0))
                reason = exit_event.get("reason", exit_event.get("exit_reason", "unknown"))
                time_str = exit_event.get("time", exit_event.get("timestamp", "unknown"))
                result = "✅ WIN" if pnl > 0 else "❌ LOSS" if pnl < 0 else "➡️ BREAKEVEN"
                print(f"    {coin}: {result} ${pnl:.2f} ({reason}) at {time_str}")
            print()

    # Comparison to backtest
    if state and events:
        ledgers = state.get("ledgers", state.get("coins", {}))
        total_closes = sum(c.get("closes", 0) for c in ledgers.values())
        total_wins = sum(c.get("wins", 0) for c in ledgers.values())
        overall_wr = total_wins / total_closes * 100 if total_closes > 0 else 0

        print(f"  {'='*70}")
        print(f"  LIVE vs BACKTEST COMPARISON")
        print(f"  {'='*70}\n")

        if total_closes >= 5:
            print(f"  Trades: {total_closes} (need 5+ for meaningful comparison)")
            print(f"  Live WR: {overall_wr:.1f}%")
            print(f"  Expected WR: ~50.5% (weighted average of 3 strategies)")

            deviation = overall_wr - 50.5
            if abs(deviation) <= 10:
                print(f"  Deviation: {deviation:+.1f}pp ✅ Within expected range")
            elif abs(deviation) <= 15:
                print(f"  Deviation: {deviation:+.1f}pp ⚠️ Marginal deviation")
            else:
                print(f"  Deviation: {deviation:+.1f}pp 🚨 Significant deviation")
        else:
            print(f"  Trades: {total_closes} (need 5+ for meaningful comparison)")
            print(f"  ⏳ Waiting for more trades...")

    print(f"\n{'='*70}\n")


def main():
    parser = argparse.ArgumentParser(description="Live Proof Run — First Signal Analyzer")
    parser.add_argument("--watch", action="store_true", help="Continuous monitoring mode")
    parser.add_argument("--interval", type=int, default=10, help="Update interval in seconds (default: 10)")
    args = parser.parse_args()

    if args.watch:
        print(f"Starting continuous monitoring (every {args.interval}s)...")
        print(f"Press Ctrl+C to stop.\n")
        try:
            while True:
                os.system("cls" if sys.platform == "win32" else "clear")
                print_analysis()
                time.sleep(args.interval)
        except KeyboardInterrupt:
            print(f"\nMonitoring stopped.")
    else:
        print_analysis()


if __name__ == "__main__":
    main()
