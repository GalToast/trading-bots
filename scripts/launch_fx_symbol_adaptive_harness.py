#!/usr/bin/env python3
from __future__ import annotations

"""Register and optionally launch dedicated live FX adaptive harness lanes.

This is the cutover path from bundled FX seats toward one lane per symbol.
By default it prints the exact contract without mutating the registry or
launching anything. Use `--apply` to update registry/watchdog, and `--launch`
to start the lane after the contract is written.
"""

import argparse
import ctypes
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import MetaTrader5 as mt5

import live_penetration_lattice_mirror as live_mirror
import mt5_terminal_guard

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from live_penetration_lattice_shadow import BoundedConfig, RawConfig, default_apex_mix


REGISTRY_PATH = ROOT / "configs" / "penetration_lattice_runner_registry.json"
WATCHDOG_PATH = ROOT / "configs" / "watchdog_groups.json"
SHAPE_LIBRARY_PATH = ROOT / "configs" / "adaptive_lattice_shape_library.json"
REGIME_PATH = ROOT / "reports" / "regime_classification_live.json"
WATCHDOG_GROUP = "fx_watchdog"

PINNED_BATCH_SYMBOLS = ("EURUSD", "GBPUSD", "NZDUSD", "USDJPY")
PINNED_LIVE_MAGICS = {
    "EURUSD": 941885,
    "GBPUSD": 941777,
    "NZDUSD": 941778,
    "USDJPY": 941888,
}
COMMENT_PREFIXES = {
    "EURUSD": "PLIVE-EUR",
    "GBPUSD": "PLIVE-GBP",
    "NZDUSD": "PLIVE-NZD",
    "USDJPY": "PLIVE-JPY",
    "USDCHF": "PLIVE-CHF",
}

RAW_ADAPTIVE_SHAPE_ID = {
    "GBPUSD": "gbpusd_trend_harvest_v1",
    "EURUSD": "eurusd_mixed_floor_v1",
    "NZDUSD": "nzdusd_asym_probe_v1",
}

RAW_SYMBOL_TUNING = {
    "EURUSD": {
        "raw_close_alpha": 0.5,
        "raw_rearm_variant": "rearm_lvl2_exc2",
        "raw_rearm_cooldown_bars": 12,
        "raw_rearm_momentum_gate": False,
        "raw_close_style": "all_profitable",
        "raw_sell_gap": 1,
        "raw_buy_gap": 1,
        "max_floating_loss_usd": -50.0,
        "max_entry_spread_ratio": 0.3,
        "min_positive_close_profit_usd": 0.25,
        "max_lattice_window_bars": 288,
    },
    "GBPUSD": {
        "raw_close_alpha": 0.5,
        "raw_rearm_variant": "rearm_lvl2_exc2",
        "raw_rearm_cooldown_bars": 12,
        "raw_rearm_momentum_gate": False,
        "max_floating_loss_usd": -50.0,
        "max_entry_spread_ratio": 0.3,
        "min_positive_close_profit_usd": 0.25,
        "max_lattice_window_bars": 288,
    },
    "NZDUSD": {
        "raw_close_alpha": 1.0,
        "raw_rearm_variant": "rearm_lvl2_exc1",
        "raw_rearm_cooldown_bars": 12,
        "raw_rearm_momentum_gate": True,
        "max_floating_loss_usd": -33.0,
        "max_entry_spread_ratio": 0.35,
        "min_positive_close_profit_usd": 0.25,
        "max_lattice_window_bars": 288,
    },
}

BOUNDED_SYMBOL_TUNING = {
    "USDJPY": {
        "bounded_rearm_variant": "rearm_lvl2_exc2",
        "bounded_close_gap": 2,
        "max_entry_spread_ratio": 1.20,
        "max_floating_loss_usd": -15.0,
        "min_positive_close_profit_usd": 0.25,
    },
    "USDCHF": {
        "bounded_rearm_variant": "rearm_lvl2_exc2",
        "bounded_close_gap": 2,
        "max_entry_spread_ratio": 0.30,
        "max_floating_loss_usd": -15.0,
        "min_positive_close_profit_usd": 0.25,
    },
}


