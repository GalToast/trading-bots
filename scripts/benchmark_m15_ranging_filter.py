#!/usr/bin/env python3
"""
M15 Ranging Filter — Multi-Coin Validation
============================================
@main's breakthrough: Mean reversion ONLY works when M15 is RANGE-BOUND.

M15 Range% = (High - Low) / Close * 100 for each 15-min candle
If range% < 5% → market is ranging → safe for mean reversion
If range% >= 5% → market is trending → stay in cash

Testing across RAVE, BAL, BLUR, ALEPH, IOTX.
"""
from __future__ import annotations

import json
import time
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
from benchmark_coinbase_spot_rsi import fetch_candles_72h, rsi as compute_rsi
from coinbase_advanced_client import CoinbaseAdvancedClient

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_REPORT_PATH = ROOT / "reports" / "m15_ranging_filter.json"

PRODUCTS = ["RAVE-USD", "BAL-USD", "BLUR-USD", "ALEPH-USD", "IOTX-USD"]
STARTING_CASH = 48.0


def compute_m15_range_pct(candles_m15, lookback=4):
    """
    Compute M15 range% for recent candles.
    range% = (high - low) / close * 100
    Returns True if market is ranging (avg range < threshold).
    """
    if len(candles_m15) < lookback:
        return False, 0.0
    
    recent = candles_m15[-lookback:]
    ranges = []
    for c in recent:
        h = float(c["high"])
        l = float(c["low"])
        cl = float(c["close"])
        if cl > 0:
            ranges.append((h - l) / cl * 100)
    
    avg_range = sum(ranges) / len(ranges) if ranges else 0
    return avg_range, avg_range


def run_m15_ranging_strategy(candles_m5, candles_m15, product_id,
                               rsi_period, rsi_entry, rsi_exit,
                               m15_range_thresh, tp_pct=0.0, sl_pct=0.0, max_hold=24,
                               deploy_pct=0.95):
    """RSI mean reversion with M15 ranging filter."""
    if len(candles_m5) < rsi_period + 20 or len(candles_m15) < 10:
        return None
    
    closes = [float(c["close"]) for c in candles_m5]
    rsi_vals = compute_rsi(closes, rsi_period)
    fee_rate = 0.0040
    
    # Build M15 lookup by timestamp
    m15_by_time = {}
    for c in candles_m15:
        t = int(c.get("start", c.get("time", 0)))
        m15_by_time[t] = c
    
    # Get sorted M15 times for range computation
    m15_times = sorted(m15_by_time.keys())
    
    cash = STARTING_CASH
    in_position = False
    position = None
    trades = []
    ranging_bars = 0
    trending_bars = 0
    ranging_trades = 0
    trending_trades = 0
    
    for i in range(rsi_period + 10, len(candles_m5) - 1):
        c = candles_m5[i]
        h = float(c["high"])
        l = float(c["low"])
        cl = float(c["close"])
        ts = int(c.get("start", c.get("time", 0)))
        current_rsi = rsi_vals[i]
        
        # M15 RANGE CHECK — find nearest M15 candle
        is_ranging = True
        avg_range = 0.0
        if len(m15_times) >= 4:
            # Get last 4 M15 candles before this timestamp
            recent_m15_times = [t for t in m15_times if t <= ts][-4:]
            if len(recent_m15_times) >= 2:
                recent_m15 = [m15_by_time[t] for t in recent_m15_times]
                ranges = []
                for mc in recent_m15:
                    mh = float(mc["high"])
                    ml = float(mc["low"])
                    mcl = float(mc["close"])
                    if mcl > 0:
                        ranges.append((mh - ml) / mcl * 100)
                if ranges:
                    avg_range = sum(ranges) / len(ranges)
                    is_ranging = avg_range < m15_range_thresh
        
        if is_ranging:
            ranging_bars += 1
        else:
            trending_bars += 1
        
        # EXIT
        if in_position and position:
            exit_price = None
            exit_reason = None
            
            if tp_pct > 0 and h >= position["entry"] * (1 + tp_pct):
                exit_price = position["entry"] * (1 + tp_pct)
                exit_reason = "tp"
            elif sl_pct > 0 and l <= position["entry"] * (1 - sl_pct):
                exit_price = position["entry"] * (1 - sl_pct)
                exit_reason = "sl"
            elif rsi_exit > 0 and current_rsi >= rsi_exit:
                exit_price = cl
                exit_reason = "rsi_exit"
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
                trades.append({
                    "net": net, "reason": exit_reason, "win": net > 0,
                    "is_ranging": position.get("is_ranging", False)
                })
                if position.get("is_ranging", False):
                    ranging_trades += 1
                else:
                    trending_trades += 1
                in_position = False
                position = None
                continue
        
        # ENTRY with M15 ranging filter
        if not in_position and cash >= 10.0 and current_rsi < rsi_entry and is_ranging:
            deploy = cash * deploy_pct
            entry_fee = cl * (deploy / cl) * fee_rate
            qty = (deploy - entry_fee) / cl
            
            if qty > 0:
                cash -= deploy
                position = {"entry": cl, "qty": qty, "bar": i, "quote": deploy, "is_ranging": True}
                in_position = True
    
    # Also test WITHOUT the filter for comparison
    cash_no_filter = STARTING_CASH
    trades_no_filter = []
    in_position_nf = False
    position_nf = None
    
    for i in range(rsi_period + 10, len(candles_m5) - 1):
        c = candles_m5[i]
        h = float(c["high"])
        l = float(c["low"])
        cl = float(c["close"])
        current_rsi = rsi_vals[i]
        
        if in_position_nf and position_nf:
            exit_price = None
            exit_reason = None
            
            if tp_pct > 0 and h >= position_nf["entry"] * (1 + tp_pct):
                exit_price = position_nf["entry"] * (1 + tp_pct)
                exit_reason = "tp"
            elif sl_pct > 0 and l <= position_nf["entry"] * (1 - sl_pct):
                exit_price = position_nf["entry"] * (1 - sl_pct)
                exit_reason = "sl"
            elif rsi_exit > 0 and current_rsi >= rsi_exit:
                exit_price = cl
                exit_reason = "rsi_exit"
            elif (i - position_nf["bar"]) >= max_hold:
                exit_price = cl
                exit_reason = "timeout"
            
            if exit_price is not None:
                qty = position_nf["qty"]
                gross = (exit_price - position_nf["entry"]) * qty
                entry_fee = position_nf["entry"] * qty * fee_rate
                exit_fee = exit_price * qty * fee_rate
                net = gross - entry_fee - exit_fee
                
                cash_no_filter += position_nf["quote"] + net
                trades_no_filter.append({"net": net, "win": net > 0})
                in_position_nf = False
                position_nf = None
                continue
        
        if not in_position_nf and cash_no_filter >= 10.0 and current_rsi < rsi_entry:
            deploy = cash_no_filter * deploy_pct
            entry_fee = cl * (deploy / cl) * fee_rate
            qty = (deploy - entry_fee) / cl
            
            if qty > 0:
                cash_no_filter -= deploy
                position_nf = {"entry": cl, "qty": qty, "bar": i, "quote": deploy}
                in_position_nf = True
    
    if not trades:
        return None
    
    wins = [t for t in trades if t["win"]]
    net = sum(t["net"] for t in trades)
    net_no_filter = cash_no_filter - STARTING_CASH
    wins_nf = [t for t in trades_no_filter if t["win"]]
    
    return {
        "product": product_id,
        "net_with_filter": round(net, 2),
        "net_without_filter": round(net_no_filter, 2),
        "improvement": round(net - net_no_filter, 2),
        "trades_with_filter": len(trades),
        "trades_without_filter": len(trades_no_filter),
        "wr_with_filter": round(len(wins) / max(1, len(trades)) * 100, 1),
        "wr_without_filter": round(len(wins_nf) / max(1, len(trades_no_filter)) * 100, 1),
        "ranging_bars": ranging_bars,
        "trending_bars": trending_bars,
        "ranging_pct": round(ranging_bars / max(1, ranging_bars + trending_bars) * 100, 1),
        "avg_m15_range": round(avg_range, 2),
    }


