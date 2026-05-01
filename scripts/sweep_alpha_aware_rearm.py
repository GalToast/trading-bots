#!/usr/bin/env python3
"""
Alpha-aware stateful rearm benchmark.

Combines two orthogonal improvements:
1. Stateful rearm with cooldown_12bar (more closes: ~2.1x baseline)
2. Alpha=0.50 close extension (more PnL per close: ~1.44x baseline)

Hypothesis: combined effect is multiplicative (~3x baseline).

Tests:
- cooldown_12bar at alpha=0.0 (baseline fill)
- cooldown_12bar at alpha=0.50 (mid-bar fill, shadow-verified)
- cooldown_12bar at alpha=0.75 (aggressive fill, crypto-validated)
- cooldown_12bar sweep across alpha [0.0, 0.25, 0.50, 0.75, 1.0]
- Raw momentum_cool6 at alpha=0.50 (if momentum survives alpha degradation)
"""
from __future__ import annotations

import argparse
import csv
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

import MetaTrader5 as mt5

from benchmark_inside_geometry_churn import default_raw_configs
from penetration_lattice_hybrid_apex import RawConfig, simulate_raw_close2
from penetration_lattice_lab_v2 import Ticket, dynamic_step, load_bars, pip_size_for, spread_price, unit_pnl_usd


ROOT = Path(__file__).resolve().parent.parent
DEFAULT_SYMBOLS = ["GBPUSD", "EURUSD", "NZDUSD"]
ALPHA_SWEEP = [0.0, 0.25, 0.50, 0.75, 1.0]


@dataclass(frozen=True)
class Variant:
    name: str
    min_level_idx: int = 2
    excursion_levels: int = 1
    cooldown_bars: int = 0
    close_alpha: float = 0.0       # 0.0 = penetration level, 0.5 = mid-bar, 1.0 = extreme
    skip_symbols: set[str] = field(default_factory=set)


