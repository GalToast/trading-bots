"""Combined System: RSI Mean Rev + Momentum Breakout across ALL profitable coins."""
import json, time, sys, os
from datetime import datetime, timezone
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from coinbase_advanced_client import CoinbaseAdvancedClient

BTC = "BTC-USD"
# Full universe - find which coins work for Momentum Breakout
COINS = [
    "RAVE-USD", "FARTCOIN-USD", "FET-USD", "IOTX-USD", "BAL-USD",
    "BLUR-USD", "ALEPH-USD", "PEPE-USD", "WIF-USD", "TRUMP-USD",
    "RENDER-USD", "DOGE-USD", "BONK-USD", "MOG-USD", "POPCAT-USD",
    "SEI-USD", "TIA-USD", "WLD-USD", "STX-USD", "IMX-USD",
    "ONDO-USD", "PENDLE-USD", "INJ-USD", "NEAR-USD", "APT-USD",
    "SUI-USD", "ARB-USD", "OP-USD", "AVAX-USD", "LINK-USD",
    "AAVE-USD", "FIL-USD", "UNI-USD", "SOL-USD", "XRP-USD",
    "MATIC-USD"
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

def bt_rsi_mean_rev(candles, btc_lk, cash_start=48.0):
    """RAVE champion: RSI(3)<30, TP25, no SL, no timeout."""
    cash=cash_start; pos=None; cl=0; w=0; vol=0.0; h=[]; pk=cash_start; mdd=0.0
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
                exit_p=pos["tp"]; w+=1
                u=pos["q"]/pos["ep"]
                pnl=(exit_p-pos["ep"])*u-(pos["q"]*fr)-(exit_p*u*fr)
                cash+=pos["q"]+pnl; vol+=pos["q"]+exit_p*u; cl+=1
                if cash>pk: pk=cash
                dd=(pk-cash)/pk
                if dd>mdd: mdd=dd
                pos=None
        if pos is None and cash>=10 and boc and len(h)>=5:
            rv=rsi(h[:-1],3)
            if rv<30:
                ep=float(c["open"]); tq=cash
                if tq>=10:
                    pos={"ep":ep,"q":tq,"h":0,"tp":ep*1.25}
                    cash-=tq
    if pos: cash+=pos["q"]
    net=cash-cash_start; wr2=w/max(1,cl)*100
    return {"net":round(net,2),"rpct":round(net/cash_start*100,1),"trades":cl,"wr":round(wr2,1),
            "avg":round(net/max(1,cl),2),"mdd":round(mdd*100,2),"vol":round(vol,2)}

def bt_momentum_breakout(candles, btc_lk, cash_start=48.0):
    """Buy when price breaks above 20-bar high, TP10, SL5, max 30 bars."""
    cash=cash_start; pos=None; cl=0; w=0; vol=0.0; h=[]; pk=cash_start; mdd=0.0
    for i, c in enumerate(candles):
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
            exited=False
            tp=pos["ep"]*1.10
            sl=pos["ep"]*0.95
            if hi>=tp: exit_p=tp; w+=1; exited=True
            elif lo<=sl: exit_p=sl; exited=True
            elif pos["h"]>=30: exit_p=close; exited=True
            if exited:
                u=pos["q"]/pos["ep"]
                pnl=(exit_p-pos["ep"])*u-(pos["q"]*fr)-(exit_p*u*fr)
                cash+=pos["q"]+pnl; vol+=pos["q"]+exit_p*u; cl+=1
                if cash>pk: pk=cash
                dd=(pk-cash)/pk
                if dd>mdd: mdd=dd
                pos=None
        if pos is None and cash>=10 and boc and i >= 21:
            recent_high = max(float(candles[j]["high"]) for j in range(i-21, i))
            if close > recent_high:
                ep=float(c["open"]); tq=cash
                if tq>=10:
                    pos={"ep":ep,"q":tq,"h":0,"tp":ep*1.10,"sl":ep*0.95}
                    cash-=tq
    if pos: cash+=pos["q"]
    net=cash-cash_start; wr2=w/max(1,cl)*100
    return {"net":round(net,2),"rpct":round(net/cash_start*100,1),"trades":cl,"wr":round(wr2,1),
            "avg":round(net/max(1,cl),2),"mdd":round(mdd*100,2),"vol":round(vol,2)}

def main():
    client = CoinbaseAdvancedClient()
    now = int(time.time())
    s30 = now-30*24*3600

    print("Fetching BTC M5...")
    btc = fetch(client, BTC, s30, now)
    btc_lk = {int(c["start"]): float(c["close"]) for c in btc}

    # Phase 1: Momentum Breakout scan across ALL coins
    print(f"\n🔬 Phase 1: Momentum Breakout scan across {len(COINS)} coins (30d)")
    mb_results = []
    for i, coin in enumerate(COINS):
        if i % 10 == 0: print(f"  {i}/{len(COINS)}...")
        try:
            candles = fetch(client, coin, s30, now)
            if len(candles) < 50: continue
            r = bt_momentum_breakout(candles, btc_lk)
            r["coin"] = coin
            mb_results.append(r)
            flag = "🔥" if r["net"] > 20 else "✅" if r["net"] > 0 else "❌"
            print(f"    {flag} {coin}: ${r['net']:.2f} ({r['rpct']}%), {r['trades']}t, {r['wr']}% WR, DD={r['mdd']}%")
        except Exception as e:
            pass

    mb_results.sort(key=lambda x: x["net"], reverse=True)

    # Phase 2: Combined system on coins where BOTH strategies work
    print(f"\n🔬 Phase 2: Combined system (RSI MR + Momentum Breakout)")
    # RSI Mean Rev on RAVE
    rave_candles = [r for r in mb_results if r["coin"] == "RAVE-USD"]
    if rave_candles:
        rave_data = fetch(client, "RAVE-USD", s30, now)
        rsi_rave = bt_rsi_mean_rev(rave_data, btc_lk)
        print(f"  RSI Mean Rev on RAVE: ${rsi_rave['net']:.2f}, {rsi_rave['trades']}t, {rsi_rave['wr']}% WR, DD={rsi_rave['mdd']}%")

        mb_rave = bt_momentum_breakout(rave_data, btc_lk)
        print(f"  Momentum Breakout on RAVE: ${mb_rave['net']:.2f}, {mb_rave['trades']}t, {mb_rave['wr']}% WR, DD={mb_rave['mdd']}%")

        # Combined: $48 for RSI MR, $48 for each Momentum Breakout coin
        profitable_mb = [r for r in mb_results if r["net"] > 5]
        total_capital = 48 + 48 * len(profitable_mb)  # RSI MR + each MB coin
        total_profit = rsi_rave["net"] + sum(r["net"] for r in profitable_mb)
        total_trades = rsi_rave["trades"] + sum(r["trades"] for r in profitable_mb)

        print(f"\n{'='*80}")
        print(f"🏆 COMBINED SYSTEM")
        print(f"{'='*80}")
        print(f"  RSI Mean Rev on RAVE: ${rsi_rave['net']:.2f}, {rsi_rave['trades']}t, {rsi_rave['wr']}% WR, DD={rsi_rave['mdd']}%")
        for r in profitable_mb[:5]:
            print(f"  Momentum Breakout on {r['coin']}: ${r['net']:.2f}, {r['trades']}t, {r['wr']}% WR, DD={r['mdd']}%")
        print(f"\n  TOTAL CAPITAL: ${total_capital} ({1 + len(profitable_mb)} × $48)")
        print(f"  TOTAL PROFIT: ${total_profit:.2f}/30d")
        print(f"  TOTAL TRADES: {total_trades}")
        print(f"  RETURN ON CAPITAL: {total_profit/total_capital*100:.1f}%/30d")
        print(f"  PROJECTED MONTHLY: ${total_profit:.2f}")
        print(f"  Per-$48 efficiency: ${total_profit/total_capital*48:.2f}/month")

    # Phase 3: Per-coin comparison
    print(f"\n{'='*80}")
    print(f"📊 MOMENTUM BREAKOUT — ALL COINS RANKED")
    print(f"{'='*80}")
    for i, r in enumerate(mb_results[:15]):
        print(f"  {i+1:>3}. {r['coin']:<20} ${r['net']:>8.2f} ({r['rpct']:>6.1f}%), {r['trades']:>3}t, {r['wr']:>5.1f}% WR, DD={r['mdd']:.1f}%")

    with open("reports/combined_system.json", "w") as f:
        json.dump({"mb_scan": mb_results, "rsi_rave": rsi_rave if rave_candles else None,
                    "mb_rave": mb_rave if rave_candles else None}, f, indent=2)
    print(f"\nSaved to reports/combined_system.json")

if __name__ == "__main__":
    main()
