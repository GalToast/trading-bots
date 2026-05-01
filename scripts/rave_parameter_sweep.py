import json
import time
from datetime import datetime, timezone
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from coinbase_advanced_client import CoinbaseAdvancedClient

PRODUCT = "RAVE-USD"
BTC = "BTC-USD"
FEE_RATE = 0.0040  # 40bps worst case

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

        # BTC Gate
        btc_ok = True
        if btc_gate:
            p_t = ts - 60; p_t3 = ts - 180
            if p_t in btc_lookup and p_t3 in btc_lookup:
                mom = (btc_lookup[p_t] - btc_lookup[p_t3]) / btc_lookup[p_t3]
                if mom < -0.001: btc_ok = False

        # Session Gate
        hour = datetime.fromtimestamp(ts, tz=timezone.utc).hour
        session_ok = (hour not in session_gate_hours)

        # Exit logic
        if pos:
            pos["hold"] += 1
            exit_p = None
            closed = False

            # TP hit
            if h >= pos["tp"]:
                exit_p = pos["tp"]; wins += 1; closed = True
            # SL hit
            elif l <= pos["sl"]:
                exit_p = pos["sl"]; closed = True
            # RSI exit
            elif len(history) >= rsi_period + 1:
                cur_rsi = compute_rsi(history, rsi_period)
                if cur_rsi >= rsi_exit:
                    exit_p = cl; closed = True
                    if exit_p > pos["ep"]: wins += 1
            # Timeout
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

        # Entry logic
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
        "net": round(net, 2),
        "return_pct": round(net/cash_start*100, 1),
        "trades": closes,
        "wr": round(wr, 1),
        "avg_trade": round(avg_trade, 2),
        "volume": round(total_volume, 2),
        "final_cash": round(cash, 2)
    }

