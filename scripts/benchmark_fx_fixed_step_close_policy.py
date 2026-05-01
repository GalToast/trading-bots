#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
from dataclasses import dataclass
from pathlib import Path

import MetaTrader5 as mt5

from benchmark_inside_geometry_churn import default_raw_configs
from penetration_lattice_hybrid_apex import RawConfig
from penetration_lattice_lab_v2 import Ticket, dynamic_step, load_bars, pip_size_for, spread_price, unit_pnl_usd


ROOT = Path(__file__).resolve().parent.parent
DEFAULT_OUTPUT_CSV = ROOT / "reports" / "fx_fixed_step_close_policy_ladder.csv"
DEFAULT_OUTPUT_MD = ROOT / "reports" / "fx_fixed_step_close_policy_ladder.md"


@dataclass(frozen=True)
class ClosePolicy:
    name: str
    close_gap: int
    close_alpha: float
    close_style: str  # outer | inner | all_profitable


POLICIES = [
    ClosePolicy(name="outer_gap2_alpha0", close_gap=2, close_alpha=0.0, close_style="outer"),
    ClosePolicy(name="outer_gap2_alpha50", close_gap=2, close_alpha=0.5, close_style="outer"),
    ClosePolicy(name="outer_gap2_alpha100", close_gap=2, close_alpha=1.0, close_style="outer"),
    ClosePolicy(name="outer_gap1_alpha0", close_gap=1, close_alpha=0.0, close_style="outer"),
    ClosePolicy(name="outer_gap1_alpha50", close_gap=1, close_alpha=0.5, close_style="outer"),
    ClosePolicy(name="outer_gap1_alpha100", close_gap=1, close_alpha=1.0, close_style="outer"),
    ClosePolicy(name="inner_gap2_alpha0", close_gap=2, close_alpha=0.0, close_style="inner"),
    ClosePolicy(name="inner_gap2_alpha50", close_gap=2, close_alpha=0.5, close_style="inner"),
    ClosePolicy(name="allprof_gap2_alpha0", close_gap=2, close_alpha=0.0, close_style="all_profitable"),
    ClosePolicy(name="allprof_gap2_alpha50", close_gap=2, close_alpha=0.5, close_style="all_profitable"),
    ClosePolicy(name="allprof_gap1_alpha0", close_gap=1, close_alpha=0.0, close_style="all_profitable"),
    ClosePolicy(name="allprof_gap1_alpha50", close_gap=1, close_alpha=0.5, close_style="all_profitable"),
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Benchmark fixed-step FX close policy ladders.")
    parser.add_argument("--symbols", nargs="*", default=["GBPUSD", "EURUSD", "NZDUSD"])
    parser.add_argument("--days", type=int, default=60)
    parser.add_argument("--output-csv", default=str(DEFAULT_OUTPUT_CSV))
    parser.add_argument("--output-md", default=str(DEFAULT_OUTPUT_MD))
    return parser.parse_args()


def _interp_close(level_price: float, bar_extreme: float, direction: str, alpha: float) -> float:
    if direction == "SELL":
        return level_price + alpha * (bar_extreme - level_price)
    return level_price + alpha * (bar_extreme - level_price)


def select_close_positions(side_len: int, gap: int, style: str, profitable_positions: list[int] | None = None) -> list[int]:
    if side_len <= gap:
        return []
    if style == "outer":
        return [0]
    if style == "inner":
        return [max(0, gap - 1)]
    if style == "all_profitable":
        return list(profitable_positions or [])
    raise ValueError(f"Unsupported close style: {style}")


def simulate_close_policy(symbol: str, bars: list[dict], symbol_info, cfg: RawConfig, policy: ClosePolicy) -> dict:
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
    alpha_closes = 0
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
        current_sell_step = dynamic_step(base_step_px, open_sell, adapt_cfg)
        current_buy_step = dynamic_step(base_step_px, open_buy, adapt_cfg)

        while bar["high"] >= next_sell_level and open_sell < cfg.max_open_per_side:
            open_tickets.append(Ticket(direction="SELL", entry_price=next_sell_level, opened_idx=idx))
            open_sell += 1
            current_sell_step = dynamic_step(base_step_px, open_sell, adapt_cfg)
            next_sell_level += current_sell_step

        while bar["low"] <= next_buy_level and open_buy < cfg.max_open_per_side:
            open_tickets.append(Ticket(direction="BUY", entry_price=next_buy_level, opened_idx=idx))
            open_buy += 1
            current_buy_step = dynamic_step(base_step_px, open_buy, adapt_cfg)
            next_buy_level -= current_buy_step

        sells = sorted((t for t in open_tickets if t.direction == "SELL"), key=lambda t: t.entry_price, reverse=True)
        while len(sells) > policy.close_gap and bar["low"] <= sells[policy.close_gap].entry_price:
            level_price = sells[policy.close_gap].entry_price
            close_ref = _interp_close(level_price, bar["low"], "SELL", policy.close_alpha)
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
            if policy.close_alpha > 0:
                alpha_closes += len(close_indices)
            sells = sorted((t for t in open_tickets if t.direction == "SELL"), key=lambda t: t.entry_price, reverse=True)

        buys = sorted((t for t in open_tickets if t.direction == "BUY"), key=lambda t: t.entry_price)
        while len(buys) > policy.close_gap and bar["high"] >= buys[policy.close_gap].entry_price:
            level_price = buys[policy.close_gap].entry_price
            close_ref = _interp_close(level_price, bar["high"], "BUY", policy.close_alpha)
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
            if policy.close_alpha > 0:
                alpha_closes += len(close_indices)
            buys = sorted((t for t in open_tickets if t.direction == "BUY"), key=lambda t: t.entry_price)

        max_open = max(max_open, len(open_tickets))
        max_open_buy = max(max_open_buy, sum(1 for t in open_tickets if t.direction == "BUY"))
        max_open_sell = max(max_open_sell, sum(1 for t in open_tickets if t.direction == "SELL"))

        if not open_tickets and abs(bar["close"] - anchor) >= base_step_px:
            anchor = bar["close"]
            next_sell_level = anchor + base_step_px
            next_buy_level = anchor - base_step_px

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
        "alpha_closes": alpha_closes,
        "max_open_total": max_open,
        "max_open_buy": max_open_buy,
        "max_open_sell": max_open_sell,
    }


