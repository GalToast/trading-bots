#!/usr/bin/env python3
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent.parent
REPORTS = ROOT / "reports"
ETH_BOARD_JSON = REPORTS / "eth_atr_runtime_status_board.json"
SHAPESHIFTER_BOARD_JSON = REPORTS / "structure_shapeshifter_proof_board.json"
COVERAGE_BOARD_JSON = REPORTS / "lattice_phase1_event_coverage_board.json"
OUTPUT_JSON = REPORTS / "experimental_proof_watch_board.json"
OUTPUT_MD = REPORTS / "experimental_proof_watch_board.md"


def load_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


def parse_iso(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except Exception:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def age_seconds_from_iso(value: Any, *, now: datetime) -> float | None:
    parsed = parse_iso(value)
    if parsed is None:
        return None
    return max(0.0, (now - parsed).total_seconds())


def build_payload(
    *,
    now: datetime | None = None,
    eth_payload: dict[str, Any] | None = None,
    shapeshifter_payload: dict[str, Any] | None = None,
    coverage_payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    now = now or datetime.now(timezone.utc)
    eth_payload = eth_payload if eth_payload is not None else load_json(ETH_BOARD_JSON)
    shapeshifter_payload = shapeshifter_payload if shapeshifter_payload is not None else load_json(SHAPESHIFTER_BOARD_JSON)
    coverage_payload = coverage_payload if coverage_payload is not None else load_json(COVERAGE_BOARD_JSON)

    eth_rows = list(eth_payload.get("active_rows") or []) if isinstance(eth_payload.get("active_rows"), list) else []
    eth_total_closes = sum(int(row.get("realized_closes", 0) or 0) for row in eth_rows)
    eth_total_opens = sum(int(row.get("open_count", 0) or 0) for row in eth_rows)
    eth_total_net = round(sum(float(row.get("realized_net_usd", 0.0) or 0.0) for row in eth_rows), 2)
    eth_healthy_count = sum(1 for row in eth_rows if str(row.get("watchdog_status") or "") == "ok")
    eth_latest_heartbeat_age = min(
        (
            age_seconds_from_iso(row.get("runner_heartbeat_at"), now=now)
            for row in eth_rows
            if age_seconds_from_iso(row.get("runner_heartbeat_at"), now=now) is not None
        ),
        default=None,
    )

    runner = shapeshifter_payload.get("runner") if isinstance(shapeshifter_payload.get("runner"), dict) else {}
    events = shapeshifter_payload.get("events") if isinstance(shapeshifter_payload.get("events"), dict) else {}
    economics = shapeshifter_payload.get("economics") if isinstance(shapeshifter_payload.get("economics"), dict) else {}
    coverage_summary = coverage_payload.get("summary") if isinstance(coverage_payload.get("summary"), dict) else {}
    coverage_burst = (
        coverage_payload.get("same_tick_burst_summary")
        if isinstance(coverage_payload.get("same_tick_burst_summary"), dict)
        else {}
    )
    close_path_summary = (
        coverage_payload.get("close_path_summary")
        if isinstance(coverage_payload.get("close_path_summary"), dict)
        else {}
    )
    first_path_triage = (
        coverage_payload.get("first_path_triage")
        if isinstance(coverage_payload.get("first_path_triage"), dict)
        else {}
    )
    market_state_hypothesis = (
        coverage_payload.get("market_state_hypothesis")
        if isinstance(coverage_payload.get("market_state_hypothesis"), dict)
        else {}
    )
    rearm_timing_summary = (
        coverage_payload.get("rearm_timing_summary")
        if isinstance(coverage_payload.get("rearm_timing_summary"), dict)
        else {}
    )
    deployment_context = (
        coverage_payload.get("deployment_context")
        if isinstance(coverage_payload.get("deployment_context"), dict)
        else {}
    )
    shapeshifter_deployment_context = (
        shapeshifter_payload.get("deployment_context")
        if isinstance(shapeshifter_payload.get("deployment_context"), dict)
        else {}
    )
    proof_status = str(shapeshifter_payload.get("proof_status") or "")
    structure_flip_count_since_runner_start = int(events.get("structure_flip_count_since_runner_start", 0) or 0)
    box_adjust_count_since_runner_start = int(events.get("box_geometry_adjust_count_since_runner_start", 0) or 0)
    event_log_newer_than_reference = deployment_context.get("event_log_is_newer_than_reference_code")
    reference_code_mtime = parse_iso(deployment_context.get("reference_code_mtime"))
    eth_started_after_reference_count = 0
    if reference_code_mtime is not None:
        eth_started_after_reference_count = sum(
            1
            for row in eth_rows
            if (runner_started_at := parse_iso(row.get("runner_started_at"))) is not None
            and runner_started_at >= reference_code_mtime
        )
    eth_all_started_after_reference = bool(eth_rows) and eth_started_after_reference_count == len(eth_rows)
    shapeshifter_runner_started_after_reference = bool(
        shapeshifter_deployment_context.get("runner_started_after_reference_code", False)
    )

    if structure_flip_count_since_runner_start > 0:
        overall_status = "new_runtime_proof_available"
        next_action = "Review post-repair shapeshifter structure-flip evidence before changing any lane posture."
    elif eth_total_closes > 0:
        overall_status = "new_eth_forward_sample_available"
        next_action = "Review the first ETH ATR closes before making any more runtime changes."
    elif (
        str(coverage_payload.get("readiness") or "") == "stale_or_pre_enrichment_log"
        and event_log_newer_than_reference is False
    ):
        if shapeshifter_runner_started_after_reference and eth_all_started_after_reference:
            overall_status = "waiting_post_restart_event"
            next_action = (
                "The telemetry-bearing runners are already live. Wait for the first post-restart ETH ATR close or "
                "shapeshifter event before judging Phase 1 path quality."
            )
        else:
            overall_status = "needs_attention"
            next_action = "Shapeshifter telemetry code is newer than the watched event log. Restart or refresh the lane so a fresh enriched event window exists before judging path quality."
    elif not bool(runner.get("fresh", False)):
        overall_status = "needs_attention"
        next_action = "Shapeshifter runtime is stale; confirm supervision before treating the passive-wait state as healthy."
    elif eth_rows and eth_healthy_count < len(eth_rows):
        overall_status = "needs_attention"
        next_action = "At least one ETH ATR lane is not healthy; restore runtime integrity before reading the pack as passive-wait only."
    else:
        overall_status = "waiting_market_proof"
        next_action = "No new proof yet. Let ETH ATR and shapeshifter run until the market produces first closes or a first post-repair structure flip."

    return {
        "generated_at": now.isoformat(),
        "overall_status": overall_status,
        "next_action": next_action,
        "eth_atr": {
            "board_generated_at": str(eth_payload.get("generated_at") or ""),
            "lane_count": len(eth_rows),
            "healthy_lane_count": eth_healthy_count,
            "total_realized_closes": eth_total_closes,
            "total_open_positions": eth_total_opens,
            "total_realized_net_usd": eth_total_net,
            "latest_heartbeat_age_seconds": eth_latest_heartbeat_age,
            "post_patch_lane_count": eth_started_after_reference_count,
            "all_lanes_started_after_reference_code": eth_all_started_after_reference,
            "lanes": [
                {
                    "lane": str(row.get("lane") or ""),
                    "timeframe": str(row.get("timeframe") or ""),
                    "runner_pid": int(row.get("runner_pid", 0) or 0),
                    "watchdog_status": str(row.get("watchdog_status") or ""),
                    "realized_closes": int(row.get("realized_closes", 0) or 0),
                    "realized_net_usd": float(row.get("realized_net_usd", 0.0) or 0.0),
                    "open_count": int(row.get("open_count", 0) or 0),
                    "anchor_resets": int(row.get("anchor_resets", 0) or 0),
                    "runner_heartbeat_at": str(row.get("runner_heartbeat_at") or ""),
                    "runner_started_at": str(row.get("runner_started_at") or ""),
                }
                for row in eth_rows
            ],
        },
        "shapeshifter": {
            "board_generated_at": str(shapeshifter_payload.get("generated_at") or ""),
            "proof_status": proof_status,
            "readiness_verdict": str(shapeshifter_payload.get("readiness_verdict") or ""),
            "runner_fresh": bool(runner.get("fresh", False)),
            "runner_pid": int(runner.get("pid", 0) or 0),
            "heartbeat_age_seconds": age_seconds_from_iso(runner.get("heartbeat_at"), now=now),
            "structure_flip_count_since_runner_start": structure_flip_count_since_runner_start,
            "box_geometry_adjust_count_since_runner_start": box_adjust_count_since_runner_start,
            "realized_closes": int(economics.get("realized_closes", 0) or 0),
            "realized_net_usd": float(economics.get("realized_net_usd", 0.0) or 0.0),
            "anchor_resets": int(economics.get("anchor_resets", 0) or 0),
            "phase1_event_coverage_board_generated_at": str(coverage_payload.get("generated_at") or ""),
            "phase1_event_coverage_readiness": str(coverage_payload.get("readiness") or ""),
            "phase1_event_coverage_next_action": str(coverage_payload.get("next_action") or ""),
            "phase1_event_covered_field_count": int(coverage_summary.get("covered_field_count", 0) or 0),
            "phase1_event_field_count": int(coverage_summary.get("field_count", 0) or 0),
            "phase1_event_log_is_newer_than_reference_code": deployment_context.get(
                "event_log_is_newer_than_reference_code"
            ),
            "runner_started_after_reference_code": shapeshifter_runner_started_after_reference,
            "phase1_same_tick_burst_cluster_count_ge_2": int(coverage_burst.get("cluster_count_ge_2", 0) or 0),
            "phase1_same_tick_burst_max_open_count": int(coverage_burst.get("max_open_count", 0) or 0),
            "phase1_same_tick_burst_direction": str(
                ((coverage_burst.get("largest_cluster") or {}) if isinstance(coverage_burst.get("largest_cluster"), dict) else {}).get("direction")
                or ""
            ),
            "phase1_close_metric_event_count": int(close_path_summary.get("phase1_close_metric_event_count", 0) or 0),
            "phase1_loss_with_first_green_count": int(close_path_summary.get("loss_with_first_green_count", 0) or 0),
            "phase1_loss_without_first_green_count": int(close_path_summary.get("loss_without_first_green_count", 0) or 0),
            "phase1_avg_hold_seconds": close_path_summary.get("avg_hold_seconds"),
            "phase1_median_time_to_first_green_seconds": close_path_summary.get("median_time_to_first_green_seconds"),
            "phase1_avg_peak_pnl_before_exit": close_path_summary.get("avg_peak_pnl_before_exit"),
            "phase1_first_path_verdict": str(first_path_triage.get("verdict") or ""),
            "phase1_first_path_rationale": str(first_path_triage.get("rationale") or ""),
            "phase1_first_path_close_ts_utc": str(first_path_triage.get("first_close_ts_utc") or ""),
            "phase1_first_path_close_realized_pnl": first_path_triage.get("first_close_realized_pnl"),
            "phase1_first_path_close_ttfg_seconds": first_path_triage.get("first_close_time_to_first_green_seconds"),
            "phase1_market_state_hypothesis_verdict": str(market_state_hypothesis.get("verdict") or ""),
            "phase1_market_state_hypothesis_confidence": str(market_state_hypothesis.get("confidence") or ""),
            "phase1_market_state_hypothesis_rationale": str(market_state_hypothesis.get("rationale") or ""),
            "phase1_rearm_open_count": int(rearm_timing_summary.get("rearm_open_count", 0) or 0),
            "phase1_avg_token_age_at_fire_seconds": rearm_timing_summary.get("avg_token_age_at_fire_seconds"),
        },
    }


def render_markdown(payload: dict[str, Any]) -> str:
    eth_atr = payload.get("eth_atr") or {}
    shapeshifter = payload.get("shapeshifter") or {}
    eth_heartbeat_age = eth_atr.get("latest_heartbeat_age_seconds")
    eth_heartbeat_text = "missing" if eth_heartbeat_age is None else f"{float(eth_heartbeat_age):.1f}s"
    shape_heartbeat_age = shapeshifter.get("heartbeat_age_seconds")
    shape_heartbeat_text = "missing" if shape_heartbeat_age is None else f"{float(shape_heartbeat_age):.1f}s"
    event_log_newer = shapeshifter.get("phase1_event_log_is_newer_than_reference_code")
    if isinstance(event_log_newer, bool):
        event_log_newer_text = str(event_log_newer).lower()
    else:
        event_log_newer_text = "missing"

    lines = [
        "# Experimental Proof Watch Board",
        "",
        "> Current runtime generated board.",
        "> Use this as the compact read for passive proof experiments waiting on real market evidence, not as a promotion verdict by itself.",
        "",
        f"- generated_at: `{payload.get('generated_at', '')}`",
        f"- overall_status: `{payload.get('overall_status', '')}`",
        f"- next_action: `{payload.get('next_action', '')}`",
        "",
        "## ETH ATR Pack",
        "",
        f"- board_generated_at: `{eth_atr.get('board_generated_at', '') or 'missing'}`",
        f"- healthy_lanes: `{int(eth_atr.get('healthy_lane_count', 0) or 0)}` / `{int(eth_atr.get('lane_count', 0) or 0)}`",
        f"- total_realized_closes: `{int(eth_atr.get('total_realized_closes', 0) or 0)}`",
        f"- total_open_positions: `{int(eth_atr.get('total_open_positions', 0) or 0)}`",
        f"- total_realized_net_usd: `{float(eth_atr.get('total_realized_net_usd', 0.0) or 0.0):.2f}`",
        f"- latest_heartbeat_age: `{eth_heartbeat_text}`",
        f"- post_patch_lane_count: `{int(eth_atr.get('post_patch_lane_count', 0) or 0)}` / `{int(eth_atr.get('lane_count', 0) or 0)}`",
        "",
        "| Lane | TF | PID | Watchdog | Closes | Net USD | Opens | Resets |",
        "| --- | --- | ---: | --- | ---: | ---: | ---: | ---: |",
    ]
    for row in eth_atr.get("lanes") or []:
        lines.append(
            f"| `{row.get('lane', '')}` | `{row.get('timeframe', '')}` | `{int(row.get('runner_pid', 0) or 0)}` | `{row.get('watchdog_status', '') or '-'}` | `{int(row.get('realized_closes', 0) or 0)}` | `{float(row.get('realized_net_usd', 0.0) or 0.0):.2f}` | `{int(row.get('open_count', 0) or 0)}` | `{int(row.get('anchor_resets', 0) or 0)}` |"
        )
    if not (eth_atr.get("lanes") or []):
        lines.append("| _none_ | | | | | | | |")

    lines.extend(
        [
            "",
            "## Structure Shapeshifter",
            "",
            f"- board_generated_at: `{shapeshifter.get('board_generated_at', '') or 'missing'}`",
            f"- proof_status: `{shapeshifter.get('proof_status', '')}`",
            f"- readiness_verdict: `{shapeshifter.get('readiness_verdict', '') or 'unknown'}`",
            f"- runner_pid: `{int(shapeshifter.get('runner_pid', 0) or 0)}`",
            f"- runner_fresh: `{bool(shapeshifter.get('runner_fresh', False))}`",
            f"- heartbeat_age: `{shape_heartbeat_text}`",
            f"- structure_flip_count_since_runner_start: `{int(shapeshifter.get('structure_flip_count_since_runner_start', 0) or 0)}`",
            f"- box_geometry_adjust_count_since_runner_start: `{int(shapeshifter.get('box_geometry_adjust_count_since_runner_start', 0) or 0)}`",
            f"- realized_closes: `{int(shapeshifter.get('realized_closes', 0) or 0)}`",
            f"- realized_net_usd: `{float(shapeshifter.get('realized_net_usd', 0.0) or 0.0):.2f}`",
            f"- anchor_resets: `{int(shapeshifter.get('anchor_resets', 0) or 0)}`",
            f"- phase1_event_coverage_readiness: `{shapeshifter.get('phase1_event_coverage_readiness', '') or 'missing'}`",
            f"- phase1_event_covered_field_count: `{int(shapeshifter.get('phase1_event_covered_field_count', 0) or 0)}` / `{int(shapeshifter.get('phase1_event_field_count', 0) or 0)}`",
            f"- phase1_event_log_is_newer_than_reference_code: `{event_log_newer_text}`",
            f"- runner_started_after_reference_code: `{bool(shapeshifter.get('runner_started_after_reference_code', False))}`",
            f"- phase1_event_coverage_next_action: `{shapeshifter.get('phase1_event_coverage_next_action', '') or 'missing'}`",
            f"- phase1_same_tick_burst_cluster_count_ge_2: `{int(shapeshifter.get('phase1_same_tick_burst_cluster_count_ge_2', 0) or 0)}`",
            f"- phase1_same_tick_burst_max_open_count: `{int(shapeshifter.get('phase1_same_tick_burst_max_open_count', 0) or 0)}`",
            f"- phase1_close_metric_event_count: `{int(shapeshifter.get('phase1_close_metric_event_count', 0) or 0)}`",
            f"- phase1_loss_with_first_green_count: `{int(shapeshifter.get('phase1_loss_with_first_green_count', 0) or 0)}`",
            f"- phase1_loss_without_first_green_count: `{int(shapeshifter.get('phase1_loss_without_first_green_count', 0) or 0)}`",
            f"- phase1_avg_hold_seconds: `{shapeshifter.get('phase1_avg_hold_seconds', 'missing')}`",
            f"- phase1_median_time_to_first_green_seconds: `{shapeshifter.get('phase1_median_time_to_first_green_seconds', 'missing')}`",
            f"- phase1_avg_peak_pnl_before_exit: `{shapeshifter.get('phase1_avg_peak_pnl_before_exit', 'missing')}`",
            f"- phase1_first_path_verdict: `{shapeshifter.get('phase1_first_path_verdict', '') or 'missing'}`",
            f"- phase1_first_path_rationale: `{shapeshifter.get('phase1_first_path_rationale', '') or 'missing'}`",
            f"- phase1_first_path_close_ts_utc: `{shapeshifter.get('phase1_first_path_close_ts_utc', '') or 'missing'}`",
            f"- phase1_first_path_close_realized_pnl: `{shapeshifter.get('phase1_first_path_close_realized_pnl', 'missing')}`",
            f"- phase1_first_path_close_ttfg_seconds: `{shapeshifter.get('phase1_first_path_close_ttfg_seconds', 'missing')}`",
            f"- phase1_market_state_hypothesis_verdict: `{shapeshifter.get('phase1_market_state_hypothesis_verdict', '') or 'missing'}`",
            f"- phase1_market_state_hypothesis_confidence: `{shapeshifter.get('phase1_market_state_hypothesis_confidence', '') or 'missing'}`",
            f"- phase1_market_state_hypothesis_rationale: `{shapeshifter.get('phase1_market_state_hypothesis_rationale', '') or 'missing'}`",
            f"- phase1_rearm_open_count: `{int(shapeshifter.get('phase1_rearm_open_count', 0) or 0)}`",
            f"- phase1_avg_token_age_at_fire_seconds: `{shapeshifter.get('phase1_avg_token_age_at_fire_seconds', 'missing')}`",
            "",
            "## Interpretation",
            "",
        ]
    )

    status = str(payload.get("overall_status") or "")
    if status == "new_runtime_proof_available":
        lines.append("The shapeshifter path has produced post-repair `structure_flip` evidence in the current runner window. Treat that as freshness proof first.")
        if int(shapeshifter.get("phase1_event_covered_field_count", 0) or 0) < int(shapeshifter.get("phase1_event_field_count", 0) or 0):
            lines.append("Diagnostic caveat: the Phase 1 event-coverage board is still incomplete, so a fresh `structure_flip` does not yet make path quality or failure-cause diagnosis legible by itself.")
    elif status == "new_eth_forward_sample_available":
        lines.append("ETH ATR is no longer just idling; the next honest question is whether the first closes are good enough to justify more attention.")
    elif status == "waiting_post_restart_event":
        lines.append("The monitored ETH ATR and shapeshifter runners are already on telemetry-bearing processes, but the watched event log has not emitted a fresh enriched event yet.")
        lines.append("Treat the current zero-coverage read as a post-restart waiting window, not as another restart order or a missing-patch diagnosis.")
    elif status == "needs_attention":
        lines.append("At least one monitored proof lane is no longer in a clean passive-wait state. Fix runtime integrity before making inference-heavy experimental calls.")
        if str(shapeshifter.get("phase1_event_coverage_readiness") or "") == "stale_or_pre_enrichment_log":
            lines.append("Shapeshifter-specific caveat: the watched event log predates the telemetry-bearing code, so the next honest move is a fresh enriched runtime window rather than more board interpretation.")
    else:
        lines.append("Both monitored proof paths are healthy, but the market has not produced the next decisive evidence yet. The right move is to keep the pack running and review this board for the first closes or first post-repair `structure_flip`.")
        if int(shapeshifter.get("phase1_event_covered_field_count", 0) or 0) < int(shapeshifter.get("phase1_event_field_count", 0) or 0):
            lines.append("Current shapeshifter caveat: if a fresh adaptive event arrives while Phase 1 event coverage remains incomplete, this board can confirm aliveness/freshness, but not honest path-quality diagnosis.")
    if str(shapeshifter.get("phase1_event_coverage_readiness") or "") == "stale_or_pre_enrichment_log":
        lines.append(
            "Coverage interpretation: the inspected shapeshifter event log predates the telemetry-bearing lattice patch, so the current zero-coverage read is a deployment-freshness issue rather than missing Phase 1 code."
        )
    coverage_next_action = str(shapeshifter.get("phase1_event_coverage_next_action") or "")
    if coverage_next_action:
        lines.append(f"Coverage next action: {coverage_next_action}")
    if int(shapeshifter.get("phase1_close_metric_event_count", 0) or 0) > 0:
        lines.append(
            "Phase 1 path summary: "
            f"close_metrics={int(shapeshifter.get('phase1_close_metric_event_count', 0) or 0)}, "
            f"loss_without_green={int(shapeshifter.get('phase1_loss_without_first_green_count', 0) or 0)}, "
            f"median_ttfg={shapeshifter.get('phase1_median_time_to_first_green_seconds', 'missing')}, "
            f"avg_hold={shapeshifter.get('phase1_avg_hold_seconds', 'missing')}."
        )
    first_path_verdict = str(shapeshifter.get("phase1_first_path_verdict") or "")
    first_path_rationale = str(shapeshifter.get("phase1_first_path_rationale") or "")
    if first_path_verdict:
        lines.append(f"First-path triage: `{first_path_verdict}`.")
        if first_path_rationale:
            lines.append(f"First-path rationale: {first_path_rationale}")
    market_state_verdict = str(shapeshifter.get("phase1_market_state_hypothesis_verdict") or "")
    market_state_rationale = str(shapeshifter.get("phase1_market_state_hypothesis_rationale") or "")
    if market_state_verdict:
        lines.append(f"Market-state hypothesis: `{market_state_verdict}`.")
        if market_state_rationale:
            lines.append(f"Market-state rationale: {market_state_rationale}")
    lines.append("")
    return "\n".join(lines)


def main() -> int:
    payload = build_payload()
    OUTPUT_JSON.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    OUTPUT_MD.write_text(render_markdown(payload), encoding="utf-8")
    print(f"wrote {OUTPUT_JSON}")
    print(f"wrote {OUTPUT_MD}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
