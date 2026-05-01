#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import MetaTrader5 as mt5

import build_execution_monitor_report as execution_monitor
import mt5_terminal_guard


ROOT = Path(__file__).resolve().parent.parent
REGISTRY_PATH = ROOT / "configs" / "penetration_lattice_runner_registry.json"
EXECUTION_MONITOR_JSON = ROOT / "reports" / "execution_monitor_report.json"
BTC_CONCENTRATION_JSON = ROOT / "reports" / "live_btcusd_concentration_board.json"
GHOST_AUDIT_JSON = ROOT / "reports" / "ghost_position_audit.json"
REPORT_JSON = ROOT / "reports" / "live_m5_portfolio_board.json"
REPORT_MD = ROOT / "reports" / "live_m5_portfolio_board.md"

LIVE_M5_LANES = (
    "live_btcusd_m5_warp_probation_941780",
    "live_ethusd_m5_warp_941784",
    "live_solusd_m5_warp_941783",
)
M5_EXPANSION_WATCH_LANES = (
    "shadow_gbpusd_m5_warp",
    "shadow_usdjpy_m5_warp",
    "shadow_xauusd_m5_warp",
    "shadow_nas100_m5_warp",
)
NEW_LANE_NAMES = {
    "live_ethusd_m5_warp_941784",
    "live_solusd_m5_warp_941783",
}
COMBINED_FLOATING_REVIEW_USD = -8000.0
COMBINED_FLOATING_REVIEW_PCT = -10.0
EARLY_NEGATIVE_CLOSE_LIMIT = 10


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def utc_now_iso() -> str:
    return utc_now().isoformat()


def load_json(path: Path) -> Any:
    return execution_monitor.load_json(path)


def parse_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except Exception:
        return default


def parse_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except Exception:
        return default


