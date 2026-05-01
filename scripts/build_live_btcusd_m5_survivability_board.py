#!/usr/bin/env python3
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import MetaTrader5 as mt5
import mt5_terminal_guard

from tick_penetration_lattice_core import engine_from_args, tick_pnl_usd


ROOT = Path(__file__).resolve().parent.parent
REPORTS = ROOT / "reports"
CONFIGS = ROOT / "configs"
STATE_PATH = REPORTS / "penetration_lattice_live_btcusd_m5_warp_state.json"
JSON_PATH = REPORTS / "live_btcusd_m5_survivability_board.json"
MD_PATH = REPORTS / "live_btcusd_m5_survivability_board.md"
RUNNER_REGISTRY_PATH = CONFIGS / "penetration_lattice_runner_registry.json"
LANE_NAME = "live_btcusd_m5_warp_probation_941780"
SYMBOL = "BTCUSD"
STRESS_MOVES = [250.0, 500.0, 1000.0, 1500.0]


@dataclass(frozen=True)
class Scenario:
    label: str
    move_points: float


def load_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def registry_lane(path: Path = RUNNER_REGISTRY_PATH) -> dict[str, Any]:
    if not path.exists():
        return {}
    payload = load_json(path)
    for row in payload.get("lanes") or []:
        if isinstance(row, dict) and str(row.get("name") or "") == LANE_NAME:
            return row
    return {}


def build_engine_from_state(metadata: dict[str, Any], symbol_state: dict[str, Any]):
    engine = engine_from_args(
        symbol=SYMBOL,
        timeframe_name=str(metadata.get("timeframe") or "M5"),
        step=float(metadata.get("step") or 100.0),
        max_open_per_side=int(metadata.get("max_open_per_side") or 12),
        variant_name=str(metadata.get("raw_rearm_variant") or "rearm_lvl2_exc1"),
        close_alpha=float(metadata.get("raw_close_alpha") or 1.0),
        momentum_gate=bool(metadata.get("raw_rearm_momentum_gate")),
        cooldown_bars=int(metadata.get("raw_rearm_cooldown_bars") or 0),
        sell_gap=int(metadata.get("raw_sell_gap") or 1),
        buy_gap=int(metadata.get("raw_buy_gap") or 1),
    )
    engine.load_snapshot(symbol_state)
    return engine


def mark_engine(engine, bid_px: float, ask_px: float) -> dict[str, Any]:
    floating = 0.0
    tickets = list(engine.state.open_tickets or [])
    buy_count = 0
    sell_count = 0
    for ticket in tickets:
        direction = str(ticket.get("direction", "")).upper()
        fill_price = float(ticket.get("fill_price", ticket.get("entry_fill_price", ticket.get("trigger_level", 0.0))) or 0.0)
        if direction == "BUY":
            buy_count += 1
            floating += tick_pnl_usd(SYMBOL, direction, fill_price, bid_px)
        elif direction == "SELL":
            sell_count += 1
            floating += tick_pnl_usd(SYMBOL, direction, fill_price, ask_px)
    realized = float(engine.state.realized_net_usd or 0.0)
    return {
        "realized_usd": round(realized, 2),
        "floating_usd": round(floating, 2),
        "net_usd": round(realized + floating, 2),
        "open_count": len(tickets),
        "buy_count": buy_count,
        "sell_count": sell_count,
        "max_open_total": int(engine.state.max_open_total or 0),
        "next_buy_level": round(float(engine.state.next_buy_level or 0.0), 2),
        "next_sell_level": round(float(engine.state.next_sell_level or 0.0), 2),
    }


