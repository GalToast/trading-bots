#!/usr/bin/env python3
"""
GHST-USD Fibonacci Lookback A/B Test

Compares fib_lookback=20 (original backtest config) vs fib_lookback=10 (live runner config).
The live runner uses lookback=10 because Coinbase only returns ~11 candles in the 120-min
window for GHST-USD. But the $430/mo projection was from lookback=20 tests.

Usage:
    python scripts/ghst_fib_lookback_ab_test.py
"""
import json
import sys
import os
from pathlib import Path
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

ROOT = Path(__file__).resolve().parent.parent
CANDLE_PATH = ROOT / "reports" / "candle_cache" / "GHST_USD_FIVE_MINUTE_30d.json"
OUTPUT_PATH = ROOT / "reports" / "ghst_fib_lookback_comparison.json"

# Shared backtest constants
FEE_RATE = 0.004
DEPLOY_FRACTION = 0.90
MIN_CASH = 2.0
SESSION_DEAD = {0, 6, 12, 19}
SESSION_BYPASS = False


def load_candles():
    """Load GHST-USD 30d M5 candles from cache."""
    if not CANDLE_PATH.exists():
        print(f"[ERROR] Candle file not found: {CANDLE_PATH}")
        sys.exit(1)
    with open(CANDLE_PATH) as f:
        data = json.load(f)
    candles = data.get("candles", data if isinstance(data, list) else [])
    # Normalize to list of dicts
    normalized = []
    for c in candles:
        if isinstance(c, dict):
            normalized.append(c)
        elif isinstance(c, (list, tuple)):
            # [time, open, high, low, close, volume]
            normalized.append({
                "time": c[0], "open": c[1], "high": c[2],
                "low": c[3], "close": c[4], "volume": c[5],
            })
    # Sort by time
    normalized.sort(key=lambda x: int(x.get("time", x.get("start", 0))))
    return normalized


def fibonacci_breakout_signal(candles_hist, closes, lookback, fib_level=0.618):
    """Fibonacci breakout with volume + momentum confirmation (live runner version)."""
    if len(candles_hist) < lookback + 5:
        return False

    # Find swing high/low over lookback window
    window = candles_hist[-(lookback + 1):-1]  # exclude current candle
    if len(window) < lookback * 0.5:  # need at least 50% of lookback
        return False

    swing_high = max(float(c["high"]) for c in window)
    swing_low = min(float(c["low"]) for c in window)
    range_size = swing_high - swing_low

    if range_size <= 0:
        return False

    # Fib 0.618 retracement level
    fib_618 = swing_high - fib_level * range_size

    # Current candle
    current = candles_hist[-1]
    current_price = float(current["close"])
    current_high = float(current["high"])
    current_volume = float(current.get("volume", 0))

    # Price must be rising
    if current_price <= closes[-2]:
        return False

    # Breakout: price above Fib 618 level
    if current_price <= fib_618:
        return False

    # Volume confirmation: current volume > 80% of 20-period avg
    if len(candles_hist) >= 20:
        recent_volumes = [float(c.get("volume", 0)) for c in candles_hist[-20:-1]]
        avg_volume = sum(recent_volumes) / len(recent_volumes)
        if avg_volume > 0 and current_volume < 0.8 * avg_volume:
            return False

    # Momentum confirmation: 2 of last 3 candles green
    if len(closes) >= 4:
        green_count = 0
        for i in range(-3, 0):
            if closes[i] > closes[i - 1]:
                green_count += 1
        if green_count < 2:
            return False

    return True


