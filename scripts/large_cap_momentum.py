#!/usr/bin/env python3
"""
Large-Cap Momentum/Trend Strategy — The Anti-RSI-MR

WHY: @main's 8-coin scan proved RSI(3) mean-reversion LOSES on large caps:
- SOL: 16.5% WR, -$36.87/month
- DOGE: 19.9% WR, -$35.74/month  
- XRP: 13.5% WR, -$34.82/month
- AAVE: 20.0% WR, -$35.60/month

When large caps hit RSI(3)<30, they're NOT mean-reverting — they're in a REAL downtrend.
RSI MR is trying to catch falling knives.

THE HYPOTHESIS: Trend-following should WIN where RSI MR loses.
- When RSI(3) is LOW on large caps, the trend is DOWN → SHORT (or stay in cash)
- When RSI(3) is HIGH on large caps, the trend is UP → LONG
- Use moving average crossovers to capture sustained moves
- Use ATR-based trailing stops to ride trends

Strategy: Dual MA Crossover + ATR Trail
- Fast MA(9) / Slow MA(21) on 5-min candles
- Enter LONG when fast crosses above slow
- Enter SHORT when fast crosses below short (or just stay in cash for spot)
- ATR trailing stop at 2x ATR from entry
- Session-gated (same death zones)

COINS: SOL-USD, DOGE-USD, XRP-USD (highest volume large caps)
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from candle_cache_service import load_candles

ROOT = Path(__file__).resolve().parent.parent
REPORT_DIR = ROOT / "reports"

LARGE_CAPS = ["SOL-USD", "DOGE-USD", "XRP-USD", "AAVE-USD", "ETH-USD"]


def compute_sma(closes, period):
    """Simple moving average."""
    if len(closes) < period:
        return [None] * len(closes)
    result = [None] * (period - 1)
    sma = sum(closes[:period]) / period
    result.append(sma)
    for i in range(period, len(closes)):
        sma = sma + (closes[i] - closes[i - period]) / period
        result.append(sma)
    return result


def compute_ema(closes, period):
    """Exponential moving average."""
    if len(closes) < period:
        return [None] * len(closes)
    result = [None] * (period - 1)
    multiplier = 2 / (period + 1)
    ema = sum(closes[:period]) / period  # Start with SMA
    result.append(ema)
    for i in range(period, len(closes)):
        ema = (closes[i] - ema) * multiplier + ema
        result.append(ema)
    return result


def compute_atr(highs, lows, closes, period=14):
    """Average True Range."""
    if len(closes) < period + 1:
        return [None] * len(closes)
    trs = []
    for i in range(1, len(closes)):
        tr = max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]), abs(lows[i] - closes[i - 1]))
        trs.append(tr)
    result = [None] * (period)
    atr = sum(trs[:period]) / period
    result.append(atr)
    for i in range(period, len(trs)):
        atr = (atr * (period - 1) + trs[i]) / period
        result.append(atr)
    return result


def backtest_trend(candles, fast_period=9, slow_period=21,
                    atr_mult=2.0, fee_rate=0.0040,
                    starting_cash=48.0, session_gate=True):
    """
    Dual EMA crossover trend-following strategy.

    - Enter LONG when fast EMA crosses above slow EMA
    - Exit when fast EMA crosses below slow EMA (or trailing stop hits)
    - ATR trailing stop: exit if price drops below entry - atr_mult * ATR
    - No shorting (spot only) — stay in cash during downtrends
    """
    if len(candles) < slow_period + 10:
        return {"error": "not enough candles"}

    closes = [float(c["close"]) for c in candles]
    highs = [float(c["high"]) for c in candles]
    lows = [float(c["low"]) for c in candles]
    timestamps = [int(c.get("time") or c.get("start") or 0) for c in candles]

    fast_ema = compute_ema(closes, fast_period)
    slow_ema = compute_ema(closes, slow_period)
    atr_vals = compute_atr(highs, lows, closes, period=14)

    cash = starting_cash
    in_position = False
    position = None
    trades = []
    total_volume = 0.0
    total_fees = 0.0
    peak_equity = starting_cash
    max_dd = 0.0

    for i in range(slow_period + 5, len(candles) - 1):
        if fast_ema[i] is None or slow_ema[i] is None:
            continue

        # Session gate
        if session_gate:
            hour = (timestamps[i] % 86400) // 3600
            if hour in [12, 19, 6, 0]:
                # Force exit during death zone
                if in_position and position:
                    exit_price = closes[i]
                    units = position["qty"]
                    gross = units * exit_price
                    exit_fee = gross * fee_rate
                    net = gross - exit_fee - position["entry_cost"]
                    cash += gross - exit_fee
                    total_volume += position["deploy"] + gross
                    total_fees += position["entry_fee"] + exit_fee
                    trades.append({
                        "net": net, "win": net > 0, "reason": "session_exit",
                        "hold_bars": i - position["bar"],
                    })
                    equity = cash
                    peak_equity = max(peak_equity, equity)
                    if peak_equity > 0:
                        dd = (peak_equity - equity) / peak_equity * 100
                        max_dd = max(max_dd, dd)
                    in_position = False
                    position = None
                continue

        # Exit logic
        if in_position and position:
            current_atr = atr_vals[i] if atr_vals[i] else atr_vals[i - 1]
            trailing_stop = position["entry"] - atr_mult * (current_atr or 0.01)

            exit_price = None
            exit_reason = None

            # EMA crossover exit (fast crosses below slow)
            if fast_ema[i] is not None and slow_ema[i] is not None:
                if fast_ema[i] < slow_ema[i]:
                    exit_price = closes[i]
                    exit_reason = "ema_cross"

            # Trailing stop
            if closes[i] <= trailing_stop and trailing_stop > 0:
                exit_price = trailing_stop
                exit_reason = "trail_stop"

            if exit_price is not None:
                units = position["qty"]
                gross = units * exit_price
                exit_fee = gross * fee_rate
                net = gross - exit_fee - position["entry_cost"]
                cash += gross - exit_fee
                total_volume += position["deploy"] + gross
                total_fees += position["entry_fee"] + exit_fee

                trades.append({
                    "net": net, "win": net > 0, "reason": exit_reason,
                    "hold_bars": i - position["bar"],
                    "entry_atr_pct": position.get("atr_pct", 0),
                })

                equity = cash
                peak_equity = max(peak_equity, equity)
                if peak_equity > 0:
                    dd = (peak_equity - equity) / peak_equity * 100
                    max_dd = max(max_dd, dd)

                in_position = False
                position = None

        # Entry logic: fast EMA crosses above slow EMA
        if not in_position and cash >= 10.0:
            if fast_ema[i] is not None and slow_ema[i] is not None:
                # Check for crossover: fast was below slow, now above
                if i > 0 and fast_ema[i - 1] is not None and slow_ema[i - 1] is not None:
                    if fast_ema[i - 1] <= slow_ema[i - 1] and fast_ema[i] > slow_ema[i]:
                        # Bullish crossover — enter LONG
                        entry_price = closes[i]
                        deploy = cash * 0.95
                        entry_fee = deploy * fee_rate
                        qty = (deploy - entry_fee) / entry_price

                        current_atr = atr_vals[i] if atr_vals[i] else atr_vals[i - 1]
                        atr_pct = (current_atr / entry_price * 100) if entry_price > 0 and current_atr else 0

                        if qty > 0:
                            cash -= deploy
                            in_position = True
                            position = {
                                "entry": entry_price,
                                "qty": qty,
                                "bar": i,
                                "deploy": deploy,
                                "entry_fee": entry_fee,
                                "entry_cost": deploy,
                                "atr_pct": atr_pct,
                            }

    # Close remaining position
    if in_position and position:
        exit_price = closes[-1]
        units = position["qty"]
        gross = units * exit_price
        exit_fee = gross * fee_rate
        net = gross - exit_fee - position["entry_cost"]
        cash += gross - exit_fee
        total_volume += position["deploy"] + gross
        total_fees += position["entry_fee"] + exit_fee
        trades.append({
            "net": net, "win": net > 0, "reason": "close_remaining",
            "hold_bars": len(candles) - position["bar"],
        })

    # Compute stats
    net = cash - starting_cash
    wins = [t for t in trades if t["win"]]
    losses = [t for t in trades if not t["win"]]
    total_trades = len(trades)
    wr = len(wins) / max(1, total_trades) * 100

    gross_wins = sum(t["net"] for t in wins)
    gross_losses = abs(sum(t["net"] for t in losses))
    pf = gross_wins / gross_losses if gross_losses > 0 else float("inf")

    avg_hold = sum(t["hold_bars"] for t in trades) / max(1, total_trades)

    bars = len(candles)
    days = bars / 288
    monthly = net / max(0.001, days) * 30

    return {
        "net": round(net, 2),
        "return_pct": round(net / starting_cash * 100, 1),
        "trades": total_trades,
        "wins": len(wins),
        "losses": len(losses),
        "win_rate": round(wr, 1),
        "total_volume": round(total_volume, 2),
        "total_fees": round(total_fees, 2),
        "max_drawdown": round(max_dd, 1),
        "profit_factor": round(pf, 3) if pf != float("inf") else 999.0,
        "avg_hold_bars": round(avg_hold, 1),
        "monthly_projection": round(monthly, 2),
        "bars": bars,
        "days": round(days, 1),
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--window", default="30d")
    parser.add_argument("--fast", type=int, default=9)
    parser.add_argument("--slow", type=int, default=21)
    parser.add_argument("--atr-mult", type=float, default=2.0)
    args = parser.parse_args()

    days = int(args.window.replace("d", ""))

    print("=" * 80)
    print(f"  LARGE-CAP MOMENTUM — Dual EMA Crossover + ATR Trail")
    print(f"  Fast EMA({args.fast}) / Slow EMA({args.slow}) / ATR Trail {args.atr_mult}x")
    print("=" * 80)

    all_results = {}

    for coin in LARGE_CAPS:
        candles = load_candles(coin, "FIVE_MINUTE", days, max_age_minutes=days * 24 * 60)
        if not candles:
            print(f"  {coin}: NO DATA")
            continue

        result = backtest_trend(candles, fast_period=args.fast, slow_period=args.slow,
                                 atr_mult=args.atr_mult)
        if "error" in result:
            print(f"  {coin}: {result['error']}")
            continue

        all_results[coin] = result
        emoji = "✅" if result["net"] > 0 else "❌"
        print(f"  {emoji} {coin:<12} Net: ${result['net']:+8.2f}  "
              f"{result['trades']:>3}t  {result['win_rate']:>5.1f}%WR  "
              f"DD={result['max_drawdown']:>5.1f}%  monthly=${result['monthly_projection']:+8.2f}")

    # Summary
    profitable = [c for c, r in all_results.items() if r["net"] > 0]
    print(f"\n{'=' * 80}")
    if profitable:
        print(f"  PROFITABLE: {', '.join(profitable)}")
        best = max(all_results, key=lambda c: all_results[c]["net"])
        print(f"  BEST: {best} (${all_results[best]['net']:+.2f})")
    else:
        print(f"  NO PROFITABLE coins found with EMA({args.fast})/EMA({args.slow})")
        print(f"  Trying alternative parameters...")

        # Quick parameter sweep
        best_coin = None
        best_net = -999999
        best_params = None
        for fp in [5, 7, 9, 12]:
            for sp in [14, 21, 34, 50]:
                if fp >= sp:
                    continue
                for coin, candles_data in [(c, load_candles(c, "FIVE_MINUTE", days, max_age_minutes=days * 24 * 60))
                                            for c in LARGE_CAPS]:
                    if not candles_data:
                        continue
                    r = backtest_trend(candles_data, fast_period=fp, slow_period=sp, atr_mult=2.0)
                    if "error" not in r and r["net"] > best_net:
                        best_net = r["net"]
                        best_coin = coin
                        best_params = (fp, sp)

        if best_coin and best_net > 0:
            fp, sp = best_params
            print(f"  Best found: {best_coin} EMA({fp})/EMA({sp}) → ${best_net:+.2f}")
        else:
            print(f"  No profitable EMA crossover configuration found for large caps.")
            print(f"  Trend-following may not work on 5-min large-cap data either.")

    print(f"{'=' * 80}")

    # Save
    output_path = REPORT_DIR / f"large_cap_momentum_{args.window}.json"
    with open(output_path, "w") as f:
        json.dump(all_results, f, indent=2, default=str)
    print(f"\n  Saved: {output_path}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
