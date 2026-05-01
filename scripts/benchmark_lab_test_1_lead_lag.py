#!/usr/bin/env python3
"""
Lab Test #1: BTC → Altcoin Lead-Lag
=====================================
Does BTC movement predict altcoin movement with 1-5 bar delay?

If BTC moves > X% in a bar, does RAVE/BAL/IOTX follow in the NEXT 1-5 bars?

This is a STRUCTURAL edge based on market plumbing, not indicators.
"""
from __future__ import annotations

import json
import time
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
from candle_cache_service import load_candles

ROOT = Path(__file__).resolve().parent.parent
REPORT_PATH = ROOT / "reports" / "lab_qwen_trading_test_1_lead_lag.json"


def compute_correlation(x, y, lag=0):
    """Compute correlation between x and y with optional lag."""
    if lag > 0:
        x = x[lag:]
        y = y[:len(x)]
    if len(x) < 10:
        return 0.0
    
    n = len(x)
    mean_x = sum(x) / n
    mean_y = sum(y) / n
    
    cov = sum((x[i] - mean_x) * (y[i] - mean_y) for i in range(n)) / n
    std_x = (sum((xi - mean_x) ** 2 for xi in x) / n) ** 0.5
    std_y = (sum((yi - mean_y) ** 2 for yi in y) / n) ** 0.5
    
    if std_x == 0 or std_y == 0:
        return 0.0
    
    return cov / (std_x * std_y)


def run_lead_lag_test(btc_returns, alt_returns, alt_name, lags=[1, 2, 3, 5, 10]):
    """Test if BTC returns predict altcoin returns with lag."""
    results = {}
    
    for lag in lags:
        corr = compute_correlation(btc_returns, alt_returns, lag=lag)
        results[lag] = round(corr, 4)
    
    # Find the lag with highest correlation
    best_lag = max(results, key=results.get) if results else 0
    best_corr = results.get(best_lag, 0)
    
    # Conditional probability: when BTC moves > threshold, what's alt's expected return?
    thresholds = [0.5, 1.0, 1.5, 2.0]
    conditional = {}
    
    for thresh in thresholds:
        # Find bars where BTC moved > threshold
        btc_moves = [i for i, r in enumerate(btc_returns) if abs(r) > thresh]
        if len(btc_moves) < 5:
            conditional[thresh] = {"count": len(btc_moves), "alt_avg_return": 0, "alt_win_rate": 0}
            continue
        
        # Check alt returns in NEXT lag bars
        alt_returns_after = []
        alt_wins = 0
        for i in btc_moves:
            for lag in [1, 2, 3]:
                if i + lag < len(alt_returns):
                    alt_returns_after.append(alt_returns[i + lag])
                    if alt_returns[i + lag] > 0:
                        alt_wins += 1
        
        avg_return = sum(alt_returns_after) / len(alt_returns_after) if alt_returns_after else 0
        win_rate = alt_wins / len(alt_returns_after) * 100 if alt_returns_after else 0
        
        conditional[thresh] = {
            "count": len(btc_moves),
            "alt_avg_return": round(avg_return, 4),
            "alt_win_rate": round(win_rate, 1),
            "total_follow_ups": len(alt_returns_after),
        }
    
    return {
        "correlations_by_lag": results,
        "best_lag": best_lag,
        "best_correlation": best_corr,
        "conditional_probability": conditional,
    }


def run_lead_lag_strategy(btc_returns, alt_returns, alt_candles, lag=1, btc_threshold=1.0):
    """
    Strategy: When BTC moves > threshold, enter altcoin with lag.
    Entry: 1 bar after BTC move, exit after 3 bars or TP/SL.
    """
    if len(alt_returns) < 10 or len(alt_candles) < 10:
        return None
    
    cash = 48.0
    starting_cash = 48.0
    fee_rate = 0.0040  # 40bps
    trades = []
    
    for i in range(lag, len(btc_returns) - 3):  # -3 for exit window
        # Check if BTC moved > threshold in previous bar
        if abs(btc_returns[i - lag]) > btc_threshold:
            # Enter altcoin position
            entry_price = float(alt_candles[i]["close"])
            deploy = cash * 0.95
            
            if deploy < 1.0:
                continue
            
            entry_fee = deploy * fee_rate
            units = (deploy - entry_fee) / entry_price
            cash -= deploy
            
            # Exit after 3 bars
            exit_bar = min(i + 3, len(alt_candles) - 1)
            exit_price = float(alt_candles[exit_bar]["close"])
            
            # Apply 5% TP / 3% SL
            tp_price = entry_price * 1.05
            sl_price = entry_price * 0.97
            
            if exit_price >= tp_price:
                exit_price = tp_price
                exit_reason = "tp"
            elif exit_price <= sl_price:
                exit_price = sl_price
                exit_reason = "sl"
            else:
                exit_reason = "timeout"
            
            exit_proceeds = exit_price * units
            exit_fee = exit_proceeds * fee_rate
            net = exit_proceeds - deploy - entry_fee - exit_fee
            
            cash += exit_proceeds - exit_fee
            trades.append({"net": net, "reason": exit_reason, "win": net > 0})
    
    net = cash - starting_cash
    wins = [t for t in trades if t["win"]]
    
    return {
        "net": round(net, 2),
        "return_pct": round(net / starting_cash * 100, 1),
        "trades": len(trades),
        "wr": round(len(wins) / max(1, len(trades)) * 100, 1),
        "avg_trade": round(net / max(1, len(trades)), 4),
    }


