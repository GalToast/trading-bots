#!/usr/bin/env python3
from __future__ import annotations

"""Register and optionally launch the FX M1 hybrid hedge live research lanes."""

import argparse
import ctypes
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

import MetaTrader5 as mt5

import live_penetration_lattice_mirror as live_mirror
import mt5_terminal_guard


ROOT = Path(__file__).resolve().parent.parent
REGISTRY_PATH = ROOT / "configs" / "penetration_lattice_runner_registry.json"
WATCHDOG_PATH = ROOT / "configs" / "watchdog_groups.json"
WATCHDOG_GROUP = "fx_watchdog"

LANE_SPECS: dict[str, dict[str, Any]] = {
    "GBPUSD": {
        "lane_name": "live_gbpusd_m1_snake_hybrid_941797",
        "live_magic": 941797,
        "state_path": "reports/live_gbpusd_m1_snake_hybrid_state.json",
        "event_path": "reports/live_gbpusd_m1_snake_hybrid_events.jsonl",
        "exec_state_path": "reports/live_gbpusd_m1_snake_hybrid_exec_state.json",
        "exec_log_path": "reports/live_gbpusd_m1_snake_hybrid_exec_events.jsonl",
        "step_pips": "0.1",
        "retrace_steps": "1",
        "hold_frontier": "0",
        "rebase_on_flat": True,
        "max_open_per_side": "16",
        "controller_mode": "static",
        "portfolio_close_mode": "float_zero",
        "hedge_mode": "depth_threshold",
        "hedge_trigger_depth": "4",
        "max_entry_spread_ratio": "0.00",
        "liquidity_gap_spread_multiplier": "2.5",
        "liquidity_gap_spread_lookback": "60",
        "liquidity_gap_spread_floor_ratio": "1.0",
        "min_harvest_profit_usd": "0.35",
        "variant_label": "snake_step0.1pip_retrace1_hold0_static_float_zero_hedgedepth_threshold4_cap16_rebase",
        "winner_booked_usd_per_hour": 3.35,
    },
    "EURUSD": {
        "lane_name": "live_eurusd_m1_snake_hybrid_941798",
        "live_magic": 941798,
        "state_path": "reports/live_eurusd_m1_snake_hybrid_state.json",
        "event_path": "reports/live_eurusd_m1_snake_hybrid_events.jsonl",
        "exec_state_path": "reports/live_eurusd_m1_snake_hybrid_exec_state.json",
        "exec_log_path": "reports/live_eurusd_m1_snake_hybrid_exec_events.jsonl",
        "step_pips": "0.1",
        "retrace_steps": "1",
        "hold_frontier": "0",
        "rebase_on_flat": False,
        "max_open_per_side": "16",
        "controller_mode": "static",
        "portfolio_close_mode": "float_zero",
        "hedge_mode": "same_level",
        "hedge_trigger_depth": "4",
        "max_entry_spread_ratio": "0.00",
        "liquidity_gap_spread_multiplier": "2.5",
        "liquidity_gap_spread_lookback": "60",
        "liquidity_gap_spread_floor_ratio": "1.0",
        "min_harvest_profit_usd": "0.20",
        "variant_label": "snake_step0.1pip_retrace1_hold0_static_float_zero_hedgesame_level_cap16_fixed",
        "winner_booked_usd_per_hour": 0.976,
    },
}


def _resolved_lane_runtime(spec: dict[str, Any], *, live_magic: int | None = None) -> dict[str, Any]:
    default_magic = int(spec["live_magic"])
    resolved_magic = int(default_magic if live_magic is None else live_magic)
    default_lane_name = str(spec["lane_name"])
    if resolved_magic == default_magic:
        return {
            "lane_name": default_lane_name,
            "state_path": str(spec["state_path"]),
            "event_path": str(spec["event_path"]),
            "exec_state_path": str(spec["exec_state_path"]),
            "exec_log_path": str(spec["exec_log_path"]),
            "live_magic": resolved_magic,
            "family_prefix": f"{default_lane_name.rsplit('_', 1)[0]}_",
        }
    lane_prefix = default_lane_name.rsplit("_", 1)[0]
    lane_name = f"{lane_prefix}_{resolved_magic}"
    return {
        "lane_name": lane_name,
        "state_path": f"reports/{lane_name}_state.json",
        "event_path": f"reports/{lane_name}_events.jsonl",
        "exec_state_path": f"reports/{lane_name}_exec_state.json",
        "exec_log_path": f"reports/{lane_name}_exec_events.jsonl",
        "live_magic": resolved_magic,
        "family_prefix": f"{lane_prefix}_",
    }


