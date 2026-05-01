#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent.parent
REPORTS = ROOT / "reports"
DEFAULT_BOARD_PATH = REPORTS / "kraken_spot_money_velocity_board.json"
DEFAULT_STATE_PATH = REPORTS / "kraken_spot_velocity_shadow_state.json"
DEFAULT_EVENTS_PATH = REPORTS / "kraken_spot_velocity_shadow_events.jsonl"


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def to_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def load_json(path: Path) -> Any:
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    tmp.replace(path)


def append_event(path: Path, event: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(event, sort_keys=True) + "\n")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Shadow-only Kraken spot velocity runner.")
    parser.add_argument("--board-path", default=str(DEFAULT_BOARD_PATH))
    parser.add_argument("--state-path", default=str(DEFAULT_STATE_PATH))
    parser.add_argument("--event-path", default=str(DEFAULT_EVENTS_PATH))
    parser.add_argument("--starting-cash", type=float, default=100.0)
    parser.add_argument("--deploy-pct", type=float, default=0.8)
    parser.add_argument("--poll-seconds", type=float, default=10.0)
    parser.add_argument("--refresh-board", action="store_true")
    parser.add_argument("--min-kraken-edge-bps", type=float, default=50.0)
    parser.add_argument("--max-spread-bps", type=float, default=100.0)
    parser.add_argument("--allowed-signal-states", default="live_hot")
    parser.add_argument("--allowed-best-windows", default="last,30s,60s,5m")
    parser.add_argument("--required-verdicts", default="clears_both_fee_models,kraken_fee_flip_candidate")
    parser.add_argument("--max-entry-chase-bps", type=float, default=450.0)
    parser.add_argument("--taker-fee-bps", type=float, default=40.0)
    parser.add_argument("--profit-lock-retention-pct", type=float, default=70.0)
    parser.add_argument("--min-profit-to-trail-usd", type=float, default=0.005)
    parser.add_argument("--max-loss-pct", type=float, default=1.25)
    parser.add_argument("--manifest-positive-within-seconds", type=float, default=300.0)
    parser.add_argument("--manifest-positive-min-net-pct", type=float, default=0.0)
    parser.add_argument("--cooldown-after-loss-seconds", type=float, default=1800.0)
    parser.add_argument("--max-realized-drawdown-pct", type=float, default=5.0)
    parser.add_argument("--fresh-start", action="store_true")
    parser.add_argument("--loop", action="store_true")
    return parser.parse_args()


def default_state(args: argparse.Namespace) -> dict[str, Any]:
    return {
        "mode": "kraken_spot_velocity_shadow",
        "shadow_only": True,
        "started_at": utc_now_iso(),
        "starting_cash_usd": float(args.starting_cash),
        "cash_usd": float(args.starting_cash),
        "deploy_pct": float(args.deploy_pct),
        "taker_fee_bps": float(args.taker_fee_bps),
        "position": None,
        "realized_net_usd": 0.0,
        "realized_closes": 0,
        "total_fees": 0.0,
        "loss_cooldowns": {},
        "last_action": "initialized",
    }


