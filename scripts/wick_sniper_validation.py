"""Wick-Sniper backtest — independently validate @gemini's edge."""
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

def get_fee(tv):
    if tv >= 50000: return 0.0015
    elif tv >= 10000: return 0.0025
    else: return 0.0040

def bt_wick_sniper(candles, btc_lk, wick_pct, exit_pct, max_hold, cash_start=48.0):
    """
    Wick-Sniper: At each candle, place limit buy at open * (1 - wick_pct).
    If candle low reaches that price, fill at limit price.
    Exit at open * (1 + exit_pct) or after max_hold candles.
    """
    cash = cash_start
    pos = None
    cl = 0
    w = 0
    vol = 0.0
    pk = cash_start
    mdd = 0.0
    gross_profit = 0.0
    gross_loss = 0.0

    for i, c in enumerate(candles):
        ts = int(c["start"])
        hi = float(c["high"])
        lo = float(c["low"])
        close = float(c["close"])
        op = float(c["open"])

        # BTC gate (skip during BTC dumps)
        boc = True
        pt, pt3 = ts - 60, ts - 180
        if pt in btc_lk and pt3 in btc_lk:
            mom = (btc_lk[pt] - btc_lk[pt3]) / btc_lk[pt3]
            if mom < -0.001: boc = False

        hr = datetime.fromtimestamp(ts, tz=timezone.utc).hour
        if hr in {0, 6, 12, 19}: continue

        fr = get_fee(vol)

        # Limit buy: if low <= limit_price, fill at limit_price
        limit_price = op * (1 - wick_pct / 100)
        filled = lo <= limit_price

        # Process existing position
        if pos:
            pos["h"] += 1
            exited = False
            exit_p = None

            # Exit at target (open of current candle + exit_pct, or current close)
            target = pos["entry_open"] * (1 + exit_pct / 100)
            if hi >= target:
                exit_p = target
                w += 1
                exited = True
            elif max_hold and pos["h"] >= max_hold:
                exit_p = close
                exited = True
                if exit_p > pos["fill_price"]: w += 1

            if exited:
                u = pos["q"] / pos["fill_price"]
                pnl = (exit_p - pos["fill_price"]) * u - (pos["q"] * fr) - (exit_p * u * fr)
                if pnl > 0: gross_profit += pnl
                else: gross_loss += abs(pnl)
                cash += pos["q"] + pnl
                vol += pos["q"] + exit_p * u
                cl += 1
                if cash > pk: pk = cash
                dd = (pk - cash) / pk
                if dd > mdd: mdd = dd
                pos = None

        # New entry: limit buy
        if pos is None and cash >= 10 and boc and filled:
            fill_price = limit_price
            tq = cash
            if tq >= 10:
                pos = {
                    "fill_price": fill_price,
                    "entry_open": op,
                    "q": tq,
                    "h": 0,
                }
                cash -= tq

    if pos:
        u = pos["q"] / pos["fill_price"]
        exit_p = close
        pnl = (exit_p - pos["fill_price"]) * u - (pos["q"] * fr) - (exit_p * u * fr)
        if pnl > 0: gross_profit += pnl
        else: gross_loss += abs(pnl)
        cash += pos["q"] + pnl
        vol += pos["q"] + exit_p * u
        cl += 1
        if exit_p > pos["fill_price"]: w += 1

    net = cash - cash_start
    wr = w / max(1, cl) * 100
    pf = gross_profit / max(0.01, gross_loss) if gross_loss > 0 else 999.0
    return {
        "net": round(net, 2), "rpct": round(net / cash_start * 100, 1),
        "trades": cl, "wr": round(wr, 1), "avg": round(net / max(1, cl), 2),
        "mdd": round(mdd * 100, 2), "vol": round(vol, 2),
        "profit_factor": round(pf, 2), "gross_profit": round(gross_profit, 2),
        "gross_loss": round(gross_loss, 2),
    }

