#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
import os
import shlex
import traceback
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import MetaTrader5 as mt5
import mt5_terminal_guard
try:
    import psutil
except ImportError:  # pragma: no cover - optional dependency fallback
    psutil = None

from clean_forward_baselines import load_reset_baselines, record_reset_baseline
from supervision_policy import restart_policy
from process_lifecycle import (
    load_process_tracker,
    save_process_tracker,
    record_lane_launch,
    remove_lane,
    sweep_lane_processes,
    reconcile_on_startup,
    stop_process_forceful,
)


ROOT = Path(__file__).resolve().parent.parent
DEFAULT_REGISTRY = ROOT / "configs" / "penetration_lattice_runner_registry.json"
DEFAULT_REPORT_JSON = ROOT / "reports" / "penetration_lattice_runner_watchdog.json"
DEFAULT_REPORT_MD = ROOT / "reports" / "penetration_lattice_runner_watchdog.md"
DEFAULT_EVENTS_JSONL = ROOT / "reports" / "penetration_lattice_runner_watchdog_events.jsonl"
DEFAULT_LOG_DIR = ROOT / "reports" / "watchdog"
DEFAULT_LOOP_STATE_JSON = DEFAULT_LOG_DIR / "watchdog_loop_state.json"
DEFAULT_SCOREBOARD_SCRIPT = ROOT / "scripts" / "build_penetration_lane_scoreboard.py"
DEFAULT_SCOREBOARD_CSV = ROOT / "reports" / "penetration_lattice_lane_scoreboard.csv"
DEFAULT_SCOREBOARD_MD = ROOT / "reports" / "penetration_lattice_lane_scoreboard.md"
DEFAULT_COINBASE_RSI_FORWARD_SCRIPT = ROOT / "scripts" / "build_coinbase_spot_rsi_forward_review.py"
DEFAULT_COINBASE_RSI_FORWARD_CSV = ROOT / "reports" / "coinbase_spot_rsi_forward_review.csv"
DEFAULT_COINBASE_RATIO_FORWARD_SCRIPT = ROOT / "scripts" / "build_coinbase_ratio_forward_review.py"
DEFAULT_COINBASE_RATIO_FORWARD_CSV = ROOT / "reports" / "coinbase_ratio_forward_review.csv"
DEFAULT_COINBASE_RATIO_PROOF_READINESS_SCRIPT = ROOT / "scripts" / "build_coinbase_ratio_proof_readiness.py"
DEFAULT_COINBASE_RATIO_PROOF_READINESS_CSV = ROOT / "reports" / "coinbase_ratio_proof_readiness.csv"
DEFAULT_FX_GRADUATION_READINESS_SCRIPT = ROOT / "scripts" / "build_fx_graduation_readiness.py"
DEFAULT_FX_GRADUATION_READINESS_JSON = ROOT / "reports" / "fx_graduation_readiness.json"
DEFAULT_ETH_M15_WARP_READINESS_SCRIPT = ROOT / "scripts" / "build_eth_m15_warp_readiness.py"
DEFAULT_ETH_M15_WARP_READINESS_JSON = ROOT / "reports" / "eth_m15_warp_readiness.json"
DEFAULT_CRYPTO_M15_WARP_READINESS_SCRIPT = ROOT / "scripts" / "build_crypto_m15_warp_readiness.py"
DEFAULT_CRYPTO_M15_WARP_READINESS_JSON = ROOT / "reports" / "crypto_m15_warp_readiness.json"
DEFAULT_COINBASE_BURST_SCOREBOARD_SCRIPT = ROOT / "scripts" / "build_coinbase_burst_shadow_scoreboard.py"
DEFAULT_COINBASE_BURST_SCOREBOARD_CSV = ROOT / "reports" / "coinbase_burst_shadow_scoreboard.csv"
DEFAULT_COINBASE_BURST_FORWARD_SCRIPT = ROOT / "scripts" / "build_coinbase_burst_forward_review.py"
DEFAULT_COINBASE_BURST_FORWARD_CSV = ROOT / "reports" / "coinbase_burst_forward_review.csv"
DEFAULT_COINBASE_EXPERIMENTAL_SCOREBOARD_SCRIPT = ROOT / "scripts" / "build_coinbase_experimental_shadow_scoreboard.py"
DEFAULT_COINBASE_EXPERIMENTAL_SCOREBOARD_CSV = ROOT / "reports" / "coinbase_experimental_shadow_scoreboard.csv"
DEFAULT_COINBASE_EXPERIMENTAL_FORWARD_SCRIPT = ROOT / "scripts" / "build_coinbase_experimental_forward_review.py"
DEFAULT_COINBASE_EXPERIMENTAL_FORWARD_CSV = ROOT / "reports" / "coinbase_experimental_forward_review.csv"
DEFAULT_BTCUSD_H1_STEP_FORWARD_SCRIPT = ROOT / "scripts" / "build_btcusd_h1_step_forward_review.py"
DEFAULT_BTCUSD_H1_STEP_FORWARD_CSV = ROOT / "reports" / "btcusd_h1_step_forward_review.csv"
DEFAULT_BTCUSD_H1_STEP_READINESS_SCRIPT = ROOT / "scripts" / "build_btcusd_h1_step_readiness_board.py"
DEFAULT_BTCUSD_H1_STEP_READINESS_JSON = ROOT / "reports" / "btcusd_h1_step_readiness_board.json"
DEFAULT_EXECUTION_MONITOR_SCRIPT = ROOT / "scripts" / "build_execution_monitor_report.py"
DEFAULT_EXECUTION_MONITOR_JSON = ROOT / "reports" / "execution_monitor_report.json"
DEFAULT_SHARED_PRICE_FEEDER_STATUS_SCRIPT = ROOT / "scripts" / "build_shared_price_feeder_status.py"
DEFAULT_SHARED_PRICE_FEEDER_STATUS_JSON = ROOT / "reports" / "shared_price_feeder_status.json"
DEFAULT_SHARED_PRICE_FEEDER_STATUS_MD = ROOT / "reports" / "shared_price_feeder_status.md"
REFRESH_LOCK_DIR = DEFAULT_LOG_DIR / "refresh_locks"
LOOP_LOCK_DIR = DEFAULT_LOG_DIR / "loop_locks"
RUNTIME_ARG_DRIFT_VALUE_FLAGS = {
    "--config",
    "--event-path",
    "--live-comment-prefix",
    "--live-magic",
    "--live-volume",
    "--max-open-per-side",
    "--poll-seconds",
    "--raw-buy-gap",
    "--raw-close-alpha",
    "--raw-rearm-variant",
    "--raw-sell-gap",
    "--shared-price-max-age-ms",
    "--shared-price-path",
    "--state-dir",
    "--state-path",
    "--step",
    "--symbol",
    "--timeframe",
    "--trade-window-json",
}
RUNTIME_ARG_DRIFT_BOOL_FLAGS = {
    "--direct-live",
}
RUNTIME_ARG_DRIFT_IGNORED_FLAGS = {
    "--fresh-start",
}
RUNTIME_ARG_DRIFT_PATH_FLAGS = {
    "--config",
    "--event-path",
    "--shared-price-path",
    "--state-dir",
    "--state-path",
    "--trade-window-json",
}

REFRESH_POLICY_BY_SCRIPT: dict[str, dict[str, Any]] = {
    DEFAULT_SCOREBOARD_SCRIPT.name: {
        "outputs": (DEFAULT_SCOREBOARD_CSV, DEFAULT_SCOREBOARD_MD),
        "min_age_seconds": 120.0,
    },
    DEFAULT_COINBASE_RSI_FORWARD_SCRIPT.name: {
        "outputs": (DEFAULT_COINBASE_RSI_FORWARD_CSV,),
        "min_age_seconds": 180.0,
    },
    DEFAULT_COINBASE_RATIO_FORWARD_SCRIPT.name: {
        "outputs": (DEFAULT_COINBASE_RATIO_FORWARD_CSV,),
        "min_age_seconds": 180.0,
    },
    DEFAULT_COINBASE_RATIO_PROOF_READINESS_SCRIPT.name: {
        "outputs": (DEFAULT_COINBASE_RATIO_PROOF_READINESS_CSV,),
        "min_age_seconds": 180.0,
    },
    DEFAULT_FX_GRADUATION_READINESS_SCRIPT.name: {
        "outputs": (DEFAULT_FX_GRADUATION_READINESS_JSON,),
        "min_age_seconds": 180.0,
    },
    DEFAULT_ETH_M15_WARP_READINESS_SCRIPT.name: {
        "outputs": (DEFAULT_ETH_M15_WARP_READINESS_JSON,),
        "min_age_seconds": 180.0,
    },
    DEFAULT_CRYPTO_M15_WARP_READINESS_SCRIPT.name: {
        "outputs": (DEFAULT_CRYPTO_M15_WARP_READINESS_JSON,),
        "min_age_seconds": 180.0,
    },
    DEFAULT_COINBASE_BURST_SCOREBOARD_SCRIPT.name: {
        "outputs": (DEFAULT_COINBASE_BURST_SCOREBOARD_CSV,),
        "min_age_seconds": 180.0,
    },
    DEFAULT_COINBASE_BURST_FORWARD_SCRIPT.name: {
        "outputs": (DEFAULT_COINBASE_BURST_FORWARD_CSV,),
        "min_age_seconds": 180.0,
    },
    DEFAULT_COINBASE_EXPERIMENTAL_SCOREBOARD_SCRIPT.name: {
        "outputs": (DEFAULT_COINBASE_EXPERIMENTAL_SCOREBOARD_CSV,),
        "min_age_seconds": 180.0,
    },
    DEFAULT_COINBASE_EXPERIMENTAL_FORWARD_SCRIPT.name: {
        "outputs": (DEFAULT_COINBASE_EXPERIMENTAL_FORWARD_CSV,),
        "min_age_seconds": 180.0,
    },
    DEFAULT_BTCUSD_H1_STEP_FORWARD_SCRIPT.name: {
        "outputs": (DEFAULT_BTCUSD_H1_STEP_FORWARD_CSV,),
        "min_age_seconds": 180.0,
    },
    DEFAULT_BTCUSD_H1_STEP_READINESS_SCRIPT.name: {
        "outputs": (DEFAULT_BTCUSD_H1_STEP_READINESS_JSON,),
        "min_age_seconds": 180.0,
    },
    DEFAULT_EXECUTION_MONITOR_SCRIPT.name: {
        "outputs": (DEFAULT_EXECUTION_MONITOR_JSON,),
        "min_age_seconds": 90.0,
    },
    DEFAULT_SHARED_PRICE_FEEDER_STATUS_SCRIPT.name: {
        "outputs": (DEFAULT_SHARED_PRICE_FEEDER_STATUS_JSON, DEFAULT_SHARED_PRICE_FEEDER_STATUS_MD),
        "min_age_seconds": 60.0,
    },
}

DETACHED_FLAGS = 0
NO_WINDOW_FLAGS = 0
if os.name == "nt":
    DETACHED_FLAGS = 0x00000008 | 0x00000200 | 0x08000000
    NO_WINDOW_FLAGS = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def utc_now_iso() -> str:
    return utc_now().isoformat()


def parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except Exception:
        return None


def load_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        try:
            payload, _ = json.JSONDecoder().raw_decode(path.read_text(encoding="utf-8", errors="ignore"))
            return payload if isinstance(payload, dict) else {}
        except Exception:
            return {}
    except Exception:
        return {}


def append_jsonl(path: Path, record: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, sort_keys=True) + "\n")


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")


def append_startup_event(path: Path, *, event: str, **fields: Any) -> None:
    try:
        append_jsonl(
            path,
            {
                "ts_utc": utc_now_iso(),
                "action": "watchdog_startup",
                "event": str(event),
                **fields,
            },
        )
    except Exception:
        pass


def default_quarantine_state_path(loop_state_path: Path) -> Path:
    stem = loop_state_path.name
    if stem.endswith("_loop_state.json"):
        return loop_state_path.with_name(stem.replace("_loop_state.json", "_quarantine_state.json"))
    if stem.endswith(".json"):
        return loop_state_path.with_name(stem.replace(".json", "_quarantine_state.json"))
    return loop_state_path.with_name(stem + "_quarantine_state.json")


