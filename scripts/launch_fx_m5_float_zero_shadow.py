#!/usr/bin/env python3
from __future__ import annotations

"""Register and optionally launch the honest FX M5 snake float-zero proof lanes.

These lanes forward-proof the current 5d M5 money-velocity leaders from the
`backtest_snake_counter_web.py` study using the dedicated
`live_snake_counter_web_shadow.py` runtime. They are not raw-family lattice
contracts.
"""

import argparse
import ctypes
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent.parent
REGISTRY_PATH = ROOT / "configs" / "penetration_lattice_runner_registry.json"
WATCHDOG_PATH = ROOT / "configs" / "watchdog_groups.json"
WATCHDOG_GROUP = "shadow_watchdog"

LANE_SPECS: dict[str, dict[str, Any]] = {
    "GBPUSD": {
        "lane_name": "shadow_gbpusd_m5_snake_float_zero_v1",
        "state_path": "reports/penetration_lattice_shadow_gbpusd_m5_snake_float_zero_v1_state.json",
        "event_path": "reports/penetration_lattice_shadow_gbpusd_m5_snake_float_zero_v1_events.jsonl",
        "step_pips": "0.5",
        "retrace_steps": "5",
        "hold_frontier": "0",
        "max_open_per_side": "64",
        "controller_mode": "static",
        "portfolio_close_mode": "float_zero",
        "variant_label": "snake_step0.5pip_retrace5_hold0_static_float_zero_cap64_rebase",
        "winner_booked_usd_per_hour": 4.316,
    },
    "EURUSD": {
        "lane_name": "shadow_eurusd_m5_snake_float_zero_v1",
        "state_path": "reports/penetration_lattice_shadow_eurusd_m5_snake_float_zero_v1_state.json",
        "event_path": "reports/penetration_lattice_shadow_eurusd_m5_snake_float_zero_v1_events.jsonl",
        "step_pips": "0.5",
        "retrace_steps": "6",
        "hold_frontier": "0",
        "max_open_per_side": "64",
        "controller_mode": "static",
        "portfolio_close_mode": "float_zero",
        "variant_label": "snake_step0.5pip_retrace6_hold0_static_float_zero_cap64_rebase",
        "winner_booked_usd_per_hour": 2.632,
    },
}


def build_lane_contract(symbol: str) -> dict[str, Any]:
    spec = LANE_SPECS[str(symbol).upper()]
    restart_args = [
        "scripts/live_snake_counter_web_shadow.py",
        "--symbol",
        str(symbol).upper(),
        "--timeframe",
        "M5",
        "--step-pips",
        spec["step_pips"],
        "--retrace-steps",
        spec["retrace_steps"],
        "--hold-frontier",
        spec["hold_frontier"],
        "--rebase-on-flat",
        "--max-open-per-side",
        spec["max_open_per_side"],
        "--controller-mode",
        spec["controller_mode"],
        "--portfolio-close-mode",
        spec["portfolio_close_mode"],
        "--variant-label",
        spec["variant_label"],
        "--state-path",
        spec["state_path"],
        "--event-path",
        spec["event_path"],
        "--poll-seconds",
        "5",
        "--shared-price-max-age-ms",
        "0",
    ]
    return {
        "name": spec["lane_name"],
        "kind": "shadow_fx",
        "symbol": str(symbol).upper(),
        "engine_family": "snake_counter_web_shadow",
        "state_path": spec["state_path"],
        "event_path": spec["event_path"],
        "poll_seconds": 5,
        "stale_after_seconds": 120,
        "process_match_substrings": [
            "scripts/live_snake_counter_web_shadow.py",
            spec["state_path"],
        ],
        "restart_args": restart_args,
        "contract_meta": {
            "study_variant_label": spec["variant_label"],
            "timeframe": "M5",
            "step_pips": float(spec["step_pips"]),
            "retrace_steps": int(spec["retrace_steps"]),
            "hold_frontier": int(spec["hold_frontier"]),
            "rebase_on_flat": True,
            "controller_mode": spec["controller_mode"],
            "portfolio_close_mode": spec["portfolio_close_mode"],
            "max_open_per_side": int(spec["max_open_per_side"]),
            "winner_booked_usd_per_hour": float(spec["winner_booked_usd_per_hour"]),
        },
    }


