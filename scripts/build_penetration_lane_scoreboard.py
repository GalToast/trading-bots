#!/usr/bin/env python3
from __future__ import annotations

import csv
import json
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import MetaTrader5 as mt5
import mt5_terminal_guard

from penetration_lattice_lab_v2 import pip_size_for, spread_price, unit_pnl_usd


ROOT = Path(__file__).resolve().parent.parent
REPORTS = ROOT / "reports"
CSV_PATH = REPORTS / "penetration_lattice_lane_scoreboard.csv"
MD_PATH = REPORTS / "penetration_lattice_lane_scoreboard.md"


@dataclass(frozen=True)
class LaneSpec:
    lane_id: str
    lane_type: str
    state_path: Path
    exec_log_path: Path | None = None
    live_magic: int | None = None
    live_prefix: str | None = None


LANES = [
    LaneSpec(
        lane_id="live_rearm_941777",
        lane_type="live",
        state_path=REPORTS / "penetration_lattice_live_source_state.json",
        exec_log_path=REPORTS / "penetration_lattice_live_mirror_events.jsonl",
        live_magic=941777,
        live_prefix="PLIVE-LATTICE",
    ),
    LaneSpec(
        lane_id="live_momentum_alpha50_941778",
        lane_type="live",
        state_path=REPORTS / "penetration_lattice_live_momentum_alpha50_source_state.json",
        exec_log_path=REPORTS / "penetration_lattice_live_momentum_alpha50_exec_events.jsonl",
        live_magic=941778,
        live_prefix="PLIVE-MOM",
    ),
    LaneSpec(
        lane_id="live_btcusd_exc2_tight_941779",
        lane_type="live",
        state_path=REPORTS / "penetration_lattice_shadow_btcusd_exc2_tight_state.json",
        exec_log_path=REPORTS / "penetration_lattice_live_btcusd_exc2_tight_exec_events.jsonl",
        live_magic=941779,
        live_prefix="PLIVE-BTC",
    ),
    LaneSpec(
        lane_id="live_btcusd_m15_warp_941781",
        lane_type="live",
        state_path=REPORTS / "penetration_lattice_live_btcusd_m15_warp_state.json",
        exec_log_path=REPORTS / "penetration_lattice_live_btcusd_m15_warp_exec_events.jsonl",
        live_magic=941781,
        live_prefix="PLIVE-WARP",
    ),
    LaneSpec(
        lane_id="live_btcusd_m5_warp_probation_941780",
        lane_type="live",
        state_path=REPORTS / "penetration_lattice_live_btcusd_m5_warp_state.json",
        exec_log_path=REPORTS / "penetration_lattice_live_btcusd_m5_warp_exec_events.jsonl",
        live_magic=941780,
        live_prefix="PLIVE-BTCM5",
    ),
    LaneSpec(
        lane_id="shadow_momentum_alpha50",
        lane_type="shadow",
        state_path=REPORTS / "penetration_lattice_shadow_momentum_alpha50_state.json",
    ),
    LaneSpec(
        lane_id="shadow_sg1_bg1_a100",
        lane_type="shadow",
        state_path=REPORTS / "penetration_lattice_shadow_sg1bg1a100_state.json",
    ),
    LaneSpec(
        lane_id="shadow_usdjpy_gap2",
        lane_type="shadow",
        state_path=REPORTS / "penetration_lattice_shadow_usdjpy_gap2_state.json",
    ),
    LaneSpec(
        lane_id="shadow_usdjpy_shallow03",
        lane_type="shadow",
        state_path=REPORTS / "penetration_lattice_shadow_usdjpy_shallow03_state.json",
    ),
    LaneSpec(
        lane_id="shadow_btcusd_h1",
        lane_type="shadow",
        state_path=REPORTS / "penetration_lattice_shadow_btcusd_h1_state.json",
    ),
    LaneSpec(
        lane_id="shadow_btcusd_h1_step30",
        lane_type="shadow",
        state_path=REPORTS / "penetration_lattice_shadow_btcusd_h1_step30_state.json",
    ),
    LaneSpec(
        lane_id="shadow_btcusd_h1_step50",
        lane_type="shadow",
        state_path=REPORTS / "penetration_lattice_shadow_btcusd_h1_step50_state.json",
    ),
]


def load_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def parse_iso_utc(value: str | None) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None


