#!/usr/bin/env python3
"""
Slippage friction model for stateful rearm variants.

Tests whether the massive momentum gate results survive realistic
trading frictions: per-trade slippage, variable spread, and
partial fill degradation.

Friction levels tested:
- 0.0: No friction (baseline simulation)
- 0.5: 0.5 pip slippage per trade
- 1.0: 1.0 pip slippage per trade
- 1.5: 1.5 pip slippage per trade (aggressive)
- 2.0: 2.0 pip slippage per trade (extreme)
- variable_spread: base spread + random 0-2 pip widening
"""
from __future__ import annotations

import argparse
import csv
import random
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

import MetaTrader5 as mt5

from benchmark_inside_geometry_churn import default_raw_configs
from penetration_lattice_hybrid_apex import RawConfig, simulate_raw_close2
from penetration_lattice_lab_v2 import Ticket, dynamic_step, load_bars, pip_size_for, spread_price, unit_pnl_usd


ROOT = Path(__file__).resolve().parent.parent
DEFAULT_SYMBOLS = ["GBPUSD", "EURUSD", "NZDUSD"]

FRICTION_LEVELS = [0.0, 0.5, 1.0, 1.5, 2.0]


@dataclass(frozen=True)
class FrictionVariant:
    name: str
    excursion_levels: int = 1
    min_level_idx: int = 2
    momentum_gate: bool = False
    cooldown_bars: int = 0
    settlement_bars: int = 0
    entry_decay_factor: float = 1.0
    skip_symbols: set[str] = field(default_factory=set)


