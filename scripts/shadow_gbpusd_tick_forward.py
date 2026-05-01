#!/usr/bin/env python3
"""
GBPUSD Tick-Native Forward-Shadow Validation

This is the live forward-proof lane for the surviving GBPUSD low-step FX shape:
- symbol: GBPUSD
- sell_step=0.5 / buy_step=1.0
- sell_gap=1 / buy_gap=3
- close_alpha=0.5 (all_profitable style)

Important:
- this is shadow-only, not live execution
- it uses live MT5 ticks with broker-touch semantics
- it now runs as a persistent supervised lane instead of a one-shot probe
"""
from __future__ import annotations

import argparse
import json
import os
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import MetaTrader5 as mt5

import mt5_terminal_guard
from tick_penetration_lattice_core import (
    TickStatefulRearmEngine,
    engine_from_args,
    load_recent_bars,
    load_ticks_since,
    tick_pnl_usd,
)

ROOT = Path(__file__).resolve().parent.parent

SYMBOL = "GBPUSD"
STEP_SELL = 0.5
STEP_BUY = 1.0
SELL_GAP = 1
BUY_GAP = 3
CLOSE_ALPHA = 0.5
MAX_OPEN_PER_SIDE = 40
VOLUME = 0.01
DEFAULT_POLL_SECONDS = 5

DEFAULT_STATE_PATH = ROOT / "reports" / "shadow_gbpusd_tick_forward_state.json"
DEFAULT_EVENT_PATH = ROOT / "reports" / "shadow_gbpusd_tick_forward_events.jsonl"
DEFAULT_REPORT_MD_PATH = ROOT / "reports" / "gbpusd_tick_forward_shadow.md"


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def load_json(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    return payload if isinstance(payload, dict) else None


def atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    tmp.replace(path)


def append_event(path: Path, event: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    record = dict(event)
    record["ts_utc"] = utc_now_iso()
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, sort_keys=True) + "\n")


def parse_event_progress(path: Path) -> dict[str, Any]:
    best: dict[str, Any] = {
        "durable_realized_closes": 0,
        "durable_realized_net_usd": 0.0,
        "durable_open_count": 0,
        "last_seen_at": "",
        "source": "",
        "counter_regressed": False,
    }
    if not path.exists():
        return best
    try:
        lines = path.read_text(encoding="utf-8", errors="ignore").splitlines()
    except Exception:
        return best

    for line in lines:
        try:
            row = json.loads(line)
        except Exception:
            continue

        closes_value = row.get("closes", row.get("realized_closes"))
        realized_value = row.get("realized", row.get("realized_net_usd"))
        open_value = row.get("open_count")
        if closes_value is None and realized_value is None:
            continue
        try:
            closes = int(closes_value or 0)
        except Exception:
            closes = 0
        try:
            realized = float(realized_value or 0.0)
        except Exception:
            realized = 0.0
        try:
            open_count = int(open_value or 0)
        except Exception:
            open_count = 0
        seen_at = str(row.get("ts_utc") or "")
        if (
            closes > int(best["durable_realized_closes"])
            or (
                closes == int(best["durable_realized_closes"])
                and seen_at
                and seen_at >= str(best["last_seen_at"] or "")
            )
        ):
            best = {
                "durable_realized_closes": closes,
                "durable_realized_net_usd": realized,
                "durable_open_count": open_count,
                "last_seen_at": seen_at,
                "source": "event_log_summary",
                "counter_regressed": False,
            }
    return best


def parse_trade_action_inventory(path: Path) -> dict[str, Any]:
    inventory: dict[str, Any] = {
        "durable_open_count": 0,
        "last_seen_at": "",
        "source": "",
    }
    if not path.exists():
        return inventory
    try:
        lines = path.read_text(encoding="utf-8", errors="ignore").splitlines()
    except Exception:
        return inventory

    open_count = 0
    trade_actions_seen = False
    latest_resume: dict[str, Any] = {"durable_open_count": 0, "last_seen_at": "", "source": ""}

    for line in lines:
        try:
            row = json.loads(line)
        except Exception:
            continue

        action = str(row.get("action") or "")
        seen_at = str(row.get("ts_utc") or "")
        if action == "runner_resume":
            try:
                latest_resume = {
                    "durable_open_count": int(row.get("open_count") or 0),
                    "last_seen_at": seen_at,
                    "source": "runner_resume",
                }
            except Exception:
                pass
            continue

        if action in {"open", "open_ticket", "open_sleeve"}:
            trade_actions_seen = True
            open_count += 1
            inventory = {
                "durable_open_count": open_count,
                "last_seen_at": seen_at,
                "source": "trade_action_inventory",
            }
            continue

        if action in {"close", "close_ticket", "close_sleeve"}:
            trade_actions_seen = True
            open_count = max(open_count - 1, 0)
            inventory = {
                "durable_open_count": open_count,
                "last_seen_at": seen_at,
                "source": "trade_action_inventory",
            }

    if trade_actions_seen:
        return inventory
    return latest_resume


