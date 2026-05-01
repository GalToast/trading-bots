import json
import time
from datetime import datetime, timezone
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from coinbase_advanced_client import CoinbaseAdvancedClient

COINS = {
    "RAVE-USD": "RAVE",
    "BAL-USD": "BAL",
    "BLUR-USD": "BLUR",
    "ALEPH-USD": "ALEPH",
    "IOTX-USD": "IOTX"
}
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

def run_single_coin(candles, btc_lookup, rsi_period, rsi_entry, rsi_exit,
                     tp_pct, sl_pct, timeout, use_sl=True, use_rsi_exit=True,
                     use_timeout=True, session_gate_hours=None, btc_gate=True,
                     cash_start=48.0):
    """Single-coin backtest with toggleable exit mechanisms."""
    if session_gate_hours is None:
        session_gate_hours = {12, 19, 6, 0}

    cash = cash_start
    pos = None
    closes = 0
    wins = 0
    total_volume = 0.0
    history = []
    max_drawdown = 0.0
    peak_cash = cash_start

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
            exit_p = None
            closed = False

            # TP always works
            if h >= pos["tp"]:
                exit_p = pos["tp"]; wins += 1; closed = True
            # SL (toggleable)
            elif use_sl and l <= pos["sl"]:
                exit_p = pos["sl"]; closed = True
            # RSI exit (toggleable)
            elif use_rsi_exit and len(history) >= rsi_period + 1:
                cur_rsi = compute_rsi(history, rsi_period)
                if cur_rsi >= rsi_exit:
                    exit_p = cl; closed = True
                    if exit_p > pos["ep"]: wins += 1
            # Timeout (toggleable)
            elif use_timeout and pos["hold"] >= timeout:
                exit_p = cl; closed = True
                if exit_p > pos["ep"]: wins += 1

            if closed:
                units = pos["quote"] / pos["ep"]
                pnl = (exit_p - pos["ep"]) * units - (pos["quote"] * fr) - (exit_p * units * fr)
                cash += pos["quote"] + pnl
                total_volume += pos["quote"] + (exit_p * units)
                closes += 1
                if cash > peak_cash: peak_cash = cash
                dd = (peak_cash - cash) / peak_cash
                if dd > max_drawdown: max_drawdown = dd
                pos = None

        if pos is None and cash >= 10.0 and btc_ok and session_ok:
            if len(history) >= rsi_period + 2:
                rsi_val = compute_rsi(history[:-1], rsi_period)
                if rsi_val <= rsi_entry:
                    ep = float(c["open"])
                    tq = cash
                    if tq >= 10.0:
                        pos = {
                            "ep": ep, "quote": tq, "hold": 0,
                            "tp": ep * (1 + tp_pct / 100.0),
                            "sl": ep * (1 - sl_pct / 100.0) if use_sl else 0
                        }
                        cash -= tq

    if pos: cash += pos["quote"]
    net = cash - cash_start
    wr = wins/max(1, closes)*100
    return {
        "net": round(net, 2), "return_pct": round(net/cash_start*100, 1),
        "trades": closes, "wr": round(wr, 1),
        "avg_trade": round(net/max(1, closes), 2),
        "volume": round(total_volume, 2), "final_cash": round(cash, 2),
        "max_drawdown": round(max_drawdown*100, 2),
        "fee_rate_final": get_fee_rate(total_volume)
    }

