#!/usr/bin/env python3
"""
Combined Adversarial Audit — Phase 1: RSI MR + Regime Filter on RAVE

Tests realistic execution assumptions:
- Fill probability: 100%, 75%, 50%, 25%
- Execution delay: 0, 1, 3, 5 bars
- Regime gate: None, ATR>1.5%, ATR>2.0%, ATR>3.0%

Output: Does regime filter reduce DD without killing profits?
"""
import json, os, sys, time, statistics, itertools
from datetime import datetime, timezone
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from coinbase_advanced_client import CoinbaseAdvancedClient

PRODUCT = "RAVE-USD"
BTC = "BTC-USD"

def fetch_candles(client, pid, start, end, granularity="FIVE_MINUTE"):
    chunk_sec = 300 * 5 * 60
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
            time.sleep(0.15)
        except:
            cs = ce
            time.sleep(0.3)
    all_c.sort(key=lambda c: int(c["start"]))
    return all_c

def compute_rsi(closes, period=3):
    if len(closes) < period + 1: return 50.0
    deltas = [closes[i] - closes[i-1] for i in range(1, len(closes))]
    gains = [d if d > 0 else 0 for d in deltas[-period:]]
    losses = [-d if d < 0 else 0 for d in deltas[-period:]]
    avg_g = sum(gains) / period
    avg_l = sum(losses) / period
    if avg_l > 0:
        rs = avg_g / avg_l
        return 100 - 100 / (1 + rs)
    return 100.0

def compute_atr_pct(candles, period=14):
    if len(candles) < period + 1: return 0.0
    atrs = []
    for i in range(1, len(candles)):
        hi = float(candles[i]["high"])
        lo = float(candles[i]["low"])
        prev_close = float(candles[i-1]["close"])
        tr = max(hi - lo, abs(hi - prev_close), abs(lo - prev_close))
        atrs.append(tr)
    if len(atrs) < period: return 0.0
    atr = statistics.mean(atrs[-period:])
    avg_price = statistics.mean(float(c["close"]) for c in candles[-period:])
    return atr / avg_price * 100 if avg_price > 0 else 0.0

def get_fee(vol):
    if vol >= 50000: return 0.0015
    elif vol >= 10000: return 0.0025
    else: return 0.0040

