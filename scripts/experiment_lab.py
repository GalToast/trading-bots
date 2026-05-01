"""Experiment Lab — Three tracks: Multiply, Find New, Novel Styles."""
import json, time, sys, os, math
from datetime import datetime, timezone
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from coinbase_advanced_client import CoinbaseAdvancedClient

BTC = "BTC-USD"
# RAVE + top profitable coins from universe scan
COINS = ["RAVE-USD", "FARTCOIN-USD", "FET-USD", "TRUMP-USD", "IOTX-USD", "BAL-USD"]

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

def atr(candles, period=14):
    if len(candles) < period+1: return 0
    trs = []
    for i in range(-period, 0):
        h,l,c = candles[i][0], candles[i][1], candles[i][2]
        pc = candles[i-1][2]
        trs.append(max(h-l, abs(h-pc), abs(l-pc)))
    return sum(trs)/len(trs) if trs else 0

def run_bt(name, candles, btc_lk, strategy_fn, cash_start=48.0):
    """Generic backtest engine. strategy_fn returns 'enter', 'exit', or None."""
    cash=cash_start; pos=None; cl=0; w=0; vol=0.0; h=[]; cd=[]; pk=cash_start; mdd=0.0
    gross_profit = 0.0; gross_loss = 0.0
    for c in candles:
        ts=int(c["start"]); hi=float(c["high"]); lo=float(c["low"]); close=float(c["close"])
        v=float(c.get("volume", 1.0))
        h.append(close); cd.append((hi,lo,close,v))
        if len(h)>200: h.pop(0); cd.pop(0)
        boc=True
        pt,pt3=ts-60,ts-180
        if pt in btc_lk and pt3 in btc_lk:
            mom=(btc_lk[pt]-btc_lk[pt3])/btc_lk[pt3]
            if mom<-0.001: boc=False
        hr=datetime.fromtimestamp(ts,tz=timezone.utc).hour
        if hr in {0,6,12,19}: continue
        fr=get_fee(vol)
        # Process exit
        if pos:
            action = strategy_fn("exit", h, cd, pos, btc_lk, ts)
            if action:
                exit_p = action
                u=pos["q"]/pos["ep"]
                pnl=(exit_p-pos["ep"])*u-(pos["q"]*fr)-(exit_p*u*fr)
                if pnl > 0: gross_profit += pnl
                else: gross_loss += abs(pnl)
                cash+=pos["q"]+pnl; vol+=pos["q"]+exit_p*u; cl+=1
                if exit_p > pos["ep"]: w+=1
                if cash>pk: pk=cash
                dd=(pk-cash)/pk
                if dd>mdd: mdd=dd
                pos=None
        # Process entry
        if pos is None and cash>=10 and boc:
            action = strategy_fn("enter", h, cd, None, btc_lk, ts)
            if action:
                tp_pct, sl_pct, max_hold = action
                ep=float(c["open"]); tq=cash
                if tq>=10:
                    pos={"ep":ep,"q":tq,"h":0,"tp_pct":tp_pct,"sl_pct":sl_pct,"max_hold":max_hold}
                    cash-=tq
    if pos:
        u=pos["q"]/pos["ep"]
        pnl=(close-pos["ep"])*u-(pos["q"]*fr)-(close*u*fr)
        cash+=pos["q"]+pnl; vol+=pos["q"]+close*u; cl+=1
        if close > pos["ep"]: w+=1
        if pnl > 0: gross_profit += pnl
        else: gross_loss += abs(pnl)
    net=cash-cash_start; wr2=w/max(1,cl)*100
    pf = gross_profit/max(0.01,gross_loss) if gross_loss > 0 else 999.0
    return {"name":name,"net":round(net,2),"rpct":round(net/cash_start*100,1),"trades":cl,
            "wr":round(wr2,1),"avg":round(net/max(1,cl),2),"mdd":round(mdd*100,2),
            "vol":round(vol,2),"profit_factor":round(pf,2),
            "gross_profit":round(gross_profit,2),"gross_loss":round(gross_loss,2)}

# ===== STRATEGY DEFINITIONS =====