def run_directional_stress(
    *,
    metadata: dict[str, Any],
    symbol_state: dict[str, Any],
    bid: float,
    ask: float,
    base_time: int,
    base_msc: int,
    move_points: float,
    direction: str,
) -> dict[str, Any]:
    engine = build_engine_from_state(metadata, symbol_state)
    current_bid = float(bid)
    current_ask = float(ask)
    current_time = int(base_time)
    current_msc = int(base_msc)
    delta = 5.0 if direction == "up" else -5.0
    target_bid = bid + move_points if direction == "up" else bid - move_points
    while (delta > 0 and current_bid < target_bid) or (delta < 0 and current_bid > target_bid):
        current_bid += delta
        current_ask += delta
        current_time += 1
        current_msc += 1000
        engine.process_tick(
            {"bid": current_bid, "ask": current_ask, "time": current_time, "time_msc": current_msc},
            action_sink=None,
            event_path=None,
            emit=False,
        )
    marked = mark_engine(engine, current_bid, current_ask)
    return {
        "scenario": f"{direction}_{int(move_points)}",
        "direction": direction,
        "move_points": int(move_points),
        "end_bid": round(current_bid, 2),
        "end_ask": round(current_ask, 2),
        **marked,
    }


def pct_of_equity(value: float, equity: float) -> float:
    if equity <= 0:
        return 0.0
    return round(value / equity * 100.0, 3)