def merge_durable_proof(*, prior_payload: dict[str, Any], symbol_snapshot: dict[str, Any], event_path: Path) -> dict[str, Any]:
    prior = prior_payload.get("durable_proof") if isinstance(prior_payload.get("durable_proof"), dict) else {}
    event_progress = parse_event_progress(event_path)
    trade_inventory = parse_trade_action_inventory(event_path)
    current = {
        "durable_realized_closes": int(symbol_snapshot.get("realized_closes", 0) or 0),
        "durable_realized_net_usd": float(symbol_snapshot.get("realized_net_usd", 0.0) or 0.0),
        "durable_open_count": len(symbol_snapshot.get("open_tickets") or []),
        "last_seen_at": utc_now_iso(),
        "source": "state_snapshot",
        "counter_regressed": False,
    }

    candidates: list[dict[str, Any]] = []
    for candidate in (prior, event_progress, current):
        if not isinstance(candidate, dict):
            continue
        try:
            closes = int(candidate.get("durable_realized_closes", 0) or 0)
            realized = float(candidate.get("durable_realized_net_usd", 0.0) or 0.0)
            open_count = int(candidate.get("durable_open_count", 0) or 0)
        except Exception:
            continue
        candidates.append(
            {
                "durable_realized_closes": closes,
                "durable_realized_net_usd": realized,
                "durable_open_count": open_count,
                "last_seen_at": str(candidate.get("last_seen_at") or ""),
                "source": str(candidate.get("source") or ""),
                "counter_regressed": False,
            }
        )

    if not candidates:
        return current

    candidates.sort(
        key=lambda item: (
            int(item["durable_realized_closes"]),
            str(item["last_seen_at"] or ""),
        )
    )
    best_close = dict(candidates[-1])

    inventory_candidates: list[dict[str, Any]] = []
    for candidate in (prior, trade_inventory, current):
        if not isinstance(candidate, dict):
            continue
        try:
            open_count = int(candidate.get("durable_open_count", 0) or 0)
        except Exception:
            continue
        inventory_candidates.append(
            {
                "durable_open_count": open_count,
                "last_seen_at": str(candidate.get("last_seen_at") or ""),
                "source": str(candidate.get("source") or ""),
            }
        )

    inventory_candidates.sort(key=lambda item: str(item["last_seen_at"] or ""))
    best_inventory = dict(inventory_candidates[-1]) if inventory_candidates else {
        "durable_open_count": int(current["durable_open_count"]),
        "last_seen_at": str(current["last_seen_at"]),
        "source": str(current["source"]),
    }

    best = {
        "durable_realized_closes": int(best_close["durable_realized_closes"]),
        "durable_realized_net_usd": float(best_close["durable_realized_net_usd"]),
        "durable_open_count": int(best_inventory["durable_open_count"]),
        "last_seen_at": str(best_close["last_seen_at"] or best_inventory["last_seen_at"] or ""),
        "source": str(best_close["source"] or ""),
        "inventory_source": str(best_inventory["source"] or ""),
        "inventory_last_seen_at": str(best_inventory["last_seen_at"] or ""),
        "counter_regressed": int(current["durable_realized_closes"]) < int(best_close["durable_realized_closes"]),
    }
    return best