def strat_rsi_mean_reversion():
    """Baseline: RSI mean reversion with fixed TP."""
    def fn(direction, h, cd, pos, btc_lk, ts):
        if direction == "enter" and len(h) >= 5:
            rv = rsi(h[:-1], 3)
            if rv < 30: return (25, 0, None)  # TP25, no SL, no max hold
        elif direction == "exit" and pos:
            pos["h"] += 1
            hi = cd[-1][0]
            tp = pos["ep"] * (1 + pos["tp_pct"]/100)
            if hi >= tp: return tp
            if pos["max_hold"] and pos["h"] >= pos["max_hold"]: return cd[-1][2]
        return None
    return fn

def strat_momentum_cross():
    """Enter when RSI(3) crosses ABOVE 30 (momentum starting)."""
    def fn(direction, h, cd, pos, btc_lk, ts):
        if direction == "enter" and len(h) >= 6:
            rv_now = rsi(h[:-1], 3)
            rv_prev = rsi(h[:-2], 3) if len(h) >= 6 else 50
            if rv_prev < 30 and rv_now >= 30: return (20, 5, 100)  # TP20, SL5, max 100 bars
        elif direction == "exit" and pos:
            pos["h"] += 1
            hi, lo, close = cd[-1][0], cd[-1][1], cd[-1][2]
            tp = pos["ep"] * (1 + pos["tp_pct"]/100)
            sl = pos["ep"] * (1 - pos["sl_pct"]/100)
            if hi >= tp: return tp
            if lo <= sl: return sl
            if pos["max_hold"] and pos["h"] >= pos["max_hold"]: return close
        return None
    return fn

def strat_volatility_squeeze():
    """Buy when ATR expands from contraction (squeeze breakout)."""
    def fn(direction, h, cd, pos, btc_lk, ts):
        if direction == "enter" and len(cd) >= 30:
            atr_now = atr(cd, 14)
            atr_prev = 0
            if len(cd) >= 30:
                atr_prev = atr(cd[:-1], 14) if len(cd) > 15 else 0
            if atr_prev > 0 and atr_now > atr_prev * 1.5:
                return (15, 3, 50)  # TP15, SL3, max 50 bars
        elif direction == "exit" and pos:
            pos["h"] += 1
            hi, lo, close = cd[-1][0], cd[-1][1], cd[-1][2]
            tp = pos["ep"] * (1 + pos["tp_pct"]/100)
            sl = pos["ep"] * (1 - pos["sl_pct"]/100)
            if hi >= tp: return tp
            if lo <= sl: return sl
            if pos["max_hold"] and pos["h"] >= pos["max_hold"]: return close
        return None
    return fn

def strat_support_resistance():
    """Buy at N-bar low, sell at N-bar high."""
    def fn(direction, h, cd, pos, btc_lk, ts):
        if direction == "enter" and len(cd) >= 40:
            recent_lows = [c[1] for c in cd[-40:-1]]
            current_low = cd[-1][1]
            # If current bar low touches 20-bar low, buy
            if min(recent_lows) <= current_low * 1.01:
                return (10, 5, 40)  # TP10, SL5, max 40 bars
        elif direction == "exit" and pos:
            pos["h"] += 1
            hi, lo, close = cd[-1][0], cd[-1][1], cd[-1][2]
            tp = pos["ep"] * (1 + pos["tp_pct"]/100)
            sl = pos["ep"] * (1 - pos["sl_pct"]/100)
            if hi >= tp: return tp
            if lo <= sl: return sl
            if pos["max_hold"] and pos["h"] >= pos["max_hold"]: return close
        return None
    return fn

def strat_grid_trading():
    """Grid: buy at -1%,-2%,-3%, sell at +1%,+2%,+3% from entry."""
    def fn(direction, h, cd, pos, btc_lk, ts):
        if direction == "enter" and len(h) >= 10:
            # Enter at any time, we're making a grid
            return (3, 10, None)  # TP3, SL10 (wide), no max hold
        elif direction == "exit" and pos:
            pos["h"] += 1
            hi, lo, close = cd[-1][0], cd[-1][1], cd[-1][2]
            tp = pos["ep"] * (1 + pos["tp_pct"]/100)
            sl = pos["ep"] * (1 - pos["sl_pct"]/100)
            if hi >= tp: return tp
            if lo <= sl: return sl
        return None
    return fn