def run_rsi_mr_adversarial(candles, btc_lk, rsi_period=3, os_thresh=30, tp_pct=25,
                            cash_start=48.0, fill_prob=1.0, delay_bars=0, 
                            atr_gate=0.0, slippage_bps=0.0):
    """
    RSI MR with realistic execution assumptions.
    
    fill_prob: Probability of getting filled at signal price (0.0-1.0)
    delay_bars: Number of bars between signal and execution
    atr_gate: Minimum ATR% to enter (0.0 = no gate)
    slippage_bps: Slippage on entry in basis points
    """
    import random
    random.seed(42)  # Reproducible
    
    cash = cash_start
    pos = None
    closes_count = 0
    wins = 0
    vol = 0.0
    h = []
    pk = cash_start
    mdd = 0.0
    signals = 0
    filled = 0
    regime_filtered = 0
    entry_buffer = []  # For delayed execution
    
    for i in range(len(candles)):
        c = candles[i]
        ts = int(c["start"])
        close = float(c["close"])
        hi = float(c["high"])
        lo = float(c["low"])
        
        h.append(close)
        if len(h) > 500: h.pop(0)
        
        hr = datetime.fromtimestamp(ts, tz=timezone.utc).hour
        if hr in {0, 6, 12, 19}: continue
        
        boc = True
        pt, pt3 = ts - 60, ts - 180
        if pt in btc_lk and pt3 in btc_lk:
            mom = (btc_lk[pt] - btc_lk[pt3]) / btc_lk[pt3]
            if mom < -0.001: boc = False
        
        fr = get_fee(vol)
        
        # Process delayed entries
        if delay_bars > 0:
            new_buffer = []
            for entry_signal in entry_buffer:
                entry_signal["bars_left"] -= 1
                if entry_signal["bars_left"] <= 0:
                    # Execute delayed entry
                    if random.random() < fill_prob:
                        fill_price = float(c["open"]) * (1 + slippage_bps / 10000.0)
                        tq = entry_signal["cash_at_signal"]
                        if tq >= 10 and pos is None:  # Can only enter if no position
                            units = tq / fill_price
                            cash -= tq
                            pos = {"ep": fill_price, "q": tq, "h": 0, "tp_pct": tp_pct, "units": units}
                            filled += 1
                    # else: not filled, skip
                else:
                    new_buffer.append(entry_signal)
            entry_buffer = new_buffer
        
        # Exit
        if pos:
            pos["h"] += 1
            tp = pos["ep"] * (1 + pos["tp_pct"]/100)
            if hi >= tp:
                units = pos.get("units", pos["q"] / pos["ep"])
                pnl = (tp - pos["ep"]) * units - (pos["q"] * fr) - (tp * units * fr)
                cash += pos["q"] + pnl
                vol += pos["q"] + tp * units
                closes_count += 1
                wins += 1
                pos = None
                if cash > pk: pk = cash
                dd = (pk - cash) / pk if pk > 0 else 0
                if dd > mdd: mdd = dd
        
        # Check regime gate
        regime_ok = True
        if atr_gate > 0:
            atr = compute_atr_pct(candles[:i+1], 14)
            if atr < atr_gate:
                regime_ok = False
                regime_filtered += 1
        
        # Entry signal
        if pos is None and cash >= 10 and boc and regime_ok and len(h) >= rsi_period + 2:
            rv = compute_rsi(h[:-1], rsi_period)
            if rv < os_thresh:
                signals += 1
                if delay_bars > 0:
                    entry_buffer.append({
                        "cash_at_signal": cash,
                        "bars_left": delay_bars,
                    })
                else:
                    if random.random() < fill_prob:
                        ep = float(c["open"]) * (1 + slippage_bps / 10000.0)
                        tq = cash
                        if tq >= 10:
                            units = tq / ep
                            pos = {"ep": ep, "q": tq, "h": 0, "tp_pct": tp_pct, "units": units}
                            cash -= tq
                            filled += 1
    
    # Close remaining
    if pos:
        close = float(candles[-1]["close"])
        units = pos.get("units", pos["q"] / pos["ep"])
        pnl = (close - pos["ep"]) * units - (pos["q"] * fr) - (close * units * fr)
        cash += pos["q"] + pnl
        vol += pos["q"] + close * units
        closes_count += 1
        if close > pos["ep"]: wins += 1
    
    # Process remaining buffer
    for entry_signal in entry_buffer:
        if random.random() < fill_prob:
            pass  # Would have entered but no candles left
    
    net = cash - cash_start
    wr = wins / max(1, closes_count) * 100
    
    return {
        "net": round(net, 2), "return_pct": round(net / cash_start * 100, 1),
        "closes": closes_count, "wr": round(wr, 1),
        "signals": signals, "filled": filled, "fill_rate": round(filled / max(1, signals) * 100, 1),
        "regime_filtered": regime_filtered,
        "max_dd": round(mdd * 100, 1),
        "final_cash": round(cash, 2),
    }

