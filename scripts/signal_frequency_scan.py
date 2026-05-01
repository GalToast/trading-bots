import json
import time
from datetime import datetime, timezone
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from coinbase_advanced_client import CoinbaseAdvancedClient

BTC = "BTC-USD"

def fetch_candles(client, pid, start, end, granularity="FIVE_MINUTE"):
    chunk_sec = 300 * 5 * 60
    if granularity == "ONE_MINUTE": chunk_sec = 300 * 60
    all_c = []
    cs = start
    while cs < end:
        ce = min(cs + chunk_sec, end)
        try:
            resp = client.market_candles(pid, start=cs, end=ce, granularity=granularity)
            cands = resp.get("candles", [])
            all_c.extend(cands)
            cs = ce
            if not cands: break
            time.sleep(0.05)
        except:
            cs = ce
            time.sleep(0.3)
    all_c.sort(key=lambda c: int(c["start"]))
    return all_c

def compute_rsi(closes, period=4):
    if len(closes) < period + 1: return 50.0
    deltas = [closes[i] - closes[i-1] for i in range(1, len(closes))]
    gains = [d if d > 0 else 0 for d in deltas[-period:]]
    losses = [-d if d < 0 else 0 for d in deltas[-period:]]
    avg_gain = sum(gains) / period
    avg_loss = sum(losses) / period
    if avg_loss > 0:
        rs = avg_gain / avg_loss
        return 100 - 100 / (1 + rs)
    return 100.0

def get_fee_rate(total_volume):
    if total_volume >= 50000: return 0.0015
    elif total_volume >= 10000: return 0.0025
    else: return 0.0040

def run_backtest(candles, btc_lookup, rsi_period, rsi_entry, tp_pct,
                 session_gate_hours=None, btc_gate=True, cash_start=48.0):
    if session_gate_hours is None:
        session_gate_hours = {12, 19, 6, 0}

    cash = cash_start
    pos = None
    closes = 0
    wins = 0
    total_volume = 0.0
    history = []
    peak_cash = cash_start
    max_dd = 0.0

    for i in range(len(candles)):
        c = candles[i]
        ts = int(c["start"])
        h = float(c["high"]); l = float(c["low"]); cl = float(c["close"])

        history.append(cl)
        if len(history) > 50: history.pop(0)

        btc_ok = True
        if btc_gate:
            p_t = ts - 60; p_t3 = ts - 180
            if p_t in btc_lookup and p_t3 in btc_lookup:
                mom = (btc_lookup[p_t] - btc_lookup[p_t3]) / btc_lookup[p_t3]
                if mom < -0.001: btc_ok = False

        hour = datetime.fromtimestamp(ts, tz=timezone.utc).hour
        session_ok = (hour not in session_gate_hours)
        fr = get_fee_rate(total_volume)

        if pos:
            pos["hold"] += 1
            closed = False
            if h >= pos["tp"]:
                exit_p = pos["tp"]; wins += 1; closed = True

            if closed:
                units = pos["quote"] / pos["ep"]
                pnl = (exit_p - pos["ep"]) * units - (pos["quote"] * fr) - (exit_p * units * fr)
                cash += pos["quote"] + pnl
                total_volume += pos["quote"] + (exit_p * units)
                closes += 1
                if cash > peak_cash: peak_cash = cash
                dd = (peak_cash - cash) / peak_cash
                if dd > max_dd: max_dd = dd
                pos = None

        if pos is None and cash >= 10.0 and btc_ok and session_ok:
            if len(history) >= rsi_period + 2:
                rsi_val = compute_rsi(history[:-1], rsi_period)
                if rsi_val <= rsi_entry:
                    ep = float(c["open"])
                    tq = cash
                    if tq >= 10.0:
                        pos = {"ep": ep, "quote": tq, "hold": 0, "tp": ep * (1 + tp_pct / 100.0)}
                        cash -= tq

    if pos: cash += pos["quote"]
    net = cash - cash_start
    wr = wins/max(1, closes)*100
    return {
        "net": round(net, 2), "return_pct": round(net/cash_start*100, 1),
        "trades": closes, "wr": round(wr, 1),
        "avg_trade": round(net/max(1, closes), 2),
        "max_drawdown": round(max_dd*100, 2),
        "candles": len(candles)
    }

