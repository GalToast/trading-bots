#!/usr/bin/env python3
"""
Regime-Gated Ceiling Champion — Only trade during explosive windows
Adds: ATR% > 2.0 AND Volume > 3x 24h baseline filter
"""
import json
import time
import statistics
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

def compute_rsi(closes, period=3):
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

def compute_atr_pct(candles, period=14):
    """Compute ATR% over recent candles."""
    if len(candles) < period + 1:
        return 0
    highs = [float(c["high"]) for c in candles[-period-1:]]
    lows = [float(c["low"]) for c in candles[-period-1:]]
    closes = [float(c["close"]) for c in candles[-period-1:]]
    trs = []
    for i in range(1, len(highs)):
        tr = max(highs[i] - lows[i], abs(highs[i] - closes[i-1]), abs(lows[i] - closes[i-1]))
        trs.append(tr)
    atr = statistics.mean(trs)
    avg_price = statistics.mean(closes)
    return atr / avg_price * 100 if avg_price > 0 else 0

def compute_volume_ratio(candles, baseline_period=288):
    """Volume vs 24h (288 M5 candles) average."""
    if len(candles) < baseline_period + 1:
        return 1.0
    recent_vol = float(candles[-1]["volume"])
    baseline_vol = statistics.mean(float(c["volume"]) for c in candles[-baseline_period-1:-1])
    return recent_vol / baseline_vol if baseline_vol > 0 else 1.0

