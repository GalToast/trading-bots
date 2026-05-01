#!/usr/bin/env python3
"""
Signal Frequency Analysis — How long until each strategy fires?
================================================================

Answers: "How many cycles do we need to wait to see at least 1 signal per coin?"
This gives the probe process concrete duration targets instead of arbitrary 1-3 cycle windows.

For each coin + strategy, computes:
- Total signals in 30d data
- Average bars between signals
- Expected wait time for first signal (in 5-min candles and minutes)
- Recommended minimum probe duration (to have >90% chance of seeing ≥1 signal)
"""
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

# Runner configs
STRATEGIES = {
    "fibonacci_breakout": {"fib_lookback": 20, "tp_pct": 0.08, "sl_pct": 0.03, "max_hold": 24},
    "supertrend": {"supertrend_atr_period": 10, "supertrend_atr_mult": 3.0, "tp_pct": 0.10, "sl_pct": 0.03, "max_hold": 48},
    "momentum": {"lookback": 15, "tp_pct": 0.10, "sl_pct": 0.00, "max_hold": 48},
}

COINS = [
    "RAVE-USD", "NOM-USD", "GHST-USD", "TRU-USD", "SUP-USD",
    "A8-USD", "BAL-USD", "CFG-USD", "IOTX-USD",
]

# Optimal assignment
OPTIMAL = {
    "NOM-USD": "fibonacci_breakout",
    "GHST-USD": "fibonacci_breakout",
    "SUP-USD": "fibonacci_breakout",
    "RAVE-USD": "supertrend",
    "TRU-USD": "supertrend",
    "BAL-USD": "supertrend",
    "IOTX-USD": "supertrend",
    "A8-USD": "momentum",
    "CFG-USD": "momentum",
}

OUTPUT_PATH = Path(__file__).resolve().parent.parent / "reports" / "signal_frequency_analysis.json"


def get_entry_signals(candles, strategy, params):
    """Get list of candle indices where signals fire."""
    from datetime import datetime as dt, timezone as tz
    SESSION_DEAD = {0, 6, 12, 19}

    signals = []
    history = []
    candle_history = []

    for i, candle in enumerate(candles):
        ts = int(candle.get("time", candle.get("start", 0)))
        close = float(candle["close"])
        high = float(candle["high"])
        open_price = float(candle["open"])

        if open_price <= 0 or close <= 0:
            continue

        history.append(close)
        candle_history.append(candle)
        if len(history) > 500:
            history = history[-500:]
            candle_history = candle_history[-500:]

        hour = dt.fromtimestamp(ts, tz=tz.utc).hour
        session_open = hour not in SESSION_DEAD

        if not session_open:
            continue

        signal = False

        if strategy == "fibonacci_breakout":
            lookback = params.get("fib_lookback", 20)
            if len(candle_history) >= lookback:
                highs = [float(c["high"]) for c in candle_history[-lookback:]]
                lows = [float(c["low"]) for c in candle_history[-lookback:]]
                swing_high = max(highs)
                swing_low = min(lows)
                fib_618 = swing_high - 0.618 * (swing_high - swing_low)
                if close > fib_618 and len(history) > 1 and history[-1] > history[-2]:
                    signal = True

        elif strategy == "supertrend":
            atr_period = params.get("supertrend_atr_period", 10)
            atr_mult = params.get("supertrend_atr_mult", 3.0)
            if len(candle_history) >= atr_period + 10:
                trs = []
                for j in range(1, len(candle_history)):
                    h = float(candle_history[j]["high"])
                    l = float(candle_history[j]["low"])
                    c_prev = float(candle_history[j - 1]["close"])
                    tr = max(h - l, abs(h - c_prev), abs(l - c_prev))
                    trs.append(tr)
                if len(trs) >= atr_period:
                    atr = sum(trs[-atr_period:]) / atr_period
                    hl2 = (float(candle["high"]) + float(candle["low"])) / 2
                    supertrend = hl2 - atr_mult * atr
                    if close > supertrend and len(history) > 1 and history[-1] > history[-2]:
                        signal = True

        elif strategy == "momentum":
            lookback = params.get("lookback", 15)
            if len(candle_history) > lookback + 1:
                recent_high = max(float(c["high"]) for c in candle_history[-(lookback + 1):-1])
                if high > recent_high:
                    signal = True

        if signal:
            signals.append(i)

    return signals