def parse_iso(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00"))
    except Exception:
        return None


def age_seconds(now: datetime, value: Any) -> float | None:
    parsed = parse_iso(value)
    if parsed is None:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return round((now - parsed).total_seconds(), 1)


def format_money(value: Any) -> str:
    try:
        return f"${float(value):+,.2f}"
    except Exception:
        return "-"


def format_pct(value: Any) -> str:
    try:
        return f"{float(value):+.2f}%"
    except Exception:
        return "-"


def format_age_seconds(value: Any) -> str:
    try:
        seconds = float(value)
    except Exception:
        return "-"
    if seconds < 60.0:
        return f"{seconds:.1f}s"
    minutes = seconds / 60.0
    if minutes < 60.0:
        return f"{minutes:.1f}m"
    return f"{minutes / 60.0:.2f}h"


def avg_per_close(realized_usd: float, closes: int) -> float | None:
    if closes <= 0:
        return None
    return round(realized_usd / closes, 2)


def read_registry_rows(lane_names: tuple[str, ...] = LIVE_M5_LANES) -> list[dict[str, Any]]:
    rows = execution_monitor.read_registry(REGISTRY_PATH)
    wanted = set(lane_names)
    return [row for row in rows if str(row.get("name") or "") in wanted]


def registry_arg_value(lane: dict[str, Any], flag: str) -> str:
    restart_args = lane.get("restart_args") if isinstance(lane.get("restart_args"), list) else []
    for index, item in enumerate(restart_args):
        if str(item) != flag:
            continue
        if index + 1 < len(restart_args):
            return str(restart_args[index + 1])
    return ""


def execution_rows_by_lane() -> dict[str, dict[str, Any]]:
    payload = load_json(EXECUTION_MONITOR_JSON)
    rows = payload.get("rows") if isinstance(payload, dict) else []
    mapped: dict[str, dict[str, Any]] = {}
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        lane = str(row.get("lane") or "").strip()
        if lane:
            mapped[lane] = row
    return mapped


def broker_snapshot(mt5_module: Any = mt5) -> tuple[bool, dict[str, Any], dict[int, dict[str, Any]], dict[str, Any]]:
    connected, mt5_connection = mt5_terminal_guard.initialize_mt5(mt5_module=mt5_module)
    if not connected:
        return False, {}, {}, mt5_connection
    try:
        account_info = mt5_module.account_info()
        account_payload = {
            "balance_usd": parse_float(getattr(account_info, "balance", 0.0) if account_info else 0.0),
            "equity_usd": parse_float(getattr(account_info, "equity", 0.0) if account_info else 0.0),
            "profit_usd": parse_float(getattr(account_info, "profit", 0.0) if account_info else 0.0),
            "margin_level_pct": parse_float(getattr(account_info, "margin_level", 0.0) if account_info else 0.0),
        }
        grouped: dict[int, dict[str, Any]] = {}
        for pos in mt5_module.positions_get() or []:
            magic = parse_int(getattr(pos, "magic", 0))
            row = grouped.setdefault(
                magic,
                {
                    "open_count": 0,
                    "buy_count": 0,
                    "sell_count": 0,
                    "floating_usd": 0.0,
                },
            )
            row["open_count"] += 1
            side = "SELL" if parse_int(getattr(pos, "type", 0)) == 1 else "BUY"
            if side == "SELL":
                row["sell_count"] += 1
            else:
                row["buy_count"] += 1
            row["floating_usd"] = round(row["floating_usd"] + parse_float(getattr(pos, "profit", 0.0)), 2)
        return True, account_payload, grouped, mt5_connection
    finally:
        mt5_module.shutdown()


def state_symbol_payload(state_payload: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    symbols = state_payload.get("symbols") if isinstance(state_payload.get("symbols"), dict) else {}
    for symbol, payload in symbols.items():
        if isinstance(payload, dict):
            return str(symbol), payload
    return "", {}


def runtime_mismatch_fields(
    *,
    configured_timeframe: str,
    configured_poll_seconds: float | None,
    configured_step: float | None,
    runtime_timeframe: str,
    runtime_poll_seconds: float | None,
    runtime_step: float | None,
) -> list[str]:
    fields: list[str] = []
    if configured_timeframe and runtime_timeframe and configured_timeframe != runtime_timeframe:
        fields.append("timeframe")
    if configured_poll_seconds is not None and runtime_poll_seconds is not None:
        if abs(configured_poll_seconds - runtime_poll_seconds) > 0.001:
            fields.append("poll_seconds")
    if configured_step is not None and runtime_step is not None:
        if abs(configured_step - runtime_step) > 0.000001:
            fields.append("step")
    return fields


def build_rows(
    *,
    now: datetime,
    registry_rows: list[dict[str, Any]],
    execution_rows: dict[str, dict[str, Any]],
    broker_connected: bool,
    broker_by_magic: dict[int, dict[str, Any]],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []

    for lane in registry_rows:
        lane_name = str(lane.get("name") or "")
        state_path = ROOT / str(lane.get("state_path") or "")
        state_payload = load_json(state_path)
        symbol_name, symbol_payload = state_symbol_payload(state_payload if isinstance(state_payload, dict) else {})
        metadata = state_payload.get("metadata") if isinstance(state_payload.get("metadata"), dict) else {}
        runner = state_payload.get("runner") if isinstance(state_payload.get("runner"), dict) else {}
        execution_row = execution_rows.get(lane_name) or {}

        live_magic = execution_monitor.lane_live_magic(lane, state_payload if isinstance(state_payload, dict) else {})
        broker_row = broker_by_magic.get(live_magic, {})
        configured_timeframe = registry_arg_value(lane, "--timeframe") or str(metadata.get("timeframe") or "")
        configured_step = parse_float(registry_arg_value(lane, "--step"), parse_float(metadata.get("step"), 0.0))
        configured_poll_seconds = parse_float(
            registry_arg_value(lane, "--poll-seconds"),
            parse_float(lane.get("poll_seconds"), 0.0),
        )
        runtime_timeframe = str(metadata.get("timeframe") or symbol_payload.get("timeframe") or "")
        runtime_poll_seconds = parse_float(runner.get("poll_seconds"), 0.0)
        runtime_step = parse_float(metadata.get("step"), parse_float(symbol_payload.get("base_step_px"), 0.0))

        open_tickets = symbol_payload.get("open_tickets") if isinstance(symbol_payload.get("open_tickets"), list) else []
        managed_buy_count = sum(1 for ticket in open_tickets if str(ticket.get("direction") or "").upper() == "BUY")
        managed_sell_count = sum(1 for ticket in open_tickets if str(ticket.get("direction") or "").upper() == "SELL")
        realized_net_usd = round(parse_float(symbol_payload.get("realized_net_usd")), 2)
        realized_closes = parse_int(symbol_payload.get("realized_closes"))
        floating_usd = round(parse_float(broker_row.get("floating_usd")), 2) if broker_connected else None
        net_usd = round(realized_net_usd + floating_usd, 2) if floating_usd is not None else None
        inherited_closes = parse_int(execution_row.get("broker_sync_inherited_closes"))
        inherited_realized_usd = round(parse_float(execution_row.get("broker_sync_inherited_realized_usd")), 2)
        fresh_trade_events = bool(str(execution_row.get("last_trade_event_at") or "").strip())
        inherited_only_probe = (
            lane_name in NEW_LANE_NAMES
            and inherited_closes > 0
            and inherited_closes == realized_closes
            and abs(inherited_realized_usd - realized_net_usd) <= 0.01
            and not fresh_trade_events
        )

        row = {
            "lane": lane_name,
            "kind": str(lane.get("kind") or ""),
            "enabled": bool(lane.get("enabled", True)),
            "pause_note": str(lane.get("pause_note") or ""),
            "symbol": symbol_name,
            "live_magic": live_magic,
            "configured_timeframe": configured_timeframe,
            "runtime_timeframe": runtime_timeframe,
            "configured_step": round(configured_step, 6) if configured_step else 0.0,
            "runtime_step": round(runtime_step, 6) if runtime_step else 0.0,
            "configured_poll_seconds": round(configured_poll_seconds, 3) if configured_poll_seconds else 0.0,
            "runtime_poll_seconds": round(runtime_poll_seconds, 3) if runtime_poll_seconds else 0.0,
            "runtime_mismatch_fields": runtime_mismatch_fields(
                configured_timeframe=configured_timeframe,
                configured_poll_seconds=configured_poll_seconds if configured_poll_seconds else None,
                configured_step=configured_step if configured_step else None,
                runtime_timeframe=runtime_timeframe,
                runtime_poll_seconds=runtime_poll_seconds if runtime_poll_seconds else None,
                runtime_step=runtime_step if runtime_step else None,
            ),
            "pid": parse_int(runner.get("pid")),
            "heartbeat_at": str(runner.get("heartbeat_at") or ""),
            "heartbeat_age_seconds": age_seconds(now, runner.get("heartbeat_at")),
            "started_at": str(runner.get("started_at") or ""),
            "realized_net_usd": realized_net_usd,
            "realized_closes": realized_closes,
            "realized_usd_per_close": avg_per_close(realized_net_usd, realized_closes),
            "floating_usd": floating_usd,
            "net_usd": net_usd,
            "managed_open_count": len(open_tickets),
            "managed_buy_count": managed_buy_count,
            "managed_sell_count": managed_sell_count,
            "broker_open_count": parse_int(broker_row.get("open_count")),
            "broker_buy_count": parse_int(broker_row.get("buy_count")),
            "broker_sell_count": parse_int(broker_row.get("sell_count")),
            "anchor_resets": parse_int(symbol_payload.get("anchor_resets")),
            "rearm_opens": parse_int(symbol_payload.get("rearm_opens")),
            "quote_bid": round(parse_float(execution_row.get("quote_bid")), 6),
            "quote_ask": round(parse_float(execution_row.get("quote_ask")), 6),
            "next_buy_level": round(parse_float(symbol_payload.get("next_buy_level"), parse_float(execution_row.get("next_buy_level"))), 6),
            "next_sell_level": round(parse_float(symbol_payload.get("next_sell_level"), parse_float(execution_row.get("next_sell_level"))), 6),
            "watchdog_status": str(execution_row.get("watchdog_status") or ""),
            "last_trade_event_at": str(execution_row.get("last_trade_event_at") or ""),
            "last_trade_event_age_seconds": age_seconds(now, execution_row.get("last_trade_event_at")),
            "clean_forward_realized_delta_usd": round(parse_float(execution_row.get("clean_forward_realized_delta_usd")), 2),
            "clean_forward_new_closes": parse_int(execution_row.get("clean_forward_new_closes")),
            "broker_sync_inherited_closes": inherited_closes,
            "broker_sync_inherited_realized_usd": inherited_realized_usd,
            "inherited_only_probe": inherited_only_probe,
            "notes": str(execution_row.get("notes") or ""),
            "state_path": str(state_path.relative_to(ROOT)),
        }
        row["detached_broker_inventory"] = bool(
            parse_int(row.get("broker_open_count")) > 0
            and (
                not bool(row.get("enabled", True))
                or parse_int(row.get("pid")) <= 0
                or str(row.get("watchdog_status") or "").strip().lower() not in {"ok"}
            )
        )
        if not row["enabled"]:
            row["watchdog_status"] = "paused"
            row["pid"] = 0
            row["heartbeat_at"] = ""
            row["heartbeat_age_seconds"] = None
        rows.append(row)

    rows.sort(key=lambda row: row["lane"])
    return rows


def build_payload_from_inputs(
    *,
    generated_at: str,
    registry_rows: list[dict[str, Any]],
    expansion_registry_rows: list[dict[str, Any]] | None,
    execution_rows: dict[str, dict[str, Any]],
    broker_connected: bool,
    account_payload: dict[str, Any],
    broker_by_magic: dict[int, dict[str, Any]],
    btc_concentration_summary: dict[str, Any],
    ghost_positions_payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    now = parse_iso(generated_at) or utc_now()
    rows = build_rows(
        now=now,
        registry_rows=registry_rows,
        execution_rows=execution_rows,
        broker_connected=broker_connected,
        broker_by_magic=broker_by_magic,
    )
    expansion_rows = build_rows(
        now=now,
        registry_rows=expansion_registry_rows or [],
        execution_rows=execution_rows,
        broker_connected=False,
        broker_by_magic={},
    )
    lane_by_magic = {parse_int(row.get("live_magic")): row for row in rows if parse_int(row.get("live_magic")) > 0}
    ghost_positions = ghost_positions_payload.get("positions") if isinstance(ghost_positions_payload, dict) else []
    ghost_rows_map: dict[str, dict[str, Any]] = {}
    for position in ghost_positions if isinstance(ghost_positions, list) else []:
        if not isinstance(position, dict):
            continue
        magic = parse_int(position.get("magic"))
        owner_row = lane_by_magic.get(magic)
        lane_name = owner_row.get("lane") if owner_row else (f"magic_{magic}" if magic > 0 else "unmapped")
        ghost_row = ghost_rows_map.setdefault(
            lane_name,
            {
                "lane": lane_name,
                "symbol": str(position.get("symbol") or (owner_row.get("symbol") if owner_row else "")),
                "live_magic": magic,
                "status": str(position.get("status") or ("UNKNOWN" if owner_row else "UNMAPPED")),
                "audit_state": "unmapped" if owner_row is None else "",
                "current_broker_open_count": parse_int(owner_row.get("broker_open_count")) if owner_row else 0,
                "position_count": 0,
                "floating_usd": 0.0,
                "tickets": [],
            },
        )
        ghost_row["position_count"] += 1
        ghost_row["floating_usd"] = round(parse_float(ghost_row.get("floating_usd")) + parse_float(position.get("profit")), 2)
        ticket = parse_int(position.get("ticket"))
        if ticket > 0:
            ghost_row["tickets"].append(ticket)
    for ghost_row in ghost_rows_map.values():
        current_broker_open_count = parse_int(ghost_row.get("current_broker_open_count"))
        if str(ghost_row.get("audit_state") or "").strip():
            continue
        ghost_row["audit_state"] = "active" if current_broker_open_count > 0 else "stale_or_cleared"
    ghost_rows = sorted(
        ghost_rows_map.values(),
        key=lambda row: (-parse_int(row.get("position_count")), str(row.get("lane") or "")),
    )
    ghost_summary = {
        "generated_at": str(ghost_positions_payload.get("ts_utc") or "") if isinstance(ghost_positions_payload, dict) else "",
        "position_count": sum(parse_int(row.get("position_count")) for row in ghost_rows),
        "lane_count": len(ghost_rows),
        "active_position_count": sum(
            parse_int(row.get("position_count")) for row in ghost_rows if str(row.get("audit_state") or "") == "active"
        ),
        "stale_position_count": sum(
            parse_int(row.get("position_count")) for row in ghost_rows if str(row.get("audit_state") or "") == "stale_or_cleared"
        ),
        "floating_usd": round(sum(parse_float(row.get("floating_usd")) for row in ghost_rows), 2),
    }

    combined_realized = round(sum(parse_float(row.get("realized_net_usd")) for row in rows), 2)
    combined_realized_closes = sum(parse_int(row.get("realized_closes")) for row in rows)
    combined_floating = round(sum(parse_float(row.get("floating_usd")) for row in rows), 2) if broker_connected else None
    combined_net = round(combined_realized + combined_floating, 2) if combined_floating is not None else None
    combined_managed_open = sum(parse_int(row.get("managed_open_count")) for row in rows)
    combined_broker_open = sum(parse_int(row.get("broker_open_count")) for row in rows)
    equity_usd = parse_float(account_payload.get("equity_usd"))

    flags: list[dict[str, Any]] = []
    if combined_floating is not None:
        if combined_floating <= COMBINED_FLOATING_REVIEW_USD:
            flags.append(
                {
                    "flag": "combined_floating_pressure",
                    "read": f"combined_floating={combined_floating:+.2f} <= {COMBINED_FLOATING_REVIEW_USD:+.2f}",
                }
            )
        if equity_usd > 0:
            floating_pct = (combined_floating / equity_usd) * 100.0
            if floating_pct <= COMBINED_FLOATING_REVIEW_PCT:
                flags.append(
                    {
                        "flag": "combined_floating_pct_pressure",
                        "read": f"combined_floating_pct={floating_pct:+.2f}% <= {COMBINED_FLOATING_REVIEW_PCT:+.2f}%",
                    }
                )

    missing_watchdog = [
        row["lane"]
        for row in rows
        if bool(row.get("enabled", True))
        and str(row.get("watchdog_status") or "").strip().lower() != "ok"
    ]
    if missing_watchdog:
        flags.append(
            {
                "flag": "watchdog_surface_gap",
                "read": ", ".join(missing_watchdog),
            }
        )

    runtime_drift = {row["lane"]: row["runtime_mismatch_fields"] for row in rows if row["runtime_mismatch_fields"]}
    if runtime_drift:
        flags.append(
            {
                "flag": "runtime_config_drift",
                "read": ", ".join(f"{lane}({','.join(fields)})" for lane, fields in runtime_drift.items()),
            }
        )

    early_negative = [
        row["lane"]
        for row in rows
        if row["lane"] in NEW_LANE_NAMES
        and 0 < parse_int(row.get("realized_closes")) < EARLY_NEGATIVE_CLOSE_LIMIT
        and parse_float(row.get("realized_net_usd")) < 0.0
        and not bool(row.get("inherited_only_probe"))
    ]
    if early_negative:
        flags.append(
            {
                "flag": "new_lane_early_negative",
                "read": ", ".join(early_negative),
            }
        )

    probe_recovering = [row["lane"] for row in rows if bool(row.get("inherited_only_probe"))]
    if probe_recovering:
        flags.append(
            {
                "flag": "inherited_probe_carry",
                "read": ", ".join(probe_recovering),
            }
        )

    detached_inventory = [
        f"{row['lane']}({parse_int(row.get('broker_open_count'))} broker open,{row.get('watchdog_status') or 'unknown'})"
        for row in rows
        if bool(row.get("detached_broker_inventory"))
    ]
    if detached_inventory:
        flags.append(
            {
                "flag": "detached_broker_inventory",
                "read": ", ".join(detached_inventory),
            }
        )

    btc_triggered = btc_concentration_summary.get("triggered_thresholds")
    if isinstance(btc_triggered, list) and btc_triggered:
        flags.append(
            {
                "flag": "btc_concentration_triggers",
                "read": ",".join(str(item) for item in btc_triggered),
            }
        )

    operator_posture = "monitor"
    if any(
        flag["flag"] in {"combined_floating_pressure", "combined_floating_pct_pressure", "runtime_config_drift", "detached_broker_inventory"}
        for flag in flags
    ):
        operator_posture = "operator_review_required"
    elif flags:
        operator_posture = "watch_closely"

    summary = {
        "lane_count": len(rows),
        "combined_realized_usd": combined_realized,
        "combined_realized_closes": combined_realized_closes,
        "combined_realized_usd_per_close": avg_per_close(combined_realized, combined_realized_closes),
        "combined_floating_usd": combined_floating,
        "combined_net_usd": combined_net,
        "combined_managed_open_count": combined_managed_open,
        "combined_broker_open_count": combined_broker_open,
        "equity_usd": round(equity_usd, 2),
        "balance_usd": round(parse_float(account_payload.get("balance_usd")), 2),
        "account_profit_usd": round(parse_float(account_payload.get("profit_usd")), 2),
        "margin_level_pct": round(parse_float(account_payload.get("margin_level_pct")), 2),
        "operator_posture": operator_posture,
        "flag_count": len(flags),
        "btc_combined_floating_usd": btc_concentration_summary.get("combined_floating_usd"),
        "btc_combined_net_usd": btc_concentration_summary.get("combined_net_usd"),
        "btc_triggered_thresholds": btc_triggered if isinstance(btc_triggered, list) else [],
    }
    if combined_floating is not None and equity_usd > 0:
        summary["combined_floating_pct_equity"] = round((combined_floating / equity_usd) * 100.0, 3)
        summary["combined_net_pct_equity"] = round(((combined_net or 0.0) / equity_usd) * 100.0, 3)
    else:
        summary["combined_floating_pct_equity"] = None
        summary["combined_net_pct_equity"] = None

    return {
        "generated_at": generated_at,
        "broker_connected": broker_connected,
        "sources": [
            str(REGISTRY_PATH.relative_to(ROOT)),
            str(EXECUTION_MONITOR_JSON.relative_to(ROOT)),
            str(BTC_CONCENTRATION_JSON.relative_to(ROOT)),
            str(GHOST_AUDIT_JSON.relative_to(ROOT)),
        ],
        "summary": summary,
        "ghost_summary": ghost_summary,
        "flags": flags,
        "rows": rows,
        "ghost_rows": ghost_rows,
        "expansion_watch_rows": expansion_rows,
    }


def build_payload(mt5_module: Any = mt5) -> dict[str, Any]:
    generated_at = utc_now_iso()
    btc_payload = load_json(BTC_CONCENTRATION_JSON)
    btc_summary = btc_payload.get("summary") if isinstance(btc_payload, dict) else {}
    broker_connected, account_payload, broker_by_magic, mt5_connection = broker_snapshot(mt5_module=mt5_module)
    if not broker_connected and isinstance(btc_summary, dict):
        account_payload = {
            "balance_usd": parse_float(btc_summary.get("balance_usd")),
            "equity_usd": parse_float(btc_summary.get("equity_usd")),
            "profit_usd": 0.0,
            "margin_level_pct": parse_float(btc_summary.get("margin_level_pct")),
        }
    payload = build_payload_from_inputs(
        generated_at=generated_at,
        registry_rows=read_registry_rows(LIVE_M5_LANES),
        expansion_registry_rows=read_registry_rows(M5_EXPANSION_WATCH_LANES),
        execution_rows=execution_rows_by_lane(),
        broker_connected=broker_connected,
        account_payload=account_payload,
        broker_by_magic=broker_by_magic,
        btc_concentration_summary=btc_summary if isinstance(btc_summary, dict) else {},
        ghost_positions_payload=load_json(GHOST_AUDIT_JSON),
    )
    payload["mt5_connection"] = mt5_connection
    return payload


def render_markdown(payload: dict[str, Any]) -> str:
    summary = payload.get("summary") if isinstance(payload.get("summary"), dict) else {}
    ghost_summary = payload.get("ghost_summary") if isinstance(payload.get("ghost_summary"), dict) else {}
    rows = payload.get("rows") if isinstance(payload.get("rows"), list) else []
    ghost_rows = payload.get("ghost_rows") if isinstance(payload.get("ghost_rows"), list) else []
    expansion_rows = payload.get("expansion_watch_rows") if isinstance(payload.get("expansion_watch_rows"), list) else []
    flags = payload.get("flags") if isinstance(payload.get("flags"), list) else []
    mt5_connection = payload.get("mt5_connection") if isinstance(payload.get("mt5_connection"), dict) else {}
    lines = [
        "# Live M5 Portfolio Board",
        "",
        "> Current runtime generated board.",
        "",
        f"- Generated at: `{payload.get('generated_at', '')}`",
        f"- Broker connected: `{str(bool(payload.get('broker_connected'))).lower()}`",
        f"- MT5 identity guard: `{'ok' if mt5_connection.get('identity_ok') else mt5_connection.get('reason', 'unknown')}`",
        f"- Account equity: `{format_money(summary.get('equity_usd'))}` | balance `{format_money(summary.get('balance_usd'))}` | live PnL `{format_money(summary.get('account_profit_usd'))}` | margin `{parse_float(summary.get('margin_level_pct')):.2f}%`",
        f"- Combined realized: `{format_money(summary.get('combined_realized_usd'))}` across `{parse_int(summary.get('combined_realized_closes'))}` closes (`{format_money(summary.get('combined_realized_usd_per_close'))}/close`)",
        f"- Combined floating: `{format_money(summary.get('combined_floating_usd'))}` ({format_pct(summary.get('combined_floating_pct_equity'))} of equity)",
        f"- Combined net: `{format_money(summary.get('combined_net_usd'))}` ({format_pct(summary.get('combined_net_pct_equity'))} of equity)",
        f"- Combined opens: managed `{parse_int(summary.get('combined_managed_open_count'))}` / broker `{parse_int(summary.get('combined_broker_open_count'))}`",
        f"- Operator posture: `{summary.get('operator_posture', '-')}`",
    ]
    btc_triggers = summary.get("btc_triggered_thresholds")
    if isinstance(btc_triggers, list):
        lines.append(
            f"- BTC concentration carry context: floating `{format_money(summary.get('btc_combined_floating_usd'))}` / net `{format_money(summary.get('btc_combined_net_usd'))}` / triggers `{','.join(str(item) for item in btc_triggers) or 'none'}`"
        )

    lines.extend(
        [
            "",
            "| Lane | Realized | Floating | Net | Closes | Open M/B | $/Close | Resets | Watchdog | Last Trade | Notes |",
            "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- | --- | --- |",
        ]
    )
    for row in rows:
        notes = row.get("notes") or "-"
        if bool(row.get("inherited_only_probe")):
            notes = f"{notes}; fresh probe after restart, losses inherited via broker sync"
        if not bool(row.get("enabled", True)):
            paused_note = str(row.get("pause_note") or "paused_in_registry")
            notes = f"{notes}; {paused_note}" if notes and notes != "-" else paused_note
        if bool(row.get("detached_broker_inventory")):
            detached_note = "broker inventory still open while lane is paused/stale"
            notes = f"{notes}; {detached_note}" if notes and notes != "-" else detached_note
        lines.append(
            f"| {row['lane']} | {format_money(row.get('realized_net_usd'))} | {format_money(row.get('floating_usd'))} | "
            f"{format_money(row.get('net_usd'))} | {parse_int(row.get('realized_closes'))} | {parse_int(row.get('managed_open_count'))}/{parse_int(row.get('broker_open_count'))} | "
            f"{format_money(row.get('realized_usd_per_close'))} | {parse_int(row.get('anchor_resets'))} | "
            f"{row.get('watchdog_status') or '-'} | {format_age_seconds(row.get('last_trade_event_age_seconds'))} | {notes} |"
        )

    lines.extend(
        [
            "",
            "## Runtime",
            "",
            "| Lane | Symbol | Magic | PID | Step | Timeframe | Poll | Quote | Next Buy | Next Sell | Drift |",
            "| --- | --- | ---: | ---: | ---: | --- | ---: | --- | ---: | ---: | --- |",
        ]
    )
    for row in rows:
        drift = "paused" if not bool(row.get("enabled", True)) else (",".join(row.get("runtime_mismatch_fields") or []) or "-")
        lines.append(
            f"| {row['lane']} | {row.get('symbol') or '-'} | {parse_int(row.get('live_magic'))} | {parse_int(row.get('pid'))} | "
            f"{parse_float(row.get('runtime_step')):.6g} | {row.get('runtime_timeframe') or '-'} | {parse_float(row.get('runtime_poll_seconds')):.3g} | "
            f"{parse_float(row.get('quote_bid')):.6g}/{parse_float(row.get('quote_ask')):.6g} | {parse_float(row.get('next_buy_level')):.6g} | {parse_float(row.get('next_sell_level')):.6g} | {drift} |"
        )

    if ghost_rows:
        lines.extend(
            [
                "",
                "## Ghost Carry Audit",
                "",
                f"- Audit generated at: `{ghost_summary.get('generated_at') or '-'}`",
                f"- Ghost positions: `{parse_int(ghost_summary.get('position_count'))}` across `{parse_int(ghost_summary.get('lane_count'))}` lanes, floating `{format_money(ghost_summary.get('floating_usd'))}`",
                f"- Live reconciliation: active `{parse_int(ghost_summary.get('active_position_count'))}` / stale-or-cleared `{parse_int(ghost_summary.get('stale_position_count'))}`",
                "",
                "| Lane | Symbol | Magic | Status | Audit State | Tickets | Floating | Ticket IDs |",
                "| --- | --- | ---: | --- | --- | ---: | ---: | --- |",
            ]
        )
        for row in ghost_rows:
            ticket_ids = ",".join(str(ticket) for ticket in row.get("tickets") or []) or "-"
            lines.append(
                f"| {row.get('lane') or '-'} | {row.get('symbol') or '-'} | {parse_int(row.get('live_magic'))} | {row.get('status') or '-'} | "
                f"{row.get('audit_state') or '-'} | {parse_int(row.get('position_count'))} | {format_money(row.get('floating_usd'))} | {ticket_ids} |"
            )

    if expansion_rows:
        lines.extend(
            [
                "",
                "## Expansion Watch",
                "",
                "| Lane | Kind | Closes | Open | $/Close | Resets | Watchdog | Last Trade | Quote | Next Buy | Next Sell | Notes |",
                "| --- | --- | ---: | ---: | ---: | ---: | --- | --- | --- | ---: | ---: | --- |",
            ]
        )
        for row in expansion_rows:
            lines.append(
                f"| {row['lane']} | {row.get('kind') or '-'} | {parse_int(row.get('realized_closes'))} | {parse_int(row.get('managed_open_count'))} | "
                f"{format_money(row.get('realized_usd_per_close'))} | {parse_int(row.get('anchor_resets'))} | {row.get('watchdog_status') or '-'} | "
                f"{format_age_seconds(row.get('last_trade_event_age_seconds'))} | {parse_float(row.get('quote_bid')):.6g}/{parse_float(row.get('quote_ask')):.6g} | "
                f"{parse_float(row.get('next_buy_level')):.6g} | {parse_float(row.get('next_sell_level')):.6g} | {row.get('notes') or '-'} |"
            )

    lines.extend(["", "## Flags", ""])
    if not flags:
        lines.append("- none")
    for flag in flags:
        lines.append(f"- `{flag.get('flag', '-')}`: {flag.get('read', '-')}")

    lines.extend(
        [
            "",
            "## Read",
            "",
            "- Use this board as the combined M5 live answer before changing volume or adding new M5 exposure.",
            "- `detached_broker_inventory` means MT5 still has positions for that lane's live magic while the lane is paused or no longer heartbeating cleanly. Treat that as a manual carry review, not as a normal paused probe.",
            "- `Ghost Carry Audit` preserves the last ticket-level paused/stale carry audit. Read `audit_state=active` as still confirmed by the live broker snapshot and `stale_or_cleared` as historical evidence that should be refreshed before anyone liquidates.",
            "- BTC M5 still dominates portfolio floating risk; ETH/SOL should be read as fresh-launch probes until they clear a larger close sample.",
            "- Use `Expansion Watch` for the new FX/index M5 probes; those rows are execution/runtime visibility only and do not change the live BTC/ETH/SOL exposure totals.",
            "- Inherited broker-sync closes on a zero-open restart are carry-in truth, not fresh launch failure evidence.",
            "- `runtime_config_drift` is not a kill switch, but it means the running lane is not matching its registry posture and needs operator review.",
        ]
    )
    return "\n".join(lines).rstrip() + "\n"


def write_outputs(payload: dict[str, Any]) -> None:
    REPORT_JSON.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    REPORT_MD.write_text(render_markdown(payload), encoding="utf-8")


def run_once(*, as_json: bool, mt5_module: Any = mt5) -> int:
    payload = build_payload(mt5_module=mt5_module)
    write_outputs(payload)
    if as_json:
        print(json.dumps(payload, indent=2))
    else:
        print(render_markdown(payload))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Build or poll the live M5 portfolio operator board")
    parser.add_argument("--json", action="store_true", help="Print JSON payload instead of Markdown")
    parser.add_argument("--loop", action="store_true", help="Continuously refresh the board")
    parser.add_argument("--sleep-seconds", type=float, default=300.0, help="Loop refresh interval when --loop is set")
    args = parser.parse_args()

    if not args.loop:
        return run_once(as_json=args.json)

    try:
        while True:
            run_once(as_json=args.json)
            time.sleep(max(args.sleep_seconds, 1.0))
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
