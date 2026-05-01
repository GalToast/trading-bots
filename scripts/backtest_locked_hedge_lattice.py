#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import MetaTrader5 as mt5

from backtest_adaptive_deployment_study import TIMEFRAME_MAP, load_bars
from backtest_snake_counter_web import (
    TIMEFRAME_SECONDS,
    _cross_down_levels,
    _cross_up_levels,
    _segment_path,
    filter_rows,
    pip_size_for,
    research_unit_pnl_usd,
    safe_float,
    score_key,
    spread_price,
    utc_now_iso,
)


ROOT = Path(__file__).resolve().parent.parent
OUTPUT_CSV = ROOT / "reports" / "locked_hedge_lattice_study.csv"
OUTPUT_MD = ROOT / "reports" / "locked_hedge_lattice_study.md"
OUTPUT_JSON = ROOT / "reports" / "locked_hedge_lattice_study.json"


@dataclass(frozen=True)
class LockedHedgeContract:
    symbol: str
    timeframe: str
    step_px: float
    mode: str
    oscillation_trigger_steps: int
    oscillation_close_steps: int
    max_oscillation_per_side: int
    reanchor_threshold_steps: int
    variant_label: str


@dataclass
class HedgeTicket:
    direction: str
    entry_price: float
    opened_time: int
    ticket_kind: str = "oscillation"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Locked-base hedge lattice study for bounded-floating oscillation harvesting."
    )
    parser.add_argument("--symbols", nargs="*", default=["GBPUSD", "EURUSD"])
    parser.add_argument("--days", type=int, default=5)
    parser.add_argument("--timeframe", choices=sorted(TIMEFRAME_MAP.keys()), default="M1")
    parser.add_argument("--step-pips", nargs="*", type=float, default=[0.03, 0.05, 0.1, 0.2, 0.5])
    parser.add_argument("--modes", nargs="*", default=["locked_spread"], choices=["locked_spread", "full_hedge"])
    parser.add_argument("--oscillation-trigger-steps", nargs="*", type=int, default=[1, 2, 3])
    parser.add_argument("--oscillation-close-steps", nargs="*", type=int, default=[1, 2])
    parser.add_argument("--max-oscillation-per-side-values", nargs="*", type=int, default=[8, 16, 32])
    parser.add_argument("--reanchor-threshold-steps", nargs="*", type=int, default=[10])
    parser.add_argument(
        "--rank-mode",
        choices=["booked_usd_per_hour", "balanced"],
        default="booked_usd_per_hour",
    )
    parser.add_argument("--max-mae-abs-usd", type=float, default=None)
    parser.add_argument("--max-final-open", type=int, default=None)
    parser.add_argument("--max-max-open", type=int, default=None)
    parser.add_argument("--require-realized-cover", action="store_true")
    parser.add_argument("--starting-balance-usd", type=float, default=None)
    parser.add_argument("--hard-floor-usd", type=float, default=None)
    parser.add_argument("--output-csv", default=str(OUTPUT_CSV))
    parser.add_argument("--output-md", default=str(OUTPUT_MD))
    parser.add_argument("--output-json", default=str(OUTPUT_JSON))
    return parser.parse_args()


def build_contracts(args: argparse.Namespace) -> list[LockedHedgeContract]:
    contracts: list[LockedHedgeContract] = []
    for symbol in [str(s).upper() for s in args.symbols]:
        info = mt5.symbol_info(symbol)
        if info is None:
            continue
        pip = pip_size_for(info)
        for step_pips in args.step_pips:
            step_px = float(step_pips) * float(pip)
            for mode in [str(mode) for mode in args.modes]:
                for trigger_steps in args.oscillation_trigger_steps:
                    for close_steps in args.oscillation_close_steps:
                        for max_oscillation in args.max_oscillation_per_side_values:
                            for reanchor_steps in args.reanchor_threshold_steps:
                                label = (
                                    f"locked_{mode}_step{step_pips:g}pip"
                                    f"_trigger{int(trigger_steps)}"
                                    f"_close{int(close_steps)}"
                                    f"_osc{int(max_oscillation)}"
                                    f"_reanchor{int(reanchor_steps)}"
                                )
                                contracts.append(
                                    LockedHedgeContract(
                                        symbol=symbol,
                                        timeframe=str(args.timeframe),
                                        step_px=step_px,
                                        mode=mode,
                                        oscillation_trigger_steps=int(trigger_steps),
                                        oscillation_close_steps=int(close_steps),
                                        max_oscillation_per_side=int(max_oscillation),
                                        reanchor_threshold_steps=int(reanchor_steps),
                                        variant_label=label,
                                    )
                                )
    return contracts


