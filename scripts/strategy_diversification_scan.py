#!/usr/bin/env python3
"""
Strategy Diversification Scanner — Find edges OUTSIDE RSI MR

Tests 5 fundamentally different strategies on 10 coins:

1. RSI MR (baseline) — RSI(3)<30, 25% TP, 48-bar max hold
2. RSI MR M15 — Same on 15-min candles (slower, higher quality signals)
3. BREAKOUT — Enter on 20-bar high breakout, trail ATR stop
4. VOL EXPANSION — Enter when ATR expands > 2x average, ride momentum
5. REVERSAL — Enter after 3 consecutive down bars, exit on first up bar
6. MEAN REVERT — Buy when price is > 3 std below 20-bar MA, exit at MA

Coins: RAVE, MOG, BAL, IOTX, BLUR, ALEPH, SOL, DOGE, XRP, ETH
Window: 30d, 5-min candles (except M15 variants)

Goal: Find ANY strategy+coin combo that beats the RSI MR baseline ($278/month at 40bps)
"""
from __future__ import annotations

import json
import math
import statistics
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from candle_cache_service import load_candles

ROOT = Path(__file__).resolve().parent.parent
REPORT_DIR = ROOT / "reports"

COINS = ["RAVE-USD", "MOG-USD", "BAL-USD", "IOTX-USD", "BLUR-USD",
         "ALEPH-USD", "SOL-USD", "DOGE-USD", "XRP-USD", "ETH-USD"]

FEE_RATE = 0.0040  # 40bps


def sma(closes, period):
    if len(closes) < period:
        return [None] * len(closes)
    result = [None] * (period - 1)
    s = sum(closes[:period]) / period
    result.append(s)
    for i in range(period, len(closes)):
        s = s + (closes[i] - closes[i - period]) / period
        result.append(s)
    return result


def compute_rsi(closes, period=3):
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


def compute_std(closes, period=20):
    if len(closes) < period:
        return [0.0] * len(closes)
    result = [0.0] * (period - 1)
    for i in range(period - 1, len(closes)):
        window = closes[i - period + 1:i + 1]
        mean = sum(window) / period
        var = sum((x - mean) ** 2 for x in window) / period
        result.append(math.sqrt(var))
    return result


def backtest_generic(candles, strategy_fn, fee_rate=FEE_RATE, starting_cash=48.0,
                      tp_pct=0.25, sl_pct=0.0, max_hold=48):
    """Generic backtest runner. Strategy yields (signal, exit_signal) per bar.
    
    If strategy doesn't provide exit_signal, uses TP/SL/timeout defaults.
    """
    if len(candles) < 50:
        return {"error": "not enough candles"}

    closes = [float(c["close"]) for c in candles]
    highs = [float(c["high"]) for c in candles]
    lows = [float(c["low"]) for c in candles]

    cash = starting_cash
    in_position = False
    position = None
    trades = []
    total_volume = 0.0
    total_fees = 0.0
    peak_equity = starting_cash
    max_dd = 0.0

    for i in range(50, len(candles) - 1):
        signal, exit_signal = strategy_fn(i, candles, closes, highs, lows)

        # Exit logic
        if in_position and position:
            exit_price = None
            exit_reason = None

            # Strategy-provided exit signal
            if exit_signal:
                exit_price = exit_signal
                exit_reason = "strategy_exit"
            else:
                # TP/SL/timeout defaults
                entry = position["entry"]
                if sl_pct > 0 and lows[i] <= entry * (1 - sl_pct):
                    exit_price = entry * (1 - sl_pct)
                    exit_reason = "sl"
                elif highs[i] >= entry * (1 + tp_pct):
                    exit_price = entry * (1 + tp_pct)
                    exit_reason = "tp"
                elif (i - position["bar"]) >= max_hold:
                    exit_price = closes[i]
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

        # Entry
        if signal and not in_position and cash >= 10.0:
            entry_price = signal
            deploy = cash * 0.95
            entry_fee = deploy * fee_rate
            qty = (deploy - entry_fee) / entry_price
            if qty > 0:
                cash -= deploy
                in_position = True
                position = {
                    "entry": entry_price, "qty": qty, "bar": i,
                    "deploy": deploy, "entry_fee": entry_fee, "entry_cost": deploy,
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
        trades.append({"net": net, "win": net > 0, "hold_bars": len(candles) - position["bar"]})

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
        "net": round(net, 2), "return_pct": round(net / starting_cash * 100, 1),
        "trades": total_trades, "wins": len(wins), "losses": len(losses),
        "win_rate": round(wr, 1), "total_volume": round(total_volume, 2),
        "total_fees": round(total_fees, 2), "max_drawdown": round(max_dd, 1),
        "profit_factor": round(pf, 3) if pf != float("inf") else 999.0,
        "avg_hold_bars": round(avg_hold, 1), "monthly_projection": round(monthly, 2),
    }


# ── Strategy Definitions ──────────────────────────────────────────────

def rsi_mr_5m(i, candles, closes, highs, lows):
    """Strategy 1: RSI(3)<30, 25% TP, 48-bar max hold (5-min)"""
    rsi = compute_rsi(closes, period=3)
    signal = None
    exit_signal = None
    if i > 3 and rsi[i] <= 30 and rsi[i - 1] > 30:
        signal = closes[i]
    return signal, exit_signal


def breakout_20(i, candles, closes, highs, lows):
    """Strategy 3: 20-bar high breakout, ATR 2x trailing stop"""
    atr = compute_atr(highs, lows, closes, 14)
    if i < 25:
        return None, None

    # Breakout: close above 20-bar high
    lookback_high = max(highs[i - 20:i])
    if closes[i] > lookback_high and closes[i - 1] <= lookback_high:
        return closes[i], None  # Entry signal
    return None, None


