#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent.parent
REPORTS = ROOT / "reports"
DEFAULT_EVENTS_PATH = REPORTS / "kraken_spot_velocity_shadow_events.jsonl"
DEFAULT_JSON_PATH = REPORTS / "kraken_spot_velocity_loss_autopsy.json"
DEFAULT_CSV_PATH = REPORTS / "kraken_spot_velocity_loss_autopsy.csv"
DEFAULT_MD_PATH = REPORTS / "kraken_spot_velocity_loss_autopsy.md"


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def to_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def parse_time(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value))
    except ValueError:
        return None


def parse_set(value: Any) -> set[str]:
    return {str(item).strip() for item in str(value or "").split(",") if str(item).strip()}


def load_events(path: Path) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    if not path.exists():
        return events
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                parsed = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(parsed, dict):
                events.append(parsed)
    events.sort(key=lambda event: str(event.get("at") or ""))
    return events


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    tmp.replace(path)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Autopsy Kraken velocity shadow closes against entry guardrails.")
    parser.add_argument("--events-path", default=str(DEFAULT_EVENTS_PATH))
    parser.add_argument("--json-path", default=str(DEFAULT_JSON_PATH))
    parser.add_argument("--csv-path", default=str(DEFAULT_CSV_PATH))
    parser.add_argument("--md-path", default=str(DEFAULT_MD_PATH))
    parser.add_argument("--min-kraken-edge-bps", type=float, default=50.0)
    parser.add_argument("--max-spread-bps", type=float, default=100.0)
    parser.add_argument("--allowed-signal-states", default="live_hot")
    parser.add_argument("--allowed-best-windows", default="last,30s,60s,5m")
    parser.add_argument("--required-verdicts", default="clears_both_fee_models,kraken_fee_flip_candidate")
    parser.add_argument("--max-entry-chase-bps", type=float, default=450.0)
    return parser.parse_args()


def guard_blockers(row: dict[str, Any], args: argparse.Namespace) -> list[str]:
    blockers: list[str] = []
    allowed_signal_states = parse_set(args.allowed_signal_states)
    allowed_best_windows = parse_set(args.allowed_best_windows)
    required_verdicts = parse_set(args.required_verdicts)
    if to_float(row.get("kraken_edge_bps")) < float(args.min_kraken_edge_bps):
        blockers.append("edge_below_min")
    if to_float(row.get("spread_bps")) > float(args.max_spread_bps):
        blockers.append("spread_too_wide")
    if allowed_signal_states and str(row.get("signal_state") or "") not in allowed_signal_states:
        blockers.append("signal_state_not_allowed")
    if allowed_best_windows and str(row.get("best_move_window") or "") not in allowed_best_windows:
        blockers.append("best_window_not_allowed")
    if required_verdicts and str(row.get("verdict") or "") not in required_verdicts:
        blockers.append("verdict_not_allowed")
    if to_float(row.get("best_move_bps")) > float(args.max_entry_chase_bps):
        blockers.append("entry_chase_too_large")
    if not bool(row.get("can_trade_starting_cash", True)):
        blockers.append("blocked_min_size")
    return blockers