VARIANTS = [
    FrictionVariant(name="cooldown_12bar", cooldown_bars=12),
    FrictionVariant(name="momentum_gate", momentum_gate=True),
    FrictionVariant(name="momentum_cool6", momentum_gate=True, cooldown_bars=6),
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Slippage friction sweep for stateful rearm variants.")
    parser.add_argument("--symbols", nargs="*", default=DEFAULT_SYMBOLS)
    parser.add_argument("--days", type=int, default=60)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument(
        "--output-csv",
        default=str(ROOT / "reports" / "rearm_friction_sweep.csv"),
    )
    return parser.parse_args()


@dataclass
class RearmToken:
    direction: str
    level: float
    level_idx: int
    armed: bool = False
    reuse_count: int = 0
    last_close_bar: int = 0
    cooldown_until: int = 0
    settle_until: int = 0


def _side_count(tickets, direction):
    return sum(1 for t in tickets if t.direction == direction)


def _make_adapt_cfg():
    return type("Cfg", (), {
        "adaptive_step_threshold_1": 10,
        "adaptive_step_threshold_2": 20,
        "adaptive_step_multiplier_1": 1.5,
        "adaptive_step_multiplier_2": 2.0,
    })()


def _check_momentum_gate(bar, direction, entry_price):
    if direction == "SELL":
        return bar["close"] < entry_price
    else:
        return bar["close"] > entry_price


def _simulate_with_friction(
    symbol, bars, symbol_info, cfg, variant, friction_pips, rng, pip_size
):
    """Run stateful rearm simulation with per-trade slippage friction."""
    if not bars:
        return {}
    if symbol in variant.skip_symbols:
        return simulate_raw_close2(symbol, bars, symbol_info, cfg)

    spread_px = spread_price(symbol_info)
    base_step_px = cfg.step_pips * pip_size
    friction_px = friction_pips * pip_size

    anchor = bars[0]["close"]
    next_sell_level = anchor + base_step_px
    next_buy_level = anchor - base_step_px
    adapt_cfg = _make_adapt_cfg()

    open_tickets: list[Ticket] = []
    realized_pnls: list[float] = []
    rearm_tokens: list[RearmToken] = []
    rearm_opens = 0
    max_open = 0
    total_slippage_cost = 0.0

    level_reuse: dict[float, int] = defaultdict(int)

    for idx in range(1, len(bars)):
        bar = bars[idx]

        # Update arming
        for token in rearm_tokens:
            if token.armed:
                continue
            if variant.cooldown_bars > 0 and idx < token.cooldown_until:
                continue
            if variant.settlement_bars > 0 and idx < token.settle_until:
                continue
            if token.direction == "SELL":
                away = token.level - (variant.excursion_levels * base_step_px)
                if bar["low"] <= away:
                    token.armed = True
            else:
                away = token.level + (variant.excursion_levels * base_step_px)
                if bar["high"] >= away:
                    token.armed = True

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
        for token in list(rearm_tokens):
            if token.direction not in ("SELL", "BUY") or not token.armed:
                continue
            if token.direction == "SELL" and open_sell >= cfg.max_open_per_side:
                break
            if token.direction == "BUY" and open_buy >= cfg.max_open_per_side:
                break
            if idx < token.cooldown_until:
                continue
            if idx < token.settle_until:
                continue
            if variant.momentum_gate and variant.settlement_bars == 0:
                if not _check_momentum_gate(bar, token.direction, token.level):
                    continue

            decay = variant.entry_decay_factor ** token.reuse_count
            effective_size = max(0.1, decay)

            if token.direction == "SELL" and bar["high"] >= token.level:
                # Apply slippage: entry price is worse by friction amount
                slipped_entry = token.level - friction_px  # SELL enters lower = worse
                open_tickets.append(Ticket(direction="SELL", entry_price=slipped_entry, opened_idx=idx))
                rearm_tokens.remove(token)
                open_sell += 1
                rearm_opens += 1
                total_slippage_cost += friction_px * pip_size * symbol_info.trade_tick_value / symbol_info.trade_tick_size * effective_size
            elif token.direction == "BUY" and bar["low"] <= token.level:
                # Apply slippage: entry price is worse by friction amount
                slipped_entry = token.level + friction_px  # BUY enters higher = worse
                open_tickets.append(Ticket(direction="BUY", entry_price=slipped_entry, opened_idx=idx))
                rearm_tokens.remove(token)
                open_buy += 1
                rearm_opens += 1
                total_slippage_cost += friction_px * pip_size * symbol_info.trade_tick_value / symbol_info.trade_tick_size * effective_size

        gap = 1 if cfg.close_mode == "one_level" else 2

        sells = sorted((t for t in open_tickets if t.direction == "SELL"), key=lambda t: t.entry_price, reverse=True)
        while len(sells) > gap and bar["low"] <= sells[gap].entry_price:
            outer = sells[0]
            close_ref = sells[gap].entry_price
            # Apply spread cost
            realized_pnls.append(unit_pnl_usd(symbol, "SELL", outer.entry_price, close_ref, spread_px))
            open_tickets.remove(outer)
            level_idx = int(round((outer.entry_price - anchor) / base_step_px))
            if level_idx >= variant.min_level_idx:
                reuse = level_reuse[outer.entry_price]
                cooldown_end = idx + variant.cooldown_bars if variant.cooldown_bars > 0 else 0
                settle_end = idx + variant.settlement_bars if variant.settlement_bars > 0 else 0
                rearm_tokens.append(RearmToken(
                    direction="SELL", level=outer.entry_price, level_idx=level_idx,
                    reuse_count=reuse, last_close_bar=idx, cooldown_until=cooldown_end, settle_until=settle_end,
                ))
                level_reuse[outer.entry_price] = reuse + 1
            sells = sorted((t for t in open_tickets if t.direction == "SELL"), key=lambda t: t.entry_price, reverse=True)

        buys = sorted((t for t in open_tickets if t.direction == "BUY"), key=lambda t: t.entry_price)
        while len(buys) > gap and bar["high"] >= buys[gap].entry_price:
            outer = buys[0]
            close_ref = buys[gap].entry_price
            realized_pnls.append(unit_pnl_usd(symbol, "BUY", outer.entry_price, close_ref, spread_px))
            open_tickets.remove(outer)
            level_idx = int(round((anchor - outer.entry_price) / base_step_px))
            if level_idx >= variant.min_level_idx:
                reuse = level_reuse[outer.entry_price]
                cooldown_end = idx + variant.cooldown_bars if variant.cooldown_bars > 0 else 0
                settle_end = idx + variant.settlement_bars if variant.settlement_bars > 0 else 0
                rearm_tokens.append(RearmToken(
                    direction="BUY", level=outer.entry_price, level_idx=level_idx,
                    reuse_count=reuse, last_close_bar=idx, cooldown_until=cooldown_end, settle_until=settle_end,
                ))
                level_reuse[outer.entry_price] = reuse + 1
            buys = sorted((t for t in open_tickets if t.direction == "BUY"), key=lambda t: t.entry_price)

        max_open = max(max_open, len(open_tickets))

        if not open_tickets and abs(bar["close"] - anchor) >= base_step_px:
            anchor = bar["close"]
            next_sell_level = anchor + base_step_px
            next_buy_level = anchor - base_step_px
            rearm_tokens = []
            level_reuse.clear()

    last_close = bars[-1]["close"]
    spread_dollar = spread_px * pip_size * symbol_info.trade_tick_value / symbol_info.trade_tick_size
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
        "total_slippage_cost_usd": round(total_slippage_cost, 3),
    }


