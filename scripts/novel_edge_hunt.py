"""Novel Edge Hunt — 10 completely untested strategies × 5 coins = 50 backtests."""
import json, time, sys, os, math
from datetime import datetime, timezone, timedelta
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

def run_bt(name, candles, btc_lk, signal_fn, cash_start=48.0):
    cash=cash_start; pos=None; cl=0; w=0; vol=0.0; pk=cash_start; mdd=0.0; gp=0.0; gl=0.0
    for i, c in enumerate(candles):
        ts=int(c["start"]); hi=float(c["high"]); lo=float(c["low"]); close=float(c["close"])
        op=float(c["open"]); v=float(c.get("volume",1.0))
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
            tp=pos.get("tp"); sl=pos.get("sl"); mh=pos.get("max_hold")
            if tp and hi>=tp: exit_p=tp; w+=1; exited=True
            elif sl and lo<=sl: exit_p=sl; exited=True
            elif mh and pos["h"]>=mh: exit_p=close; exited=True
            if exited:
                u=pos["q"]/pos["fill"]; pnl=(exit_p-pos["fill"])*u-(pos["q"]*fr)-(exit_p*u*fr)
                if pnl>0: gp+=pnl
                else: gl+=abs(pnl)
                cash+=pos["q"]+pnl; vol+=pos["q"]+exit_p*u; cl+=1
                if cash>pk: pk=cash
                dd=(pk-cash)/pk
                if dd>mdd: mdd=dd
                pos=None
        if pos is None and cash>=10 and boc:
            sig = signal_fn(i, candles, ts, op, hi, lo, close, v, btc_lk)
            if sig:
                fill = sig.get("fill", op); tq = cash
                if tq>=10:
                    pos={"fill":fill,"q":tq,"h":0,"tp":sig.get("tp"),"sl":sig.get("sl"),"max_hold":sig.get("max_hold")}
                    cash-=tq
    if pos:
        u=pos["q"]/pos["fill"]; exit_p=close
        pnl=(exit_p-pos["fill"])*u-(pos["q"]*fr)-(exit_p*u*fr)
        if pnl>0: gp+=pnl; cl+=1; w+=1
        else: gl+=abs(pnl); cl+=1
        cash+=pos["q"]+pnl; vol+=pos["q"]+exit_p*u
    net=cash-cash_start; wr=w/max(1,cl)*100
    pf=gp/max(0.01,gl) if gl>0 else 999.0
    return {"name":name,"net":round(net,2),"rpct":round(net/cash_start*100,1),"trades":cl,
            "wr":round(wr,1),"avg":round(net/max(1,cl),2),"mdd":round(mdd*100,2),
            "vol":round(vol,2),"pf":round(pf,2)}

# ===== 10 NOVEL STRATEGIES =====

def strat_gap_fill():
    """Overnight Gap Fill — if price gaps down from previous close, buy expecting fill."""
    def fn(i, candles, ts, op, hi, lo, close, v, btc_lk):
        if i < 1: return None
        prev_close = float(candles[i-1]["close"])
        gap_pct = (op - prev_close) / prev_close * 100
        # Gap down > 1%: buy expecting fill to prev_close
        if gap_pct < -1.0:
            return {"fill": op, "tp": prev_close, "sl": op * 0.95, "max_hold": 20}
        return None
    return fn

def strat_candle_pattern():
    """Candle Pattern Entries — hammer/bullish engulfing as triggers."""
    def fn(i, candles, ts, op, hi, lo, close, v, btc_lk):
        if i < 2: return None
        prev_o, prev_h, prev_l, prev_c = [float(candles[i-1][k]) for k in ["open","high","low","close"]]
        curr_o, curr_h, curr_l, curr_c = op, hi, lo, close
        prev_body = abs(prev_c - prev_o)
        curr_body = abs(curr_c - curr_o)
        # Bullish engulfing
        if prev_c < prev_o and curr_c > curr_o and curr_o <= prev_c and curr_c >= prev_o:
            return {"fill": curr_c, "tp": curr_c * 1.10, "sl": curr_l * 0.98, "max_hold": 30}
        # Hammer (small body at top, long lower wick)
        if curr_body > 0 and (curr_h - max(curr_o, curr_c)) < curr_body * 0.5 and \
           (min(curr_o, curr_c) - curr_l) > curr_body * 2:
            return {"fill": curr_c, "tp": curr_c * 1.08, "sl": curr_l * 0.97, "max_hold": 30}
        return None
    return fn

