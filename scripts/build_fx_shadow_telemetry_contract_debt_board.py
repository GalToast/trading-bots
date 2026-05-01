#!/usr/bin/env python3
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

try:
    from scripts import build_fx_shadow_telemetry_recycle_board as recycle_board
except ImportError:  # pragma: no cover - script execution path
    import build_fx_shadow_telemetry_recycle_board as recycle_board


ROOT = Path(__file__).resolve().parent.parent
REPORTS = ROOT / "reports"
REGISTRY_PATH = ROOT / "configs" / "penetration_lattice_runner_registry.json"
QUEUE_PATH = REPORTS / "fx_shadow_telemetry_recycle_board.json"
OUTPUT_JSON = REPORTS / "fx_shadow_telemetry_contract_debt_board.json"
OUTPUT_MD = REPORTS / "fx_shadow_telemetry_contract_debt_board.md"


def load_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


def registry_row_by_name(registry_payload: dict[str, Any], lane_name: str) -> dict[str, Any]:
    for row in registry_payload.get("lanes") or []:
        if str(row.get("name") or "") == lane_name:
            return dict(row)
    return {}


def arg_value(restart_args: list[Any], flag: str) -> str:
    values = [str(arg) for arg in restart_args or []]
    for idx, arg in enumerate(values):
        if arg == flag and idx + 1 < len(values):
            return values[idx + 1]
    return ""


def path_display(value: str) -> str:
    if not value:
        return ""
    try:
        return str((ROOT / value).relative_to(ROOT)).replace("\\", "/")
    except Exception:
        return str(value).replace("\\", "/")


def projected_candidate(queue_row: dict[str, Any]) -> tuple[str, int, str]:
    return recycle_board.candidate_verdict(
        status=str(queue_row.get("status") or ""),
        restart_posture=str(queue_row.get("restart_posture") or ""),
        open_inventory_count=int(queue_row.get("open_inventory_count", 0) or 0),
        activity=str(queue_row.get("activity_bucket") or ""),
    )


def build_row(queue_row: dict[str, Any], registry_row: dict[str, Any]) -> dict[str, Any]:
    restart_args = list(registry_row.get("restart_args") or [])
    projected_verdict, projected_rank, projected_rationale = projected_candidate(queue_row)
    return {
        "lane": str(queue_row.get("lane") or ""),
        "symbol": arg_value(restart_args, "--symbol"),
        "timeframe": arg_value(restart_args, "--timeframe"),
        "step": arg_value(restart_args, "--step"),
        "raw_close_alpha": arg_value(restart_args, "--raw-close-alpha"),
        "current_verdict": str(queue_row.get("candidate_verdict") or ""),
        "projected_verdict_without_fresh_start": projected_verdict,
        "projected_rank_without_fresh_start": projected_rank,
        "projected_rationale_without_fresh_start": projected_rationale,
        "open_inventory_count": int(queue_row.get("open_inventory_count", 0) or 0),
        "trade_event_count": int(queue_row.get("trade_event_count", 0) or 0),
        "activity_bucket": str(queue_row.get("activity_bucket") or ""),
        "hours_since_latest_trade": queue_row.get("hours_since_latest_trade"),
        "watchdog_groups": list(queue_row.get("watchdog_groups") or []),
        "state_path": path_display(str(queue_row.get("state_path") or registry_row.get("state_path") or "")),
        "event_path": path_display(str(queue_row.get("event_path") or registry_row.get("event_path") or "")),
        "contract_change_required": "Remove `--fresh-start` from registry `restart_args` before using this row for continuity-preserving telemetry acceleration.",
        "current_block_reason": str(queue_row.get("rationale") or ""),
    }


