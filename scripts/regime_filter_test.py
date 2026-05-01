#!/usr/bin/env python3
"""
Regime Filter Test — @main's ask: Apply ATR% filter to 3-window robustness test.

Tests: Does ATR% > 3% filter out losing windows and keep profitable ones?
If YES → regime-filtered edge is real
If NO → there is no edge
"""
import json, os, sys, time, statistics
from datetime import datetime, timezone
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from coinbase_advanced_client import CoinbaseAdvancedClient

TEST_COINS = ["RAVE-USD", "BAL-USD", "BLUR-USD", "IOTX-USD", "FARTCOIN-USD", "ALEPH-USD"]
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
    """Compute ATR% over the entire candle set."""
    if len(candles) < period + 1:
        return 0.0
    atrs = []
    for i in range(1, len(candles)):
        c = candles[i]
        cp = candles[i-1]
        hi = float(c["high"])
        lo = float(c["low"])
        prev_close = float(cp["close"])
        tr = max(hi - lo, abs(hi - prev_close), abs(lo - prev_close))
        atrs.append(tr)
    if len(atrs) < period:
        return 0.0
    avg_atr = statistics.mean(atrs[-period:])
    avg_price = statistics.mean(float(c["close"]) for c in candles[-period:])
    return avg_atr / avg_price * 100 if avg_price > 0 else 0.0

def get_fee(vol):
    if vol >= 50000: return 0.0015
    elif vol >= 10000: return 0.0025
    else: return 0.0040

def run_rsi_mr_regime_filtered(candles, btc_lk, atr_threshold=3.0, rsi_period=3, os_thresh=30, tp_pct=25, cash_start=48.0):
    """RSI MR with regime filter: only trade when rolling ATR% > threshold."""
    cash = cash_start
    pos = None
    closes_count = 0
    wins = 0
    vol = 0.0
    h = []
    cd = []
    pk = cash_start
    mdd = 0.0
    regime_filtered_out = 0
    regime_active_trades = 0
    
    for c in candles:
        ts = int(c["start"])
        close = float(c["close"])
        hi = float(c["high"])
        lo = float(c["low"])
        h.append(close)
        cd.append(c)
        if len(h) > 500:
            h.pop(0)
            cd.pop(0)
        
        hr = datetime.fromtimestamp(ts, tz=timezone.utc).hour
        if hr in {0, 6, 12, 19}: continue
        
        boc = True
        pt, pt3 = ts - 60, ts - 180
        if pt in btc_lk and pt3 in btc_lk:
            mom = (btc_lk[pt] - btc_lk[pt3]) / btc_lk[pt3]
            if mom < -0.001: boc = False
        
        fr = get_fee(vol)
        
        # Exit
        if pos:
            pos["h"] += 1
            tp = pos["ep"] * (1 + pos["tp_pct"]/100)
            if hi >= tp:
                u = pos["q"] / pos["ep"]
                pnl = (tp - pos["ep"]) * u - (pos["q"] * fr) - (tp * u * fr)
                cash += pos["q"] + pnl
                vol += pos["q"] + tp * u
                closes_count += 1
                wins += 1
                pos = None
                if cash > pk: pk = cash
                dd = (pk - cash) / pk if pk > 0 else 0
                if dd > mdd: mdd = dd
        
        # Regime check
        atr_pct = compute_atr_pct(cd, 14)
        regime_ok = atr_pct >= atr_threshold
        
        # Entry
        if pos is None and cash >= 10 and boc and regime_ok and len(h) >= rsi_period + 2:
            rv = compute_rsi(h[:-1], rsi_period)
            if rv < os_thresh:
                ep = float(c["open"])
                tq = cash
                if tq >= 10:
                    pos = {"ep": ep, "q": tq, "h": 0, "tp_pct": tp_pct}
                    cash -= tq
                    regime_active_trades += 1
        elif pos is None and not regime_ok and len(h) >= rsi_period + 2:
            rv = compute_rsi(h[:-1], rsi_period)
            if rv < os_thresh:
                regime_filtered_out += 1
    
    if pos:
        close = float(candles[-1]["close"])
        u = pos["q"] / pos["ep"]
        pnl = (close - pos["ep"]) * u - (pos["q"] * fr) - (close * u * fr)
        cash += pos["q"] + pnl
        vol += pos["q"] + close * u
        closes_count += 1
        if close > pos["ep"]: wins += 1
    
    net = cash - cash_start
    wr = wins / max(1, closes_count) * 100
    
    return {
        "net": round(net, 2), "return_pct": round(net / cash_start * 100, 1),
        "closes": closes_count, "wr": round(wr, 1),
        "regime_filtered_out": regime_filtered_out,
        "regime_active_trades": regime_active_trades,
        "max_dd": round(mdd * 100, 1),
    }

