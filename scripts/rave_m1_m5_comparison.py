import json, time, sys, os
from datetime import datetime, timezone
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from coinbase_advanced_client import CoinbaseAdvancedClient

PRODUCT = "RAVE-USD"
BTC = "BTC-USD"

def fetch(client, pid, start, end, gran="FIVE_MINUTE"):
    chunk = 300*5*60 if gran == "FIVE_MINUTE" else 300*60
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
        fr=0.0015 if vol>=50000 else (0.0025 if vol>=10000 else 0.0040)
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

def main():
    client = CoinbaseAdvancedClient()
    now = int(time.time())
    s30 = now-30*24*3600

    print("Fetching RAVE M5 (30d)...")
    rave5 = fetch(client, PRODUCT, s30, now, "FIVE_MINUTE")
    print(f"  {len(rave5)} candles")

    print("Fetching RAVE M1 (30d) — this may take a while...")
    rave1 = fetch(client, PRODUCT, s30, now, "ONE_MINUTE")
    print(f"  {len(rave1)} candles")

    print("Fetching BTC M1 + M5...")
    btc1 = fetch(client, BTC, s30, now, "ONE_MINUTE")
    btc5 = fetch(client, BTC, s30, now, "FIVE_MINUTE")
    btc1_lk = {int(c["start"]): float(c["close"]) for c in btc1}
    btc5_lk = {int(c["start"]): float(c["close"]) for c in btc5}

    results = {}

    # M5 sweep
    print(f"\n🔬 M5 sweep:")
    m5_r = []
    for rp in [2,3,4,5]:
        for re in [20,25,30,35,40]:
            for tp in [20,25,30]:
                r = bt(rave5, btc5_lk, rp, re, tp)
                r["label"] = f"M5 RSI({rp})<{re} TP{tp}"
                m5_r.append(r)
    m5_r.sort(key=lambda x: x["net"], reverse=True)
    print(f"  Top 5:")
    for i,r in enumerate(m5_r[:5]):
        print(f"    {i+1}. {r['label']}: ${r['net']:.2f}, {r['trades']}t, {r['wr']}% WR, ${r['avg']}/t")
    results["m5"] = m5_r[:10]

    # M1 sweep (smaller grid — this is expensive)
    print(f"\n🔬 M1 sweep (focused):")
    m1_r = []
    for rp in [2,3,4]:
        for re in [20,25,30]:
            for tp in [20,25,30]:
                r = bt(rave1, btc1_lk, rp, re, tp)
                r["label"] = f"M1 RSI({rp})<{re} TP{tp}"
                m1_r.append(r)
                print(f"    {r['label']}: ${r['net']:.2f}, {r['trades']}t, {r['wr']}% WR, DD={r['mdd']}%")
    m1_r.sort(key=lambda x: x["net"], reverse=True)
    print(f"\n  Top 5:")
    for i,r in enumerate(m1_r[:5]):
        print(f"    {i+1}. {r['label']}: ${r['net']:.2f}, {r['trades']}t, {r['wr']}% WR, ${r['avg']}/t")
    results["m1"] = m1_r[:10]

    # Compare
    best_m5 = m5_r[0]
    best_m1 = m1_r[0]
    print(f"\n{'='*60}")
    print(f"🏆 M5 vs M1 COMPARISON")
    print(f"{'='*60}")
    print(f"M5 best: {best_m5['label']} -> ${best_m5['net']:.2f}, {best_m5['trades']}t, {best_m5['wr']}% WR")
    print(f"M1 best: {best_m1['label']} -> ${best_m1['net']:.2f}, {best_m1['trades']}t, {best_m1['wr']}% WR")
    print(f"M1/M5 trade ratio: {best_m1['trades']}/{best_m5['trades']} = {best_m1['trades']/max(1,best_m5['trades']):.1f}x")

    with open("reports/m1_m5_comparison.json", "w") as f:
        json.dump(results, f, indent=2)
    print(f"\nSaved to reports/m1_m5_comparison.json")

if __name__ == "__main__":
    main()