def _open_locked_base(anchor: float, step_px: float, mode: str, bar_time: int) -> list[HedgeTicket]:
    if mode == "full_hedge":
        return [
            HedgeTicket(direction="BUY", entry_price=anchor, opened_time=bar_time, ticket_kind="locked"),
            HedgeTicket(direction="SELL", entry_price=anchor, opened_time=bar_time, ticket_kind="locked"),
        ]
    return [
        HedgeTicket(direction="SELL", entry_price=anchor, opened_time=bar_time, ticket_kind="locked"),
        HedgeTicket(direction="BUY", entry_price=anchor + step_px, opened_time=bar_time, ticket_kind="locked"),
    ]


def _floating_pnl_usd(
    *,
    symbol: str,
    symbol_info,
    tickets: list[HedgeTicket],
    price: float,
    spread_px: float,
) -> float:
    total = 0.0
    for ticket in tickets:
        total += research_unit_pnl_usd(
            symbol,
            ticket.direction,
            float(ticket.entry_price),
            float(price),
            spread_px,
            symbol_info,
        )
    return float(total)


def _close_ticket(
    *,
    symbol: str,
    symbol_info,
    tickets: list[HedgeTicket],
    ticket: HedgeTicket,
    price: float,
    spread_px: float,
    stats: dict[str, Any],
) -> None:
    pnl = research_unit_pnl_usd(
        symbol,
        ticket.direction,
        float(ticket.entry_price),
        float(price),
        spread_px,
        symbol_info,
    )
    if ticket in tickets:
        tickets.remove(ticket)
    stats["realized_net_usd"] += pnl
    if pnl > 0.0:
        stats["gross_positive_booked_usd"] += pnl
        stats["wins"] += 1
    stats["realized_closes"] += 1


