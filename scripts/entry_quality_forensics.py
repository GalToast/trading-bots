#!/usr/bin/env python3
"""Entry-quality report for current live-style symbols and modes."""

from __future__ import annotations

import argparse
import json
from collections import defaultdict
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
LOG_FILE = ROOT / "trade_behavior_log.jsonl"
DEFAULT_SYMBOLS = {"AUDCHF", "GBPUSD", "NAS100", "USDCHF", "USDJPY"}
DEFAULT_MODES = {"SNIPER", "SHOTGUN"}


def load_trades(path: Path) -> list[dict]:
    trades: list[dict] = []
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                trade = json.loads(line)
            except json.JSONDecodeError:
                continue
            try:
                trade["_pnl"] = float(trade.get("realized_pnl", 0.0) or 0.0)
            except (TypeError, ValueError):
                trade["_pnl"] = 0.0
            trades.append(trade)
    return trades


def is_fast_green(trade: dict, threshold_s: float) -> bool:
    ttfg = trade.get("time_to_first_green_seconds")
    return isinstance(ttfg, (int, float)) and ttfg is not None and ttfg <= threshold_s


def has_green(trade: dict) -> bool:
    ttfg = trade.get("time_to_first_green_seconds")
    return isinstance(ttfg, (int, float)) and ttfg is not None


def summarize_group(rows: list[dict], label: str, fast_green_threshold_s: float) -> dict:
    wins = sum(1 for row in rows if row["_pnl"] > 0)
    fast_green = sum(1 for row in rows if is_fast_green(row, fast_green_threshold_s))
    ever_green = sum(1 for row in rows if has_green(row))
    pnl = sum(row["_pnl"] for row in rows)
    ttfg_values = [
        float(row["time_to_first_green_seconds"])
        for row in rows
        if isinstance(row.get("time_to_first_green_seconds"), (int, float))
    ]
    return {
        "label": label,
        "count": len(rows),
        "avg_pnl": pnl / len(rows) if rows else 0.0,
        "win_rate": wins / len(rows) * 100.0 if rows else 0.0,
        "green_30": fast_green / len(rows) * 100.0 if rows else 0.0,
        "ever_green": ever_green / len(rows) * 100.0 if rows else 0.0,
        "avg_ttfg_seen": (sum(ttfg_values) / len(ttfg_values)) if ttfg_values else None,
    }


def print_table(title: str, rows: list[dict]) -> None:
    print()
    print(title)
    print("-" * len(title))
    print(f"{'bucket':<48} {'n':>4} {'avg_pnl':>10} {'win%':>8} {'green30%':>10} {'ever%':>8} {'avg_ttfg':>10}")
    for row in rows:
        avg_ttfg = f"{row['avg_ttfg_seen']:.1f}" if row["avg_ttfg_seen"] is not None else "-"
        print(
            f"{row['label']:<48} "
            f"{row['count']:>4} "
            f"{row['avg_pnl']:>+10.2f} "
            f"{row['win_rate']:>7.1f}% "
            f"{row['green_30']:>9.1f}% "
            f"{row['ever_green']:>7.1f}% "
            f"{avg_ttfg:>10}"
        )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fast-green-seconds", type=float, default=30.0)
    parser.add_argument("--min-samples", type=int, default=2)
    args = parser.parse_args()

    trades = load_trades(LOG_FILE)
    filtered = [
        trade
        for trade in trades
        if str(trade.get("symbol", "")).upper() in DEFAULT_SYMBOLS
        and str(trade.get("entry_mode", "")).upper() in DEFAULT_MODES
        and not trade.get("adopted")
    ]

    print("ENTRY QUALITY FORENSIC REPORT")
    print("=" * 72)
    print(f"Filtered trades: {len(filtered)} | symbols={sorted(DEFAULT_SYMBOLS)} | modes={sorted(DEFAULT_MODES)}")

    by_signal: dict[str, list[dict]] = defaultdict(list)
    by_symbol_signal: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for trade in filtered:
        signal = str(trade.get("entry_signal_type", "") or "unknown")
        symbol = str(trade.get("symbol", "")).upper()
        by_signal[signal].append(trade)
        by_symbol_signal[(symbol, signal)].append(trade)

    signal_rows = [
        summarize_group(rows, signal, args.fast_green_seconds)
        for signal, rows in by_signal.items()
        if len(rows) >= args.min_samples
    ]
    signal_rows.sort(key=lambda row: (row["avg_pnl"], row["count"]))
    print_table("By signal", signal_rows)

    symbol_signal_rows = [
        summarize_group(rows, f"{symbol} | {signal}", args.fast_green_seconds)
        for (symbol, signal), rows in by_symbol_signal.items()
        if len(rows) >= args.min_samples
    ]
    symbol_signal_rows.sort(key=lambda row: (row["avg_pnl"], row["count"]))
    print_table("By symbol + signal", symbol_signal_rows)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