def live_positions_by_magic(magic: int, prefix: str | None) -> list[Any]:
    positions = mt5.positions_get()
    if positions is None:
        return []
    results = []
    for pos in positions:
        if int(getattr(pos, "magic", 0) or 0) != int(magic):
            continue
        comment = str(getattr(pos, "comment", "") or "")
        if prefix and not comment.startswith(prefix):
            continue
        results.append(pos)
    return results


def first_log_timestamp(path: Path | None) -> datetime | None:
    if path is None or not path.exists():
        return None
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            text = line.strip()
            if not text:
                continue
            try:
                payload = json.loads(text)
            except json.JSONDecodeError:
                continue
            ts = parse_iso_utc(payload.get("ts_utc"))
            if ts is not None:
                return ts
    return None


def session_started_at(spec: LaneSpec, state: dict[str, Any]) -> datetime:
    candidates = [
        first_log_timestamp(spec.exec_log_path),
        parse_iso_utc((state.get("runner") or {}).get("started_at")),
        parse_iso_utc(state.get("updated_at")),
    ]
    valid = [candidate for candidate in candidates if candidate is not None]
    if valid:
        return min(valid)
    now = datetime.now(timezone.utc)
    return now.replace(hour=0, minute=0, second=0, microsecond=0)


def deal_net_usd(deal: Any) -> float:
    return (
        float(deal_field(deal, "profit", 0.0) or 0.0)
        + float(deal_field(deal, "swap", 0.0) or 0.0)
        + float(deal_field(deal, "commission", 0.0) or 0.0)
        + float(deal_field(deal, "fee", 0.0) or 0.0)
    )


def is_exit_deal(deal: Any) -> bool:
    entry_code = int(deal_field(deal, "entry", -1) or -1)
    exit_codes = {int(getattr(mt5, "DEAL_ENTRY_OUT", 1) or 1)}
    out_by = getattr(mt5, "DEAL_ENTRY_OUT_BY", None)
    if out_by is not None:
        exit_codes.add(int(out_by))
    return entry_code in exit_codes


def deal_field(deal: Any, name: str, default: Any = None) -> Any:
    if isinstance(deal, dict):
        return deal.get(name, default)
    return getattr(deal, name, default)


def exact_logged_deals(spec: LaneSpec) -> list[Any]:
    if spec.exec_log_path is None or not spec.exec_log_path.exists():
        return []
    resolved: list[Any] = []
    seen_tickets: set[int] = set()
    with spec.exec_log_path.open("r", encoding="utf-8") as handle:
        for line in handle:
            text = line.strip()
            if not text:
                continue
            try:
                payload = json.loads(text)
            except json.JSONDecodeError:
                continue
            result = payload.get("result") or {}
            event = payload.get("event") or {}
            symbol = str(event.get("symbol") or payload.get("symbol") or "")
            for attempt in result.get("attempts") or []:
                deal_ticket = int(attempt.get("deal", 0) or 0)
                if deal_ticket <= 0 or deal_ticket in seen_tickets:
                    continue
                seen_tickets.add(deal_ticket)
                deals = mt5.history_deals_get(ticket=deal_ticket) or []
                if deals:
                    resolved.append(deals[-1])
                    continue
                broker_fill = result.get("broker_fill")
                if isinstance(broker_fill, dict):
                    resolved.append(
                        {
                            "ticket": deal_ticket,
                            "symbol": symbol,
                            "entry": broker_fill.get("entry", -1),
                            "comment": broker_fill.get("comment") or result.get("comment", ""),
                            "profit": broker_fill.get("profit", 0.0),
                            "commission": broker_fill.get("commission", 0.0),
                            "swap": broker_fill.get("swap", 0.0),
                            "fee": broker_fill.get("fee", 0.0),
                        }
                    )
    return resolved


def live_deals_for_lane(spec: LaneSpec, state: dict[str, Any]) -> tuple[list[Any], datetime]:
    started_at = session_started_at(spec, state)
    exact_deals = exact_logged_deals(spec)
    if exact_deals:
        return exact_deals, started_at
    deals = mt5.history_deals_get(started_at, datetime.now(timezone.utc)) or []
    filtered: list[Any] = []
    for deal in deals:
        comment = str(deal_field(deal, "comment", "") or "")
        if spec.live_prefix and not comment.startswith(spec.live_prefix):
            continue
        deal_magic = int(deal_field(deal, "magic", 0) or 0)
        if spec.live_magic and deal_magic not in {0, int(spec.live_magic)}:
            continue
        filtered.append(deal)
    return filtered, started_at


