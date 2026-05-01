"""Hardened RAVE Sniper Audit — Session Gate + Volume Gate (Gulp Shield proxy)."""
import json, time, sys, os, math, random
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

def bt_hardened_sniper(candles, btc_lk, rsi_threshold=30, tp_pct=25, cash_start=48.0,
                        session_gate=True, volume_gate=False, vol_mult=2.0,
                        latency=2, fill_prob=0.75, slippage_pct=1.0):
    """RAVE RSI MR with hardening filters."""
    cash = cash_start
    pos = None
    cl = 0
    w = 0
    vol = 0.0
    h = []
    v_hist = []
    pk = cash_start
    mdd = 0.0
    gp = 0.0
    gl = 0.0
    blocked_session = 0
    blocked_volume = 0

    for i, c in enumerate(candles):
        ts = int(c["start"])
        hi = float(c["high"])
        lo = float(c["low"])
        close = float(c["close"])
        candle_vol = float(c.get("volume", 1.0))

        h.append(close)
        v_hist.append(candle_vol)
        if len(h) > 100: h.pop(0); v_hist.pop(0)

        # Session Gate
        if session_gate:
            hr = datetime.fromtimestamp(ts, tz=timezone.utc).hour
            if hr in {0, 6, 12, 19}:
                continue

        # Volume Gate (Gulp Shield proxy)
        if volume_gate and len(v_hist) >= 20:
            med_vol = sorted(v_hist[:-1])[len(v_hist[:-1])//2]
            if candle_vol > med_vol * vol_mult:
                blocked_volume += 1
                continue

        boc = True
        pt, pt3 = ts - 60, ts - 180
        if pt in btc_lk and pt3 in btc_lk:
            mom = (btc_lk[pt] - btc_lk[pt3]) / btc_lk[pt3]
            if mom < -0.001: boc = False

        fr = get_fee(vol)
        effective_fee = fr + slippage_pct / 100

        if pos:
            pos["h"] += 1
            exited = False
            exit_p = None
            tp_price = pos["ep"] * (1 + tp_pct / 100)

            # Latency simulation
            if hi >= tp_price:
                lat_slip = latency * 0.001
                exit_p = tp_price * (1 - lat_slip)
                if fill_prob >= 1.0 or random.random() < fill_prob:
                    w += 1
                    exited = True
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
                fill = float(c["open"])
                tq = cash
                if tq >= 10:
                    pos = {"ep": float(c["open"]), "fill": fill, "q": tq, "h": 0}
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
        "blocked_session": blocked_session, "blocked_volume": blocked_volume,
        "gp": round(gp, 2), "gl": round(gl, 2),
    }

