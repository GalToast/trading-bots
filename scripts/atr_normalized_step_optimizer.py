#!/usr/bin/env python3
"""
ATR-Normalized Step Optimizer
==============================

Pulls ATR from MT5 for crypto symbols at M5 and M15 timeframes,
then computes optimal step sizes at 0.25x, 0.5x, and 1.0x ATR multiples.

Compares against current deployed steps to find the universal formula.
"""
import MetaTrader5 as mt5
import sys
from datetime import datetime, timezone

SYMBOLS = ["BTCUSD", "ETHUSD", "SOLUSD", "XRPUSD"]
TIMEFRAMES = {
    "M5": mt5.TIMEFRAME_M5,
    "M15": mt5.TIMEFRAME_M15,
}
ATR_PERIOD = 14
ATR_MULTIPLIERS = [0.25, 0.5, 1.0]

# Current deployed steps for comparison
CURRENT_STEPS = {
    "BTCUSD": {"M5": 100.0, "M15": 15.0},
    "ETHUSD": {"M5": 3.0, "M15": 5.0},
    "SOLUSD": {"M5": 0.30, "M15": 0.30},
    "XRPUSD": {"M5": 0.02, "M15": 0.02},
}


def compute_atr(symbol: str, timeframe, period: int = 14, num_bars: int = 100) -> float:
    """Compute ATR for a symbol at given timeframe."""
    rates = mt5.copy_rates_from_pos(symbol, timeframe, 0, num_bars + 1)
    if rates is None or len(rates) < period + 1:
        return 0.0

    true_ranges = []
    for i in range(1, len(rates)):
        high = rates[i]["high"]
        low = rates[i]["low"]
        prev_close = rates[i - 1]["close"]
        tr = max(high - low, abs(high - prev_close), abs(low - prev_close))
        true_ranges.append(tr)

    # ATR = SMA of true ranges over the last `period` bars
    atr_values = true_ranges[-period:]
    return sum(atr_values) / len(atr_values)


def main():
    if not mt5.initialize():
        print(f"MT5 init failed: {mt5.last_error()}")
        sys.exit(1)

    print("=" * 90)
    print("ATR-Normalized Step Optimizer — Universal Crypto Edge Formula")
    print("=" * 90)
    print(f"Time: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}")
    print(f"ATR Period: {ATR_PERIOD}")
    print()

    # Header
    print(f"{'Symbol':<10} {'TF':<5} {'Price':>10} {'ATR':>10} {'ATR%':>8} {'0.25×ATR':>10} {'0.5×ATR':>10} {'1.0×ATR':>10} {'Current':>10} {'Ratio':>8}")
    print("-" * 90)

    results = []

    for symbol in SYMBOLS:
        tick = mt5.symbol_info_tick(symbol)
        if tick is None:
            print(f"{symbol:<10} (no data)")
            continue

        price = tick.bid
        print(f"\n{symbol} @ ${price:.4f}")

        for tf_name, tf_val in TIMEFRAMES.items():
            atr = compute_atr(symbol, tf_val, ATR_PERIOD)
            atr_pct = (atr / price) * 100 if price > 0 else 0

            step_025 = atr * 0.25
            step_050 = atr * 0.50
            step_100 = atr * 1.00

            current = CURRENT_STEPS.get(symbol, {}).get(tf_name, None)
            current_str = f"${current:.4f}" if current else "N/A"

            if current and atr > 0:
                ratio = current / atr
                ratio_str = f"{ratio:.2f}×"
            else:
                ratio_str = "—"

            print(f"  {tf_name:<7} ATR=${atr:.4f} ({atr_pct:.3f}%)  →  0.25×=${step_025:.4f}  0.5×=${step_050:.4f}  1.0×=${step_100:.4f}  Current={current_str}  ({ratio_str})")

            results.append({
                "symbol": symbol,
                "tf": tf_name,
                "price": price,
                "atr": atr,
                "atr_pct": atr_pct,
                "step_025": step_025,
                "step_050": step_050,
                "step_100": step_100,
                "current": current,
                "ratio": ratio if current and atr > 0 else None,
            })

    print("\n" + "=" * 90)
    print("RECOMMENDATIONS")
    print("=" * 90)

    # Find the ATR ratio of the BTC M5 champion
    btc_m5 = next((r for r in results if r["symbol"] == "BTCUSD" and r["tf"] == "M5"), None)
    if btc_m5 and btc_m5["atr"] > 0:
        champion_ratio = btc_m5["current"] / btc_m5["atr"]
        print(f"\n🏆 BTC M5 Champion ATR ratio: {champion_ratio:.2f}× (step = {champion_ratio:.2f} × ATR)")
        print(f"   This means the optimal universal step ≈ {champion_ratio:.2f}× ATR")
        print()

        # Apply champion ratio to all symbols
        print(f"Universal step = ATR × {champion_ratio:.2f}")
        print(f"{'Symbol':<10} {'TF':<5} {'ATR':>10} {'Recommended Step':>18} {'Current':>10} {'Action':>12}")
        print("-" * 75)
        for r in results:
            if r["atr"] > 0:
                recommended = r["atr"] * champion_ratio
                action = ""
                if r["current"] is not None:
                    diff = (recommended - r["current"]) / r["current"] * 100
                    if abs(diff) < 10:
                        action = "✅ OK"
                    elif diff > 0:
                        action = f"⬆️ Widen {diff:+.0f}%"
                    else:
                        action = f"⬇️ Tighten {diff:+.0f}%"
                print(f"  {r['symbol']:<8} {r['tf']:<5} ${r['atr']:>9.4f} ${recommended:>17.4f} ${r['current'] if r['current'] else 'N/A':>9} {action:>12}")

    mt5.shutdown()
    print("\n✅ Done. Apply recommended steps to shadows/lives and validate.")


if __name__ == "__main__":
    main()
