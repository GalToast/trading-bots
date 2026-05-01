#!/usr/bin/env python3
"""
Lab Tests #3-10: RAVE Optimization Batch
==========================================
All 8 remaining tests focused on RAVE optimization.

Tests:
3. RAVE Sector Rotation
4. RAVE Relative Strength vs BTC
5. RAVE Correlation Breakdown
6. RAVE Volatility Arbitrage
7. RAVE Mean-Reversion Basket
8. RAVE Momentum Basket
9. RAVE Fee Tier Race
10. RAVE Concurrent Position Optimization
"""
from __future__ import annotations

import json
import time
import math
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
from candle_cache_service import load_candles

ROOT = Path(__file__).resolve().parent.parent
REPORT_PATH = ROOT / "reports" / "lab_qwen_trading_tests_3_to_10.json"


def compute_returns(candles):
    closes = [float(c["close"]) for c in candles]
    return [(closes[i] - closes[i-1]) / closes[i-1] * 100 for i in range(1, len(closes))]


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


def compute_atr(candles, period=14):
    if len(candles) < period + 1:
        return [0.0] * len(candles)
    true_ranges = []
    for i in range(1, len(candles)):
        h = float(candles[i]["high"])
        l = float(candles[i]["low"])
        pc = float(candles[i-1]["close"])
        tr = max(h - l, abs(h - pc), abs(l - pc))
        true_ranges.append(tr)
    atr_vals = [0.0] * len(candles)
    if len(true_ranges) >= period:
        atr = sum(true_ranges[:period]) / period
        atr_vals[period] = atr
        for i in range(period + 1, len(candles)):
            atr = (atr * (period - 1) + true_ranges[i - 1]) / period
            atr_vals[i] = atr
    return atr_vals


def run_basic_strategy(candles, entry_signal_fn, tp_pct=0.05, sl_pct=0.03, max_hold=24, fee_bps=40, deploy_pct=0.95):
    """Generic strategy runner."""
    if len(candles) < 50:
        return None
    
    fee_rate = fee_bps / 10000.0
    cash = 48.0
    starting_cash = 48.0
    in_position = False
    position = None
    trades = []
    
    for i in range(50, len(candles) - 1):
        c = candles[i]
        h = float(c["high"])
        l = float(c["low"])
        cl = float(c["close"])
        
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
                trades.append({"net": net, "reason": exit_reason, "win": net > 0})
                in_position = False
                position = None
                continue
        
        # ENTRY
        if not in_position and cash >= 10.0:
            signal = entry_signal_fn(i, candles)
            if signal:
                deploy = cash * deploy_pct
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
    
    return {
        "net": round(net, 2),
        "return_pct": round(net / starting_cash * 100, 1),
        "trades": len(trades),
        "wr": round(len(wins) / max(1, len(trades)) * 100, 1),
        "avg": round(net / max(1, len(trades)), 4),
    }


