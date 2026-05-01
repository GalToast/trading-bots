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

def sma(closes, p=50):
    if len(closes) < p: return None
    return sum(closes[-p:]) / p

def get_fee(tv):
    if tv >= 50000: return 0.0015
    elif tv >= 10000: return 0.0025
    else: return 0.0040

def bt_baseline(candles, btc_lk, rp, re, tp):
    """Fixed TP, no SL - the current champion."""
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
            "avg":round(net/max(1,cl),2),"mdd":round(mdd*100,2),"vol":round(vol,2)}

def bt_trailing(candles, btc_lk, rp, re, trail_pct):
    """Trailing stop exit instead of fixed TP."""
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
            # Update peak price since entry
            if close > pos["peak"]: pos["peak"] = close
            # Trailing stop: exit if price drops trail_pct from peak
            trail_price = pos["peak"] * (1 - trail_pct/100)
            if lo <= trail_price:
                exit_p = trail_price; w+=1
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
                    pos={"ep":ep,"q":tq,"h":0,"peak":ep}
                    cash-=tq
    if pos:
        # Close at current price
        u=pos["q"]/pos["ep"]
        pnl=(close-pos["ep"])*u-(pos["q"]*fr)-(close*u*fr)
        cash+=pos["q"]+pnl; vol+=pos["q"]+close*u; cl+=1
        if close > pos["ep"]: w+=1
    net=cash-48; wr2=w/max(1,cl)*100
    return {"net":round(net,2),"rpct":round(net/48*100,1),"trades":cl,"wr":round(wr2,1),
            "avg":round(net/max(1,cl),2),"mdd":round(mdd*100,2),"vol":round(vol,2)}

def bt_trend_filtered(candles, btc_lk, rp, re, tp, sma_period=50):
    """Only enter when SMA is sloping up."""
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
            # Check SMA slope
            sma_curr = sma(h, sma_period)
            sma_prev = sma(h[:-5], sma_period)  # 5 candles ago
            trend_ok = (sma_curr is not None and sma_prev is not None and sma_curr > sma_prev)
            if rv<=re and trend_ok:
                ep=float(c["open"]); tq=cash
                if tq>=10:
                    pos={"ep":ep,"q":tq,"h":0,"tp":ep*(1+tp/100)}
                    cash-=tq
    if pos: cash+=pos["q"]
    net=cash-48; wr2=w/max(1,cl)*100
    return {"net":round(net,2),"rpct":round(net/48*100,1),"trades":cl,"wr":round(wr2,1),
            "avg":round(net/max(1,cl),2),"mdd":round(mdd*100,2),"vol":round(vol,2)}

