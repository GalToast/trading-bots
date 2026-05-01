import json, time, sys, os
from datetime import datetime, timezone
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from coinbase_advanced_client import CoinbaseAdvancedClient

BTC = "BTC-USD"
# All coins that showed ANY profit in the universe scan + extras
COINS = [
    "RAVE-USD", "FARTCOIN-USD", "FET-USD", "IOTX-USD", "BAL-USD",
    "BLUR-USD", "ALEPH-USD", "PEPE-USD", "WIF-USD", "TRUMP-USD",
    "RENDER-USD", "DOGE-USD", "BONK-USD", "VIRTUAL-USD", "MOG-USD",
    "POPCAT-USD", "BRETT-USD", "SEI-USD", "TIA-USD", "WLD-USD",
    "STX-USD", "IMX-USD", "ONDO-USD", "PENDLE-USD", "RUNE-USD",
    "INJ-USD", "NEAR-USD", "APT-USD", "SUI-USD", "ARB-USD",
    "OP-USD", "AVAX-USD", "LINK-USD", "AAVE-USD", "FIL-USD",
    "UNI-USD", "SOL-USD", "XRP-USD", "MATIC-USD"
]

def fetch(client, pid, start, end, gran="FIVE_MINUTE"):
    chunk = 300*5*60
    all_c, cs = [], start
    while cs < end:
        ce = min(cs + chunk, end)
        try:
            resp = client.market_candles(pid, start=cs, end=ce, granularity=gran)
            cands = resp.get("candles", [])
            all_c.extend(cands); cs = ce
            if not cands: break
            time.sleep(0.05)
        except:
            cs = ce; time.sleep(0.2)
    all_c.sort(key=lambda c: int(c["start"]))
    return all_c

def rsi(closes, p=4):
    if len(closes) < p+1: return 50.0
    d = [closes[i]-closes[i-1] for i in range(1, len(closes))]
    g = [x if x>0 else 0 for x in d[-p:]]
    l = [-x if x<0 else 0 for x in d[-p:]]
    ag, al = sum(g)/p, sum(l)/p
    if al > 0: return 100 - 100/(1+ag/al)
    return 100.0

def get_fee(tv):
    if tv >= 50000: return 0.0015
    elif tv >= 10000: return 0.0025
    else: return 0.0040

def bt(candles, btc_lk, rp, re, tp):
    cash=48.0; pos=None; cl=0; w=0; vol=0.0; h=[]; pk=48.0; mdd=0.0
    for c in candles:
        ts=int(c["start"]); hi=float(c["high"]); lo=float(c["low"]); close=float(c["close"])
        h.append(close)
        if len(h)>50: h.pop(0)
        boc=True
        pt,pt3=ts-60,ts-180
        if pt in btc_lk and pt3 in btc_lk:
            mom=(btc_lk[pt]-btc_lk[pt3])/btc_lk[pt3]
            if mom<-0.001: boc=False
        hr=datetime.fromtimestamp(ts,tz=timezone.utc).hour
        if hr in {0,6,12,19}: continue
        fr=get_fee(vol)
        if pos:
            pos["h"]+=1
            if hi>=pos["tp"]:
                ep2=pos["tp"]; w+=1
                u=pos["q"]/pos["ep"]
                pnl=(ep2-pos["ep"])*u-(pos["q"]*fr)-(ep2*u*fr)
                cash+=pos["q"]+pnl; vol+=pos["q"]+ep2*u; cl+=1
                if cash>pk: pk=cash
                dd=(pk-cash)/pk
                if dd>mdd: mdd=dd
                pos=None
        if pos is None and cash>=10 and boc and len(h)>=rp+2:
            rv=rsi(h[:-1],rp)
            if rv<=re:
                ep=float(c["open"]); tq=cash
                if tq>=10:
                    pos={"ep":ep,"q":tq,"h":0,"tp":ep*(1+tp/100)}
                    cash-=tq
    if pos: cash+=pos["q"]
    net=cash-48; wr2=w/max(1,cl)*100
    return {"net":round(net,2),"rpct":round(net/48*100,1),"trades":cl,"wr":round(wr2,1),
            "avg":round(net/max(1,cl),2),"mdd":round(mdd*100,2)}