def simulate_contract(contract: LockedHedgeContract, bars: list[dict[str, Any]], info) -> dict[str, Any]:
    if not bars:
        return {
            "symbol": contract.symbol,
            "timeframe": contract.timeframe,
            "variant_label": contract.variant_label,
            "error": "no_bars",
        }

    spread_px = spread_price(info)
    anchor = float(bars[0]["close"])
    tickets = _open_locked_base(anchor, float(contract.step_px), str(contract.mode), int(bars[0]["time"]))
    high_level = 0
    low_level = 0
    stats: dict[str, Any] = {
        "realized_net_usd": 0.0,
        "gross_positive_booked_usd": 0.0,
        "realized_closes": 0,
        "wins": 0,
        "opens": 0,
        "locked_opens": len(tickets),
        "anchor_resets": 0,
        "max_open_total": len(tickets),
        "max_open_buy": sum(1 for ticket in tickets if ticket.direction == "BUY"),
        "max_open_sell": sum(1 for ticket in tickets if ticket.direction == "SELL"),
        "min_floating_pnl_usd": 0.0,
        "max_floating_pnl_usd": 0.0,
        "min_combined_equity_usd": 0.0,
        "max_combined_equity_usd": 0.0,
        "min_realized_cover_gap_usd": 0.0,
        "min_combined_equity_delta_usd": 0.0,
        "realized_cover_violation_bars": 0,
    }

    for bar in bars:
        bar_time = int(bar["time"])
        path = _segment_path(bar)
        for start, end in zip(path, path[1:]):
            for level in _cross_up_levels(anchor, start, end, float(contract.step_px), high_level):
                if level >= int(contract.oscillation_trigger_steps):
                    sell_count = sum(
                        1 for ticket in tickets if ticket.direction == "SELL" and ticket.ticket_kind == "oscillation"
                    )
                    entry_price = anchor + (level * float(contract.step_px))
                    existing = any(
                        ticket.direction == "SELL"
                        and ticket.ticket_kind == "oscillation"
                        and abs(float(ticket.entry_price) - float(entry_price)) < 1e-12
                        for ticket in tickets
                    )
                    if sell_count < int(contract.max_oscillation_per_side) and not existing:
                        tickets.append(
                            HedgeTicket(
                                direction="SELL",
                                entry_price=entry_price,
                                opened_time=bar_time,
                                ticket_kind="oscillation",
                            )
                        )
                        stats["opens"] += 1
                high_level = max(high_level, level)

            for level in _cross_down_levels(anchor, start, end, float(contract.step_px), low_level):
                if level >= int(contract.oscillation_trigger_steps):
                    buy_count = sum(
                        1 for ticket in tickets if ticket.direction == "BUY" and ticket.ticket_kind == "oscillation"
                    )
                    entry_price = anchor - (level * float(contract.step_px))
                    existing = any(
                        ticket.direction == "BUY"
                        and ticket.ticket_kind == "oscillation"
                        and abs(float(ticket.entry_price) - float(entry_price)) < 1e-12
                        for ticket in tickets
                    )
                    if buy_count < int(contract.max_oscillation_per_side) and not existing:
                        tickets.append(
                            HedgeTicket(
                                direction="BUY",
                                entry_price=entry_price,
                                opened_time=bar_time,
                                ticket_kind="oscillation",
                            )
                        )
                        stats["opens"] += 1
                low_level = max(low_level, level)

            floating_pnl = _floating_pnl_usd(
                symbol=contract.symbol,
                symbol_info=info,
                tickets=tickets,
                price=end,
                spread_px=spread_px,
            )
            stats["min_floating_pnl_usd"] = min(float(stats["min_floating_pnl_usd"]), floating_pnl)
            stats["max_floating_pnl_usd"] = max(float(stats["max_floating_pnl_usd"]), floating_pnl)
            combined_equity = float(stats["realized_net_usd"]) + floating_pnl
            stats["min_combined_equity_usd"] = min(float(stats["min_combined_equity_usd"]), combined_equity)
            stats["max_combined_equity_usd"] = max(float(stats["max_combined_equity_usd"]), combined_equity)
            realized_cover_gap = float(stats["realized_net_usd"]) - abs(min(0.0, floating_pnl))
            stats["min_realized_cover_gap_usd"] = min(float(stats["min_realized_cover_gap_usd"]), realized_cover_gap)
            stats["min_combined_equity_delta_usd"] = min(
                float(stats["min_combined_equity_delta_usd"]),
                combined_equity,
            )
            if realized_cover_gap < 0.0:
                stats["realized_cover_violation_bars"] += 1

            oscillation_rows = []
            for ticket in list(tickets):
                if ticket.ticket_kind != "oscillation":
                    continue
                pnl = research_unit_pnl_usd(
                    contract.symbol,
                    ticket.direction,
                    float(ticket.entry_price),
                    float(end),
                    spread_px,
                    info,
                )
                if pnl <= 0.0:
                    continue
                if ticket.direction == "SELL":
                    close_threshold = float(ticket.entry_price) - (
                        float(contract.step_px) * float(contract.oscillation_close_steps)
                    )
                    if float(end) > close_threshold:
                        continue
                else:
                    close_threshold = float(ticket.entry_price) + (
                        float(contract.step_px) * float(contract.oscillation_close_steps)
                    )
                    if float(end) < close_threshold:
                        continue
                oscillation_rows.append(ticket)
            for ticket in oscillation_rows:
                _close_ticket(
                    symbol=contract.symbol,
                    symbol_info=info,
                    tickets=tickets,
                    ticket=ticket,
                    price=end,
                    spread_px=spread_px,
                    stats=stats,
                )

            if abs(float(end) - float(anchor)) >= float(contract.reanchor_threshold_steps) * float(contract.step_px):
                locked = [ticket for ticket in tickets if ticket.ticket_kind == "locked"]
                for ticket in locked:
                    _close_ticket(
                        symbol=contract.symbol,
                        symbol_info=info,
                        tickets=tickets,
                        ticket=ticket,
                        price=end,
                        spread_px=spread_px,
                        stats=stats,
                    )
                anchor = float(end)
                tickets.extend(_open_locked_base(anchor, float(contract.step_px), str(contract.mode), bar_time))
                stats["locked_opens"] += 2
                stats["anchor_resets"] += 1
                high_level = 0
                low_level = 0

        open_sell = sum(1 for ticket in tickets if ticket.direction == "SELL")
        open_buy = sum(1 for ticket in tickets if ticket.direction == "BUY")
        open_total = open_sell + open_buy
        stats["max_open_total"] = max(int(stats["max_open_total"]), open_total)
        stats["max_open_sell"] = max(int(stats["max_open_sell"]), open_sell)
        stats["max_open_buy"] = max(int(stats["max_open_buy"]), open_buy)

    duration_hours = max(1e-9, (len(bars) * TIMEFRAME_SECONDS[contract.timeframe]) / 3600.0)
    realized_net_usd = float(stats["realized_net_usd"])
    gross_positive_booked_usd = float(stats["gross_positive_booked_usd"])
    realized_closes = int(stats["realized_closes"])
    return {
        "symbol": contract.symbol,
        "timeframe": contract.timeframe,
        "variant_label": contract.variant_label,
        "step_px": round(contract.step_px, 8),
        "step_pips": round(contract.step_px / pip_size_for(info), 3),
        "mode": str(contract.mode),
        "oscillation_trigger_steps": int(contract.oscillation_trigger_steps),
        "oscillation_close_steps": int(contract.oscillation_close_steps),
        "max_oscillation_per_side": int(contract.max_oscillation_per_side),
        "reanchor_threshold_steps": int(contract.reanchor_threshold_steps),
        "opens": int(stats["opens"]),
        "locked_opens": int(stats["locked_opens"]),
        "realized_closes": realized_closes,
        "wins": int(stats["wins"]),
        "anchor_resets": int(stats["anchor_resets"]),
        "realized_net_usd": round(realized_net_usd, 2),
        "gross_positive_booked_usd": round(gross_positive_booked_usd, 2),
        "gross_positive_booked_usd_per_hour": round(gross_positive_booked_usd / duration_hours, 3),
        "realized_usd_per_hour": round(realized_net_usd / duration_hours, 3),
        "avg_close_usd": round(realized_net_usd / realized_closes, 4) if realized_closes else 0.0,
        "max_open_total": int(stats["max_open_total"]),
        "max_open_sell": int(stats["max_open_sell"]),
        "max_open_buy": int(stats["max_open_buy"]),
        "min_floating_pnl_usd": round(float(stats["min_floating_pnl_usd"]), 2),
        "max_floating_pnl_usd": round(float(stats["max_floating_pnl_usd"]), 2),
        "min_combined_equity_usd": round(float(stats["min_combined_equity_usd"]), 2),
        "max_combined_equity_usd": round(float(stats["max_combined_equity_usd"]), 2),
        "max_adverse_excursion_usd": round(float(stats["min_floating_pnl_usd"]), 2),
        "final_open_count": len(tickets),
        "min_realized_cover_gap_usd": round(float(stats["min_realized_cover_gap_usd"]), 2),
        "min_combined_equity_delta_usd": round(float(stats["min_combined_equity_delta_usd"]), 2),
        "realized_cover_violation_bars": int(stats["realized_cover_violation_bars"]),
    }