def load_quarantine_state(path: Path) -> dict[str, Any]:
    payload = load_json(path)
    lanes = payload.get("lanes") if isinstance(payload, dict) else {}
    if not isinstance(lanes, dict):
        lanes = {}
    return {
        "updated_at": str(payload.get("updated_at") or "") if isinstance(payload, dict) else "",
        "lanes": {str(name): row for name, row in lanes.items() if isinstance(row, dict)},
    }


def active_quarantine_entries(payload: dict[str, Any], now_dt: datetime) -> dict[str, dict[str, Any]]:
    active: dict[str, dict[str, Any]] = {}
    lanes = payload.get("lanes") if isinstance(payload, dict) else {}
    if not isinstance(lanes, dict):
        return active
    for name, row in lanes.items():
        if not isinstance(row, dict):
            continue
        until_dt = parse_iso(str(row.get("quarantined_until") or ""))
        if until_dt is None or until_dt <= now_dt:
            continue
        active[str(name)] = row
    return active


def write_quarantine_state(path: Path, *, loop_name: str, lanes: dict[str, dict[str, Any]]) -> None:
    write_json(
        path,
        {
            "updated_at": utc_now_iso(),
            "loop_name": str(loop_name or "watchdog"),
            "lanes": lanes,
        },
    )


def read_registry(path: Path) -> list[dict[str, Any]]:
    payload = load_json(path)
    lanes = payload.get("lanes") or []
    return [lane for lane in lanes if isinstance(lane, dict) and lane.get("name")]


def refresh_lane_contract(registry_path: Path, lane: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    lane_name = str(lane.get("name") or "").strip()
    if not lane_name:
        return lane, {"used_refresh": False, "refresh_status": "missing_name", "contract_changed": False}
    try:
        current_registry = read_registry(registry_path)
    except Exception as exc:
        return lane, {
            "used_refresh": False,
            "refresh_status": "read_failed",
            "contract_changed": False,
            "error": f"{type(exc).__name__}: {exc}",
        }
    refreshed_lane = next(
        (candidate for candidate in current_registry if str(candidate.get("name") or "").strip() == lane_name),
        None,
    )
    if not isinstance(refreshed_lane, dict):
        return lane, {"used_refresh": False, "refresh_status": "missing_lane", "contract_changed": False}
    stale_restart_args = [str(part) for part in (lane.get("restart_args") or []) if str(part)]
    fresh_restart_args = [str(part) for part in (refreshed_lane.get("restart_args") or []) if str(part)]
    contract_changed = (
        stale_restart_args != fresh_restart_args
        or str(lane.get("state_path") or "") != str(refreshed_lane.get("state_path") or "")
        or bool(lane.get("enabled", True)) != bool(refreshed_lane.get("enabled", True))
        or str(lane.get("restart_group") or "") != str(refreshed_lane.get("restart_group") or "")
        or str(lane.get("kind") or "") != str(refreshed_lane.get("kind") or "")
    )
    return refreshed_lane, {
        "used_refresh": True,
        "refresh_status": "ok",
        "contract_changed": contract_changed,
        "restart_args_changed": stale_restart_args != fresh_restart_args,
        "state_path_changed": str(lane.get("state_path") or "") != str(refreshed_lane.get("state_path") or ""),
        "enabled_changed": bool(lane.get("enabled", True)) != bool(refreshed_lane.get("enabled", True)),
        "restart_group_changed": str(lane.get("restart_group") or "") != str(refreshed_lane.get("restart_group") or ""),
        "kind_changed": str(lane.get("kind") or "") != str(refreshed_lane.get("kind") or ""),
    }


def lane_enabled(lane: dict[str, Any]) -> bool:
    return bool(lane.get("enabled", True))


def lane_pause_note(lane: dict[str, Any]) -> str:
    return str(lane.get("pause_note", "") or "disabled_in_registry")


def list_python_processes() -> list[dict[str, Any]]:
    if psutil is not None:
        out: list[dict[str, Any]] = []
        for proc in psutil.process_iter(["pid", "name", "cmdline", "create_time"]):
            try:
                info = proc.info
            except (psutil.Error, OSError):
                continue
            name = str(info.get("name") or "").lower()
            if name not in {"python.exe", "pythonw.exe", "python"}:
                continue
            cmdline_parts = [str(part) for part in (info.get("cmdline") or []) if str(part)]
            command_line = subprocess.list2cmdline(cmdline_parts) if cmdline_parts else ""
            started_at = ""
            create_time = info.get("create_time")
            if create_time:
                try:
                    started_at = datetime.fromtimestamp(float(create_time), tz=timezone.utc).isoformat()
                except Exception:
                    started_at = ""
            out.append(
                {
                    "pid": int(info.get("pid", 0) or 0),
                    "cmdline": cmdline_parts,
                    "command_line": command_line,
                    "started_at": started_at,
                }
            )
        return out

    cmd = [
        "powershell",
        "-NoProfile",
        "-Command",
        "Get-CimInstance Win32_Process | "
        "Where-Object { $_.Name -eq 'python.exe' } | "
        "Select-Object ProcessId,CommandLine,@{Name='StartedAt';Expression={$_.CreationDate.ToUniversalTime().ToString('o')}} | ConvertTo-Json -Compress",
    ]
    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        cwd=ROOT,
        creationflags=NO_WINDOW_FLAGS,
    )
    raw = (result.stdout or "").strip()
    if result.returncode != 0 or not raw:
        return []
    try:
        data = json.loads(raw)
    except Exception:
        return []
    if isinstance(data, dict):
        data = [data]
    out: list[dict[str, Any]] = []
    for row in data or []:
        out.append(
            {
                "pid": int(row.get("ProcessId", 0) or 0),
                "cmdline": [],
                "command_line": str(row.get("CommandLine", "") or ""),
                "started_at": str(row.get("StartedAt", "") or ""),
            }
        )
    return out


def matching_processes(processes: list[dict[str, Any]], substrings: list[str]) -> list[dict[str, Any]]:
    needles = [str(s).lower().replace("\\", "/") for s in substrings if s]
    if not needles:
        return []
    out = []
    for proc in processes:
        if is_watchdog_loop_process(proc):
            continue
        hay = str(proc.get("command_line", "") or "").lower().replace("\\", "/")
        if all(needle in hay for needle in needles):
            out.append(proc)
    return out


def is_watchdog_loop_process(proc: dict[str, Any]) -> bool:
    command_line = str(proc.get("command_line", "") or "").lower().replace("\\", "/")
    if not command_line:
        return False
    return (
        "watch_penetration_lattice_runners.py" in command_line
        and "--loop" in command_line
        and "--lanes" in command_line
    )


def split_process_command(proc: dict[str, Any]) -> list[str]:
    cmdline = proc.get("cmdline")
    if isinstance(cmdline, list):
        parts = [str(part) for part in cmdline if str(part)]
        if parts:
            return parts
    command_line = str(proc.get("command_line", "") or "").strip()
    if not command_line:
        return []
    try:
        return [str(part) for part in shlex.split(command_line, posix=False) if str(part)]
    except Exception:
        return [part for part in command_line.split() if part]


def _normalize_process_path_token(value: str) -> str:
    return str(value or "").strip().strip("\"'").replace("\\", "/").lower()


def _process_has_lane_state_path(proc: dict[str, Any], state_path: str) -> bool:
    needle = _normalize_process_path_token(state_path)
    if not needle:
        return False
    parts = split_process_command(proc)
    for idx, token in enumerate(parts):
        token_text = str(token or "")
        flag = token_text
        inline_value = None
        if "=" in token_text:
            flag, inline_value = token_text.split("=", 1)
        if flag not in {"--state-path", "--direct-exec-state-path"}:
            continue
        value = inline_value
        if value is None and idx + 1 < len(parts):
            value = str(parts[idx + 1] or "")
        if _normalize_process_path_token(value or "") == needle:
            return True
    return False


def normalize_runtime_arg_value(flag: str, value: str) -> str:
    text = str(value or "").strip().strip("\"'")
    if flag in RUNTIME_ARG_DRIFT_PATH_FLAGS:
        return text.replace("\\", "/").lower()
    try:
        return f"{float(text):g}"
    except Exception:
        pass
    if flag in {"--symbol", "--timeframe"}:
        return text.upper()
    return text.lower()


def extract_runtime_arg_expectations(parts: list[str]) -> tuple[dict[str, str], set[str]]:
    values: dict[str, str] = {}
    booleans: set[str] = set()
    idx = 0
    while idx < len(parts):
        token = str(parts[idx] or "")
        if not token.startswith("--"):
            idx += 1
            continue
        flag, inline_value = token, None
        if "=" in token:
            flag, inline_value = token.split("=", 1)
        if flag in RUNTIME_ARG_DRIFT_IGNORED_FLAGS:
            idx += 1 if inline_value is not None else 2
            continue
        if flag in RUNTIME_ARG_DRIFT_BOOL_FLAGS:
            booleans.add(flag)
            idx += 1
            continue
        if flag not in RUNTIME_ARG_DRIFT_VALUE_FLAGS:
            idx += 1 if inline_value is not None else 1
            continue
        if inline_value is None:
            next_idx = idx + 1
            if next_idx < len(parts) and not str(parts[next_idx] or "").startswith("--"):
                inline_value = str(parts[next_idx] or "")
                idx += 2
            else:
                idx += 1
        else:
            idx += 1
        if inline_value is None:
            continue
        values[flag] = normalize_runtime_arg_value(flag, inline_value)
    return values, booleans


def process_runtime_arg_drift(proc: dict[str, Any], restart_args: list[str]) -> list[str]:
    expected_values, expected_booleans = extract_runtime_arg_expectations([str(part) for part in restart_args if str(part)])
    if not expected_values and not expected_booleans:
        return []
    actual_values, actual_booleans = extract_runtime_arg_expectations(split_process_command(proc))
    drift: list[str] = []
    for flag in sorted(expected_booleans):
        if flag not in actual_booleans:
            drift.append(f"missing {flag}")
    for flag in sorted(expected_values):
        expected_value = expected_values[flag]
        actual_value = actual_values.get(flag)
        if actual_value is None:
            drift.append(f"missing {flag}={expected_value}")
        elif actual_value != expected_value:
            drift.append(f"mismatch {flag} expected={expected_value} actual={actual_value}")
    return drift


def youngest_process_age_seconds(processes: list[dict[str, Any]]) -> float | None:
    ages: list[float] = []
    now = utc_now()
    for proc in processes:
        started_at = parse_iso(str(proc.get("started_at") or ""))
        if started_at is None:
            continue
        ages.append(max(0.0, (now - started_at).total_seconds()))
    if not ages:
        return None
    return min(ages)


def conflicting_processes(
    processes: list[dict[str, Any]],
    *,
    state_path: str | None,
    event_path: str | None,
    expected_substrings: list[str],
    conflict_match_substrings: list[str] | None = None,
) -> list[dict[str, Any]]:
    needles = [str(s).lower().replace("\\", "/") for s in expected_substrings if s]
    conflict_needles = [str(s).lower().replace("\\", "/") for s in (conflict_match_substrings or []) if s]
    state_needle = str(state_path or "").lower().replace("\\", "/")
    event_needle = str(event_path or "").lower().replace("\\", "/")
    out: list[dict[str, Any]] = []
    for proc in processes:
        if is_watchdog_loop_process(proc):
            continue
        hay = str(proc.get("command_line", "") or "").lower().replace("\\", "/")
        if conflict_needles:
            if not all(needle in hay for needle in conflict_needles):
                continue
        else:
            if state_needle and state_needle not in hay and (not event_needle or event_needle not in hay):
                continue
            if event_needle and event_needle not in hay and (not state_needle or state_needle not in hay):
                continue
        if needles and all(needle in hay for needle in needles):
            continue
        out.append(proc)
    return out


def event_tail_exception(event_path: Path) -> dict[str, Any] | None:
    if not event_path.exists():
        return None
    try:
        lines = event_path.read_text(encoding="utf-8", errors="ignore").splitlines()[-100:]
    except Exception:
        return None
    for line in reversed(lines):
        try:
            row = json.loads(line)
        except Exception:
            continue
        if row.get("action") == "runner_exception":
            return row
    return None


