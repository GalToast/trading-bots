#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent.parent
REPORTS = ROOT / "reports"
REFRESH_SCRIPTS = [
    ROOT / "scripts" / "build_adaptive_overnight_launch_packet_board.py",
    ROOT / "scripts" / "build_gbpusd_adaptive_shadow_packet.py",
    ROOT / "scripts" / "build_gbpusd_adaptive_first_path_board.py",
    ROOT / "scripts" / "build_adaptive_incumbent_study_board.py",
    ROOT / "scripts" / "build_adaptive_harness_acceptance_verdict_board.py",
    ROOT / "scripts" / "build_adaptive_lattice_perfection_scorecard_board.py",
]
SWITCHBOARD_CLI_SCRIPT = ROOT / "scripts" / "switchboard_cli.py"
BOARD_JSON = REPORTS / "adaptive_overnight_launch_packet_board.json"
GBP_FIRST_PATH_JSON = REPORTS / "gbpusd_adaptive_first_path_board.json"
INCUMBENT_STUDY_JSON = REPORTS / "adaptive_incumbent_study_board.json"
ACCEPTANCE_JSON = REPORTS / "adaptive_harness_acceptance_verdict_board.json"
PERFECTION_JSON = REPORTS / "adaptive_lattice_perfection_scorecard_board.json"
STATE_JSON = REPORTS / "adaptive_overnight_launch_packet_monitor_state.json"
WATCHED_PACKET_FIELDS = (
    "action_status",
    "execution_watchdog_status",
    "artifact_started",
    "artifact_runner_started_at",
    "artifact_runner_heartbeat_age_seconds",
    "current_run_trade_opens",
    "current_run_trade_closes",
    "pre_start_trade_opens",
    "pre_start_trade_closes",
    "first_path_verdict",
    "first_path_rationale",
    "first_path_close_realized_pnl",
    "first_path_open_entry_context",
)
WATCHED_PACKETS = (
    {
        "packet_id": "btc_restore_comparison_shadow",
        "prefix": "restore",
        "message_prefix": "restore",
        "display_label": "btc_restore",
    },
    {
        "packet_id": "gbpusd_adaptive_comparison_packet",
        "prefix": "gbp",
        "message_prefix": "gbp",
        "display_label": "gbp_adaptive",
    },
)


def utc_now_text() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")


def load_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