def pip_price_unit(symbol: str) -> float:
    return 0.01 if symbol.upper().endswith("JPY") else 0.0001


def state_prefix(symbol: str) -> str:
    return f"penetration_lattice_live_{symbol.lower()}_adaptive_harness"


def _resolved_lane_runtime(symbol: str, *, live_magic: int | None = None) -> dict[str, Any]:
    resolved_magic = resolve_live_magic(symbol, live_magic)
    default_magic = PINNED_LIVE_MAGICS.get(symbol)
    default_prefix = state_prefix(symbol)
    lane_prefix = f"live_{symbol.lower()}_adaptive_harness_"
    lane_name = f"{lane_prefix}{resolved_magic}"
    if default_magic is not None and int(default_magic) == int(resolved_magic):
        state_base = default_prefix
    else:
        state_base = f"{default_prefix}_{resolved_magic}"
    return {
        "lane_name": lane_name,
        "state_path": f"reports/{state_base}_state.json",
        "event_path": f"reports/{state_base}_events.jsonl",
        "exec_state_path": f"reports/{state_base}_exec_state.json",
        "exec_log_path": f"reports/{state_base}_exec_events.jsonl",
        "live_magic": int(resolved_magic),
        "family_prefix": lane_prefix,
    }


def resolve_symbols(args: argparse.Namespace) -> list[str]:
    if args.all:
        return list(PINNED_BATCH_SYMBOLS)
    selected = [str(symbol or "").upper() for symbol in args.symbol]
    if not selected:
        raise SystemExit("Select at least one --symbol or pass --all.")
    return selected


def resolve_live_magic(symbol: str, explicit_magic: int | None) -> int:
    if explicit_magic is not None:
        return explicit_magic
    try:
        return int(PINNED_LIVE_MAGICS[symbol])
    except KeyError as exc:
        raise SystemExit(f"No pinned live magic for {symbol}; pass --live-magic explicitly.") from exc


def comment_prefix_for(symbol: str) -> str:
    return COMMENT_PREFIXES.get(symbol, f"PLIVE-{symbol[:3]}")


def resolve_raw_close_gap(cfg: RawConfig, tuning: dict[str, Any], *, side: str) -> int:
    side_key = str(side or "").strip().lower()
    tuning_key = f"raw_{side_key}_gap"
    if tuning_key in tuning:
        return max(0, int(tuning[tuning_key]))
    cfg_gap = getattr(cfg, "sell_gap" if side_key == "sell" else "buy_gap", None)
    if cfg_gap is not None:
        return max(0, int(cfg_gap))
    close_mode = str(getattr(cfg, "close_mode", "") or "").strip().lower()
    return 1 if close_mode == "one_level" else 2


def load_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def resolve_raw_adaptive_shape(symbol: str) -> dict[str, Any]:
    shape_id = RAW_ADAPTIVE_SHAPE_ID.get(symbol)
    if not shape_id:
        return {}
    library = load_json(SHAPE_LIBRARY_PATH)
    symbol_payload = dict((library.get("symbols") or {}).get(symbol) or {})
    for shape in list(symbol_payload.get("candidate_shapes") or []):
        if str(shape.get("shape_id") or "") == shape_id:
            return dict(shape)
    raise SystemExit(f"Adaptive shape library is missing {shape_id} for {symbol}.")


def safe_float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def resolve_regime_row(symbol: str) -> dict[str, Any]:
    regime_payload = load_json(REGIME_PATH)
    for row in list(regime_payload.get("symbols") or []):
        if str(row.get("symbol") or "").upper() == symbol.upper():
            return dict(row)
    return {}


