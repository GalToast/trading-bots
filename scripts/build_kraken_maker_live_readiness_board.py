#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent.parent
REPORTS = ROOT / "reports"
DEFAULT_NEXT_PROOF_PATH = REPORTS / "kraken_maker_next_proof_board.json"
DEFAULT_RADAR_PATH = REPORTS / "kraken_spot_live_radar.json"
DEFAULT_EVENTS_PATH = REPORTS / "kraken_spot_maker_machinegun_parallel_ratio50_taker_guard_live_exec_ab_events.jsonl"
DEFAULT_JSON_PATH = REPORTS / "kraken_maker_live_readiness_board.json"
DEFAULT_MD_PATH = REPORTS / "kraken_maker_live_readiness_board.md"
DEFAULT_LIVE_FILL_TELEMETRY_PATH = REPORTS / "kraken_live_fill_telemetry_board.json"
LANE_EVENTS_PATHS = {
    "parallel_ratio50_taker_guard": REPORTS
    / "kraken_spot_maker_machinegun_parallel_ratio50_taker_guard_ab_events.jsonl",
    "parallel_ratio50_taker_guard_live_exec": REPORTS
    / "kraken_spot_maker_machinegun_parallel_ratio50_taker_guard_live_exec_ab_events.jsonl",
    "parallel_ratio50_taker_guard_live_exec_fast_cooldown": REPORTS
    / "kraken_spot_maker_machinegun_parallel_ratio50_taker_guard_live_exec_fast_cooldown_ab_events.jsonl",
    "parallel_ratio50_taker_guard_live_exec_dds25": REPORTS
    / "kraken_spot_maker_machinegun_parallel_ratio50_taker_guard_live_exec_dds25_ab_events.jsonl",
    "parallel_ratio50_taker_guard_live_exec_dds25_fixed": REPORTS
    / "kraken_spot_maker_machinegun_parallel_ratio50_taker_guard_live_exec_dds25_fixed_ab_events.jsonl",
}


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def load_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(row, dict):
            rows.append(row)
    return rows


def to_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def to_int(value: Any, default: int = 0) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return default


def product_rows(radar: dict[str, Any]) -> dict[str, dict[str, Any]]:
    rows = radar.get("rows") if isinstance(radar.get("rows"), list) else []
    out: dict[str, dict[str, Any]] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        pid = str(row.get("product_id") or "").upper()
        if pid:
            out[pid] = row
    return out


def extract_primary(next_proof: dict[str, Any]) -> dict[str, Any]:
    summary = next_proof.get("summary") if isinstance(next_proof.get("summary"), dict) else {}
    primary_lane = str(summary.get("primary_lane") or "")
    primary_payload = next_proof.get(primary_lane) if isinstance(next_proof.get(primary_lane), dict) else {}
    return {
        "lane": primary_lane,
        "status": str(summary.get("primary_status") or primary_payload.get("status") or ""),
        "next_action": str(summary.get("next_action") or primary_payload.get("next_action") or ""),
        "closes": to_int(primary_payload.get("closes")),
        "losses": to_int(primary_payload.get("losses")),
        "ghost_marks": to_int(primary_payload.get("ghost_marks")),
        "open_positions": to_int(primary_payload.get("open_positions")),
        "max_concurrent_positions": to_int(primary_payload.get("max_concurrent_positions")),
        "realized_net_usd": round(to_float(primary_payload.get("realized_net_usd")), 6),
        "closes_remaining": to_int(primary_payload.get("closes_remaining")),
        "ghost_marks_remaining": to_int(primary_payload.get("ghost_marks_remaining")),
        "read": str(summary.get("read") or ""),
    }


def default_events_path_for_primary(primary_lane: str, requested_path: Path) -> Path:
    if requested_path != DEFAULT_EVENTS_PATH:
        return requested_path
    return LANE_EVENTS_PATHS.get(primary_lane, requested_path)