def event_log_has_trade_actions(path: Path) -> bool:
    if not path.exists():
        return False
    try:
        lines = path.read_text(encoding="utf-8", errors="ignore").splitlines()
    except Exception:
        return False
    for line in lines:
        try:
            row = json.loads(line)
        except Exception:
            continue
        if str(row.get("action") or "") in {"open", "close", "open_ticket", "close_ticket"}:
            return True
    return False


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Persistent GBPUSD tick-native forward shadow runner")
    parser.add_argument("--state-path", type=Path, default=DEFAULT_STATE_PATH)
    parser.add_argument("--event-path", type=Path, default=DEFAULT_EVENT_PATH)
    parser.add_argument("--report-md-path", type=Path, default=DEFAULT_REPORT_MD_PATH)
    parser.add_argument("--poll-seconds", type=float, default=DEFAULT_POLL_SECONDS)
    parser.add_argument("--once", action="store_true", help="Run one cycle and exit")
    parser.add_argument("--no-offensive-escape", action="store_true",
                        help="Disable tier0 offensive escape (closure-policy diagnosis)")
    return parser.parse_args()


def build_engine(no_offensive_escape: bool = False) -> TickStatefulRearmEngine:
    return engine_from_args(
        symbol=SYMBOL,
        timeframe_name="M1",
        step=max(STEP_SELL, STEP_BUY),
        max_open_per_side=MAX_OPEN_PER_SIDE,
        variant_name="rearm_lvl2_exc2",
        close_alpha=CLOSE_ALPHA,
        momentum_gate=False,
        cooldown_bars=0,
        sell_gap=SELL_GAP,
        buy_gap=BUY_GAP,
        step_sell=STEP_SELL,
        step_buy=STEP_BUY,
        volume=VOLUME,
        offensive_closure_enabled=not no_offensive_escape,
        offensive_safety_margin_usd=2.0,
    )


def build_symbol_snapshot(engine: TickStatefulRearmEngine) -> dict[str, Any]:
    last_tick = getattr(engine.state, "last_tick", None) or {}
    bid = float(last_tick.get("bid", 0.0) or 0.0)
    ask = float(last_tick.get("ask", 0.0) or 0.0)
    if bid <= 0.0 or ask <= 0.0:
        live_tick = mt5.symbol_info_tick(SYMBOL)
        if live_tick:
            bid = float(getattr(live_tick, "bid", 0.0) or 0.0)
            ask = float(getattr(live_tick, "ask", 0.0) or 0.0)
    return {
        "symbol": SYMBOL,
        "mode": "tick_forward_shadow",
        "anchor": float(engine.state.anchor or 0.0),
        "next_sell_level": float(engine.state.next_sell_level or 0.0),
        "next_buy_level": float(engine.state.next_buy_level or 0.0),
        "open_tickets": list(engine.state.open_tickets or []),
        "rearm_tokens": list(engine.state.rearm_tokens or []),
        "rearm_opens": int(engine.state.rearm_opens or 0),
        "realized_net_usd": float(engine.state.realized_net_usd or 0.0),
        "realized_closes": int(engine.state.realized_closes or 0),
        "anchor_resets": int(engine.state.anchor_resets or 0),
        "max_open_total": int(engine.state.max_open_total or 0),
        "lattice_started_time": int(engine.state.lattice_started_time or 0),
        "last_tick_time": int(engine.state.last_tick_time or 0),
        "last_tick_msc": int(engine.state.last_tick_msc or 0),
        "last_bar_time": int(engine.state.last_bar_time or 0),
        "last_bid": bid,
        "last_ask": ask,
    }


def load_engine_from_state(payload: dict[str, Any] | None, no_offensive_escape: bool = False) -> tuple[TickStatefulRearmEngine, dict[str, Any], bool]:
    engine = build_engine(no_offensive_escape=no_offensive_escape)
    payload = payload or {}
    saved_snapshot = None
    if isinstance(payload.get("symbols"), dict):
        saved_snapshot = payload.get("symbols", {}).get(SYMBOL)
    if not isinstance(saved_snapshot, dict):
        saved_snapshot = payload.get("state") if isinstance(payload.get("state"), dict) else None
    if not isinstance(saved_snapshot, dict):
        saved_snapshot = payload.get("engine_state") if isinstance(payload.get("engine_state"), dict) else None
    if isinstance(saved_snapshot, dict):
        engine.load_snapshot(saved_snapshot)
        return engine, payload, True

    bars = load_recent_bars(SYMBOL, "M1", count=120)
    if not bars:
        raise RuntimeError("No bars available for GBPUSD anchor initialization")
    engine.state.last_bar_time = int(bars[-1]["time"])
    engine.state.open_tickets = []
    engine.state.rearm_tokens = []
    engine.state.rearm_opens = 0
    engine.state.realized_closes = 0
    engine.state.realized_net_usd = 0.0
    engine.state.anchor_resets = 0
    engine.state.max_open_total = 0
    engine.state.last_tick_time = 0
    engine.state.last_tick_msc = 0
    engine.prime(float(bars[-1]["close"]), int(bars[-1]["time"]))
    return engine, payload, False