def build_payload(
    *,
    now: datetime | None = None,
    queue_payload: dict[str, Any] | None = None,
    registry_payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    now = now or datetime.now(timezone.utc)
    queue_payload = queue_payload if queue_payload is not None else load_json(QUEUE_PATH)
    registry_payload = registry_payload if registry_payload is not None else load_json(REGISTRY_PATH)
    queue_rows = queue_payload.get("rows") if isinstance(queue_payload.get("rows"), list) else []

    blocked_rows = [
        build_row(row, registry_row_by_name(registry_payload, str(row.get("lane") or "")))
        for row in queue_rows
        if str(row.get("candidate_verdict") or "") == "blocked_fresh_start_contract"
    ]
    blocked_rows.sort(
        key=lambda row: (
            int(row.get("projected_rank_without_fresh_start", 999) or 999),
            int(row.get("open_inventory_count", 0) or 0),
            float(row.get("hours_since_latest_trade", 9999.0) or 9999.0),
            -int(row.get("trade_event_count", 0) or 0),
            str(row.get("lane") or ""),
        )
    )

    unlockable_first_wave = [
        row for row in blocked_rows if str(row.get("projected_verdict_without_fresh_start") or "") == "recycle_first_wave"
    ]
    unlockable_second_wave = [
        row for row in blocked_rows if str(row.get("projected_verdict_without_fresh_start") or "") == "recycle_second_wave"
    ]
    current_safe_first_wave_count = int((queue_payload.get("summary") or {}).get("recycle_first_wave_count") or 0)
    projected_safe_first_wave_count = current_safe_first_wave_count + len(unlockable_first_wave)

    if blocked_rows:
        readiness = "contract_debt_actionable"
        next_action = (
            "If the room wants to widen the safe FX shadow acceleration queue, remove `--fresh-start` only from the blocked "
            f"rows after contract review. Current top unlock: {blocked_rows[0]['lane']}; projected safe first-wave count would move "
            f"from {current_safe_first_wave_count} to {projected_safe_first_wave_count}."
        )
    else:
        readiness = "contract_debt_clear"
        next_action = "No blocked FX shadow acceleration rows currently depend on `--fresh-start` contract cleanup."

    summary = {
        "blocked_lane_count": len(blocked_rows),
        "unlockable_first_wave_count": len(unlockable_first_wave),
        "unlockable_second_wave_count": len(unlockable_second_wave),
        "current_safe_first_wave_count": current_safe_first_wave_count,
        "projected_safe_first_wave_count": projected_safe_first_wave_count,
        "top_unlock_candidate": str(blocked_rows[0]["lane"]) if blocked_rows else "",
    }

    return {
        "generated_at": now.isoformat(),
        "source_queue": str(QUEUE_PATH.relative_to(ROOT)).replace("\\", "/"),
        "source_readiness": str(queue_payload.get("readiness") or ""),
        "readiness": readiness,
        "next_action": next_action,
        "summary": summary,
        "rows": blocked_rows,
        "read_rules": [
            "`projected_verdict_without_fresh_start` is the recycle-queue verdict this row would receive if the only change were removing `--fresh-start` from its registry restart contract.",
            "This board does not authorize contract edits or lane restarts; it isolates contract debt so the room can separate queue desirability from wipe-risk posture.",
            "Use this surface with `reports/fresh_start_risk_report.md` and `docs/deployment-safe-restart-protocol.md` before changing any restart contract.",
        ],
    }


def format_hours(hours: float | None) -> str:
    if hours is None:
        return "-"
    return f"{hours:.2f}"


def render_markdown(payload: dict[str, Any]) -> str:
    summary = payload.get("summary") if isinstance(payload.get("summary"), dict) else {}
    rows = payload.get("rows") if isinstance(payload.get("rows"), list) else []
    lines = [
        "# FX Shadow Telemetry Contract Debt Board",
        "",
        "> Planning/operator surface for the FX shadow rows blocked only by restart-contract wipe risk.",
        "> Use this board when the question is not which safe lane exists now, but what contract debt is suppressing additional FX telemetry acceleration options.",
        "",
        f"- generated_at: `{payload.get('generated_at', '')}`",
        f"- source_queue: `{payload.get('source_queue', '')}`",
        f"- source_readiness: `{payload.get('source_readiness', '')}`",
        f"- readiness: `{payload.get('readiness', '')}`",
        f"- next_action: `{payload.get('next_action', '')}`",
        "",
        "## Summary",
        "",
        f"- blocked_lane_count: `{int(summary.get('blocked_lane_count', 0) or 0)}`",
        f"- unlockable_first_wave_count: `{int(summary.get('unlockable_first_wave_count', 0) or 0)}`",
        f"- unlockable_second_wave_count: `{int(summary.get('unlockable_second_wave_count', 0) or 0)}`",
        f"- current_safe_first_wave_count: `{int(summary.get('current_safe_first_wave_count', 0) or 0)}`",
        f"- projected_safe_first_wave_count: `{int(summary.get('projected_safe_first_wave_count', 0) or 0)}`",
        f"- top_unlock_candidate: `{summary.get('top_unlock_candidate', '') or 'none'}`",
        "",
        "## Blocked Rows",
        "",
    ]
    if not rows:
        lines.extend(["_none_", ""])
    else:
        lines.extend(
            [
                "| Lane | Symbol | Timeframe | Step | Alpha | Current verdict | Projected verdict without fresh-start | Open inventory | Activity | Hours since trade | Trade events |",
                "| --- | --- | --- | --- | --- | --- | --- | ---: | --- | ---: | ---: |",
            ]
        )
        for row in rows:
            lines.append(
                f"| `{row.get('lane', '')}` | `{row.get('symbol', '') or '-'}` | `{row.get('timeframe', '') or '-'}` | "
                f"`{row.get('step', '') or '-'}` | `{row.get('raw_close_alpha', '') or '-'}` | "
                f"`{row.get('current_verdict', '')}` | `{row.get('projected_verdict_without_fresh_start', '')}` | "
                f"`{int(row.get('open_inventory_count', 0) or 0)}` | `{row.get('activity_bucket', '') or '-'}` | "
                f"`{format_hours(row.get('hours_since_latest_trade'))}` | `{int(row.get('trade_event_count', 0) or 0)}` |"
            )
        lines.extend(
            [
                "",
                "## Contract Debt Detail",
                "",
                "| Lane | State path | Event path | Required contract change | Why blocked now | Why it would unlock |",
                "| --- | --- | --- | --- | --- | --- |",
            ]
        )
        for row in rows:
            lines.append(
                f"| `{row.get('lane', '')}` | `{row.get('state_path', '') or '-'}` | `{row.get('event_path', '') or '-'}` | "
                f"{row.get('contract_change_required', '')} | {row.get('current_block_reason', '')} | "
                f"{row.get('projected_rationale_without_fresh_start', '')} |"
            )
        lines.append("")

    lines.extend(["## Read Rules", ""])
    for rule in list(payload.get("read_rules") or []):
        lines.append(f"- {rule}")
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
