#!/usr/bin/env python3
"""
Multi-Timeframe Edge Scanner — The REAL question is WHEN to trade, not WHAT to trade.

Tests 3 coins × 5 timeframes × 3 strategies = 45 combos.

COINS:
- RAVE-USD (microcap, high vol) — known M5 edge
- SOL-USD (large cap, trending) — loses at M5, should work at H1+
- ETH-USD (established, liquid) — loses at M5, should work at H1+

TIMEFRAMES:
- M1 (1-min) — noise for most, microstructure for RAVE
- M5 (5-min) — where RSI MR works on RAVE
- M15 (15-min) — slower, higher quality signals
- H1 (60-min) — trend territory for large caps
- H4 (4-hour) — swing trading, structural moves

STRATEGIES:
- RSI MR — RSI(period)<threshold, TP, timeout (mean-reversion)
- Trend Following — EMA cross + ATR trail (momentum)
- Breakout — N-bar high/low breakout (breakout)

Fees: 40bps per side (80bps round trip)
"""
from __future__ import annotations

import json
import math
import statistics
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from candle_cache_service import load_candles

ROOT = Path(__file__).resolve().parent.parent
REPORT_DIR = ROOT / "reports"

COINS = ["RAVE-USD", "SOL-USD", "ETH-USD"]
TIMEFRAMES = {
    "M1": "ONE_MINUTE",
    "M5": "FIVE_MINUTE",
    "M15": "FIFTEEN_MINUTE",
    "H1": "ONE_HOUR",
    "H4": "FOUR_HOUR",
}
FEE_RATE = 0.0040
DAYS = 30


def compute_rsi(closes, period=14):
    if len(closes) < period + 1:
        return [50.0] * len(closes)
    deltas = [closes[i] - closes[i - 1] for i in range(1, len(closes))]
    gains = [max(0, d) for d in deltas]
    losses = [max(0, -d) for d in deltas]
    avg_g = sum(gains[:period]) / period
    avg_l = sum(losses[:period]) / period
    rsi = [50.0] * period
    if avg_l > 0:
        rsi.append(100 - 100 / (1 + avg_g / avg_l))
    else:
        rsi.append(100.0)
    for i in range(period, len(deltas)):
        avg_g = (avg_g * (period - 1) + gains[i]) / period
        avg_l = (avg_l * (period - 1) + losses[i]) / period
        if avg_l > 0:
            rsi.append(100 - 100 / (1 + avg_g / avg_l))
        else:
            rsi.append(100.0)
    return rsi


def compute_ema(closes, period):
    if len(closes) < period:
        return [None] * len(closes)
    result = [None] * (period - 1)
    mult = 2 / (period + 1)
    ema = sum(closes[:period]) / period
    result.append(ema)
    for i in range(period, len(closes)):
        ema = (closes[i] - ema) * mult + ema
        result.append(ema)
    return result


def compute_atr(highs, lows, closes, period=14):
    if len(closes) < period + 1:
        return [0.0] * len(closes)
    trs = []
    for i in range(1, len(closes)):
        tr = max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]), abs(lows[i] - closes[i - 1]))
        trs.append(tr)
    atr = [0.0] * period
    atr.append(sum(trs[:period]) / period)
    for i in range(period, len(trs)):
        atr.append((atr[-1] * (period - 1) + trs[i]) / period)
    return atr


