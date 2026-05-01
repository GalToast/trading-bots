#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
from dataclasses import dataclass
from pathlib import Path

import MetaTrader5 as mt5

from benchmark_fx_fixed_policy_spacing import FIXED_POLICIES
from benchmark_fx_fixed_step_close_policy import ClosePolicy, select_close_positions
from benchmark_inside_geometry_churn import default_raw_configs
from penetration_lattice_lab_v2 import Ticket, dynamic_step, load_bars, pip_size_for, spread_price, unit_pnl_usd


ROOT = Path(__file__).resolve().parent.parent
DEFAULT_OUTPUT_CSV = ROOT / "reports" / "fx_fixed_policy_asymmetric_geometry.csv"
DEFAULT_OUTPUT_MD = ROOT / "reports" / "fx_fixed_policy_asymmetric_geometry.md"

ASYM_STEP_GRIDS: dict[str, list[float]] = {
    "GBPUSD": [0.5, 0.75, 1.0, 1.25, 1.5],
    "EURUSD": [1.0, 1.25, 1.5, 1.75, 2.0],
    "NZDUSD": [0.25, 0.5, 0.75, 1.0],
}

SYMMETRIC_WINNERS: dict[str, float] = {
    "GBPUSD": 1.0,
    "EURUSD": 1.5,
    "NZDUSD": 0.5,
}


@dataclass(frozen=True)
class AsymmetricShape:
    step_sell: float
    step_buy: float

    @property
    def name(self) -> str:
        return f"sell_{self.step_sell:g}_buy_{self.step_buy:g}"

    @property
    def is_symmetric(self) -> bool:
        return abs(self.step_sell - self.step_buy) < 1e-9


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Benchmark asymmetric FX side spacing with stronger close policy held fixed."
    )
    parser.add_argument("--symbols", nargs="*", default=["GBPUSD", "EURUSD", "NZDUSD"])
    parser.add_argument("--days", type=int, default=60)
    parser.add_argument("--output-csv", default=str(DEFAULT_OUTPUT_CSV))
    parser.add_argument("--output-md", default=str(DEFAULT_OUTPUT_MD))
    return parser.parse_args()


def _interp_close(level_price: float, bar_extreme: float, alpha: float) -> float:
    return level_price + alpha * (bar_extreme - level_price)


def simulate_asymmetric_close_policy(
    symbol: str,
    bars: list[dict],
    symbol_info,
    *,
    step_sell: float,
    step_buy: float,
    max_open_per_side: int,
    policy: ClosePolicy,
) -> dict:
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
    max_open = 0
    max_open_buy = 0
    max_open_sell = 0
    close_events = 0
    tickets_closed = 0

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

    for idx in range(1, len(bars)):
        bar = bars[idx]

        open_buy = sum(1 for t in open_tickets if t.direction == "BUY")
        open_sell = sum(1 for t in open_tickets if t.direction == "SELL")
        current_sell_step = dynamic_step(base_step_sell_px, open_sell, adapt_cfg)
        current_buy_step = dynamic_step(base_step_buy_px, open_buy, adapt_cfg)

        while bar["high"] >= next_sell_level and open_sell < max_open_per_side:
            open_tickets.append(Ticket(direction="SELL", entry_price=next_sell_level, opened_idx=idx))
            open_sell += 1
            current_sell_step = dynamic_step(base_step_sell_px, open_sell, adapt_cfg)
            next_sell_level += current_sell_step

        while bar["low"] <= next_buy_level and open_buy < max_open_per_side:
            open_tickets.append(Ticket(direction="BUY", entry_price=next_buy_level, opened_idx=idx))
            open_buy += 1
            current_buy_step = dynamic_step(base_step_buy_px, open_buy, adapt_cfg)
            next_buy_level -= current_buy_step

        sells = sorted((t for t in open_tickets if t.direction == "SELL"), key=lambda t: t.entry_price, reverse=True)
        while len(sells) > policy.close_gap and bar["low"] <= sells[policy.close_gap].entry_price:
            level_price = sells[policy.close_gap].entry_price
            close_ref = _interp_close(level_price, bar["low"], policy.close_alpha)
            profitable_positions = [
                pos
                for pos, ticket in enumerate(sells)
                if unit_pnl_usd(symbol, "SELL", ticket.entry_price, close_ref, spread_px) > 0
            ]
            close_positions = select_close_positions(len(sells), policy.close_gap, policy.close_style, profitable_positions)
            if not close_positions:
                break
            close_indices = sorted(set(close_positions), reverse=True)
            closed_any = False
            for pos in close_indices:
                ticket = sells[pos]
                pnl = unit_pnl_usd(symbol, "SELL", ticket.entry_price, close_ref, spread_px)
                if pnl <= 0:
                    continue
                realized_pnls.append(pnl)
                open_tickets.remove(ticket)
                tickets_closed += 1
                closed_any = True
            if not closed_any:
                break
            close_events += 1
            sells = sorted((t for t in open_tickets if t.direction == "SELL"), key=lambda t: t.entry_price, reverse=True)

        buys = sorted((t for t in open_tickets if t.direction == "BUY"), key=lambda t: t.entry_price)
        while len(buys) > policy.close_gap and bar["high"] >= buys[policy.close_gap].entry_price:
            level_price = buys[policy.close_gap].entry_price
            close_ref = _interp_close(level_price, bar["high"], policy.close_alpha)
            profitable_positions = [
                pos
                for pos, ticket in enumerate(buys)
                if unit_pnl_usd(symbol, "BUY", ticket.entry_price, close_ref, spread_px) > 0
            ]
            close_positions = select_close_positions(len(buys), policy.close_gap, policy.close_style, profitable_positions)
            if not close_positions:
                break
            close_indices = sorted(set(close_positions), reverse=True)
            closed_any = False
            for pos in close_indices:
                ticket = buys[pos]
                pnl = unit_pnl_usd(symbol, "BUY", ticket.entry_price, close_ref, spread_px)
                if pnl <= 0:
                    continue
                realized_pnls.append(pnl)
                open_tickets.remove(ticket)
                tickets_closed += 1
                closed_any = True
            if not closed_any:
                break
            close_events += 1
            buys = sorted((t for t in open_tickets if t.direction == "BUY"), key=lambda t: t.entry_price)

        max_open = max(max_open, len(open_tickets))
        max_open_buy = max(max_open_buy, sum(1 for t in open_tickets if t.direction == "BUY"))
        max_open_sell = max(max_open_sell, sum(1 for t in open_tickets if t.direction == "SELL"))

        if not open_tickets and (
            bar["close"] >= anchor + base_step_sell_px or bar["close"] <= anchor - base_step_buy_px
        ):
            anchor = bar["close"]
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
    return {
        "combined_net_usd": round(combined_net, 3),
        "realized_net_usd": round(realized_net, 3),
        "floating_net_usd": round(floating_net, 3),
        "realized_closes": len(realized_pnls),
        "close_events": close_events,
        "tickets_closed": tickets_closed,
        "max_open_total": max_open,
        "max_open_buy": max_open_buy,
        "max_open_sell": max_open_sell,
    }