def run_multi_rotation(all_candles, btc_lookup, rsi_period, rsi_entry, rsi_exit,
                        tp_pct, sl_pct, timeout, use_sl=True, use_rsi_exit=True,
                        use_timeout=True, session_gate_hours=None, btc_gate=True,
                        cash_start=48.0):
    """Multi-coin rotation: shared bankroll, one position at a time, picks best signal."""
    if session_gate_hours is None:
        session_gate_hours = {12, 19, 6, 0}

    cash = cash_start
    pos = None  # {"coin": "...", "ep": ..., "quote": ..., "hold": ..., "tp": ..., "sl": ...}
    closes = 0
    wins = 0
    total_volume = 0.0
    histories = {coin: [] for coin in all_candles}
    max_drawdown = 0.0
    peak_cash = cash_start

    # Build time-indexed candle lists
    all_ts = set()
    for coin_candles in all_candles.values():
        for c in coin_candles:
            all_ts.add(int(c["start"]))
    all_ts = sorted(all_ts)
    candle_map = {}
    for coin, candles in all_candles.items():
        candle_map[coin] = {int(c["start"]): c for c in candles}

    for ts in all_ts:
        fr = get_fee_rate(total_volume)
        hour = datetime.fromtimestamp(ts, tz=timezone.utc).hour
        session_ok = (hour not in session_gate_hours)

        btc_ok = True
        if btc_gate:
            p_t = ts - 60; p_t3 = ts - 180
            if p_t in btc_lookup and p_t3 in btc_lookup:
                mom = (btc_lookup[p_t] - btc_lookup[p_t3]) / btc_lookup[p_t3]
                if mom < -0.001: btc_ok = False

        # Update histories
        for coin in all_candles:
            if ts in candle_map[coin]:
                c = candle_map[coin][ts]
                histories[coin].append(float(c["close"]))
                if len(histories[coin]) > 50: histories[coin].pop(0)

        # Process exit
        if pos:
            pos["hold"] += 1
            coin = pos["coin"]
            if ts in candle_map[coin]:
                c = candle_map[coin][ts]
                h = float(c["high"]); l = float(c["low"]); cl = float(c["close"])
                exit_p = None
                closed = False

                if h >= pos["tp"]:
                    exit_p = pos["tp"]; wins += 1; closed = True
                elif use_sl and l <= pos["sl"]:
                    exit_p = pos["sl"]; closed = True
                elif use_rsi_exit and len(histories[coin]) >= rsi_period + 1:
                    cur_rsi = compute_rsi(histories[coin], rsi_period)
                    if cur_rsi >= rsi_exit:
                        exit_p = cl; closed = True
                        if exit_p > pos["ep"]: wins += 1
                elif use_timeout and pos["hold"] >= timeout:
                    exit_p = cl; closed = True
                    if exit_p > pos["ep"]: wins += 1

                if closed:
                    units = pos["quote"] / pos["ep"]
                    pnl = (exit_p - pos["ep"]) * units - (pos["quote"] * fr) - (exit_p * units * fr)
                    cash += pos["quote"] + pnl
                    total_volume += pos["quote"] + (exit_p * units)
                    closes += 1
                    if cash > peak_cash: peak_cash = cash
                    dd = (peak_cash - cash) / peak_cash
                    if dd > max_drawdown: max_drawdown = dd
                    pos = None

        # Process entry: scan all coins for signals
        if pos is None and cash >= 10.0 and btc_ok and session_ok:
            for coin in all_candles:
                if len(histories[coin]) >= rsi_period + 2:
                    rsi_val = compute_rsi(histories[coin][:-1], rsi_period)
                    if rsi_val <= rsi_entry:
                        if ts in candle_map[coin]:
                            c = candle_map[coin][ts]
                            ep = float(c["open"])
                            tq = cash
                            if tq >= 10.0:
                                pos = {
                                    "coin": coin, "ep": ep, "quote": tq, "hold": 0,
                                    "tp": ep * (1 + tp_pct / 100.0),
                                    "sl": ep * (1 - sl_pct / 100.0) if use_sl else 0
                                }
                                cash -= tq
                                break  # One position at a time

    if pos: cash += pos["quote"]
    net = cash - cash_start
    wr = wins/max(1, closes)*100
    return {
        "net": round(net, 2), "return_pct": round(net/cash_start*100, 1),
        "trades": closes, "wr": round(wr, 1),
        "avg_trade": round(net/max(1, closes), 2),
        "volume": round(total_volume, 2), "final_cash": round(cash, 2),
        "max_drawdown": round(max_drawdown*100, 2),
        "coins_traded": {}
    }