def simulate(candles, lookback, starting_cash, tp_pct=0.08, sl_pct=0.03, max_hold=24):
    """Run a backtest with the given fib_lookback parameter."""
    cash = starting_cash
    position = None  # {"entry": price, "units": n, "hold": bars, "tp": price, "sl": price}
    trades = []
    signals = 0
    total_fees = 0.0
    total_volume = 0.0
    max_dd = 0.0
    peak_equity = starting_cash

    # We need a sliding window of candles for the signal
    MIN_CANDLES = lookback + 5

    for i in range(MIN_CANDLES, len(candles)):
        candle = candles[i]
        candle_time = int(candle.get("time", candle.get("start", 0)))
        candle_hour = datetime.fromtimestamp(candle_time, tz=timezone.utc).hour

        closes = [float(c["close"]) for c in candles[max(0, i - 500):i + 1]]
        candles_hist = candles[max(0, i - 500):i + 1]

        high = float(candle["high"])
        low = float(candle["low"])
        open_p = float(candle["open"])

        # Session gate
        if not SESSION_BYPASS and candle_hour in SESSION_DEAD:
            # Check if position exits during dead hours
            if position:
                position["hold"] += 1
                # Check TP/SL
                if high >= position["tp"]:
                    # Take profit
                    exit_price = position["tp"]
                    pnl = (exit_price - position["entry"]) * position["units"]
                    fee = exit_price * position["units"] * FEE_RATE
                    net = pnl - fee - position["entry_fee"]
                    cash += position["deploy"] + net
                    total_fees += fee
                    total_volume += exit_price * position["units"]
                    trades.append({
                        "entry": position["entry"], "exit": exit_price,
                        "net": net, "hold": position["hold"], "reason": "tp",
                    })
                    position = None
                    continue
                elif low <= position["sl"]:
                    # Stop loss
                    exit_price = position["sl"]
                    pnl = (exit_price - position["entry"]) * position["units"]
                    fee = exit_price * position["units"] * FEE_RATE
                    net = pnl - fee - position["entry_fee"]
                    cash += position["deploy"] + net
                    total_fees += fee
                    total_volume += exit_price * position["units"]
                    trades.append({
                        "entry": position["entry"], "exit": exit_price,
                        "net": net, "hold": position["hold"], "reason": "sl",
                    })
                    position = None
                    continue
                elif position["hold"] >= max_hold:
                    # Timeout exit
                    exit_price = open_p  # fill at next candle open
                    pnl = (exit_price - position["entry"]) * position["units"]
                    fee = exit_price * position["units"] * FEE_RATE
                    net = pnl - fee - position["entry_fee"]
                    cash += position["deploy"] + net
                    total_fees += fee
                    total_volume += exit_price * position["units"]
                    trades.append({
                        "entry": position["entry"], "exit": exit_price,
                        "net": net, "hold": position["hold"], "reason": "timeout",
                    })
                    position = None
                    continue
            continue  # No new entries during dead hours

        # Manage open position
        if position:
            position["hold"] += 1

            # Check TP/SL on this candle
            if high >= position["tp"]:
                exit_price = position["tp"]
                pnl = (exit_price - position["entry"]) * position["units"]
                fee = exit_price * position["units"] * FEE_RATE
                net = pnl - fee - position["entry_fee"]
                cash += position["deploy"] + net
                total_fees += fee
                total_volume += exit_price * position["units"]
                trades.append({
                    "entry": position["entry"], "exit": exit_price,
                    "net": net, "hold": position["hold"], "reason": "tp",
                })
                position = None
            elif low <= position["sl"]:
                exit_price = position["sl"]
                pnl = (exit_price - position["entry"]) * position["units"]
                fee = exit_price * position["units"] * FEE_RATE
                net = pnl - fee - position["entry_fee"]
                cash += position["deploy"] + net
                total_fees += fee
                total_volume += exit_price * position["units"]
                trades.append({
                    "entry": position["entry"], "exit": exit_price,
                    "net": net, "hold": position["hold"], "reason": "sl",
                })
                position = None
            elif position["hold"] >= max_hold:
                exit_price = open_p
                pnl = (exit_price - position["entry"]) * position["units"]
                fee = exit_price * position["units"] * FEE_RATE
                net = pnl - fee - position["entry_fee"]
                cash += position["deploy"] + net
                total_fees += fee
                total_volume += exit_price * position["units"]
                trades.append({
                    "entry": position["entry"], "exit": exit_price,
                    "net": net, "hold": position["hold"], "reason": "timeout",
                })
                position = None

        # Check for entry signal (only if no position)
        if position is None and cash >= MIN_CASH:
            if fibonacci_breakout_signal(candles_hist, closes, lookback):
                signals += 1
                deploy = cash * DEPLOY_FRACTION
                entry_price = open_p  # fill at next candle open
                units = deploy / entry_price
                entry_fee = deploy * FEE_RATE
                tp = entry_price * (1 + tp_pct)
                sl = entry_price * (1 - sl_pct)

                cash -= deploy
                position = {
                    "entry": entry_price, "units": units, "hold": 0,
                    "tp": tp, "sl": sl, "deploy": deploy,
                    "entry_fee": entry_fee,
                }

        # Track equity for drawdown
        equity = cash + (position["deploy"] if position else 0)
        if equity > peak_equity:
            peak_equity = equity
        dd = peak_equity - equity
        if dd > max_dd:
            max_dd = dd

    # Close any remaining position at last candle
    if position:
        last = candles[-1]
        exit_price = float(last["close"])
        pnl = (exit_price - position["entry"]) * position["units"]
        fee = exit_price * position["units"] * FEE_RATE
        net = pnl - fee - position["entry_fee"]
        cash += position["deploy"] + net
        total_fees += fee
        trades.append({
            "entry": position["entry"], "exit": exit_price,
            "net": net, "hold": position["hold"], "reason": "end_of_data",
        })

    net_pnl = cash - starting_cash
    wins = sum(1 for t in trades if t["net"] > 0)
    losses = sum(1 for t in trades if t["net"] <= 0)
    wr = wins / max(1, len(trades)) * 100
    avg_hold = sum(t["hold"] for t in trades) / max(1, len(trades))
    pnl_per_trade = net_pnl / max(1, len(trades))

    return {
        "lookback": lookback,
        "starting_cash": starting_cash,
        "ending_cash": round(cash, 4),
        "net_pnl": round(net_pnl, 4),
        "win_rate": round(wr, 1),
        "trades": len(trades),
        "wins": wins,
        "losses": losses,
        "signals": signals,
        "total_fees": round(total_fees, 4),
        "total_volume": round(total_volume, 4),
        "max_drawdown": round(max_dd, 4),
        "avg_hold_bars": round(avg_hold, 1),
        "pnl_per_trade": round(pnl_per_trade, 4),
    }


