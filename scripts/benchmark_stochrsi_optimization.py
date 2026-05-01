#!/usr/bin/env python3
"""
StochRSI Optimization Sweep
============================
Now that we've found StochRSI as the crown jewel (+$124.43/72h),
let's find the ABSOLUTE optimal parameters.

Sweeping:
- Stoch period: 2-6
- Stoch oversold threshold: 1-15
- RSI period: 3-6
- TP: 15%-40%
- SL: 2%-5%
"""
import json
import time
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
from benchmark_coinbase_spot_rsi import fetch_candles_72h, rsi as compute_rsi
from coinbase_advanced_client import CoinbaseAdvancedClient

ROOT = Path(__file__).resolve().parent.parent
PRODUCT = "RAVE-USD"
STARTING_CASH = 48.0
FEE_BPS = 5.0


def run_stochrsi_backtest(candles, rsi_period, stoch_period, stoch_os, tp_pct, sl_pct, max_hold, ob_thresh=80):
    """Fast StochRSI backtest engine."""
    if len(candles) < rsi_period + stoch_period + 10:
        return None
    
    closes = [float(c["close"]) for c in candles]
    fee_rate = FEE_BPS / 10000.0
    
    rsi_vals = compute_rsi(closes, rsi_period)
    
    # Compute StochRSI
    stoch_vals = [50.0] * len(closes)
    for i in range(rsi_period + stoch_period, len(rsi_vals)):
        window = rsi_vals[max(0, i - stoch_period + 1):i + 1]
        low = min(window)
        high = max(window)
        if high > low:
            stoch_vals[i] = (rsi_vals[i] - low) / (high - low) * 100
        else:
            stoch_vals[i] = 50.0
    
    cash = STARTING_CASH
    in_position = False
    position = None
    trades = []
    
    for i in range(rsi_period + stoch_period + 5, len(candles) - 1):
        c = candles[i]
        h = float(c["high"])
        l = float(c["low"])
        cl = float(c["close"])
        current_rsi = rsi_vals[i]
        current_stoch = stoch_vals[i]
        
        # EXIT
        if in_position and position:
            tp_price = position["entry"] * (1 + tp_pct)
            sl_price = position["entry"] * (1 - sl_pct)
            
            exit_price = None
            exit_reason = None
            
            if h >= tp_price:
                exit_price = tp_price
                exit_reason = "tp"
            elif l <= sl_price:
                exit_price = sl_price
                exit_reason = "sl"
            elif current_rsi >= ob_thresh or (i - position["bar"]) >= max_hold:
                exit_price = cl
                exit_reason = "rsi_or_timeout"
            
            if exit_price:
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
            if current_stoch < stoch_os:
                deploy = cash * 0.95
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
        "params": f"RSI({rsi_period})+Stoch({stoch_period})+OS{stoch_os}+TP{tp_pct*100:.0f}%+SL{sl_pct*100:.1f}%"
    }


def main():
    client = CoinbaseAdvancedClient()
    print(f"Fetching 72h candles for {PRODUCT}...")
    candles = fetch_candles_72h(client, PRODUCT, "FIVE_MINUTE")
    print(f"  Got {len(candles)} candles\n")
    
    results = []
    
    # Parameter grid
    rsi_periods = [3, 4, 5]
    stoch_periods = [2, 3, 4, 5]
    stoch_os_thresholds = [1, 2, 3, 5, 7, 10, 15]
    tp_levels = [0.15, 0.20, 0.25, 0.30, 0.35, 0.40]
    sl_levels = [0.02, 0.025, 0.03, 0.04, 0.05]
    max_hold_bars = [12, 18, 24, 30]
    
    total = len(rsi_periods) * len(stoch_periods) * len(stoch_os_thresholds) * len(tp_levels) * len(sl_levels) * len(max_hold_bars)
    print(f"  Sweeping {total:,} configurations...\n")
    
    count = 0
    start_time = time.time()
    
    for rsi_p in rsi_periods:
        for stoch_p in stoch_periods:
            for stoch_os in stoch_os_thresholds:
                for tp in tp_levels:
                    for sl in sl_levels:
                        for mh in max_hold_bars:
                            count += 1
                            if count % 5000 == 0:
                                elapsed = time.time() - start_time
                                print(f"  Progress: {count:,}/{total:,} ({count/total*100:.1f}%) — {elapsed:.1f}s elapsed")
                            
                            result = run_stochrsi_backtest(candles, rsi_p, stoch_p, stoch_os, tp, sl, mh)
                            if result:
                                results.append(result)
    
    elapsed = time.time() - start_time
    print(f"\n  Sweep complete: {count:,} configs in {elapsed:.1f}s")
    print(f"  Valid results: {len(results):,}\n")
    
    # Sort by net
    results.sort(key=lambda r: r["net"], reverse=True)
    
    # Top 20
    print(f"{'='*110}")
    print(f"{'Rank':<5} {'Net $':>8} {'Trades':>7} {'Win%':>6} {'Avg/Tr':>9} {'Parameters'}")
    print(f"{'='*110}")
    for idx, r in enumerate(results[:20], 1):
        print(f"{idx:<5} ${r['net']:>6.2f} {r['trades']:>7} {r['wr']:>5.1f}% ${r['avg']:>7.4f}   {r['params']}")
    
    # Bottom 5
    print(f"\n{'='*110}")
    print(f"Bottom 5 (worst):")
    for idx, r in enumerate(results[-5:], len(results)-4):
        print(f"{idx:<5} ${r['net']:>6.2f} {r['trades']:>7} {r['wr']:>5.1f}% ${r['avg']:>7.4f}   {r['params']}")
    
    # Write report
    report = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "total_configs": count,
        "elapsed_seconds": round(elapsed, 1),
        "top_20": results[:20],
        "crown_jewel_old": {"net": 79.45, "trades": 40, "strategy": "RSI(4)+25%TP"},
        "stoch_rsi_best": results[0] if results else None,
    }
    
    out = ROOT / "reports" / "stochrsi_optimization_sweep.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(f"\nReport: {out}")
    
    if results[0]["net"] > 124.43:
        print(f"\n🚨🚨🚨 BEAT THE BEAST! New best: ${results[0]['net']:.2f} (+{(results[0]['net']-124.43)/124.43*100:.1f}% over StochRSI champion)")
    else:
        print(f"\n👑 StochRSI(4,3) < 5 + 25% TP still stands at $124.43")
    
    return 0


if __name__ == "__main__":
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    raise SystemExit(main())
