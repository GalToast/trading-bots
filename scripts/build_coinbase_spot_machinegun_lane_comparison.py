#!/usr/bin/env python3
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent.parent
REPORTS = ROOT / "reports"
DEFAULT_STATE_PATH = REPORTS / "coinbase_spot_machinegun_shadow_state.json"
STRICT_STATE_PATH = REPORTS / "coinbase_spot_machinegun_ml_strict_shadow_state.json"
FAST_GREEN_STATE_PATH = REPORTS / "coinbase_spot_machinegun_fast_green_shadow_state.json"
BUBBLE_STATE_PATH = REPORTS / "coinbase_spot_machinegun_bubble_capture_shadow_state.json"
DEFAULT_EVENT_PATH = REPORTS / "coinbase_spot_machinegun_shadow_events.jsonl"
STRICT_EVENT_PATH = REPORTS / "coinbase_spot_machinegun_ml_strict_shadow_events.jsonl"
FAST_GREEN_EVENT_PATH = REPORTS / "coinbase_spot_machinegun_fast_green_shadow_events.jsonl"
BUBBLE_EVENT_PATH = REPORTS / "coinbase_spot_machinegun_bubble_capture_shadow_events.jsonl"
DEFAULT_TAPE_PATH = REPORTS / "coinbase_spot_machinegun_opportunity_tape.jsonl"
STRICT_TAPE_PATH = REPORTS / "coinbase_spot_machinegun_ml_strict_opportunity_tape.jsonl"
FAST_GREEN_TAPE_PATH = REPORTS / "coinbase_spot_machinegun_fast_green_opportunity_tape.jsonl"
BUBBLE_TAPE_PATH = REPORTS / "coinbase_spot_machinegun_bubble_capture_opportunity_tape.jsonl"
STRATEGY_PATH = REPORTS / "coinbase_spot_machinegun_strategy_board.json"
JSON_PATH = REPORTS / "coinbase_spot_machinegun_lane_comparison.json"
MD_PATH = REPORTS / "coinbase_spot_machinegun_lane_comparison.md"


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def load_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def to_float(value: Any) -> float:
    try:
        return float(value or 0.0)
    except (TypeError, ValueError):
        return 0.0


def age_seconds(value: Any) -> float | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return (datetime.now(timezone.utc) - parsed.astimezone(timezone.utc)).total_seconds()


def tail_jsonl(path: Path, limit: int = 8) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows = []
    for line in path.read_text(encoding="utf-8", errors="ignore").splitlines()[-limit * 3 :]:
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict):
            rows.append(payload)
    return rows[-limit:]


def iter_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows = []
    for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict):
            rows.append(payload)
    return rows


def avg(values: list[float]) -> float | None:
    return sum(values) / len(values) if values else None


def ge_rate(values: list[float], threshold: float) -> float | None:
    if not values:
        return None
    return (sum(1 for value in values if value >= threshold) / len(values)) * 100.0


def summarize_capture(event_path: Path, action: str) -> dict[str, Any]:
    closes = [row for row in iter_jsonl(event_path) if row.get("action") == action]
    gross_capture = [to_float(row.get("gross_mfe_capture_pct")) for row in closes if row.get("gross_mfe_capture_pct") is not None]
    net_capture = [to_float(row.get("net_mfe_capture_pct")) for row in closes if row.get("net_mfe_capture_pct") is not None]
    max_net_pct = [to_float(row.get("max_net_pct_on_cost")) for row in closes if row.get("max_net_pct_on_cost") is not None]
    net_pct = [to_float(row.get("net_pct_on_cost")) for row in closes if row.get("net_pct_on_cost") is not None]
    net_pnl = [to_float(row.get("net_pnl")) for row in closes if row.get("net_pnl") is not None]
    avg_net_capture = avg(net_capture)
    if avg_net_capture is None:
        verdict = "no_mfe_capture_evidence"
    elif avg_net_capture >= 20.0:
        verdict = "coinbase_capture_floor_cleared"
    elif avg_net_capture >= 10.0:
        verdict = "coinbase_capture_watch_zone"
    else:
        verdict = "capture_below_coinbase_break_even"
    return {
        "event_path": str(event_path),
        "close_count": len(closes),
        "mfe_capture_count": len(gross_capture),
        "avg_gross_mfe_capture_pct": round(avg(gross_capture) or 0.0, 6) if gross_capture else None,
        "avg_net_mfe_capture_pct": round(avg_net_capture or 0.0, 6) if avg_net_capture is not None else None,
        "gross_capture_ge_10_rate_pct": round(ge_rate(gross_capture, 10.0) or 0.0, 6) if gross_capture else None,
        "gross_capture_ge_20_rate_pct": round(ge_rate(gross_capture, 20.0) or 0.0, 6) if gross_capture else None,
        "net_capture_ge_10_rate_pct": round(ge_rate(net_capture, 10.0) or 0.0, 6) if net_capture else None,
        "net_capture_ge_20_rate_pct": round(ge_rate(net_capture, 20.0) or 0.0, 6) if net_capture else None,
        "avg_max_net_pct_on_cost": round(avg(max_net_pct) or 0.0, 6) if max_net_pct else None,
        "avg_close_net_pct_on_cost": round(avg(net_pct) or 0.0, 6) if net_pct else None,
        "total_net_pnl": round(sum(net_pnl), 6),
        "verdict": verdict,
    }


