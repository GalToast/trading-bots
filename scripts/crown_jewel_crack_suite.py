#!/usr/bin/env python3
"""
Crown Jewel Crack Suite — Finding what beats RAVE RSI(4)+25% at +$79.45/72h

Experiments:
EXP 1: Exit asymmetry analysis — which exit actually triggers? (TP vs SL vs timeout vs RSI)
EXP 2: SL sweep — 3% vs 5% vs 7% vs 10% vs None (is 3% too tight for volatile RAVE?)
EXP 3: TP sweep — 25% vs 30% vs 40% vs 50% (are we leaving money on the table?)
EXP 4: 2-red-candle confirmation — require 2 consecutive red candles before entry
EXP 5: RSI exit gate — add RSI(4) > 70 exit (earlier than 80, catch reversals sooner)
EXP 6: Kelly-adaptive sizing — (WR*2-1) * cash, adjusted dynamically
EXP 7: Multi-coin rotation — RAVE + IOTX both running RSI(4)+25%, uncorrelated signals
EXP 8: Trailing stop — once up 10%, trail SL to breakeven; at 15%, trail 5% below high
EXP 9: RSI entry refinement — RSI(4) < 25 (deeper oversold) vs < 30 (current)
EXP 10: Combined champion — best of all worlds
"""
import json
import time
from datetime import datetime, timezone
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from coinbase_advanced_client import CoinbaseAdvancedClient

PRODUCTS = ["RAVE-USD", "IOTX-USD"]
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

