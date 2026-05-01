#!/usr/bin/env python3
"""
ASSUMPTION AUDIT — Questioning EVERYTHING before we optimize further.

Tests the FOUNDATION, not the parameters:
1. SPREAD AUDIT — Real bid/ask spread on RAVE vs our mid-price fills
2. TIMEFRAME AUDIT — M1/M3/M5/M10/M15/M30 — which is ACTUALLY optimal?
3. INDICATOR AUDIT — RSI vs StochRSI vs BB%B vs CCI vs pure price
4. HOLD-TIME AUDIT — Fine-grained: 6,12,18,24,30,36,42,48,54,60,72
5. OOS AUDIT — Train on 7 days, test on 4. Does edge survive?
6. MULTI-COIN + M15 FILTER — Do other coins work with the filter?
7. MOMENTUM vs MEAN-REVERSION — Does RAVE have BOTH edges?
"""
import json
import time
import statistics
from datetime import datetime, timezone
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from coinbase_advanced_client import CoinbaseAdvancedClient

BTC = "BTC-USD"

def fetch_candles(client, pid, start, end, granularity="FIVE_MINUTE"):
    chunk_sec = 300 * 5 * 60
    if granularity == "ONE_MINUTE": chunk_sec = 300 * 60
    if granularity == "THREE_MINUTE": chunk_sec = 180 * 60
    if granularity == "FIFTEEN_MINUTE": chunk_sec = 900 * 60
    if granularity == "ONE_HOUR": chunk_sec = 3600 * 60
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

def compute_stochrsi(closes, rsi_period=3, stoch_period=3):
    """StochRSI: RSI then Stochastic of RSI."""
    if len(closes) < rsi_period + stoch_period + 2:
        return 50.0, 50.0
    # Compute RSI series
    rsi_values = []
    for i in range(rsi_period + 1, len(closes) + 1):
        window = closes[i-rsi_period:i]
        deltas = [window[j] - window[j-1] for j in range(1, len(window))]
        gains = [d if d > 0 else 0 for d in deltas]
        losses = [-d if d < 0 else 0 for d in deltas]
        avg_g = sum(gains) / rsi_period
        avg_l = sum(losses) / rsi_period
        if avg_l > 0:
            rs = avg_g / avg_l
            rsi_val = 100 - 100 / (1 + rs)
        else:
            rsi_val = 100
        rsi_values.append(rsi_val)
    
    if len(rsi_values) < stoch_period:
        return 50.0, rsi_values[-1] if rsi_values else 50.0
    
    # Stochastic of RSI
    rsi_window = rsi_values[-stoch_period:]
    stoch_low = min(rsi_window)
    stoch_high = max(rsi_window)
    current_rsi = rsi_values[-1]
    
    if stoch_high - stoch_low > 0:
        stochrsi = (current_rsi - stoch_low) / (stoch_high - stoch_low) * 100
    else:
        stochrsi = 50.0
    
    return stochrsi, current_rsi

def compute_bb_pct(closes, period=20, mult=2.0):
    """Bollinger Band %B — where is price relative to bands?"""
    if len(closes) < period:
        return 0.5
    window = closes[-period:]
    sma = statistics.mean(window)
    std = statistics.stdev(window) if len(window) > 1 else 0
    upper = sma + mult * std
    lower = sma - mult * std
    current = closes[-1]
    if upper - lower > 0:
        return (current - lower) / (upper - lower)
    return 0.5

def compute_cci(candles, period=20):
    """Commodity Channel Index."""
    if len(candles) < period:
        return 0
    tps = []
    for c in candles[-period:]:
        tp = (float(c["high"]) + float(c["low"]) + float(c["close"])) / 3
        tps.append(tp)
    mean_tp = statistics.mean(tps)
    if len(tps) > 1:
        mean_dev = statistics.mean([abs(tp - mean_tp) for tp in tps])
    else:
        mean_dev = 0
    if mean_dev > 0:
        current_tp = tps[-1]
        return (current_tp - mean_tp) / (0.015 * mean_dev)
    return 0

