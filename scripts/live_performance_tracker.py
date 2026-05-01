#!/usr/bin/env python3
"""
Live Performance Tracker — Closes the loop: backtest → deploy → monitor → validate.

Reads the isolated runner's state/events in real-time, compares actual live PnL
vs backtest predictions, tracks strategy performance, and generates alerts.

Usage:
    python scripts/live_performance_tracker.py              # One-shot check
    python scripts/live_performance_tracker.py --watch       # Continuous monitoring
    python scripts/live_performance_tracker.py --report      # Generate report
"""
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
STATE_PATH = ROOT / "reports" / "multi_coin_isolated_state.json"
EVENT_PATH = ROOT / "reports" / "multi_coin_isolated_events.jsonl"
EDGE_REGISTRY_PATH = ROOT / "reports" / "edge_registry.json"
TRACKER_OUTPUT = ROOT / "reports" / "live_performance_tracker.json"

# Backtest predictions per coin (from 30d validations at $5.33/coin)
BACKTEST_PREDICTIONS = {
    "RAVE-USD": {"strategy": "supertrend", "predicted_pnl": 42.0, "predicted_wr": 52.2},
    "NOM-USD": {"strategy": "fibonacci", "predicted_pnl": 101.0, "predicted_wr": 68.0},
    "GHST-USD": {"strategy": "supertrend", "predicted_pnl": 50.0, "predicted_wr": 48.0},
    "TRU-USD": {"strategy": "fibonacci", "predicted_pnl": 23.0, "predicted_wr": 52.0},
    "SUP-USD": {"strategy": "fibonacci", "predicted_pnl": 3.0, "predicted_wr": 43.0},
    "A8-USD": {"strategy": "momentum", "predicted_pnl": 6.0, "predicted_wr": 53.0},
    "BAL-USD": {"strategy": "momentum", "predicted_pnl": 4.0, "predicted_wr": 52.0},
    "CFG-USD": {"strategy": "momentum", "predicted_pnl": 3.0, "predicted_wr": 41.0},
    "IOTX-USD": {"strategy": "momentum", "predicted_pnl": -0.5, "predicted_wr": 35.0},
}

ALERT_WR_DEVIATION = 10  # Alert if live WR deviates >10pp from predicted


def load_state():
    if not STATE_PATH.exists():
        return None
    try:
        with open(STATE_PATH) as f:
            return json.load(f)
    except Exception:
        return None


def load_events():
    if not EVENT_PATH.exists():
        return []
    events = []
    with open(EVENT_PATH) as f:
        for line in f:
            try:
                events.append(json.loads(line.strip()))
            except Exception:
                pass
    return events


def load_edge_registry():
    if not EDGE_REGISTRY_PATH.exists():
        return None
    try:
        with open(EDGE_REGISTRY_PATH) as f:
            return json.load(f)
    except Exception:
        return None


def analyze_live_performance(state, events):
    if not state or not events:
        return None

    # Filter open/close events
    opens = [e for e in events if e.get("action") == "open"]
    closes = [e for e in events if e.get("action") == "close"]

    # Per-coin analysis
    coin_analysis = {}
    for coin in BACKTEST_PREDICTIONS:
        coin_opens = [e for e in opens if e.get("coin") == coin]
        coin_closes = [e for e in closes if e.get("coin") == coin]

        live_state = state.get("ledgers", {}).get(coin, {})
        signals = live_state.get("signals", 0)
        closes_count = live_state.get("closes", 0)
        wins = live_state.get("wins", 0)
        losses = live_state.get("losses", 0)
        pnl = live_state.get("pnl", 0)
        wr = live_state.get("win_rate", 0)
        position = live_state.get("position", "flat")

        pred = BACKTEST_PREDICTIONS.get(coin, {})
        pred_pnl = pred.get("predicted_pnl", 0)
        pred_wr = pred.get("predicted_wr", 0)

        # Calculate deviation
        wr_deviation = wr - pred_wr if wr > 0 else 0
        pnl_ratio = pnl / pred_pnl if pred_pnl != 0 else 0

        # Alert status
        alert = None
        if closes_count >= 5 and abs(wr_deviation) > ALERT_WR_DEVIATION:
            alert = "HIGH" if wr_deviation < -ALERT_WR_DEVIATION else "INFO"

        coin_analysis[coin] = {
            "strategy": pred.get("strategy", "unknown"),
            "live_signals": signals,
            "live_closes": closes_count,
            "live_wins": wins,
            "live_losses": losses,
            "live_wr": wr,
            "live_pnl": pnl,
            "live_position": position,
            "predicted_pnl": pred_pnl,
            "predicted_wr": pred_wr,
            "wr_deviation": round(wr_deviation, 1),
            "pnl_ratio": round(pnl_ratio, 2),
            "alert": alert,
        }

    # Overall stats
    total_signals = sum(v["live_signals"] for v in coin_analysis.values())
    total_closes = sum(v["live_closes"] for v in coin_analysis.values())
    total_wins = sum(v["live_wins"] for v in coin_analysis.values())
    total_pnl = sum(v["live_pnl"] for v in coin_analysis.values())
    total_pred_pnl = sum(v["predicted_pnl"] for v in coin_analysis.values())
    overall_wr = total_wins / max(total_closes, 1) * 100
    overall_pred_wr = sum(v["predicted_wr"] * v["live_closes"] for v in coin_analysis.values()) / max(total_closes, 1)

    alerts = [f"{coin}: WR {v['live_wr']}% vs predicted {v['predicted_wr']}% ({v['wr_deviation']:+.1f}pp)"
              for coin, v in coin_analysis.items() if v["alert"]]

    return {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "runner_cycle": state.get("cycle", 0),
        "total_equity": state.get("total_equity", 0),
        "total_pnl": state.get("total_pnl", 0),
        "total_signals": total_signals,
        "total_closes": total_closes,
        "total_wins": total_wins,
        "overall_wr": round(overall_wr, 1),
        "overall_pred_wr": round(overall_pred_wr, 1),
        "total_live_pnl": round(total_pnl, 2),
        "total_predicted_pnl": round(total_pred_pnl, 2),
        "pnl_ratio": round(total_pnl / total_pred_pnl, 2) if total_pred_pnl != 0 else 0,
        "alerts": alerts,
        "coins": coin_analysis,
    }


