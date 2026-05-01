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
ETH_BOARD_SCRIPT = ROOT / "scripts" / "build_eth_atr_runtime_status_board.py"
SHAPESHIFTER_BOARD_SCRIPT = ROOT / "scripts" / "build_structure_shapeshifter_proof_board.py"
COVERAGE_BOARD_SCRIPT = ROOT / "scripts" / "build_lattice_phase1_event_coverage_board.py"
EXPERIMENTAL_BOARD_SCRIPT = ROOT / "scripts" / "build_experimental_proof_watch_board.py"
SWITCHBOARD_CLI_SCRIPT = ROOT / "scripts" / "switchboard_cli.py"
BOARD_JSON = REPORTS / "experimental_proof_watch_board.json"
STATE_JSON = REPORTS / "experimental_proof_watch_monitor_state.json"


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


def refresh_surfaces() -> None:
    for script in (ETH_BOARD_SCRIPT, SHAPESHIFTER_BOARD_SCRIPT, COVERAGE_BOARD_SCRIPT, EXPERIMENTAL_BOARD_SCRIPT):
        result = subprocess.run(
            [sys.executable, str(script)],
            cwd=ROOT,
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            raise RuntimeError(f"{script.name} failed: {(result.stderr or result.stdout).strip()}")


def snapshot_from_payload(payload: dict[str, Any]) -> dict[str, Any]:
    eth = payload.get("eth_atr") if isinstance(payload.get("eth_atr"), dict) else {}
    shapeshifter = payload.get("shapeshifter") if isinstance(payload.get("shapeshifter"), dict) else {}
    return {
        "overall_status": str(payload.get("overall_status") or ""),
        "next_action": str(payload.get("next_action") or ""),
        "eth_total_realized_closes": int(eth.get("total_realized_closes", 0) or 0),
        "eth_total_open_positions": int(eth.get("total_open_positions", 0) or 0),
        "eth_total_realized_net_usd": float(eth.get("total_realized_net_usd", 0.0) or 0.0),
        "shapeshifter_proof_status": str(shapeshifter.get("proof_status") or ""),
        "shapeshifter_structure_flip_count_since_runner_start": int(
            shapeshifter.get("structure_flip_count_since_runner_start", 0) or 0
        ),
        "shapeshifter_realized_closes": int(shapeshifter.get("realized_closes", 0) or 0),
        "shapeshifter_phase1_event_coverage_readiness": str(shapeshifter.get("phase1_event_coverage_readiness") or ""),
        "shapeshifter_phase1_event_coverage_next_action": str(shapeshifter.get("phase1_event_coverage_next_action") or ""),
        "shapeshifter_phase1_event_covered_field_count": int(
            shapeshifter.get("phase1_event_covered_field_count", 0) or 0
        ),
        "shapeshifter_phase1_event_field_count": int(shapeshifter.get("phase1_event_field_count", 0) or 0),
        "shapeshifter_phase1_close_metric_event_count": int(
            shapeshifter.get("phase1_close_metric_event_count", 0) or 0
        ),
        "shapeshifter_phase1_loss_without_first_green_count": int(
            shapeshifter.get("phase1_loss_without_first_green_count", 0) or 0
        ),
        "shapeshifter_phase1_first_path_verdict": str(shapeshifter.get("phase1_first_path_verdict") or ""),
        "shapeshifter_phase1_market_state_hypothesis_verdict": str(
            shapeshifter.get("phase1_market_state_hypothesis_verdict") or ""
        ),
    }


def diff_messages(previous: dict[str, Any], current: dict[str, Any]) -> list[str]:
    if not previous:
        return []
    messages: list[str] = []
    if current.get("overall_status") != previous.get("overall_status"):
        messages.append(
            f"overall_status {previous.get('overall_status', 'missing')} -> {current.get('overall_status', 'missing')}"
        )
    if current.get("eth_total_realized_closes") != previous.get("eth_total_realized_closes"):
        messages.append(
            "eth_total_realized_closes "
            f"{previous.get('eth_total_realized_closes', 0)} -> {current.get('eth_total_realized_closes', 0)}"
        )
    if current.get("eth_total_open_positions") != previous.get("eth_total_open_positions"):
        messages.append(
            "eth_total_open_positions "
            f"{previous.get('eth_total_open_positions', 0)} -> {current.get('eth_total_open_positions', 0)}"
        )
    if current.get("shapeshifter_structure_flip_count_since_runner_start") != previous.get(
        "shapeshifter_structure_flip_count_since_runner_start"
    ):
        messages.append(
            "shapeshifter_structure_flip_count_since_runner_start "
            f"{previous.get('shapeshifter_structure_flip_count_since_runner_start', 0)} -> "
            f"{current.get('shapeshifter_structure_flip_count_since_runner_start', 0)}"
        )
    if current.get("shapeshifter_proof_status") != previous.get("shapeshifter_proof_status"):
        messages.append(
            "shapeshifter_proof_status "
            f"{previous.get('shapeshifter_proof_status', 'missing')} -> {current.get('shapeshifter_proof_status', 'missing')}"
        )
    previous_coverage = int(previous.get("shapeshifter_phase1_event_covered_field_count", 0) or 0)
    current_coverage = int(current.get("shapeshifter_phase1_event_covered_field_count", 0) or 0)
    if current_coverage != previous_coverage:
        messages.append(
            "shapeshifter_phase1_event_covered_field_count "
            f"{previous_coverage} -> {current_coverage}"
        )
    if current.get("shapeshifter_phase1_event_coverage_readiness") != previous.get(
        "shapeshifter_phase1_event_coverage_readiness"
    ):
        messages.append(
            "shapeshifter_phase1_event_coverage_readiness "
            f"{previous.get('shapeshifter_phase1_event_coverage_readiness', 'missing')} -> "
            f"{current.get('shapeshifter_phase1_event_coverage_readiness', 'missing')}"
        )
    previous_close_metric_count = int(previous.get("shapeshifter_phase1_close_metric_event_count", 0) or 0)
    current_close_metric_count = int(current.get("shapeshifter_phase1_close_metric_event_count", 0) or 0)
    if current_close_metric_count != previous_close_metric_count:
        messages.append(
            "shapeshifter_phase1_close_metric_event_count "
            f"{previous_close_metric_count} -> {current_close_metric_count}"
        )
    if current.get("shapeshifter_phase1_first_path_verdict") != previous.get("shapeshifter_phase1_first_path_verdict"):
        messages.append(
            "shapeshifter_phase1_first_path_verdict "
            f"{previous.get('shapeshifter_phase1_first_path_verdict', 'missing')} -> "
            f"{current.get('shapeshifter_phase1_first_path_verdict', 'missing')}"
        )
    if current.get("shapeshifter_phase1_market_state_hypothesis_verdict") != previous.get(
        "shapeshifter_phase1_market_state_hypothesis_verdict"
    ):
        messages.append(
            "shapeshifter_phase1_market_state_hypothesis_verdict "
            f"{previous.get('shapeshifter_phase1_market_state_hypothesis_verdict', 'missing')} -> "
            f"{current.get('shapeshifter_phase1_market_state_hypothesis_verdict', 'missing')}"
        )
    return messages


def proof_arrived(snapshot: dict[str, Any]) -> bool:
    return str(snapshot.get("overall_status") or "") in {
        "new_runtime_proof_available",
        "new_eth_forward_sample_available",
    }


def write_monitor_state(snapshot: dict[str, Any]) -> None:
    payload = dict(snapshot)
    payload["checked_at"] = datetime.now(timezone.utc).isoformat()
    STATE_JSON.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def print_snapshot(payload: dict[str, Any], snapshot: dict[str, Any]) -> None:
    print(f"[{utc_now_text()}] experimental_proof_watch")
    print(f"  overall_status: {snapshot.get('overall_status', '')}")
    print(f"  next_action: {snapshot.get('next_action', '')}")
    print(
        "  eth_atr: "
        f"closes={snapshot.get('eth_total_realized_closes', 0)} "
        f"opens={snapshot.get('eth_total_open_positions', 0)} "
        f"net=${float(snapshot.get('eth_total_realized_net_usd', 0.0) or 0.0):.2f}"
    )
    print(
        "  shapeshifter: "
        f"proof_status={snapshot.get('shapeshifter_proof_status', '')} "
        f"structure_flips_since_runner_start={snapshot.get('shapeshifter_structure_flip_count_since_runner_start', 0)} "
        f"closes={snapshot.get('shapeshifter_realized_closes', 0)} "
        "phase1_readiness="
        f"{snapshot.get('shapeshifter_phase1_event_coverage_readiness', '')} "
        "phase1_coverage="
        f"{snapshot.get('shapeshifter_phase1_event_covered_field_count', 0)}/"
        f"{snapshot.get('shapeshifter_phase1_event_field_count', 0)} "
        "phase1_close_metrics="
        f"{snapshot.get('shapeshifter_phase1_close_metric_event_count', 0)}"
        " phase1_first_path="
        f"{snapshot.get('shapeshifter_phase1_first_path_verdict', '')}"
        " phase1_market_state="
        f"{snapshot.get('shapeshifter_phase1_market_state_hypothesis_verdict', '')}"
    )
    coverage_next_action = str(snapshot.get("shapeshifter_phase1_event_coverage_next_action") or "")
    if coverage_next_action:
        print(f"  shapeshifter_phase1_next_action: {coverage_next_action}")
    print(f"  board_generated_at: {payload.get('generated_at', '')}")


def format_switchboard_message(changes: list[str], snapshot: dict[str, Any]) -> str:
    header = (
        "Passive-proof watcher alert: "
        f"status={snapshot.get('overall_status', '')}, "
        f"ETH closes={snapshot.get('eth_total_realized_closes', 0)}, "
        f"ETH opens={snapshot.get('eth_total_open_positions', 0)}, "
        "shapeshifter flips="
        f"{snapshot.get('shapeshifter_structure_flip_count_since_runner_start', 0)}, "
        f"shapeshifter proof={snapshot.get('shapeshifter_proof_status', '')}, "
        "shapeshifter phase1 readiness="
        f"{snapshot.get('shapeshifter_phase1_event_coverage_readiness', '')}, "
        "shapeshifter phase1 coverage="
        f"{snapshot.get('shapeshifter_phase1_event_covered_field_count', 0)}/"
        f"{snapshot.get('shapeshifter_phase1_event_field_count', 0)}, "
        "shapeshifter phase1 close_metrics="
        f"{snapshot.get('shapeshifter_phase1_close_metric_event_count', 0)}, "
        "shapeshifter first_path="
        f"{snapshot.get('shapeshifter_phase1_first_path_verdict', '')}, "
        "shapeshifter market_state="
        f"{snapshot.get('shapeshifter_phase1_market_state_hypothesis_verdict', '')}, "
        "shapeshifter loss_without_green="
        f"{snapshot.get('shapeshifter_phase1_loss_without_first_green_count', 0)}."
    )
    coverage_next_action = str(snapshot.get("shapeshifter_phase1_event_coverage_next_action") or "")
    if coverage_next_action:
        header += f" Coverage next_action={coverage_next_action}"
    if not changes:
        return header
    return header + " Changes: " + "; ".join(changes)


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
    refresh_surfaces()
    payload = load_json(BOARD_JSON)
    snapshot = snapshot_from_payload(payload)
    changes = diff_messages(previous_snapshot or {}, snapshot)
    if not quiet:
        print_snapshot(payload, snapshot)
        if changes:
            for change in changes:
                print(f"  ALERT change: {change}")
    write_monitor_state(snapshot)
    return payload, changes, snapshot


def main() -> int:
    parser = argparse.ArgumentParser(description="Watch the passive proof board for first ETH closes or shapeshifter proof transitions.")
    parser.add_argument("--watch", action="store_true", help="Poll repeatedly instead of running once.")
    parser.add_argument("--interval", type=int, default=30, help="Seconds between polls in --watch mode.")
    parser.add_argument("--until-proof", action="store_true", help="In --watch mode, exit when real proof arrives.")
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
                print(f"[{utc_now_text()}] experimental_proof_watch")
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
                print(f"[{utc_now_text()}] proof threshold reached: {snapshot.get('overall_status', '')}")
                return 0
            if not args.watch:
                return 0
            time.sleep(max(1, int(args.interval)))
    except KeyboardInterrupt:
        print(f"[{utc_now_text()}] experimental_proof_watch stopped")
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