def lane_payload(name: str, state_path: Path, event_path: Path, tape_path: Path) -> dict[str, Any]:
    payload = load_json(state_path)
    runner = payload.get("runner") if isinstance(payload.get("runner"), dict) else {}
    state = payload.get("state") if isinstance(payload.get("state"), dict) else {}
    position = state.get("position") if isinstance(state.get("position"), dict) else None
    ghosts = state.get("ghost_positions") if isinstance(state.get("ghost_positions"), dict) else {}
    ghost_stats = state.get("ghost_stats") if isinstance(state.get("ghost_stats"), dict) else {}
    tape = tail_jsonl(tape_path, limit=5)
    last_scan = next((row for row in reversed(tape) if row.get("action") == "machinegun_opportunity_scan"), {})
    decision = last_scan.get("decision") if isinstance(last_scan.get("decision"), dict) else {}
    top_candidates = last_scan.get("top_candidates") if isinstance(last_scan.get("top_candidates"), list) else []
    return {
        "name": name,
        "state_path": str(state_path),
        "event_path": str(event_path),
        "tape_path": str(tape_path),
        "pid": runner.get("pid"),
        "heartbeat_at": runner.get("heartbeat_at"),
        "heartbeat_age_seconds": round(age_seconds(runner.get("heartbeat_at")) or 0.0, 3) if runner.get("heartbeat_at") else None,
        "consecutive_exceptions": int(runner.get("consecutive_exceptions") or 0),
        "last_exception_type": str(runner.get("last_exception_type") or ""),
        "shadow_only": bool(runner.get("shadow_only")),
        "require_ml_survival_prob": to_float(state.get("require_ml_survival_prob", runner.get("require_ml_survival_prob"))),
        "require_fast_green_prob": to_float(state.get("require_fast_green_prob", runner.get("require_fast_green_prob"))),
        "require_bubble_capture_net_pct_per_hour": to_float(
            state.get("require_bubble_capture_net_pct_per_hour", runner.get("require_bubble_capture_net_pct_per_hour"))
        ),
        "manifest_positive_within_seconds": to_float(
            state.get("manifest_positive_within_seconds", runner.get("manifest_positive_within_seconds"))
        ),
        "manifest_positive_min_net_pct": to_float(state.get("manifest_positive_min_net_pct", runner.get("manifest_positive_min_net_pct"))),
        "cash_usd": round(to_float(state.get("cash_usd")), 6),
        "realized_net_usd": round(to_float(state.get("realized_net_usd")), 6),
        "realized_closes": int(state.get("realized_closes") or 0),
        "total_fees": round(to_float(state.get("total_fees")), 6),
        "position_product": position.get("product_id") if position else "",
        "position_opened_at": position.get("opened_at") if position else "",
        "ghost_open_count": len(ghosts),
        "ghost_stat_count": len(ghost_stats),
        "last_action": str(state.get("last_action") or ""),
        "last_decision": decision,
        "top_candidates": top_candidates[:5],
        "position_capture": summarize_capture(event_path, "close_machinegun_shadow"),
        "ghost_capture": summarize_capture(event_path, "close_machinegun_ghost"),
    }