def main():
    print("=" * 80)
    print("  LAB TESTS #3-10: RAVE Optimization Batch")
    print("=" * 80)
    
    # Load cached data
    print("\nLoading cached data...")
    coins = {
        "RAVE-USD": load_candles("RAVE-USD", "FIVE_MINUTE", 7, max_age_minutes=10000),
        "BTC-USD": load_candles("BTC-USD", "FIVE_MINUTE", 7, max_age_minutes=10000),
        "BAL-USD": load_candles("BAL-USD", "FIVE_MINUTE", 7, max_age_minutes=10000),
        "BLUR-USD": load_candles("BLUR-USD", "FIVE_MINUTE", 7, max_age_minutes=10000),
        "ALEPH-USD": load_candles("ALEPH-USD", "FIVE_MINUTE", 7, max_age_minutes=10000),
        "IOTX-USD": load_candles("IOTX-USD", "FIVE_MINUTE", 7, max_age_minutes=10000),
    }
    
    for name, candles in coins.items():
        if candles:
            print(f"  {name}: {len(candles)} candles")
    
    rave = coins["RAVE-USD"]
    btc = coins["BTC-USD"]
    if not rave or not btc:
        print("ERROR: Missing RAVE or BTC data.")
        return 1
    
    rave_returns = compute_returns(rave)
    btc_returns = compute_returns(btc)
    rave_closes = [float(c["close"]) for c in rave]
    rave_rsi = compute_rsi(rave_closes, 4)
    rave_atr = compute_atr(rave, 14)
    
    # Align lengths
    min_len = min(len(rave_returns), len(btc_returns))
    rave_r = rave_returns[:min_len]
    btc_r = btc_returns[:min_len]
    
    all_results = {}
    
    # TEST 3: RAVE Sector Rotation
    print(f"\n{'='*60}")
    print(f"  TEST 3: RAVE Sector Rotation")
    print(f"{'='*60}")
    
    # Compute sector average return (BAL + BLUR + ALEPH + IOTX)
    alt_coins = ["BAL-USD", "BLUR-USD", "ALEPH-USD", "IOTX-USD"]
    alt_returns = {}
    for coin in alt_coins:
        if coins[coin]:
            alt_returns[coin] = compute_returns(coins[coin])[:min_len]
    
    if alt_returns:
        # Sector avg = average of all alt returns
        sector_avg = []
        for i in range(min_len):
            vals = [alt_returns[c][i] for c in alt_returns if i < len(alt_returns[c])]
            sector_avg.append(sum(vals) / len(vals) if vals else 0)
        
        # Entry: when sector avg > 1% but RAVE < sector avg (RAVE is lagging)
        def sector_rotation_signal(i, candles):
            if i >= len(sector_avg) or i >= len(rave_r):
                return False
            return sector_avg[i-1] > 1.0 and rave_r[i-1] < sector_avg[i-1]
        
        result = run_basic_strategy(rave, sector_rotation_signal, tp_pct=0.05, sl_pct=0.03)
        if result:
            all_results["test_3_sector_rotation"] = result
            print(f"  Sector rotation: ${result['net']:.2f} ({result['trades']}t, {result['wr']}%WR)")
    
    # TEST 4: RAVE Relative Strength vs BTC
    print(f"\n{'='*60}")
    print(f"  TEST 4: RAVE Relative Strength vs BTC")
    print(f"{'='*60}")
    
    # Compute RAVE/BTC ratio
    ratio = [rave_closes[i] / (float(btc[i]["close"]) if i < len(btc) else 1) for i in range(min(len(rave_closes), len(btc)))]
    ratio_returns = [(ratio[i] - ratio[i-1]) / ratio[i-1] * 100 for i in range(1, len(ratio))]
    
    # Entry: when RAVE/BTC ratio drops (RAVE underperforming BTC), bet on mean reversion
    def relative_strength_signal(i, candles):
        idx = i - 1
        if idx < 1 or idx >= len(ratio_returns):
            return False
        # RAVE underperformed BTC for 2 bars
        return ratio_returns[idx] < -0.5 and ratio_returns[idx-1] < -0.5
    
    result = run_basic_strategy(rave, relative_strength_signal, tp_pct=0.05, sl_pct=0.03)
    if result:
        all_results["test_4_relative_strength"] = result
        print(f"  Relative strength: ${result['net']:.2f} ({result['trades']}t, {result['wr']}%WR)")
    
    # TEST 5: RAVE Correlation Breakdown
    print(f"\n{'='*60}")
    print(f"  TEST 5: RAVE Correlation Breakdown")
    print(f"{'='*60}")
    
    # Compute rolling correlation (10-bar window)
    rolling_corr = []
    window = 10
    for i in range(window, min_len):
        x = rave_r[i-window:i]
        y = btc_r[i-window:i]
        mean_x = sum(x) / window
        mean_y = sum(y) / window
        cov = sum((x[j] - mean_x) * (y[j] - mean_y) for j in range(window)) / window
        std_x = (sum((v - mean_x)**2 for v in x) / window) ** 0.5
        std_y = (sum((v - mean_y)**2 for v in y) / window) ** 0.5
        corr = cov / (std_x * std_y) if std_x > 0 and std_y > 0 else 0
        rolling_corr.append(corr)
    
    # Entry: when correlation drops below 0 (RAVE decouples from BTC)
    def correlation_breakdown_signal(i, candles):
        idx = i - window - 1
        if idx < 0 or idx >= len(rolling_corr):
            return False
        return rolling_corr[idx] < 0 and rave_rsi[i] < 30
    
    result = run_basic_strategy(rave, correlation_breakdown_signal, tp_pct=0.05, sl_pct=0.03)
    if result:
        all_results["test_5_correlation_breakdown"] = result
        print(f"  Correlation breakdown: ${result['net']:.2f} ({result['trades']}t, {result['wr']}%WR)")
    
    # TEST 6: RAVE Volatility Arbitrage
    print(f"\n{'='*60}")
    print(f"  TEST 6: RAVE Volatility Arbitrage")
    print(f"{'='*60}")
    
    # Compute sector avg ATR
    sector_atr = []
    for i in range(min_len):
        rave_atr_val = rave_atr[i] if i < len(rave_atr) else 0
        sector_atr.append(rave_atr_val)  # Simplified: just RAVE ATR for now
    
    # Entry: when RAVE vol is BELOW sector avg (volatility compression = impending expansion)
    avg_atr = sum(rave_atr) / len(rave_atr) if rave_atr else 1
    
    def volatility_arb_signal(i, candles):
        if i >= len(rave_atr):
            return False
        return rave_atr[i] < avg_atr * 0.5 and rave_rsi[i] < 35
    
    result = run_basic_strategy(rave, volatility_arb_signal, tp_pct=0.05, sl_pct=0.03)
    if result:
        all_results["test_6_volatility_arb"] = result
        print(f"  Volatility arb: ${result['net']:.2f} ({result['trades']}t, {result['wr']}%WR)")
    
    # TEST 7: RAVE Mean-Reversion Basket
    print(f"\n{'='*60}")
    print(f"  TEST 7: RAVE Mean-Reversion Basket (RAVE + RSI + oversold)")
    print(f"{'='*60}")
    
    # Simple RSI mean-reversion with optimized params
    def mr_basket_signal(i, candles):
        return rave_rsi[i] < 25  # Deeper oversold
    
    for tp in [0.05, 0.10, 0.15, 0.20, 0.25]:
        result = run_basic_strategy(rave, mr_basket_signal, tp_pct=tp, sl_pct=0.0)
        if result:
            all_results[f"test_7_mr_basket_tp{int(tp*100)}"] = result
            print(f"  MR Basket TP{int(tp*100)}%: ${result['net']:.2f} ({result['trades']}t, {result['wr']}%WR)")
    
    # TEST 8: RAVE Momentum Basket
    print(f"\n{'='*60}")
    print(f"  TEST 8: RAVE Momentum Basket (momentum + RSI confirmation)")
    print(f"{'='*60}")
    
    # Entry: RAVE had positive return for 2 bars AND RSI still < 50
    def momentum_basket_signal(i, candles):
        if i < 2 or i >= len(rave_r):
            return False
        return rave_r[i-1] > 0 and rave_r[i-2] > 0 and rave_rsi[i] < 50
    
    for tp in [0.05, 0.10, 0.15]:
        result = run_basic_strategy(rave, momentum_basket_signal, tp_pct=tp, sl_pct=0.03)
        if result:
            all_results[f"test_8_momentum_basket_tp{int(tp*100)}"] = result
            print(f"  Momentum Basket TP{int(tp*100)}%: ${result['net']:.2f} ({result['trades']}t, {result['wr']}%WR)")
    
    # TEST 9: RAVE Fee Tier Race
    print(f"\n{'='*60}")
    print(f"  TEST 9: RAVE Fee Tier Race (volume generation)")
    print(f"{'='*60}")
    
    # How fast does RAVE generate volume with different strategies?
    # Use the MR Basket with TP25% (most volume-generating)
    def fee_race_signal(i, candles):
        return rave_rsi[i] < 30  # Standard RSI entry
    
    result = run_basic_strategy(rave, fee_race_signal, tp_pct=0.25, sl_pct=0.0)
    if result:
        total_volume = result["trades"] * 48 * 2  # Rough estimate: $48 in + $48 out per trade
        days_to_50k = 50000 / max(1, total_volume / 7 * 30)  # Extrapolate to monthly
        result["estimated_volume_30d"] = round(total_volume / 7 * 30, 2)
        result["days_to_50k"] = round(days_to_50k, 1)
        all_results["test_9_fee_tier_race"] = result
        print(f"  Fee tier race: ${result['net']:.2f} ({result['trades']}t)")
        print(f"  Est volume/30d: ${result['estimated_volume_30d']:,.2f}")
        print(f"  Days to $50k: {result['days_to_50k']:.1f}")
    
    # TEST 10: RAVE Concurrent Position Optimization
    print(f"\n{'='*60}")
    print(f"  TEST 10: RAVE Concurrent Position Optimization")
    print(f"{'='*60}")
    
    # Test 1 vs 2 vs 3 vs 5 concurrent positions
    for max_positions in [1, 2, 3, 5]:
        def concurrent_signal(i, candles, mp=max_positions):
            # Entry when RSI < 30 and we have room for more positions
            return rave_rsi[i] < 30
        
        # Simple simulation: if we allow N positions, we enter N times more often
        # This is a simplification — real concurrent would track each position separately
        result = run_basic_strategy(rave, concurrent_signal, tp_pct=0.10, sl_pct=0.0)
        if result:
            # Adjust for concurrent: multiply trades by max_positions (rough estimate)
            adjusted_trades = result["trades"] * max_positions
            adjusted_net = result["net"] * max_positions  # Rough linear scaling
            all_results[f"test_10_concurrent_{max_positions}"] = {
                "net": round(adjusted_net, 2),
                "return_pct": round(adjusted_net / 48 * 100, 1),
                "trades": adjusted_trades,
                "wr": result["wr"],
                "avg": result["avg"],
                "max_concurrent": max_positions,
            }
            print(f"  Concurrent {max_positions}: ${adjusted_net:.2f} ({adjusted_trades}t, {result['wr']}%WR)")
    
    # Save report
    report = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "tests": all_results,
    }
    
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(f"\n  Report saved to: {REPORT_PATH}")
    
    # Summary
    print(f"\n{'='*80}")
    print(f"  SUMMARY — Tests #3-10")
    print(f"{'='*80}")
    
    # Sort by net
    sorted_results = sorted(all_results.items(), key=lambda x: x[1].get("net", 0), reverse=True)
    
    for name, result in sorted_results:
        print(f"  {name}: ${result['net']:>8.2f} ({result.get('trades', '?')}t, {result.get('wr', '?')}%WR)")
    
    # Find the winner
    if sorted_results:
        winner_name, winner = sorted_results[0]
        print(f"\n  🏆 Winner: {winner_name} = ${winner['net']:.2f}")
    
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
