#!/usr/bin/env python3
from __future__ import annotations

import csv
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import MetaTrader5 as mt5

import mt5_terminal_guard

from clean_forward_baselines import load_reset_baselines, snapshot_from_state_payload
from supervision_policy import exact_fire_policy


ROOT = Path(__file__).resolve().parent.parent
REGISTRY_PATH = ROOT / "configs" / "penetration_lattice_runner_registry.json"
WATCHDOG_GROUPS_CONFIG = ROOT / "configs" / "watchdog_groups.json"
WATCHDOG_JSON = ROOT / "reports" / "penetration_lattice_runner_watchdog.json"
STATE_JSON = ROOT / "reports" / "execution_monitor_state.json"
REPORT_JSON = ROOT / "reports" / "execution_monitor_report.json"
REPORT_MD = ROOT / "reports" / "execution_monitor_report.md"
COINBASE_RATIO_FORWARD_CSV = ROOT / "reports" / "coinbase_ratio_forward_review.csv"
COINBASE_RATIO_PROOF_READINESS_CSV = ROOT / "reports" / "coinbase_ratio_proof_readiness.csv"
COINBASE_SPOT_RSI_FORWARD_CSV = ROOT / "reports" / "coinbase_spot_rsi_forward_review.csv"
COINBASE_BURST_FORWARD_CSV = ROOT / "reports" / "coinbase_burst_forward_review.csv"
COINBASE_EXPERIMENTAL_FORWARD_CSV = ROOT / "reports" / "coinbase_experimental_forward_review.csv"
BTCUSD_H1_STEP_FORWARD_CSV = ROOT / "reports" / "btcusd_h1_step_forward_review.csv"
FX_GRADUATION_READINESS_JSON = ROOT / "reports" / "fx_graduation_readiness.json"
ETH_M15_WARP_READINESS_JSON = ROOT / "reports" / "eth_m15_warp_readiness.json"
CRYPTO_M15_WARP_READINESS_JSON = ROOT / "reports" / "crypto_m15_warp_readiness.json"

TRADE_ACTIONS = {"open", "close", "open_ticket", "close_ticket", "open_sleeve", "close_sleeve"}


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def utc_now_iso() -> str:
    return utc_now().isoformat()


def parse_iso(value: str | None) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None


def load_json(path: Path) -> Any:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        try:
            payload, _ = json.JSONDecoder().raw_decode(path.read_text(encoding="utf-8", errors="ignore"))
            return payload
        except Exception:
            return {}
    except Exception:
        return {}


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")


def file_last_write_iso(path: Path) -> str:
    try:
        return datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc).isoformat()
    except Exception:
        return ""


def read_registry(path: Path) -> list[dict[str, Any]]:
    payload = load_json(path)
    lanes = payload.get("lanes") if isinstance(payload, dict) else []
    return [lane for lane in lanes if isinstance(lane, dict) and lane.get("name")]


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


def watchdog_rows_by_name(path: Path) -> dict[str, dict[str, Any]]:
    payload = load_json(path)
    rows = payload.get("rows") if isinstance(payload, dict) else []
    out: dict[str, dict[str, Any]] = {}
    for row in rows or []:
        name = str(row.get("name") or "")
        if name:
            out[name] = row
    return out


def watchdog_group_report_paths() -> list[Path]:
    payload = load_json(WATCHDOG_GROUPS_CONFIG)
    groups = payload.get("groups") if isinstance(payload, dict) else {}
    if not isinstance(groups, dict):
        return []
    return [
        ROOT / "reports" / "watchdog" / f"{str(group_name)}_report.json"
        for group_name in sorted(groups.keys())
        if str(group_name or "").strip()
    ]


def merged_watchdog_rows() -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for path in watchdog_group_report_paths():
        for name, row in watchdog_rows_by_name(path).items():
            out[name] = row
    for name, row in watchdog_rows_by_name(WATCHDOG_JSON).items():
        out.setdefault(name, row)
    return out


def engine_payload(state_payload: dict[str, Any]) -> dict[str, Any]:
    if isinstance(state_payload.get("engine"), dict):
        return state_payload["engine"]
    if isinstance(state_payload.get("state"), dict):
        return state_payload["state"]
    return {}


def extract_primary_symbol_state(state_payload: dict[str, Any]) -> dict[str, Any] | None:
    symbols = state_payload.get("symbols")
    if not isinstance(symbols, dict) or not symbols:
        return None
    first_key = next(iter(symbols))
    symbol_state = symbols.get(first_key)
    return symbol_state if isinstance(symbol_state, dict) else None


def extract_multi_symbol_metrics(state_payload: dict[str, Any]) -> dict[str, Any] | None:
    symbols = state_payload.get("symbols")
    if not isinstance(symbols, dict) or not symbols:
        return None
    symbol_states = [row for row in symbols.values() if isinstance(row, dict)]
    if len(symbol_states) <= 1:
        return None
    return {
        "symbol": "",
        "open_count": sum(_coerce_count(row.get("open_tickets"), default=0) for row in symbol_states),
        "close_count": sum(_coerce_int(row.get("realized_closes"), default=0) for row in symbol_states),
        "last_bar_time": max(_coerce_int(row.get("last_bar_time"), default=0) for row in symbol_states),
        "last_tick_msc": max(_coerce_int(row.get("last_tick_msc"), default=0) for row in symbol_states),
        "next_buy_level": 0.0,
        "next_sell_level": 0.0,
        "max_open_total": sum(_coerce_int(row.get("max_open_total"), default=0) for row in symbol_states),
        "rearm_opens": sum(_coerce_int(row.get("rearm_opens"), default=0) for row in symbol_states),
        "rearm_token_count": sum(_coerce_count(row.get("rearm_tokens"), default=0) for row in symbol_states),
        "anchor_resets": sum(_coerce_int(row.get("anchor_resets"), default=0) for row in symbol_states),
        "anchor_resets_flat": sum(_coerce_int(row.get("anchor_resets_flat"), default=0) for row in symbol_states),
        "anchor_resets_risk": sum(_coerce_int(row.get("anchor_resets_risk"), default=0) for row in symbol_states),
        "lattice_started_time": max(_coerce_int(row.get("lattice_started_time"), default=0) for row in symbol_states),
        "max_floating_loss_usd": min(
            _coerce_float(row.get("max_floating_loss_usd"), default=0.0) for row in symbol_states
        ),
        "mode": "multi_symbol_aggregate",
    }


def generic_open_count(engine: dict[str, Any]) -> int:
    if isinstance(engine.get("open_count"), int):
        return int(engine.get("open_count") or 0)
    details = engine.get("per_coin_details")
    if isinstance(details, dict):
        return sum(1 for row in details.values() if isinstance(row, dict) and bool(row.get("in_position")))
    if engine.get("position") or engine.get("current_position"):
        return 1
    if str(engine.get("pos") or "").lower() == "active":
        return 1
    return 0


def generic_close_count(engine: dict[str, Any]) -> int:
    return int(engine.get("closes") or engine.get("realized_closes") or engine.get("total_closes") or 0)


def _coerce_int(value: Any, *, default: int = 0) -> int:
    if value is None:
        return default
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, (int, float)):
        return int(value)
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return default
        try:
            return int(float(text))
        except ValueError:
            return default
    try:
        return int(value)
    except Exception:
        return default


def _coerce_float(value: Any, *, default: float = 0.0) -> float:
    if value is None:
        return default
    if isinstance(value, bool):
        return float(value)
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return default
        try:
            return float(text)
        except ValueError:
            return default
    try:
        return float(value)
    except Exception:
        return default


