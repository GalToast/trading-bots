"""Volume-filtered RAVE backtest — only trade high-volume dips."""
import json, time, sys, os
from datetime import datetime, timezone
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from coinbase_advanced_client import CoinbaseAdvancedClient

PRODUCT = "RAVE-USD"
BTC = "BTC-USD"

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

def rsi(closes, p=3):
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

def bt_volume_filtered(candles, btc_lk, rp, re, tp, vol_mult, max_hold=None):
    cash=48.0; pos=None; cl=0; w=0; vol=0.0; h=[]; vols=[]; pk=48.0; mdd=0.0; rejected=0
    for c in candles:
        ts=int(c["start"]); hi=float(c["high"]); lo=float(c["low"]); close=float(c["close"])
        cvol=float(c.get("volume", 1.0))
        h.append(close); vols.append(cvol)
        if len(h)>100: h.pop(0); vols.pop(0)
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
            exited=False
            if hi>=pos["tp"]:
                exit_p=pos["tp"]; w+=1; exited=True
            elif max_hold and pos["h"]>=max_hold:
                exit_p=close; exited=True
                if exit_p>pos["ep"]: w+=1
            if exited:
                u=pos["q"]/pos["ep"]
                pnl=(exit_p-pos["ep"])*u-(pos["q"]*fr)-(exit_p*u*fr)
                cash+=pos["q"]+pnl; vol+=pos["q"]+exit_p*u; cl+=1
                if cash>pk: pk=cash
                dd=(pk-cash)/pk
                if dd>mdd: mdd=dd
                pos=None
        if pos is None and cash>=10 and boc and len(h)>=rp+2:
            rv=rsi(h[:-1],rp)
            # Volume filter
            med_vol = sorted(vols[:-1])[len(vols[:-1])//2] if len(vols)>10 else 1
            vol_ok = cvol >= med_vol * vol_mult
            if rv<=re and vol_ok:
                ep=float(c["open"]); tq=cash
                if tq>=10:
                    pos={"ep":ep,"q":tq,"h":0,"tp":ep*(1+tp/100)}
                    cash-=tq
            elif rv<=re:
                rejected += 1
    if pos: cash+=pos["q"]
    net=cash-48; wr2=w/max(1,cl)*100
    return {"net":round(net,2),"rpct":round(net/48*100,1),"trades":cl,"wr":round(wr2,1),
            "avg":round(net/max(1,cl),2),"mdd":round(mdd*100,2),"vol":round(vol,2),
            "rejected":rejected}

def main():
    client = CoinbaseAdvancedClient()
    now = int(time.time())
    s60 = now-60*24*3600
    s30 = now-30*24*3600

    print("Fetching RAVE M5 (60d)...")
    rave60 = fetch(client, PRODUCT, s60, now)
    rave30 = [c for c in rave60 if int(c["start"]) >= s30]
    print(f"  60d: {len(rave60)}, 30d: {len(rave30)}")
    print("Fetching BTC M5...")
    btc60 = fetch(client, BTC, s60, now)
    btc60_lk = {int(c["start"]): float(c["close"]) for c in btc60}
    btc30_lk = {k:v for k,v in btc60_lk.items() if k >= s30}

    results = {}

    # 60d sweep
    print(f"\n🔬 60d Volume-Filtered Sweep:")
    for vm in [0, 1.0, 1.5, 2.0, 2.5, 3.0, 4.0, 5.0]:
        r = bt_volume_filtered(rave60, btc60_lk, 3, 30, 25, vm)
        r["label"] = f"Vol>{vm}x"
        results[f"60d_vol{vm}"] = r
        print(f"   Vol>{vm}x: ${r['net']:.2f} ({r['rpct']}%), {r['trades']}t, {r['wr']}% WR, DD={r['mdd']}%, rejected={r['rejected']}")

    # 30d sweep
    print(f"\n🔬 30d Volume-Filtered Sweep:")
    for vm in [0, 1.0, 1.5, 2.0, 2.5, 3.0, 4.0, 5.0]:
        r = bt_volume_filtered(rave30, btc30_lk, 3, 30, 25, vm)
        r["label"] = f"Vol>{vm}x"
        results[f"30d_vol{vm}"] = r
        print(f"   Vol>{vm}x: ${r['net']:.2f} ({r['rpct']}%), {r['trades']}t, {r['wr']}% WR, DD={r['mdd']}%, rejected={r['rejected']}")

    # RSI(3)<20 TP50 with volume filter (the per-coin opt champion)
    print(f"\n🔬 RSI(3)<20 TP50 with Volume Filter (60d):")
    for vm in [0, 2.0, 3.0, 4.0]:
        r = bt_volume_filtered(rave60, btc60_lk, 3, 20, 50, vm)
        r["label"] = f"RSI<20 TP50 Vol>{vm}x"
        results[f"rsi20_tp50_vol{vm}"] = r
        print(f"   Vol>{vm}x: ${r['net']:.2f} ({r['rpct']}%), {r['trades']}t, {r['wr']}% WR, DD={r['mdd']}%")

    # Summary
    print(f"\n{'='*80}")
    print(f"🏗️ VOLUME-FILTERED RESULTS")
    print(f"{'='*80}")
    best_60 = max((r for k,r in results.items() if k.startswith("60d")), key=lambda x: x["net"])
    best_30 = max((r for k,r in results.items() if k.startswith("30d")), key=lambda x: x["net"])
    print(f"Best 60d: {best_60['label']} -> ${best_60['net']:.2f}, {best_60['trades']}t, {best_60['wr']}% WR")
    print(f"Best 30d: {best_30['label']} -> ${best_30['net']:.2f}, {best_30['trades']}t, {best_30['wr']}% WR")

    with open("reports/volume_filtered_results.json", "w") as f:
        json.dump(results, f, indent=2)
    print(f"\nSaved to reports/volume_filtered_results.json")

if __name__ == "__main__":
    main()
