#!/usr/bin/env python3
"""
NOM-USD Optimized Single-Coin Runner

Uses the full $48 bankroll on just NOM-USD fibonacci with:
- Optimized session hours: {05, 08, 09, 13, 17, 21} UTC (from session analysis)
- Full concentration: $48/coin instead of $5.33/coin across 9 coins
- Same fibonacci breakout logic with volume + momentum confirmation

This is a SHADOW analysis tool — not for live deployment without team review.

Usage:
    python scripts/nom_optimized_runner.py          # $48 bankroll
    python scripts/nom_optimized_runner.py --cash 30  # $30 bankroll
    python scripts/nom_optimized_runner.py --all-hours  # Use default dead-hour gate for comparison
"""
import json
import sys
import os
from pathlib import Path
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

ROOT = Path(__file__).resolve().parent.parent
CANDLE_PATH = ROOT / "reports" / "candle_cache" / "NOM_USD_FIVE_MINUTE_30d.json"
OUTPUT_PATH = ROOT / "reports" / "nom_optimized_runner.json"

# Session hours: only trade during these UTC hours
# From session analysis: top 6 profitable hours
OPTIMIZED_HOURS = {5, 8, 9, 13, 17, 21}
DEFAULT_DEAD_HOURS = {0, 6, 12, 19}  # Current runner default

FEE_RATE = 0.004
DEPLOY_FRACTION = 0.90
MIN_CASH = 2.0
LOOKBACK = 20
TP_PCT = 0.08
SL_PCT = 0.03
MAX_HOLD = 24


def load_candles():
    if not CANDLE_PATH.exists():
        print(f"[ERROR] {CANDLE_PATH}")
        sys.exit(1)
    with open(CANDLE_PATH) as f:
        data = json.load(f)
    candles = data.get("candles", data if isinstance(data, list) else [])
    normalized = []
    for c in candles:
        if isinstance(c, dict):
            normalized.append(c)
        elif isinstance(c, (list, tuple)):
            normalized.append({
                "time": c[0], "open": c[1], "high": c[2],
                "low": c[3], "close": c[4], "volume": c[5],
            })
    normalized.sort(key=lambda x: int(x.get("time", x.get("start", 0))))
    return normalized


def fibonacci_signal(candles_hist, closes):
    """Fibonacci breakout with volume + momentum confirmation."""
    if len(candles_hist) < LOOKBACK + 5:
        return False

    window = candles_hist[-(LOOKBACK + 1):-1]
    if len(window) < LOOKBACK * 0.5:
        return False

    swing_high = max(float(c["high"]) for c in window)
    swing_low = min(float(c["low"]) for c in window)
    range_size = swing_high - swing_low
    if range_size <= 0:
        return False

    fib_618 = swing_high - 0.618 * range_size

    current = candles_hist[-1]
    current_price = float(current["close"])
    current_volume = float(current.get("volume", 0))

    if current_price <= closes[-2]:
        return False
    if current_price <= fib_618:
        return False

    # Volume confirmation
    if len(candles_hist) >= 20:
        recent_volumes = [float(c.get("volume", 0)) for c in candles_hist[-20:-1]]
        avg_volume = sum(recent_volumes) / len(recent_volumes)
        if avg_volume > 0 and current_volume < 0.8 * avg_volume:
            return False

    # Momentum: 2 of last 3 green
    if len(closes) >= 4:
        green = sum(1 for i in range(-3, 0) if closes[i] > closes[i - 1])
        if green < 2:
            return False

    return True