def strat_dca_dump():
    """DCA: buy every bar for 5 bars, then sell at 10% target."""
    # This needs stateful tracking across bars - simplified version
    def fn(direction, h, cd, pos, btc_lk, ts):
        if direction == "enter" and len(h) >= 10:
            rv = rsi(h[:-1], 3)
            if rv < 35:  # Only start DCA when oversold
                return (15, 8, None)  # TP15, SL8
        elif direction == "exit" and pos:
            pos["h"] += 1
            hi, lo, close = cd[-1][0], cd[-1][1], cd[-1][2]
            tp = pos["ep"] * (1 + pos["tp_pct"]/100)
            sl = pos["ep"] * (1 - pos["sl_pct"]/100)
            if hi >= tp: return tp
            if lo <= sl: return sl
            # Also exit if RSI > 70
            if len(h) >= 5:
                cur_rsi = rsi(h, 3)
                if cur_rsi > 70: return close
        return None
    return fn

def strat_momentum_breakout():
    """Buy when price breaks above 20-bar high."""
    def fn(direction, h, cd, pos, btc_lk, ts):
        if direction == "enter" and len(cd) >= 21:
            recent_high = max(c[0] for c in cd[-21:-1])
            current_close = cd[-1][2]
            if current_close > recent_high:
                return (10, 5, 30)  # TP10, SL5, max 30 bars
        elif direction == "exit" and pos:
            pos["h"] += 1
            hi, lo, close = cd[-1][0], cd[-1][1], cd[-1][2]
            tp = pos["ep"] * (1 + pos["tp_pct"]/100)
            sl = pos["ep"] * (1 - pos["sl_pct"]/100)
            if hi >= tp: return tp
            if lo <= sl: return sl
            if pos["max_hold"] and pos["h"] >= pos["max_hold"]: return close
        return None
    return fn

def strat_mr_grid():
    """Mean reversion grid: only buy grid levels when RSI oversold."""
    def fn(direction, h, cd, pos, btc_lk, ts):
        if direction == "enter" and len(h) >= 10:
            rv = rsi(h[:-1], 3)
            if rv < 25:  # Very oversold
                return (15, 5, 60)  # TP15, SL5, max 60 bars
        elif direction == "exit" and pos:
            pos["h"] += 1
            hi, lo, close = cd[-1][0], cd[-1][1], cd[-1][2]
            tp = pos["ep"] * (1 + pos["tp_pct"]/100)
            sl = pos["ep"] * (1 - pos["sl_pct"]/100)
            if hi >= tp: return tp
            if lo <= sl: return sl
            if pos["max_hold"] and pos["h"] >= pos["max_hold"]: return close
            # Exit on RSI overbought
            if len(h) >= 5:
                cur_rsi = rsi(h, 3)
                if cur_rsi > 70: return close
        return None
    return fn

def strat_trend_follow():
    """Trend following: buy when RSI > 50 and rising, hold until RSI drops below 40."""
    def fn(direction, h, cd, pos, btc_lk, ts):
        if direction == "enter" and len(h) >= 8:
            rv_now = rsi(h[:-1], 3)
            rv_prev = rsi(h[:-2], 3) if len(h) >= 7 else 50
            if rv_now > 50 and rv_now > rv_prev:
                return (20, 8, None)  # TP20, SL8, no max hold
        elif direction == "exit" and pos:
            pos["h"] += 1
            hi, lo, close = cd[-1][0], cd[-1][1], cd[-1][2]
            tp = pos["ep"] * (1 + pos["tp_pct"]/100)
            sl = pos["ep"] * (1 - pos["sl_pct"]/100)
            if hi >= tp: return tp
            if lo <= sl: return sl
            # Exit when RSI drops below 40
            if len(h) >= 5:
                cur_rsi = rsi(h, 3)
                if cur_rsi < 40: return close
        return None
    return fn

