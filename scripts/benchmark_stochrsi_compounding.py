#!/usr/bin/env python3
"""
StochRSI + RSI_exit_80 with GEOMETRIC COMPOUNDING
===================================================
Now that we know @main's $422.64 uses geometric compounding (95% of growing bankroll),
let's apply the same compounding to the StochRSI champion.

StochRSI(5,2) + 30% TP + 4% SL fixed sizing = $240.08
StochRSI(5,2) + 30% TP + 4% SL COMPOUNDING = ???

Also testing: StochRSI(5,2) + RSI>80 exit + No SL COMPOUNDING
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
DEFAULT_REPORT_PATH = ROOT / "reports" / "stochrsi_compounding.json"

PRODUCT = "RAVE-USD"
STARTING_CASH = 48.0


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


def run_compounding_strategy(candles, strategy_name, **params):
    """Run strategy with GEOMETRIC COMPOUNDING (95% of growing bankroll)."""
    if len(candles) < 50:
        return None
    
    closes = [float(c["close"]) for c in candles]
    
    # Fee tiers (Coinbase advanced)
    def get_fee_rate(volume):
        if volume >= 50000: return 0.0015
        elif volume >= 10000: return 0.0025
        else: return 0.0040
    
    rsi_period = params.get("rsi_period", 4)
    rsi_vals = compute_rsi(closes, rsi_period)
    
    use_stoch = params.get("use_stoch", False)
    stoch_vals = None
    if use_stoch:
        stoch_vals = compute_stochrsi(closes, rsi_period, params.get("stoch_period", 2))
    
    rsi_entry = params.get("rsi_entry", 30)
    stoch_entry = params.get("stoch_entry", 10)
    rsi_exit = params.get("rsi_exit", 0)  # 0 = disabled
    tp_pct = params.get("tp_pct", 0.25)
    sl_pct = params.get("sl_pct", 0.03)
    max_hold = params.get("max_hold", 24)
    deploy_pct = params.get("deploy_pct", 0.95)
    btc_gate = params.get("btc_gate", False)
    session_gate = params.get("session_gate", False)
    
    cash = STARTING_CASH
    in_position = False
    position = None
    trades = []
    total_volume = 0.0
    total_fees = 0.0
    
    from datetime import datetime, timezone
    
    for i in range(rsi_period + 10, len(candles) - 1):
        c = candles[i]
        h = float(c["high"])
        l = float(c["low"])
        cl = float(c["close"])
        ts = int(c.get("start", c.get("time", 0)))
        current_rsi = rsi_vals[i]
        
        fr = get_fee_rate(total_volume)
        
        # Session Gate
        if session_gate:
            hour = datetime.fromtimestamp(ts, tz=timezone.utc).hour
            if hour in [12, 19, 6, 0]:
                # EXIT still processes, but no entry
                pass
        
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
                entry_fee = position["entry"] * qty * fr
                exit_fee = exit_price * qty * fr
                net = gross - entry_fee - exit_fee
                
                cash += position["quote"] + net
                total_volume += position["quote"] + (exit_price * qty)
                total_fees += entry_fee + exit_fee
                
                trades.append({"net": net, "reason": exit_reason, "win": net > 0})
                in_position = False
                position = None
                continue
        
        # ENTRY
        if not in_position and cash >= 10.0:
            entry_signal = False
            
            if use_stoch and stoch_vals:
                stoch_val = stoch_vals[i]
                if current_rsi < rsi_entry and stoch_val < stoch_entry:
                    entry_signal = True
            else:
                if current_rsi < rsi_entry:
                    entry_signal = True
            
            # Session gate check
            if session_gate and entry_signal:
                hour = datetime.fromtimestamp(ts, tz=timezone.utc).hour
                if hour in [12, 19, 6, 0]:
                    entry_signal = False
            
            if entry_signal:
                # GEOMETRIC COMPOUNDING: deploy % of CURRENT cash
                deploy = cash * deploy_pct
                entry_fee = cl * (deploy / cl) * fr
                qty = (deploy - entry_fee) / cl
                
                if qty > 0:
                    cash -= deploy
                    position = {"entry": cl, "qty": qty, "bar": i, "quote": deploy}
                    in_position = True
    
    # Close open position
    if position:
        cash += position["quote"]
    
    net = cash - STARTING_CASH
    wins = [t for t in trades if t["win"]]
    
    return {
        "strategy": strategy_name,
        "net": round(net, 2),
        "return_pct": round(net / STARTING_CASH * 100, 1),
        "trades": len(trades),
        "wr": round(len(wins) / max(1, len(trades)) * 100, 1),
        "avg": round(net / max(1, len(trades)), 4),
        "total_volume": round(total_volume, 2),
        "total_fees": round(total_fees, 2),
        "final_cash": round(cash, 2),
    }


def main():
    client = CoinbaseAdvancedClient()
    
    print(f"Fetching 72h candles for {PRODUCT}...")
    candles = fetch_candles_72h(client, PRODUCT, "FIVE_MINUTE")
    print(f"  Got {len(candles)} candles\n")
    
    results = []
    
    # 1. RSI(4)<30 + RSI>80 exit + 3% SL (replicate @main's $422.64)
    print("1. RSI(4)<30 + RSI>80 exit + 3% SL (compound)")
    result = run_compounding_strategy(candles, "rsi4_30_rsi80_exit_sl3_compound",
        rsi_period=4, rsi_entry=30, rsi_exit=80, sl_pct=0.03, tp_pct=0.25, max_hold=24)
    if result:
        results.append(result)
        print(f"  ${result['net']:.2f} ({result['return_pct']}%), {result['trades']}t, {result['wr']}%WR, vol=${result['total_volume']:.0f}, fees=${result['total_fees']:.2f}")
    
    # 2. RSI(4)<30 + RSI>80 exit + NO SL (compound)
    print("\n2. RSI(4)<30 + RSI>80 exit + No SL (compound)")
    result = run_compounding_strategy(candles, "rsi4_30_rsi80_exit_nosl_compound",
        rsi_period=4, rsi_entry=30, rsi_exit=80, sl_pct=0.0, tp_pct=0.25, max_hold=24)
    if result:
        results.append(result)
        print(f"  ${result['net']:.2f} ({result['return_pct']}%), {result['trades']}t, {result['wr']}%WR")
    
    # 3. StochRSI(5,2) + 30% TP + 4% SL (compound)
    print("\n3. StochRSI(5,2) + 30% TP + 4% SL (compound)")
    result = run_compounding_strategy(candles, "stochrsi5_2_tp30_sl4_compound",
        rsi_period=5, use_stoch=True, stoch_period=2, rsi_entry=30, stoch_entry=10,
        tp_pct=0.30, sl_pct=0.04, max_hold=24)
    if result:
        results.append(result)
        print(f"  ${result['net']:.2f} ({result['return_pct']}%), {result['trades']}t, {result['wr']}%WR")
    
    # 4. StochRSI(5,2) + RSI>80 exit + No SL (compound)
    print("\n4. StochRSI(5,2) + RSI>80 exit + No SL (compound)")
    result = run_compounding_strategy(candles, "stochrsi5_2_rsi80_exit_nosl_compound",
        rsi_period=5, use_stoch=True, stoch_period=2, rsi_entry=30, stoch_entry=10,
        rsi_exit=80, tp_pct=0.0, sl_pct=0.0, max_hold=24)
    if result:
        results.append(result)
        print(f"  ${result['net']:.2f} ({result['return_pct']}%), {result['trades']}t, {result['wr']}%WR")
    
    # 5. RSI(4)<45 + RSI>80 exit (compound) — @main's wider entry
    print("\n5. RSI(4)<45 + RSI>80 exit (compound)")
    result = run_compounding_strategy(candles, "rsi4_45_rsi80_exit_compound",
        rsi_period=4, rsi_entry=45, rsi_exit=80, sl_pct=0.0, tp_pct=0.0, max_hold=24)
    if result:
        results.append(result)
        print(f"  ${result['net']:.2f} ({result['return_pct']}%), {result['trades']}t, {result['wr']}%WR")
    
    # 6. RSI(4)<45 + RSI>80 exit + BTC gate + session gate (compound)
    print("\n6. RSI(4)<45 + RSI>80 exit + gates (compound)")
    result = run_compounding_strategy(candles, "rsi4_45_rsi80_exit_gates_compound",
        rsi_period=4, rsi_entry=45, rsi_exit=80, sl_pct=0.0, tp_pct=0.0, max_hold=24,
        btc_gate=True, session_gate=True)
    if result:
        results.append(result)
        print(f"  ${result['net']:.2f} ({result['return_pct']}%), {result['trades']}t, {result['wr']}%WR")
    
    # 7. StochRSI(5,2) + RSI>80 + session gate (compound)
    print("\n7. StochRSI(5,2) + RSI>80 + session gate (compound)")
    result = run_compounding_strategy(candles, "stochrsi5_2_rsi80_session_compound",
        rsi_period=5, use_stoch=True, stoch_period=2, rsi_entry=30, stoch_entry=10,
        rsi_exit=80, tp_pct=0.0, sl_pct=0.0, max_hold=24, session_gate=True)
    if result:
        results.append(result)
        print(f"  ${result['net']:.2f} ({result['return_pct']}%), {result['trades']}t, {result['wr']}%WR")
    
    # Sort and print
    results.sort(key=lambda r: r["net"], reverse=True)
    
    print(f"\n{'='*110}")
    print(f"{'Strategy':<45} {'Net $':>8} {'Ret%':>7} {'Trades':>7} {'Win%':>6} {'Vol$':>10} {'Fees$':>8}")
    print(f"{'='*110}")
    for r in results:
        print(f"{r['strategy']:<45} ${r['net']:>6.2f} {r['return_pct']:>6.1f}% {r['trades']:>7} {r['wr']:>5.1f}% ${r['total_volume']:>8.0f} ${r['total_fees']:>6.2f}")
    
    # Write report
    report = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "results": results,
        "baselines": {
            "rsi_exit_80_compound": 422.64,
            "stochrsi_fixed_sizing": 240.08,
        },
    }
    
    out = Path(DEFAULT_REPORT_PATH)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(f"\nReport: {out}")
    
    if results:
        best = results[0]
        print(f"\n🏆 NEW CHAMPION: {best['strategy']}")
        print(f"   ${best['net']:.2f}/72h ({best['return_pct']}%) on $48")
        print(f"   Projected monthly: ${best['net']*10:.0f}")
        
        if best["net"] > 422.64:
            print(f"\n🚨🚨🚨 BEAT THE $422 CEILING! +${best['net']-422.64:.2f} ({(best['net']-422.64)/422.64*100:.1f}% improvement)")
        else:
            print(f"\n👑 $422.64 RSI_exit_80 still stands")
    
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