def main():
    client = CoinbaseAdvancedClient()
    now = int(time.time())
    start = now - 72 * 3600

    print(f"Fetching 72h data for {PRODUCT} Parameter Sweep...")
    rave_candles = fetch_candles(client, PRODUCT, start, now)
    btc_m1 = fetch_candles(client, BTC, start, now, granularity="ONE_MINUTE")
    btc_lookup = {int(c["start"]): float(c["close"]) for c in btc_m1}
    print(f"Loaded {len(rave_candles)} RAVE candles, {len(btc_lookup)} BTC candles")

    baseline = run_backtest(rave_candles, btc_lookup, 4, 30, 80, 25, 3, 24)
    print(f"\n📊 BASELINE: RSI(4)<30, >80 exit, TP25/SL3, 24-bar timeout")
    print(f"   Net: ${baseline['net']:.2f} ({baseline['return_pct']}%), {baseline['trades']} trades, {baseline['wr']}% WR, ${baseline['avg_trade']}/trade")

    results = {"baseline": baseline, "experiments": {}}

    # EXP 1: TP/SL Grid Sweep
    print(f"\n🔬 EXP 1: TP/SL Grid Sweep")
    tp_sl_results = []
    for tp in [15, 20, 25, 30, 35, 40, 50]:
        for sl in [1, 2, 3, 4, 5]:
            r = run_backtest(rave_candles, btc_lookup, 4, 30, 80, tp, sl, 24)
            r["label"] = f"TP{tp}/SL{sl}"
            tp_sl_results.append(r)

    tp_sl_results.sort(key=lambda x: x["net"], reverse=True)
    print("   Top 10 TP/SL configs:")
    for i, r in enumerate(tp_sl_results[:10]):
        print(f"   {i+1}. {r['label']}: ${r['net']:.2f} ({r['return_pct']}%), {r['trades']}t, {r['wr']}% WR, ${r['avg_trade']}/t")
    results["experiments"]["tp_sl_grid"] = tp_sl_results[:10]

    # EXP 2: RSI Entry Threshold Sweep
    print(f"\n🔬 EXP 2: RSI Entry Threshold Sweep (exit=80, TP25/SL3)")
    entry_results = []
    for entry in [10, 15, 20, 25, 30, 35, 40, 45]:
        r = run_backtest(rave_candles, btc_lookup, 4, entry, 80, 25, 3, 24)
        r["label"] = f"RSI_entry<={entry}"
        entry_results.append(r)
        print(f"   RSI<={entry}: ${r['net']:.2f} ({r['return_pct']}%), {r['trades']}t, {r['wr']}% WR")
    results["experiments"]["rsi_entry_sweep"] = entry_results

    # EXP 3: RSI Exit Threshold Sweep
    print(f"\n🔬 EXP 3: RSI Exit Threshold Sweep (entry<30, TP25/SL3)")
    exit_results = []
    for exit_val in [50, 55, 60, 65, 70, 75, 80, 85, 90]:
        r = run_backtest(rave_candles, btc_lookup, 4, 30, exit_val, 25, 3, 24)
        r["label"] = f"RSI_exit>={exit_val}"
        exit_results.append(r)
        print(f"   RSI>={exit_val}: ${r['net']:.2f} ({r['return_pct']}%), {r['trades']}t, {r['wr']}% WR")
    results["experiments"]["rsi_exit_sweep"] = exit_results

    # EXP 4: Timeout Sweep
    print(f"\n🔬 EXP 4: Timeout Sweep (entry<30, exit>80, TP25/SL3)")
    timeout_results = []
    for to in [4, 8, 12, 16, 20, 24, 32, 48, 72]:
        r = run_backtest(rave_candles, btc_lookup, 4, 30, 80, 25, 3, to)
        r["label"] = f"timeout={to}"
        timeout_results.append(r)
        print(f"   timeout={to}: ${r['net']:.2f} ({r['return_pct']}%), {r['trades']}t, {r['wr']}% WR")
    results["experiments"]["timeout_sweep"] = timeout_results

    # EXP 5: Combined Best Parameters
    print(f"\n🔬 EXP 5: Combined Best Parameters")
    best_tp_sl = tp_sl_results[0]
    best_entry = max(entry_results, key=lambda x: x["net"])
    best_exit = max(exit_results, key=lambda x: x["net"])
    best_timeout = max(timeout_results, key=lambda x: x["net"])

    # Parse best values
    best_tp = int(best_tp_sl["label"].split("/")[0].replace("TP", ""))
    best_sl = int(best_tp_sl["label"].split("SL")[1])
    best_entry_val = int(best_entry["label"].split("<=")[1])
    best_exit_val = int(best_exit["label"].split(">=")[1])
    best_to = int(best_timeout["label"].split("=")[1])

    combined = run_backtest(rave_candles, btc_lookup, 4, best_entry_val, best_exit_val, best_tp, best_sl, best_to)
    combined["label"] = f"RSI(4)<={best_entry_val}, >={best_exit_val}, TP{best_tp}/SL{best_sl}, to={best_to}"
    results["experiments"]["combined_best"] = combined

    print(f"   Combined: ${combined['net']:.2f} ({combined['return_pct']}%), {combined['trades']}t, {combined['wr']}% WR, ${combined['avg_trade']}/t")
    print(f"   Components: TP/SL from {best_tp_sl['label']}, entry from {best_entry['label']}, exit from {best_exit['label']}, timeout from {best_timeout['label']}")

    # EXP 6: No session gate vs session gate
    print(f"\n🔬 EXP 6: Session Gate Impact (using combined best params)")
    no_session = run_backtest(rave_candles, btc_lookup, 4, best_entry_val, best_exit_val, best_tp, best_sl, best_to, session_gate_hours=set())
    no_session["label"] = "No session gate"
    with_session = run_backtest(rave_candles, btc_lookup, 4, best_entry_val, best_exit_val, best_tp, best_sl, best_to)
    with_session["label"] = "Session gate (12,19,6,0)"
    print(f"   No session gate: ${no_session['net']:.2f} ({no_session['return_pct']}%), {no_session['trades']}t")
    print(f"   With session gate: ${with_session['net']:.2f} ({with_session['return_pct']}%), {with_session['trades']}t")
    results["experiments"]["session_gate"] = [no_session, with_session]

    # EXP 7: No BTC gate vs BTC gate
    print(f"\n🔬 EXP 7: BTC Gate Impact (using combined best params)")
    no_btc = run_backtest(rave_candles, btc_lookup, 4, best_entry_val, best_exit_val, best_tp, best_sl, best_to, btc_gate=False)
    no_btc["label"] = "No BTC gate"
    with_btc = run_backtest(rave_candles, btc_lookup, 4, best_entry_val, best_exit_val, best_tp, best_sl, best_to)
    with_btc["label"] = "BTC gate"
    print(f"   No BTC gate: ${no_btc['net']:.2f} ({no_btc['return_pct']}%), {no_btc['trades']}t")
    print(f"   With BTC gate: ${with_btc['net']:.2f} ({with_btc['return_pct']}%), {with_btc['trades']}t")
    results["experiments"]["btc_gate"] = [no_btc, with_btc]

    # Summary
    print(f"\n{'='*80}")
    print(f"📊 PARAMETER SWEEP SUMMARY")
    print(f"{'='*80}")
    print(f"Baseline (RSI(4)<30, >80, TP25/SL3, 24-bar): ${baseline['net']:.2f} ({baseline['return_pct']}%)")
    print(f"Best TP/SL:                                  ${best_tp_sl['net']:.2f} ({best_tp_sl['return_pct']}%)")
    print(f"Best Entry:                                  ${best_entry['net']:.2f} ({best_entry['return_pct']}%)")
    print(f"Best Exit:                                   ${best_exit['net']:.2f} ({best_exit['return_pct']}%)")
    print(f"Best Timeout:                                ${best_timeout['net']:.2f} ({best_timeout['return_pct']}%)")
    print(f"COMBINED BEST:                               ${combined['net']:.2f} ({combined['return_pct']}%)")
    print(f"{'='*80}")

    os.makedirs("reports", exist_ok=True)
    with open("reports/parameter_sweep_results.json", "w") as f:
        json.dump(results, f, indent=2)
    print(f"\nResults saved to reports/parameter_sweep_results.json")

if __name__ == "__main__":
    main()
