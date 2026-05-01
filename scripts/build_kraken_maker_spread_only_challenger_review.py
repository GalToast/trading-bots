#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from statistics import mean
from typing import Any


ROOT = Path(__file__).resolve().parent.parent
REPORTS = ROOT / "reports"
DEFAULT_EVENT_PATH = REPORTS / "kraken_maker_spread_only_challenger_tape.jsonl"
DEFAULT_JSON_PATH = REPORTS / "kraken_maker_spread_only_challenger_review.json"
DEFAULT_MD_PATH = REPORTS / "kraken_maker_spread_only_challenger_review.md"


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def to_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def load_events(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict):
            rows.append(payload)
    return rows


def summarize_horizons(events: list[dict[str, Any]]) -> dict[str, Any]:
    groups: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for event in events:
        if str(event.get("action") or "") == "spread_only_challenger_mark":
            groups[int(to_float(event.get("horizon_seconds")))].append(event)
    output: dict[str, Any] = {}
    for horizon, marks in sorted(groups.items()):
        ask = [to_float(mark.get("ask_maker_net_pct_on_cost")) for mark in marks]
        bid = [to_float(mark.get("bid_taker_net_pct_on_cost")) for mark in marks]
        ask_net = [to_float(mark.get("ask_maker_net_usd")) for mark in marks]
        bid_net = [to_float(mark.get("bid_taker_net_usd")) for mark in marks]
        fill_supported = [mark for mark in marks if bool(mark.get("fill_supported"))]
        output[str(horizon)] = {
            "marks": len(marks),
            "fill_supported_marks": len(fill_supported),
            "fill_supported_rate": round(len(fill_supported) / len(marks), 6) if marks else 0.0,
            "ask_maker_win_rate": round(sum(1 for value in ask if value > 0) / len(ask), 6) if ask else 0.0,
            "bid_taker_win_rate": round(sum(1 for value in bid if value > 0) / len(bid), 6) if bid else 0.0,
            "harvest_clear_rate": round(sum(1 for mark in marks if bool(mark.get("spread_harvest_clears"))) / len(marks), 6) if marks else 0.0,
            "avg_ask_maker_net_pct": round(mean(ask), 6) if ask else 0.0,
            "avg_bid_taker_net_pct": round(mean(bid), 6) if bid else 0.0,
            "sum_ask_maker_net_usd": round(sum(ask_net), 6),
            "sum_bid_taker_net_usd": round(sum(bid_net), 6),
        }
    return output