def pair_trades(events: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    opens_by_product: dict[str, list[dict[str, Any]]] = defaultdict(list)
    trades: list[dict[str, Any]] = []
    unmatched_closes: list[dict[str, Any]] = []
    used_open_ids: set[int] = set()
    for event in events:
        event_type = event.get("event")
        product_id = str(event.get("product_id") or "")
        if event_type == "shadow_open" and product_id:
            opens_by_product[product_id].append(event)
        elif event_type == "shadow_close" and product_id:
            candidates = [open_event for open_event in opens_by_product.get(product_id, []) if id(open_event) not in used_open_ids]
            if not candidates:
                unmatched_closes.append(event)
                continue
            open_event = candidates[-1]
            used_open_ids.add(id(open_event))
            trades.append({"open": open_event, "close": event})
    orphan_opens = [event for rows in opens_by_product.values() for event in rows if id(event) not in used_open_ids]
    return trades, orphan_opens + unmatched_closes


def trade_row(pair: dict[str, Any], args: argparse.Namespace) -> dict[str, Any]:
    open_event = pair.get("open") or {}
    close_event = pair.get("close") or {}
    entry = open_event.get("row") if isinstance(open_event.get("row"), dict) else {}
    open_at = parse_time(open_event.get("at"))
    close_at = parse_time(close_event.get("at"))
    duration_seconds = (close_at - open_at).total_seconds() if open_at and close_at else 0.0
    blockers = guard_blockers(entry, args)
    net_pnl = to_float(close_event.get("net_pnl"))
    return {
        "product_id": close_event.get("product_id") or open_event.get("product_id") or "",
        "open_at": open_event.get("at") or "",
        "close_at": close_event.get("at") or "",
        "duration_seconds": round(duration_seconds, 3),
        "close_reason": close_event.get("reason") or "",
        "net_pnl": round(net_pnl, 6),
        "net_pct_on_cost": round(to_float(close_event.get("net_pct_on_cost")), 6),
        "entry_verdict": entry.get("verdict") or "",
        "entry_signal_state": entry.get("signal_state") or "",
        "entry_best_window": entry.get("best_move_window") or "",
        "entry_best_move_bps": round(to_float(entry.get("best_move_bps")), 6),
        "entry_spread_bps": round(to_float(entry.get("spread_bps")), 4),
        "entry_kraken_edge_bps": round(to_float(entry.get("kraken_edge_bps")), 6),
        "entry_coinbase_edge_bps": round(to_float(entry.get("coinbase_edge_bps")), 6),
        "entry_kraken_net_usd_on_deploy": round(to_float(entry.get("kraken_net_usd_on_deploy")), 6),
        "new_guard_would_allow": not blockers,
        "new_guard_blockers": ",".join(blockers),
    }


def summarize(rows: list[dict[str, Any]], anomalies: list[dict[str, Any]]) -> dict[str, Any]:
    blocked = [row for row in rows if not row.get("new_guard_would_allow")]
    allowed = [row for row in rows if row.get("new_guard_would_allow")]
    by_blocker: dict[str, int] = defaultdict(int)
    for row in blocked:
        for blocker in str(row.get("new_guard_blockers") or "").split(","):
            if blocker:
                by_blocker[blocker] += 1
    by_reason: dict[str, dict[str, Any]] = {}
    for row in rows:
        reason = str(row.get("close_reason") or "unknown")
        bucket = by_reason.setdefault(reason, {"closes": 0, "net_pnl": 0.0})
        bucket["closes"] += 1
        bucket["net_pnl"] += to_float(row.get("net_pnl"))
    for bucket in by_reason.values():
        bucket["net_pnl"] = round(bucket["net_pnl"], 6)
    return {
        "closed_trades": len(rows),
        "total_net_pnl": round(sum(to_float(row.get("net_pnl")) for row in rows), 6),
        "avg_net_pnl": round(sum(to_float(row.get("net_pnl")) for row in rows) / len(rows), 6) if rows else 0.0,
        "winning_closes": sum(1 for row in rows if to_float(row.get("net_pnl")) > 0),
        "new_guard_would_block": len(blocked),
        "new_guard_would_allow": len(allowed),
        "blocked_net_pnl": round(sum(to_float(row.get("net_pnl")) for row in blocked), 6),
        "allowed_net_pnl": round(sum(to_float(row.get("net_pnl")) for row in allowed), 6),
        "orphan_or_unmatched_events": len(anomalies),
        "blocker_counts": dict(sorted(by_blocker.items())),
        "close_reason_breakdown": by_reason,
    }


def build(args: argparse.Namespace) -> dict[str, Any]:
    events = load_events(Path(str(args.events_path)))
    pairs, anomalies = pair_trades(events)
    rows = [trade_row(pair, args) for pair in pairs]
    payload = {
        "generated_at": utc_now_iso(),
        "mode": "kraken_spot_velocity_loss_autopsy",
        "shadow_only": True,
        "parameters": {
            "events_path": str(args.events_path),
            "min_kraken_edge_bps": float(args.min_kraken_edge_bps),
            "max_spread_bps": float(args.max_spread_bps),
            "allowed_signal_states": sorted(parse_set(args.allowed_signal_states)),
            "allowed_best_windows": sorted(parse_set(args.allowed_best_windows)),
            "required_verdicts": sorted(parse_set(args.required_verdicts)),
            "max_entry_chase_bps": float(args.max_entry_chase_bps),
        },
        "read": [
            "This is a shadow-only execution autopsy, not a live-trading verdict.",
            "Each close is paired to the latest prior open event for the same product; orphan opens are counted separately because early state-loader bugs produced duplicate open events.",
            "The new-guard columns show whether the current guarded runner defaults would have admitted the historical paper entry.",
        ],
        "summary": summarize(rows, anomalies),
        "rows": rows,
        "anomalies": anomalies,
    }
    write_reports(payload, Path(str(args.json_path)), Path(str(args.csv_path)), Path(str(args.md_path)))
    return payload


def write_reports(payload: dict[str, Any], json_path: Path, csv_path: Path, md_path: Path) -> None:
    write_json(json_path, payload)
    columns = [
        "product_id",
        "open_at",
        "close_at",
        "duration_seconds",
        "close_reason",
        "net_pnl",
        "net_pct_on_cost",
        "entry_verdict",
        "entry_signal_state",
        "entry_best_window",
        "entry_best_move_bps",
        "entry_spread_bps",
        "entry_kraken_edge_bps",
        "entry_coinbase_edge_bps",
        "new_guard_would_allow",
        "new_guard_blockers",
    ]
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    with csv_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns)
        writer.writeheader()
        for row in payload.get("rows") or []:
            writer.writerow({column: row.get(column, "") for column in columns})
    summary = payload.get("summary") or {}
    lines = [
        "# Kraken Spot Velocity Loss Autopsy",
        "",
        f"- Generated: `{payload.get('generated_at')}`",
        f"- Shadow only: `{payload.get('shadow_only')}`",
        "",
        "## Read",
        "",
    ]
    lines.extend([f"- {item}" for item in payload.get("read") or []])
    lines.extend(
        [
            "",
            "## Summary",
            "",
            f"- Closed trades: `{summary.get('closed_trades')}`",
            f"- Total net PnL: `${to_float(summary.get('total_net_pnl')):.4f}`",
            f"- Winning closes: `{summary.get('winning_closes')}`",
            f"- New guard would block: `{summary.get('new_guard_would_block')}` closes, net `${to_float(summary.get('blocked_net_pnl')):.4f}`",
            f"- New guard would allow: `{summary.get('new_guard_would_allow')}` closes, net `${to_float(summary.get('allowed_net_pnl')):.4f}`",
            f"- Orphan/unmatched events: `{summary.get('orphan_or_unmatched_events')}`",
            "",
            "## Blockers",
            "",
            "| Blocker | Count |",
            "| --- | ---: |",
        ]
    )
    blocker_counts = summary.get("blocker_counts") or {}
    if blocker_counts:
        for blocker, count in blocker_counts.items():
            lines.append(f"| {blocker} | {count} |")
    else:
        lines.append("| none | 0 |")
    lines.extend(
        [
            "",
            "## Trades",
            "",
            "| Product | Reason | Net $ | Net % | Duration s | Window | Move bps | Spread | Edge | New Guard | Blockers |",
            "| --- | --- | ---: | ---: | ---: | --- | ---: | ---: | ---: | --- | --- |",
        ]
    )
    for row in payload.get("rows") or []:
        lines.append(
            "| {product_id} | {close_reason} | {net_pnl:.4f} | {net_pct_on_cost:.4f} | {duration_seconds:.1f} | {entry_best_window} | {entry_best_move_bps:.4f} | {entry_spread_bps:.2f} | {entry_kraken_edge_bps:.4f} | {new_guard_would_allow} | {new_guard_blockers} |".format(
                **row
            )
        )
    md_path.parent.mkdir(parents=True, exist_ok=True)
    md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    payload = build(parse_args())
    print(
        json.dumps(
            {
                "json_path": str(DEFAULT_JSON_PATH.resolve()),
                "md_path": str(DEFAULT_MD_PATH.resolve()),
                "closed_trades": payload["summary"]["closed_trades"],
                "total_net_pnl": payload["summary"]["total_net_pnl"],
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
