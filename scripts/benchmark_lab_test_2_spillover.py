#!/usr/bin/env python3
"""
Lab Test #2: RAVE Cross-Coin Momentum Spillover
=================================================
When other coins pump/dump, does RAVE follow with a delay?

Testing: If BAL/BLUR/ALEPH/IOTX moves > X%, does RAVE move in the same direction within 1-3 bars?

This is a STRUCTURAL edge based on capital flow between microcaps.
"""
from __future__ import annotations

import json
import time
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
from candle_cache_service import load_candles

ROOT = Path(__file__).resolve().parent.parent
REPORT_PATH = ROOT / "reports" / "lab_qwen_trading_test_2_spillover.json"


def compute_returns(candles):
    """Compute per-bar returns."""
    closes = [float(c["close"]) for c in candles]
    return [(closes[i] - closes[i-1]) / closes[i-1] * 100 for i in range(1, len(closes))]


def run_spillover_test(source_returns, target_returns, lags=[1, 2, 3]):
    """Test if source coin moves predict target coin moves with lag."""
    results = {}
    
    for lag in lags:
        # Align returns
        src = source_returns[:-lag] if lag > 0 else source_returns
        tgt = target_returns[lag:] if lag > 0 else target_returns
        
        min_len = min(len(src), len(tgt))
        src = src[:min_len]
        tgt = tgt[:min_len]
        
        # Find bars where source moved > threshold
        thresholds = [1.0, 2.0, 3.0, 5.0]
        threshold_results = {}
        
        for thresh in thresholds:
            big_moves = [i for i, r in enumerate(src) if abs(r) > thresh]
            if len(big_moves) < 3:
                threshold_results[thresh] = {
                "count": len(big_moves),
                "avg_target_return": 0,
                "win_rate": 0,
                "same_direction_pct": 0,
                "total_opportunities": 0,
            }
                continue
            
            # Check target returns in the lagged bars
            target_after = [tgt[i] for i in big_moves if i < len(tgt)]
            wins = sum(1 for r in target_after if r > 0)
            
            avg_return = sum(target_after) / len(target_after) if target_after else 0
            win_rate = wins / len(target_after) * 100 if target_after else 0
            
            # Same direction check
            same_direction = sum(1 for i in big_moves if i < len(tgt) and src[i] * tgt[i] > 0)
            same_dir_pct = same_direction / len(big_moves) * 100 if big_moves else 0
            
            threshold_results[thresh] = {
                "count": len(big_moves),
                "avg_target_return": round(avg_return, 4),
                "win_rate": round(win_rate, 1),
                "same_direction_pct": round(same_dir_pct, 1),
                "total_opportunities": len(target_after),
            }
        
        results[lag] = threshold_results
    
    return results


def run_spillover_strategy(source_returns, target_returns, target_candles, lag=1, threshold=2.0):
    """
    Strategy: When source coin moves > threshold, enter target coin with lag.
    """
    if len(target_returns) < 10 or len(target_candles) < 10:
        return None
    
    cash = 48.0
    starting_cash = 48.0
    fee_rate = 0.0040
    trades = []
    
    min_len = min(len(source_returns), len(target_returns)) - lag - 3
    
    for i in range(lag, min_len):
        if abs(source_returns[i - lag]) > threshold:
            # Enter target position
            entry_idx = i
            if entry_idx >= len(target_candles):
                continue
            
            entry_price = float(target_candles[entry_idx]["close"])
            deploy = cash * 0.95
            
            if deploy < 1.0:
                continue
            
            entry_fee = deploy * fee_rate
            units = (deploy - entry_fee) / entry_price
            cash -= deploy
            
            # Exit after 3 bars
            exit_idx = min(i + 3, len(target_candles) - 1)
            exit_price = float(target_candles[exit_idx]["close"])
            
            # TP/SL
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
    print("  LAB TEST #2: RAVE Cross-Coin Momentum Spillover")
    print("=" * 80)
    
    # Load cached data
    print("\nLoading cached data...")
    source_coins = ["BAL-USD", "BLUR-USD", "ALEPH-USD", "IOTX-USD"]
    target_coin = "RAVE-USD"
    
    all_candles = {}
    for coin in source_coins + [target_coin]:
        candles = load_candles(coin, "FIVE_MINUTE", 7, max_age_minutes=10000)
        if candles:
            all_candles[coin] = candles
            print(f"  {coin}: {len(candles)} candles")
    
    if target_coin not in all_candles:
        print("ERROR: Missing RAVE data.")
        return 1
    
    target_returns = compute_returns(all_candles[target_coin])
    all_results = {}
    
    for src_coin in source_coins:
        if src_coin not in all_candles:
            continue
        
        print(f"\n{'='*60}")
        print(f"  {src_coin} → {target_coin}")
        print(f"{'='*60}")
        
        source_returns = compute_returns(all_candles[src_coin])
        
        # Align to same length
        min_len = min(len(source_returns), len(target_returns))
        src_r = source_returns[:min_len]
        tgt_r = target_returns[:min_len]
        
        # Spillover analysis
        spillover_results = run_spillover_test(src_r, tgt_r)
        all_results[src_coin] = spillover_results
        
        print(f"  Spillover analysis:")
        for lag, threshold_results in spillover_results.items():
            print(f"    Lag {lag}:")
            for thresh, data in threshold_results.items():
                print(f"      Source>{thresh}%: {data['count']} moves → target avg: {data['avg_target_return']:.4f}%, WR: {data['win_rate']}%, Same dir: {data['same_direction_pct']}%")
        
        # Strategy test
        print(f"  Spillover strategy test:")
        for thresh in [1.0, 2.0, 3.0]:
            strat_result = run_spillover_strategy(src_r, tgt_r, all_candles[target_coin][:min_len], lag=1, threshold=thresh)
            if strat_result:
                print(f"    Source>{thresh}%: ${strat_result['net']:.2f} ({strat_result['trades']}t, {strat_result['wr']}%WR)")
    
    # Save report
    report = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "test": "rave_cross_coin_spillover",
        "results": all_results,
    }
    
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(f"\n  Report saved to: {REPORT_PATH}")
    
    # Summary
    print(f"\n{'='*80}")
    print(f"  SUMMARY")
    print(f"{'='*80}")
    
    for src_coin, results in all_results.items():
        # Find the best threshold/lag combo
        best_wr = 0
        best_config = ""
        for lag, threshold_results in results.items():
            for thresh, data in threshold_results.items():
                if data["win_rate"] > best_wr and data["count"] >= 5:
                    best_wr = data["win_rate"]
                    best_config = f"lag={lag}, thresh={thresh}%, WR={best_wr}%"
        
        if best_config:
            print(f"  {src_coin} → RAVE: Best = {best_config}")
        else:
            print(f"  {src_coin} → RAVE: No significant spillover edge")
    
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
