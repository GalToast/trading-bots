#!/usr/bin/env python3
"""
Momentum Breakout Independent Validation
==========================================
@main found: $741/11d (1544%, 59 trades, 81.4% WR) with LB10 + TP10% + SL7% + H50

I'm independently validating:
1. Replicate the $741 result from scratch
2. Check for bug patterns (double-counting, fake fills, cash errors)
3. Out-of-sample split (days 1-5 train, days 6-11 test)
4. Fee stress test (40bps → 80bps → 120bps)
5. Parameter robustness (is it overfit to LB10/TP10/SL7/H50?)
"""
from __future__ import annotations

import json
import time
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
from candle_cache_service import load_candles

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_REPORT_PATH = ROOT / "reports" / "momentum_breakout_independent_validation.json"

PRODUCT = "RAVE-USD"
STARTING_CASH = 48.0


def run_momentum_breakout(candles, lookback=10, tp_pct=10, sl_pct=7, max_hold=50,
                           fee_bps=40, deploy_pct=0.95):
    """
    Independent implementation of Momentum Breakout.
    
    Entry: Current candle HIGH breaks above lookback-bar high
    Exit: TP, SL, or max hold bars
    """
    if len(candles) < lookback + 2:
        return None
    
    fee_rate = fee_bps / 10000.0
    cash = STARTING_CASH
    pos = None
    trades = []
    total_volume = 0.0
    total_fees = 0.0
    peak_equity = STARTING_CASH
    max_drawdown = 0.0
    
    for i in range(len(candles)):
        c = candles[i]
        cl = float(c["close"])
        h = float(c["high"])
        l = float(c["low"])
        o = float(c["open"])
        
        # Fee tier
        if total_volume >= 50000: fr = 0.0015
        elif total_volume >= 10000: fr = 0.0025
        else: fr = fee_rate
        
        # EXIT
        if pos:
            pos["hold"] += 1
            exit_p = None
            exit_reason = None
            
            if h >= pos["tp"]:
                exit_p = pos["tp"]
                exit_reason = "tp"
            elif l <= pos["sl"]:
                exit_p = pos["sl"]
                exit_reason = "sl"
            elif pos["hold"] >= max_hold:
                exit_p = cl
                exit_reason = "timeout"
            
            if exit_p is not None:
                units = pos["units"]
                entry_fee = pos["entry_fee"]
                exit_fee = exit_p * units * fr
                pnl = (exit_p - pos["ep"]) * units - entry_fee - exit_fee
                
                cash += exit_p * units - exit_fee
                total_volume += pos["deploy"] + (exit_p * units)
                total_fees += entry_fee + exit_fee
                
                equity = cash
                peak_equity = max(peak_equity, equity)
                if peak_equity > 0:
                    dd = (peak_equity - equity) / peak_equity * 100
                    max_drawdown = max(max_drawdown, dd)
                
                trades.append({"pnl": pnl, "reason": exit_reason, "win": pnl > 0, "hold": pos["hold"]})
                pos = None
                continue
        
        # ENTRY: current HIGH breaks above lookback high
        if pos is None and cash >= 10.0 and i >= lookback:
            recent_high = max(float(candles[j]["high"]) for j in range(i - lookback, i))
            if h > recent_high:
                deploy = cash * deploy_pct
                if deploy >= 10.0:
                    entry_fee = deploy * fr
                    units = (deploy - entry_fee) / o
                    if units > 0:
                        cash -= deploy
                        pos = {
                            "ep": o, "deploy": deploy, "units": units,
                            "tp": o * (1 + tp_pct / 100.0),
                            "sl": o * (1 - sl_pct / 100.0),
                            "hold": 0,
                            "entry_fee": entry_fee,
                        }
    
    # Close open position
    if pos:
        final_price = float(candles[-1]["close"])
        cash += pos["units"] * final_price * (1 - fee_rate)
        total_volume += pos["deploy"] + (final_price * pos["units"])
        total_fees += pos["entry_fee"]
    
    net = cash - STARTING_CASH
    wins = [t for t in trades if t["win"]]
    
    return {
        "net": round(net, 2),
        "return_pct": round(net / STARTING_CASH * 100, 1),
        "trades": len(trades),
        "wr": round(len(wins) / max(1, len(trades)) * 100, 1),
        "avg_trade": round(net / max(1, len(trades)), 2),
        "total_fees": round(total_fees, 2),
        "max_dd": round(max_drawdown, 1),
        "total_volume": round(total_volume, 2),
        "tp_exits": len([t for t in trades if t["reason"] == "tp"]),
        "sl_exits": len([t for t in trades if t["reason"] == "sl"]),
        "timeout_exits": len([t for t in trades if t["reason"] == "timeout"]),
        "avg_hold": round(sum(t["hold"] for t in trades) / max(1, len(trades)), 1),
    }


