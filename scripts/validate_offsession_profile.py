#!/usr/bin/env python3
"""Fresh-entry off-session validator for configured and proposed profiles."""

from __future__ import annotations

import argparse
import ast
import json
from collections import defaultdict
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent.parent
LOG_FILE = ROOT / "trade_behavior_log.jsonl"
BOT_FILE = ROOT / "mt5_bot_v10.py"
OFF_SESSION_HOURS = set(range(21, 24)) | set(range(0, 7))
ASIAN_SIGNAL_TYPES = {"asian_range_buy", "asian_range_sell"}


@dataclass
class ProfileMetrics:
    count: int
    realized_pnl: float
    avg_pnl: float
    win_rate: float
    green_30_rate: float
    ever_green_rate: float
    avg_confidence: float | None
    avg_hold_seconds: float | None


def load_trades(path: Path) -> list[dict[str, Any]]:
    trades: list[dict[str, Any]] = []
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
            trade["_symbol"] = str(trade.get("symbol", "")).upper()
            trade["_signal"] = str(trade.get("entry_signal_type") or "unlabeled")
            trade["_mode"] = str(trade.get("entry_mode") or "")
            trade["_confidence"] = _float_or_none(trade.get("entry_confidence_raw"))
            trade["_hold_seconds"] = _float_or_none(trade.get("hold_seconds"))
            trade["_time_to_first_green_seconds"] = _float_or_none(trade.get("time_to_first_green_seconds"))
            trade["_utc_hour"] = extract_utc_hour(trade.get("entry_time_utc"))
            trades.append(trade)
    return trades


