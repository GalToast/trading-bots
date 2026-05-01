import json, time, sys, os, math
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

def rsi(closes, p=4):
    if len(closes) < p+1: return 50.0
    d = [closes[i]-closes[i-1] for i in range(1, len(closes))]
    g = [x if x>0 else 0 for x in d[-p:]]
    l = [-x if x<0 else 0 for x in d[-p:]]
    ag, al = sum(g)/p, sum(l)/p
    if al > 0: return 100 - 100/(1+ag/al)
    return 100.0

def stoch_rsi(closes, rsi_p=14, stoch_p=14, k_p=3):
    if len(closes) < rsi_p + stoch_p + k_p: return 50.0
    rsi_vals = []
    for i in range(rsi_p, len(closes)+1):
        rsi_vals.append(rsi(closes[:i], rsi_p))
    if len(rsi_vals) < stoch_p: return 50.0
    stoch_window = rsi_vals[-stoch_p:]
    ll, hh = min(stoch_window), max(stoch_window)
    if hh == ll: return 50.0
    return (rsi_vals[-1] - ll) / (hh - ll) * 100

def cci(candles_data, period=20):
    """CCI from list of (high, low, close) tuples."""
    if len(candles_data) < period: return 0.0
    typicals = [(h+l+c)/3 for h,l,c in candles_data[-period:]]
    sma = sum(typicals) / period
    md = sum(abs(t - sma) for t in typicals) / period
    if md == 0: return 0.0
    return (typicals[-1] - sma) / (0.015 * md)

def bb_pctb(closes, period=20, mult=2.0):
    if len(closes) < period: return 0.5
    sma = sum(closes[-period:]) / period
    std = math.sqrt(sum((c-sma)**2 for c in closes[-period:]) / period)
    upper = sma + mult * std
    lower = sma - mult * std
    if upper == lower: return 0.5
    return (closes[-1] - lower) / (upper - lower)

def williams_r(candles_data, period=14):
    if len(candles_data) < period: return -50.0
    hh = max(h for h,l,c in candles_data[-period:])
    ll = min(l for h,l,c in candles_data[-period:])
    if hh == ll: return -50.0
    return (hh - candles_data[-1][2]) / (hh - ll) * -100

def mfi(candles_data, period=14):
    """Money Flow Index."""
    if len(candles_data) < period + 1: return 50.0
    positive_flow, negative_flow = 0.0, 0.0
    for i in range(-period, 0):
        h,l,c = candles_data[i][0], candles_data[i][1], candles_data[i][2]
        tp = (h+l+c)/3
        prev_tp = (candles_data[i-1][0]+candles_data[i-1][1]+candles_data[i-1][2])/3
        raw_money = tp * candles_data[i][3] if len(candles_data[i]) > 3 else tp * 1.0
        if tp > prev_tp: positive_flow += raw_money
        else: negative_flow += raw_money
    if negative_flow == 0: return 100.0
    rs = positive_flow / negative_flow
    return 100 - 100 / (1 + rs)

def get_fee(tv):
    if tv >= 50000: return 0.0015
    elif tv >= 10000: return 0.0025
    else: return 0.0040

def bt_single(candles, btc_lk, signal_func, tp_pct, cash_start=48.0):
    """Backtest with custom signal function. signal_func returns True when to enter."""
    cash=cash_start; pos=None; cl=0; w=0; vol=0.0; h=[]; cd=[]; pk=cash_start; mdd=0.0
    for c in candles:
        ts=int(c["start"]); hi=float(c["high"]); lo=float(c["low"]); close=float(c["close"])
        v=float(c.get("volume", 1.0))
        h.append(close); cd.append((hi,lo,close,v))
        if len(h)>100: h.pop(0); cd.pop(0)
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
        if pos is None and cash>=10 and boc:
            if signal_func(h, [(x[0],x[1],x[2]) for x in cd]):
                ep=float(c["open"]); tq=cash
                if tq>=10:
                    pos={"ep":ep,"q":tq,"h":0,"tp":ep*(1+tp_pct/100)}
                    cash-=tq
    if pos: cash+=pos["q"]
    net=cash-cash_start; wr2=w/max(1,cl)*100
    return {"net":round(net,2),"rpct":round(net/cash_start*100,1),"trades":cl,"wr":round(wr2,1),
            "avg":round(net/max(1,cl),2),"mdd":round(mdd*100,2),"vol":round(vol,2)}

def bt_combo(candles, btc_lk, strategies, cash_start=48.0):
    """Run multiple strategies in parallel, sharing bankroll. First signal gets in."""
    cash=cash_start; pos=None; cl=0; w=0; vol=0.0; h=[]; cd=[]; pk=cash_start; mdd=0.0
    for c in candles:
        ts=int(c["start"]); hi=float(c["high"]); lo=float(c["low"]); close=float(c["close"])
        v=float(c.get("volume", 1.0))
        h.append(close); cd.append((hi,lo,close,v))
        if len(h)>100: h.pop(0); cd.pop(0)
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
        if pos is None and cash>=10 and boc:
            for sig_func, tp in strategies:
                if sig_func(h, [(x[0],x[1],x[2]) for x in cd]):
                    ep=float(c["open"]); tq=cash
                    if tq>=10:
                        pos={"ep":ep,"q":tq,"h":0,"tp":ep*(1+tp/100)}
                        cash-=tq
                    break
    if pos: cash+=pos["q"]
    net=cash-cash_start; wr2=w/max(1,cl)*100
    return {"net":round(net,2),"rpct":round(net/cash_start*100,1),"trades":cl,"wr":round(wr2,1),
            "avg":round(net/max(1,cl),2),"mdd":round(mdd*100,2)}

