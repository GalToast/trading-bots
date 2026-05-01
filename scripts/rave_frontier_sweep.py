import json
import time
from datetime import datetime, timezone
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from coinbase_advanced_client import CoinbaseAdvancedClient

PRODUCT = "RAVE-USD"
BTC = "BTC-USD"
FEE_RATE = 0.0040

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
            time.sleep(0.1)
        except:
            cs = ce
            time.sleep(0.5)
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

def run_backtest(rave_candles, btc_lookup, rsi_period, rsi_entry, rsi_exit,
                 tp_pct, sl_pct, timeout, session_gate_hours=None, btc_gate=True, cash_start=48.0):
    if session_gate_hours is None:
        session_gate_hours = {12, 19, 6, 0}

    cash = cash_start
    pos = None
    closes = 0
    wins = 0
    total_volume = 0.0
    history = []

    for i in range(len(rave_candles)):
        c = rave_candles[i]
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

        if pos:
            pos["hold"] += 1
            exit_p = None
            closed = False

            if h >= pos["tp"]:
                exit_p = pos["tp"]; wins += 1; closed = True
            elif l <= pos["sl"]:
                exit_p = pos["sl"]; closed = True
            elif len(history) >= rsi_period + 1:
                cur_rsi = compute_rsi(history, rsi_period)
                if cur_rsi >= rsi_exit:
                    exit_p = cl; closed = True
                    if exit_p > pos["ep"]: wins += 1
            elif pos["hold"] >= timeout:
                exit_p = cl; closed = True
                if exit_p > pos["ep"]: wins += 1

            if closed:
                units = pos["quote"] / pos["ep"]
                pnl = (exit_p - pos["ep"]) * units - (pos["quote"] * FEE_RATE) - (exit_p * units * FEE_RATE)
                cash += pos["quote"] + pnl
                total_volume += pos["quote"] + (exit_p * units)
                closes += 1
                pos = None

        if pos is None and cash >= 10.0 and btc_ok and session_ok:
            if len(history) >= rsi_period + 2:
                rsi_val = compute_rsi(history[:-1], rsi_period)
                if rsi_val <= rsi_entry:
                    ep = float(c["open"])
                    tq = cash * 0.95
                    if tq >= 10.0:
                        pos = {
                            "ep": ep, "quote": tq, "hold": 0,
                            "tp": ep * (1 + tp_pct / 100.0),
                            "sl": ep * (1 - sl_pct / 100.0)
                        }
                        cash -= tq

    if pos: cash += pos["quote"]
    net = cash - cash_start
    wr = wins/max(1, closes)*100
    avg_trade = net / max(1, closes)
    return {
        "net": round(net, 2), "return_pct": round(net/cash_start*100, 1),
        "trades": closes, "wr": round(wr, 1), "avg_trade": round(avg_trade, 2),
        "volume": round(total_volume, 2), "final_cash": round(cash, 2)
    }

