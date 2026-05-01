#!/usr/bin/env python3
from __future__ import annotations

import json
from collections import Counter, defaultdict
from pathlib import Path
from statistics import mean


ROOT = Path(__file__).resolve().parent.parent
TRADE_LOG = ROOT / "trade_behavior_log.jsonl"
TARGET_PREFIX = "usd_breakout_"


def load_rows() -> list[dict]:
    rows: list[dict] = []
    if not TRADE_LOG.exists():
        return rows
    with TRADE_LOG.open("r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            lane_id = str(row.get("strategy_lab_lane_id", "") or "")
            if lane_id.startswith(TARGET_PREFIX):
                rows.append(row)
    return rows


def fmt_money(value: float) -> str:
    return f"{value:+.2f}"


def summarize(rows: list[dict]) -> str:
    trades = len(rows)
    pnl_values = [float(r.get("realized_pnl", 0.0) or 0.0) for r in rows]
    wins = [v for v in pnl_values if v > 0]
    losses = [v for v in pnl_values if v < 0]
    total = sum(pnl_values)
    expectancy = total / trades if trades else 0.0
    first_green = (
        sum(1 for r in rows if r.get("first_green_before_fail")) / trades * 100.0 if trades else 0.0
    )
    capture = [
        float(r.get("mfe_capture_pct"))
        for r in rows
        if r.get("mfe_capture_pct") is not None
    ]
    exits = Counter(str(r.get("exit_reason", "UNKNOWN")).split("(")[0].strip() or "UNKNOWN" for r in rows)
    return (
        f"trades={trades} wr={(len(wins) / trades * 100.0 if trades else 0.0):.1f}% "
        f"net={fmt_money(total)} exp={fmt_money(expectancy)} "
        f"fg={first_green:.1f}% capture={(mean(capture) if capture else 0.0):.1f}% "
        f"exits=" + ",".join(f"{k}={v}" for k, v in exits.most_common(3))
    )


def main() -> None:
    rows = load_rows()
    print(f"Trade log: {TRADE_LOG}")
    if not rows:
        print("No USDJPY lane-portfolio rows found yet.")
        return

    grouped: dict[str, list[dict]] = defaultdict(list)
    for row in rows:
        lane_id = str(row.get("strategy_lab_lane_id", "") or "unknown")
        grouped[lane_id].append(row)

    print("USDJPY lane portfolio")
    for lane_id, lane_rows in sorted(grouped.items()):
        variant = str(lane_rows[-1].get("strategy_lab_variant", "") or "")
        role = str(lane_rows[-1].get("strategy_lab_role", "") or "")
        hypothesis = str(lane_rows[-1].get("strategy_lab_hypothesis", "") or "")
        print(f"{lane_id} [{role}]")
        print(f"  variant: {variant}")
        print(f"  hypothesis: {hypothesis}")
        print(f"  {summarize(lane_rows)}")


if __name__ == "__main__":
    main()