VARIANTS = [
    # Baseline re-confirmation (no rearm, raw)
    # Note: we compare against simulate_raw_close2 baseline, not a variant here.

    # cooldown_12bar at different alpha values
    Variant(name="cool12_alpha0", cooldown_bars=12, close_alpha=0.0),
    Variant(name="cool12_alpha25", cooldown_bars=12, close_alpha=0.25),
    Variant(name="cool12_alpha50", cooldown_bars=12, close_alpha=0.50),
    Variant(name="cool12_alpha75", cooldown_bars=12, close_alpha=0.75),
    Variant(name="cool12_alpha100", cooldown_bars=12, close_alpha=1.0),

    # Raw rearm (no cooldown) at alpha=0.50 for comparison
    Variant(name="rearm_alpha50", cooldown_bars=0, close_alpha=0.50),

    # NZDUSD skip + alpha50
    Variant(name="cool12_alpha50_noNZD", cooldown_bars=12, close_alpha=0.50, skip_symbols={"NZDUSD"}),
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Sweep alpha values on stateful rearm with cooldown.")
    parser.add_argument("--symbols", nargs="*", default=DEFAULT_SYMBOLS)
    parser.add_argument("--days", type=int, default=60)
    parser.add_argument(
        "--output-csv",
        default=str(ROOT / "reports" / "alpha_aware_rearm_sweep.csv"),
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


@dataclass
class RearmToken:
    direction: str
    level: float
    level_idx: int
    armed: bool = False
    reuse_count: int = 0
    last_close_bar: int = 0
    cooldown_until: int = 0


def _update_token_arming(
    tokens: list[RearmToken], bar: dict, base_step_px: float, variant: Variant, current_bar: int
) -> None:
    for token in tokens:
        if token.armed:
            continue
        if variant.cooldown_bars > 0 and current_bar < token.cooldown_until:
            continue
        if token.direction == "SELL":
            away_trigger = token.level - (variant.excursion_levels * base_step_px)
            if bar["low"] <= away_trigger:
                token.armed = True
        else:
            away_trigger = token.level + (variant.excursion_levels * base_step_px)
            if bar["high"] >= away_trigger:
                token.armed = True


def _interpolate_close_ref(level_price: float, bar_extreme: float, direction: str, alpha: float) -> float:
    """Interpolate between penetration level and bar extreme.
    
    For SELL: level_price is the penetration level (floor). bar_extreme = bar["low"].
      alpha=0: close at level_price (guaranteed fill)
      alpha=1: close at bar_extreme (optimistic)
      alpha=0.5: close halfway between (realistic mid-bar fill)
    
    For BUY: level_price is the penetration level (ceiling). bar_extreme = bar["high"].
      alpha=0: close at level_price
      alpha=1: close at bar_extreme
    """
    if direction == "SELL":
        # level_price > bar_extreme (price swept below level)
        # We want: level_price - alpha * (level_price - bar_extreme)
        # = level_price + alpha * (bar_extreme - level_price)
        return level_price + alpha * (bar_extreme - level_price)
    else:
        # level_price < bar_extreme (price swept above level)
        # We want: level_price + alpha * (bar_extreme - level_price)
        return level_price + alpha * (bar_extreme - level_price)


def simulate_alpha_aware_rearm(
    symbol: str, bars: list[dict], symbol_info, cfg: RawConfig, variant: Variant
) -> dict:
    if not bars:
        return {}

    if symbol in variant.skip_symbols:
        return simulate_raw_close2(symbol, bars, symbol_info, cfg)

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
    alpha_closes = 0  # tracks closes that benefited from alpha > 0

    level_reuse: dict[float, int] = defaultdict(int)

    for idx in range(1, len(bars)):
        bar = bars[idx]

        _update_token_arming(rearm_tokens, bar, base_step_px, variant, idx)

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

        # Consume rearm tokens
        open_sell = _side_count(open_tickets, "SELL")
        open_buy = _side_count(open_tickets, "BUY")
        for token in list(rearm_tokens):
            if token.direction == "SELL" and token.armed and open_sell < cfg.max_open_per_side:
                if bar["high"] >= token.level:
                    open_tickets.append(Ticket(direction="SELL", entry_price=token.level, opened_idx=idx))
                    rearm_tokens.remove(token)
                    open_sell += 1
                    rearm_opens += 1
            elif token.direction == "BUY" and token.armed and open_buy < cfg.max_open_per_side:
                if bar["low"] <= token.level:
                    open_tickets.append(Ticket(direction="BUY", entry_price=token.level, opened_idx=idx))
                    rearm_tokens.remove(token)
                    open_buy += 1
                    rearm_opens += 1

        gap = 1 if cfg.close_mode == "one_level" else 2

        # Sell closes
        sells = sorted((t for t in open_tickets if t.direction == "SELL"), key=lambda t: t.entry_price, reverse=True)
        while len(sells) > gap and bar["low"] <= sells[gap].entry_price:
            outer = sells[0]
            level_price = sells[gap].entry_price
            # Alpha-aware close reference
            close_ref = _interpolate_close_ref(level_price, bar["low"], "SELL", variant.close_alpha)
            if variant.close_alpha > 0:
                alpha_closes += 1
            pnl = unit_pnl_usd(symbol, "SELL", outer.entry_price, close_ref, spread_px)
            realized_pnls.append(pnl)
            open_tickets.remove(outer)
            level_idx = int(round((outer.entry_price - anchor) / base_step_px))
            if level_idx >= variant.min_level_idx:
                reuse = level_reuse[outer.entry_price]
                cooldown_end = idx + variant.cooldown_bars if variant.cooldown_bars > 0 else 0
                rearm_tokens.append(RearmToken(
                    direction="SELL",
                    level=outer.entry_price,
                    level_idx=level_idx,
                    reuse_count=reuse,
                    last_close_bar=idx,
                    cooldown_until=cooldown_end,
                ))
                level_reuse[outer.entry_price] = reuse + 1
            sells = sorted((t for t in open_tickets if t.direction == "SELL"), key=lambda t: t.entry_price, reverse=True)

        # Buy closes
        buys = sorted((t for t in open_tickets if t.direction == "BUY"), key=lambda t: t.entry_price)
        while len(buys) > gap and bar["high"] >= buys[gap].entry_price:
            outer = buys[0]
            level_price = buys[gap].entry_price
            close_ref = _interpolate_close_ref(level_price, bar["high"], "BUY", variant.close_alpha)
            if variant.close_alpha > 0:
                alpha_closes += 1
            pnl = unit_pnl_usd(symbol, "BUY", outer.entry_price, close_ref, spread_px)
            realized_pnls.append(pnl)
            open_tickets.remove(outer)
            level_idx = int(round((anchor - outer.entry_price) / base_step_px))
            if level_idx >= variant.min_level_idx:
                reuse = level_reuse[outer.entry_price]
                cooldown_end = idx + variant.cooldown_bars if variant.cooldown_bars > 0 else 0
                rearm_tokens.append(RearmToken(
                    direction="BUY",
                    level=outer.entry_price,
                    level_idx=level_idx,
                    reuse_count=reuse,
                    last_close_bar=idx,
                    cooldown_until=cooldown_end,
                ))
                level_reuse[outer.entry_price] = reuse + 1
            buys = sorted((t for t in open_tickets if t.direction == "BUY"), key=lambda t: t.entry_price)

        max_open = max(max_open, len(open_tickets))
        max_open_buy = max(max_open_buy, _side_count(open_tickets, "BUY"))
        max_open_sell = max(max_open_sell, _side_count(open_tickets, "SELL"))

        if not open_tickets and abs(bar["close"] - anchor) >= base_step_px:
            anchor = bar["close"]
            next_sell_level = anchor + base_step_px
            next_buy_level = anchor - base_step_px
            rearm_tokens = []
            level_reuse.clear()

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
        "alpha_closes": alpha_closes,
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
        variant_totals: dict[str, float] = {v.name: 0.0 for v in VARIANTS}
        variant_by_symbol: dict[str, dict[str, float]] = defaultdict(dict)

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
                result = simulate_alpha_aware_rearm(symbol, bars, info, raw_cfg, variant)
                if not result:
                    continue
                variant_totals[variant.name] += float(result["combined_net_usd"])
                variant_by_symbol[variant.name][symbol] = float(result["combined_net_usd"])

                rows.append(
                    {
                        "symbol": symbol,
                        "variant": variant.name,
                        "days": args.days,
                        "step_pips": raw_cfg.step_pips,
                        "max_open_per_side": raw_cfg.max_open_per_side,
                        "close_alpha": variant.close_alpha,
                        "cooldown_bars": variant.cooldown_bars,
                        "baseline_combined_usd": baseline["combined_net_usd"],
                        "baseline_closes": baseline["realized_closes"],
                        "variant_combined_usd": result["combined_net_usd"],
                        "variant_realized_usd": result["realized_net_usd"],
                        "variant_floating_usd": result["floating_net_usd"],
                        "variant_closes": result["realized_closes"],
                        "variant_alpha_closes": result.get("alpha_closes", 0),
                        "variant_max_open": result.get("max_open_total", ""),
                        "variant_rearm_opens": result.get("rearm_opens", ""),
                        "delta_combined_usd": round(result["combined_net_usd"] - baseline["combined_net_usd"], 3),
                    }
                )

        out_path = Path(args.output_csv)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        fieldnames = [
            "symbol", "variant", "days", "step_pips", "max_open_per_side",
            "close_alpha", "cooldown_bars",
            "baseline_combined_usd", "baseline_closes",
            "variant_combined_usd", "variant_realized_usd", "variant_floating_usd",
            "variant_closes", "variant_alpha_closes", "variant_max_open",
            "variant_rearm_opens", "delta_combined_usd",
        ]
        with out_path.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(rows)

        # Summary
        summary_path = out_path.with_name("alpha_aware_rearm_summary.csv")
        with summary_path.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=[
                "variant", "baseline_total_usd", "variant_total_usd", "delta_total_usd",
                "GBPUSD", "EURUSD", "NZDUSD",
            ])
            writer.writeheader()
            for variant in VARIANTS:
                gbp = variant_by_symbol[variant.name].get("GBPUSD", 0)
                eur = variant_by_symbol[variant.name].get("EURUSD", 0)
                nzd = variant_by_symbol[variant.name].get("NZDUSD", 0)
                writer.writerow(
                    {
                        "variant": variant.name,
                        "baseline_total_usd": round(baseline_total, 3),
                        "variant_total_usd": round(variant_totals[variant.name], 3),
                        "delta_total_usd": round(variant_totals[variant.name] - baseline_total, 3),
                        "GBPUSD": round(gbp, 3),
                        "EURUSD": round(eur, 3),
                        "NZDUSD": round(nzd, 3),
                    }
                )

        print(f"Wrote {out_path}")
        print(f"Wrote {summary_path}")
        print(f"\nBaseline total: ${baseline_total:,.2f}")
        print(f"\n{'Variant':<30} {'Total':>12} {'Delta':>12} {'%OverBase':>10} {'GBPUSD':>10} {'EURUSD':>10} {'NZDUSD':>10}")
        print("-" * 105)
        for variant in VARIANTS:
            gbp = variant_by_symbol[variant.name].get("GBPUSD", 0)
            eur = variant_by_symbol[variant.name].get("EURUSD", 0)
            nzd = variant_by_symbol[variant.name].get("NZDUSD", 0)
            delta = variant_totals[variant.name] - baseline_total
            pct = (variant_totals[variant.name] / baseline_total - 1) * 100 if baseline_total else 0
            print(
                f"{variant.name:<30} {variant_totals[variant.name]:>12.2f} {delta:>12.2f} {pct:>9.1f}% "
                f"{gbp:>10.2f} {eur:>10.2f} {nzd:>10.2f}"
            )
        return 0
    finally:
        mt5.shutdown()


if __name__ == "__main__":
    raise SystemExit(main())