def floating_pnl_usd(symbol_snapshot: dict[str, Any]) -> float:
    bid = float(symbol_snapshot.get("last_bid", 0.0) or 0.0)
    ask = float(symbol_snapshot.get("last_ask", 0.0) or 0.0)
    total = 0.0
    for ticket in symbol_snapshot.get("open_tickets") or []:
        direction = str(ticket.get("direction", "") or "").upper()
        fill_price = float(ticket.get("fill_price", ticket.get("entry_fill_price", ticket.get("trigger_level", 0.0))) or 0.0)
        if direction == "BUY" and bid > 0.0:
            total += tick_pnl_usd(SYMBOL, direction, fill_price, bid, volume=VOLUME)
        elif direction == "SELL" and ask > 0.0:
            total += tick_pnl_usd(SYMBOL, direction, fill_price, ask, volume=VOLUME)
    return float(total)


def build_payload(
    *,
    prior_payload: dict[str, Any],
    engine: TickStatefulRearmEngine,
    runner_status: dict[str, Any],
    total_ticks: int,
    cycles: int,
    errors: list[dict[str, Any]],
    initialized_from_state: bool,
    event_path: Path,
) -> dict[str, Any]:
    snapshot = build_symbol_snapshot(engine)
    durable_proof = merge_durable_proof(
        prior_payload=prior_payload,
        symbol_snapshot=snapshot,
        event_path=event_path,
    )
    no_offensive_escape = bool(prior_payload.get("metadata", {}).get("no_offensive_escape", False)) if isinstance(prior_payload.get("metadata"), dict) else False
    return {
        "started_at": str(prior_payload.get("started_at") or runner_status["started_at"]),
        "updated_at": utc_now_iso(),
        "symbol": SYMBOL,
        "cycles": int(cycles),
        "total_ticks": int(total_ticks),
        "last_tick_time": int(snapshot["last_tick_time"]),
        "last_tick_msc": int(snapshot["last_tick_msc"]),
        "errors": list(errors[-20:]),
        "metadata": {
            "symbol": SYMBOL,
            "timeframe": "M1",
            "step_sell": STEP_SELL,
            "step_buy": STEP_BUY,
            "sell_gap": SELL_GAP,
            "buy_gap": BUY_GAP,
            "close_alpha": CLOSE_ALPHA,
            "max_open_per_side": MAX_OPEN_PER_SIDE,
            "volume": VOLUME,
            "poll_seconds": float(runner_status["poll_seconds"]),
            "initialized_from_state": bool(initialized_from_state),
            "no_offensive_escape": no_offensive_escape,
            "offensive_closure_enabled": not no_offensive_escape,
        },
        "runner": dict(runner_status),
        "durable_proof": durable_proof,
        "symbols": {SYMBOL: snapshot},
        "state": dict(snapshot),
        "engine_state": dict(snapshot),
    }