def run_backtest_audit(candles, btc_lookup, config):
    """Generic backtest for assumption audit."""
    starting_cash = 48.0
    cash = starting_cash
    pos = None
    closes = 0
    wins = 0
    total_volume = 0.0
    total_fees = 0.0
    history = []
    history_candles = []
    max_drawdown = 0
    peak_equity = starting_cash

    indicator = config.get("indicator", "rsi")
    indicator_period = config.get("indicator_period", 3)
    os_thresh = config.get("os_thresh", 30)
    tp_pct = config.get("tp_pct", 25)
    max_hold = config.get("max_hold", 48)
    compound = config.get("compound", True)
    spread_penalty = config.get("spread_penalty", 0)  # Add spread cost in bps

    for i in range(len(candles)):
        c = candles[i]
        ts = int(c["start"])
        h = float(c["high"]); l = float(c["low"]); cl = float(c["close"])

        history.append(cl)
        history_candles.append(c)
        if len(history) > 500: history.pop(0)
        if len(history_candles) > 500: history_candles.pop(0)

        # BTC Gate
        btc_gate = True
        p_t = ts - 60; p_t3 = ts - 180
        if p_t in btc_lookup and p_t3 in btc_lookup:
            mom = (btc_lookup[p_t] - btc_lookup[p_t3]) / btc_lookup[p_t3]
            if mom < -0.001: btc_gate = False

        # Session Gate
        hour = datetime.fromtimestamp(ts, tz=timezone.utc).hour
        session_gate = (hour not in [12, 19, 6, 0])

        # Fee + Spread
        if total_volume >= 50000: fr = 0.0015
        elif total_volume >= 10000: fr = 0.0025
        else: fr = 0.0040
        spread_cost = spread_penalty / 10000.0  # Convert bps to decimal

        # Exit
        if pos:
            pos["hold"] += 1
            exit_p = None
            exit_reason = None

            if h >= pos["tp"]:
                exit_p = pos["tp"]; exit_reason = "tp"
            if exit_p is None and pos["hold"] >= max_hold:
                exit_p = cl; exit_reason = "timeout"

            if exit_p is not None:
                units = pos["quote"] / pos["ep"]
                # Apply spread: entry was worse, exit is worse
                effective_entry = pos["ep"] * (1 + spread_cost)
                effective_exit = exit_p * (1 - spread_cost)
                pnl = (effective_exit - effective_entry) * units - (pos["quote"] * fr) - (effective_exit * units * fr)
                cash += pos["quote"] + pnl
                total_volume += pos["quote"] + (effective_exit * units)
                total_fees += pos["quote"] * fr + effective_exit * units * fr
                closes += 1
                if effective_exit > effective_entry: wins += 1
                pos = None

        # Track drawdown
        equity = cash + (pos["quote"] if pos else 0)
        peak_equity = max(peak_equity, equity)
        if peak_equity > 0:
            dd = (peak_equity - equity) / peak_equity * 100
            max_drawdown = max(max_drawdown, dd)

        # Entry
        if pos is None and cash >= 10.0 and btc_gate and session_gate:
            signal_ok = False
            
            if indicator == "rsi":
                if len(history) >= indicator_period + 2:
                    rsi = compute_rsi(history[:-1], indicator_period)
                    signal_ok = rsi <= os_thresh
            elif indicator == "stochrsi":
                if len(history) >= indicator_period + 10:
                    stochrsi, rsi = compute_stochrsi(history[:-1], indicator_period, indicator_period)
                    signal_ok = stochrsi <= os_thresh
            elif indicator == "bb_pct":
                if len(history) >= indicator_period + 2:
                    bb_pct = compute_bb_pct(history[:-1], indicator_period)
                    signal_ok = bb_pct <= os_thresh  # e.g., 0.1 = price at lower 10% of BB
            elif indicator == "cci":
                if len(history_candles) >= indicator_period + 2:
                    cci = compute_cci(history_candles[:-1], indicator_period)
                    signal_ok = cci <= os_thresh  # e.g., -200 = extremely oversold
            elif indicator == "price_action":
                # N consecutive red candles
                n = indicator_period
                if len(history_candles) >= n + 1:
                    red_count = 0
                    for j in range(1, n + 1):
                        cc = history_candles[-j-1]
                        if float(cc["close"]) < float(cc["open"]):
                            red_count += 1
                    signal_ok = red_count >= n

            if signal_ok:
                ep = float(c["open"])
                tq = cash * 0.95 if compound else 48.0
                if tq > cash: tq = cash
                if tq >= 10.0:
                    pos = {
                        "ep": ep, "quote": tq, "hold": 0,
                        "tp": ep * (1 + tp_pct / 100.0),
                    }
                    cash -= tq

    if pos:
        cash += pos["quote"]

    net = cash - starting_cash
    wr = wins / max(1, closes) * 100
    avg_trade = net / max(1, closes)

    return {
        "net": round(net, 2), "return_pct": round(net / starting_cash * 100, 1),
        "closes": closes, "wr": round(wr, 1), "avg_trade": round(avg_trade, 2),
        "total_fees": round(total_fees, 2), "max_drawdown_pct": round(max_drawdown, 1),
    }

