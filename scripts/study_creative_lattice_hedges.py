#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import MetaTrader5 as mt5

from penetration_lattice_lab_v2 import spread_price, unit_pnl_usd
from study_creative_lattice_families import (
    VARIANTS,
    _entry_levels,
    _exit_price,
    _exit_touched,
    load_bars,
    load_live_crypto_configs,
)


ROOT = Path(__file__).resolve().parent.parent
DEFAULT_OUTPUT_CSV = ROOT / "reports" / "creative_lattice_hedge_study.csv"
DEFAULT_OUTPUT_MD = ROOT / "reports" / "creative_lattice_hedge_study.md"
DEFAULT_SYMBOLS = ["SOLUSD", "XRPUSD", "ADAUSD", "LTCUSD"]
DEFAULT_VARIANTS = ["vwap20_touch", "vwap50_touch", "ema20_touch", "ribbon_mid_touch", "ribbon_outerplus1_touch"]
DEFAULT_STEP_MULTIPLIERS = [1.0, 1.5]
DEFAULT_HEDGE_MODES = ["none", "same_level", "depth_threshold3"]


@dataclass
class Ticket:
    direction: str
    entry_price: float
    opened_idx: int
    entry_center: float
    ticket_kind: str = "core"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Focused hedge tournament for the strongest creative crypto lattice families."
    )
    parser.add_argument("--symbols", nargs="*", default=DEFAULT_SYMBOLS)
    parser.add_argument("--days", type=int, default=30)
    parser.add_argument("--variants", nargs="*", default=DEFAULT_VARIANTS)
    parser.add_argument("--step-multipliers", nargs="*", type=float, default=DEFAULT_STEP_MULTIPLIERS)
    parser.add_argument("--hedge-modes", nargs="*", default=DEFAULT_HEDGE_MODES)
    parser.add_argument("--output-csv", default=str(DEFAULT_OUTPUT_CSV))
    parser.add_argument("--output-md", default=str(DEFAULT_OUTPUT_MD))
    return parser.parse_args()


def _same_direction_core_depth(tickets: list[Ticket], direction: str) -> int:
    return sum(1 for ticket in tickets if ticket.direction == direction and ticket.ticket_kind == "core")


def _maybe_add_hedge(
    *,
    tickets: list[Ticket],
    level_direction: str,
    entry_price: float,
    entry_center: float,
    idx: int,
    max_open_per_side: int,
    hedge_mode: str,
) -> None:
    if hedge_mode == "none":
        return
    opposite = "BUY" if level_direction == "SELL" else "SELL"
    if sum(1 for ticket in tickets if ticket.direction == opposite) >= max_open_per_side:
        return
    if hedge_mode == "depth_threshold3" and _same_direction_core_depth(tickets, level_direction) < 3:
        return
    tickets.append(
        Ticket(
            direction=opposite,
            entry_price=entry_price,
            opened_idx=idx,
            entry_center=entry_center,
            ticket_kind="hedge",
        )
    )


