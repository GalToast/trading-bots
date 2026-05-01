"""Shared Bankroll Simulator — RSI MR Sniper + Grinder for fee tier management."""
import json, time, sys, os, math
from datetime import datetime, timezone
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from coinbase_advanced_client import CoinbaseAdvancedClient

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

def get_fee_rate(cumulative_volume):
    """Coinbase fee tiers based on rolling 30-day volume."""
    if cumulative_volume >= 100000: return 0.0010  # 10bps
    elif cumulative_volume >= 50000: return 0.0015   # 15bps
    elif cumulative_volume >= 10000: return 0.0025   # 25bps
    else: return 0.0040  # 40bps

def simulate_shared_bankroll(rave_candles, grinder_candles_list, btc_lk,
                              rsi_threshold=30, tp_pct=25,
                              grinder_spread_pct=0.5, grinder_hold=10,
                              latency_s=2, fill_prob=1.0,
                              cash_start=288.0):
    """
    Shared bankroll simulation:
    - RAVE sniper: RSI(3) < threshold, buy, sell at TP
    - Grinder: when sniper not in position, do round-trips on grinder coins
    - Realistic: latency delay, fill probability
    """
    cash = cash_start
    pos = None  # Sniper position
    grinder_pos = None  # Grinder position
    cumulative_volume = 0.0
    total_trades = 0
    sniper_trades = 0
    sniper_wins = 0
    grinder_trades = 0
    grinder_wins = 0
    peak_cash = cash_start
    max_dd = 0.0
    sniper_pnl = 0.0
    grinder_pnl = 0.0
    fee_history = []

    # Build time-indexed data for all coins
    all_ts = set()
    for c in rave_candles:
        all_ts.add(int(c["start"]))
    for gc in grinder_candles_list:
        for c in gc:
            all_ts.add(int(c["start"]))
    all_ts = sorted(all_ts)

    rave_lookup = {int(c["start"]): c for c in rave_candles}
    grinder_lookups = [{int(c["start"]): c for c in gc} for gc in grinder_candles_list]

    rave_history = []

    for ts in all_ts:
        # Skip dead hours
        hr = datetime.fromtimestamp(ts, tz=timezone.utc).hour
        if hr in {0, 6, 12, 19}: continue

        # BTC gate
        boc = True
        pt, pt3 = ts-60, ts-180
        if pt in btc_lk and pt3 in btc_lk:
            mom = (btc_lk[pt] - btc_lk[pt3]) / btc_lk[pt3]
            if mom < -0.001: boc = False

        fee = get_fee_rate(cumulative_volume)
        fee_history.append({"ts": ts, "fee_bps": round(fee*10000, 1), "vol": round(cumulative_volume, 0)})

        # Update RAVE history
        if ts in rave_lookup:
            c = rave_lookup[ts]
            close = float(c["close"])
            rave_history.append(close)
            if len(rave_history) > 100: rave_history.pop(0)

        # === SNIPER LOGIC ===
        if pos:
            pos["h"] += 1
            if ts in rave_lookup:
                c = rave_lookup[ts]
                hi = float(c["high"])
                tp_price = pos["ep"] * (1 + tp_pct/100)

                # Simulate latency: TP hit only if price exceeds TP by latency buffer
                # With 2s latency, we might miss the exact TP fill
                if hi >= tp_price * (1 + latency_s * 0.001):  # 0.1% per second slippage
                    exit_p = tp_price
                    if fill_prob >= 1.0 or (__import__('random').random() < fill_prob):
                        # Fill confirmed
                        units = pos["q"] / pos["ep"]
                        pnl = (exit_p - pos["ep"]) * units - (pos["q"] * fee) - (exit_p * units * fee)
                        cash += pos["q"] + pnl
                        cumulative_volume += pos["q"] + exit_p * units
                        total_trades += 1
                        sniper_trades += 1
                        sniper_wins += 1
                        sniper_pnl += pnl
                        if cash > peak_cash: peak_cash = cash
                        dd = (peak_cash - cash) / peak_cash
                        if dd > max_dd: max_dd = dd
                        pos = None

            # Safety exit after 200 bars
            if pos and pos["h"] >= 200:
                if ts in rave_lookup:
                    exit_p = float(rave_lookup[ts]["close"])
                    units = pos["q"] / pos["ep"]
                    pnl = (exit_p - pos["ep"]) * units - (pos["q"] * fee) - (exit_p * units * fee)
                    cash += pos["q"] + pnl
                    cumulative_volume += pos["q"] + exit_p * units
                    total_trades += 1
                    sniper_trades += 1
                    if exit_p > pos["ep"]: sniper_wins += 1
                    sniper_pnl += pnl
                    if cash > peak_cash: peak_cash = cash
                    dd = (peak_cash - cash) / peak_cash
                    if dd > max_dd: max_dd = dd
                    pos = None

        # === GRINDER LOGIC (only when sniper is out) ===
        if pos is None and boc and cash >= 10:
            # Check if sniper signal fires
            if len(rave_history) >= 5:
                rv = rsi(rave_history[:-1], 3)
                if rv < rsi_threshold:
                    # Sniper fires — enter sniper position
                    if ts in rave_lookup:
                        c = rave_lookup[ts]
                        ep = float(c["open"])
                        tq = cash  # Full bankroll
                        if tq >= 10:
                            pos = {"ep": ep, "q": tq, "h": 0}
                            cash -= tq
                    continue  # Skip grinder this bar

            # No sniper signal — do grinder round-trip
            if grinder_pos is None:
                # Enter grinder position on first available grinder coin
                for gi, g_lookup in enumerate(grinder_lookups):
                    if ts in g_lookup:
                        c = g_lookup[ts]
                        ep = float(c["open"])
                        # Grinder: buy at open, target spread_pct above
                        tq = cash * 0.5  # Half bankroll for grinder
                        if tq >= 10:
                            grinder_pos = {
                                "ep": ep, "q": tq, "h": 0,
                                "tp": ep * (1 + grinder_spread_pct/100),
                                "sl": ep * (1 - grinder_spread_pct/100 * 2),  # 2x spread as stop
                                "gi": gi
                            }
                            cash -= tq
                        break
            else:
                # Process grinder position
                grinder_pos["h"] += 1
                gi = grinder_pos["gi"]
                if ts in grinder_lookups[gi]:
                    c = grinder_lookups[gi][ts]
                    hi = float(c["high"])
                    lo = float(c["low"])
                    close = float(c["close"])
                    exited = False
                    exit_p = None

                    if hi >= grinder_pos["tp"]:
                        exit_p = grinder_pos["tp"]
                        exited = True
                    elif lo <= grinder_pos["sl"]:
                        exit_p = grinder_pos["sl"]
                        exited = True
                    elif grinder_pos["h"] >= grinder_hold:
                        exit_p = close
                        exited = True

                    if exited:
                        if fill_prob >= 1.0 or (__import__('random').random() < fill_prob):
                            units = grinder_pos["q"] / grinder_pos["ep"]
                            pnl = (exit_p - grinder_pos["ep"]) * units - (grinder_pos["q"] * fee) - (exit_p * units * fee)
                            cash += grinder_pos["q"] + pnl
                            cumulative_volume += grinder_pos["q"] + exit_p * units
                            total_trades += 1
                            grinder_trades += 1
                            if exit_p > grinder_pos["ep"]: grinder_wins += 1
                            grinder_pnl += pnl
                            if cash > peak_cash: peak_cash = cash
                            dd = (peak_cash - cash) / peak_cash
                            if dd > max_dd: max_dd = dd
                        grinder_pos = None

    # Close remaining positions
    if pos:
        last_ts = max(rave_lookup.keys())
        if last_ts in rave_lookup:
            exit_p = float(rave_lookup[last_ts]["close"])
            units = pos["q"] / pos["ep"]
            pnl = (exit_p - pos["ep"]) * units - (pos["q"] * fee) - (exit_p * units * fee)
            cash += pos["q"] + pnl
            cumulative_volume += pos["q"] + exit_p * units
            total_trades += 1
            sniper_trades += 1
            if exit_p > pos["ep"]: sniper_wins += 1
            sniper_pnl += pnl

    if grinder_pos:
        gi = grinder_pos["gi"]
        last_ts = max(grinder_lookups[gi].keys())
        if last_ts in grinder_lookups[gi]:
            exit_p = float(grinder_lookups[gi][last_ts]["close"])
            units = grinder_pos["q"] / grinder_pos["ep"]
            pnl = (exit_p - grinder_pos["ep"]) * units - (grinder_pos["q"] * fee) - (exit_p * units * fee)
            cash += grinder_pos["q"] + pnl
            cumulative_volume += grinder_pos["q"] + exit_p * units
            total_trades += 1
            grinder_trades += 1
            if exit_p > grinder_pos["ep"]: grinder_wins += 1
            grinder_pnl += pnl

    net = cash - cash_start
    sniper_wr = sniper_wins / max(1, sniper_trades) * 100
    grinder_wr = grinder_wins / max(1, grinder_trades) * 100

    return {
        "net": round(net, 2), "rpct": round(net/cash_start*100, 1),
        "total_trades": total_trades, "sniper_trades": sniper_trades, "sniper_wr": round(sniper_wr, 1),
        "grinder_trades": grinder_trades, "grinder_wr": round(grinder_wr, 1),
        "sniper_pnl": round(sniper_pnl, 2), "grinder_pnl": round(grinder_pnl, 2),
        "cumulative_volume": round(cumulative_volume, 0),
        "final_fee_bps": round(get_fee_rate(cumulative_volume)*10000, 1),
        "max_dd": round(max_dd*100, 2), "final_cash": round(cash, 2),
        "fee_history": fee_history[::max(1, len(fee_history)//100)],  # Sample
    }

def main():
    client = CoinbaseAdvancedClient()
    now = int(time.time())
    s30 = now - 30*24*3600

    print("Fetching data (30d)...")
    print("  RAVE...")
    rave = fetch(client, "RAVE-USD", s30, now)
    print(f"    {len(rave)} candles")
    print("  IOTX...")
    iotx = fetch(client, "IOTX-USD", s30, now)
    print(f"    {len(iotx)} candles")
    print("  BAL...")
    bal = fetch(client, "BAL-USD", s30, now)
    print(f"    {len(bal)} candles")
    print("  BTC...")
    btc = fetch(client, BTC, s30, now)
    btc_lk = {int(c["start"]): float(c["close"]) for c in btc}
    print(f"    {len(btc_lk)} candles")

    grinder_coins = [iotx, bal]

    print(f"\n🧪 SHARED BANKROLL SIMULATOR — $288 starting capital")
    print(f"{'='*80}")

    # Test matrix
    configs = [
        {"name": "Sniper Only (baseline)", "grinder_spread": 0, "grinder_hold": 0},
        {"name": "Sniper + Light Grinder (0.5%, 10 bars)", "grinder_spread": 0.5, "grinder_hold": 10},
        {"name": "Sniper + Heavy Grinder (1.0%, 20 bars)", "grinder_spread": 1.0, "grinder_hold": 20},
        {"name": "Sniper + Ultra Grinder (0.25%, 5 bars)", "grinder_spread": 0.25, "grinder_hold": 5},
    ]

    results = []
    for cfg in configs:
        r = simulate_shared_bankroll(
            rave, grinder_coins, btc_lk,
            rsi_threshold=30, tp_pct=25,
            grinder_spread_pct=cfg["grinder_spread"],
            grinder_hold=cfg["grinder_hold"],
            latency_s=2, fill_prob=1.0,
            cash_start=288.0
        )
        r["config"] = cfg["name"]
        results.append(r)

        print(f"\n  {cfg['name']}:")
        print(f"    Net: ${r['net']:.2f} ({r['rpct']}%)")
        print(f"    Sniper: {r['sniper_trades']}t, {r['sniper_wr']}% WR, PnL=${r['sniper_pnl']:.2f}")
        print(f"    Grinder: {r['grinder_trades']}t, {r['grinder_wr']}% WR, PnL=${r['grinder_pnl']:.2f}")
        print(f"    Volume: ${r['cumulative_volume']:,.0f} → {r['final_fee_bps']}bps")
        print(f"    Max DD: {r['max_dd']}%")

    # Best config
    best = max(results, key=lambda x: x["net"])
    print(f"\n{'='*80}")
    print(f"🏆 BEST CONFIG: {best['config']}")
    print(f"   Net: ${best['net']:.2f} ({best['rpct']}%), {best['total_trades']} trades")
    print(f"   Sniper PnL: ${best['sniper_pnl']:.2f}, Grinder PnL: ${best['grinder_pnl']:.2f}")
    print(f"   Fee tier reached: {best['final_fee_bps']}bps at ${best['cumulative_volume']:,.0f} volume")
    print(f"   Max DD: {best['max_dd']}%")

    with open("reports/shared_bankroll_sim.json", "w") as f:
        json.dump(results, f, indent=2)
    print(f"\nSaved to reports/shared_bankroll_sim.json")

if __name__ == "__main__":
    main()
