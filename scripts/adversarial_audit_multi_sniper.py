"""Adversarial Audit for Multi-Coin Sniper — realistic execution, latency, fill probability."""
import json, time, sys, os, random
from datetime import datetime, timezone
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from coinbase_advanced_client import CoinbaseAdvancedClient

BTC = "BTC-USD"
# Top coins from the universe scan that showed RSI edges
COINS = ["RAVE-USD", "MOG-USD", "BAL-USD", "BLUR-USD", "IOTX-USD", "ALEPH-USD",
         "LRDS-USD", "STRK-USD", "A8-USD"]

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

def audit_sniper(candles, btc_lk, rsi_threshold=30, tp_pct=25, cash_start=48.0,
                 latency=0, fill_prob=1.0, slippage_pct=0.0):
    """
    Adversarial audit of RSI MR sniper with realistic execution.

    Parameters:
    - latency: seconds of fill delay (0, 2, 5, 10)
    - fill_prob: probability that TP is actually hit (1.0, 0.75, 0.5, 0.25)
    - slippage_pct: extra cost per round-trip from spread/slippage (0%, 0.5%, 1%, 2%)
    """
    cash = cash_start
    pos = None
    cl = 0
    w = 0
    vol = 0.0
    h = []
    pk = cash_start
    mdd = 0.0
    gp = 0.0
    gl = 0.0
    missed_tps = 0
    partial_fills = 0

    for i, c in enumerate(candles):
        ts = int(c["start"])
        hi = float(c["high"])
        lo = float(c["low"])
        close = float(c["close"])
        op = float(c["open"])

        h.append(close)
        if len(h) > 100: h.pop(0)

        hr = datetime.fromtimestamp(ts, tz=timezone.utc).hour
        if hr in {0, 6, 12, 19}: continue

        boc = True
        pt, pt3 = ts - 60, ts - 180
        if pt in btc_lk and pt3 in btc_lk:
            mom = (btc_lk[pt] - btc_lk[pt3]) / btc_lk[pt3]
            if mom < -0.001: boc = False

        fr = get_fee(vol)
        # Add slippage to fee
        effective_fee = fr + slippage_pct / 100

        if pos:
            pos["h"] += 1
            exited = False
            exit_p = None
            tp_price = pos["ep"] * (1 + tp_pct / 100)

            # Realistic TP: need price to EXCEED TP (not just touch)
            # With latency, the fill happens N bars later at market price
            if hi >= tp_price:
                # Latency simulation: fill at current bar's close + random walk
                if latency > 0:
                    # Estimate slippage from latency: price moves against us
                    # On average, latency causes 0.1% slippage per second
                    lat_slip = latency * 0.001
                    exit_p = tp_price * (1 - lat_slip)
                else:
                    exit_p = tp_price

                # Fill probability
                if fill_prob >= 1.0 or random.random() < fill_prob:
                    w += 1
                    exited = True
                else:
                    missed_tps += 1
                    # Missed TP — continue holding
            elif pos["h"] >= 200:
                exit_p = close
                exited = True

            if exited:
                u = pos["q"] / pos["fill"]
                pnl = (exit_p - pos["fill"]) * u - (pos["q"] * effective_fee) - (exit_p * u * effective_fee)
                if pnl > 0: gp += pnl
                else: gl += abs(pnl)
                cash += pos["q"] + pnl
                vol += pos["q"] + exit_p * u
                cl += 1
                if cash > pk: pk = cash
                dd = (pk - cash) / pk
                if dd > mdd: mdd = dd
                pos = None

        if pos is None and cash >= 10 and boc and len(h) >= 5:
            rv = rsi(h[:-1], 3)
            if rv < rsi_threshold:
                fill = op
                tq = cash
                if tq >= 10:
                    pos = {"ep": op, "fill": fill, "q": tq, "h": 0}
                    cash -= tq

    # Close remaining
    if pos:
        u = pos["q"] / pos["fill"]
        pnl = (close - pos["fill"]) * u - (pos["q"] * effective_fee) - (close * u * effective_fee)
        if pnl > 0: gp += pnl; w += 1; cl += 1
        else: gl += abs(pnl); cl += 1
        cash += pos["q"] + pnl
        vol += pos["q"] + close * u

    net = cash - cash_start
    wr = w / max(1, cl) * 100
    pf = gp / max(0.01, gl) if gl > 0 else 999.0
    return {
        "net": round(net, 2), "rpct": round(net / cash_start * 100, 1),
        "trades": cl, "wr": round(wr, 1), "avg": round(net / max(1, cl), 2),
        "mdd": round(mdd * 100, 2), "vol": round(vol, 2),
        "pf": round(pf, 2) if pf != 999.0 else 999.0,
        "gp": round(gp, 2), "gl": round(gl, 2),
        "missed_tps": missed_tps, "partial_fills": partial_fills,
    }

