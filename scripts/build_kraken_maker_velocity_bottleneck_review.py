#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from collections import defaultdict, deque
from datetime import datetime, timezone
from pathlib import Path
from statistics import mean, median
from typing import Any


ROOT = Path(__file__).resolve().parent.parent
REPORTS = ROOT / "reports"
DEFAULT_EVENTS_PATH = REPORTS / "kraken_spot_maker_machinegun_shadow_events.jsonl"
DEFAULT_BOARD_PATH = REPORTS / "kraken_maker_opportunity_board.json"
DEFAULT_JSON_PATH = REPORTS / "kraken_maker_velocity_bottleneck_review.json"
DEFAULT_MD_PATH = REPORTS / "kraken_maker_velocity_bottleneck_review.md"


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
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def load_events(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            payload = json.loads(line)
        except Exception:
            continue
        if isinstance(payload, dict):
            rows.append(payload)
    return rows


def load_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


def pair_trades(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    opens: dict[str, deque[dict[str, Any]]] = defaultdict(deque)
    trades: list[dict[str, Any]] = []
    for event in events:
        action = str(event.get("action") or "")
        product_id = str(event.get("product_id") or "")
        if not product_id:
            continue
        if action == "open_maker_shadow":
            opens[product_id].append(event)
        elif action == "close_maker_shadow":
            open_event = opens[product_id].popleft() if opens[product_id] else {}
            trades.append(
                {
                    "product_id": product_id,
                    "open_ts": open_event.get("ts_utc") or event.get("opened_at") or "",
                    "close_ts": event.get("ts_utc") or "",
                    "mode": open_event.get("mode") or "",
                    "net": to_float(event.get("net")),
                    "net_pct": to_float(event.get("net_pct")),
                    "age_seconds": to_float(event.get("age_seconds")),
                    "reason": str(event.get("reason") or ""),
                    "entry_mer": to_float(open_event.get("mer"), to_float(event.get("entry_mer"))),
                    "entry_spread_bps": to_float(open_event.get("board_spread_bps"), to_float(event.get("spread_bps"))),
                    "live_spread_bps": to_float(open_event.get("live_spread_bps")),
                    "cost_usd": to_float(event.get("cost_usd")),
                    "quote_usd": to_float(open_event.get("quote_usd"), to_float(event.get("cost_usd"))),
                    "open_has_live_spread_guard": "live_spread_bps" in open_event,
                }
            )
    return trades


def elapsed_hours(times: list[datetime]) -> float:
    if len(times) < 2:
        return 0.0
    seconds = (max(times) - min(times)).total_seconds()
    return max(seconds / 3600.0, 0.0)


def gaps_seconds(times: list[datetime]) -> list[float]:
    ordered = sorted(times)
    return [
        (right - left).total_seconds()
        for left, right in zip(ordered, ordered[1:])
        if right >= left
    ]


def summarize_phase(name: str, trades: list[dict[str, Any]], events: list[dict[str, Any]]) -> dict[str, Any]:
    open_times = [
        parsed
        for parsed in (parse_time(trade.get("open_ts")) for trade in trades)
        if parsed is not None
    ]
    close_times = [
        parsed
        for parsed in (parse_time(trade.get("close_ts")) for trade in trades)
        if parsed is not None
    ]
    hours = elapsed_hours(open_times + close_times)
    wins = [trade for trade in trades if to_float(trade.get("net")) > 0]
    losses = [trade for trade in trades if to_float(trade.get("net")) <= 0]
    net_sum = sum(to_float(trade.get("net")) for trade in trades)
    hold_times = [to_float(trade.get("age_seconds")) for trade in trades if to_float(trade.get("age_seconds")) > 0]
    open_gaps = gaps_seconds(open_times)
    entry_attempts = [
        event
        for event in events
        if str(event.get("action") or "") in {"open_maker_shadow", "maker_entry_miss"}
    ]
    misses = [event for event in events if str(event.get("action") or "") == "maker_entry_miss"]
    return {
        "phase": name,
        "trades": len(trades),
        "wins": len(wins),
        "losses": len(losses),
        "win_rate": round(len(wins) / len(trades), 6) if trades else 0.0,
        "net_usd": round(net_sum, 6),
        "hours": round(hours, 6),
        "closes_per_hour": round(len(trades) / hours, 6) if hours > 0 else 0.0,
        "net_per_hour": round(net_sum / hours, 6) if hours > 0 else 0.0,
        "avg_hold_seconds": round(mean(hold_times), 3) if hold_times else 0.0,
        "median_hold_seconds": round(median(hold_times), 3) if hold_times else 0.0,
        "avg_open_gap_seconds": round(mean(open_gaps), 3) if open_gaps else 0.0,
        "median_open_gap_seconds": round(median(open_gaps), 3) if open_gaps else 0.0,
        "entry_attempts": len(entry_attempts),
        "entry_misses": len(misses),
        "entry_miss_rate": round(len(misses) / len(entry_attempts), 6) if entry_attempts else 0.0,
    }


def product_rows(trades: list[dict[str, Any]]) -> list[dict[str, Any]]:
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for trade in trades:
        groups[str(trade.get("product_id") or "")].append(trade)
    rows = []
    for product_id, product_trades in groups.items():
        net = sum(to_float(trade.get("net")) for trade in product_trades)
        wins = sum(1 for trade in product_trades if to_float(trade.get("net")) > 0)
        hold_times = [to_float(trade.get("age_seconds")) for trade in product_trades if to_float(trade.get("age_seconds")) > 0]
        rows.append(
            {
                "product_id": product_id,
                "trades": len(product_trades),
                "wins": wins,
                "losses": len(product_trades) - wins,
                "win_rate": round(wins / len(product_trades), 6) if product_trades else 0.0,
                "net_usd": round(net, 6),
                "avg_net_usd": round(net / len(product_trades), 6) if product_trades else 0.0,
                "avg_hold_seconds": round(mean(hold_times), 3) if hold_times else 0.0,
            }
        )
    return sorted(rows, key=lambda row: to_float(row["net_usd"]), reverse=True)


def gate_counts(board_rows: list[dict[str, Any]]) -> dict[str, Any]:
    maker_rows = [row for row in board_rows if str(row.get("playbook") or "") == "maker_harvest"]

    def passes(row: dict[str, Any], spread: float, mer: float) -> bool:
        return to_float(row.get("spread_bps")) >= spread and to_float(row.get("mer")) >= mer

    gates = {
        "tight_spread100_mer3p5": [row for row in maker_rows if passes(row, 100.0, 3.5)],
        "middle_spread75_mer2p5": [row for row in maker_rows if passes(row, 75.0, 2.5)],
        "loose_spread50_mer2p0": [row for row in maker_rows if passes(row, 50.0, 2.0)],
        "spread_only300_any_mer": [
            row
            for row in maker_rows
            if to_float(row.get("spread_bps")) >= 300.0
        ],
        "spread_only300_low_mer": [
            row
            for row in maker_rows
            if to_float(row.get("spread_bps")) >= 300.0 and to_float(row.get("mer")) < 2.0
        ],
    }
    return {
        name: {
            "count": len(rows),
            "products": [str(row.get("product_id") or "") for row in rows[:20]],
            "rows": [
                {
                    "product_id": str(row.get("product_id") or ""),
                    "spread_bps": round(to_float(row.get("spread_bps")), 6),
                    "mer": round(to_float(row.get("mer")), 6),
                }
                for row in rows[:20]
            ],
        }
        for name, rows in gates.items()
    }


def bottleneck_verdict(phase: dict[str, Any], gates: dict[str, Any]) -> list[str]:
    reasons: list[str] = []
    if gates.get("tight_spread100_mer3p5", {}).get("count", 0) <= 3:
        reasons.append("candidate_supply_tight_gate")
    if gates.get("spread_only300_low_mer", {}).get("count", 0) > 0:
        reasons.append("untested_spread_only_challenger")
    if to_float(phase.get("avg_open_gap_seconds")) > 180.0:
        reasons.append("open_gap_frequency")
    if to_float(phase.get("entry_miss_rate")) > 0.20:
        reasons.append("maker_fill_miss_rate")
    if to_float(phase.get("avg_hold_seconds")) > 300.0:
        reasons.append("hold_time_drag")
    if not reasons:
        reasons.append("scale_size_or_collect_more")
    return reasons


def build_payload(*, events_path: Path, board_path: Path) -> dict[str, Any]:
    events = load_events(events_path)
    trades = pair_trades(events)
    guarded_trades = [trade for trade in trades if bool(trade.get("open_has_live_spread_guard"))]
    board_payload = load_json(board_path)
    board_rows = [row for row in board_payload.get("rows", []) if isinstance(row, dict)]
    gates = gate_counts(board_rows)
    all_phase = summarize_phase("all", trades, events)
    guarded_events = [
        event
        for event in events
        if parse_time(event.get("ts_utc"))
        and guarded_trades
        and parse_time(event.get("ts_utc")) >= min(parse_time(trade.get("open_ts")) for trade in guarded_trades if parse_time(trade.get("open_ts")))
    ]
    guarded_phase = summarize_phase("live_spread_guarded", guarded_trades, guarded_events)
    return {
        "generated_at": utc_now_iso(),
        "mode": "kraken_maker_velocity_bottleneck_review",
        "parameters": {
            "events_path": str(events_path),
            "board_path": str(board_path),
            "phase_note": "live_spread_guarded starts with opens that include runner-side live_spread_bps telemetry.",
        },
        "summary": {
            "all": all_phase,
            "live_spread_guarded": guarded_phase,
            "current_gate_counts": gates,
            "next_bottlenecks": bottleneck_verdict(guarded_phase if guarded_trades else all_phase, gates),
        },
        "top_products_all": product_rows(trades)[:15],
        "top_products_live_spread_guarded": product_rows(guarded_trades)[:15],
    }


def write_reports(payload: dict[str, Any], *, json_path: Path, md_path: Path) -> None:
    json_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    phase = payload["summary"]["live_spread_guarded"]
    if phase["trades"] == 0:
        phase = payload["summary"]["all"]
    lines = [
        "# Kraken Maker Velocity Bottleneck Review",
        "",
        "## Summary",
        "",
        f"- Active phase: `{phase['phase']}`",
        f"- Trades: `{phase['trades']}`",
        f"- Net: `${phase['net_usd']:.6f}`",
        f"- Win rate: `{phase['win_rate']:.2%}`",
        f"- Closes/hour: `{phase['closes_per_hour']:.4f}`",
        f"- Net/hour: `${phase['net_per_hour']:.6f}`",
        f"- Avg open gap: `{phase['avg_open_gap_seconds']:.1f}s`",
        f"- Median hold: `{phase['median_hold_seconds']:.1f}s`",
        f"- Entry miss rate: `{phase['entry_miss_rate']:.2%}`",
        f"- Next bottlenecks: `{payload['summary']['next_bottlenecks']}`",
        "",
        "## Current Gate Counts",
        "",
    ]
    for gate, data in payload["summary"]["current_gate_counts"].items():
        products = ", ".join(
            f"{row['product_id']}({row['spread_bps']:.1f}bps/MER{row['mer']:.2f})"
            for row in data.get("rows", [])
        )
        lines.append(f"- `{gate}`: `{data['count']}` products - {products}")
    lines.extend(
        [
            "",
            "## Top Products - Live Spread Guarded",
            "",
            "| Product | Trades | Wins | Net $ | Avg Net $ | Avg Hold Sec |",
            "| --- | ---: | ---: | ---: | ---: | ---: |",
        ]
    )
    for row in payload["top_products_live_spread_guarded"]:
        lines.append(
            "| {product_id} | {trades} | {wins} | {net_usd:.6f} | {avg_net_usd:.6f} | {avg_hold_seconds:.1f} |".format(
                **row
            )
        )
    md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Review Kraken maker velocity and throughput bottlenecks.")
    parser.add_argument("--events-path", default=str(DEFAULT_EVENTS_PATH))
    parser.add_argument("--board-path", default=str(DEFAULT_BOARD_PATH))
    parser.add_argument("--json-path", default=str(DEFAULT_JSON_PATH))
    parser.add_argument("--md-path", default=str(DEFAULT_MD_PATH))
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    payload = build_payload(events_path=Path(args.events_path), board_path=Path(args.board_path))
    write_reports(payload, json_path=Path(args.json_path), md_path=Path(args.md_path))
    print(json.dumps({"summary": payload["summary"], "md_path": args.md_path}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