def main():
    client = CoinbaseAdvancedClient()
    now = int(time.time())
    days = 11
    start = now - days * 24 * 3600

    print(f"Fetching {days}-day data for regime-gated test...")
    rave_candles = fetch_candles(client, PRODUCT, start, now)
    btc_m1 = fetch_candles(client, BTC, start, now, granularity="ONE_MINUTE")
    btc_lookup = {int(c["start"]): float(c["close"]) for c in btc_m1}
    print(f"  RAVE: {len(rave_candles)}, BTC: {len(btc_m1)}")

    # Run both versions: gated vs ungated
    configs = [
        {"name": "UNGATED", "atr_threshold": 0, "vol_threshold": 0},
        {"name": "GATED_ATR2", "atr_threshold": 2.0, "vol_threshold": 0},
        {"name": "GATED_VOL3x", "atr_threshold": 0, "vol_threshold": 3.0},
        {"name": "GATED_ATR2_VOL3x", "atr_threshold": 2.0, "vol_threshold": 3.0},
        {"name": "GATED_ATR15_VOL2x", "atr_threshold": 1.5, "vol_threshold": 2.0},
        {"name": "GATED_ATR1_VOL2x", "atr_threshold": 1.0, "vol_threshold": 2.0},
    ]

    results = []

    for cfg in configs:
        cash = 48.0
        pos = None
        closes = 0
        wins = 0
        total_volume = 0.0
        total_fees = 0.0
        history = []
        candles_used = []
        regime_filtered_out = 0
        exit_reasons = {"tp": 0, "sl": 0, "timeout": 0, "rsi_ob": 0}

        for i in range(len(rave_candles)):
            c = rave_candles[i]
            ts = int(c["start"])
            h = float(c["high"]); l = float(c["low"]); cl = float(c["close"])

            history.append(cl)
            candles_used.append(c)
            if len(history) > 500: history.pop(0)
            if len(candles_used) > 500: candles_used.pop(0)

            # BTC Gate
            btc_gate = True
            p_t = ts - 60; p_t3 = ts - 180
            if p_t in btc_lookup and p_t3 in btc_lookup:
                mom = (btc_lookup[p_t] - btc_lookup[p_t3]) / btc_lookup[p_t3]
                if mom < -0.001: btc_gate = False

            # Session Gate
            hour = datetime.fromtimestamp(ts, tz=timezone.utc).hour
            session_gate = (hour not in [12, 19, 6, 0])

            # REGIME GATE
            regime_ok = True
            if cfg["atr_threshold"] > 0 or cfg["vol_threshold"] > 0:
                atr = compute_atr_pct(candles_used)
                vol_ratio = compute_volume_ratio(candles_used)
                
                if cfg["atr_threshold"] > 0 and atr < cfg["atr_threshold"]:
                    regime_ok = False
                if cfg["vol_threshold"] > 0 and vol_ratio < cfg["vol_threshold"]:
                    regime_ok = False
                
                if not regime_ok:
                    regime_filtered_out += 1

            # Fee Tier
            if total_volume >= 50000: fr = 0.0015
            elif total_volume >= 10000: fr = 0.0025
            else: fr = 0.0040

            # Exit
            if pos:
                pos["hold"] += 1
                exit_p = None
                exit_reason = None

                if h >= pos["tp"]:
                    exit_p = pos["tp"]; exit_reason = "tp"
                if exit_p is None and l <= pos["sl"]:
                    exit_p = pos["sl"]; exit_reason = "sl"
                if exit_p is None and pos["hold"] >= 48:
                    exit_p = cl; exit_reason = "timeout"

                if exit_p is not None:
                    units = pos["quote"] / pos["ep"]
                    pnl = (exit_p - pos["ep"]) * units - (pos["quote"] * fr) - (exit_p * units * fr)
                    cash += pos["quote"] + pnl
                    total_volume += pos["quote"] + (exit_p * units)
                    total_fees += pos["quote"] * fr + exit_p * units * fr
                    closes += 1
                    if exit_p > pos["ep"]: wins += 1
                    exit_reasons[exit_reason] = exit_reasons.get(exit_reason, 0) + 1
                    pos = None

            # Entry
            if pos is None and cash >= 10.0 and btc_gate and session_gate and regime_ok:
                if len(history) >= 5:
                    rsi_prev = compute_rsi(history[:-1])
                    if rsi_prev <= 30:
                        ep = float(c["open"])
                        tq = cash * 0.95  # Compound
                        if tq >= 10.0:
                            pos = {
                                "ep": ep, "quote": tq, "hold": 0,
                                "tp": ep * 1.50,  # 50% TP
                                "sl": ep * 0.001,  # Effectively no SL
                            }
                            cash -= tq

        if pos:
            cash += pos["quote"]

        net = cash - 48.0
        wr = wins / max(1, closes) * 100

        results.append({
            "name": cfg["name"],
            "net": round(net, 2),
            "return_pct": round(net / 48 * 100, 1),
            "closes": closes,
            "wr": round(wr, 1),
            "avg_trade": round(net / max(1, closes), 2),
            "total_fees": round(total_fees, 2),
            "regime_filtered_out": regime_filtered_out,
            "exit_reasons": exit_reasons,
        })

    # Print results
    print(f"\n{'=' * 100}")
    print("REGIME-GATED CEILING CHAMPION")
    print(f"{'=' * 100}")
    print(f"{'Config':<25} {'Net $':>8} {'Ret%':>7} {'Trades':>7} {'WR%':>6} {'Avg/Tr':>8} {'Fees':>8} {'Filtered':>9}")
    print("-" * 100)
    for r in sorted(results, key=lambda x: x["net"], reverse=True):
        baseline = " ← BASELINE" if r["name"] == "UNGATED" else ""
        print(f"{r['name']:<25} ${r['net']:>7.2f} {r['return_pct']:>6.1f}% {r['closes']:>7} {r['wr']:>5.1f}% ${r['avg_trade']:>7.2f} ${r['total_fees']:>7.2f} {r['regime_filtered_out']:>9}{baseline}")

    # Walk-forward test: does the gated version improve consistency?
    print(f"\n{'=' * 100}")
    print("WALK-FORWARD: GATED vs UNGATED (3 x 72h windows)")
    print(f"{'=' * 100}")

    candles_per_72h = int(72 * 60 / 5)
    total_candles = len(rave_candles)
    num_windows = total_candles // candles_per_72h

    for window_idx in range(num_windows):
        w_start = window_idx * candles_per_72h
        w_end = min((window_idx + 1) * candles_per_72h, total_candles)
        window_candles = rave_candles[w_start:w_end]

        # Build BTC lookup
        w_start_ts = int(window_candles[0]["start"])
        w_end_ts = int(window_candles[-1]["start"])
        w_btc = {k: v for k, v in btc_lookup.items() if w_start_ts - 300 <= k <= w_end_ts + 300}

        # Run ungated
        ug_net = _run_window(window_candles, w_btc, atr_thresh=0, vol_thresh=0)
        # Run gated
        g_net = _run_window(window_candles, w_btc, atr_thresh=2.0, vol_thresh=3.0)

        day_label = f"Days {window_idx*3+1}-{(window_idx+1)*3}"
        print(f"  {day_label}: Ungated=${ug_net:.2f} | Gated=${g_net:.2f}")

    # Save
    output = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "results": results,
    }
    out_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "reports", "regime_gated_results.json")
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "w") as f:
        json.dump(output, f, indent=2)
    print(f"\nResults saved to {out_path}")