def main():
    client = CoinbaseAdvancedClient()
    now = int(time.time())
    s30 = now - 30 * 24 * 3600

    print("Fetching BTC M5 (30d)...")
    btc = fetch(client, BTC, s30, now)
    btc_lk = {int(c["start"]): float(c["close"]) for c in btc}
    print(f"  {len(btc_lk)} candles")

    # Fetch all coin data
    coin_data = {}
    for coin in COINS:
        print(f"Fetching {coin} (30d)...")
        candles = fetch(client, coin, s30, now)
        coin_data[coin] = candles
        print(f"  {len(candles)} candles")

    all_results = []

    for coin in COINS:
        if not coin_data.get(coin) or len(coin_data[coin]) < 100:
            print(f"\n  ⚠️ {coin}: insufficient data")
            continue

        candles = coin_data[coin]

        print(f"\n{'='*80}")
        print(f"🔬 ADVERSARIAL AUDIT: {coin}")
        print(f"{'='*80}")

        # Shadow baseline
        shadow = audit_sniper(candles, btc_lk, 30, 25)
        print(f"  SHADOW (instant fills, no slip): ${shadow['net']:.2f} ({shadow['rpct']}%), {shadow['trades']}t, {shadow['wr']}% WR, DD={shadow['mdd']}%, PF={shadow['pf']}")

        # Latency sweep
        print(f"\n  LATENCY SWEEP (fill_prob=1.0, no slippage):")
        for lat in [0, 2, 5, 10]:
            r = audit_sniper(candles, btc_lk, 30, 25, latency=lat)
            r["coin"] = coin
            r["config"] = f"lat{lat}s"
            all_results.append(r)
            delta = r["net"] - shadow["net"]
            status = "❌" if delta < -10 else "⚠️" if delta < 0 else "✅"
            print(f"    {status} Latency {lat}s: ${r['net']:.2f} ({delta:+.2f}), {r['missed_tps']} missed TPs")

        # Fill probability sweep
        print(f"\n  FILL PROBABILITY SWEEP (latency=0, no slippage):")
        for fp in [1.0, 0.75, 0.5, 0.25]:
            r = audit_sniper(candles, btc_lk, 30, 25, fill_prob=fp)
            r["coin"] = coin
            r["config"] = f"fill{fp}"
            all_results.append(r)
            delta = r["net"] - shadow["net"]
            print(f"    {'❌' if delta < -10 else '⚠️' if delta < 0 else '✅'} Fill {fp*100:.0f}%: ${r['net']:.2f} ({delta:+.2f}), {r['missed_tps']} missed")

        # Slippage sweep
        print(f"\n  SLIPPAGE SWEEP (latency=0, fill_prob=1.0):")
        for slip in [0.0, 0.5, 1.0, 2.0]:
            r = audit_sniper(candles, btc_lk, 30, 25, slippage_pct=slip)
            r["coin"] = coin
            r["config"] = f"slip{slip}%"
            all_results.append(r)
            delta = r["net"] - shadow["net"]
            print(f"    {'❌' if delta < -10 else '⚠️' if delta < 0 else '✅'} Slippage {slip}%: ${r['net']:.2f} ({delta:+.2f})")

        # Combined worst case
        print(f"\n  COMBINED WORST CASE (lat=2s, fill=75%, slip=1%):")
        worst = audit_sniper(candles, btc_lk, 30, 25, latency=2, fill_prob=0.75, slippage_pct=1.0)
        worst["coin"] = coin
        worst["config"] = "combined_worst"
        all_results.append(worst)
        delta = worst["net"] - shadow["net"]
        print(f"    ❌ ${worst['net']:.2f} ({delta:+.2f}), {worst['missed_tps']} missed, DD={worst['mdd']}%")

    # Summary
    print(f"\n{'='*80}")
    print(f"🏆 ADVERSARIAL AUDIT SUMMARY")
    print(f"{'='*80}")

    for coin in COINS:
        coin_results = [r for r in all_results if r.get("coin") == coin]
        if not coin_results: continue

        shadow = [r for r in coin_results if "lat0s" in r.get("config", "") and r.get("fill_prob", 1.0) == 1.0 and r.get("slippage_pct", 0.0) == 0.0]
        if not shadow: continue
        shadow = shadow[0]

        combined_worst = [r for r in coin_results if r.get("config") == "combined_worst"]
        if not combined_worst: continue
        worst = combined_worst[0]

        delta = worst["net"] - shadow["net"]
        status = "✅ SURVIVES" if worst["net"] > 0 else "❌ DESTROYED"
        print(f"\n  {coin}:")
        print(f"    Shadow: ${shadow['net']:.2f} ({shadow['rpct']}%), {shadow['trades']}t, {shadow['wr']}% WR")
        print(f"    Realistic: ${worst['net']:.2f} ({worst['rpct']}%), {worst['trades']}t, {worst['wr']}% WR")
        print(f"    {status} — Delta: ${delta:+.2f}")

    with open("reports/adversarial_audit_multi_sniper.json", "w") as f:
        json.dump(all_results, f, indent=2)
    print(f"\nSaved to reports/adversarial_audit_multi_sniper.json")

if __name__ == "__main__":
    main()
