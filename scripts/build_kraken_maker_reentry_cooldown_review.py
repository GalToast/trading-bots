#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from statistics import mean
from typing import Any


ROOT = Path(__file__).resolve().parent.parent
REPORTS = ROOT / "reports"
DEFAULT_EVENTS_PATH = REPORTS / "kraken_spot_maker_machinegun_shadow_events.jsonl"
DEFAULT_JSON_PATH = REPORTS / "kraken_maker_reentry_cooldown_review.json"
DEFAULT_MD_PATH = REPORTS / "kraken_maker_reentry_cooldown_review.md"


WIN_REASONS = {
    "maker_rent_harvest",
    "maker_min_profit_harvest",
    "maker_green_then_red_insurance",
}


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


def seconds_between(left: Any, right: Any) -> float:
    left_dt = parse_time(left)
    right_dt = parse_time(right)
    if not left_dt or not right_dt:
        return 999999.0
    return abs((right_dt - left_dt).total_seconds())


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


def pair_blocks_to_closes(events: list[dict[str, Any]], *, max_seconds: float = 5.0) -> list[dict[str, Any]]:
    closes = [event for event in events if str(event.get("action") or "") == "close_maker_shadow"]
    paired: list[dict[str, Any]] = []
    for block in events:
        if str(block.get("action") or "") != "block_maker_reentry":
            continue
        product_id = str(block.get("product_id") or "")
        candidates = [
            close
            for close in closes
            if str(close.get("product_id") or "") == product_id
            and str(close.get("reason") or "") == str(block.get("reason") or "")
            and seconds_between(block.get("ts_utc"), close.get("ts_utc")) <= max_seconds
        ]
        close = min(candidates, key=lambda row: seconds_between(block.get("ts_utc"), row.get("ts_utc"))) if candidates else {}
        paired.append(
            {
                "product_id": product_id,
                "block_ts": block.get("ts_utc") or "",
                "cooldown_polls": int(to_float(block.get("cooldown_polls"))),
                "reason": str(block.get("reason") or ""),
                "paired_close_net": to_float(close.get("net")) if close else 0.0,
                "paired_close_net_pct": to_float(close.get("net_pct")) if close else 0.0,
                "paired_close_age_seconds": to_float(close.get("age_seconds")) if close else 0.0,
                "paired": bool(close),
                "paired_close_ts": close.get("ts_utc") or "",
            }
        )
    return paired