def resolve_raw_step_contract(symbol: str, adaptive_shape: dict[str, Any]) -> dict[str, Any]:
    step_method = dict(adaptive_shape.get("step_method") or {})
    if str(step_method.get("kind") or "") != "atr_multiple_asymmetric":
        return {}
    regime_row = resolve_regime_row(symbol)
    current_atr = safe_float(regime_row.get("current_atr"))
    if current_atr is None or current_atr <= 0:
        return {}
    step_buy = round(current_atr * float(step_method.get("buy_coeff", 1.0) or 1.0), 5)
    step_sell = round(current_atr * float(step_method.get("sell_coeff", 1.0) or 1.0), 5)
    step_avg = round((step_buy + step_sell) / 2.0, 5)
    return {
        "step_buy": step_buy,
        "step_sell": step_sell,
        "step_avg": step_avg,
        "step_source": "regime_classification_live.current_atr * adaptive_shape_coeffs",
        "current_atr": round(current_atr, 5),
    }


def build_raw_lane(symbol: str, cfg: RawConfig, runtime: dict[str, Any]) -> dict[str, Any]:
    tuning = dict(
        RAW_SYMBOL_TUNING.get(
            symbol,
            {
                "raw_close_alpha": 0.5,
                "raw_rearm_variant": "rearm_lvl2_exc2",
                "raw_rearm_cooldown_bars": 12,
                "raw_rearm_momentum_gate": False,
                "max_floating_loss_usd": -50.0,
                "max_entry_spread_ratio": 0.3,
                "min_positive_close_profit_usd": 0.25,
                "max_lattice_window_bars": 288,
            },
        )
    )
    adaptive_shape = resolve_raw_adaptive_shape(symbol)
    adaptive_close = dict(adaptive_shape.get("close") or {})
    adaptive_rearm = dict(adaptive_shape.get("rearm") or {})
    step_contract = resolve_raw_step_contract(symbol, adaptive_shape)
    raw_close_alpha = float(adaptive_close.get("alpha", tuning["raw_close_alpha"]))
    raw_close_style = str(tuning.get("raw_close_style", adaptive_close.get("style") or "all_profitable"))
    raw_rearm_variant = str(adaptive_rearm.get("variant") or tuning["raw_rearm_variant"])
    raw_rearm_cooldown_bars = int(adaptive_rearm.get("cooldown_bars", tuning["raw_rearm_cooldown_bars"]))
    raw_sell_gap = (
        max(0, int(tuning["raw_sell_gap"]))
        if "raw_sell_gap" in tuning
        else max(0, int(adaptive_close["sell_gap"]))
        if "sell_gap" in adaptive_close
        else resolve_raw_close_gap(cfg, tuning, side="sell")
    )
    raw_buy_gap = (
        max(0, int(tuning["raw_buy_gap"]))
        if "raw_buy_gap" in tuning
        else max(0, int(adaptive_close["buy_gap"]))
        if "buy_gap" in adaptive_close
        else resolve_raw_close_gap(cfg, tuning, side="buy")
    )
    step_ceiling = float(cfg.step_pips) * pip_price_unit(symbol)
    proven_step_ceiling = None if step_contract else step_ceiling
    proven_step_buy_ceiling = step_contract.get("step_buy")
    proven_step_sell_ceiling = step_contract.get("step_sell")
    state_path = str(runtime["state_path"])
    event_path = str(runtime["event_path"])
    exec_state_path = str(runtime["exec_state_path"])
    exec_log_path = str(runtime["exec_log_path"])
    lane_name = str(runtime["lane_name"])
    live_magic = int(runtime["live_magic"])

    restart_args: list[str] = [
        "scripts/live_penetration_lattice_tick_shadow.py",
        "--direct-live",
        "--symbols",
        symbol,
        "--raw-close-alpha",
        str(raw_close_alpha),
        "--raw-close-style",
        raw_close_style,
        "--raw-rearm-variant",
        raw_rearm_variant,
        "--raw-rearm-cooldown-bars",
        str(raw_rearm_cooldown_bars),
        "--raw-sell-gap",
        str(raw_sell_gap),
        "--raw-buy-gap",
        str(raw_buy_gap),
        "--step-buy",
        str(step_contract.get("step_buy", step_ceiling)),
        "--step-sell",
        str(step_contract.get("step_sell", step_ceiling)),
        "--state-path",
        state_path,
        "--event-path",
        event_path,
        "--direct-exec-state-path",
        exec_state_path,
        "--direct-exec-log-path",
        exec_log_path,
        "--live-magic",
        str(live_magic),
        "--live-comment-prefix",
        comment_prefix_for(symbol),
        "--max-floating-loss-usd",
        str(tuning["max_floating_loss_usd"]),
        "--min-positive-close-profit-usd",
        str(tuning["min_positive_close_profit_usd"]),
        "--positive-only-closes",
        "--max-entry-spread-ratio",
        str(tuning["max_entry_spread_ratio"]),
        "--max-lattice-window-bars",
        str(tuning["max_lattice_window_bars"]),
        "--live-volume",
        "0.01",
        "--poll-seconds",
        "1",
        "--session-gate",
        "--adaptive-overlay-autopilot",
    ]
    if proven_step_ceiling is not None:
        restart_args.extend(
            [
                "--proven-step-ceiling",
                f"{float(proven_step_ceiling):.5f}".rstrip("0").rstrip("."),
            ]
        )
    if proven_step_buy_ceiling is not None:
        restart_args.extend(
            [
                "--proven-step-buy-ceiling",
                f"{float(proven_step_buy_ceiling):.5f}".rstrip("0").rstrip("."),
            ]
        )
    if proven_step_sell_ceiling is not None:
        restart_args.extend(
            [
                "--proven-step-sell-ceiling",
                f"{float(proven_step_sell_ceiling):.5f}".rstrip("0").rstrip("."),
            ]
        )
    if bool(tuning["raw_rearm_momentum_gate"]):
        restart_args.append("--raw-rearm-momentum-gate")

    return {
        "name": lane_name,
        "kind": "live_fx",
        "symbol": symbol,
        "engine_family": "raw",
        "state_path": state_path,
        "event_path": event_path,
        "poll_seconds": 1,
        "stale_after_seconds": 45,
        "process_match_substrings": [
            "scripts/live_penetration_lattice_tick_shadow.py",
            state_path,
            str(live_magic),
        ],
        "restart_args": restart_args,
        "contract_meta": {
            "adaptive_shape_id": str(adaptive_shape.get("shape_id") or ""),
            "raw_step_pips": float(cfg.step_pips),
            "raw_step_price_units": step_ceiling,
            "step_source": str(step_contract.get("step_source") or "default_apex_mix"),
            "step_buy_price_units": float(step_contract.get("step_buy", step_ceiling)),
            "step_sell_price_units": float(step_contract.get("step_sell", step_ceiling)),
            "current_atr": step_contract.get("current_atr"),
            "max_open_per_side": int(cfg.max_open_per_side),
            "close_mode": str(cfg.close_mode),
            "raw_close_alpha": raw_close_alpha,
            "raw_close_style": raw_close_style,
            "raw_rearm_variant": raw_rearm_variant,
            "raw_rearm_cooldown_bars": raw_rearm_cooldown_bars,
            "raw_sell_gap": raw_sell_gap,
            "raw_buy_gap": raw_buy_gap,
            "min_positive_close_profit_usd": float(tuning["min_positive_close_profit_usd"]),
            "positive_only_closes": True,
            "live_magic": live_magic,
        },
    }