def recent_rate_limit_stats(event_path: Path, *, lookback_seconds: float = 1800.0) -> dict[str, Any]:
    if not event_path.exists():
        return {"total": 0, "live_fetch": 0, "chunk": 0, "last_at": ""}
    cutoff = utc_now().timestamp() - max(0.0, float(lookback_seconds))
    try:
        lines = event_path.read_text(encoding="utf-8", errors="ignore").splitlines()[-2000:]
    except Exception:
        return {"total": 0, "live_fetch": 0, "chunk": 0, "last_at": ""}
    total = 0
    live_fetch = 0
    chunk = 0
    last_at = ""
    for line in lines:
        try:
            row = json.loads(line)
        except Exception:
            continue
        action = str(row.get("action") or "")
        if action not in {"rate_limit_skip_live_fetch", "rate_limit_skip_chunk"}:
            continue
        ts = parse_iso(str(row.get("ts_utc") or ""))
        if ts is None or ts.timestamp() < cutoff:
            continue
        total += 1
        if action == "rate_limit_skip_live_fetch":
            live_fetch += 1
        elif action == "rate_limit_skip_chunk":
            chunk += 1
        last_at = str(row.get("ts_utc") or last_at)
    return {"total": total, "live_fetch": live_fetch, "chunk": chunk, "last_at": last_at}


def recent_restart_count(events_path: Path, lane_name: str, *, lookback_seconds: float) -> int:
    if not events_path.exists():
        return 0
    cutoff = utc_now().timestamp() - max(0.0, float(lookback_seconds))
    total = 0
    try:
        lines = events_path.read_text(encoding="utf-8", errors="ignore").splitlines()[-2000:]
    except Exception:
        return 0
    for line in lines:
        try:
            row = json.loads(line)
        except Exception:
            continue
        if str(row.get("action") or "") != "watchdog_restart":
            continue
        if str(row.get("lane") or "") != str(lane_name or ""):
            continue
        ts = parse_iso(str(row.get("ts_utc") or ""))
        if ts is None or ts.timestamp() < cutoff:
            continue
        total += 1
    return total


def heartbeat_from_state(path: Path, payload: dict[str, Any]) -> tuple[str | None, float | None, str]:
    updated_at = str(payload.get("updated_at", "") or "") if payload else ""
    dt = parse_iso(updated_at)
    if dt is not None:
        return updated_at, max(0.0, (utc_now() - dt).total_seconds()), "state.updated_at"
    if path.exists():
        dt = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
        return dt.isoformat(), max(0.0, (utc_now() - dt).total_seconds()), "state.mtime"
    return None, None, "missing"


def outputs_are_fresh(script_path: Path, outputs: list[Path] | tuple[Path, ...], min_age_seconds: float) -> bool:
    if not outputs:
        return False
    now_ts = time.time()
    try:
        script_mtime = script_path.stat().st_mtime
    except Exception:
        script_mtime = None
    for output in outputs:
        if not output.exists():
            return False
        try:
            output_mtime = output.stat().st_mtime
        except Exception:
            return False
        if script_mtime is not None and output_mtime < script_mtime:
            return False
        if now_ts - output_mtime > max(0.0, float(min_age_seconds)):
            return False
    return True