def build_markdown(rows: list[dict[str, str]], summary_rows: list[dict[str, str]]) -> str:
    lines: list[str] = []
    lines.append("# FX Fixed-Step Close Policy Ladder")
    lines.append("")
    lines.append("This ladder holds the validated FX raw entry geometry fixed and varies close policy only.")
    lines.append("")

    best_overall = max(summary_rows, key=lambda row: float(row["variant_total_usd"]))
    practical_summary_rows = [row for row in summary_rows if float(row["close_alpha"]) <= 0.5]
    practical_best_overall = max(practical_summary_rows, key=lambda row: float(row["variant_total_usd"]))
    lines.append("## Basket Read")
    lines.append("")
    lines.append(
        f"- Best current basket policy is `{best_overall['policy']}` at `${float(best_overall['variant_total_usd']):.2f}`, "
        f"vs baseline `{best_overall['baseline_total_usd']}` and delta `${float(best_overall['delta_total_usd']):.2f}`."
    )
    lines.append(
        f"- Best practical mid-fill basket policy (`alpha <= 0.5`) is `{practical_best_overall['policy']}` at "
        f"`${float(practical_best_overall['variant_total_usd']):.2f}`, delta "
        f"`${float(practical_best_overall['delta_total_usd']):.2f}`."
    )
    lines.append("")
    lines.append("## Best By Symbol")
    lines.append("")

    by_symbol: dict[str, list[dict[str, str]]] = {}
    for row in rows:
        by_symbol.setdefault(row["symbol"], []).append(row)

    for symbol in ("GBPUSD", "EURUSD", "NZDUSD"):
        symbol_rows = by_symbol.get(symbol, [])
        if not symbol_rows:
            continue
        best = max(symbol_rows, key=lambda row: float(row["variant_combined_usd"]))
        practical_rows = [row for row in symbol_rows if float(row["close_alpha"]) <= 0.5]
        practical_best = max(practical_rows, key=lambda row: float(row["variant_combined_usd"]))
        lines.append(
            f"- `{symbol}`: `{best['policy']}` -> `${float(best['variant_combined_usd']):.2f}` "
            f"(baseline `${float(best['baseline_combined_usd']):.2f}`, delta `${float(best['delta_combined_usd']):.2f}`, "
            f"closes `{best['variant_closes']}`, close_events `{best['close_events']}`, max_open `{best['variant_max_open']}`)."
        )
        if practical_best["policy"] != best["policy"]:
            lines.append(
                f"- `{symbol}` practical mid-fill winner: `{practical_best['policy']}` -> "
                f"`${float(practical_best['variant_combined_usd']):.2f}`."
            )

    lines.append("")
    lines.append("## Interpretation")
    lines.append("")
    lines.append("- Use this ladder before touching FX spacing. It isolates close behavior from step size and cap changes.")
    lines.append("- `alpha=1.0` rows are the optimistic bar-extreme ceiling, not an automatic promotion candidate.")
    lines.append("- If the practical winner is mostly alpha-driven, prioritize close fill quality work next.")
    lines.append("- If the winning policy is mostly gap/style-driven, prioritize close-order and penetration-threshold work next.")
    return "\n".join(lines) + "\n"