def summarize_live_lane(spec: LaneSpec, state: dict[str, Any]) -> list[dict[str, Any]]:
    positions = live_positions_by_magic(spec.live_magic or 0, spec.live_prefix)
    positions_by_symbol: dict[str, list[Any]] = defaultdict(list)
    for pos in positions:
        positions_by_symbol[str(pos.symbol)].append(pos)
    deals, started_at = live_deals_for_lane(spec, state)
    realized_by_symbol: dict[str, float] = defaultdict(float)
    closes_by_symbol: dict[str, int] = defaultdict(int)
    for deal in deals:
        symbol = str(deal_field(deal, "symbol", "") or "")
        realized_by_symbol[symbol] += deal_net_usd(deal)
        if is_exit_deal(deal):
            closes_by_symbol[symbol] += 1

    rows: list[dict[str, Any]] = []
    total_realized = 0.0
    total_modeled_realized = 0.0
    total_floating = 0.0
    total_closes = 0
    total_open = 0
    symbols = state.get("symbols", {})
    for symbol, symbol_state in symbols.items():
        modeled_realized = float(symbol_state.get("realized_net_usd", 0.0) or 0.0)
        realized = float(realized_by_symbol.get(symbol, 0.0) or 0.0)
        closes = int(closes_by_symbol.get(symbol, 0) or 0)
        broker_positions = positions_by_symbol.get(symbol, [])
        floating = sum(float(getattr(pos, "profit", 0.0) or 0.0) for pos in broker_positions)
        open_count = len(broker_positions)
        total_realized += realized
        total_modeled_realized += modeled_realized
        total_floating += floating
        total_closes += closes
        total_open += open_count
        rows.append(
            {
                "lane_id": spec.lane_id,
                "lane_type": spec.lane_type,
                "symbol": symbol,
                "updated_at": state.get("updated_at", ""),
                "session_started_at": started_at.isoformat(),
                "realized_basis": "broker",
                "realized_usd": round(realized, 3),
                "modeled_realized_usd": round(modeled_realized, 3),
                "realized_gap_usd": round(realized - modeled_realized, 3),
                "floating_usd": round(floating, 3),
                "net_usd": round(realized + floating, 3),
                "closes": closes,
                "open_count": open_count,
                "avg_usd_per_close": round(realized / closes, 3) if closes else 0.0,
            }
        )

    rows.append(
        {
            "lane_id": spec.lane_id,
            "lane_type": spec.lane_type,
            "symbol": "TOTAL",
            "updated_at": state.get("updated_at", ""),
            "session_started_at": started_at.isoformat(),
            "realized_basis": "broker",
            "realized_usd": round(total_realized, 3),
            "modeled_realized_usd": round(total_modeled_realized, 3),
            "realized_gap_usd": round(total_realized - total_modeled_realized, 3),
            "floating_usd": round(total_floating, 3),
            "net_usd": round(total_realized + total_floating, 3),
            "closes": total_closes,
            "open_count": total_open,
            "avg_usd_per_close": round(total_realized / total_closes, 3) if total_closes else 0.0,
        }
    )
    return rows


def synthetic_shadow_floating(symbol: str, open_tickets: list[dict[str, Any]]) -> float:
    if not open_tickets:
        return 0.0
    info = mt5.symbol_info(symbol)
    tick = mt5.symbol_info_tick(symbol)
    if info is None or tick is None:
        return 0.0
    spread_px = spread_price(info)
    mark_price = float(tick.bid or tick.last or 0.0)
    if mark_price <= 0:
        mark_price = float(tick.ask or tick.last or 0.0)
    total = 0.0
    for ticket in open_tickets:
        direction = str(ticket.get("direction", ""))
        entry_price = float(ticket.get("entry_price", 0.0) or 0.0)
        if not direction or entry_price <= 0:
            continue
        total += unit_pnl_usd(symbol, direction, entry_price, mark_price, spread_px)
    return total