def build_bounded_lane(symbol: str, cfg: BoundedConfig, runtime: dict[str, Any]) -> dict[str, Any]:
    tuning = dict(
        BOUNDED_SYMBOL_TUNING.get(
            symbol,
            {
                "bounded_rearm_variant": "rearm_lvl2_exc2",
                "bounded_close_gap": 2,
                "max_entry_spread_ratio": 0.30,
                "max_floating_loss_usd": float(cfg.max_floating_loss_usd),
                "min_positive_close_profit_usd": 0.25,
            },
        )
    )
    state_path = str(runtime["state_path"])
    event_path = str(runtime["event_path"])
    exec_state_path = str(runtime["exec_state_path"])
    exec_log_path = str(runtime["exec_log_path"])
    lane_name = str(runtime["lane_name"])
    live_magic = int(runtime["live_magic"])

    restart_args = [
        "scripts/live_penetration_lattice_tick_shadow.py",
        "--direct-live",
        "--symbols",
        symbol,
        "--bounded-rearm-variant",
        str(tuning["bounded_rearm_variant"]),
        "--bounded-close-gap",
        str(tuning["bounded_close_gap"]),
        "--bounded-vwap-lookback",
        str(int(cfg.vwap_lookback)),
        "--bounded-regime-lookback-bars",
        str(int(cfg.regime_lookback_bars)),
        "--bounded-max-range-pips",
        str(float(cfg.max_range_pips)),
        "--bounded-breakout-buffer-pips",
        str(float(cfg.breakout_buffer_pips)),
        "--bounded-max-lattice-window-bars",
        str(int(cfg.max_lattice_window_bars)),
        "--bounded-cooldown-bars",
        str(int(cfg.cooldown_bars)),
        "--state-path",
        state_path,
        "--event-path",
        event_path,
        "--direct-exec-state-path",
        exec_state_path,
        "--direct-exec-log-path",
        exec_log_path,
        "--live-magic",
        str(live_magic),
        "--live-comment-prefix",
        comment_prefix_for(symbol),
        "--max-floating-loss-usd",
        str(tuning["max_floating_loss_usd"]),
        "--min-positive-close-profit-usd",
        str(tuning["min_positive_close_profit_usd"]),
        "--positive-only-closes",
        "--max-entry-spread-ratio",
        str(tuning["max_entry_spread_ratio"]),
        "--live-volume",
        "0.01",
        "--poll-seconds",
        "1",
        "--session-gate",
        "--adaptive-overlay-autopilot",
    ]

    return {
        "name": lane_name,
        "kind": "live_fx",
        "symbol": symbol,
        "engine_family": "bounded",
        "state_path": state_path,
        "event_path": event_path,
        "poll_seconds": 1,
        "stale_after_seconds": 45,
        "process_match_substrings": [
            "scripts/live_penetration_lattice_tick_shadow.py",
            state_path,
            str(live_magic),
        ],
        "restart_args": restart_args,
        "contract_meta": {
            "bounded_step_pips": float(cfg.step_pips),
            "max_open_per_side": int(cfg.max_open_per_side),
            "max_floating_loss_usd": float(cfg.max_floating_loss_usd),
            "vwap_lookback": int(cfg.vwap_lookback),
            "regime_lookback_bars": int(cfg.regime_lookback_bars),
            "max_range_pips": float(cfg.max_range_pips),
            "breakout_buffer_pips": float(cfg.breakout_buffer_pips),
            "max_lattice_window_bars": int(cfg.max_lattice_window_bars),
            "cooldown_bars": int(cfg.cooldown_bars),
            "min_positive_close_profit_usd": float(tuning["min_positive_close_profit_usd"]),
            "positive_only_closes": True,
            "live_magic": live_magic,
        },
    }


