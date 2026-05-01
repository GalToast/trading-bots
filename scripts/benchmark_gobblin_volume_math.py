#!/usr/bin/env python3
"""
Gobblin Volume Math Validation
================================
@gemini's thesis: Run a high-frequency volume swarm to hit $50k in 48h → unlock 15bps fees → 
                   every rsi_parallel trade becomes 2.6x more profitable.

Testing whether this is actually achievable:
1. Can we generate $50k volume in 48h without bleeding capital?
2. What's the minimum spread needed to break even at 40bps while churning?
3. How long does it take to hit $50k at different churn rates?
4. What happens to capital during the churn (drawdown risk)?
"""
from __future__ import annotations

import json
import time
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
from candle_cache_service import load_candles
from coinbase_advanced_client import CoinbaseAdvancedClient

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_REPORT_PATH = ROOT / "reports" / "gobblin_volume_validation.json"


def run_gobblin_churn(candles, spread_pct, fee_bps, starting_cash=48.0, churn_interval_bars=2):
    """
    Simulate Gobblin market-making churn.
    
    Strategy: Buy at candle close, sell at next candle open (or vice versa),
    capturing the spread. This is the market-making churn that generates volume.
    
    Args:
        candles: List of candle dicts
        spread_pct: The spread we're trying to capture (as %)
        fee_bps: Fee tier in basis points
        starting_cash: Starting capital
        churn_interval_bars: How often to churn (every N bars)
    
    Returns:
        Dict with volume, PnL, drawdown, time-to-50k, etc.
    """
    if len(candles) < churn_interval_bars + 5:
        return None
    
    fee_rate = fee_bps / 10000.0
    cash = starting_cash
    position = None  # "long" or "short"
    entry_price = 0
    entry_fee = 0
    entry_bar = 0
    
    total_volume = 0.0
    total_fees = 0.0
    total_pnl = 0.0
    max_drawdown = 0.0
    peak_cash = starting_cash
    trades = []
    churn_count = 0
    
    for i in range(churn_interval_bars, len(candles)):
        c = candles[i]
        cl = float(c["close"])
        op = float(c["open"])
        h = float(c["high"])
        l = float(c["low"])
        
        # EXIT previous position
        if position:
            exit_price = cl
            qty = entry_price  # Simplified: qty = 1 unit for volume calculation
            
            # Simulate spread capture
            if position == "long":
                # Bought at open, selling at close
                gross_pnl = (cl - entry_price) / entry_price
            else:
                # Short simulation (for volume churn only)
                gross_pnl = (entry_price - cl) / entry_price
            
            # Apply spread as edge
            effective_pnl = gross_pnl + (spread_pct / 100)
            
            entry_fee_amt = entry_price * fee_rate
            exit_fee_amt = cl * fee_rate
            net_pnl = effective_pnl - entry_fee_amt - exit_fee_amt
            
            cash += net_pnl
            total_fees += entry_fee_amt + exit_fee_amt
            total_volume += entry_price + cl
            total_pnl += net_pnl
            churn_count += 1
            
            if cash > peak_cash:
                peak_cash = cash
            drawdown = (peak_cash - cash) / peak_cash * 100
            if drawdown > max_drawdown:
                max_drawdown = drawdown
            
            trades.append({
                "bar": i,
                "pnl": net_pnl,
                "volume": entry_price + cl,
                "cash": cash,
            })
            
            position = None
        
        # ENTER new churn trade (every N bars)
        if position is None and i % churn_interval_bars == 0 and cash >= 10.0:
            entry_price = cl
            entry_fee = cl * fee_rate
            position = "long"  # Simple long for churn
            entry_bar = i
    
    # Close final position
    if position:
        total_volume += entry_price
        total_fees += entry_fee
    
    bars = len(candles)
    hours = bars / 12  # M5 candles: 12 per hour
    days = hours / 24
    vol_per_day = total_volume / max(0.001, days)
    vol_per_48h = vol_per_day * 2
    time_to_50k_days = 50000 / max(0.001, vol_per_day)
    
    return {
        "starting_cash": starting_cash,
        "ending_cash": round(cash, 2),
        "net_pnl": round(total_pnl, 2),
        "return_pct": round(total_pnl / starting_cash * 100, 1),
        "total_volume": round(total_volume, 2),
        "total_fees": round(total_fees, 2),
        "churn_trades": churn_count,
        "max_drawdown_pct": round(max_drawdown, 1),
        "peak_cash": round(peak_cash, 2),
        "bars": bars,
        "hours": round(hours, 1),
        "days": round(days, 2),
        "volume_per_day": round(vol_per_day, 2),
        "volume_per_48h": round(vol_per_48h, 2),
        "time_to_50k_days": round(time_to_50k_days, 1),
        "volume_per_trade": round(total_volume / max(1, churn_count), 2),
    }