def main():
    client = CoinbaseAdvancedClient()
    now = int(time.time())
    start_60d = now - 60 * 24 * 3600

    # Step 1: Get all products and pick the volatile mid-cap ones
    print("Fetching product list...")
    try:
        products = client.get_products()
        all_products = [p["product_id"] for p in products.get("products", []) if p.get("status") == "online"]
        # Filter to USD pairs, exclude stablecoins and BTC/ETH
        exclude = {"USDC-USD", "USDT-USD", "DAI-USD", "PYUSD-USD", "BTC-USD", "ETH-USD", "ETH-USDC", "BTC-USDC"}
        coins = [p for p in all_products if "-USD" in p and p not in exclude]
        print(f"  Found {len(coins)} USD pairs (excluded stablecoins + BTC/ETH)")
    except Exception as e:
        print(f"  Error fetching products: {e}")
        # Fallback: known volatile coins
        coins = [
            "RAVE-USD", "BAL-USD", "BLUR-USD", "ALEPH-USD", "IOTX-USD",
            "SOL-USD", "DOGE-USD", "XRP-USD", "ARB-USD", "OP-USD",
            "MATIC-USD", "AVAX-USD", "LINK-USD", "UNI-USD", "AAVE-USD",
            "FIL-USD", "NEAR-USD", "APT-USD", "SUI-USD", "SEI-USD",
            "TIA-USD", "INJ-USD", "RUNE-USD", "FET-USD", "RENDER-USD",
            "PEPE-USD", "WIF-USD", "BONK-USD", "FARTCOIN-USD", "TRUMP-USD",
            "VIRTUAL-USD", "CLANKER-USD", "MOG-USD", "POPCAT-USD",
            "BRETT-USD", "GIGA-USD", "MEW-USD", "MYRO-USD", "WLD-USD",
            "STX-USD", "IMX-USD", "TIA-USD", "ONDO-USD", "PENDLE-USD"
        ]

    # Step 2: Fetch BTC lookup
    print("Fetching BTC M1 data...")
    btc_60d = fetch_candles(client, BTC, start_60d, now, granularity="ONE_MINUTE")
    btc_lookup = {int(c["start"]): float(c["close"]) for c in btc_60d}
    print(f"  {len(btc_lookup)} BTC candles")

    # Step 3: Scan each coin
    print(f"\n🔬 Scanning {len(coins)} coins for TP-only mean reversion (60d, RSI<45, TP20)...")
    results = []
    for i, coin in enumerate(coins):
        if i % 10 == 0:
            print(f"  Progress: {i}/{len(coins)}")
        try:
            candles = fetch_candles(client, coin, start_60d, now)
            if len(candles) < 200:  # Need enough data
                continue
            r = run_backtest(candles, btc_lookup, 4, 45, 20)
            r["coin"] = coin
            r["candles"] = len(candles)
            r["per_month"] = round(r["net"] * 30 / 60, 2)
            results.append(r)
            if r["net"] > 0:
                print(f"    ✅ {coin}: ${r['net']:.2f} ({r['return_pct']}%), {r['trades']}t, {r['wr']}% WR, DD={r['max_drawdown']}%")
        except Exception as e:
            pass

    # Sort by profit
    results.sort(key=lambda x: x["net"], reverse=True)

    print(f"\n{'='*80}")
    print(f"🏆 UNIVERSE SCAN RESULTS — Top 20 coins")
    print(f"{'='*80}")
    for i, r in enumerate(results[:20]):
        print(f"  {i+1}. {r['coin']}: ${r['net']:.2f} ({r['return_pct']}%), {r['trades']}t, {r['wr']}% WR, DD={r['max_drawdown']}%, per-mo=${r['per_month']:.2f}")

    # Step 4: Deep dive on RAVE with faster RSI + M1
    print(f"\n🔬 DEEP DIVE: RAVE RSI(2)/RSI(3) + M1/M3 candles")

    # Fetch M1 RAVE data
    print("  Fetching RAVE M1 candles...")
    rave_m1_30d = fetch_candles(client, "RAVE-USD", now - 30*24*3600, now, granularity="ONE_MINUTE")
    print(f"    {len(rave_m1_30d)} M1 candles (30d)")

    # M1 BTC lookup
    btc_lookup_m1 = {int(c["start"]): float(c["close"]) for c in btc_60d if int(c["start"]) >= now - 30*24*3600}

    m1_results = []
    for rsi_period in [2, 3, 4]:
        for rsi_entry in [20, 30, 40, 45]:
            r = run_backtest(rave_m1_30d, btc_lookup_m1, rsi_period, rsi_entry, 20)
            r["label"] = f"M1 RSI({rsi_period})<{rsi_entry}"
            r["per_month"] = round(r["net"] * 30 / 30, 2)
            m1_results.append(r)
            print(f"    {r['label']}: ${r['net']:.2f} ({r['return_pct']}%), {r['trades']}t, {r['wr']}% WR, DD={r['max_drawdown']}%")

    m1_results.sort(key=lambda x: x["net"], reverse=True)
    print(f"\n  M1 best:")
    for i, r in enumerate(m1_results[:5]):
        print(f"    {i+1}. {r['label']}: ${r['net']:.2f}, {r['trades']}t, {r['wr']}% WR, per-mo=${r['per_month']:.2f}")

    # Save
    os.makedirs("reports", exist_ok=True)
    with open("reports/signal_frequency_scan.json", "w") as f:
        json.dump({"universe_scan": results[:20], "m1_deep_dive": m1_results}, f, indent=2)
    print(f"\nResults saved to reports/signal_frequency_scan.json")

if __name__ == "__main__":
    main()