def main() -> int:
    args = parse_args()
    cfg_map = default_raw_configs()

    if not mt5.initialize():
        print("MetaTrader5 initialize() failed")
        return 1

    try:
        rows: list[dict[str, object]] = []
        summary_rows: list[dict[str, object]] = []
        baseline_total = 0.0
        policy_totals: dict[str, float] = {policy.name: 0.0 for policy in POLICIES}
        policy_symbol_totals: dict[str, dict[str, float]] = {policy.name: {} for policy in POLICIES}

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
            baseline_policy = next(policy for policy in POLICIES if policy.name == "outer_gap2_alpha0")
            baseline = simulate_close_policy(symbol, bars, info, raw_cfg, baseline_policy)
            baseline_total += float(baseline["combined_net_usd"])

            for policy in POLICIES:
                result = simulate_close_policy(symbol, bars, info, raw_cfg, policy)
                policy_totals[policy.name] += float(result["combined_net_usd"])
                policy_symbol_totals[policy.name][symbol] = float(result["combined_net_usd"])
                rows.append(
                    {
                        "symbol": symbol,
                        "policy": policy.name,
                        "days": args.days,
                        "step_pips": raw_cfg.step_pips,
                        "max_open_per_side": raw_cfg.max_open_per_side,
                        "close_gap": policy.close_gap,
                        "close_alpha": policy.close_alpha,
                        "close_style": policy.close_style,
                        "baseline_combined_usd": baseline["combined_net_usd"],
                        "baseline_closes": baseline["realized_closes"],
                        "variant_combined_usd": result["combined_net_usd"],
                        "variant_realized_usd": result["realized_net_usd"],
                        "variant_floating_usd": result["floating_net_usd"],
                        "variant_closes": result["realized_closes"],
                        "close_events": result["close_events"],
                        "tickets_closed": result["tickets_closed"],
                        "variant_alpha_closes": result["alpha_closes"],
                        "variant_max_open": result["max_open_total"],
                        "delta_combined_usd": round(float(result["combined_net_usd"]) - float(baseline["combined_net_usd"]), 3),
                    }
                )

        for policy in POLICIES:
            summary_rows.append(
                {
                    "policy": policy.name,
                    "close_gap": policy.close_gap,
                    "close_alpha": policy.close_alpha,
                    "close_style": policy.close_style,
                    "baseline_total_usd": round(baseline_total, 3),
                    "variant_total_usd": round(policy_totals[policy.name], 3),
                    "delta_total_usd": round(policy_totals[policy.name] - baseline_total, 3),
                    "GBPUSD": round(policy_symbol_totals[policy.name].get("GBPUSD", 0.0), 3),
                    "EURUSD": round(policy_symbol_totals[policy.name].get("EURUSD", 0.0), 3),
                    "NZDUSD": round(policy_symbol_totals[policy.name].get("NZDUSD", 0.0), 3),
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
                    "step_pips",
                    "max_open_per_side",
                    "close_gap",
                    "close_alpha",
                    "close_style",
                    "baseline_combined_usd",
                    "baseline_closes",
                    "variant_combined_usd",
                    "variant_realized_usd",
                    "variant_floating_usd",
                    "variant_closes",
                    "close_events",
                    "tickets_closed",
                    "variant_alpha_closes",
                    "variant_max_open",
                    "delta_combined_usd",
                ],
            )
            writer.writeheader()
            writer.writerows(rows)

        summary_path = out_csv.with_name("fx_fixed_step_close_policy_summary.csv")
        with summary_path.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(
                handle,
                fieldnames=[
                    "policy",
                    "close_gap",
                    "close_alpha",
                    "close_style",
                    "baseline_total_usd",
                    "variant_total_usd",
                    "delta_total_usd",
                    "GBPUSD",
                    "EURUSD",
                    "NZDUSD",
                ],
            )
            writer.writeheader()
            writer.writerows(summary_rows)

        markdown = build_markdown(
            rows=[{key: str(value) for key, value in row.items()} for row in rows],
            summary_rows=[{key: str(value) for key, value in row.items()} for row in summary_rows],
        )
        out_md = Path(args.output_md)
        out_md.write_text(markdown, encoding="utf-8")

        print(f"Wrote {out_csv}")
        print(f"Wrote {summary_path}")
        print(f"Wrote {out_md}")
        return 0
    finally:
        mt5.shutdown()


if __name__ == "__main__":
    raise SystemExit(main())