def write_outputs(rows: list[dict[str, Any]], args: argparse.Namespace) -> None:
    csv_path = Path(str(args.output_csv))
    json_path = Path(str(args.output_json))
    md_path = Path(str(args.output_md))
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.parent.mkdir(parents=True, exist_ok=True)
    md_path.parent.mkdir(parents=True, exist_ok=True)

    with json_path.open("w", encoding="utf-8") as handle:
        json.dump(rows, handle, indent=2)

    if rows:
        with csv_path.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=list(rows[0].keys()))
            writer.writeheader()
            writer.writerows(rows)

    lines = [
        "# Locked Hedge Lattice Study",
        "",
        f"- Generated: `{utc_now_iso()}`",
        f"- Symbols: `{', '.join(str(s).upper() for s in args.symbols)}`",
        f"- Timeframe: `{args.timeframe}`",
        f"- Days: `{args.days}`",
        "",
    ]
    if rows:
        best = rows[0]
        lines.extend(
            [
                "## Best Row",
                "",
                f"- Variant: `{best['variant_label']}`",
                f"- Gross positive booked USD / hour: `${best['gross_positive_booked_usd_per_hour']}`",
                f"- Min floating / min combined equity: `${best['min_floating_pnl_usd']}` / `${best['min_combined_equity_usd']}`",
                f"- Max open total / final open: `{best['max_open_total']}` / `{best['final_open_count']}`",
                "",
                "## Rows",
                "",
                "| Variant | $+/h | Min Float | Min Equity | Closes | Max Open |",
                "| --- | ---: | ---: | ---: | ---: | ---: |",
            ]
        )
        for row in rows[:20]:
            lines.append(
                f"| `{row['variant_label']}` | `${row['gross_positive_booked_usd_per_hour']}` | "
                f"`${row['min_floating_pnl_usd']}` | `${row['min_combined_equity_usd']}` | "
                f"`{row['realized_closes']}` | `{row['max_open_total']}` |"
            )
    else:
        lines.extend(["No rows satisfied the requested constraints."])

    md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    args = parse_args()
    if not mt5.initialize():
        raise SystemExit("MetaTrader5 initialize() failed")

    try:
        contracts = build_contracts(args)
        rows: list[dict[str, Any]] = []
        for contract in contracts:
            info = mt5.symbol_info(contract.symbol)
            if info is None:
                continue
            bars = load_bars(contract.symbol, contract.timeframe, int(args.days))
            rows.append(simulate_contract(contract, bars, info))
        rows = [row for row in rows if "error" not in row]
        rows = filter_rows(rows, args)
        rows.sort(key=lambda row: score_key(row, rank_mode=str(args.rank_mode)), reverse=True)
        write_outputs(rows, args)
        print(json.dumps(rows[:20], indent=2))
    finally:
        mt5.shutdown()


if __name__ == "__main__":
    main()