def load_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def upsert_registry_lane(registry: dict[str, Any], lane: dict[str, Any]) -> bool:
    lanes = list(registry.get("lanes") or [])
    registry["lanes"] = lanes
    lane_name = str(lane["name"])
    new_lane = {k: v for k, v in lane.items() if k != "contract_meta"}
    new_lane["enabled"] = True
    new_lane["pause_note"] = ""
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


def ensure_watchdog_membership(watchdog: dict[str, Any], lane_name: str) -> bool:
    changed = False
    groups = watchdog.setdefault("groups", {})
    group = groups.setdefault(WATCHDOG_GROUP, {"label": "Shadow", "lanes": []})
    group_lanes = list(group.get("lanes") or [])
    if lane_name not in group_lanes:
        group_lanes.append(lane_name)
        group["lanes"] = group_lanes
        changed = True

    legacy_group = watchdog.setdefault(WATCHDOG_GROUP, {"lanes": []})
    legacy_lanes = list(legacy_group.get("lanes") or [])
    if lane_name not in legacy_lanes:
        legacy_lanes.append(lane_name)
        legacy_group["lanes"] = legacy_lanes
        changed = True
    return changed


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
            state = json.loads(state_path.read_text(encoding="utf-8"))
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
    state_path = ROOT / str(lane["state_path"])
    existing_pid = find_running_pid(state_path)
    if existing_pid:
        return False, existing_pid
    popen_kwargs: dict[str, Any] = {"cwd": str(ROOT)}
    if os.name == "nt":
        popen_kwargs["creationflags"] = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
        popen_kwargs["close_fds"] = True
    proc = subprocess.Popen([sys.executable, *list(lane["restart_args"])], **popen_kwargs)
    return True, int(proc.pid)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="FX M5 snake float-zero shadow launcher")
    parser.add_argument("--symbol", choices=sorted(LANE_SPECS.keys()))
    parser.add_argument("--all", action="store_true", help="Apply to all supported symbols")
    parser.add_argument("--apply", action="store_true", help="Write or refresh registry/watchdog contracts")
    parser.add_argument("--launch", action="store_true", help="Launch the lane after apply")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if bool(args.all) == bool(args.symbol):
        raise SystemExit("Choose exactly one of --symbol or --all.")
    symbols = sorted(LANE_SPECS.keys()) if args.all else [str(args.symbol).upper()]
    results: list[dict[str, Any]] = []
    registry = load_json(REGISTRY_PATH) if args.apply else {}
    watchdog = load_json(WATCHDOG_PATH) if args.apply else {}

    for symbol in symbols:
        lane = build_lane_contract(symbol)
        registry_changed = False
        watchdog_changed = False
        launch_result: dict[str, Any] = {}

        if args.apply:
            registry_changed = upsert_registry_lane(registry, lane)
            watchdog_changed = ensure_watchdog_membership(watchdog, str(lane["name"]))

        if args.launch:
            if not args.apply:
                raise SystemExit("--launch requires --apply.")
            started, pid = launch_lane(lane)
            launch_result = {"started": started, "pid": pid}

        results.append(
            {
                "symbol": symbol,
                "lane_name": lane["name"],
                "registry_changed": registry_changed,
                "watchdog_changed": watchdog_changed,
                "launch": launch_result,
                "state_path": lane["state_path"],
                "event_path": lane["event_path"],
                "restart_args": lane["restart_args"],
            }
        )

    if args.apply:
        write_json(REGISTRY_PATH, registry)
        write_json(WATCHDOG_PATH, watchdog)

    print(json.dumps({"results": results}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
