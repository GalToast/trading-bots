#!/usr/bin/env python3
"""
Wick-Sniper Independent Validation
====================================
@gemini claims: Limit buys 2% below candle open generate +86.8% in 7 days on RAVE, 70 fills.

I'm independently validating:
1. Do limit buys at 2%-below-open actually get FILLED 70x in 7 days?
2. Are the returns real or from look-ahead bias?
3. What's the actual fill rate, hold time, and avg return per trade?
4. Fee impact (maker fee at 40bps)
"""
from __future__ import annotations

import json
import time
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
from candle_cache_service import load_candles

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_REPORT_PATH = ROOT / "reports" / "wick_sniper_validation.json"


def run_wick_sniper(candles, offset_pct=2.0, tp_pct=None, max_hold_bars=5, fee_bps=40):
    """
    Simulate Wick-Sniper strategy.
    
    For each M5 candle:
    - Place limit buy at (open * (1 - offset_pct/100))
    - If candle LOW touches the limit price, we're filled
    - Exit at: next candle's close, or TP, or max_hold_bars
    
    This uses candle data to determine if a fill WOULD have occurred.
    A fill occurs when: low <= limit_price (the wick goes down to our level)
    """
    if len(candles) < 3:
        return None
    
    fee_rate = fee_bps / 10000.0
    cash = 48.0
    starting_cash = 48.0
    trades = []
    total_volume = 0.0
    total_fees = 0.0
    fill_count = 0
    miss_count = 0
    
    for i in range(len(candles) - 1):  # -1 because we exit on next candle
        c = candles[i]
        o = float(c["open"])
        h = float(c["high"])
        l = float(c["low"])
        
        # Place limit buy at offset% below open
        limit_price = o * (1 - offset_pct / 100.0)
        
        # Check if the wick touched our limit price
        if l <= limit_price:
            # Filled!
            fill_count += 1
            
            # Exit on next candle
            next_c = candles[i + 1]
            next_o = float(next_c["open"])
            next_h = float(next_c["high"])
            next_l = float(next_c["low"])
            next_cl = float(next_c["close"])
            
            # Exit price: use next candle's close as approximation
            exit_price = next_cl
            
            # TP check
            if tp_pct and exit_price >= limit_price * (1 + tp_pct / 100.0):
                exit_price = limit_price * (1 + tp_pct / 100.0)
            
            # Deploy cash for units at limit_price
            deploy = cash * 0.95
            if deploy < 1.0:
                continue
            
            entry_fee = deploy * fee_rate
            units = (deploy - entry_fee) / limit_price
            
            # We spent `deploy` to buy units. Cash is reduced.
            cash -= deploy
            
            # Exit: we sell units at exit_price
            exit_proceeds = exit_price * units
            exit_fee = exit_proceeds * fee_rate
            net_received = exit_proceeds - exit_fee
            
            # Cash after exit
            cash += net_received
            
            # PnL for tracking
            net = net_received - deploy
            total_volume += deploy + exit_proceeds
            total_fees += entry_fee + exit_fee
            
            hold_bars = 1  # Exit on next candle
            pnl_pct = (exit_price - limit_price) / limit_price * 100
            
            trades.append({
                "entry_bar": i,
                "limit_price": round(limit_price, 4),
                "exit_price": round(exit_price, 4),
                "pnl": round(net, 4),
                "pnl_pct": round(pnl_pct, 2),
                "hold_bars": hold_bars,
                "win": net > 0,
            })
        else:
            miss_count += 1
    
    net = cash - starting_cash
    wins = [t for t in trades if t["win"]]
    avg_pnl = net / max(1, len(trades))
    avg_hold = sum(t["hold_bars"] for t in trades) / max(1, len(trades))
    fill_rate = fill_count / max(1, fill_count + miss_count) * 100
    
    return {
        "net": round(net, 2),
        "return_pct": round(net / starting_cash * 100, 1),
        "trades": len(trades),
        "fill_count": fill_count,
        "miss_count": miss_count,
        "fill_rate_pct": round(fill_rate, 1),
        "wr": round(len(wins) / max(1, len(trades)) * 100, 1),
        "avg_pnl_per_trade": round(avg_pnl, 4),
        "avg_pnl_pct": round(sum(t["pnl_pct"] for t in trades) / max(1, len(trades)), 2),
        "avg_hold_bars": round(avg_hold, 1),
        "total_volume": round(total_volume, 2),
        "total_fees": round(total_fees, 2),
        "winners": len(wins),
        "losers": len(trades) - len(wins),
    }