def summarize_shadow_lane(spec: LaneSpec, state: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    total_realized = 0.0
    total_floating = 0.0
    total_closes = 0
    total_open = 0
    symbols = state.get("symbols", {})
    for symbol, symbol_state in symbols.items():
        realized = float(symbol_state.get("realized_net_usd", 0.0) or 0.0)
        closes = int(symbol_state.get("realized_closes", 0) or 0)
        open_tickets = list(symbol_state.get("open_tickets", []) or [])
        floating = synthetic_shadow_floating(symbol, open_tickets)
        open_count = len(open_tickets)
        total_realized += realized
        total_floating += floating
        total_closes += closes
        total_open += open_count
        rows.append(
            {
                "lane_id": spec.lane_id,
                "lane_type": spec.lane_type,
                "symbol": symbol,
                "updated_at": state.get("updated_at", ""),
                "session_started_at": "",
                "realized_basis": "modeled",
                "realized_usd": round(realized, 3),
                "modeled_realized_usd": round(realized, 3),
                "realized_gap_usd": 0.0,
                "floating_usd": round(floating, 3),
                "net_usd": round(realized + floating, 3),
                "closes": closes,
                "open_count": open_count,
                "avg_usd_per_close": round(realized / closes, 3) if closes else 0.0,
            }
        )

    rows.append(
        {
            "lane_id": spec.lane_id,
            "lane_type": spec.lane_type,
            "symbol": "TOTAL",
            "updated_at": state.get("updated_at", ""),
            "session_started_at": "",
            "realized_basis": "modeled",
            "realized_usd": round(total_realized, 3),
            "modeled_realized_usd": round(total_realized, 3),
            "realized_gap_usd": 0.0,
            "floating_usd": round(total_floating, 3),
            "net_usd": round(total_realized + total_floating, 3),
            "closes": total_closes,
            "open_count": total_open,
            "avg_usd_per_close": round(total_realized / total_closes, 3) if total_closes else 0.0,
        }
    )
    return rows


def write_csv(rows: list[dict[str, Any]]) -> None:
    CSV_PATH.parent.mkdir(parents=True, exist_ok=True)
    fields = [
        "lane_id",
        "lane_type",
        "symbol",
        "updated_at",
        "session_started_at",
        "realized_basis",
        "realized_usd",
        "modeled_realized_usd",
        "realized_gap_usd",
        "floating_usd",
        "net_usd",
        "closes",
        "open_count",
        "avg_usd_per_close",
    ]
    with CSV_PATH.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def write_md(rows: list[dict[str, Any]]) -> None:
    lines = [
        "# Penetration Lattice Lane Scoreboard",
        "",
        "| Lane | Type | Symbol | Realized Basis | Realized USD | Modeled Realized USD | Gap USD | Floating USD | Net USD | Closes | Open | Avg USD/Close | Session Start | Updated |",
        "| --- | --- | --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- | --- |",
    ]
    for row in rows:
        lines.append(
            f"| `{row['lane_id']}` | `{row['lane_type']}` | `{row['symbol']}` | `{row['realized_basis']}` | "
            f"{row['realized_usd']:.3f} | {row['modeled_realized_usd']:.3f} | {row['realized_gap_usd']:.3f} | "
            f"{row['floating_usd']:.3f} | {row['net_usd']:.3f} | {row['closes']} | {row['open_count']} | "
            f"{row['avg_usd_per_close']:.3f} | {row['session_started_at']} | {row['updated_at']} |"
        )
    MD_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    mt5_ready, mt5_connection = mt5_terminal_guard.initialize_mt5(mt5_module=mt5)
    if not mt5_ready:
        print(mt5_terminal_guard.failure_summary(mt5_connection))
        return 1
    try:
        rows: list[dict[str, Any]] = []
        for spec in LANES:
            if not spec.state_path.exists():
                continue
            state = load_json(spec.state_path)
            if spec.lane_type == "live":
                rows.extend(summarize_live_lane(spec, state))
            else:
                rows.extend(summarize_shadow_lane(spec, state))
        write_csv(rows)
        write_md(rows)
        print(f"Wrote {CSV_PATH}")
        print(f"Wrote {MD_PATH}")
        for row in rows:
            if row["symbol"] == "TOTAL":
                print(
                    f"{row['lane_id']}: realized={row['realized_usd']} "
                    f"floating={row['floating_usd']} net={row['net_usd']} "
                    f"closes={row['closes']} open={row['open_count']} avg={row['avg_usd_per_close']}"
                )
        return 0
    finally:
        mt5.shutdown()


if __name__ == "__main__":
    raise SystemExit(main())