def run_rsi_mr_baseline(candles, btc_lk, rsi_period=3, os_thresh=30, tp_pct=25, cash_start=48.0):
    """RSI MR without regime filter (for comparison)."""
    cash = cash_start
    pos = None
    closes_count = 0
    wins = 0
    vol = 0.0
    h = []
    pk = cash_start
    mdd = 0.0
    
    for c in candles:
        ts = int(c["start"])
        close = float(c["close"])
        hi = float(c["high"])
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
        
        if pos:
            pos["h"] += 1
            tp = pos["ep"] * (1 + pos["tp_pct"]/100)
            if hi >= tp:
                u = pos["q"] / pos["ep"]
                pnl = (tp - pos["ep"]) * u - (pos["q"] * fr) - (tp * u * fr)
                cash += pos["q"] + pnl
                vol += pos["q"] + tp * u
                closes_count += 1
                wins += 1
                pos = None
                if cash > pk: pk = cash
                dd = (pk - cash) / pk if pk > 0 else 0
                if dd > mdd: mdd = dd
        
        if pos is None and cash >= 10 and boc and len(h) >= rsi_period + 2:
            rv = compute_rsi(h[:-1], rsi_period)
            if rv < os_thresh:
                ep = float(c["open"])
                tq = cash
                if tq >= 10:
                    pos = {"ep": ep, "q": tq, "h": 0, "tp_pct": tp_pct}
                    cash -= tq
    
    if pos:
        close = float(candles[-1]["close"])
        u = pos["q"] / pos["ep"]
        pnl = (close - pos["ep"]) * u - (pos["q"] * fr) - (close * u * fr)
        cash += pos["q"] + pnl
        vol += pos["q"] + close * u
        closes_count += 1
        if close > pos["ep"]: wins += 1
    
    net = cash - cash_start
    wr = wins / max(1, closes_count) * 100
    
    return {
        "net": round(net, 2), "return_pct": round(net / cash_start * 100, 1),
        "closes": closes_count, "wr": round(wr, 1), "max_dd": round(mdd * 100, 1),
    }