def main():
    print("=" * 60)
    print("  GHST-USD Fibonacci Lookback A/B Test")
    print("=" * 60)

    candles = load_candles()
    print(f"\nLoaded {len(candles)} candles")

    # Test both lookbacks at multiple cash levels
    lookbacks = [20, 10]
    cash_levels = [5.33, 10.0, 20.0, 50.0, 100.0]
    max_holds = {20: 24, 10: 96}  # live runner uses max_hold=96 for GHST lookback=10

    results = {}
    for lb in lookbacks:
        results[lb] = {}
        for cash in cash_levels:
            r = simulate(candles, lb, cash, max_hold=max_holds[lb])
            results[lb][cash] = r
            print(f"  LB={lb}, Cash=${cash:.2f}: PnL=${r['net_pnl']:+.2f} WR={r['win_rate']:.1f}% "
                  f"Trades={r['trades']} Fees=${r['total_fees']:.2f}")

    # Comparison table
    print(f"\n{'=' * 60}")
    print(f"  COMPARISON ($5.33 starting cash, live config)")
    print(f"{'=' * 60}")

    r20 = results[20][5.33]
    r10 = results[10][5.33]

    print(f"\n  {'Metric':<20} {'LB=20':>12} {'LB=10':>12} {'Delta':>12}")
    print(f"  {'─' * 56}")
    print(f"  {'Net PnL':<20} ${r20['net_pnl']:>10.2f} ${r10['net_pnl']:>10.2f} ${r10['net_pnl'] - r20['net_pnl']:>+10.2f}")
    print(f"  {'Win Rate':<20} {r20['win_rate']:>10.1f}% {r10['win_rate']:>10.1f}% {r10['win_rate'] - r20['win_rate']:>+10.1f}%")
    print(f"  {'Trades':<20} {r20['trades']:>10} {r10['trades']:>10} {r10['trades'] - r20['trades']:>+10}")
    print(f"  {'Signals':<20} {r20['signals']:>10} {r10['signals']:>10} {r10['signals'] - r20['signals']:>+10}")
    print(f"  {'Max Drawdown':<20} ${r20['max_drawdown']:>10.2f} ${r10['max_drawdown']:>10.2f} ${r10['max_drawdown'] - r20['max_drawdown']:>+10.2f}")
    print(f"  {'Avg Hold (bars)':<20} {r20['avg_hold_bars']:>10.1f} {r10['avg_hold_bars']:>10.1f} {r10['avg_hold_bars'] - r20['avg_hold_bars']:>+10.1f}")
    print(f"  {'PnL/Trade':<20} ${r20['pnl_per_trade']:>10.2f} ${r10['pnl_per_trade']:>10.2f} ${r10['pnl_per_trade'] - r20['pnl_per_trade']:>+10.2f}")
    print(f"  {'Total Fees':<20} ${r20['total_fees']:>10.2f} ${r10['total_fees']:>10.2f} ${r10['total_fees'] - r20['total_fees']:>+10.2f}")

    # Verdict
    pnl_ratio = r10['net_pnl'] / r20['net_pnl'] if r20['net_pnl'] != 0 else float('inf')
    print(f"\n  {'=' * 60}")
    print(f"  VERDICT")
    print(f"  {'=' * 60}")

    if r20['net_pnl'] > 0 and r10['net_pnl'] > 0:
        if pnl_ratio >= 0.8:
            verdict = "✅ VALIDATED — LB=10 preserves 80%+ of LB=20 edge"
        elif pnl_ratio >= 0.5:
            verdict = "⚠️  DEGRADED — LB=10 retains 50-80% of LB=20 edge"
        else:
            verdict = "🚨 SEVERE — LB=10 retains <50% of LB=20 edge"
    elif r10['net_pnl'] > 0 and r20['net_pnl'] <= 0:
        verdict = "✅ SURPRISE — LB=10 is profitable while LB=20 is not"
    elif r10['net_pnl'] <= 0 and r20['net_pnl'] > 0:
        verdict = "🚨 INVALIDATED — LB=10 kills the edge"
    else:
        verdict = "🚨 BOTH LOSE — GHST fibonacci may not be viable"

    print(f"  {verdict}")
    print(f"  LB=10/LB=20 PnL ratio: {pnl_ratio:.2f}")

    if r10['net_pnl'] < r20['net_pnl']:
        degradation = (1 - pnl_ratio) * 100
        print(f"  Edge degradation: {degradation:.1f}%")
    else:
        improvement = (pnl_ratio - 1) * 100
        print(f"  Edge improvement: {improvement:.1f}%")

    # Recommendation
    print(f"\n  RECOMMENDATION:")
    if pnl_ratio >= 0.8:
        print(f"  Keep LB=10 as-is. The sparse candle adaptation works.")
    elif pnl_ratio >= 0.5:
        print(f"  LB=10 is acceptable but suboptimal. Consider finding")
        print(f"  a different strategy for GHST that doesn't need 20 candles.")
    else:
        print(f"  LB=10 severely degrades the edge. Consider:")
        print(f"  1. Switching GHST to momentum (needs fewer candles)")
        print(f"  2. Using a different data granularity (M15 instead of M5)")
        print(f"  3. Removing GHST from the runner entirely")

    # Save results
    report = {
        "ts_utc": datetime.now(timezone.utc).isoformat(),
        "coin": "GHST-USD",
        "candles_loaded": len(candles),
        "lookback_20": results[20],
        "lookback_10": results[10],
        "comparison_at_5_33": {
            "lookback_20": r20,
            "lookback_10": r10,
            "pnl_ratio_10_over_20": round(pnl_ratio, 4),
        },
        "verdict": verdict,
    }
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(f"\n  Full report: {OUTPUT_PATH}")


if __name__ == "__main__":
    main()