def build_lane_contract(symbol: str, live_magic: int | None = None) -> dict[str, Any]:
    mix = default_apex_mix()
    if symbol not in mix:
        raise SystemExit(f"{symbol} is not present in default_apex_mix().")
    mode, cfg = mix[symbol]
    runtime = _resolved_lane_runtime(symbol, live_magic=live_magic)
    if mode == "raw_close2":
        return build_raw_lane(symbol, cfg, runtime)
    if mode == "v3_bounded":
        return build_bounded_lane(symbol, cfg, runtime)
    raise SystemExit(f"Unsupported FX engine family for {symbol}: {mode}")
def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def upsert_registry_lane(registry: dict[str, Any], lane: dict[str, Any], *, enabled: bool | None) -> bool:
    lanes = list(registry.get("lanes") or [])
    registry["lanes"] = lanes
    lane_name = str(lane["name"])
    new_lane = {k: v for k, v in lane.items() if k != "contract_meta"}
    if enabled is not None:
        new_lane["enabled"] = bool(enabled)
        new_lane["pause_note"] = "" if enabled else "awaiting_cutover_from_shared_fx_seats"
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
    fx_group = groups.setdefault(WATCHDOG_GROUP, {})
    lanes = list(fx_group.get("lanes") or [])
    if lane_name not in lanes:
        lanes.append(lane_name)
        fx_group["lanes"] = lanes
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
        if not name.startswith(str(family_prefix)) or name == str(keep_lane_name):
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
    restart_args = list(lane_row.get("restart_args") or [])
    for index, arg in enumerate(restart_args):
        if str(arg) != "--live-magic":
            continue
        try:
            return int(restart_args[index + 1])
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
    parser = argparse.ArgumentParser(description="Dedicated live FX adaptive harness launcher")
    parser.add_argument("--symbol", action="append", default=[], help="FX symbol to target (repeatable)")
    parser.add_argument("--all", action="store_true", help="Use the pinned batch: EURUSD GBPUSD NZDUSD USDJPY")
    parser.add_argument("--live-magic", type=int, default=None, help="Explicit live magic for a single-symbol launch")
    parser.add_argument("--apply", action="store_true", help="Write or refresh registry/watchdog contracts")
    parser.add_argument("--launch", action="store_true", help="Launch the lane after apply")
    parser.add_argument("--cutover", action="store_true", help="When used with --live-magic, pause other same-family registry rows for that symbol and keep only the override row active.")
    parser.add_argument("--disable", action="store_true", help="When applying, register rows disabled for later cutover")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.live_magic is not None and (args.all or len(args.symbol) != 1):
        raise SystemExit("--live-magic may only be used with exactly one --symbol.")
    if args.cutover and args.live_magic is None:
        raise SystemExit("--cutover requires --live-magic.")
    if args.cutover and args.disable:
        raise SystemExit("--cutover cannot be combined with --disable.")

    symbols = resolve_symbols(args)
    lanes = [build_lane_contract(symbol, live_magic=args.live_magic) for symbol in symbols]

    registry_changed = False
    watchdog_changed = False
    if args.apply:
        registry = load_json(REGISTRY_PATH)
        watchdog = load_json(WATCHDOG_PATH)
        enabled = not args.disable
        for symbol, lane in zip(symbols, lanes):
            if args.cutover:
                runtime = _resolved_lane_runtime(symbol, live_magic=args.live_magic)
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
            registry_changed = upsert_registry_lane(registry, lane, enabled=enabled) or registry_changed
            watchdog_changed = ensure_watchdog_membership(watchdog, str(lane["name"])) or watchdog_changed
        if registry_changed:
            write_json(REGISTRY_PATH, registry)
        if watchdog_changed:
            write_json(WATCHDOG_PATH, watchdog)

    launch_rows: list[dict[str, Any]] = []
    if args.launch:
        if not args.apply:
            raise SystemExit("--launch requires --apply.")
        if args.disable:
            raise SystemExit("--launch cannot be combined with --disable.")
        for lane in lanes:
            started, pid = launch_lane(lane)
            launch_rows.append(
                {
                    "lane": lane["name"],
                    "started": started,
                    "pid": pid,
                }
            )

    payload = {
        "mode": "apply" if args.apply else "dry_run",
        "registry_changed": registry_changed,
        "watchdog_changed": watchdog_changed,
        "lanes": lanes,
        "launch": launch_rows,
    }
    print(json.dumps(payload, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
