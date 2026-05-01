#!/usr/bin/env python3
from __future__ import annotations

import csv
import json
from collections import defaultdict
from pathlib import Path
from typing import Any

import MetaTrader5 as mt5
import mt5_terminal_guard


ROOT = Path(__file__).resolve().parent.parent
REPORTS = ROOT / "reports"
STATE_PATH = REPORTS / "penetration_lattice_shadow_btcusd_exc2_tight_state.json"
SCOREBOARD_PATH = REPORTS / "penetration_lattice_lane_scoreboard.csv"
JSON_PATH = REPORTS / "live_btcusd_h1_inventory_board.json"
MD_PATH = REPORTS / "live_btcusd_h1_inventory_board.md"
LANE_ID = "live_btcusd_exc2_tight_941779"
SYMBOL = "BTCUSD"
LIVE_MAGIC = 941779
LIVE_PREFIX = "PLIVE-BTC"


def load_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def load_broker_total_row() -> dict[str, str] | None:
    if not SCOREBOARD_PATH.exists():
        return None
    with SCOREBOARD_PATH.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            if (
                row.get("lane_id") == LANE_ID
                and row.get("symbol") == "TOTAL"
                and row.get("realized_basis") == "broker"
            ):
                return row
    return None


def as_float(row: dict[str, str] | None, key: str, default: float = 0.0) -> float:
    if not row:
        return default
    try:
        return float(row.get(key, default) or default)
    except (TypeError, ValueError):
        return default


def as_int(row: dict[str, str] | None, key: str, default: int = 0) -> int:
    if not row:
        return default
    try:
        return int(float(row.get(key, default) or default))
    except (TypeError, ValueError):
        return default


def direction_from_position(pos: Any) -> str:
    pos_type = int(getattr(pos, "type", -1) or -1)
    buy_type = int(getattr(mt5, "POSITION_TYPE_BUY", 0) or 0)
    sell_type = int(getattr(mt5, "POSITION_TYPE_SELL", 1) or 1)
    if pos_type == buy_type:
        return "BUY"
    if pos_type == sell_type:
        return "SELL"
    comment = str(getattr(pos, "comment", "") or "")
    if comment.endswith("-B"):
        return "BUY"
    if comment.endswith("-S"):
        return "SELL"
    return "UNKNOWN"


def live_positions() -> list[Any]:
    positions = mt5.positions_get() or []
    filtered = []
    for pos in positions:
        if int(getattr(pos, "magic", 0) or 0) != LIVE_MAGIC:
            continue
        comment = str(getattr(pos, "comment", "") or "")
        if not comment.startswith(LIVE_PREFIX):
            continue
        if str(getattr(pos, "symbol", "") or "") != SYMBOL:
            continue
        filtered.append(pos)
    return filtered