def acquire_refresh_lock(lock_path: Path, *, stale_after_seconds: float) -> bool:
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    attempts = 0
    while attempts < 2:
        attempts += 1
        try:
            fd = os.open(str(lock_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            try:
                age_seconds = max(0.0, time.time() - lock_path.stat().st_mtime)
            except Exception:
                age_seconds = None
            if age_seconds is not None and age_seconds > max(30.0, float(stale_after_seconds)):
                try:
                    lock_path.unlink()
                except OSError:
                    return False
                continue
            return False
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(json.dumps({"pid": os.getpid(), "created_at": utc_now_iso()}))
        return True
    return False


def current_process_create_time() -> float | None:
    if psutil is None:
        return None
    try:
        return float(psutil.Process(os.getpid()).create_time())
    except (psutil.Error, OSError, ValueError, TypeError):
        return None


def process_identity_alive(pid: int, create_time: float | None = None) -> bool:
    if pid <= 0:
        return False
    if psutil is None:
        try:
            os.kill(pid, 0)
            return True
        except OSError:
            return False
    try:
        proc = psutil.Process(pid)
        if create_time is not None:
            proc_create_time = float(proc.create_time())
            if abs(proc_create_time - float(create_time)) > 1.0:
                return False
        return bool(proc.is_running())
    except (psutil.Error, OSError, ValueError, TypeError):
        return False


def acquire_loop_lock(
    lock_path: Path,
    *,
    loop_name: str,
    stale_after_seconds: float,
) -> tuple[bool, dict[str, Any]]:
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    owner_payload: dict[str, Any] = {}
    attempts = 0
    while attempts < 2:
        attempts += 1
        try:
            fd = os.open(str(lock_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            owner_payload = load_json(lock_path)
            owner_pid = int(owner_payload.get("pid") or 0)
            owner_create_time_raw = owner_payload.get("create_time")
            try:
                owner_create_time = float(owner_create_time_raw) if owner_create_time_raw is not None else None
            except (TypeError, ValueError):
                owner_create_time = None
            if process_identity_alive(owner_pid, owner_create_time):
                return False, owner_payload
            try:
                lock_path.unlink()
            except OSError:
                return False, owner_payload
            continue
        payload: dict[str, Any] = {
            "pid": os.getpid(),
            "loop_name": str(loop_name or "watchdog"),
            "created_at": utc_now_iso(),
        }
        create_time = current_process_create_time()
        if create_time is not None:
            payload["create_time"] = round(create_time, 6)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, sort_keys=True))
        return True, payload
    return False, owner_payload


def release_loop_lock(lock_path: Path) -> None:
    payload = load_json(lock_path)
    if int(payload.get("pid") or 0) != os.getpid():
        return
    try:
        lock_path.unlink()
    except OSError:
        pass


def refresh_lane_scoreboard(script_path: Path) -> dict[str, Any]:
    if not script_path.exists():
        return {"ok": False, "reason": "missing_scoreboard_script"}
    policy = REFRESH_POLICY_BY_SCRIPT.get(script_path.name) or {}
    outputs = tuple(Path(path) for path in (policy.get("outputs") or ()))
    min_age_seconds = float(policy.get("min_age_seconds") or 0.0)
    if outputs and outputs_are_fresh(script_path, outputs, min_age_seconds):
        return {
            "ok": True,
            "skipped": True,
            "reason": "fresh_outputs",
            "outputs": [str(path) for path in outputs],
        }
    lock_path = REFRESH_LOCK_DIR / f"{script_path.stem}.lock"
    if outputs and not acquire_refresh_lock(lock_path, stale_after_seconds=max(min_age_seconds * 2.0, 300.0)):
        return {
            "ok": True,
            "skipped": True,
            "reason": "refresh_in_progress",
            "outputs": [str(path) for path in outputs],
        }
    try:
        if outputs and outputs_are_fresh(script_path, outputs, min_age_seconds):
            return {
                "ok": True,
                "skipped": True,
                "reason": "fresh_outputs",
                "outputs": [str(path) for path in outputs],
            }
        result = subprocess.run(
            [sys.executable, str(script_path)],
            capture_output=True,
            text=True,
            cwd=ROOT,
            timeout=120,
            creationflags=NO_WINDOW_FLAGS,
        )
        return {
            "ok": result.returncode == 0,
            "returncode": int(result.returncode),
            "stdout": str(result.stdout or ""),
            "stderr": str(result.stderr or ""),
            "outputs": [str(path) for path in outputs],
        }
    finally:
        if outputs:
            try:
                lock_path.unlink()
            except OSError:
                pass


def load_scoreboard_totals(path: Path) -> dict[str, dict[str, Any]]:
    if not path.exists():
        return {}
    rows: dict[str, dict[str, Any]] = {}
    with path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            if str(row.get("symbol", "") or "").upper() != "TOTAL":
                continue
            lane_id = str(row.get("lane_id", "") or "")
            if lane_id:
                rows[lane_id] = row
    return rows


def load_forward_review_rows(path: Path) -> dict[str, dict[str, Any]]:
    if not path.exists():
        return {}
    rows: dict[str, dict[str, Any]] = {}
    with path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            lane_name = str(row.get("lane_name", "") or "")
            if lane_name and lane_name != "TOTAL":
                rows[lane_name] = row
    return rows


def load_combined_forward_review_rows(paths: list[Path]) -> dict[str, dict[str, Any]]:
    rows: dict[str, dict[str, Any]] = {}
    for path in paths:
        rows.update(load_forward_review_rows(path))
    return rows


def load_fx_graduation_rows(path: Path) -> dict[str, dict[str, Any]]:
    payload = load_json(path)
    rows = payload.get("rows") if isinstance(payload, dict) else []
    mapped: dict[str, dict[str, Any]] = {}
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        lane_name = str(row.get("lane_name") or "").strip()
        if lane_name:
            mapped[lane_name] = row
        aliases = row.get("lane_aliases")
        if isinstance(aliases, list):
            for alias in aliases:
                alias_name = str(alias or "").strip()
                if alias_name:
                    mapped[alias_name] = row
    return mapped


def broker_gap_reason(lane: dict[str, Any], total_row: dict[str, Any] | None) -> str | None:
    if not total_row:
        return None
    try:
        threshold = float(lane.get("broker_gap_alert_usd") or 0.0)
    except Exception:
        threshold = 0.0
    if threshold <= 0.0:
        return None
    try:
        gap = float(total_row.get("realized_gap_usd") or 0.0)
        realized = float(total_row.get("realized_usd") or 0.0)
        modeled = float(total_row.get("modeled_realized_usd") or 0.0)
    except Exception:
        return None
    if abs(gap) < threshold:
        return None
    return f"broker_gap={gap:+.2f} broker={realized:+.2f} modeled={modeled:+.2f}"


def forward_review_reason(lane: dict[str, Any], review_row: dict[str, Any] | None) -> str | None:
    if not review_row:
        return None
    if str(lane.get("kind") or "") not in {"shadow_coinbase_spot", "shadow_crypto_candidate"}:
        return None
    status = str(review_row.get("forward_status") or "").strip()
    if not status:
        return None
    realized = float(review_row.get("realized_net_usd") or 0.0)
    closes = int(float(review_row.get("realized_closes") or review_row.get("closes") or 0))
    if status.startswith("holding_up"):
        return f"forward={status} realized={realized:+.2f} closes={closes}"
    if status.startswith("lagging"):
        return f"forward={status} realized={realized:+.2f} closes={closes}"
    return f"forward={status} closes={closes}"


def proof_readiness_reason(lane: dict[str, Any], readiness_row: dict[str, Any] | None) -> str | None:
    if not readiness_row:
        return None
    lane_name = str(lane.get("name") or "")
    if not lane_name.endswith("_ratio_sleeve"):
        return None
    gate = str(readiness_row.get("current_gate") or "").strip()
    posture = str(readiness_row.get("deployment_posture") or "").strip()
    role = str(readiness_row.get("role") or "").strip()
    if not gate and not posture and not role:
        return None
    parts: list[str] = []
    if role:
        parts.append(f"role={role}")
    if gate:
        parts.append(f"gate={gate}")
    if posture:
        parts.append(f"posture={posture}")
    return "proof_" + " ".join(parts)


def fx_graduation_reason(lane: dict[str, Any], readiness_row: dict[str, Any] | None) -> str | None:
    if not readiness_row:
        return None
    readiness = str(readiness_row.get("readiness") or "").strip()
    progress_label = str(readiness_row.get("progress_label") or "").strip()
    progress_pct = str(readiness_row.get("progress_pct") or "").strip()
    next_gate = str(readiness_row.get("next_gate") or "").strip()
    if not readiness:
        return None
    parts = [f"fx_grad={readiness}"]
    if progress_label:
        progress_text = progress_label
        if progress_pct and progress_pct != "-":
            progress_text += f"({progress_pct})"
        parts.append(f"progress={progress_text}")
    if next_gate:
        parts.append(f"next={next_gate}")
    return " ".join(parts)


def crypto_readiness_reason(lane: dict[str, Any], readiness_row: dict[str, Any] | None) -> str | None:
    if not readiness_row:
        return None
    if str(lane.get("name") or "") != "shadow_ethusd_m15_warp":
        return None
    readiness = str(readiness_row.get("readiness") or "").strip()
    progress_label = str(readiness_row.get("progress_label") or "").strip()
    progress_pct = str(readiness_row.get("progress_pct") or "").strip()
    next_gate = str(readiness_row.get("next_gate") or "").strip()
    if not readiness:
        return None
    parts = [f"crypto_grad={readiness}"]
    if progress_label:
        progress_text = progress_label
        if progress_pct and progress_pct != "-":
            progress_text += f"({progress_pct})"
        parts.append(f"progress={progress_text}")
    if next_gate:
        parts.append(f"next={next_gate}")
    return " ".join(parts)


def crypto_probe_reason(lane: dict[str, Any], readiness_row: dict[str, Any] | None) -> str | None:
    if not readiness_row:
        return None
    lane_name = str(lane.get("name") or "")
    if lane_name not in {
        "shadow_solusd_m15_warp_v2",
        "shadow_xrpusd_m15_warp_v2",
        "shadow_solusd_m15_warp",
        "shadow_xrpusd_m15_warp",
        "shadow_ltcusd_m15_warp",
        "shadow_adausd_m15_warp",
    }:
        return None
    readiness = str(readiness_row.get("readiness") or "").strip()
    progress_label = str(readiness_row.get("progress_label") or "").strip()
    progress_pct = str(readiness_row.get("progress_pct") or "").strip()
    next_gate = str(readiness_row.get("next_gate") or "").strip()
    if not readiness:
        return None
    parts = [f"warp_probe={readiness}"]
    if progress_label:
        progress_text = progress_label
        if progress_pct and progress_pct != "-":
            progress_text += f"({progress_pct})"
        parts.append(f"progress={progress_text}")
    if next_gate:
        parts.append(f"next={next_gate}")
    return " ".join(parts)


def runner_exception_reason(runner: dict[str, Any]) -> str | None:
    if not isinstance(runner, dict):
        return None
    try:
        consecutive = int(runner.get("consecutive_exceptions") or 0)
    except Exception:
        consecutive = 0
    if consecutive <= 0:
        return None
    last_exception_at = parse_iso(str(runner.get("last_exception_at") or ""))
    last_successful_run_at = parse_iso(str(runner.get("last_successful_run_at") or ""))
    if last_exception_at is not None and last_successful_run_at is not None and last_exception_at < last_successful_run_at:
        return None
    exc_type = str(runner.get("last_exception_type") or "Exception").strip() or "Exception"
    message = " ".join(str(runner.get("last_exception_message") or "").split())
    if len(message) > 120:
        message = message[:117] + "..."
    if message:
        return f"runner_erroring={consecutive} {exc_type}: {message}"
    return f"runner_erroring={consecutive} {exc_type}"


def current_symbol_tick_info(
    symbol: str,
    *,
    max_tick_age_seconds: float = 120.0,
    mt5_session_ready: bool = False,
) -> dict[str, Any] | None:
    symbol_name = str(symbol or "").upper()
    if not symbol_name:
        return None
    if not mt5_session_ready and not mt5_terminal_guard.initialize_mt5(mt5_module=mt5)[0]:
        return None
    try:
        mt5.symbol_select(symbol_name, True)
        tick = mt5.symbol_info_tick(symbol_name)
        if not tick:
            return None
        tick_time = int(getattr(tick, "time", 0) or 0)
        tick_msc = int(getattr(tick, "time_msc", 0) or 0)
        if tick_time <= 0 or tick_msc <= 0:
            return None
        age_seconds = max(0.0, time.time() - float(tick_time))
        return {
            "symbol": symbol_name,
            "tick_time": tick_time,
            "tick_msc": tick_msc,
            "tick_age_seconds": age_seconds,
            "is_fresh": age_seconds <= float(max_tick_age_seconds),
        }
    finally:
        if not mt5_session_ready:
            mt5.shutdown()


def inspect_source_tick_progress(
    lane: dict[str, Any],
    payload: dict[str, Any],
    *,
    mt5_session_ready: bool = False,
) -> dict[str, Any] | None:
    metadata = payload.get("metadata") if isinstance(payload.get("metadata"), dict) else {}
    symbols = payload.get("symbols") if isinstance(payload.get("symbols"), dict) else {}
    if not metadata or not symbols:
        return None
    if not bool(metadata.get("tick_native")):
        return None
    lane_kind = str(lane.get("kind", "") or "").strip().lower()
    if lane_kind not in {"live_crypto", "shadow_crypto", "shadow_unified"}:
        return None
    if not bool(metadata.get("direct_live")) and lane_kind not in {"shadow_crypto", "shadow_unified"}:
        return None
    threshold = float(
        lane.get("source_tick_stale_after_seconds")
        or max(float(lane.get("poll_seconds", 5.0)) * 3.0, 120.0)
    )
    best: dict[str, Any] | None = None
    for symbol, snap in symbols.items():
        state_tick_msc = int(snap.get("last_tick_msc", 0) or 0)
        if state_tick_msc <= 0:
            continue
        live_tick = current_symbol_tick_info(str(symbol or ""), mt5_session_ready=mt5_session_ready)
        if not live_tick or not live_tick.get("is_fresh"):
            continue
        lag_seconds = max(0.0, (int(live_tick["tick_msc"]) - state_tick_msc) / 1000.0)
        candidate = {
            "symbol": str(symbol or "").upper(),
            "threshold_seconds": threshold,
            "lag_seconds": lag_seconds,
            "state_tick_msc": state_tick_msc,
            "live_tick_msc": int(live_tick["tick_msc"]),
            "live_tick_age_seconds": float(live_tick.get("tick_age_seconds") or 0.0),
        }
        if best is None or lag_seconds > float(best.get("lag_seconds") or 0.0):
            best = candidate
    if best is None:
        return None
    lag_seconds = float(best["lag_seconds"])
    if lag_seconds > threshold:
        best["reason"] = (
            f"source_tick_lag={lag_seconds:.1f}s>{threshold:.1f}s"
            f" state_tick={int(best['state_tick_msc'])} live_tick={int(best['live_tick_msc'])}"
        )
    else:
        best["reason"] = None
    return best


def source_tick_recurrence_details(
    lane_name: str,
    reset_baselines: dict[str, dict[str, Any]] | None,
) -> dict[str, Any] | None:
    reset_row = (reset_baselines or {}).get(str(lane_name or ""))
    if not isinstance(reset_row, dict) or str(reset_row.get("reset_type") or "") != "stale_tick_repair":
        return None
    reset_at = parse_iso(str(reset_row.get("reset_at") or ""))
    age_seconds = None
    if reset_at is not None:
        age_seconds = max(0.0, (utc_now() - reset_at).total_seconds())
    return {
        "reset_at": str(reset_row.get("reset_at") or ""),
        "age_seconds": age_seconds,
        "reason": str(reset_row.get("reason") or ""),
    }


def summarize_lane(
    lane: dict[str, Any],
    processes: list[dict[str, Any]],
    scoreboard_totals: dict[str, dict[str, Any]],
    forward_review_rows: dict[str, dict[str, Any]],
    proof_readiness_rows: dict[str, dict[str, Any]] | None = None,
    fx_graduation_rows: dict[str, dict[str, Any]] | None = None,
    crypto_readiness_rows: dict[str, dict[str, Any]] | None = None,
    crypto_probe_rows: dict[str, dict[str, Any]] | None = None,
    reset_baselines: dict[str, dict[str, Any]] | None = None,
    loop_started_at: str | None = None,
    mt5_session_ready: bool = False,
) -> dict[str, Any]:
    state_path = ROOT / str(lane["state_path"])
    event_path = ROOT / str(lane.get("event_path") or "")
    payload = load_json(state_path)
    heartbeat_at, age_seconds, heartbeat_source = heartbeat_from_state(state_path, payload)
    runner = payload.get("runner") if isinstance(payload.get("runner"), dict) else {}
    symbols = payload.get("symbols") if isinstance(payload.get("symbols"), dict) else {}
    last_bar_time = max((int(sym.get("last_bar_time", 0) or 0) for sym in symbols.values()), default=0)
    open_count = sum(len(sym.get("open_tickets") or []) for sym in symbols.values()) if symbols else None
    if open_count is None and isinstance(payload.get("positions"), list):
        open_count = len(payload.get("positions") or [])
    stale_after = float(lane.get("stale_after_seconds") or max(float(lane.get("poll_seconds", 5)) * 6.0, 45.0))
    expected_substrings = list(lane.get("process_match_substrings") or [])
    matches = matching_processes(processes, expected_substrings)
    conflicts = conflicting_processes(
        processes,
        state_path=str(lane.get("state_path") or ""),
        event_path=str(lane.get("event_path") or ""),
        expected_substrings=expected_substrings,
        conflict_match_substrings=list(lane.get("conflict_match_substrings") or []),
    )
    last_exception = event_tail_exception(event_path) if lane.get("event_path") else None
    rate_limit_stats = recent_rate_limit_stats(event_path) if lane.get("event_path") else {"total": 0, "live_fetch": 0, "chunk": 0, "last_at": ""}
    total_row = scoreboard_totals.get(str(lane.get("name") or ""))
    forward_row = forward_review_rows.get(str(lane.get("name") or ""))
    proof_row = (proof_readiness_rows or {}).get(str(lane.get("name") or ""))
    fx_graduation_row = (fx_graduation_rows or {}).get(str(lane.get("name") or ""))
    crypto_readiness_row = (crypto_readiness_rows or {}).get(str(lane.get("name") or ""))
    crypto_probe_row = (crypto_probe_rows or {}).get(str(lane.get("name") or ""))
    startup_grace = float(lane.get("startup_grace_seconds") or 0.0)
    poll_seconds = max(float(lane.get("poll_seconds", 5) or 5.0), 1.0)
    loop_bootstrap_grace = min(startup_grace, 45.0) if startup_grace > 0.0 else min(stale_after, max(min(poll_seconds * 2.0, 45.0), 15.0))
    loop_bootstrap_age_seconds = None
    loop_started_dt = parse_iso(loop_started_at)
    if loop_started_dt is not None:
        loop_bootstrap_age_seconds = max((utc_now() - loop_started_dt).total_seconds(), 0.0)
    within_loop_bootstrap = (
        loop_bootstrap_age_seconds is not None and loop_bootstrap_age_seconds <= loop_bootstrap_grace
    )
    status = "ok"
    reasons: list[str] = []
    arg_drift_process_ids: list[int] = []
    arg_drift_details: list[dict[str, Any]] = []
    if not lane_enabled(lane):
        status = "paused"
        reasons.append(lane_pause_note(lane))
    else:
        if not matches and not conflicts and within_loop_bootstrap and (
            age_seconds is None or age_seconds <= loop_bootstrap_grace
        ):
            status = "starting"
            reasons.append(f"loop_bootstrap_grace={loop_bootstrap_age_seconds:.1f}s/{loop_bootstrap_grace:.1f}s")
        elif not matches:
            status = "missing_process"
            reasons.append("no_matching_process")
        max_processes = max(1, int(lane.get("max_processes") or 1))
        if len(matches) > max_processes:
            status = "conflict"
            reasons.append("duplicate_matching_processes=" + ",".join(str(proc["pid"]) for proc in matches))
        if conflicts:
            status = "conflict"
            reasons.append("conflicting_processes=" + ",".join(str(proc["pid"]) for proc in conflicts))
        if age_seconds is None:
            youngest_age = youngest_process_age_seconds(matches)
            if matches and startup_grace > 0.0 and youngest_age is not None and youngest_age <= startup_grace:
                status = "starting"
                reasons.append(f"bootstrap_grace={youngest_age:.1f}s/{startup_grace:.1f}s")
            elif status != "starting":
                status = "missing_state"
                reasons.append("state_missing")
        elif age_seconds > stale_after and status != "starting":
            status = "stale"
            reasons.append(f"heartbeat_age={age_seconds:.1f}s")
        if matches and status != "starting":
            restart_args = [str(part) for part in (lane.get("restart_args") or []) if str(part)]
            for proc in matches:
                drift = process_runtime_arg_drift(proc, restart_args)
                if not drift:
                    continue
                pid = int(proc.get("pid", 0) or 0)
                arg_drift_process_ids.append(pid)
                arg_drift_details.append({"pid": pid, "issues": drift})
                reasons.append(f"arg_drift pid={pid} " + ", ".join(drift))
            if arg_drift_process_ids and status == "ok":
                status = "arg_drift"
    runner_reason = runner_exception_reason(runner)
    if runner_reason:
        reasons.append(runner_reason)
        if status in {"ok", "starting"}:
            status = "erroring"
    gap_reason = broker_gap_reason(lane, total_row)
    if gap_reason:
        reasons.append(gap_reason)
    review_reason = forward_review_reason(lane, forward_row)
    if review_reason:
        reasons.append(review_reason)
    readiness_reason = proof_readiness_reason(lane, proof_row)
    if readiness_reason:
        reasons.append(readiness_reason)
    fx_reason = fx_graduation_reason(lane, fx_graduation_row)
    if fx_reason:
        reasons.append(fx_reason)
    crypto_reason = crypto_readiness_reason(lane, crypto_readiness_row)
    if crypto_reason:
        reasons.append(crypto_reason)
    crypto_probe = crypto_probe_reason(lane, crypto_probe_row)
    if crypto_probe:
        reasons.append(crypto_probe)
    if int(rate_limit_stats.get("total") or 0) > 0:
        reasons.append(
            "rate_limit_skips_30m="
            f"{int(rate_limit_stats.get('total') or 0)}"
            f" live={int(rate_limit_stats.get('live_fetch') or 0)}"
            f" chunk={int(rate_limit_stats.get('chunk') or 0)}"
        )
    source_tick = inspect_source_tick_progress(lane, payload, mt5_session_ready=mt5_session_ready)
    source_tick_reason = str(source_tick.get("reason") or "") if source_tick else ""
    source_tick_recurrence = None
    if source_tick_reason:
        source_tick_recurrence = source_tick_recurrence_details(str(lane.get("name") or ""), reset_baselines)
        status = "stale_recurrence" if source_tick_recurrence else "stale"
        reasons.append(source_tick_reason)
        if source_tick_recurrence:
            recurrence_age = source_tick_recurrence.get("age_seconds")
            recurrence_age_text = "-" if recurrence_age is None else f"{float(recurrence_age):.1f}s"
            reasons.append(
                "source_tick_recurrence"
                f" reset_at={source_tick_recurrence.get('reset_at') or '-'}"
                f" age_since_reset={recurrence_age_text}"
            )
    if total_row and str(lane.get("kind", "") or "").startswith("live"):
        try:
            open_count = int(total_row.get("open_count") or open_count or 0)
        except Exception:
            pass
    
    # Turbulence check (punitive resets)
    # We use a threshold of 5 risk-based resets to flag structural instability
    # Lanes that are just flat-chasing (flat resets) will NOT be flagged.
    for sym_snap in symbols.values():
        risk_resets = int(sym_snap.get("anchor_resets_risk", 0))
        if risk_resets >= 5:
            if status == "ok":
                status = "turbulent"
            reasons.append(f"risk_resets={risk_resets}>=5")
            break

    return {
        "name": lane["name"],
        "kind": lane.get("kind", ""),
        "enabled": lane_enabled(lane),
        "status": status,
        "reasons": reasons,
        "process_ids": [proc["pid"] for proc in matches],
        "conflicting_process_ids": [proc["pid"] for proc in conflicts],
        "arg_drift_process_ids": arg_drift_process_ids,
        "arg_drift_details": arg_drift_details,
        "heartbeat_at": heartbeat_at,
        "heartbeat_age_seconds": round(age_seconds, 1) if age_seconds is not None else None,
        "heartbeat_source": heartbeat_source,
        "stale_after_seconds": stale_after,
        "runner": runner,
        "last_bar_time": last_bar_time,
        "open_count": open_count,
        "last_exception": last_exception,
        "state_path": str(state_path),
        "event_path": str(event_path) if lane.get("event_path") else "",
        "scoreboard_total": total_row or {},
        "forward_review": forward_row or {},
        "proof_readiness": proof_row or {},
        "fx_graduation": fx_graduation_row or {},
        "crypto_readiness": crypto_readiness_row or {},
        "crypto_probe_readiness": crypto_probe_row or {},
        "rate_limit_skip_count_30m": int(rate_limit_stats.get("total") or 0),
        "rate_limit_skip_live_fetch_30m": int(rate_limit_stats.get("live_fetch") or 0),
        "rate_limit_skip_chunk_30m": int(rate_limit_stats.get("chunk") or 0),
        "rate_limit_skip_last_at": str(rate_limit_stats.get("last_at") or ""),
        "source_tick_symbol": str(source_tick.get("symbol") or "") if source_tick else "",
        "source_tick_lag_seconds": round(float(source_tick.get("lag_seconds") or 0.0), 1) if source_tick else None,
        "source_tick_threshold_seconds": round(float(source_tick.get("threshold_seconds") or 0.0), 1) if source_tick else None,
        "source_tick_state_msc": int(source_tick.get("state_tick_msc") or 0) if source_tick else 0,
        "source_tick_live_msc": int(source_tick.get("live_tick_msc") or 0) if source_tick else 0,
        "source_tick_live_age_seconds": round(float(source_tick.get("live_tick_age_seconds") or 0.0), 1) if source_tick else None,
        "source_tick_recurrence": bool(source_tick_recurrence),
        "source_tick_recurrence_reset_at": str(source_tick_recurrence.get("reset_at") or "") if source_tick_recurrence else "",
        "source_tick_recurrence_age_seconds": (
            round(float(source_tick_recurrence.get("age_seconds") or 0.0), 1) if source_tick_recurrence and source_tick_recurrence.get("age_seconds") is not None else None
        ),
    }


def _get_child_pids(pid: int) -> list[int]:
    """Return direct and indirect child PIDs of *pid* via Win32_Process."""
    try:
        import ctypes
        out = subprocess.run(
            [
                "powershell", "-NoProfile", "-Command",
                (
                    "Get-CimInstance Win32_Process | "
                    "Where-Object { $_.ParentProcessId -eq $args[0] } | "
                    "Select-Object -ExpandProperty ProcessId"
                ),
                str(pid),
            ],
            capture_output=True, text=True, timeout=10,
            creationflags=NO_WINDOW_FLAGS,
        )
        children = []
        for line in out.stdout.splitlines():
            stripped = line.strip()
            if stripped.isdigit():
                children.append(int(stripped))
        return children
    except Exception:
        return []


def _wait_for_death(pid: int, timeout_seconds: float = 10.0) -> bool:
    """Poll until *pid* is gone or timeout expires. Returns True if process died."""
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        try:
            proc = psutil.Process(pid)
            if not proc.is_running():
                return True
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            return True
        time.sleep(0.3)
    return not _process_alive(pid)


def _process_alive(pid: int) -> bool:
    """Check if a PID is still alive, without raising."""
    try:
        p = psutil.Process(pid)
        return p.is_running()
    except (psutil.NoSuchProcess, psutil.AccessDenied, OSError):
        return False


def stop_process(pid: int) -> dict[str, Any]:
    """Kill *pid* and all descendant processes. Verifies death before returning."""
    result: dict[str, Any] = {"pid": pid, "killed_children": [], "main_killed": False, "errors": []}

    # 1. Kill process tree bottom-up (children first)
    child_pids = _get_child_pids(pid)
    for child_pid in child_pids:
        try:
            subprocess.run(
                ["powershell", "-NoProfile", "-Command",
                 f"Stop-Process -Id {child_pid} -Force -ErrorAction SilentlyContinue"],
                capture_output=True, text=True, timeout=10,
                creationflags=NO_WINDOW_FLAGS,
            )
            result["killed_children"].append(child_pid)
        except Exception as exc:
            result["errors"].append(f"child {child_pid}: {exc}")

    # 2. Kill the target PID
    try:
        subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             f"Stop-Process -Id {int(pid)} -Force -ErrorAction SilentlyContinue"],
            capture_output=True, text=True, timeout=10,
            creationflags=NO_WINDOW_FLAGS,
        )
    except Exception as exc:
        result["errors"].append(f"main {pid}: {exc}")

    # 3. Verify death (up to 10 seconds)
    if _wait_for_death(pid, timeout_seconds=10.0):
        result["main_killed"] = True
    else:
        result["errors"].append(f"pid {pid} still alive after stop attempt")

    return result