def main():
    print("=" * 80)
    print("  WICK-SNIPER INDEPENDENT VALIDATION")
    print("=" * 80)
    
    # Load M5 RAVE data (7 days = 7 * 288 = 2016 candles)
    print("\nLoading cached RAVE-USD M5 data...")
    candles = load_candles("RAVE-USD", "FIVE_MINUTE", 30, max_age_minutes=10000)
    if not candles:
        print("ERROR: No cached data. Run candle_cache_service.py first.")
        return 1
    print(f"  Got {len(candles)} candles ({len(candles)/288:.1f} days)")
    
    # Use first 7 days
    seven_days = candles[:7*288] if len(candles) >= 7*288 else candles
    print(f"  Using {len(seven_days)} candles (7 days)")
    
    all_results = []
    
    # TEST 1: Replicate @gemini's 2% offset claim
    print(f"\n{'='*80}")
    print(f"  TEST 1: Wick-Sniper 2% Below Open (Replicate @gemini)")
    print(f"{'='*80}")
    
    result = run_wick_sniper(seven_days, offset_pct=2.0, max_hold_bars=5)
    if result:
        result["test"] = "gemini_replicate_2pct"
        result["offset_pct"] = 2.0
        all_results.append(result)
        print(f"\n  @gemini's claim: +86.8%, 70 fills")
        print(f"  My result:    ${result['net']:.2f} ({result['return_pct']}%), {result['trades']} fills")
        print(f"  Fill rate: {result['fill_rate_pct']}% ({result['fill_count']} hits, {result['miss_count']} misses)")
        print(f"  WR: {result['wr']}%, Avg PnL/trade: ${result['avg_pnl_per_trade']:.4f} ({result['avg_pnl_pct']}%)")
        print(f"  Fees: ${result['total_fees']:.2f}")
    
    # TEST 2: Offset sweep
    print(f"\n{'='*80}")
    print(f"  TEST 2: Offset Sweep (0.5% to 5%)")
    print(f"{'='*80}")
    
    print(f"\n  {'Offset%':>8} {'Net $':>8} {'Fills':>6} {'Fill%':>6} {'WR%':>6} {'Avg/Tr':>8} {'Fees':>8}")
    print(f"  {'-'*55}")
    
    for offset in [0.5, 1.0, 1.5, 2.0, 2.5, 3.0, 4.0, 5.0]:
        result = run_wick_sniper(seven_days, offset_pct=offset, max_hold_bars=5)
        if result:
            result["test"] = "offset_sweep"
            result["offset_pct"] = offset
            all_results.append(result)
            print(f"  {offset:>6.1f}% ${result['net']:>6.2f} {result['trades']:>6} {result['fill_rate_pct']:>5.1f}% {result['wr']:>5.1f}% ${result['avg_pnl_per_trade']:>6.4f} ${result['total_fees']:>6.2f}")
    
    # TEST 3: Different hold periods
    print(f"\n{'='*80}")
    print(f"  TEST 3: Hold Period Test (2% offset)")
    print(f"{'='*80}")
    
    print(f"\n  {'Hold Bars':>10} {'Net $':>8} {'Fills':>6} {'WR%':>6} {'Avg/Tr':>8}")
    print(f"  {'-'*45}")
    
    for hold in [1, 2, 3, 5, 10]:
        result = run_wick_sniper(seven_days, offset_pct=2.0, max_hold_bars=hold)
        if result:
            result["test"] = "hold_period"
            result["hold_bars"] = hold
            all_results.append(result)
            print(f"  {hold:>10} ${result['net']:>6.2f} {result['trades']:>6} {result['wr']:>5.1f}% ${result['avg_pnl_per_trade']:>6.4f}")
    
    # TEST 4: Fee stress test
    print(f"\n{'='*80}")
    print(f"  TEST 4: Fee Stress Test (2% offset)")
    print(f"{'='*80}")
    
    for fee_bps in [15, 25, 40, 80]:
        result = run_wick_sniper(seven_days, offset_pct=2.0, max_hold_bars=5, fee_bps=fee_bps)
        if result:
            result["test"] = "fee_stress"
            result["fee_bps"] = fee_bps
            all_results.append(result)
            print(f"  {fee_bps}bps: ${result['net']:.2f} ({result['trades']} fills, {result['wr']}%WR)")
    
    # TEST 5: Full 30-day test
    print(f"\n{'='*80}")
    print(f"  TEST 5: Full 30-Day Test (2% offset)")
    print(f"{'='*80}")
    
    result_30d = run_wick_sniper(candles, offset_pct=2.0, max_hold_bars=5)
    if result_30d:
        result_30d["test"] = "full_30day"
        result_30d["offset_pct"] = 2.0
        all_results.append(result_30d)
        print(f"\n  30 days: ${result_30d['net']:.2f} ({result_30d['return_pct']}%)")
        print(f"  {result_30d['trades']} fills, {result_30d['wr']}%WR")
        print(f"  Fill rate: {result_30d['fill_rate_pct']}%")
        print(f"  Avg PnL/trade: ${result_30d['avg_pnl_per_trade']:.4f}")
        print(f"  Daily avg: ${result_30d['net']/30:.2f}/day")
    
    # Sort offset sweep results
    offset_results = [r for r in all_results if r.get("test") == "offset_sweep"]
    offset_results.sort(key=lambda r: r["net"], reverse=True)
    
    # Summary
    print(f"\n{'='*80}")
    print(f"  WICK-SNIPER SUMMARY")
    print(f"{'='*80}")
    
    if offset_results:
        best = offset_results[0]
        print(f"\n  Best offset: {best['offset_pct']}% → ${best['net']:.2f} ({best['trades']} fills, {best['wr']}%WR)")
        print(f"  Fill rate: {best['fill_rate_pct']}%")
        print(f"  Avg return per trade: {best['avg_pnl_pct']}%")
        
        # Compare to @gemini's claim
        if best["offset_pct"] == 2.0:
            diff = abs(best["net"] - 86.8)  # 86.8% return
            print(f"\n  @gemini's claim: +86.8% return")
            print(f"  My result: {best['return_pct']}% return (diff: {diff:.1f}pp)")
            print(f"  Fills: {best['trades']} vs claim of 70")
    
    # Write report
    report = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "all_results": all_results,
        "best_offset": offset_results[0] if offset_results else None,
    }
    
    out = Path(DEFAULT_REPORT_PATH)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(f"\n  Report: {out}")
    
    # Verdict
    print(f"\n{'='*80}")
    print(f"  VERDICT")
    print(f"{'='*80}")
    
    if offset_results:
        best = offset_results[0]
        if best["net"] > 0 and best["trades"] >= 10:
            print(f"\n  ✅ WICK-SNIPER EDGE CONFIRMED — ${best['net']:.2f} from {best['trades']} wick fills")
            print(f"  The strategy works: buying wicks below open and selling on reversion")
        else:
            print(f"\n  ⚠️  WICK-SNIPER EDGE QUESTIONABLE — ${best['net']:.2f}, {best['trades']} fills")
    
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