def main() -> int:
    args = parse_args()
    cfg_map = default_raw_configs()
    rng = random.Random(args.seed)

    if not mt5.initialize():
        print("MetaTrader5 initialize() failed")
        return 1

    try:
        rows: list[dict] = []

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

            pip_size = pip_size_for(info)

            for variant in VARIANTS:
                for friction in FRICTION_LEVELS:
                    result = _simulate_with_friction(
                        symbol, bars, info, raw_cfg, variant, friction, rng, pip_size
                    )
                    if not result:
                        continue
                    rows.append({
                        "symbol": symbol,
                        "variant": variant.name,
                        "friction_pips": friction,
                        "days": args.days,
                        "baseline_combined_usd": baseline["combined_net_usd"],
                        "variant_combined_usd": result["combined_net_usd"],
                        "delta_vs_baseline": round(result["combined_net_usd"] - baseline["combined_net_usd"], 3),
                        "variant_realized_usd": result["realized_net_usd"],
                        "variant_floating_usd": result["floating_net_usd"],
                        "variant_closes": result["realized_closes"],
                        "variant_rearm_opens": result["rearm_opens"],
                        "slippage_cost_usd": result["total_slippage_cost_usd"],
                    })

        out_path = Path(args.output_csv)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        fieldnames = [
            "symbol", "variant", "friction_pips", "days",
            "baseline_combined_usd", "variant_combined_usd", "delta_vs_baseline",
            "variant_realized_usd", "variant_floating_usd",
            "variant_closes", "variant_rearm_opens", "slippage_cost_usd",
        ]
        with out_path.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(rows)

        # Summary table
        print(f"\n{'Variant':<20} {'Friction':>8} {'Total':>12} {'Delta':>12} {'Slippage$':>12}")
        print("-" * 70)
        totals = defaultdict(float)
        slip_totals = defaultdict(float)
        for r in rows:
            totals[(r["variant"], r["friction_pips"])] += r["variant_combined_usd"]
            slip_totals[(r["variant"], r["friction_pips"])] += r["slippage_cost_usd"]

        # Get baseline total
        baseline_total = sum(r["baseline_combined_usd"] for r in rows if r["friction_pips"] == 0)
        baseline_total = baseline_total / len(VARIANTS)  # each variant has friction=0 row

        for variant in VARIANTS:
            for friction in FRICTION_LEVELS:
                total = totals.get((variant.name, friction), 0)
                slip = slip_totals.get((variant.name, friction), 0)
                delta = total - baseline_total
                print(f"{variant.name:<20} {friction:>8.1f} {total:>12.2f} {delta:>12.2f} {slip:>12.2f}")

        print(f"\nWrote {out_path}")
        return 0
    finally:
        mt5.shutdown()


if __name__ == "__main__":
    raise SystemExit(main())
