#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
from dataclasses import dataclass
from pathlib import Path

import MetaTrader5 as mt5

from benchmark_inside_geometry_churn import default_raw_configs
from live_penetration_lattice_shadow import _apply_close_realism, _bar_reaches_price_level
from penetration_lattice_lab_v2 import Ticket, dynamic_step, load_bars, pip_size_for, spread_price, unit_pnl_usd


ROOT = Path(__file__).resolve().parent.parent
DEFAULT_OUTPUT_CSV = ROOT / "reports" / "fx_fixed_shape_side_gap.csv"
DEFAULT_OUTPUT_MD = ROOT / "reports" / "fx_fixed_shape_side_gap.md"


@dataclass(frozen=True)
class FixedShapeSpec:
    symbol: str
    step_sell: float
    step_buy: float
    close_alpha: float
    close_style: str
    reference_sell_gap: int
    reference_buy_gap: int


@dataclass(frozen=True)
class RealismMode:
    name: str
    open_mode: str
    close_mode: str


FIXED_SHAPES: dict[str, FixedShapeSpec] = {
    "GBPUSD": FixedShapeSpec(
        symbol="GBPUSD",
        step_sell=0.5,
        step_buy=1.0,
        close_alpha=0.5,
        close_style="all_profitable",
        reference_sell_gap=1,
        reference_buy_gap=1,
    ),
    "EURUSD": FixedShapeSpec(
        symbol="EURUSD",
        step_sell=1.0,
        step_buy=1.0,
        close_alpha=0.5,
        close_style="outer",
        reference_sell_gap=2,
        reference_buy_gap=2,
    ),
}

GAP_VALUES = [1, 2, 3]