def main():
    client = CoinbaseAdvancedClient()
    now = int(time.time())
    start_30d = now - 30 * 24 * 3600
    
    print(f"Fetching 30-day RAVE data...")
    candles = fetch_candles(client, PRODUCT, start_30d, now)
    btc = fetch_candles(client, BTC, start_30d, now)
    btc_lk = {int(c["start"]): float(c["close"]) for c in btc}
    print(f"  RAVE: {len(candles)} candles, BTC: {len(btc)} candles")
    
    # Parameter grid
    fill_probs = [1.0, 0.75, 0.5, 0.25]
    delays = [0, 1, 3, 5]
    atr_gates = [0.0, 1.5, 2.0, 3.0]
    
    print(f"\n{'=' * 120}")
    print(f"PHASE 1 ADVERSARIAL AUDIT — RSI MR on RAVE (30 days)")
    print(f"Testing {len(fill_probs)} fill_probs × {len(delays)} delays × {len(atr_gates)} atr_gates = {len(fill_probs)*len(delays)*len(atr_gates)} configs")
    print(f"{'=' * 120}")
    
    results = []
    
    for fp, delay, atr in itertools.product(fill_probs, delays, atr_gates):
        r = run_rsi_mr_adversarial(candles, btc_lk, fill_prob=fp, delay_bars=delay, atr_gate=atr)
        r["fill_prob"] = fp
        r["delay_bars"] = delay
        r["atr_gate"] = atr
        results.append(r)
    
    # Print summary by fill probability
    print(f"\n{'=' * 120}")
    print(f"BY FILL PROBABILITY (all delays, all atr gates)")
    print(f"{'=' * 120}")
    
    for fp in fill_probs:
        fp_results = [r for r in results if r["fill_prob"] == fp]
        avg_net = statistics.mean([r["net"] for r in fp_results])
        avg_wr = statistics.mean([r["wr"] for r in fp_results])
        avg_dd = statistics.mean([r["max_dd"] for r in fp_results])
        avg_signals = statistics.mean([r["signals"] for r in fp_results])
        avg_filled = statistics.mean([r["filled"] for r in fp_results])
        print(f"  Fill={fp*100:.0f}%: Avg Net=${avg_net:>7.2f} WR={avg_wr:>5.1f}% DD={avg_dd:>5.1f}% "
              f"Signals={avg_signals:.0f} Filled={avg_filled:.0f}")
    
    # Print summary by delay
    print(f"\n{'=' * 120}")
    print(f"BY EXECUTION DELAY (all fill probs, all atr gates)")
    print(f"{'=' * 120}")
    
    for delay in delays:
        delay_results = [r for r in results if r["delay_bars"] == delay]
        avg_net = statistics.mean([r["net"] for r in delay_results])
        avg_wr = statistics.mean([r["wr"] for r in delay_results])
        avg_dd = statistics.mean([r["max_dd"] for r in delay_results])
        print(f"  Delay={delay} bars: Avg Net=${avg_net:>7.2f} WR={avg_wr:>5.1f}% DD={avg_dd:>5.1f}%")
    
    # Print summary by ATR gate
    print(f"\n{'=' * 120}")
    print(f"BY ATR GATE (all fill probs, all delays)")
    print(f"{'=' * 120}")
    
    for atr in atr_gates:
        atr_results = [r for r in results if r["atr_gate"] == atr]
        avg_net = statistics.mean([r["net"] for r in atr_results])
        avg_wr = statistics.mean([r["wr"] for r in atr_results])
        avg_dd = statistics.mean([r["max_dd"] for r in atr_results])
        avg_filtered = statistics.mean([r["regime_filtered"] for r in atr_results])
        print(f"  ATR>{atr}%: Avg Net=${avg_net:>7.2f} WR={avg_wr:>5.1f}% DD={avg_dd:>5.1f}% "
              f"RegimeFiltered={avg_filtered:.0f}")
    
    # Top 10 configs
    results.sort(key=lambda x: x["net"], reverse=True)
    print(f"\n{'=' * 120}")
    print(f"TOP 10 CONFIGS")
    print(f"{'=' * 120}")
    print(f"{'Fill%':>6} {'Delay':>6} {'ATR%':>6} {'Net $':>8} {'Ret%':>7} {'Trades':>7} {'WR%':>6} {'DD%':>6} {'Signals':>8} {'Filled':>7}")
    print("-" * 120)
    for r in results[:10]:
        print(f"{r['fill_prob']*100:>5.0f}% {r['delay_bars']:>6} {r['atr_gate']:>5.1f}% ${r['net']:>7.2f} {r['return_pct']:>6.1f}% {r['closes']:>7} {r['wr']:>5.1f}% {r['max_dd']:>5.1f}% {r['signals']:>8} {r['filled']:>7}")
    
    # Worst 5 configs
    print(f"\n{'=' * 120}")
    print(f"WORST 5 CONFIGS")
    print(f"{'=' * 120}")
    for r in results[-5:]:
        print(f"{r['fill_prob']*100:>5.0f}% {r['delay_bars']:>6} {r['atr_gate']:>5.1f}% ${r['net']:>7.2f} {r['return_pct']:>6.1f}% {r['closes']:>7} {r['wr']:>5.1f}% {r['max_dd']:>5.1f}% {r['signals']:>8} {r['filled']:>7}")
    
    # Key question: does regime filter help?
    print(f"\n{'=' * 120}")
    print(f"KEY QUESTION: Does ATR Gate reduce DD without killing profits?")
    print(f"{'=' * 120}")
    
    for fp in [1.0, 0.5]:
        for delay in [0, 3]:
            no_gate = next(r for r in results if r["fill_prob"]==fp and r["delay_bars"]==delay and r["atr_gate"]==0.0)
            with_gate = next(r for r in results if r["fill_prob"]==fp and r["delay_bars"]==delay and r["atr_gate"]==2.0)
            print(f"  Fill={fp*100:.0f}% Delay={delay}: No Gate Net=${no_gate['net']:>7.2f} DD={no_gate['max_dd']:.1f}%  |  "
                  f"ATR>2% Net=${with_gate['net']:>7.2f} DD={with_gate['max_dd']:.1f}%  |  "
                  f"Δ Net=${with_gate['net']-no_gate['net']:>+.2f} ΔDD={with_gate['max_dd']-no_gate['max_dd']:+.1f}%")
    
    # Save
    output = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "total_configs": len(results),
        "top10": results[:10],
        "worst5": results[-5:],
        "all_results": results,
    }
    out_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "reports", "combined_adversarial_audit_phase1.json")
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "w") as f:
        json.dump(output, f, indent=2, default=str)
    print(f"\nResults saved to {out_path}")

if __name__ == "__main__":
    main()
