#!/usr/bin/env python3
"""
Temporal Quality Index (TQI) — Real-time measure of market information density.

Computes TQI from 5-min candles for NOM-USD, GHST-USD, and RAVE-USD,
then backtests the fibonacci breakout strategy on NOM-USD with and without
TQI gating to measure quality-of-signal impact.

TQI Components:
  VV  = Volume Velocity       (current_vol / median_vol, normalized)
  CC  = Candle Coherence       (1 - ADX/50, mean-reversion friendliness)
  SW  = Spread Width           (bid-ask spread normalized)
  VR  = Volatility Regime      (stability of ATR ratio)
  HQ  = Hour Quality           (session-based activity score)

Composite: TQI = 0.25*VV_norm + 0.25*CC + 0.20*SW_norm + 0.15*VR + 0.15*HQ
"""
from __future__ import annotations

import json
import math
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from coinbase_advanced_client import CoinbaseAdvancedClient

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
COINS = ["NOM-USD", "GHST-USD", "RAVE-USD"]
GRANULARITY = "FIVE_MINUTE"
DAYS = 30
FEE_RATE = 0.004
STARTING_CASH = 100.0
ENTRY_SLIP = 0.0008

SESSION_DEAD_HOURS = {0, 6, 12, 19}
SESSION_ACTIVE_HOURS = {1, 2, 3, 4, 5, 7, 8, 9, 10, 11, 13, 14, 15, 16, 17, 18, 20, 21, 22, 23}


# ---------------------------------------------------------------------------
# Data fetching
# ---------------------------------------------------------------------------
def fetch_candles(client: CoinbaseAdvancedClient, pid: str, start: int, end: int, granularity: str = GRANULARITY) -> list[dict]:
    """Fetch candles in chunks to avoid API rate limits."""
    chunk_sec = 300 * 5 * 60  # ~25 hours per chunk
    all_c = []
    cs = start
    while cs < end:
        ce = min(cs + chunk_sec, end)
        try:
            resp = client.market_candles(pid, start=cs, end=ce, granularity=granularity)
            cands = resp.get("candles", [])
            all_c.extend(cands)
            cs = ce
            if not cands:
                break
            time.sleep(0.2)
        except Exception:
            cs = ce
            time.sleep(0.5)
    all_c.sort(key=lambda c: int(c["start"]))
    return all_c


