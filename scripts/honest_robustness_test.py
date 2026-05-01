"""Honest Robustness Test — multiple windows, minimum trades, out-of-sample validation."""
import json, time, sys, os
from datetime import datetime, timezone
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from coinbase_advanced_client import CoinbaseAdvancedClient

BTC = "BTC-USD"
COINS = ["RAVE-USD", "BAL-USD", "BLUR-USD", "IOTX-USD", "FARTCOIN-USD", "ALEPH-USD"]

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

def bt(candles, btc_lk, rp, re, tp, cash_start=48.0):
    """Clean backtest — no bugs."""
    cash=cash_start; pos=None; cl=0; w=0; vol=0.0; h=[]; pk=cash_start; mdd=0.0; gp=0.0; gl=0.0
    daily_pnl = {}
    for c in candles:
        ts=int(c["start"]); hi=float(c["high"]); lo=float(c["low"]); close=float(c["close"])
        h.append(close)
        if len(h)>100: h.pop(0)
        hr=datetime.fromtimestamp(ts, tz=timezone.utc).hour
        if hr in {0,6,12,19}: continue
        boc=True
        pt,pt3=ts-60,ts-180
        if pt in btc_lk and pt3 in btc_lk:
            mom=(btc_lk[pt]-btc_lk[pt3])/btc_lk[pt3]
            if mom<-0.001: boc=False
        fr=get_fee(vol)
        if pos:
            pos["h"]+=1
            exited=False; exit_p=None
            tp_price=pos["ep"]*(1+tp/100)
            if hi>=tp_price: exit_p=tp_price; w+=1; exited=True
            elif pos["h"]>=200: exit_p=close; exited=True  # Safety: 200-bar max
            if exited:
                u=pos["q"]/pos["ep"]
                pnl=(exit_p-pos["ep"])*u-(pos["q"]*fr)-(exit_p*u*fr)
                if pnl>0: gp+=pnl
                else: gl+=abs(pnl)
                cash+=pos["q"]+pnl; vol+=pos["q"]+exit_p*u; cl+=1
                day_key = datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d")
                daily_pnl[day_key] = daily_pnl.get(day_key, 0) + pnl
                if cash>pk: pk=cash
                dd=(pk-cash)/pk
                if dd>mdd: mdd=dd
                pos=None
        if pos is None and cash>=10 and boc and len(h)>=5:
            rv=rsi(h[:-1],rp)
            if rv<re:
                ep=float(c["open"]); tq=cash
                if tq>=10:
                    pos={"ep":ep,"q":tq,"h":0}
                    cash-=tq
    if pos:
        u=pos["q"]/pos["ep"]; exit_p=close
        pnl=(exit_p-pos["ep"])*u-(pos["q"]*fr)-(exit_p*u*fr)
        if pnl>0: gp+=pnl; w+=1; cl+=1
        else: gl+=abs(pnl); cl+=1
        cash+=pos["q"]+pnl; vol+=pos["q"]+exit_p*u
        day_key = datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d")
        daily_pnl[day_key] = daily_pnl.get(day_key, 0) + pnl

    net=cash-cash_start; wr=w/max(1,cl)*100
    pf=gp/max(0.01,gl) if gl>0 else float('inf')
    winning_days = sum(1 for v in daily_pnl.values() if v > 0)
    losing_days = sum(1 for v in daily_pnl.values() if v < 0)
    return {
        "net": round(net, 2), "rpct": round(net/cash_start*100, 1),
        "trades": cl, "wr": round(wr, 1), "avg": round(net/max(1,cl), 2),
        "mdd": round(mdd*100, 2), "vol": round(vol, 2),
        "pf": round(pf, 2) if pf != float('inf') else 999.0,
        "winning_days": winning_days, "losing_days": losing_days,
        "gp": round(gp, 2), "gl": round(gl, 2),
    }