def _find_running_lane_pid(lane_name: str, state_path: str, *, stale_after_seconds: float = 240.0) -> int | None:
    """Idempotency guard: find an existing running process for this lane.

    Returns the PID if a healthy process is already running, None otherwise.
    Checks both the process table (via psutil) and state-file heartbeat.
    """
    if psutil is None:
        return None

    # 1. Check state file heartbeat
    state_p = Path(state_path)
    if state_p.exists():
        try:
            state_mtime = state_p.stat().st_mtime
            age = time.time() - state_mtime
            if age < stale_after_seconds:
                # State file is fresh — try to extract PID from it
                try:
                    with open(state_p, "r", encoding="utf-8") as f:
                        state = json.load(f)
                    runner_pid = state.get("runner", {}).get("pid")
                    if runner_pid is not None:
                        # Verify the process is actually running
                        proc = psutil.Process(runner_pid)
                        if proc.is_running() and proc.status() != psutil.STATUS_ZOMBIE:
                            proc_info = {
                                "pid": int(runner_pid),
                                "cmdline": [str(part) for part in (proc.cmdline() or []) if str(part)],
                                "command_line": subprocess.list2cmdline([str(part) for part in (proc.cmdline() or []) if str(part)]),
                            }
                            if not is_watchdog_loop_process(proc_info) and _process_has_lane_state_path(proc_info, state_path):
                                return int(runner_pid)
                except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess, json.JSONDecodeError, KeyError):
                    pass
        except OSError:
            pass

    # 2. Scan for Python processes matching this lane's exact state path.
    for proc in psutil.process_iter(["pid", "name", "cmdline"]):
        try:
            if "python" not in (proc.info.get("name") or "").lower():
                continue
            proc_info = {
                "pid": int(proc.info.get("pid", 0) or 0),
                "cmdline": [str(part) for part in (proc.info.get("cmdline") or []) if str(part)],
                "command_line": subprocess.list2cmdline([str(part) for part in (proc.info.get("cmdline") or []) if str(part)]),
            }
            command_line = str(proc_info.get("command_line", "") or "")
            if is_watchdog_loop_process(proc_info):
                continue
            if "--fresh-start" in command_line:
                continue
            if _process_has_lane_state_path(proc_info, state_path):
                return int(proc.info["pid"])
        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
            continue

    return None


def start_lane(lane: dict[str, Any]) -> dict[str, Any]:
    log_dir = DEFAULT_LOG_DIR
    log_dir.mkdir(parents=True, exist_ok=True)
    stdout_path = log_dir / f"{lane['name']}.out.log"
    stderr_path = log_dir / f"{lane['name']}.err.log"

    # Idempotency guard: check if lane is already running before launching
    state_path = str(lane.get("state_path") or "")
    existing_pid = _find_running_lane_pid(lane["name"], state_path)
    if existing_pid is not None:
        return {
            "started_pid": existing_pid,
            "stdout_path": str(stdout_path),
            "stderr_path": str(stderr_path),
            "skipped_duplicate": True,
        }

    stdout_handle = stdout_path.open("a", encoding="utf-8")
    stderr_handle = stderr_path.open("a", encoding="utf-8")
    try:
        proc = subprocess.Popen(
            [sys.executable, *list(lane.get("restart_args") or [])],
            cwd=str(ROOT),
            stdout=stdout_handle,
            stderr=stderr_handle,
            creationflags=DETACHED_FLAGS,
        )
    finally:
        stdout_handle.close()
        stderr_handle.close()
    return {
        "started_pid": int(proc.pid),
        "stdout_path": str(stdout_path),
        "stderr_path": str(stderr_path),
    }


