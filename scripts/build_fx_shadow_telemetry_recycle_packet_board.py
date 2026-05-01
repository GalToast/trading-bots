#!/usr/bin/env python3
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent.parent
REPORTS = ROOT / "reports"
REGISTRY_PATH = ROOT / "configs" / "penetration_lattice_runner_registry.json"
QUEUE_PATH = REPORTS / "fx_shadow_telemetry_recycle_board.json"
OUTPUT_JSON = REPORTS / "fx_shadow_telemetry_recycle_packet_board.json"
OUTPUT_MD = REPORTS / "fx_shadow_telemetry_recycle_packet_board.md"


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


def packet_row(queue_row: dict[str, Any], registry_row: dict[str, Any]) -> dict[str, Any]:
    restart_args = list(registry_row.get("restart_args") or [])
    return {
        "lane": str(queue_row.get("lane") or ""),
        "candidate_verdict": str(queue_row.get("candidate_verdict") or ""),
        "symbol": arg_value(restart_args, "--symbol"),
        "timeframe": arg_value(restart_args, "--timeframe"),
        "step": arg_value(restart_args, "--step"),
        "raw_close_alpha": arg_value(restart_args, "--raw-close-alpha"),
        "open_inventory_count": int(queue_row.get("open_inventory_count", 0) or 0),
        "trade_event_count": int(queue_row.get("trade_event_count", 0) or 0),
        "watchdog_groups": list(queue_row.get("watchdog_groups") or []),
        "state_path": path_display(str(queue_row.get("state_path") or registry_row.get("state_path") or "")),
        "event_path": path_display(str(queue_row.get("event_path") or registry_row.get("event_path") or "")),
        "has_fresh_start": bool(queue_row.get("has_fresh_start")),
        "latest_trade_event_ts_utc": str(queue_row.get("latest_trade_event_ts_utc") or ""),
        "rationale": str(queue_row.get("rationale") or ""),
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

    rows = [packet_row(row, registry_row_by_name(registry_payload, str(row.get("lane") or ""))) for row in queue_rows]
    safe_first_wave = [row for row in rows if row.get("candidate_verdict") == "recycle_first_wave"]
    safe_second_wave = [row for row in rows if row.get("candidate_verdict") == "recycle_second_wave"]
    blocked = [row for row in rows if row.get("candidate_verdict") == "blocked_fresh_start_contract"]
    preserve = [row for row in rows if row.get("candidate_verdict") == "preserve_continuity_first"]

    if safe_first_wave:
        readiness = "packet_ready_first_wave"
        next_action = (
            "If the room deliberately wants faster FX telemetry evidence, use the first safe candidate packet in this board, "
            "then refresh FX operator surfaces and watch the visibility board for a post-patch trade-path event."
        )
    elif safe_second_wave:
        readiness = "packet_ready_second_wave_only"
        next_action = "No first-wave safe packet exists, but a second-wave shadow recycle packet is available if the room still wants acceleration."
    elif blocked:
        readiness = "packet_blocked_by_restart_contract"
        next_action = "The attractive candidates are blocked by `--fresh-start` contract risk. Fix the restart contract before using them for continuity-preserving acceleration."
    else:
        readiness = "packet_not_actionable"
        next_action = "No FX shadow recycle packet is currently actionable; wait for runtime evidence or reassess the queue."

    summary = {
        "safe_first_wave_count": len(safe_first_wave),
        "safe_second_wave_count": len(safe_second_wave),
        "blocked_fresh_start_contract_count": len(blocked),
        "preserve_continuity_first_count": len(preserve),
        "top_safe_candidate": str(safe_first_wave[0]["lane"]) if safe_first_wave else (str(safe_second_wave[0]["lane"]) if safe_second_wave else ""),
    }

    return {
        "generated_at": now.isoformat(),
        "source_queue": str(QUEUE_PATH.relative_to(ROOT)).replace("\\", "/"),
        "source_readiness": str(queue_payload.get("readiness") or ""),
        "readiness": readiness,
        "next_action": next_action,
        "summary": summary,
        "safe_first_wave": safe_first_wave,
        "safe_second_wave": safe_second_wave,
        "blocked_fresh_start_contract": blocked,
        "preserve_continuity_first": preserve,
        "watch_steps": [
            "Record the pre-action state and event paths from this packet before any deliberate recycle.",
            "Do not use any packet row with `has_fresh_start=yes` for continuity-preserving telemetry acceleration.",
            "After a deliberate shadow recycle, run `python scripts/refresh_fx_operator_surfaces.py`.",
            "Read `reports/fx_phase1_telemetry_visibility_board.md` first; the honest next transition is `awaiting_first_post_patch_trade_event` or `phase1_visible`, not another immediate recycle.",
            "Then read `reports/fx_shadow_telemetry_recycle_board.md` and `reports/team_leverage_execution_docket.md` to confirm the room still agrees on queue posture.",
        ],
        "no_go_rules": [
            "Do not restart live FX lanes from this packet.",
            "Do not recycle any row blocked by `blocked_fresh_start_contract` unless the restart contract is changed first.",
            "Do not use high-inventory `preserve_continuity_first` rows as first-wave telemetry accelerators.",
        ],
    }


def render_packet_table(title: str, rows: list[dict[str, Any]]) -> list[str]:
    lines = [f"## {title}", ""]
    if not rows:
        lines.append("_none_")
        lines.append("")
        return lines
    lines.extend(
        [
            "| Lane | Symbol | Timeframe | Step | Alpha | Fresh start | Open inventory | State path | Event path |",
            "| --- | --- | --- | --- | --- | --- | ---: | --- | --- |",
        ]
    )
    for row in rows:
        lines.append(
            f"| `{row.get('lane', '')}` | `{row.get('symbol', '') or '-'}` | `{row.get('timeframe', '') or '-'}` | "
            f"`{row.get('step', '') or '-'}` | `{row.get('raw_close_alpha', '') or '-'}` | "
            f"`{'yes' if row.get('has_fresh_start') else 'no'}` | `{int(row.get('open_inventory_count', 0) or 0)}` | "
            f"`{row.get('state_path', '') or '-'}` | `{row.get('event_path', '') or '-'}` |"
        )
    lines.append("")
    return lines


def render_markdown(payload: dict[str, Any]) -> str:
    summary = payload.get("summary") if isinstance(payload.get("summary"), dict) else {}
    lines = [
        "# FX Shadow Telemetry Recycle Packet Board",
        "",
        "> Operator packet for the current safe FX shadow telemetry acceleration candidates.",
        "> This is a planning/runbook surface only. It does not authorize any live restart.",
        "",
        f"- generated_at: `{payload.get('generated_at', '')}`",
        f"- source_queue: `{payload.get('source_queue', '')}`",
        f"- source_readiness: `{payload.get('source_readiness', '')}`",
        f"- readiness: `{payload.get('readiness', '')}`",
        f"- next_action: `{payload.get('next_action', '')}`",
        "",
        "## Summary",
        "",
        f"- safe_first_wave_count: `{int(summary.get('safe_first_wave_count', 0) or 0)}`",
        f"- safe_second_wave_count: `{int(summary.get('safe_second_wave_count', 0) or 0)}`",
        f"- blocked_fresh_start_contract_count: `{int(summary.get('blocked_fresh_start_contract_count', 0) or 0)}`",
        f"- preserve_continuity_first_count: `{int(summary.get('preserve_continuity_first_count', 0) or 0)}`",
        f"- top_safe_candidate: `{summary.get('top_safe_candidate', '') or 'none'}`",
        "",
    ]
    lines.extend(render_packet_table("Safe First Wave", list(payload.get("safe_first_wave") or [])))
    lines.extend(render_packet_table("Safe Second Wave", list(payload.get("safe_second_wave") or [])))
    lines.extend(render_packet_table("Blocked By Fresh-Start Contract", list(payload.get("blocked_fresh_start_contract") or [])))
    lines.extend(render_packet_table("Preserve Continuity First", list(payload.get("preserve_continuity_first") or [])))
    lines.extend(
        [
            "## Watch Steps",
            "",
        ]
    )
    for step in list(payload.get("watch_steps") or []):
        lines.append(f"- {step}")
    lines.extend(
        [
            "",
            "## No-Go Rules",
            "",
        ]
    )
    for rule in list(payload.get("no_go_rules") or []):
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