def optimize_coin(candles, btc_lk):
    """Find optimal params for a single coin."""
    best = {"net": -999}
    for rp in [2, 3, 4, 5, 7]:
        for re in [15, 20, 25, 30, 35, 40, 45, 50]:
            for tp in [15, 20, 25, 30, 40, 50]:
                r = bt(candles, btc_lk, rp, re, tp)
                if r["net"] > best["net"]:
                    best = r
                    best["rp"], best["re"], best["tp"] = rp, re, tp
    return best

def main():
    client = CoinbaseAdvancedClient()
    now = int(time.time())
    s30 = now-30*24*3600

    print("Fetching BTC M5 (30d)...")
    btc5 = fetch(client, BTC, s30, now)
    btc5_lk = {int(c["start"]): float(c["close"]) for c in btc5}
    print(f"  {len(btc5_lk)} candles")

    print(f"\n🚀 SPACE LAUNCH: Per-Coin Parameter Optimization (30d)")
    print(f"   Each coin gets its OWN optimal RSI period, entry threshold, and TP level.")
    print(f"   Testing 5×8×6 = 240 configs per coin × {len(COINS)} coins = {240*len(COINS)} total backtests\n")

    results = []
    for i, coin in enumerate(COINS):
        if i % 5 == 0:
            print(f"  Progress: {i}/{len(COINS)}")
        try:
            candles = fetch(client, coin, s30, now)
            if len(candles) < 200:
                print(f"    ⚠️ {coin}: only {len(candles)} candles, skipping")
                continue
            opt = optimize_coin(candles, btc5_lk)
            opt["coin"] = coin
            opt["candles"] = len(candles)
            results.append(opt)
            flag = "🔥" if opt["net"] > 100 else "✅" if opt["net"] > 0 else "❌"
            print(f"    {flag} {coin}: RSI({opt.get('rp','?')})<{opt.get('re','?')}> TP{opt.get('tp','?')} = ${opt['net']:.2f} ({opt['rpct']}%), {opt['trades']}t, {opt['wr']}% WR, DD={opt['mdd']}%")
        except Exception as e:
            print(f"    ⚠️ {coin}: {e}")

    results.sort(key=lambda x: x["net"], reverse=True)

    print(f"\n{'='*80}")
    print(f"🚀 PER-COIN OPTIMIZED RESULTS — TOP 20")
    print(f"{'='*80}")
    for i, r in enumerate(results[:20]):
        print(f"  {i+1}. {r['coin']}: RSI({r['rp']})<{r['re']}> TP{r['tp']} = ${r['net']:.2f} ({r['rpct']}%), {r['trades']}t, {r['wr']}% WR, DD={r['mdd']}%")

    # Total if we could trade all profitable coins with separate $48
    profitable = [r for r in results if r["net"] > 0]
    total_profit = sum(r["net"] for r in profitable)
    total_capital = len(profitable) * 48
    print(f"\n{'='*80}")
    print(f"🌌 THE SPACE ANSWER")
    print(f"{'='*80}")
    print(f"Profitable coins: {len(profitable)}/{len(results)}")
    print(f"Total profit if each had $48: ${total_profit:.2f}/30d")
    print(f"Total capital needed: ${total_capital}")
    print(f"Return on total capital: {total_profit/total_capital*100:.1f}%")
    print(f"RAVE alone: ${results[0]['net']:.2f} with $48")
    print(f"ALL coins combined: ${total_profit:.2f} with ${total_capital}")
    print(f"Per-$48 efficiency: RAVE=${results[0]['net']:.2f} vs avg=${total_profit/len(profitable):.2f}")

    # Check if any coin is better than RAVE
    non_rave = [r for r in results if r["coin"] != "RAVE-USD" and r["net"] > 0]
    if non_rave:
        print(f"\nNon-RAVE coins that work:")
        for r in non_rave[:10]:
            print(f"  {r['coin']}: ${r['net']:.2f} with RSI({r['rp']})<{r['re']}> TP{r['tp']}")

    with open("reports/per_coin_optimization.json", "w") as f:
        json.dump(results, f, indent=2)
    print(f"\nSaved to reports/per_coin_optimization.json")

if __name__ == "__main__":
    main()