def build_position_rows(
    *,
    positions: list[Any],
    state_tickets: dict[int, dict[str, Any]],
    bid: float,
    ask: float,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for pos in positions:
        ticket = int(getattr(pos, "ticket", 0) or 0)
        direction = direction_from_position(pos)
        open_price = float(getattr(pos, "price_open", 0.0) or 0.0)
        volume = float(getattr(pos, "volume", 0.0) or 0.0)
        profit = float(getattr(pos, "profit", 0.0) or 0.0)
        state_ticket = state_tickets.get(ticket, {})
        if direction == "BUY":
            distance_to_open = bid - open_price
        elif direction == "SELL":
            distance_to_open = open_price - ask
        else:
            distance_to_open = 0.0
        rows.append(
            {
                "ticket": ticket,
                "direction": direction,
                "open_price": round(open_price, 2),
                "volume": round(volume, 4),
                "profit": round(profit, 2),
                "comment": str(getattr(pos, "comment", "") or ""),
                "level_idx": state_ticket.get("level_idx"),
                "trigger_level": state_ticket.get("trigger_level"),
                "fill_price": state_ticket.get("entry_fill_price", state_ticket.get("fill_price")),
                "distance_to_open_points": round(distance_to_open, 2),
            }
        )
    rows.sort(key=lambda row: (row["profit"], row["ticket"]))
    return rows


def summarize_side(rows: list[dict[str, Any]], direction: str) -> dict[str, Any]:
    side_rows = [row for row in rows if row["direction"] == direction]
    total_volume = sum(float(row["volume"]) for row in side_rows)
    weighted_open = 0.0
    if total_volume > 0:
        weighted_open = sum(float(row["open_price"]) * float(row["volume"]) for row in side_rows) / total_volume
    return {
        "direction": direction,
        "count": len(side_rows),
        "volume": round(total_volume, 4),
        "floating_usd": round(sum(float(row["profit"]) for row in side_rows), 2),
        "weighted_open_price": round(weighted_open, 2) if total_volume > 0 else 0.0,
        "best_profit_usd": round(max((float(row["profit"]) for row in side_rows), default=0.0), 2),
        "worst_profit_usd": round(min((float(row["profit"]) for row in side_rows), default=0.0), 2),
    }


def cluster_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[tuple[str, float], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[(str(row["direction"]), float(row["open_price"]))].append(row)
    clusters: list[dict[str, Any]] = []
    for (direction, open_price), members in grouped.items():
        level_indices = [int(v) for v in (member.get("level_idx") for member in members) if v is not None]
        clusters.append(
            {
                "direction": direction,
                "open_price": round(open_price, 2),
                "count": len(members),
                "floating_usd": round(sum(float(member["profit"]) for member in members), 2),
                "tickets": [int(member["ticket"]) for member in members],
                "level_idx_min": min(level_indices) if level_indices else None,
                "level_idx_max": max(level_indices) if level_indices else None,
            }
        )
    clusters.sort(key=lambda row: (row["direction"], row["floating_usd"], -row["count"], row["open_price"]))
    return clusters


def write_outputs(payload: dict[str, Any]) -> None:
    JSON_PATH.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")

    broker = payload["broker"]
    state = payload["state"]
    market = payload["market"]
    inventory = payload["inventory"]
    lines = [
        "# Live BTCUSD H1 Inventory Board",
        "",
        f"- Generated at: `{payload['generated_at']}`",
        f"- Lane: `{payload['lane']}`",
        f"- Current bid/ask: `{market['bid']:.2f}` / `{market['ask']:.2f}`",
        f"- Broker realized/floating/net: `${broker['realized_usd']:+.2f}` / `${broker['floating_usd']:+.2f}` / `${broker['net_usd']:+.2f}`",
        f"- Broker closes/open: `{broker['closes']}` / `{broker['open_count']}`",
        f"- Current side split: `{inventory['buy_side']['count']}` BUY / `{inventory['sell_side']['count']}` SELL",
        f"- Floating by side: BUY `${inventory['buy_side']['floating_usd']:+.2f}` | SELL `${inventory['sell_side']['floating_usd']:+.2f}`",
        f"- State anchor / next buy / next sell: `{state['anchor']:.2f}` / `{state['next_buy_level']:.2f}` / `{state['next_sell_level']:.2f}`",
        "",
        "## Side Summary",
        "",
        "| Side | Count | Volume | Floating USD | Weighted Open | Best Ticket | Worst Ticket |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for side in (inventory["buy_side"], inventory["sell_side"]):
        lines.append(
            f"| `{side['direction']}` | {side['count']} | {side['volume']:.4f} | {side['floating_usd']:+.2f} | "
            f"{side['weighted_open_price']:.2f} | {side['best_profit_usd']:+.2f} | {side['worst_profit_usd']:+.2f} |"
        )

    lines.extend(
        [
            "",
            "## Concentration Clusters",
            "",
            "| Side | Open Price | Count | Floating USD | Level Span | Tickets |",
            "| --- | ---: | ---: | ---: | --- | --- |",
        ]
    )
    for row in payload["clusters"][:12]:
        level_span = ""
        if row["level_idx_min"] is not None:
            if row["level_idx_min"] == row["level_idx_max"]:
                level_span = str(row["level_idx_min"])
            else:
                level_span = f"{row['level_idx_min']}..{row['level_idx_max']}"
        ticket_list = ", ".join(str(ticket) for ticket in row["tickets"][:4])
        if len(row["tickets"]) > 4:
            ticket_list += ", ..."
        lines.append(
            f"| `{row['direction']}` | {row['open_price']:.2f} | {row['count']} | {row['floating_usd']:+.2f} | `{level_span}` | `{ticket_list}` |"
        )

    lines.extend(
        [
            "",
            "## Worst Tickets",
            "",
            "| Ticket | Side | Open Price | Floating USD | Dist to Open | Level | Trigger |",
            "| --- | --- | ---: | ---: | ---: | ---: | ---: |",
        ]
    )
    for row in payload["worst_tickets"]:
        trigger = "" if row["trigger_level"] is None else f"{float(row['trigger_level']):.2f}"
        level = "" if row["level_idx"] is None else str(row["level_idx"])
        lines.append(
            f"| `{row['ticket']}` | `{row['direction']}` | {row['open_price']:.2f} | {row['profit']:+.2f} | "
            f"{row['distance_to_open_points']:+.2f} | `{level}` | `{trigger}` |"
        )

    lines.extend(
        [
            "",
            "## Read",
            "",
            "- This board is current-state broker truth for the live H1 BTC lane, not a historical cap replay.",
            "- Broker realized/floating/net comes from the lane scoreboard; open-ticket concentration comes from live MT5 positions mapped to state tickets.",
            "- Use this to judge current side bias and trapped inventory shape before proposing any cap or unwind experiments.",
        ]
    )
    MD_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    if not STATE_PATH.exists():
        print(f"Missing {STATE_PATH}")
        return 1
    mt5_ready, mt5_connection = mt5_terminal_guard.initialize_mt5(mt5_module=mt5)
    if not mt5_ready:
        print(mt5_terminal_guard.failure_summary(mt5_connection))
        return 1
    try:
        state_doc = load_json(STATE_PATH)
        symbol_state = (state_doc.get("symbols") or {}).get(SYMBOL)
        if not isinstance(symbol_state, dict):
            print(f"Missing {SYMBOL} state in {STATE_PATH}")
            return 1
        tick = mt5.symbol_info_tick(SYMBOL)
        if tick is None:
            print(f"Missing live tick for {SYMBOL}")
            return 1
        broker_row = load_broker_total_row()
        if broker_row is None:
            print(f"Missing broker scoreboard row for {LANE_ID}")
            return 1

        positions = live_positions()
        state_tickets = {
            int(ticket.get("live_ticket", 0) or 0): ticket
            for ticket in list(symbol_state.get("open_tickets", []) or [])
            if int(ticket.get("live_ticket", 0) or 0) > 0
        }
        rows = build_position_rows(
            positions=positions,
            state_tickets=state_tickets,
            bid=float(tick.bid or 0.0),
            ask=float(tick.ask or 0.0),
        )
        buy_side = summarize_side(rows, "BUY")
        sell_side = summarize_side(rows, "SELL")
        positions_floating = round(sum(float(row["profit"]) for row in rows), 2)

        payload = {
            "generated_at": state_doc.get("updated_at"),
            "lane": LANE_ID,
            "market": {
                "bid": round(float(tick.bid or 0.0), 2),
                "ask": round(float(tick.ask or 0.0), 2),
            },
            "broker": {
                "realized_usd": round(as_float(broker_row, "realized_usd"), 2),
                "floating_usd": round(as_float(broker_row, "floating_usd"), 2),
                "net_usd": round(as_float(broker_row, "net_usd"), 2),
                "closes": as_int(broker_row, "closes"),
                "open_count": as_int(broker_row, "open_count"),
                "positions_floating_check_usd": positions_floating,
                "floating_delta_vs_positions_usd": round(
                    as_float(broker_row, "floating_usd") - positions_floating, 2
                ),
            },
            "state": {
                "anchor": round(float(symbol_state.get("anchor", 0.0) or 0.0), 2),
                "next_buy_level": round(float(symbol_state.get("next_buy_level", 0.0) or 0.0), 2),
                "next_sell_level": round(float(symbol_state.get("next_sell_level", 0.0) or 0.0), 2),
                "max_open_total": int(symbol_state.get("max_open_total", 0) or 0),
                "rearm_opens": int(symbol_state.get("rearm_opens", 0) or 0),
                "rearm_token_count": len(list(symbol_state.get("rearm_tokens", []) or [])),
            },
            "inventory": {
                "buy_side": buy_side,
                "sell_side": sell_side,
                "rows": rows,
            },
            "clusters": cluster_rows(rows),
            "worst_tickets": rows[:8],
        }
        write_outputs(payload)
        print(f"Wrote {JSON_PATH}")
        print(f"Wrote {MD_PATH}")
        print(
            f"{LANE_ID}: broker net={payload['broker']['net_usd']:+.2f} | "
            f"BUY {buy_side['count']} / SELL {sell_side['count']} | "
            f"BUY float {buy_side['floating_usd']:+.2f} / SELL float {sell_side['floating_usd']:+.2f}"
        )
        return 0
    finally:
        mt5.shutdown()


if __name__ == "__main__":
    raise SystemExit(main())
