#!/usr/bin/env python3
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent.parent
REPORTS = ROOT / "reports"

WATCH_BOARD_PATH = REPORTS / "hungry_hippo_forward_shadow_watch_board.json"
ROLLOUT_GATE_PATH = REPORTS / "hungry_hippo_parallel_rollout_gate_board.json"
LAUNCH_SAFETY_PATH = REPORTS / "hungry_hippo_launch_safety_validation.json"
OUTPUT_JSON_PATH = REPORTS / "hungry_hippo_first_proof_launch_packet_board.json"
OUTPUT_MD_PATH = REPORTS / "hungry_hippo_first_proof_launch_packet_board.md"

VERDICT_PRIORITY = {"pass": 0, "research_only": 1, "fail": 2}


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def find_symbol(rows: list[dict[str, Any]], symbol: str) -> dict[str, Any] | None:
    clean_symbol = str(symbol or "").upper()
    for row in rows:
        if str(row.get("symbol") or "").upper() == clean_symbol:
            return dict(row)
    return None


def as_bool(value: Any) -> bool:
    return bool(value)


def choose_contract_row(rows: list[dict[str, Any]], symbol: str) -> dict[str, Any] | None:
    clean_symbol = str(symbol or "").upper()
    candidates = [
        dict(row)
        for row in rows
        if str(row.get("symbol") or "").upper() == clean_symbol
        and str(row.get("scope") or "") != "live_surface"
        and str(row.get("runner_family") or "") not in {"config_surface", "unknown"}
    ]
    if not candidates:
        return None
    candidates.sort(
        key=lambda row: (
            0 if bool(row.get("enabled")) else 1,
            VERDICT_PRIORITY.get(str(row.get("verdict") or ""), 9),
            str(row.get("config_path") or ""),
        )
    )
    return candidates[0]


def load_config_payload(contract_row: dict[str, Any] | None) -> dict[str, Any]:
    if not contract_row:
        return {}
    config_path = ROOT / Path(str(contract_row.get("config_path") or "").replace("\\", "/"))
    if not config_path.exists():
        return {}
    return load_json(config_path)


def build_row(
    symbol: str,
    watch_row: dict[str, Any] | None,
    contract_row: dict[str, Any] | None,
    *,
    launch_order: int,
    packet_role: str,
    rollout_blocker: str,
) -> dict[str, Any]:
    row = dict(watch_row or {})
    contract = dict(contract_row or {})
    config_payload = load_config_payload(contract_row)
    runtime_state = str(row.get("runtime_state") or "")
    validation_verdict = str(row.get("validation_verdict") or contract.get("verdict") or "")

    if not runtime_state and contract:
        runtime_state = "excluded_from_forward_watch"

    if validation_verdict == "fail":
        launch_readiness = "blocked_validation_fail"
        next_action = "Do not launch this row. Its selected contract currently fails launch-safety validation and is excluded from the forward-watch set."
    elif runtime_state and runtime_state != "not_launched_yet":
        launch_readiness = "already_started"
        next_action = "This lane has already left the parked state. Do not relaunch from this packet; monitor the forward-watch board instead."
    elif packet_role == "starter_candidate":
        launch_readiness = "launch_now"
        next_action = "Start this parked proof lane on current code, then switch to the forward watch board for first-open / first-close monitoring."
    elif packet_role == "watch_only_outside_current_unlock_ladder":
        launch_readiness = "watch_only_outside_current_unlock_ladder"
        next_action = "Keep this contract parked. It is in the current forward-watch set, but it is outside the current tiny-account unlock ladder and should not be started from this packet."
    else:
        launch_readiness = "hold_until_prior_gate_clears"
        next_action = "Keep this contract parked. It is not the current launch candidate because an earlier rollout gate is still blocked."
    return {
        "launch_order": launch_order,
        "symbol": symbol,
        "packet_role": packet_role,
        "launch_readiness": launch_readiness,
        "runtime_state": runtime_state,
        "config_path": str(row.get("config_path") or contract.get("config_path") or ""),
        "watchdog_group": str(row.get("watchdog_group") or config_payload.get("watchdog_group") or ""),
        "runner_family": str(row.get("runner_family") or contract.get("runner_family") or ""),
        "state_path": str(row.get("state_path") or config_payload.get("state_path") or ""),
        "event_path": str(row.get("event_path") or config_payload.get("event_path") or ""),
        "enabled": as_bool(row.get("enabled") if watch_row is not None else config_payload.get("enabled")),
        "pause_note": str(row.get("pause_note") or config_payload.get("pause_note") or ""),
        "validation_verdict": validation_verdict,
        "proof_started": as_bool(row.get("proof_started")),
        "current_open_count": int(row.get("current_open_count") or 0),
        "realized_closes": int(row.get("realized_closes") or 0),
        "realized_net_usd": float(row.get("realized_net_usd") or 0.0),
        "rollout_blocker": rollout_blocker,
        "next_action": next_action,
    }