def run_backtest(name, candles_map, btc_lookup, config):
    """
    config dict:
      - rsi_period: int
      - os_thresh: float (entry RSI < X)
      - tp_pct: float (take profit %)
      - sl_pct: float (stop loss %)
      - max_hold: int (max bars to hold)
      - rsi_exit_ob: float (exit if RSI > X, 0 = disabled)
      - two_red: bool (require 2 red candles before entry)
      - kelly_sizing: bool (use Kelly criterion vs fixed 95%)
      - trailing_stop: bool (trail stop after +10%)
      - product: str (which coin to trade)
      - multi_coin: bool (trade both RAVE + IOTX)
    """
    starting_cash = 48.0
    cash = starting_cash
    pos = None  # {"product": ..., "ep": ..., "quote": ..., "hold": 0, "tp": ..., "sl": ..., "highest": ...}
    closes = 0
    wins = 0
    total_volume = 0.0
    total_fees = 0.0
    history = {}  # product -> closes list
    exit_reasons = {"tp": 0, "sl": 0, "timeout": 0, "rsi_ob": 0, "rsi_exit_profit": 0, "rsi_exit_loss": 0}

    products_to_trade = PRODUCTS if config.get("multi_coin", False) else [config.get("product", "RAVE-USD")]

    # Build per-product histories
    for prod in products_to_trade:
        history[prod] = []

    # Align to shortest product to avoid index errors
    min_len = min(len(candles_map[p]) for p in products_to_trade)
    for i in range(min_len):
        # Get candle for each product at this timestep
        candle_batch = {}
        ts = None
        for prod in products_to_trade:
            c = candles_map[prod][i]
            candle_batch[prod] = c
            if ts is None:
                ts = int(c["start"])

        # Update histories
        for prod in products_to_trade:
            c = candle_batch[prod]
            cl = float(c["close"])
            history[prod].append(cl)
            if len(history[prod]) > 100: history[prod].pop(0)

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

        # Process Exit for active position
        if pos:
            prod = pos["product"]
            c = candle_batch[prod]
            h = float(c["high"]); l = float(c["low"]); cl = float(c["close"])
            pos["hold"] += 1
            pos["highest"] = max(pos["highest"], h)

            # Trailing stop logic
            current_sl = pos["sl"]
            if config.get("trailing_stop", False):
                if pos["highest"] >= pos["ep"] * 1.10:  # Up 10%
                    current_sl = max(current_sl, pos["ep"])  # At least breakeven
                if pos["highest"] >= pos["ep"] * 1.15:  # Up 15%
                    current_sl = max(current_sl, pos["highest"] * 0.95)  # Trail 5% below high

            exit_p = None
            exit_reason = None

            if h >= pos["tp"]:
                exit_p = pos["tp"]; exit_reason = "tp"
            elif l <= current_sl:
                exit_p = current_sl; exit_reason = "sl"
            elif pos["hold"] >= config["max_hold"]:
                exit_p = cl; exit_reason = "timeout"
            elif config.get("rsi_exit_ob", 0) > 0 and len(history[prod]) >= config["rsi_period"] + 1:
                rsi_now = compute_rsi(history[prod], config["rsi_period"])
                if rsi_now >= config["rsi_exit_ob"]:
                    exit_p = cl; exit_reason = "rsi_ob" if rsi_now > 50 else "rsi_exit_loss"
                    if cl > pos["ep"]:
                        exit_reason = "rsi_ob"
                    else:
                        exit_reason = "rsi_exit_loss"

            if exit_p is not None:
                units = pos["quote"] / pos["ep"]
                pnl = (exit_p - pos["ep"]) * units - (pos["quote"] * fr) - (exit_p * units * fr)
                cash += pos["quote"] + pnl
                total_volume += pos["quote"] + (exit_p * units)
                total_fees += pos["quote"] * fr + exit_p * units * fr
                closes += 1
                if exit_p > pos["ep"]:
                    wins += 1
                exit_reasons[exit_reason] = exit_reasons.get(exit_reason, 0) + 1
                pos = None

        # Process Entry (single position at a time)
        if pos is None and cash >= 10.0 and btc_gate and session_gate:
            # Try each product, pick the one with lowest RSI
            best_prod = None
            best_rsi = 999
            for prod in products_to_trade:
                if len(history[prod]) >= config["rsi_period"] + 2:
                    rsi_prev = compute_rsi(history[prod][:-1], config["rsi_period"])
                    if rsi_prev < best_rsi:
                        best_rsi = rsi_prev
                        best_prod = prod

            if best_prod and best_rsi <= config["os_thresh"]:
                # Check 2-red-candle confirmation
                if config.get("two_red", False):
                    prod_candles = candles_map[best_prod]
                    if i >= 2:
                        prev_close = float(prod_candles[i-1]["close"])
                        prev_open = float(prod_candles[i-1]["open"])
                        prev2_close = float(prod_candles[i-2]["close"])
                        prev2_open = float(prod_candles[i-2]["open"])
                        two_red = (prev_close < prev_open) and (prev2_close < prev2_open)
                        if not two_red:
                            best_prod = None

            if best_prod:
                c = candle_batch[best_prod]
                ep = float(c["open"])
                
                # Sizing
                if config.get("kelly_sizing", False) and closes >= 5:
                    recent_wr = wins / max(1, closes)
                    kelly = (recent_wr * 2 - 1) * 0.5  # Half-Kelly
                    kelly = max(0.1, min(0.95, kelly))  # Clamp 10%-95%
                    tq = cash * kelly
                else:
                    tq = cash * 0.95

                if tq >= 10.0:
                    pos = {
                        "product": best_prod,
                        "ep": ep, "quote": tq, "hold": 0,
                        "tp": ep * (1 + config["tp_pct"] / 100.0),
                        "sl": ep * (1 - config["sl_pct"] / 100.0),
                        "highest": ep,
                    }
                    cash -= tq

    # Close any open position at market
    if pos:
        cash += pos["quote"]

    net = cash - starting_cash
    wr = wins / max(1, closes) * 100
    avg_trade = net / max(1, closes)

    return {
        "name": name,
        "net": round(net, 2),
        "return_pct": round(net / starting_cash * 100, 1),
        "closes": closes,
        "wr": round(wr, 1),
        "avg_trade": round(avg_trade, 2),
        "total_volume": round(total_volume, 2),
        "total_fees": round(total_fees, 2),
        "exit_reasons": exit_reasons,
    }

