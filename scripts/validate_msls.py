#!/usr/bin/env python3
"""Stricter offline validator for the Gemini v2 MSLS prototype."""

from __future__ import annotations

import argparse
import json
import statistics
import sys
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import MetaTrader5 as mt5

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from bot.gemini_v2 import detect_msls_signal


DEFAULT_SYMBOLS = ["NAS100", "US30", "AUDCHF", "EURJPY", "USDCHF", "GBPUSD"]
INDEX_SYMBOL_KEYS = ("NAS100", "US30", "GER30", "FRA40", "ESP35", "JPN225")


@dataclass
class SignalResult:
    symbol: str
    signal: str
    entry_time_utc: str
    entry_price: float
    stop_loss: float
    risk: float
    next_bar_green: bool
    green_within_3: bool
    green_within_5: bool
    first_green_bar: int | None
    stop_hit_bar: int | None
    target_1r_bar: int | None
    target_2r_bar: int | None
    target_1r_before_stop: bool
    target_2r_before_stop: bool
    stop_before_1r: bool
    stop_before_2r: bool
    mfe_r: float
    mae_r: float
    max_favorable_points: float
    max_adverse_points: float


def symbol_point_multiplier(symbol: str) -> float:
    upper = symbol.upper()
    if any(key in upper for key in INDEX_SYMBOL_KEYS):
        return 1.0
    if "JPY" in upper:
        return 100.0
    return 10000.0


def conservative_touch_bar(signal: str, bar: dict[str, float], stop_loss: float, target: float) -> tuple[bool, bool]:
    if signal == "BUY":
        stop_hit = bar["l"] <= stop_loss
        target_hit = bar["h"] >= target
    else:
        stop_hit = bar["h"] >= stop_loss
        target_hit = bar["l"] <= target
    return stop_hit, target_hit


def first_green_bar(signal: str, entry_price: float, future_bars: list[dict[str, float]]) -> int | None:
    for offset, bar in enumerate(future_bars, start=1):
        if signal == "BUY" and bar["h"] > entry_price:
            return offset
        if signal == "SELL" and bar["l"] < entry_price:
            return offset
    return None


def analyze_signal(
    symbol: str,
    signal: str,
    entry_time_utc: str,
    entry_price: float,
    stop_loss: float,
    future_bars: list[dict[str, float]],
) -> SignalResult | None:
    risk = abs(entry_price - stop_loss)
    if risk <= 0:
        return None

    target_1r = entry_price + risk if signal == "BUY" else entry_price - risk
    target_2r = entry_price + (2 * risk) if signal == "BUY" else entry_price - (2 * risk)

    stop_hit_bar: int | None = None
    target_1r_bar: int | None = None
    target_2r_bar: int | None = None
    mfe_r = 0.0
    mae_r = 0.0
    max_favorable_points = 0.0
    max_adverse_points = 0.0
    points_multiplier = symbol_point_multiplier(symbol)

    for offset, bar in enumerate(future_bars, start=1):
        if signal == "BUY":
            favorable = max(0.0, bar["h"] - entry_price)
            adverse = max(0.0, entry_price - bar["l"])
        else:
            favorable = max(0.0, entry_price - bar["l"])
            adverse = max(0.0, bar["h"] - entry_price)

        mfe_r = max(mfe_r, favorable / risk)
        mae_r = max(mae_r, adverse / risk)
        max_favorable_points = max(max_favorable_points, favorable * points_multiplier)
        max_adverse_points = max(max_adverse_points, adverse * points_multiplier)

        if stop_hit_bar is None:
            stop_hit, target_1_hit = conservative_touch_bar(signal, bar, stop_loss, target_1r)
            if stop_hit:
                stop_hit_bar = offset
            elif target_1_hit:
                target_1r_bar = offset

        if target_2r_bar is None:
            stop_hit, target_2_hit = conservative_touch_bar(signal, bar, stop_loss, target_2r)
            if stop_hit and stop_hit_bar is None:
                stop_hit_bar = offset
            elif target_2_hit:
                target_2r_bar = offset

    green_bar = first_green_bar(signal, entry_price, future_bars)
    return SignalResult(
        symbol=symbol,
        signal=signal,
        entry_time_utc=entry_time_utc,
        entry_price=entry_price,
        stop_loss=stop_loss,
        risk=risk,
        next_bar_green=green_bar == 1,
        green_within_3=(green_bar or 9999) <= 3,
        green_within_5=(green_bar or 9999) <= 5,
        first_green_bar=green_bar,
        stop_hit_bar=stop_hit_bar,
        target_1r_bar=target_1r_bar,
        target_2r_bar=target_2r_bar,
        target_1r_before_stop=target_1r_bar is not None and (stop_hit_bar is None or target_1r_bar < stop_hit_bar),
        target_2r_before_stop=target_2r_bar is not None and (stop_hit_bar is None or target_2r_bar < stop_hit_bar),
        stop_before_1r=stop_hit_bar is not None and (target_1r_bar is None or stop_hit_bar < target_1r_bar),
        stop_before_2r=stop_hit_bar is not None and (target_2r_bar is None or stop_hit_bar < target_2r_bar),
        mfe_r=mfe_r,
        mae_r=mae_r,
        max_favorable_points=max_favorable_points,
        max_adverse_points=max_adverse_points,
    )


