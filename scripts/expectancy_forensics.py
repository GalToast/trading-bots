#!/usr/bin/env python3
"""Symbol expectancy report from trade_behavior_log.jsonl."""

from __future__ import annotations

import argparse
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
LOG_FILE = ROOT / "trade_behavior_log.jsonl"
LEARNER_FILE = ROOT / "symbol_learner.json"
DEFAULT_SYMBOLS = ["AUDCHF", "GBPUSD", "NAS100", "USDCHF", "USDJPY"]


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


def load_learner(path: Path) -> dict:
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def analyze_symbol(trades: list[dict], symbol: str) -> dict | None:
    matching = [trade for trade in trades if str(trade.get("symbol", "")).upper() == symbol]
    if not matching:
        return None
    wins = sum(1 for trade in matching if trade["_pnl"] > 0)
    pnl_sum = sum(trade["_pnl"] for trade in matching)
    count = len(matching)
    return {
        "symbol": symbol,
        "samples": count,
        "avg_pnl": pnl_sum / count if count else 0.0,
        "win_rate": (wins / count * 100.0) if count else 0.0,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("symbols", nargs="*", help="Symbols to analyze")
    args = parser.parse_args()

    if not LOG_FILE.exists():
        print(f"Log file not found: {LOG_FILE}")
        return 1

    trades = load_trades(LOG_FILE)
    learner = load_learner(LEARNER_FILE)
    symbols = [symbol.upper() for symbol in (args.symbols or DEFAULT_SYMBOLS)]

    print("EXPECTANCY FORENSIC REPORT")
    print("=" * 72)
    for symbol in symbols:
        result = analyze_symbol(trades, symbol)
        if result is None:
            print(f"{symbol:<8} no samples")
            continue
        learner_row = learner.get(symbol, {})
        status = "UNLEASHED" if symbol in {"AUDCHF", "GBPUSD", "NAS100", "USDCHF"} else "SHIELDED"
        print(
            f"{symbol:<8} {status:<10} "
            f"samples={result['samples']:<3} "
            f"avg_pnl={result['avg_pnl']:+8.2f} "
            f"win_rate={result['win_rate']:>5.1f}% "
            f"learner_mode={learner_row.get('last_mode', 'N/A')}"
        )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
