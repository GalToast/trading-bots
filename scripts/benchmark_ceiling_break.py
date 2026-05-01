#!/usr/bin/env python3
"""
Ceiling Break Experiment — Ultimate Combinations
=================================================
Testing 4 combinations nobody has tried:

1. StochRSI(5,2) entry + RSI>80 EXIT + No SL
2. RSI(4)<45 + StochRSI(2) confirmation + RSI>80 exit
3. Multi-coin RSI>80 rotation (RAVE, BAL, BLUR, ALEPH, IOTX)
4. Tick-native StochRSI on M1 candles

Target: Beat $422.64/72h (RSI_exit_80 champion)
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
DEFAULT_REPORT_PATH = ROOT / "reports" / "ceiling_break_experiment.json"

PRODUCTS = ["RAVE-USD", "BAL-USD", "BLUR-USD", "ALEPH-USD", "IOTX-USD"]
STARTING_CASH = 48.0
FEE_BPS = 5.0

# Current champions
RSI_EXIT_80_BASELINE = 422.64
STOCHRSI_CHAMPION = 240.08


def compute_stochrsi(closes, rsi_period=5, stoch_period=2):
    """Compute StochRSI for entire series."""
    rsi_vals = compute_rsi(closes, rsi_period)
    result = [50.0] * len(closes)
    
    for i in range(rsi_period + stoch_period, len(rsi_vals)):
        window = rsi_vals[max(0, i - stoch_period + 1):i + 1]
        low = min(window)
        high = max(window)
        if high > low:
            result[i] = (rsi_vals[i] - low) / (high - low) * 100
        else:
            result[i] = 50.0
    return result


def run_single_coin_strategy(candles, strategy_name, **params):
    """Run a single-coin strategy."""
    if len(candles) < 50:
        return None
    
    closes = [float(c["close"]) for c in candles]
    fee_rate = FEE_BPS / 10000.0
    
    rsi_period = params.get("rsi_period", 4)
    rsi_vals = compute_rsi(closes, rsi_period)
    
    use_stoch = params.get("use_stoch", False)
    stoch_vals = None
    if use_stoch:
        stoch_vals = compute_stochrsi(closes, rsi_period, params.get("stoch_period", 2))
    
    rsi_entry_thresh = params.get("rsi_entry", 30)
    stoch_entry_thresh = params.get("stoch_entry", 10)
    rsi_exit_thresh = params.get("rsi_exit", 80)
    tp_pct = params.get("tp_pct", 0.25)
    sl_pct = params.get("sl_pct", 0.0)  # Default: NO stop loss
    max_hold = params.get("max_hold", 24)
    deploy_pct = params.get("deploy_pct", 0.95)
    
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
            
            # TP check
            if tp_pct > 0:
                tp_price = position["entry"] * (1 + tp_pct)
                if h >= tp_price:
                    exit_price = tp_price
                    exit_reason = "tp"
            
            # SL check
            if sl_pct > 0 and exit_price is None:
                sl_price = position["entry"] * (1 - sl_pct)
                if l <= sl_price:
                    exit_price = sl_price
                    exit_reason = "sl"
            
            # RSI exit check
            if exit_price is None and current_rsi >= rsi_exit_thresh:
                exit_price = cl
                exit_reason = "rsi_exit"
            
            # Timeout check
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
            entry_signal = False
            
            if use_stoch and stoch_vals:
                # Dual filter: RSI < X AND StochRSI < Y
                stoch_val = stoch_vals[i]
                if current_rsi < rsi_entry_thresh and stoch_val < stoch_entry_thresh:
                    entry_signal = True
            else:
                # Simple RSI entry
                if current_rsi < rsi_entry_thresh:
                    entry_signal = True
            
            if entry_signal:
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
        "strategy": strategy_name,
        "net": round(net, 2),
        "trades": len(trades),
        "wr": round(len(wins) / len(trades) * 100, 1),
        "avg": round(net / len(trades), 4),
        "volume": round(sum(t["net"] for t in trades) + STARTING_CASH - cash, 2),
    }


def run_multi_coin_rotation(all_candles, strategy_name, **params):
    """Multi-coin rotation — 1 position at a time across multiple coins."""
    fee_rate = FEE_BPS / 10000.0
    cash = STARTING_CASH
    position = None  # {"product": ..., "entry": ..., "qty": ..., "bar": ..., "entry_fee": ...}
    trades = []
    
    # Precompute RSI for all coins
    coin_data = {}
    rsi_period = params.get("rsi_period", 4)
    rsi_exit_thresh = params.get("rsi_exit", 80)
    max_hold = params.get("max_hold", 24)
    deploy_pct = params.get("deploy_pct", 0.95)
    rsi_entry = params.get("rsi_entry", 45)
    
    for pid, candles in all_candles.items():
        closes = [float(c["close"]) for c in candles]
        rsi_vals = compute_rsi(closes, rsi_period)
        coin_data[pid] = {
            "candles": candles,
            "rsi": rsi_vals,
            "closes": closes,
        }
    
    # Find max bars
    max_bars = max(len(cd["candles"]) for cd in coin_data.values())
    
    for i in range(rsi_period + 10, max_bars - 1):
        # EXIT current position
        if position:
            pid = position["product"]
            cd = coin_data[pid]
            if i < len(cd["candles"]):
                c = cd["candles"][i]
                h = float(c["high"])
                l = float(c["low"])
                cl = float(c["close"])
                current_rsi = cd["rsi"][i]
                
                exit_price = None
                exit_reason = None
                
                if current_rsi >= rsi_exit_thresh:
                    exit_price = cl
                    exit_reason = "rsi_exit"
                elif (i - position["bar"]) >= max_hold:
                    exit_price = cl
                    exit_reason = "timeout"
                
                if exit_price:
                    qty = position["qty"]
                    gross = (exit_price - position["entry"]) * qty
                    exit_fee = exit_price * qty * fee_rate
                    net = gross - position["entry_fee"] - exit_fee
                    
                    cash += position["entry"] * qty + net
                    trades.append({"net": net, "reason": exit_reason, "win": net > 0, "product": pid})
                    position = None
        
        # ENTRY — scan all coins for signal
        if position is None and cash >= 10.0:
            best_signal = None
            best_rsi = 999
            
            for pid, cd in coin_data.items():
                if i < len(cd["candles"]):
                    current_rsi = cd["rsi"][i]
                    if current_rsi < rsi_entry and current_rsi < best_rsi:
                        best_rsi = current_rsi
                        best_signal = (pid, cd["candles"][i])
            
            if best_signal:
                pid, c = best_signal
                cl = float(c["close"])
                deploy = cash * deploy_pct
                entry_fee = cl * (deploy / cl) * fee_rate
                qty = (deploy - entry_fee) / cl
                
                if qty > 0:
                    cash -= deploy
                    position = {"product": pid, "entry": cl, "qty": qty, "bar": i, "entry_fee": entry_fee}
    
    if not trades:
        return None
    
    wins = [t for t in trades if t["win"]]
    net = sum(t["net"] for t in trades)
    
    return {
        "strategy": strategy_name,
        "net": round(net, 2),
        "trades": len(trades),
        "wr": round(len(wins) / len(trades) * 100, 1),
        "avg": round(net / len(trades), 4),
        "coins_used": len(set(t["product"] for t in trades)),
    }


def main():
    client = CoinbaseAdvancedClient()
    
    # Fetch all coins
    all_candles = {}
    for pid in PRODUCTS:
        print(f"Fetching 72h candles for {pid}...")
        candles = fetch_candles_72h(client, pid, "FIVE_MINUTE")
        all_candles[pid] = candles
        print(f"  Got {len(candles)} candles")
    
    print(f"\n{'='*80}")
    print(f"  CEILING BREAK EXPERIMENTS")
    print(f"{'='*80}\n")
    
    results = []
    rave_candles = all_candles["RAVE-USD"]
    
    # EXP 1: StochRSI(5,2) entry + RSI>80 EXIT + No SL
    print("EXP 1: StochRSI(5,2) + RSI>80 exit + No SL")
    result = run_single_coin_strategy(
        rave_candles, "stochrsi_rsi_exit_nosl",
        rsi_period=5, use_stoch=True, stoch_period=2,
        rsi_entry=30, stoch_entry=10,
        rsi_exit=80, tp_pct=0.0, sl_pct=0.0, max_hold=24
    )
    if result:
        results.append(result)
        print(f"  Net: ${result['net']:.2f}, Trades: {result['trades']}, WR: {result['wr']}%, Avg: ${result['avg']:.4f}")
        print(f"  vs RSI_exit_80 ($422.64): {'✅ BEAT IT' if result['net'] > 422.64 else '❌'}")
    
    # EXP 1b: StochRSI + RSI>70 exit (earlier exit)
    print("\nEXP 1b: StochRSI(5,2) + RSI>70 exit + No SL")
    result = run_single_coin_strategy(
        rave_candles, "stochrsi_rsi70_exit_nosl",
        rsi_period=5, use_stoch=True, stoch_period=2,
        rsi_entry=30, stoch_entry=10,
        rsi_exit=70, tp_pct=0.0, sl_pct=0.0, max_hold=24
    )
    if result:
        results.append(result)
        print(f"  Net: ${result['net']:.2f}, Trades: {result['trades']}, WR: {result['wr']}%, Avg: ${result['avg']:.4f}")
    
    # EXP 1c: StochRSI + RSI>90 exit (let winners run more)
    print("\nEXP 1c: StochRSI(5,2) + RSI>90 exit + No SL")
    result = run_single_coin_strategy(
        rave_candles, "stochrsi_rsi90_exit_nosl",
        rsi_period=5, use_stoch=True, stoch_period=2,
        rsi_entry=30, stoch_entry=10,
        rsi_exit=90, tp_pct=0.0, sl_pct=0.0, max_hold=24
    )
    if result:
        results.append(result)
        print(f"  Net: ${result['net']:.2f}, Trades: {result['trades']}, WR: {result['wr']}%, Avg: ${result['avg']:.4f}")
    
    # EXP 2: RSI(4)<45 + StochRSI(2) confirmation + RSI>80 exit
    print("\nEXP 2: RSI(4)<45 + StochRSI<10 + RSI>80 exit")
    result = run_single_coin_strategy(
        rave_candles, "rsi45_stoch_confirm_rsi80_exit",
        rsi_period=4, use_stoch=True, stoch_period=2,
        rsi_entry=45, stoch_entry=10,
        rsi_exit=80, tp_pct=0.0, sl_pct=0.0, max_hold=24
    )
    if result:
        results.append(result)
        print(f"  Net: ${result['net']:.2f}, Trades: {result['trades']}, WR: {result['wr']}%, Avg: ${result['avg']:.4f}")
        print(f"  vs RSI_exit_80 ($422.64): {'✅ BEAT IT' if result['net'] > 422.64 else '❌'}")
    
    # EXP 2b: RSI(4)<45 + StochRSI<20 + RSI>80 exit (looser Stoch filter)
    print("\nEXP 2b: RSI(4)<45 + StochRSI<20 + RSI>80 exit")
    result = run_single_coin_strategy(
        rave_candles, "rsi45_stoch20_rsi80_exit",
        rsi_period=4, use_stoch=True, stoch_period=2,
        rsi_entry=45, stoch_entry=20,
        rsi_exit=80, tp_pct=0.0, sl_pct=0.0, max_hold=24
    )
    if result:
        results.append(result)
        print(f"  Net: ${result['net']:.2f}, Trades: {result['trades']}, WR: {result['wr']}%, Avg: ${result['avg']:.4f}")
    
    # EXP 3: Multi-coin RSI>80 rotation
    print("\nEXP 3: Multi-coin RSI(4)<45 + RSI>80 exit rotation")
    result = run_multi_coin_rotation(
        all_candles, "multi_coin_rsi45_rsi80_exit",
        rsi_period=4, rsi_entry=45, rsi_exit=80, max_hold=24
    )
    if result:
        results.append(result)
        print(f"  Net: ${result['net']:.2f}, Trades: {result['trades']}, WR: {result['wr']}%, Avg: ${result['avg']:.4f}")
        print(f"  Coins used: {result['coins_used']}")
        print(f"  vs RSI_exit_80 ($422.64): {'✅ BEAT IT' if result['net'] > 422.64 else '❌'}")
    
    # EXP 3b: Multi-coin RSI(4)<30 + RSI>80 exit (stricter entry)
    print("\nEXP 3b: Multi-coin RSI(4)<30 + RSI>80 exit rotation")
    result = run_multi_coin_rotation(
        all_candles, "multi_coin_rsi30_rsi80_exit",
        rsi_period=4, rsi_entry=30, rsi_exit=80, max_hold=24
    )
    if result:
        results.append(result)
        print(f"  Net: ${result['net']:.2f}, Trades: {result['trades']}, WR: {result['wr']}%, Avg: ${result['avg']:.4f}")
    
    # EXP 4: M1 tick-native StochRSI
    print("\nEXP 4: M1 StochRSI(5,2) + RSI>80 exit")
    print("  Fetching M1 candles for RAVE-USD...")
    rave_m1 = fetch_candles_72h(client, "RAVE-USD", "ONE_MINUTE")
    print(f"  Got {len(rave_m1)} M1 candles")
    
    result = run_single_coin_strategy(
        rave_m1, "m1_stochrsi_rsi80_exit",
        rsi_period=5, use_stoch=True, stoch_period=2,
        rsi_entry=30, stoch_entry=10,
        rsi_exit=80, tp_pct=0.0, sl_pct=0.0, max_hold=24*5  # 24 5-min bars = 120 1-min bars
    )
    if result:
        results.append(result)
        print(f"  Net: ${result['net']:.2f}, Trades: {result['trades']}, WR: {result['wr']}%, Avg: ${result['avg']:.4f}")
        print(f"  vs RSI_exit_80 ($422.64): {'✅ BEAT IT' if result['net'] > 422.64 else '❌'}")
    
    # EXP 4b: M1 with tighter timeout (60 bars = 1 hour)
    print("\nEXP 4b: M1 StochRSI(5,2) + RSI>80 exit + 60-bar timeout")
    result = run_single_coin_strategy(
        rave_m1, "m1_stochrsi_rsi80_exit_60bar",
        rsi_period=5, use_stoch=True, stoch_period=2,
        rsi_entry=30, stoch_entry=10,
        rsi_exit=80, tp_pct=0.0, sl_pct=0.0, max_hold=60
    )
    if result:
        results.append(result)
        print(f"  Net: ${result['net']:.2f}, Trades: {result['trades']}, WR: {result['wr']}%, Avg: ${result['avg']:.4f}")
    
    # Sort and print summary
    results.sort(key=lambda r: r["net"], reverse=True)
    
    print(f"\n{'='*100}")
    print(f"{'Strategy':<40} {'Net $':>8} {'Trades':>7} {'Win%':>6} {'Avg/Tr':>9} {'vs $422':>8}")
    print(f"{'='*100}")
    for r in results:
        vs = f"+{r['net']-422.64:.0f}" if r['net'] > 422.64 else f"{r['net']-422.64:.0f}"
        print(f"{r['strategy']:<40} ${r['net']:>6.2f} {r['trades']:>7} {r['wr']:>5.1f}% ${r['avg']:>7.4f} {vs:>7}")
    
    # Write report
    report = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "baselines": {
            "rsi_exit_80": 422.64,
            "stochrsi_champion": 240.08,
            "omni_projected": 300.0,
        },
        "results": results,
    }
    
    out = Path(DEFAULT_REPORT_PATH)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(f"\nReport: {out}")
    
    if results and results[0]["net"] > 422.64:
        print(f"\n🚨🚨🚨 CEILING SMASHED! ${results[0]['net']:.2f} (+{results[0]['net']-422.64:.0f} over RSI_exit_80)")
    else:
        print(f"\n👑 RSI_exit_80 ($422.64) still stands as the ceiling")
    
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
