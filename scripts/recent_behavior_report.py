from __future__ import annotations

import argparse
import json
import math
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from statistics import median
from typing import Any


ROOT_DIR = Path(__file__).resolve().parents[1]
TRADE_BEHAVIOR_LOG = ROOT_DIR / "trade_behavior_log.jsonl"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Recent trade behavior telemetry report.")
    parser.add_argument("--lookback-days", type=int, default=7, help="How many days of telemetry to include.")
    parser.add_argument("--limit", type=int, default=0, help="Optional cap on most-recent records after filtering.")
    parser.add_argument("--json", action="store_true", help="Emit JSON instead of text.")
    return parser.parse_args()


def parse_iso8601(value: str) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def load_records(lookback_days: int, limit: int) -> list[dict[str, Any]]:
    if not TRADE_BEHAVIOR_LOG.exists():
        return []

    cutoff = datetime.now(timezone.utc) - timedelta(days=lookback_days)
    records: list[dict[str, Any]] = []
    with TRADE_BEHAVIOR_LOG.open("r", encoding="utf-8") as handle:
        for raw_line in handle:
            line = raw_line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            recorded_at = parse_iso8601(str(record.get("recorded_at_utc", "") or ""))
            if recorded_at is None or recorded_at < cutoff:
                continue
            records.append(record)

    records.sort(key=lambda item: item.get("recorded_at_utc", ""))
    if limit > 0:
        records = records[-limit:]
    return records


def valid_number(value: Any) -> float | None:
    if value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if math.isnan(number) or math.isinf(number):
        return None
    return number


def summarize_bucket(records: list[dict[str, Any]]) -> dict[str, Any]:
    trades = len(records)
    first_green = [r for r in records if valid_number(r.get("time_to_first_green_seconds")) is not None]
    hit_025 = [r for r in records if r.get("hit_0_25_atr_before_minus_0_35_atr")]
    hit_05 = [r for r in records if r.get("hit_0_5_atr_before_minus_0_35_atr")]
    winners = [r for r in records if valid_number(r.get("realized_pnl")) is not None and float(r["realized_pnl"]) > 0]
    pnls = [float(r["realized_pnl"]) for r in records if valid_number(r.get("realized_pnl")) is not None]
    time_to_green = [float(r["time_to_first_green_seconds"]) for r in records if valid_number(r.get("time_to_first_green_seconds")) is not None]
    mfe_atr = [float(r["max_favorable_excursion_atr"]) for r in records if valid_number(r.get("max_favorable_excursion_atr")) is not None]
    mae_atr = [float(r["max_adverse_excursion_atr"]) for r in records if valid_number(r.get("max_adverse_excursion_atr")) is not None]

    return {
        "trades": trades,
        "green_fast_rate": len(first_green) / trades if trades else 0.0,
        "hit_0_25_before_fail_rate": len(hit_025) / trades if trades else 0.0,
        "hit_0_5_before_fail_rate": len(hit_05) / trades if trades else 0.0,
        "profit_rate": len(winners) / trades if trades else 0.0,
        "net_pnl": sum(pnls),
        "median_time_to_first_green_seconds": median(time_to_green) if time_to_green else None,
        "median_mfe_atr": median(mfe_atr) if mfe_atr else None,
        "median_mae_atr": median(mae_atr) if mae_atr else None,
    }


def summarize_by(records: list[dict[str, Any]], key_name: str) -> list[dict[str, Any]]:
    buckets: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for record in records:
        key = str(record.get(key_name, "UNKNOWN") or "UNKNOWN")
        buckets[key].append(record)

    rows: list[dict[str, Any]] = []
    for key, bucket in buckets.items():
        row = summarize_bucket(bucket)
        row["key"] = key
        rows.append(row)

    rows.sort(
        key=lambda row: (
            row["hit_0_25_before_fail_rate"],
            row["profit_rate"],
            row["net_pnl"],
            row["trades"],
        ),
        reverse=True,
    )
    return rows


def fmt_pct(value: float) -> str:
    return f"{value * 100:5.1f}%"


def fmt_money(value: float) -> str:
    return f"${value:+.2f}"


def fmt_metric(value: float | None) -> str:
    if value is None:
        return "-"
    return f"{value:.2f}"


def render_rows(title: str, rows: list[dict[str, Any]]) -> list[str]:
    lines = [title]
    if not rows:
        lines.append("  (no records)")
        return lines

    for row in rows:
        lines.append(
            "  "
            f"{row['key']:<26} "
            f"trades={row['trades']:<3} "
            f"green={fmt_pct(row['green_fast_rate'])} "
            f"hit025={fmt_pct(row['hit_0_25_before_fail_rate'])} "
            f"hit05={fmt_pct(row['hit_0_5_before_fail_rate'])} "
            f"profit={fmt_pct(row['profit_rate'])} "
            f"net={fmt_money(row['net_pnl']):>9} "
            f"ttfg={fmt_metric(row['median_time_to_first_green_seconds']):>6} "
            f"mfe_atr={fmt_metric(row['median_mfe_atr']):>5} "
            f"mae_atr={fmt_metric(row['median_mae_atr']):>5}"
        )
    return lines


def main() -> int:
    args = parse_args()
    records = load_records(args.lookback_days, args.limit)
    overall = summarize_bucket(records)
    by_mode = summarize_by(records, "entry_mode")
    by_signal_type = summarize_by(records, "entry_signal_type")[:12]
    by_context = summarize_by(records, "entry_context")[:12]
    by_symbol = summarize_by(records, "symbol")[:10]
    by_exit = summarize_by(records, "exit_reason")[:10]

    if args.json:
        payload = {
            "record_count": len(records),
            "overall": overall,
            "by_mode": by_mode,
            "by_signal_type_top12": by_signal_type,
            "by_context_top12": by_context,
            "by_symbol_top10": by_symbol,
            "by_exit_reason_top10": by_exit,
        }
        print(json.dumps(payload, indent=2, sort_keys=True))
        return 0

    lines = [
        f"Behavior telemetry records: {len(records)}",
        (
            "Overall "
            f"green={fmt_pct(overall['green_fast_rate'])} "
            f"hit025={fmt_pct(overall['hit_0_25_before_fail_rate'])} "
            f"hit05={fmt_pct(overall['hit_0_5_before_fail_rate'])} "
            f"profit={fmt_pct(overall['profit_rate'])} "
            f"net={fmt_money(overall['net_pnl'])} "
            f"ttfg={fmt_metric(overall['median_time_to_first_green_seconds'])} "
            f"mfe_atr={fmt_metric(overall['median_mfe_atr'])} "
            f"mae_atr={fmt_metric(overall['median_mae_atr'])}"
        ),
        "",
    ]
    lines.extend(render_rows("By mode", by_mode))
    lines.append("")
    lines.extend(render_rows("By signal type", by_signal_type))
    lines.append("")
    lines.extend(render_rows("By entry context", by_context))
    lines.append("")
    lines.extend(render_rows("Top symbols", by_symbol))
    lines.append("")
    lines.extend(render_rows("Top exit reasons", by_exit))
    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
