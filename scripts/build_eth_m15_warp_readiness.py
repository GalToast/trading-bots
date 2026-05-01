#!/usr/bin/env python3
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent.parent
REPORTS = ROOT / "reports"
ETH_STATE_PATH = REPORTS / "penetration_lattice_shadow_ethusd_m15_warp_state.json"
SPEC_PATH = REPORTS / "eth_m15_warp_live_deployment_spec.md"
JSON_PATH = REPORTS / "eth_m15_warp_readiness.json"
MD_PATH = REPORTS / "eth_m15_warp_readiness.md"

TARGET_CLOSES = 50
MIN_REALIZED_USD = 500.0
MIN_DOLLARS_PER_CLOSE = 15.0


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def utc_now_iso() -> str:
    return utc_now().isoformat()


def load_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


def parse_iso(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None


def hours_since(value: Any) -> float | None:
    dt = parse_iso(value)
    if dt is None:
        return None
    return round(max(0.0, (utc_now() - dt).total_seconds()) / 3600.0, 2)


def format_pct(numerator: int, denominator: int) -> str:
    if denominator <= 0:
        return "-"
    return f"{(float(numerator) / float(denominator)) * 100.0:.1f}%"


def _resolve_symbol_tick_source(runner: dict[str, Any], *, symbol: str, source_key: str) -> str:
    symbol_key = str(symbol or "").upper().strip()
    bucket = runner.get(f"{source_key}_by_symbol")
    if isinstance(bucket, dict):
        bucket_entry = bucket.get(symbol_key) if symbol_key else None
        if isinstance(bucket_entry, dict):
            value = bucket_entry.get("last")
            if str(value or "").strip():
                return str(value)
    legacy_last = str(runner.get(f"{source_key}_last") or "").strip()
    if legacy_last:
        return legacy_last
    legacy = str(runner.get(source_key) or "").strip()
    return legacy


def load_eth_snapshot(path: Path = ETH_STATE_PATH) -> dict[str, Any]:
    payload = load_json(path)
    runner = payload.get("runner") if isinstance(payload.get("runner"), dict) else {}
    metadata = payload.get("metadata") if isinstance(payload.get("metadata"), dict) else {}
    symbols = payload.get("symbols") if isinstance(payload.get("symbols"), dict) else {}
    eth = symbols.get("ETHUSD") if isinstance(symbols, dict) else {}
    open_tickets = eth.get("open_tickets") if isinstance(eth.get("open_tickets"), list) else []
    realized_closes = int(eth.get("realized_closes") or 0)
    realized_net_usd = float(eth.get("realized_net_usd") or 0.0)
    dollars_per_close = (realized_net_usd / realized_closes) if realized_closes > 0 else 0.0
    return {
        "state_exists": path.exists(),
        "lane_name": "shadow_ethusd_m15_warp",
        "candidate": "ETH M15 Warp",
        "symbol": "ETHUSD",
        "heartbeat_at": str(runner.get("heartbeat_at") or payload.get("updated_at") or ""),
        "runtime_age_hours": hours_since(runner.get("started_at")),
        "shared_price_max_age_ms": int(metadata.get("shared_price_max_age_ms") or 0),
        "tick_history_source": _resolve_symbol_tick_source(
            runner,
            symbol="ETHUSD",
            source_key="tick_history_source",
        ),
        "latest_tick_source": _resolve_symbol_tick_source(
            runner,
            symbol="ETHUSD",
            source_key="latest_tick_source",
        ),
        "step": float(metadata.get("step") or eth.get("base_step_px") or 0.0),
        "realized_closes": realized_closes,
        "realized_net_usd": realized_net_usd,
        "dollars_per_close": round(dollars_per_close, 2),
        "anchor_resets": int(eth.get("anchor_resets") or 0),
        "open_count": len(open_tickets),
        "max_open_seen": int(eth.get("max_open_total") or 0),
        "raw_close_alpha": float(eth.get("raw_close_alpha") or metadata.get("raw_close_alpha") or 0.0),
        "raw_close_style": str(eth.get("raw_close_style") or ""),
        "momentum_gate": bool(eth.get("momentum_gate") or metadata.get("raw_rearm_momentum_gate")),
    }


def classify_row(snapshot: dict[str, Any]) -> dict[str, Any]:
    closes = int(snapshot.get("realized_closes") or 0)
    net_usd = float(snapshot.get("realized_net_usd") or 0.0)
    dollars_per_close = float(snapshot.get("dollars_per_close") or 0.0)
    anchor_resets = int(snapshot.get("anchor_resets") or 0)
    heartbeat_at = str(snapshot.get("heartbeat_at") or "")
    gate_fail_reasons: list[str] = []

    lane_status = "running" if heartbeat_at else "missing_state"
    readiness = "shadow_collecting"
    gate_status = "await_first_close"
    next_gate = "first_realized_close"

    if not snapshot.get("state_exists"):
        readiness = "missing_state"
        gate_status = "state_missing"
        next_gate = "restore_shadow_lane"
    elif closes <= 0:
        readiness = "seeded_flat"
        gate_status = "await_first_close"
        next_gate = "first_realized_close"
    elif closes >= TARGET_CLOSES:
        if net_usd < MIN_REALIZED_USD:
            gate_fail_reasons.append("realized_below_bar")
        if dollars_per_close < MIN_DOLLARS_PER_CLOSE:
            gate_fail_reasons.append("dollars_per_close_below_bar")
        if anchor_resets > 0:
            gate_fail_reasons.append("anchor_resets_present")
        if not gate_fail_reasons:
            readiness = "live_review_ready"
            gate_status = "shadow_gate_cleared"
            next_gate = "manual_live_review"
        else:
            readiness = "shadow_gate_failed"
            gate_status = "shadow_gate_failed"
            next_gate = "pause_and_recover_before_new_shadow"
    else:
        readiness = "shadow_collecting"
        gate_status = "collecting_to_50_close_gate"
        next_gate = "reach_50_closes_positive_reset_free"

    progress_label = f"{closes}/{TARGET_CLOSES} shadow closes"
    operator_posture_parts = [
        f"open={int(snapshot.get('open_count') or 0)}",
        f"max_open_seen={int(snapshot.get('max_open_seen') or 0)}",
        f"alpha={float(snapshot.get('raw_close_alpha') or 0.0):.1f}",
        f"close_style={str(snapshot.get('raw_close_style') or '-')}",
        "shared_history=yes" if int(snapshot.get("shared_price_max_age_ms") or 0) > 0 else "shared_history=no",
    ]
    if snapshot.get("tick_history_source"):
        operator_posture_parts.append(f"history={snapshot['tick_history_source']}")
    if snapshot.get("latest_tick_source"):
        operator_posture_parts.append(f"latest={snapshot['latest_tick_source']}")
    if snapshot.get("momentum_gate"):
        operator_posture_parts.append("momentum_gate=yes")

    evidence_parts = [
        f"{closes} closes",
        f"${net_usd:+.2f} realized",
        f"${dollars_per_close:.2f}/close" if closes > 0 else "$0.00/close",
        f"{anchor_resets} anchor resets",
        f"{int(snapshot.get('open_count') or 0)} open",
    ]
    if snapshot.get("runtime_age_hours") is not None:
        evidence_parts.append(f"{float(snapshot['runtime_age_hours']):.2f}h runtime")

    return {
        **snapshot,
        "lane_status": lane_status,
        "readiness": readiness,
        "gate_status": gate_status,
        "progress_label": progress_label,
        "progress_pct": format_pct(closes, TARGET_CLOSES),
        "next_gate": next_gate,
        "gate_fail_reasons": gate_fail_reasons,
        "operator_posture": ", ".join(operator_posture_parts),
        "evidence": " / ".join(evidence_parts),
    }


def build_payload() -> dict[str, Any]:
    row = classify_row(load_eth_snapshot())
    closes = int(row.get("realized_closes") or 0)
    net_usd = float(row.get("realized_net_usd") or 0.0)
    dollars_per_close = float(row.get("dollars_per_close") or 0.0)
    resets = int(row.get("anchor_resets") or 0)

    current_read = [
        (
            f"ETH M15 Warp remains shadow-only: {row['progress_label']} against the current live-review gate, "
            f"with ${net_usd:+.2f} realized, ${dollars_per_close:.2f}/close, {resets} anchor resets, "
            f"and {int(row.get('open_count') or 0)} open tickets."
        )
    ]
    if closes >= 20 and net_usd >= MIN_REALIZED_USD and dollars_per_close >= MIN_DOLLARS_PER_CLOSE and resets == 0:
        current_read.append(
            "The economic thresholds are already clear; close count is the active blocker until the lane reaches 50 shadow closes."
        )
    if row.get("readiness") == "live_review_ready":
        current_read.append(
            "The explicit shadow gate is now clear; use the deployment spec for a deliberate manual live review rather than treating this as auto-promoted."
        )
    elif row.get("readiness") == "shadow_gate_failed":
        current_read.append(
            "The lane has already cleared the close-count gate but failed the economic/reset contract; pause graduation and treat the deployment spec as a recovery-only reference, not as live-launch authorization."
        )
    else:
        current_read.append(
            "Use this board for the compact ETH answer and keep `reports/eth_m15_warp_live_deployment_spec.md` as the detailed manual launch spec."
        )

    active_blocker = ""
    if closes < TARGET_CLOSES:
        active_blocker = "close_count"
    elif row.get("readiness") == "shadow_gate_failed":
        active_blocker = ",".join(list(row.get("gate_fail_reasons") or []))

    return {
        "generated_at": utc_now_iso(),
        "promotion_bar": (
            "Require >=50 shadow closes, >=$500 realized, $/close >= $15, and 0 anchor resets before any manual ETH live-review argument."
        ),
        "watch_lead": {
            "candidate": str(row.get("candidate") or ""),
            "lane_name": str(row.get("lane_name") or ""),
            "readiness": str(row.get("readiness") or ""),
            "progress_label": str(row.get("progress_label") or ""),
            "progress_pct": str(row.get("progress_pct") or ""),
            "next_gate": str(row.get("next_gate") or ""),
        },
        "summary": {
            "ready_for_live_review": str(row.get("readiness") or "") == "live_review_ready",
            "active_blocker": active_blocker,
            "spec_path": str(SPEC_PATH.relative_to(ROOT)),
        },
        "current_read": current_read,
        "rows": [row],
    }


def write_reports(payload: dict[str, Any], *, json_path: Path = JSON_PATH, md_path: Path = MD_PATH) -> None:
    json_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")

    lines = [
        "# ETH M15 Warp Readiness",
        "",
        f"- Generated at: `{payload['generated_at']}`",
        f"- Promotion bar: {payload['promotion_bar']}",
        f"- Detailed spec: `{payload['summary']['spec_path']}`",
        "",
        "## Current Read",
        "",
    ]
    for line in payload.get("current_read") or []:
        lines.append(f"- {line}")
    lines.extend(
        [
            "",
            "## Rows",
            "",
            "| Lane | Candidate | Lane Status | Readiness | Gate Status | Progress | Next Gate | Realized $ | $/Close | Anchor Resets | Open | Max Open Seen | Runtime Age Hrs | Operator Posture | Evidence |",
            "| --- | --- | --- | --- | --- | --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | --- | --- |",
        ]
    )
    for row in payload.get("rows") or []:
        runtime_age = "-" if row.get("runtime_age_hours") is None else f"{float(row['runtime_age_hours']):.2f}"
        lines.append(
            f"| {row['lane_name']} | {row['candidate']} | {row['lane_status']} | {row['readiness']} | {row['gate_status']} | "
            f"{row['progress_label']} ({row['progress_pct']}) | {row['next_gate']} | "
            f"{float(row['realized_net_usd']):+.2f} | {float(row['dollars_per_close']):.2f} | {int(row['anchor_resets'])} | "
            f"{int(row['open_count'])} | {int(row['max_open_seen'])} | {runtime_age} | {row['operator_posture']} | {row['evidence']} |"
        )
    md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    payload = build_payload()
    write_reports(payload)
    print(json.dumps(payload, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