def refresh_surface() -> None:
    for script in REFRESH_SCRIPTS:
        result = subprocess.run(
            [sys.executable, str(script)],
            cwd=ROOT,
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            raise RuntimeError(f"{script.name} failed: {(result.stderr or result.stdout).strip()}")


def _find_row(payload: dict[str, Any], packet_id: str) -> dict[str, Any]:
    for row in list(payload.get("rows") or []):
        if str(row.get("packet_id") or "") == packet_id:
            return dict(row)
    return {}


def _packet_snapshot(prefix: str, row: dict[str, Any]) -> dict[str, Any]:
    return {
        f"{prefix}_action_status": str(row.get("action_status") or ""),
        f"{prefix}_execution_watchdog_status": str(row.get("execution_watchdog_status") or ""),
        f"{prefix}_artifact_started": bool(row.get("artifact_started")),
        f"{prefix}_artifact_runner_started_at": str(row.get("artifact_runner_started_at") or ""),
        f"{prefix}_artifact_runner_heartbeat_age_seconds": row.get("artifact_runner_heartbeat_age_seconds"),
        f"{prefix}_current_run_trade_opens": int(row.get("artifact_trade_opens", 0) or 0),
        f"{prefix}_current_run_trade_closes": int(row.get("artifact_trade_closes", 0) or 0),
        f"{prefix}_pre_start_trade_opens": int(row.get("artifact_pre_start_trade_opens", 0) or 0),
        f"{prefix}_pre_start_trade_closes": int(row.get("artifact_pre_start_trade_closes", 0) or 0),
        f"{prefix}_first_path_verdict": str(row.get("first_path_verdict") or ""),
        f"{prefix}_first_path_rationale": str(row.get("first_path_rationale") or ""),
        f"{prefix}_first_path_close_realized_pnl": row.get("first_path_close_realized_pnl"),
        f"{prefix}_first_path_open_entry_context": str(row.get("first_path_open_entry_context") or ""),
    }


def snapshot_from_payload(payload: dict[str, Any]) -> dict[str, Any]:
    summary = dict(payload.get("summary") or {})
    snapshot = {
        "launch_now_lanes": [str(item) for item in list(summary.get("launch_now_lanes") or [])],
        "already_running_lanes": [str(item) for item in list(summary.get("already_running_lanes") or [])],
        "hold_lanes": [str(item) for item in list(summary.get("hold_lanes") or [])],
    }
    for config in WATCHED_PACKETS:
        snapshot.update(_packet_snapshot(config["prefix"], _find_row(payload, config["packet_id"])))
    return snapshot


def _find_symbol_row(payload: dict[str, Any], symbol: str) -> dict[str, Any]:
    for row in list(payload.get("rows") or []):
        if str(row.get("symbol") or "").upper() == symbol.upper():
            return dict(row)
    return {}


def _gbp_proof_snapshot(payload: dict[str, Any]) -> dict[str, Any]:
    summary = dict(payload.get("summary") or {})
    seat = dict(payload.get("seat") or {})
    queue = dict(payload.get("queue") or {})
    return {
        "gbp_proof_gate_status": str(summary.get("proof_gate_status") or ""),
        "gbp_queue_status": str(summary.get("queue_status") or ""),
        "gbp_queue_next_action_class": str(queue.get("next_action_class") or ""),
        "gbp_seat_actionability_status": str(summary.get("seat_actionability_status") or ""),
        "gbp_seat_contract_gap_status": str(summary.get("seat_contract_gap_status") or ""),
        "gbp_seat_execution_gate_status": str(summary.get("seat_execution_gate_status") or ""),
        "gbp_seat_execution_gate_read": str(seat.get("seat_execution_gate_read") or ""),
        "gbp_shared_score_verdict": str(summary.get("shared_score_verdict") or ""),
    }


def enrich_snapshot(snapshot: dict[str, Any]) -> dict[str, Any]:
    gbp_first_path_payload = load_json(GBP_FIRST_PATH_JSON)
    study_payload = load_json(INCUMBENT_STUDY_JSON)
    acceptance_payload = load_json(ACCEPTANCE_JSON)
    perfection_payload = load_json(PERFECTION_JSON)

    btc_study = _find_symbol_row(study_payload, "BTCUSD")
    btc_contract = dict(btc_study.get("btc_max_profit_comparison") or {})
    acceptance_summary = dict(acceptance_payload.get("summary") or {})
    perfection_summary = dict(perfection_payload.get("summary") or {})

    snapshot.update(
        {
            **_gbp_proof_snapshot(gbp_first_path_payload),
            "btc_max_profit_verdict": str(btc_contract.get("verdict") or ""),
            "btc_max_profit_restore_lane": str(btc_contract.get("restore_lane") or ""),
            "btc_max_profit_adaptive_shape_id": str(btc_contract.get("adaptive_shape_id") or ""),
            "btc_max_profit_adaptive_close_count": btc_contract.get("adaptive_runner_session_close_count"),
            "btc_max_profit_adaptive_realized_usd": btc_contract.get("adaptive_runner_session_realized_usd"),
            "btc_max_profit_adaptive_carry_usd": btc_contract.get("adaptive_pre_start_carry_realized_usd"),
            "btc_max_profit_score_gap": btc_contract.get("score_gap"),
            "btc_max_profit_read": str(btc_contract.get("read") or ""),
            "acceptance_btc_max_profit_verdict": str(acceptance_summary.get("btc_max_profit_verdict") or ""),
            "perfection_btc_max_profit_verdict": str(perfection_summary.get("btc_max_profit_verdict") or ""),
        }
    )
    return snapshot


def _append_packet_diff(messages: list[str], previous: dict[str, Any], current: dict[str, Any], *, prefix: str, message_prefix: str) -> None:
    current_action = str(current.get(f"{prefix}_action_status") or "")
    previous_action = str(previous.get(f"{prefix}_action_status") or "")
    if current_action != previous_action:
        messages.append(
            f"{message_prefix}_action_status "
            f"{previous_action or 'missing'} -> {current_action or 'missing'}"
        )
    current_watchdog = str(current.get(f"{prefix}_execution_watchdog_status") or "")
    previous_watchdog = str(previous.get(f"{prefix}_execution_watchdog_status") or "")
    if current_watchdog != previous_watchdog:
        messages.append(
            f"{message_prefix}_execution_watchdog_status "
            f"{previous_watchdog or 'missing'} -> {current_watchdog or 'missing'}"
        )
    current_opens = int(current.get(f"{prefix}_current_run_trade_opens", 0) or 0)
    previous_opens = int(previous.get(f"{prefix}_current_run_trade_opens", 0) or 0)
    if current_opens != previous_opens:
        messages.append(
            f"{message_prefix}_current_run_trade_opens "
            f"{previous_opens} -> {current_opens}"
        )
    current_closes = int(current.get(f"{prefix}_current_run_trade_closes", 0) or 0)
    previous_closes = int(previous.get(f"{prefix}_current_run_trade_closes", 0) or 0)
    if current_closes != previous_closes:
        messages.append(
            f"{message_prefix}_current_run_trade_closes "
            f"{previous_closes} -> {current_closes}"
        )
    current_verdict = str(current.get(f"{prefix}_first_path_verdict") or "")
    previous_verdict = str(previous.get(f"{prefix}_first_path_verdict") or "")
    if current_verdict != previous_verdict:
        messages.append(
            f"{message_prefix}_first_path_verdict "
            f"{previous_verdict or 'missing'} -> {current_verdict or 'missing'}"
        )
    current_close_pnl = current.get(f"{prefix}_first_path_close_realized_pnl")
    previous_close_pnl = previous.get(f"{prefix}_first_path_close_realized_pnl")
    if current_close_pnl != previous_close_pnl:
        messages.append(
            f"{message_prefix}_first_path_close_realized_pnl "
            f"{previous_close_pnl if previous_close_pnl is not None else 'missing'} -> "
            f"{current_close_pnl if current_close_pnl is not None else 'missing'}"
        )


def diff_messages(previous: dict[str, Any], current: dict[str, Any]) -> list[str]:
    if not previous:
        return []
    messages: list[str] = []
    if current.get("launch_now_lanes") != previous.get("launch_now_lanes"):
        messages.append(f"launch_now_lanes {previous.get('launch_now_lanes', [])} -> {current.get('launch_now_lanes', [])}")
    if current.get("already_running_lanes") != previous.get("already_running_lanes"):
        messages.append(
            f"already_running_lanes {previous.get('already_running_lanes', [])} -> {current.get('already_running_lanes', [])}"
        )
    for config in WATCHED_PACKETS:
        _append_packet_diff(
            messages,
            previous,
            current,
            prefix=str(config["prefix"]),
            message_prefix=str(config["message_prefix"]),
        )
    for field in (
        "gbp_proof_gate_status",
        "gbp_queue_status",
        "gbp_queue_next_action_class",
        "gbp_seat_actionability_status",
        "gbp_seat_contract_gap_status",
        "gbp_seat_execution_gate_status",
        "gbp_shared_score_verdict",
    ):
        if current.get(field) != previous.get(field):
            messages.append(
                f"{field} {previous.get(field, 'missing')} -> {current.get(field, 'missing')}"
            )
    if current.get("gbp_seat_execution_gate_read") != previous.get("gbp_seat_execution_gate_read"):
        messages.append(
            "gbp_seat_execution_gate_read "
            f"{previous.get('gbp_seat_execution_gate_read', 'missing')} -> "
            f"{current.get('gbp_seat_execution_gate_read', 'missing')}"
        )
    if current.get("btc_max_profit_verdict") != previous.get("btc_max_profit_verdict"):
        messages.append(
            "btc_max_profit_verdict "
            f"{previous.get('btc_max_profit_verdict', 'missing')} -> {current.get('btc_max_profit_verdict', 'missing')}"
        )
    if current.get("btc_max_profit_adaptive_close_count") != previous.get("btc_max_profit_adaptive_close_count"):
        messages.append(
            "btc_max_profit_adaptive_close_count "
            f"{previous.get('btc_max_profit_adaptive_close_count', 'missing')} -> "
            f"{current.get('btc_max_profit_adaptive_close_count', 'missing')}"
        )
    if current.get("btc_max_profit_adaptive_realized_usd") != previous.get("btc_max_profit_adaptive_realized_usd"):
        messages.append(
            "btc_max_profit_adaptive_realized_usd "
            f"{previous.get('btc_max_profit_adaptive_realized_usd', 'missing')} -> "
            f"{current.get('btc_max_profit_adaptive_realized_usd', 'missing')}"
        )
    return messages


def _proof_verdict_arrived(verdict: str) -> bool:
    return verdict not in {"", "awaiting_first_trade_path_event", "first_path_opened_waiting_close"}


def proof_arrived(snapshot: dict[str, Any]) -> bool:
    if _proof_verdict_arrived(str(snapshot.get("restore_first_path_verdict") or "")):
        return True
    if _proof_verdict_arrived(str(snapshot.get("gbp_first_path_verdict") or "")):
        return True
    if str(snapshot.get("gbp_proof_gate_status") or "") in {
        "first_path_recorded_wait_shared_score_refresh",
        "shared_score_comparable",
    }:
        return True
    try:
        return int(snapshot.get("btc_max_profit_adaptive_close_count") or 0) > 0
    except Exception:
        return False


def write_monitor_state(snapshot: dict[str, Any]) -> None:
    payload = dict(snapshot)
    payload["checked_at"] = datetime.now(timezone.utc).isoformat()
    STATE_JSON.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def print_snapshot(payload: dict[str, Any], snapshot: dict[str, Any]) -> None:
    print(f"[{utc_now_text()}] adaptive_overnight_launch_packet")
    print(f"  launch_now_lanes: {snapshot.get('launch_now_lanes', [])}")
    print(f"  already_running_lanes: {snapshot.get('already_running_lanes', [])}")
    print(f"  hold_lanes: {snapshot.get('hold_lanes', [])}")
    print(
        "  btc_restore: "
        f"action={snapshot.get('restore_action_status', '')} "
        f"watchdog={snapshot.get('restore_execution_watchdog_status', '')} "
        f"current_run_trades={snapshot.get('restore_current_run_trade_opens', 0)}/"
        f"{snapshot.get('restore_current_run_trade_closes', 0)} "
        f"pre_start_history={snapshot.get('restore_pre_start_trade_opens', 0)}/"
        f"{snapshot.get('restore_pre_start_trade_closes', 0)} "
        f"first_path={snapshot.get('restore_first_path_verdict', '')}"
    )
    rationale = str(snapshot.get("restore_first_path_rationale") or "")
    if rationale:
        print(f"  btc_restore_rationale: {rationale}")
    print(
        "  gbp_adaptive: "
        f"action={snapshot.get('gbp_action_status', '')} "
        f"watchdog={snapshot.get('gbp_execution_watchdog_status', '')} "
        f"current_run_trades={snapshot.get('gbp_current_run_trade_opens', 0)}/"
        f"{snapshot.get('gbp_current_run_trade_closes', 0)} "
        f"pre_start_history={snapshot.get('gbp_pre_start_trade_opens', 0)}/"
        f"{snapshot.get('gbp_pre_start_trade_closes', 0)} "
        f"first_path={snapshot.get('gbp_first_path_verdict', '')}"
    )
    gbp_rationale = str(snapshot.get("gbp_first_path_rationale") or "")
    if gbp_rationale:
        print(f"  gbp_adaptive_rationale: {gbp_rationale}")
    print(
        "  gbp_proof_gate: "
        f"status={snapshot.get('gbp_proof_gate_status', '')} "
        f"queue={snapshot.get('gbp_queue_status', '')} "
        f"next_action={snapshot.get('gbp_queue_next_action_class', '')} "
        f"seat={snapshot.get('gbp_seat_actionability_status', '')} "
        f"contract_gap={snapshot.get('gbp_seat_contract_gap_status', '')} "
        f"execution_gate={snapshot.get('gbp_seat_execution_gate_status', '')} "
        f"shared_score={snapshot.get('gbp_shared_score_verdict', '')}"
    )
    print(
        "  btc_max_profit: "
        f"verdict={snapshot.get('btc_max_profit_verdict', '')} "
        f"adaptive_shape={snapshot.get('btc_max_profit_adaptive_shape_id', '')} "
        f"adaptive_closes={snapshot.get('btc_max_profit_adaptive_close_count', 'missing')} "
        f"adaptive_realized={snapshot.get('btc_max_profit_adaptive_realized_usd', 'missing')} "
        f"score_gap={snapshot.get('btc_max_profit_score_gap', 'missing')}"
    )
    print(f"  board_generated_at: {payload.get('generated_at', '')}")


def format_switchboard_message(changes: list[str], snapshot: dict[str, Any]) -> str:
    content = (
        "Adaptive overnight watcher alert: "
        f"launch_now={snapshot.get('launch_now_lanes', [])}, "
        f"already_running={snapshot.get('already_running_lanes', [])}, "
        f"btc_restore_action={snapshot.get('restore_action_status', '')}, "
        f"btc_restore_watchdog={snapshot.get('restore_execution_watchdog_status', '')}, "
        "btc_restore_current_run="
        f"{snapshot.get('restore_current_run_trade_opens', 0)}/{snapshot.get('restore_current_run_trade_closes', 0)}, "
        "btc_restore_pre_start_history="
        f"{snapshot.get('restore_pre_start_trade_opens', 0)}/{snapshot.get('restore_pre_start_trade_closes', 0)}, "
        f"btc_restore_first_path={snapshot.get('restore_first_path_verdict', '')}, "
        f"gbp_action={snapshot.get('gbp_action_status', '')}, "
        f"gbp_watchdog={snapshot.get('gbp_execution_watchdog_status', '')}, "
        f"gbp_current_run={snapshot.get('gbp_current_run_trade_opens', 0)}/{snapshot.get('gbp_current_run_trade_closes', 0)}, "
        f"gbp_pre_start_history={snapshot.get('gbp_pre_start_trade_opens', 0)}/{snapshot.get('gbp_pre_start_trade_closes', 0)}, "
        f"gbp_first_path={snapshot.get('gbp_first_path_verdict', '')}, "
        f"gbp_proof_gate={snapshot.get('gbp_proof_gate_status', '')}, "
        f"gbp_next_action={snapshot.get('gbp_queue_next_action_class', '')}, "
        f"gbp_execution_gate={snapshot.get('gbp_seat_execution_gate_status', '')}, "
        f"gbp_shared_score={snapshot.get('gbp_shared_score_verdict', '')}, "
        f"btc_max_profit_verdict={snapshot.get('btc_max_profit_verdict', '')}, "
        f"btc_max_profit_adaptive={snapshot.get('btc_max_profit_adaptive_shape_id', '')}, "
        f"btc_max_profit_closes={snapshot.get('btc_max_profit_adaptive_close_count', 'missing')}, "
        f"btc_max_profit_realized={snapshot.get('btc_max_profit_adaptive_realized_usd', 'missing')}."
    )
    rationale = str(snapshot.get("restore_first_path_rationale") or "")
    if rationale:
        content += f" BTC rationale: {rationale}"
    gbp_rationale = str(snapshot.get("gbp_first_path_rationale") or "")
    if gbp_rationale:
        content += f" GBP rationale: {gbp_rationale}"
    gbp_execution_gate_read = str(snapshot.get("gbp_seat_execution_gate_read") or "")
    if gbp_execution_gate_read:
        content += f" GBP execution gate: {gbp_execution_gate_read}"
    if changes:
        content += " Changes: " + "; ".join(changes)
    return content


def post_switchboard_message(
    *,
    sender: str,
    content: str,
    channel: str = "general",
    message_type: str = "status",
) -> None:
    result = subprocess.run(
        [
            sys.executable,
            str(SWITCHBOARD_CLI_SCRIPT),
            "post",
            "--sender",
            sender,
            "--channel",
            channel,
            "--type",
            message_type,
            "--content",
            content,
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(f"switchboard post failed: {(result.stderr or result.stdout).strip()}")


def run_once(*, previous_snapshot: dict[str, Any] | None = None, quiet: bool = False) -> tuple[dict[str, Any], list[str], dict[str, Any]]:
    refresh_surface()
    payload = load_json(BOARD_JSON)
    snapshot = enrich_snapshot(snapshot_from_payload(payload))
    changes = diff_messages(previous_snapshot or {}, snapshot)
    if not quiet:
        print_snapshot(payload, snapshot)
        if changes:
            for change in changes:
                print(f"  ALERT change: {change}")
    write_monitor_state(snapshot)
    return payload, changes, snapshot


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Watch the adaptive overnight packet, GBP proof-gate surface, and BTC max-profit contract for BTC restore and GBP comparison proof changes."
    )
    parser.add_argument("--watch", action="store_true", help="Poll repeatedly instead of running once.")
    parser.add_argument("--interval", type=int, default=30, help="Seconds between polls in --watch mode.")
    parser.add_argument("--until-proof", action="store_true", help="In --watch mode, exit when BTC restore or GBP comparison reaches a close-like first-path or GBP proof-gate outcome, or the BTC adaptive cash-harvest candidate records a fresh close.")
    parser.add_argument("--quiet", action="store_true", help="Suppress the per-cycle summary; only print change alerts.")
    parser.add_argument(
        "--notify-switchboard",
        action="store_true",
        help="Post change alerts to switchboard via scripts/switchboard_cli.py.",
    )
    parser.add_argument(
        "--switchboard-sender",
        default="",
        help="Sender token used when posting switchboard alerts. Required with --notify-switchboard.",
    )
    parser.add_argument(
        "--switchboard-channel",
        default="general",
        help="Switchboard channel for change alerts.",
    )
    parser.add_argument(
        "--switchboard-message-type",
        default="status",
        help="Switchboard message type for change alerts.",
    )
    args = parser.parse_args()
    if args.notify_switchboard and not str(args.switchboard_sender or "").strip():
        parser.error("--switchboard-sender is required with --notify-switchboard")

    previous_snapshot = load_json(STATE_JSON)
    try:
        while True:
            payload, changes, snapshot = run_once(previous_snapshot=previous_snapshot, quiet=args.quiet)
            if args.quiet and changes:
                print(f"[{utc_now_text()}] adaptive_overnight_launch_packet")
                for change in changes:
                    print(f"  ALERT change: {change}")
            if args.notify_switchboard and changes:
                post_switchboard_message(
                    sender=str(args.switchboard_sender),
                    channel=str(args.switchboard_channel),
                    message_type=str(args.switchboard_message_type),
                    content=format_switchboard_message(changes, snapshot),
                )
            previous_snapshot = snapshot
            if args.until_proof and proof_arrived(snapshot):
                print(
                    f"[{utc_now_text()}] proof threshold reached: "
                    f"restore_first_path={snapshot.get('restore_first_path_verdict', '')} "
                    f"btc_max_profit_verdict={snapshot.get('btc_max_profit_verdict', '')}"
                )
                return 0
            if not args.watch:
                return 0
            time.sleep(max(1, int(args.interval)))
    except KeyboardInterrupt:
        print(f"[{utc_now_text()}] adaptive_overnight_launch_packet stopped")
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