# ---------------------------------------------------------------------------
# Technical indicators
# ---------------------------------------------------------------------------
def compute_median(values: list[float]) -> float:
    s = sorted(values)
    n = len(s)
    if n == 0:
        return 0.0
    if n % 2 == 1:
        return s[n // 2]
    return (s[n // 2 - 1] + s[n // 2]) / 2


def compute_adx_series(candles: list[dict], period: int = 14) -> list[float | None]:
    """Compute ADX for every candle, returning a list aligned with candle index."""
    n = len(candles)
    result: list[float | None] = [None] * n
    if n < period * 2 + 1:
        return result

    plus_dm = [0.0] * n
    minus_dm = [0.0] * n
    trs = [0.0] * n

    for i in range(1, n):
        h = float(candles[i]["high"])
        l = float(candles[i]["low"])
        ph = float(candles[i - 1]["high"])
        pl = float(candles[i - 1]["low"])
        pc = float(candles[i - 1]["close"])
        up_move = h - ph
        down_move = pl - l
        plus_dm[i] = up_move if (up_move > down_move and up_move > 0) else 0.0
        minus_dm[i] = down_move if (down_move > up_move and down_move > 0) else 0.0
        trs[i] = max(h - l, abs(h - pc), abs(l - pc))

    for i in range(period * 2, n):
        window_plus = plus_dm[i - period + 1:i + 1]
        window_minus = minus_dm[i - period + 1:i + 1]
        window_tr = trs[i - period + 1:i + 1]
        atr_val = sum(window_tr) / period
        if atr_val == 0:
            result[i] = None
            continue
        avg_plus = sum(window_plus) / period
        avg_minus = sum(window_minus) / period
        plus_di = 100 * avg_plus / atr_val
        minus_di = 100 * avg_minus / atr_val
        denom = plus_di + minus_di
        if denom == 0:
            result[i] = None
            continue
        dx = 100 * abs(plus_di - minus_di) / denom
        result[i] = dx
    return result


def compute_atr_series(candles: list[dict], period: int = 14) -> list[float | None]:
    """ATR series aligned with candle index."""
    n = len(candles)
    result: list[float | None] = [None] * n
    if n < period + 1:
        return result
    for i in range(period, n):
        trs = []
        for j in range(i - period + 1, i + 1):
            h = float(candles[j]["high"])
            l = float(candles[j]["low"])
            pc = float(candles[j - 1]["close"])
            trs.append(max(h - l, abs(h - pc), abs(l - pc)))
        result[i] = sum(trs) / period
    return result


# ---------------------------------------------------------------------------
# TQI components
# ---------------------------------------------------------------------------
def compute_volume_velocity(volumes: list[float], i: int, window: int = 200) -> float:
    """VV = current_volume / median_volume(rolling 200)."""
    start = max(0, i - window + 1)
    window_vols = volumes[start:i + 1]
    if len(window_vols) < window // 2:
        return 1.0  # not enough data, neutral
    med = compute_median(window_vols[:-1]) if len(window_vols) > 1 else window_vols[0]
    if med <= 0:
        return 1.0
    return volumes[i] / med


def compute_candle_coherence(adx_val: float | None) -> float:
    """CC = max(0, 1 - ADX/50). High when choppy (ADX < 20), low when trending (ADX > 25)."""
    if adx_val is None:
        return 0.5  # neutral
    return max(0.0, 1.0 - adx_val / 50.0)


def compute_spread_width_normalized(spread_pct: float) -> float:
    """SW_norm = 1 - min(spread / 0.02, 1). Narrow spread = high quality."""
    return 1.0 - min(spread_pct / 0.02, 1.0)


def compute_volatility_regime(atr_14: float | None, atr_50: float | None) -> float:
    """VR = 1 - abs(ATR_14/ATR_50 - 1). Peaks at 1 when ratio = 1."""
    if atr_14 is None or atr_50 is None or atr_50 == 0:
        return 0.5
    ratio = atr_14 / atr_50
    return 1.0 - abs(ratio - 1.0)


def compute_hour_quality(ts: int) -> float:
    """HQ = 1.0 for active hours, 0.3 for dead hours."""
    hour = datetime.fromtimestamp(ts, tz=timezone.utc).hour
    return 1.0 if hour in SESSION_ACTIVE_HOURS else 0.3


# ---------------------------------------------------------------------------
# Main TQI computation
# ---------------------------------------------------------------------------
def compute_tqi_series(candles: list[dict], client: CoinbaseAdvancedClient, coin: str) -> list[dict]:
    """Compute full TQI time series for a coin's candles."""
    n = len(candles)
    volumes = [float(c["volume"]) for c in candles]
    closes = [float(c["close"]) for c in candles]

    adx_series = compute_adx_series(candles, period=14)
    atr_14_series = compute_atr_series(candles, period=14)
    atr_50_series = compute_atr_series(candles, period=50)

    # Fetch live spread once (used for all candles as baseline;
    # in a live system you'd fetch per-candle but for backtest a single
    # snapshot is the best we can do historically)
    spread_pct = 0.005  # default 0.5% spread
    try:
        ticker = client.public_exchange_ticker(coin)
        mid = (ticker.bid_price + ticker.ask_price) / 2
        if mid > 0:
            spread_pct = (ticker.ask_price - ticker.bid_price) / mid
    except Exception:
        pass

    results = []
    for i in range(n):
        c = candles[i]
        ts = int(c["start"])

        vv = compute_volume_velocity(volumes, i, window=200)
        vv_norm = min(vv / 2.0, 1.0)

        cc = compute_candle_coherence(adx_series[i])

        sw_norm = compute_spread_width_normalized(spread_pct)

        vr = compute_volatility_regime(atr_14_series[i], atr_50_series[i])

        hq = compute_hour_quality(ts)

        tqi = 0.25 * vv_norm + 0.25 * cc + 0.20 * sw_norm + 0.15 * vr + 0.15 * hq

        results.append({
            "index": i,
            "timestamp": ts,
            "datetime": datetime.fromtimestamp(ts, tz=timezone.utc).isoformat(),
            "close": closes[i],
            "volume": volumes[i],
            "vv": round(vv, 4),
            "vv_norm": round(vv_norm, 4),
            "adx": round(adx_series[i], 2) if adx_series[i] is not None else None,
            "cc": round(cc, 4),
            "sw_pct": round(spread_pct, 6),
            "sw_norm": round(sw_norm, 4),
            "atr_14": round(atr_14_series[i], 6) if atr_14_series[i] is not None else None,
            "atr_50": round(atr_50_series[i], 6) if atr_50_series[i] is not None else None,
            "vr": round(vr, 4),
            "hq": round(hq, 4),
            "tqi": round(tqi, 4),
        })
    return results


# ---------------------------------------------------------------------------
# Fibonacci breakout entry (mirrors breakout_50_sweep)
# ---------------------------------------------------------------------------
def compute_fib_levels(swing_high: float, swing_low: float) -> dict[str, float]:
    diff = swing_high - swing_low
    return {
        "0.0": swing_low,
        "0.236": swing_low + 0.236 * diff,
        "0.382": swing_low + 0.382 * diff,
        "0.5": swing_low + 0.5 * diff,
        "0.618": swing_low + 0.618 * diff,
        "0.786": swing_low + 0.786 * diff,
        "1.0": swing_high,
    }


def fibonacci_breakout_entry(candles_hist: list[dict], closes: list[float], candle: dict, params: dict) -> bool:
    """Enter when price breaks above 0.618 Fibonacci level from recent swing."""
    if len(candles_hist) < 30:
        return False
    lookback = params.get("lookback", 20)
    highs = [float(c["high"]) for c in candles_hist[-lookback:]]
    lows = [float(c["low"]) for c in candles_hist[-lookback:]]
    swing_high = max(highs)
    swing_low = min(lows)
    fib = compute_fib_levels(swing_high, swing_low)
    fib_618 = fib["0.618"]
    current_price = float(candle["close"])
    if current_price > fib_618 and len(closes) > 1 and closes[-1] > closes[-2]:
        return True
    return False


# ---------------------------------------------------------------------------
# Backtest engine with optional TQI gating
# ---------------------------------------------------------------------------
def backtest_with_tqi(
    candles: list[dict],
    tqi_series: list[dict] | None,
    entry_fn,
    params: dict,
    tqi_threshold: float | None = None,
    fee_rate: float = FEE_RATE,
    starting_cash: float = STARTING_CASH,
    entry_slip: float = ENTRY_SLIP,
) -> dict:
    """
    Backtest engine that optionally gates entries on TQI threshold.
    When tqi_threshold is set, only fires when TQI > threshold.
    """
    import random
    rng = random.Random(42)

    cash = starting_cash
    pos = None
    closes_count = 0
    wins = 0
    losses = 0
    total_fees = 0.0
    peak = starting_cash
    max_dd = 0.0
    signals_count = 0
    signals_filtered_tqi = 0
    trade_log = []

    closes_history: list[float] = []
    candles_history: list[dict] = []

    tp_pct = params.get("tp_pct", 0)
    sl_pct = params.get("sl_pct", 0)
    max_hold = params.get("max_hold", 48)

    for i in range(len(candles)):
        c = candles[i]
        close = float(c["close"])
        high = float(c["high"])
        low = float(c["low"])
        candle_open = float(c["open"])

        closes_history.append(close)
        candles_history.append(dict(c))
        if len(closes_history) > 500:
            closes_history = closes_history[-500:]
            candles_history = candles_history[-500:]

        ts = int(c.get("start", c.get("time", 0)))
        hour = datetime.fromtimestamp(ts, tz=timezone.utc).hour
        session_open = hour not in SESSION_DEAD_HOURS

        # Current TQI value
        current_tqi = None
        if tqi_series is not None and i < len(tqi_series):
            current_tqi = tqi_series[i]["tqi"]

        # EXIT
        if pos:
            pos["hold"] += 1
            exit_price = None
            exit_reason = None

            if high >= pos["tp"]:
                exit_price = pos["tp"]
                exit_reason = "tp"
            elif pos["sl"] > 0 and low <= pos["sl"]:
                exit_price = pos["sl"]
                exit_reason = "sl"
            elif pos["hold"] >= pos["max_hold"]:
                exit_price = close
                exit_reason = "timeout"

            if exit_price is not None:
                actual_exit = exit_price * (1 - 0.0)  # no exit slip for simplicity
                units = pos["units"]
                gross = (actual_exit - pos["ep"]) * units
                entry_fee = pos["entry_fee"]
                exit_fee = actual_exit * units * fee_rate
                net = gross - entry_fee - exit_fee

                cash += pos["q"] + net
                closes_count += 1
                total_fees += entry_fee + exit_fee
                if net > 0:
                    wins += 1
                else:
                    losses += 1

                trade_log.append({
                    "exit_ts": ts,
                    "exit_reason": exit_reason,
                    "entry_tqi": pos.get("entry_tqi"),
                    "pnl": round(net, 4),
                })

                peak = max(peak, cash)
                dd = (peak - cash) / peak if peak > 0 else 0
                max_dd = max(max_dd, dd)
                pos = None

        # ENTRY
        if pos is None:
            signal = entry_fn(candles_history, closes_history, c, params)
            if signal:
                signals_count += 1

                if not session_open:
                    continue

                # TQI gate
                if tqi_threshold is not None and current_tqi is not None:
                    if current_tqi <= tqi_threshold:
                        signals_filtered_tqi += 1
                        continue

                if rng.random() > 1.0:  # fill probability = 1.0
                    continue

                if cash < 10.0:
                    continue

                actual_entry = candle_open * (1 + entry_slip)
                deploy = cash
                entry_fee = deploy * fee_rate
                units = (deploy - entry_fee) / actual_entry

                tp = actual_entry * (1 + tp_pct / 100.0) if tp_pct > 0 else actual_entry * 1.08
                sl = actual_entry * (1 - sl_pct / 100.0) if sl_pct > 0 else 0

                cash -= deploy
                pos = {
                    "ep": actual_entry, "q": deploy, "hold": 0,
                    "tp": tp, "sl": sl, "units": units,
                    "entry_fee": entry_fee, "max_hold": max_hold,
                    "entry_tqi": current_tqi,
                }

    # Close any open position at last candle
    if pos and candles:
        last_close = float(candles[-1]["close"])
        actual_exit = last_close
        units = pos["units"]
        gross = (actual_exit - pos["ep"]) * units
        entry_fee = pos["entry_fee"]
        exit_fee = actual_exit * units * fee_rate
        net = gross - entry_fee - exit_fee
        cash += pos["q"] + net
        closes_count += 1
        total_fees += entry_fee + exit_fee
        if net > 0:
            wins += 1
        else:
            losses += 1
        peak = max(peak, cash)
        dd = (peak - cash) / peak if peak > 0 else 0
        max_dd = max(max_dd, dd)

    net = cash - starting_cash
    wr = wins / max(closes_count, 1) * 100

    return {
        "net_pnl": round(net, 2),
        "return_pct": round(net / starting_cash * 100, 2),
        "trades": closes_count,
        "wins": wins,
        "losses": losses,
        "win_rate": round(wr, 1),
        "max_drawdown": round(max_dd * 100, 1),
        "signals": signals_count,
        "signals_filtered_tqi": signals_filtered_tqi,
        "total_fees": round(total_fees, 2),
        "trade_log": trade_log,
    }


# ---------------------------------------------------------------------------
# Statistics helpers
# ---------------------------------------------------------------------------
def tqi_stats(series: list[dict]) -> dict:
    values = [r["tqi"] for r in series]
    if not values:
        return {}
    values_sorted = sorted(values)
    n = len(values_sorted)
    mean = sum(values) / n
    median = (values_sorted[n // 2] if n % 2 == 1
              else (values_sorted[n // 2 - 1] + values_sorted[n // 2]) / 2)
    variance = sum((x - mean) ** 2 for x in values) / max(n - 1, 1)
    std = math.sqrt(variance)

    # Percentiles
    def pctile(data, p):
        k = (len(data) - 1) * p / 100
        f = int(k)
        c = f + 1 if f + 1 < len(data) else f
        return data[f] + (k - f) * (data[c] - data[f]) if c != f else data[f]

    return {
        "count": n,
        "mean": round(mean, 4),
        "median": round(median, 4),
        "std": round(std, 4),
        "min": round(min(values), 4),
        "max": round(max(values), 4),
        "p10": round(pctile(values_sorted, 10), 4),
        "p25": round(pctile(values_sorted, 25), 4),
        "p50": round(pctile(values_sorted, 50), 4),
        "p75": round(pctile(values_sorted, 75), 4),
        "p90": round(pctile(values_sorted, 90), 4),
    }


def component_correlations(series: list[dict]) -> dict:
    """Compute pairwise Pearson correlations between TQI components."""
    components = ["vv_norm", "cc", "sw_norm", "vr", "hq"]
    # Filter out rows with None values in any component
    rows = []
    for r in series:
        vals = [r.get(c) for c in components]
        if all(v is not None for v in vals):
            rows.append(vals)
    if len(rows) < 3:
        return {}

    def pearson(x, y):
        n = len(x)
        mx = sum(x) / n
        my = sum(y) / n
        cov = sum((x[i] - mx) * (y[i] - my) for i in range(n))
        sx = math.sqrt(sum((xi - mx) ** 2 for xi in x))
        sy = math.sqrt(sum((yi - my) ** 2 for yi in y))
        if sx == 0 or sy == 0:
            return 0.0
        return cov / (sx * sy)

    result = {}
    for i, ci in enumerate(components):
        for j, cj in enumerate(components):
            if j <= i:
                continue
            xi = [r[i] for r in rows]
            xj = [r[j] for r in rows]
            result[f"{ci}_vs_{cj}"] = round(pearson(xi, xj), 4)
    return result


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main():
    start_time = time.time()
    print(f"\n{'='*70}")
    print(f"TEMPORAL QUALITY INDEX (TQI)")
    print(f"Started: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}")
    print(f"{'='*70}\n")

    client = CoinbaseAdvancedClient()
    now = int(time.time())
    start_ts = now - DAYS * 86400

    # ------------------------------------------------------------------
    # 1. Fetch candles
    # ------------------------------------------------------------------
    print("[1/5] Fetching 30-day 5-min candles...")
    all_candles = {}
    for coin in COINS:
        try:
            candles = fetch_candles(client, coin, start_ts, now)
            all_candles[coin] = candles
            print(f"  {coin}: {len(candles)} candles")
        except Exception as e:
            print(f"  {coin}: ERROR - {e}")
            all_candles[coin] = []

    # ------------------------------------------------------------------
    # 2. Compute TQI for each coin
    # ------------------------------------------------------------------
    print(f"\n[2/5] Computing TQI components...")
    all_tqi = {}
    coin_stats = {}
    coin_correlations = {}

    for coin in COINS:
        candles = all_candles[coin]
        if not candles:
            print(f"  {coin}: no data, skipping")
            continue
        tqi = compute_tqi_series(candles, client, coin)
        all_tqi[coin] = tqi

        stats = tqi_stats(tqi)
        coin_stats[coin] = stats
        correlations = component_correlations(tqi)
        coin_correlations[coin] = correlations

        print(f"  {coin}: mean={stats.get('mean', 'N/A')}, median={stats.get('median', 'N/A')}, "
              f"std={stats.get('std', 'N/A')}, range=[{stats.get('min', 'N/A')}, {stats.get('max', 'N/A')}]")

    # ------------------------------------------------------------------
    # 3. TQI distribution analysis
    # ------------------------------------------------------------------
    print(f"\n[3/5] TQI Distribution Analysis:")
    for coin in COINS:
        if coin not in coin_stats:
            continue
        s = coin_stats[coin]
        print(f"\n  --- {coin} ---")
        print(f"    Count:   {s['count']}")
        print(f"    Mean:    {s['mean']}")
        print(f"    Median:  {s['median']}")
        print(f"    Std:     {s['std']}")
        print(f"    Min:     {s['min']}")
        print(f"    Max:     {s['max']}")
        print(f"    P10:     {s['p10']}")
        print(f"    P25:     {s['p25']}")
        print(f"    P50:     {s['p50']}")
        print(f"    P75:     {s['p75']}")
        print(f"    P90:     {s['p90']}")

    for coin in COINS:
        if coin in coin_correlations and coin_correlations[coin]:
            print(f"\n  --- {coin} Component Correlations ---")
            for pair, val in coin_correlations[coin].items():
                print(f"    {pair}: {val}")

    # ------------------------------------------------------------------
    # 4. Backtest: fibonacci breakout on NOM-USD, gated vs ungated
    # ------------------------------------------------------------------
    print(f"\n[4/5] Backtesting fibonacci breakout on NOM-USD...")
    nom_candles = all_candles.get("NOM-USD", [])
    nom_tqi = all_tqi.get("NOM-USD", None)

    if not nom_candles:
        print("  No NOM-USD data, skipping backtest.")
        return

    fib_params = {"lookback": 20, "tp_pct": 8, "sl_pct": 3, "max_hold": 24}

    # Ungated (always fire)
    print("  Running ungated (always fire)...")
    ungated = backtest_with_tqi(nom_candles, nom_tqi, fibonacci_breakout_entry, fib_params, tqi_threshold=None)
    print(f"    PnL: ${ungated['net_pnl']:.2f} | Trades: {ungated['trades']} | "
          f"Win Rate: {ungated['win_rate']}% | MaxDD: {ungated['max_drawdown']}% | "
          f"Signals: {ungated['signals']}")

    # Gated at TQI > 0.5
    print("  Running gated (TQI > 0.5)...")
    gated_05 = backtest_with_tqi(nom_candles, nom_tqi, fibonacci_breakout_entry, fib_params, tqi_threshold=0.5)
    print(f"    PnL: ${gated_05['net_pnl']:.2f} | Trades: {gated_05['trades']} | "
          f"Win Rate: {gated_05['win_rate']}% | MaxDD: {gated_05['max_drawdown']}% | "
          f"Signals: {gated_05['signals']} | Filtered by TQI: {gated_05['signals_filtered_tqi']}")

    # ------------------------------------------------------------------
    # 5. Optimal TQI threshold sweep
    # ------------------------------------------------------------------
    print(f"\n[5/5] Sweeping TQI thresholds...")
    thresholds = [0.3, 0.4, 0.5, 0.6, 0.7]
    sweep_results = []

    for thresh in thresholds:
        result = backtest_with_tqi(nom_candles, nom_tqi, fibonacci_breakout_entry, fib_params, tqi_threshold=thresh)
        sweep_results.append({
            "threshold": thresh,
            "net_pnl": result["net_pnl"],
            "return_pct": result["return_pct"],
            "trades": result["trades"],
            "win_rate": result["win_rate"],
            "max_drawdown": result["max_drawdown"],
            "signals": result["signals"],
            "signals_filtered_tqi": result["signals_filtered_tqi"],
            "total_fees": result["total_fees"],
        })
        print(f"    TQI > {thresh}: PnL=${result['net_pnl']:>7.2f} | Trades:{result['trades']:>3} | "
              f"WR:{result['win_rate']:>5.1f}% | MaxDD:{result['max_drawdown']:>5.1f}% | "
              f"Filtered:{result['signals_filtered_tqi']:>3}")

    # ------------------------------------------------------------------
    # Save results
    # ------------------------------------------------------------------
    out_dir = Path(__file__).parent.parent / "reports"
    out_dir.mkdir(exist_ok=True)
    out_path = out_dir / "temporal_quality_index_results.json"

    output = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "elapsed_seconds": round(time.time() - start_time, 1),
        "days_of_data": DAYS,
        "granularity": GRANULARITY,
        "coins": COINS,
        "candle_counts": {coin: len(all_candles.get(coin, [])) for coin in COINS},
        "tqi_stats": coin_stats,
        "component_correlations": coin_correlations,
        "backtest_comparison": {
            "ungated": {k: v for k, v in ungated.items() if k != "trade_log"},
            "gated_tqi_0.5": {k: v for k, v in gated_05.items() if k != "trade_log"},
        },
        "threshold_sweep": sweep_results,
        "optimal_threshold": max(sweep_results, key=lambda r: r["net_pnl"])["threshold"] if sweep_results else None,
    }

    with open(out_path, "w") as f:
        json.dump(output, f, indent=2)

    print(f"\n{'='*70}")
    print(f"TQI ANALYSIS COMPLETE in {time.time() - start_time:.1f}s")
    print(f"Results saved to: {out_path}")

    # Summary
    print(f"\n--- SUMMARY ---")
    print(f"Ungated:  PnL=${ungated['net_pnl']:.2f}  Trades={ungated['trades']}  WR={ungated['win_rate']}%  MaxDD={ungated['max_drawdown']}%")
    print(f"Gated>0.5: PnL=${gated_05['net_pnl']:.2f}  Trades={gated_05['trades']}  WR={gated_05['win_rate']}%  MaxDD={gated_05['max_drawdown']}%")

    best = max(sweep_results, key=lambda r: r["net_pnl"])
    print(f"Best threshold: TQI > {best['threshold']} -> PnL=${best['net_pnl']:.2f}, Trades={best['trades']}, WR={best['win_rate']}%")
    print(f"{'='*70}\n")


if __name__ == "__main__":
    main()