def print_report(perf):
    if not perf:
        print("No live performance data available. Is the runner running?", flush=True)
        return

    print("=" * 80, flush=True)
    print("LIVE PERFORMANCE TRACKER")
    print("=" * 80, flush=True)
    print(f"Runner cycle: {perf['runner_cycle']}")
    print(f"Total equity: ${perf['total_equity']:.2f}")
    print(f"Total PnL: ${perf['total_pnl']:+.2f}")
    print(f"Signals: {perf['total_signals']}, Closes: {perf['total_closes']}, WR: {perf['overall_wr']:.1f}%")
    print(f"Predicted WR: {perf['overall_pred_wr']:.1f}%")
    print(f"Live PnL vs Predicted: ${perf['total_live_pnl']:+.2f} vs ${perf['total_predicted_pnl']:+.2f} (ratio: {perf['pnl_ratio']:.2f}x)")

    if perf["alerts"]:
        print(f"\n⚠️ ALERTS ({len(perf['alerts'])}):", flush=True)
        for alert in perf["alerts"]:
            print(f"  {alert}", flush=True)

    print(f"\n{'Coin':<12} {'Strategy':<15} {'Signals':<8} {'Closes':<8} {'WR%':<7} {'PnL':<8} {'PredWR':<7} {'Dev':<7} {'Pos':<8}", flush=True)
    print("-" * 80, flush=True)
    for coin, v in perf["coins"].items():
        alert_flag = "⚠️" if v["alert"] else ""
        print(f"{coin:<12} {v['strategy']:<15} {v['live_signals']:<8} {v['live_closes']:<8} "
              f"{v['live_wr']:<7.1f} ${v['live_pnl']:<7.2f} {v['predicted_wr']:<7.1f} "
              f"{v['wr_deviation']:<+.1f} {v['live_position']:<8} {alert_flag}", flush=True)


def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--watch", action="store_true", help="Continuous monitoring")
    parser.add_argument("--interval", type=int, default=30, help="Watch interval in seconds")
    parser.add_argument("--report", action="store_true", help="Save report to file")
    args = parser.parse_args()

    if args.watch:
        print(f"Watching isolated runner every {args.interval}s... (Ctrl+C to stop)", flush=True)
        try:
            while True:
                state = load_state()
                events = load_events()
                perf = analyze_live_performance(state, events)
                print("\n" + "=" * 80, flush=True)
                print(f"Check at {datetime.now(timezone.utc).strftime('%H:%M:%S UTC')}", flush=True)
                print_report(perf)
                if args.report and perf:
                    with open(TRACKER_OUTPUT, "w") as f:
                        json.dump(perf, f, indent=2, default=str)
                    print(f"\nReport saved: {TRACKER_OUTPUT}", flush=True)
                time.sleep(args.interval)
        except KeyboardInterrupt:
            print("\nStopped.", flush=True)
    else:
        state = load_state()
        events = load_events()
        perf = analyze_live_performance(state, events)
        print_report(perf)
        if args.report and perf:
            with open(TRACKER_OUTPUT, "w") as f:
                json.dump(perf, f, indent=2, default=str)
            print(f"\nReport saved: {TRACKER_OUTPUT}", flush=True)


if __name__ == "__main__":
    main()