def main():
    client = CoinbaseAdvancedClient()
    now = int(time.time())
    start = now - 72 * 3600

    print(f"Fetching 72h data for Crown Jewel Crack Suite...")
    rave_candles = fetch_candles(client, "RAVE-USD", start, now)
    iotx_candles = fetch_candles(client, "IOTX-USD", start, now)
    btc_m1 = fetch_candles(client, BTC, start, now, granularity="ONE_MINUTE")
    btc_lookup = {int(c["start"]): float(c["close"]) for c in btc_m1}

    print(f"  RAVE candles: {len(rave_candles)}")
    print(f"  IOTX candles: {len(iotx_candles)}")
    print(f"  BTC candles: {len(btc_m1)}")
    print()

    candles_map = {"RAVE-USD": rave_candles, "IOTX-USD": iotx_candles}

    results = []

    # BASELINE: Crown Jewel
    results.append(run_backtest("CROWN_JEWEL (baseline)", candles_map, btc_lookup, {
        "rsi_period": 4, "os_thresh": 30, "tp_pct": 25, "sl_pct": 3,
        "max_hold": 24, "rsi_exit_ob": 0, "two_red": False,
        "kelly_sizing": False, "trailing_stop": False,
        "product": "RAVE-USD", "multi_coin": False,
    }))

    # EXP 1: Exit Asymmetry Analysis (informational — same as baseline but reports exit breakdown)
    # Already captured in baseline exit_reasons

    # EXP 2: SL Sweep
    for sl in [5, 7, 10, 999]:  # 999 = effectively no SL
        sl_label = "NoSL" if sl == 999 else f"{sl}pct"
        results.append(run_backtest(f"SL_{sl_label}", candles_map, btc_lookup, {
            "rsi_period": 4, "os_thresh": 30, "tp_pct": 25, "sl_pct": sl,
            "max_hold": 24, "rsi_exit_ob": 0, "two_red": False,
            "kelly_sizing": False, "trailing_stop": False,
            "product": "RAVE-USD", "multi_coin": False,
        }))

    # EXP 3: TP Sweep
    for tp in [30, 40, 50]:
        results.append(run_backtest(f"TP_{tp}pct", candles_map, btc_lookup, {
            "rsi_period": 4, "os_thresh": 30, "tp_pct": tp, "sl_pct": 3,
            "max_hold": 24, "rsi_exit_ob": 0, "two_red": False,
            "kelly_sizing": False, "trailing_stop": False,
            "product": "RAVE-USD", "multi_coin": False,
        }))

    # EXP 4: 2-Red-Candle Confirmation
    results.append(run_backtest("TwoRed_Confirm", candles_map, btc_lookup, {
        "rsi_period": 4, "os_thresh": 30, "tp_pct": 25, "sl_pct": 3,
        "max_hold": 24, "rsi_exit_ob": 0, "two_red": True,
        "kelly_sizing": False, "trailing_stop": False,
        "product": "RAVE-USD", "multi_coin": False,
    }))

    # EXP 5: RSI Exit Gate (earlier exits)
    for rsi_exit in [60, 70, 80]:
        results.append(run_backtest(f"RSI_exit_{rsi_exit}", candles_map, btc_lookup, {
            "rsi_period": 4, "os_thresh": 30, "tp_pct": 25, "sl_pct": 3,
            "max_hold": 24, "rsi_exit_ob": rsi_exit, "two_red": False,
            "kelly_sizing": False, "trailing_stop": False,
            "product": "RAVE-USD", "multi_coin": False,
        }))

    # EXP 6: Kelly-Adaptive Sizing
    results.append(run_backtest("Kelly_Adaptive", candles_map, btc_lookup, {
        "rsi_period": 4, "os_thresh": 30, "tp_pct": 25, "sl_pct": 3,
        "max_hold": 24, "rsi_exit_ob": 0, "two_red": False,
        "kelly_sizing": True, "trailing_stop": False,
        "product": "RAVE-USD", "multi_coin": False,
    }))

    # EXP 7: Multi-Coin Rotation (RAVE + IOTX)
    results.append(run_backtest("MultiCoin_RAVE_IOTX", candles_map, btc_lookup, {
        "rsi_period": 4, "os_thresh": 30, "tp_pct": 25, "sl_pct": 3,
        "max_hold": 24, "rsi_exit_ob": 0, "two_red": False,
        "kelly_sizing": False, "trailing_stop": False,
        "product": "RAVE-USD", "multi_coin": True,
    }))

    # EXP 8: Trailing Stop
    results.append(run_backtest("Trailing_Stop", candles_map, btc_lookup, {
        "rsi_period": 4, "os_thresh": 30, "tp_pct": 25, "sl_pct": 3,
        "max_hold": 24, "rsi_exit_ob": 0, "two_red": False,
        "kelly_sizing": False, "trailing_stop": True,
        "product": "RAVE-USD", "multi_coin": False,
    }))

    # EXP 9: Deeper RSI Entry (< 25 vs < 30)
    results.append(run_backtest("RSI_25_entry", candles_map, btc_lookup, {
        "rsi_period": 4, "os_thresh": 25, "tp_pct": 25, "sl_pct": 3,
        "max_hold": 24, "rsi_exit_ob": 0, "two_red": False,
        "kelly_sizing": False, "trailing_stop": False,
        "product": "RAVE-USD", "multi_coin": False,
    }))

    # EXP 10: Combined Champion — Best of all worlds
    # Hypothesis: SL 7% (less noise), TP 30% (wider), RSI exit 70, trailing stop, two-red confirmation
    results.append(run_backtest("COMBINED_v1", candles_map, btc_lookup, {
        "rsi_period": 4, "os_thresh": 30, "tp_pct": 30, "sl_pct": 7,
        "max_hold": 24, "rsi_exit_ob": 70, "two_red": True,
        "kelly_sizing": False, "trailing_stop": True,
        "product": "RAVE-USD", "multi_coin": False,
    }))

    # EXP 11: Combined v2 — Multi-coin + trailing + wider SL
    results.append(run_backtest("COMBINED_v2_multi", candles_map, btc_lookup, {
        "rsi_period": 4, "os_thresh": 30, "tp_pct": 30, "sl_pct": 7,
        "max_hold": 24, "rsi_exit_ob": 70, "two_red": True,
        "kelly_sizing": False, "trailing_stop": True,
        "product": "RAVE-USD", "multi_coin": True,
    }))

    # EXP 12: RSI(3) ultra-fast — @assist found RSI(3)+25% at +$69.59, but with SL sweep?
    results.append(run_backtest("RSI3_SL7", candles_map, btc_lookup, {
        "rsi_period": 3, "os_thresh": 30, "tp_pct": 25, "sl_pct": 7,
        "max_hold": 24, "rsi_exit_ob": 0, "two_red": False,
        "kelly_sizing": False, "trailing_stop": False,
        "product": "RAVE-USD", "multi_coin": False,
    }))

    # Print results sorted by net profit
    results.sort(key=lambda r: r["net"], reverse=True)

    print("=" * 100)
    print("CROWN JEWEL CRACK SUITE RESULTS (72h backtest, $48 starting cash)")
    print("=" * 100)
    print(f"{'Config':<30} {'Net $':>8} {'Ret%':>7} {'Trades':>7} {'WR%':>6} {'Avg/Tr':>8} {'Fees':>8}")
    print("-" * 100)
    for r in results:
        baseline_marker = " ← BASELINE" if r["name"] == "CROWN_JEWEL (baseline)" else ""
        print(f"{r['name']:<30} ${r['net']:>7.2f} {r['return_pct']:>6.1f}% {r['closes']:>7} {r['wr']:>5.1f}% ${r['avg_trade']:>7.2f} ${r['total_fees']:>7.2f}{baseline_marker}")

    print()
    print("=" * 100)
    print("EXIT REASON BREAKDOWN (Baseline)")
    print("=" * 100)
    baseline = next(r for r in results if r["name"] == "CROWN_JEWEL (baseline)")
    for reason, count in baseline["exit_reasons"].items():
        if count > 0:
            pct = count / max(1, baseline["closes"]) * 100
            print(f"  {reason}: {count} ({pct:.1f}%)")

    print()
    top = results[0]
    if top["name"] != "CROWN_JEWEL (baseline)":
        improvement = top["net"] - baseline["net"]
        print(f"🚨 NEW CHAMPION: {top['name']} at ${top['net']:.2f} (+${improvement:.2f} over baseline)")
    else:
        print(f"🎯 Crown Jewel holds: ${baseline['net']:.2f} — no experiment beat it.")

    # Save results
    output = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "results": results,
    }
    out_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "reports", "crown_jewel_crack_results.json")
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "w") as f:
        json.dump(output, f, indent=2)
    print(f"\nResults saved to {out_path}")

if __name__ == "__main__":
    main()