def restart_group_name(lane: dict[str, Any]) -> str:
    return str(lane.get("restart_group") or "").strip()


def log_stop_disabled_event(
    events_path: Path,
    lane: dict[str, Any],
    row: dict[str, Any],
) -> None:
    append_jsonl(
        events_path,
        {
            "ts_utc": utc_now_iso(),
            "action": "watchdog_stop_disabled",
            "lane": lane["name"],
            "kind": lane.get("kind", ""),
            "prior_status": row.get("status", ""),
            "prior_reasons": list(row.get("reasons") or []),
            "prior_pids": list(row.get("process_ids") or []),
            "prior_conflicting_pids": list(row.get("conflicting_process_ids") or []),
            "state_path": row.get("state_path", ""),
            "event_path": row.get("event_path", ""),
        },
    )


def log_cleanup_event(
    events_path: Path,
    lane: dict[str, Any],
    row: dict[str, Any],
    *,
    stopped_pids: list[int],
    reason: str,
) -> None:
    append_jsonl(
        events_path,
        {
            "ts_utc": utc_now_iso(),
            "action": "watchdog_cleanup",
            "lane": lane["name"],
            "kind": lane.get("kind", ""),
            "reason": reason,
            "stopped_pids": [int(pid) for pid in stopped_pids],
            "prior_status": row.get("status", ""),
            "prior_reasons": list(row.get("reasons") or []),
            "prior_pids": list(row.get("process_ids") or []),
            "prior_conflicting_pids": list(row.get("conflicting_process_ids") or []),
            "state_path": row.get("state_path", ""),
            "event_path": row.get("event_path", ""),
        },
    )


def log_quarantine_event(
    events_path: Path,
    lane: dict[str, Any],
    row: dict[str, Any],
    *,
    quarantine_until: str,
    restart_count: int,
    window_seconds: int,
    quarantine_seconds: int,
    reason: str,
) -> None:
    append_jsonl(
        events_path,
        {
            "ts_utc": utc_now_iso(),
            "action": "watchdog_quarantine",
            "lane": lane["name"],
            "kind": lane.get("kind", ""),
            "reason": reason,
            "quarantined_until": quarantine_until,
            "restart_count_window": int(restart_count),
            "restart_window_seconds": int(window_seconds),
            "quarantine_seconds": int(quarantine_seconds),
            "prior_status": row.get("status", ""),
            "prior_reasons": list(row.get("reasons") or []),
            "prior_pids": list(row.get("process_ids") or []),
            "prior_conflicting_pids": list(row.get("conflicting_process_ids") or []),
            "state_path": row.get("state_path", ""),
            "event_path": row.get("event_path", ""),
        },
    )


def log_repair_event(
    events_path: Path,
    lane: dict[str, Any],
    row: dict[str, Any],
    repair_info: dict[str, Any],
    reset_baseline: dict[str, Any] | None = None,
) -> None:
    append_jsonl(
        events_path,
        {
            "ts_utc": utc_now_iso(),
            "action": "watchdog_restart",
            "lane": lane["name"],
            "kind": lane.get("kind", ""),
            "prior_status": row.get("status", ""),
            "prior_reasons": list(row.get("reasons") or []),
            "prior_pids": list(row.get("process_ids") or []),
            "prior_conflicting_pids": list(row.get("conflicting_process_ids") or []),
            "prior_heartbeat_at": row.get("heartbeat_at"),
            "prior_heartbeat_age_seconds": row.get("heartbeat_age_seconds"),
            "prior_last_bar_time": row.get("last_bar_time"),
            "prior_open_count": row.get("open_count"),
            "state_path": row.get("state_path", ""),
            "event_path": row.get("event_path", ""),
            "started_pid": repair_info.get("started_pid"),
            "stdout_path": repair_info.get("stdout_path"),
            "stderr_path": repair_info.get("stderr_path"),
            "clean_forward_reset": reset_baseline or {},
        },
    )


def build_loop_state_payload(
    *,
    loop_name: str,
    status: str,
    args: argparse.Namespace,
    loop_started_at: str | None = None,
    cycle_started_at: str | None = None,
    cycle_completed_at: str | None = None,
    consecutive_failures: int = 0,
    rows: list[dict[str, Any]] | None = None,
    error: BaseException | None = None,
) -> dict[str, Any]:
    lane_names = [str(lane) for lane in (args.lanes or []) if str(lane or "").strip()]
    status_counts: dict[str, int] = {}
    for row in rows or []:
        row_status = str(row.get("status") or "")
        if not row_status:
            continue
        status_counts[row_status] = int(status_counts.get(row_status, 0)) + 1
    payload: dict[str, Any] = {
        "loop_name": str(loop_name or "watchdog"),
        "status": str(status or "unknown"),
        "pid": int(os.getpid()),
        "script": Path(__file__).name,
        "registry": str(args.registry),
        "report_json": str(args.report_json),
        "report_md": str(args.report_md),
        "events_jsonl": str(args.events_jsonl),
        "quarantine_state_json": str(getattr(args, "quarantine_state_json", "") or ""),
        "repair": bool(args.repair),
        "force_restart": bool(args.force_restart),
        "loop": bool(args.loop),
        "interval_seconds": float(args.interval_seconds),
        "skip_shared_operator_refresh": bool(getattr(args, "skip_shared_operator_refresh", False)),
        "lanes": lane_names,
        "loop_started_at": str(loop_started_at or ""),
        "cycle_started_at": str(cycle_started_at or ""),
        "cycle_completed_at": str(cycle_completed_at or ""),
        "updated_at": utc_now_iso(),
        "consecutive_failures": int(consecutive_failures),
        "rows_total": len(rows or []),
        "status_counts": status_counts,
    }
    if error is not None:
        payload["last_error"] = {
            "type": type(error).__name__,
            "message": " ".join(str(error).split()),
            "at": utc_now_iso(),
        }
    return payload


def write_loop_state(
    path: Path,
    *,
    loop_name: str,
    status: str,
    args: argparse.Namespace,
    loop_started_at: str | None = None,
    cycle_started_at: str | None = None,
    cycle_completed_at: str | None = None,
    consecutive_failures: int = 0,
    rows: list[dict[str, Any]] | None = None,
    error: BaseException | None = None,
) -> None:
    write_json(
        path,
        build_loop_state_payload(
            loop_name=loop_name,
            status=status,
            args=args,
            loop_started_at=loop_started_at,
            cycle_started_at=cycle_started_at,
            cycle_completed_at=cycle_completed_at,
            consecutive_failures=consecutive_failures,
            rows=rows,
            error=error,
        ),
    )