def main():
    client = CoinbaseAdvancedClient()
    now = int(time.time())
    s30 = now - 30 * 24 * 3600

    print("Fetching data (30d)...")
    print("  RAVE...")
    rave = fetch(client, PRODUCT, s30, now)
    print(f"    {len(rave)} candles")
    print("  BTC...")
    btc = fetch(client, BTC, s30, now)
    btc_lk = {int(c["start"]): float(c["close"]) for c in btc}
    print(f"    {len(btc_lk)} candles")

    random.seed(42)  # Reproducible

    print(f"\n🧪 HARDENED RAVE SNIPER AUDIT — $48 bankroll, realistic execution")
    print(f"{'='*80}")

    # Baseline: no hardening
    baseline = bt_hardened_sniper(rave, btc_lk, session_gate=False, volume_gate=False)
    print(f"\n  BASELINE (no hardening):")
    print(f"    ${baseline['net']:.2f} ({baseline['rpct']}%), {baseline['trades']}t, {baseline['wr']}% WR, DD={baseline['mdd']}%, PF={baseline['pf']}")

    # Session Gate only
    sg = bt_hardened_sniper(rave, btc_lk, session_gate=True, volume_gate=False)
    sg["config"] = "Session Gate"
    print(f"\n  SESSION GATE:")
    print(f"    ${sg['net']:.2f} ({sg['rpct']}%), {sg['trades']}t, {sg['wr']}% WR, DD={sg['mdd']}%, PF={sg['pf']}")
    print(f"    Blocked {sg['blocked_session']} entries during death hours")
    delta = sg["net"] - baseline["net"]
    print(f"    Delta vs baseline: ${delta:+.2f}")

    # Volume Gate only (Gulp Shield proxy)
    for vol_m in [1.5, 2.0, 3.0]:
        vg = bt_hardened_sniper(rave, btc_lk, session_gate=False, volume_gate=True, vol_mult=vol_m)
        vg["config"] = f"Volume Gate >{vol_m}x"
        print(f"\n  VOLUME GATE >{vol_m}x:")
        print(f"    ${vg['net']:.2f} ({vg['rpct']}%), {vg['trades']}t, {vg['wr']}% WR, DD={vg['mdd']}%, PF={vg['pf']}")
        print(f"    Blocked {vg['blocked_volume']} entries during high volume")
        delta = vg["net"] - baseline["net"]
        print(f"    Delta vs baseline: ${delta:+.2f}")

    # Full hardening: Session + Volume
    for vol_m in [1.5, 2.0, 3.0]:
        full = bt_hardened_sniper(rave, btc_lk, session_gate=True, volume_gate=True, vol_mult=vol_m)
        full["config"] = f"Full Hardening (Session + Volume >{vol_m}x)"
        print(f"\n  FULL HARDENING (Session + Volume >{vol_m}x):")
        print(f"    ${full['net']:.2f} ({full['rpct']}%), {full['trades']}t, {full['wr']}% WR, DD={full['mdd']}%, PF={full['pf']}")
        print(f"    Blocked {full['blocked_session']} session + {full['blocked_volume']} volume")
        delta = full["net"] - baseline["net"]
        wr_delta = full["wr"] - baseline["wr"]
        dd_delta = full["mdd"] - baseline["mdd"]
        print(f"    Delta: ${delta:+.2f} net, {wr_delta:+.1f}% WR, {dd_delta:+.1f}% DD")

    # Summary
    print(f"\n{'='*80}")
    print(f"🏆 HARDENING AUDIT SUMMARY")
    print(f"{'='*80}")

    configs = [
        ("Baseline (no hardening)", baseline),
        ("Session Gate", sg),
    ]
    for vol_m in [1.5, 2.0, 3.0]:
        vg = bt_hardened_sniper(rave, btc_lk, session_gate=False, volume_gate=True, vol_mult=vol_m)
        configs.append((f"Volume Gate >{vol_m}x", vg))
    for vol_m in [1.5, 2.0, 3.0]:
        full = bt_hardened_sniper(rave, btc_lk, session_gate=True, volume_gate=True, vol_mult=vol_m)
        configs.append((f"Full (Session + Vol >{vol_m}x)", full))

    best_net = max(configs, key=lambda x: x[1]["net"])
    best_wr = max(configs, key=lambda x: x[1]["wr"])
    best_dd = min(configs, key=lambda x: x[1]["mdd"])

    print(f"\n  Baseline: ${baseline['net']:.2f}, {baseline['wr']}% WR, {baseline['mdd']}% DD")
    print(f"  Best net: {best_net[0]} → ${best_net[1]['net']:.2f}")
    print(f"  Best WR: {best_wr[0]} → {best_wr[1]['wr']}%")
    print(f"  Best DD: {best_dd[0]} → {best_dd[1]['mdd']}%")

    # Does hardening actually help?
    hardening_helps = False
    for name, r in configs[1:]:
        if r["net"] > baseline["net"] and r["wr"] >= baseline["wr"] and r["mdd"] <= baseline["mdd"]:
            hardening_helps = True
            print(f"\n  ✅ {name} improves on baseline: ${r['net']:.2f} vs ${baseline['net']:.2f}, {r['wr']}% vs {baseline['wr']}% WR, {r['mdd']}% vs {baseline['mdd']}% DD")

    if not hardening_helps:
        print(f"\n  ❌ NO hardening config improves net profit AND WR AND DD simultaneously")
        print(f"     Session gate and volume gate reduce risk but also reduce profit")
        print(f"     This is a risk/reward tradeoff, not a free lunch")

    with open("reports/hardened_sniper_audit.json", "w") as f:
        json.dump({k: v for k, v in configs}, f, indent=2)
    print(f"\nSaved to reports/hardened_sniper_audit.json")

if __name__ == "__main__":
    main()
