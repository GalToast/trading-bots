#!/usr/bin/env python3
"""
Ceiling Finder — Full Grid Search over RSI Entry/Exit/Period/Hold/SL/TP space
Finds the ABSOLUTE MAXIMUM edge on RAVE-USD in 72h.
"""
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

def run_backtest(candles, btc_lookup, config):
    starting_cash = 48.0
    cash = starting_cash
    pos = None
    closes = 0
    wins = 0
    total_volume = 0.0
    total_fees = 0.0
    history = []
    exit_reasons = {"tp": 0, "sl": 0, "timeout": 0, "rsi_ob": 0}
    trade_details = []

    rsi_period = config["rsi_period"]
    os_thresh = config["os_thresh"]
    tp_pct = config["tp_pct"]
    sl_pct = config.get("sl_pct", 999)  # default no SL
    max_hold = config["max_hold"]
    rsi_exit_ob = config.get("rsi_exit_ob", 0)
    compound = config.get("compound", False)

    for i in range(len(candles)):
        c = candles[i]
        ts = int(c["start"])
        h = float(c["high"]); l = float(c["low"]); cl = float(c["close"])

        history.append(cl)
        if len(history) > 200: history.pop(0)

        # BTC Gate
        btc_gate = True
        p_t = ts - 60; p_t3 = ts - 180
        if p_t in btc_lookup and p_t3 in btc_lookup:
            mom = (btc_lookup[p_t] - btc_lookup[p_t3]) / btc_lookup[p_t3]
            if mom < -0.001: btc_gate = False

        # Session Gate
        hour = datetime.fromtimestamp(ts, tz=timezone.utc).hour
        session_gate = (hour not in [12, 19, 6, 0])

        # Fee Tier
        if total_volume >= 50000: fr = 0.0015
        elif total_volume >= 10000: fr = 0.0025
        else: fr = 0.0040

        # Exit
        if pos:
            pos["hold"] += 1
            exit_p = None
            exit_reason = None

            if rsi_exit_ob > 0 and len(history) >= rsi_period + 1:
                rsi_now = compute_rsi(history, rsi_period)
                if rsi_now >= rsi_exit_ob:
                    exit_p = cl; exit_reason = "rsi_ob"

            if exit_p is None and h >= pos["tp"]:
                exit_p = pos["tp"]; exit_reason = "tp"
            if exit_p is None and l <= pos["sl"]:
                exit_p = pos["sl"]; exit_reason = "sl"
            if exit_p is None and pos["hold"] >= max_hold:
                exit_p = cl; exit_reason = "timeout"

            if exit_p is not None:
                units = pos["quote"] / pos["ep"]
                pnl = (exit_p - pos["ep"]) * units - (pos["quote"] * fr) - (exit_p * units * fr)
                cash += pos["quote"] + pnl
                total_volume += pos["quote"] + (exit_p * units)
                total_fees += pos["quote"] * fr + exit_p * units * fr
                closes += 1
                is_win = exit_p > pos["ep"]
                if is_win: wins += 1
                exit_reasons[exit_reason] = exit_reasons.get(exit_reason, 0) + 1
                trade_details.append({
                    "bar": i, "entry": pos["ep"], "exit": exit_p,
                    "pnl": round(pnl, 4), "win": is_win, "hold_bars": pos["hold"],
                    "reason": exit_reason, "rsi_at_entry": round(pos.get("rsi_entry", 0), 1)
                })
                pos = None

        # Entry
        if pos is None and cash >= 10.0 and btc_gate and session_gate:
            if len(history) >= rsi_period + 2:
                rsi_prev = compute_rsi(history[:-1], rsi_period)
                if rsi_prev <= os_thresh:
                    ep = float(c["open"])
                    if compound:
                        tq = cash * 0.95
                    else:
                        tq = starting_cash
                    if tq > cash: tq = cash
                    if tq >= 10.0:
                        pos = {
                            "ep": ep, "quote": tq, "hold": 0,
                            "tp": ep * (1 + tp_pct / 100.0),
                            "sl": ep * (1 - sl_pct / 100.0),
                            "rsi_entry": rsi_prev,
                        }
                        cash -= tq

    if pos:
        cash += pos["quote"]

    net = cash - starting_cash
    wr = wins / max(1, closes) * 100
    avg_trade = net / max(1, closes)

    return {
        "net": round(net, 2),
        "return_pct": round(net / starting_cash * 100, 1),
        "closes": closes,
        "wr": round(wr, 1),
        "avg_trade": round(avg_trade, 2),
        "total_fees": round(total_fees, 2),
        "exit_reasons": exit_reasons,
        "trade_details": trade_details,
    }