def bt_volume_weighted(candles, btc_lk, rp, re, tp, vol_mult=1.5):
    """Only enter when volume > median × vol_mult."""
    cash=48.0; pos=None; cl=0; w=0; vol=0.0; h=[]; vols=[]; pk=48.0; mdd=0.0
    for c in candles:
        ts=int(c["start"]); hi=float(c["high"]); lo=float(c["low"]); close=float(c["close"])
        candle_vol = float(c["volume"])
        h.append(close)
        vols.append(candle_vol)
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
            # Volume filter
            med_vol = sorted(vols[:-1])[len(vols[:-1])//2] if len(vols) > 10 else 1
            vol_ok = candle_vol >= med_vol * vol_mult
            if rv<=re and vol_ok:
                ep=float(c["open"]); tq=cash
                if tq>=10:
                    pos={"ep":ep,"q":tq,"h":0,"tp":ep*(1+tp/100)}
                    cash-=tq
    if pos: cash+=pos["q"]
    net=cash-48; wr2=w/max(1,cl)*100
    return {"net":round(net,2),"rpct":round(net/48*100,1),"trades":cl,"wr":round(wr2,1),
            "avg":round(net/max(1,cl),2),"mdd":round(mdd*100,2),"vol":round(vol,2)}

def bt_hold_trend(candles, btc_lk, rp, re, rsi_exit=90, ma_period=200):
    """Enter on RSI dip, exit on RSI>90 or MA cross. No fixed TP."""
    cash=48.0; pos=None; cl=0; w=0; vol=0.0; h=[]; pk=48.0; mdd=0.0
    for c in candles:
        ts=int(c["start"]); hi=float(c["high"]); lo=float(c["low"]); close=float(c["close"])
        h.append(close)
        if len(h)>300: h.pop(0)
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
            exit_p = None
            closed = False
            # RSI overbought exit
            if len(h) >= 4:
                cur_rsi = rsi(h, 4)
                if cur_rsi >= rsi_exit:
                    exit_p = close; closed = True
                    if exit_p > pos["ep"]: w+=1
            # MA cross exit
            if not closed and len(h) >= ma_period:
                ma = sma(h, ma_period)
                if ma is not None and close < ma:
                    exit_p = close; closed = True
                    if exit_p > pos["ep"]: w+=1
            if closed:
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
                    pos={"ep":ep,"q":tq,"h":0}
                    cash-=tq
    if pos:
        u=pos["q"]/pos["ep"]
        pnl=(close-pos["ep"])*u-(pos["q"]*fr)-(close*u*fr)
        cash+=pos["q"]+pnl; vol+=pos["q"]+close*u; cl+=1
        if close > pos["ep"]: w+=1
    net=cash-48; wr2=w/max(1,cl)*100
    return {"net":round(net,2),"rpct":round(net/48*100,1),"trades":cl,"wr":round(wr2,1),
            "avg":round(net/max(1,cl),2),"mdd":round(mdd*100,2),"vol":round(vol,2)}

def bt_scale_in(candles, btc_lk, rp, re_levels, tp):
    """Scale in at multiple RSI levels."""
    cash=48.0; positions=[]; cl=0; w=0; vol=0.0; h=[]; pk=48.0; mdd=0.0
    # positions = list of {"ep":, "q":, "h":, "tp":}
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
        # Check exits for all positions
        to_remove = []
        for i, pos in enumerate(positions):
            pos["h"]+=1
            if hi>=pos["tp"]:
                ep2=pos["tp"]; w+=1
                u=pos["q"]/pos["ep"]
                pnl=(ep2-pos["ep"])*u-(pos["q"]*fr)-(ep2*u*fr)
                cash+=pos["q"]+pnl; vol+=pos["q"]+ep2*u; cl+=1
                if cash>pk: pk=cash
                dd=(pk-cash)/pk
                if dd>mdd: mdd=dd
                to_remove.append(i)
        for i in reversed(to_remove):
            positions.pop(i)
        # Check entries
        if len(positions) < len(re_levels) and cash>=10 and boc and len(h)>=rp+2:
            rv=rsi(h[:-1],rp)
            for re_val, alloc_pct in re_levels:
                if rv <= re_val:
                    # Check if we already have a position at this level
                    already = any(abs(pos["ep"] - float(c["open"])) < 0.001 for pos in positions if pos.get("re") == re_val)
                    if not already and cash * alloc_pct / 100 >= 10:
                        ep=float(c["open"])
                        tq = cash * alloc_pct / 100
                        positions.append({"ep":ep,"q":tq,"h":0,"tp":ep*(1+tp/100),"re":re_val})
                        cash -= tq
                    break
    # Close remaining positions
    for pos in positions:
        u=pos["q"]/pos["ep"]
        pnl=(close-pos["ep"])*u-(pos["q"]*fr)-(close*u*fr)
        cash+=pos["q"]+pnl; vol+=pos["q"]+close*u; cl+=1
        if close > pos["ep"]: w+=1
    net=cash-48; wr2=w/max(1,cl)*100
    return {"net":round(net,2),"rpct":round(net/48*100,1),"trades":cl,"wr":round(wr2,1),
            "avg":round(net/max(1,cl),2),"mdd":round(mdd*100,2),"vol":round(vol,2)}

def main():
    client = CoinbaseAdvancedClient()
    now = int(time.time())
    s30 = now-30*24*3600

    print("Fetching RAVE M5 (30d)...")
    rave5 = fetch(client, PRODUCT, s30, now)
    print(f"  {len(rave5)} candles")

    print("Fetching BTC M1 + M5...")
    btc1 = fetch(client, BTC, s30, now, "ONE_MINUTE")
    btc5 = fetch(client, BTC, s30, now)
    btc1_lk = {int(c["start"]): float(c["close"]) for c in btc1}
    btc5_lk = {int(c["start"]): float(c["close"]) for c in btc5}

    results = {}

    # Baseline
    baseline = bt_baseline(rave5, btc5_lk, 3, 30, 25)
    print(f"\n📊 BASELINE: M5 RSI(3)<30 TP25 = ${baseline['net']:.2f} ({baseline['rpct']}%), {baseline['trades']}t, {baseline['wr']}% WR")
    results["baseline"] = baseline

    # EXP 1: Trailing stops
    print(f"\n🚀 EXP 1: Trailing Stops")
    trail_r = []
    for trail in [5, 10, 15, 20, 25, 30]:
        r = bt_trailing(rave5, btc5_lk, 3, 30, trail)
        r["label"] = f"Trail{trail}%"
        trail_r.append(r)
        print(f"   Trail{trail}%: ${r['net']:.2f} ({r['rpct']}%), {r['trades']}t, {r['wr']}% WR, ${r['avg']}/t")
    trail_r.sort(key=lambda x: x["net"], reverse=True)
    results["trailing"] = trail_r

    # EXP 2: Scale-in
    print(f"\n🚀 EXP 2: Scale-In (Pyramid)")
    scale_configs = [
        ("RSI<30 100%", [(30, 100)]),
        ("RSI<30/20 50/50", [(30, 50), (20, 50)]),
        ("RSI<30/20/10 50/30/20", [(30, 50), (20, 30), (10, 20)]),
        ("RSI<25/15/5 40/35/25", [(25, 40), (15, 35), (5, 25)]),
    ]
    scale_r = []
    for label, re_levels in scale_configs:
        r = bt_scale_in(rave5, btc5_lk, 3, re_levels, 25)
        r["label"] = label
        scale_r.append(r)
        print(f"   {label}: ${r['net']:.2f} ({r['rpct']}%), {r['trades']}t, {r['wr']}% WR")
    results["scale_in"] = scale_r

    # EXP 3: Trend-filtered
    print(f"\n🚀 EXP 3: Trend-Filtered (SMA slope)")
    trend_r = []
    for sma_p in [20, 30, 50, 100]:
        for tp in [20, 25, 30]:
            r = bt_trend_filtered(rave5, btc5_lk, 3, 30, tp, sma_p)
            r["label"] = f"SMA{sma_p}_slope TP{tp}"
            trend_r.append(r)
            print(f"   {r['label']}: ${r['net']:.2f} ({r['rpct']}%), {r['trades']}t, {r['wr']}% WR")
    trend_r.sort(key=lambda x: x["net"], reverse=True)
    results["trend_filtered"] = trend_r

    # EXP 4: Volume-weighted
    print(f"\n🚀 EXP 4: Volume-Weighted Entries")
    vol_r = []
    for vm in [1.2, 1.5, 2.0, 3.0]:
        for tp in [20, 25, 30]:
            r = bt_volume_weighted(rave5, btc5_lk, 3, 30, tp, vm)
            r["label"] = f"Vol>{vm}x TP{tp}"
            vol_r.append(r)
            print(f"   {r['label']}: ${r['net']:.2f} ({r['rpct']}%), {r['trades']}t, {r['wr']}% WR")
    vol_r.sort(key=lambda x: x["net"], reverse=True)
    results["volume_weighted"] = vol_r

    # EXP 5: Hold the trend
    print(f"\n🚀 EXP 5: Hold The Trend (RSI/MA exit only)")
    hold_r = []
    for rsi_ex in [80, 85, 90, 95]:
        for ma_p in [50, 100, 200]:
            r = bt_hold_trend(rave5, btc5_lk, 3, 30, rsi_ex, ma_p)
            r["label"] = f"RSI>{rsi_ex} or MA{ma_p}"
            hold_r.append(r)
            print(f"   {r['label']}: ${r['net']:.2f} ({r['rpct']}%), {r['trades']}t, {r['wr']}% WR, DD={r['mdd']}%")
    hold_r.sort(key=lambda x: x["net"], reverse=True)
    results["hold_trend"] = hold_r

    # Summary
    print(f"\n{'='*80}")
    print(f"🚀 SPACE PROGRAM SUMMARY")
    print(f"{'='*80}")
    print(f"Baseline (fixed TP25):           ${baseline['net']:.2f} ({baseline['rpct']}%)")
    print(f"Best trailing:                   ${trail_r[0]['net']:.2f} ({trail_r[0]['rpct']}%) — {trail_r[0]['label']}")
    print(f"Best scale-in:                   ${scale_r[0]['net']:.2f} ({scale_r[0]['rpct']}%) — {scale_r[0]['label']}")
    print(f"Best trend-filtered:             ${trend_r[0]['net']:.2f} ({trend_r[0]['rpct']}%) — {trend_r[0]['label']}")
    print(f"Best volume-weighted:            ${vol_r[0]['net']:.2f} ({vol_r[0]['rpct']}%) — {vol_r[0]['label']}")
    print(f"Best hold-trend:                 ${hold_r[0]['net']:.2f} ({hold_r[0]['rpct']}%) — {hold_r[0]['label']}")

    all_results = [baseline] + trail_r + scale_r + trend_r + vol_r + hold_r
    best = max(all_results, key=lambda x: x["net"])
    print(f"\n🏆 NEW CEILING: {best.get('label','baseline')} -> ${best['net']:.2f} ({best['rpct']}%)")
    print(f"   Improvement vs baseline: +${best['net']-baseline['net']:.2f} ({(best['net']/baseline['net']-1)*100:.1f}%)")

    with open("reports/space_program.json", "w") as f:
        json.dump(results, f, indent=2)
    print(f"\nSaved to reports/space_program.json")

if __name__ == "__main__":
    main()