def main():
    client = CoinbaseAdvancedClient()
    now = int(time.time())
    
    # 3 windows: 90-60d, 60-30d, 30-0d ago
    windows = [
        ("90-60d", now - 90*24*3600, now - 60*24*3600),
        ("60-30d", now - 60*24*3600, now - 30*24*3600),
        ("30-0d", now - 30*24*3600, now),
    ]
    
    print(f"Regime Filter Test — 3 windows × {len(TEST_COINS)} coins")
    print(f"{'=' * 100}")
    
    all_results = []
    
    for window_name, w_start, w_end in windows:
        print(f"\n--- Window: {window_name} ---")
        
        # Fetch BTC for gate
        btc = fetch_candles(client, BTC, w_start, w_end)
        btc_lk = {int(c["start"]): float(c["close"]) for c in btc}
        
        for coin in TEST_COINS:
            try:
                candles = fetch_candles(client, coin, w_start, w_end)
                if len(candles) < 100:
                    print(f"  {coin}: insufficient data ({len(candles)} candles)")
                    continue
                
                # Compute ATR% for this window
                atr_pct = compute_atr_pct(candles, 14)
                
                # Baseline (no regime filter)
                baseline = run_rsi_mr_baseline(candles, btc_lk)
                
                # With regime filter ATR% > 3%
                filtered_3 = run_rsi_mr_regime_filtered(candles, btc_lk, atr_threshold=3.0)
                
                # With regime filter ATR% > 2%
                filtered_2 = run_rsi_mr_regime_filtered(candles, btc_lk, atr_threshold=2.0)
                
                # With regime filter ATR% > 1.5%
                filtered_15 = run_rsi_mr_regime_filtered(candles, btc_lk, atr_threshold=1.5)
                
                print(f"  {coin:<15} ATR={atr_pct:.1f}%  Base: ${baseline['net']:>7.2f} ({baseline['closes']}t)  "
                      f">1.5%: ${filtered_15['net']:>7.2f} ({filtered_15['closes']}t, {filtered_15['regime_filtered_out']}filtered)  "
                      f">2%: ${filtered_2['net']:>7.2f} ({filtered_2['closes']}t, {filtered_2['regime_filtered_out']}filtered)  "
                      f">3%: ${filtered_3['net']:>7.2f} ({filtered_3['closes']}t, {filtered_3['regime_filtered_out']}filtered)")
                
                all_results.append({
                    "window": window_name, "coin": coin, "atr_pct": round(atr_pct, 1),
                    "baseline": baseline, "filtered_15": filtered_15,
                    "filtered_2": filtered_2, "filtered_3": filtered_3,
                })
            except Exception as e:
                print(f"  {coin}: ERROR - {e}")
    
    # Analysis: Does regime filter improve results?
    print(f"\n{'=' * 100}")
    print(f"ANALYSIS — Does ATR% regime filter improve results?")
    print(f"{'=' * 100}")
    
    for window_name in ["90-60d", "60-30d", "30-0d"]:
        window_results = [r for r in all_results if r["window"] == window_name]
        if not window_results:
            continue
        
        total_baseline = sum(r["baseline"]["net"] for r in window_results)
        total_filtered_3 = sum(r["filtered_3"]["net"] for r in window_results)
        total_filtered_2 = sum(r["filtered_2"]["net"] for r in window_results)
        total_filtered_15 = sum(r["filtered_15"]["net"] for r in window_results)
        
        # Count profitable windows
        prof_base = sum(1 for r in window_results if r["baseline"]["net"] > 0)
        prof_3 = sum(1 for r in window_results if r["filtered_3"]["net"] > 0)
        prof_2 = sum(1 for r in window_results if r["filtered_2"]["net"] > 0)
        prof_15 = sum(1 for r in window_results if r["filtered_15"]["net"] > 0)
        
        print(f"\n  {window_name}:")
        print(f"    Baseline:  ${total_baseline:>8.2f}  ({prof_base}/{len(window_results)} profitable)")
        print(f"    ATR>1.5%:  ${total_filtered_15:>8.2f}  ({prof_15}/{len(window_results)} profitable)")
        print(f"    ATR>2.0%:  ${total_filtered_2:>8.2f}  ({prof_2}/{len(window_results)} profitable)")
        print(f"    ATR>3.0%:  ${total_filtered_3:>8.2f}  ({prof_3}/{len(window_results)} profitable)")
        
        # Does filtering help?
        if total_filtered_3 > total_baseline:
            print(f"    ✅ ATR>3% IMPROVES results by ${total_filtered_3 - total_baseline:+.2f}")
        else:
            print(f"    ❌ ATR>3% DEGRADES results by ${total_filtered_3 - total_baseline:+.2f}")
    
    # Overall verdict
    print(f"\n{'=' * 100}")
    print(f"VERDICT")
    print(f"{'=' * 100}")
    
    all_baseline = sum(r["baseline"]["net"] for r in all_results)
    all_filtered_3 = sum(r["filtered_3"]["net"] for r in all_results)
    all_filtered_2 = sum(r["filtered_2"]["net"] for r in all_results)
    all_filtered_15 = sum(r["filtered_15"]["net"] for r in all_results)
    
    print(f"  Total across all windows:")
    print(f"    Baseline:  ${all_baseline:>8.2f}")
    print(f"    ATR>1.5%:  ${all_filtered_15:>8.2f}")
    print(f"    ATR>2.0%:  ${all_filtered_2:>8.2f}")
    print(f"    ATR>3.0%:  ${all_filtered_3:>8.2f}")
    
    if all_filtered_3 > all_baseline:
        print(f"\n  ✅ REGIME FILTER WORKS — ATR>3% improves total by ${all_filtered_3 - all_baseline:+.2f}")
        print(f"  The edge is regime-dependent and the filter successfully captures this.")
    else:
        print(f"\n  ❌ REGIME FILTER FAILS — ATR>3% degrades total by ${all_filtered_3 - all_baseline:+.2f}")
        print(f"  The edge is NOT regime-dependent, or ATR% is not the right filter.")
    
    # Save
    output = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "results": all_results,
        "verdict": "REGIME FILTER WORKS" if all_filtered_3 > all_baseline else "REGIME FILTER FAILS",
        "improvement": round(all_filtered_3 - all_baseline, 2),
    }
    out_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "reports", "regime_filter_test.json")
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "w") as f:
        json.dump(output, f, indent=2, default=str)
    print(f"\nResults saved to {out_path}")

if __name__ == "__main__":
    main()