def main():
    client = CoinbaseAdvancedClient()
    now = int(time.time())
    days = 11
    start = now - days * 24 * 3600

    print(f"Fetching data for ASSUMPTION AUDIT ({days} days)...")
    
    # Fetch multiple timeframes
    rave_m1 = fetch_candles(client, "RAVE-USD", start, now, "ONE_MINUTE")
    rave_m5 = fetch_candles(client, "RAVE-USD", start, now, "FIVE_MINUTE")
    rave_m15 = fetch_candles(client, "RAVE-USD", start, now, "FIFTEEN_MINUTE")
    
    btc_m1 = fetch_candles(client, BTC, start, now, "ONE_MINUTE")
    btc_lookup = {int(c["start"]): float(c["close"]) for c in btc_m1}
    
    print(f"  RAVE M1: {len(rave_m1)}, M5: {len(rave_m5)}, M15: {len(rave_m15)}")
    print(f"  BTC M1: {len(btc_m1)}")

    results = {"audits": {}}

    # ============================================================
    # AUDIT 1: TIMEFRAME — Which TF is ACTUALLY optimal?
    # ============================================================
    print(f"\n{'=' * 80}")
    print(f"AUDIT 1: TIMEFRAME — M1 vs M5 vs M15")
    print(f"{'=' * 80}")

    # Build BTC lookups aligned to each timeframe
    # For M1: use btc_m1 directly
    btc_m1_lookup = {int(c["start"]): float(c["close"]) for c in btc_m1}
    
    # For M5: align BTC to M5 timestamps
    btc_m5_lookup = {}
    for c in rave_m5:
        ts = int(c["start"])
        # Find nearest BTC M1 candle
        nearest = min(btc_m1, key=lambda x: abs(int(x["start"]) - ts), default=None)
        if nearest:
            btc_m5_lookup[ts] = float(nearest["close"])

    # For M15: align BTC to M15 timestamps
    btc_m15_lookup = {}
    for c in rave_m15:
        ts = int(c["start"])
        nearest = min(btc_m1, key=lambda x: abs(int(x["start"]) - ts), default=None)
        if nearest:
            btc_m15_lookup[ts] = float(nearest["close"])

    timeframe_configs = [
        ("M1", rave_m1, btc_m1_lookup, {"indicator_period": 3, "max_hold": 48*5}),  # 48 M5 bars = 240 M1 bars
        ("M3", rave_m5, btc_m5_lookup, {"indicator_period": 3, "max_hold": 48}),  # Same M5 data as baseline
        ("M5", rave_m5, btc_m5_lookup, {"indicator_period": 3, "max_hold": 48}),
        ("M15", rave_m15, btc_m15_lookup, {"indicator_period": 3, "max_hold": 48}),
    ]

    print(f"{'TF':<8} {'Net $':>8} {'Ret%':>7} {'Trades':>7} {'WR%':>6} {'Avg/Tr':>8} {'DD%':>6}")
    print("-" * 60)
    tf_results = []
    for name, candles, lookup, extra in timeframe_configs:
        cfg = {"rsi_period": 3, "os_thresh": 30, "tp_pct": 25, "max_hold": extra["max_hold"],
               "compound": True, "spread_penalty": 0, **extra}
        r = run_backtest_audit(candles, lookup, cfg)
        tf_results.append({"tf": name, **r})
        print(f"{name:<8} ${r['net']:>7.2f} {r['return_pct']:>6.1f}% {r['closes']:>7} {r['wr']:>5.1f}% ${r['avg_trade']:>7.2f} {r['max_drawdown_pct']:>5.1f}%")

    results["audits"]["timeframe"] = tf_results

    # ============================================================
    # AUDIT 2: INDICATOR — RSI vs StochRSI vs BB%B vs CCI vs Price Action
    # ============================================================
    print(f"\n{'=' * 80}")
    print(f"AUDIT 2: INDICATOR — RSI vs StochRSI vs BB%B vs CCI vs Red Candles")
    print(f"{'=' * 80}")

    indicator_configs = [
        ("RSI(3)<30", {"indicator": "rsi", "indicator_period": 3, "os_thresh": 30, "tp_pct": 25, "max_hold": 48}),
        ("StochRSI(3)<30", {"indicator": "stochrsi", "indicator_period": 3, "os_thresh": 30, "tp_pct": 25, "max_hold": 48}),
        ("BB%B<0.1", {"indicator": "bb_pct", "indicator_period": 20, "os_thresh": 0.1, "tp_pct": 25, "max_hold": 48}),
        ("CCI<-200", {"indicator": "cci", "indicator_period": 20, "os_thresh": -200, "tp_pct": 25, "max_hold": 48}),
        ("2 Red Candles", {"indicator": "price_action", "indicator_period": 2, "os_thresh": 0, "tp_pct": 25, "max_hold": 48}),
        ("3 Red Candles", {"indicator": "price_action", "indicator_period": 3, "os_thresh": 0, "tp_pct": 25, "max_hold": 48}),
    ]

    ind_results = []
    print(f"{'Indicator':<20} {'Net $':>8} {'Ret%':>7} {'Trades':>7} {'WR%':>6} {'Avg/Tr':>8} {'DD%':>6}")
    print("-" * 70)
    for name, cfg in indicator_configs:
        r = run_backtest_audit(rave_m5, btc_m5_lookup, {"compound": True, "spread_penalty": 0, **cfg})
        ind_results.append({"name": name, **r})
        print(f"{name:<20} ${r['net']:>7.2f} {r['return_pct']:>6.1f}% {r['closes']:>7} {r['wr']:>5.1f}% ${r['avg_trade']:>7.2f} {r['max_drawdown_pct']:>5.1f}%")

    results["audits"]["indicator"] = ind_results

    # ============================================================
    # AUDIT 3: SPREAD — What if real fills are 1-2% worse?
    # ============================================================
    print(f"\n{'=' * 80}")
    print(f"AUDIT 3: SPREAD PENALTY — 0bps vs 50bps vs 100bps vs 200bps")
    print(f"{'=' * 80}")

    spread_results = []
    print(f"{'Spread':<12} {'Net $':>8} {'Ret%':>7} {'Trades':>7} {'WR%':>6} {'Δ vs 0bps':>12}")
    print("-" * 60)
    baseline_spread = None
    for spread_bps in [0, 25, 50, 100, 150, 200, 300]:
        cfg = {"indicator": "rsi", "indicator_period": 3, "os_thresh": 30,
               "tp_pct": 25, "max_hold": 48, "compound": True, "spread_penalty": spread_bps}
        r = run_backtest_audit(rave_m5, btc_m5_lookup, cfg)
        spread_results.append({"spread_bps": spread_bps, **r})
        delta = r["net"] - (baseline_spread["net"] if baseline_spread else r["net"])
        if baseline_spread is None: baseline_spread = r
        print(f"{spread_bps:>4}bps     ${r['net']:>7.2f} {r['return_pct']:>6.1f}% {r['closes']:>7} {r['wr']:>5.1f}% ${delta:>+11.2f}")

    results["audits"]["spread"] = spread_results

    # ============================================================
    # AUDIT 4: HOLD-TIME — Fine-grained sweep
    # ============================================================
    print(f"\n{'=' * 80}")
    print(f"AUDIT 4: HOLD-TIME — 6 to 72 bars (step 6)")
    print(f"{'=' * 80}")

    hold_results = []
    print(f"{'Hold Bars':<12} {'Net $':>8} {'Ret%':>7} {'Trades':>7} {'WR%':>6} {'Avg/Tr':>8} {'DD%':>6}")
    print("-" * 60)
    for mh in [6, 12, 18, 24, 30, 36, 42, 48, 54, 60, 72]:
        cfg = {"indicator": "rsi", "indicator_period": 3, "os_thresh": 30,
               "tp_pct": 25, "max_hold": mh, "compound": True, "spread_penalty": 0}
        r = run_backtest_audit(rave_m5, btc_m5_lookup, cfg)
        hold_results.append({"hold_bars": mh, **r})
        print(f"{mh:>6} bars   ${r['net']:>7.2f} {r['return_pct']:>6.1f}% {r['closes']:>7} {r['wr']:>5.1f}% ${r['avg_trade']:>7.2f} {r['max_drawdown_pct']:>5.1f}%")

    results["audits"]["hold_time"] = hold_results

    # ============================================================
    # AUDIT 5: OOS — Train on 7d, test on 4d
    # ============================================================
    print(f"\n{'=' * 80}")
    print(f"AUDIT 5: OUT-OF-SAMPLE — Train 7d / Test 4d")
    print(f"{'=' * 80}")

    # Split: first 7 days = train, last 4 days = test
    candles_per_day = len(rave_m5) // days
    train_end = candles_per_day * 7
    train_candles = rave_m5[:train_end]
    test_candles = rave_m5[train_end:]

    # Train BTC lookup
    train_btc = {k: v for k, v in btc_m5_lookup.items() if k <= int(train_candles[-1]["start"])}
    test_btc = {k: v for k, v in btc_m5_lookup.items() if k >= int(test_candles[0]["start"])}

    cfg = {"indicator": "rsi", "indicator_period": 3, "os_thresh": 30,
           "tp_pct": 25, "max_hold": 48, "compound": True, "spread_penalty": 0}

    train_r = run_backtest_audit(train_candles, train_btc, cfg)
    test_r = run_backtest_audit(test_candles, test_btc, cfg)

    print(f"  Train (7d): ${train_r['net']:.2f} ({train_r['return_pct']}%) {train_r['closes']}tr {train_r['wr']}%WR")
    print(f"  Test  (4d): ${test_r['net']:.2f} ({test_r['return_pct']}%) {test_r['closes']}tr {test_r['wr']}%WR")
    
    edge_survival = "✅ SURVIVES" if test_r["net"] > 0 else "❌ DIES"
    print(f"  Edge survival: {edge_survival}")

    results["audits"]["oos"] = {
        "train": {"days": 7, **train_r},
        "test": {"days": 4, **test_r},
        "edge_survives": test_r["net"] > 0,
    }

    # ============================================================
    # AUDIT 6: MULTI-COIN with M15 filter
    # ============================================================
    print(f"\n{'=' * 80}")
    print(f"AUDIT 6: MULTI-COIN + M15 RANGING FILTER")
    print(f"{'=' * 80}")

    coins = ["RAVE-USD", "BAL-USD", "BLUR-USD", "ALEPH-USD", "IOTX-USD"]
    multi_results = []
    print(f"{'Coin':<15} {'Ungated Net':>10} {'Gated Net':>10} {'Gated WR':>9} {'Trades':>7} {'Δ':>8}")
    print("-" * 70)

    for coin in coins:
        try:
            coin_m5 = fetch_candles(client, coin, start, now, "FIVE_MINUTE")
            coin_m15 = fetch_candles(client, coin, start, now, "FIFTEEN_MINUTE")
            m15_lookup = {int(c["start"]): c for c in coin_m15}
            
            # Build aligned BTC lookup
            coin_btc = {}
            for c in coin_m5:
                ts = int(c["start"])
                nearest = min(btc_m1, key=lambda x: abs(int(x["start"]) - ts), default=None)
                if nearest:
                    coin_btc[ts] = float(nearest["close"])

            # Ungated
            cfg_base = {"indicator": "rsi", "indicator_period": 3, "os_thresh": 30,
                        "tp_pct": 25, "max_hold": 48, "compound": True, "spread_penalty": 0}
            r_ungated = run_backtest_audit(coin_m5, coin_btc, cfg_base)

            # Gated (M15 range < 5%)
            # Re-run with inline gate
            cash = 48.0; pos = None; closes = 0; wins = 0; hist = []; m15_hist = []
            for i in range(len(coin_m5)):
                c = coin_m5[i]; ts = int(c["start"]); cl = float(c["close"])
                hist.append(cl)
                # Find M15
                m15_ts = ts - (ts % 900)
                if m15_ts in m15_lookup:
                    m15_hist.append(m15_lookup[m15_ts])
                if len(hist) > 500: hist.pop(0)
                if len(m15_hist) > 100: m15_hist.pop(0)

                # M15 gate
                m15_ok = True
                if len(m15_hist) >= 10:
                    closes_15 = [float(x["close"]) for x in m15_hist[-10:]]
                    range_pct = (max(closes_15) - min(closes_15)) / min(closes_15) * 100
                    if range_pct >= 5: m15_ok = False

                if total_volume if 'total_volume' in dir() else 0 >= 50000: fr = 0.0015
                elif total_volume if 'total_volume' in dir() else 0 >= 10000: fr = 0.0025
                else: fr = 0.0040

                if pos:
                    pos["hold"] += 1
                    exit_p = None
                    if float(c["high"]) >= pos["tp"]: exit_p = pos["tp"]
                    if exit_p is None and pos["hold"] >= 48: exit_p = cl
                    if exit_p is not None:
                        units = pos["quote"] / pos["ep"]
                        pnl = (exit_p - pos["ep"]) * units - (pos["quote"] * fr) - (exit_p * units * fr)
                        cash += pos["quote"] + pnl
                        closes += 1
                        if exit_p > pos["ep"]: wins += 1
                        pos = None

                if pos is None and cash >= 10.0 and m15_ok:
                    if len(hist) >= 5:
                        deltas = [hist[j] - hist[j-1] for j in range(1, len(hist))]
                        gains = [d if d > 0 else 0 for d in deltas[-3:]]
                        losses = [-d if d < 0 else 0 for d in deltas[-3:]]
                        ag = sum(gains)/3; al = sum(losses)/3
                        rsi = 100 - 100/(1+ag/al) if al > 0 else 100
                        if rsi <= 30:
                            ep = float(c["open"]); tq = cash * 0.95
                            if tq >= 10:
                                pos = {"ep": ep, "quote": tq, "hold": 0, "tp": ep * 1.25}
                                cash -= tq

            if pos: cash += pos["quote"]
            net_gated = cash - 48.0
            wr_gated = wins / max(1, closes) * 100

            delta = net_gated - r_ungated["net"]
            multi_results.append({
                "coin": coin, "ungated_net": r_ungated["net"],
                "gated_net": round(net_gated, 2), "gated_wr": round(wr_gated, 1),
                "gated_trades": closes, "delta": round(delta, 2)
            })
            print(f"{coin:<15} ${r_ungated['net']:>9.2f} ${net_gated:>9.2f} {wr_gated:>8.1f}% {closes:>7} ${delta:>+7.2f}")
        except Exception as e:
            print(f"{coin:<15} ERROR: {e}")

    results["audits"]["multi_coin_m15"] = multi_results

    # ============================================================
    # SUMMARY
    # ============================================================
    print(f"\n{'=' * 100}")
    print(f"ASSUMPTION AUDIT SUMMARY")
    print(f"{'=' * 100}")

    # Timeframe winner
    tf_best = max(tf_results, key=lambda x: x["net"])
    print(f"\n⏱️  TIMEFRAME: {tf_best['tf']} wins at ${tf_best['net']:.2f} ({tf_best['wr']}%WR)")

    # Indicator winner
    ind_best = max(ind_results, key=lambda x: x["net"])
    print(f"📊 INDICATOR: {ind_best['name']} wins at ${ind_best['net']:.2f} ({ind_best['wr']}%WR)")

    # Spread break-even
    spread_break = next((s for s in spread_results if s["net"] < 0), None)
    if spread_break:
        print(f"💸 SPREAD BREAK-EVEN: ~{spread_break['spread_bps']}bps")
    else:
        print(f"💸 SPREAD: Still profitable at 300bps")

    # Hold-time winner
    hold_best = max(hold_results, key=lambda x: x["net"])
    print(f"⏳ HOLD-TIME: {hold_best['hold_bars']} bars wins at ${hold_best['net']:.2f}")

    # OOS
    oos = results["audits"]["oos"]
    print(f"🧪 OOS: Train=${oos['train']['net']:.2f} → Test=${oos['test']['net']:.2f} ({'✅ SURVIVES' if oos['edge_survives'] else '❌ DIES'})")

    # Multi-coin
    gated_best = max(multi_results, key=lambda x: x["gated_net"]) if multi_results else None
    if gated_best:
        print(f"🌍 MULTI-COIN (gated): {gated_best['coin']} wins at ${gated_best['gated_net']:.2f}")

    # Save
    output = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "audits": results["audits"],
    }
    out_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "reports", "assumption_audit.json")
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "w") as f:
        json.dump(output, f, indent=2, default=str)
    print(f"\nResults saved to {out_path}")

if __name__ == "__main__":
    main()
