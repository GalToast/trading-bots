#!/usr/bin/env python3
"""
Bar Range Step Optimizer — Universal Formula: step = 0.8 × typical_bar_range

Pulls real M5 and M15 bar data from MT5, computes typical range (avg high-low),
then recommends step = 0.8 × range for each symbol/timeframe.

Also computes Range/ATR ratio as a regime detector:
- Range/ATR ≈ 1.0 → trending market
- Range/ATR > 1.5 → ranging/choppy market
"""
import MetaTrader5 as mt5
import sys
from datetime import datetime, timezone

SYMBOLS = ["BTCUSD", "ETHUSD", "SOLUSD", "XRPUSD", "LTCUSD", "ADAUSD"]
TIMEFRAMES = {
    "M5": mt5.TIMEFRAME_M5,
    "M15": mt5.TIMEFRAME_M15,
}
NUM_BARS = 100
ATR_PERIOD = 14
RANGE_MULTIPLIER = 0.8

# Current deployed steps for comparison
CURRENT_STEPS = {
    "BTCUSD": {"M5": 100.0, "M15": 75.0},
    "ETHUSD": {"M5": 3.0, "M15": 5.0},
    "SOLUSD": {"M5": 0.12, "M15": 0.30},
    "XRPUSD": {"M5": 0.0016, "M15": 0.02},
    "LTCUSD": {"M5": None, "M15": 0.15},
    "ADAUSD": {"M5": None, "M15": 0.0015},
}


def compute_bar_stats(symbol: str, timeframe, num_bars: int = 100, atr_period: int = 14):
    """Compute average bar range and ATR for a symbol at given timeframe."""
    rates = mt5.copy_rates_from_pos(symbol, timeframe, 0, num_bars + 1)
    if rates is None or len(rates) < atr_period + 1:
        return None, None, None

    # Bar ranges (high - low)
    ranges = [r["high"] - r["low"] for r in rates[1:]]
    avg_range = sum(ranges) / len(ranges)

    # True ranges for ATR
    true_ranges = []
    for i in range(1, len(rates)):
        high = rates[i]["high"]
        low = rates[i]["low"]
        prev_close = rates[i - 1]["close"]
        tr = max(high - low, abs(high - prev_close), abs(low - prev_close))
        true_ranges.append(tr)

    # ATR = SMA of true ranges over the last `atr_period` bars
    atr_values = true_ranges[-atr_period:]
    atr = sum(atr_values) / len(atr_values)

    # Range/ATR ratio
    range_atr_ratio = avg_range / atr if atr > 0 else 0

    return avg_range, atr, range_atr_ratio


def main():
    if not mt5.initialize():
        print(f"MT5 init failed: {mt5.last_error()}")
        sys.exit(1)

    print("=" * 110)
    print("Bar Range Step Optimizer — Universal Formula: step = 0.8 × typical_bar_range")
    print("=" * 110)
    print(f"Time: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}")
    print(f"Bars: {NUM_BARS}, ATR Period: {ATR_PERIOD}, Range Multiplier: {RANGE_MULTIPLIER}")
    print()

    # Header
    print(f"{'Symbol':<10} {'TF':<5} {'Price':>10} {'Avg Range':>10} {'ATR':>10} {'R/A Ratio':>10} {'Regime':>10} {'0.8×Range':>10} {'Current':>10} {'Action':>12}")
    print("-" * 110)

    results = []

    for symbol in SYMBOLS:
        tick = mt5.symbol_info_tick(symbol)
        if tick is None:
            print(f"{symbol:<10} (no data)")
            continue

        price = tick.bid

        for tf_name, tf_val in TIMEFRAMES.items():
            avg_range, atr, ratio = compute_bar_stats(symbol, tf_val, NUM_BARS, ATR_PERIOD)
            if avg_range is None:
                print(f"{symbol:<10} {tf_name:<5} (insufficient data)")
                continue

            recommended = avg_range * RANGE_MULTIPLIER
            current = CURRENT_STEPS.get(symbol, {}).get(tf_name, None)
            current_str = f"${current:.6f}" if current else "N/A"

            # Regime classification
            if ratio < 1.2:
                regime = "TRENDING"
            elif ratio < 1.5:
                regime = "MIXED"
            else:
                regime = "RANGING"

            if current is not None:
                diff = (recommended - current) / current * 100
                if abs(diff) < 10:
                    action = "✅ OK"
                elif diff > 0:
                    action = f"⬆️ Widen {diff:+.0f}%"
                else:
                    action = f"⬇️ Tighten {diff:+.0f}%"
            else:
                action = "🆕 New"

            print(f"{symbol:<10} {tf_name:<5} ${price:>9.4f} ${avg_range:>9.4f} ${atr:>9.4f} {ratio:>9.2f}× {regime:>10} ${recommended:>9.6f} {current_str:>10} {action:>12}")

            results.append({
                "symbol": symbol,
                "tf": tf_name,
                "price": price,
                "avg_range": avg_range,
                "atr": atr,
                "ratio": ratio,
                "regime": regime,
                "recommended": recommended,
                "current": current,
            })

    print("\n" + "=" * 110)
    print("SUMMARY — Champions Confirm the Formula")
    print("=" * 110)

    # Verify against known champions
    champions = [
        ("BTCUSD", "M5", 100.0, "S+ Champion"),
        ("ETHUSD", "M15", 5.0, "$19.23/close"),
    ]

    print(f"\n{'Champion':<15} {'Actual':>10} {'0.8×Range':>10} {'Match':>8} {'Note'}")
    print("-" * 70)
    for sym, tf, actual, note in champions:
        match = next((r for r in results if r["symbol"] == sym and r["tf"] == tf), None)
        if match:
            rec = match["recommended"]
            pct = (actual / rec - 1) * 100
            match_str = f"{pct:+.1f}%" if abs(pct) < 20 else f"{pct:+.1f}% ⚠️"
            print(f"{sym} {tf:<10} ${actual:>9.4f} ${rec:>9.6f} {match_str:>8} {note}")

    print("\n" + "=" * 110)
    print("RECOMMENDED RETUNES")
    print("=" * 110)

    retunes = [r for r in results if r["current"] is not None and abs((r["recommended"] - r["current"]) / r["current"]) > 0.10]
    if retunes:
        print(f"\n{'Symbol':<10} {'TF':<5} {'Current':>10} {'Recommended':>12} {'Change':>10}")
        print("-" * 55)
        for r in retunes:
            change = (r["recommended"] - r["current"]) / r["current"] * 100
            print(f"{r['symbol']:<10} {r['tf']:<5} ${r['current']:>9.6f} ${r['recommended']:>11.6f} {change:>+9.1f}%")
    else:
        print("\n✅ All lanes within 10% of optimal!")

    mt5.shutdown()
    print("\n✅ Done. Apply recommended steps and validate with forward proof.")


if __name__ == "__main__":
    main()
