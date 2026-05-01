#!/usr/bin/env python3
"""
M1 TP Level Sweep — Finding What Actually Gets Hit
=====================================================
60-day data showed 0% of trades hit 25% TP. What TP levels actually work?

Testing: 2%, 3%, 5%, 7%, 10%, 15% on M1 RSI(3)<30
"""
from __future__ import annotations

import json
import time
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
from benchmark_coinbase_spot_rsi import fetch_candles_72h
from coinbase_advanced_client import CoinbaseAdvancedClient

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_REPORT_PATH = ROOT / "reports" / "m1_tp_level_sweep.json"

PRODUCT = "RAVE-USD"
STARTING_CASH = 48.0


def compute_rsi(closes, period=3):
    if len(closes) < period + 1:
        return [50.0] * len(closes)
    deltas = [closes[i] - closes[i-1] for i in range(1, len(closes))]
    gains = [d if d > 0 else 0 for d in deltas]
    losses = [-d if d < 0 else 0 for d in deltas]
    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period
    result = []
    if avg_loss > 0:
        rs = avg_gain / avg_loss
        result.append(100 - 100 / (1 + rs))
    else:
        result.append(100.0)
    for i in range(period, len(deltas)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period
        if avg_loss > 0:
            rs = avg_gain / avg_loss
            result.append(100 - 100 / (1 + rs))
        else:
            result.append(100.0)
    return [50.0] * period + result


def run_m1_with_tp(candles, rsi_period, rsi_entry, tp_pct, max_hold, fee_bps=40, deploy_pct=0.95):
    if len(candles) < rsi_period + 60:
        return None
    
    fee_rate = fee_bps / 10000.0
    closes = [c["close"] for c in candles]
    rsi_vals = compute_rsi(closes, rsi_period)
    
    cash = STARTING_CASH
    in_position = False
    position = None
    trades = []
    
    for i in range(rsi_period + 60, len(candles) - 1):
        c = candles[i]
        h = c["high"]
        l = c["low"]
        cl = c["close"]
        current_rsi = rsi_vals[i]
        
        if in_position and position:
            exit_price = None
            exit_reason = None
            
            if h >= position["entry"] * (1 + tp_pct):
                exit_price = position["entry"] * (1 + tp_pct)
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
                trades.append({"net": net, "reason": exit_reason, "win": net > 0})
                in_position = False
                position = None
                continue
        
        if not in_position and cash >= 10.0 and current_rsi < rsi_entry:
            deploy = cash * deploy_pct
            entry_fee = cl * (deploy / cl) * fee_rate
            qty = (deploy - entry_fee) / cl
            
            if qty > 0:
                cash -= deploy
                position = {"entry": cl, "qty": qty, "bar": i, "quote": deploy}
                in_position = True
    
    if position:
        cash += position["quote"]
    
    net = cash - STARTING_CASH
    wins = [t for t in trades if t["win"]]
    tp_hits = len([t for t in trades if t["reason"] == "tp"])
    
    return {
        "tp_pct": tp_pct * 100,
        "net": round(net, 2),
        "return_pct": round(net / STARTING_CASH * 100, 1),
        "trades": len(trades),
        "wr": round(len(wins) / max(1, len(trades)) * 100, 1),
        "avg": round(net / max(1, len(trades)), 4),
        "tp_hits": tp_hits,
        "tp_hit_pct": round(tp_hits / max(1, len(trades)) * 100, 1),
        "timeouts": len(trades) - tp_hits,
    }


def main():
    client = CoinbaseAdvancedClient()
    
    print("=" * 80)
    print("  M1 TP LEVEL SWEEP — Finding What Actually Gets Hit")
    print("=" * 80)
    
    # Use 72h data (faster) to find optimal TP, then verify on 60d
    print(f"\nFetching 72h M1 candles for {PRODUCT}...")
    candles = fetch_candles_72h(client, PRODUCT, "ONE_MINUTE")
    print(f"  Got {len(candles)} M1 candles")
    
    if not candles:
        print("ERROR: Could not fetch candles")
        return 1
    
    results = []
    
    # Sweep TP levels
    tp_levels = [0.02, 0.03, 0.05, 0.07, 0.10, 0.15, 0.20, 0.25]
    hold_periods = [24, 54, 100]
    
    print(f"\n  {'TP%':>6} {'Hold':>6} {'Net $':>8} {'Trades':>7} {'Win%':>6} {'TP Hits':>8} {'TP%':>6} {'Avg/Tr':>8}")
    print(f"  {'-'*60}")
    
    for tp in tp_levels:
        for hold in hold_periods:
            result = run_m1_with_tp(candles, rsi_period=3, rsi_entry=30, tp_pct=tp, max_hold=hold)
            if result:
                results.append(result)
                print(f"  {result['tp_pct']:>5.0f}% {hold:>6} ${result['net']:>6.2f} {result['trades']:>7} {result['wr']:>5.1f}% {result['tp_hits']:>7} {result['tp_hit_pct']:>5.1f}% ${result['avg']:>6.4f}")
    
    # Sort by net
    results.sort(key=lambda r: r["net"], reverse=True)
    
    print(f"\n{'='*60}")
    print(f"  TOP 10 CONFIGS:")
    print(f"  {'TP%':>6} {'Hold':>6} {'Net $':>8} {'Trades':>7} {'Win%':>6} {'TP Hits':>8}")
    print(f"  {'-'*45}")
    for r in results[:10]:
        print(f"  {r['tp_pct']:>5.0f}% {r.get('hold', '?'):>6} ${r['net']:>6.2f} {r['trades']:>7} {r['wr']:>5.1f}% {r['tp_hits']:>7}")
    
    # Write report
    report = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "top_10": results[:10],
        "all_results": results,
    }
    
    out = Path(DEFAULT_REPORT_PATH)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(f"\n  Report: {out}")
    
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
