#!/usr/bin/env python3
"""
Frequency Frontier — Beat $422.64 by maximizing signal frequency
================================================================
Key insight: RSI_exit_80 wins because of 232 trades, not per-trade quality.

Experiments:
1. RSI(4)<50 + RSI>80 exit (wider entry)
2. RSI(4)<55 + RSI>80 exit (even wider)
3. RSI(3)<45 + RSI>80 exit (faster RSI)
4. RSI(3)<50 + RSI>80 exit (faster + wider)
5. RSI(4)<45 + RSI>70 exit (earlier exit = more trades)
6. RSI(4)<45 + RSI>60 exit (even earlier exit)
7. CONCURRENT multi-coin (2-3 positions at once, not rotation)
8. RSI(2)<40 + RSI>80 exit (ULTRA-fast RSI)
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
DEFAULT_REPORT_PATH = ROOT / "reports" / "frequency_frontier.json"

PRODUCTS = ["RAVE-USD", "BAL-USD", "BLUR-USD", "ALEPH-USD", "IOTX-USD"]
STARTING_CASH = 48.0
FEE_BPS = 5.0
BASELINE = 422.64


def run_rsi_exit_strategy(candles, rsi_period, rsi_entry, rsi_exit, max_hold=24, sl_pct=0.0, deploy_pct=0.95):
    """Fast RSI entry + RSI exit strategy."""
    if len(candles) < rsi_period + 10:
        return None
    
    closes = [float(c["close"]) for c in candles]
    fee_rate = FEE_BPS / 10000.0
    rsi_vals = compute_rsi(closes, rsi_period)
    
    cash = STARTING_CASH
    in_position = False
    position = None
    trades = []
    
    for i in range(rsi_period + 10, len(candles) - 1):
        c = candles[i]
        h = float(c["high"])
        l = float(c["low"])
        cl = float(c["close"])
        current_rsi = rsi_vals[i]
        
        # EXIT
        if in_position and position:
            exit_price = None
            exit_reason = None
            
            if sl_pct > 0:
                sl_price = position["entry"] * (1 - sl_pct)
                if l <= sl_price:
                    exit_price = sl_price
                    exit_reason = "sl"
            
            if exit_price is None and current_rsi >= rsi_exit:
                exit_price = cl
                exit_reason = "rsi_exit"
            
            if exit_price is None and (i - position["bar"]) >= max_hold:
                exit_price = cl
                exit_reason = "timeout"
            
            if exit_price is not None:
                qty = position["qty"]
                gross = (exit_price - position["entry"]) * qty
                exit_fee = exit_price * qty * fee_rate
                net = gross - position["entry_fee"] - exit_fee
                
                cash += position["entry"] * qty + net
                trades.append({"net": net, "reason": exit_reason, "win": net > 0})
                in_position = False
                position = None
                continue
        
        # ENTRY
        if not in_position and cash >= 10.0:
            if current_rsi < rsi_entry:
                deploy = cash * deploy_pct
                entry_fee = cl * (deploy / cl) * fee_rate
                qty = (deploy - entry_fee) / cl
                
                if qty > 0:
                    cash -= deploy
                    position = {"entry": cl, "qty": qty, "bar": i, "entry_fee": entry_fee}
                    in_position = True
    
    if not trades:
        return None
    
    wins = [t for t in trades if t["win"]]
    net = sum(t["net"] for t in trades)
    
    return {
        "net": round(net, 2),
        "trades": len(trades),
        "wr": round(len(wins) / len(trades) * 100, 1),
        "avg": round(net / len(trades), 4),
    }


def run_concurrent_multi_coin(all_candles, rsi_period, rsi_entry, rsi_exit, max_concurrent=2, max_hold=24):
    """Multi-coin with concurrent positions."""
    fee_rate = FEE_BPS / 10000.0
    
    # Precompute RSI
    coin_data = {}
    for pid, candles in all_candles.items():
        closes = [float(c["close"]) for c in candles]
        rsi_vals = compute_rsi(closes, rsi_period)
        coin_data[pid] = {"candles": candles, "rsi": rsi_vals, "closes": closes}
    
    max_bars = max(len(cd["candles"]) for cd in coin_data.values())
    cash = STARTING_CASH
    positions = []  # list of {"product", "entry", "qty", "bar", "entry_fee"}
    trades = []
    
    for i in range(rsi_period + 10, max_bars - 1):
        # EXIT positions
        still_open = []
        for pos in positions:
            pid = pos["product"]
            cd = coin_data[pid]
            if i < len(cd["candles"]):
                c = cd["candles"][i]
                cl = float(c["close"])
                current_rsi = cd["rsi"][i]
                
                exit_price = None
                exit_reason = None
                
                if current_rsi >= rsi_exit:
                    exit_price = cl
                    exit_reason = "rsi_exit"
                elif (i - pos["bar"]) >= max_hold:
                    exit_price = cl
                    exit_reason = "timeout"
                
                if exit_price:
                    qty = pos["qty"]
                    gross = (exit_price - pos["entry"]) * qty
                    exit_fee = exit_price * qty * fee_rate
                    net = gross - pos["entry_fee"] - exit_fee
                    
                    cash += pos["entry"] * qty + net
                    trades.append({"net": net, "reason": exit_reason, "win": net > 0, "product": pid})
                else:
                    still_open.append(pos)
        
        positions = still_open
        
        # ENTRY — scan all coins for signals, fill up to max_concurrent
        if len(positions) < max_concurrent and cash >= 10.0:
            signals = []
            for pid, cd in coin_data.items():
                if i < len(cd["candles"]):
                    current_rsi = cd["rsi"][i]
                    if current_rsi < rsi_entry:
                        signals.append((pid, cd["candles"][i], current_rsi))
            
            # Sort by RSI (lowest first = most oversold)
            signals.sort(key=lambda x: x[2])
            
            for pid, c, _ in signals[:max_concurrent - len(positions)]:
                if cash < 10.0:
                    break
                cl = float(c["close"])
                deploy = cash * 0.95 / (max_concurrent - len(positions))  # Split remaining cash
                deploy = min(deploy, cash * 0.95)
                if deploy < 10.0:
                    deploy = cash * 0.95
                
                entry_fee = cl * (deploy / cl) * fee_rate
                qty = (deploy - entry_fee) / cl
                
                if qty > 0:
                    cash -= deploy
                    positions.append({"product": pid, "entry": cl, "qty": qty, "bar": i, "entry_fee": entry_fee})
    
    if not trades:
        return None
    
    wins = [t for t in trades if t["win"]]
    net = sum(t["net"] for t in trades)
    
    return {
        "net": round(net, 2),
        "trades": len(trades),
        "wr": round(len(wins) / len(trades) * 100, 1),
        "avg": round(net / len(trades), 4),
        "max_concurrent": max_concurrent,
    }


def main():
    client = CoinbaseAdvancedClient()
    
    # Fetch RAVE
    print(f"Fetching 72h candles for RAVE-USD...")
    rave_candles = fetch_candles_72h(client, "RAVE-USD", "FIVE_MINUTE")
    print(f"  Got {len(rave_candles)} candles\n")
    
    results = []
    
    # EXP 1: RSI(4)<50 + RSI>80 exit
    print("EXP 1: RSI(4)<50 + RSI>80 exit")
    result = run_rsi_exit_strategy(rave_candles, rsi_period=4, rsi_entry=50, rsi_exit=80)
    if result:
        result["name"] = "RSI4_50_exit80"
        results.append(result)
        print(f"  ${result['net']:.2f}, {result['trades']}t, {result['wr']}%WR, ${result['avg']:.4f}/t")
    
    # EXP 2: RSI(4)<55 + RSI>80 exit
    print("\nEXP 2: RSI(4)<55 + RSI>80 exit")
    result = run_rsi_exit_strategy(rave_candles, rsi_period=4, rsi_entry=55, rsi_exit=80)
    if result:
        result["name"] = "RSI4_55_exit80"
        results.append(result)
        print(f"  ${result['net']:.2f}, {result['trades']}t, {result['wr']}%WR, ${result['avg']:.4f}/t")
    
    # EXP 3: RSI(3)<45 + RSI>80 exit
    print("\nEXP 3: RSI(3)<45 + RSI>80 exit")
    result = run_rsi_exit_strategy(rave_candles, rsi_period=3, rsi_entry=45, rsi_exit=80)
    if result:
        result["name"] = "RSI3_45_exit80"
        results.append(result)
        print(f"  ${result['net']:.2f}, {result['trades']}t, {result['wr']}%WR, ${result['avg']:.4f}/t")
    
    # EXP 4: RSI(3)<50 + RSI>80 exit
    print("\nEXP 4: RSI(3)<50 + RSI>80 exit")
    result = run_rsi_exit_strategy(rave_candles, rsi_period=3, rsi_entry=50, rsi_exit=80)
    if result:
        result["name"] = "RSI3_50_exit80"
        results.append(result)
        print(f"  ${result['net']:.2f}, {result['trades']}t, {result['wr']}%WR, ${result['avg']:.4f}/t")
    
    # EXP 5: RSI(4)<45 + RSI>70 exit
    print("\nEXP 5: RSI(4)<45 + RSI>70 exit")
    result = run_rsi_exit_strategy(rave_candles, rsi_period=4, rsi_entry=45, rsi_exit=70)
    if result:
        result["name"] = "RSI4_45_exit70"
        results.append(result)
        print(f"  ${result['net']:.2f}, {result['trades']}t, {result['wr']}%WR, ${result['avg']:.4f}/t")
    
    # EXP 6: RSI(4)<45 + RSI>60 exit
    print("\nEXP 6: RSI(4)<45 + RSI>60 exit")
    result = run_rsi_exit_strategy(rave_candles, rsi_period=4, rsi_entry=45, rsi_exit=60)
    if result:
        result["name"] = "RSI4_45_exit60"
        results.append(result)
        print(f"  ${result['net']:.2f}, {result['trades']}t, {result['wr']}%WR, ${result['avg']:.4f}/t")
    
    # EXP 7: RSI(2)<40 + RSI>80 exit (ULTRA-fast)
    print("\nEXP 7: RSI(2)<40 + RSI>80 exit (ULTRA-fast)")
    result = run_rsi_exit_strategy(rave_candles, rsi_period=2, rsi_entry=40, rsi_exit=80)
    if result:
        result["name"] = "RSI2_40_exit80"
        results.append(result)
        print(f"  ${result['net']:.2f}, {result['trades']}t, {result['wr']}%WR, ${result['avg']:.4f}/t")
    
    # EXP 8: RSI(2)<45 + RSI>80 exit
    print("\nEXP 8: RSI(2)<45 + RSI>80 exit")
    result = run_rsi_exit_strategy(rave_candles, rsi_period=2, rsi_entry=45, rsi_exit=80)
    if result:
        result["name"] = "RSI2_45_exit80"
        results.append(result)
        print(f"  ${result['net']:.2f}, {result['trades']}t, {result['wr']}%WR, ${result['avg']:.4f}/t")
    
    # EXP 9: RSI(3)<55 + RSI>70 exit (MAX frequency)
    print("\nEXP 9: RSI(3)<55 + RSI>70 exit (MAX frequency)")
    result = run_rsi_exit_strategy(rave_candles, rsi_period=3, rsi_entry=55, rsi_exit=70)
    if result:
        result["name"] = "RSI3_55_exit70"
        results.append(result)
        print(f"  ${result['net']:.2f}, {result['trades']}t, {result['wr']}%WR, ${result['avg']:.4f}/t")
    
    # EXP 10: RSI(4)<45 + RSI>80 exit (baseline replication)
    print("\nEXP 10: RSI(4)<45 + RSI>80 exit (baseline check)")
    result = run_rsi_exit_strategy(rave_candles, rsi_period=4, rsi_entry=45, rsi_exit=80)
    if result:
        result["name"] = "RSI4_45_exit80_baseline"
        results.append(result)
        print(f"  ${result['net']:.2f}, {result['trades']}t, {result['wr']}%WR, ${result['avg']:.4f}/t")
    
    # Now concurrent multi-coin
    print(f"\nFetching multi-coin data...")
    all_candles = {"RAVE-USD": rave_candles}
    for pid in ["BAL-USD", "BLUR-USD", "ALEPH-USD", "IOTX-USD"]:
        print(f"  Fetching {pid}...")
        candles = fetch_candles_72h(client, pid, "FIVE_MINUTE")
        all_candles[pid] = candles
    
    # EXP 11: Concurrent 2-coin RSI(4)<45 + RSI>80
    print("\nEXP 11: Concurrent 2-coin RSI(4)<45 + RSI>80")
    result = run_concurrent_multi_coin(all_candles, rsi_period=4, rsi_entry=45, rsi_exit=80, max_concurrent=2)
    if result:
        result["name"] = "Concurrent2_RSI4_45_exit80"
        results.append(result)
        print(f"  ${result['net']:.2f}, {result['trades']}t, {result['wr']}%WR, ${result['avg']:.4f}/t")
    
    # EXP 12: Concurrent 3-coin RSI(4)<45 + RSI>80
    print("\nEXP 12: Concurrent 3-coin RSI(4)<45 + RSI>80")
    result = run_concurrent_multi_coin(all_candles, rsi_period=4, rsi_entry=45, rsi_exit=80, max_concurrent=3)
    if result:
        result["name"] = "Concurrent3_RSI4_45_exit80"
        results.append(result)
        print(f"  ${result['net']:.2f}, {result['trades']}t, {result['wr']}%WR, ${result['avg']:.4f}/t")
    
    # EXP 13: Concurrent 2-coin RSI(4)<50 + RSI>80
    print("\nEXP 13: Concurrent 2-coin RSI(4)<50 + RSI>80")
    result = run_concurrent_multi_coin(all_candles, rsi_period=4, rsi_entry=50, rsi_exit=80, max_concurrent=2)
    if result:
        result["name"] = "Concurrent2_RSI4_50_exit80"
        results.append(result)
        print(f"  ${result['net']:.2f}, {result['trades']}t, {result['wr']}%WR, ${result['avg']:.4f}/t")
    
    # EXP 14: Concurrent 3-coin RSI(3)<50 + RSI>70
    print("\nEXP 14: Concurrent 3-coin RSI(3)<50 + RSI>70 (MAX FREQUENCY)")
    result = run_concurrent_multi_coin(all_candles, rsi_period=3, rsi_entry=50, rsi_exit=70, max_concurrent=3)
    if result:
        result["name"] = "Concurrent3_RSI3_50_exit70"
        results.append(result)
        print(f"  ${result['net']:.2f}, {result['trades']}t, {result['wr']}%WR, ${result['avg']:.4f}/t")
    
    # Sort and summary
    results.sort(key=lambda r: r["net"], reverse=True)
    
    print(f"\n{'='*100}")
    print(f"{'Strategy':<40} {'Net $':>8} {'Trades':>7} {'Win%':>6} {'Avg/Tr':>9} {'vs $422':>8}")
    print(f"{'='*100}")
    for r in results:
        vs = f"+{r['net']-422.64:.0f}" if r['net'] > 422.64 else f"{r['net']-422.64:.0f}"
        beats = "✅" if r['net'] > 422.64 else ""
        print(f"{r['name']:<40} ${r['net']:>6.2f} {r['trades']:>7} {r['wr']:>5.1f}% ${r['avg']:>7.4f} {vs:>7} {beats}")
    
    # Write report
    report = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "baseline": BASELINE,
        "results": results,
    }
    
    out = Path(DEFAULT_REPORT_PATH)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(f"\nReport: {out}")
    
    if results and results[0]["net"] > 422.64:
        print(f"\n🚨🚨🚨 CEILING SMASHED! ${results[0]['net']:.2f} (+{results[0]['net']-422.64:.0f})")
    else:
        print(f"\n👑 RSI_exit_80 ($422.64) still stands")
        print(f"   Best attempt: ${results[0]['net']:.2f} ({results[0]['name']})")
    
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
