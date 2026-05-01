"""60-day Momentum Breakout validation — does the edge survive?"""
import json, time, sys, os
from datetime import datetime, timezone
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from coinbase_advanced_client import CoinbaseAdvancedClient

BTC = "BTC-USD"
COINS = ["RAVE-USD", "BAL-USD", "IOTX-USD", "BLUR-USD", "FARTCOIN-USD"]

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

def bt_mb(candles, btc_lk, breakout_period, tp_pct, sl_pct, max_hold, cash_start=48.0):
    cash=cash_start; pos=None; cl=0; w=0; vol=0.0; pk=cash_start; mdd=0.0
    for i, c in enumerate(candles):
        ts=int(c["start"]); hi=float(c["high"]); lo=float(c["low"]); close=float(c["close"])
        boc=True
        pt,pt3=ts-60,ts-180
        if pt in btc_lk and pt3 in btc_lk:
            mom=(btc_lk[pt]-btc_lk[pt3])/btc_lk[pt3]
            if mom<-0.001: boc=False
        hr=datetime.fromtimestamp(ts,tz=timezone.utc).hour
        if hr in {0,6,12,19}: continue
        # Simplified fee
        fr = 0.0040
        if pos:
            pos["h"]+=1
            exited=False
            tp=pos["ep"]*(1+tp_pct/100)
            sl=pos["ep"]*(1-sl_pct/100)
            if hi>=tp: exit_p=tp; w+=1; exited=True
            elif lo<=sl: exit_p=sl; exited=True
            elif max_hold and pos["h"]>=max_hold: exit_p=close; exited=True
            if exited:
                u=pos["q"]/pos["ep"]
                pnl=(exit_p-pos["ep"])*u-(pos["q"]*fr)-(exit_p*u*fr)
                cash+=pos["q"]+pnl; vol+=pos["q"]+exit_p*u; cl+=1
                if cash>pk: pk=cash
                dd=(pk-cash)/pk
                if dd>mdd: mdd=dd
                pos=None
        if pos is None and cash>=10 and boc and i>=breakout_period:
            recent_high = max(float(candles[j]["high"]) for j in range(i-breakout_period, i))
            if close > recent_high:
                ep=float(c["open"]); tq=cash
                if tq>=10:
                    pos={"ep":ep,"q":tq,"h":0}
                    cash-=tq
    if pos: cash+=pos["q"]
    net=cash-cash_start; wr2=w/max(1,cl)*100
    return {"net":round(net,2),"rpct":round(net/cash_start*100,1),"trades":cl,"wr":round(wr2,1),
            "avg":round(net/max(1,cl),2),"mdd":round(mdd*100,2),"vol":round(vol,2)}

def main():
    client = CoinbaseAdvancedClient()
    now = int(time.time())
    s60 = now-60*24*3600

    print("Fetching BTC M5 (60d)...")
    btc = fetch(client, BTC, s60, now)
    btc_lk = {int(c["start"]): float(c["close"]) for c in btc}

    print(f"\n🔬 60-DAY MOMENTUM BREAKOUT — Parameter Sweep")
    results = {}

    for coin in COINS:
        print(f"\n{'='*60}")
        print(f"📊 {coin} (60d)")
        print(f"{'='*60}")
        try:
            candles = fetch(client, coin, s60, now)
            print(f"  {len(candles)} candles")

            coin_results = []
            # LB period sweep
            for lb in [5, 10, 15, 20, 30]:
                for tp in [10, 15, 20]:
                    for sl in [3, 5, 7, 10]:
                        for mh in [20, 30, 50]:
                            r = bt_mb(candles, btc_lk, lb, tp, sl, mh)
                            r["label"] = f"LB{lb}/TP{tp}/SL{sl}/H{mh}"
                            coin_results.append(r)

            coin_results.sort(key=lambda x: x["net"], reverse=True)
            results[coin] = coin_results[:10]

            print(f"  Top 5:")
            for i, r in enumerate(coin_results[:5]):
                print(f"    {i+1}. {r['label']}: ${r['net']:.2f} ({r['rpct']}%), {r['trades']}t, {r['wr']}% WR, DD={r['mdd']}%")
            print(f"  Bottom 3:")
            for r in coin_results[-3:]:
                print(f"    ❌ {r['label']}: ${r['net']:.2f} ({r['rpct']}%), {r['trades']}t, {r['wr']}% WR, DD={r['mdd']}%")
        except Exception as e:
            print(f"  ⚠️ Error: {e}")
            results[coin] = []

    # Summary
    print(f"\n{'='*80}")
    print(f"🏆 60-DAY PARAMETER OPTIMIZATION SUMMARY")
    print(f"{'='*80}")
    for coin in COINS:
        if results.get(coin):
            best = results[coin][0]
            print(f"  {coin}: {best['label']} → ${best['net']:.2f} ({best['rpct']}%), {best['trades']}t, {best['wr']}% WR, DD={best['mdd']}%")

    # Check if top params are consistent across timeframes
    print(f"\n{'='*80}")
    print(f"📊 CONSISTENCY CHECK — Do top params overlap?")
    print(f"{'='*80}")
    for coin in COINS:
        if not results.get(coin): continue
        top5_labels = [r["label"] for r in results[coin][:5]]
        print(f"  {coin}: {', '.join(top5_labels)}")

    with open("reports/mb_60d_paramsweep.json", "w") as f:
        json.dump(results, f, indent=2)
    print(f"\nSaved to reports/mb_60d_paramsweep.json")

if __name__ == "__main__":
    main()
