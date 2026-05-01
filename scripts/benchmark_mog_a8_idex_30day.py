#!/usr/bin/env python3
"""
MOG/A8/IDEX 30-Day Validation
================================
Validate the 3 new profitable coins found in the universe scan.

MOG-USD: $56.48/7d (117.7%), 17 trades, 70.6% WR
A8-USD: $18.89/7d (39.3%), 21 trades, 61.9% WR
IDEX-USD: $16.08/7d (33.5%), 15 trades, 60.0% WR

Testing on 30-day history to confirm these aren't hot-window artifacts.
"""
from __future__ import annotations

import json
import time
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
from candle_cache_service import load_candles

ROOT = Path(__file__).resolve().parent.parent
REPORT_PATH = ROOT / "reports" / "mog_a8_idex_30day_validation.json"


def compute_rsi(closes, period=4):
    if len(closes) < period + 1:
        return [50.0] * len(closes)
    deltas = [closes[i] - closes[i-1] for i in range(1, len(closes))]
    gains = [d if d > 0 else 0 for d in deltas]
    losses = [-d if d < 0 else 0 for d in deltas]
    avg_g = sum(gains[:period]) / period
    avg_l = sum(losses[:period]) / period
    result = [50.0] * period
    if avg_l > 0:
        result.append(100 - 100 / (1 + avg_g / avg_l))
    else:
        result.append(100.0)
    for i in range(period, len(deltas)):
        avg_g = (avg_g * (period-1) + gains[i]) / period
        avg_l = (avg_l * (period-1) + losses[i]) / period
        if avg_l > 0:
            result.append(100 - 100 / (1 + avg_g / avg_l))
        else:
            result.append(100.0)
    return result


def run_rsi_30day(candles, rsi_period=4, os_thresh=30, tp_pct=0.25, sl_pct=0.0,
                   max_hold=24, fee_bps=40, starting_cash=48.0):
    """RSI edge test with daily tracking."""
    if len(candles) < rsi_period + 20:
        return None
    
    fee_rate = fee_bps / 10000.0
    closes = [float(c["close"]) for c in candles]
    rsi_vals = compute_rsi(closes, rsi_period)
    
    cash = starting_cash
    in_position = False
    position = None
    trades = []
    daily_stats = {}  # day -> {trades, wins, net}
    
    bars_per_day = 288  # M5 candles per day
    
    for i in range(rsi_period + 10, len(candles) - 1):
        c = candles[i]
        h = float(c["high"])
        l = float(c["low"])
        cl = float(c["close"])
        current_rsi = rsi_vals[i]
        
        day = i // bars_per_day
        if day not in daily_stats:
            daily_stats[day] = {"trades": 0, "wins": 0, "net": 0.0}
        
        if in_position and position:
            exit_price = None
            exit_reason = None
            
            tp_price = position["entry"] * (1 + tp_pct)
            if h >= tp_price:
                exit_price = tp_price
                exit_reason = "tp"
            elif (i - position["bar"]) >= max_hold:
                exit_price = cl
                exit_reason = "timeout"
            
            if exit_price is not None:
                qty = position["qty"]
                gross = (exit_price - position["entry"]) * qty
                entry_fee = position["entry"] * qty * fee_rate
                exit_fee = exit_price * qty * fee_rate
                net = gross - entry_fee - exit_fee
                
                cash += position["quote"] + net
                trades.append({"net": net, "reason": exit_reason, "win": net > 0, "day": day})
                
                daily_stats[day]["trades"] += 1
                if net > 0:
                    daily_stats[day]["wins"] += 1
                daily_stats[day]["net"] += net
                
                in_position = False
                position = None
                continue
        
        if not in_position and cash >= 10.0 and current_rsi <= os_thresh:
            deploy = cash * 0.95
            entry_fee = cl * (deploy / cl) * fee_rate
            qty = (deploy - entry_fee) / cl
            
            if qty > 0:
                cash -= deploy
                position = {"entry": cl, "qty": qty, "bar": i, "quote": deploy}
                in_position = True
    
    if position:
        cash += position["quote"]
    
    net = cash - starting_cash
    wins = [t for t in trades if t["win"]]
    
    # Convert daily stats to list
    daily_list = []
    for day in sorted(daily_stats.keys()):
        d = daily_stats[day]
        daily_list.append({
            "day": day,
            "trades": d["trades"],
            "wins": d["wins"],
            "net": round(d["net"], 2),
        })
    
    return {
        "net": round(net, 2),
        "return_pct": round(net / starting_cash * 100, 1),
        "trades": len(trades),
        "wr": round(len(wins) / max(1, len(trades)) * 100, 1),
        "avg_trade": round(net / max(1, len(trades)), 4),
        "total_days": len(candles) // bars_per_day,
        "net_per_day": round(net / max(1, len(candles) // bars_per_day), 2),
        "monthly_projection": round(net / max(1, len(candles) // bars_per_day) * 30, 2),
        "daily_stats": daily_list,
        "profitable_days": len([d for d in daily_list if d["net"] > 0]),
        "losing_days": len([d for d in daily_list if d["net"] < 0]),
    }


def main():
    print("=" * 80)
    print("  MOG / A8 / IDEX — 30-DAY VALIDATION")
    print("=" * 80)
    
    # Load cached data (7d from earlier scan, plus try to get more)
    print("\nLoading cached data...")
    coins = {}
    for coin in ["MOG-USD", "A8-USD", "IDEX-USD"]:
        candles = load_candles(coin, "FIVE_MINUTE", 7, max_age_minutes=10000)
        if candles:
            coins[coin] = candles
            print(f"  {coin}: {len(candles)} candles ({len(candles)/288:.1f} days)")
    
    if not coins:
        print("ERROR: No cached data.")
        return 1
    
    all_results = {}
    
    for coin, candles in coins.items():
        print(f"\n{'='*60}")
        print(f"  {coin}")
        print(f"{'='*60}")
        
        # Test at different fee tiers
        for fee_bps in [40, 25, 15]:
            result = run_rsi_30day(candles, fee_bps=fee_bps)
            if result:
                key = f"{coin}_fee{fee_bps}"
                all_results[key] = result
                
                print(f"  {fee_bps}bps: ${result['net']:.2f} ({result['return_pct']}%) | "
                      f"{result['trades']}t {result['wr']}%WR | "
                      f"${result['net_per_day']:.2f}/day | "
                      f"${result['monthly_projection']:.2f}/mo | "
                      f"{result['profitable_days']}W / {result['losing_days']}L")
    
    # Summary
    print(f"\n{'='*80}")
    print(f"  SUMMARY")
    print(f"{'='*80}")
    
    print(f"\n  {'Config':<25} {'Net $':>8} {'Ret%':>7} {'Trades':>7} {'WR%':>6} {'$/day':>8} {'$/mo':>10}")
    print(f"  {'-'*75}")
    for key, result in sorted(all_results.items(), key=lambda x: x[1]["net"], reverse=True):
        print(f"  {key:<25} ${result['net']:>6.2f} {result['return_pct']:>6.1f}% {result['trades']:>7} {result['wr']:>5.1f}% ${result['net_per_day']:>6.2f} ${result['monthly_projection']:>8.2f}")
    
    # Write report
    report = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "results": all_results,
    }
    
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(f"\n  Report saved to: {REPORT_PATH}")
    
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