def main():
    client = CoinbaseAdvancedClient()
    now = int(time.time())
    start = now - 72 * 3600

    print(f"Fetching 72h data for {PRODUCT} Fine-Grained Frontier Sweep...")
    rave_candles = fetch_candles(client, PRODUCT, start, now)
    btc_m1 = fetch_candles(client, BTC, start, now, granularity="ONE_MINUTE")
    btc_lookup = {int(c["start"]): float(c["close"]) for c in btc_m1}
    print(f"Loaded {len(rave_candles)} RAVE candles, {len(btc_lookup)} BTC candles")

    results = {}

    # EXP 1: Fine-grained RSI entry around 40-50
    print(f"\n🔬 EXP 1: RSI Entry Fine-Grained (40-50, step 1)")
    entry_fine = []
    for entry in range(40, 51):
        r = run_backtest(rave_candles, btc_lookup, 4, entry, 90, 20, 2, 4)
        r["label"] = f"RSI<={entry}"
        entry_fine.append(r)
        print(f"   RSI<={entry}: ${r['net']:.2f} ({r['return_pct']}%), {r['trades']}t, {r['wr']}% WR, ${r['avg_trade']}/t")
    results["rsi_entry_fine"] = entry_fine

    # EXP 2: Fine-grained TP around 15-25
    print(f"\n🔬 EXP 2: TP Fine-Grained (15-25, step 1, SL=2)")
    tp_fine = []
    for tp in range(15, 26):
        r = run_backtest(rave_candles, btc_lookup, 4, 45, 90, tp, 2, 4)
        r["label"] = f"TP{tp}/SL2"
        tp_fine.append(r)
        print(f"   TP{tp}/SL2: ${r['net']:.2f} ({r['return_pct']}%), {r['trades']}t, {r['wr']}% WR")
    results["tp_fine"] = tp_fine

    # EXP 3: Fine-grained SL around 1-4
    print(f"\n🔬 EXP 3: SL Fine-Grained (1.0-4.0, step 0.25)")
    sl_fine = []
    sl_vals = [1.0, 1.25, 1.5, 1.75, 2.0, 2.25, 2.5, 2.75, 3.0, 3.5, 4.0]
    for sl in sl_vals:
        r = run_backtest(rave_candles, btc_lookup, 4, 45, 90, 20, sl, 4)
        r["label"] = f"TP20/SL{sl}"
        sl_fine.append(r)
        print(f"   TP20/SL{sl}: ${r['net']:.2f} ({r['return_pct']}%), {r['trades']}t, {r['wr']}% WR")
    results["sl_fine"] = sl_fine

    # EXP 4: RSI exit fine-grained (85-95)
    print(f"\n🔬 EXP 4: RSI Exit Fine-Grained (85-95, step 1)")
    exit_fine = []
    for ex in range(85, 96):
        r = run_backtest(rave_candles, btc_lookup, 4, 45, ex, 20, 2, 4)
        r["label"] = f"RSI_exit>={ex}"
        exit_fine.append(r)
        print(f"   RSI>={ex}: ${r['net']:.2f} ({r['return_pct']}%), {r['trades']}t, {r['wr']}% WR")
    results["rsi_exit_fine"] = exit_fine

    # EXP 5: Combined ULTIMATE (best of each fine sweep)
    best_entry = max(entry_fine, key=lambda x: x["net"])
    best_tp = max(tp_fine, key=lambda x: x["net"])
    best_sl = max(sl_fine, key=lambda x: x["net"])
    best_exit = max(exit_fine, key=lambda x: x["net"])

    best_entry_val = int(best_entry["label"].split("<=")[1])
    best_tp_val = int(best_tp["label"].split("/")[0].replace("TP", ""))
    best_sl_val = float(best_sl["label"].split("SL")[1])
    best_exit_val = int(best_exit["label"].split(">=")[1])

    ultimate = run_backtest(rave_candles, btc_lookup, 4, best_entry_val, best_exit_val, best_tp_val, best_sl_val, 4)
    ultimate["label"] = f"RSI(4)<={best_entry_val}, TP{best_tp_val}/SL{best_sl_val}, RSI>{best_exit_val}"
    results["ultimate"] = ultimate

    print(f"\n{'='*80}")
    print(f"🏆 ULTIMATE CONFIGURATION: ${ultimate['net']:.2f} ({ultimate['return_pct']}%)")
    print(f"   {ultimate['label']}, 4-bar timeout")
    print(f"   {ultimate['trades']} trades, {ultimate['wr']}% WR, ${ultimate['avg_trade']}/trade")
    print(f"   Volume: ${ultimate['volume']:.2f}")
    print(f"{'='*80}")
    print(f"\nImprovement vs previous crown (+$87.26): +${ultimate['net']-87.26:.2f} ({(ultimate['net']-87.26)/87.26*100:.1f}%)")

    with open("reports/frontier_sweep_results.json", "w") as f:
        json.dump(results, f, indent=2)
    print(f"\nResults saved to reports/frontier_sweep_results.json")

if __name__ == "__main__":
    main()
