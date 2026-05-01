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
COMPARISON_SCRIPT = ROOT / "scripts" / "build_kraken_maker_ab_comparison_board.py"
GHOST_SCRIPT = ROOT / "scripts" / "build_kraken_maker_ab_ghost_giveback_board.py"
GATE_SCRIPT = ROOT / "scripts" / "build_kraken_maker_ab_promotion_gate.py"
NEXT_PROOF_SCRIPT = ROOT / "scripts" / "build_kraken_maker_next_proof_board.py"
SWITCHBOARD_CLI_SCRIPT = ROOT / "scripts" / "switchboard_cli.py"
NEXT_PROOF_JSON = REPORTS / "kraken_maker_next_proof_board.json"
STATE_JSON = REPORTS / "kraken_maker_next_proof_watch_state.json"


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


def refresh_surfaces() -> None:
    for script in (COMPARISON_SCRIPT, GHOST_SCRIPT, GATE_SCRIPT, NEXT_PROOF_SCRIPT):
        result = subprocess.run(
            [sys.executable, str(script)],
            cwd=ROOT,
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            raise RuntimeError(f"{script.name} failed: {(result.stderr or result.stdout).strip()}")


def lane_snapshot(payload: dict[str, Any], lane_name: str) -> dict[str, Any]:
    row = payload.get(lane_name) if isinstance(payload.get(lane_name), dict) else {}
    return {
        "lane": lane_name,
        "status": str(row.get("status") or ""),
        "next_action": str(row.get("next_action") or ""),
        "closes": to_int(row.get("closes")),
        "losses": to_int(row.get("losses")),
        "ghost_marks": to_int(row.get("ghost_marks")),
        "open_positions": to_int(row.get("open_positions")),
        "max_concurrent_positions": to_int(row.get("max_concurrent_positions")),
        "realized_net_usd": round(to_float(row.get("realized_net_usd")), 6),
        "closes_remaining": to_int(row.get("closes_remaining")),
        "ghost_marks_remaining": to_int(row.get("ghost_marks_remaining")),
        "gate_reasons": list(row.get("gate_reasons") or []),
    }


def snapshot_from_payload(payload: dict[str, Any]) -> dict[str, Any]:
    summary = payload.get("summary") if isinstance(payload.get("summary"), dict) else {}
    primary_lane = str(summary.get("primary_lane") or "")
    lanes = {
        "ratio50": lane_snapshot(payload, "ratio50"),
        "parallel_ratio50": lane_snapshot(payload, "parallel_ratio50"),
        "parallel_ratio50_taker_guard": lane_snapshot(payload, "parallel_ratio50_taker_guard"),
        "parallel_ratio50_taker_guard_live_exec": lane_snapshot(
            payload,
            "parallel_ratio50_taker_guard_live_exec",
        ),
        "parallel_ratio50_taker_guard_live_exec_dds25": lane_snapshot(
            payload,
            "parallel_ratio50_taker_guard_live_exec_dds25",
        ),
        "parallel_ratio50_taker_guard_live_exec_dds25_fixed": lane_snapshot(
            payload,
            "parallel_ratio50_taker_guard_live_exec_dds25_fixed",
        ),
        "parallel_ratio50_taker_guard_live_exec_dds25_fixed_texas_safe_epoch1": lane_snapshot(
            payload,
            "parallel_ratio50_taker_guard_live_exec_dds25_fixed_texas_safe_epoch1",
        ),
        "parallel_ratio50_taker_guard_live_exec_fast_cooldown": lane_snapshot(
            payload,
            "parallel_ratio50_taker_guard_live_exec_fast_cooldown",
        ),
    }
    primary = lanes.get(primary_lane) or lanes.get("parallel_ratio50_taker_guard") or {}
    return {
        "generated_at": str(payload.get("generated_at") or ""),
        "primary_lane": primary_lane,
        "primary_status": str(summary.get("primary_status") or ""),
        "next_action": str(summary.get("next_action") or ""),
        "read": str(summary.get("read") or ""),
        "blocked_lanes": list(summary.get("blocked_lanes") or []),
        "admitted_now": list(summary.get("admitted_now") or []),
        "reentry_blocked": list(summary.get("reentry_blocked") or []),
        "primary_closes": to_int(primary.get("closes")),
        "primary_losses": to_int(primary.get("losses")),
        "primary_ghost_marks": to_int(primary.get("ghost_marks")),
        "primary_open_positions": to_int(primary.get("open_positions")),
        "primary_max_concurrent_positions": to_int(primary.get("max_concurrent_positions")),
        "primary_realized_net_usd": round(to_float(primary.get("realized_net_usd")), 6),
        "primary_closes_remaining": to_int(primary.get("closes_remaining")),
        "primary_ghost_marks_remaining": to_int(primary.get("ghost_marks_remaining")),
        "primary_gate_reasons": list(primary.get("gate_reasons") or []),
    }


def diff_messages(previous: dict[str, Any], current: dict[str, Any]) -> list[str]:
    if not previous:
        return []
    messages: list[str] = []
    for key, label in (
        ("primary_lane", "primary lane"),
        ("primary_status", "primary status"),
        ("next_action", "next action"),
    ):
        if current.get(key) != previous.get(key):
            messages.append(f"{label} {previous.get(key, 'missing')} -> {current.get(key, 'missing')}")
    for key, label in (
        ("primary_closes", "closes"),
        ("primary_losses", "losses"),
        ("primary_ghost_marks", "ghost marks"),
        ("primary_open_positions", "open positions"),
    ):
        if to_int(current.get(key)) != to_int(previous.get(key)):
            messages.append(f"{label} {to_int(previous.get(key))} -> {to_int(current.get(key))}")
    previous_net = to_float(previous.get("primary_realized_net_usd"))
    current_net = to_float(current.get("primary_realized_net_usd"))
    if abs(current_net - previous_net) >= 0.000001:
        messages.append(f"net ${previous_net:.6f} -> ${current_net:.6f}")
    if current.get("primary_closes_remaining") != previous.get("primary_closes_remaining"):
        messages.append(
            "closes remaining "
            f"{to_int(previous.get('primary_closes_remaining'))} -> {to_int(current.get('primary_closes_remaining'))}"
        )
    if current.get("primary_ghost_marks_remaining") != previous.get("primary_ghost_marks_remaining"):
        messages.append(
            "ghost marks remaining "
            f"{to_int(previous.get('primary_ghost_marks_remaining'))} -> "
            f"{to_int(current.get('primary_ghost_marks_remaining'))}"
        )
    return messages


def reached_terminal_attention(snapshot: dict[str, Any]) -> bool:
    return str(snapshot.get("primary_status") or "") in {"failed_red_packet", "ready_for_next_shadow_stage"}


def write_monitor_state(snapshot: dict[str, Any]) -> None:
    payload = dict(snapshot)
    payload["checked_at"] = datetime.now(timezone.utc).isoformat()
    STATE_JSON.parent.mkdir(parents=True, exist_ok=True)
    STATE_JSON.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def print_snapshot(snapshot: dict[str, Any]) -> None:
    print(f"[{utc_now_text()}] kraken_maker_next_proof")
    print(
        "  primary: "
        f"{snapshot.get('primary_lane')} status={snapshot.get('primary_status')} "
        f"action={snapshot.get('next_action')}"
    )
    print(
        "  proof: "
        f"closes={snapshot.get('primary_closes')} "
        f"losses={snapshot.get('primary_losses')} "
        f"ghosts={snapshot.get('primary_ghost_marks')} "
        f"open={snapshot.get('primary_open_positions')} "
        f"net=${to_float(snapshot.get('primary_realized_net_usd')):.6f} "
        f"remaining={snapshot.get('primary_closes_remaining')}/"
        f"{snapshot.get('primary_ghost_marks_remaining')}"
    )
    print(f"  read: {snapshot.get('read', '')}")


def format_switchboard_message(changes: list[str], snapshot: dict[str, Any]) -> str:
    base = (
        "Kraken maker next-proof watcher: "
        f"primary={snapshot.get('primary_lane')} "
        f"status={snapshot.get('primary_status')} "
        f"closes={snapshot.get('primary_closes')} "
        f"losses={snapshot.get('primary_losses')} "
        f"ghosts={snapshot.get('primary_ghost_marks')} "
        f"open={snapshot.get('primary_open_positions')} "
        f"net=${to_float(snapshot.get('primary_realized_net_usd')):.6f} "
        f"remaining={snapshot.get('primary_closes_remaining')}/"
        f"{snapshot.get('primary_ghost_marks_remaining')}. "
        f"Next={snapshot.get('next_action')}."
    )
    if not changes:
        return base
    return base + " Changes: " + "; ".join(changes)


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


def run_once(*, previous_snapshot: dict[str, Any] | None = None, quiet: bool = False) -> tuple[list[str], dict[str, Any]]:
    refresh_surfaces()
    payload = load_json(NEXT_PROOF_JSON)
    snapshot = snapshot_from_payload(payload)
    changes = diff_messages(previous_snapshot or {}, snapshot)
    if not quiet:
        print_snapshot(snapshot)
        for change in changes:
            print(f"  ALERT change: {change}")
    write_monitor_state(snapshot)
    return changes, snapshot


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Watch Kraken maker next-proof authority for maturity or red packets.")
    parser.add_argument("--watch", action="store_true", help="Poll repeatedly instead of running once.")
    parser.add_argument("--interval", type=int, default=30, help="Seconds between polls in --watch mode.")
    parser.add_argument("--until-terminal", action="store_true", help="Exit once maturity or red-packet attention is reached.")
    parser.add_argument("--quiet", action="store_true", help="Suppress per-cycle summaries; still prints changes.")
    parser.add_argument("--notify-switchboard", action="store_true", help="Post change alerts to switchboard.")
    parser.add_argument("--switchboard-sender", default="", help="Sender token required with --notify-switchboard.")
    parser.add_argument("--switchboard-channel", default="general")
    parser.add_argument("--switchboard-message-type", default="status")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.notify_switchboard and not str(args.switchboard_sender or "").strip():
        raise SystemExit("--switchboard-sender is required with --notify-switchboard")
    previous_snapshot = load_json(STATE_JSON)
    try:
        while True:
            changes, snapshot = run_once(previous_snapshot=previous_snapshot, quiet=bool(args.quiet))
            if args.quiet and changes:
                print(f"[{utc_now_text()}] kraken_maker_next_proof")
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
            if args.until_terminal and reached_terminal_attention(snapshot):
                print(f"[{utc_now_text()}] terminal attention reached: {snapshot.get('primary_status')}")
                return 0
            if not args.watch:
                return 0
            time.sleep(max(1, int(args.interval)))
    except KeyboardInterrupt:
        print(f"[{utc_now_text()}] kraken_maker_next_proof watcher stopped")
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
