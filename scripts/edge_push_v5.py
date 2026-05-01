#!/usr/bin/env python3
"""
Edge Push v5 — Testing the boundaries we haven't touched yet.

Untested vectors:
1. Different BB periods (10, 15, 25, 30 instead of 20)
2. Different RSI periods for confluence (5, 9, 14 instead of 7)
3. EMA crossover filter - only enter when price > EMA(50)
4. ATR-based stops instead of fixed %
5. Candle pattern recognition (hammer, engulfing at BB lower)
6. Multi-timeframe filter (15m BB+RSI must also be oversold)
7. Multiple entry scaling (50% at signal, 50% if drops 3% more)
8. Dynamic TP based on recent volatility (wider in high vol)
9. Fibonacci-based TP (1.618x the BB width)
10. VWAP deviation entry (only enter when price >5% below VWAP)
"""
import json, time, datetime, math
from pathlib import Path
from coinbase_advanced_client import CoinbaseAdvancedClient

ROOT = Path(__file__).resolve().parent.parent
REPORT_PATH = ROOT / "reports" / "edge_push_v5.json"

TOP_5 = ["RAVE-USD", "BAL-USD", "BLUR-USD", "ALEPH-USD", "IOTX-USD"]


def fetch_candles_72h(client, pid, granularity="FIVE_MINUTE"):
    gsec_map = {"FIVE_MINUTE": 300, "FIFTEEN_MINUTE": 900}
    gsec = gsec_map.get(granularity, 300)
    max_per_req = 300
    end = int(time.time())
    start = end - (72 * 3600)
    all_c = []
    seen = set()
    chunk_end = end
    while chunk_end > start:
        chunk_start = max(start, chunk_end - max_per_req * gsec)
        resp = client.market_candles(pid, start=chunk_start, end=chunk_end, granularity=granularity)
        raw = resp.get("candles", [])
        if not raw:
            break
        for c in raw:
            t = int(c["start"])
            if t not in seen:
                seen.add(t)
                all_c.append({"time": t, "open": float(c["open"]), "high": float(c["high"]),
                              "low": float(c["low"]), "close": float(c["close"]), "volume": float(c.get("volume", 0))})
        chunk_end = chunk_start - 1
        time.sleep(0.05)
    return sorted(all_c, key=lambda x: x["time"])


def rsi(closes, period):
    if len(closes) < period + 1:
        return [50.0] * len(closes)
    deltas = [closes[i] - closes[i-1] for i in range(1, len(closes))]
    gains = [d if d > 0 else 0 for d in deltas]
    losses = [-d if d < 0 else 0 for d in deltas]
    avg_g = sum(gains[:period]) / period
    avg_l = sum(losses[:period]) / period
    result = [50.0] * period
    if avg_l > 0:
        result.append(100 - 100 / (1 + avg_g / avg_l))
    else:
        result.append(100.0)
    for i in range(period, len(deltas)):
        avg_g = (avg_g * (period-1) + gains[i]) / period
        avg_l = (avg_l * (period-1) + losses[i]) / period
        if avg_l > 0:
            result.append(100 - 100 / (1 + avg_g / avg_l))
        else:
            result.append(100.0)
    return result


def ema(closes, period):
    if len(closes) < period:
        return [None] * len(closes)
    k = 2.0 / (period + 1)
    result = [None] * (period - 1)
    val = sum(closes[:period]) / period
    result.append(val)
    for i in range(period, len(closes)):
        val = closes[i] * k + val * (1 - k)
        result.append(val)
    return result


def atr(candles, period=14):
    """Average True Range."""
    if len(candles) < period + 1:
        return [0] * len(candles)
    true_ranges = []
    for i in range(1, len(candles)):
        h = candles[i]["high"]
        l = candles[i]["low"]
        prev_c = candles[i-1]["close"]
        tr = max(h - l, abs(h - prev_c), abs(l - prev_c))
        true_ranges.append(tr)
    result = [0] * period
    result.append(sum(true_ranges[:period]) / period)
    for i in range(period, len(true_ranges)):
        result.append((result[-1] * (period - 1) + true_ranges[i]) / period)
    return result


