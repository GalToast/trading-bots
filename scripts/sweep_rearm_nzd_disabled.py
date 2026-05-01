#!/usr/bin/env python3
"""Follow-up sweep: disable re-arm on NZDUSD to confirm the symbol-specific gating fix."""
from __future__ import annotations

import argparse
import csv
from dataclasses import dataclass
from pathlib import Path

import MetaTrader5 as mt5

from benchmark_inside_geometry_churn import default_raw_configs
from penetration_lattice_hybrid_apex import RawConfig, simulate_raw_close2
from penetration_lattice_lab_v2 import Ticket, dynamic_step, load_bars, pip_size_for, spread_price, unit_pnl_usd


ROOT = Path(__file__).resolve().parent.parent


@dataclass(frozen=True)
class Variant:
    name: str
    min_level_idx: int = 1
    excursion_levels: int = 1
    disable_rearm_symbols: tuple[str, ...] = ()


@dataclass
class RearmToken:
    direction: str
    level: float
    level_idx: int
    armed: bool = False


VARIANTS = [
    # Original variants for comparison
    Variant(name="rearm_lvl2_exc2_all", min_level_idx=2, excursion_levels=2),
    # NZDUSD-disabled variants (the key fix)
    Variant(name="rearm_lvl2_exc2_noNZD", min_level_idx=2, excursion_levels=2, disable_rearm_symbols=("NZDUSD",)),
    # Also try higher excursion on NZDUSD
    Variant(name="rearm_lvl2_exc2_NZDexc3", min_level_idx=2, excursion_levels=2),
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Sweep with NZDUSD re-arm disabled.")
    parser.add_argument("--symbols", nargs="*", default=["GBPUSD", "EURUSD", "NZDUSD"])
    parser.add_argument("--days", type=int, default=60)
    parser.add_argument(
        "--output-csv",
        default=str(ROOT / "reports" / "stateful_rearm_nzd_disabled_sweep.csv"),
    )
    return parser.parse_args()


def _side_count(tickets: list[Ticket], direction: str) -> int:
    return sum(1 for t in tickets if t.direction == direction)


def _make_adapt_cfg():
    return type(
        "Cfg",
        (),
        {
            "adaptive_step_threshold_1": 10,
            "adaptive_step_threshold_2": 20,
            "adaptive_step_multiplier_1": 1.5,
            "adaptive_step_multiplier_2": 2.0,
        },
    )()


def _update_token_arming(tokens: list[RearmToken], bar: dict, base_step_px: float, excursion_levels: int) -> None:
    for token in tokens:
        if token.armed:
            continue
        if token.direction == "SELL":
            away_trigger = token.level - (excursion_levels * base_step_px)
            if bar["low"] <= away_trigger:
                token.armed = True
        else:
            away_trigger = token.level + (excursion_levels * base_step_px)
            if bar["high"] >= away_trigger:
                token.armed = True


def _consume_rearm_tokens(
    *,
    tokens: list[RearmToken],
    bar: dict,
    idx: int,
    tickets: list[Ticket],
    direction: str,
    max_open_per_side: int,
) -> int:
    open_count = _side_count(tickets, direction)
    opened = 0
    for token in list(tokens):
        if token.direction != direction or not token.armed:
            continue
        if open_count >= max_open_per_side:
            break
        if direction == "SELL" and bar["high"] >= token.level:
            tickets.append(Ticket(direction="SELL", entry_price=token.level, opened_idx=idx))
            tokens.remove(token)
            open_count += 1
            opened += 1
        elif direction == "BUY" and bar["low"] <= token.level:
            tickets.append(Ticket(direction="BUY", entry_price=token.level, opened_idx=idx))
            tokens.remove(token)
            open_count += 1
            opened += 1
    return opened


def simulate_stateful_rearm(
    symbol: str, bars: list[dict], symbol_info, cfg: RawConfig, variant: Variant
) -> dict:
    if not bars:
        return {}

    disable_rearm = symbol in variant.disable_rearm_symbols

    pip_size = pip_size_for(symbol_info)
    spread_px = spread_price(symbol_info)
    base_step_px = cfg.step_pips * pip_size

    anchor = bars[0]["close"]
    next_sell_level = anchor + base_step_px
    next_buy_level = anchor - base_step_px
    adapt_cfg = _make_adapt_cfg()

    open_tickets: list[Ticket] = []
    realized_pnls: list[float] = []
    rearm_tokens: list[RearmToken] = []
    rearm_opens = 0
    max_open = 0
    max_open_buy = 0
    max_open_sell = 0

    for idx in range(1, len(bars)):
        bar = bars[idx]

        if not disable_rearm:
            _update_token_arming(rearm_tokens, bar, base_step_px, variant.excursion_levels)

        open_buy = _side_count(open_tickets, "BUY")
        open_sell = _side_count(open_tickets, "SELL")

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

        if not disable_rearm:
            rearm_opens += _consume_rearm_tokens(
                tokens=rearm_tokens,
                bar=bar,
                idx=idx,
                tickets=open_tickets,
                direction="SELL",
                max_open_per_side=cfg.max_open_per_side,
            )
            rearm_opens += _consume_rearm_tokens(
                tokens=rearm_tokens,
                bar=bar,
                idx=idx,
                tickets=open_tickets,
                direction="BUY",
                max_open_per_side=cfg.max_open_per_side,
            )

        gap = 1 if cfg.close_mode == "one_level" else 2

        sells = sorted((t for t in open_tickets if t.direction == "SELL"), key=lambda t: t.entry_price, reverse=True)
        while len(sells) > gap and bar["low"] <= sells[gap].entry_price:
            outer = sells[0]
            close_ref = sells[gap].entry_price
            realized_pnls.append(unit_pnl_usd(symbol, "SELL", outer.entry_price, close_ref, spread_px))
            open_tickets.remove(outer)
            level_idx = int(round((outer.entry_price - anchor) / base_step_px))
            if not disable_rearm and level_idx >= variant.min_level_idx:
                rearm_tokens.append(RearmToken(direction="SELL", level=outer.entry_price, level_idx=level_idx))
            sells = sorted((t for t in open_tickets if t.direction == "SELL"), key=lambda t: t.entry_price, reverse=True)

        buys = sorted((t for t in open_tickets if t.direction == "BUY"), key=lambda t: t.entry_price)
        while len(buys) > gap and bar["high"] >= buys[gap].entry_price:
            outer = buys[0]
            close_ref = buys[gap].entry_price
            realized_pnls.append(unit_pnl_usd(symbol, "BUY", outer.entry_price, close_ref, spread_px))
            open_tickets.remove(outer)
            level_idx = int(round((anchor - outer.entry_price) / base_step_px))
            if not disable_rearm and level_idx >= variant.min_level_idx:
                rearm_tokens.append(RearmToken(direction="BUY", level=outer.entry_price, level_idx=level_idx))
            buys = sorted((t for t in open_tickets if t.direction == "BUY"), key=lambda t: t.entry_price)

        max_open = max(max_open, len(open_tickets))
        max_open_buy = max(max_open_buy, _side_count(open_tickets, "BUY"))
        max_open_sell = max(max_open_sell, _side_count(open_tickets, "SELL"))

        if not open_tickets and abs(bar["close"] - anchor) >= base_step_px:
            anchor = bar["close"]
            next_sell_level = anchor + base_step_px
            next_buy_level = anchor - base_step_px
            rearm_tokens = []

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
        "max_open_total": max_open,
        "rearm_opens": rearm_opens,
    }