def build_payload(watch_payload: dict[str, Any], rollout_payload: dict[str, Any], launch_safety_payload: dict[str, Any]) -> dict[str, Any]:
    watch_rows = list(watch_payload.get("rows") or [])
    rollout_summary = dict(rollout_payload.get("summary") or {})
    rollout_rows = list(rollout_payload.get("rows") or [])
    launch_contract_rows = list(launch_safety_payload.get("rows") or [])
    watch_symbols = [str(row.get("symbol") or "").upper() for row in watch_rows if str(row.get("symbol") or "")]

    starter_symbol = str(rollout_summary.get("starter_candidate_symbol") or "")
    starter_next_symbol = str(rollout_summary.get("starter_next_symbol") or "")

    lane1_blocker = str((rollout_rows[0] if len(rollout_rows) > 0 else {}).get("blocker_reason") or "")
    lane2_blocker = str((rollout_rows[1] if len(rollout_rows) > 1 else {}).get("blocker_reason") or "")
    outside_unlock_ladder_blocker = (
        "This symbol is in the current forward-watch set, but it is not inside the current tiny-account unlock ladder. "
        "Do not start it from this packet until the unlock ladder is deliberately revised."
    )

    rows: list[dict[str, Any]] = []
    ordered_symbols: list[str] = []
    if starter_symbol:
        ordered_symbols.append(starter_symbol.upper())
        rows.append(
            build_row(
                starter_symbol,
                find_symbol(watch_rows, starter_symbol),
                choose_contract_row(launch_contract_rows, starter_symbol),
                launch_order=1,
                packet_role="starter_candidate",
                rollout_blocker=lane1_blocker,
            )
        )
    if starter_next_symbol:
        ordered_symbols.append(starter_next_symbol.upper())
        rows.append(
            build_row(
                starter_next_symbol,
                find_symbol(watch_rows, starter_next_symbol),
                choose_contract_row(launch_contract_rows, starter_next_symbol),
                launch_order=2,
                packet_role="starter_next",
                rollout_blocker=lane2_blocker,
            )
        )
    for symbol in watch_symbols:
        if symbol in ordered_symbols:
            continue
        rows.append(
            build_row(
                symbol,
                find_symbol(watch_rows, symbol),
                choose_contract_row(launch_contract_rows, symbol),
                launch_order=len(rows) + 1,
                packet_role="watch_only_outside_current_unlock_ladder",
                rollout_blocker=outside_unlock_ladder_blocker,
            )
        )

    not_launchable_now = [row["symbol"] for row in rows if row["launch_readiness"] != "launch_now"]
    launch_now = [row["symbol"] for row in rows if row["launch_readiness"] == "launch_now"]
    watch_only = [row["symbol"] for row in rows if row["launch_readiness"] == "watch_only_outside_current_unlock_ladder"]
    blocked_validation = [row["symbol"] for row in rows if row["launch_readiness"] == "blocked_validation_fail"]

    return {
        "generated_at": utc_now_iso(),
        "sources": [
            str(WATCH_BOARD_PATH.relative_to(ROOT)),
            str(ROLLOUT_GATE_PATH.relative_to(ROOT)),
            str(LAUNCH_SAFETY_PATH.relative_to(ROOT)),
        ],
        "summary": {
            "launch_now_symbols": launch_now,
            "hold_symbols": not_launchable_now,
            "watch_only_symbols": watch_only,
            "blocked_validation_symbols": blocked_validation,
            "starter_candidate_symbol": starter_symbol,
            "starter_next_symbol": starter_next_symbol,
            "current_max_honest_active_lanes": int(rollout_summary.get("current_max_honest_active_lanes") or 0),
        },
        "leadership_read": [
            f"Current first-proof HH launch order is `{[row['symbol'] for row in rows] or ['none']}`.",
            f"Only `{launch_now or ['none']}` is launchable from this packet right now; `{not_launchable_now or ['none']}` stays parked until the earlier rollout gate clears.",
            f"Validation-blocked rows are `{blocked_validation or ['none']}`; watch-only rows outside the current unlock ladder are `{watch_only or ['none']}`.",
        ],
        "rows": rows,
        "watch_steps": [
            "Launch only the first row whose `launch_readiness` is `launch_now`.",
            "After launch, read `reports/hungry_hippo_forward_shadow_watch_board.md` to distinguish `not_launched_yet`, `launched_waiting_first_open`, and `forward_proof_started`.",
            "Do not launch the second row until `reports/hungry_hippo_parallel_rollout_gate_board.md` no longer blocks lane 2.",
            "Do not launch any row marked `watch_only_outside_current_unlock_ladder` until the unlock ladder itself changes.",
        ],
        "no_go_rules": [
            "Do not start more than one row from this packet at the same time on a tiny account.",
            "Do not treat parked contract existence as permission to bypass the rollout gate.",
            "Do not treat forward-watch membership as permission to bypass the unlock ladder.",
            "Do not use this packet to justify live promotion; it is shadow-proof launch support only.",
        ],
    }