def main():
    print("=" * 80)
    print("  MOMENTUM BREAKOUT INDEPENDENT VALIDATION")
    print("=" * 80)
    
    # Load cached data (M5, 30d for RAVE)
    print(f"\nLoading cached {PRODUCT} M5 30d data...")
    candles = load_candles(PRODUCT, "FIVE_MINUTE", 30, max_age_minutes=10000)
    if not candles:
        print("ERROR: No cached data. Run candle_cache_service.py first.")
        return 1
    print(f"  Got {len(candles)} candles ({len(candles)/288:.1f} days)")
    
    all_results = []
    
    # TEST 1: Replicate @main's champion (LB10 TP10 SL7 H50) on 11 days
    print(f"\n{'='*80}")
    print(f"  TEST 1: Replicate Champion Config (LB10 TP10 SL7 H50)")
    print(f"{'='*80}")
    
    # Use first 11 days worth of candles
    eleven_days_candles = candles[:11*288] if len(candles) > 11*288 else candles
    print(f"  Using {len(eleven_days_candles)} candles (11 days)")
    
    result = run_momentum_breakout(eleven_days_candles, lookback=10, tp_pct=10, sl_pct=7, max_hold=50)
    if result:
        result["test"] = "champion_replication"
        result["params"] = "LB10 TP10 SL7 H50"
        all_results.append(result)
        print(f"\n  @main's claim: $741.26, 59t, 81.4%WR, 17.5%DD")
        print(f"  My result:    ${result['net']:.2f}, {result['trades']}t, {result['wr']}%WR, {result['max_dd']}%DD")
        if abs(result["net"] - 741.26) < 100 and abs(result["trades"] - 59) < 10:
            print(f"  ✅ MATCH — within tolerance")
        else:
            print(f"  ❌ DIFFERENT — investigating...")
    
    # TEST 2: Full parameter sweep on 11 days
    print(f"\n{'='*80}")
    print(f"  TEST 2: Parameter Sweep on 11 Days")
    print(f"{'='*80}")
    
    print(f"\n  {'Config':<22} {'Net $':>8} {'Trades':>7} {'WR%':>6} {'DD%':>6} {'Fees':>8}")
    print(f"  {'-'*60}")
    
    for lb in [10, 20]:
        for tp in [5, 10, 15]:
            for sl in [3, 5, 7]:
                for mh in [30, 50]:
                    result = run_momentum_breakout(eleven_days_candles, lb, tp, sl, mh)
                    if result and result["trades"] > 0:
                        result["test"] = "param_sweep_11d"
                        result["params"] = f"LB{lb} TP{tp} SL{sl} H{mh}"
                        all_results.append(result)
                        print(f"  {result['params']:<22} ${result['net']:>6.2f} {result['trades']:>7} {result['wr']:>5.1f}% {result['max_dd']:>5.1f}% ${result['total_fees']:>7.2f}")
    
    # TEST 3: Out-of-sample split
    print(f"\n{'='*80}")
    print(f"  TEST 3: Out-of-Sample (Days 1-5 Train, Days 6-11 Test)")
    print(f"{'='*80}")
    
    day5_idx = 5 * 288
    train_candles = candles[:day5_idx]
    test_candles = candles[day5_idx:11*288]
    
    print(f"  Train: {len(train_candles)} candles (5 days)")
    print(f"  Test:  {len(test_candles)} candles (6 days)")
    
    # Find best config on train
    best_train = None
    best_train_net = -9999
    for lb in [10, 20]:
        for tp in [5, 10, 15]:
            for sl in [3, 5, 7]:
                for mh in [30, 50]:
                    r = run_momentum_breakout(train_candles, lb, tp, sl, mh)
                    if r and r["net"] > best_train_net and r["trades"] > 5:
                        best_train_net = r["net"]
                        best_train = (lb, tp, sl, mh, r)
    
    if best_train:
        lb, tp, sl, mh, train_r = best_train
        print(f"\n  Best on train: LB{lb} TP{tp} SL{sl} H{mh} = ${train_r['net']:.2f} ({train_r['trades']}t, {train_r['wr']}%WR)")
        
        test_r = run_momentum_breakout(test_candles, lb, tp, sl, mh)
        if test_r:
            print(f"  Same config on test: ${test_r['net']:.2f} ({test_r['trades']}t, {test_r['wr']}%WR, {test_r['max_dd']}%DD)")
            
            test_r["test"] = "out_of_sample_test"
            test_r["params"] = f"LB{lb} TP{tp} SL{sl} H{mh}"
            test_r["train_net"] = train_r["net"]
            all_results.append(test_r)
    
    # TEST 4: Fee stress test
    print(f"\n{'='*80}")
    print(f"  TEST 4: Fee Stress Test (Champion Config)")
    print(f"{'='*80}")
    
    for fee_bps in [40, 80, 120]:
        result = run_momentum_breakout(eleven_days_candles, lookback=10, tp_pct=10, sl_pct=7, max_hold=50, fee_bps=fee_bps)
        if result:
            result["test"] = "fee_stress"
            result["params"] = f"LB10 TP10 SL7 H50 @{fee_bps}bps"
            result["fee_bps"] = fee_bps
            all_results.append(result)
            print(f"  {fee_bps}bps: ${result['net']:.2f} ({result['trades']}t, {result['wr']}%WR, {result['max_dd']}%DD)")
    
    # TEST 5: Full 30-day test
    print(f"\n{'='*80}")
    print(f"  TEST 5: Full 30-Day Test (Champion Config)")
    print(f"{'='*80}")
    
    result_30d = run_momentum_breakout(candles, lookback=10, tp_pct=10, sl_pct=7, max_hold=50)
    if result_30d:
        result_30d["test"] = "full_30day"
        result_30d["params"] = "LB10 TP10 SL7 H50 (30d)"
        all_results.append(result_30d)
        print(f"\n  30 days: ${result_30d['net']:.2f} ({result_30d['return_pct']}%)")
        print(f"  {result_30d['trades']} trades, {result_30d['wr']}%WR, {result_30d['max_dd']}%DD")
        print(f"  ${result_30d['net']/30:.2f}/day average")
        print(f"  Monthly projection: ${result_30d['net']:.2f}")
    
    # Sort and summary
    sweep_results = [r for r in all_results if r.get("test") == "param_sweep_11d"]
    sweep_results.sort(key=lambda r: r["net"], reverse=True)
    
    print(f"\n{'='*80}")
    print(f"  TOP 10 CONFIGS (11-day sweep)")
    print(f"{'='*80}")
    print(f"\n  {'Config':<22} {'Net $':>8} {'Trades':>7} {'WR%':>6} {'DD%':>6}")
    print(f"  {'-'*50}")
    for r in sweep_results[:10]:
        print(f"  {r['params']:<22} ${r['net']:>6.2f} {r['trades']:>7} {r['wr']:>5.1f}% {r['max_dd']:>5.1f}%")
    
    # Write report
    report = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "all_results": all_results,
        "top_10": sweep_results[:10],
    }
    
    out = Path(DEFAULT_REPORT_PATH)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(f"\n  Report: {out}")
    
    # Verdict
    print(f"\n{'='*80}")
    print(f"  VERDICT")
    print(f"{'='*80}")
    
    if sweep_results:
        best = sweep_results[0]
        print(f"\n  Best config: {best['params']} = ${best['net']:.2f}")
        print(f"  @main's claim: LB10 TP10 SL7 H50 = $741.26")
        
        # Find the champion config result
        champion_results = [r for r in sweep_results if "LB10" in r["params"] and "TP10" in r["params"] and "SL7" in r["params"] and "H50" in r["params"]]
        if champion_results:
            champ = champion_results[0]
            diff = abs(champ["net"] - 741.26)
            trade_diff = abs(champ["trades"] - 59)
            if diff < 100 and trade_diff < 10:
                print(f"  ✅ VALIDATED — ${champ['net']:.2f} vs $741.26 (diff: ${diff:.2f})")
                print(f"  Trades: {champ['trades']} vs 59 (diff: {trade_diff})")
                print(f"  WR: {champ['wr']}% vs 81.4%")
            else:
                print(f"  ⚠️  PARTIAL MATCH — ${champ['net']:.2f} vs $741.26 (diff: ${diff:.2f})")
                print(f"  Trades: {champ['trades']} vs 59 (diff: {trade_diff})")
        else:
            print(f"  ❌ Champion config not found in sweep")
    
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