def bollinger_bands(closes, period=20, mult=2.0):
    """Returns (sma, upper, lower) arrays."""
    if len(closes) < period:
        return [None]*len(closes), [None]*len(closes), [None]*len(closes)
    sma = [None] * (period - 1)
    upper = [None] * (period - 1)
    lower = [None] * (period - 1)
    for i in range(period - 1, len(closes)):
        window = closes[i-period+1:i+1]
        mean = sum(window) / len(window)
        std = (sum((c - mean)**2 for c in window) / len(window)) ** 0.5
        sma.append(mean)
        upper.append(mean + mult * std)
        lower.append(mean - mult * std)
    return sma, upper, lower


def is_hammer(candle):
    """Hammer candle: small body at top, long lower wick."""
    o = candle["open"]
    c = candle["close"]
    h = candle["high"]
    l = candle["low"]
    body = abs(c - o)
    lower_wick = min(o, c) - l
    upper_wick = h - max(o, c)
    if body <= 0:
        return False
    return lower_wick >= 2 * body and upper_wick <= body * 0.5


def is_bullish_engulfing(candles, i):
    """Bullish engulfing pattern."""
    if i < 1:
        return False
    prev = candles[i-1]
    curr = candles[i]
    return prev["close"] < prev["open"] and curr["close"] > curr["open"] and \
           curr["open"] <= prev["close"] and curr["close"] >= prev["open"]


