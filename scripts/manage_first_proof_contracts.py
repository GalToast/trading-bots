#!/usr/bin/env python3
from __future__ import annotations

"""Manage parked first-proof launch contracts for symbols without a live seat.

This bridges the passive max-profit queue packet with the Hungry Hippo first-proof
launch packet. By default it prints the selected contracts without mutating the
registry or launching anything. Use `--apply` to register/update the parked lane
contracts, `--enable` to clear the parked flag, and `--launch` to start only
rows the launch packet currently marks as launchable.
"""

import argparse
import ctypes
import json
import subprocess
import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent.parent
REPORTS = ROOT / "reports"
REGISTRY_PATH = ROOT / "configs" / "penetration_lattice_runner_registry.json"
WATCHDOG_PATH = ROOT / "configs" / "watchdog_groups.json"
QUEUE_PACKET_PATH = REPORTS / "max_profit_queue_contract_packet.json"
FIRST_PROOF_PACKET_PATH = REPORTS / "hungry_hippo_first_proof_launch_packet_board.json"

LAUNCHABLE_READINESS = {"launch_now", "already_started"}


def load_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def normalize_symbol(value: Any) -> str:
    return str(value or "").upper().strip()


def normalize_repo_path(path_text: str) -> Path:
    return ROOT / Path(str(path_text or "").replace("\\", "/"))


def find_symbol(rows: list[dict[str, Any]], symbol: str) -> dict[str, Any] | None:
    clean_symbol = normalize_symbol(symbol)
    for row in rows:
        if normalize_symbol(row.get("symbol")) == clean_symbol:
            return dict(row)
    return None


def resolve_symbols(args: argparse.Namespace, queue_payload: dict[str, Any]) -> list[str]:
    if args.all_ready:
        symbols = [
            normalize_symbol(row.get("symbol"))
            for row in list(queue_payload.get("rows") or [])
            if normalize_symbol(row.get("symbol"))
            and str(row.get("proposal_status") or "") == "proposal_ready_for_launch_contract"
            and str(row.get("next_action_class") or "") == "formalize_first_seat_proof_contract"
        ]
        if not symbols:
            raise SystemExit("No `proposal_ready_for_launch_contract` first-proof rows are currently available.")
        return symbols
    selected = [normalize_symbol(symbol) for symbol in list(args.symbol or []) if normalize_symbol(symbol)]
    if not selected:
        raise SystemExit("Select at least one --symbol or pass --all-ready.")
    return selected


def build_contract(
    symbol: str,
    *,
    queue_payload: dict[str, Any],
    first_proof_payload: dict[str, Any],
) -> dict[str, Any]:
    queue_row = find_symbol(list(queue_payload.get("rows") or []), symbol) or {}
    proof_row = find_symbol(list(first_proof_payload.get("rows") or []), symbol)
    if proof_row is None:
        raise SystemExit(f"No first-proof launch packet row found for {symbol}.")
    config_path_text = str(proof_row.get("config_path") or "")
    if not config_path_text:
        raise SystemExit(f"First-proof packet row for {symbol} is missing config_path.")
    config_path = normalize_repo_path(config_path_text)
    if not config_path.exists():
        raise SystemExit(f"Config path for {symbol} does not exist: {config_path}")
    lane = load_json(config_path)
    lane_name = str(lane.get("name") or "")
    if not lane_name:
        raise SystemExit(f"Config for {symbol} is missing lane name: {config_path}")
    restart_args = list(lane.get("restart_args") or [])
    if not restart_args:
        raise SystemExit(f"Config for {symbol} is missing restart_args: {config_path}")
    watchdog_group = str(lane.get("watchdog_group") or proof_row.get("watchdog_group") or "")
    if not watchdog_group:
        raise SystemExit(f"Config for {symbol} is missing watchdog_group: {config_path}")
    return {
        "symbol": normalize_symbol(symbol),
        "task_id": str(queue_row.get("task_id") or ""),
        "title": str(queue_row.get("title") or ""),
        "proposal_status": str(queue_row.get("proposal_status") or ""),
        "next_action_class": str(queue_row.get("next_action_class") or ""),
        "proposal_read": str(queue_row.get("proposal_read") or ""),
        "packet_role": str(proof_row.get("packet_role") or ""),
        "launch_readiness": str(proof_row.get("launch_readiness") or ""),
        "runtime_state": str(proof_row.get("runtime_state") or ""),
        "rollout_blocker": str(proof_row.get("rollout_blocker") or ""),
        "next_action": str(proof_row.get("next_action") or ""),
        "validation_verdict": str(proof_row.get("validation_verdict") or ""),
        "config_path": str(config_path.relative_to(ROOT)).replace("/", "\\"),
        "watchdog_group": watchdog_group,
        "lane": lane,
    }


def upsert_registry_lane(registry: dict[str, Any], lane: dict[str, Any], *, enabled_override: bool | None) -> bool:
    lanes = list(registry.get("lanes") or [])
    registry["lanes"] = lanes
    lane_name = str(lane.get("name") or "")
    new_lane = dict(lane)
    if enabled_override is not None:
        new_lane["enabled"] = bool(enabled_override)
        if enabled_override:
            new_lane["pause_note"] = ""
        else:
            new_lane["pause_note"] = str(new_lane.get("pause_note") or "parked_by_first_proof_contract_manager")
    for index, existing in enumerate(lanes):
        if str(existing.get("name") or "") != lane_name:
            continue
        merged = dict(existing)
        merged.update(new_lane)
        if merged != existing:
            lanes[index] = merged
            return True
        return False
    lanes.append(new_lane)
    return True


