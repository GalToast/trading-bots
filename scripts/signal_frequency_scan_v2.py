import json
import time
from datetime import datetime, timezone
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from coinbase_advanced_client import CoinbaseAdvancedClient

BTC = "BTC-USD"
# Focused list: volatile mid-caps + meme coins
COINS = [
    "RAVE-USD", "IOTX-USD", "BAL-USD", "BLUR-USD", "ALEPH-USD",
    "DOGE-USD", "PEPE-USD", "WIF-USD", "BONK-USD", "FARTCOIN-USD",
    "VIRTUAL-USD", "TRUMP-USD", "MOG-USD", "POPCAT-USD", "BRETT-USD",
    "SEI-USD", "TIA-USD", "FET-USD", "RENDER-USD", "WLD-USD",
    "STX-USD", "IMX-USD", "ONDO-USD", "PENDLE-USD", "RUNE-USD",
    "INJ-USD", "NEAR-USD", "APT-USD", "SUI-USD", "ARB-USD",
    "OP-USD", "MATIC-USD", "AVAX-USD", "LINK-USD", "AAVE-USD",
    "FIL-USD", "UNI-USD", "SOL-USD", "XRP-USD"
]

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
            time.sleep(0.05)
        except:
            cs = ce
            time.sleep(0.2)
    all_c.sort(key=lambda c: int(c["start"]))
    return all_c

def compute_rsi(closes, period=4):
    if len(closes) < period + 1: return 50.0
    deltas = [closes[i] - closes[i-1] for i in range(1, len(closes))]
    gains = [d if d > 0 else 0 for d in deltas[-period:]]
    losses = [-d if d < 0 else 0 for d in deltas[-period:]]
    avg_gain = sum(gains) / period
    avg_loss = sum(losses) / period
    if avg_loss > 0:
        rs = avg_gain / avg_loss
        return 100 - 100 / (1 + rs)
    return 100.0

def get_fee_rate(tv):
    if tv >= 50000: return 0.0015
    elif tv >= 10000: return 0.0025
    else: return 0.0040

def run_bt(candles, btc_lk, rp, re, tp):
    cash = 48.0; pos = None; closes = 0; wins = 0; vol = 0.0; hist = []; pk = 48.0; mdd = 0.0
    for c in candles:
        ts = int(c["start"]); h = float(c["high"]); l = float(c["low"]); cl = float(c["close"])
        hist.append(cl)
        if len(hist) > 50: hist.pop(0)
        # BTC gate
        btc_ok = True
        p_t, p_t3 = ts-60, ts-180
        if p_t in btc_lk and p_t3 in btc_lk:
            mom = (btc_lk[p_t] - btc_lk[p_t3]) / btc_lk[p_t3]
            if mom < -0.001: btc_ok = False
        # Session gate
        hour = datetime.fromtimestamp(ts, tz=timezone.utc).hour
        if hour in {0, 6, 12, 19}: continue
        fr = get_fee_rate(vol)
        # Exit
        if pos:
            pos["h"] += 1
            if h >= pos["tp"]:
                exit_p = pos["tp"]; wins += 1
                units = pos["q"] / pos["ep"]
                pnl = (exit_p - pos["ep"]) * units - (pos["q"] * fr) - (exit_p * units * fr)
                cash += pos["q"] + pnl; vol += pos["q"] + exit_p * units; closes += 1
                if cash > pk: pk = cash
                dd = (pk - cash) / pk
                if dd > mdd: mdd = dd
                pos = None
        # Entry
        if pos is None and cash >= 10 and btc_ok and len(hist) >= rp + 2:
            rsi_v = compute_rsi(hist[:-1], rp)
            if rsi_v <= re:
                ep = float(c["open"]); tq = cash
                if tq >= 10:
                    pos = {"ep": ep, "q": tq, "h": 0, "tp": ep * (1 + tp/100)}
                    cash -= tq
    if pos: cash += pos["q"]
    net = cash - 48; wr = wins/max(1,closes)*100
    return {"net": round(net,2), "return_pct": round(net/48*100,1), "trades": closes,
            "wr": round(wr,1), "avg_trade": round(net/max(1,closes),2), "mdd": round(mdd*100,2)}

def main():
    client = CoinbaseAdvancedClient()
    now = int(time.time())
    start_30d = now - 30*24*3600

    # BTC lookup
    print("Fetching BTC M1 (30d)...")
    btc = fetch_candles(client, BTC, start_30d, now, granularity="ONE_MINUTE")
    btc_lk = {int(c["start"]): float(c["close"]) for c in btc}
    print(f"  {len(btc_lk)} candles")

    # Scan
    print(f"\n🔬 Scanning {len(COINS)} coins (30d, RSI<45, TP20, no SL)...")
    results = []
    for i, coin in enumerate(COINS):
        if i % 5 == 0: print(f"  {i}/{len(COINS)}: {coin}")
        try:
            candles = fetch_candles(client, coin, start_30d, now)
            if len(candles) < 200: continue
            r = run_bt(candles, btc_lk, 4, 45, 20)
            r["coin"] = coin; r["candles"] = len(candles)
            r["per_month"] = r["net"]  # already 30d
            results.append(r)
            flag = "🔥" if r["net"] > 50 else "✅" if r["net"] > 0 else "❌"
            print(f"    {flag} {coin}: ${r['net']:.2f} ({r['return_pct']}%), {r['trades']}t, {r['wr']}% WR, DD={r['mdd']}%")
        except Exception as e:
            print(f"    ⚠️ {coin}: {e}")

    results.sort(key=lambda x: x["net"], reverse=True)

    print(f"\n{'='*80}")
    print(f"🏆 TOP 15 COINS")
    print(f"{'='*80}")
    for i, r in enumerate(results[:15]):
        print(f"  {i+1}. {r['coin']}: ${r['net']:.2f} ({r['return_pct']}%), {r['trades']}t, {r['wr']}% WR, DD={r['mdd']}%")

    # Deep dive on top 3: RSI(2)/RSI(3) + RSI(30)/RSI(45)/RSI(50)
    print(f"\n🔬 DEEP DIVE: Top 3 coins with RSI(2)/RSI(3) variants")
    top3 = [r["coin"] for r in results[:3] if r["net"] > 0]
    deep = {}
    for coin in top3:
        print(f"  Testing {coin}...")
        try:
            candles = fetch_candles(client, coin, start_30d, now)
            coin_results = []
            for rp in [2, 3, 4]:
                for re in [20, 30, 40, 45, 50]:
                    for tp in [15, 20, 25]:
                        r = run_bt(candles, btc_lk, rp, re, tp)
                        r["label"] = f"RSI({rp})<{re} TP{tp}"
                        coin_results.append(r)
            coin_results.sort(key=lambda x: x["net"], reverse=True)
            deep[coin] = coin_results[:5]
            print(f"    Top 5:")
            for i, r in enumerate(coin_results[:5]):
                print(f"      {i+1}. {r['label']}: ${r['net']:.2f}, {r['trades']}t, {r['wr']}% WR")
        except Exception as e:
            print(f"    Error: {e}")

    with open("reports/signal_frequency_scan.json", "w") as f:
        json.dump({"universe_scan": results, "deep_dive": deep}, f, indent=2)
    print(f"\nSaved to reports/signal_frequency_scan.json")

if __name__ == "__main__":
    main()