def build_recent_incidents(
    previous_payload: dict[str, Any] | None,
    status_rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    previous_rows = previous_payload.get("rows") if isinstance(previous_payload, dict) else []
    previous_by_name: dict[str, dict[str, Any]] = {}
    for row in previous_rows or []:
        if not isinstance(row, dict):
            continue
        name = str(row.get("name") or "")
        if name:
            previous_by_name[name] = row
    incidents: list[dict[str, Any]] = []
    for row in status_rows:
        name = str(row.get("name") or "")
        if not name:
            continue
        previous = previous_by_name.get(name)
        if not previous:
            continue
        old_status = str(previous.get("status") or "")
        new_status = str(row.get("status") or "")
        if not old_status or old_status == new_status:
            continue
        incidents.append(
            {
                "lane": name,
                "old_status": old_status,
                "new_status": new_status,
                "heartbeat_age_seconds": row.get("heartbeat_age_seconds"),
                "source_tick_lag_seconds": row.get("source_tick_lag_seconds"),
                "reasons": list(row.get("reasons") or []),
            }
        )
    incidents.sort(key=lambda item: str(item.get("lane") or ""))
    return incidents


def write_reports(status_rows: list[dict[str, Any]], report_json: Path, report_md: Path) -> None:
    report_json.parent.mkdir(parents=True, exist_ok=True)
    previous_payload = load_json(report_json)
    incidents = build_recent_incidents(previous_payload, status_rows)
    payload = {"generated_at": utc_now_iso(), "rows": status_rows, "recent_incidents": incidents}
    report_json.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    lines = [
        "# Runner Watchdog",
        "",
        f"Generated: `{payload['generated_at']}`",
        "",
        "## Recent Incidents",
        "",
    ]
    if incidents:
        lines.extend(
            [
                "| Lane | Status Change | Heartbeat Age (s) | Source Lag (s) | Note |",
                "| --- | --- | ---: | ---: | --- |",
            ]
        )
        for incident in incidents:
            heartbeat_age = incident.get("heartbeat_age_seconds")
            heartbeat_text = "-" if heartbeat_age is None else f"{float(heartbeat_age):.1f}"
            source_lag = incident.get("source_tick_lag_seconds")
            source_lag_text = "-" if source_lag is None else f"{float(source_lag):.1f}"
            note = ", ".join(incident.get("reasons") or []) or "-"
            lines.append(
                f"| {incident['lane']} | {incident['old_status']} -> {incident['new_status']} | "
                f"{heartbeat_text} | {source_lag_text} | {note} |"
            )
        lines.append("")
    else:
        lines.extend(["No status changes since previous cycle.", ""])
    lines.extend(
        [
        "| Lane | Status | PIDs | Heartbeat Age (s) | Source Lag (s) | 429 Skips 30m | Open | Last Bar | Realized Gap USD | Broker Net USD | Note |",
        "| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- |",
        ]
    )
    for row in status_rows:
        note = ", ".join(row.get("reasons") or []) or "-"
        pids = ",".join(str(pid) for pid in row.get("process_ids") or []) or "-"
        age = row.get("heartbeat_age_seconds")
        age_text = "-" if age is None else f"{age:.1f}"
        source_lag = row.get("source_tick_lag_seconds")
        source_lag_text = "-" if source_lag is None else f"{float(source_lag):.1f}"
        rate_limit_skips = int(row.get("rate_limit_skip_count_30m") or 0)
        rate_limit_skips_text = "-" if rate_limit_skips <= 0 else str(rate_limit_skips)
        open_count = row.get("open_count")
        open_text = "-" if open_count is None else str(open_count)
        last_bar = row.get("last_bar_time") or "-"
        total = row.get("scoreboard_total") or {}
        gap_text = "-"
        net_text = "-"
        try:
            gap_text = f"{float(total.get('realized_gap_usd') or 0.0):.2f}"
            net_text = f"{float(total.get('net_usd') or 0.0):.2f}"
        except Exception:
            pass
        lines.append(f"| {row['name']} | {row['status']} | {pids} | {age_text} | {source_lag_text} | {rate_limit_skips_text} | {open_text} | {last_bar} | {gap_text} | {net_text} | {note} |")
    report_md.write_text("\n".join(lines) + "\n", encoding="utf-8")


def run_watchdog(
    registry_path: Path,
    report_json: Path,
    report_md: Path,
    events_path: Path,
    repair: bool,
    lanes_filter: set[str] | None,
    force_restart: bool,
    quarantine_state_path: Path | None = None,
    loop_state_path: Path | None = None,
    loop_name: str = "watchdog",
    loop_started_at: str | None = None,
    refresh_shared_operator_artifacts: bool = True,
    mt5_session_ready: bool | None = None,
) -> list[dict[str, Any]]:
    append_startup_event(
        events_path,
        event="run_watchdog_enter",
        loop_name=str(loop_name or "watchdog"),
        loop_started_at=str(loop_started_at or ""),
        refresh_shared_operator_artifacts=bool(refresh_shared_operator_artifacts),
    )
    registry = read_registry(registry_path)
    append_startup_event(
        events_path,
        event="run_watchdog_registry_loaded",
        loop_name=str(loop_name or "watchdog"),
        configured_lanes=len(registry),
    )
    if lanes_filter:
        registry = [lane for lane in registry if lane["name"] in lanes_filter]
        append_startup_event(
            events_path,
            event="run_watchdog_registry_filtered",
            loop_name=str(loop_name or "watchdog"),
            filtered_lanes=len(registry),
        )
    if refresh_shared_operator_artifacts:
        refresh_lane_scoreboard(DEFAULT_SCOREBOARD_SCRIPT)
        refresh_lane_scoreboard(DEFAULT_COINBASE_RSI_FORWARD_SCRIPT)
        refresh_lane_scoreboard(DEFAULT_COINBASE_BURST_SCOREBOARD_SCRIPT)
        refresh_lane_scoreboard(DEFAULT_COINBASE_BURST_FORWARD_SCRIPT)
        refresh_lane_scoreboard(DEFAULT_COINBASE_EXPERIMENTAL_SCOREBOARD_SCRIPT)
        refresh_lane_scoreboard(DEFAULT_COINBASE_EXPERIMENTAL_FORWARD_SCRIPT)
        refresh_lane_scoreboard(DEFAULT_COINBASE_RATIO_FORWARD_SCRIPT)
        refresh_lane_scoreboard(DEFAULT_COINBASE_RATIO_PROOF_READINESS_SCRIPT)
        refresh_lane_scoreboard(DEFAULT_FX_GRADUATION_READINESS_SCRIPT)
        refresh_lane_scoreboard(DEFAULT_ETH_M15_WARP_READINESS_SCRIPT)
        refresh_lane_scoreboard(DEFAULT_CRYPTO_M15_WARP_READINESS_SCRIPT)
        refresh_lane_scoreboard(DEFAULT_BTCUSD_H1_STEP_FORWARD_SCRIPT)
        refresh_lane_scoreboard(DEFAULT_BTCUSD_H1_STEP_READINESS_SCRIPT)
        refresh_lane_scoreboard(DEFAULT_EXECUTION_MONITOR_SCRIPT)
        refresh_lane_scoreboard(DEFAULT_SHARED_PRICE_FEEDER_STATUS_SCRIPT)
    scoreboard_totals = load_scoreboard_totals(DEFAULT_SCOREBOARD_CSV)
    reset_baselines = load_reset_baselines()
    quarantine_state = load_quarantine_state(quarantine_state_path or default_quarantine_state_path(report_json))
    now_dt = utc_now()
    active_quarantines = active_quarantine_entries(quarantine_state, now_dt)
    forward_review_rows = load_combined_forward_review_rows(
        [
            DEFAULT_COINBASE_RSI_FORWARD_CSV,
            DEFAULT_COINBASE_RATIO_FORWARD_CSV,
            DEFAULT_COINBASE_BURST_FORWARD_CSV,
            DEFAULT_COINBASE_EXPERIMENTAL_FORWARD_CSV,
            DEFAULT_BTCUSD_H1_STEP_FORWARD_CSV,
        ]
    )
    proof_readiness_rows = load_forward_review_rows(DEFAULT_COINBASE_RATIO_PROOF_READINESS_CSV)
    fx_graduation_rows = load_fx_graduation_rows(DEFAULT_FX_GRADUATION_READINESS_JSON)
    crypto_readiness_rows = load_fx_graduation_rows(DEFAULT_ETH_M15_WARP_READINESS_JSON)
    crypto_probe_rows = load_fx_graduation_rows(DEFAULT_CRYPTO_M15_WARP_READINESS_JSON)
    processes = list_python_processes()

    mt5_session_required = any(
        str(lane.get("kind", "") or "").strip().lower() in {"live_crypto", "shadow_crypto", "shadow_unified"}
        for lane in registry
    )

    append_startup_event(
        events_path,
        event="run_watchdog_state_loaded",
        loop_name=str(loop_name or "watchdog"),
        registry_size=len(registry),
        processes=len(processes),
        scoreboard_symbols=len(scoreboard_totals),
        forward_review_rows=len(forward_review_rows),
        proof_readiness_rows=len(proof_readiness_rows),
        fx_graduation_rows=len(fx_graduation_rows),
        crypto_readiness_rows=len(crypto_readiness_rows),
        crypto_probe_rows=len(crypto_probe_rows),
        active_quarantines=len(active_quarantines),
        mt5_session_required=mt5_session_required,
    )
    
    internal_mt5_session = False
    if mt5_session_ready is None:
        if mt5_session_required:
            mt5_session_ready, _mt5_connection = mt5_terminal_guard.initialize_mt5(mt5_module=mt5)
            internal_mt5_session = True
        else:
            mt5_session_ready = False
            
    active_lane_name = ""
    active_lane_index = -1
    try:
        rows: list[dict[str, Any]] = []
        append_startup_event(
            events_path,
            event="run_watchdog_summary_begin",
            loop_name=str(loop_name or "watchdog"),
            registry_size=len(registry),
        )
        for active_lane_index, lane in enumerate(registry):
            lane_name = str(lane.get("name") or "")
            active_lane_name = lane_name
            append_startup_event(
                events_path,
                event="run_watchdog_summary_enter",
                loop_name=str(loop_name or "watchdog"),
                lane_name=lane_name,
            )
            row = summarize_lane(
                lane,
                processes,
                scoreboard_totals,
                forward_review_rows,
                proof_readiness_rows,
                fx_graduation_rows,
                crypto_readiness_rows,
                crypto_probe_rows,
                reset_baselines,
                loop_started_at=loop_started_at,
                mt5_session_ready=mt5_session_ready,
            )
            rows.append(row)
            append_startup_event(
                events_path,
                event="run_watchdog_summary_exit",
                loop_name=str(loop_name or "watchdog"),
                lane_name=lane_name,
                status=str(row.get("status") or ""),
            )
    except Exception as exc:
        append_startup_event(
            events_path,
            event="run_watchdog_summary_exception",
            loop_name=str(loop_name or "watchdog"),
            lane_name=active_lane_name,
            lane_index=active_lane_index,
            exception_type=type(exc).__name__,
            error=" ".join(str(exc).split()),
            traceback_text="".join(traceback.format_exception(type(exc), exc, exc.__traceback__)).strip(),
        )
        raise
    finally:
        if internal_mt5_session and mt5_session_ready:
            mt5.shutdown()
    next_quarantines: dict[str, dict[str, Any]] = dict(active_quarantines)
    for lane, row in zip(registry, rows):
        lane_name = str(lane.get("name") or "")
        quarantine_entry = active_quarantines.get(lane_name)
        if str(row.get("status") or "") == "ok" and lane_name in next_quarantines:
            next_quarantines.pop(lane_name, None)
        if not quarantine_entry or str(row.get("status") or "") == "ok":
            continue
        row["status"] = "quarantined"
        row["quarantine_until"] = str(quarantine_entry.get("quarantined_until") or "")
        row["quarantine_reason"] = str(quarantine_entry.get("reason") or "restart_storm")
        row["quarantine_restart_count_window"] = int(quarantine_entry.get("restart_count_window") or 0)
        row["reasons"].append(
            "quarantined_until="
            f"{row['quarantine_until'] or '-'}"
            f" reason={row['quarantine_reason'] or 'restart_storm'}"
        )
    restarted_groups: set[str] = set()
    if repair:
        for lane, row in zip(registry, rows):
            repair_lane, refresh_info = refresh_lane_contract(registry_path, lane)
            lane_name = str(repair_lane.get("name") or lane.get("name") or "")
            restart_group = restart_group_name(repair_lane)
            if not lane_enabled(repair_lane):
                if row.get("process_ids"):
                    stopped_pids: list[int] = []
                    stop_errors: list[str] = []
                    for pid in row.get("process_ids") or []:
                        stopped_pids.append(int(pid))
                        stop_result = stop_process(int(pid))
                        stop_errors.extend(stop_result.get("errors", []))
                    row["repair_action"] = "stop_disabled"
                    row["repair_stopped_pids"] = stopped_pids
                    if stop_errors:
                        row["repair_stop_errors"] = stop_errors
                    log_cleanup_event(events_path, repair_lane, row, stopped_pids=stopped_pids, reason="disabled_lane")
                    log_stop_disabled_event(events_path, repair_lane, row)
                continue
            if str(row.get("status") or "") == "quarantined":
                quarantine_entry = active_quarantines.get(lane_name)
                if quarantine_entry:
                    next_quarantines[lane_name] = quarantine_entry
                if refresh_info.get("contract_changed"):
                    row["repair_action"] = "quarantined_contract_drift_pending"
                    row["repair_launch_contract_refreshed"] = True
                    row["repair_pending_restart_args_changed"] = bool(refresh_info.get("restart_args_changed"))
                    row["repair_pending_state_path_changed"] = bool(refresh_info.get("state_path_changed"))
                    row["repair_pending_enabled_changed"] = bool(refresh_info.get("enabled_changed"))
                    row["repair_pending_restart_group_changed"] = bool(refresh_info.get("restart_group_changed"))
                    row["repair_pending_kind_changed"] = bool(refresh_info.get("kind_changed"))
                    row["reasons"].append("quarantined_contract_drift_pending")
                else:
                    row["repair_action"] = "quarantined"
                continue
            lane_kind = str(repair_lane.get("kind") or "")
            live_open_count = int(row.get("open_count") or 0)
            if str(row.get("status") or "") == "arg_drift" and lane_kind.startswith("live") and live_open_count > 0 and not force_restart:
                row["repair_action"] = "defer_arg_drift_open_positions"
                row["reasons"].append(f"repair_suppressed_open_positions={live_open_count}")
                continue
            if row["status"] in {"ok", "starting"} and not force_restart:
                continue
            if restart_group and restart_group in restarted_groups:
                row["repair_action"] = "restart_group_deferred"
                row["reasons"].append(f"restart_group={restart_group} already_restarted_this_cycle")
                continue
            restart_cfg = restart_policy(str(repair_lane.get("kind") or ""), lane_name)
            restart_count_window = recent_restart_count(
                events_path,
                lane_name,
                lookback_seconds=float(restart_cfg["window_seconds"]),
            )
            if row["status"] != "ok" and not force_restart and restart_count_window >= int(restart_cfg["max_restarts"]):
                quarantined_until_dt = now_dt + timedelta(seconds=int(restart_cfg["quarantine_seconds"]))
                quarantined_until = quarantined_until_dt.isoformat()
                quarantine_reason = (
                    "restart_storm="
                    f"{restart_count_window}/{int(restart_cfg['max_restarts'])}"
                    f" within {int(restart_cfg['window_seconds'])}s"
                )
                row["status"] = "quarantined"
                row["repair_action"] = "quarantine"
                row["quarantine_until"] = quarantined_until
                row["quarantine_reason"] = quarantine_reason
                row["quarantine_restart_count_window"] = restart_count_window
                row["reasons"].append(
                    f"restart_quarantine count={restart_count_window} window_s={int(restart_cfg['window_seconds'])} until={quarantined_until}"
                )
                next_quarantines[lane_name] = {
                    "kind": str(repair_lane.get("kind") or ""),
                    "reason": quarantine_reason,
                    "quarantined_at": now_dt.isoformat(),
                    "quarantined_until": quarantined_until,
                    "restart_count_window": int(restart_count_window),
                    "restart_window_seconds": int(restart_cfg["window_seconds"]),
                    "quarantine_seconds": int(restart_cfg["quarantine_seconds"]),
                    "policy_version": str(restart_cfg["policy_version"]),
                }
                log_quarantine_event(
                    events_path,
                    repair_lane,
                    row,
                    quarantine_until=quarantined_until,
                    restart_count=restart_count_window,
                    window_seconds=int(restart_cfg["window_seconds"]),
                    quarantine_seconds=int(restart_cfg["quarantine_seconds"]),
                    reason=quarantine_reason,
                )
                continue
            stopped_pids: list[int] = []
            stop_errors: list[str] = []
            for pid in row.get("process_ids") or []:
                stopped_pids.append(int(pid))
                stop_result = stop_process(int(pid))
                stop_errors.extend(stop_result.get("errors", []))
            for pid in row.get("conflicting_process_ids") or []:
                stopped_pids.append(int(pid))
                stop_result = stop_process(int(pid))
                stop_errors.extend(stop_result.get("errors", []))
            if stopped_pids:
                row["repair_stopped_pids"] = stopped_pids
                if stop_errors:
                    row["repair_stop_errors"] = stop_errors
                cleanup_reason = "force_restart" if row["status"] == "ok" else "pre_restart_cleanup"
                log_cleanup_event(events_path, repair_lane, row, stopped_pids=stopped_pids, reason=cleanup_reason)
            reset_baseline = None
            if any("source_tick_lag=" in str(reason or "") for reason in (row.get("reasons") or [])):
                reset_reason = next(
                    (str(reason or "") for reason in (row.get("reasons") or []) if "source_tick_lag=" in str(reason or "")),
                    "source_tick_lag",
                )
                reset_baseline = record_reset_baseline(
                    lane_name=str(repair_lane.get("name") or ""),
                    kind=str(repair_lane.get("kind") or ""),
                    state_path=Path(str(row.get("state_path") or "")),
                    reason=reset_reason,
                )
            
            # Process lifecycle: sweep for orphan processes before launching
            lane_magic = None
            lane_state_path = str(repair_lane.get("state_path") or row.get("state_path") or "")
            repair_restart_args = list(repair_lane.get("restart_args") or [])
            for idx, arg in enumerate(repair_restart_args):
                if "--live-magic" in str(arg):
                    if idx + 1 < len(repair_restart_args):
                        try:
                            lane_magic = int(repair_restart_args[idx + 1])
                        except ValueError:
                            pass
                    break
            if refresh_info.get("contract_changed"):
                row["repair_launch_contract_refreshed"] = True
                append_startup_event(
                    events_path,
                    event="repair_launch_contract_refreshed",
                    loop_name=str(loop_name),
                    lane_name=lane_name,
                    restart_args_changed=bool(refresh_info.get("restart_args_changed")),
                    state_path_changed=bool(refresh_info.get("state_path_changed")),
                    enabled_changed=bool(refresh_info.get("enabled_changed")),
                    restart_group_changed=bool(refresh_info.get("restart_group_changed")),
                    kind_changed=bool(refresh_info.get("kind_changed")),
                )
            
            if loop_state_path is not None and lane_magic is not None:
                tracker = load_process_tracker(loop_state_path)
                orphans = sweep_lane_processes(
                    lane_name=lane_name,
                    magic=lane_magic,
                    state_path=lane_state_path,
                    expected_pid=None,  # We haven't launched yet
                )
                if orphans:
                    append_startup_event(
                        events_path,
                        event="orphan_processes_cleaned_before_launch",
                        loop_name=str(loop_name),
                        lane_name=lane_name,
                        orphan_count=len(orphans),
                        orphan_pids=[o["pid"] for o in orphans],
                    )
                    for orphan in orphans:
                        stop_process_forceful(int(orphan["pid"]))
            
            repair_info = start_lane(repair_lane)

            # Idempotency guard: if a duplicate was detected, skip repair logging
            if repair_info.get("skipped_duplicate"):
                row["repair_action"] = "duplicate_already_running"
                row["repair_started_pid"] = repair_info["started_pid"]
                row["status"] = "ok"
                row["reasons"] = [r for r in row.get("reasons") or [] if "child_exited_unexpected" not in str(r)]
                append_startup_event(
                    events_path,
                    event="duplicate_launch_skipped",
                    loop_name=str(loop_name),
                    lane_name=lane_name,
                    existing_pid=repair_info["started_pid"],
                    reason="idempotency_guard_already_running",
                )
                continue

            # Process lifecycle: record the launch
            if loop_state_path is not None:
                tracker = load_process_tracker(loop_state_path)
                record_lane_launch(
                    tracker,
                    lane_name=lane_name,
                    pid=repair_info["started_pid"],
                    magic=lane_magic,
                    state_path=lane_state_path,
                )
                save_process_tracker(loop_state_path, tracker)
            
            if restart_group:
                restarted_groups.add(restart_group)
            row["repair_action"] = "restart" if row["status"] != "ok" else "force_restart"
            row["repair_started_pid"] = repair_info["started_pid"]
            row["repair_stdout_path"] = repair_info["stdout_path"]
            row["repair_stderr_path"] = repair_info["stderr_path"]
            log_repair_event(events_path, lane, row, repair_info, reset_baseline)
    write_quarantine_state(
        quarantine_state_path or default_quarantine_state_path(report_json),
        loop_name=loop_name,
        lanes=next_quarantines,
    )
    write_reports(rows, report_json, report_md)
    return rows


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Check and optionally repair penetration lattice runner liveness.")
    parser.add_argument("--registry", default=str(DEFAULT_REGISTRY))
    parser.add_argument("--report-json", default=str(DEFAULT_REPORT_JSON))
    parser.add_argument("--report-md", default=str(DEFAULT_REPORT_MD))
    parser.add_argument("--events-jsonl", default=str(DEFAULT_EVENTS_JSONL))
    parser.add_argument("--loop-state-json", default=str(DEFAULT_LOOP_STATE_JSON))
    parser.add_argument("--quarantine-state-json", default="")
    parser.add_argument("--loop-name", default="watchdog")
    parser.add_argument("--repair", action="store_true")
    parser.add_argument("--force-restart", action="store_true")
    parser.add_argument("--loop", action="store_true")
    parser.add_argument("--interval-seconds", type=float, default=30.0)
    parser.add_argument("--skip-shared-operator-refresh", action="store_true")
    parser.add_argument("--lanes", nargs="*", default=None)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    registry_path = Path(args.registry)
    report_json = Path(args.report_json)
    report_md = Path(args.report_md)
    events_path = Path(args.events_jsonl)
    loop_state_path = Path(args.loop_state_json)
    loop_lock_path = LOOP_LOCK_DIR / f"{loop_state_path.stem}.lock"
    quarantine_state_path = Path(args.quarantine_state_json) if str(args.quarantine_state_json or "").strip() else default_quarantine_state_path(loop_state_path)
    lanes_filter = {lane for lane in (args.lanes or [])}
    consecutive_failures = 0
    loop_started_at = utc_now_iso()
    append_startup_event(
        events_path,
        event="args_parsed",
        loop_name=str(args.loop_name),
        loop_mode="loop" if args.loop else "single",
        interval_seconds=float(args.interval_seconds),
        loop_state_path=str(loop_state_path),
        lane_count=len(lanes_filter),
        skip_shared_operator_refresh=bool(args.skip_shared_operator_refresh),
    )
    loop_lock_acquired = False
    mt5_session_ready = False
    mt5_initialized_globally = False

    try:
        loop_lock_acquired, loop_lock_owner = acquire_loop_lock(
            loop_lock_path,
            loop_name=str(args.loop_name),
            stale_after_seconds=max(float(args.interval_seconds) * 6.0, 180.0),
        )
        if not loop_lock_acquired:
            owner_pid = int(loop_lock_owner.get("pid") or 0)
            owner_text = f" owner_pid={owner_pid}" if owner_pid > 0 else ""
            append_startup_event(
                events_path,
                event="duplicate_launch_blocked",
                loop_name=str(args.loop_name),
                owner_pid=owner_pid,
                owner_loop_name=str(loop_lock_owner.get("loop_name") or ""),
            )
            print(f"watchdog loop '{args.loop_name}' skipped duplicate launch;{owner_text}".rstrip(";"))
            return 0

        append_startup_event(
            events_path,
            event="lock_acquired",
            loop_name=str(args.loop_name),
            lock_path=str(loop_lock_path),
        )
        
        # Process lifecycle reconciliation: reconcile tracker with actual processes
        tracker = load_process_tracker(loop_state_path)
        reconciliation = reconcile_on_startup(tracker)
        save_process_tracker(loop_state_path, reconciliation["reconciled_tracker"])
        
        # Log reconciliation results
        append_startup_event(
            events_path,
            event="process_lifecycle_reconciliation",
            loop_name=str(args.loop_name),
            stale_entries_found=len(reconciliation["stale_entries"]),
            orphaned_processes_found=len(reconciliation["orphaned_lanes"]),
            stale_entries=[
                {"lane": e["lane"], "stale_pid": e["stale_pid"], "launched_at": e["launched_at"]}
                for e in reconciliation["stale_entries"]
            ],
            orphaned_processes=[
                {"pid": o["pid"], "identified_as": o["identified_as"]}
                for o in reconciliation["orphaned_lanes"]
            ],
        )
        
        # Kill orphaned watchdog children
        for orphan in reconciliation["orphaned_lanes"]:
            orphan_pid = int(orphan["pid"])
            append_startup_event(
                events_path,
                event="orphan_process_terminated",
                loop_name=str(args.loop_name),
                orphan_pid=orphan_pid,
                orphan_cmdline=orphan.get("cmdline", ""),
            )
            stop_process_forceful(orphan_pid)
        
        write_loop_state(
            loop_state_path,
            loop_name=str(args.loop_name),
            status="starting",
            args=args,
            loop_started_at=loop_started_at,
            consecutive_failures=consecutive_failures,
        )

        if args.loop:
            # Check if any lane in registry needs MT5
            registry = read_registry(registry_path)
            if lanes_filter:
                registry = [lane for lane in registry if lane["name"] in lanes_filter]
            mt5_session_required = any(
                str(lane.get("kind", "") or "").strip().lower() in {"live_crypto", "shadow_crypto", "shadow_unified"}
                for lane in registry
            )
            append_startup_event(
                events_path,
                event="mt5_session_check",
                loop_name=str(args.loop_name),
                mt5_session_required=bool(mt5_session_required),
            )
            if mt5_session_required:
                mt5_session_ready, _mt5_connection = mt5_terminal_guard.initialize_mt5(mt5_module=mt5)
                mt5_initialized_globally = True
                append_startup_event(
                    events_path,
                    event="mt5_session_ready",
                    loop_name=str(args.loop_name),
                    mt5_session_ready=bool(mt5_session_ready),
                )

        while True:
            cycle_started_at = utc_now_iso()
            try:
                append_startup_event(
                    events_path,
                    event="cycle_begin",
                    loop_name=str(args.loop_name),
                    cycle_started_at=cycle_started_at,
                )
                rows = run_watchdog(
                    registry_path,
                    report_json,
                    report_md,
                    events_path,
                    bool(args.repair),
                    lanes_filter or None,
                    bool(args.force_restart),
                    quarantine_state_path,
                    loop_state_path,
                    str(args.loop_name),
                    loop_started_at,
                    not bool(args.skip_shared_operator_refresh),
                    mt5_session_ready=mt5_session_ready if mt5_initialized_globally else None,
                )
                consecutive_failures = 0
                write_loop_state(
                    loop_state_path,
                    loop_name=str(args.loop_name),
                    status="ok",
                    args=args,
                    loop_started_at=loop_started_at,
                    cycle_started_at=cycle_started_at,
                    cycle_completed_at=utc_now_iso(),
                    consecutive_failures=consecutive_failures,
                    rows=rows,
                )
            except Exception as exc:
                consecutive_failures += 1
                append_startup_event(
                    events_path,
                    event="cycle_exception",
                    loop_name=str(args.loop_name),
                    exception_type=type(exc).__name__,
                    error=" ".join(str(exc).split()),
                    consecutive_failures=consecutive_failures,
                    cycle_started_at=cycle_started_at,
                )
                write_loop_state(
                    loop_state_path,
                    loop_name=str(args.loop_name),
                    status="error",
                    args=args,
                    loop_started_at=loop_started_at,
                    cycle_started_at=cycle_started_at,
                    cycle_completed_at=utc_now_iso(),
                    consecutive_failures=consecutive_failures,
                    error=exc,
                )
                if not args.loop:
                    raise
                time.sleep(max(5.0, float(args.interval_seconds)))
                continue
            if not args.loop:
                break
            time.sleep(max(5.0, float(args.interval_seconds)))
    except Exception as exc:
        append_startup_event(
            events_path,
            event="main_unexpected_failure",
            loop_name=str(args.loop_name),
            exception_type=type(exc).__name__,
            error=" ".join(str(exc).split()),
            traceback_text="".join(traceback.format_exception(type(exc), exc, exc.__traceback__)).strip(),
            loop_started_at=loop_started_at,
        )
        try:
            write_loop_state(
                loop_state_path,
                loop_name=str(args.loop_name),
                status="error",
                args=args,
                loop_started_at=loop_started_at,
                cycle_started_at=utc_now_iso(),
                cycle_completed_at=utc_now_iso(),
                consecutive_failures=consecutive_failures + 1,
                error=exc,
            )
        except Exception:
            pass
        raise
    finally:
        if mt5_initialized_globally and mt5_session_ready:
            mt5.shutdown()
        if loop_lock_acquired:
            release_loop_lock(loop_lock_path)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
