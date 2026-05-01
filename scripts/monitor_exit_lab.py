#!/usr/bin/env python3
"""Live monitor for the USDJPY breakout exit challenger.

Polls both the trade log and lab events log, printing a compact
dashboard of challenger vs baseline performance.
Run: python scripts/monitor_exit_lab.py
"""
from __future__ import annotations

import json
import time
from collections import Counter, defaultdict
from pathlib import Path
from statistics import mean

ROOT = Path(__file__).resolve().parent.parent
TRADE_LOG = ROOT / "trade_behavior_log.jsonl"
LAB_LOG = ROOT / "strategy_lab_events.jsonl"

SYMBOL = "USDJPY"
SIGNAL = "breakout_hold_above_high"
MODE = "SNIPER"
REGIME = "PRICE"


def load_jsonl(path: Path) -> list[dict]:
    rows: list[dict] = []
    if not path.exists():
        return rows
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return rows


def is_lab_trade(row: dict) -> bool:
    return (
        str(row.get("symbol", "")).upper() == SYMBOL
        and str(row.get("entry_signal_type", "")) == SIGNAL
        and str(row.get("entry_mode", "")).upper() == MODE
        and str(row.get("regime_at_entry", "")).upper() == REGIME
    )


def classify_trade(row: dict) -> str:
    exit_reason = str(row.get("exit_reason", ""))
    if "TRAIL_LAB" in exit_reason:
        return "LAB"
    if "TRAIL" in exit_reason:
        return "BASELINE"
    return exit_reason.split("(")[0].split(":")[0].strip() or "OTHER"


def main():
    trades = [r for r in load_jsonl(TRADE_LOG) if is_lab_trade(r)]
    events = [r for r in load_jsonl(LAB_LOG)
              if str(r.get("symbol", "")).upper() == SYMBOL
              and str(r.get("signal_type", "")) == SIGNAL]

    # Classify recent trades
    recent = trades[-30:] if len(trades) > 30 else trades
    lab_trades = []
    baseline_trades = []
    other_trades = []

    for t in recent:
        pnl = float(t.get("realized_pnl", 0.0) or 0.0)
        cls = classify_trade(t)
        if cls == "LAB":
            lab_trades.append(t)
        elif cls == "BASELINE":
            baseline_trades.append(t)
        else:
            other_trades.append(t)

    # Event counts
    event_counts = Counter(e.get("event_type", "?") for e in events)

    # Give-back analysis
    def giveback(trade):
        peak = float(trade.get("max_favorable_excursion_pnl", 0.0) or 0.0)
        realized = float(trade.get("realized_pnl", 0.0) or 0.0)
        if peak <= 0:
            return None
        return (peak - realized) / peak * 100

    print("=" * 70)
    print(f"  EXIT LAB MONITOR — {SYMBOL}|{SIGNAL}|{MODE}|{REGIME}")
    print("=" * 70)
    print()

    print(f"Lab events total: {sum(event_counts.values())}")
    for evt, cnt in event_counts.most_common():
        print(f"  {evt}: {cnt}")
    print()

    print(f"Recent lab trades (last {len(lab_trades)}):")
    for t in lab_trades:
        pnl = float(t.get("realized_pnl", 0.0) or 0.0)
        peak = float(t.get("max_favorable_excursion_pnl", 0.0) or 0.0)
        gb = giveback(t)
        hold = float(t.get("hold_seconds", 0.0) or 0.0)
        fg = t.get("first_green_before_fail", "?")
        print(f"  P/L ${pnl:+.2f} | peak ${peak:+.2f} | give-back {gb:.0f}% | hold {hold:.0f}s | FG={fg}")

    print()
    print(f"Recent baseline trades (last {len(baseline_trades)}):")
    for t in baseline_trades:
        pnl = float(t.get("realized_pnl", 0.0) or 0.0)
        peak = float(t.get("max_favorable_excursion_pnl", 0.0) or 0.0)
        gb = giveback(t)
        hold = float(t.get("hold_seconds", 0.0) or 0.0)
        fg = t.get("first_green_before_fail", "?")
        print(f"  P/L ${pnl:+.2f} | peak ${peak:+.2f} | give-back {gb:.0f}% | hold {hold:.0f}s | FG={fg}")

    print()
    print(f"Other exits ({len(other_trades)}):")
    for t in other_trades:
        pnl = float(t.get("realized_pnl", 0.0) or 0.0)
        reason = str(t.get("exit_reason", "?"))[:50]
        print(f"  P/L ${pnl:+.2f} | {reason}")

    if lab_trades:
        lab_pnl = sum(float(t.get("realized_pnl", 0.0) or 0.0) for t in lab_trades)
        lab_gb = [giveback(t) for t in lab_trades if giveback(t) is not None]
        print(f"\n  LAB summary: n={len(lab_trades)} pnl=${lab_pnl:+.2f} avg_giveback={mean(lab_gb):.0f}%")

    if baseline_trades:
        base_pnl = sum(float(t.get("realized_pnl", 0.0) or 0.0) for t in baseline_trades)
        base_gb = [giveback(t) for t in baseline_trades if giveback(t) is not None]
        print(f"  BASELINE summary: n={len(baseline_trades)} pnl=${base_pnl:+.2f} avg_giveback={mean(base_gb):.0f}%")

    print()
    print("=" * 70)


if __name__ == "__main__":
    main()