def simulate_variant_with_hedge(
    *,
    symbol: str,
    bars: list[dict[str, Any]],
    symbol_info: Any,
    variant,
    step_px: float,
    max_open_per_side: int,
    hedge_mode: str,
) -> dict[str, Any]:
    if not bars:
        return {}
    spread_px = spread_price(symbol_info)
    from study_creative_lattice_families import compute_ema_series, compute_vwap_series

    ema_periods = {variant.primary_period, variant.secondary_period}
    vwap_periods = {variant.primary_period}
    series: dict[str, list[float]] = {}
    for period in ema_periods:
        if period > 0:
            series[f"ema_{period}"] = compute_ema_series(bars, period)
    for period in vwap_periods:
        if period > 0:
            series[f"vwap_{period}"] = compute_vwap_series(bars, period)

    tickets: list[Ticket] = []
    realized_net = 0.0
    realized_closes = 0
    max_open_total = 0
    max_open_buy = 0
    max_open_sell = 0
    min_floating_net = 0.0

    for idx in range(1, len(bars)):
        bar = bars[idx]
        center, first_sell, first_buy, entry_center = _entry_levels(variant=variant, idx=idx, step_px=step_px, series=series)
        if center <= 0 or step_px <= 0:
            continue

        sell_count = sum(1 for t in tickets if t.direction == "SELL")
        buy_count = sum(1 for t in tickets if t.direction == "BUY")

        sell_steps_reached = 0
        if float(bar["high"]) >= first_sell:
            sell_steps_reached = int(((float(bar["high"]) - first_sell) / step_px) + 1.0000001)
        buy_steps_reached = 0
        if float(bar["low"]) <= first_buy:
            buy_steps_reached = int(((first_buy - float(bar["low"])) / step_px) + 1.0000001)

        target_sell_count = min(sell_steps_reached, max_open_per_side)
        target_buy_count = min(buy_steps_reached, max_open_per_side)

        while sell_count < target_sell_count:
            level_idx = sell_count
            entry_price = first_sell + (level_idx * step_px)
            tickets.append(
                Ticket(
                    direction="SELL",
                    entry_price=entry_price,
                    opened_idx=idx,
                    entry_center=entry_center,
                    ticket_kind="core",
                )
            )
            sell_count += 1
            _maybe_add_hedge(
                tickets=tickets,
                level_direction="SELL",
                entry_price=entry_price,
                entry_center=entry_center,
                idx=idx,
                max_open_per_side=max_open_per_side,
                hedge_mode=hedge_mode,
            )

        while buy_count < target_buy_count:
            level_idx = buy_count
            entry_price = first_buy - (level_idx * step_px)
            tickets.append(
                Ticket(
                    direction="BUY",
                    entry_price=entry_price,
                    opened_idx=idx,
                    entry_center=entry_center,
                    ticket_kind="core",
                )
            )
            buy_count += 1
            _maybe_add_hedge(
                tickets=tickets,
                level_direction="BUY",
                entry_price=entry_price,
                entry_center=entry_center,
                idx=idx,
                max_open_per_side=max_open_per_side,
                hedge_mode=hedge_mode,
            )

        for ticket in list(tickets):
            if ticket.opened_idx >= idx:
                continue
            exit_price = _exit_price(ticket, variant, idx, step_px, series)
            if not _exit_touched(ticket, exit_price, bar):
                continue
            pnl = unit_pnl_usd(symbol, ticket.direction, ticket.entry_price, exit_price, spread_px)
            if pnl > 0:
                realized_net += pnl
                realized_closes += 1
                tickets.remove(ticket)

        mark_price = float(bar["close"])
        floating_net = sum(unit_pnl_usd(symbol, t.direction, t.entry_price, mark_price, spread_px) for t in tickets)
        min_floating_net = min(min_floating_net, floating_net)

        open_buy = sum(1 for t in tickets if t.direction == "BUY")
        open_sell = sum(1 for t in tickets if t.direction == "SELL")
        max_open_buy = max(max_open_buy, open_buy)
        max_open_sell = max(max_open_sell, open_sell)
        max_open_total = max(max_open_total, len(tickets))

    last_close = float(bars[-1]["close"])
    floating_net = sum(unit_pnl_usd(symbol, t.direction, t.entry_price, last_close, spread_px) for t in tickets)
    total_hours = (bars[-1]["time"] - bars[0]["time"]) / 3600.0 if len(bars) > 1 else 0.0
    usd_per_hour = realized_net / total_hours if total_hours > 0 else 0.0
    closes_per_hour = realized_closes / total_hours if total_hours > 0 else 0.0

    return {
        "realized_closes": realized_closes,
        "realized_net_usd": round(realized_net, 3),
        "floating_net_usd": round(floating_net, 3),
        "min_floating_net_usd": round(min_floating_net, 3),
        "combined_net_usd": round(realized_net + floating_net, 3),
        "usd_per_hour": round(usd_per_hour, 4),
        "closes_per_hour": round(closes_per_hour, 4),
        "open_tickets_left": len(tickets),
        "max_open_total": max_open_total,
        "max_open_buy": max_open_buy,
        "max_open_sell": max_open_sell,
    }