def run_realistic_gobblin(candles, coin_params, fee_bps=40, starting_cash=48.0):
    """
    Realistic Gobblin: Enter RSI signals, capture TP, generate volume as byproduct.
    
    This tests the ACTUAL thesis: rsi_parallel trades generate volume as a side effect,
    not pure market-making churn.
    """
    if len(candles) < 50:
        return None
    
    fee_rate = fee_bps / 10000.0
    cash = starting_cash
    in_position = False
    position = None
    
    total_volume = 0.0
    total_fees = 0.0
    total_pnl = 0.0
    trades = []
    
    rsi_period = coin_params.get("p", 4)
    rsi_entry = coin_params.get("os", 30)
    tp_pct = coin_params.get("t", 5) / 100.0
    sl_pct = coin_params.get("s", 3) / 100.0
    rsi_exit = coin_params.get("ob", 70)
    
    # Compute RSI
    closes = [float(c["close"]) for c in candles]
    deltas = [closes[i] - closes[i-1] for i in range(1, len(closes))]
    gains = [d if d > 0 else 0 for d in deltas]
    losses = [-d if d < 0 else 0 for d in deltas]
    avg_g = sum(gains[:rsi_period]) / rsi_period
    avg_l = sum(losses[:rsi_period]) / rsi_period
    rsi_vals = [50.0] * rsi_period
    if avg_l > 0:
        rsi_vals.append(100 - 100 / (1 + avg_g / avg_l))
    else:
        rsi_vals.append(100.0)
    for i in range(rsi_period, len(deltas)):
        avg_g = (avg_g * (rsi_period-1) + gains[i]) / rsi_period
        avg_l = (avg_l * (rsi_period-1) + losses[i]) / rsi_period
        if avg_l > 0:
            rsi_vals.append(100 - 100 / (1 + avg_g / avg_l))
        else:
            rsi_vals.append(100.0)
    
    for i in range(rsi_period + 10, len(candles) - 1):
        c = candles[i]
        cl = float(c["close"])
        h = float(c["high"])
        l = float(c["low"])
        current_rsi = rsi_vals[i]
        
        # EXIT
        if in_position and position:
            exit_price = None
            exit_reason = None
            
            tp_price = position["entry"] * (1 + tp_pct)
            sl_price = position["entry"] * (1 - sl_pct)
            
            if h >= tp_price:
                exit_price = tp_price
                exit_reason = "tp"
            elif l <= sl_price:
                exit_price = sl_price
                exit_reason = "sl"
            elif current_rsi >= rsi_exit:
                exit_price = cl
                exit_reason = "rsi_exit"
            
            if exit_price is not None:
                qty = position["qty"]
                gross = (exit_price - position["entry"]) * qty
                entry_fee = position["entry"] * qty * fee_rate
                exit_fee = exit_price * qty * fee_rate
                net = gross - entry_fee - exit_fee
                
                cash += position["quote"] + net
                total_volume += position["quote"] + (exit_price * qty)
                total_fees += entry_fee + exit_fee
                total_pnl += net
                
                trades.append({"bar": i, "pnl": net, "reason": exit_reason, "volume": position["quote"] + (exit_price * qty)})
                in_position = False
                position = None
                continue
        
        # ENTRY
        if not in_position and cash >= 10.0 and current_rsi <= rsi_entry:
            deploy = cash
            entry_fee = cl * (deploy / cl) * fee_rate
            qty = (deploy - entry_fee) / cl
            
            if qty > 0:
                cash -= deploy
                position = {"entry": cl, "qty": qty, "quote": deploy, "bar": i}
                in_position = True
    
    if position:
        cash += position["quote"]
        total_volume += position["quote"]
    
    bars = len(candles)
    hours = bars / 12
    days = hours / 24
    vol_per_day = total_volume / max(0.001, days)
    vol_per_48h = vol_per_day * 2
    time_to_50k_days = 50000 / max(0.001, vol_per_day)
    
    return {
        "starting_cash": starting_cash,
        "ending_cash": round(cash, 2),
        "net_pnl": round(total_pnl, 2),
        "return_pct": round(total_pnl / starting_cash * 100, 1),
        "total_volume": round(total_volume, 2),
        "total_fees": round(total_fees, 2),
        "trades": len(trades),
        "win_rate": round(len([t for t in trades if t["pnl"] > 0]) / max(1, len(trades)) * 100, 1),
        "bars": bars,
        "hours": round(hours, 1),
        "days": round(days, 2),
        "volume_per_day": round(vol_per_day, 2),
        "volume_per_48h": round(vol_per_48h, 2),
        "time_to_50k_days": round(time_to_50k_days, 1),
    }