def strat_vwap_reversion():
    """VWAP Mean Reversion — buy when price deviates >2% below session VWAP."""
    state = {"cum_vp": 0.0, "cum_v": 0.0, "session_start": None}
    def fn(i, candles, ts, op, hi, lo, close, v, btc_lk):
        dt = datetime.fromtimestamp(ts, tz=timezone.utc)
        # Reset at start of each UTC day
        day_start = dt.replace(hour=0, minute=0, second=0, microsecond=0)
        day_ts = int(day_start.timestamp())
        if state["session_start"] != day_ts:
            state["cum_vp"] = 0.0; state["cum_v"] = 0.0; state["session_start"] = day_ts
        state["cum_vp"] += close * v; state["cum_v"] += v
        if state["cum_v"] > 0:
            vwap = state["cum_vp"] / state["cum_v"]
            dev_pct = (close - vwap) / vwap * 100
            if dev_pct < -2.0:
                return {"fill": op, "tp": vwap, "sl": op * 0.95, "max_hold": 24}
        return None
    return fn

def strat_red_candle_bounce():
    """Consecutive Red Candle Bounce — buy after 3+ red candles."""
    def fn(i, candles, ts, op, hi, lo, close, v, btc_lk):
        if i < 3: return None
        red_count = 0
        for j in range(i-1, max(i-10, 0), -1):
            c_data = candles[j]
            if float(c_data["close"]) < float(c_data["open"]): red_count += 1
            else: break
        if red_count >= 3:
            return {"fill": op, "tp": op * 1.10, "sl": lo * 0.97, "max_hold": 20}
        return None
    return fn

def strat_opening_range():
    """Opening Range Breakout — first 1h (12 M5 bars) sets range, trade breakout."""
    state = {"day": None, "range_high": 0, "range_low": float("inf"), "bars": 0}
    def fn(i, candles, ts, op, hi, lo, close, v, btc_lk):
        dt = datetime.fromtimestamp(ts, tz=timezone.utc)
        day = dt.strftime("%Y-%m-%d")
        hour = dt.hour
        if state["day"] != day:
            state["day"] = day; state["range_high"] = 0; state["range_low"] = float("inf"); state["bars"] = 0
        if hour == 0 and state["bars"] < 12:
            state["range_high"] = max(state["range_high"], hi)
            state["range_low"] = min(state["range_low"], lo)
            state["bars"] += 1
            return None
        if state["bars"] >= 12 and hour >= 0:
            if close > state["range_high"]:
                return {"fill": close, "tp": close * 1.10, "sl": state["range_low"] * 0.98, "max_hold": 24}
        return None
    return fn

def strat_fibonacci():
    """Fibonacci Retracement — buy at 38.2%/50%/61.8% of recent swing down."""
    def fn(i, candles, ts, op, hi, lo, close, v, btc_lk):
        if i < 20: return None
        # Find recent swing high and low (last 20 bars)
        recent = [float(candles[j]["high"]) for j in range(max(0,i-20), i)]
        recent_lows = [float(candles[j]["low"]) for j in range(max(0,i-20), i)]
        if not recent or not recent_lows: return None
        swing_high = max(recent)
        swing_low = min(recent_lows)
        swing_size = swing_high - swing_low
        if swing_size <= 0: return None
        fib_382 = swing_high - swing_size * 0.382
        fib_500 = swing_high - swing_size * 0.500
        fib_618 = swing_high - swing_size * 0.618
        # Price at or near a Fib level
        for fib_level in [fib_618, fib_500, fib_382]:
            if abs(close - fib_level) / fib_level < 0.005:
                return {"fill": op, "tp": op * 1.10, "sl": op * 0.95, "max_hold": 30}
        return None
    return fn