def main():
    print("=" * 80)
    print("  LAB TEST #1: BTC → Altcoin Lead-Lag")
    print("=" * 80)
    
    # Load cached data
    print("\nLoading cached data...")
    btc_candles = load_candles("BTC-USD", "FIVE_MINUTE", 7, max_age_minutes=10000)
    
    alt_coins = ["RAVE-USD", "BAL-USD", "BLUR-USD", "ALEPH-USD", "IOTX-USD"]
    alt_candles = {}
    for coin in alt_coins:
        candles = load_candles(coin, "FIVE_MINUTE", 7, max_age_minutes=10000)
        if candles:
            alt_candles[coin] = candles
            print(f"  {coin}: {len(candles)} candles")
    
    if not btc_candles or not alt_candles:
        print("ERROR: Missing cached data.")
        return 1
    
    # Compute returns
    btc_closes = [float(c["close"]) for c in btc_candles]
    btc_returns = [(btc_closes[i] - btc_closes[i-1]) / btc_closes[i-1] * 100 
                   for i in range(1, len(btc_closes))]
    
    all_results = {}
    
    for coin, candles in alt_candles.items():
        print(f"\n{'='*60}")
        print(f"  {coin}")
        print(f"{'='*60}")
        
        alt_closes = [float(c["close"]) for c in candles]
        alt_returns = [(alt_closes[i] - alt_closes[i-1]) / alt_closes[i-1] * 100 
                       for i in range(1, len(alt_closes))]
        
        # Align returns to same length
        min_len = min(len(btc_returns), len(alt_returns))
        btc_r = btc_returns[:min_len]
        alt_r = alt_returns[:min_len]
        
        # Correlation analysis
        corr_results = run_lead_lag_test(btc_r, alt_r, coin)
        all_results[coin] = corr_results
        
        print(f"  Correlations by lag:")
        for lag, corr in corr_results["correlations_by_lag"].items():
            print(f"    Lag {lag}: {corr:.4f}")
        print(f"  Best lag: {corr_results['best_lag']} (corr: {corr_results['best_correlation']:.4f})")
        
        # Conditional probability
        print(f"  Conditional probability (alt return after BTC move):")
        for thresh, data in corr_results["conditional_probability"].items():
            print(f"    BTC>{thresh}%: {data['count']} moves → alt avg return: {data['alt_avg_return']:.4f}%, WR: {data['alt_win_rate']:.1f}%")
        
        # Strategy test
        print(f"  Lead-lag strategy test:")
        for btc_thresh in [0.5, 1.0, 1.5]:
            strat_result = run_lead_lag_strategy(btc_r, alt_r, candles[:min_len], lag=1, btc_threshold=btc_thresh)
            if strat_result:
                print(f"    BTC>{btc_thresh}%: ${strat_result['net']:.2f} ({strat_result['trades']}t, {strat_result['wr']}%WR)")
    
    # Save report
    report = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "test": "lead_lag_btc_to_altcoin",
        "results": all_results,
    }
    
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(f"\n  Report saved to: {REPORT_PATH}")
    
    # Summary
    print(f"\n{'='*80}")
    print(f"  SUMMARY")
    print(f"{'='*80}")
    
    for coin, result in all_results.items():
        best_lag = result["best_lag"]
        best_corr = result["best_correlation"]
        print(f"  {coin}: Best lag={best_lag}, correlation={best_corr:.4f}")
        
        # Check if any conditional probability shows edge
        for thresh, data in result["conditional_probability"].items():
            if data["alt_win_rate"] > 55 and data["count"] > 10:
                print(f"    ✅ BTC>{thresh}% → alt WR={data['alt_win_rate']}% ({data['count']} opportunities)")
    
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
