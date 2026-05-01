#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
from dataclasses import dataclass
from pathlib import Path

import MetaTrader5 as mt5

from penetration_lattice_hybrid_apex import RawConfig, simulate_raw_close2
from penetration_lattice_lab_v2 import Ticket, dynamic_step, load_bars, pip_size_for, spread_price, unit_pnl_usd


ROOT = Path(__file__).resolve().parent.parent
DEFAULT_SYMBOLS = ["GBPUSD", "EURUSD", "NZDUSD"]


@dataclass(frozen=True)
class CompareConfig:
    symbol: str
    step_pips: float
    max_open_per_side: int
    close_mode: str = "two_level"


def compare_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Compare current raw outside-only lattice entries against an inside-geometry refill variant."
    )
    parser.add_argument("--symbols", nargs="*", default=DEFAULT_SYMBOLS)
    parser.add_argument("--days", type=int, default=60)
    parser.add_argument(
        "--output-csv",
        default=str(ROOT / "reports" / "inside_geometry_churn_benchmark.csv"),
    )
    return parser.parse_args()


def default_raw_configs() -> dict[str, CompareConfig]:
    return {
        "GBPUSD": CompareConfig(symbol="GBPUSD", step_pips=2.0, max_open_per_side=20),
        "EURUSD": CompareConfig(symbol="EURUSD", step_pips=3.0, max_open_per_side=20),
        "NZDUSD": CompareConfig(symbol="NZDUSD", step_pips=1.5, max_open_per_side=12),
    }


def _side_count(tickets: list[Ticket], direction: str) -> int:
    return sum(1 for t in tickets if t.direction == direction)


def _open_repeated_interior_levels(
    *,
    bar: dict,
    idx: int,
    anchor: float,
    base_step_px: float,
    tickets: list[Ticket],
    direction: str,
    max_open_per_side: int,
) -> None:
    side = [t for t in tickets if t.direction == direction]
    if not side:
        return
    open_count = len(side)
    if open_count >= max_open_per_side:
        return

    if direction == "SELL":
        outer = max(t.entry_price for t in side)
        max_idx = int(round((outer - anchor) / base_step_px))
        for level_idx in range(1, max_idx):
            if open_count >= max_open_per_side:
                break
            level = anchor + (level_idx * base_step_px)
            if bar["high"] >= level:
                tickets.append(Ticket(direction="SELL", entry_price=level, opened_idx=idx))
                open_count += 1
    else:
        outer = min(t.entry_price for t in side)
        max_idx = int(round((anchor - outer) / base_step_px))
        for level_idx in range(1, max_idx):
            if open_count >= max_open_per_side:
                break
            level = anchor - (level_idx * base_step_px)
            if bar["low"] <= level:
                tickets.append(Ticket(direction="BUY", entry_price=level, opened_idx=idx))
                open_count += 1