def main() -> int:
    args = parse_args()
    cfg_map = default_raw_configs()

    if not mt5.initialize():
        print("MetaTrader5 initialize() failed")
        return 1

    try:
        rows: list[dict] = []
        baseline_total = 0.0
        variant_totals = {v.name: 0.0 for v in VARIANTS}

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
            if not baseline:
                continue
            baseline_total += float(baseline["combined_net_usd"])

            for variant in VARIANTS:
                result = simulate_stateful_rearm(symbol, bars, info, raw_cfg, variant)
                variant_totals[variant.name] += float(result["combined_net_usd"])
                rows.append(
                    {
                        "symbol": symbol,
                        "variant": variant.name,
                        "days": args.days,
                        "step_pips": raw_cfg.step_pips,
                        "max_open_per_side": raw_cfg.max_open_per_side,
                        "baseline_combined_usd": baseline["combined_net_usd"],
                        "baseline_closes": baseline["realized_closes"],
                        "variant_combined_usd": result["combined_net_usd"],
                        "variant_realized_usd": result["realized_net_usd"],
                        "variant_floating_usd": result["floating_net_usd"],
                        "variant_closes": result["realized_closes"],
                        "variant_max_open": result["max_open_total"],
                        "variant_rearm_opens": result["rearm_opens"],
                        "delta_combined_usd": round(result["combined_net_usd"] - baseline["combined_net_usd"], 3),
                    }
                )

        out_path = Path(args.output_csv)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        with out_path.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=list(rows[0].keys()) if rows else [])
            writer.writeheader()
            writer.writerows(rows)

        summary_path = out_path.with_name("stateful_rearm_nzd_disabled_summary.csv")
        with summary_path.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=["variant", "baseline_total_usd", "variant_total_usd", "delta_total_usd"])
            writer.writeheader()
            for variant in VARIANTS:
                writer.writerow(
                    {
                        "variant": variant.name,
                        "baseline_total_usd": round(baseline_total, 3),
                        "variant_total_usd": round(variant_totals[variant.name], 3),
                        "delta_total_usd": round(variant_totals[variant.name] - baseline_total, 3),
                    }
                )

        print(f"Wrote {out_path}")
        print(f"Wrote {summary_path}")
        for variant in VARIANTS:
            print(f"{variant.name}: total={round(variant_totals[variant.name], 3)} delta={round(variant_totals[variant.name] - baseline_total, 3)}")
        return 0
    finally:
        mt5.shutdown()


if __name__ == "__main__":
    raise SystemExit(main())