def summarize_events(events: list[dict[str, Any]]) -> dict[str, Any]:
    opens = [row for row in events if row.get("action") == "open_maker_shadow"]
    closes = [row for row in events if row.get("action") == "close_maker_shadow"]
    exit_misses = [row for row in events if row.get("action") == "maker_exit_miss"]
    maker_exit_dependent_wins = [
        row
        for row in closes
        if to_float(row.get("net")) > 0.0 and row.get("maker_exit_dependent") is True
    ]
    products = sorted({str(row.get("product_id") or "") for row in opens + closes if row.get("product_id")})
    closes_by_product: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in closes:
        closes_by_product[str(row.get("product_id") or "")].append(row)
    close_spreads = [to_float(row.get("spread_bps")) for row in closes if to_float(row.get("spread_bps")) > 0.0]
    exit_fee_counter = Counter(str(row.get("exit_fee_bps") or "") for row in closes)
    quote_sizes = sorted({round(to_float(row.get("quote_usd")), 6) for row in opens if to_float(row.get("quote_usd")) > 0.0})
    return {
        "opens": len(opens),
        "closes": len(closes),
        "losses": sum(1 for row in closes if to_float(row.get("net")) <= 0.0),
        "products": products,
        "quote_usd_values": quote_sizes,
        "quote_usd_min": quote_sizes[0] if quote_sizes else 0.0,
        "quote_usd_max": quote_sizes[-1] if quote_sizes else 0.0,
        "mixed_quote_sizes": len(quote_sizes) > 1,
        "maker_exit_misses": len(exit_misses),
        "maker_exit_dependent_wins": len(maker_exit_dependent_wins),
        "maker_exit_dependent_products": sorted(
            {str(row.get("product_id") or "") for row in maker_exit_dependent_wins if row.get("product_id")}
        ),
        "taker_exits": sum(1 for row in closes if to_float(row.get("exit_fee_bps")) >= 40.0),
        "maker_exits": sum(1 for row in closes if to_float(row.get("exit_fee_bps")) < 40.0),
        "exit_fee_bps_counts": dict(exit_fee_counter),
        "min_close_spread_bps": round(min(close_spreads), 6) if close_spreads else 0.0,
        "avg_close_spread_bps": round(sum(close_spreads) / len(close_spreads), 6) if close_spreads else 0.0,
        "close_products": {
            product: {
                "closes": len(rows),
                "losses": sum(1 for row in rows if to_float(row.get("net")) <= 0.0),
                "net_usd": round(sum(to_float(row.get("net")) for row in rows), 6),
                "min_exit_spread_bps": round(
                    min([to_float(row.get("spread_bps")) for row in rows if to_float(row.get("spread_bps")) > 0.0] or [0.0]),
                    6,
                ),
            }
            for product, rows in sorted(closes_by_product.items())
        },
    }


def product_live_constraints(products: list[str], radar: dict[str, Any], *, max_quote_usd: float) -> list[dict[str, Any]]:
    by_product = product_rows(radar)
    rows: list[dict[str, Any]] = []
    for product in products:
        radar_row = by_product.get(product.upper()) or {}
        min_notional = to_float(radar_row.get("min_notional_usd"))
        can_trade = bool(radar_row.get("can_trade_starting_cash")) if radar_row else False
        rows.append(
            {
                "product_id": product,
                "radar_present": bool(radar_row),
                "rest_pair": str(radar_row.get("rest_pair") or ""),
                "min_notional_usd": round(min_notional, 6),
                "max_quote_usd": round(max_quote_usd, 6),
                "max_quote_clears_min_notional": bool(radar_row) and min_notional <= max_quote_usd,
                "can_trade_starting_cash": can_trade,
                "order_min_base": to_float(radar_row.get("order_min_base")),
                "cost_min": to_float(radar_row.get("cost_min")),
                "latest_spread_bps": round(to_float(radar_row.get("spread_bps")), 6),
                "samples": to_int(radar_row.get("samples")),
            }
        )
    return rows