def product_summary(events: list[dict[str, Any]], block_pairs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    closes_by_product: dict[str, list[dict[str, Any]]] = defaultdict(list)
    opens_by_product: Counter[str] = Counter()
    misses_by_product: Counter[str] = Counter()
    for event in events:
        product_id = str(event.get("product_id") or "")
        if not product_id:
            continue
        action = str(event.get("action") or "")
        if action == "close_maker_shadow":
            closes_by_product[product_id].append(event)
        elif action == "open_maker_shadow":
            opens_by_product[product_id] += 1
        elif action == "maker_entry_miss":
            misses_by_product[product_id] += 1
    blocks_by_product: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for pair in block_pairs:
        blocks_by_product[str(pair.get("product_id") or "")].append(pair)
    rows: list[dict[str, Any]] = []
    for product_id in sorted(set(closes_by_product) | set(blocks_by_product) | set(opens_by_product)):
        closes = closes_by_product.get(product_id, [])
        blocks = blocks_by_product.get(product_id, [])
        nets = [to_float(close.get("net")) for close in closes]
        net_pcts = [to_float(close.get("net_pct")) for close in closes]
        wins = [close for close in closes if to_float(close.get("net")) > 0]
        loss_count = len(closes) - len(wins)
        paired_winning_blocks = [
            block
            for block in blocks
            if to_float(block.get("paired_close_net")) > 0 or str(block.get("reason") or "") in WIN_REASONS
        ]
        block_reasons = Counter(str(block.get("reason") or "") for block in blocks)
        avg_block_net_pct = mean([to_float(block.get("paired_close_net_pct")) for block in paired_winning_blocks]) if paired_winning_blocks else 0.0
        rows.append(
            {
                "product_id": product_id,
                "opens": opens_by_product.get(product_id, 0),
                "closes": len(closes),
                "wins": len(wins),
                "losses": loss_count,
                "win_rate": round(len(wins) / len(closes), 6) if closes else 0.0,
                "net_usd": round(sum(nets), 6),
                "avg_net_pct": round(mean(net_pcts), 6) if net_pcts else 0.0,
                "entry_misses": misses_by_product.get(product_id, 0),
                "reentry_blocks": len(blocks),
                "paired_winning_blocks": len(paired_winning_blocks),
                "avg_paired_winning_block_net_pct": round(avg_block_net_pct, 6),
                "cooldown_polls": sorted({int(to_float(block.get("cooldown_polls"))) for block in blocks}),
                "block_reasons": dict(block_reasons),
                "cooldown_ab_candidate": (
                    len(paired_winning_blocks) >= 2
                    and len(wins) >= 2
                    and loss_count == 0
                    and avg_block_net_pct > 0.5
                ),
            }
        )
    return sorted(rows, key=lambda row: (int(row["cooldown_ab_candidate"]), to_float(row["net_usd"]), row["reentry_blocks"]), reverse=True)


def build_payload(events_path: Path) -> dict[str, Any]:
    events = load_events(events_path)
    block_pairs = pair_blocks_to_closes(events)
    rows = product_summary(events, block_pairs)
    candidates = [row for row in rows if bool(row.get("cooldown_ab_candidate"))]
    return {
        "generated_at": utc_now_iso(),
        "mode": "kraken_maker_reentry_cooldown_review",
        "events_path": str(events_path),
        "summary": {
            "events": len(events),
            "reentry_blocks": len(block_pairs),
            "paired_blocks": sum(1 for block in block_pairs if bool(block.get("paired"))),
            "cooldown_ab_candidates": [row["product_id"] for row in candidates],
            "verdict": "cooldown_ab_candidates_present" if candidates else "collect_more_or_keep_cooldown",
            "read": (
                "Candidate means repeated winning closes are immediately followed by reentry blocks. "
                "It is not permission to change the main runner without an A/B or narrow override."
            ),
        },
        "products": rows[:30],
        "recent_blocks": block_pairs[-30:],
    }


def write_reports(payload: dict[str, Any], *, json_path: Path, md_path: Path) -> None:
    json_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    summary = payload.get("summary") or {}
    lines = [
        "# Kraken Maker Reentry Cooldown Review",
        "",
        f"- Generated: `{payload.get('generated_at')}`",
        f"- Reentry blocks: `{summary.get('reentry_blocks', 0)}`",
        f"- Paired blocks: `{summary.get('paired_blocks', 0)}`",
        f"- Verdict: `{summary.get('verdict')}`",
        f"- Candidates: `{summary.get('cooldown_ab_candidates')}`",
        f"- Read: {summary.get('read')}",
        "",
        "## Products",
        "",
        "| Product | Opens | Closes | Wins | Losses | Net $ | Avg Net % | Blocks | Winning Blocks | Avg Winning Block % | Candidate | Reasons |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- | --- |",
    ]
    for row in payload.get("products") or []:
        lines.append(
            "| {product_id} | {opens} | {closes} | {wins} | {losses} | {net_usd:.6f} | {avg_net_pct:.4f} | {reentry_blocks} | {paired_winning_blocks} | {avg_paired_winning_block_net_pct:.4f} | {cooldown_ab_candidate} | {reasons} |".format(
                reasons=json.dumps(row.get("block_reasons", {}), sort_keys=True),
                **row,
            )
        )
    lines.extend(
        [
            "",
            "## Recent Blocks",
            "",
            "| Time | Product | Reason | Cooldown Polls | Paired | Close Net % |",
            "| --- | --- | --- | ---: | --- | ---: |",
        ]
    )
    for block in payload.get("recent_blocks") or []:
        lines.append(
            "| {block_ts} | {product_id} | {reason} | {cooldown_polls} | {paired} | {paired_close_net_pct:.4f} |".format(
                **block
            )
        )
    md_path.parent.mkdir(parents=True, exist_ok=True)
    md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Review Kraken maker reentry cooldown blocks.")
    parser.add_argument("--events-path", default=str(DEFAULT_EVENTS_PATH))
    parser.add_argument("--json-path", default=str(DEFAULT_JSON_PATH))
    parser.add_argument("--md-path", default=str(DEFAULT_MD_PATH))
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    payload = build_payload(Path(args.events_path))
    write_reports(payload, json_path=Path(args.json_path), md_path=Path(args.md_path))
    print(json.dumps({"summary": payload["summary"], "md_path": str(Path(args.md_path).resolve())}, indent=2))


if __name__ == "__main__":
    main()