REALISM_MODES = [
    RealismMode(name="intrabar_intrabar", open_mode="intrabar", close_mode="intrabar"),
    RealismMode(name="broker_touch_bar_close", open_mode="broker_touch", close_mode="bar_close"),
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Benchmark side-gap asymmetry on surviving fixed-shape FX winners.")
    parser.add_argument("--symbols", nargs="*", default=["GBPUSD", "EURUSD"])
    parser.add_argument("--days", type=int, default=60)
    parser.add_argument("--output-csv", default=str(DEFAULT_OUTPUT_CSV))
    parser.add_argument("--output-md", default=str(DEFAULT_OUTPUT_MD))
    return parser.parse_args()


def _interp_close(level_price: float, bar_extreme: float, alpha: float) -> float:
    return level_price + alpha * (bar_extreme - level_price)


def simulate_fixed_shape_gap(
    symbol: str,
    bars: list[dict],
    symbol_info,
    *,
    step_sell: float,
    step_buy: float,
    max_open_per_side: int,
    close_alpha: float,
    close_style: str,
    sell_gap: int,
    buy_gap: int,
    open_realism_mode: str,
    close_realism_mode: str,
) -> dict[str, float | int]:
    if not bars:
        return {}

    pip_size = pip_size_for(symbol_info)
    spread_px = spread_price(symbol_info)
    base_step_sell_px = step_sell * pip_size
    base_step_buy_px = step_buy * pip_size

    anchor = bars[0]["close"]
    next_sell_level = anchor + base_step_sell_px
    next_buy_level = anchor - base_step_buy_px

    open_tickets: list[Ticket] = []
    realized_pnls: list[float] = []
    open_events = 0
    close_events = 0
    same_bar_roundtrips = 0
    max_open = 0

    adapt_cfg = type(
        "Cfg",
        (),
        {
            "adaptive_step_threshold_1": 10,
            "adaptive_step_threshold_2": 20,
            "adaptive_step_multiplier_1": 1.5,
            "adaptive_step_multiplier_2": 2.0,
        },
    )()

    def select_positions(side_len: int, profitable_positions: list[int], gap: int) -> list[int]:
        if side_len <= gap:
            return []
        if close_style == "outer":
            return [0]
        if close_style == "inner":
            return [max(0, gap - 1)]
        if close_style == "all_profitable":
            return list(profitable_positions)
        raise ValueError(f"Unsupported close style: {close_style}")

    for idx in range(1, len(bars)):
        bar = bars[idx]

        open_buy = sum(1 for t in open_tickets if t.direction == "BUY")
        open_sell = sum(1 for t in open_tickets if t.direction == "SELL")
        current_sell_step = dynamic_step(base_step_sell_px, open_sell, adapt_cfg)
        current_buy_step = dynamic_step(base_step_buy_px, open_buy, adapt_cfg)

        while (
            _bar_reaches_price_level("SELL", next_sell_level, bar, spread_px=spread_px, mode=open_realism_mode, purpose="open")
            and open_sell < max_open_per_side
        ):
            open_tickets.append(Ticket(direction="SELL", entry_price=next_sell_level, opened_idx=idx))
            open_sell += 1
            open_events += 1
            current_sell_step = dynamic_step(base_step_sell_px, open_sell, adapt_cfg)
            next_sell_level += current_sell_step

        while (
            _bar_reaches_price_level("BUY", next_buy_level, bar, spread_px=spread_px, mode=open_realism_mode, purpose="open")
            and open_buy < max_open_per_side
        ):
            open_tickets.append(Ticket(direction="BUY", entry_price=next_buy_level, opened_idx=idx))
            open_buy += 1
            open_events += 1
            current_buy_step = dynamic_step(base_step_buy_px, open_buy, adapt_cfg)
            next_buy_level -= current_buy_step

        sells = sorted((t for t in open_tickets if t.direction == "SELL"), key=lambda t: t.entry_price, reverse=True)
        while (
            len(sells) > sell_gap
            and _bar_reaches_price_level("SELL", sells[sell_gap].entry_price, bar, spread_px=spread_px, mode=open_realism_mode, purpose="close")
        ):
            level_price = sells[sell_gap].entry_price
            close_ref = _interp_close(level_price, float(bar["low"]), close_alpha)
            close_ref = _apply_close_realism("SELL", close_ref, bar, close_realism_mode)
            profitable_positions = [
                pos
                for pos, ticket in enumerate(sells)
                if unit_pnl_usd(symbol, "SELL", ticket.entry_price, close_ref, spread_px) > 0
            ]
            close_positions = sorted(set(select_positions(len(sells), profitable_positions, sell_gap)), reverse=True)
            if not close_positions:
                break
            closed_any = False
            for pos in close_positions:
                ticket = sells[pos]
                pnl = unit_pnl_usd(symbol, "SELL", ticket.entry_price, close_ref, spread_px)
                if pnl <= 0:
                    continue
                realized_pnls.append(pnl)
                close_events += 1
                if int(ticket.opened_idx) == idx:
                    same_bar_roundtrips += 1
                open_tickets.remove(ticket)
                closed_any = True
            if not closed_any:
                break
            sells = sorted((t for t in open_tickets if t.direction == "SELL"), key=lambda t: t.entry_price, reverse=True)

        buys = sorted((t for t in open_tickets if t.direction == "BUY"), key=lambda t: t.entry_price)
        while (
            len(buys) > buy_gap
            and _bar_reaches_price_level("BUY", buys[buy_gap].entry_price, bar, spread_px=spread_px, mode=open_realism_mode, purpose="close")
        ):
            level_price = buys[buy_gap].entry_price
            close_ref = _interp_close(level_price, float(bar["high"]), close_alpha)
            close_ref = _apply_close_realism("BUY", close_ref, bar, close_realism_mode)
            profitable_positions = [
                pos
                for pos, ticket in enumerate(buys)
                if unit_pnl_usd(symbol, "BUY", ticket.entry_price, close_ref, spread_px) > 0
            ]
            close_positions = sorted(set(select_positions(len(buys), profitable_positions, buy_gap)), reverse=True)
            if not close_positions:
                break
            closed_any = False
            for pos in close_positions:
                ticket = buys[pos]
                pnl = unit_pnl_usd(symbol, "BUY", ticket.entry_price, close_ref, spread_px)
                if pnl <= 0:
                    continue
                realized_pnls.append(pnl)
                close_events += 1
                if int(ticket.opened_idx) == idx:
                    same_bar_roundtrips += 1
                open_tickets.remove(ticket)
                closed_any = True
            if not closed_any:
                break
            buys = sorted((t for t in open_tickets if t.direction == "BUY"), key=lambda t: t.entry_price)

        max_open = max(max_open, len(open_tickets))

        if not open_tickets and (
            float(bar["close"]) >= float(anchor) + base_step_sell_px or float(bar["close"]) <= float(anchor) - base_step_buy_px
        ):
            anchor = float(bar["close"])
            next_sell_level = anchor + base_step_sell_px
            next_buy_level = anchor - base_step_buy_px

    last_close = bars[-1]["close"]
    floating_pnls = [
        unit_pnl_usd(symbol, t.direction, t.entry_price, last_close, spread_px)
        for t in open_tickets
    ]
    realized_net = sum(realized_pnls)
    floating_net = sum(floating_pnls)
    combined_net = realized_net + floating_net
    closes = len(realized_pnls)
    return {
        "combined_net_usd": round(combined_net, 3),
        "realized_net_usd": round(realized_net, 3),
        "floating_net_usd": round(floating_net, 3),
        "realized_closes": closes,
        "open_events": open_events,
        "close_events": close_events,
        "same_bar_roundtrips": same_bar_roundtrips,
        "same_bar_roundtrip_pct": round((same_bar_roundtrips / closes) * 100.0, 1) if closes else 0.0,
        "avg_realized_per_close_usd": round(realized_net / closes, 4) if closes else 0.0,
        "max_open_total": max_open,
    }


def build_markdown(rows: list[dict[str, str]]) -> str:
    lines: list[str] = []
    lines.append("# FX Fixed-Shape Side-Gap Ladder")
    lines.append("")
    lines.append("This ladder holds the surviving FX step shapes fixed and varies only `sell_gap` / `buy_gap`.")
    lines.append("")
    lines.append("## Current Read")
    lines.append("")

    for symbol in ("GBPUSD", "EURUSD"):
        modeled_rows = [row for row in rows if row["symbol"] == symbol and row["realism_mode"] == "broker_touch_bar_close"]
        if not modeled_rows:
            continue
        best = max(modeled_rows, key=lambda row: float(row["combined_net_usd"]))
        reference = next(row for row in modeled_rows if row["is_reference"] == "1")
        lines.append(
            f"- `{symbol}` best modeled-live side-gap row is `sell_gap={best['sell_gap']} / buy_gap={best['buy_gap']}` -> "
            f"`${float(best['combined_net_usd']):.2f}` versus fixed-shape reference "
            f"`{reference['sell_gap']} / {reference['buy_gap']}` -> `${float(reference['combined_net_usd']):.2f}`, "
            f"delta `${float(best['delta_vs_reference']):.2f}` with `{best['same_bar_roundtrip_pct']}`% same-bar round-trips."
        )

    lines.append("")
    lines.append("## Interpretation")
    lines.append("")
    lines.append("- If the modeled-live best row keeps the reference gap, side-gap asymmetry is not the next real lever for that symbol.")
    lines.append("- If a new side-gap row wins under modeled-live semantics, it becomes the next fixed-gap candidate before stricter executable realism.")
    lines.append("- Use this ladder before any live retune argument on the surviving low-step FX shapes.")
    return "\n".join(lines) + "\n"


def main() -> int:
    args = parse_args()
    cfg_map = default_raw_configs()

    if not mt5.initialize():
        print("MetaTrader5 initialize() failed")
        return 1

    try:
        rows: list[dict[str, object]] = []
        for symbol in args.symbols:
            if symbol not in FIXED_SHAPES or symbol not in cfg_map:
                continue
            info = mt5.symbol_info(symbol)
            if info is None:
                continue
            bars = load_bars(symbol, args.days)
            spec = FIXED_SHAPES[symbol]
            max_open_per_side = cfg_map[symbol].max_open_per_side

            results: dict[tuple[int, int, str], dict[str, float | int]] = {}
            for sell_gap in GAP_VALUES:
                for buy_gap in GAP_VALUES:
                    for realism in REALISM_MODES:
                        results[(sell_gap, buy_gap, realism.name)] = simulate_fixed_shape_gap(
                            symbol,
                            bars,
                            info,
                            step_sell=spec.step_sell,
                            step_buy=spec.step_buy,
                            max_open_per_side=max_open_per_side,
                            close_alpha=spec.close_alpha,
                            close_style=spec.close_style,
                            sell_gap=sell_gap,
                            buy_gap=buy_gap,
                            open_realism_mode=realism.open_mode,
                            close_realism_mode=realism.close_mode,
                        )

            for sell_gap in GAP_VALUES:
                for buy_gap in GAP_VALUES:
                    for realism in REALISM_MODES:
                        result = results[(sell_gap, buy_gap, realism.name)]
                        reference = results[(spec.reference_sell_gap, spec.reference_buy_gap, realism.name)]
                        intrabar = results[(sell_gap, buy_gap, "intrabar_intrabar")]
                        rows.append(
                            {
                                "symbol": symbol,
                                "days": args.days,
                                "step_sell": spec.step_sell,
                                "step_buy": spec.step_buy,
                                "close_style": spec.close_style,
                                "close_alpha": spec.close_alpha,
                                "sell_gap": sell_gap,
                                "buy_gap": buy_gap,
                                "realism_mode": realism.name,
                                "open_realism_mode": realism.open_mode,
                                "close_realism_mode": realism.close_mode,
                                "combined_net_usd": result["combined_net_usd"],
                                "realized_net_usd": result["realized_net_usd"],
                                "floating_net_usd": result["floating_net_usd"],
                                "realized_closes": result["realized_closes"],
                                "open_events": result["open_events"],
                                "close_events": result["close_events"],
                                "same_bar_roundtrips": result["same_bar_roundtrips"],
                                "same_bar_roundtrip_pct": result["same_bar_roundtrip_pct"],
                                "avg_realized_per_close_usd": result["avg_realized_per_close_usd"],
                                "max_open_total": result["max_open_total"],
                                "reference_combined_usd": reference["combined_net_usd"],
                                "delta_vs_reference": round(float(result["combined_net_usd"]) - float(reference["combined_net_usd"]), 3),
                                "intrabar_combined_usd": intrabar["combined_net_usd"],
                                "retention_vs_intrabar_pct": round(
                                    (float(result["combined_net_usd"]) / float(intrabar["combined_net_usd"]) * 100.0)
                                    if float(intrabar["combined_net_usd"]) != 0.0
                                    else 0.0,
                                    1,
                                ),
                                "is_reference": 1 if sell_gap == spec.reference_sell_gap and buy_gap == spec.reference_buy_gap else 0,
                            }
                        )

        out_csv = Path(args.output_csv)
        out_csv.parent.mkdir(parents=True, exist_ok=True)
        with out_csv.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(
                handle,
                fieldnames=[
                    "symbol",
                    "days",
                    "step_sell",
                    "step_buy",
                    "close_style",
                    "close_alpha",
                    "sell_gap",
                    "buy_gap",
                    "realism_mode",
                    "open_realism_mode",
                    "close_realism_mode",
                    "combined_net_usd",
                    "realized_net_usd",
                    "floating_net_usd",
                    "realized_closes",
                    "open_events",
                    "close_events",
                    "same_bar_roundtrips",
                    "same_bar_roundtrip_pct",
                    "avg_realized_per_close_usd",
                    "max_open_total",
                    "reference_combined_usd",
                    "delta_vs_reference",
                    "intrabar_combined_usd",
                    "retention_vs_intrabar_pct",
                    "is_reference",
                ],
            )
            writer.writeheader()
            writer.writerows(rows)

        out_md = Path(args.output_md)
        out_md.write_text(build_markdown([{k: str(v) for k, v in row.items()} for row in rows]), encoding="utf-8")

        print(f"Wrote {out_csv}")
        print(f"Wrote {out_md}")
        return 0
    finally:
        mt5.shutdown()


if __name__ == "__main__":
    raise SystemExit(main())