def main():
    print("=" * 80)
    print("  GOBBLIN VOLUME MATH VALIDATION")
    print("=" * 80)
    
    # Load cached data
    print("\nLoading cached candle data...")
    
    coins_data = {}
    for coin, gran, days in [
        ("BLUR-USD", "FIVE_MINUTE", 30),
        ("RAVE-USD", "FIVE_MINUTE", 30),
        ("ALEPH-USD", "FIVE_MINUTE", 30),
        ("BAL-USD", "FIVE_MINUTE", 30),
    ]:
        candles = load_candles(coin, gran, days, max_age_minutes=10000)
        if candles:
            coins_data[coin] = candles
            print(f"  {coin}: {len(candles)} candles ({len(candles)/12/24:.1f} days)")
    
    if not coins_data:
        print("ERROR: No cached data found. Run candle_cache_service.py first.")
        return 1
    
    all_results = []
    
    # TEST 1: Pure volume churn (market-making simulation)
    print(f"\n{'='*80}")
    print(f"  TEST 1: Pure Volume Churn (Market-Making)")
    print(f"{'='*80}")
    
    for coin, candles in coins_data.items():
        for spread in [0.1, 0.5, 1.0, 2.0]:
            result = run_gobblin_churn(candles, spread_pct=spread, fee_bps=40, churn_interval_bars=2)
            if result:
                result["coin"] = coin
                result["test"] = "pure_churn"
                result["spread_pct"] = spread
                all_results.append(result)
                print(f"  {coin} spread={spread}%: ${result['net_pnl']:+.2f} | Vol/day=${result['volume_per_day']:,.0f} | "
                      f"To 50k: {result['time_to_50k_days']:.1f}d | DD: {result['max_drawdown_pct']:.1f}%")
    
    # TEST 2: Realistic Gobblin (RSI signals generating volume)
    print(f"\n{'='*80}")
    print(f"  TEST 2: Realistic Gobblin (RSI Signals as Volume Engine)")
    print(f"{'='*80}")
    
    coin_params = {
        "BLUR-USD": {"p": 4, "os": 30, "t": 5, "s": 3, "ob": 70},
        "RAVE-USD": {"p": 4, "os": 30, "t": 5, "s": 3, "ob": 70},
        "ALEPH-USD": {"p": 4, "os": 30, "t": 5, "s": 3, "ob": 70},
        "BAL-USD": {"p": 4, "os": 30, "t": 5, "s": 3, "ob": 70},
    }
    
    for coin, candles in coins_data.items():
        params = coin_params.get(coin, {})
        for fee_bps in [40, 25, 15]:
            result = run_realistic_gobblin(candles, params, fee_bps=fee_bps)
            if result:
                result["coin"] = coin
                result["test"] = "realistic_gobblin"
                result["fee_bps"] = fee_bps
                all_results.append(result)
                print(f"  {coin} {fee_bps}bps: ${result['net_pnl']:+.2f} ({result['return_pct']}%) | "
                      f"{result['trades']}t {result['win_rate']}%WR | Vol/day=${result['volume_per_day']:,.0f} | "
                      f"To 50k: {result['time_to_50k_days']:.1f}d")
    
    # TEST 3: Multi-coin swarm (combined volume from 4 coins)
    print(f"\n{'='*80}")
    print(f"  TEST 3: Multi-Coin Gobblin Swarm (Combined Volume)")
    print(f"{'='*80}")
    
    # Sum volume from all coins running simultaneously
    total_vol_day = 0
    total_pnl = 0
    total_trades = 0
    total_days = 0
    
    for coin, candles in coins_data.items():
        params = coin_params.get(coin, {})
        result = run_realistic_gobblin(candles, params, fee_bps=40)
        if result:
            total_vol_day += result["volume_per_day"]
            total_pnl += result["net_pnl"]
            total_trades += result["trades"]
            total_days = max(total_days, result["days"])
    
    swarm_vol_48h = total_vol_day * 2
    swarm_time_to_50k = 50000 / max(0.001, total_vol_day)
    
    print(f"  Combined 4-coin swarm:")
    print(f"    Volume/day: ${total_vol_day:,.0f}")
    print(f"    Volume/48h: ${swarm_vol_48h:,.0f}")
    print(f"    Time to $50k: {swarm_time_to_50k:.1f} days ({swarm_time_to_50k/24:.1f} hours)")
    print(f"    Total PnL: ${total_pnl:+.2f}")
    print(f"    Total trades: {total_trades}")
    
    swarm_result = {
        "test": "multi_coin_swarm",
        "coins": list(coins_data.keys()),
        "volume_per_day": round(total_vol_day, 2),
        "volume_per_48h": round(swarm_vol_48h, 2),
        "time_to_50k_days": round(swarm_time_to_50k, 1),
        "time_to_50k_hours": round(swarm_time_to_50k * 24, 1),
        "total_pnl": round(total_pnl, 2),
        "total_trades": total_trades,
        "total_days": round(total_days, 2),
    }
    all_results.append(swarm_result)
    
    # Summary
    print(f"\n{'='*80}")
    print(f"  GOBBLIN VALIDATION SUMMARY")
    print(f"{'='*80}")
    
    print(f"\n  {'Test':<20} {'Coin':<12} {'Net $':>8} {'Vol/day':>12} {'To $50k':>10} {'DD%':>6}")
    print(f"  {'-'*70}")
    
    for r in all_results:
        test = r.get("test", "?")
        coin = r.get("coin", "swarm")
        net = r.get("net_pnl", 0)
        vol = r.get("volume_per_day", 0)
        to_50k = r.get("time_to_50k_days", 0)
        dd = r.get("max_drawdown_pct", 0)
        
        if test == "multi_coin_swarm":
            print(f"  {'Swarm (4 coins)':<20} {'ALL':<12} ${r['total_pnl']:>6.2f} ${vol:>10,.0f} {to_50k:>8.1f}d {'—':>6}")
        else:
            print(f"  {test:<20} {coin:<12} ${net:>6.2f} ${vol:>10,.0f} {to_50k:>8.1f}d {dd:>5.1f}%")
    
    # Verdict
    print(f"\n  VERDICT:")
    if swarm_time_to_50k < 48:
        print(f"  ✅ GOBBLIN VALIDATED — $50k in {swarm_time_to_50k/24:.1f} hours with 4-coin swarm")
    elif swarm_time_to_50k < 168:  # 1 week
        print(f"  ⚠️  GOBBLIN PARTIALLY VALIDATED — $50k in {swarm_time_to_50k:.1f} days ({swarm_time_to_50k/24:.1f}h)")
    else:
        print(f"  ❌ GOBBLIN NOT VIABLE — $50k in {swarm_time_to_50k:.1f} days ({swarm_time_to_50k/7:.1f} weeks)")
    
    # Write report
    report = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "swarm_summary": swarm_result,
        "all_results": all_results,
    }
    
    out = Path(DEFAULT_REPORT_PATH)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(f"\n  Report: {out}")
    
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