def write_outputs(payload: dict[str, Any]) -> None:
    JSON_PATH.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")

    account = payload["account"]
    current = payload["current_lane"]
    lane_status = str(payload.get("lane_status") or "active")
    lines = [
        "# Live BTCUSD M5 Survivability Board",
        "",
        f"- Generated at: `{payload['generated_at']}`",
        f"- Lane: `{payload['lane']}`",
        f"- Lane status: `{lane_status}`",
        f"- Current bid/ask: `{payload['current_bid']:.2f}` / `{payload['current_ask']:.2f}`",
        f"- Account equity: `${account['equity']:.2f}` | balance `${account['balance']:.2f}` | free margin `${account['margin_free']:.2f}` | margin level `{account['margin_level']:.2f}%`",
        f"- Current lane net: `${current['net_usd']:+.2f}` ({current['net_pct_equity']:+.3f}% of equity)",
        f"- Current lane composition: `{current['buy_count']}` buys / `{current['sell_count']}` sells / `{current['open_count']}` open",
    ]
    if lane_status != "active":
        lines.extend(
            [
                f"- Registry pause note: `{payload.get('pause_note') or '-'}`",
                "",
                "## Read",
                "",
                "- This lane is currently parked in registry, so this board is a broker/account context surface only.",
                "- Directional stress is intentionally omitted while the lane is disabled; use the BTC concentration board and MT5 visibility board for the active operator answer.",
            ]
        )
        MD_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return
    lines.extend(
        [
            "",
            "## Directional Stress",
            "",
            "| Scenario | Move | End Bid | Realized | Floating | Net | Net % Equity | Delta vs Current | Delta % Equity | Open | B | S |",
            "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
        ]
    )
    for row in payload["scenarios"]:
        lines.append(
            f"| `{row['scenario']}` | {row['move_points']} | {row['end_bid']:.2f} | "
            f"{row['realized_usd']:+.2f} | {row['floating_usd']:+.2f} | {row['net_usd']:+.2f} | "
            f"{row['net_pct_equity']:+.3f}% | {row['delta_vs_current_usd']:+.2f} | {row['delta_vs_current_pct_equity']:+.3f}% | "
            f"{row['open_count']} | {row['buy_count']} | {row['sell_count']} |"
        )
    lines.extend(
        [
            "",
            "## Read",
            "",
            "- This board reports lane impact relative to live account equity, not just raw floating loss.",
            "- Stress scenarios are monotonic tick-path simulations from the current live lane state; they are useful for one-sided snake intuition, not for exact market forecasting.",
            "- Margin-under-stress is intentionally omitted here because the repo does not yet have a trustworthy broker-backed per-scenario margin simulator for this lane.",
        ]
    )
    MD_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    lane_registry = registry_lane()
    mt5_ready, mt5_connection = mt5_terminal_guard.initialize_mt5(mt5_module=mt5)
    if not mt5_ready:
        print(mt5_terminal_guard.failure_summary(mt5_connection))
        return 1
    try:
        tick = mt5.symbol_info_tick(SYMBOL)
        account = mt5.account_info()
        if tick is None or account is None:
            print("Missing live MT5 tick/account data")
            return 1

        bid = float(tick.bid or 0.0)
        ask = float(tick.ask or 0.0)
        base_time = int(getattr(tick, "time", 0) or 0)
        base_msc = int(getattr(tick, "time_msc", base_time * 1000) or 0)
        equity = float(account.equity or 0.0)
        enabled = bool(lane_registry.get("enabled", True))

        if not enabled:
            payload = {
                "generated_at": datetime.now().astimezone(timezone.utc).isoformat(),
                "lane": LANE_NAME,
                "lane_status": "inactive",
                "pause_note": str(lane_registry.get("pause_note") or ""),
                "current_bid": round(bid, 2),
                "current_ask": round(ask, 2),
                "account": {
                    "balance": round(float(account.balance or 0.0), 2),
                    "equity": round(equity, 2),
                    "margin": round(float(account.margin or 0.0), 2),
                    "margin_free": round(float(account.margin_free or 0.0), 2),
                    "margin_level": round(float(account.margin_level or 0.0), 2),
                },
                "current_lane": {
                    "realized_usd": 0.0,
                    "floating_usd": 0.0,
                    "net_usd": 0.0,
                    "net_pct_equity": 0.0,
                    "open_count": 0,
                    "buy_count": 0,
                    "sell_count": 0,
                    "max_open_total": 0,
                    "next_buy_level": 0.0,
                    "next_sell_level": 0.0,
                },
                "scenarios": [],
            }
            write_outputs(payload)
            print(f"Wrote {JSON_PATH}")
            print(f"Wrote {MD_PATH}")
            print(f"Lane inactive in registry | pause_note={payload['pause_note'] or '-'}")
            return 0

        if not STATE_PATH.exists():
            print(f"Missing {STATE_PATH}")
            return 1
        state_doc = load_json(STATE_PATH)
        metadata = state_doc.get("metadata") or {}
        symbol_state = (state_doc.get("symbols") or {}).get(SYMBOL)
        if not isinstance(symbol_state, dict):
            print(f"Missing {SYMBOL} state in {STATE_PATH}")
            return 1
        current = mark_engine(build_engine_from_state(metadata, symbol_state), bid, ask)
        current["net_pct_equity"] = pct_of_equity(float(current["net_usd"]), equity)

        scenarios = []
        for move_points in STRESS_MOVES:
            for direction in ("up", "down"):
                row = run_directional_stress(
                    metadata=metadata,
                    symbol_state=symbol_state,
                    bid=bid,
                    ask=ask,
                    base_time=base_time,
                    base_msc=base_msc,
                    move_points=move_points,
                    direction=direction,
                )
                row["net_pct_equity"] = pct_of_equity(float(row["net_usd"]), equity)
                row["delta_vs_current_usd"] = round(float(row["net_usd"]) - float(current["net_usd"]), 2)
                row["delta_vs_current_pct_equity"] = pct_of_equity(float(row["delta_vs_current_usd"]), equity)
                scenarios.append(row)

        payload = {
            "generated_at": state_doc.get("updated_at"),
            "lane": LANE_NAME,
            "lane_status": "active",
            "pause_note": str(lane_registry.get("pause_note") or ""),
            "current_bid": round(bid, 2),
            "current_ask": round(ask, 2),
            "account": {
                "balance": round(float(account.balance or 0.0), 2),
                "equity": round(equity, 2),
                "margin": round(float(account.margin or 0.0), 2),
                "margin_free": round(float(account.margin_free or 0.0), 2),
                "margin_level": round(float(account.margin_level or 0.0), 2),
            },
            "current_lane": current,
            "scenarios": scenarios,
        }
        write_outputs(payload)
        print(f"Wrote {JSON_PATH}")
        print(f"Wrote {MD_PATH}")
        print(
            f"Current net {current['net_usd']:+.2f} ({current['net_pct_equity']:+.3f}% equity) | "
            f"account equity ${equity:.2f}"
        )
        return 0
    finally:
        mt5.shutdown()


if __name__ == "__main__":
    raise SystemExit(main())