def strat_vcp():
    """Volatility Contraction Pattern — buy when range compresses to <25% of avg."""
    def fn(i, candles, ts, op, hi, lo, close, v, btc_lk):
        if i < 20: return None
        ranges = [float(candles[j]["high"]) - float(candles[j]["low"]) for j in range(max(0,i-20), i)]
        if not ranges: return None
        avg_range = sum(ranges) / len(ranges)
        curr_range = hi - lo
        if avg_range > 0 and curr_range < avg_range * 0.25:
            return {"fill": op, "tp": op * 1.12, "sl": op * 0.96, "max_hold": 20}
        return None
    return fn

def strat_relative_strength():
    """Relative Strength vs BTC — buy coin when it holds up during BTC dip."""
    def fn(i, candles, ts, op, hi, lo, close, v, btc_lk):
        if i < 5: return None
        pt, pt3 = ts-60, ts-180
        if pt not in btc_lk or pt3 not in btc_lk: return None
        btc_mom = (btc_lk[pt] - btc_lk[pt3]) / btc_lk[pt3]
        # BTC is dipping but coin is holding
        if btc_mom < -0.002:
            # Check if coin price is above its 10-bar average (relative strength)
            avg_10 = sum(float(candles[j]["close"]) for j in range(max(0,i-10), i)) / min(10, i)
            if close > avg_10:
                return {"fill": op, "tp": op * 1.10, "sl": op * 0.95, "max_hold": 20}
        return None
    return fn

def strat_multi_tf_rsi():
    """Multi-TF RSI Alignment — M5 RSI<30 AND check M15 equivalent (3-bar avg) < 30."""
    def fn(i, candles, ts, op, hi, lo, close, v, btc_lk):
        if i < 15: return None
        # M5 RSI(3)
        closes_m5 = [float(candles[j]["close"]) for j in range(max(0,i-5), i)]
        if len(closes_m5) < 3: return None
        rsi_m5 = rsi(closes_m5, 3)
        # M15 equivalent (average of last 3 M5 closes = 1 M15 candle)
        closes_m15 = [float(candles[j]["close"]) for j in range(max(0,i-15), i, 3)]
        if len(closes_m15) < 3: return None
        rsi_m15 = rsi(closes_m15, 3)
        if rsi_m5 < 30 and rsi_m15 < 30:
            return {"fill": op, "tp": op * 1.15, "sl": op * 0.95, "max_hold": 30}
        return None
    return fn

def strat_time_of_day():
    """Time-of-Day Seasonality — only trade during historically profitable hours."""
    # Based on common crypto patterns: 08-10 UTC (EU open), 14-16 UTC (US open), 18 UTC (power hour)
    profitable_hours = {8, 9, 14, 15, 18}
    def fn(i, candles, ts, op, hi, lo, close, v, btc_lk):
        hr = datetime.fromtimestamp(ts, tz=timezone.utc).hour
        if hr not in profitable_hours: return None
        if i < 5: return None
        # Simple mean reversion: buy if close < open of this bar
        if close < op:
            return {"fill": op, "tp": op * 1.05, "sl": op * 0.97, "max_hold": 12}
        return None
    return fn

