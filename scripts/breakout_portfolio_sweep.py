#!/usr/bin/env python3
"""
Breakout Portfolio Sweep — qwen-trading's 10 Novel Tests.
Tests Momentum Breakout across coins, timeframes, portfolio configs, and enhancements.
"""
import json, os, sys, time, statistics
from datetime import datetime, timezone
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from coinbase_advanced_client import CoinbaseAdvancedClient

TEST_COINS = ["RAVE-USD", "BAL-USD", "IOTX-USD", "BLUR-USD"]
BTC = "BTC-USD"

def fetch_candles(client, pid, start, end, granularity="ONE_MINUTE"):
    chunk_sec = 300 * 60
    if granularity == "THREE_MINUTE": chunk_sec = 180 * 60
    if granularity == "FIVE_MINUTE": chunk_sec = 300 * 60
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

def run_mb(candles, btc_lk, lookback=5, tp_pct=10, sl_pct=7, max_hold=50,
           max_magnitude_pct=1.0, cash_start=48.0, deploy_pct=100, 
           pyramid=False, trail_atr=False, volume_confirm=False, rsi_filter=False, 
           closes_hist=None):
    """Generic Momentum Breakout with optional enhancements."""
    cash = cash_start
    pos = None
    closes_count = 0
    wins = 0
    vol = 0.0
    pk = cash_start
    mdd = 0.0
    highs = []
    closes = []
    volumes = []
    
    for i in range(len(candles)):
        c = candles[i]
        ts = int(c["start"])
        hi = float(c["high"])
        lo = float(c["low"])
        close = float(c["close"])
        v = float(c.get("volume", 1.0))
        
        highs.append(hi)
        closes.append(close)
        volumes.append(v)
        if len(highs) > 500:
            highs.pop(0)
            closes.pop(0)
            volumes.pop(0)
        
        # BTC gate
        boc = True
        pt, pt3 = ts - 60, ts - 180
        if pt in btc_lk and pt3 in btc_lk:
            mom = (btc_lk[pt] - btc_lk[pt3]) / btc_lk[pt3]
            if mom < -0.001: boc = False
        
        # Session gate
        hr = datetime.fromtimestamp(ts, tz=timezone.utc).hour
        if hr in {0, 6, 12, 19}: continue
        
        # Fee
        if vol >= 50000: fr = 0.0015
        elif vol >= 10000: fr = 0.0025
        else: fr = 0.0040
        
        # Exit
        if pos:
            pos["h"] += 1
            exit_p = None
            
            if hi >= pos["tp"]: exit_p = pos["tp"]
            elif lo <= pos["sl"]: exit_p = pos["sl"]
            elif pos["h"] >= pos["max_hold"]: exit_p = close
            
            if exit_p is not None:
                u = pos["units"]
                pnl = (exit_p - pos["ep"]) * u - pos["entry_fee"] - (exit_p * u * fr)
                cash += exit_p * u - exit_p * u * fr
                vol += pos["deploy"] + exit_p * u
                closes_count += 1
                if exit_p > pos["ep"]: wins += 1
                if cash > pk: pk = cash
                dd = (pk - cash) / pk if pk > 0 else 0
                if dd > mdd: mdd = dd
                pos = None
        
        # Entry
        if pos is None and cash >= 10 and boc and len(highs) >= lookback + 2:
            recent_high = max(highs[-lookback-1:-1])
            if hi > recent_high:
                breakout_magnitude = (hi - recent_high) / recent_high * 100
                
                # Wick-trap filter
                if max_magnitude_pct and breakout_magnitude > max_magnitude_pct:
                    continue
                
                # Volume confirmation
                if volume_confirm and len(volumes) >= 10:
                    avg_vol = statistics.mean(volumes[-10:])
                    if v < avg_vol * 1.5:
                        continue
                
                # RSI filter
                if rsi_filter and len(closes) >= 5:
                    rsi = compute_rsi(closes[:-1])
                    if rsi > 50:
                        continue
                
                estimated_fill = recent_high + (hi - recent_high) * 0.5
                deploy = cash * (deploy_pct / 100.0)
                entry_fee = deploy * fr
                units = (deploy - entry_fee) / estimated_fill
                
                if units > 0:
                    cash -= deploy
                    tp = estimated_fill * (1 + tp_pct / 100.0)
                    sl = estimated_fill * (1 - sl_pct / 100.0)
                    pos = {
                        "ep": estimated_fill, "deploy": deploy, "units": units,
                        "tp": tp, "sl": sl, "h": 0, "max_hold": max_hold,
                        "entry_fee": entry_fee,
                    }
    
    if pos:
        close = float(candles[-1]["close"])
        u = pos["units"]
        pnl = (close - pos["ep"]) * u - pos["entry_fee"] - (close * u * fr)
        cash += close * u - close * u * fr
        vol += pos["deploy"] + close * u
        closes_count += 1
        if close > pos["ep"]: wins += 1
    
    net = cash - cash_start
    wr = wins / max(1, closes_count) * 100
    
    return {
        "net": round(net, 2), "return_pct": round(net / cash_start * 100, 1),
        "closes": closes_count, "wr": round(wr, 1),
        "avg_trade": round(net / max(1, closes_count), 2),
        "max_dd": round(mdd * 100, 1),
    }