def main():
    client = CoinbaseAdvancedClient()
    
    all_results = []
    
    for pid in PRODUCTS:
        print(f"\n{'='*80}")
        print(f"  {pid}")
        print(f"{'='*80}")
        
        # Fetch M5 and M15 candles
        print(f"  Fetching M5 candles...")
        candles_m5 = fetch_candles_72h(client, pid, "FIVE_MINUTE")
        print(f"  Got {len(candles_m5)} M5 candles")
        
        print(f"  Fetching M15 candles...")
        candles_m15 = fetch_candles_72h(client, pid, "FIFTEEN_MINUTE")
        print(f"  Got {len(candles_m15)} M15 candles")
        
        # Threshold sweep
        thresholds = [3.0, 5.0, 7.0, 10.0]
        coin_results = []
        
        for thresh in thresholds:
            result = run_m15_ranging_strategy(
                candles_m5, candles_m15, pid,
                rsi_period=4, rsi_entry=45, rsi_exit=80,
                m15_range_thresh=thresh
            )
            if result:
                result["threshold"] = thresh
                coin_results.append(result)
                improvement_str = ""
                if result["improvement"] > 0:
                    improvement_str = f" ✅ +${result['improvement']:.2f} vs no-filter"
                elif result["improvement"] < 0:
                    improvement_str = f" ❌ ${result['improvement']:.2f} vs no-filter"
                else:
                    improvement_str = f" = same"
                print(f"  Range<{thresh}%: ${result['net_with_filter']:.2f} ({result['trades_with_filter']}t, {result['wr_with_filter']}%WR, {result['ranging_pct']}% ranging){improvement_str}")
        
        all_results.extend(coin_results)
        
        if coin_results:
            best = max(coin_results, key=lambda r: r["net_with_filter"])
            print(f"\n  🏆 Best for {pid}: Range<{best['threshold']}% = ${best['net_with_filter']:.2f} "
                  f"({best['trades_with_filter']}t, {best['wr_with_filter']}%WR)")
            print(f"  vs No Filter: ${best['net_without_filter']:.2f} ({best['trades_without_filter']}t)")
    
    # Overall summary
    print(f"\n{'='*120}")
    print(f"  M15 RANGING FILTER — OVERALL SUMMARY")
    print(f"{'='*120}")
    
    # Best per coin
    for pid in PRODUCTS:
        coin_results = [r for r in all_results if r["product"] == pid]
        if coin_results:
            best = max(coin_results, key=lambda r: r["net_with_filter"])
            no_filter_net = best["net_without_filter"]
            icon = "✅" if best["improvement"] > 0 else "❌"
            print(f"\n  {pid}: Range<{best['threshold']}% → ${best['net_with_filter']:.2f} vs ${no_filter_net:.2f} "
                  f"(no filter) {icon}")
    
    # Write report
    report = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "results": all_results,
    }
    
    out = Path(DEFAULT_REPORT_PATH)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(f"\n  Report: {out}")
    
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
