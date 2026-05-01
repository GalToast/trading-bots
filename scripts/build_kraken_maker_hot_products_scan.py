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
DEFAULT_BOARD_PATH = REPORTS / "kraken_spot_frontier_strategy_board.json"
DEFAULT_STATE_PATH = REPORTS / "kraken_spot_maker_machinegun_cooldown_ab_state.json"
DEFAULT_EVENTS_PATH = REPORTS / "kraken_spot_maker_machinegun_cooldown_ab_events.jsonl"
DEFAULT_JSON_PATH = REPORTS / "kraken_maker_hot_products_scan.json"
DEFAULT_MD_PATH = REPORTS / "kraken_maker_hot_products_scan.md"


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def to_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def load_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


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


def state_body(payload: dict[str, Any]) -> dict[str, Any]:
    state = payload.get("state")
    return state if isinstance(state, dict) else payload


def close_stats(events: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    closes_by_product: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for event in events:
        if str(event.get("action") or "") != "close_maker_shadow":
            continue
        product_id = str(event.get("product_id") or "")
        if product_id:
            closes_by_product[product_id].append(event)

    stats: dict[str, dict[str, Any]] = {}
    for product_id, closes in closes_by_product.items():
        nets = [to_float(close.get("net")) for close in closes]
        net_pcts = [to_float(close.get("net_pct")) for close in closes]
        wins = [net for net in nets if net > 0.0]
        stats[product_id] = {
            "closes": len(closes),
            "wins": len(wins),
            "losses": len(closes) - len(wins),
            "net_usd": round(sum(nets), 6),
            "avg_net_pct": round(mean(net_pcts), 6) if net_pcts else 0.0,
            "last_close_ts": str(closes[-1].get("ts_utc") or ""),
        }
    return stats


def latest_open_rows(events: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    rows: dict[str, dict[str, Any]] = {}
    for event in events:
        if str(event.get("action") or "") != "open_maker_shadow":
            continue
        product_id = str(event.get("product_id") or "")
        if not product_id:
            continue
        rows[product_id] = {
            "product_id": product_id,
            "playbook": str(event.get("playbook") or "maker_harvest"),
            "spread_bps": to_float(event.get("board_spread_bps")),
            "mer": to_float(event.get("mer")),
            "frontier_score": 0.0,
            "rank": 0,
            "source": "recent_open_event",
            "latest_entry_live_spread_bps": to_float(event.get("live_spread_bps")),
        }
    return rows


def classify_row(
    row: dict[str, Any],
    *,
    active_products: set[str],
    reentry_blocks: dict[str, Any],
    min_spread_bps: float,
    min_mer: float,
    near_spread_bps: float,
    near_mer: float,
    extreme_spread_bps: float,
    max_extreme_mer: float,
) -> tuple[str, list[str]]:
    product_id = str(row.get("product_id") or "")
    playbook = str(row.get("playbook") or "")
    spread_bps = to_float(row.get("spread_bps"))
    mer = to_float(row.get("mer"))
    reasons: list[str] = []

    if playbook != "maker_harvest":
        return "not_maker_harvest", ["playbook_not_maker_harvest"]
    if product_id in active_products:
        return "active_position", ["active_position"]
    if int(to_float(reentry_blocks.get(product_id))) > 0:
        return "reentry_blocked", [f"reentry_blocks={int(to_float(reentry_blocks.get(product_id)))}"]

    spread_pass = spread_bps >= min_spread_bps
    mer_pass = mer >= min_mer
    if spread_pass and mer_pass:
        return "admitted_now", ["tight_gate_pass"]

    if spread_bps >= extreme_spread_bps and mer <= max_extreme_mer:
        return "spread_only_proof_candidate", ["extreme_spread_low_mer", "proof_only_until_fill_supported"]

    if not spread_pass:
        reasons.append(f"needs_spread_bps+{max(min_spread_bps - spread_bps, 0.0):.2f}")
    if not mer_pass:
        reasons.append(f"needs_mer+{max(min_mer - mer, 0.0):.2f}")
    if spread_bps >= min_spread_bps - near_spread_bps and mer >= min_mer - near_mer:
        return "near_miss", reasons
    return "rejected_by_gate", reasons


def build_payload(
    *,
    board_path: Path,
    state_path: Path,
    events_path: Path,
    min_spread_bps: float = 100.0,
    min_mer: float = 3.5,
    near_spread_bps: float = 20.0,
    near_mer: float = 2.0,
    extreme_spread_bps: float = 300.0,
    max_extreme_mer: float = 2.0,
) -> dict[str, Any]:
    board = load_json(board_path)
    state = state_body(load_json(state_path))
    events = load_events(events_path)
    stats = close_stats(events)
    latest_opens = latest_open_rows(events)
    active_products = set((state.get("active_positions") or {}).keys())
    reentry_blocks = state.get("reentry_blocks") if isinstance(state.get("reentry_blocks"), dict) else {}
    board_rows = board.get("rows") if isinstance(board.get("rows"), list) else []
    rows_by_product: dict[str, dict[str, Any]] = {}
    for row in board_rows:
        if not isinstance(row, dict):
            continue
        product_id = str(row.get("product_id") or "")
        if product_id:
            rows_by_product[product_id] = dict(row)
    for product_id, open_row in latest_opens.items():
        merged = dict(rows_by_product.get(product_id, {}))
        merged.update(open_row)
        rows_by_product[product_id] = merged
    rows = list(rows_by_product.values())

    scanned_rows: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        product_id = str(row.get("product_id") or "")
        classification, reasons = classify_row(
            row,
            active_products=active_products,
            reentry_blocks=reentry_blocks,
            min_spread_bps=min_spread_bps,
            min_mer=min_mer,
            near_spread_bps=near_spread_bps,
            near_mer=near_mer,
            extreme_spread_bps=extreme_spread_bps,
            max_extreme_mer=max_extreme_mer,
        )
        spread_bps = to_float(row.get("spread_bps"))
        mer = to_float(row.get("mer"))
        product_stats = stats.get(product_id, {})
        scanned_rows.append(
            {
                "product_id": product_id,
                "classification": classification,
                "reasons": reasons,
                "spread_bps": round(spread_bps, 6),
                "mer": round(mer, 6),
                "score": round(spread_bps * mer, 6),
                "frontier_score": round(to_float(row.get("frontier_score")), 6),
                "rank": int(to_float(row.get("rank"))),
                "playbook": str(row.get("playbook") or ""),
                "source": str(row.get("source") or ""),
                "latest_entry_live_spread_bps": round(to_float(row.get("latest_entry_live_spread_bps")), 6),
                "closes": int(to_float(product_stats.get("closes"))),
                "wins": int(to_float(product_stats.get("wins"))),
                "losses": int(to_float(product_stats.get("losses"))),
                "net_usd": round(to_float(product_stats.get("net_usd")), 6),
                "avg_net_pct": round(to_float(product_stats.get("avg_net_pct")), 6),
                "last_close_ts": str(product_stats.get("last_close_ts") or ""),
            }
        )

    order = {
        "admitted_now": 0,
        "active_position": 1,
        "reentry_blocked": 2,
        "near_miss": 3,
        "spread_only_proof_candidate": 4,
        "rejected_by_gate": 5,
        "not_maker_harvest": 6,
    }
    scanned_rows.sort(
        key=lambda item: (
            order.get(str(item.get("classification")), 99),
            to_float(item.get("score")),
            to_float(item.get("net_usd")),
        ),
        reverse=False,
    )
    buckets = Counter(str(row.get("classification") or "") for row in scanned_rows)
    admitted = [row for row in scanned_rows if row["classification"] == "admitted_now"]
    active_or_blocked = [
        row for row in scanned_rows if row["classification"] in {"active_position", "reentry_blocked"}
    ]
    near_misses = [row for row in scanned_rows if row["classification"] == "near_miss"]
    spread_only = [row for row in scanned_rows if row["classification"] == "spread_only_proof_candidate"]

    if admitted:
        bottleneck = "candidate_supply_available"
    elif active_or_blocked:
        bottleneck = "active_or_cooldown_bound"
    elif near_misses:
        bottleneck = "gate_threshold_bound"
    else:
        bottleneck = "no_tight_gate_supply"

    return {
        "generated_at": utc_now_iso(),
        "mode": "kraken_maker_hot_products_scan",
        "board_path": str(board_path),
        "state_path": str(state_path),
        "events_path": str(events_path),
        "parameters": {
            "min_spread_bps": min_spread_bps,
            "min_mer": min_mer,
            "near_spread_bps": near_spread_bps,
            "near_mer": near_mer,
            "extreme_spread_bps": extreme_spread_bps,
            "max_extreme_mer": max_extreme_mer,
        },
        "summary": {
            "rows_scanned": len(scanned_rows),
            "active_positions": len(active_products),
            "reentry_blocked_products": sum(1 for value in reentry_blocks.values() if int(to_float(value)) > 0),
            "classification_counts": dict(buckets),
            "admitted_now": [row["product_id"] for row in admitted[:10]],
            "active_or_blocked": [row["product_id"] for row in active_or_blocked[:10]],
            "near_misses": [row["product_id"] for row in near_misses[:10]],
            "spread_only_proof_candidates": [row["product_id"] for row in spread_only[:10]],
            "bottleneck": bottleneck,
            "read": (
                "Read-only runner-board scan. Admitted rows pass the tight maker gate now; "
                "spread-only rows remain proof-only until public fill support exists."
            ),
        },
        "rows": scanned_rows,
    }


def write_reports(payload: dict[str, Any], *, json_path: Path, md_path: Path) -> None:
    json_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    summary = payload.get("summary") or {}
    lines = [
        "# Kraken Maker Hot Products Scan",
        "",
        f"- Generated: `{payload.get('generated_at')}`",
        f"- Bottleneck: `{summary.get('bottleneck')}`",
        f"- Rows scanned: `{summary.get('rows_scanned')}`",
        f"- Classification counts: `{summary.get('classification_counts')}`",
        f"- Admitted now: `{summary.get('admitted_now')}`",
        f"- Active or blocked: `{summary.get('active_or_blocked')}`",
        f"- Near misses: `{summary.get('near_misses')}`",
        f"- Spread-only proof candidates: `{summary.get('spread_only_proof_candidates')}`",
        f"- Read: {summary.get('read')}",
        "",
        "## Top Rows",
        "",
        "| Product | Class | Reasons | Spread bps | MER | Score | Closes | Wins | Losses | Net $ | Avg Net % |",
        "| --- | --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for row in (payload.get("rows") or [])[:30]:
        lines.append(
            "| {product_id} | {classification} | {reasons} | {spread_bps:.2f} | {mer:.4f} | {score:.2f} | {closes} | {wins} | {losses} | {net_usd:.6f} | {avg_net_pct:.4f} |".format(
                **{**row, "reasons": ", ".join(row.get("reasons") or [])}
            )
        )
    md_path.parent.mkdir(parents=True, exist_ok=True)
    md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description="Build a read-only hot-product scan for Kraken maker lanes.")
    parser.add_argument("--board-path", type=Path, default=DEFAULT_BOARD_PATH)
    parser.add_argument("--state-path", type=Path, default=DEFAULT_STATE_PATH)
    parser.add_argument("--events-path", type=Path, default=DEFAULT_EVENTS_PATH)
    parser.add_argument("--json-path", type=Path, default=DEFAULT_JSON_PATH)
    parser.add_argument("--md-path", type=Path, default=DEFAULT_MD_PATH)
    parser.add_argument("--min-spread-bps", type=float, default=100.0)
    parser.add_argument("--min-mer", type=float, default=3.5)
    parser.add_argument("--near-spread-bps", type=float, default=20.0)
    parser.add_argument("--near-mer", type=float, default=2.0)
    parser.add_argument("--extreme-spread-bps", type=float, default=300.0)
    parser.add_argument("--max-extreme-mer", type=float, default=2.0)
    args = parser.parse_args()

    payload = build_payload(
        board_path=args.board_path,
        state_path=args.state_path,
        events_path=args.events_path,
        min_spread_bps=args.min_spread_bps,
        min_mer=args.min_mer,
        near_spread_bps=args.near_spread_bps,
        near_mer=args.near_mer,
        extreme_spread_bps=args.extreme_spread_bps,
        max_extreme_mer=args.max_extreme_mer,
    )
    write_reports(payload, json_path=args.json_path, md_path=args.md_path)
    print(json.dumps({"summary": payload["summary"], "md_path": str(args.md_path)}, indent=2))


if __name__ == "__main__":
    main()