def build_lane_contract(symbol: str, live_magic: int | None = None) -> dict[str, Any]:
    spec = LANE_SPECS[str(symbol).upper()]
    runtime = _resolved_lane_runtime(spec, live_magic=live_magic)
    restart_args = [
        "scripts/live_snake_counter_web_shadow.py",
        "--symbol",
        str(symbol).upper(),
        "--timeframe",
        "M1",
        "--step-pips",
        spec["step_pips"],
        "--retrace-steps",
        spec["retrace_steps"],
        "--hold-frontier",
        spec["hold_frontier"],
        "--max-open-per-side",
        spec["max_open_per_side"],
        "--controller-mode",
        spec["controller_mode"],
        "--portfolio-close-mode",
        spec["portfolio_close_mode"],
        "--hedge-mode",
        spec["hedge_mode"],
        "--hedge-trigger-depth",
        spec["hedge_trigger_depth"],
        "--min-harvest-profit-usd",
        spec["min_harvest_profit_usd"],
        "--variant-label",
        spec["variant_label"],
        "--state-path",
        runtime["state_path"],
        "--event-path",
        runtime["event_path"],
        "--poll-seconds",
        "1",
        "--shared-price-max-age-ms",
        "0",
        "--max-entry-spread-ratio",
        spec["max_entry_spread_ratio"],
        "--liquidity-gap-spread-multiplier",
        spec["liquidity_gap_spread_multiplier"],
        "--liquidity-gap-spread-lookback",
        spec["liquidity_gap_spread_lookback"],
        "--liquidity-gap-spread-floor-ratio",
        spec["liquidity_gap_spread_floor_ratio"],
        "--require-live-admissibility",
        "--positive-only-closes",
        "--direct-live",
        "--live-magic",
        str(runtime["live_magic"]),
        "--live-comment-prefix",
        f"PSNAKEH-{str(symbol).upper()}",
        "--live-volume",
        "0.01",
        "--block-on-prestart-open-carry",
        "--direct-exec-state-path",
        runtime["exec_state_path"],
        "--direct-exec-log-path",
        runtime["exec_log_path"],
    ]
    if bool(spec.get("rebase_on_flat", True)):
        restart_args.insert(restart_args.index("--max-open-per-side"), "--rebase-on-flat")
    return {
        "name": runtime["lane_name"],
        "kind": "live_fx",
        "symbol": str(symbol).upper(),
        "engine_family": "snake_counter_web_live",
        "state_path": runtime["state_path"],
        "event_path": runtime["event_path"],
        "poll_seconds": 1,
        "stale_after_seconds": 30,
        "process_match_substrings": [
            "scripts/live_snake_counter_web_shadow.py",
            runtime["state_path"],
            str(runtime["live_magic"]),
        ],
        "restart_args": restart_args,
        "contract_meta": {
            "study_variant_label": spec["variant_label"],
            "timeframe": "M1",
            "step_pips": float(spec["step_pips"]),
            "retrace_steps": int(spec["retrace_steps"]),
            "hold_frontier": int(spec["hold_frontier"]),
            "rebase_on_flat": bool(spec.get("rebase_on_flat", True)),
            "controller_mode": spec["controller_mode"],
            "portfolio_close_mode": spec["portfolio_close_mode"],
            "hedge_mode": spec["hedge_mode"],
            "hedge_trigger_depth": int(spec["hedge_trigger_depth"]),
            "min_harvest_profit_usd": float(spec["min_harvest_profit_usd"]),
            "max_open_per_side": int(spec["max_open_per_side"]),
            "max_entry_spread_ratio": float(spec["max_entry_spread_ratio"]),
            "liquidity_gap_spread_multiplier": float(spec["liquidity_gap_spread_multiplier"]),
            "liquidity_gap_spread_lookback": int(spec["liquidity_gap_spread_lookback"]),
            "liquidity_gap_spread_floor_ratio": float(spec["liquidity_gap_spread_floor_ratio"]),
            "require_live_admissibility": True,
            "positive_only_closes": True,
            "winner_booked_usd_per_hour": float(spec["winner_booked_usd_per_hour"]),
            "direct_live": True,
            "live_magic": int(runtime["live_magic"]),
            "live_volume": 0.01,
            "block_on_prestart_open_carry": True,
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
    group = groups.setdefault(WATCHDOG_GROUP, {"label": "FX", "lanes": []})
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


def deactivate_other_family_rows(
    registry: dict[str, Any],
    watchdog: dict[str, Any],
    *,
    family_prefix: str,
    keep_lane_name: str,
) -> bool:
    changed = False
    lanes = list(registry.get("lanes") or [])
    for row in lanes:
        name = str(row.get("name") or "")
        if not name.startswith(str(family_prefix)):
            continue
        if name == str(keep_lane_name):
            continue
        if bool(row.get("enabled", True)) or str(row.get("pause_note") or "") != f"superseded_by_{keep_lane_name}":
            row["enabled"] = False
            row["pause_note"] = f"superseded_by_{keep_lane_name}"
            changed = True
    groups = watchdog.get("groups") or {}
    for group in groups.values():
        if not isinstance(group, dict):
            continue
        group_lanes = list(group.get("lanes") or [])
        filtered = [lane_name for lane_name in group_lanes if not (str(lane_name).startswith(str(family_prefix)) and str(lane_name) != str(keep_lane_name))]
        if filtered != group_lanes:
            group["lanes"] = filtered
            changed = True
    legacy_group = watchdog.get(WATCHDOG_GROUP)
    if isinstance(legacy_group, dict):
        legacy_lanes = list(legacy_group.get("lanes") or [])
        filtered = [lane_name for lane_name in legacy_lanes if not (str(lane_name).startswith(str(family_prefix)) and str(lane_name) != str(keep_lane_name))]
        if filtered != legacy_lanes:
            legacy_group["lanes"] = filtered
            changed = True
    return changed


def lane_live_magic(lane_row: dict[str, Any]) -> int:
    for index, arg in enumerate(list(lane_row.get("restart_args") or [])):
        if str(arg) == "--live-magic":
            try:
                return int((lane_row.get("restart_args") or [])[index + 1])
            except Exception:
                return 0
    return 0


def ensure_cutover_rows_are_broker_flat(
    registry: dict[str, Any],
    *,
    family_prefix: str,
    keep_lane_name: str,
) -> None:
    mt5_ready, payload = mt5_terminal_guard.initialize_mt5(require_trade_allowed=False)
    if not mt5_ready:
        raise RuntimeError(mt5_terminal_guard.failure_summary(payload))
    try:
        for row in list(registry.get("lanes") or []):
            name = str(row.get("name") or "")
            if not name.startswith(str(family_prefix)) or name == str(keep_lane_name):
                continue
            live_magic = lane_live_magic(row)
            if live_magic <= 0:
                continue
            positions = live_mirror.broker_live_positions(live_magic=live_magic)
            if positions:
                raise RuntimeError(
                    f"Cannot cut over while superseded lane {name} still has {len(positions)} broker positions under magic {live_magic}."
                )
    finally:
        mt5.shutdown()


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
    state_path_token = str(state_path).replace("/", "\\").lower()
    for row in rows:
        command_line = str(row.get("CommandLine") or "").lower()
        if state_path_token not in command_line:
            continue
        pid = int(row.get("ProcessId") or 0)
        if pid and is_process_alive(pid):
            return pid
    return None


def backup_state_file(state_path: Path) -> Path | None:
    if not state_path.exists():
        return None
    from datetime import datetime, timezone

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup_path = state_path.with_name(f"{state_path.stem}.{stamp}.bak{state_path.suffix}")
    shutil.copy2(state_path, backup_path)
    return backup_path


def terminate_process(pid: int, *, timeout_seconds: float = 10.0) -> None:
    kernel32 = ctypes.windll.kernel32
    handle = kernel32.OpenProcess(0x0001 | 0x00100000, False, int(pid))
    if handle == 0:
        raise RuntimeError(f"Unable to open process {pid} for termination.")
    try:
        if kernel32.TerminateProcess(handle, 1) == 0:
            raise RuntimeError(f"TerminateProcess failed for pid {pid}.")
        wait_result = kernel32.WaitForSingleObject(handle, int(max(1.0, float(timeout_seconds)) * 1000))
        if wait_result == 0x00000102:
            raise RuntimeError(f"Timed out waiting for pid {pid} to exit.")
    finally:
        kernel32.CloseHandle(handle)


def launch_lane(
    lane: dict[str, Any],
    *,
    recycle: bool = False,
    fresh_start: bool = False,
) -> tuple[bool, int | None, int | None]:
    state_path = ROOT / str(lane["state_path"])
    existing_pid = find_running_pid(state_path)
    if existing_pid:
        if not recycle:
            return False, existing_pid, None
        backup_state_file(state_path)
        terminate_process(existing_pid)
    popen_kwargs: dict[str, Any] = {"cwd": str(ROOT)}
    if os.name == "nt":
        popen_kwargs["creationflags"] = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
        popen_kwargs["close_fds"] = True
    command = [sys.executable, *list(lane["restart_args"])]
    if fresh_start:
        command.append("--fresh-start")
    proc = subprocess.Popen(command, **popen_kwargs)
    return True, int(proc.pid), existing_pid


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="FX M1 hybrid hedge live launcher")
    parser.add_argument("--symbol", choices=sorted(LANE_SPECS.keys()))
    parser.add_argument("--all", action="store_true", help="Apply to all supported symbols")
    parser.add_argument("--apply", action="store_true", help="Write or refresh registry/watchdog contracts")
    parser.add_argument("--launch", action="store_true", help="Launch the lane after apply")
    parser.add_argument("--recycle", action="store_true", help="If the lane is already running, terminate it and relaunch from current state.")
    parser.add_argument("--fresh-start", action="store_true", help="Launch the seat with empty runner state; broker positions under the same magic may still rehydrate.")
    parser.add_argument("--live-magic", type=int, default=None, help="Override the pinned live magic for a single-symbol seat.")
    parser.add_argument("--cutover", action="store_true", help="When used with --live-magic, pause other same-family registry rows for that symbol and keep only the override row active.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if bool(args.all) == bool(args.symbol):
        raise SystemExit("Choose exactly one of --symbol or --all.")
    if args.live_magic is not None and (args.all or not args.symbol):
        raise SystemExit("--live-magic may only be used with exactly one --symbol.")
    if args.cutover and args.live_magic is None:
        raise SystemExit("--cutover requires --live-magic.")
    symbols = sorted(LANE_SPECS.keys()) if args.all else [str(args.symbol).upper()]
    results: list[dict[str, Any]] = []
    registry = load_json(REGISTRY_PATH) if args.apply else {}
    watchdog = load_json(WATCHDOG_PATH) if args.apply else {}

    for symbol in symbols:
        lane = build_lane_contract(symbol, live_magic=args.live_magic)
        registry_changed = False
        watchdog_changed = False
        launch_result: dict[str, Any] = {}

        if args.apply:
            if args.cutover:
                runtime = _resolved_lane_runtime(LANE_SPECS[symbol], live_magic=args.live_magic)
                ensure_cutover_rows_are_broker_flat(
                    registry,
                    family_prefix=str(runtime["family_prefix"]),
                    keep_lane_name=str(lane["name"]),
                )
                registry_changed = deactivate_other_family_rows(
                    registry,
                    watchdog,
                    family_prefix=str(runtime["family_prefix"]),
                    keep_lane_name=str(lane["name"]),
                ) or registry_changed
            registry_changed = upsert_registry_lane(registry, lane)
            watchdog_changed = ensure_watchdog_membership(watchdog, str(lane["name"]))

        if args.launch:
            if not args.apply:
                raise SystemExit("--launch requires --apply.")
            started, pid, recycled_from_pid = launch_lane(
                lane,
                recycle=bool(args.recycle),
                fresh_start=bool(args.fresh_start),
            )
            launch_result = {"started": started, "pid": pid}
            if recycled_from_pid:
                launch_result["recycled_from_pid"] = recycled_from_pid
            if args.fresh_start:
                launch_result["fresh_start"] = True

        results.append(
            {
                "symbol": symbol,
                "lane_name": lane["name"],
                "registry_changed": registry_changed,
                "watchdog_changed": watchdog_changed,
                "launch": launch_result,
                "restart_args": lane["restart_args"],
            }
        )

    if args.apply:
        write_json(REGISTRY_PATH, registry)
        write_json(WATCHDOG_PATH, watchdog)

    print(json.dumps(results, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