def render_markdown(payload: dict[str, Any]) -> str:
    summary = dict(payload.get("summary") or {})
    lines = [
        "# Hungry Hippo First-Proof Launch Packet Board",
        "",
        "> Operator packet for the first honest Hungry Hippo proof launches on a tiny account.",
        "",
        f"- generated_at: `{payload.get('generated_at')}`",
        "",
        "## Leadership Read",
        "",
    ]
    for item in list(payload.get("leadership_read") or []):
        lines.append(f"- {item}")
    lines.extend(
        [
            "",
            "## Summary",
            "",
            f"- launch_now_symbols: `{summary.get('launch_now_symbols')}`",
            f"- hold_symbols: `{summary.get('hold_symbols')}`",
            f"- watch_only_symbols: `{summary.get('watch_only_symbols')}`",
            f"- blocked_validation_symbols: `{summary.get('blocked_validation_symbols')}`",
            f"- starter_candidate_symbol: `{summary.get('starter_candidate_symbol')}`",
            f"- starter_next_symbol: `{summary.get('starter_next_symbol')}`",
            f"- current_max_honest_active_lanes: `{summary.get('current_max_honest_active_lanes')}`",
            "",
            "## Packet",
            "",
            "| Order | Symbol | Role | Readiness | Watchdog | Config | State | Events |",
            "| --- | --- | --- | --- | --- | --- | --- | --- |",
        ]
    )
    for row in list(payload.get("rows") or []):
        lines.append(
            f"| `{row.get('launch_order')}` | `{row.get('symbol')}` | `{row.get('packet_role')}` | `{row.get('launch_readiness')}` | "
            f"`{row.get('watchdog_group') or '-'}` | `{row.get('config_path') or '-'}` | "
            f"`{row.get('state_path') or '-'}` | `{row.get('event_path') or '-'}` |"
        )
    lines.extend(["", "## Row Detail", ""])
    for row in list(payload.get("rows") or []):
        lines.extend(
            [
                f"### {row.get('launch_order')}. {row.get('symbol')}",
                "",
                f"- Packet role: `{row.get('packet_role')}`",
                f"- Launch readiness: `{row.get('launch_readiness')}`",
                f"- Runtime state: `{row.get('runtime_state')}`",
                f"- Validation verdict: `{row.get('validation_verdict')}`",
                f"- Rollout blocker: {row.get('rollout_blocker')}",
                f"- Next action: {row.get('next_action')}",
                "",
            ]
        )
    lines.extend(["## Watch Steps", ""])
    for step in list(payload.get("watch_steps") or []):
        lines.append(f"- {step}")
    lines.extend(["", "## No-Go Rules", ""])
    for rule in list(payload.get("no_go_rules") or []):
        lines.append(f"- {rule}")
    lines.append("")
    return "\n".join(lines)


def main() -> None:
    payload = build_payload(load_json(WATCH_BOARD_PATH), load_json(ROLLOUT_GATE_PATH), load_json(LAUNCH_SAFETY_PATH))
    OUTPUT_JSON_PATH.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    OUTPUT_MD_PATH.write_text(render_markdown(payload), encoding="utf-8")


if __name__ == "__main__":
    main()