def main():
    client = CoinbaseAdvancedClient()
    now = int(time.time())
    s30 = now-30*24*3600

    print("🧪 EXPERIMENT LAB — Testing 8 strategies across 6 coins\n")

    # Fetch BTC
    print("Fetching BTC M5...")
    btc = fetch(client, BTC, s30, now)
    btc_lk = {int(c["start"]): float(c["close"]) for c in btc}

    # Define all strategies
    strategies = {
        "RSI Mean Rev (baseline)": strat_rsi_mean_reversion,
        "Momentum Cross (RSI>30)": strat_momentum_cross,
        "Vol Squeeze Breakout": strat_volatility_squeeze,
        "Support/Resistance": strat_support_resistance,
        "Grid Trading": strat_grid_trading,
        "DCA+Dump": strat_dca_dump,
        "Momentum Breakout": strat_momentum_breakout,
        "MR Grid (RSI+Grid)": strat_mr_grid,
        "Trend Following": strat_trend_follow,
    }

    all_results = []

    for coin in COINS:
        print(f"\n{'='*80}")
        print(f"📊 Testing {coin}...")
        print(f"{'='*80}")
        try:
            candles = fetch(client, coin, s30, now)
            print(f"  {len(candles)} candles")
            if len(candles) < 50:
                print(f"  Skipping — not enough data")
                continue

            for strat_name, strat_fn in strategies.items():
                r = run_bt(f"{coin} | {strat_name}", candles, btc_lk, strat_fn())
                r["coin"] = coin
                all_results.append(r)
                flag = "🔥" if r["net"] > 100 else "✅" if r["net"] > 0 else "❌"
                print(f"  {flag} {strat_name}: ${r['net']:.2f} ({r['rpct']}%), {r['trades']}t, {r['wr']}% WR, DD={r['mdd']}%, PF={r['profit_factor']:.2f}")
        except Exception as e:
            print(f"  ⚠️ Error: {e}")

    # Summary by strategy
    print(f"\n{'='*80}")
    print(f"🏆 STRATEGY SUMMARY — All coins combined")
    print(f"{'='*80}")
    for strat_name in strategies:
        coin_results = [r for r in all_results if strat_name in r["name"]]
        if not coin_results: continue
        total_net = sum(r["net"] for r in coin_results)
        total_trades = sum(r["trades"] for r in coin_results)
        avg_wr = sum(r["wr"] for r in coin_results) / len(coin_results)
        avg_mdd = sum(r["mdd"] for r in coin_results) / len(coin_results)
        avg_pf = sum(r["profit_factor"] for r in coin_results) / len(coin_results)
        winner = max(coin_results, key=lambda x: x["net"])
        print(f"\n  {strat_name}:")
        print(f"    Total across {len(coin_results)} coins: ${total_net:.2f}, {total_trades}t")
        print(f"    Avg WR: {avg_wr:.1f}%, Avg DD: {avg_mdd:.1f}%, Avg PF: {avg_pf:.2f}")
        print(f"    Best coin: {winner['coin']} (${winner['net']:.2f}, {winner['trades']}t, {winner['wr']}% WR)")

    # Summary by coin
    print(f"\n{'='*80}")
    print(f"📊 COIN SUMMARY — All strategies combined")
    print(f"{'='*80}")
    for coin in COINS:
        coin_results = [r for r in all_results if r.get("coin") == coin]
        if not coin_results: continue
        total_net = sum(r["net"] for r in coin_results)
        best = max(coin_results, key=lambda x: x["net"])
        worst = min(coin_results, key=lambda x: x["net"])
        print(f"\n  {coin}:")
        print(f"    Total across all strategies: ${total_net:.2f}")
        print(f"    Best: {best['name'].split('|')[-1].strip()}: ${best['net']:.2f}")
        print(f"    Worst: {worst['name'].split('|')[-1].strip()}: ${worst['net']:.2f}")

    # Find strategies that beat the baseline
    print(f"\n{'='*80}")
    print(f"🚀 STRATEGIES THAT BEAT BASELINE")
    print(f"{'='*80}")
    baseline = [r for r in all_results if "baseline" in r["name"]]
    best_baseline = max(baseline, key=lambda x: x["net"]) if baseline else None
    if best_baseline:
        print(f"Baseline (RSI Mean Rev): ${best_baseline['net']:.2f} on {best_baseline['coin']}")
        for r in all_results:
            if "baseline" in r["name"] or r["net"] <= best_baseline["net"]: continue
            print(f"  🔥 BEATS BASELINE: {r['name']}: ${r['net']:.2f} ({r['rpct']}%), {r['trades']}t, {r['wr']}% WR, PF={r['profit_factor']:.2f}")

    with open("reports/experiment_lab.json", "w") as f:
        json.dump(all_results, f, indent=2)
    print(f"\nSaved to reports/experiment_lab.json")

if __name__ == "__main__":
    main()