def build_payload(
    *,
    next_proof_path: Path = DEFAULT_NEXT_PROOF_PATH,
    radar_path: Path = DEFAULT_RADAR_PATH,
    events_path: Path = DEFAULT_EVENTS_PATH,
    live_fill_telemetry_path: Path = DEFAULT_LIVE_FILL_TELEMETRY_PATH,
    max_quote_usd: float = 10.0,
) -> dict[str, Any]:
    next_proof = load_json(next_proof_path)
    radar = load_json(radar_path)
    primary = extract_primary(next_proof)
    effective_events_path = default_events_path_for_primary(str(primary.get("lane") or ""), Path(events_path))
    events = load_jsonl(effective_events_path)
    event_summary = summarize_events(events)
    effective_max_quote_usd = max_quote_usd
    if Path(events_path) == DEFAULT_EVENTS_PATH and to_float(event_summary.get("quote_usd_max")) > 0.0:
        effective_max_quote_usd = to_float(event_summary.get("quote_usd_max"))
    product_constraints = product_live_constraints(
        event_summary["products"], radar, max_quote_usd=effective_max_quote_usd
    )
    product_minimum_blockers = [
        row["product_id"] for row in product_constraints if not row["max_quote_clears_min_notional"]
    ]
    min_required_quote_usd = max(
        [to_float(row["min_notional_usd"]) for row in product_constraints] or [0.0]
    )
    recommended_probe_quote_usd = round(min_required_quote_usd * 1.02, 2) if min_required_quote_usd > 0.0 else 0.0

    shadow_mature = (
        primary["closes"] >= 20
        and primary["losses"] == 0
        and primary["ghost_marks"] >= 20
        and primary["open_positions"] == 0
        and primary["realized_net_usd"] > 0.0
    )
    product_minimums_clear = bool(product_constraints) and all(
        row["max_quote_clears_min_notional"] for row in product_constraints
    )
    live_fill_telemetry = load_json(live_fill_telemetry_path)
    live_fill_summary = (
        live_fill_telemetry.get("summary") if isinstance(live_fill_telemetry.get("summary"), dict) else {}
    )
    live_fill_cycles = to_int(live_fill_summary.get("complete_live_roundtrips"))
    live_fill_promotion_status = str(live_fill_summary.get("promotion_status") or "")
    live_events_present = any(str(row.get("action") or "").startswith("live_") for row in events)
    live_order_telemetry_present = live_events_present or live_fill_cycles > 0
    live_order_telemetry_promotable = bool(
        (live_fill_cycles > 0 and live_fill_promotion_status != "blocked_for_autonomous_live")
        or (live_events_present and live_fill_cycles == 0)
    )
    validate_rows = [
        row
        for row in events
        if str(row.get("action") or "") == "kraken_validate_order" and not bool(row.get("dry_run"))
    ]
    validate_success_rows = [
        row
        for row in validate_rows
        if row.get("ok") is not False and str(row.get("status") or "validated") in {"validated", ""}
    ]
    validate_success_products = sorted({str(row.get("product_id") or "").upper() for row in validate_success_rows if row.get("product_id")})
    validate_failure_rows = [row for row in validate_rows if row.get("ok") is False]
    validate_failure_products = sorted({str(row.get("product_id") or "").upper() for row in validate_failure_rows if row.get("product_id")})
    traded_products = [str(product).upper() for product in event_summary["products"]]
    validate_missing_products = sorted(
        product
        for product in traded_products
        if product not in validate_success_products and product not in validate_failure_products
    )
    validate_only_evidence_present = bool(traded_products) and not validate_failure_products and not validate_missing_products

    blockers: list[str] = []
    if not shadow_mature:
        blockers.append("shadow_maturity_not_met")
    if not product_minimums_clear:
        blockers.append(f"product_minimums_not_fully_verified_for_{effective_max_quote_usd:g}usd")
    if not validate_success_products:
        blockers.append("post_only_validate_order_not_recorded")
    elif validate_failure_products:
        blockers.append(f"post_only_validate_failed_for_{','.join(validate_failure_products)}")
    elif validate_missing_products:
        blockers.append(f"post_only_validate_missing_for_{','.join(validate_missing_products)}")
    if to_int(event_summary.get("maker_exit_dependent_wins")) > 0:
        blockers.append("maker_exit_dependency_not_resolved")
    if not live_order_telemetry_present:
        blockers.append("no_live_fill_telemetry")
    elif not live_order_telemetry_promotable:
        blockers.append("live_fill_telemetry_not_promotable")

    if not shadow_mature:
        verdict = "shadow_collect_more"
        lane = str(primary.get("lane") or "active_shadow")
        next_action = f"finish_{lane}_no_loss_gate_before_any_live_probe"
    elif not product_minimums_clear:
        verdict = "blocked_by_product_minimums"
        next_action = "refresh_kraken_radar_and_verify_order_min_costmin_for_traded_products"
    elif not validate_only_evidence_present:
        verdict = "needs_validate_only_probe"
        next_action = "run_private_validate_only_post_only_probe_after_explicit_user_approval"
    elif not live_order_telemetry_present:
        verdict = "needs_min_size_live_probe"
        next_action = "run_min_size_post_only_live_probe_after_explicit_user_approval_and_compare_to_shadow"
    elif not live_order_telemetry_promotable:
        verdict = "live_fill_telemetry_collect_more"
        next_action = "collect_more_tiny_live_fill_cycles_after_explicit_user_approval_or_repair_exit_logic_in_shadow"
    else:
        verdict = "live_probe_evidence_present"
        next_action = "compare_live_fill_slippage_fee_and_queue_metrics_against_shadow"

    return {
        "generated_at": utc_now_iso(),
        "mode": "kraken_maker_live_readiness_board",
        "parameters": {
            "next_proof_path": str(next_proof_path),
            "radar_path": str(radar_path),
            "events_path": str(effective_events_path),
            "requested_events_path": str(events_path),
            "live_fill_telemetry_path": str(live_fill_telemetry_path),
            "max_quote_usd": effective_max_quote_usd,
            "requested_max_quote_usd": max_quote_usd,
        },
        "summary": {
            "verdict": verdict,
            "next_action": next_action,
            "blockers": blockers,
            "product_minimum_blockers": product_minimum_blockers,
            "min_required_quote_usd": round(min_required_quote_usd, 6),
            "recommended_probe_quote_usd": recommended_probe_quote_usd,
            "read": (
                "This board is not live-order permission. It separates shadow edge from the missing "
                "post-only validate, fill-quality, and min-size evidence required before a tiny live probe."
            ),
        },
        "primary_shadow": primary,
        "event_summary": event_summary,
        "product_constraints": product_constraints,
        "live_evidence": {
            "shadow_mature": shadow_mature,
            "product_minimums_clear": product_minimums_clear,
            "post_only_validate_order_recorded": validate_only_evidence_present,
            "post_only_validate_order_success_count": len(validate_success_rows),
            "post_only_validate_order_failure_count": len(validate_failure_rows),
            "post_only_validate_success_products": validate_success_products,
            "post_only_validate_failure_products": validate_failure_products,
            "post_only_validate_missing_products": validate_missing_products,
            "live_order_telemetry_present": live_order_telemetry_present,
            "live_order_telemetry_complete_roundtrips": live_fill_cycles,
            "live_order_telemetry_promotion_status": live_fill_promotion_status,
            "live_order_telemetry_promotable": live_order_telemetry_promotable,
            "live_order_telemetry_blockers": live_fill_summary.get("promotion_blockers") or [],
        },
    }