def simulate(candles, starting_cash, allowed_hours):
    """Run backtest with given cash and session hours."""
    cash = starting_cash
    position = None
    trades = []
    signals = 0
    total_fees = 0.0
    peak_equity = starting_cash
    max_dd = 0.0

    for i in range(LOOKBACK + 5, len(candles)):
        candle = candles[i]
        candle_time = int(candle.get("time", candle.get("start", 0)))
        hour = datetime.fromtimestamp(candle_time, tz=timezone.utc).hour

        closes = [float(c["close"]) for c in candles[max(0, i - 500):i + 1]]
        candles_hist = candles[max(0, i - 500):i + 1]

        high = float(candle["high"])
        low = float(candle["low"])
        open_p = float(candle["open"])

        # Session gate
        if hour not in allowed_hours:
            if position:
                position["hold"] += 1
                if high >= position["tp"]:
                    pnl = (position["tp"] - position["entry"]) * position["units"]
                    fee = position["tp"] * position["units"] * FEE_RATE
                    net = pnl - fee - position["entry_fee"]
                    cash += position["deploy"] + net
                    total_fees += fee
                    trades.append({"entry": position["entry"], "exit": position["tp"], "net": net, "hold": position["hold"], "reason": "tp"})
                    position = None
                elif low <= position["sl"]:
                    pnl = (position["sl"] - position["entry"]) * position["units"]
                    fee = position["sl"] * position["units"] * FEE_RATE
                    net = pnl - fee - position["entry_fee"]
                    cash += position["deploy"] + net
                    total_fees += fee
                    trades.append({"entry": position["entry"], "exit": position["sl"], "net": net, "hold": position["hold"], "reason": "sl"})
                    position = None
                elif position["hold"] >= MAX_HOLD:
                    pnl = (open_p - position["entry"]) * position["units"]
                    fee = open_p * position["units"] * FEE_RATE
                    net = pnl - fee - position["entry_fee"]
                    cash += position["deploy"] + net
                    total_fees += fee
                    trades.append({"entry": position["entry"], "exit": open_p, "net": net, "hold": position["hold"], "reason": "timeout"})
                    position = None
            continue

        # Manage position
        if position:
            position["hold"] += 1
            if high >= position["tp"]:
                pnl = (position["tp"] - position["entry"]) * position["units"]
                fee = position["tp"] * position["units"] * FEE_RATE
                net = pnl - fee - position["entry_fee"]
                cash += position["deploy"] + net
                total_fees += fee
                trades.append({"entry": position["entry"], "exit": position["tp"], "net": net, "hold": position["hold"], "reason": "tp"})
                position = None
            elif low <= position["sl"]:
                pnl = (position["sl"] - position["entry"]) * position["units"]
                fee = position["sl"] * position["units"] * FEE_RATE
                net = pnl - fee - position["entry_fee"]
                cash += position["deploy"] + net
                total_fees += fee
                trades.append({"entry": position["entry"], "exit": position["sl"], "net": net, "hold": position["hold"], "reason": "sl"})
                position = None
            elif position["hold"] >= MAX_HOLD:
                pnl = (open_p - position["entry"]) * position["units"]
                fee = open_p * position["units"] * FEE_RATE
                net = pnl - fee - position["entry_fee"]
                cash += position["deploy"] + net
                total_fees += fee
                trades.append({"entry": position["entry"], "exit": open_p, "net": net, "hold": position["hold"], "reason": "timeout"})
                position = None

        # Check signal
        if position is None and cash >= MIN_CASH:
            if fibonacci_signal(candles_hist, closes):
                signals += 1
                deploy = cash * DEPLOY_FRACTION
                entry_price = open_p
                units = deploy / entry_price
                entry_fee = deploy * FEE_RATE
                tp = entry_price * (1 + TP_PCT)
                sl = entry_price * (1 - SL_PCT)
                cash -= deploy
                position = {
                    "entry": entry_price, "units": units, "hold": 0,
                    "tp": tp, "sl": sl, "deploy": deploy, "entry_fee": entry_fee,
                }

        equity = cash + (position["deploy"] if position else 0)
        if equity > peak_equity:
            peak_equity = equity
        dd = peak_equity - equity
        if dd > max_dd:
            max_dd = dd

    # Close remaining
    if position:
        exit_price = float(candles[-1]["close"])
        pnl = (exit_price - position["entry"]) * position["units"]
        fee = exit_price * position["units"] * FEE_RATE
        net = pnl - fee - position["entry_fee"]
        cash += position["deploy"] + net
        total_fees += fee
        trades.append({"entry": position["entry"], "exit": exit_price, "net": net, "hold": position["hold"], "reason": "end"})

    net_pnl = cash - starting_cash
    wins = sum(1 for t in trades if t["net"] > 0)
    losses = len(trades) - wins
    wr = wins / max(1, len(trades)) * 100
    avg_hold = sum(t["hold"] for t in trades) / max(1, len(trades))

    return {
        "starting_cash": starting_cash,
        "ending_cash": round(cash, 4),
        "net_pnl": round(net_pnl, 4),
        "win_rate": round(wr, 1),
        "trades": len(trades),
        "wins": wins,
        "losses": losses,
        "signals": signals,
        "total_fees": round(total_fees, 4),
        "max_drawdown": round(max_dd, 4),
        "avg_hold_bars": round(avg_hold, 1),
    }