def build_markdown(rows: list[dict[str, Any]]) -> str:
    lines = [
        "# Creative Lattice Hedge Study",
        "",
        "Focused follow-up on the strongest raw creative crypto families, adding profitable-only hedge overlays.",
        "",
        "## Best Per Symbol",
        "",
        "| Symbol | Variant | Hedge | Step Mult | $/h | Realized | Final Float | Worst Float | Closes/h | Max Open |",
        "| --- | --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    best_by_symbol: dict[str, dict[str, Any]] = {}
    for row in rows:
        symbol = str(row["symbol"])
        current = best_by_symbol.get(symbol)
        if current is None or float(row["usd_per_hour"]) > float(current["usd_per_hour"]):
            best_by_symbol[symbol] = row
    for symbol in sorted(best_by_symbol):
        row = best_by_symbol[symbol]
        lines.append(
            f"| {row['symbol']} | {row['variant']} | {row['hedge_mode']} | {row['step_multiplier']} | {row['usd_per_hour']} | "
            f"{row['realized_net_usd']} | {row['floating_net_usd']} | {row['min_floating_net_usd']} | {row['closes_per_hour']} | {row['max_open_total']} |"
        )
    lines.extend(
        [
            "",
            "## Full Ranking",
            "",
            "| Symbol | Variant | Hedge | Step Mult | $/h | Realized | Final Float | Worst Float | Closes/h | Max Open |",
            "| --- | --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
        ]
    )
    for row in sorted(rows, key=lambda r: (r["symbol"], -float(r["usd_per_hour"]))):
        lines.append(
            f"| {row['symbol']} | {row['variant']} | {row['hedge_mode']} | {row['step_multiplier']} | {row['usd_per_hour']} | "
            f"{row['realized_net_usd']} | {row['floating_net_usd']} | {row['min_floating_net_usd']} | {row['closes_per_hour']} | {row['max_open_total']} |"
        )
    return "\n".join(lines) + "\n"


def main() -> int:
    args = parse_args()
    symbol_filter = set(args.symbols) if args.symbols else None
    lane_configs = load_live_crypto_configs(symbol_filter)
    variant_map = {variant.name: variant for variant in VARIANTS if variant.name in set(args.variants)}
    if not lane_configs or not variant_map:
        print("No matching lanes or variants.")
        return 1

    if not mt5.initialize():
        print("MetaTrader5 initialize() failed")
        return 1

    try:
        rows: list[dict[str, Any]] = []
        for cfg in lane_configs:
            info = mt5.symbol_info(cfg.symbol)
            if info is None:
                continue
            bars = load_bars(cfg.symbol, cfg.timeframe, args.days)
            if not bars:
                continue
            for variant_name, variant in variant_map.items():
                for step_multiplier in args.step_multipliers:
                    for hedge_mode in args.hedge_modes:
                        step_px = cfg.step_px * float(step_multiplier)
                        result = simulate_variant_with_hedge(
                            symbol=cfg.symbol,
                            bars=bars,
                            symbol_info=info,
                            variant=variant,
                            step_px=step_px,
                            max_open_per_side=cfg.max_open_per_side,
                            hedge_mode=hedge_mode,
                        )
                        if not result:
                            continue
                        result.update(
                            {
                                "lane_name": cfg.lane_name,
                                "symbol": cfg.symbol,
                                "timeframe": cfg.timeframe,
                                "variant": variant_name,
                                "step_multiplier": round(float(step_multiplier), 4),
                                "hedge_mode": hedge_mode,
                            }
                        )
                        rows.append(result)
                        print(
                            f"{cfg.symbol:<7} {variant_name:<22} {hedge_mode:<16} x{step_multiplier:<3} "
                            f"$/h={result['usd_per_hour']:+.4f} realized={result['realized_net_usd']:+.2f} "
                            f"float={result['floating_net_usd']:+.2f} worst={result['min_floating_net_usd']:+.2f}"
                        )

        if not rows:
            print("No rows produced.")
            return 1

        out_csv = Path(args.output_csv)
        out_csv.parent.mkdir(parents=True, exist_ok=True)
        with out_csv.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=list(rows[0].keys()))
            writer.writeheader()
            writer.writerows(rows)

        out_md = Path(args.output_md)
        out_md.parent.mkdir(parents=True, exist_ok=True)
        out_md.write_text(build_markdown(rows), encoding="utf-8")
        print(f"Saved {out_csv}")
        print(f"Saved {out_md}")
        return 0
    finally:
        mt5.shutdown()


if __name__ == "__main__":
    raise SystemExit(main())