def main():
    client = CoinbaseAdvancedClient()
    now = int(time.time())
    start = now - 72 * 3600

    print(f"Fetching 72h data for Ceiling Finder...")
    rave_candles = fetch_candles(client, PRODUCT, start, now)
    btc_m1 = fetch_candles(client, BTC, start, now, granularity="ONE_MINUTE")
    btc_lookup = {int(c["start"]): float(c["close"]) for c in btc_m1}
    print(f"  RAVE: {len(rave_candles)}, BTC: {len(btc_m1)}")

    # FULL GRID SEARCH
    # RSI period: 3, 4, 5
    # Entry RSI: 20, 25, 30, 35
    # Exit RSI: 0 (disabled), 60, 65, 70, 75, 80, 85, 90
    # TP: 15, 20, 25, 30, 40, 50
    # SL: 999 (no SL), 5, 7, 10
    # Max hold: 12, 18, 24, 36, 48
    # Compound: True, False

    rsi_periods = [3, 4, 5]
    os_threshes = [20, 25, 30, 35]
    rsi_exits = [0, 60, 65, 70, 75, 80, 85, 90]
    tps = [15, 20, 25, 30, 40, 50]
    sls = [999, 5, 7, 10]
    max_holds = [12, 18, 24, 36, 48]
    compounds = [False, True]

    # That's 3*4*8*6*4*5*2 = 23,040 combos. Too many for a single pass.
    # Strategy: Do a smart search — fix some params and sweep others.

    results = []
    iteration = 0

    # PHASE 1: Fix compound=False, SL=999, max_hold=24, TP=25, sweep RSI period + entry + exit
    print("\n=== PHASE 1: RSI Period + Entry + Exit sweep (no SL, fixed TP=25, hold=24) ===")
    for rp in rsi_periods:
        for os_t in os_threshes:
            for re_t in rsi_exits:
                cfg = {"rsi_period": rp, "os_thresh": os_t, "tp_pct": 25, "sl_pct": 999,
                       "max_hold": 24, "rsi_exit_ob": re_t, "compound": False}
                r = run_backtest(rave_candles, btc_lookup, cfg)
                r["config"] = f"RSI({rp}) OS<{os_t} Exit>{re_t} TP25 NoSL H24"
                results.append(r)
                iteration += 1

    top5 = sorted(results, key=lambda x: x["net"], reverse=True)[:5]
    print(f"  Ran {iteration} configs. Top 5:")
    for t in top5:
        print(f"    {t['config']}: ${t['net']:.2f} ({t['return_pct']}%) {t['closes']}tr {t['wr']}%WR")

    # PHASE 2: Take the best RSI period+entry+exit from phase 1, sweep TP, SL, max_hold
    best_phase1 = top5[0]
    best_config_parts = best_phase1["config"].split()
    # Parse: RSI(4) OS<30 Exit>80 TP25 NoSL H24
    best_rp = int(best_config_parts[0].split("(")[1].rstrip(")"))
    best_os = int(best_config_parts[1].split("<")[1])
    best_re = int(best_config_parts[2].split(">")[1])

    print(f"\n=== PHASE 2: Best from P1 = RSI({best_rp}) OS<{best_os} Exit>{best_re}")
    print(f"    Sweeping TP, SL, max_hold ===")

    phase2_results = []
    for tp in tps:
        for sl in sls:
            for mh in max_holds:
                cfg = {"rsi_period": best_rp, "os_thresh": best_os, "tp_pct": tp,
                       "sl_pct": sl, "max_hold": mh, "rsi_exit_ob": best_re, "compound": False}
                r = run_backtest(rave_candles, btc_lookup, cfg)
                sl_str = "NoSL" if sl == 999 else f"SL{sl}"
                r["config"] = f"RSI({best_rp}) OS<{best_os} Exit>{best_re} TP{tp} {sl_str} H{mh}"
                phase2_results.append(r)
                iteration += 1

    top5_p2 = sorted(phase2_results, key=lambda x: x["net"], reverse=True)[:10]
    print(f"  Ran {len(phase2_results)} configs. Top 10:")
    for t in top5_p2:
        print(f"    {t['config']}: ${t['net']:.2f} ({t['return_pct']}%) {t['closes']}tr {t['wr']}%WR fees=${t['total_fees']:.2f}")

    # PHASE 3: Take best from P2, test compound sizing
    best_phase2 = top5_p2[0]
    # Parse TP, SL, max_hold
    # Config format: RSI(4) OS<30 Exit>80 TP25 NoSL H24
    parts = best_phase2["config"].split()
    best_tp = int(parts[3][2:])
    best_sl_str = parts[4]
    best_sl = 999 if best_sl_str == "NoSL" else int(best_sl_str[2:])
    best_mh = int(parts[5][1:])

    print(f"\n=== PHASE 3: Best from P2 = {best_phase2['config']}")
    print(f"    Testing compound sizing ===")

    cfg_fixed = {"rsi_period": best_rp, "os_thresh": best_os, "tp_pct": best_tp,
                 "sl_pct": best_sl, "max_hold": best_mh, "rsi_exit_ob": best_re, "compound": False}
    r_fixed = run_backtest(rave_candles, btc_lookup, cfg_fixed)
    r_fixed["config"] = f"FIXED_SIZING: {best_phase2['config']}"

    cfg_compound = {"rsi_period": best_rp, "os_thresh": best_os, "tp_pct": best_tp,
                    "sl_pct": best_sl, "max_hold": best_mh, "rsi_exit_ob": best_re, "compound": True}
    r_compound = run_backtest(rave_candles, btc_lookup, cfg_compound)
    r_compound["config"] = f"COMPOUND: {best_phase2['config']}"
    iteration += 2

    print(f"  Fixed:   ${r_fixed['net']:.2f} ({r_fixed['return_pct']}%) {r_fixed['closes']}tr {r_fixed['wr']}%WR")
    print(f"  Compound: ${r_compound['net']:.2f} ({r_compound['return_pct']}%) {r_compound['closes']}tr {r_compound['wr']}%WR")

    # PHASE 4: Fine-grain sweep around best params
    print(f"\n=== PHASE 4: Fine-grain around best (±1 RSI, ±5 entry, ±5 exit) ===")
    phase4_results = []
    for rp in range(max(2, best_rp - 1), best_rp + 3):
        for os_t in range(max(10, best_os - 5), best_os + 6, 5):
            for re_t in range(max(50, best_re - 10), min(96, best_re + 11), 5):
                cfg = {"rsi_period": rp, "os_thresh": os_t, "tp_pct": best_tp,
                       "sl_pct": best_sl, "max_hold": best_mh, "rsi_exit_ob": re_t, "compound": False}
                r = run_backtest(rave_candles, btc_lookup, cfg)
                r["config"] = f"RSI({rp}) OS<{os_t} Exit>{re_t} TP{best_tp} {'NoSL' if best_sl==999 else 'SL'+str(best_sl)} H{best_mh}"
                phase4_results.append(r)
                iteration += 1

    top5_p4 = sorted(phase4_results, key=lambda x: x["net"], reverse=True)[:5]
    print(f"  Ran {len(phase4_results)} configs. Top 5:")
    for t in top5_p4:
        print(f"    {t['config']}: ${t['net']:.2f} ({t['return_pct']}%) {t['closes']}tr {t['wr']}%WR")

    # Combine ALL results
    all_results = results + phase2_results + [r_fixed, r_compound] + phase4_results
    all_results.sort(key=lambda x: x["net"], reverse=True)

    # Print top 20 overall
    print("\n" + "=" * 120)
    print("TOP 20 CONFIGS OVER ALL PHASES")
    print("=" * 120)
    print(f"{'Rank':<5} {'Config':<50} {'Net $':>8} {'Ret%':>7} {'Trades':>7} {'WR%':>6} {'Avg/Tr':>8} {'Fees':>8}")
    print("-" * 120)
    for rank, r in enumerate(all_results[:20], 1):
        print(f"{rank:<5} {r['config']:<50} ${r['net']:>7.2f} {r['return_pct']:>6.1f}% {r['closes']:>7} {r['wr']:>5.1f}% ${r['avg_trade']:>7.2f} ${r['total_fees']:>7.2f}")

    # Exit reason breakdown for #1
    champion = all_results[0]
    print(f"\n{'=' * 120}")
    print(f"🏆 CHAMPION: {champion['config']}")
    print(f"   Net: ${champion['net']:.2f} ({champion['return_pct']}%) | {champion['closes']} trades | {champion['wr']}% WR")
    print(f"   Exit reasons:")
    for reason, count in champion["exit_reasons"].items():
        if count > 0:
            pct = count / max(1, champion["closes"]) * 100
            print(f"     {reason}: {count} ({pct:.1f}%)")

    # Save everything
    output = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "total_configs_tested": iteration,
        "champion": champion,
        "top_20": all_results[:20],
    }
    out_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "reports", "ceiling_finder_results.json")
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "w") as f:
        json.dump(output, f, indent=2)
    print(f"\nResults saved to {out_path}")
    print(f"Total configs tested: {iteration}")

if __name__ == "__main__":
    main()