def write_report(path: Path, payload: dict[str, Any]) -> None:
    runner = payload.get("runner") if isinstance(payload.get("runner"), dict) else {}
    metadata = payload.get("metadata") if isinstance(payload.get("metadata"), dict) else {}
    symbol = (payload.get("symbols") if isinstance(payload.get("symbols"), dict) else {}).get(SYMBOL, {})
    realized = float(symbol.get("realized_net_usd", 0.0) or 0.0)
    closes = int(symbol.get("realized_closes", 0) or 0)
    open_count = len(symbol.get("open_tickets") or [])
    bid = float(symbol.get("last_bid", 0.0) or 0.0)
    ask = float(symbol.get("last_ask", 0.0) or 0.0)
    floating = floating_pnl_usd(symbol)
    marked_net = realized + floating
    durable = payload.get("durable_proof") if isinstance(payload.get("durable_proof"), dict) else {}
    durable_closes = int(durable.get("durable_realized_closes", closes) or closes)
    durable_realized = float(durable.get("durable_realized_net_usd", realized) or realized)
    durable_open_count = int(durable.get("durable_open_count", open_count) or open_count)
    durable_source = str(durable.get("source") or "")
    durable_last_seen_at = str(durable.get("last_seen_at") or "")
    counter_regressed = bool(durable.get("counter_regressed"))
    started_at = str(payload.get("started_at") or "")
    started_dt = None
    try:
        started_dt = datetime.fromisoformat(started_at.replace("Z", "+00:00")) if started_at else None
    except Exception:
        started_dt = None
    runtime_hours = (
        max((datetime.now(timezone.utc) - started_dt).total_seconds() / 3600.0, 0.0)
        if started_dt is not None
        else 0.0
    )
    poll_seconds = float(runner.get("poll_seconds", DEFAULT_POLL_SECONDS) or DEFAULT_POLL_SECONDS)
    no_offensive_escape = bool(metadata.get("no_offensive_escape", False))
    lane_label = "No-Escape Closure-Diagnosis" if no_offensive_escape else "Baseline"

    lines = [
        f"# GBPUSD Tick-Native Forward-Shadow Validation ({lane_label})",
        "",
        f"- Configuration: sell_step={STEP_SELL} / buy_step={STEP_BUY}, sell_gap={SELL_GAP} / buy_gap={BUY_GAP}",
        f"- Close alpha: {CLOSE_ALPHA}, Max open per side: {MAX_OPEN_PER_SIDE}",
        f"- Tier 0 offensive closure enabled: `{str(not no_offensive_escape).lower()}`",
        f"- Started: `{started_at or 'unknown'}`",
        f"- Status: **RUNNING** (polling every {poll_seconds:.0f}s)",
        f"- Runtime: {runtime_hours:.2f}h",
        "",
        "## Current State",
        "",
        "| Metric | Value |",
        "| --- | --- |",
        f"| Total Ticks | {int(payload.get('total_ticks', 0)):,} |",
        f"| Poll Cycles | {int(payload.get('cycles', 0))} |",
        f"| Current Bid/Ask | {bid:.5f} / {ask:.5f} |" if bid > 0.0 and ask > 0.0 else "| Current Bid/Ask | N/A |",
        f"| Realized Net (USD) | ${realized:+.2f} |",
        f"| Realized Closes | {closes} |",
        f"| Open Positions | {open_count} |",
        f"| Floating (USD) | ${floating:+.2f} |",
        f"| Marked Net (USD) | ${marked_net:+.2f} |",
        f"| Avg PnL/Close (USD) | ${realized / closes:+.2f} |" if closes > 0 else "| Avg PnL/Close (USD) | N/A (no closes yet) |",
    ]
    if durable_closes > closes or abs(durable_realized - realized) > 1e-9:
        lines.extend(
            [
                f"| Durable Proof Closes | {durable_closes} |",
                f"| Durable Proof Net (USD) | ${durable_realized:+.2f} |",
                f"| Durable Proof Open Count | {durable_open_count} |",
                f"| Durable Proof Seen At | {durable_last_seen_at or '-'} |",
                f"| Durable Proof Source | {durable_source or '-'} |",
            ]
        )
    lines.extend(
        [
            "",
            "## Interpretation",
            "",
        ]
    )
    if counter_regressed and durable_closes > 0:
        lines.append(
            f"- Durable proof already exists: {durable_closes} closes and ${durable_realized:+.2f} realized were captured in the event-backed ledger."
        )
        lines.append(
            "- The current snapshot is behind that proof after a restart or interrupted save, so use durable proof for graduation and current inventory for posture."
        )
    elif closes == 0:
        lines.append("- No closes yet. The forward shadow still needs live tick-native evidence before any promotion claim.")
        lines.append("- Open inventory alone is not proof. Judge this lane on realized closes and marked-net behavior over time.")
    elif closes < 10:
        lines.append(f"- Early signal: {closes} closes, ${realized:+.2f} net. Still too few closes for a durable verdict.")
    else:
        lines.append(f"- Forward signal: {closes} closes, ${realized:+.2f} realized, ${marked_net:+.2f} marked.")
        if realized > 0:
            lines.append("- The sample is large enough to judge economics now. Keep shadowing only to see whether the positive net survives more live time.")
        else:
            lines.append("- The sample is already large and net-negative. Do not call this proof-positive; use it as closure-diagnosis evidence or a kill/demotion candidate.")

    lines.extend(
        [
            "",
            "## Read",
            "",
            "- This is tick-native forward validation on live MT5 ticks, not bar replay.",
            "- The lane now runs as a persistent supervised runner with heartbeat state and trade-event logging.",
            "- The registered no-escape lane is the closure-diagnosis control: it should disable Tier 0 entirely rather than faking it with impossible affordability settings.",
            "- Compare against the bar-replay 60d result ($6986 modeled-live, 35.7% retention) to judge realism gap honestly.",
        ]
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def run_cycle(
    *,
    engine: TickStatefulRearmEngine,
    payload: dict[str, Any],
    runner_status: dict[str, Any],
    event_path: Path,
) -> tuple[int, int]:
    total_ticks = int(payload.get("total_ticks", 0) or 0)
    cycles = int(payload.get("cycles", 0) or 0) + 1
    last_tick_msc = int(payload.get("last_tick_msc", 0) or 0)

    ticks = load_ticks_since(SYMBOL, last_tick_msc)
    processed = 0
    if ticks:
        processed = int(engine.process_ticks(ticks, action_sink=None, event_path=event_path, emit=True) or 0)
        total_ticks += processed
        last_tick_msc = int(ticks[-1].get("time_msc", last_tick_msc))
        append_event(
            event_path,
            {
                "action": "tick_batch",
                "tick_count": len(ticks),
                "processed": processed,
                "open_count": len(engine.state.open_tickets or []),
                "realized_net_usd": float(engine.state.realized_net_usd or 0.0),
                "realized_closes": int(engine.state.realized_closes or 0),
            },
        )

    runner_status["heartbeat_at"] = utc_now_iso()
    runner_status["last_successful_run_at"] = str(runner_status["heartbeat_at"])
    runner_status["consecutive_exceptions"] = 0
    runner_status["last_exception_at"] = ""
    runner_status["last_exception_type"] = ""
    runner_status["last_exception_message"] = ""
    runner_status["cycle"] = int(cycles)

    next_payload = {
        **payload,
        "total_ticks": int(total_ticks),
        "cycles": int(cycles),
        "last_tick_msc": int(last_tick_msc),
    }
    return total_ticks, cycles


def main() -> int:
    args = parse_args()
    mt5_ready, mt5_connection = mt5_terminal_guard.initialize_mt5(mt5_module=mt5)
    if not mt5_ready:
        print(mt5_terminal_guard.failure_summary(mt5_connection))
        return 1

    state_path = Path(args.state_path)
    event_path = Path(args.event_path)
    report_md_path = Path(args.report_md_path)
    prior_payload = load_json(state_path) or {}
    errors = list(prior_payload.get("errors") or [])
    runner = prior_payload.get("runner") if isinstance(prior_payload.get("runner"), dict) else {}
    runner_status = {
        "pid": os.getpid(),
        "script": Path(__file__).name,
        "started_at": str(runner.get("started_at") or utc_now_iso()),
        "poll_seconds": max(float(args.poll_seconds or DEFAULT_POLL_SECONDS), 1.0),
        "heartbeat_at": str(runner.get("heartbeat_at") or ""),
        "last_successful_run_at": str(runner.get("last_successful_run_at") or ""),
        "consecutive_exceptions": int(runner.get("consecutive_exceptions", 0) or 0),
        "last_exception_at": str(runner.get("last_exception_at") or ""),
        "last_exception_type": str(runner.get("last_exception_type") or ""),
        "last_exception_message": str(runner.get("last_exception_message") or ""),
        "cycle": int(runner.get("cycle", prior_payload.get("cycles", 0)) or 0),
    }
    prior_metadata = prior_payload.get("metadata") if isinstance(prior_payload.get("metadata"), dict) else {}
    no_offensive_escape = bool(getattr(args, "no_offensive_escape", False))
    if prior_metadata.get("no_offensive_escape") not in {None, no_offensive_escape}:
        append_event(
            event_path,
            {
                "action": "runner_mode_change",
                "prior_no_offensive_escape": bool(prior_metadata.get("no_offensive_escape")),
                "new_no_offensive_escape": no_offensive_escape,
            },
        )

    try:
        engine, loaded_payload, initialized_from_state = load_engine_from_state(
            prior_payload, no_offensive_escape=no_offensive_escape)
        if not initialized_from_state:
            append_event(
                event_path,
                {
                    "action": "runner_start",
                    "mode": "fresh_anchor_prime",
                    "anchor": float(engine.state.anchor or 0.0),
                },
            )
        else:
            if not event_log_has_trade_actions(event_path):
                for ticket in engine.state.open_tickets or []:
                    append_event(
                        event_path,
                        {
                            "action": "open_ticket",
                            "direction": str(ticket.get("direction", "") or "").upper(),
                            "trigger_level": round(float(ticket.get("trigger_level", 0.0) or 0.0), 6),
                            "fill_price": round(float(ticket.get("fill_price", 0.0) or 0.0), 6),
                            "level_idx": int(ticket.get("level_idx", 0) or 0),
                            "backfill_resume": True,
                        },
                    )
            append_event(
                event_path,
                {
                    "action": "runner_resume",
                    "open_count": len(engine.state.open_tickets or []),
                    "realized_closes": int(engine.state.realized_closes or 0),
                },
            )

        while True:
            payload_for_cycle = load_json(state_path) or loaded_payload or prior_payload or {}
            try:
                total_ticks, cycles = run_cycle(
                    engine=engine,
                    payload=payload_for_cycle,
                    runner_status=runner_status,
                    event_path=event_path,
                )
                next_payload = build_payload(
                    prior_payload={**payload_for_cycle, "metadata": {**(payload_for_cycle.get("metadata") if isinstance(payload_for_cycle.get("metadata"), dict) else {}), "no_offensive_escape": no_offensive_escape}},
                    engine=engine,
                    runner_status=runner_status,
                    total_ticks=total_ticks,
                    cycles=cycles,
                    errors=errors,
                    initialized_from_state=initialized_from_state,
                    event_path=event_path,
                )
                atomic_write_json(state_path, next_payload)
                write_report(report_md_path, next_payload)
                print(
                    f"[{cycles}] ticks={total_ticks:,} realized=${engine.state.realized_net_usd:+.2f} "
                    f"closes={engine.state.realized_closes} open={len(engine.state.open_tickets or [])}"
                )
            except Exception as exc:
                runner_status["heartbeat_at"] = utc_now_iso()
                runner_status["consecutive_exceptions"] = int(runner_status.get("consecutive_exceptions", 0) or 0) + 1
                runner_status["last_exception_at"] = str(runner_status["heartbeat_at"])
                runner_status["last_exception_type"] = type(exc).__name__
                runner_status["last_exception_message"] = str(exc)
                errors.append(
                    {
                        "time": str(runner_status["heartbeat_at"]),
                        "type": type(exc).__name__,
                        "message": str(exc),
                    }
                )
                failure_payload = build_payload(
                    prior_payload={**payload_for_cycle, "metadata": {**(payload_for_cycle.get("metadata") if isinstance(payload_for_cycle.get("metadata"), dict) else {}), "no_offensive_escape": no_offensive_escape}},
                    engine=engine,
                    runner_status=runner_status,
                    total_ticks=int(payload_for_cycle.get("total_ticks", 0) or 0),
                    cycles=int(payload_for_cycle.get("cycles", 0) or 0),
                    errors=errors,
                    initialized_from_state=initialized_from_state,
                    event_path=event_path,
                )
                atomic_write_json(state_path, failure_payload)
                write_report(report_md_path, failure_payload)
                append_event(
                    event_path,
                    {
                        "action": "runner_error",
                        "error_type": type(exc).__name__,
                        "message": str(exc),
                    },
                )
                print(f"ERROR: {exc}")
                traceback.print_exc()

            if args.once:
                return 0
            time.sleep(max(float(args.poll_seconds or DEFAULT_POLL_SECONDS), 1.0))
    except KeyboardInterrupt:
        print("Stopped by user.")
        return 0
    finally:
        mt5.shutdown()


if __name__ == "__main__":
    raise SystemExit(main())