def analyze_signal_gaps(signals, total_candles):
    """Analyze gaps between signals."""
    if not signals:
        return None

    # Compute gaps (in candles)
    gaps = []
    for i in range(1, len(signals)):
        gap = signals[i] - signals[i - 1]
        gaps.append(gap)

    if not gaps:
        return {
            "total_signals": len(signals),
            "total_candles": total_candles,
            "signal_rate": len(signals) / total_candles,
            "avg_gap_candles": None,
            "max_gap_candles": None,
            "median_gap_candles": None,
            "p90_gap_candles": None,
        }

    import statistics
    sorted_gaps = sorted(gaps)
    p90_idx = int(len(sorted_gaps) * 0.9)

    return {
        "total_signals": len(signals),
        "total_candles": total_candles,
        "signal_rate": len(signals) / total_candles,
        "avg_gap_candles": round(statistics.mean(gaps), 1),
        "max_gap_candles": max(gaps),
        "median_gap_candles": statistics.median(gaps),
        "p90_gap_candles": sorted_gaps[min(p90_idx, len(sorted_gaps) - 1)],
    }


def main():
    print("=" * 80)
    print("  SIGNAL FREQUENCY ANALYSIS")
    print("=" * 80)
    print()

    results = {}

    for coin in COINS:
        strategy = OPTIMAL[coin]
        params = STRATEGIES[strategy]

        try:
            coin_file = f"reports/candle_cache/{coin.replace('-', '_')}_FIVE_MINUTE_30d.json"
            data = json.loads(open(coin_file).read())
            candles = data["candles"]

            signals = get_entry_signals(candles, strategy, params)
            analysis = analyze_signal_gaps(signals, len(candles))

            results[coin] = {
                "strategy": strategy,
                "total_candles": len(candles),
                "total_signals": len(signals),
                "analysis": analysis,
            }

            if analysis:
                avg_gap_min = round(analysis["avg_gap_candles"] * 5, 0) if analysis["avg_gap_candles"] else None
                p90_gap_min = round(analysis["p90_gap_candles"] * 5, 0) if analysis["p90_gap_candles"] else None
                print(f"  {coin} ({strategy}):", flush=True)
                print(f"    Signals: {len(signals)} in {len(candles)} candles", flush=True)
                print(f"    Avg gap: {analysis['avg_gap_candles']} candles ({avg_gap_min} min)", flush=True)
                print(f"    Median gap: {analysis['median_gap_candles']} candles", flush=True)
                print(f"    P90 gap: {analysis['p90_gap_candles']} candles ({p90_gap_min} min)", flush=True)
                print(f"    Max gap: {analysis['max_gap_candles']} candles", flush=True)
            else:
                print(f"  {coin} ({strategy}): NO SIGNALS", flush=True)
            print()

        except Exception as e:
            print(f"  {coin}: ERROR — {e}", flush=True)

    # ========== RECOMMENDATIONS ==========
    print(f"{'='*80}", flush=True)
    print("  RECOMMENDED PROBE DURATIONS", flush=True)
    print(f"{'='*80}", flush=True)

    print(f"\n  {'Coin':<14} | {'Strategy':<25} | {'Min Probes':>10} | {'Recommended':>12}", flush=True)
    print(f"  {'-'*14}-+-{'-'*25}-+-{'-'*10}-+-{'-'*12}", flush=True)

    for coin in COINS:
        r = results.get(coin, {})
        analysis = r.get("analysis")

        if analysis and analysis["avg_gap_candles"]:
            avg_gap = analysis["avg_gap_candles"]
            p90_gap = analysis["p90_gap_candles"] or avg_gap * 2

            # To have >90% chance of seeing ≥1 signal, need to wait at least P90 gap
            # But for probes, we need enough live cycles (each cycle = ~5 min)
            min_cycles = max(1, int(p90_gap / 1))  # 1 candle per cycle
            recommended_cycles = max(min_cycles * 2, 20)  # 2x P90 gap, minimum 20 cycles

            strategy = r.get("strategy", "?")
            print(f"  {coin:<14} | {strategy:<25} | {min_cycles:>10} | {recommended_cycles:>10} cycles", flush=True)
        else:
            print(f"  {coin:<14} | {'N/A':<25} | {'N/A':>10} | {'N/A':>12}", flush=True)

    # Save
    report = {
        "run_at": datetime.now(timezone.utc).isoformat(),
        "coins": results,
        "recommendations": {
            coin: {
                "min_probe_cycles": max(1, int((results[coin]["analysis"]["p90_gap_candles"] or 100))) if results.get(coin, {}).get("analysis", {}).get("avg_gap_candles") else None,
                "recommended_live_cycles": max(20, int((results[coin]["analysis"]["p90_gap_candles"] or 100) * 2)) if results.get(coin, {}).get("analysis", {}).get("avg_gap_candles") else None,
            }
            for coin in COINS
            if results.get(coin, {}).get("analysis", {}).get("avg_gap_candles")
        },
    }

    with open(OUTPUT_PATH, "w") as f:
        json.dump(report, f, indent=2, sort_keys=True)

    print(f"\nReport saved: {OUTPUT_PATH}", flush=True)
    print("\nDone. 🎯", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
