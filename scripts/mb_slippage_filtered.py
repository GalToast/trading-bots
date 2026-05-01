#!/usr/bin/env python3
"""
Momentum Breakout — Slippage-Filtered Backtest.
Adds mid-candle entry simulation with breakout magnitude filter.

Filters:
1. Skip breakouts where magnitude > max_magnitude_pct (eliminates wick-traps)
2. Enter at estimated_live_fill (recent_high + 50% of breakout magnitude)
3. No lookahead — signal at breakout, fill at realistic mid-candle price
"""
import json, os, sys, time, statistics
from datetime import datetime, timezone
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from coinbase_advanced_client import CoinbaseAdvancedClient

BTC = "BTC-USD"

def fetch_candles(client, pid, start, end, granularity="ONE_MINUTE"):
    chunk_sec = 300 * 60
    all_c = []
    cs = start
    while cs < end:
        ce = min(cs + chunk_sec, end)
        try:
            resp = client.market_candles(pid, start=cs, end=ce, granularity=granularity)
            cands = resp.get("candles", [])
            all_c.extend(cands)
            cs = ce
            if not cands: break
            time.sleep(0.1)
        except:
            cs = ce
            time.sleep(0.5)
    all_c.sort(key=lambda c: int(c["start"]))
    return all_c

def get_fee(vol):
    if vol >= 50000: return 0.0015
    elif vol >= 10000: return 0.0025
    else: return 0.0040

def run_mb_slippage_filtered(m1_candles, btc_lk, lookback=10, tp_pct=10, sl_pct=7, 
                              max_hold=50, max_magnitude_pct=2.0, cash_start=48.0):
    """
    Realistic Momentum Breakout with mid-candle entry simulation.
    - Detects breakout when HIGH > lookback high
    - Simulates fill at recent_high + 50% of breakout magnitude (mid-breakout)
    - Skips breakouts where magnitude > max_magnitude_pct (wick-trap filter)
    - Uses M1 candles for realistic entry timing
    """
    cash = cash_start
    pos = None
    closes_count = 0
    wins = 0
    vol = 0.0
    pk = cash_start
    mdd = 0.0
    gross_profit = 0.0
    gross_loss = 0.0
    skipped_wick_traps = 0
    
    # M1 candle histories
    highs = []
    closes = []
    
    for i in range(len(m1_candles)):
        c = m1_candles[i]
        ts = int(c["start"])
        hi = float(c["high"])
        lo = float(c["low"])
        close = float(c["close"])
        
        highs.append(hi)
        closes.append(close)
        if len(highs) > 200:
            highs.pop(0)
            closes.pop(0)
        
        # BTC gate (check at 1-min resolution)
        boc = True
        pt, pt3 = ts - 60, ts - 180
        if pt in btc_lk and pt3 in btc_lk:
            mom = (btc_lk[pt] - btc_lk[pt3]) / btc_lk[pt3]
            if mom < -0.001:
                boc = False
        
        # Session gate
        hr = datetime.fromtimestamp(ts, tz=timezone.utc).hour
        if hr in {0, 6, 12, 19}:
            continue
        
        fr = get_fee(vol)
        
        # Exit (check every M1 candle)
        if pos:
            pos["h"] += 1
            exit_p = None
            exit_reason = None
            
            if hi >= pos["tp"]:
                exit_p = pos["tp"]; exit_reason = "tp"
            elif lo <= pos["sl"]:
                exit_p = pos["sl"]; exit_reason = "sl"
            elif pos["h"] >= pos["max_hold"]:
                exit_p = close; exit_reason = "timeout"
            
            if exit_p is not None:
                u = pos["units"]
                pnl = (exit_p - pos["ep"]) * u - pos["entry_fee"] - (exit_p * u * fr)
                if pnl > 0: gross_profit += pnl
                else: gross_loss += abs(pnl)
                cash += exit_p * u - exit_p * u * fr
                vol += pos["deploy"] + exit_p * u
                closes_count += 1
                if exit_p > pos["ep"]: wins += 1
                if cash > pk: pk = cash
                dd = (pk - cash) / pk if pk > 0 else 0
                if dd > mdd: mdd = dd
                pos = None
        
        # Entry: detect breakout
        if pos is None and cash >= 10 and boc and len(highs) >= lookback + 2:
            recent_high = max(highs[-lookback-1:-1])
            
            # Breakout detected: current HIGH > recent_high
            if hi > recent_high:
                breakout_level = recent_high
                breakout_magnitude = (hi - breakout_level) / breakout_level * 100
                
                # Filter: skip huge breakouts (wick-trap filter)
                if breakout_magnitude > max_magnitude_pct:
                    skipped_wick_traps += 1
                    continue
                
                # Estimate live fill: recent_high + 50% of breakout magnitude
                estimated_fill = breakout_level + (hi - breakout_level) * 0.5
                
                # Check if we still have room to TP
                tp = estimated_fill * (1 + tp_pct / 100.0)
                sl = estimated_fill * (1 - sl_pct / 100.0)
                
                deploy = cash
                if deploy >= 10:
                    entry_fee = deploy * fr
                    units = (deploy - entry_fee) / estimated_fill
                    if units > 0:
                        cash -= deploy
                        pos = {
                            "ep": estimated_fill, "deploy": deploy, "units": units,
                            "tp": tp, "sl": sl, "h": 0, "max_hold": max_hold,
                            "entry_fee": entry_fee,
                        }
    
    # Close remaining
    if pos:
        close = float(m1_candles[-1]["close"])
        u = pos["units"]
        pnl = (close - pos["ep"]) * u - pos["entry_fee"] - (close * u * fr)
        if pnl > 0: gross_profit += pnl
        else: gross_loss += abs(pnl)
        cash += close * u - close * u * fr
        vol += pos["deploy"] + close * u
        closes_count += 1
        if close > pos["ep"]: wins += 1
    
    net = cash - cash_start
    wr = wins / max(1, closes_count) * 100
    pf = gross_profit / max(0.01, gross_loss) if gross_loss > 0 else 999.0
    
    return {
        "net": round(net, 2), "return_pct": round(net / cash_start * 100, 1),
        "closes": closes_count, "wr": round(wr, 1),
        "avg_trade": round(net / max(1, closes_count), 2),
        "max_dd": round(mdd * 100, 1),
        "profit_factor": round(pf, 2),
        "skipped_wick_traps": skipped_wick_traps,
    }

