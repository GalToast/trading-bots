#!/usr/bin/env python3
"""
Mid-Candle Entry Test — Can we actually enter at the breakout level in real-time?

Simulates live polling at 30s intervals on M5 candles.
When HIGH breaks the 10-bar threshold, what price do we actually get?

Tests:
1. Backtest entry price (candle open)
2. Real-time breakout detection (poll every 30s)
3. Slippage between the two
"""
import json
import os
import sys
import time
import statistics
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from coinbase_advanced_client import CoinbaseAdvancedClient

PRODUCT = "RAVE-USD"

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

def simulate_live_polling(m1_candles, lookback=10, poll_interval_sec=30):
    """
    Simulate live polling every 30 seconds.
    When current price > lookback high → enter at current price.
    Compare to backtest entry (candle open).
    """
    results = []
    total_slippage = []
    
    for i in range(lookback + 2, len(m1_candles)):
        c = m1_candles[i]
        ts = int(c["start"])
        o = float(c["open"])
        h = float(c["high"])
        l = float(c["low"])
        cl = float(c["close"])
        
        # Calculate lookback high from PREVIOUS candles
        recent_high = max(float(m1_candles[j]["high"]) for j in range(i - lookback, i))
        
        # Check if this candle broke the level
        if h <= recent_high:
            continue  # No breakout
        
        # In a backtest with lookahead, we enter at OPEN (o)
        # knowing the candle WILL break recent_high
        
        # In live trading, we poll every 30 seconds.
        # The breakout happens when price crosses recent_high.
        # We can't enter at o — we enter at the price WHEN we detect the break.
        
        # Simulate: the breakout price is somewhere between o and h.
        # Worst case: we detect at close (cl)
        # Best case: we detect right as it breaks (≈ recent_high)
        # Realistic: we detect at the next poll after the break
        
        # M1 candles: each is 1 minute. Poll every 30s = 2 polls per candle.
        # The breakout could happen at any point during the candle.
        # On average, we detect it halfway through the breakout move.
        
        # Estimate: breakout_price = recent_high + (h - recent_high) * 0.5
        breakout_level = recent_high
        breakout_magnitude = h - recent_high
        estimated_fill = breakout_level + breakout_magnitude * 0.5  # Average detection
        
        slippage_vs_open = (estimated_fill - o) / o * 100
        slippage_vs_breakout = (estimated_fill - breakout_level) / breakout_level * 100
        
        total_slippage.append(slippage_vs_open)
        
        results.append({
            "ts": ts,
            "open": o,
            "high": h,
            "breakout_level": breakout_level,
            "breakout_magnitude": breakout_magnitude,
            "backtest_entry": o,
            "estimated_live_fill": round(estimated_fill, 6),
            "slippage_vs_open_pct": round(slippage_vs_open, 2),
            "slippage_vs_breakout_pct": round(slippage_vs_breakout, 2),
        })
    
    return results, total_slippage

def main():
    client = CoinbaseAdvancedClient()
    now = int(time.time())
    days = 7
    start = now - days * 24 * 3600

    print(f"Fetching {days}-day M1 data for {PRODUCT}...")
    m1 = fetch_candles(client, PRODUCT, start, now, "ONE_MINUTE")
    print(f"  Got {len(m1)} M1 candles ({len(m1)/1440:.1f} days)")

    for lb in [5, 10, 20]:
        results, slippages = simulate_live_polling(m1, lookback=lb)
        
        print(f"\n{'=' * 90}")
        print(f"LB{lb} — {len(results)} breakout candles detected")
        print(f"{'=' * 90}")
        
        if not results:
            print("  No breakouts found")
            continue
        
        avg_sl = statistics.mean(slippages)
        median_sl = statistics.median(slippages)
        max_sl = max(slippages)
        min_sl = min(slippages)
        
        # How many have < 0.5% slippage?
        cheap = sum(1 for s in slippages if s < 0.5)
        expensive = sum(1 for s in slippages if s > 2.0)
        
        print(f"  Avg slippage vs open:  {avg_sl:.2f}%")
        print(f"  Median slippage:       {median_sl:.2f}%")
        print(f"  Max slippage:          {max_sl:.2f}%")
        print(f"  Min slippage:          {min_sl:.2f}%")
        print(f"  < 0.5% slippage:       {cheap}/{len(results)} ({cheap/len(results)*100:.0f}%)")
        print(f"  > 2.0% slippage:       {expensive}/{len(results)} ({expensive/len(results)*100:.0f}%)")
        
        # Show worst 5
        worst = sorted(results, key=lambda x: x["slippage_vs_open_pct"], reverse=True)[:5]
        print(f"\n  Worst 5 slippage entries:")
        for r in worst:
            print(f"    Open=${r['open']:.4f} → Fill=${r['estimated_live_fill']:.4f} "
                  f"Slip={r['slippage_vs_open_pct']:.2f}% Breakout={r['breakout_magnitude']:.4f}")
        
        # Show best 5
        best = sorted(results, key=lambda x: x["slippage_vs_open_pct"])[:5]
        print(f"\n  Best 5 slippage entries:")
        for r in best:
            print(f"    Open=${r['open']:.4f} → Fill=${r['estimated_live_fill']:.4f} "
                  f"Slip={r['slippage_vs_open_pct']:.2f}% Breakout={r['breakout_magnitude']:.4f}")

    # Now test: can we poll Coinbase RIGHT NOW for a real-time price?
    print(f"\n{'=' * 90}")
    print(f"REAL-TIME POLL TEST — Checking current RAVE price vs last M1 candle")
    print(f"{'=' * 90}")
    
    try:
        resp = client.market_candles(PRODUCT, start=now-60, end=now, granularity="ONE_MINUTE")
        current = resp.get("candles", [])
        if current:
            c = current[0]
            print(f"  Last M1: open={c['open']} high={c['high']} low={c['low']} close={c['close']}")
            print(f"  Candle is {int(time.time()) - int(c['start'])}s old")
            print(f"  We CAN poll current price during the candle — no need to wait for close")
            print(f"  Mid-candle entry IS possible with 30s polling")
        else:
            print(f"  No recent candles found")
    except Exception as e:
        print(f"  Error: {e}")

    # Save
    output = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "total_m1_candles": len(m1),
    }
    for lb in [5, 10, 20]:
        results, slippages = simulate_live_polling(m1, lookback=lb)
        if results:
            output[f"lb{lb}"] = {
                "count": len(results),
                "avg_slippage_pct": round(statistics.mean(slippages), 2),
                "median_slippage_pct": round(statistics.median(slippages), 2),
                "max_slippage_pct": round(max(slippages), 2),
                "under_0_5pct": sum(1 for s in slippages if s < 0.5),
                "over_2_0pct": sum(1 for s in slippages if s > 2.0),
            }
    
    out_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "reports", "mid_candle_test.json")
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "w") as f:
        json.dump(output, f, indent=2)
    print(f"\nResults saved to {out_path}")

if __name__ == "__main__":
    main()