def build_payload() -> dict[str, Any]:
    strategy = load_json(STRATEGY_PATH)
    rows = strategy.get("rows") if isinstance(strategy.get("rows"), list) else []
    default = lane_payload("default_machinegun", DEFAULT_STATE_PATH, DEFAULT_EVENT_PATH, DEFAULT_TAPE_PATH)
    strict = lane_payload("ml_strict_manifest", STRICT_STATE_PATH, STRICT_EVENT_PATH, STRICT_TAPE_PATH)
    fast_green = lane_payload("fast_green_manifest", FAST_GREEN_STATE_PATH, FAST_GREEN_EVENT_PATH, FAST_GREEN_TAPE_PATH)
    bubble = lane_payload("bubble_capture_manifest", BUBBLE_STATE_PATH, BUBBLE_EVENT_PATH, BUBBLE_TAPE_PATH)
    strict_prob = strict["require_ml_survival_prob"]
    fast_green_prob = fast_green["require_fast_green_prob"]
    below_ml_threshold = [
        {
            "product_id": row.get("product_id"),
            "ml_survival_prob": row.get("ml_survival_prob"),
            "ml_gate_verdict": row.get("ml_gate_verdict"),
            "edge_over_hurdle_pct": row.get("edge_over_hurdle_pct"),
            "playbook": row.get("playbook"),
        }
        for row in rows
        if row.get("ml_survival_prob") is None or to_float(row.get("ml_survival_prob")) < strict_prob
    ]
    below_fast_green_threshold = [
        {
            "product_id": row.get("product_id"),
            "fast_green_prob": row.get("fast_green_prob"),
            "fast_green_verdict": row.get("fast_green_verdict"),
            "fast_green_label": row.get("fast_green_label"),
            "edge_over_hurdle_pct": row.get("edge_over_hurdle_pct"),
            "playbook": row.get("playbook"),
        }
        for row in rows
        if fast_green_prob > 0.0
        and (row.get("fast_green_prob") is None or to_float(row.get("fast_green_prob")) < fast_green_prob)
    ]
    return {
        "generated_at": utc_now_iso(),
        "mode": "coinbase_spot_machinegun_lane_comparison",
        "leadership_read": [
            "Default lane shows current shadow behavior; strict lane tests whether p>=0.98 fee-survival plus fast positive manifestation can find needles.",
            "Fast-green lane tests p>=0.95 for the fast_pay_1pct_10m spike-capture label, with separate state/tape and no live orders.",
            "Bubble-capture lane tests products with positive longer-bubble replay metadata, with separate state/tape and no live orders.",
            "Strict lane starving is acceptable until the radar/model sees a candidate in the historical positive zone.",
            "A candidate that clears raw fee hurdle but scores below the active ML thresholds should be treated as a rejected chase unless ghost proof says otherwise.",
            "MFE capture is now the primary proof metric: Coinbase needs roughly 15-20% net MFE capture for the Tail/FastGreen historical signal to survive current fees.",
        ],
        "strategy_generated_at": strategy.get("generated_at"),
        "strategy_rows": len(rows),
        "ml_threshold": strict_prob,
        "fast_green_threshold": fast_green_prob,
        "below_ml_threshold_rows": below_ml_threshold,
        "below_fast_green_threshold_rows": below_fast_green_threshold,
        "lanes": [default, strict, fast_green, bubble],
    }


