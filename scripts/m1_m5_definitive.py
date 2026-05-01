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

def get_fee(tv):
    if tv >= 50000: return 0.0015
    elif tv >= 10000: return 0.0025
    else: return 0.0040

def bt(candles, btc_lk, rp, re, tp, max_hold=None):
    """Core backtest with optional max hold limit."""
    cash=48.0; pos=None; cl=0; w=0; vol=0.0; h=[]; pk=48.0; mdd=0.0
    for c in candles:
        ts=int(c["start"]); hi=float(c["high"]); lo=float(c["low"]); close=float(c["close"])
        h.append(close)
        if len(h)>100: h.pop(0)
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
            exited = False
            # TP hit
            if hi>=pos["tp"]:
                exit_p=pos["tp"]; w+=1; exited=True
            # Max hold timeout
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
            if rv<=re:
                ep=float(c["open"]); tq=cash
                if tq>=10:
                    pos={"ep":ep,"q":tq,"h":0,"tp":ep*(1+tp/100)}
                    cash-=tq
    if pos: cash+=pos["q"]
    net=cash-48; wr2=w/max(1,cl)*100
    return {"net":round(net,2),"rpct":round(net/48*100,1),"trades":cl,"wr":round(wr2,1),
            "avg":round(net/max(1,cl),2),"mdd":round(mdd*100,2),"vol":round(vol,2)}

def main():
    client = CoinbaseAdvancedClient()
    now = int(time.time())
    s60 = now-60*24*3600
    s30 = now-30*24*3600
    s11 = now-11*24*3600

    print("Fetching data...")
    # M5 data
    rave5_60 = fetch(client, PRODUCT, s60, now)
    btc5_60 = fetch(client, BTC, s60, now)
    btc5_60_lk = {int(c["start"]): float(c["close"]) for c in btc5_60}
    print(f"  M5 60d: {len(rave5_60)} RAVE, {len(btc5_60_lk)} BTC")

    # M1 data (expensive)
    print("  Fetching M1 60d...")
    rave1_60 = fetch(client, PRODUCT, s60, now, "ONE_MINUTE")
    btc1_60 = fetch(client, BTC, s60, now, "ONE_MINUTE")
    btc1_60_lk = {int(c["start"]): float(c["close"]) for c in btc1_60}
    print(f"  M1 60d: {len(rave1_60)} RAVE, {len(btc1_60_lk)} BTC")

    # Slice to 30d and 11d
    rave5_30 = [c for c in rave5_60 if int(c["start"]) >= s30]
    rave1_30 = [c for c in rave1_60 if int(c["start"]) >= s30]
    btc5_30_lk = {k:v for k,v in btc5_60_lk.items() if k >= s30}
    btc1_30_lk = {k:v for k,v in btc1_60_lk.items() if k >= s30}

    rave5_11 = [c for c in rave5_60 if int(c["start"]) >= s11]
    rave1_11 = [c for c in rave1_60 if int(c["start"]) >= s11]
    btc5_11_lk = {k:v for k,v in btc5_60_lk.items() if k >= s11}
    btc1_11_lk = {k:v for k,v in btc1_60_lk.items() if k >= s11}

    results = {}

    # @qwen-trading's claim: M1 RSI(3)<30 + 54-bar hold + TP25 = $251.61/11d
    print(f"\n🔬 REPLICATING @qwen-trading's M1 claim (11 days)")
    qt_m1_11 = bt(rave1_11, btc1_11_lk, 3, 30, 25, max_hold=54)
    print(f"   M1 RSI(3)<30 + 54-bar hold + TP25: ${qt_m1_11['net']:.2f} ({qt_m1_11['rpct']}%), {qt_m1_11['trades']}t, {qt_m1_11['wr']}% WR")
    print(f"   vs their claim: $251.61, 22t, 88.9% WR")
    results["qwen_trading_replication_11d"] = qt_m1_11

    # Same on M5 for comparison
    qt_m5_11 = bt(rave5_11, btc5_11_lk, 3, 30, 25, max_hold=None)
    print(f"   M5 RSI(3)<30 + TP25 (no hold): ${qt_m5_11['net']:.2f} ({qt_m5_11['rpct']}%), {qt_m5_11['trades']}t, {qt_m5_11['wr']}% WR")
    results["m5_11d"] = qt_m5_11

    # 30-day comparison
    print(f"\n🔬 30-DAY: M1 vs M5 with various hold times")
    hold_times = [None, 24, 36, 48, 54, 72, 100]
    m1_30_r = []
    m5_30_r = []
    for hold in hold_times:
        label = f"hold={hold}" if hold else "no_hold"
        r1 = bt(rave1_30, btc1_30_lk, 3, 30, 25, max_hold=hold)
        r1["label"] = f"M1 {label}"
        m1_30_r.append(r1)
        r5 = bt(rave5_30, btc5_30_lk, 3, 30, 25, max_hold=hold)
        r5["label"] = f"M5 {label}"
        m5_30_r.append(r5)
        print(f"   M1 {label}: ${r1['net']:.2f} ({r1['rpct']}%), {r1['trades']}t, {r1['wr']}% WR, ${r1['avg']}/t")
        print(f"   M5 {label}: ${r5['net']:.2f} ({r5['rpct']}%), {r5['trades']}t, {r5['wr']}% WR, ${r5['avg']}/t")
    results["m1_30d"] = m1_30_r
    results["m5_30d"] = m5_30_r

    # 60-day comparison (the real test)
    print(f"\n🔬 60-DAY: M1 vs M5 (the truth)")
    m1_60_r = []
    m5_60_r = []
    for hold in hold_times:
        label = f"hold={hold}" if hold else "no_hold"
        r1 = bt(rave1_60, btc1_60_lk, 3, 30, 25, max_hold=hold)
        r1["label"] = f"M1 {label}"
        m1_60_r.append(r1)
        r5 = bt(rave5_60, btc5_60_lk, 3, 30, 25, max_hold=hold)
        r5["label"] = f"M5 {label}"
        m5_60_r.append(r5)
        print(f"   M1 {label}: ${r1['net']:.2f} ({r1['rpct']}%), {r1['trades']}t, {r1['wr']}% WR, DD={r1['mdd']}%")
        print(f"   M5 {label}: ${r5['net']:.2f} ({r5['rpct']}%), {r5['trades']}t, {r5['wr']}% WR, DD={r5['mdd']}%")
    results["m1_60d"] = m1_60_r
    results["m5_60d"] = m5_60_r

    # Per-day normalization
    print(f"\n{'='*80}")
    print(f"🚀 M1 vs M5 — THE DEFINITIVE ANSWER")
    print(f"{'='*80}")
    best_m1_60 = max(m1_60_r, key=lambda x: x["net"])
    best_m5_60 = max(m5_60_r, key=lambda x: x["net"])
    print(f"Best M1 60d: {best_m1_60['label']} -> ${best_m1_60['net']:.2f} = ${best_m1_60['net']/60:.2f}/day = ${best_m1_60['net']/2:.2f}/month")
    print(f"Best M5 60d: {best_m5_60['label']} -> ${best_m5_60['net']:.2f} = ${best_m5_60['net']/60:.2f}/day = ${best_m5_60['net']/2:.2f}/month")
    print(f"Winner: {'M1' if best_m1_60['net'] > best_m5_60['net'] else 'M5'} by ${abs(best_m1_60['net']-best_m5_60['net']):.2f}")

    with open("reports/m1_m5_definitive.json", "w") as f:
        json.dump(results, f, indent=2)
    print(f"\nSaved to reports/m1_m5_definitive.json")

if __name__ == "__main__":
    main()