def refresh_board() -> None:
    subprocess.run(
        [sys.executable, "scripts/build_kraken_spot_money_velocity_board.py", "--starting-cash", "100", "--deploy-pct", "0.8"],
        cwd=ROOT,
        check=False,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def parse_set(value: Any) -> set[str]:
    return {str(item).strip() for item in str(value or "").split(",") if str(item).strip()}


def cooldown_active(until_iso: Any) -> bool:
    if not until_iso:
        return False
    try:
        until = datetime.fromisoformat(str(until_iso))
    except ValueError:
        return False
    return datetime.now(timezone.utc) < until


def candidate_rows(board: dict[str, Any], args: argparse.Namespace, state: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    allowed_signal_states = parse_set(getattr(args, "allowed_signal_states", ""))
    allowed_best_windows = parse_set(getattr(args, "allowed_best_windows", ""))
    required_verdicts = parse_set(getattr(args, "required_verdicts", ""))
    cooldowns = (state or {}).get("loss_cooldowns") if isinstance(state, dict) else {}
    if not isinstance(cooldowns, dict):
        cooldowns = {}
    rows = []
    for row in board.get("rows") or []:
        product_id = str(row.get("product_id") or "")
        if cooldown_active(cooldowns.get(product_id)):
            continue
        if not row.get("can_trade_starting_cash"):
            continue
        if to_float(row.get("spread_bps")) > float(args.max_spread_bps):
            continue
        if to_float(row.get("kraken_edge_bps")) < float(args.min_kraken_edge_bps):
            continue
        if allowed_signal_states and str(row.get("signal_state") or "") not in allowed_signal_states:
            continue
        if allowed_best_windows and str(row.get("best_move_window") or "") not in allowed_best_windows:
            continue
        if required_verdicts and str(row.get("verdict") or "") not in required_verdicts:
            continue
        if to_float(row.get("best_move_bps")) > float(args.max_entry_chase_bps):
            continue
        if int(to_float(row.get("samples"))) < 2:
            continue
        rows.append(row)
    rows.sort(key=lambda row: (to_float(row.get("kraken_edge_bps")), to_float(row.get("best_move_bps"))), reverse=True)
    return rows


def mark_position(position: dict[str, Any], row: dict[str, Any], args: argparse.Namespace) -> dict[str, Any]:
    bid = to_float(row.get("bid"))
    if bid <= 0:
        bid = to_float(position.get("highest_bid"))
    gross_exit_value = bid * to_float(position.get("quantity"))
    exit_fee = gross_exit_value * float(args.taker_fee_bps) / 10000.0
    net_exit_value = gross_exit_value - exit_fee
    net_pnl = net_exit_value - to_float(position.get("cost_usd"))
    net_pct = (net_pnl / to_float(position.get("cost_usd"))) * 100.0 if to_float(position.get("cost_usd")) else 0.0
    highest_bid = max(to_float(position.get("highest_bid")), bid)
    max_net_pnl = max(to_float(position.get("max_net_pnl"), net_pnl), net_pnl)
    max_net_pct = max(to_float(position.get("max_net_pct_on_cost"), net_pct), net_pct)
    return {
        **position,
        "current_bid": bid,
        "highest_bid": highest_bid,
        "net_exit_value": net_exit_value,
        "exit_fee": exit_fee,
        "net_pnl": net_pnl,
        "net_pct_on_cost": net_pct,
        "max_net_pnl": max_net_pnl,
        "max_net_pct_on_cost": max_net_pct,
    }


def should_exit(position: dict[str, Any], args: argparse.Namespace) -> tuple[bool, str]:
    net_pnl = to_float(position.get("net_pnl"))
    net_pct = to_float(position.get("net_pct_on_cost"))
    max_net_pnl = to_float(position.get("max_net_pnl"))
    opened_at = datetime.fromisoformat(str(position.get("opened_at")))
    age_seconds = (datetime.now(timezone.utc) - opened_at).total_seconds()
    if net_pct <= -abs(float(args.max_loss_pct)):
        return True, "max_loss"
    if (
        age_seconds >= float(args.manifest_positive_within_seconds)
        and to_float(position.get("max_net_pct_on_cost")) < float(args.manifest_positive_min_net_pct)
    ):
        return True, "manifest_positive_timeout"
    if max_net_pnl >= float(args.min_profit_to_trail_usd):
        retained = max_net_pnl * float(args.profit_lock_retention_pct) / 100.0
        if net_pnl < retained:
            return True, "profit_lock_trail"
    return False, "hold"


def open_position(row: dict[str, Any], state: dict[str, Any], args: argparse.Namespace) -> dict[str, Any]:
    cash = to_float(state.get("cash_usd"))
    deploy_usd = min(cash, to_float(state.get("starting_cash_usd")) * float(args.deploy_pct))
    ask = to_float(row.get("ask"))
    if deploy_usd <= 0 or ask <= 0:
        state["last_action"] = f"open_blocked_missing_ask_{row.get('product_id')}"
        return state
    entry_fee = deploy_usd * float(args.taker_fee_bps) / 10000.0
    spend_after_fee = deploy_usd - entry_fee
    quantity = spend_after_fee / ask
    state["cash_usd"] = cash - deploy_usd
    state["position"] = {
        "product_id": row.get("product_id"),
        "opened_at": utc_now_iso(),
        "entry_price": ask,
        "quantity": quantity,
        "gross_deploy_usd": deploy_usd,
        "entry_fee": entry_fee,
        "cost_usd": deploy_usd,
        "highest_bid": to_float(row.get("bid")),
        "max_net_pnl": -entry_fee,
        "max_net_pct_on_cost": -float(args.taker_fee_bps) / 100.0,
        "entry_verdict": row.get("verdict"),
        "entry_kraken_edge_bps": row.get("kraken_edge_bps"),
        "entry_best_move_bps": row.get("best_move_bps"),
    }
    state["total_fees"] = to_float(state.get("total_fees")) + entry_fee
    state["last_action"] = f"shadow_open_{row.get('product_id')}"
    return state


def close_position(state: dict[str, Any], reason: str, event_path: Path) -> dict[str, Any]:
    position = state.get("position") or {}
    exit_fee = to_float(position.get("exit_fee"))
    net_exit_value = to_float(position.get("net_exit_value"))
    net_pnl = to_float(position.get("net_pnl"))
    state["cash_usd"] = to_float(state.get("cash_usd")) + net_exit_value
    state["realized_net_usd"] = to_float(state.get("realized_net_usd")) + net_pnl
    state["realized_closes"] = int(to_float(state.get("realized_closes"))) + 1
    state["total_fees"] = to_float(state.get("total_fees")) + exit_fee
    state["last_action"] = f"shadow_close_{reason}_{position.get('product_id')}"
    if net_pnl < 0:
        cooldowns = state.get("loss_cooldowns")
        if not isinstance(cooldowns, dict):
            cooldowns = {}
        cooldown_until = datetime.fromtimestamp(
            time.time() + to_float(state.get("cooldown_after_loss_seconds"), 1800.0),
            tz=timezone.utc,
        ).isoformat()
        cooldowns[str(position.get("product_id") or "")] = cooldown_until
        state["loss_cooldowns"] = cooldowns
    append_event(
        event_path,
        {
            "event": "shadow_close",
            "at": utc_now_iso(),
            "reason": reason,
            "product_id": position.get("product_id"),
            "net_pnl": net_pnl,
            "net_pct_on_cost": to_float(position.get("net_pct_on_cost")),
            "cash_usd": state["cash_usd"],
        },
    )
    state["position"] = None
    return state


def run_once(args: argparse.Namespace) -> dict[str, Any]:
    state_path = Path(str(args.state_path))
    event_path = Path(str(args.event_path))
    if args.fresh_start or not state_path.exists():
        state = default_state(args)
    else:
        loaded = load_json(state_path)
        state = loaded.get("state") if isinstance(loaded, dict) and isinstance(loaded.get("state"), dict) else loaded
    state["cooldown_after_loss_seconds"] = float(args.cooldown_after_loss_seconds)
    if args.refresh_board:
        refresh_board()
    board = load_json(Path(str(args.board_path)))
    rows = candidate_rows(board, args, state)
    row_by_product = {row.get("product_id"): row for row in board.get("rows") or []}
    if state.get("position"):
        position = state["position"]
        row = row_by_product.get(position.get("product_id"))
        if row:
            state["position"] = mark_position(position, row, args)
            exit_now, reason = should_exit(state["position"], args)
            if exit_now:
                state = close_position(state, reason, event_path)
            else:
                state["last_action"] = "shadow_hold"
        else:
            state["last_action"] = "shadow_hold_no_mark"
    elif to_float(state.get("realized_net_usd")) <= -(
        to_float(state.get("starting_cash_usd")) * abs(float(args.max_realized_drawdown_pct)) / 100.0
    ):
        state["last_action"] = "circuit_breaker_realized_drawdown"
    elif rows:
        max_drawdown_usd = to_float(state.get("starting_cash_usd")) * abs(float(args.max_realized_drawdown_pct)) / 100.0
        if to_float(state.get("realized_net_usd")) <= -max_drawdown_usd:
            state["last_action"] = "circuit_breaker_realized_drawdown"
        else:
            state = open_position(rows[0], state, args)
            append_event(event_path, {"event": "shadow_open", "at": utc_now_iso(), "product_id": rows[0].get("product_id"), "row": rows[0]})
    else:
        state["last_action"] = "idle_no_eligible_kraken_velocity_candidate"
    snapshot = {
        "runner": {
            "script": "live_kraken_spot_velocity_shadow.py",
            "pid": os.getpid(),
            "heartbeat_at": utc_now_iso(),
            "shadow_only": True,
            "poll_seconds": float(args.poll_seconds),
            "board_path": str(args.board_path),
            "event_path": str(args.event_path),
            "min_kraken_edge_bps": float(args.min_kraken_edge_bps),
            "max_realized_drawdown_pct": float(args.max_realized_drawdown_pct),
        },
        "state": state,
        "top_candidates": rows[:8],
        "updated_at": utc_now_iso(),
    }
    write_json(state_path, snapshot)
    return snapshot


def main() -> None:
    args = parse_args()
    args.fresh_start = bool(args.fresh_start)
    while True:
        snapshot = run_once(args)
        if not args.loop:
            print(json.dumps({"state_path": str(Path(args.state_path).resolve()), "last_action": snapshot["state"].get("last_action")}, indent=2))
            return
        args.fresh_start = False
        time.sleep(max(1.0, float(args.poll_seconds)))


if __name__ == "__main__":
    main()