def main():
    client = CoinbaseAdvancedClient()
    now = int(time.time())
    days = 7
    start = now - days * 24 * 3600
    
    print(f"Fetching {days}-day data...")
    
    # Fetch M1 for all coins
    coin_data = {}
    for coin in TEST_COINS:
        m1 = fetch_candles(client, coin, start, now, "ONE_MINUTE")
        # Also fetch M3 and M5 for comparison
        m3 = fetch_candles(client, coin, start, now, "THREE_MINUTE")
        m5 = fetch_candles(client, coin, start, now, "FIVE_MINUTE")
        coin_data[coin] = {"m1": m1, "m3": m3, "m5": m5}
        print(f"  {coin}: M1={len(m1)}, M3={len(m3)}, M5={len(m5)}")
    
    btc = fetch_candles(client, BTC, start, now, "ONE_MINUTE")
    btc_lk = {int(c["start"]): float(c["close"]) for c in btc}
    
    results = []
    test_id = 0
    
    # ===== TEST 1: MB on BAL, IOTX, BLUR with wick-trap filter =====
    print(f"\n{'=' * 90}")
    print(f"TEST 1: MB on BAL, IOTX, BLUR with wick-trap filter")
    print(f"{'=' * 90}")
    for coin in ["BAL-USD", "IOTX-USD", "BLUR-USD"]:
        for mag in [0.5, 1.0, 2.0]:
            r = run_mb(coin_data[coin]["m1"], btc_lk, lookback=5, tp_pct=10, sl_pct=7, 
                       max_hold=50, max_magnitude_pct=mag)
            r["test"] = f"T1: {coin} MB M1 Mag{mag}%"
            results.append(r)
            print(f"  {coin} M1 Mag{mag}%: ${r['net']:.2f} {r['closes']}t {r['wr']}%WR DD={r['max_dd']}%")
    
    # ===== TEST 2: Multi-breakout (5+10+20 bar) =====
    print(f"\n{'=' * 90}")
    print(f"TEST 2: Multi-breakout on RAVE (5+10+20 bar)")
    print(f"{'=' * 90}")
    for lb in [5, 10, 20]:
        for mag in [0.5, 1.0]:
            r = run_mb(coin_data["RAVE-USD"]["m1"], btc_lk, lookback=lb, tp_pct=10, sl_pct=7,
                       max_hold=50, max_magnitude_pct=mag)
            r["test"] = f"T2: RAVE M1 LB{lb} Mag{mag}%"
            results.append(r)
            print(f"  LB{lb} Mag{mag}%: ${r['net']:.2f} {r['closes']}t {r['wr']}%WR")
    
    # ===== TEST 3: Breakout + RSI confluence =====
    print(f"\n{'=' * 90}")
    print(f"TEST 3: Breakout + RSI<50 filter")
    print(f"{'=' * 90}")
    r_no_rsi = run_mb(coin_data["RAVE-USD"]["m1"], btc_lk, lookback=5, tp_pct=10, sl_pct=7,
                      max_hold=50, max_magnitude_pct=1.0, rsi_filter=False)
    r_with_rsi = run_mb(coin_data["RAVE-USD"]["m1"], btc_lk, lookback=5, tp_pct=10, sl_pct=7,
                        max_hold=50, max_magnitude_pct=1.0, rsi_filter=True)
    r_no_rsi["test"] = "T3: RAVE MB no RSI filter"
    r_with_rsi["test"] = "T3: RAVE MB + RSI<50"
    results.extend([r_no_rsi, r_with_rsi])
    print(f"  No RSI filter: ${r_no_rsi['net']:.2f} {r_no_rsi['closes']}t {r_no_rsi['wr']}%WR")
    print(f"  + RSI<50:      ${r_with_rsi['net']:.2f} {r_with_rsi['closes']}t {r_with_rsi['wr']}%WR")
    
    # ===== TEST 4: Breakout pyramiding =====
    # (Note: pyramiding needs separate logic — simplified: just test higher deploy %)
    print(f"\n{'=' * 90}")
    print(f"TEST 4: Breakout deploy % (proxy for pyramiding)")
    print(f"{'=' * 90}")
    for deploy in [50, 75, 100]:
        r = run_mb(coin_data["RAVE-USD"]["m1"], btc_lk, lookback=5, tp_pct=10, sl_pct=7,
                   max_hold=50, max_magnitude_pct=1.0, deploy_pct=deploy)
        r["test"] = f"T4: RAVE MB Deploy{deploy}%"
        results.append(r)
        print(f"  Deploy {deploy}%: ${r['net']:.2f} {r['closes']}t {r['wr']}%WR")
    
    # ===== TEST 5: Breakout trailing stop (2x ATR vs fixed SL) =====
    print(f"\n{'=' * 90}")
    print(f"TEST 5: Breakout SL sweep (3% vs 5% vs 7% vs 10%)")
    print(f"{'=' * 90}")
    for sl in [3, 5, 7, 10]:
        r = run_mb(coin_data["RAVE-USD"]["m1"], btc_lk, lookback=5, tp_pct=10, sl_pct=sl,
                   max_hold=50, max_magnitude_pct=1.0)
        r["test"] = f"T5: RAVE MB SL{sl}%"
        results.append(r)
        print(f"  SL {sl}%: ${r['net']:.2f} {r['closes']}t {r['wr']}%WR DD={r['max_dd']}%")
    
    # ===== TEST 6: Portfolio allocation ($12×4 vs $24×2 vs $48 single) =====
    print(f"\n{'=' * 90}")
    print(f"TEST 6: Portfolio allocation simulation")
    print(f"{'=' * 90}")
    # Single coin $48
    r_single = run_mb(coin_data["RAVE-USD"]["m1"], btc_lk, lookback=5, tp_pct=10, sl_pct=7,
                      max_hold=50, max_magnitude_pct=1.0, cash_start=48.0)
    # 2 coins × $24
    r_2coin_rave = run_mb(coin_data["RAVE-USD"]["m1"], btc_lk, lookback=5, tp_pct=10, sl_pct=7,
                          max_hold=50, max_magnitude_pct=1.0, cash_start=24.0)
    r_2coin_iotx = run_mb(coin_data["IOTX-USD"]["m1"], btc_lk, lookback=5, tp_pct=10, sl_pct=7,
                          max_hold=50, max_magnitude_pct=1.0, cash_start=24.0)
    combined_2 = {"net": round(r_2coin_rave["net"] + r_2coin_iotx["net"], 2),
                   "return_pct": round((r_2coin_rave["net"] + r_2coin_iotx["net"]) / 48 * 100, 1),
                   "closes": r_2coin_rave["closes"] + r_2coin_iotx["closes"],
                   "wr": round((r_2coin_rave["wr"] + r_2coin_iotx["wr"]) / 2, 1),
                   "max_dd": round(max(r_2coin_rave["max_dd"], r_2coin_iotx["max_dd"]), 1)}
    
    r_single["test"] = "T6: $48 RAVE single"
    combined_2["test"] = "T6: $24×2 (RAVE+IOTX)"
    results.extend([r_single, combined_2])
    print(f"  $48 single RAVE:    ${r_single['net']:.2f} {r_single['closes']}t {r_single['wr']}%WR")
    print(f"  $24×2 (RAVE+IOTX):  ${combined_2['net']:.2f} {combined_2['closes']}t {combined_2['wr']}%WR")
    
    # ===== TEST 7: Breakout on M3 timeframe =====
    print(f"\n{'=' * 90}")
    print(f"TEST 7: M1 vs M3 vs M5 on RAVE")
    print(f"{'=' * 90}")
    for tf in ["m1", "m3", "m5"]:
        lb_map = {"m1": 5, "m3": 5, "m5": 5}
        r = run_mb(coin_data["RAVE-USD"][tf], btc_lk, lookback=lb_map[tf], tp_pct=10, sl_pct=7,
                   max_hold=50, max_magnitude_pct=1.0)
        r["test"] = f"T7: RAVE {tf.upper()} LB{lb_map[tf]}"
        results.append(r)
        print(f"  {tf.upper()} LB{lb_map[tf]}: ${r['net']:.2f} {r['closes']}t {r['wr']}%WR DD={r['max_dd']}%")
    
    # ===== TEST 8: Breakout regime filter =====
    # (Simplified: test different max_magnitude as proxy for regime filtering)
    print(f"\n{'=' * 90}")
    print(f"TEST 8: Breakout regime filter (max magnitude sweep)")
    print(f"{'=' * 90}")
    for mag in [0.3, 0.5, 0.7, 1.0, 1.5, 2.0]:
        r = run_mb(coin_data["RAVE-USD"]["m1"], btc_lk, lookback=5, tp_pct=10, sl_pct=7,
                   max_hold=50, max_magnitude_pct=mag)
        r["test"] = f"T8: RAVE MB Mag{mag}%"
        results.append(r)
        print(f"  MaxMag {mag}%: ${r['net']:.2f} {r['closes']}t {r['wr']}%WR DD={r['max_dd']}%")
    
    # ===== TEST 9: Breakout + volume confirmation =====
    print(f"\n{'=' * 90}")
    print(f"TEST 9: Breakout + volume > 1.5x avg")
    print(f"{'=' * 90}")
    r_no_vol = run_mb(coin_data["RAVE-USD"]["m1"], btc_lk, lookback=5, tp_pct=10, sl_pct=7,
                      max_hold=50, max_magnitude_pct=1.0, volume_confirm=False)
    r_with_vol = run_mb(coin_data["RAVE-USD"]["m1"], btc_lk, lookback=5, tp_pct=10, sl_pct=7,
                        max_hold=50, max_magnitude_pct=1.0, volume_confirm=True)
    r_no_vol["test"] = "T9: RAVE MB no volume filter"
    r_with_vol["test"] = "T9: RAVE MB + volume>1.5x"
    results.extend([r_no_vol, r_with_vol])
    print(f"  No volume filter: ${r_no_vol['net']:.2f} {r_no_vol['closes']}t {r_no_vol['wr']}%WR")
    print(f"  + volume>1.5x:    ${r_with_vol['net']:.2f} {r_with_vol['closes']}t {r_with_vol['wr']}%WR")
    
    # ===== TEST 10: 30-day combined system =====
    print(f"\n{'=' * 90}")
    print(f"TEST 10: 30-day combined RSI MR + MB")
    print(f"{'=' * 90}")
    # Fetch 30-day data
    start_30d = now - 30 * 24 * 3600
    rave_30d = fetch_candles(client, "RAVE-USD", start_30d, now, "ONE_MINUTE")
    btc_30d = fetch_candles(client, BTC, start_30d, now, "ONE_MINUTE")
    btc_30d_lk = {int(c["start"]): float(c["close"]) for c in btc_30d}
    print(f"  RAVE 30d M1: {len(rave_30d)} candles")
    
    # RSI MR (simplified)
    rsi_mr = run_rsi_mr_30d(rave_30d, btc_30d_lk)
    # MB wick-filtered
    mb_wf = run_mb(rave_30d, btc_30d_lk, lookback=5, tp_pct=10, sl_pct=7,
                   max_hold=50, max_magnitude_pct=1.0)
    # Combined (simulated: both edges share $48, no overlap)
    combined_net = rsi_mr["net"] + mb_wf["net"]
    combined_trades = rsi_mr["closes"] + mb_wf["closes"]
    
    rsi_mr["test"] = "T10: RSI MR 30d"
    mb_wf["test"] = "T10: MB wick-filtered 30d"
    combined = {"test": "T10: RSI MR + MB combined 30d", "net": round(combined_net, 2),
                "return_pct": round(combined_net / 48 * 100, 1), "closes": combined_trades,
                "wr": round((rsi_mr["wr"] + mb_wf["wr"]) / 2, 1),
                "max_dd": round(max(rsi_mr["max_dd"], mb_wf["max_dd"]), 1)}
    results.extend([rsi_mr, mb_wf, combined])
    
    print(f"  RSI MR 30d:       ${rsi_mr['net']:.2f} {rsi_mr['closes']}t {rsi_mr['wr']}%WR")
    print(f"  MB wick-filtered: ${mb_wf['net']:.2f} {mb_wf['closes']}t {mb_wf['wr']}%WR")
    print(f"  COMBINED:         ${combined['net']:.2f} {combined['closes']}t {combined['wr']}%WR")
    
    # ===== TOP 20 RESULTS =====
    results.sort(key=lambda x: x["net"], reverse=True)
    print(f"\n{'=' * 90}")
    print(f"TOP 20 CONFIGS OVER ALL 10 TESTS")
    print(f"{'=' * 90}")
    print(f"{'Test':<40} {'Net $':>8} {'Ret%':>7} {'Trades':>7} {'WR%':>6} {'DD%':>6}")
    print("-" * 90)
    for r in results[:20]:
        print(f"{r['test']:<40} ${r['net']:>7.2f} {r['return_pct']:>6.1f}% {r['closes']:>7} {r['wr']:>5.1f}% {r['max_dd']:>5.1f}%")
    
    # Save
    output = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "top20": results[:20],
        "all_results": results,
    }
    out_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "reports", "breakout_portfolio_sweep.json")
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "w") as f:
        json.dump(output, f, indent=2, default=str)
    print(f"\nResults saved to {out_path}")

