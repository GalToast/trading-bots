#!/usr/bin/env python3
"""
Deep Lab — Questioning everything about the ceiling champion.
Testing assumptions we NEVER questioned before.
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
    if granularity == "FIFTEEN_MINUTE": chunk_sec = 900 * 5 * 60
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

def run_backtest_v2(candles, btc_lookup, m15_candles, config):
    """
    Advanced backtest supporting:
    - Partial exits (scale_out_at, scale_out_pct)
    - Signal-strength sizing (size increases as RSI goes lower)
    - Pyramiding (add_to_winners)
    - Volume confirmation on entry
    - M15 trend filter
    - Time-decay TP (TP shrinks over hold time)
    - Smart SL (structural, not fixed %)
    """
    starting_cash = 48.0
    cash = starting_cash
    positions = []  # List of positions (support pyramiding)
    closes = 0
    wins = 0
    total_volume = 0.0
    total_fees = 0.0
    history = []
    exit_reasons = {"tp": 0, "sl": 0, "timeout": 0, "rsi_ob": 0, "partial": 0}
    max_drawdown = 0
    peak_equity = starting_cash

    rsi_period = config.get("rsi_period", 3)
    os_thresh = config.get("os_thresh", 30)
    tp_pct = config.get("tp_pct", 50)
    max_hold = config.get("max_hold", 48)
    compound = config.get("compound", True)
    
    # New features
    scale_out_at = config.get("scale_out_at", None)  # e.g., 25 → exit 50% at 25%, rest at tp_pct
    signal_sizing = config.get("signal_sizing", None)  # e.g., "linear" → size = 0.95 * (1 - rsi/100)
    pyramid_at = config.get("pyramid_at", None)  # e.g., 10 → add position at +10%
    vol_confirm = config.get("vol_confirm", None)  # e.g., 1.5 → require volume > 1.5x avg
    m15_filter = config.get("m15_filter", None)  # e.g., "ranging" → only enter if M15 is ranging
    time_decay_tp = config.get("time_decay_tp", None)  # e.g., {"start": 50, "end": 10, "bars": 48}
    smart_sl = config.get("smart_sl", None)  # e.g., {"lookback": 20, "mult": 1.5} → ATR-based SL

    for i in range(len(candles)):
        c = candles[i]
        ts = int(c["start"])
        h = float(c["high"]); l = float(c["low"]); cl = float(c["close"])

        history.append(cl)
        if len(history) > 500: history.pop(0)

        # BTC Gate
        btc_gate = True
        p_t = ts - 60; p_t3 = ts - 180
        if p_t in btc_lookup and p_t3 in btc_lookup:
            mom = (btc_lookup[p_t] - btc_lookup[p_t3]) / btc_lookup[p_t3]
            if mom < -0.001: btc_gate = False

        # Session Gate
        hour = datetime.fromtimestamp(ts, tz=timezone.utc).hour
        session_gate = (hour not in [12, 19, 6, 0])

        # M15 Trend Filter
        m15_ok = True
        if m15_filter and m15_candles and len(m15_candles) > 20:
            # Get corresponding M15 candle
            m15_recent = m15_candles[:i//3+1] if i >= 0 else []
            if len(m15_recent) >= 10:
                m15_closes = [float(x["close"]) for x in m15_recent[-10:]]
                m15_range = (max(m15_closes) - min(m15_closes)) / min(m15_closes) * 100
                if m15_filter == "ranging" and m15_range > 5:
                    m15_ok = False
                elif m15_filter == "trending" and m15_range < 2:
                    m15_ok = False

        # Fee Tier
        if total_volume >= 50000: fr = 0.0015
        elif total_volume >= 10000: fr = 0.0025
        else: fr = 0.0040

        # Process exits for all positions
        positions_to_remove = []
        for pi, pos in enumerate(positions):
            pos["hold"] += 1
            
            # Current TP (may decay over time)
            current_tp_pct = tp_pct
            if time_decay_tp:
                progress = pos["hold"] / max(1, time_decay_tp["bars"])
                progress = min(1.0, progress)
                current_tp_pct = time_decay_tp["start"] * (1 - progress) + time_decay_tp["end"] * progress
            
            current_tp = pos["ep"] * (1 + current_tp_pct / 100.0)
            
            # Current SL (smart or fixed)
            current_sl = pos.get("sl", 0)
            if smart_sl and pos.get("atr_base") is not None:
                current_sl = pos["ep"] - smart_sl["mult"] * pos["atr_base"]

            exit_p = None
            exit_reason = None

            # Scale out logic
            if scale_out_at and not pos.get("scaled_out", False):
                scale_tp = pos["ep"] * (1 + scale_out_at / 100.0)
                if h >= scale_tp:
                    # Exit half
                    half_units = pos["units"] / 2
                    half_pnl = (scale_tp - pos["ep"]) * half_units - (pos["quote"] / 2 * fr) - (scale_tp * half_units * fr)
                    cash += pos["quote"] / 2 + half_pnl
                    total_volume += pos["quote"] / 2 + (scale_tp * half_units)
                    total_fees += pos["quote"] / 2 * fr + scale_tp * half_units * fr
                    pos["scaled_out"] = True
                    pos["quote"] = pos["quote"] / 2
                    pos["units"] = half_units
                    exit_reasons["partial"] = exit_reasons.get("partial", 0) + 1
                    # Don't close position, just scale

            # Full exit
            if h >= current_tp:
                exit_p = current_tp; exit_reason = "tp"
            if exit_p is None and l <= current_sl and current_sl > 0:
                exit_p = current_sl; exit_reason = "sl"
            if exit_p is None and pos["hold"] >= max_hold:
                exit_p = cl; exit_reason = "timeout"

            if exit_p is not None:
                units = pos["units"]
                pnl = (exit_p - pos["ep"]) * units - (pos["quote"] * fr) - (exit_p * units * fr)
                cash += pos["quote"] + pnl
                total_volume += pos["quote"] + (exit_p * units)
                total_fees += pos["quote"] * fr + exit_p * units * fr
                closes += 1
                if exit_p > pos["ep"]: wins += 1
                exit_reasons[exit_reason] = exit_reasons.get(exit_reason, 0) + 1
                positions_to_remove.append(pi)

        # Remove exited positions (reverse order)
        for pi in sorted(positions_to_remove, reverse=True):
            positions.pop(pi)

        # Pyramid: add to winners
        if pyramid_at and len(positions) > 0:
            pos = positions[0]
            if not pos.get("pyramided", False):
                pyramid_tp = pos["ep"] * (1 + pyramid_at / 100.0)
                if h >= pyramid_tp and cl >= pyramid_tp:
                    # Add another position
                    add_size = pos["quote"] * 0.5  # Add half the original size
                    if cash >= add_size:
                        new_ep = cl
                        positions.append({
                            "ep": new_ep, "quote": add_size, "hold": 0,
                            "tp": new_ep * (1 + tp_pct / 100.0),
                            "sl": new_ep * 0.001,
                            "units": add_size / new_ep,
                            "pyramided": True,
                            "atr_base": pos.get("atr_base"),
                        })
                        cash -= add_size

        # Track drawdown
        equity = cash + sum(p["quote"] for p in positions)
        peak_equity = max(peak_equity, equity)
        if peak_equity > 0:
            dd = (peak_equity - equity) / peak_equity * 100
            max_drawdown = max(max_drawdown, dd)

        # Entry
        if len(positions) == 0 and cash >= 10.0 and btc_gate and session_gate and m15_ok:
            if len(history) >= rsi_period + 2:
                rsi_prev = compute_rsi(history[:-1], rsi_period)
                if rsi_prev <= os_thresh:
                    # Volume confirmation
                    vol_ok = True
                    if vol_confirm and len(candles) > 0:
                        vol_lookback = candles[max(0,i-100):i]
                        if len(vol_lookback) > 10:
                            avg_vol = statistics.mean(float(x["volume"]) for x in vol_lookback)
                            cur_vol = float(c["volume"])
                            vol_ok = cur_vol > avg_vol * vol_confirm

                    if vol_ok:
                        ep = float(c["open"])
                        
                        # Signal-strength sizing
                        if signal_sizing == "linear":
                            # Lower RSI = bigger size. RSI 0 → 95%, RSI 30 → 50%
                            size_pct = 0.95 - (rsi_prev / os_thresh) * 0.45
                            size_pct = max(0.1, min(0.95, size_pct))
                        else:
                            size_pct = 0.95
                        
                        if compound:
                            tq = cash * size_pct
                        else:
                            tq = starting_cash
                        if tq > cash: tq = cash
                        
                        # ATR for smart SL
                        atr_base = None
                        if smart_sl and len(history) > smart_sl["lookback"]:
                            lookback = history[-smart_sl["lookback"]:]
                            atrs = []
                            for j in range(1, len(lookback)):
                                tr = abs(lookback[j] - lookback[j-1])
                                atrs.append(tr)
                            atr_base = statistics.mean(atrs) if atrs else None

                        if tq >= 10.0:
                            sl_price = ep * 0.001  # No SL by default
                            if smart_sl and atr_base:
                                sl_price = ep - smart_sl["mult"] * atr_base
                            
                            positions.append({
                                "ep": ep, "quote": tq, "hold": 0,
                                "tp": ep * (1 + tp_pct / 100.0),
                                "sl": sl_price,
                                "units": tq / ep,
                                "pyramided": False,
                                "scaled_out": False,
                                "atr_base": atr_base,
                            })
                            cash -= tq

    # Close remaining positions
    for pos in positions:
        cash += pos["quote"]

    net = cash - starting_cash
    wr = wins / max(1, closes) * 100
    avg_trade = net / max(1, closes)

    return {
        "net": round(net, 2), "return_pct": round(net / starting_cash * 100, 1),
        "closes": closes, "wr": round(wr, 1), "avg_trade": round(avg_trade, 2),
        "total_fees": round(total_fees, 2), "max_drawdown_pct": round(max_drawdown, 1),
        "exit_reasons": exit_reasons,
    }

def main():
    client = CoinbaseAdvancedClient()
    now = int(time.time())
    days = 11
    start = now - days * 24 * 3600

    print(f"Fetching {days}-day data for Deep Lab...")
    rave_m5 = fetch_candles(client, PRODUCT, start, now, "FIVE_MINUTE")
    rave_m15 = fetch_candles(client, PRODUCT, start, now, "FIFTEEN_MINUTE")
    btc_m1 = fetch_candles(client, BTC, start, now, "ONE_MINUTE")
    btc_lookup = {int(c["start"]): float(c["close"]) for c in btc_m1}
    print(f"  RAVE M5: {len(rave_m5)}, M15: {len(rave_m15)}, BTC M1: {len(btc_m1)}")

    results = []

    # BASELINE: Ceiling champion
    results.append({
        **run_backtest_v2(rave_m5, btc_lookup, rave_m15, {
            "rsi_period": 3, "os_thresh": 30, "tp_pct": 50, "max_hold": 48, "compound": True,
        }),
        "name": "BASELINE: RSI(3)+TP50+NoSL+H48+Compound"
    })

    # EXP 1: Partial exits — 50% at 25%, rest at 50%
    results.append({
        **run_backtest_v2(rave_m5, btc_lookup, rave_m15, {
            "rsi_period": 3, "os_thresh": 30, "tp_pct": 50, "max_hold": 48, "compound": True,
            "scale_out_at": 25,
        }),
        "name": "EXP1: Scale out 50%@25%, rest@50%"
    })

    # EXP 2: Signal-strength sizing
    results.append({
        **run_backtest_v2(rave_m5, btc_lookup, rave_m15, {
            "rsi_period": 3, "os_thresh": 30, "tp_pct": 50, "max_hold": 48, "compound": True,
            "signal_sizing": "linear",
        }),
        "name": "EXP2: Signal-strength sizing (lower RSI = bigger)"
    })

    # EXP 3: Smart SL (ATR-based, 2x ATR)
    results.append({
        **run_backtest_v2(rave_m5, btc_lookup, rave_m15, {
            "rsi_period": 3, "os_thresh": 30, "tp_pct": 50, "max_hold": 48, "compound": True,
            "smart_sl": {"lookback": 20, "mult": 2.0},
        }),
        "name": "EXP3: Smart SL (2x ATR)"
    })

    # EXP 4: M15 ranging filter
    results.append({
        **run_backtest_v2(rave_m5, btc_lookup, rave_m15, {
            "rsi_period": 3, "os_thresh": 30, "tp_pct": 50, "max_hold": 48, "compound": True,
            "m15_filter": "ranging",
        }),
        "name": "EXP4: M15 ranging filter"
    })

    # EXP 5: Volume confirmation (1.5x avg)
    results.append({
        **run_backtest_v2(rave_m5, btc_lookup, rave_m15, {
            "rsi_period": 3, "os_thresh": 30, "tp_pct": 50, "max_hold": 48, "compound": True,
            "vol_confirm": 1.5,
        }),
        "name": "EXP5: Volume confirmation (1.5x avg)"
    })

    # EXP 6: Pyramiding (add at +10%)
    results.append({
        **run_backtest_v2(rave_m5, btc_lookup, rave_m15, {
            "rsi_period": 3, "os_thresh": 30, "tp_pct": 50, "max_hold": 48, "compound": True,
            "pyramid_at": 10,
        }),
        "name": "EXP6: Pyramid at +10%"
    })

    # EXP 7: Time-decay TP (50% → 10% over 48 bars)
    results.append({
        **run_backtest_v2(rave_m5, btc_lookup, rave_m15, {
            "rsi_period": 3, "os_thresh": 30, "tp_pct": 50, "max_hold": 48, "compound": True,
            "time_decay_tp": {"start": 50, "end": 10, "bars": 48},
        }),
        "name": "EXP7: Time-decay TP (50%→10%)"
    })

    # EXP 8: Partial + Signal sizing
    results.append({
        **run_backtest_v2(rave_m5, btc_lookup, rave_m15, {
            "rsi_period": 3, "os_thresh": 30, "tp_pct": 50, "max_hold": 48, "compound": True,
            "scale_out_at": 25, "signal_sizing": "linear",
        }),
        "name": "EXP8: Scale out + Signal sizing"
    })

    # EXP 9: Volume + Partial
    results.append({
        **run_backtest_v2(rave_m5, btc_lookup, rave_m15, {
            "rsi_period": 3, "os_thresh": 30, "tp_pct": 50, "max_hold": 48, "compound": True,
            "vol_confirm": 1.5, "scale_out_at": 25,
        }),
        "name": "EXP9: Volume confirm + Scale out"
    })

    # EXP 10: Pyramiding + Partial
    results.append({
        **run_backtest_v2(rave_m5, btc_lookup, rave_m15, {
            "rsi_period": 3, "os_thresh": 30, "tp_pct": 50, "max_hold": 48, "compound": True,
            "pyramid_at": 10, "scale_out_at": 25,
        }),
        "name": "EXP10: Pyramid + Scale out"
    })

    # EXP 11: Volume confirmation + M15 filter
    results.append({
        **run_backtest_v2(rave_m5, btc_lookup, rave_m15, {
            "rsi_period": 3, "os_thresh": 30, "tp_pct": 50, "max_hold": 48, "compound": True,
            "vol_confirm": 1.5, "m15_filter": "ranging",
        }),
        "name": "EXP11: Volume + M15 filter"
    })

    # EXP 12: ALL combined
    results.append({
        **run_backtest_v2(rave_m5, btc_lookup, rave_m15, {
            "rsi_period": 3, "os_thresh": 30, "tp_pct": 50, "max_hold": 48, "compound": True,
            "scale_out_at": 25, "signal_sizing": "linear", "vol_confirm": 1.5,
            "time_decay_tp": {"start": 50, "end": 15, "bars": 48},
        }),
        "name": "EXP12: COMBINED (scale+signal+vol+decay)"
    })

    # EXP 13: RSI(2) — even faster
    results.append({
        **run_backtest_v2(rave_m5, btc_lookup, rave_m15, {
            "rsi_period": 2, "os_thresh": 30, "tp_pct": 50, "max_hold": 48, "compound": True,
        }),
        "name": "EXP13: RSI(2) ultra-fast"
    })

    # EXP 14: RSI(3) < 25 — deeper entry
    results.append({
        **run_backtest_v2(rave_m5, btc_lookup, rave_m15, {
            "rsi_period": 3, "os_thresh": 25, "tp_pct": 50, "max_hold": 48, "compound": True,
        }),
        "name": "EXP14: RSI(3) < 25 (deeper)"
    })

    # EXP 15: TP 75% — go even wider
    results.append({
        **run_backtest_v2(rave_m5, btc_lookup, rave_m15, {
            "rsi_period": 3, "os_thresh": 30, "tp_pct": 75, "max_hold": 60, "compound": True,
        }),
        "name": "EXP15: TP 75% + H60"
    })

    # EXP 16: RSI exit at 70 + TP 50%
    results.append({
        **run_backtest_v2(rave_m5, btc_lookup, rave_m15, {
            "rsi_period": 3, "os_thresh": 30, "tp_pct": 50, "max_hold": 48, "compound": True,
        }),
        "name": "EXP16: (RSI exit variant — need code update)"
    })

    # EXP 17: Conservative compound (80% instead of 95%)
    results.append({
        **run_backtest_v2(rave_m5, btc_lookup, rave_m15, {
            "rsi_period": 3, "os_thresh": 30, "tp_pct": 50, "max_hold": 48, "compound": True,
        }),
        "name": "EXP17: (sizing variant — need code update)"
    })

    # EXP 18: TP 40% + Scale out at 20% + Signal sizing
    results.append({
        **run_backtest_v2(rave_m5, btc_lookup, rave_m15, {
            "rsi_period": 3, "os_thresh": 30, "tp_pct": 40, "max_hold": 48, "compound": True,
            "scale_out_at": 20, "signal_sizing": "linear",
        }),
        "name": "EXP18: TP40% + Scale20% + Signal"
    })

    # EXP 19: TP 60% + H60 + Signal sizing
    results.append({
        **run_backtest_v2(rave_m5, btc_lookup, rave_m15, {
            "rsi_period": 3, "os_thresh": 30, "tp_pct": 60, "max_hold": 60, "compound": True,
            "signal_sizing": "linear",
        }),
        "name": "EXP19: TP60% + H60 + Signal"
    })

    # EXP 20: Volume confirm 2.0x (stronger confirmation)
    results.append({
        **run_backtest_v2(rave_m5, btc_lookup, rave_m15, {
            "rsi_period": 3, "os_thresh": 30, "tp_pct": 50, "max_hold": 48, "compound": True,
            "vol_confirm": 2.0,
        }),
        "name": "EXP20: Volume confirm 2.0x"
    })

    # Sort by net
    results.sort(key=lambda r: r["net"], reverse=True)

    # Print results
    print(f"\n{'=' * 110}")
    print("DEEP LAB RESULTS — 11 Days, All Experiments")
    print(f"{'=' * 110}")
    print(f"{'Config':<55} {'Net $':>8} {'Ret%':>7} {'Trades':>7} {'WR%':>6} {'Avg/Tr':>8} {'DD%':>6}")
    print("-" * 110)
    for r in results:
        marker = " ← BASELINE" if "BASELINE" in r["name"] else ""
        print(f"{r['name']:<55} ${r['net']:>7.2f} {r['return_pct']:>6.1f}% {r['closes']:>7} {r['wr']:>5.1f}% ${r['avg_trade']:>7.2f} {r['max_drawdown_pct']:>5.1f}%{marker}")

    # Exit reason breakdown for top 3
    print(f"\n{'=' * 110}")
    print("EXIT REASON BREAKDOWN — Top 5")
    print(f"{'=' * 110}")
    for r in results[:5]:
        print(f"\n  {r['name']}:")
        for reason, count in r["exit_reasons"].items():
            if count > 0:
                pct = count / max(1, r["closes"]) * 100
                print(f"    {reason}: {count} ({pct:.1f}%)")

    top = results[0]
    baseline = next(r for r in results if "BASELINE" in r["name"])
    if top["name"] != baseline["name"]:
        improvement = top["net"] - baseline["net"]
        print(f"\n🚨 NEW CHAMPION: {top['name']} at ${top['net']:.2f} (+${improvement:.2f} over baseline)")
    else:
        print(f"\n🎯 Baseline holds: ${baseline['net']:.2f} — no experiment beat it across 11 days.")

    # Save
    output = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "results": results,
    }
    out_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "reports", "deep_lab_results.json")
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "w") as f:
        json.dump(output, f, indent=2)
    print(f"\nResults saved to {out_path}")

if __name__ == "__main__":
    main()
