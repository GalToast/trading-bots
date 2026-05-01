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
BOARD_SCRIPT = ROOT / "scripts" / "build_hungry_hippo_forward_shadow_watch_board.py"
SWITCHBOARD_CLI_SCRIPT = ROOT / "scripts" / "switchboard_cli.py"
BOARD_JSON = REPORTS / "hungry_hippo_forward_shadow_watch_board.json"
STATE_JSON = REPORTS / "hungry_hippo_forward_shadow_watch_monitor_state.json"


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
    result = subprocess.run(
        [sys.executable, str(BOARD_SCRIPT)],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(f"{BOARD_SCRIPT.name} failed: {(result.stderr or result.stdout).strip()}")


def snapshot_from_payload(payload: dict[str, Any]) -> dict[str, Any]:
    rows = [dict(row) for row in list(payload.get("rows") or [])]
    runtime_states = {str(row.get("symbol") or ""): str(row.get("runtime_state") or "") for row in rows if str(row.get("symbol") or "")}
    generalization_statuses = {
        str(row.get("symbol") or ""): str(row.get("generalization_status") or "") for row in rows if str(row.get("symbol") or "")
    }
    return {
        "watch_symbol_count": int((payload.get("summary") or {}).get("watch_symbol_count", 0) or 0),
        "watch_symbols": [str(row.get("symbol") or "") for row in rows if str(row.get("symbol") or "")],
        "runtime_states": runtime_states,
        "generalization_statuses": generalization_statuses,
        "not_launched_symbols": [str(item) for item in list((payload.get("summary") or {}).get("not_launched_symbols") or [])],
        "waiting_first_open_symbols": [
            str(item) for item in list((payload.get("summary") or {}).get("waiting_first_open_symbols") or [])
        ],
        "waiting_first_close_symbols": [
            str(item) for item in list((payload.get("summary") or {}).get("waiting_first_close_symbols") or [])
        ],
        "proof_started_symbols": [str(item) for item in list((payload.get("summary") or {}).get("proof_started_symbols") or [])],
        "stale_runtime_symbols": [str(item) for item in list((payload.get("summary") or {}).get("stale_runtime_symbols") or [])],
    }


def diff_messages(previous: dict[str, Any], current: dict[str, Any]) -> list[str]:
    if not previous:
        return []
    messages: list[str] = []
    if current.get("watch_symbols") != previous.get("watch_symbols"):
        messages.append(f"watch_symbols {previous.get('watch_symbols', [])} -> {current.get('watch_symbols', [])}")
    previous_states = dict(previous.get("runtime_states") or {})
    current_states = dict(current.get("runtime_states") or {})
    for symbol in sorted(set(previous_states) | set(current_states)):
        if current_states.get(symbol) != previous_states.get(symbol):
            messages.append(
                f"{symbol} runtime_state {previous_states.get(symbol, 'missing')} -> {current_states.get(symbol, 'missing')}"
            )
    previous_proof = list(previous.get("proof_started_symbols") or [])
    current_proof = list(current.get("proof_started_symbols") or [])
    if current_proof != previous_proof:
        messages.append(f"proof_started_symbols {previous_proof} -> {current_proof}")
    previous_stale = list(previous.get("stale_runtime_symbols") or [])
    current_stale = list(current.get("stale_runtime_symbols") or [])
    if current_stale != previous_stale:
        messages.append(f"stale_runtime_symbols {previous_stale} -> {current_stale}")
    return messages


def proof_arrived(snapshot: dict[str, Any]) -> bool:
    return bool(list(snapshot.get("proof_started_symbols") or []))


def write_monitor_state(snapshot: dict[str, Any]) -> None:
    payload = dict(snapshot)
    payload["checked_at"] = datetime.now(timezone.utc).isoformat()
    STATE_JSON.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def print_snapshot(payload: dict[str, Any], snapshot: dict[str, Any]) -> None:
    print(f"[{utc_now_text()}] hungry_hippo_forward_shadow_watch")
    print(f"  watch_symbols: {snapshot.get('watch_symbols', [])}")
    print(f"  proof_started_symbols: {snapshot.get('proof_started_symbols', [])}")
    print(f"  not_launched_symbols: {snapshot.get('not_launched_symbols', [])}")
    print(f"  waiting_first_open_symbols: {snapshot.get('waiting_first_open_symbols', [])}")
    print(f"  waiting_first_close_symbols: {snapshot.get('waiting_first_close_symbols', [])}")
    print(f"  stale_runtime_symbols: {snapshot.get('stale_runtime_symbols', [])}")
    print(f"  board_generated_at: {payload.get('generated_at', '')}")


def format_switchboard_message(changes: list[str], snapshot: dict[str, Any]) -> str:
    content = (
        "Hungry Hippo forward-watch alert: "
        f"watch_symbols={snapshot.get('watch_symbols', [])}, "
        f"proof_started={snapshot.get('proof_started_symbols', [])}, "
        f"not_launched={snapshot.get('not_launched_symbols', [])}, "
        f"waiting_first_open={snapshot.get('waiting_first_open_symbols', [])}, "
        f"waiting_first_close={snapshot.get('waiting_first_close_symbols', [])}, "
        f"stale_runtime={snapshot.get('stale_runtime_symbols', [])}."
    )
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
    parser = argparse.ArgumentParser(
        description="Watch the Hungry Hippo forward-shadow board for first-open / first-close proof changes."
    )
    parser.add_argument("--watch", action="store_true", help="Poll repeatedly instead of running once.")
    parser.add_argument("--interval", type=int, default=30, help="Seconds between polls in --watch mode.")
    parser.add_argument("--until-proof", action="store_true", help="In --watch mode, exit when first close-like proof arrives.")
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
                print(f"[{utc_now_text()}] hungry_hippo_forward_shadow_watch")
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
                print(f"[{utc_now_text()}] proof threshold reached: {snapshot.get('proof_started_symbols', [])}")
                return 0
            if not args.watch:
                return 0
            time.sleep(max(1, int(args.interval)))
    except KeyboardInterrupt:
        print(f"[{utc_now_text()}] hungry_hippo_forward_shadow_watch stopped")
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