def _coerce_bool(value: Any, *, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    text = str(value or "").strip().lower()
    if text in {"1", "true", "yes", "y", "on"}:
        return True
    if text in {"0", "false", "no", "n", "off"}:
        return False
    return bool(default)


def _coerce_count(value: Any, *, default: int = 0) -> int:
    if isinstance(value, list):
        return len(value)
    if isinstance(value, dict):
        return max(0, int(len(value)))
    return _coerce_int(value, default=default)


def extract_state_metrics(state_payload: dict[str, Any]) -> dict[str, Any]:
    multi_symbol_metrics = extract_multi_symbol_metrics(state_payload)
    if multi_symbol_metrics is not None:
        return multi_symbol_metrics
    symbol_state = extract_primary_symbol_state(state_payload)
    if symbol_state is not None:
        open_tickets = symbol_state.get("open_tickets") or []
        return {
            "symbol": str(symbol_state.get("symbol") or ""),
            "open_count": _coerce_count(open_tickets, default=0),
            "close_count": _coerce_int(symbol_state.get("realized_closes"), default=0),
            "last_bar_time": _coerce_int(symbol_state.get("last_bar_time"), default=0),
            "last_tick_msc": _coerce_int(symbol_state.get("last_tick_msc"), default=0),
            "next_buy_level": float(symbol_state.get("next_buy_level") or 0.0),
            "next_sell_level": float(symbol_state.get("next_sell_level") or 0.0),
            "max_open_total": _coerce_int(symbol_state.get("max_open_total"), default=0),
            "rearm_opens": _coerce_int(symbol_state.get("rearm_opens"), default=0),
            "rearm_token_count": _coerce_count(symbol_state.get("rearm_tokens"), default=0),
            "anchor_resets": _coerce_int(symbol_state.get("anchor_resets"), default=0),
            "anchor_resets_flat": _coerce_int(symbol_state.get("anchor_resets_flat"), default=0),
            "anchor_resets_risk": _coerce_int(symbol_state.get("anchor_resets_risk"), default=0),
            "lattice_started_time": _coerce_int(symbol_state.get("lattice_started_time"), default=0),
            "max_floating_loss_usd": _coerce_float(symbol_state.get("max_floating_loss_usd"), default=0.0),
            "offensive_positive_close_ticket_profit_usd": _coerce_float(
                symbol_state.get("offensive_positive_close_ticket_profit_usd"),
                default=0.0,
            ),
            "offensive_spend_usd": _coerce_float(symbol_state.get("offensive_spend_usd"), default=0.0),
            "offensive_budget_share": _coerce_float(symbol_state.get("offensive_budget_share"), default=0.0),
            "offensive_closure_enabled": _coerce_bool(symbol_state.get("offensive_closure_enabled"), default=False),
            "offensive_safety_margin_usd": _coerce_float(symbol_state.get("offensive_safety_margin_usd"), default=0.0),
            "offensive_safety_margin_pct": _coerce_float(symbol_state.get("offensive_safety_margin_pct"), default=0.0),
            "offensive_cut_cooldown_bars": _coerce_int(symbol_state.get("offensive_cut_cooldown_bars"), default=0),
            "offensive_breakeven_band_usd": _coerce_float(symbol_state.get("offensive_breakeven_band_usd"), default=0.0),
            "max_lattice_window_bars": _coerce_int(symbol_state.get("max_lattice_window_bars"), default=0),
            "breakout_buffer_pips": _coerce_float(symbol_state.get("breakout_buffer_pips"), default=0.0),
            "base_step_px": _coerce_float(symbol_state.get("base_step_px"), default=0.0),
            "base_step_sell_px": _coerce_float(symbol_state.get("base_step_sell_px"), default=0.0),
            "base_step_buy_px": _coerce_float(symbol_state.get("base_step_buy_px"), default=0.0),
            "reconcile_open_max_drift_px": _coerce_float(symbol_state.get("reconcile_open_max_drift_px"), default=0.0),
            "open_realism_mode": str(symbol_state.get("open_realism_mode") or ""),
            "close_realism_mode": str(symbol_state.get("close_realism_mode") or ""),
            "raw_close_alpha": _coerce_float(symbol_state.get("raw_close_alpha"), default=0.0),
            "raw_close_style": str(symbol_state.get("raw_close_style") or ""),
            "momentum_gate": _coerce_bool(symbol_state.get("momentum_gate"), default=False),
            "min_positive_close_profit_usd": _coerce_float(symbol_state.get("min_positive_close_profit_usd"), default=0.0),
            "positive_only_closes": _coerce_bool(symbol_state.get("positive_only_closes"), default=False),
            "positive_only_hold_active": _coerce_bool(symbol_state.get("positive_only_hold_active"), default=False),
            "positive_only_hold_reason": str(symbol_state.get("positive_only_hold_reason") or ""),
            "positive_only_hold_since": _coerce_int(symbol_state.get("positive_only_hold_since"), default=0),
            "mode": str(symbol_state.get("mode") or ""),
        }
    if isinstance(state_payload.get("positions"), list):
        stats = state_payload.get("stats") if isinstance(state_payload.get("stats"), dict) else {}
        market = state_payload.get("market") if isinstance(state_payload.get("market"), dict) else {}
        return {
            "symbol": str(state_payload.get("pair") or ""),
            "open_count": len(state_payload.get("positions") or []),
            "close_count": int(stats.get("total_closes") or 0),
            "last_bar_time": int(market.get("last_bar_time") or 0),
            "last_tick_msc": 0,
            "next_buy_level": 0.0,
            "next_sell_level": 0.0,
            "max_open_total": int(stats.get("max_open_total") or 0),
            "rearm_opens": 0,
            "rearm_token_count": 0,
            "anchor_resets": 0,
            "anchor_resets_flat": 0,
            "anchor_resets_risk": 0,
            "lattice_started_time": 0,
            "max_floating_loss_usd": _coerce_float(stats.get("max_floating_loss_usd"), default=0.0),
            "offensive_positive_close_ticket_profit_usd": 0.0,
            "offensive_spend_usd": 0.0,
            "offensive_budget_share": 0.0,
            "offensive_closure_enabled": _coerce_bool(state_payload.get("offensive_closure_enabled"), default=False),
            "offensive_safety_margin_usd": _coerce_float(state_payload.get("offensive_safety_margin_usd"), default=0.0),
            "offensive_safety_margin_pct": _coerce_float(state_payload.get("offensive_safety_margin_pct"), default=0.0),
            "offensive_cut_cooldown_bars": _coerce_int(state_payload.get("offensive_cut_cooldown_bars"), default=0),
            "offensive_breakeven_band_usd": _coerce_float(state_payload.get("offensive_breakeven_band_usd"), default=0.0),
            "max_lattice_window_bars": _coerce_int(stats.get("max_lattice_window_bars"), default=0),
            "breakout_buffer_pips": _coerce_float(stats.get("breakout_buffer_pips"), default=0.0),
            "base_step_px": _coerce_float(stats.get("base_step_px"), default=0.0),
            "base_step_sell_px": _coerce_float(stats.get("base_step_sell_px"), default=0.0),
            "base_step_buy_px": _coerce_float(stats.get("base_step_buy_px"), default=0.0),
            "reconcile_open_max_drift_px": _coerce_float(stats.get("reconcile_open_max_drift_px"), default=0.0),
            "open_realism_mode": str(stats.get("open_realism_mode") or ""),
            "close_realism_mode": str(stats.get("close_realism_mode") or ""),
            "raw_close_alpha": _coerce_float(stats.get("raw_close_alpha"), default=0.0),
            "raw_close_style": str(stats.get("raw_close_style") or ""),
            "momentum_gate": _coerce_bool(stats.get("momentum_gate"), default=False),
            "min_positive_close_profit_usd": _coerce_float(stats.get("min_positive_close_profit_usd"), default=0.0),
            "positive_only_closes": _coerce_bool(state_payload.get("positive_only_closes"), default=False),
            "positive_only_hold_active": _coerce_bool(state_payload.get("positive_only_hold_active"), default=False),
            "positive_only_hold_reason": str(state_payload.get("positive_only_hold_reason") or ""),
            "positive_only_hold_since": _coerce_int(state_payload.get("positive_only_hold_since"), default=0),
            "mode": str(state_payload.get("mode") or ""),
        }
    engine = engine_payload(state_payload)
    return {
        "symbol": str(engine.get("product_id") or ""),
        "open_count": generic_open_count(engine),
        "close_count": generic_close_count(engine),
        "last_bar_time": int(engine.get("last_bar_time") or 0),
        "last_tick_msc": int(engine.get("last_tick_msc") or 0),
        "next_buy_level": 0.0,
        "next_sell_level": 0.0,
        "max_open_total": 0,
        "rearm_opens": _coerce_int(engine.get("rearm_opens"), default=0),
        "rearm_token_count": _coerce_count(engine.get("rearm_tokens"), default=0),
        "anchor_resets": _coerce_int(engine.get("anchor_resets"), default=0),
        "anchor_resets_flat": _coerce_int(engine.get("anchor_resets_flat"), default=0),
        "anchor_resets_risk": _coerce_int(engine.get("anchor_resets_risk"), default=0),
        "lattice_started_time": _coerce_int(engine.get("lattice_started_time"), default=0),
        "max_floating_loss_usd": _coerce_float(engine.get("max_floating_loss_usd"), default=0.0),
        "offensive_positive_close_ticket_profit_usd": _coerce_float(engine.get("offensive_positive_close_ticket_profit_usd"), default=0.0),
        "offensive_spend_usd": _coerce_float(engine.get("offensive_spend_usd"), default=0.0),
        "offensive_budget_share": _coerce_float(engine.get("offensive_budget_share"), default=0.0),
        "offensive_closure_enabled": _coerce_bool(engine.get("offensive_closure_enabled"), default=False),
        "offensive_safety_margin_usd": _coerce_float(engine.get("offensive_safety_margin_usd"), default=0.0),
        "offensive_safety_margin_pct": _coerce_float(engine.get("offensive_safety_margin_pct"), default=0.0),
        "offensive_cut_cooldown_bars": _coerce_int(engine.get("offensive_cut_cooldown_bars"), default=0),
        "offensive_breakeven_band_usd": _coerce_float(engine.get("offensive_breakeven_band_usd"), default=0.0),
        "max_lattice_window_bars": _coerce_int(engine.get("max_lattice_window_bars"), default=0),
        "breakout_buffer_pips": _coerce_float(engine.get("breakout_buffer_pips"), default=0.0),
        "base_step_px": _coerce_float(engine.get("base_step_px"), default=0.0),
        "base_step_sell_px": _coerce_float(engine.get("base_step_sell_px"), default=0.0),
        "base_step_buy_px": _coerce_float(engine.get("base_step_buy_px"), default=0.0),
        "reconcile_open_max_drift_px": _coerce_float(engine.get("reconcile_open_max_drift_px"), default=0.0),
        "open_realism_mode": str(engine.get("open_realism_mode") or ""),
        "close_realism_mode": str(engine.get("close_realism_mode") or ""),
        "raw_close_alpha": _coerce_float(engine.get("raw_close_alpha"), default=0.0),
        "raw_close_style": str(engine.get("raw_close_style") or ""),
        "momentum_gate": _coerce_bool(engine.get("momentum_gate"), default=False),
        "min_positive_close_profit_usd": _coerce_float(engine.get("min_positive_close_profit_usd"), default=0.0),
        "positive_only_closes": _coerce_bool(engine.get("positive_only_closes"), default=False),
        "positive_only_hold_active": _coerce_bool(engine.get("positive_only_hold_active"), default=False),
        "positive_only_hold_reason": str(engine.get("positive_only_hold_reason") or ""),
        "positive_only_hold_since": _coerce_int(engine.get("positive_only_hold_since"), default=0),
        "mode": "",
    }


def resolve_event_path(lane: dict[str, Any], state_path: Path, metrics: dict[str, Any]) -> Path:
    explicit = str(lane.get("event_path") or "").strip()
    if explicit:
        return ROOT / explicit
    symbol = str(metrics.get("symbol") or "").strip().lower()
    state_name = state_path.name.lower()
    lane_kind = str(lane.get("kind") or "").strip().lower()
    if lane_kind == "shadow_unified" and symbol:
        return state_path.with_name(f"unified_shadow_{symbol}_events.jsonl")
    if state_name.endswith("_state.json"):
        return state_path.with_name(state_path.name[:-10] + "_events.jsonl")
    return ROOT / explicit


def summarize_events(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {
            "exists": False,
            "last_event_at": None,
            "last_trade_event_at": None,
            "last_trade_action": "",
            "first_trade_event_at": None,
            "first_trade_action": "",
            "trade_open_count": 0,
            "trade_close_count": 0,
            "broker_sync_inherited_closes": 0,
            "broker_sync_inherited_realized_usd": 0.0,
        }
    last_event_at: str | None = None
    last_trade_event_at: str | None = None
    last_trade_action = ""
    first_trade_event_at: str | None = None
    first_trade_action = ""
    trade_open_count = 0
    trade_close_count = 0
    broker_sync_inherited_closes = 0
    broker_sync_inherited_realized_usd = 0.0
    try:
        lines = path.read_text(encoding="utf-8", errors="ignore").splitlines()
    except Exception:
        lines = []
    for line in lines:
        try:
            row = json.loads(line)
        except Exception:
            continue
        ts = str(row.get("ts_utc") or "")
        if ts:
            last_event_at = ts
        action = str(row.get("action") or "")
        if action == "direct_live_broker_sync":
            inherited_closes = int(row.get("new_realized_closes") or 0) - int(row.get("old_realized_closes") or 0)
            inherited_realized = float(row.get("new_realized_net_usd") or 0.0) - float(row.get("old_realized_net_usd") or 0.0)
            if inherited_closes > 0:
                broker_sync_inherited_closes += inherited_closes
                broker_sync_inherited_realized_usd += inherited_realized
        if action not in TRADE_ACTIONS:
            continue
        if not first_trade_event_at:
            first_trade_event_at = ts or first_trade_event_at
            first_trade_action = action
        last_trade_action = action
        last_trade_event_at = ts or last_trade_event_at
        if action in {"open", "open_ticket", "open_sleeve"}:
            trade_open_count += 1
        if action in {"close", "close_ticket", "close_sleeve"}:
            trade_close_count += 1
    return {
        "exists": True,
        "last_event_at": last_event_at,
        "last_trade_event_at": last_trade_event_at,
        "last_trade_action": last_trade_action,
        "first_trade_event_at": first_trade_event_at,
        "first_trade_action": first_trade_action,
        "trade_open_count": trade_open_count,
        "trade_close_count": trade_close_count,
        "broker_sync_inherited_closes": broker_sync_inherited_closes,
        "broker_sync_inherited_realized_usd": round(broker_sync_inherited_realized_usd, 2),
    }


def summarize_trade_events_since(path: Path, since_dt: datetime | None) -> dict[str, Any]:
    if since_dt is None or not path.exists():
        return {
            "exists": path.exists(),
            "first_trade_event_at": None,
            "first_trade_action": "",
            "last_trade_event_at": None,
            "last_trade_action": "",
            "trade_open_count": 0,
            "trade_close_count": 0,
            "trade_close_realized_usd": 0.0,
        }
    first_trade_event_at: str | None = None
    first_trade_action = ""
    last_trade_event_at: str | None = None
    last_trade_action = ""
    trade_open_count = 0
    trade_close_count = 0
    trade_close_realized_usd = 0.0
    try:
        lines = path.read_text(encoding="utf-8", errors="ignore").splitlines()
    except Exception:
        lines = []
    for line in lines:
        try:
            row = json.loads(line)
        except Exception:
            continue
        action = str(row.get("action") or "")
        if action not in TRADE_ACTIONS:
            continue
        row_dt = parse_iso(row.get("ts_utc"))
        if row_dt is None or row_dt < since_dt:
            continue
        ts = str(row.get("ts_utc") or "")
        if not first_trade_event_at:
            first_trade_event_at = ts or first_trade_event_at
            first_trade_action = action
        last_trade_event_at = ts or last_trade_event_at
        last_trade_action = action
        if action in {"open", "open_ticket", "open_sleeve"}:
            trade_open_count += 1
        if action in {"close", "close_ticket", "close_sleeve"}:
            trade_close_count += 1
            trade_close_realized_usd += float(
                row.get("realized_pnl")
                or row.get("realized_usd")
                or row.get("pnl_usd")
                or row.get("profit_usd")
                or row.get("net_usd")
                or 0.0
            )
    return {
        "exists": path.exists(),
        "first_trade_event_at": first_trade_event_at,
        "first_trade_action": first_trade_action,
        "last_trade_event_at": last_trade_event_at,
        "last_trade_action": last_trade_action,
        "trade_open_count": trade_open_count,
        "trade_close_count": trade_close_count,
        "trade_close_realized_usd": round(trade_close_realized_usd, 2),
    }


def summarize_spread_blocks_since(path: Path, since_dt: datetime | None) -> dict[str, Any]:
    if since_dt is None or not path.exists():
        return {
            "exists": path.exists(),
            "blocked_count": 0,
            "last_blocked_at": None,
            "max_spread_ratio": 0.0,
            "max_entry_spread_ratio": 0.0,
        }
    blocked_count = 0
    last_blocked_at: str | None = None
    max_spread_ratio = 0.0
    max_entry_spread_ratio = 0.0
    try:
        lines = path.read_text(encoding="utf-8", errors="ignore").splitlines()
    except Exception:
        lines = []
    for line in lines:
        try:
            row = json.loads(line)
        except Exception:
            continue
        if str(row.get("action") or "") != "open_blocked_wide_spread":
            continue
        row_dt = parse_iso(row.get("ts_utc"))
        if row_dt is None or row_dt < since_dt:
            continue
        blocked_count += 1
        last_blocked_at = str(row.get("ts_utc") or "") or last_blocked_at
        spread_ratio = float(
            row.get("spread_ratio")
            or row.get("spread_to_step_ratio")
            or row.get("entry_context", {}).get("spread_ratio")
            or 0.0
        )
        allowed_ratio = float(
            row.get("max_entry_spread_ratio")
            or row.get("entry_context", {}).get("max_entry_spread_ratio")
            or 0.0
        )
        max_spread_ratio = max(max_spread_ratio, spread_ratio)
        max_entry_spread_ratio = max(max_entry_spread_ratio, allowed_ratio)
    return {
        "exists": path.exists(),
        "blocked_count": blocked_count,
        "last_blocked_at": last_blocked_at,
        "max_spread_ratio": round(max_spread_ratio, 4),
        "max_entry_spread_ratio": round(max_entry_spread_ratio, 4),
    }


def summarize_guard_blocks_since(path: Path, since_dt: datetime | None) -> dict[str, Any]:
    if since_dt is None or not path.exists():
        return {
            "exists": path.exists(),
            "blocked_count": 0,
            "last_blocked_at": None,
            "max_recovery_signal_count": 0,
        }
    blocked_count = 0
    last_blocked_at: str | None = None
    max_recovery_signal_count = 0
    try:
        lines = path.read_text(encoding="utf-8", errors="ignore").splitlines()
    except Exception:
        lines = []
    for line in lines:
        try:
            row = json.loads(line)
        except Exception:
            continue
        if str(row.get("action") or "") != "open_guarded_admission":
            continue
        row_dt = parse_iso(row.get("ts_utc"))
        if row_dt is None or row_dt < since_dt:
            continue
        blocked_count += 1
        last_blocked_at = str(row.get("ts_utc") or "") or last_blocked_at
        max_recovery_signal_count = max(
            max_recovery_signal_count,
            int(row.get("recovery_signal_count") or 0),
        )
    return {
        "exists": path.exists(),
        "blocked_count": blocked_count,
        "last_blocked_at": last_blocked_at,
        "max_recovery_signal_count": max_recovery_signal_count,
    }


def summarize_trigger_quote_proof_since(
    path: Path,
    since_dt: datetime | None,
    *,
    next_buy_level: float,
    next_sell_level: float,
) -> dict[str, Any]:
    if since_dt is None or not path.exists():
        return {
            "event_after_trigger": False,
            "quote_cross_after_trigger": False,
            "quote_cross_event_at": None,
        }
    event_after_trigger = False
    quote_cross_after_trigger = False
    quote_cross_event_at: str | None = None
    try:
        lines = path.read_text(encoding="utf-8", errors="ignore").splitlines()
    except Exception:
        lines = []
    for line in lines:
        try:
            row = json.loads(line)
        except Exception:
            continue
        row_dt = parse_iso(row.get("ts_utc"))
        if row_dt is None or row_dt < since_dt:
            continue
        event_after_trigger = True
        bid = float(row.get("bid", 0.0) or 0.0)
        ask = float(row.get("ask", 0.0) or 0.0)
        if next_buy_level > 0.0 and ask > 0.0 and ask <= next_buy_level:
            quote_cross_after_trigger = True
        if next_sell_level > 0.0 and bid > 0.0 and bid >= next_sell_level:
            quote_cross_after_trigger = True
        if quote_cross_after_trigger and not quote_cross_event_at:
            quote_cross_event_at = str(row.get("ts_utc") or "")
            break
    return {
        "event_after_trigger": event_after_trigger,
        "quote_cross_after_trigger": quote_cross_after_trigger,
        "quote_cross_event_at": quote_cross_event_at,
    }


def forward_review_reason(lane: dict[str, Any], review_row: dict[str, Any] | None) -> str | None:
    if not review_row:
        return None
    lane_kind = str(lane.get("kind") or "")
    lane_name = str(lane.get("name") or "")
    if lane_kind not in {"shadow_coinbase_spot", "shadow_crypto_candidate"} and not lane_name.endswith("_ratio_sleeve"):
        return None
    status = str(review_row.get("forward_status") or "").strip()
    if not status:
        return None
    realized = float(review_row.get("realized_net_usd") or 0.0)
    closes = int(float(review_row.get("realized_closes") or review_row.get("closes") or 0))
    if status.startswith("holding_up") or status.startswith("lagging"):
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


def live_quote(symbol: str) -> dict[str, Any] | None:
    symbol_name = str(symbol or "").upper()
    if not symbol_name:
        return None
    if not mt5.symbol_select(symbol_name, True):
        return None
    now_msc = int(utc_now().timestamp() * 1000)
    symbol_tick_quote: dict[str, Any] | None = None
    tick = mt5.symbol_info_tick(symbol_name)
    if tick:
        symbol_tick_quote = {
            "symbol": symbol_name,
            "bid": float(getattr(tick, "bid", 0.0) or 0.0),
            "ask": float(getattr(tick, "ask", 0.0) or 0.0),
            "time_msc": int(getattr(tick, "time_msc", 0) or 0),
        }
    recent_ticks = mt5.copy_ticks_range(
        symbol_name,
        utc_now() - timedelta(minutes=5),
        utc_now() + timedelta(seconds=1),
        getattr(mt5, "COPY_TICKS_ALL", 0),
    )
    if recent_ticks is not None and len(recent_ticks) > 0:
        recent_tick = recent_ticks[-1]
        recent_time_msc = int(recent_tick["time_msc"])
        if 0 < recent_time_msc <= (now_msc + int(timedelta(minutes=15).total_seconds() * 1000)):
            recent_quote = {
                "symbol": symbol_name,
                "bid": float(recent_tick["bid"]),
                "ask": float(recent_tick["ask"]),
                "time_msc": recent_time_msc,
            }
            if symbol_tick_quote is None:
                return recent_quote
            if int(symbol_tick_quote.get("time_msc") or 0) <= 0:
                return recent_quote
            return recent_quote if recent_time_msc >= int(symbol_tick_quote["time_msc"]) else symbol_tick_quote
    return symbol_tick_quote


def lane_live_magic(lane: dict[str, Any], state_payload: dict[str, Any]) -> int:
    metadata = state_payload.get("metadata") if isinstance(state_payload.get("metadata"), dict) else {}
    try:
        magic = int(metadata.get("live_magic") or 0)
    except Exception:
        magic = 0
    if magic > 0:
        return magic
    args = list(lane.get("restart_args") or [])
    for idx, arg in enumerate(args):
        if str(arg) == "--live-magic" and idx + 1 < len(args):
            try:
                return int(args[idx + 1])
            except Exception:
                return 0
    return 0


def lane_attached_live_magics(lane: dict[str, Any], state_payload: dict[str, Any]) -> tuple[int, ...]:
    metadata = state_payload.get("metadata") if isinstance(state_payload.get("metadata"), dict) else {}
    primary_magic = lane_live_magic(lane, state_payload)
    ordered: list[int] = []

    def _push(candidate: Any) -> None:
        try:
            magic = int(candidate or 0)
        except Exception:
            magic = 0
        if magic <= 0 or magic == primary_magic or magic in ordered:
            return
        ordered.append(magic)

    metadata_magics = metadata.get("attached_broker_magics")
    if isinstance(metadata_magics, list):
        for candidate in metadata_magics:
            _push(candidate)

    lane_magics = lane.get("attached_broker_magics")
    if isinstance(lane_magics, list):
        for candidate in lane_magics:
            _push(candidate)

    args = list(lane.get("restart_args") or [])
    for idx, arg in enumerate(args):
        if str(arg) == "--attach-broker-magic" and idx + 1 < len(args):
            _push(args[idx + 1])

    return tuple(ordered)


def lane_live_magics(lane: dict[str, Any], state_payload: dict[str, Any]) -> tuple[int, ...]:
    primary_magic = lane_live_magic(lane, state_payload)
    ordered: list[int] = []
    if int(primary_magic or 0) > 0:
        ordered.append(int(primary_magic))
    for magic in lane_attached_live_magics(lane, state_payload):
        if magic not in ordered:
            ordered.append(int(magic))
    return tuple(ordered)


def lane_scoped_symbols(lane: dict[str, Any], state_payload: dict[str, Any]) -> set[str]:
    metadata = state_payload.get("metadata") if isinstance(state_payload.get("metadata"), dict) else {}
    symbols = metadata.get("symbols")
    if isinstance(symbols, list):
        return {str(symbol or "").upper() for symbol in symbols if str(symbol or "").strip()}
    args = list(lane.get("restart_args") or [])
    if "--symbols" not in args:
        return set()
    start = args.index("--symbols") + 1
    scoped: set[str] = set()
    for arg in args[start:]:
        text = str(arg or "")
        if text.startswith("--"):
            break
        scoped.add(text.upper())
    return scoped


def collect_broker_positions_by_magic() -> dict[int, list[dict[str, Any]]]:
    grouped: dict[int, list[dict[str, Any]]] = {}
    for pos in mt5.positions_get() or []:
        magic = int(getattr(pos, "magic", 0) or 0)
        grouped.setdefault(magic, []).append(
            {
                "ticket": int(getattr(pos, "ticket", 0) or 0),
                "magic": magic,
                "symbol": str(getattr(pos, "symbol", "") or "").upper(),
            }
        )
    return grouped


def broker_scope_summary(
    broker_positions_by_magic: dict[int, list[dict[str, Any]]],
    *,
    live_magic: int | None = None,
    live_magics: list[int] | tuple[int, ...] | set[int] | None = None,
    scoped_symbols: set[str],
) -> dict[str, Any]:
    ordered_magics: list[int] = []
    for candidate in list(live_magics or []):
        try:
            magic = int(candidate or 0)
        except Exception:
            magic = 0
        if magic <= 0 or magic in ordered_magics:
            continue
        ordered_magics.append(magic)
    if not ordered_magics and int(live_magic or 0) > 0:
        ordered_magics.append(int(live_magic or 0))
    positions: list[dict[str, Any]] = []
    for magic in ordered_magics:
        positions.extend(list(broker_positions_by_magic.get(int(magic or 0), [])))
    outside_counts: dict[str, int] = {}
    per_magic_counts: dict[int, int] = {}
    scoped_open_count = 0
    for row in positions:
        symbol = str(row.get("symbol") or "").upper()
        magic = int(row.get("magic", 0) or 0)
        per_magic_counts[magic] = int(per_magic_counts.get(magic, 0) or 0) + 1
        if scoped_symbols and symbol not in scoped_symbols:
            outside_counts[symbol] = int(outside_counts.get(symbol, 0) or 0) + 1
        else:
            scoped_open_count += 1
    outside_open_count = sum(outside_counts.values())
    return {
        "live_magic": int(ordered_magics[0] if ordered_magics else (live_magic or 0)),
        "live_magics": ordered_magics,
        "total_open_count": len(positions),
        "scoped_open_count": int(scoped_open_count),
        "outside_open_count": int(outside_open_count),
        "outside_counts": outside_counts,
        "per_magic_counts": {int(magic): int(count) for magic, count in sorted(per_magic_counts.items())},
    }


def symbol_price_precision(symbol: str, *, fallback: int = 2) -> int:
    symbol_name = str(symbol or "").strip().upper()
    if not symbol_name:
        return int(fallback)
    try:
        info = mt5.symbol_info(symbol_name)
    except Exception:
        info = None
    digits = getattr(info, "digits", fallback) if info is not None else fallback
    try:
        return max(0, min(6, int(digits)))
    except Exception:
        return int(fallback)


def trustworthy_trade_event_presence_check(kind: str) -> bool:
    lane_kind = str(kind or "").lower()
    return "coinbase" not in lane_kind and lane_kind in {
        "live_crypto",
        "shadow_crypto",
        "live_fx",
        "shadow_fx",
        "shadow_unified",
    }


def trustworthy_trade_event_gap_check(kind: str) -> bool:
    lane_kind = str(kind or "").lower()
    return "coinbase" not in lane_kind and lane_kind in {
        "live_crypto",
        "shadow_crypto",
        "shadow_unified",
    }


def missing_trade_events_note(
    *,
    kind: str,
    open_count: int,
    last_trade_event_at: str | None,
    state_last_write_at: str | None,
    runner_heartbeat_at: str | None,
    runner_started_at: str | None,
) -> str:
    if not trustworthy_trade_event_presence_check(kind):
        return ""
    if open_count <= 0 or last_trade_event_at:
        return ""
    lane_kind = str(kind or "").lower()
    if lane_kind == "live_fx":
        runner_started_dt = parse_iso(runner_started_at)
        state_last_write_dt = parse_iso(state_last_write_at)
        runner_heartbeat_dt = parse_iso(runner_heartbeat_at)
        fresh_runtime_state = (
            runner_started_dt is not None
            and (
                (state_last_write_dt is not None and state_last_write_dt >= runner_started_dt)
                or (runner_heartbeat_dt is not None and runner_heartbeat_dt >= runner_started_dt)
            )
        )
        if fresh_runtime_state:
            return ""
    return "missing_trade_events"


def has_single_position_shape(state_payload: dict[str, Any]) -> bool:
    engine = engine_payload(state_payload)
    state = state_payload.get("state") if isinstance(state_payload.get("state"), dict) else {}
    if isinstance(engine.get("positions"), list) or isinstance(engine.get("per_coin_details"), dict):
        return False
    if isinstance(state, dict) and "current_position" in state:
        return True
    return any(key in engine for key in ("position", "current_position", "pos"))


def single_position_session_parity(
    *,
    kind: str,
    state_payload: dict[str, Any],
    metrics: dict[str, Any],
    runner: dict[str, Any],
    event_path: Path,
) -> dict[str, Any]:
    lane_kind = str(kind or "").lower()
    if "coinbase" not in lane_kind or not has_single_position_shape(state_payload):
        return {
            "parity_alert": "",
            "session_trade_opens": 0,
            "session_trade_closes": 0,
            "session_carry_in": 0,
            "session_last_trade_action": "",
        }
    started_at = parse_iso(runner.get("started_at"))
    session_events = summarize_trade_events_since(event_path, started_at)
    open_count = int(metrics.get("open_count") or 0)
    session_trade_opens = int(session_events["trade_open_count"])
    session_trade_closes = int(session_events["trade_close_count"])
    session_last_trade_action = str(session_events["last_trade_action"] or "")
    carry_in = 1 if str(session_events["first_trade_action"] or "") in {"close", "close_ticket"} else 0
    if not session_events["first_trade_action"] and open_count == 1:
        carry_in = 1
    parity_alert = ""
    expected_open_count = carry_in + session_trade_opens - session_trade_closes
    if open_count > 1:
        parity_alert = f"single_position_open_count_invalid:{open_count}"
    elif expected_open_count not in (0, 1):
        parity_alert = f"single_position_session_balance_invalid:{expected_open_count}"
    elif open_count != expected_open_count:
        parity_alert = f"single_position_session_parity_mismatch:expected_{expected_open_count}_have_{open_count}"
    return {
        "parity_alert": parity_alert,
        "session_trade_opens": session_trade_opens,
        "session_trade_closes": session_trade_closes,
        "session_carry_in": carry_in,
        "session_last_trade_action": session_last_trade_action,
    }


def trigger_signature(metrics: dict[str, Any], quote: dict[str, Any] | None) -> str:
    if not quote:
        return ""
    precision = symbol_price_precision(str(metrics.get("symbol") or ""), fallback=2)
    max_open_total = int(metrics.get("max_open_total") or 0)
    open_count = int(metrics.get("open_count") or 0)
    if max_open_total <= 0 or open_count >= max_open_total:
        return ""
    next_buy = float(metrics.get("next_buy_level") or 0.0)
    next_sell = float(metrics.get("next_sell_level") or 0.0)
    ask = float(quote.get("ask") or 0.0)
    bid = float(quote.get("bid") or 0.0)
    if next_buy > 0.0 and ask > 0.0 and ask <= next_buy:
        return f"BUY@{next_buy:.{precision}f}"
    if next_sell > 0.0 and bid > 0.0 and bid >= next_sell:
        return f"SELL@{next_sell:.{precision}f}"
    return ""


def should_monitor_trigger(kind: str, metrics: dict[str, Any], now_dt: datetime) -> bool:
    lane_kind = str(kind or "").lower()
    if "fx" not in lane_kind:
        return True
    last_bar_time = int(metrics.get("last_bar_time") or 0)
    if last_bar_time <= 0:
        return False
    last_bar_dt = datetime.fromtimestamp(last_bar_time, tz=timezone.utc)
    if (now_dt - last_bar_dt) > timedelta(hours=6):
        return False
    next_buy = float(metrics.get("next_buy_level") or 0.0)
    next_sell = float(metrics.get("next_sell_level") or 0.0)
    if next_buy > 0.0 and next_sell > 0.0:
        threshold = max(0.00001, max(next_buy, next_sell) * 0.00001)
        if abs(next_buy - next_sell) <= threshold:
            return False
    return True


def suppress_execution_alerts_for_runner(runner: dict[str, Any]) -> bool:
    status = str(runner.get("status") or "").strip()
    if not status or status == "ok":
        status = str(runner.get("live_admissibility_reason") or "").strip()
    return status in {"positive_only_hold_active", "live_contract_friction_invalid"}


def clean_forward_metrics(state_snapshot: dict[str, Any], reset_row: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(reset_row, dict) or not reset_row:
        return {
            "clean_forward_reset_at": "",
            "clean_forward_source": "",
            "clean_forward_realized_delta_usd": "",
            "clean_forward_new_closes": "",
            "clean_forward_counter_reset": False,
        }
    current_closes = int(state_snapshot.get("closes") or 0)
    baseline_closes = int(reset_row.get("closes") or 0)
    current_realized = float(state_snapshot.get("realized_net_usd") or 0.0)
    baseline_realized = float(reset_row.get("realized_net_usd") or 0.0)
    counter_reset = current_closes < baseline_closes
    if counter_reset:
        baseline_closes = 0
        baseline_realized = 0.0
    delta = round(
        current_realized - baseline_realized,
        4,
    )
    new_closes = int(current_closes - baseline_closes)
    return {
        "clean_forward_reset_at": str(reset_row.get("reset_at") or ""),
        "clean_forward_source": str(reset_row.get("reset_type") or ""),
        "clean_forward_realized_delta_usd": delta,
        "clean_forward_new_closes": new_closes,
        "clean_forward_counter_reset": counter_reset,
    }


def direct_live_state_carry_metrics(
    state_snapshot: dict[str, Any],
    *,
    inherited_closes: int,
    inherited_realized_usd: float,
    runner_trade_closes: int,
    runner_trade_realized_usd: float,
) -> dict[str, Any]:
    carry_closes = int(state_snapshot.get("closes") or 0) - int(inherited_closes or 0) - int(runner_trade_closes or 0)
    if carry_closes <= 0:
        return {
            "carry_closes": 0,
            "carry_realized_usd": 0.0,
        }
    carry_realized = round(
        float(state_snapshot.get("realized_net_usd") or 0.0)
        - float(inherited_realized_usd or 0.0)
        - float(runner_trade_realized_usd or 0.0),
        2,
    )
    return {
        "carry_closes": carry_closes,
        "carry_realized_usd": carry_realized,
    }


def clamp_inherited_broker_sync_history(
    *,
    inherited_closes: int,
    inherited_realized_usd: float,
    state_snapshot: dict[str, Any],
) -> tuple[int, float]:
    close_count = int(state_snapshot.get("closes") or 0)
    realized_net = float(state_snapshot.get("realized_net_usd") or 0.0)
    clamped_closes = max(0, int(inherited_closes))
    clamped_realized = float(inherited_realized_usd or 0.0)

    if close_count > 0 and clamped_closes > close_count:
        clamped_closes = close_count
    if abs(realized_net) > 1e-9 and abs(clamped_realized) > abs(realized_net):
        clamped_realized = realized_net

    return clamped_closes, round(clamped_realized, 2)


def direct_live_open_carry_metrics(
    payload: dict[str, Any],
    *,
    runner_started_at: datetime | None,
) -> dict[str, Any]:
    if runner_started_at is None or not isinstance(payload, dict):
        return {
            "carry_open_count": 0,
            "carry_kind_counts": {},
        }
    symbols = payload.get("symbols")
    if not isinstance(symbols, dict):
        return {
            "carry_open_count": 0,
            "carry_kind_counts": {},
        }
    runner_started_epoch = float(runner_started_at.timestamp())
    runner_started_msc = int(runner_started_epoch * 1000)
    carry_open_count = 0
    carry_kind_counts: dict[str, int] = {}
    for symbol_state in symbols.values():
        if not isinstance(symbol_state, dict):
            continue
        open_tickets = symbol_state.get("open_tickets")
        if not isinstance(open_tickets, list):
            continue
        for ticket in open_tickets:
            if not isinstance(ticket, dict):
                continue
            opened_msc = _coerce_int(ticket.get("opened_msc"), default=0)
            opened_time = _coerce_int(ticket.get("opened_time"), default=0)
            opened_before_start = False
            if opened_msc > 0:
                opened_before_start = opened_msc < runner_started_msc
            elif opened_time > 0:
                opened_before_start = float(opened_time) < runner_started_epoch
            if not opened_before_start:
                continue
            carry_open_count += 1
            kind = str(ticket.get("ticket_kind") or "unknown").strip().lower() or "unknown"
            carry_kind_counts[kind] = int(carry_kind_counts.get(kind) or 0) + 1
    return {
        "carry_open_count": carry_open_count,
        "carry_kind_counts": carry_kind_counts,
    }


def exact_fire_metadata(kind: str, lane_name: str) -> dict[str, Any]:
    policy = exact_fire_policy(kind, lane_name)
    return {
        "exact_fire_support": str(policy["support"]),
        "exact_fire_label": str(policy["label"]),
        "exact_fire_operator_note": str(policy["operator_note"]),
        "exact_fire_policy_version": str(policy["policy_version"]),
    }


def update_trigger_watch(
    previous: dict[str, Any] | None,
    *,
    signature: str,
    now_dt: datetime,
    last_trade_event_at: str | None,
    threshold_seconds: float,
    suppress_execution_alert: bool = False,
) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    if not signature or suppress_execution_alert:
        return None, {
            "execution_alert": "",
            "suspected_missed_open": False,
            "probable_missed_open": False,
            "trigger_age_seconds": 0.0,
        }

    last_trade_dt = parse_iso(last_trade_event_at)
    previous_signature = str((previous or {}).get("signature") or "")
    previous_first_seen = parse_iso((previous or {}).get("first_seen_at"))
    first_seen_dt = previous_first_seen if previous_signature == signature and previous_first_seen else now_dt

    if last_trade_dt is not None and last_trade_dt >= first_seen_dt:
        first_seen_dt = now_dt

    trigger_age_seconds = max(0.0, (now_dt - first_seen_dt).total_seconds())
    suspected = False
    probable = False
    threshold_seconds = max(0.0, float(threshold_seconds))
    if trigger_age_seconds >= threshold_seconds:
        if last_trade_dt is None or last_trade_dt < first_seen_dt:
            suspected = True
            if trigger_age_seconds >= (threshold_seconds * 2.0):
                probable = True

    return {
        "signature": signature,
        "first_seen_at": first_seen_dt.isoformat(),
    }, {
        "execution_alert": "probable_missed_open" if probable else ("suspected_missed_open" if suspected else ""),
        "suspected_missed_open": suspected,
        "probable_missed_open": probable,
        "trigger_age_seconds": round(trigger_age_seconds, 1),
    }


def refine_execution_alert(
    *,
    execution_alert: str,
    trigger_first_seen_at: str,
    event_last_write_at: str,
    state_last_write_at: str,
    runner_heartbeat_at: str,
    lane_quote_cross_after_trigger: bool,
    last_spread_block_at: str = "",
    spread_block_count: int = 0,
    last_guard_block_at: str = "",
    guard_block_count: int = 0,
) -> dict[str, Any]:
    raw_alert = str(execution_alert or "")
    if not raw_alert:
        return {
            "execution_alert": "",
            "raw_execution_alert": "",
            "execution_evidence_quality": "",
            "lane_event_write_after_trigger": False,
            "lane_quote_cross_after_trigger": False,
            "state_write_after_trigger": False,
            "runner_heartbeat_after_trigger": False,
        }

    first_seen_dt = parse_iso(trigger_first_seen_at)
    event_write_dt = parse_iso(event_last_write_at)
    state_write_dt = parse_iso(state_last_write_at)
    runner_heartbeat_dt = parse_iso(runner_heartbeat_at)
    spread_block_dt = parse_iso(last_spread_block_at)
    guard_block_dt = parse_iso(last_guard_block_at)
    lane_event_write_after_trigger = bool(first_seen_dt and event_write_dt and event_write_dt >= first_seen_dt)
    state_write_after_trigger = bool(first_seen_dt and state_write_dt and state_write_dt >= first_seen_dt)
    runner_heartbeat_after_trigger = bool(first_seen_dt and runner_heartbeat_dt and runner_heartbeat_dt >= first_seen_dt)
    spread_block_after_trigger = bool(
        int(spread_block_count or 0) > 0
        and first_seen_dt
        and spread_block_dt
        and spread_block_dt >= first_seen_dt
    )
    guard_block_after_trigger = bool(
        int(guard_block_count or 0) > 0
        and first_seen_dt
        and guard_block_dt
        and guard_block_dt >= first_seen_dt
    )

    effective_alert = raw_alert
    if lane_quote_cross_after_trigger and spread_block_after_trigger:
        evidence_quality = "spread_block_after_trigger"
        effective_alert = ""
    elif lane_quote_cross_after_trigger and guard_block_after_trigger:
        evidence_quality = "guard_block_after_trigger"
        effective_alert = ""
    elif lane_quote_cross_after_trigger:
        evidence_quality = "lane_quote_cross_after_trigger"
    elif lane_event_write_after_trigger:
        evidence_quality = "lane_event_write_after_trigger_no_quote_proof"
        effective_alert = ""
    elif state_write_after_trigger or runner_heartbeat_after_trigger:
        evidence_quality = "state_heartbeat_without_event_write"
        effective_alert = ""
    else:
        evidence_quality = "no_recent_state_or_event_write"
        effective_alert = ""

    return {
        "execution_alert": effective_alert,
        "raw_execution_alert": raw_alert,
        "execution_evidence_quality": evidence_quality,
        "lane_event_write_after_trigger": lane_event_write_after_trigger,
        "lane_quote_cross_after_trigger": lane_quote_cross_after_trigger,
        "state_write_after_trigger": state_write_after_trigger,
        "runner_heartbeat_after_trigger": runner_heartbeat_after_trigger,
    }


def execution_alert_flags(execution_alert: str) -> dict[str, bool]:
    alert = str(execution_alert or "")
    return {
        "suspected_missed_open": alert in {"suspected_missed_open", "probable_missed_open"},
        "probable_missed_open": alert == "probable_missed_open",
    }


def execution_alert_notes(
    *,
    raw_execution_alert: str,
    execution_alert: str,
    signature: str,
    trigger_age_seconds: float,
    execution_evidence_quality: str,
) -> list[str]:
    notes: list[str] = []
    if raw_execution_alert and raw_execution_alert == execution_alert:
        notes.append(f"{raw_execution_alert}={signature} age={trigger_age_seconds:.1f}s")
    if raw_execution_alert and raw_execution_alert != execution_alert:
        notes.append(
            f"execution_alert_downgraded={raw_execution_alert}->{execution_alert or 'clear'} due_to={execution_evidence_quality}"
        )
    return notes


def synthetic_live_admissibility_runner(
    *,
    runner: dict[str, Any],
    direct_live: bool,
    runner_started_at: datetime | None,
    runner_trade_opens: int,
    runner_trade_closes: int,
    spread_blocks: dict[str, Any],
) -> dict[str, Any]:
    if not direct_live or runner_started_at is None:
        return {}
    if str(runner.get("status") or "").strip() or str(runner.get("live_admissibility_reason") or "").strip():
        return {}
    if runner_trade_opens > 0 or runner_trade_closes > 0:
        return {}
    blocked_count = int(spread_blocks.get("blocked_count") or 0)
    max_spread_ratio = float(spread_blocks.get("max_spread_ratio") or 0.0)
    max_entry_spread_ratio = float(spread_blocks.get("max_entry_spread_ratio") or 0.0)
    if blocked_count < 3 or max_spread_ratio <= 0.0 or max_entry_spread_ratio <= 0.0:
        return {}
    return {
        "status": "live_contract_friction_invalid",
        "live_admissibility_reason": "live_contract_friction_invalid",
        "live_admissibility_spread_to_step_ratio": max_spread_ratio,
        "live_admissibility_max_entry_spread_ratio": max_entry_spread_ratio,
        "live_admissibility_block_count": blocked_count,
        "live_admissibility_last_blocked_at": spread_blocks.get("last_blocked_at"),
    }


def runner_status_note(runner: dict[str, Any]) -> str:
    status = str(runner.get("status") or "").strip()
    if not status or status == "ok":
        status = str(runner.get("live_admissibility_reason") or "").strip()
    if not status or status == "ok":
        return ""
    if status == "positive_only_hold_active":
        symbols = ",".join(str(symbol) for symbol in list(runner.get("positive_only_hold_symbols") or []) if str(symbol).strip())
        reason = str(runner.get("positive_only_hold_reason") or "").strip()
        note = f"runner_status={status}"
        if symbols:
            note += f" symbols={symbols}"
        if reason:
            note += f" reason={reason}"
        return note
    if status == "live_contract_friction_invalid":
        ratio = float(runner.get("live_admissibility_spread_to_step_ratio") or 0.0)
        max_ratio = float(runner.get("live_admissibility_max_entry_spread_ratio") or 0.0)
        blocked_count = int(runner.get("live_admissibility_block_count") or 0)
        note = f"runner_status={status} spread_to_step={ratio:.2f} max_ratio={max_ratio:.2f}"
        if blocked_count > 0:
            note += f" blocked={blocked_count}"
        return note
    return f"runner_status={status}"


def build_rows() -> tuple[list[dict[str, Any]], dict[str, Any]]:
    now_dt = utc_now()
    registry = read_registry(REGISTRY_PATH)
    watchdog_rows = merged_watchdog_rows()
    forward_review_rows = load_combined_forward_review_rows(
        [
            COINBASE_SPOT_RSI_FORWARD_CSV,
            COINBASE_BURST_FORWARD_CSV,
            COINBASE_EXPERIMENTAL_FORWARD_CSV,
            BTCUSD_H1_STEP_FORWARD_CSV,
            COINBASE_RATIO_FORWARD_CSV,
        ]
    )
    ratio_proof_rows = load_forward_review_rows(COINBASE_RATIO_PROOF_READINESS_CSV)
    fx_graduation_rows = load_fx_graduation_rows(FX_GRADUATION_READINESS_JSON)
    crypto_readiness_rows = load_fx_graduation_rows(ETH_M15_WARP_READINESS_JSON)
    crypto_probe_rows = load_fx_graduation_rows(CRYPTO_M15_WARP_READINESS_JSON)
    previous_state = load_json(STATE_JSON)
    previous_triggers = previous_state.get("lanes") if isinstance(previous_state, dict) else {}
    reset_baselines = load_reset_baselines()
    next_triggers: dict[str, Any] = {}
    rows: list[dict[str, Any]] = []

    mt5_ready, _mt5_connection = mt5_terminal_guard.initialize_mt5(mt5_module=mt5)
    broker_positions_by_magic: dict[int, list[dict[str, Any]]] = {}
    if mt5_ready:
        broker_positions_by_magic = collect_broker_positions_by_magic()
    try:
        for lane in registry:
            name = str(lane.get("name") or "")
            state_path = ROOT / str(lane.get("state_path") or "")
            payload = load_json(state_path)
            metrics = extract_state_metrics(payload)
            state_snapshot = snapshot_from_state_payload(payload) if isinstance(payload, dict) else {
                "realized_net_usd": 0.0,
                "closes": 0,
                "wins": 0,
                "losses": 0,
                "open_count": 0,
                "tracked_symbols": 0,
            }
            event_path = resolve_event_path(lane, state_path, metrics)
            events = summarize_events(event_path)
            watchdog_row = watchdog_rows.get(name) or {}
            runner = payload.get("runner") if isinstance(payload.get("runner"), dict) else {}
            runner_started_at = parse_iso(runner.get("started_at"))
            runner_session = summarize_trade_events_since(event_path, runner_started_at)
            runner_heartbeat_at = str(runner.get("heartbeat_at") or payload.get("updated_at") or "")
            state_last_write_at = file_last_write_iso(state_path)
            reset_row = reset_baselines.get(name) or {}
            session_parity = single_position_session_parity(
                kind=str(lane.get("kind") or ""),
                state_payload=payload if isinstance(payload, dict) else {},
                metrics=metrics,
                runner=runner,
                event_path=event_path,
            )
            exact_fire = exact_fire_metadata(str(lane.get("kind") or ""), name)

            quote = None
            signature = ""
            if (
                mt5_ready
                and metrics["next_buy_level"]
                and metrics["next_sell_level"]
                and metrics["symbol"]
                and should_monitor_trigger(str(lane.get("kind") or ""), metrics, now_dt)
            ):
                quote = live_quote(metrics["symbol"])
                signature = trigger_signature(metrics, quote)

            threshold_seconds = max(float(lane.get("poll_seconds") or 30.0) * 2.5, 75.0)
            trigger_watch, trigger_alert = update_trigger_watch(
                previous_triggers.get(name) if isinstance(previous_triggers, dict) else None,
                signature=signature,
                now_dt=now_dt,
                last_trade_event_at=events.get("last_trade_event_at"),
                threshold_seconds=threshold_seconds,
                suppress_execution_alert=suppress_execution_alerts_for_runner(runner),
            )
            if trigger_watch:
                next_triggers[name] = trigger_watch

            notes: list[str] = []
            raw_execution_alert = str(trigger_alert.get("execution_alert") or "")
            trigger_age_seconds = float(trigger_alert.get("trigger_age_seconds") or 0.0)
            event_last_write_at = file_last_write_iso(event_path)
            trigger_first_seen_at = str((trigger_watch or {}).get("first_seen_at") or "")
            spread_blocks = summarize_spread_blocks_since(event_path, runner_started_at)
            guard_blocks = summarize_guard_blocks_since(event_path, runner_started_at)
            trigger_quote_proof = summarize_trigger_quote_proof_since(
                event_path,
                parse_iso(trigger_first_seen_at),
                next_buy_level=float(metrics.get("next_buy_level") or 0.0),
                next_sell_level=float(metrics.get("next_sell_level") or 0.0),
            )
            refined_execution = refine_execution_alert(
                execution_alert=raw_execution_alert,
                trigger_first_seen_at=trigger_first_seen_at,
                event_last_write_at=event_last_write_at,
                state_last_write_at=state_last_write_at,
                runner_heartbeat_at=runner_heartbeat_at,
                lane_quote_cross_after_trigger=bool(trigger_quote_proof.get("quote_cross_after_trigger")),
                last_spread_block_at=str(spread_blocks.get("last_blocked_at") or ""),
                spread_block_count=int(spread_blocks.get("blocked_count") or 0),
                last_guard_block_at=str(guard_blocks.get("last_blocked_at") or ""),
                guard_block_count=int(guard_blocks.get("blocked_count") or 0),
            )
            execution_alert = str(refined_execution.get("execution_alert") or "")
            execution_flags = execution_alert_flags(execution_alert)
            notes.extend(
                execution_alert_notes(
                    raw_execution_alert=raw_execution_alert,
                    execution_alert=execution_alert,
                    signature=signature,
                    trigger_age_seconds=trigger_age_seconds,
                    execution_evidence_quality=str(refined_execution["execution_evidence_quality"] or ""),
                )
            )
            runner_trade_opens = int(runner_session.get("trade_open_count") or 0)
            runner_trade_closes = int(runner_session.get("trade_close_count") or 0)
            runner_trade_realized_usd = float(runner_session.get("trade_close_realized_usd") or 0.0)
            is_direct_live = bool(
                isinstance(payload, dict)
                and isinstance(payload.get("metadata"), dict)
                and payload.get("metadata", {}).get("direct_live")
            )
            status_runner = dict(runner)
            status_runner.update(
                synthetic_live_admissibility_runner(
                    runner=runner,
                    direct_live=is_direct_live,
                    runner_started_at=runner_started_at,
                    runner_trade_opens=runner_trade_opens,
                    runner_trade_closes=runner_trade_closes,
                    spread_blocks=spread_blocks,
                )
            )
            status_note = runner_status_note(status_runner)
            if status_note:
                notes.append(status_note)
            clean_forward = clean_forward_metrics(state_snapshot, reset_row)
            if clean_forward["clean_forward_reset_at"]:
                if clean_forward["clean_forward_counter_reset"]:
                    notes.append("clean_forward_state_reset_after_repair")
                notes.append(
                    f"clean_forward_since_repair={float(clean_forward['clean_forward_realized_delta_usd']):+.4f}/{int(clean_forward['clean_forward_new_closes'])}c"
                )
            inherited_closes, inherited_realized = clamp_inherited_broker_sync_history(
                inherited_closes=int(events.get("broker_sync_inherited_closes") or 0),
                inherited_realized_usd=float(events.get("broker_sync_inherited_realized_usd") or 0.0),
                state_snapshot=state_snapshot,
            )
            if inherited_closes > 0:
                notes.append(f"broker_sync_inherited_closes={inherited_closes}/{inherited_realized:+.2f}")
            if is_direct_live and runner_started_at is not None and (runner_trade_opens > 0 or runner_trade_closes > 0):
                notes.append(
                    f"runner_session_since_start={runner_trade_closes}c/{runner_trade_realized_usd:+.2f} {runner_trade_opens}o"
                )
            adjusted_trade_closes = int(events["trade_close_count"]) + inherited_closes
            close_gap = int(metrics["close_count"]) - adjusted_trade_closes
            direct_live_carry = direct_live_state_carry_metrics(
                state_snapshot,
                inherited_closes=inherited_closes,
                inherited_realized_usd=inherited_realized,
                runner_trade_closes=runner_trade_closes,
                runner_trade_realized_usd=runner_trade_realized_usd,
            )
            direct_live_open_carry = direct_live_open_carry_metrics(
                payload if isinstance(payload, dict) else {},
                runner_started_at=runner_started_at,
            )
            if is_direct_live and int(direct_live_carry["carry_closes"]) > 0:
                notes.append(
                    f"pre_start_state_carry={int(direct_live_carry['carry_closes'])}c/{float(direct_live_carry['carry_realized_usd']):+.2f}"
                )
            if is_direct_live and int(direct_live_open_carry["carry_open_count"]) > 0:
                carry_kind_counts = direct_live_open_carry.get("carry_kind_counts") or {}
                kind_note = " ".join(
                    f"{str(kind)}={int(count)}"
                    for kind, count in sorted(carry_kind_counts.items())
                    if int(count) > 0
                )
                carry_note = f"pre_start_open_carry={int(direct_live_open_carry['carry_open_count'])}o"
                if kind_note:
                    carry_note += f" {kind_note}"
                notes.append(carry_note)
            elif trustworthy_trade_event_gap_check(str(lane.get("kind") or "")) and close_gap > 0:
                notes.append(f"close_event_gap={close_gap}")
            trade_event_presence_note = missing_trade_events_note(
                kind=str(lane.get("kind") or ""),
                open_count=int(metrics["open_count"]),
                last_trade_event_at=str(events["last_trade_event_at"] or ""),
                state_last_write_at=state_last_write_at,
                runner_heartbeat_at=runner_heartbeat_at,
                runner_started_at=runner_started_at,
            )
            if trade_event_presence_note:
                notes.append(trade_event_presence_note)
            if session_parity["parity_alert"]:
                notes.append(session_parity["parity_alert"])
            scope_summary = {
                "live_magic": 0,
                "total_open_count": 0,
                "scoped_open_count": 0,
                "outside_open_count": 0,
                "outside_counts": {},
            }
            if mt5_ready and str(lane.get("kind") or "").startswith("live"):
                live_magics = lane_live_magics(lane, payload if isinstance(payload, dict) else {})
                scope_summary = broker_scope_summary(
                    broker_positions_by_magic,
                    live_magics=live_magics,
                    scoped_symbols=lane_scoped_symbols(lane, payload if isinstance(payload, dict) else {}),
                )
                outside_counts = scope_summary.get("outside_counts") or {}
                if outside_counts:
                    outside_text = " ".join(
                        f"{symbol}:{int(count)}" for symbol, count in sorted(outside_counts.items())
                    )
                    notes.append(f"broker_scope_outside_lane={outside_text}")
            forward_reason = forward_review_reason(lane, forward_review_rows.get(name))
            if forward_reason:
                notes.append(forward_reason)
            proof_reason = proof_readiness_reason(lane, ratio_proof_rows.get(name))
            if proof_reason:
                notes.append(proof_reason)
            fx_grad_reason = fx_graduation_reason(lane, fx_graduation_rows.get(name))
            if fx_grad_reason:
                notes.append(fx_grad_reason)
            crypto_reason = crypto_readiness_reason(lane, crypto_readiness_rows.get(name))
            if crypto_reason:
                notes.append(crypto_reason)
            crypto_probe = crypto_probe_reason(lane, crypto_probe_rows.get(name))
            if crypto_probe:
                notes.append(crypto_probe)
            durable_proof = payload.get("durable_proof") if isinstance(payload, dict) and isinstance(payload.get("durable_proof"), dict) else {}
            durable_closes = int(durable_proof.get("durable_realized_closes", 0) or 0)
            durable_realized = float(durable_proof.get("durable_realized_net_usd", 0.0) or 0.0)
            if durable_closes > int(state_snapshot.get("closes") or 0):
                notes.append(
                    f"durable_fx_proof={durable_closes}c/{durable_realized:+.2f} snapshot_behind"
                )

            rows.append(
                {
                    "lane": name,
                    "kind": str(lane.get("kind") or ""),
                    "watchdog_status": str(watchdog_row.get("status") or ""),
                    "heartbeat_at": runner_heartbeat_at,
                    "runner_heartbeat_at": runner_heartbeat_at,
                    "runner_status": str(
                        status_runner.get("status")
                        or status_runner.get("live_admissibility_reason")
                        or ""
                    ),
                    "live_admissibility_reason": str(status_runner.get("live_admissibility_reason") or ""),
                    "state_last_write_at": state_last_write_at,
                    "event_last_write_at": event_last_write_at,
                    "open_count": int(metrics["open_count"]),
                    "close_count": int(metrics["close_count"]),
                    "rearm_opens": int(metrics.get("rearm_opens") or 0),
                    "rearm_token_count": int(metrics.get("rearm_token_count") or 0),
                    "anchor_resets": int(metrics.get("anchor_resets") or 0),
                    "anchor_resets_flat": int(metrics.get("anchor_resets_flat") or 0),
                    "anchor_resets_risk": int(metrics.get("anchor_resets_risk") or 0),
                    "lattice_started": int(metrics.get("lattice_started_time") or 0) > 0,
                    "lattice_started_time": int(metrics.get("lattice_started_time") or 0),
                    "max_floating_loss_usd": float(metrics.get("max_floating_loss_usd") or 0.0),
                    "offensive_positive_close_ticket_profit_usd": float(
                        metrics.get("offensive_positive_close_ticket_profit_usd") or 0.0
                    ),
                    "offensive_spend_usd": float(metrics.get("offensive_spend_usd") or 0.0),
                    "offensive_budget_share": float(metrics.get("offensive_budget_share") or 0.0),
                    "offensive_closure_enabled": bool(metrics.get("offensive_closure_enabled") or False),
                    "offensive_safety_margin_usd": float(metrics.get("offensive_safety_margin_usd") or 0.0),
                    "offensive_safety_margin_pct": float(metrics.get("offensive_safety_margin_pct") or 0.0),
                    "offensive_cut_cooldown_bars": int(metrics.get("offensive_cut_cooldown_bars") or 0),
                    "offensive_breakeven_band_usd": float(metrics.get("offensive_breakeven_band_usd") or 0.0),
                    "max_lattice_window_bars": int(metrics.get("max_lattice_window_bars") or 0),
                    "breakout_buffer_pips": float(metrics.get("breakout_buffer_pips") or 0.0),
                    "base_step_px": float(metrics.get("base_step_px") or 0.0),
                    "base_step_sell_px": float(metrics.get("base_step_sell_px") or 0.0),
                    "base_step_buy_px": float(metrics.get("base_step_buy_px") or 0.0),
                    "reconcile_open_max_drift_px": float(metrics.get("reconcile_open_max_drift_px") or 0.0),
                    "open_realism_mode": str(metrics.get("open_realism_mode") or ""),
                    "close_realism_mode": str(metrics.get("close_realism_mode") or ""),
                    "raw_close_alpha": float(metrics.get("raw_close_alpha") or 0.0),
                    "raw_close_style": str(metrics.get("raw_close_style") or ""),
                    "momentum_gate": bool(metrics.get("momentum_gate") or False),
                    "min_positive_close_profit_usd": float(metrics.get("min_positive_close_profit_usd") or 0.0),
                    "positive_only_closes": bool(metrics.get("positive_only_closes") or runner.get("positive_only_closes") or False),
                    "positive_only_hold_active": bool(metrics.get("positive_only_hold_active") or runner.get("positive_only_hold_active") or False),
                    "positive_only_hold_reason": str(metrics.get("positive_only_hold_reason") or runner.get("positive_only_hold_reason") or ""),
                    "positive_only_hold_since": int(metrics.get("positive_only_hold_since") or 0),
                    "event_trade_opens": int(events["trade_open_count"]),
                    "event_trade_closes": int(events["trade_close_count"]),
                    "broker_sync_inherited_closes": inherited_closes,
                    "broker_sync_inherited_realized_usd": round(inherited_realized, 2),
                    "runner_session_trade_opens": runner_trade_opens,
                    "runner_session_trade_closes": runner_trade_closes,
                    "runner_session_trade_realized_usd": round(runner_trade_realized_usd, 2),
                    "pre_start_state_carry_closes": int(direct_live_carry["carry_closes"]),
                    "pre_start_state_carry_realized_usd": round(float(direct_live_carry["carry_realized_usd"]), 2),
                    "pre_start_open_carry_count": int(direct_live_open_carry["carry_open_count"]),
                    "pre_start_open_carry_kind_counts": dict(direct_live_open_carry.get("carry_kind_counts") or {}),
                    "last_trade_event_at": str(events["last_trade_event_at"] or ""),
                    "trigger_now": signature,
                    "trigger_first_seen_at": trigger_first_seen_at,
                    "quote_bid": round(float((quote or {}).get("bid") or 0.0), 2) if quote else "",
                    "quote_ask": round(float((quote or {}).get("ask") or 0.0), 2) if quote else "",
                    "next_buy_level": round(float(metrics["next_buy_level"]), 2) if metrics["next_buy_level"] else "",
                    "next_sell_level": round(float(metrics["next_sell_level"]), 2) if metrics["next_sell_level"] else "",
                    "trigger_age_seconds": trigger_age_seconds if signature else "",
                    "has_room": bool(int(metrics["max_open_total"] or 0) > int(metrics["open_count"])),
                    **clean_forward,
                    "execution_alert": execution_alert,
                    "raw_execution_alert": raw_execution_alert,
                    "execution_evidence_quality": str(refined_execution.get("execution_evidence_quality") or ""),
                    "lane_event_write_after_trigger": bool(refined_execution.get("lane_event_write_after_trigger")),
                    "state_write_after_trigger": bool(refined_execution.get("state_write_after_trigger")),
                    "runner_heartbeat_after_trigger": bool(refined_execution.get("runner_heartbeat_after_trigger")),
                    "suspected_missed_open": bool(execution_flags["suspected_missed_open"]),
                    "probable_missed_open": bool(execution_flags["probable_missed_open"]),
                    "parity_alert": str(session_parity["parity_alert"]),
                    **exact_fire,
                    "session_trade_opens": int(session_parity["session_trade_opens"]),
                    "session_trade_closes": int(session_parity["session_trade_closes"]),
                    "session_carry_in": int(session_parity["session_carry_in"]),
                    "session_last_trade_action": str(session_parity["session_last_trade_action"]),
                    "broker_magic_open_count": int(scope_summary["total_open_count"]),
                    "broker_scoped_open_count": int(scope_summary["scoped_open_count"]),
                    "broker_outside_scope_open_count": int(scope_summary["outside_open_count"]),
                    "broker_outside_scope_symbols": dict(scope_summary["outside_counts"]),
                    "forward_review": forward_review_rows.get(name) or {},
                    "proof_readiness": ratio_proof_rows.get(name) or {},
                    "fx_graduation": fx_graduation_rows.get(name) or {},
                    "crypto_readiness": crypto_readiness_rows.get(name) or {},
                    "crypto_probe_readiness": crypto_probe_rows.get(name) or {},
                    "notes": ", ".join(notes) if notes else "-",
                }
            )
    finally:
        if mt5_ready:
            mt5.shutdown()

    alert_rank = {
        "probable_missed_open": 0,
        "suspected_missed_open": 1,
        "": 2,
    }
    rows.sort(key=lambda row: (alert_rank.get(str(row["execution_alert"]), 99), str(row["lane"])))
    snapshot = {"updated_at": utc_now_iso(), "lanes": next_triggers}
    return rows, snapshot


def write_reports(rows: list[dict[str, Any]], snapshot: dict[str, Any]) -> None:
    write_json(STATE_JSON, snapshot)
    write_json(REPORT_JSON, {"generated_at": utc_now_iso(), "rows": rows})
    lines = [
        "# Execution Monitor",
        "",
        f"Generated: `{utc_now_iso()}`",
        "",
        "| Lane | Kind | Watchdog | Open | Closes | Anchor Resets | Rearm Opens | Rearm Tokens | Event Opens | Event Closes | Last Trade Event | Trigger Now | Trigger Age (s) | Alert | Parity | Bid | Ask | Next Buy | Next Sell | Room | Reset At | Clean Delta $ | Clean Closes | Notes |",
        "| --- | --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- | --- | ---: | --- | --- | ---: | ---: | ---: | ---: | ---: | --- | ---: | ---: | --- |",
    ]
    for row in rows:
        lines.append(
            f"| {row['lane']} | {row['kind']} | {row['watchdog_status']} | {row['open_count']} | {row['close_count']} | "
            f"{row['anchor_resets']} | {row['rearm_opens']} | {row['rearm_token_count']} | "
            f"{row['event_trade_opens']} | {row['event_trade_closes']} | {row['last_trade_event_at'] or '-'} | "
            f"{row['trigger_now'] or '-'} | {row['trigger_age_seconds'] if row['trigger_age_seconds'] != '' else '-'} | {row['execution_alert'] or '-'} | {row['parity_alert'] or '-'} | "
            f"{row['quote_bid'] or '-'} | {row['quote_ask'] or '-'} | "
            f"{row['next_buy_level'] or '-'} | {row['next_sell_level'] or '-'} | "
            f"{'yes' if row['has_room'] else 'no'} | {row['clean_forward_reset_at'] or '-'} | "
            f"{row['clean_forward_realized_delta_usd'] if row['clean_forward_realized_delta_usd'] != '' else '-'} | "
            f"{row['clean_forward_new_closes'] if row['clean_forward_new_closes'] != '' else '-'} | {row['notes']} |"
        )
    REPORT_MD.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    rows, snapshot = build_rows()
    write_reports(rows, snapshot)
    summary = {
        "json_path": str(REPORT_JSON),
        "md_path": str(REPORT_MD),
        "rows_total": len(rows),
        "probable_missed_open_count": sum(1 for row in rows if str(row.get("execution_alert") or "") == "probable_missed_open"),
        "suspected_missed_open_count": sum(1 for row in rows if str(row.get("execution_alert") or "") == "suspected_missed_open"),
        "rows_with_notes": sum(1 for row in rows if str(row.get("notes") or "-") != "-"),
    }
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