def main():
    client = CoinbaseAdvancedClient()
    now = int(time.time())
    days = 7
    start = now - days * 24 * 3600
    
    print(f"Fetching {days}-day M1 + BTC data...")
    m1 = fetch_candles(client, "RAVE-USD", start, now, "ONE_MINUTE")
    btc = fetch_candles(client, BTC, start, now, "ONE_MINUTE")
    btc_lk = {int(c["start"]): float(c["close"]) for c in btc}
    print(f"  RAVE M1: {len(m1)}, BTC M1: {len(btc)}")
    
    # Sweep: max_magnitude_pct filter
    print(f"\n{'=' * 95}")
    print(f"SLIPPAGE-FILTERED MOMENTUM BREAKOUT — {days} days, M1 candles")
    print(f"{'=' * 95}")
    
    results = []
    
    # Test different magnitude filters
    for max_mag in [0.5, 1.0, 1.5, 2.0, 3.0, 5.0, 999]:
        r = run_mb_slippage_filtered(m1, btc_lk, lookback=10, tp_pct=10, sl_pct=7, 
                                      max_hold=50, max_magnitude_pct=max_mag)
        r["config"] = f"LB10 TP10 SL7 H50 MaxMag{max_mag}%"
        results.append(r)
    
    # Test different LB + max_mag combos
    for lb in [5, 10, 20]:
        for mag in [0.5, 1.0, 2.0]:
            for tp in [5, 10, 15]:
                for sl in [3, 5, 7]:
                    r = run_mb_slippage_filtered(m1, btc_lk, lookback=lb, tp_pct=tp, sl_pct=sl, 
                                                  max_hold=50, max_magnitude_pct=mag)
                    r["config"] = f"LB{lb} TP{tp} SL{sl} H50 MaxMag{mag}%"
                    results.append(r)
    
    results.sort(key=lambda x: x["net"], reverse=True)
    
    print(f"{'Config':<45} {'Net $':>8} {'Ret%':>7} {'Trades':>7} {'WR%':>6} {'DD%':>6} {'Skipped':>9} {'PF':>6}")
    print("-" * 95)
    for r in results[:20]:
        print(f"{r['config']:<45} ${r['net']:>7.2f} {r['return_pct']:>6.1f}% {r['closes']:>7} {r['wr']:>5.1f}% {r['max_dd']:>5.1f}% {r['skipped_wick_traps']:>9} {r['profit_factor']:>5.1f}")
    
    # Also compare: no filter vs filtered
    no_filter = next(r for r in results if "MaxMag999" in r["config"])
    best = results[0]
    
    print(f"\n{'=' * 95}")
    print(f"COMPARISON")
    print(f"{'=' * 95}")
    print(f"  No filter (all breakouts): ${no_filter['net']:.2f}, {no_filter['closes']}t, {no_filter['wr']}%WR, DD={no_filter['max_dd']}%")
    print(f"  Best filtered:             ${best['net']:.2f}, {best['closes']}t, {best['wr']}%WR, DD={best['max_dd']}%, skipped={best['skipped_wick_traps']}")
    improvement = best["net"] - no_filter["net"]
    print(f"  Improvement: ${improvement:+.2f}")
    
    # Save
    output = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "top20": results[:20],
        "no_filter": no_filter,
        "best_filtered": best,
    }
    out_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "reports", "mb_slippage_filtered.json")
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "w") as f:
        json.dump(output, f, indent=2)
    print(f"\nResults saved to {out_path}")

if __name__ == "__main__":
    main()