def build_shapes(symbol: str) -> list[AsymmetricShape]:
    values = ASYM_STEP_GRIDS[symbol]
    return [AsymmetricShape(step_sell=sell, step_buy=buy) for sell in values for buy in values]


def build_markdown(rows: list[dict[str, str]]) -> str:
    by_symbol: dict[str, list[dict[str, str]]] = {}
    for row in rows:
        by_symbol.setdefault(row["symbol"], []).append(row)

    lines: list[str] = []
    lines.append("# FX Fixed-Policy Asymmetric Geometry")
    lines.append("")
    lines.append("This ladder holds the stronger close policy fixed per symbol and sweeps only side-asymmetric step geometry.")
    lines.append("")
    lines.append("## Best By Symbol")
    lines.append("")

    basket_baseline = 0.0
    basket_best = 0.0
    symmetric_best_count = 0
    asymmetric_best_count = 0
    for symbol in ("GBPUSD", "EURUSD", "NZDUSD"):
        symbol_rows = by_symbol.get(symbol, [])
        if not symbol_rows:
            continue
        baseline_row = next(row for row in symbol_rows if row["is_symmetric_winner"] == "1")
        best_row = max(symbol_rows, key=lambda row: float(row["variant_combined_usd"]))
        basket_baseline += float(baseline_row["variant_combined_usd"])
        basket_best += float(best_row["variant_combined_usd"])
        best_is_symmetric = best_row["is_symmetric"] == "1"
        if best_is_symmetric:
            symmetric_best_count += 1
        else:
            asymmetric_best_count += 1
        winner_shape = "symmetric" if best_is_symmetric else "asymmetric"
        lines.append(
            f"- `{symbol}` fixed policy `{best_row['policy']}`: best side geometry is "
            f"`sell={best_row['step_sell']} / buy={best_row['step_buy']}` -> `${float(best_row['variant_combined_usd']):.2f}`. "
            f"That row is `{winner_shape}`. Tighter symmetric winner `{baseline_row['step_sell']}` / `{baseline_row['step_buy']}` was "
            f"`${float(baseline_row['variant_combined_usd']):.2f}`, delta `${float(best_row['delta_vs_symmetric_winner']):.2f}`."
        )

    lines.append("")
    lines.append("## Basket Read")
    lines.append("")
    lines.append(
        f"- Independent best asymmetric basket under fixed close policies: `${basket_best:.2f}` vs "
        f"tighter symmetric winner basket `${basket_baseline:.2f}`, delta `${basket_best - basket_baseline:.2f}`."
    )

    leading_rows = [
        max(symbol_rows, key=lambda row: float(row["variant_combined_usd"]))
        for symbol_rows in by_symbol.values()
        if symbol_rows
    ]
    if leading_rows:
        tighter_sell_count = sum(
            float(row["step_sell"]) < float(row["step_buy"]) and row["is_symmetric"] != "1"
            for row in leading_rows
        )
        tighter_buy_count = sum(
            float(row["step_buy"]) < float(row["step_sell"]) and row["is_symmetric"] != "1"
            for row in leading_rows
        )
        lines.append(
            f"- Winner composition: `{symmetric_best_count}` symbol(s) still prefer a symmetric best row, "
            f"`{asymmetric_best_count}` symbol(s) prefer a genuinely asymmetric row."
        )
        lines.append(
            f"- Among the asymmetric winners only, tighter-sell rows lead `{tighter_sell_count}` symbol(s), "
            f"tighter-buy rows lead `{tighter_buy_count}` symbol(s)."
        )

    lines.append("")
    lines.append("## Interpretation")
    lines.append("")
    if symmetric_best_count:
        lines.append(
            "- If a widened side-geometry grid still picks a symmetric row, the prior spacing ladder likely stopped too early and tighter symmetric spacing is still the bigger lever than asymmetry."
        )
    lines.append("- If the asymmetric deltas are small, the tighter symmetric steps are already most of the edge and side bias should stay conservative.")
    lines.append("- If the same directional asymmetry keeps winning, promote that as the next fixed-shape reference for deeper churn or fill-quality checks.")
    lines.append("- Use this ladder before touching live FX geometry; it isolates side spacing from close policy and cap changes.")
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
            if symbol not in cfg_map or symbol not in FIXED_POLICIES or symbol not in SYMMETRIC_WINNERS:
                continue
            info = mt5.symbol_info(symbol)
            if info is None:
                continue
            bars = load_bars(symbol, args.days)
            policy = FIXED_POLICIES[symbol]
            max_open_per_side = cfg_map[symbol].max_open_per_side
            symmetric_step = SYMMETRIC_WINNERS[symbol]

            ladder_results: list[tuple[AsymmetricShape, dict[str, object]]] = []
            for shape in build_shapes(symbol):
                result = simulate_asymmetric_close_policy(
                    symbol,
                    bars,
                    info,
                    step_sell=shape.step_sell,
                    step_buy=shape.step_buy,
                    max_open_per_side=max_open_per_side,
                    policy=policy,
                )
                ladder_results.append((shape, result))

            symmetric_result = next(
                result
                for shape, result in ladder_results
                if abs(shape.step_sell - symmetric_step) < 1e-9 and abs(shape.step_buy - symmetric_step) < 1e-9
            )

            for shape, result in ladder_results:
                rows.append(
                    {
                        "symbol": symbol,
                        "policy": policy.name,
                        "days": args.days,
                        "step_sell": shape.step_sell,
                        "step_buy": shape.step_buy,
                        "step_ratio_sell_to_buy": round(shape.step_sell / shape.step_buy, 4),
                        "max_open_per_side": max_open_per_side,
                        "variant_combined_usd": result["combined_net_usd"],
                        "variant_realized_usd": result["realized_net_usd"],
                        "variant_floating_usd": result["floating_net_usd"],
                        "variant_closes": result["realized_closes"],
                        "close_events": result["close_events"],
                        "variant_max_open": result["max_open_total"],
                        "variant_max_open_buy": result["max_open_buy"],
                        "variant_max_open_sell": result["max_open_sell"],
                        "symmetric_winner_combined_usd": symmetric_result["combined_net_usd"],
                        "delta_vs_symmetric_winner": round(
                            float(result["combined_net_usd"]) - float(symmetric_result["combined_net_usd"]),
                            3,
                        ),
                        "is_symmetric": 1 if shape.is_symmetric else 0,
                        "is_symmetric_winner": 1
                        if abs(shape.step_sell - symmetric_step) < 1e-9 and abs(shape.step_buy - symmetric_step) < 1e-9
                        else 0,
                    }
                )

        out_csv = Path(args.output_csv)
        out_csv.parent.mkdir(parents=True, exist_ok=True)
        with out_csv.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(
                handle,
                fieldnames=[
                    "symbol",
                    "policy",
                    "days",
                    "step_sell",
                    "step_buy",
                    "step_ratio_sell_to_buy",
                    "max_open_per_side",
                    "variant_combined_usd",
                    "variant_realized_usd",
                    "variant_floating_usd",
                    "variant_closes",
                    "close_events",
                    "variant_max_open",
                    "variant_max_open_buy",
                    "variant_max_open_sell",
                    "symmetric_winner_combined_usd",
                    "delta_vs_symmetric_winner",
                    "is_symmetric",
                    "is_symmetric_winner",
                ],
            )
            writer.writeheader()
            writer.writerows(rows)

        markdown = build_markdown([{key: str(value) for key, value in row.items()} for row in rows])
        out_md = Path(args.output_md)
        out_md.write_text(markdown, encoding="utf-8")

        print(f"Wrote {out_csv}")
        print(f"Wrote {out_md}")
        return 0
    finally:
        mt5.shutdown()


if __name__ == "__main__":
    raise SystemExit(main())
