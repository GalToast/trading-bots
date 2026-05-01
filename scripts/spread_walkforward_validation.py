#!/usr/bin/env python3
"""Walk-forward validation of RAVE vs BLUR spread signal.

Split the 13-hour overlapping sample into train/test halves.
If the signal works on the second half (unseen data), it's more likely real.
If it collapses, it was curve-fitting on the first half.

Also test: does the RAVE outperformance persist, or was it a parabolic run?
"""
import json
import numpy as np
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CACHE_DIR = ROOT / "reports" / "candle_cache"

FEE_PCT = 0.024
SPREAD_FEE = FEE_PCT * 2  # 4.8%
WINDOW_BARS = 60

def load_candles(filename):
    path = CACHE_DIR / filename
    if not path.exists():
        return None
    data = json.loads(path.read_text(encoding="utf-8"))
    return data.get("candles") or data.get("data") or []

def parse_candles(raw):
    timestamps, closes = [], []
    for c in raw:
        ts = int(c.get("time", 0))
        cl = float(c.get("close", 0))
        if ts > 0 and cl > 0:
            timestamps.append(ts)
            closes.append(cl)
    return np.array(timestamps), np.array(closes)

def align(ts1, c1, ts2, c2):
    common = sorted(set(ts1) & set(ts2))
    if len(common) < WINDOW_BARS * 2:
        return None, None, None
    idx1 = {t: i for i, t in enumerate(ts1)}
    idx2 = {t: i for i, t in enumerate(ts2)}
    return np.array(common), np.array([c1[idx1[t]] for t in common]), np.array([c2[idx2[t]] for t in common])

def hourly_returns(closes, timestamps):
    rets, times = [], []
    for i in range(0, len(closes) - WINDOW_BARS + 1, WINDOW_BARS):
        ret = (closes[i + WINDOW_BARS - 1] / closes[i]) - 1.0
        rets.append(ret)
        times.append(timestamps[i])
    return np.array(rets), np.array(times)