def ensure_watchdog_membership(watchdog: dict[str, Any], group_name: str, lane_name: str) -> bool:
    groups = watchdog.setdefault("groups", {})
    group = groups.setdefault(group_name, {"label": group_name.replace("_", " ").title(), "lanes": []})
    lanes = list(group.get("lanes") or [])
    if lane_name in lanes:
        return False
    lanes.append(lane_name)
    group["lanes"] = lanes
    return True


def blocked_launch_symbols(contracts: list[dict[str, Any]]) -> list[str]:
    blocked: list[str] = []
    for contract in contracts:
        if str(contract.get("launch_readiness") or "") not in LAUNCHABLE_READINESS:
            blocked.append(str(contract.get("symbol") or ""))
    return blocked


def is_process_alive(pid: int) -> bool:
    kernel32 = ctypes.windll.kernel32
    handle = kernel32.OpenProcess(0x0400 | 0x00100000, False, pid)
    if handle == 0:
        return False
    kernel32.CloseHandle(handle)
    return True


def find_running_pid(state_path: Path) -> int | None:
    if state_path.exists():
        try:
            state = load_json(state_path)
        except Exception:
            state = {}
        pid = int(state.get("runner", {}).get("pid") or 0)
        if pid and is_process_alive(pid):
            return pid

    state_path_token = str(state_path).replace("/", "\\").lower()
    command = (
        "Get-CimInstance Win32_Process -Filter \"name='python.exe'\" | "
        "Select-Object ProcessId, CommandLine | ConvertTo-Json -Compress"
    )
    result = subprocess.run(
        ["powershell", "-NoProfile", "-Command", command],
        capture_output=True,
        text=True,
        timeout=20,
        encoding="utf-8",
    )
    if result.returncode != 0 or not result.stdout.strip():
        return None
    payload = json.loads(result.stdout)
    rows = payload if isinstance(payload, list) else [payload]
    for row in rows:
        command_line = str(row.get("CommandLine") or "").lower()
        if state_path_token not in command_line:
            continue
        pid = int(row.get("ProcessId") or 0)
        if pid and is_process_alive(pid):
            return pid
    return None


def launch_lane(lane: dict[str, Any]) -> tuple[bool, int | None]:
    state_path = normalize_repo_path(str(lane.get("state_path") or ""))
    existing_pid = find_running_pid(state_path)
    if existing_pid:
        return False, existing_pid
    proc = subprocess.Popen([sys.executable, *list(lane.get("restart_args") or [])], cwd=str(ROOT))
    return True, int(proc.pid)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Manage parked first-proof launch contracts")
    parser.add_argument("--symbol", action="append", default=[], help="Target symbol from the first-proof packet (repeatable)")
    parser.add_argument("--all-ready", action="store_true", help="Use all queue rows currently ready for first-proof launch contracts")
    parser.add_argument("--apply", action="store_true", help="Write the selected contracts into registry/watchdog")
    parser.add_argument("--enable", action="store_true", help="Override contract enabled=true when applying")
    parser.add_argument("--disable", action="store_true", help="Override contract enabled=false when applying")
    parser.add_argument("--launch", action="store_true", help="Launch the selected lane(s) after apply")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.enable and args.disable:
        raise SystemExit("--enable and --disable are mutually exclusive.")
    if args.launch and not args.apply:
        raise SystemExit("--launch requires --apply.")
    if args.launch and args.disable:
        raise SystemExit("--launch cannot be combined with --disable.")

    queue_payload = load_json(QUEUE_PACKET_PATH)
    first_proof_payload = load_json(FIRST_PROOF_PACKET_PATH)
    symbols = resolve_symbols(args, queue_payload)
    contracts = [
        build_contract(symbol, queue_payload=queue_payload, first_proof_payload=first_proof_payload)
        for symbol in symbols
    ]

    registry_changed = False
    watchdog_changed = False
    enabled_override = True if args.enable else (False if args.disable else None)
    if args.apply:
        registry = load_json(REGISTRY_PATH)
        watchdog = load_json(WATCHDOG_PATH)
        for contract in contracts:
            lane = dict(contract["lane"])
            registry_changed = upsert_registry_lane(registry, lane, enabled_override=enabled_override) or registry_changed
            watchdog_changed = ensure_watchdog_membership(
                watchdog,
                str(contract["watchdog_group"]),
                str(lane.get("name") or ""),
            ) or watchdog_changed
        if registry_changed:
            write_json(REGISTRY_PATH, registry)
        if watchdog_changed:
            write_json(WATCHDOG_PATH, watchdog)

    launch_rows: list[dict[str, Any]] = []
    if args.launch:
        blocked = blocked_launch_symbols(contracts)
        if blocked:
            raise SystemExit(
                "Launch blocked by first-proof packet readiness for: "
                + ", ".join(blocked)
                + ". Apply/register is allowed, but launch must respect the rollout packet."
            )
        for contract in contracts:
            started, pid = launch_lane(dict(contract["lane"]))
            launch_rows.append({"symbol": contract["symbol"], "started": started, "pid": pid})

    payload = {
        "mode": "apply" if args.apply else "dry_run",
        "registry_changed": registry_changed,
        "watchdog_changed": watchdog_changed,
        "contracts": [
            {
                key: value
                for key, value in contract.items()
                if key != "lane"
            }
            | {
                "lane_name": str(contract["lane"].get("name") or ""),
                "lane_enabled": bool(contract["lane"].get("enabled")),
                "lane_watchdog_group": str(contract["lane"].get("watchdog_group") or ""),
                "state_path": str(contract["lane"].get("state_path") or ""),
                "event_path": str(contract["lane"].get("event_path") or ""),
            }
            for contract in contracts
        ],
        "launch": launch_rows,
    }
    print(json.dumps(payload, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