def summarize_results(results: list[SignalResult]) -> dict[str, Any]:
    count = len(results)
    if not count:
        return {
            "signals": 0,
            "green_next_rate": 0.0,
            "green_within_3_rate": 0.0,
            "green_within_5_rate": 0.0,
            "target_1r_rate": 0.0,
            "target_2r_rate": 0.0,
            "stop_before_1r_rate": 0.0,
            "stop_before_2r_rate": 0.0,
            "unresolved_1r_rate": 0.0,
            "unresolved_2r_rate": 0.0,
            "conservative_expectancy_1r": 0.0,
            "conservative_expectancy_2r": 0.0,
            "avg_mfe_r": 0.0,
            "median_mfe_r": 0.0,
            "avg_mae_r": 0.0,
            "median_mae_r": 0.0,
            "avg_max_favorable_points": 0.0,
            "avg_max_adverse_points": 0.0,
            "median_first_green_bar": None,
        }

    green_next = sum(1 for row in results if row.next_bar_green)
    green_3 = sum(1 for row in results if row.green_within_3)
    green_5 = sum(1 for row in results if row.green_within_5)
    hit_1r = sum(1 for row in results if row.target_1r_before_stop)
    hit_2r = sum(1 for row in results if row.target_2r_before_stop)
    stop_1r = sum(1 for row in results if row.stop_before_1r)
    stop_2r = sum(1 for row in results if row.stop_before_2r)
    unresolved_1r = count - hit_1r - stop_1r
    unresolved_2r = count - hit_2r - stop_2r
    first_green_bars = [row.first_green_bar for row in results if row.first_green_bar is not None]
    mfe_values = [row.mfe_r for row in results]
    mae_values = [row.mae_r for row in results]
    max_favorable_points = [row.max_favorable_points for row in results]
    max_adverse_points = [row.max_adverse_points for row in results]

    return {
        "signals": count,
        "green_next_rate": green_next / count * 100.0,
        "green_within_3_rate": green_3 / count * 100.0,
        "green_within_5_rate": green_5 / count * 100.0,
        "target_1r_rate": hit_1r / count * 100.0,
        "target_2r_rate": hit_2r / count * 100.0,
        "stop_before_1r_rate": stop_1r / count * 100.0,
        "stop_before_2r_rate": stop_2r / count * 100.0,
        "unresolved_1r_rate": unresolved_1r / count * 100.0,
        "unresolved_2r_rate": unresolved_2r / count * 100.0,
        "conservative_expectancy_1r": (hit_1r - stop_1r) / count,
        "conservative_expectancy_2r": ((2 * hit_2r) - stop_2r) / count,
        "avg_mfe_r": sum(mfe_values) / count,
        "median_mfe_r": statistics.median(mfe_values),
        "avg_mae_r": sum(mae_values) / count,
        "median_mae_r": statistics.median(mae_values),
        "avg_max_favorable_points": sum(max_favorable_points) / count,
        "avg_max_adverse_points": sum(max_adverse_points) / count,
        "median_first_green_bar": statistics.median(first_green_bars) if first_green_bars else None,
    }


def promotion_recommendation(summary: dict[str, Any], min_signals: int) -> dict[str, Any]:
    reasons: list[str] = []
    if summary["signals"] < min_signals:
        reasons.append(f"needs >= {min_signals} signals")
    if summary["conservative_expectancy_2r"] <= 0:
        reasons.append("non-positive conservative expectancy at 2R")
    if summary["target_2r_rate"] < 40.0:
        reasons.append("2R hit rate below 40%")
    if summary["green_next_rate"] < 85.0:
        reasons.append("green-next rate below 85%")
    return {
        "eligible_for_benchmark_trial": not reasons,
        "reasons": reasons or ["passes offline promotion gate"],
    }


def fetch_bars(symbol: str, days: int) -> list[dict[str, float]]:
    rates = mt5.copy_rates_from_pos(symbol, mt5.TIMEFRAME_M1, 0, 1440 * days)
    if rates is None or len(rates) == 0:
        return []
    return [
        {
            "t": float(rate[0]),
            "o": float(rate[1]),
            "h": float(rate[2]),
            "l": float(rate[3]),
            "c": float(rate[4]),
            "v": float(rate[5]),
        }
        for rate in rates
    ]


