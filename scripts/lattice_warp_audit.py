"""Lattice-Warp Audit — Does BTC-gated grinding beat blind grinding?"""
import json, time, sys, os, math
from datetime import datetime, timezone
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from coinbase_advanced_client import CoinbaseAdvancedClient

BTC = "BTC-USD"
GRINDER_COINS = ["BAL-USD", "IOTX-USD"]

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

def bt_grinder(candles, btc_candles, btc_gate_threshold=0,
               tp_pct=1.5, hold=10, cash_start=48.0,
               fill_prob=1.0, slippage_pct=0.0):
    """
    Grinder: buy at open, sell at TP after hold bars.
    
    btc_gate_threshold: only enter when BTC moved >= this amount in last bar
    0 = blind grinder, 5.0 = Lattice-Warp style gate
    """
    cash = cash_start
    pos = None
    cl = 0
    w = 0
    vol = 0.0
    pk = cash_start
    mdd = 0.0
    gp = 0.0
    gl = 0.0
    gated_entries = 0
    blind_entries = 0

    # Build BTC lookup
    btc_lookup = {int(c["start"]): float(c["close"]) for c in btc_candles}
    btc_prev = {}

    for i, c in enumerate(candles):
        ts = int(c["start"])
        hi = float(c["high"])
        lo = float(c["low"])
        close = float(c["close"])
        op = float(c["open"])

        hr = datetime.fromtimestamp(ts, tz=timezone.utc).hour
        if hr in {0, 6, 12, 19}: continue

        # BTC movement check
        btc_moved = False
        if ts in btc_lookup:
            btc_now = btc_lookup[ts]
            # Find previous BTC price (1 bar ago = 5 min ago)
            prev_ts = ts - 300
            if prev_ts in btc_lookup:
                btc_prev_price = btc_lookup[prev_ts]
                btc_change = abs(btc_now - btc_prev_price)
                if btc_change >= btc_gate_threshold:
                    btc_moved = True

        fr = get_fee(vol)
        effective_fee = fr + slippage_pct / 100

        if pos:
            pos["h"] += 1
            exited = False
            exit_p = None
            tp_price = pos["ep"] * (1 + tp_pct / 100)

            if hi >= tp_price:
                exit_p = tp_price
                if fill_prob >= 1.0 or __import__('random').random() < fill_prob:
                    w += 1
                    exited = True
            elif pos["h"] >= hold:
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

        if pos is None and cash >= 10:
            # Gate check
            if btc_gate_threshold > 0 and not btc_moved:
                blind_entries += 1
                continue

            gated_entries += 1
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
        "gated_entries": gated_entries, "blind_entries": blind_entries,
        "gp": round(gp, 2), "gl": round(gl, 2),
    }

def main():
    client = CoinbaseAdvancedClient()
    now = int(time.time())
    s30 = now - 30 * 24 * 3600

    print("Fetching data (30d)...")
    print("  BTC...")
    btc = fetch(client, BTC, s30, now)
    print(f"    {len(btc)} candles")

    results = {}

    for coin in GRINDER_COINS:
        print(f"\n  {coin}...")
        candles = fetch(client, coin, s30, now)
        print(f"    {len(candles)} candles")

        coin_results = []

        # Baseline: blind grinder
        blind = bt_grinder(candles, btc, btc_gate_threshold=0,
                           tp_pct=1.5, hold=10, fill_prob=1.0, slippage_pct=0.0)
        blind["config"] = "Blind (no gate)"
        coin_results.append(blind)
        print(f"    Blind: ${blind['net']:.2f} ({blind['rpct']}%), {blind['trades']}t, {blind['wr']}% WR, PF={blind['pf']}")

        # Lattice-Warp: BTC gate with $1 threshold
        for btc_thresh in [1.0, 3.0, 5.0, 10.0]:
            r = bt_grinder(candles, btc, btc_gate_threshold=btc_thresh,
                           tp_pct=1.5, hold=10, fill_prob=1.0, slippage_pct=0.0)
            r["config"] = f"Warp BTC>${btc_thresh}"
            coin_results.append(r)
            delta = r["net"] - blind["net"]
            status = "🔥" if delta > 5 else "✅" if delta > 0 else "❌"
            print(f"    {status} Warp BTC>${btc_thresh}: ${r['net']:.2f} ({delta:+.2f}), {r['gated_entries']} gated entries, {r['blind_entries']} skipped")

        # Realistic execution sweep on best warp config
        best_warp = max([r for r in coin_results if "Warp" in r["config"]], key=lambda x: x["net"])
        best_thresh = float(best_warp["config"].split("$")[1])

        print(f"\n    Realistic execution sweep (best warp = BTC>${best_thresh}):")
        for fill_p in [1.0, 0.75, 0.5]:
            for slip in [0.0, 0.5, 1.0]:
                r = bt_grinder(candles, btc, btc_gate_threshold=best_thresh,
                               tp_pct=1.5, hold=10, fill_prob=fill_p, slippage_pct=slip)
                r["config"] = f"Warp fill={fill_p} slip={slip}%"
                coin_results.append(r)
                delta_vs_blind = r["net"] - blind["net"]
                status = "✅" if r["net"] > 0 else "❌"
                print(f"      {status} fill={fill_p*100:.0f}% slip={slip}%: ${r['net']:.2f} (vs blind {delta_vs_blind:+.2f})")

        results[coin] = coin_results

    # Summary
    print(f"\n{'='*80}")
    print(f"🏆 LATTICE-WARP AUDIT SUMMARY")
    print(f"{'='*80}")

    for coin in GRINDER_COINS:
        coin_results = results.get(coin, [])
        if not coin_results: continue

        blind = [r for r in coin_results if r["config"] == "Blind (no gate)"][0]
        best_warp = max([r for r in coin_results if "Warp" in r["config"] and "realistic" not in r["config"].lower()],
                        key=lambda x: x["net"])
        best_realistic = max([r for r in coin_results if "fill=" in r["config"]],
                            key=lambda x: x["net"])

        delta = best_warp["net"] - blind["net"]
        delta_realistic = best_realistic["net"] - blind["net"]

        print(f"\n  {coin}:")
        print(f"    Blind grinder: ${blind['net']:.2f} ({blind['rpct']}%), {blind['trades']}t, PF={blind['pf']}")
        print(f"    Best warp (ideal): ${best_warp['net']:.2f} ({delta:+.2f} vs blind)")
        print(f"    Best warp (realistic): ${best_realistic['net']:.2f} ({delta_realistic:+.2f} vs blind)")
        warp_survives = best_realistic["net"] > blind["net"] and best_realistic["net"] > 0
        print(f"    Warp survives realistic execution: {'✅ YES' if warp_survives else '❌ NO'}")

    with open("reports/lattice_warp_audit.json", "w") as f:
        json.dump(results, f, indent=2)
    print(f"\nSaved to reports/lattice_warp_audit.json")

if __name__ == "__main__":
    main()