def backtest(candles, strat_type="rsi_mr", params=None, fee_rate=FEE_RATE, starting_cash=48.0):
    """Unified backtest engine."""
    if len(candles) < 50:
        return {"error": "not enough candles"}

    p = params or {}
    closes = [float(c["close"]) for c in candles]
    highs = [float(c["high"]) for c in candles]
    lows = [float(c["low"]) for c in candles]

    rsi_period = p.get("rsi_period", 3)
    rsi_thresh = p.get("rsi_thresh", 30)
    tp_pct = p.get("tp_pct", 0.25)
    max_hold = p.get("max_hold", 48)
    ema_fast = p.get("ema_fast", 9)
    ema_slow = p.get("ema_slow", 21)
    atr_mult = p.get("atr_mult", 2.0)
    breakout_len = p.get("breakout_len", 20)

    # Pre-compute indicators
    rsi = compute_rsi(closes, rsi_period) if "rsi" in strat_type else None
    fast_ema = compute_ema(closes, ema_fast) if "trend" in strat_type else None
    slow_ema = compute_ema(closes, ema_slow) if "trend" in strat_type else None
    atr = compute_atr(highs, lows, closes, 14) if "trend" in strat_type else None

    cash = starting_cash
    in_position = False
    position = None
    trades = []
    total_volume = 0.0
    total_fees = 0.0
    peak_equity = starting_cash
    max_dd = 0.0

    start_bar = 50
    if "rsi" in strat_type:
        start_bar = max(start_bar, rsi_period + 5)
    if "trend" in strat_type:
        start_bar = max(start_bar, ema_slow + 5)

    for i in range(start_bar, len(candles) - 1):
        cl = closes[i]
        h = highs[i]
        l = lows[i]

        # Exit logic
        if in_position and position:
            exit_price = None
            exit_reason = None

            if strat_type == "rsi_mr":
                # TP or timeout
                if h >= position["target"]:
                    exit_price = position["target"]
                    exit_reason = "tp"
                elif (i - position["bar"]) >= max_hold:
                    exit_price = cl
                    exit_reason = "timeout"
            elif strat_type == "trend":
                # EMA cross exit or trailing stop
                if fast_ema[i] is not None and slow_ema[i] is not None:
                    if fast_ema[i] < slow_ema[i]:
                        exit_price = cl
                        exit_reason = "ema_cross"
                if exit_price is None and atr[i] > 0:
                    trail = position["entry"] - atr_mult * atr[i]
                    if l <= trail:
                        exit_price = trail
                        exit_reason = "trail"
                if exit_price is None and (i - position["bar"]) >= max_hold:
                    exit_price = cl
                    exit_reason = "timeout"
            elif strat_type == "breakout":
                # Reverse breakout (exit on opposite signal) or timeout
                if (i - position["bar"]) >= max_hold:
                    exit_price = cl
                    exit_reason = "timeout"

            if exit_price is not None:
                units = position["qty"]
                gross = units * exit_price
                exit_fee = gross * fee_rate
                net = gross - exit_fee - position["entry_cost"]
                cash += gross - exit_fee
                total_volume += position["deploy"] + gross
                total_fees += position["entry_fee"] + exit_fee
                trades.append({"net": net, "win": net > 0, "hold_bars": i - position["bar"],
                               "reason": exit_reason})
                equity = cash
                peak_equity = max(peak_equity, equity)
                if peak_equity > 0:
                    dd = (peak_equity - equity) / peak_equity * 100
                    max_dd = max(max_dd, dd)
                in_position = False
                position = None

        # Entry logic
        if not in_position and cash >= 10.0:
            signal = False
            entry_price = cl

            if strat_type == "rsi_mr":
                if rsi and rsi[i] is not None and rsi[i] <= rsi_thresh:
                    # RSI oversold — only enter if not already oversold last bar
                    if i > 0 and rsi[i - 1] is not None and rsi[i - 1] > rsi_thresh:
                        signal = True
                    elif i <= 1:
                        signal = True
            elif strat_type == "trend":
                if fast_ema and slow_ema and i > start_bar:
                    if (fast_ema[i - 1] is not None and slow_ema[i - 1] is not None
                            and fast_ema[i] is not None and slow_ema[i] is not None):
                        if fast_ema[i - 1] <= slow_ema[i - 1] and fast_ema[i] > slow_ema[i]:
                            signal = True
            elif strat_type == "breakout":
                if i >= breakout_len:
                    lookback_high = max(highs[i - breakout_len:i])
                    lookback_low = min(lows[i - breakout_len:i])
                    if cl > lookback_high and closes[i - 1] <= lookback_high:
                        signal = True

            if signal and entry_price > 0:
                deploy = cash * 0.95
                entry_fee = deploy * fee_rate
                qty = (deploy - entry_fee) / entry_price
                if qty > 0:
                    cash -= deploy
                    in_position = True
                    target = entry_price * (1 + tp_pct) if strat_type == "rsi_mr" else None
                    position = {
                        "entry": entry_price, "qty": qty, "bar": i,
                        "deploy": deploy, "entry_fee": entry_fee, "entry_cost": deploy,
                        "target": target,
                    }

    # Close remaining
    if position:
        exit_price = closes[-1]
        units = position["qty"]
        gross = units * exit_price
        exit_fee = gross * fee_rate
        net = gross - exit_fee - position["entry_cost"]
        cash += gross - exit_fee
        total_fees += position["entry_fee"] + exit_fee
        trades.append({"net": net, "win": net > 0, "hold_bars": len(candles) - position["bar"],
                       "reason": "close_remaining"})

    net = cash - starting_cash
    wins = [t for t in trades if t["win"]]
    losses = [t for t in trades if not t["win"]]
    total_trades = len(trades)
    wr = len(wins) / max(1, total_trades) * 100

    bars_per_day = {"M1": 1440, "M5": 288, "M15": 96, "H1": 24, "H4": 6}
    days = len(candles) / bars_per_day.get(tf_name, 288) if (tf_name := timeframe_name) else len(candles) / 288
    monthly = net / max(0.001, days) * 30

    return {
        "net": round(net, 2), "return_pct": round(net / starting_cash * 100, 1),
        "trades": total_trades, "wins": len(wins), "losses": len(losses),
        "win_rate": round(wr, 1), "total_fees": round(total_fees, 2),
        "max_drawdown": round(max_dd, 1),
        "monthly_projection": round(monthly, 2),
        "avg_hold_bars": round(sum(t["hold_bars"] for t in trades) / max(1, total_trades), 1),
        "bars": len(candles),
        "days": round(days, 1),
    }