def main():
    client = CoinbaseAdvancedClient()
    now = int(time.time())
    start_72h = now - 72 * 3600
    start_14d = now - 14 * 24 * 3600

    # Load 72h data
    print(f"Fetching 72h data for {len(COINS)} coins + BTC...")
    all_candles_72h = {}
    for product, label in COINS.items():
        print(f"  Fetching {product}...")
        all_candles_72h[product] = fetch_candles(client, product, start_72h, now)
        print(f"    {len(all_candles_72h[product])} candles")

    btc_m1_72h = fetch_candles(client, BTC, start_72h, now, granularity="ONE_MINUTE")
    btc_lookup_72h = {int(c["start"]): float(c["close"]) for c in btc_m1_72h}

    # Load 14d data
    print(f"\nFetching 14d data...")
    all_candles_14d = {}
    for product, label in COINS.items():
        print(f"  Fetching {product}...")
        all_candles_14d[product] = fetch_candles(client, product, start_14d, now)
        print(f"    {len(all_candles_14d[product])} candles")

    btc_m1_14d = fetch_candles(client, BTC, start_14d, now, granularity="ONE_MINUTE")
    btc_lookup_14d = {int(c["start"]): float(c["close"]) for c in btc_m1_14d}

    results = {}
    rave_72h = all_candles_72h["RAVE-USD"]
    rave_14d = all_candles_14d["RAVE-USD"]

    # EXP 1: No SL vs SL (72h)
    print(f"\n🔬 EXP 1: No SL vs SL (72h)")
    r_no_sl = run_single_coin(rave_72h, btc_lookup_72h, 4, 45, 95, 20, 2.75, 4, use_sl=False)
    r_no_sl["label"] = "No SL (RSI exit + TP only)"
    r_with_sl = run_single_coin(rave_72h, btc_lookup_72h, 4, 45, 95, 20, 2.75, 4, use_sl=True)
    r_with_sl["label"] = "SL 2.75%"
    print(f"   No SL:     ${r_no_sl['net']:.2f} ({r_no_sl['return_pct']}%), {r_no_sl['trades']}t, {r_no_sl['wr']}% WR, DD={r_no_sl['max_drawdown']}%")
    print(f"   SL 2.75%:  ${r_with_sl['net']:.2f} ({r_with_sl['return_pct']}%), {r_with_sl['trades']}t, {r_with_sl['wr']}% WR, DD={r_with_sl['max_drawdown']}%")
    results["no_sl_vs_sl_72h"] = [r_no_sl, r_with_sl]

    # EXP 2: RSI exit > 99 (effectively never) vs RSI>95
    print(f"\n🔬 EXP 2: RSI exit extremes (72h)")
    r_rsi99 = run_single_coin(rave_72h, btc_lookup_72h, 4, 45, 99, 20, 2.75, 4, use_sl=False)
    r_rsi99["label"] = "RSI exit >99 (almost never)"
    r_no_rsi = run_single_coin(rave_72h, btc_lookup_72h, 4, 45, 95, 20, 2.75, 4, use_sl=False, use_rsi_exit=False)
    r_no_rsi["label"] = "No RSI exit (TP + timeout only)"
    print(f"   RSI>99:    ${r_rsi99['net']:.2f} ({r_rsi99['return_pct']}%), {r_rsi99['trades']}t, {r_rsi99['wr']}% WR, DD={r_rsi99['max_drawdown']}%")
    print(f"   No RSI:    ${r_no_rsi['net']:.2f} ({r_no_rsi['return_pct']}%), {r_no_rsi['trades']}t, {r_no_rsi['wr']}% WR, DD={r_no_rsi['max_drawdown']}%")
    results["rsi_exit_extremes"] = [r_rsi99, r_no_rsi]

    # EXP 3: Infinite hold (no SL, no timeout, only TP or RSI>95)
    print(f"\n🔬 EXP 3: Infinite Hold (no SL, no timeout, TP20 + RSI>95 only)")
    r_infinite = run_single_coin(rave_72h, btc_lookup_72h, 4, 45, 95, 20, 2.75, 99999, use_sl=False, use_timeout=False)
    r_infinite["label"] = "Infinite hold (TP20 + RSI>95)"
    print(f"   Infinite:  ${r_infinite['net']:.2f} ({r_infinite['return_pct']}%), {r_infinite['trades']}t, {r_infinite['wr']}% WR, DD={r_infinite['max_drawdown']}%")
    results["infinite_hold"] = r_infinite

    # EXP 4: Multi-coin rotation (72h)
    print(f"\n🔬 EXP 4: Multi-Coin Rotation (72h)")
    for rsi_exit_val in [80, 95]:
        for use_sl_val in [True, False]:
            r_multi = run_multi_rotation(all_candles_72h, btc_lookup_72h, 4, 45, rsi_exit_val, 20, 2.75, 4, use_sl=use_sl_val, use_rsi_exit=True)
            label = f"Multi RSI_exit>{rsi_exit_val} {'no SL' if not use_sl_val else 'SL2.75'}"
            r_multi["label"] = label
            print(f"   {label}: ${r_multi['net']:.2f} ({r_multi['return_pct']}%), {r_multi['trades']}t, {r_multi['wr']}% WR, DD={r_multi['max_drawdown']}%")
            results.setdefault("multi_rotation_72h", []).append(r_multi)

    # EXP 5: 14-day ablation
    print(f"\n🔬 EXP 5: 14-Day Ablation")
    configs = [
        ("RSI<45, TP20/SL2.75, RSI>95", rave_14d, btc_lookup_14d, 4, 45, 95, 20, 2.75, 4, True, True, True),
        ("RSI<45, TP20, NO SL, RSI>95", rave_14d, btc_lookup_14d, 4, 45, 95, 20, 2.75, 4, False, True, True),
        ("RSI<45, TP20/SL2.75, NO RSI exit", rave_14d, btc_lookup_14d, 4, 45, 95, 20, 2.75, 4, True, False, True),
        ("RSI<45, TP20/SL2.75, RSI>80", rave_14d, btc_lookup_14d, 4, 45, 80, 20, 2.75, 4, True, True, True),
        ("RSI<30, TP25/SL3, RSI>80 (original)", rave_14d, btc_lookup_14d, 4, 30, 80, 25, 3.0, 24, True, True, True),
        ("Infinite hold (TP20 + RSI>95)", rave_14d, btc_lookup_14d, 4, 45, 95, 20, 2.75, 99999, False, True, False),
    ]
    ablation_14d = []
    for label, candles, btc_lookup_c, rp, re_entry, re_exit, tp, sl, to, us_sl, use_rsi, use_to in configs:
        r = run_single_coin(candles, btc_lookup_c, rp, re_entry, re_exit, tp, sl, to, use_sl=us_sl, use_rsi_exit=use_rsi, use_timeout=use_to)
        r["label"] = label
        r["per_72h"] = round(r["net"] / 4.67, 2)
        ablation_14d.append(r)
        print(f"   {label}: ${r['net']:.2f} ({r['return_pct']}%), {r['trades']}t, {r['wr']}% WR, DD={r['max_drawdown']}%, per-72h=${r['per_72h']:.2f}")
    results["ablation_14d"] = ablation_14d

    # EXP 6: Multi-coin 14-day
    print(f"\n🔬 EXP 6: Multi-Coin Rotation 14-day")
    r_multi_14d = run_multi_rotation(all_candles_14d, btc_lookup_14d, 4, 45, 95, 20, 2.75, 4, use_sl=True, use_rsi_exit=True)
    r_multi_14d["label"] = "Multi RSI>95 SL2.75 14d"
    r_multi_14d["per_72h"] = round(r_multi_14d["net"] / 4.67, 2)
    print(f"   Multi 14d: ${r_multi_14d['net']:.2f} ({r_multi_14d['return_pct']}%), {r_multi_14d['trades']}t, {r_multi_14d['wr']}% WR, per-72h=${r_multi_14d['per_72h']:.2f}")
    results["multi_rotation_14d"] = r_multi_14d

    # Summary
    print(f"\n{'='*80}")
    print(f"🏗️ CEILING HUNT SUMMARY")
    print(f"{'='*80}")

    best_14d = max(ablation_14d, key=lambda x: x["per_72h"])
    print(f"Best 14d per-72h: {best_14d['label']} -> ${best_14d['per_72h']:.2f}/72h (${best_14d['net']:.2f} total)")
    print(f"Best 72h: No SL = ${r_no_sl['net']:.2f}, Multi-coin = ${results['multi_rotation_72h'][0]['net']:.2f}")
    print(f"Infinite hold 72h: ${r_infinite['net']:.2f}")

    with open("reports/ceiling_hunt_results.json", "w") as f:
        json.dump(results, f, indent=2)
    print(f"\nResults saved to reports/ceiling_hunt_results.json")

if __name__ == "__main__":
    main()