def main():
    client = CoinbaseAdvancedClient()
    now = int(time.time())
    s30 = now - 30*24*3600

    strategies = {
        "1_GapFill": strat_gap_fill,
        "2_CandlePattern": strat_candle_pattern,
        "3_VWAPReversion": strat_vwap_reversion,
        "4_RedCandleBounce": strat_red_candle_bounce,
        "5_OpeningRange": strat_opening_range,
        "6_Fibonacci": strat_fibonacci,
        "7_VCP": strat_vcp,
        "8_RelativeStrength": strat_relative_strength,
        "9_MultiTFRSI": strat_multi_tf_rsi,
        "10_TimeOfDay": strat_time_of_day,
    }

    # Fetch BTC
    print("Fetching BTC M5 (30d)...")
    btc = fetch(client, BTC, s30, now)
    btc_lk = {int(c["start"]): float(c["close"]) for c in btc}
    print(f"  {len(btc_lk)} candles")

    all_results = []

    for coin in COINS:
        print(f"\n{'='*80}")
        print(f"📊 {coin} (30d) — 10 novel strategies")
        print(f"{'='*80}")
        try:
            candles = fetch(client, coin, s30, now)
            print(f"  {len(candles)} candles")
            if len(candles) < 50:
                print(f"  Skipping — not enough data")
                continue

            for strat_name, strat_fn in strategies.items():
                r = run_bt(f"{coin} | {strat_name}", candles, btc_lk, strat_fn())
                r["coin"] = coin; r["strat"] = strat_name
                all_results.append(r)
                flag = "🔥" if r["net"] > 30 else "✅" if r["net"] > 0 else "❌"
                print(f"  {flag} {strat_name}: ${r['net']:.2f} ({r['rpct']}%), {r['trades']}t, {r['wr']}% WR, DD={r['mdd']}%, PF={r['pf']:.2f}")
        except Exception as e:
            print(f"  ⚠️ Error: {e}")

    # Summary by strategy
    print(f"\n{'='*80}")
    print(f"🏆 STRATEGY SUMMARY — All coins combined")
    print(f"{'='*80}")
    for strat_name in strategies:
        strat_results = [r for r in all_results if r.get("strat") == strat_name]
        if not strat_results: continue
        total_net = sum(r["net"] for r in strat_results)
        total_trades = sum(r["trades"] for r in strat_results)
        avg_wr = sum(r["wr"] for r in strat_results) / len(strat_results)
        avg_mdd = sum(r["mdd"] for r in strat_results) / len(strat_results)
        avg_pf = sum(r["pf"] for r in strat_results) / len(strat_results)
        best = max(strat_results, key=lambda x: x["net"])
        print(f"\n  {strat_name}:")
        print(f"    Total across {len(strat_results)} coins: ${total_net:.2f}, {total_trades}t")
        print(f"    Avg WR: {avg_wr:.1f}%, Avg DD: {avg_mdd:.1f}%, Avg PF: {avg_pf:.2f}")
        print(f"    Best coin: {best['coin']} (${best['net']:.2f}, {best['trades']}t, {best['wr']}% WR)")

    # Summary by coin
    print(f"\n{'='*80}")
    print(f"📊 COIN SUMMARY — All 10 strategies combined")
    print(f"{'='*80}")
    for coin in COINS:
        coin_results = [r for r in all_results if r.get("coin") == coin]
        if not coin_results: continue
        total_net = sum(r["net"] for r in coin_results)
        best = max(coin_results, key=lambda x: x["net"])
        print(f"  {coin}: Total=${total_net:.2f} | Best: {best['strat']} (${best['net']:.2f})")

    # Strategies that beat $30 threshold
    print(f"\n{'='*80}")
    print(f"🚀 STRATEGIES THAT CLEAR $30/30d THRESHOLD")
    print(f"{'='*80}")
    profitable = [r for r in all_results if r["net"] > 30]
    if profitable:
        profitable.sort(key=lambda x: x["net"], reverse=True)
        for r in profitable:
            print(f"  🔥 {r['coin']} | {r['strat']}: ${r['net']:.2f} ({r['rpct']}%), {r['trades']}t, {r['wr']}% WR, DD={r['mdd']}%, PF={r['pf']:.2f}")
    else:
        print(f"  None cleared $30/30d")

    with open("reports/novel_edge_hunt.json", "w") as f:
        json.dump(all_results, f, indent=2)
    print(f"\nSaved to reports/novel_edge_hunt.json")

if __name__ == "__main__":
    main()