def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--cash", type=float, default=48.0)
    parser.add_argument("--all-hours", action="store_true", help="Use default dead-hour gate for comparison")
    args = parser.parse_args()

    print("=" * 60)
    print("  NOM-USD Optimized Single-Coin Runner")
    print("=" * 60)

    candles = load_candles()
    print(f"\nLoaded {len(candles)} candles")

    hours_label = "All active hours (dead-hour gate)" if args.all_hours else "Top 6 profitable hours"
    allowed = DEFAULT_DEAD_HOURS if args.all_hours else None
    # For "all hours" mode, we use the complement of dead hours
    if args.all_hours:
        allowed = set(range(24)) - DEFAULT_DEAD_HOURS
    else:
        allowed = OPTIMIZED_HOURS

    # Test at multiple cash levels
    cash_levels = [5.33, 10.0, 20.0, 30.0, 48.0]

    print(f"\nSession hours: {sorted(allowed)}")
    print(f"Starting cash: ${args.cash:.2f} (also testing ${', $'.join(f'{c:.2f}' for c in cash_levels)})")

    results = {}
    for cash in cash_levels:
        r = simulate(candles, cash, allowed)
        results[str(cash)] = r
        # Monthly projection (30d data = what we have)
        monthly_pnl = r["net_pnl"]
        print(f"  Cash=${cash:>6.2f}: PnL=${monthly_pnl:>+8.2f} WR={r['win_rate']:5.1f}% "
              f"Trades={r['trades']:3d} Fees=${r['total_fees']:.2f} DD=${r['max_drawdown']:.2f}")

    # Comparison with diversified approach
    print(f"\n{'=' * 60}")
    print(f"  COMPARISON")
    print(f"{'=' * 60}")

    r48 = results.get("48.0", {})
    r533 = results.get("5.33", {})

    print(f"\n  {'Config':<30} {'Cash':>8} {'PnL/30d':>10} {'WR':>8} {'Trades':>8} {'Fees':>8}")
    print(f"  {'─' * 72}")
    print(f"  {'NOM optimized (top 6 hrs)':<30} ${48.0:>6.2f} ${r48['net_pnl']:>8.2f} {r48['win_rate']:>6.1f}% {r48['trades']:>6} ${r48['total_fees']:>6.2f}")
    print(f"  {'NOM equal split (5.33)':<30} ${5.33:>6.2f} ${r533['net_pnl']:>8.2f} {r533['win_rate']:>6.1f}% {r533['trades']:>6} ${r533['total_fees']:>6.2f}")
    print(f"  {'9-coin equal split (from optimizer)':<30} ${48.0:>6.2f} ${182.0:>8.2f} {'—':>6} {'—':>6} ${100.0:>6.2f}")

    if args.cash == 48.0:
        print(f"\n  Edge vs equal split NOM: {r48['net_pnl']/r533['net_pnl']:.1f}x" if r533['net_pnl'] != 0 else "")
        print(f"  Edge vs 9-coin equal split: {r48['net_pnl']/182.0:.1f}x")

    # Save config
    config = {
        "coin": "NOM-USD",
        "strategy": "fibonacci",
        "fib_lookback": 20,
        "tp_pct": TP_PCT,
        "sl_pct": SL_PCT,
        "max_hold": MAX_HOLD,
        "session_hours": sorted(allowed),
        "session_label": hours_label,
        "cash_levels_tested": cash_levels,
        "results": results,
        "recommendation": {
            "cash_allocation": 48.0,
            "projected_monthly_pnl": r48.get("net_pnl", 0),
            "note": "Shadow analysis only — validate with paper trading before live deployment",
        },
    }

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.write_text(json.dumps(config, indent=2), encoding="utf-8")
    print(f"\n  Config saved: {OUTPUT_PATH}")


if __name__ == "__main__":
    main()
