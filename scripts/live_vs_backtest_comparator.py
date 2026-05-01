#!/usr/bin/env python3
"""
Live vs Backtest Performance Comparator

Compares actual live runner performance against 30d backtest predictions.
Answers: "Is live performance consistent with our backtests, or is something wrong?"

Usage:
    python scripts/live_vs_backtest_comparator.py [--state-path PATH] [--events-path PATH]
"""

import json
import math
import os
import sys
import argparse
from pathlib import Path
from datetime import datetime, timezone
from collections import defaultdict

REPORTS_DIR = Path(__file__).parent.parent / "reports"

# Backtest predictions from 30d validation
BACKTEST_PREDICTIONS = {
    "RAVE-USD": {
        "strategy": "supertrend",
        "predicted_pnl": 1095,  # monthly
        "predicted_wr": 56.6,
        "predicted_trades_per_month": 242,
        "predicted_max_dd": 41.7,
        "predicted_sharpe": 0.28,
        "predicted_profit_factor": 4.40,
    },
    "NOM-USD": {
        "strategy": "fibonacci",
        "predicted_pnl": 766,
        "predicted_wr": 46.0,
        "predicted_trades_per_month": 200,
        "predicted_max_dd": 30.2,
        "predicted_sharpe": 0.20,
    },
    "TRU-USD": {
        "strategy": "momentum",
        "predicted_pnl": 418,
        "predicted_wr": 53.7,
        "predicted_trades_per_month": 95,
        "predicted_max_dd": 22.8,
        "predicted_sharpe": 0.34,
        "predicted_profit_factor": 2.45,
    },
    "GHST-USD": {
        "strategy": "fibonacci",
        "predicted_pnl": 370,
        "predicted_wr": 49.3,
        "predicted_trades_per_month": 136,
        "predicted_max_dd": 37.2,
        "predicted_sharpe": 0.25,
    },
    "SUP-USD": {
        "strategy": "fibonacci",
        "predicted_pnl": 104,
        "predicted_wr": 51.8,
        "predicted_trades_per_month": 56,
        "predicted_max_dd": 16.0,
        "predicted_sharpe": 0.38,
        "predicted_profit_factor": 2.62,
    },
}


def load_live_state(state_path):
    """Load live runner state."""
    if not state_path.exists():
        return None
    with open(state_path) as f:
        return json.load(f)


def load_live_events(events_path):
    """Load live events and compute per-coin metrics."""
    if not events_path.exists():
        return {}

    events = []
    with open(events_path) as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    events.append(json.loads(line))
                except json.JSONDecodeError:
                    pass

    # Compute per-coin metrics from events
    coin_metrics = defaultdict(lambda: {
        "trades": 0, "wins": 0, "losses": 0,
        "total_pnl": 0.0, "entry_prices": [], "exit_prices": [],
        "max_equity": 0, "min_equity": float("inf"),
        "equity_curve": [],
    })

    current_equity = 48.0  # Starting cash
    for event in events:
        coin = event.get("coin", event.get("product_id", ""))
        event_type = event.get("type", event.get("event", ""))
        pnl = event.get("pnl", event.get("net_pnl", 0))

        if coin not in coin_metrics:
            continue

        if "entry" in event_type.lower():
            entry_price = event.get("entry_price", event.get("price", 0))
            coin_metrics[coin]["entry_prices"].append(entry_price)

        elif "exit" in event_type.lower() or "close" in event_type.lower():
            coin_metrics[coin]["trades"] += 1
            coin_metrics[coin]["total_pnl"] += pnl
            current_equity += pnl

            if pnl > 0:
                coin_metrics[coin]["wins"] += 1
            elif pnl < 0:
                coin_metrics[coin]["losses"] += 1

            coin_metrics[coin]["equity_curve"].append(current_equity)
            coin_metrics[coin]["max_equity"] = max(coin_metrics[coin]["max_equity"], current_equity)
            coin_metrics[coin]["min_equity"] = min(coin_metrics[coin]["min_equity"], current_equity)

    # Convert to regular dicts and compute derived metrics
    result = {}
    for coin, m in coin_metrics.items():
        total_trades = m["trades"]
        win_rate = m["wins"] / total_trades * 100 if total_trades > 0 else 0
        avg_pnl = m["total_pnl"] / total_trades if total_trades > 0 else 0

        # Max drawdown from equity curve
        max_dd = 0
        peak = 48.0
        for eq in m["equity_curve"]:
            if eq > peak:
                peak = eq
            dd = (peak - eq) / peak if peak > 0 else 0
            max_dd = max(max_dd, dd)

        result[coin] = {
            "trades": total_trades,
            "wins": m["wins"],
            "losses": m["losses"],
            "win_rate": round(win_rate, 1),
            "total_pnl": round(m["total_pnl"], 2),
            "avg_pnl_per_trade": round(avg_pnl, 2),
            "max_drawdown_pct": round(max_dd * 100, 1),
        }

    return result