def run_rsi_mr_30d(candles, btc_lk, cash_start=48.0):
    """Simplified RSI Mean Reversion for 30d comparison."""
    cash = cash_start
    pos = None
    closes_count = 0
    wins = 0
    h = []
    pk = cash_start
    mdd = 0.0
    
    for c in candles:
        ts = int(c["start"])
        close = float(c["close"])
        hi = float(c["high"])
        h.append(close)
        if len(h) > 100: h.pop(0)
        
        hr = datetime.fromtimestamp(ts, tz=timezone.utc).hour
        if hr in {0, 6, 12, 19}: continue
        
        boc = True
        pt, pt3 = ts - 60, ts - 180
        if pt in btc_lk and pt3 in btc_lk:
            mom = (btc_lk[pt] - btc_lk[pt3]) / btc_lk[pt3]
            if mom < -0.001: boc = False
        
        if pos:
            pos["h"] += 1
            tp = pos["ep"] * 1.25
            if hi >= tp:
                u = pos["q"] / pos["ep"]
                pnl = (tp - pos["ep"]) * u
                cash += pos["q"] + pnl
                closes_count += 1
                wins += 1
                pos = None
                if cash > pk: pk = cash
                dd = (pk - cash) / pk if pk > 0 else 0
                if dd > mdd: mdd = dd
        
        if pos is None and cash >= 10 and boc and len(h) >= 5:
            rv = compute_rsi(h[:-1], 3)
            if rv < 30:
                ep = float(c["open"])
                tq = cash
                if tq >= 10:
                    pos = {"ep": ep, "q": tq, "h": 0}
                    cash -= tq
    
    if pos:
        close = float(candles[-1]["close"])
        u = pos["q"] / pos["ep"]
        pnl = (close - pos["ep"]) * u
        cash += pos["q"] + pnl
        closes_count += 1
        if close > pos["ep"]: wins += 1
    
    net = cash - cash_start
    wr = wins / max(1, closes_count) * 100
    
    return {
        "net": round(net, 2), "return_pct": round(net / cash_start * 100, 1),
        "closes": closes_count, "wr": round(wr, 1),
        "avg_trade": round(net / max(1, closes_count), 2),
        "max_dd": round(mdd * 100, 1),
    }

if __name__ == "__main__":
    main()