def simulate_inside_geometry_repeat(symbol: str, bars: list[dict], symbol_info, cfg: RawConfig) -> dict:
    if not bars:
        return {}

    pip_size = pip_size_for(symbol_info)
    spread_px = spread_price(symbol_info)
    base_step_px = cfg.step_pips * pip_size

    anchor = bars[0]["close"]
    next_sell_level = anchor + base_step_px
    next_buy_level = anchor - base_step_px

    open_tickets: list[Ticket] = []
    realized_pnls: list[float] = []
    max_open = 0
    max_open_buy = 0
    max_open_sell = 0
    anchor_resets = 0
    interior_reopens = 0

    for idx in range(1, len(bars)):
        bar = bars[idx]

        open_buy = _side_count(open_tickets, "BUY")
        open_sell = _side_count(open_tickets, "SELL")

        current_sell_step = dynamic_step(
            base_step_px,
            open_sell,
            type(
                "Cfg",
                (),
                {
                    "adaptive_step_threshold_1": 10,
                    "adaptive_step_threshold_2": 20,
                    "adaptive_step_multiplier_1": 1.5,
                    "adaptive_step_multiplier_2": 2.0,
                },
            )(),
        )
        current_buy_step = dynamic_step(
            base_step_px,
            open_buy,
            type(
                "Cfg",
                (),
                {
                    "adaptive_step_threshold_1": 10,
                    "adaptive_step_threshold_2": 20,
                    "adaptive_step_multiplier_1": 1.5,
                    "adaptive_step_multiplier_2": 2.0,
                },
            )(),
        )

        while bar["high"] >= next_sell_level and open_sell < cfg.max_open_per_side:
            open_tickets.append(Ticket(direction="SELL", entry_price=next_sell_level, opened_idx=idx))
            open_sell += 1
            current_sell_step = dynamic_step(
                base_step_px,
                open_sell,
                type(
                    "Cfg",
                    (),
                    {
                        "adaptive_step_threshold_1": 10,
                        "adaptive_step_threshold_2": 20,
                        "adaptive_step_multiplier_1": 1.5,
                        "adaptive_step_multiplier_2": 2.0,
                    },
                )(),
            )
            next_sell_level += current_sell_step

        while bar["low"] <= next_buy_level and open_buy < cfg.max_open_per_side:
            open_tickets.append(Ticket(direction="BUY", entry_price=next_buy_level, opened_idx=idx))
            open_buy += 1
            current_buy_step = dynamic_step(
                base_step_px,
                open_buy,
                type(
                    "Cfg",
                    (),
                    {
                        "adaptive_step_threshold_1": 10,
                        "adaptive_step_threshold_2": 20,
                        "adaptive_step_multiplier_1": 1.5,
                        "adaptive_step_multiplier_2": 2.0,
                    },
                )(),
            )
            next_buy_level -= current_buy_step

        before_refill = len(open_tickets)
        _open_repeated_interior_levels(
            bar=bar,
            idx=idx,
            anchor=anchor,
            base_step_px=base_step_px,
            tickets=open_tickets,
            direction="SELL",
            max_open_per_side=cfg.max_open_per_side,
        )
        _open_repeated_interior_levels(
            bar=bar,
            idx=idx,
            anchor=anchor,
            base_step_px=base_step_px,
            tickets=open_tickets,
            direction="BUY",
            max_open_per_side=cfg.max_open_per_side,
        )
        interior_reopens += max(0, len(open_tickets) - before_refill)

        gap = 1 if cfg.close_mode == "one_level" else 2

        sells = sorted((t for t in open_tickets if t.direction == "SELL"), key=lambda t: t.entry_price, reverse=True)
        while len(sells) > gap and bar["low"] <= sells[gap].entry_price:
            outer = sells[0]
            close_ref = sells[gap].entry_price
            realized_pnls.append(unit_pnl_usd(symbol, "SELL", outer.entry_price, close_ref, spread_px))
            open_tickets.remove(outer)
            sells = sorted((t for t in open_tickets if t.direction == "SELL"), key=lambda t: t.entry_price, reverse=True)

        buys = sorted((t for t in open_tickets if t.direction == "BUY"), key=lambda t: t.entry_price)
        while len(buys) > gap and bar["high"] >= buys[gap].entry_price:
            outer = buys[0]
            close_ref = buys[gap].entry_price
            realized_pnls.append(unit_pnl_usd(symbol, "BUY", outer.entry_price, close_ref, spread_px))
            open_tickets.remove(outer)
            buys = sorted((t for t in open_tickets if t.direction == "BUY"), key=lambda t: t.entry_price)

        max_open = max(max_open, len(open_tickets))
        max_open_buy = max(max_open_buy, _side_count(open_tickets, "BUY"))
        max_open_sell = max(max_open_sell, _side_count(open_tickets, "SELL"))

        if not open_tickets and abs(bar["close"] - anchor) >= base_step_px:
            anchor = bar["close"]
            next_sell_level = anchor + base_step_px
            next_buy_level = anchor - base_step_px
            anchor_resets += 1

    last_close = bars[-1]["close"]
    floating_pnls = [
        unit_pnl_usd(symbol, t.direction, t.entry_price, last_close, spread_px)
        for t in open_tickets
    ]

    realized_net = sum(realized_pnls)
    floating_net = sum(floating_pnls)
    combined_net = realized_net + floating_net
    total_closes = len(realized_pnls)
    wins = sum(1 for p in realized_pnls if p > 0)

    return {
        "mode": "inside_geometry_repeat",
        "realized_closes": len(realized_pnls),
        "total_closes": total_closes,
        "wr_pct": round(wins / total_closes * 100.0, 1) if total_closes else 0.0,
        "realized_net_usd": round(realized_net, 3),
        "open_tickets_left": len(open_tickets),
        "floating_net_usd": round(floating_net, 3),
        "worst_floating_usd": round(min(floating_pnls), 3) if floating_pnls else 0.0,
        "combined_net_usd": round(combined_net, 3),
        "max_open_total": max_open,
        "max_open_buy": max_open_buy,
        "max_open_sell": max_open_sell,
        "anchor_resets": anchor_resets,
        "interior_reopens": interior_reopens,
    }


