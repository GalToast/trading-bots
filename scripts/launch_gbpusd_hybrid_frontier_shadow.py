#!/usr/bin/env python3
from __future__ import annotations

"""Register and optionally launch the GBPUSD hybrid frontier shadow proof lane.

By default this prints the exact contract without mutating registry/watchdog or
launching anything. Use `--apply` to write the supervision contract and
`--launch` to start the lane after the config is updated.
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

LANE_NAME = "shadow_gbpusd_m15_hybrid_frontier_v1"
STATE_PATH = "reports/penetration_lattice_shadow_gbpusd_m15_hybrid_frontier_v1_state.json"
EVENT_PATH = "reports/penetration_lattice_shadow_gbpusd_m15_hybrid_frontier_v1_events.jsonl"

STEP_BUY = "0.000825"
STEP_SELL = "0.000412"
STEP_AVG = "0.000619"
MAX_OPEN_PER_SIDE = "15"
MAX_FLOATING_LOSS_USD = "-50.0"
MAX_ENTRY_SPREAD_RATIO = "0.3"
MAX_LATTICE_WINDOW_BARS = "240"


def build_lane_contract() -> dict[str, Any]:
    restart_args = [
        "scripts/live_penetration_lattice_tick_crypto_shadow.py",
        "--symbol",
        "GBPUSD",
        "--timeframe",
        "M15",
        "--step",
        STEP_AVG,
        "--step-buy",
        STEP_BUY,
        "--step-sell",
        STEP_SELL,
        "--proven-step-buy-ceiling",
        STEP_BUY,
        "--proven-step-sell-ceiling",
        STEP_SELL,
        "--max-open-per-side",
        MAX_OPEN_PER_SIDE,
        "--raw-close-alpha",
        "1.0",
        "--raw-close-style",
        "harvest_inner_hold_frontier",
        "--raw-rearm-variant",
        "rearm_lvl2_exc1",
        "--raw-rearm-cooldown-bars",
        "0",
        "--raw-sell-gap",
        "1",
        "--raw-buy-gap",
        "2",
        "--state-path",
        STATE_PATH,
        "--event-path",
        EVENT_PATH,
        "--poll-seconds",
        "30",
        "--shared-price-max-age-ms",
        "0",
        "--session-gate",
        "--max-floating-loss-usd",
        MAX_FLOATING_LOSS_USD,
        "--max-entry-spread-ratio",
        MAX_ENTRY_SPREAD_RATIO,
        "--max-lattice-window-bars",
        MAX_LATTICE_WINDOW_BARS,
        "--adaptive-overlay-autopilot",
    ]
    return {
        "name": LANE_NAME,
        "kind": "shadow_fx",
        "symbol": "GBPUSD",
        "engine_family": "raw",
        "state_path": STATE_PATH,
        "event_path": EVENT_PATH,
        "poll_seconds": 30,
        "stale_after_seconds": 120,
        "process_match_substrings": [
            "scripts/live_penetration_lattice_tick_crypto_shadow.py",
            STATE_PATH,
        ],
        "restart_args": restart_args,
        "contract_meta": {
            "study_variant_label": "harvest_inner_hold_frontier_step0.75_cap+3",
            "timeframe": "M15",
            "step_buy_price_units": float(STEP_BUY),
            "step_sell_price_units": float(STEP_SELL),
            "step_avg_price_units": float(STEP_AVG),
            "max_open_per_side": int(MAX_OPEN_PER_SIDE),
            "raw_close_style": "harvest_inner_hold_frontier",
            "raw_close_alpha": 1.0,
            "raw_sell_gap": 1,
            "raw_buy_gap": 2,
            "raw_rearm_variant": "rearm_lvl2_exc1",
            "max_floating_loss_usd": float(MAX_FLOATING_LOSS_USD),
            "max_entry_spread_ratio": float(MAX_ENTRY_SPREAD_RATIO),
            "session_gate": True,
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
    crypto_group = groups.setdefault("crypto_watchdog", {})
    crypto_lanes = list(crypto_group.get("lanes") or [])
    if lane_name not in crypto_lanes:
        crypto_lanes.append(lane_name)
        crypto_group["lanes"] = crypto_lanes
        changed = True

    legacy_group = watchdog.setdefault("crypto_watchdog", {})
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
        popen_kwargs["creationflags"] = (
            subprocess.DETACHED_PROCESS
            | subprocess.CREATE_NEW_PROCESS_GROUP
        )
        popen_kwargs["close_fds"] = True
    proc = subprocess.Popen([sys.executable, *list(lane["restart_args"])], **popen_kwargs)
    return True, int(proc.pid)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="GBP hybrid frontier shadow launcher")
    parser.add_argument("--apply", action="store_true", help="Write or refresh registry/watchdog contract")
    parser.add_argument("--launch", action="store_true", help="Launch the lane after apply")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    lane = build_lane_contract()
    registry_changed = False
    watchdog_changed = False
    launch_result: dict[str, Any] = {}

    if args.apply:
        registry = load_json(REGISTRY_PATH)
        watchdog = load_json(WATCHDOG_PATH)
        registry_changed = upsert_registry_lane(registry, lane)
        watchdog_changed = ensure_watchdog_membership(watchdog, str(lane["name"]))
        if registry_changed:
            write_json(REGISTRY_PATH, registry)
        if watchdog_changed:
            write_json(WATCHDOG_PATH, watchdog)

    if args.launch:
        if not args.apply:
            raise SystemExit("--launch requires --apply.")
        started, pid = launch_lane(lane)
        launch_result = {"started": started, "pid": pid}

    print(
        json.dumps(
            {
                "mode": "apply" if args.apply else "dry_run",
                "registry_changed": registry_changed,
                "watchdog_changed": watchdog_changed,
                "lane": lane,
                "launch": launch_result,
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
