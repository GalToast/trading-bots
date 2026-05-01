import json
import time
from datetime import datetime, timezone
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from coinbase_advanced_client import CoinbaseAdvancedClient

PRODUCT = "RAVE-USD"
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
    daily_pnl = {}

    for i in range(len(candles)):
        c = candles[i]
        ts = int(c["start"])
        h = float(c["high"]); l = float(c["low"]); cl = float(c["close"])
        day_key = datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d")

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
                daily_pnl[day_key] = daily_pnl.get(day_key, 0) + pnl
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

    losing_days = sum(1 for v in daily_pnl.values() if v < 0)
    winning_days = sum(1 for v in daily_pnl.values() if v > 0)
    avg_winning_day = sum(v for v in daily_pnl.values() if v > 0) / max(1, winning_days)
    avg_losing_day = sum(v for v in daily_pnl.values() if v < 0) / max(1, losing_days)

    return {
        "net": round(net, 2), "return_pct": round(net/cash_start*100, 1),
        "trades": closes, "wr": round(wr, 1),
        "avg_trade": round(net/max(1, closes), 2),
        "volume": round(total_volume, 2), "max_drawdown": round(max_dd*100, 2),
        "winning_days": winning_days, "losing_days": losing_days,
        "avg_winning_day": round(avg_winning_day, 2),
        "avg_losing_day": round(avg_losing_day, 2),
        "days_traded": len(daily_pnl)
    }

def main():
    client = CoinbaseAdvancedClient()
    now = int(time.time())
    start_30d = now - 30 * 24 * 3600
    start_60d = now - 60 * 24 * 3600

    print(f"Fetching 30d + 60d data for {PRODUCT}...")
    rave_30d = fetch_candles(client, PRODUCT, start_30d, now)
    print(f"  30d: {len(rave_30d)} candles")
    rave_60d = fetch_candles(client, PRODUCT, start_60d, now)
    print(f"  60d: {len(rave_60d)} candles")
    btc_60d = fetch_candles(client, BTC, start_60d, now, granularity="ONE_MINUTE")
    btc_lookup_60d = {int(c["start"]): float(c["close"]) for c in btc_60d}
    btc_lookup_30d = {int(c["start"]): float(c["close"]) for c in btc_60d if int(c["start"]) >= start_30d}

    results = {}

    configs = [
        ("RSI<45, TP20", 45, 20),
        ("RSI<40, TP20", 40, 20),
        ("RSI<35, TP20", 35, 20),
        ("RSI<30, TP20", 30, 20),
        ("RSI<45, TP15", 45, 15),
        ("RSI<45, TP25", 45, 25),
        ("RSI<45, TP30", 45, 30),
    ]

    for window_label, candles, btc_lookup in [
        ("30d", rave_30d, btc_lookup_30d),
        ("60d", rave_60d, btc_lookup_60d)
    ]:
        print(f"\n🔬 {window_label} sweep (NO SL, NO timeout, TP-only):")
        window_results = []
        for label, rsi_entry, tp in configs:
            r = run_backtest(candles, btc_lookup, 4, rsi_entry, tp)
            r["label"] = label
            days = int(window_label.replace("d",""))
            r["per_72h"] = round(r["net"] / (days/3), 2)
            r["per_month"] = round(r["net"] * 30 / days, 2)
            window_results.append(r)
            print(f"   {label}: ${r['net']:.2f} ({r['return_pct']}%), {r['trades']}t, {r['wr']}% WR")
            print(f"      DD={r['max_drawdown']}%, {r['winning_days']}W/{r['losing_days']}L days, avg win=${r['avg_winning_day']}, avg loss=${r['avg_losing_day']}")
            print(f"      Per-72h: ${r['per_72h']:.2f} | Per-month: ${r['per_month']:.2f}")
        results[window_label] = window_results

    # Summary
    print(f"\n{'='*80}")
    print(f"🏗️ CEILING CONFIRMATION — 30d/60d RESULTS")
    print(f"{'='*80}")
    for window_label in ["30d", "60d"]:
        best = max(results[window_label], key=lambda x: x["per_72h"])
        days = int(window_label.replace("d",""))
        print(f"\n{window_label} best: {best['label']}")
        print(f"   Total: ${best['net']:.2f} ({best['return_pct']}%), {best['trades']}t, {best['wr']}% WR")
        print(f"   Per-72h: ${best['per_72h']:.2f} | Per-month: ${best['per_month']:.2f}")
        print(f"   Max DD: {best['max_drawdown']}% | {best['winning_days']}W/{best['losing_days']}L days")

    # Cross-window consistency check
    print(f"\n📊 CONSISTENCY CHECK:")
    for label_prefix in ["RSI<45, TP20", "RSI<45, TP15", "RSI<45, TP25"]:
        r30 = next((r for r in results["30d"] if r["label"] == label_prefix), None)
        r60 = next((r for r in results["60d"] if r["label"] == label_prefix), None)
        if r30 and r60:
            print(f"   {label_prefix}: 30d=${r30['per_72h']:.2f}/72h | 60d=${r60['per_72h']:.2f}/72h | delta=${r30['per_72h']-r60['per_72h']:.2f}")

    with open("reports/ceiling_confirmation.json", "w") as f:
        json.dump(results, f, indent=2)
    print(f"\nResults saved to reports/ceiling_confirmation.json")

if __name__ == "__main__":
    main()