def main() -> int:
    args = compare_args()
    cfg_map = default_raw_configs()

    if not mt5.initialize():
        print("MetaTrader5 initialize() failed")
        return 1

    try:
        rows: list[dict] = []
        total_baseline = 0.0
        total_refill = 0.0
        for symbol in args.symbols:
            if symbol not in cfg_map:
                continue
            info = mt5.symbol_info(symbol)
            if info is None:
                continue
            bars = load_bars(symbol, args.days)
            raw_cfg = RawConfig(
                step_pips=cfg_map[symbol].step_pips,
                max_open_per_side=cfg_map[symbol].max_open_per_side,
                close_mode=cfg_map[symbol].close_mode,
            )
            baseline = simulate_raw_close2(symbol, bars, info, raw_cfg)
            repeat = simulate_inside_geometry_repeat(symbol, bars, info, raw_cfg)
            if not baseline or not repeat:
                continue

            total_baseline += float(baseline["combined_net_usd"])
            total_refill += float(repeat["combined_net_usd"])

            rows.append(
                {
                    "symbol": symbol,
                    "days": args.days,
                    "step_pips": raw_cfg.step_pips,
                    "max_open_per_side": raw_cfg.max_open_per_side,
                    "baseline_combined_usd": baseline["combined_net_usd"],
                    "baseline_realized_usd": baseline["realized_net_usd"],
                    "baseline_floating_usd": baseline["floating_net_usd"],
                    "baseline_closes": baseline["realized_closes"],
                    "baseline_max_open": baseline["max_open_total"],
                    "repeat_combined_usd": repeat["combined_net_usd"],
                    "repeat_realized_usd": repeat["realized_net_usd"],
                    "repeat_floating_usd": repeat["floating_net_usd"],
                    "repeat_closes": repeat["realized_closes"],
                    "repeat_max_open": repeat["max_open_total"],
                    "repeat_interior_reopens": repeat["interior_reopens"],
                    "delta_combined_usd": round(repeat["combined_net_usd"] - baseline["combined_net_usd"], 3),
                }
            )

        out_path = Path(args.output_csv)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        with out_path.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=list(rows[0].keys()) if rows else [
                "symbol",
                "days",
                "step_pips",
                "max_open_per_side",
                "baseline_combined_usd",
                "baseline_realized_usd",
                "baseline_floating_usd",
                "baseline_closes",
                "baseline_max_open",
                "repeat_combined_usd",
                "repeat_realized_usd",
                "repeat_floating_usd",
                "repeat_closes",
                "repeat_max_open",
                "repeat_interior_reopens",
                "delta_combined_usd",
            ])
            writer.writeheader()
            writer.writerows(rows)

        print(f"Wrote {out_path}")
        print(f"Baseline total USD: {round(total_baseline, 3)}")
        print(f"Refill total USD: {round(total_refill, 3)}")
        print(f"Delta total USD: {round(total_refill - total_baseline, 3)}")
        return 0
    finally:
        mt5.shutdown()


if __name__ == "__main__":
    raise SystemExit(main())