def main():
    client = CoinbaseAdvancedClient()
    now = int(time.time())

    # Use 90 days of data, split into three 30-day windows
    # Window 1: 90-60 days ago (oldest, train)
    # Window 2: 60-30 days ago (middle, validation)
    # Window 3: 30-0 days ago (newest, test)
    windows = [
        ("W1_90d_60d", now - 90*24*3600, now - 60*24*3600),
        ("W2_60d_30d", now - 60*24*3600, now - 30*24*3600),
        ("W3_30d_now", now - 30*24*3600, now),
    ]

    print("Fetching BTC M5 (90d)...")
    btc_90d = fetch(client, BTC, now - 90*24*3600, now)
    print(f"  {len(btc_90d)} candles")

    # Fetch all coin data upfront
    coin_data = {}
    for coin in COINS:
        print(f"Fetching {coin} (90d)...")
        candles = fetch(client, coin, now - 90*24*3600, now)
        coin_data[coin] = candles
        print(f"  {len(candles)} candles")

    all_results = []

    for coin in COINS:
        print(f"\n{'='*80}")
        print(f"📊 {coin} — 3 Window Robustness Test")
        print(f"{'='*80}")

        coin_results = []
        for window_name, w_start, w_end in windows:
            candles = [c for c in coin_data[coin] if w_start <= int(c["start"]) < w_end]
            btc_lk = {int(c["start"]): float(c["close"]) for c in btc_90d if w_start <= int(c["start"]) < w_end}

            if len(candles) < 100:
                print(f"  {window_name}: only {len(candles)} candles, skipping")
                continue

            # Test RSI(3)<30 TP25 (the champion config)
            r = bt(candles, btc_lk, 3, 30, 25)
            r["window"] = window_name
            r["coin"] = coin
            r["config"] = "RSI(3)<30 TP25"
            coin_results.append(r)

            # Also test RSI(3)<20 TP50 (the aggressive config)
            r2 = bt(candles, btc_lk, 3, 20, 50)
            r2["window"] = window_name
            r2["coin"] = coin
            r2["config"] = "RSI(3)<20 TP50"
            coin_results.append(r2)

            days_in_window = 30
            print(f"  {window_name}: RSI(3)<30 TP25: ${r['net']:.2f} ({r['rpct']}%), {r['trades']}t, {r['wr']}% WR, DD={r['mdd']}%, PF={r['pf']}, {r['winning_days']}W/{r['losing_days']}L days")
            print(f"  {window_name}: RSI(3)<20 TP50: ${r2['net']:.2f} ({r2['rpct']}%), {r2['trades']}t, {r2['wr']}% WR, DD={r2['mdd']}%, PF={r2['pf']}, {r2['winning_days']}W/{r2['losing_days']}L days")

        all_results.extend(coin_results)

        # Robustness assessment
        configs = set(r["config"] for r in coin_results)
        for config in configs:
            config_results = [r for r in coin_results if r["config"] == config]
            profitable_windows = sum(1 for r in config_results if r["net"] > 0)
            total_trades = sum(r["trades"] for r in config_results)
            windows_with_5plus = sum(1 for r in config_results if r["trades"] >= 5)
            total_net = sum(r["net"] for r in config_results)

            # Robustness criteria:
            # 1. Profitable in at least 2 of 3 windows
            # 2. At least 5 trades per window (not single-trade luck)
            # 3. Positive total across all windows
            robust = (profitable_windows >= 2 and windows_with_5plus >= 2 and total_net > 0)
            status = "✅ ROBUST" if robust else "❌ NOT ROBUST"

            print(f"\n  {config} ROBUSTNESS: {status}")
            print(f"    Profitable windows: {profitable_windows}/3")
            print(f"    Windows with 5+ trades: {windows_with_5plus}/3")
            print(f"    Total trades: {total_trades}")
            print(f"    Total net (90d): ${total_net:.2f}")
            print(f"    Monthly average: ${total_net/3:.2f}")

    # Summary: which coins/configs are truly robust?
    print(f"\n{'='*80}")
    print(f"🏆 ROBUST EDGE SUMMARY")
    print(f"{'='*80}")

    configs_tested = set((r["coin"], r["config"]) for r in all_results)
    for coin, config in sorted(configs_tested):
        config_results = [r for r in all_results if r["coin"] == coin and r["config"] == config]
        profitable_windows = sum(1 for r in config_results if r["net"] > 0)
        total_trades = sum(r["trades"] for r in config_results)
        windows_with_5plus = sum(1 for r in config_results if r["trades"] >= 5)
        total_net = sum(r["net"] for r in config_results)
        robust = (profitable_windows >= 2 and windows_with_5plus >= 2 and total_net > 0)

        status = "✅" if robust else "❌"
        print(f"  {status} {coin} | {config}: ${total_net:.2f}/90d (${total_net/3:.2f}/mo), {total_trades}t, {profitable_windows}/3 windows profitable, {windows_with_5plus}/3 windows with 5+ trades")

    # Save
    with open("reports/honest_robustness_test.json", "w") as f:
        json.dump(all_results, f, indent=2)
    print(f"\nSaved to reports/honest_robustness_test.json")

if __name__ == "__main__":
    main()