def _float_or_none(value: Any) -> float | None:
    if isinstance(value, (int, float)):
        return float(value)
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def extract_utc_hour(raw_value: Any) -> int | None:
    if not raw_value:
        return None
    try:
        dt = datetime.fromisoformat(str(raw_value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt.astimezone(timezone.utc).hour


def extract_named_set(path: Path, variable_name: str) -> set[str]:
    module = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    for node in module.body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == variable_name:
                    try:
                        value = ast.literal_eval(node.value)
                    except Exception:
                        return set()
                    if isinstance(value, (set, list, tuple)):
                        return {str(item) for item in value}
    return set()


def fresh_offsession_trades(trades: list[dict[str, Any]], *, include_unlabeled: bool) -> list[dict[str, Any]]:
    rows = []
    for trade in trades:
        if trade.get("adopted"):
            continue
        if trade["_utc_hour"] not in OFF_SESSION_HOURS:
            continue
        if not include_unlabeled and trade["_signal"] == "unlabeled":
            continue
        rows.append(trade)
    return rows


def summarize(rows: list[dict[str, Any]]) -> ProfileMetrics:
    count = len(rows)
    if not count:
        return ProfileMetrics(0, 0.0, 0.0, 0.0, 0.0, 0.0, None, None)

    wins = sum(1 for row in rows if row["_pnl"] > 0)
    fast_green = sum(
        1
        for row in rows
        if row["_time_to_first_green_seconds"] is not None and row["_time_to_first_green_seconds"] <= 30.0
    )
    ever_green = sum(1 for row in rows if row["_time_to_first_green_seconds"] is not None)
    confidences = [row["_confidence"] for row in rows if row["_confidence"] is not None]
    holds = [row["_hold_seconds"] for row in rows if row["_hold_seconds"] is not None]
    pnl = sum(row["_pnl"] for row in rows)
    return ProfileMetrics(
        count=count,
        realized_pnl=pnl,
        avg_pnl=(pnl / count) if count else 0.0,
        win_rate=wins / count * 100.0,
        green_30_rate=fast_green / count * 100.0,
        ever_green_rate=ever_green / count * 100.0,
        avg_confidence=(sum(confidences) / len(confidences)) if confidences else None,
        avg_hold_seconds=(sum(holds) / len(holds)) if holds else None,
    )


def filter_profile(
    rows: list[dict[str, Any]],
    *,
    symbols: set[str] | None = None,
    signals_allow: set[str] | None = None,
    signals_block: set[str] | None = None,
) -> list[dict[str, Any]]:
    filtered = []
    for row in rows:
        if symbols is not None and row["_symbol"] not in symbols:
            continue
        if signals_block and row["_signal"] in signals_block:
            continue
        if signals_allow is not None and row["_signal"] not in signals_allow:
            continue
        filtered.append(row)
    return filtered


def summarize_groups(
    rows: list[dict[str, Any]],
    *,
    key_builder,
    min_samples: int,
) -> list[dict[str, Any]]:
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        groups[key_builder(row)].append(row)

    summaries = []
    for key, group_rows in groups.items():
        if len(group_rows) < min_samples:
            continue
        metrics = summarize(group_rows)
        summaries.append(
            {
                "label": key,
                **asdict(metrics),
            }
        )
    summaries.sort(key=lambda item: (item["realized_pnl"], item["count"]))
    return summaries


def recommendation(metrics: ProfileMetrics, *, min_samples: int) -> dict[str, Any]:
    reasons: list[str] = []
    if metrics.count < min_samples:
        reasons.append(f"needs >= {min_samples} fresh samples")
    if metrics.realized_pnl <= 0:
        reasons.append("non-positive realized pnl")
    if metrics.avg_pnl <= 0:
        reasons.append("non-positive avg pnl")
    if metrics.win_rate < 40.0:
        reasons.append("win rate below 40%")
    return {
        "eligible_for_reload_trial": not reasons,
        "reasons": reasons or ["passes fresh-entry off-session gate"],
    }


def build_report(*, include_unlabeled: bool, min_samples: int) -> dict[str, Any]:
    trades = load_trades(LOG_FILE)
    base_rows = fresh_offsession_trades(trades, include_unlabeled=include_unlabeled)

    symbol_allowlist = extract_named_set(BOT_FILE, "SYMBOL_ALLOWLIST")
    off_session_allowlist = extract_named_set(BOT_FILE, "OFF_SESSION_ALLOWLIST")
    off_session_signal_allowlist = extract_named_set(BOT_FILE, "OFF_SESSION_SIGNAL_ALLOWLIST")
    off_session_signal_blocklist = extract_named_set(BOT_FILE, "OFF_SESSION_SIGNAL_BLOCKLIST")
    asian_session_symbols = extract_named_set(BOT_FILE, "ASIAN_SESSION_SYMBOLS")

    configured_off_session_rows = filter_profile(
        base_rows,
        symbols=off_session_allowlist or None,
        signals_allow=off_session_signal_allowlist or None,
        signals_block=off_session_signal_blocklist or None,
    )
    reachable_off_session_symbols = off_session_allowlist & symbol_allowlist if off_session_allowlist else set()
    reachable_off_session_rows = filter_profile(
        base_rows,
        symbols=reachable_off_session_symbols or None,
        signals_allow=off_session_signal_allowlist or None,
        signals_block=off_session_signal_blocklist or None,
    )
    asian_lane_rows = filter_profile(
        base_rows,
        symbols=asian_session_symbols or None,
        signals_allow=ASIAN_SIGNAL_TYPES,
    )

    return {
        "config": {
            "include_unlabeled": include_unlabeled,
            "min_samples": min_samples,
            "off_session_hours_utc": sorted(OFF_SESSION_HOURS),
            "configured_off_session_symbols": sorted(off_session_allowlist),
            "global_symbol_allowlist": sorted(symbol_allowlist),
            "reachable_off_session_symbols": sorted(reachable_off_session_symbols),
            "configured_off_session_signal_allowlist": sorted(off_session_signal_allowlist),
            "configured_off_session_signal_blocklist": sorted(off_session_signal_blocklist),
            "asian_session_symbols": sorted(asian_session_symbols),
            "asian_signal_types": sorted(ASIAN_SIGNAL_TYPES),
        },
        "base_fresh_offsession": {
            "summary": asdict(summarize(base_rows)),
            "by_symbol": summarize_groups(base_rows, key_builder=lambda row: row["_symbol"], min_samples=min_samples),
            "by_signal": summarize_groups(base_rows, key_builder=lambda row: row["_signal"], min_samples=min_samples),
            "by_symbol_signal": summarize_groups(
                base_rows,
                key_builder=lambda row: f"{row['_symbol']} | {row['_signal']}",
                min_samples=min_samples,
            ),
        },
        "profiles": {
            "configured_off_session_profile": {
                "summary": asdict(summarize(configured_off_session_rows)),
                "recommendation": recommendation(summarize(configured_off_session_rows), min_samples=min_samples),
            },
            "reachable_off_session_profile": {
                "summary": asdict(summarize(reachable_off_session_rows)),
                "recommendation": recommendation(summarize(reachable_off_session_rows), min_samples=min_samples),
            },
            "asian_lane_realized_evidence": {
                "summary": asdict(summarize(asian_lane_rows)),
                "recommendation": recommendation(summarize(asian_lane_rows), min_samples=min_samples),
            },
        },
    }


def print_rows(title: str, rows: list[dict[str, Any]]) -> None:
    print()
    print(title)
    print("-" * len(title))
    if not rows:
        print("no rows")
        return
    print(f"{'bucket':<40} {'n':>4} {'pnl':>10} {'avg':>9} {'win%':>8} {'g30%':>8} {'ever%':>8}")
    for row in rows:
        print(
            f"{row['label']:<40} "
            f"{row['count']:>4} "
            f"{row['realized_pnl']:>+10.2f} "
            f"{row['avg_pnl']:>+9.2f} "
            f"{row['win_rate']:>7.1f}% "
            f"{row['green_30_rate']:>7.1f}% "
            f"{row['ever_green_rate']:>7.1f}%"
        )


def print_profile(label: str, payload: dict[str, Any]) -> None:
    summary = payload["summary"]
    rec = payload["recommendation"]
    print()
    print(label)
    print("-" * len(label))
    print(
        f"count={summary['count']} pnl={summary['realized_pnl']:+.2f} avg={summary['avg_pnl']:+.2f} "
        f"win={summary['win_rate']:.1f}% green30={summary['green_30_rate']:.1f}%"
    )
    print(
        f"reload_gate={'PASS' if rec['eligible_for_reload_trial'] else 'FAIL'} "
        f"({' ; '.join(rec['reasons'])})"
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--include-unlabeled", action="store_true", help="Include unlabeled entries (default excludes them)")
    parser.add_argument("--min-samples", type=int, default=2)
    parser.add_argument("--json-out", type=Path, default=None)
    args = parser.parse_args()

    report = build_report(include_unlabeled=args.include_unlabeled, min_samples=args.min_samples)

    print("OFF-SESSION FRESH ENTRY VALIDATION")
    print("=" * 72)
    print(
        f"fresh_only=yes | include_unlabeled={'yes' if args.include_unlabeled else 'no'} "
        f"| min_samples={args.min_samples}"
    )
    print(
        f"configured_symbols={report['config']['configured_off_session_symbols']} "
        f"| reachable={report['config']['reachable_off_session_symbols']}"
    )
    print_rows("By symbol", report["base_fresh_offsession"]["by_symbol"])
    print_rows("By signal", report["base_fresh_offsession"]["by_signal"])
    print_rows("By symbol + signal", report["base_fresh_offsession"]["by_symbol_signal"])
    for label, payload in report["profiles"].items():
        print_profile(label, payload)

    if args.json_out:
        args.json_out.write_text(json.dumps(report, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