def write_outputs(payload: dict[str, Any]) -> None:
    JSON_PATH.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    lines = [
        "# Coinbase Spot Machinegun Lane Comparison",
        "",
        f"- Generated: `{payload['generated_at']}`",
        f"- Strategy generated: `{payload.get('strategy_generated_at')}`",
        f"- Strategy rows: `{payload['strategy_rows']}`",
        f"- ML strict threshold: `{payload['ml_threshold']}`",
        f"- Fast-green threshold: `{payload['fast_green_threshold']}`",
        "",
        "## Read",
        "",
    ]
    lines.extend([f"- {item}" for item in payload["leadership_read"]])
    lines.extend(
        [
            "",
            "## Lanes",
            "",
            "| Lane | PID | Heartbeat Age s | ML Req | Fast Req | Bubble Req | Manifest s | Manifest Net % | Cash | Realized Net | Closes | Position | MFE Closes | Avg Net MFE Capture % | Net Capture >=20% | Capture Verdict | Ghost Open | Last Action |",
            "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- | ---: | ---: | ---: | --- | ---: | --- |",
        ]
    )
    for lane in payload["lanes"]:
        capture = lane["position_capture"]
        lines.append(
            "| {name} | {pid} | {heartbeat_age_seconds} | {require_ml_survival_prob:.4f} | {require_fast_green_prob:.4f} | {require_bubble_capture_net_pct_per_hour:.4f} | {manifest_positive_within_seconds:.0f} | {manifest_positive_min_net_pct:.4f} | {cash_usd:.4f} | {realized_net_usd:.4f} | {realized_closes} | {position_product} | {mfe_capture_count} | {avg_net_mfe_capture_pct} | {net_capture_ge_20_rate_pct} | {verdict} | {ghost_open_count} | {last_action} |".format(
                mfe_capture_count=capture["mfe_capture_count"],
                avg_net_mfe_capture_pct="" if capture["avg_net_mfe_capture_pct"] is None else f"{capture['avg_net_mfe_capture_pct']:.2f}",
                net_capture_ge_20_rate_pct="" if capture["net_capture_ge_20_rate_pct"] is None else f"{capture['net_capture_ge_20_rate_pct']:.2f}",
                verdict=capture["verdict"],
                **lane,
            )
        )
    lines.extend(["", "## MFE Capture Detail", ""])
    lines.extend(
        [
            "| Lane | Stream | Closes | MFE Closes | Avg Gross Capture % | Avg Net Capture % | Net >=10% | Net >=20% | Avg Max Net % | Avg Close Net % | Verdict |",
            "| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- |",
        ]
    )
    for lane in payload["lanes"]:
        for stream, capture in (("position", lane["position_capture"]), ("ghost", lane["ghost_capture"])):
            lines.append(
                "| {lane} | {stream} | {close_count} | {mfe_capture_count} | {avg_gross} | {avg_net} | {ge10} | {ge20} | {max_net} | {close_net} | {verdict} |".format(
                    lane=lane["name"],
                    stream=stream,
                    close_count=capture["close_count"],
                    mfe_capture_count=capture["mfe_capture_count"],
                    avg_gross="" if capture["avg_gross_mfe_capture_pct"] is None else f"{capture['avg_gross_mfe_capture_pct']:.2f}",
                    avg_net="" if capture["avg_net_mfe_capture_pct"] is None else f"{capture['avg_net_mfe_capture_pct']:.2f}",
                    ge10="" if capture["net_capture_ge_10_rate_pct"] is None else f"{capture['net_capture_ge_10_rate_pct']:.2f}",
                    ge20="" if capture["net_capture_ge_20_rate_pct"] is None else f"{capture['net_capture_ge_20_rate_pct']:.2f}",
                    max_net="" if capture["avg_max_net_pct_on_cost"] is None else f"{capture['avg_max_net_pct_on_cost']:.2f}",
                    close_net="" if capture["avg_close_net_pct_on_cost"] is None else f"{capture['avg_close_net_pct_on_cost']:.2f}",
                    verdict=capture["verdict"],
                )
            )
    lines.extend(["", "## Current Strategy ML Rejections", ""])
    lines.extend(["| Product | ML p | Verdict | Edge % | Playbook |", "| --- | ---: | --- | ---: | --- |"])
    for row in payload["below_ml_threshold_rows"][:20]:
        ml_p = row.get("ml_survival_prob")
        lines.append(
            "| {product_id} | {ml_p} | {verdict} | {edge} | {playbook} |".format(
                product_id=row.get("product_id") or "",
                ml_p="" if ml_p is None else f"{to_float(ml_p):.6f}",
                verdict=row.get("ml_gate_verdict") or "",
                edge=row.get("edge_over_hurdle_pct") or "",
                playbook=row.get("playbook") or "",
            )
        )
    lines.extend(["", "## Current Strategy Fast-Green Rejections", ""])
    lines.extend(["| Product | Fast p | Verdict | Label | Edge % | Playbook |", "| --- | ---: | --- | --- | ---: | --- |"])
    for row in payload["below_fast_green_threshold_rows"][:20]:
        fast_p = row.get("fast_green_prob")
        lines.append(
            "| {product_id} | {fast_p} | {verdict} | {label} | {edge} | {playbook} |".format(
                product_id=row.get("product_id") or "",
                fast_p="" if fast_p is None else f"{to_float(fast_p):.6f}",
                verdict=row.get("fast_green_verdict") or "",
                label=row.get("fast_green_label") or "",
                edge=row.get("edge_over_hurdle_pct") or "",
                playbook=row.get("playbook") or "",
            )
        )
    lines.extend(["", "## Top Candidates By Lane", ""])
    for lane in payload["lanes"]:
        lines.extend([f"### {lane['name']}", ""])
        top = lane.get("top_candidates") or []
        if not top:
            lines.append("- No recent candidate rows in tape.")
            lines.append("")
            continue
        for row in top:
            lines.append(
                "- `{product}` score=`{score}` edge=`{edge}` ml=`{ml}` fast=`{fast}` bubble=`{bubble}` verdict=`{verdict}` fast_verdict=`{fast_verdict}` bubble_verdict=`{bubble_verdict}`".format(
                    product=row.get("product_id"),
                    score=row.get("machinegun_score"),
                    edge=row.get("edge_over_hurdle_pct"),
                    ml=row.get("ml_survival_prob"),
                    fast=row.get("fast_green_prob"),
                    bubble=row.get("bubble_capture_net_pct_per_hour"),
                    verdict=row.get("ml_gate_verdict"),
                    fast_verdict=row.get("fast_green_verdict"),
                    bubble_verdict=row.get("bubble_capture_verdict"),
                )
            )
        lines.append("")
    MD_PATH.write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    payload = build_payload()
    write_outputs(payload)
    print(json.dumps({"json_path": str(JSON_PATH), "md_path": str(MD_PATH), "lanes": len(payload["lanes"])}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
