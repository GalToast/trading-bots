#!/usr/bin/env python3
"""
Universe Scan — All Coinbase USD spot pairs tested with RSI(3)<30 TP25 edge.
Phase 1: 7-day backtest on ALL liquid USD coins
Phase 2: Top 10 get 30-day backtest
Phase 3: Top 3 get live shadow runners
"""
import json, os, sys, time, statistics
from datetime import datetime, timezone
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from coinbase_advanced_client import CoinbaseAdvancedClient

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

def get_fee(vol):
    if vol >= 50000: return 0.0015
    elif vol >= 10000: return 0.0025
    else: return 0.0040

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

def run_rsi_mr(candles, btc_lk, rsi_period=3, os_thresh=30, tp_pct=25, cash_start=48.0):
    """RSI Mean Reversion: RSI(n)<threshold, TP x%, no SL, no timeout."""
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
        if len(h) > 200: h.pop(0)
        
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
        "closes": closes_count, "wr": round(wr, 1),
        "avg_trade": round(net / max(1, closes_count), 2),
        "max_dd": round(mdd * 100, 1),
        "volume": round(vol, 2),
    }

def main():
    client = CoinbaseAdvancedClient()
    
    # STEP 1: Get all USD spot products
    print(f"Fetching all USD spot products...", flush=True)
    all_products = []
    pagination = None
    while True:
        kwargs = {"product_type": "SPOT", "limit": 500}
        if pagination:
            kwargs["pagination"] = pagination
        resp = client.list_products(**kwargs)
        products = resp.get("products", [])
        all_products.extend(products)
        pagination = resp.get("pagination", {}).get("next") if resp.get("pagination") else None
        if not products or not pagination:
            break
    
    print(f"  Total products: {len(all_products)}", flush=True)
    usd_pairs = [p["product_id"] for p in all_products if p.get("quote_currency_id") == "USD" and p.get("status") == "online"]
    print(f"  USD spot pairs (online): {len(usd_pairs)}", flush=True)
    
    # Exclude major pairs (BTC, ETH, SOL) — we want microcaps/altcoins
    exclusions = {"BTC-USD", "ETH-USD", "SOL-USD", "USDC-USD", "USD-USD", "BCH-USD", "LTC-USD", "LINK-USD", "AVAX-USD", "MATIC-USD", "UNI-USD", "DOGE-USD", "SHIB-USD", "XRP-USD", "ADA-USD", "DOT-USD", "AAVE-USD", "ATOM-USD", "NEAR-USD", "FIL-USD", "APT-USD", "ARB-USD", "OP-USD", "SUI-USD", "SEI-USD", "TIA-USD", "RENDER-USD", "FET-USD", "INJ-USD", "PEPE-USD", "WIF-USD", "BONK-USD", "FLOKI-USD", "TRUMP-USD", "MELANIA-USD", "XLM-USD", "ALGO-USD", "HBAR-USD", "VET-USD", "ICP-USD", "GRT-USD", "MKR-USD", "CRV-USD", "SAND-USD", "MANA-USD", "AXS-USD", "ENJ-USD", "CHZ-USD", "GALA-USD", "IMX-USD", "RUNE-USD", "STX-USD", "LDO-USD", "COMP-USD", "SNX-USD", "YFI-USD", "SUSHI-USD", "BAL-USD", "ZRX-USD", "1INCH-USD", "UMA-USD", "REN-USD", "KNC-USD", "BAND-USD", "NMR-USD", "SKL-USD", "STORJ-USD", "ANKR-USD", "COTI-USD", "RLC-USD", "OCEAN-USD", "CELR-USD", "CVC-USD", "DNT-USD", "NU-USD", "KEEP-USD", "BADGER-USD", "TRIBE-USD", "FEI-USD", "RAI-USD", "LCX-USD", "CTX-USD", "CLV-USD", "MASK-USD", "POLY-USD", "SUPER-USD", "FORTH-USD", "BAKE-USD", "AMP-USD", "PUNDIX-USD", "DDX-USD", "TRAC-USD", "DAFI-USD", "FARM-USD", "ALCX-USD", "BOND-USD", "MATH-USD", "GTC-USD", "MIR-USD", "RARI-USD", "FIS-USD", "POWR-USD", "CLV-USD", "SHPING-USD", "FIDA-USD", "FORTH-USD"}
    
    # Actually, let's be smarter. We want coins that are NOT the major ones but ARE liquid enough.
    # Let's keep it simple: test ALL USD pairs except BTC, ETH, SOL, USDC
    major_excl = {"BTC-USD", "ETH-USD", "SOL-USD", "USDC-USD"}
    test_coins = [c for c in usd_pairs if c not in major_excl]
    
    # Limit to 50 coins for Phase 1 (most liquid)
    # The API returns them sorted by some order — let's take the first 50 non-major
    test_coins = test_coins[:50]
    print(f"  Testing {len(test_coins)} coins (Phase 1, 7-day RSI MR)", flush=True)
    print(f"  Coins: {test_coins[:10]}...", flush=True)
    
    # Fetch BTC for gate
    now = int(time.time())
    start_7d = now - 7 * 24 * 3600
    btc = fetch_candles(client, BTC, start_7d, now)
    btc_lk = {int(c["start"]): float(c["close"]) for c in btc}
    
    # Phase 1: 7-day scan
    results = []
    failed = []
    
    print(f"\n{'=' * 80}")
    print(f"PHASE 1: 7-DAY RSI MR SCAN — {len(test_coins)} coins")
    print(f"{'=' * 80}")
    print(f"{'Coin':<15} {'Net $':>8} {'Ret%':>7} {'Trades':>7} {'WR%':>6} {'DD%':>6} {'Vol':>10}")
    print("-" * 80)
    
    for coin in test_coins:
        try:
            candles = fetch_candles(client, coin, start_7d, now)
            if len(candles) < 100:
                failed.append({"coin": coin, "reason": f"only {len(candles)} candles"})
                continue
            
            r = run_rsi_mr(candles, btc_lk)
            r["coin"] = coin
            results.append(r)
            print(f"{coin:<15} ${r['net']:>7.2f} {r['return_pct']:>6.1f}% {r['closes']:>7} {r['wr']:>5.1f}% {r['max_dd']:>5.1f}% ${r['volume']:>9.0f}")
        except Exception as e:
            failed.append({"coin": coin, "reason": str(e)[:100]})
    
    # Sort by net profit
    results.sort(key=lambda x: x["net"], reverse=True)
    
    print(f"\n{'=' * 80}")
    print(f"TOP 20 COINS — Phase 1 (7-day)")
    print(f"{'=' * 80}")
    print(f"{'Rank':<5} {'Coin':<15} {'Net $':>8} {'Ret%':>7} {'Trades':>7} {'WR%':>6} {'DD%':>6}")
    print("-" * 65)
    for rank, r in enumerate(results[:20], 1):
        print(f"{rank:<5} {r['coin']:<15} ${r['net']:>7.2f} {r['return_pct']:>6.1f}% {r['closes']:>7} {r['wr']:>5.1f}% {r['max_dd']:>5.1f}%")
    
    # Profitable vs losing
    profitable = [r for r in results if r["net"] > 0]
    losing = [r for r in results if r["net"] <= 0]
    print(f"\n  Profitable: {len(profitable)}/{len(results)}")
    print(f"  Losing: {len(losing)}/{len(results)}")
    if failed:
        print(f"  Failed/Insufficient data: {len(failed)}")
    
    # Save Phase 1
    output = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "phase": 1,
        "total_coins_scanned": len(test_coins),
        "profitable": len(profitable),
        "top20": results[:20],
        "all_results": results,
        "failed": failed[:20],
    }
    out_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "reports", "universe_scan_phase1.json")
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "w") as f:
        json.dump(output, f, indent=2, default=str)
    print(f"\nResults saved to {out_path}")
    
    # Phase 2: 30-day scan on top 10
    print(f"\n{'=' * 80}")
    print(f"PHASE 2: 30-DAY SCAN — Top 10 coins")
    print(f"{'=' * 80}")
    
    start_30d = now - 30 * 24 * 3600
    btc_30d = fetch_candles(client, BTC, start_30d, now)
    btc_30d_lk = {int(c["start"]): float(c["close"]) for c in btc_30d}
    
    top10 = results[:10]
    phase2_results = []
    
    for r in top10:
        coin = r["coin"]
        try:
            candles_30d = fetch_candles(client, coin, start_30d, now)
            if len(candles_30d) < 500:
                print(f"  {coin}: insufficient 30d data ({len(candles_30d)} candles)")
                continue
            
            r30 = run_rsi_mr(candles_30d, btc_30d_lk)
            r30["coin"] = coin
            phase2_results.append(r30)
            print(f"  {coin:<15} ${r30['net']:>7.2f} {r30['return_pct']:>6.1f}% {r30['closes']:>7} {r30['wr']:>5.1f}% DD={r30['max_dd']}%")
        except Exception as e:
            print(f"  {coin}: ERROR - {e}")
    
    phase2_results.sort(key=lambda x: x["net"], reverse=True)
    
    print(f"\n{'=' * 80}")
    print(f"TOP 10 — Phase 2 (30-day)")
    print(f"{'=' * 80}")
    for rank, r in enumerate(phase2_results[:10], 1):
        print(f"  {rank}. {r['coin']:<15} ${r['net']:>7.2f} ({r['return_pct']}%) {r['closes']}t {r['wr']}%WR DD={r['max_dd']}%")
    
    # Save Phase 2
    output["phase2"] = phase2_results
    with open(out_path, "w") as f:
        json.dump(output, f, indent=2, default=str)
    print(f"\nUpdated results saved to {out_path}")

if __name__ == "__main__":
    main()