def main():
    client = CoinbaseAdvancedClient()
    now = int(time.time())
    s30 = now - 30 * 24 * 3600
    s7 = now - 7 * 24 * 3600

    print("Fetching BTC M5 (30d)...")
    btc = fetch(client, BTC, s30, now)
    btc_lk = {int(c["start"]): float(c["close"]) for c in btc}

    results = {}

    # Phase 1: Replicate @gemini's 7-day Wick-Sniper on RAVE
    print(f"\n🔬 Phase 1: 7-Day Wick-Sniper on RAVE (replicating @gemini)")
    rave_7d = [c for c in btc if False]  # Placeholder — need RAVE data
    # Actually fetch RAVE 7d
    rave_7d = fetch(client, "RAVE-USD", s7, now)
    btc_lk_7d = {k: v for k, v in btc_lk.items() if k >= s7}
    print(f"  RAVE 7d: {len(rave_7d)} candles")

    for wick_pct in [1.0, 1.5, 2.0, 2.5, 3.0]:
        for exit_pct in [0.5, 1.0, 1.5, 2.0]:
            r = bt_wick_sniper(rave_7d, btc_lk_7d, wick_pct, exit_pct, max_hold=10)
            r["label"] = f"Wick{wick_pct}%/Exit{exit_pct}%"
            print(f"  {r['label']}: ${r['net']:.2f} ({r['rpct']}%), {r['trades']}t, {r['wr']}% WR, PF={r['profit_factor']:.2f}")

    # Phase 2: 30-day sweep across all coins
    print(f"\n🔬 Phase 2: 30-Day Wick-Sniper Sweep (5 coins × 5 wick depths × 4 exits × 3 hold times)")
    for coin in COINS:
        print(f"\n{'='*60}")
        print(f"📊 {coin} (30d)")
        print(f"{'='*60}")
        try:
            candles = fetch(client, coin, s30, now)
            print(f"  {len(candles)} candles")
            coin_results = []
            for wick_pct in [1.0, 1.5, 2.0, 2.5, 3.0]:
                for exit_pct in [0.5, 1.0, 1.5, 2.0]:
                    for max_hold in [5, 10, 20]:
                        r = bt_wick_sniper(candles, btc_lk, wick_pct, exit_pct, max_hold)
                        r["label"] = f"W{wick_pct}/E{exit_pct}/H{max_hold}"
                        coin_results.append(r)

            coin_results.sort(key=lambda x: x["net"], reverse=True)
            results[coin] = coin_results[:10]

            print(f"  Top 5:")
            for i, r in enumerate(coin_results[:5]):
                print(f"    {i+1}. {r['label']}: ${r['net']:.2f} ({r['rpct']}%), {r['trades']}t, {r['wr']}% WR, PF={r['profit_factor']:.2f}")
            print(f"  Bottom 3:")
            for r in coin_results[-3:]:
                print(f"    ❌ {r['label']}: ${r['net']:.2f} ({r['rpct']}%), {r['trades']}t, {r['wr']}% WR, PF={r['profit_factor']:.2f}")
        except Exception as e:
            print(f"  ⚠️ Error: {e}")
            results[coin] = []

    # Phase 3: Unified system — RSI MR + MB + Wick-Sniper
    print(f"\n🔬 Phase 3: Unified System (sharing $48 bankroll)")
    # This is complex — need to combine three strategies with one bankroll
    # Simplified: allocate % to each
    # For now, just report what we have

    print(f"\n{'='*80}")
    print(f"🏆 WICK-SNIPER SUMMARY")
    print(f"{'='*80}")
    for coin in COINS:
        if results.get(coin):
            best = results[coin][0]
            print(f"  {coin}: {best['label']} → ${best['net']:.2f} ({best['rpct']}%), {best['trades']}t, {best['wr']}% WR, PF={best['profit_factor']:.2f}")

    with open("reports/wick_sniper_validation.json", "w") as f:
        json.dump(results, f, indent=2)
    print(f"\nSaved to reports/wick_sniper_validation.json")

if __name__ == "__main__":
    main()
