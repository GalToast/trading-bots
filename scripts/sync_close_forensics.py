#!/usr/bin/env python3
"""Break down SYNC_CLOSE exits by source, symbol, mode, and ownership."""

from __future__ import annotations

import argparse
import json
from collections import defaultdict
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
LOG_FILE = ROOT / "trade_behavior_log.jsonl"


def load_sync_close_trades(path: Path) -> list[dict]:
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
            exit_reason = str(trade.get("exit_reason", "") or "")
            if not exit_reason.startswith("SYNC_CLOSE"):
                continue
            try:
                trade["_pnl"] = float(trade.get("realized_pnl", 0.0) or 0.0)
            except (TypeError, ValueError):
                trade["_pnl"] = 0.0
            trade["_sync_mode"], trade["_sync_symbol"] = parse_sync_reason(exit_reason, trade)
            trade["_ownership"] = "adopted" if trade.get("adopted") else "direct"
            trade["_origin"] = infer_origin(trade)
            trade["_context_family"] = infer_context_family(trade)
            trades.append(trade)
    return trades


def parse_sync_reason(reason: str, trade: dict) -> tuple[str, str]:
    parts = reason.split(":")
    if len(parts) >= 3:
        return parts[1], ":".join(parts[2:])
    if len(parts) == 2:
        return str(trade.get("entry_mode", "UNKNOWN") or "UNKNOWN"), parts[1]
    return str(trade.get("entry_mode", "UNKNOWN") or "UNKNOWN"), str(trade.get("symbol", "?") or "?")


def infer_origin(trade: dict) -> str:
    context = str(trade.get("entry_context", "") or "")
    if context == "reloaded_position":
        return "reloaded_position"
    return "live_position"


def infer_context_family(trade: dict) -> str:
    context = str(trade.get("entry_context", "") or "")
    if context == "reloaded_position":
        return "reloaded_position"
    if "flat_rebuild=" in context:
        return "flat_rebuild"
    if "post_cleanup" in context:
        return "post_cleanup"
    if "posture=REARM" in context:
        return "rearm"
    if "posture=DEFEND" in context:
        return "defend"
    return "other"


def summarize(trades: list[dict], key_fn) -> list[dict]:
    groups: dict[str, dict[str, float | int]] = defaultdict(lambda: {"pnl": 0.0, "count": 0, "wins": 0})
    for trade in trades:
        key = key_fn(trade)
        row = groups[key]
        row["pnl"] += trade["_pnl"]
        row["count"] += 1
        row["wins"] += 1 if trade["_pnl"] > 0 else 0
    results = []
    for key, row in groups.items():
        count = int(row["count"])
        pnl = float(row["pnl"])
        wins = int(row["wins"])
        results.append(
            {
                "key": key,
                "count": count,
                "pnl": pnl,
                "avg_pnl": pnl / count if count else 0.0,
                "win_rate": wins / count * 100.0 if count else 0.0,
            }
        )
    results.sort(key=lambda item: item["pnl"])
    return results


def print_table(title: str, rows: list[dict], limit: int) -> None:
    print()
    print(title)
    print("-" * len(title))
    print(f"{'bucket':<24} {'count':>5} {'pnl':>12} {'avg':>10} {'win_rate':>10}")
    for row in rows[:limit]:
        print(
            f"{row['key']:<24} "
            f"{row['count']:>5} "
            f"{row['pnl']:>+12.2f} "
            f"{row['avg_pnl']:>+10.2f} "
            f"{row['win_rate']:>9.1f}%"
        )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--limit", type=int, default=12, help="Rows to show per section")
    args = parser.parse_args()

    if not LOG_FILE.exists():
        print(f"Log file not found: {LOG_FILE}")
        return 1

    trades = load_sync_close_trades(LOG_FILE)
    total_pnl = sum(trade["_pnl"] for trade in trades)
    wins = sum(1 for trade in trades if trade["_pnl"] > 0)

    print("SYNC_CLOSE FORENSIC REPORT")
    print("=" * 72)
    print("Emitter path: main_loop_sync in mt5_bot_v10.py")
    print("Cleanup exits use their own reasons and are not part of this SYNC_CLOSE bucket.")
    print(
        f"Total sync closes: {len(trades)} | total_pnl={total_pnl:+.2f} "
        f"| avg_pnl={total_pnl / len(trades):+.2f} | win_rate={wins / len(trades) * 100.0:.1f}%"
    )

    print_table("By origin", summarize(trades, lambda trade: trade["_origin"]), args.limit)
    print_table("By context family", summarize(trades, lambda trade: trade["_context_family"]), args.limit)
    print_table("By ownership", summarize(trades, lambda trade: trade["_ownership"]), args.limit)
    print_table("By sync mode", summarize(trades, lambda trade: trade["_sync_mode"]), args.limit)
    print_table("Worst symbols", summarize(trades, lambda trade: trade["_sync_symbol"]), args.limit)
    print_table("Worst raw reasons", summarize(trades, lambda trade: str(trade.get("exit_reason", ""))), args.limit)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