def main():
    client = CoinbaseAdvancedClient()
    now = int(time.time())
    s30 = now-30*24*3600

    print("Fetching RAVE M5 (30d)...")
    rave5 = fetch(client, PRODUCT, s30, now)
    print(f"  {len(rave5)} candles")
    print("Fetching BTC M5...")
    btc5 = fetch(client, BTC, s30, now)
    btc5_lk = {int(c["start"]): float(c["close"]) for c in btc5}

    # Define signal functions
    def rsi_3_20(h, cd): return len(h)>=5 and rsi(h,3)<20
    def rsi_3_30(h, cd): return len(h)>=5 and rsi(h,3)<30
    def rsi_3_25(h, cd): return len(h)>=5 and rsi(h,3)<25
    def stoch_rsi_14_20(h, cd): return len(h)>=30 and stoch_rsi(h,14,14)<20
    def cci_20_100(h, cd): return len(cd)>=21 and cci(cd,20)<-100
    def cci_20_200(h, cd): return len(cd)>=21 and cci(cd,20)<-200
    def bb_pctl_05(h, cd): return len(h)>=21 and bb_pctb(h,20,2.0)<0.05
    def bb_pctl_01(h, cd): return len(h)>=21 and bb_pctb(h,20,2.0)<0.01
    def williams_80(h, cd): return len(cd)>=15 and williams_r(cd,14)<-80
    def williams_90(h, cd): return len(cd)>=15 and williams_r(cd,14)<-90

    signals = {
        "RSI(3)<20": (rsi_3_20, 50),
        "RSI(3)<25": (rsi_3_25, 30),
        "RSI(3)<30": (rsi_3_30, 25),
        "StochRSI<20": (stoch_rsi_14_20, 25),
        "CCI<-100": (cci_20_100, 25),
        "CCI<-200": (cci_20_200, 30),
        "BB%B<0.05": (bb_pctl_05, 25),
        "BB%B<0.01": (bb_pctl_01, 30),
        "Williams<-80": (williams_80, 25),
        "Williams<-90": (williams_90, 30),
    }

    # Individual signals
    print(f"\n🔬 Individual signals (30d):")
    indiv = {}
    for name, (sig, tp) in signals.items():
        r = bt_single(rave5, btc5_lk, sig, tp)
        r["label"] = name
        indiv[name] = r
        flag = "✅" if r["net"]>0 else "❌"
        print(f"   {flag} {name} TP{tp}: ${r['net']:.2f} ({r['rpct']}%), {r['trades']}t, {r['wr']}% WR, ${r['avg']}/t")

    # Combo: RSI + one other
    print(f"\n🚀 COMBO TEST: RSI(3)<20 TP50 + each other signal")
    baseline_rsi = indiv["RSI(3)<20"]
    combos = []
    for name, (sig, tp) in signals.items():
        if name == "RSI(3)<20": continue
        strategies = [(rsi_3_20, 50), (sig, tp)]
        r = bt_combo(rave5, btc5_lk, strategies)
        r["label"] = f"RSI(3)<20 TP50 + {name} TP{tp}"
        combos.append(r)
        improvement = r["net"] - baseline_rsi["net"]
        flag = "🔥" if improvement > 50 else "✅" if improvement > 0 else "❌"
        print(f"   {flag} {r['label']}: ${r['net']:.2f} ({r['rpct']}%), {r['trades']}t, {r['wr']}% WR (vs baseline +${improvement:.2f})")

    combos.sort(key=lambda x: x["net"], reverse=True)

    # Triple combo: RSI + best 2 others
    print(f"\n🚀 TRIPLE COMBOS:")
    top2 = [c for c in combos[:2]]
    if len(top2) >= 2:
        # Extract signal names from top combos
        for c1_name, (c1_sig, c1_tp) in signals.items():
            if c1_name == "RSI(3)<20": continue
            for c2_name, (c2_sig, c2_tp) in signals.items():
                if c2_name == "RSI(3)<20" or c2_name == c1_name: continue
                strategies = [(rsi_3_20, 50), (c1_sig, c1_tp), (c2_sig, c2_tp)]
                r = bt_combo(rave5, btc5_lk, strategies)
                r["label"] = f"RSI(3)<20 + {c1_name} + {c2_name}"
                improvement = r["net"] - baseline_rsi["net"]
                if improvement > 0:
                    print(f"   ✅ {r['label']}: ${r['net']:.2f}, {r['trades']}t, {r['wr']}% WR (+${improvement:.2f})")

    # Summary
    print(f"\n{'='*80}")
    print(f"🌌 SPACE PROGRAM — INDEPENDENT STRATEGY COMBOS")
    print(f"{'='*80}")
    print(f"Baseline RSI(3)<20 TP50: ${baseline_rsi['net']:.2f} ({baseline_rsi['rpct']}%), {baseline_rsi['trades']}t")
    if combos:
        print(f"Best combo: ${combos[0]['net']:.2f} ({combos[0]['rpct']}%), {combos[0]['trades']}t")
        print(f"Improvement: +${combos[0]['net']-baseline_rsi['net']:.2f} ({(combos[0]['net']/baseline_rsi['net']-1)*100:.1f}%)")

    with open("reports/independent_strategies.json", "w") as f:
        json.dump({"individual": indiv, "combos": combos}, f, indent=2)
    print(f"\nSaved to reports/independent_strategies.json")

if __name__ == "__main__":
    main()
