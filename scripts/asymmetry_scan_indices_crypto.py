#!/usr/bin/env python3
"""Asymmetric geometry scan for indices and crypto.

Pulls 1000 M15 bars via MetaTrader5, then analyses directional
excursion patterns to find the optimal buy/sell step ratio for each
symbol.  Results are printed and saved to reports/asymmetry_scan_indices_crypto.json.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import MetaTrader5 as mt5

ROOT = Path(__file__).resolve().parent.parent
REPORTS = ROOT / "reports"
SYMBOLS = ["NAS100", "US30", "BTCUSD", "ETHUSD"]
BARS = 1000
TIMEFRAME = mt5.TIMEFRAME_M15
ATR_PERIOD = 14


def ensure_mt5() -> bool:
    if not mt5.initialize():
        print("ERROR: MT5 initialise failed", file=sys.stderr)
        return False
    return True


def fetch_rates(symbol: str, n: int) -> list[mt5.Rate] | None:
    rates = mt5.copy_rates_from_pos(symbol, TIMEFRAME, 0, n)
    if rates is None or len(rates) == 0:
        return None
    return rates


def compute_atr(rates: list[mt5.Rate], period: int) -> float | None:
    if len(rates) < period + 1:
        return None
    true_ranges: list[float] = []
    for i in range(1, len(rates)):
        high = rates[i]["high"]
        low = rates[i]["low"]
        prev_close = rates[i - 1]["close"]
        tr = max(high - low, abs(high - prev_close), abs(low - prev_close))
        true_ranges.append(tr)
    if len(true_ranges) < period:
        return None
    return sum(true_ranges[-period:]) / period


def analyse_excursions(rates: list[mt5.Rate]) -> dict:
    """Compute up/down bar statistics."""
    changes = []
    for i in range(1, len(rates)):
        changes.append(rates[i]["close"] - rates[i - 1]["close"])

    if not changes:
        return {}

    up_changes = [c for c in changes if c > 0]
    down_changes = [c for c in changes if c < 0]
    flat = len(changes) - len(up_changes) - len(down_changes)

    n = len(changes)
    up_pct = len(up_changes) / n * 100 if n else 0
    down_pct = len(down_changes) / n * 100 if n else 0

    avg_up = sum(up_changes) / len(up_changes) if up_changes else 0.0
    avg_down = sum(down_changes) / len(down_changes) if down_changes else 0.0  # negative

    # Optimal step ratio: if up moves are smaller on average, buy_step should
    # be tighter (smaller) and sell_step wider, and vice versa.
    # step_ratio = avg_up_mag / avg_down_mag  (both positive magnitudes)
    avg_up_mag = avg_up
    avg_down_mag = abs(avg_down)
    if avg_down_mag > 0:
        ratio = avg_up_mag / avg_down_mag
    else:
        ratio = 1.0

    # If ratio > 1: up moves are larger -> sell_step should be wider (larger),
    #   buy_step tighter.  Optimal: buy_step = 1/ratio, sell_step = ratio
    # If ratio < 1: down moves are larger -> buy_step wider, sell_step tighter.
    # We normalise so that geometric mean = 1 (product = 1).
    # buy_step = sqrt(1/ratio), sell_step = sqrt(ratio)  when ratio > 1 means ups larger
    # But the user wants the intuitive: step inversely proportional to freq*size.
    # Simplest: optimal_buy_step_factor proportional to avg_down_mag,
    #           optimal_sell_step_factor proportional to avg_up_mag,
    # normalised so the average is 1.0 baseline.

    mean_mag = (avg_up_mag + avg_down_mag) / 2 if (avg_up_mag + avg_down_mag) > 0 else 1.0
    optimal_buy_step = avg_down_mag / mean_mag       # wider when downs are bigger
    optimal_sell_step = avg_up_mag / mean_mag         # wider when ups are bigger

    return {
        "total_bars": n,
        "up_bars": len(up_changes),
        "down_bars": len(down_changes),
        "flat_bars": flat,
        "up_pct": round(up_pct, 2),
        "down_pct": round(down_pct, 2),
        "avg_up": round(avg_up, 8),
        "avg_down": round(avg_down, 8),
        "avg_up_magnitude": round(avg_up_mag, 8),
        "avg_down_magnitude": round(avg_down_mag, 8),
        "magnitude_ratio": round(ratio, 4),
        "optimal_buy_step_factor": round(optimal_buy_step, 4),
        "optimal_sell_step_factor": round(optimal_sell_step, 4),
    }


def main() -> None:
    if not ensure_mt5():
        sys.exit(1)

    REPORTS.mkdir(parents=True, exist_ok=True)

    results: dict = {}

    for symbol in SYMBOLS:
        print(f"\n{'='*60}")
        print(f"  {symbol}")
        print(f"{'='*60}")

        rates = fetch_rates(symbol, BARS)
        if rates is None:
            print(f"  WARN: no rates for {symbol}, skipping")
            results[symbol] = {"error": "no data"}
            continue

        atr = compute_atr(rates, ATR_PERIOD)
        exc = analyse_excursions(list(rates))

        if atr is not None:
            atr_rounded = round(atr, 5)
        else:
            atr_rounded = None

        line_parts = [f"{symbol}:"]
        if atr_rounded is not None:
            line_parts.append(f"ATR={atr_rounded}")
        if exc:
            line_parts.append(f"up_bars={exc['up_pct']}%")
            line_parts.append(f"avg_up={exc['avg_up']}")
            line_parts.append(f"avg_down={exc['avg_down']}")
            line_parts.append(f"optimal_buy_step={exc['optimal_buy_step_factor']}")
            line_parts.append(f"optimal_sell_step={exc['optimal_sell_step_factor']}")
            # Interpretation
            if exc["optimal_sell_step_factor"] < exc["optimal_buy_step_factor"]:
                line_parts.append("[tight-sell / wide-buy pattern like FX]")
            else:
                line_parts.append("[tight-buy / wide-sell pattern — REVERSE of FX]")

        print("  " + ", ".join(line_parts))

        results[symbol] = {
            "atr": atr_rounded,
            "excursions": exc,
        }

    # Save JSON report
    output_path = REPORTS / "asymmetry_scan_indices_crypto.json"
    with open(output_path, "w") as f:
        json.dump(results, f, indent=2)
    print(f"\nResults saved to {output_path}")

    # Summary table
    print(f"\n{'='*60}")
    print("  SUMMARY")
    print(f"{'='*60}")
    print(f"{'Symbol':<10} {'ATR':>10} {'Up%':>7} {'AvgUp':>12} {'AvgDown':>12} {'BuyStep':>9} {'SellStep':>10} {'Pattern'}")
    print("-" * 100)
    for sym in SYMBOLS:
        r = results.get(sym, {})
        if "error" in r:
            print(f"{sym:<10} {'N/A':>10} {'N/A':>7} {'N/A':>12} {'N/A':>12} {'N/A':>9} {'N/A':>10} N/A")
            continue
        exc = r.get("excursions", {})
        atr_str = f"{r.get('atr', 'N/A')}" if r.get("atr") is not None else "N/A"
        print(
            f"{sym:<10} {atr_str:>10} {exc.get('up_pct', 'N/A'):>7} "
            f"{exc.get('avg_up', 0):>12.6f} {exc.get('avg_down', 0):>12.6f} "
            f"{exc.get('optimal_buy_step_factor', 0):>9.4f} "
            f"{exc.get('optimal_sell_step_factor', 0):>10.4f} "
            f"{'FX-like' if exc.get('optimal_sell_step_factor', 1) < exc.get('optimal_buy_step_factor', 1) else 'REVERSE'}"
        )

    mt5.shutdown()


if __name__ == "__main__":
    main()