def main():
    print("=" * 80)
    print("WALK-FORWARD VALIDATION: RAVE vs BLUR Spread")
    print("=" * 80)
    
    rave_raw = load_candles("RAVE_USD_ONE_MINUTE_30d.json")
    blur_raw = load_candles("BLUR_USD_ONE_MINUTE_7d.json")
    
    if not rave_raw or not blur_raw:
        print("ERROR: Missing candle data")
        return
    
    rave_ts, rave_cl = parse_candles(rave_raw)
    blur_ts, blur_cl = parse_candles(blur_raw)
    
    common_ts, rave_aligned, blur_aligned = align(rave_ts, rave_cl, blur_ts, blur_cl)
    if common_ts is None:
        print("ERROR: Insufficient overlapping data")
        return
    
    print(f"Overlapping data: {len(common_ts)} candles")
    print(f"Time range: {common_ts[0]} → {common_ts[-1]}")
    
    # Convert timestamps to human-readable
    from datetime import datetime
    start_dt = datetime.utcfromtimestamp(common_ts[0])
    end_dt = datetime.utcfromtimestamp(common_ts[-1])
    print(f"UTC: {start_dt} → {end_dt} ({(end_dt - start_dt).total_seconds()/3600:.1f} hours)")
    
    rave_ret, times = hourly_returns(rave_aligned, common_ts)
    blur_ret, _ = hourly_returns(blur_aligned, common_ts)
    
    spread = rave_ret - blur_ret  # Long RAVE, Short BLUR
    reverse_spread = blur_ret - rave_ret  # Long BLUR, Short RAVE
    
    n = len(spread)
    half = n // 2
    
    print(f"\nTotal hours: {n}")
    print(f"First half: {half} hours, Second half: {n - half} hours")
    
    # FIRST HALF analysis
    print(f"\n{'='*60}")
    print(f"FIRST HALF (Hours 1-{half})")
    print(f"{'='*60}")
    
    first_spread = spread[:half]
    first_rev_spread = reverse_spread[:half]
    
    # Toy signal: follow last hour's winner
    first_wins = 0
    first_cumulative = 0.0
    first_trades = 0
    
    for i in range(1, half):
        first_trades += 1
        if spread[i-1] > 0:
            # Predict spread continues
            if spread[i] > SPREAD_FEE:
                first_wins += 1
                first_cumulative += spread[i] - SPREAD_FEE
            else:
                first_cumulative += spread[i] - SPREAD_FEE
        else:
            # Predict reverse spread
            if reverse_spread[i] > SPREAD_FEE:
                first_wins += 1
                first_cumulative += reverse_spread[i] - SPREAD_FEE
            else:
                first_cumulative += reverse_spread[i] - SPREAD_FEE
    
    print(f"  RAVE avg hourly return: {np.mean(rave_ret[:half])*100:.2f}%")
    print(f"  BLUR avg hourly return: {np.mean(blur_ret[:half])*100:.2f}%")
    print(f"  Spread mean: {np.mean(first_spread)*100:.2f}%")
    print(f"  Signal: {first_trades} trades, {first_wins} wins ({first_wins/first_trades:.0%})")
    print(f"  Cumulative net: {first_cumulative*100:.2f}%")
    print(f"  Avg per trade: {(first_cumulative/first_trades)*100:.4f}%")
    
    # Print individual hour results for first half
    print(f"\n  Hour-by-hour (first half):")
    print(f"  {'Hr':>3} {'RAVE%':>8} {'BLUR%':>8} {'Spread':>8} {'Signal':>8} {'Result':>8}")
    print(f"  {'--':>3} {'-----':>8} {'-----':>8} {'------':>8} {'------':>8} {'------':>8}")
    
    for i in range(1, half):
        s = spread[i]
        rs = reverse_spread[i]
        if spread[i-1] > 0:
            direction = "L-R/S-B"
            result = s - SPREAD_FEE
        else:
            direction = "L-B/S-R"
            result = rs - SPREAD_FEE
        
        marker = "✓" if result > 0 else "✗"
        print(f"  {i:>3} {rave_ret[i]*100:>8.2f} {blur_ret[i]*100:>8.2f} {s*100:>8.2f} {direction:>8} {result*100:>7.2f}%{marker}")
    
    # SECOND HALF analysis (unseen data)
    print(f"\n{'='*60}")
    print(f"SECOND HALF (Hours {half+1}-{n}) — UNSEEN DATA")
    print(f"{'='*60}")
    
    second_spread = spread[half:]
    second_rev_spread = reverse_spread[half:]
    
    # Use FIRST HALF to learn the signal direction
    # Which direction was more profitable in the first half?
    first_spread_wins = np.sum(first_spread > SPREAD_FEE)
    first_rev_wins = np.sum(first_rev_spread > SPREAD_FEE)
    
    # If spread was better in first half, always go long RAVE + short BLUR
    # This is the simplest learned signal
    if first_spread_wins >= first_rev_wins:
        signal_name = "Always Long RAVE + Short BLUR"
        second_wins = 0
        second_cumulative = 0.0
        second_trades = 0
        for i in range(len(second_spread)):
            second_trades += 1
            if second_spread[i] > SPREAD_FEE:
                second_wins += 1
                second_cumulative += second_spread[i] - SPREAD_FEE
            else:
                second_cumulative += second_spread[i] - SPREAD_FEE
    else:
        signal_name = "Always Long BLUR + Short RAVE"
        second_wins = 0
        second_cumulative = 0.0
        second_trades = 0
        for i in range(len(second_rev_spread)):
            second_trades += 1
            if second_rev_spread[i] > SPREAD_FEE:
                second_wins += 1
                second_cumulative += second_rev_spread[i] - SPREAD_FEE
            else:
                second_cumulative += second_rev_spread[i] - SPREAD_FEE
    
    print(f"  RAVE avg hourly return: {np.mean(rave_ret[half:])*100:.2f}%")
    print(f"  BLUR avg hourly return: {np.mean(blur_ret[half:])*100:.2f}%")
    print(f"  Spread mean: {np.mean(second_spread)*100:.2f}%")
    print(f"  Learned signal: {signal_name}")
    print(f"  Signal: {second_trades} trades, {second_wins} wins ({second_wins/second_trades if second_trades > 0 else 0:.0%})")
    print(f"  Cumulative net: {second_cumulative*100:.2f}%")
    print(f"  Avg per trade: {(second_cumulative/second_trades)*100:.4f}%" if second_trades > 0 else "  Avg per trade: N/A")
    
    # Print individual hour results for second half
    print(f"\n  Hour-by-hour (second half):")
    print(f"  {'Hr':>3} {'RAVE%':>8} {'BLUR%':>8} {'Spread':>8} {'Signal':>8} {'Result':>8}")
    print(f"  {'--':>3} {'-----':>8} {'-----':>8} {'------':>8} {'------':>8} {'------':>8}")
    
    for i in range(half, n):
        s = spread[i]
        rs = reverse_spread[i]
        if first_spread_wins >= first_rev_wins:
            direction = "L-R/S-B"
            result = s - SPREAD_FEE
        else:
            direction = "L-B/S-R"
            result = rs - SPREAD_FEE
        
        marker = "✓" if result > 0 else "✗"
        print(f"  {i+1:>3} {rave_ret[i]*100:>8.2f} {blur_ret[i]*100:>8.2f} {s*100:>8.2f} {direction:>8} {result*100:>7.2f}%{marker}")
    
    # Also test: what if we used the TOY SIGNAL (follow last winner) on second half?
    print(f"\n  TOY SIGNAL (follow last winner) on SECOND HALF:")
    toy_wins = 0
    toy_cumulative = 0.0
    toy_trades = 0
    for i in range(half + 1, n):
        toy_trades += 1
        if spread[i-1] > 0:
            if spread[i] > SPREAD_FEE:
                toy_wins += 1
                toy_cumulative += spread[i] - SPREAD_FEE
            else:
                toy_cumulative += spread[i] - SPREAD_FEE
        else:
            if reverse_spread[i] > SPREAD_FEE:
                toy_wins += 1
                toy_cumulative += reverse_spread[i] - SPREAD_FEE
            else:
                toy_cumulative += reverse_spread[i] - SPREAD_FEE
    
    print(f"  Trades: {toy_trades}, Wins: {toy_wins}")
    print(f"  Cumulative net: {toy_cumulative*100:.2f}%")
    print(f"  Avg per trade: {(toy_cumulative/toy_trades)*100:.4f}%" if toy_trades > 0 else "  Avg per trade: N/A")
    
    # Overall verdict
    print(f"\n{'='*60}")
    print(f"VERDICT")
    print(f"{'='*60}")
    
    first_ok = first_cumulative > 0
    second_ok = second_cumulative > 0
    toy_second_ok = toy_cumulative > 0
    
    print(f"  First half (training):  {'✓ POSITIVE' if first_ok else '✗ NEGATIVE'} ({first_cumulative*100:.2f}%)")
    print(f"  Second half (test):     {'✓ POSITIVE' if second_ok else '✗ NEGATIVE'} ({second_cumulative*100:.2f}%)")
    print(f"  Toy signal on test:     {'✓ POSITIVE' if toy_second_ok else '✗ NEGATIVE'} ({toy_cumulative*100:.2f}%)")
    
    if first_ok and second_ok:
        print(f"\n  🟢 LEGITIMATE: Signal works on both halves. The spread persistence is real.")
    elif first_ok and not second_ok:
        print(f"\n  🔴 CURVE-FITTED: Signal works on training data but fails on unseen data.")
        print(f"     The first half had RAVE outperforming BLUR. The second half didn't.")
    elif not first_ok and second_ok:
        print(f"\n  🟡 FLIPPED: Signal fails on training but works on test. Unlikely but possible.")
    else:
        print(f"\n  🔴 NOISE: Signal fails on both halves. The original +106% was a lucky streak.")
    
    # Check if RAVE's outperformance is consistent
    rave_first = np.mean(rave_ret[:half])
    rave_second = np.mean(rave_ret[half:])
    print(f"\n  RAVE consistency: First half {rave_first*100:.2f}%/hr, Second half {rave_second*100:.2f}%/hr")
    if rave_first > 0 and rave_second > 0:
        print(f"  → RAVE outperformance is CONSISTENT across both halves.")
    elif rave_first > 0 and rave_second < 0:
        print(f"  → RAVE outperformance REVERSED in second half (parabolic run ended).")
    elif rave_first < 0 and rave_second > 0:
        print(f"  → RAVE underperformance flipped to outperformance (recovery).")
    else:
        print(f"  → RAVE underperformed in both halves (unexpected given overall stats).")
    
    print(f"\n{'='*60}")


if __name__ == "__main__":
    main()