def validate_symbol(symbol: str, days: int, lookback: int, lookahead_bars: int, sample_limit: int | None) -> dict[str, Any]:
    bars = fetch_bars(symbol, days)
    results: list[SignalResult] = []
    if not bars:
        return {"symbol": symbol, "summary": summarize_results(results), "examples": []}

    for idx in range(lookback + 5, len(bars) - lookahead_bars):
        window = bars[: idx + 1]
        signal, confidence, stop_loss, thesis = detect_msls_signal(window, lookback=lookback)
        if not signal:
            continue
        entry_bar = bars[idx]
        future_bars = bars[idx + 1 : idx + 1 + lookahead_bars]
        entry_time_utc = datetime.utcfromtimestamp(entry_bar["t"]).isoformat() + "Z"
        analyzed = analyze_signal(
            symbol=symbol,
            signal=signal,
            entry_time_utc=entry_time_utc,
            entry_price=entry_bar["c"],
            stop_loss=stop_loss,
            future_bars=future_bars,
        )
        if analyzed is None:
            continue
        results.append(analyzed)

    summary = summarize_results(results)
    examples = [asdict(result) for result in results[:sample_limit]] if sample_limit else []
    return {
        "symbol": symbol,
        "summary": summary,
        "examples": examples,
        "_results": results,
    }


def run_validation(
    symbols: list[str],
    days: int,
    lookback: int,
    lookahead_bars: int,
    min_signals: int,
    sample_limit: int | None = 5,
) -> dict[str, Any]:
    if not mt5.initialize():
        raise RuntimeError(f"MT5 initialize failed: {mt5.last_error()}")
    try:
        symbol_reports = []
        all_results: list[SignalResult] = []
        for symbol in symbols:
            report = validate_symbol(
                symbol.upper(),
                days=days,
                lookback=lookback,
                lookahead_bars=lookahead_bars,
                sample_limit=sample_limit,
            )
            all_results.extend(report["_results"])
            symbol_reports.append(report)
    finally:
        mt5.shutdown()

    aggregate_summary = summarize_results(all_results)
    for report in symbol_reports:
        report["promotion_gate"] = promotion_recommendation(report["summary"], min_signals)
        report.pop("_results", None)

    aggregate_gate = promotion_recommendation(aggregate_summary, max(min_signals, 100))
    return {
        "symbols": symbol_reports,
        "aggregate": aggregate_summary,
        "aggregate_promotion_gate": aggregate_gate,
        "config": {
            "symbols": symbols,
            "days": days,
            "lookback": lookback,
            "lookahead_bars": lookahead_bars,
            "min_signals": min_signals,
            "sample_limit": sample_limit,
        },
    }


def print_report(report: dict[str, Any]) -> None:
    print("MSLS STRICT VALIDATION REPORT")
    print("=" * 72)
    config = report["config"]
    print(
        f"Symbols={','.join(config['symbols'])} | days={config['days']} "
        f"| lookback={config['lookback']} | lookahead_bars={config['lookahead_bars']}"
    )
    print()
    print(
        f"{'symbol':<8} {'signals':>7} {'green1':>8} {'hit1R':>8} {'hit2R':>8} "
        f"{'stop<2R':>9} {'exp2R':>8} {'avgMFE':>8} {'avgMAE':>8}"
    )
    for symbol_report in report["symbols"]:
        summary = symbol_report["summary"]
        print(
            f"{symbol_report['symbol']:<8} "
            f"{summary['signals']:>7} "
            f"{summary['green_next_rate']:>7.1f}% "
            f"{summary['target_1r_rate']:>7.1f}% "
            f"{summary['target_2r_rate']:>7.1f}% "
            f"{summary['stop_before_2r_rate']:>8.1f}% "
            f"{summary['conservative_expectancy_2r']:>+8.2f} "
            f"{summary['avg_mfe_r']:>8.2f} "
            f"{summary['avg_mae_r']:>8.2f}"
        )
    print()
    aggregate = report["aggregate"]
    print("Aggregate")
    print(
        f"signals={aggregate['signals']} "
        f"green_next={aggregate['green_next_rate']:.1f}% "
        f"hit1R={aggregate['target_1r_rate']:.1f}% "
        f"hit2R={aggregate['target_2r_rate']:.1f}% "
        f"stop<2R={aggregate['stop_before_2r_rate']:.1f}% "
        f"exp2R={aggregate['conservative_expectancy_2r']:+.2f} "
        f"avgMFE={aggregate['avg_mfe_r']:.2f} "
        f"avgMAE={aggregate['avg_mae_r']:.2f}"
    )
    gate = report["aggregate_promotion_gate"]
    print(
        f"Promotion gate: {'PASS' if gate['eligible_for_benchmark_trial'] else 'FAIL'} "
        f"({' ; '.join(gate['reasons'])})"
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--days", type=int, default=30)
    parser.add_argument("--lookback", type=int, default=40)
    parser.add_argument("--lookahead-bars", type=int, default=60)
    parser.add_argument("--min-signals", type=int, default=50)
    parser.add_argument("--sample-limit", type=int, default=5, help="Per-symbol example signals to retain; use -1 for all")
    parser.add_argument("--json-out", type=Path, default=None)
    parser.add_argument("symbols", nargs="*", default=DEFAULT_SYMBOLS)
    args = parser.parse_args()

    report = run_validation(
        symbols=[symbol.upper() for symbol in args.symbols],
        days=args.days,
        lookback=args.lookback,
        lookahead_bars=args.lookahead_bars,
        min_signals=args.min_signals,
        sample_limit=args.sample_limit,
    )
    print_report(report)
    if args.json_out:
        args.json_out.write_text(json.dumps(report, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