def run_rotation_v5(candles_by_pid, starting_cash=48.0, fee_rate=0.004,
                    bb_period=20, bb_mult=2.0, rsi_period=7, rsi_threshold=30,
                    tp_pct=0.10, sl_pct=0.025, ema_filter=False, ema_period=50,
                    candle_pattern_filter=False, multi_tf_filter=False,
                    candles_15m_by_pid=None, multi_entry=False,
                    dynamic_tp=False, vwap_deviation=False, fib_tp=False,
                    atr_sl=False, atr_period=14, atr_mult=1.5):
    """
    Rotation system with all new experimental features.
    """
    products = list(candles_by_pid.keys())
    if len(products) < 2:
        return {"error": "need at least 2 coins"}

    all_times = set()
    time_lookup = {}
    for pid, candles in candles_by_pid.items():
        for c in candles:
            t = int(c["time"])
            all_times.add(t)
            if t not in time_lookup:
                time_lookup[t] = {}
            time_lookup[t][pid] = c
    all_times = sorted(all_times)

    cash = starting_cash
    in_position = False
    position_pid = None
    entry_price = 0
    entry_fee = 0
    qty = 0
    entry_bar = 0
    price_history = {pid: [] for pid in products}
    candles_history = {pid: [] for pid in products}  # For candle patterns
    volume_history = {pid: [] for pid in products}
    vwap_history = {pid: [] for pid in products}
    trades = []
    partial_entered = False

    # Pre-compute 15m candles for multi-timeframe filter
    time_lookup_15m = {}
    if multi_tf_filter and candles_15m_by_pid:
        for pid, candles in candles_15m_by_pid.items():
            for c in candles:
                t = int(c["time"])
                if t not in time_lookup_15m:
                    time_lookup_15m[t] = {}
                time_lookup_15m[t][pid] = c

    for t in all_times:
        tick = time_lookup.get(t, {})

        # Update histories
        for pid in products:
            if pid in tick:
                c = tick[pid]
                cl = float(c["close"])
                vol = float(c["volume"])
                price_history[pid].append(cl)
                candles_history[pid].append(c)
                volume_history[pid].append(vol)
                if len(price_history[pid]) > 100:
                    price_history[pid] = price_history[pid][-100:]
                    candles_history[pid] = candles_history[pid][-100:]
                    volume_history[pid] = volume_history[pid][-100:]

        # Exit
        if in_position and position_pid in tick:
            c = tick[position_pid]
            h = float(c["high"])
            l = float(c["low"])
            cl = float(c["close"])

            # Calculate exit targets
            if fib_tp and len(price_history[position_pid]) >= bb_period:
                closes = price_history[position_pid][-bb_period:]
                bb_sma, bb_upper, bb_lower = bollinger_bands(closes, bb_period, bb_mult)
                if bb_lower[-1] is not None:
                    bb_width = bb_upper[-1] - bb_lower[-1]
                    fib_tp_price = entry_price + 1.618 * bb_width / 2
                    tp = min(entry_price * (1 + tp_pct), fib_tp_price)
                else:
                    tp = entry_price * (1 + tp_pct)
            elif dynamic_tp and len(price_history[position_pid]) >= 20:
                # Wider TP in high vol, tighter in low vol
                closes = price_history[position_pid][-20:]
                returns = [(closes[i] - closes[i-1])/closes[i-1] for i in range(1, len(closes))]
                vol = sum(abs(r) for r in returns) / len(returns)
                tp = entry_price * (1 + max(0.05, min(0.15, vol * 5)))
            else:
                tp = entry_price * (1 + tp_pct)

            if atr_sl and position_pid in candles_history and len(candles_history[position_pid]) > atr_period:
                atr_values = atr(candles_history[position_pid][-atr_period-1:], atr_period)
                if atr_values and atr_values[-1] > 0:
                    sl = entry_price - atr_values[-1] * atr_mult
                else:
                    sl = entry_price * (1 - sl_pct)
            else:
                sl = entry_price * (1 - sl_pct)

            ph = price_history[position_pid]
            rsi_val = rsi(ph, rsi_period)[-1] if len(ph) > rsi_period else 50

            exit_price = None
            if h >= tp:
                exit_price = tp
            elif l <= sl:
                exit_price = sl
            elif rsi_val >= 75:
                exit_price = cl
            elif t - entry_bar >= 12 * 300:
                exit_price = cl

            if exit_price is not None:
                # If multi-entry and we haven't entered second leg, just exit the first
                gross = (exit_price - entry_price) * qty
                exit_fee = exit_price * qty * fee_rate
                net = gross - entry_fee - exit_fee
                cash += exit_price * qty - exit_fee
                trades.append({"pid": position_pid, "net_pnl": round(net, 4),
                               "fee": round(entry_fee + exit_fee, 4), "hold_bars": (t - entry_bar) // 300})
                in_position = False
                position_pid = None
                partial_entered = False

        # Entry
        if not in_position:
            best_pid = None
            best_rsi = 999

            for pid in products:
                if pid not in tick or pid not in candles_by_pid:
                    continue
                ph = price_history[pid]
                ch = candles_history[pid]
                if len(ph) < max(bb_period, rsi_period, 10):
                    continue

                # BB calculation
                closes = ph[-bb_period:]
                bb_sma, bb_upper, bb_lower = bollinger_bands(closes, bb_period, bb_mult)
                if bb_lower[-1] is None:
                    continue
                lower_bb = bb_lower[-1]
                rsi_val = rsi(ph, rsi_period)[-1]
                curr_price = ph[-1]

                # Base signal
                signal = curr_price <= lower_bb * 1.005 and rsi_val < rsi_threshold

                # EMA filter: only enter when price > EMA(50) = trend is up
                if ema_filter and len(ph) >= ema_period:
                    ema_vals = ema(ph, ema_period)
                    if ema_vals[-1] is None or curr_price < ema_vals[-1]:
                        signal = False

                # Candle pattern filter: only enter on hammer or engulfing
                if candle_pattern_filter and len(ch) >= 2:
                    if not (is_hammer(ch[-1]) or is_bullish_engulfing(ch, len(ch)-1)):
                        signal = False

                # Multi-timeframe filter: 15m BB+RSI must also be oversold
                if multi_tf_filter and pid in candles_15m_by_pid:
                    # Find nearest 15m candle
                    ph_15m = []
                    for t15 in sorted(time_lookup_15m.keys()):
                        if t15 <= t and pid in time_lookup_15m.get(t15, {}):
                            ph_15m.append(float(time_lookup_15m[t15][pid]["close"]))
                    if len(ph_15m) >= bb_period:
                        _, _, lower_15m = bollinger_bands(ph_15m[-bb_period:], bb_period, bb_mult)
                        rsi_15m = rsi(ph_15m, rsi_period)[-1] if len(ph_15m) > rsi_period else 50
                        if lower_15m[-1] is not None:
                            if ph_15m[-1] > lower_15m[-1] * 1.01 or rsi_15m > 35:
                                signal = False

                # VWAP deviation filter
                if vwap_deviation and len(ph) >= 20 and len(volume_history[pid]) >= 20:
                    typ_prices = [(candles_history[pid][-20+j]["high"] + candles_history[pid][-20+j]["low"] + candles_history[pid][-20+j]["close"])/3 for j in range(20)]
                    vols = volume_history[pid][-20:]
                    if sum(vols) > 0:
                        vwap = sum(tp * v for tp, v in zip(typ_prices, vols)) / sum(vols)
                        deviation = (curr_price - vwap) / vwap
                        if deviation > -0.03:  # Not far enough below VWAP
                            signal = False

                if signal and rsi_val < best_rsi:
                    best_rsi = rsi_val
                    best_pid = pid

            if best_pid and cash >= 1.0:
                cl = float(tick[best_pid]["close"])
                entry_price = cl
                deploy = cash
                entry_fee = entry_price * (deploy / entry_price) * fee_rate
                qty = (deploy - entry_fee) / entry_price
                if qty > 0:
                    cash -= deploy
                    in_position = True
                    position_pid = best_pid
                    entry_bar = t
                    entry_fee = entry_fee
                    partial_entered = False

    # Close any open position
    if in_position and position_pid in candles_by_pid:
        candles = candles_by_pid[position_pid]
        if candles:
            exit_price = float(candles[-1]["close"])
            gross = (exit_price - entry_price) * qty
            exit_fee = exit_price * qty * fee_rate
            net = gross - entry_fee - exit_fee
            trades.append({"pid": position_pid, "net_pnl": round(net, 4),
                           "fee": round(entry_fee + exit_fee, 4), "hold_bars": (t - entry_bar) // 300})
            cash += exit_price * qty - exit_fee

    wins = sum(1 for t in trades if t["net_pnl"] > 0)
    return {
        "starting_cash": starting_cash, "ending_cash": round(cash, 2),
        "realized_net": round(sum(t["net_pnl"] for t in trades), 4),
        "return_pct": round(sum(t["net_pnl"] for t in trades) / starting_cash * 100, 2),
        "trades": len(trades), "wins": wins, "losses": len(trades) - wins,
        "win_rate": round(wins / max(1, len(trades)), 3),
        "avg_net_per_trade": round(sum(t["net_pnl"] for t in trades) / max(1, len(trades)), 4),
        "total_fees": round(sum(t["fee"] for t in trades), 4),
    }


def main():
    client = CoinbaseAdvancedClient()

    print("Fetching candles...")
    candles_cache = {}
    candles_15m_cache = {}
    for pid in TOP_5:
        try:
            candles_cache[pid] = fetch_candles_72h(client, pid, "FIVE_MINUTE")
            print(f"  {pid}: {len(candles_cache[pid])} 5m candles")
        except Exception as e:
            print(f"  {pid}: 5m ERROR {e}")
        try:
            candles_15m_cache[pid] = fetch_candles_72h(client, pid, "FIFTEEN_MINUTE")
            print(f"  {pid}: {len(candles_15m_cache[pid])} 15m candles")
        except Exception as e:
            print(f"  {pid}: 15m ERROR {e}")

    top5_candles = {pid: candles_cache[pid] for pid in TOP_5 if pid in candles_cache}
    top5_15m = {pid: candles_15m_cache[pid] for pid in TOP_5 if pid in candles_15m_cache}

    all_results = {}
    config_count = 0

    print(f"\n{'='*150}")
    print(f"{'Config':70s} {'Net $':>8} {'Ret%':>7} {'Trades':>6} {'WR':>6} {'Avg/Tr':>8}")
    print(f"{'='*150}")

    # 1. BB period optimization
    for bb_p in [10, 15, 20, 25, 30]:
        config_count += 1
        name = f"bb_period_{bb_p}"
        r = run_rotation_v5(top5_candles, bb_period=bb_p)
        if "error" not in r:
            all_results[name] = r
            if r["realized_net"] > 25:
                print(f"{name:70s} ${r['realized_net']:>6.2f} {r['return_pct']:>6.1f}% {r['trades']:>6} {r['win_rate']:>5.1%} ${r['avg_net_per_trade']:>6.4f}")

    # 2. RSI period optimization
    for rsi_p in [5, 7, 9, 14]:
        config_count += 1
        name = f"rsi_period_{rsi_p}"
        r = run_rotation_v5(top5_candles, rsi_period=rsi_p)
        if "error" not in r:
            all_results[name] = r
            if r["realized_net"] > 25:
                print(f"{name:70s} ${r['realized_net']:>6.2f} {r['return_pct']:>6.1f}% {r['trades']:>6} {r['win_rate']:>5.1%} ${r['avg_net_per_trade']:>6.4f}")

    # 3. BB multiplier optimization
    for bb_m in [1.5, 1.8, 2.0, 2.2, 2.5]:
        config_count += 1
        name = f"bb_mult_{bb_m}"
        r = run_rotation_v5(top5_candles, bb_mult=bb_m)
        if "error" not in r:
            all_results[name] = r
            if r["realized_net"] > 25:
                print(f"{name:70s} ${r['realized_net']:>6.2f} {r['return_pct']:>6.1f}% {r['trades']:>6} {r['win_rate']:>5.1%} ${r['avg_net_per_trade']:>6.4f}")

    # 4. EMA filter
    for ema_p in [20, 30, 50, 100]:
        config_count += 1
        name = f"ema_filter_{ema_p}"
        r = run_rotation_v5(top5_candles, ema_filter=True, ema_period=ema_p)
        if "error" not in r:
            all_results[name] = r
            if r["realized_net"] > 25:
                print(f"{name:70s} ${r['realized_net']:>6.2f} {r['return_pct']:>6.1f}% {r['trades']:>6} {r['win_rate']:>5.1%} ${r['avg_net_per_trade']:>6.4f}")

    # 5. Candle pattern filter
    config_count += 1
    name = f"candle_pattern_filter"
    r = run_rotation_v5(top5_candles, candle_pattern_filter=True)
    if "error" not in r:
        all_results[name] = r
        if r["realized_net"] > 25:
            print(f"{name:70s} ${r['realized_net']:>6.2f} {r['return_pct']:>6.1f}% {r['trades']:>6} {r['win_rate']:>5.1%} ${r['avg_net_per_trade']:>6.4f}")

    # 6. Multi-timeframe filter
    config_count += 1
    name = f"multi_tf_filter"
    r = run_rotation_v5(top5_candles, multi_tf_filter=True, candles_15m_by_pid=top5_15m)
    if "error" not in r:
        all_results[name] = r
        if r["realized_net"] > 25:
            print(f"{name:70s} ${r['realized_net']:>6.2f} {r['return_pct']:>6.1f}% {r['trades']:>6} {r['win_rate']:>5.1%} ${r['avg_net_per_trade']:>6.4f}")

    # 7. VWAP deviation
    config_count += 1
    name = f"vwap_deviation"
    r = run_rotation_v5(top5_candles, vwap_deviation=True)
    if "error" not in r:
        all_results[name] = r
        if r["realized_net"] > 25:
            print(f"{name:70s} ${r['realized_net']:>6.2f} {r['return_pct']:>6.1f}% {r['trades']:>6} {r['win_rate']:>5.1%} ${r['avg_net_per_trade']:>6.4f}")

    # 8. ATR-based stops
    for atr_m in [1.0, 1.5, 2.0]:
        config_count += 1
        name = f"atr_sl_{atr_m}"
        r = run_rotation_v5(top5_candles, atr_sl=True, atr_mult=atr_m)
        if "error" not in r:
            all_results[name] = r
            if r["realized_net"] > 25:
                print(f"{name:70s} ${r['realized_net']:>6.2f} {r['return_pct']:>6.1f}% {r['trades']:>6} {r['win_rate']:>5.1%} ${r['avg_net_per_trade']:>6.4f}")

    # 9. Fibonacci TP
    config_count += 1
    name = f"fib_tp"
    r = run_rotation_v5(top5_candles, fib_tp=True)
    if "error" not in r:
        all_results[name] = r
        if r["realized_net"] > 25:
            print(f"{name:70s} ${r['realized_net']:>6.2f} {r['return_pct']:>6.1f}% {r['trades']:>6} {r['win_rate']:>5.1%} ${r['avg_net_per_trade']:>6.4f}")

    # 10. Dynamic TP
    config_count += 1
    name = f"dynamic_tp"
    r = run_rotation_v5(top5_candles, dynamic_tp=True)
    if "error" not in r:
        all_results[name] = r
        if r["realized_net"] > 25:
            print(f"{name:70s} ${r['realized_net']:>6.2f} {r['return_pct']:>6.1f}% {r['trades']:>6} {r['win_rate']:>5.1%} ${r['avg_net_per_trade']:>6.4f}")

    # 11. Combined: best BB period + best RSI period + best BB mult
    # Based on individual results, pick best combo
    best_bb_p = max([10, 15, 20, 25, 30], key=lambda x: all_results.get(f"bb_period_{x}", {}).get("realized_net", -999))
    best_rsi_p = max([5, 7, 9, 14], key=lambda x: all_results.get(f"rsi_period_{x}", {}).get("realized_net", -999))
    best_bb_m = max([1.5, 1.8, 2.0, 2.2, 2.5], key=lambda x: all_results.get(f"bb_mult_{x}", {}).get("realized_net", -999))
    
    config_count += 1
    name = f"combo_best_bb{best_bb_p}_rsi{best_rsi_p}_bbmult{best_bb_m}"
    r = run_rotation_v5(top5_candles, bb_period=best_bb_p, rsi_period=best_rsi_p, bb_mult=best_bb_m)
    if "error" not in r:
        all_results[name] = r
        print(f"{name:70s} ${r['realized_net']:>6.2f} {r['return_pct']:>6.1f}% {r['trades']:>6} {r['win_rate']:>5.1%} ${r['avg_net_per_trade']:>6.4f}")

    # Top 20
    print(f"\n{'='*150}")
    print(f"TOP 20 CONFIGURATIONS:")
    print(f"{'='*150}")
    sorted_configs = sorted(all_results.items(), key=lambda x: x[1].get("realized_net", -999), reverse=True)
    for i, (name, r) in enumerate(sorted_configs[:20]):
        print(f"{i+1:>2}. {name:68s} ${r['realized_net']:>6.2f} ({r['return_pct']:>5.1f}%) {r['trades']:3d}c {r['win_rate']:.1%} WR ${r['avg_net_per_trade']:+.4f}/tr")

    out = Path(REPORT_PATH)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "total_configs": config_count,
        "top_20": sorted_configs[:20],
        "all_results": all_results,
    }, indent=2), encoding="utf-8")
    print(f"\nFull report: {out}")


if __name__ == "__main__":
    main()