def statistical_significance_test(live_wr, live_trades, predicted_wr):
    """
    Test if live WR is significantly different from predicted WR.
    Uses binomial test approximation.
    Returns (is_significant, p_value, interpretation)
    """
    if live_trades < 5:
        return False, 1.0, "insufficient_data"

    # Expected wins under null hypothesis
    expected_wins = live_trades * predicted_wr / 100
    actual_wins = round(live_trades * live_wr / 100)

    # Standard error
    se = math.sqrt(predicted_wr * (100 - predicted_wr) / live_trades)

    if se == 0:
        return False, 1.0, "zero_variance"

    # Z-score
    z = abs(live_wr - predicted_wr) / se

    # Approximate p-value (two-tailed)
    # Using simple approximation: p ≈ 2 * (1 - Φ(|z|))
    p_value = 2 * math.erfc(z / math.sqrt(2)) / 2

    is_significant = p_value < 0.05

    if p_value < 0.01:
        interpretation = "highly_significant"
    elif p_value < 0.05:
        interpretation = "significant"
    else:
        interpretation = "not_significant"

    return is_significant, round(p_value, 4), interpretation


def compare(live_metrics):
    """Compare live metrics vs backtest predictions."""
    comparisons = {}

    for coin, predicted in BACKTEST_PREDICTIONS.items():
        live = live_metrics.get(coin, {})

        if not live:
            comparisons[coin] = {
                "status": "no_live_data",
                "predicted": predicted,
                "message": "No live data yet for this coin",
            }
            continue

        # Compute deviations
        wr_deviation = live.get("win_rate", 0) - predicted["predicted_wr"]
        pnl_deviation = live.get("total_pnl", 0)  # vs predicted monthly

        # Annualize trade rate
        # We don't know the exact live duration, so we use trade count comparison
        # Expected trades per hour = predicted_trades_per_month / (30 * 24)
        expected_trades_per_hour = predicted["predicted_trades_per_month"] / 720

        # Statistical significance
        is_sig, p_val, interp = statistical_significance_test(
            live.get("win_rate", 0),
            live.get("trades", 0),
            predicted["predicted_wr"]
        )

        # Overall assessment
        if live.get("trades", 0) < 3:
            assessment = "too_few_trades"
        elif abs(wr_deviation) <= 10:
            assessment = "consistent"
        elif abs(wr_deviation) <= 15:
            assessment = "marginal_deviation"
        else:
            assessment = "significant_deviation"

        if is_sig and abs(wr_deviation) > 10:
            assessment = "significantly_different"

        comparisons[coin] = {
            "status": "live_data_available",
            "assessment": assessment,
            "predicted": {
                "win_rate": predicted["predicted_wr"],
                "monthly_pnl": predicted["predicted_pnl"],
                "monthly_trades": predicted["predicted_trades_per_month"],
                "max_dd": predicted["predicted_max_dd"],
            },
            "live": {
                "win_rate": live.get("win_rate", 0),
                "total_pnl": live.get("total_pnl", 0),
                "trades": live.get("trades", 0),
                "max_dd": live.get("max_drawdown_pct", 0),
            },
            "deviation": {
                "wr_deviation_pp": round(wr_deviation, 1),
                "is_significant": is_sig,
                "p_value": p_val,
                "significance": interp,
            },
        }

    return comparisons