# Strategy parameter grids per type
STRATEGY_PARAMS = {
    "rsi_mr": [
        {"rsi_period": 3, "rsi_thresh": 30, "tp_pct": 0.25, "max_hold": 48},
        {"rsi_period": 3, "rsi_thresh": 30, "tp_pct": 0.15, "max_hold": 24},
        {"rsi_period": 4, "rsi_thresh": 30, "tp_pct": 0.40, "max_hold": 48},
        {"rsi_period": 5, "rsi_thresh": 25, "tp_pct": 0.20, "max_hold": 36},
        {"rsi_period": 7, "rsi_thresh": 20, "tp_pct": 0.15, "max_hold": 24},
    ],
    "trend": [
        {"ema_fast": 9, "ema_slow": 21, "atr_mult": 2.0, "max_hold": 200},
        {"ema_fast": 5, "ema_slow": 13, "atr_mult": 1.5, "max_hold": 100},
        {"ema_fast": 12, "ema_slow": 26, "atr_mult": 2.5, "max_hold": 300},
    ],
    "breakout": [
        {"breakout_len": 20, "tp_pct": 0.10, "max_hold": 48},
        {"breakout_len": 50, "tp_pct": 0.15, "max_hold": 100},
    ],
}


def main():
    print("=" * 80)
    print(f"  MULTI-TIMEFRAME EDGE SCAN — {len(COINS)} coins × {len(TIMEFRAMES)} TFs × strategies")
    print("=" * 80)

    all_results = {}
    profitable = []

    for coin in COINS:
        print(f"\n{'=' * 70}")
        print(f"  {coin}")
        print(f"{'=' * 70}")

        coin_results = {}

        for tf_name, tf_key in TIMEFRAMES.items():
            print(f"\n  [{tf_name}] Loading candles...")
            candles = load_candles(coin, tf_key, DAYS, max_age_minutes=DAYS * 24 * 60)
            if not candles:
                print(f"    NO DATA")
                continue

            print(f"    {len(candles)} bars ({len(candles) / {'M1': 1440, 'M5': 288, 'M15': 96, 'H1': 24, 'H4': 6}[tf_name]:.0f} days)")

            for strat_type, param_list in STRATEGY_PARAMS.items():
                for pi, params in enumerate(param_list):
                    # Save timeframe name for backtest
                    global timeframe_name
                    timeframe_name = tf_name

                    result = backtest(candles, strat_type=strat_type, params=params)
                    if "error" in result:
                        continue

                    key = f"{tf_name}/{strat_type}_v{pi + 1}"
                    coin_results[key] = {**result, "params": params, "tf": tf_name, "strat": strat_type}

                    emoji = "✅" if result["net"] > 0 else "❌"
                    print(f"    {emoji} {strat_type:<10} v{pi + 1}  ${result['net']:+7.2f}  "
                          f"{result['trades']:>3}t  {result['win_rate']:>5.1f}%WR  "
                          f"DD={result['max_drawdown']:>5.1f}%  monthly=${result['monthly_projection']:+8.2f}")

                    if result["net"] > 0 and result["trades"] >= 5:
                        profitable.append({
                            "coin": coin, "tf": tf_name, "strategy": strat_type,
                            "version": pi + 1, **result,
                        })

        all_results[coin] = coin_results

    # Summary
    print(f"\n{'=' * 80}")
    print(f"  SUMMARY — {len(profitable)} profitable combos")
    print(f"{'=' * 80}")

    if profitable:
        profitable.sort(key=lambda x: x["monthly_projection"], reverse=True)
        print(f"\n  {'Coin':<10} {'TF':<4} {'Strategy':<10} {'V':<2} {'Net':>7} {'Trades':>6} {'WR%':>5} {'DD%':>5} {'Monthly':>8}")
        print(f"  {'─' * 10} {'─' * 4} {'─' * 10} {'─' * 2} {'─' * 7} {'─' * 6} {'─' * 5} {'─' * 5} {'─' * 8}")
        for p in profitable:
            print(f"  {p['coin']:<10} {p['tf']:<4} {p['strategy']:<10} {p['version']:<2} "
                  f"${p['net']:>+6.2f}  {p['trades']:>6}  {p['win_rate']:>4.1f}% {p['max_drawdown']:>4.1f}% "
                  f"${p['monthly_projection']:>+7.2f}")
    else:
        print(f"\n  ZERO profitable combos with 5+ trades.")

    # Save
    output_path = REPORT_DIR / "multi_timeframe_edge_scan_30d.json"
    with open(output_path, "w") as f:
        json.dump({
            "profitable": profitable,
            "all_results": {coin: results for coin, results in all_results.items()},
        }, f, indent=2, default=str)
    print(f"\n  Saved: {output_path}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