def write_reports(payload: dict[str, Any], *, json_path: Path, md_path: Path) -> None:
    json_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    summary = payload["summary"]
    primary = payload["primary_shadow"]
    event_summary = payload["event_summary"]
    lines = [
        "# Kraken Maker Live Readiness Board",
        "",
        f"- Generated: `{payload['generated_at']}`",
        f"- Verdict: `{summary['verdict']}`",
        f"- Next action: `{summary['next_action']}`",
        f"- Blockers: `{summary['blockers']}`",
        f"- Product minimum blockers: `{summary['product_minimum_blockers']}`",
        f"- Minimum quote needed for traded products: `${summary['min_required_quote_usd']:.6f}`",
        f"- Recommended probe quote with 2% cushion: `${summary['recommended_probe_quote_usd']:.2f}`",
        f"- Event path: `{payload['parameters']['events_path']}`",
        f"- Read: {summary['read']}",
        "",
        "## Shadow Gate",
        "",
        "| Lane | Status | Closes | Losses | Ghosts | Open | Net $ | Closes Remaining |",
        "| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |",
        "| {lane} | {status} | {closes} | {losses} | {ghost_marks} | {open_positions} | {realized_net_usd:.6f} | {closes_remaining} |".format(
            **primary
        ),
        "",
        "## Execution Tape",
        "",
        f"- Products: `{event_summary['products']}`",
        f"- Maker exits: `{event_summary['maker_exits']}`",
        f"- Taker exits: `{event_summary['taker_exits']}`",
        f"- Maker exit misses: `{event_summary['maker_exit_misses']}`",
        f"- Maker-dependent wins: `{event_summary['maker_exit_dependent_wins']}`",
        f"- Maker-dependent products: `{event_summary['maker_exit_dependent_products']}`",
        f"- Quote sizes seen: `{event_summary['quote_usd_values']}`",
        f"- Mixed quote sizes: `{event_summary['mixed_quote_sizes']}`",
        f"- Min close spread bps: `{event_summary['min_close_spread_bps']}`",
        f"- Avg close spread bps: `{event_summary['avg_close_spread_bps']}`",
        "",
        "## Products",
        "",
        "| Product | Radar | Rest Pair | Min Notional $ | Max Quote $ | Clears Min | Latest Spread bps | Samples |",
        "| --- | --- | --- | ---: | ---: | --- | ---: | ---: |",
    ]
    for row in payload["product_constraints"]:
        lines.append(
            "| {product_id} | {radar_present} | {rest_pair} | {min_notional_usd:.6f} | {max_quote_usd:.6f} | {max_quote_clears_min_notional} | {latest_spread_bps:.4f} | {samples} |".format(
                **row
            )
        )
    lines.extend(
        [
            "",
            "## Live Evidence",
            "",
            "| Check | Present |",
            "| --- | --- |",
        ]
    )
    for key, value in payload["live_evidence"].items():
        lines.append(f"| {key} | `{value}` |")
    md_path.parent.mkdir(parents=True, exist_ok=True)
    md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build Kraken maker shadow-to-live readiness board.")
    parser.add_argument("--next-proof-path", type=Path, default=DEFAULT_NEXT_PROOF_PATH)
    parser.add_argument("--radar-path", type=Path, default=DEFAULT_RADAR_PATH)
    parser.add_argument("--events-path", type=Path, default=DEFAULT_EVENTS_PATH)
    parser.add_argument("--json-path", type=Path, default=DEFAULT_JSON_PATH)
    parser.add_argument("--md-path", type=Path, default=DEFAULT_MD_PATH)
    parser.add_argument("--live-fill-telemetry-path", type=Path, default=DEFAULT_LIVE_FILL_TELEMETRY_PATH)
    parser.add_argument("--max-quote-usd", type=float, default=10.0)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    payload = build_payload(
        next_proof_path=args.next_proof_path,
        radar_path=args.radar_path,
        events_path=args.events_path,
        live_fill_telemetry_path=args.live_fill_telemetry_path,
        max_quote_usd=float(args.max_quote_usd),
    )
    write_reports(payload, json_path=args.json_path, md_path=args.md_path)
    print(json.dumps({"summary": payload["summary"], "md_path": str(args.md_path)}, indent=2))


if __name__ == "__main__":
    main()