def print_report(comparisons):
    """Print human-readable comparison report."""
    print(f"\n{'='*80}")
    print(f"  LIVE vs BACKTEST PERFORMANCE COMPARISON")
    print(f"  Generated: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}")
    print(f"{'='*80}\n")

    for coin, comp in comparisons.items():
        print(f"  {'='*50}")
        print(f"  {coin} — {comp.get('predicted', {}).get('strategy', 'unknown')}")
        print(f"  {'='*50}\n")

        if comp["status"] == "no_live_data":
            print(f"  ⏳ No live data yet for this coin\n")
            continue

        live = comp["live"]
        pred = comp["predicted"]
        dev = comp["deviation"]
        assessment = comp["assessment"]

        # Assessment emoji
        if assessment == "consistent":
            emoji = "✅"
        elif assessment in ["marginal_deviation", "too_few_trades"]:
            emoji = "⚠️"
        elif assessment == "significantly_different":
            emoji = "🚨"
        else:
            emoji = "❓"

        print(f"  {emoji} Assessment: {assessment}\n")
        print(f"  {'Metric':<20} {'Predicted':<15} {'Live':<15} {'Deviation':<15}")
        print(f"  {'-'*65}")
        print(f"  {'Win Rate':<20} {pred['win_rate']:>5.1f}%         {live['win_rate']:>5.1f}%         {dev['wr_deviation_pp']:>+.1f} pp")
        print(f"  {'Total PnL':<20} ${pred['monthly_pnl']:>8.0f}/mo    ${live['total_pnl']:>8.2f}       {'':<15}")
        print(f"  {'Trades':<20} {pred['monthly_trades']:>5}/mo     {live['trades']:>5}          {'':<15}")
        print(f"  {'Max Drawdown':<20} {pred['max_dd']:>5.1f}%        {live['max_dd']:>5.1f}%        {'':<15}")

        if live["trades"] >= 5:
            print(f"\n  Statistical Significance:")
            print(f"    p-value: {dev['p_value']:.4f}")
            print(f"    Interpretation: {dev['significance']}")

        print(f"\n")

    # Summary
    print(f"  {'='*80}")
    print(f"  SUMMARY")
    print(f"  {'='*80}\n")

    consistent = sum(1 for c in comparisons.values() if c.get("assessment") == "consistent")
    concerning = sum(1 for c in comparisons.values() if c.get("assessment") in ["significant_deviation", "significantly_different"])
    waiting = sum(1 for c in comparisons.values() if c["status"] == "no_live_data")
    total = len(comparisons)

    print(f"  Consistent with backtest: {consistent}/{total}")
    print(f"  Concerning deviation: {concerning}/{total}")
    print(f"  Waiting for data: {waiting}/{total}\n")

    if concerning > 0:
        print(f"  🚨 ALERT: {concerning} coin(s) showing significant deviation from backtest predictions.")
        print(f"  Review the affected coins above and consider pausing the runner.\n")
    elif consistent > 0 and concerning == 0:
        print(f"  ✅ All live coins with data are consistent with backtest predictions.\n")
    else:
        print(f"  ⏳ Waiting for live data to accumulate.\n")

    print(f"  {'='*80}\n")


def main():
    parser = argparse.ArgumentParser(description="Live vs Backtest Performance Comparator")
    parser.add_argument("--state-path", default=None, help="Path to live state file")
    parser.add_argument("--events-path", default=None, help="Path to live events file")
    args = parser.parse_args()

    # Default paths
    base_dir = Path(__file__).parent.parent
    state_path = Path(args.state_path) if args.state_path else base_dir / "multi_coin_isolated_state.json"
    events_path = Path(args.events_path) if args.events_path else base_dir / "multi_coin_isolated_events.jsonl"

    # Load live data
    live_metrics = load_live_events(events_path)

    if not live_metrics:
        print(f"\n⏳ No live events data found at {events_path}")
        print(f"   Run the isolated runner first, then re-run this tool.\n")
        return

    # Compare
    comparisons = compare(live_metrics)

    # Print report
    print_report(comparisons)

    # Save report
    report = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "state_file": str(state_path),
        "events_file": str(events_path),
        "comparisons": comparisons,
    }

    out_path = REPORTS_DIR / "live_vs_backtest_comparison.json"
    out_path.parent.mkdir(exist_ok=True)
    with open(out_path, "w") as f:
        json.dump(report, f, indent=2)

    print(f"  Report saved: {out_path}\n")


if __name__ == "__main__":
    main()