def _run_window(candles, btc_lookup, atr_thresh=0, vol_thresh=0):
    """Quick window runner for walk-forward test."""
    import statistics
    cash = 48.0
    pos = None
    history = []
    candles_used = []
    total_volume = 0.0

    for i in range(len(candles)):
        c = candles[i]
        ts = int(c["start"])
        h = float(c["high"]); l = float(c["low"]); cl = float(c["close"])
        history.append(cl)
        candles_used.append(c)
        if len(history) > 500: history.pop(0)
        if len(candles_used) > 500: candles_used.pop(0)

        # BTC Gate
        btc_gate = True
        p_t = ts - 60; p_t3 = ts - 180
        if p_t in btc_lookup and p_t3 in btc_lookup:
            mom = (btc_lookup[p_t] - btc_lookup[p_t3]) / btc_lookup[p_t3]
            if mom < -0.001: btc_gate = False

        # Session Gate
        hour = datetime.fromtimestamp(ts, tz=timezone.utc).hour
        session_gate = (hour not in [12, 19, 6, 0])

        # REGIME GATE
        regime_ok = True
        if atr_thresh > 0 or vol_thresh > 0:
            if len(candles_used) >= 15:
                highs = [float(x["high"]) for x in candles_used[-15:]]
                lows = [float(x["low"]) for x in candles_used[-15:]]
                closes = [float(x["close"]) for x in candles_used[-15:]]
                trs = [max(highs[j] - lows[j], abs(highs[j] - closes[j-1]), abs(lows[j] - closes[j-1])) for j in range(1, len(highs))]
                atr = statistics.mean(trs) if trs else 0
                avg_price = statistics.mean(closes)
                atr_pct = atr / avg_price * 100 if avg_price > 0 else 0
                
                if len(candles_used) >= 289:
                    recent_vol = float(candles_used[-1]["volume"])
                    baseline_vol = statistics.mean(float(x["volume"]) for x in candles_used[-289:-1])
                    vol_ratio = recent_vol / baseline_vol if baseline_vol > 0 else 1.0
                else:
                    vol_ratio = 1.0

                if atr_thresh > 0 and atr_pct < atr_thresh:
                    regime_ok = False
                if vol_thresh > 0 and vol_ratio < vol_thresh:
                    regime_ok = False

        # Fee
        if total_volume >= 50000: fr = 0.0015
        elif total_volume >= 10000: fr = 0.0025
        else: fr = 0.0040

        # Exit
        if pos:
            pos["hold"] += 1
            exit_p = None
            if h >= pos["tp"]: exit_p = pos["tp"]
            if exit_p is None and l <= pos["sl"]: exit_p = pos["sl"]
            if exit_p is None and pos["hold"] >= 48: exit_p = cl

            if exit_p is not None:
                units = pos["quote"] / pos["ep"]
                pnl = (exit_p - pos["ep"]) * units - (pos["quote"] * fr) - (exit_p * units * fr)
                cash += pos["quote"] + pnl
                total_volume += pos["quote"] + (exit_p * units)
                pos = None

        # Entry
        if pos is None and cash >= 10.0 and btc_gate and session_gate and regime_ok:
            if len(history) >= 5:
                deltas = [history[j] - history[j-1] for j in range(1, len(history))]
                gains = [d if d > 0 else 0 for d in deltas[-3:]]
                losses = [-d if d < 0 else 0 for d in deltas[-3:]]
                avg_gain = sum(gains) / 3
                avg_loss = sum(losses) / 3
                rsi = 100 - 100 / (1 + avg_gain / avg_loss) if avg_loss > 0 else 100
                if rsi <= 30:
                    ep = float(c["open"])
                    tq = cash * 0.95
                    if tq >= 10.0:
                        pos = {"ep": ep, "quote": tq, "hold": 0, "tp": ep * 1.50, "sl": ep * 0.001}
                        cash -= tq

    if pos: cash += pos["quote"]
    return cash - 48.0

if __name__ == "__main__":
    main()