def summarize_products(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    opens: dict[str, int] = defaultdict(int)
    stops: dict[str, int] = defaultdict(int)
    fill_supported: dict[str, int] = defaultdict(int)
    marks: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for event in events:
        product_id = str(event.get("product_id") or "")
        if not product_id:
            continue
        action = str(event.get("action") or "")
        if action == "spread_only_challenger_open":
            opens[product_id] += 1
        elif action == "spread_only_challenger_fill_supported":
            fill_supported[product_id] += 1
        elif action == "spread_only_challenger_stop":
            stops[product_id] += 1
        elif action == "spread_only_challenger_mark":
            marks[product_id].append(event)
    rows = []
    for product_id in sorted(set(opens) | set(stops) | set(marks)):
        product_marks = marks.get(product_id, [])
        ask = [to_float(mark.get("ask_maker_net_pct_on_cost")) for mark in product_marks]
        bid = [to_float(mark.get("bid_taker_net_pct_on_cost")) for mark in product_marks]
        rows.append(
            {
                "product_id": product_id,
                "opens": opens.get(product_id, 0),
                "stops": stops.get(product_id, 0),
                "fill_supported_events": fill_supported.get(product_id, 0),
                "marks": len(product_marks),
                "fill_supported_marks": sum(1 for mark in product_marks if bool(mark.get("fill_supported"))),
                "avg_ask_maker_net_pct": round(mean(ask), 6) if ask else 0.0,
                "avg_bid_taker_net_pct": round(mean(bid), 6) if bid else 0.0,
                "harvest_clears": sum(1 for mark in product_marks if bool(mark.get("spread_harvest_clears"))),
            }
        )
    return sorted(rows, key=lambda row: (to_float(row["avg_ask_maker_net_pct"]), -to_float(row["stops"])), reverse=True)


def verdict(summary: dict[str, Any]) -> list[str]:
    opens = int(summary.get("opens", 0))
    stops = int(summary.get("stops", 0))
    fill_supported_events = int(summary.get("fill_supported_events", 0))
    horizons = summary.get("horizons") if isinstance(summary.get("horizons"), dict) else {}
    marked = sum(int(data.get("marks", 0)) for data in horizons.values() if isinstance(data, dict))
    if opens == 0:
        return ["collect_candidates_no_spread_only_entries_yet"]
    if fill_supported_events == 0:
        return ["proof_has_no_public_fill_support_yet"]
    if marked < 10:
        return ["collect_more_marks_before_promotion"]
    stop_rate = stops / opens if opens else 0.0
    best_harvest = max((to_float(data.get("harvest_clear_rate")) for data in horizons.values() if isinstance(data, dict)), default=0.0)
    best_bid = max((to_float(data.get("avg_bid_taker_net_pct")) for data in horizons.values() if isinstance(data, dict)), default=-999.0)
    if stop_rate > 0.5:
        return ["kill_or_tighten_spread_only_gate_stop_rate_high"]
    if best_harvest >= 0.7 and best_bid > -0.25:
        return ["spread_only_challenger_alive_keep_collecting"]
    return ["not_promotable_yet"]


def build_payload(event_path: Path) -> dict[str, Any]:
    events = load_events(event_path)
    opens = [event for event in events if str(event.get("action") or "") == "spread_only_challenger_open"]
    stops = [event for event in events if str(event.get("action") or "") == "spread_only_challenger_stop"]
    completes = [event for event in events if str(event.get("action") or "") == "spread_only_challenger_complete"]
    fill_events = [event for event in events if str(event.get("action") or "") == "spread_only_challenger_fill_supported"]
    fill_supported_completes = [event for event in completes if bool(event.get("fill_supported"))]
    summary = {
        "opens": len(opens),
        "stops": len(stops),
        "completes": len(completes),
        "fill_supported_events": len(fill_events),
        "fill_supported_completes": len(fill_supported_completes),
        "missed_or_unproven_completes": len(completes) - len(fill_supported_completes),
        "horizons": summarize_horizons(events),
    }
    summary["verdict"] = verdict(summary)
    return {
        "generated_at": utc_now_iso(),
        "mode": "kraken_maker_spread_only_challenger_review",
        "event_path": str(event_path),
        "shadow_only": True,
        "passive_only": True,
        "fill_model_warning": "Opens are assumed maker-bid fills; review is proof-seeking, not executable fill proof.",
        "summary": summary,
        "products": summarize_products(events)[:30],
        "recent_events": events[-30:],
    }


def write_reports(payload: dict[str, Any], *, json_path: Path, md_path: Path) -> None:
    json_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    summary = payload.get("summary") or {}
    lines = [
        "# Kraken Maker Spread-Only Challenger Review",
        "",
        f"- Generated: `{payload.get('generated_at')}`",
        f"- Shadow only: `{payload.get('shadow_only')}`",
        f"- Passive only: `{payload.get('passive_only')}`",
        f"- Fill warning: `{payload.get('fill_model_warning')}`",
        f"- Opens: `{summary.get('opens', 0)}`",
        f"- Stops: `{summary.get('stops', 0)}`",
        f"- Completes: `{summary.get('completes', 0)}`",
        f"- Fill-supported events: `{summary.get('fill_supported_events', 0)}`",
        f"- Fill-supported completes: `{summary.get('fill_supported_completes', 0)}`",
        f"- Missed/unproven completes: `{summary.get('missed_or_unproven_completes', 0)}`",
        f"- Verdict: `{summary.get('verdict')}`",
        "",
        "## Horizon Results",
        "",
        "| Horizon | Marks | Fill-Supported % | Ask/Maker Win % | Bid/Taker Win % | Harvest Clear % | Avg Ask/Maker % | Avg Bid/Taker % |",
        "| ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for horizon, stats in (summary.get("horizons") or {}).items():
        lines.append(
            "| {horizon}s | {marks} | {fill:.2%} | {ask:.2%} | {bid:.2%} | {clear:.2%} | {ask_avg:.4f} | {bid_avg:.4f} |".format(
                horizon=horizon,
                marks=stats.get("marks", 0),
                fill=to_float(stats.get("fill_supported_rate")),
                ask=to_float(stats.get("ask_maker_win_rate")),
                bid=to_float(stats.get("bid_taker_win_rate")),
                clear=to_float(stats.get("harvest_clear_rate")),
                ask_avg=to_float(stats.get("avg_ask_maker_net_pct")),
                bid_avg=to_float(stats.get("avg_bid_taker_net_pct")),
            )
        )
    lines.extend(
        [
            "",
            "## Products",
            "",
            "| Product | Opens | Stops | Fill Events | Marks | Fill Marks | Harvest Clears | Avg Ask/Maker % | Avg Bid/Taker % |",
            "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
        ]
    )
    for row in payload.get("products") or []:
        lines.append(
            "| {product_id} | {opens} | {stops} | {fill_supported_events} | {marks} | {fill_supported_marks} | {harvest_clears} | {avg_ask_maker_net_pct:.4f} | {avg_bid_taker_net_pct:.4f} |".format(
                **row
            )
        )
    md_path.parent.mkdir(parents=True, exist_ok=True)
    md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Review passive Kraken spread-only challenger tape.")
    parser.add_argument("--event-path", default=str(DEFAULT_EVENT_PATH))
    parser.add_argument("--json-path", default=str(DEFAULT_JSON_PATH))
    parser.add_argument("--md-path", default=str(DEFAULT_MD_PATH))
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    payload = build_payload(Path(args.event_path))
    write_reports(payload, json_path=Path(args.json_path), md_path=Path(args.md_path))
    print(json.dumps({"json_path": str(Path(args.json_path).resolve()), "summary": payload["summary"]}, indent=2))


if __name__ == "__main__":
    main()