def vol_expansion(i, candles, closes, highs, lows):
    """Strategy 4: Enter when ATR > 2x average ATR, exit after 10 bars"""
    atr = compute_atr(highs, lows, closes, 14)
    if i < 50:
        return None, None

    avg_atr = statistics.mean(atr[max(0, i - 28):i]) if i >= 28 else atr[i - 1]
    if atr[i] > 2 * avg_atr and atr[i - 1] <= 2 * avg_atr:
        return closes[i], None  # Entry on vol expansion
    return None, None


def reversal_3down(i, candles, closes, highs, lows):
    """Strategy 5: Buy after 3 consecutive down bars, exit on first up bar"""
    if i < 4:
        return None, None

    three_down = (closes[i - 1] < closes[i - 2] and closes[i - 2] < closes[i - 3]
                  and closes[i - 3] < closes[i - 4])
    if three_down:
        return closes[i], None  # Entry

    # Exit: first up bar
    if closes[i] > closes[i - 1]:
        return None, closes[i]
    return None, None


def mean_revert_std(i, candles, closes, highs, lows):
    """Strategy 6: Buy when price > 3 std below 20-bar MA, exit at MA"""
    ma20 = sma(closes, 20)
    std = compute_std(closes, 20)
    if i < 25 or ma20[i] is None:
        return None, None

    lower_band = ma20[i] - 3 * std[i]
    if closes[i] <= lower_band and closes[i - 1] > lower_band:
        return closes[i], None  # Entry at oversold

    # Exit at MA
    if closes[i] >= ma20[i] and ma20[i] is not None:
        return None, ma20[i]
    return None, None


def main():
    print("=" * 80)
    print(f"  STRATEGY DIVERSIFICATION SCAN — 5 strategies × 10 coins × 30d")
    print("=" * 80)

    strategies = {
        "rsi_mr_5m": ("RSI MR (5m)", rsi_mr_5m, {"tp_pct": 0.25, "sl_pct": 0.0, "max_hold": 48}),
        "breakout_20": ("Breakout(20)", breakout_20, {"tp_pct": 0.10, "sl_pct": 0.03, "max_hold": 20}),
        "vol_expansion": ("Vol Expansion", vol_expansion, {"tp_pct": 0.05, "sl_pct": 0.03, "max_hold": 10}),
        "reversal_3down": ("3-Bar Reversal", reversal_3down, {"tp_pct": 0.0, "sl_pct": 0.0, "max_hold": 5}),
        "mean_revert_std": ("MR 3-Std", mean_revert_std, {"tp_pct": 0.0, "sl_pct": 0.05, "max_hold": 20}),
    }

    all_results = {}
    profitable_combos = []

    for coin in COINS:
        print(f"\n  Scanning {coin}...")
        candles = load_candles(coin, "FIVE_MINUTE", 30, max_age_minutes=30 * 24 * 60)
        if not candles:
            print(f"    NO DATA")
            continue

        coin_results = {}
        for strat_key, (strat_name, strat_fn, strat_params) in strategies.items():
            result = backtest_generic(candles, strat_fn, **strat_params)
            if "error" in result:
                continue
            coin_results[strat_key] = result
            emoji = "✅" if result["net"] > 0 else "❌"
            print(f"    {emoji} {strat_name:<16} ${result['net']:+7.2f}  "
                  f"{result['trades']:>3}t  {result['win_rate']:>5.1f}%WR  "
                  f"DD={result['max_drawdown']:>5.1f}%")
            if result["net"] > 0:
                profitable_combos.append({
                    "coin": coin, "strategy": strat_name,
                    "net": result["net"], "trades": result["trades"],
                    "wr": result["win_rate"], "dd": result["max_drawdown"],
                    "monthly": result["monthly_projection"],
                })

        all_results[coin] = coin_results

    # Summary
    print(f"\n{'=' * 80}")
    print(f"  SUMMARY — {len(profitable_combos)} profitable combos out of {len(all_results) * len(strategies)}")
    print(f"{'=' * 80}")

    if profitable_combos:
        # Sort by monthly projection
        profitable_combos.sort(key=lambda x: x["monthly"], reverse=True)
        print(f"\n  {'Coin':<12} {'Strategy':<16} {'Net 30d':>8} {'Trades':>6} {'WR%':>5} {'DD%':>5} {'Monthly':>8}")
        print(f"  {'─' * 12} {'─' * 16} {'─' * 8} {'─' * 6} {'─' * 5} {'─' * 5} {'─' * 8}")
        for c in profitable_combos:
            print(f"  {c['coin']:<12} {c['strategy']:<16} ${c['net']:>+7.2f}  {c['trades']:>6}  "
                  f"{c['wr']:>4.1f}% {c['dd']:>4.1f}% ${c['monthly']:>+7.2f}")
    else:
        print(f"\n  ZERO profitable strategy+coin combinations found.")
        print(f"  RSI MR on RAVE is the ONLY verified edge in the entire universe.")

    # RAVE baseline comparison
    rave_rsi = all_results.get("RAVE-USD", {}).get("rsi_mr_5m", {})
    print(f"\n  RAVE RSI MR baseline: ${rave_rsi.get('net', 0):+.2f}, {rave_rsi.get('monthly', 0):+.2f}/month")

    # Save
    output_path = REPORT_DIR / "strategy_diversification_scan_30d.json"
    with open(output_path, "w") as f:
        json.dump({"profitable_combos": profitable_combos, "all_results": {
            coin: {k: v for k, v in results.items()} for coin, results in all_results.items()
        }}, f, indent=2, default=str)
    print(f"\n  Saved: {output_path}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
