#!/usr/bin/env python3
"""
Next-gen stateful rearm sweep — testing symbol-adaptive gating, entry decay,
cooldown windows, and NZDUSD baseline-only (kill switch).

Building on the original stateful_rearm_churn results which showed:
- GBPUSD: 2.1x baseline with rearm
- EURUSD: 2.3x baseline with rearm
- NZDUSD: -$1,053 floating drag with rearm (baseline was +$1,467)

Hypothesis: symbol-specific tuning + entry decay can fix NZDUSD bleed
while preserving the GBPUSD/EURUSD amplification.
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


@dataclass(frozen=True)
class Variant:
    name: str
    # Symbol-specific excursion levels: {symbol: levels} or None for uniform
    symbol_excursion: dict[str, int] | None = None
    # Uniform excursion (fallback if symbol_excursor doesn't match)
    excursion_levels: int = 1
    # Minimum level index to rearm
    min_level_idx: int = 1
    # Entry decay: size multiplier per reuse (1.0 = no decay, 0.75 = 25% reduction per reuse)
    entry_decay_factor: float = 1.0
    # Cooldown: bars to wait before a level can rearm again (0 = no cooldown)
    cooldown_bars: int = 0
    # Skip rearm entirely for these symbols (use baseline entry only)
    skip_symbols: set[str] = field(default_factory=set)
    # Momentum gate: require bar to close in profitable direction for rearm entry
    momentum_gate: bool = False
    # Settlement delay: bars to wait after momentum gate signal before entry (realistic fill)
    settlement_bars: int = 0
    # Close alpha: extend close reference beyond penetration level (0.0 = penetration only, 0.5 = mid-bar)
    close_alpha: float = 0.0


VARIANTS = [
    # Baseline re-confirmation (should match original rearm_lvl2_exc1)
    Variant(name="rearm_v2_baseline", excursion_levels=1, min_level_idx=2),

    # 1. Symbol-adaptive excursion: NZDUSD needs wider gating
    Variant(
        name="rearm_sym_adaptive_exc3",
        symbol_excursion={"NZDUSD": 3, "GBPUSD": 1, "EURUSD": 1},
        min_level_idx=2,
    ),
    Variant(
        name="rearm_sym_adaptive_exc4",
        symbol_excursion={"NZDUSD": 4, "GBPUSD": 1, "EURUSD": 1},
        min_level_idx=2,
    ),
    Variant(
        name="rearm_sym_adaptive_exc5",
        symbol_excursion={"NZDUSD": 5, "GBPUSD": 1, "EURUSD": 1},
        min_level_idx=2,
    ),

    # 2. Entry decay: 25% size reduction per reuse
    Variant(
        name="rearm_decay_25pct",
        excursion_levels=1,
        min_level_idx=2,
        entry_decay_factor=0.75,
    ),
    Variant(
        name="rearm_decay_50pct",
        excursion_levels=1,
        min_level_idx=2,
        entry_decay_factor=0.50,
    ),

    # 3. Cooldown: require 6 or 12 bars before same level can rearm
    Variant(
        name="rearm_cooldown_6bar",
        excursion_levels=1,
        min_level_idx=2,
        cooldown_bars=6,
    ),
    Variant(
        name="rearm_cooldown_12bar",
        excursion_levels=1,
        min_level_idx=2,
        cooldown_bars=12,
    ),

    # 4. Combo: symbol-adaptive + decay
    Variant(
        name="rearm_sym_exc3_decay25",
        symbol_excursion={"NZDUSD": 3, "GBPUSD": 1, "EURUSD": 1},
        min_level_idx=2,
        entry_decay_factor=0.75,
    ),
    Variant(
        name="rearm_sym_exc4_decay25",
        symbol_excursion={"NZDUSD": 4, "GBPUSD": 1, "EURUSD": 1},
        min_level_idx=2,
        entry_decay_factor=0.75,
    ),

    # 5. Combo: symbol-adaptive + cooldown
    Variant(
        name="rearm_sym_exc3_cool6",
        symbol_excursion={"NZDUSD": 3, "GBPUSD": 1, "EURUSD": 1},
        min_level_idx=2,
        cooldown_bars=6,
    ),
    Variant(
        name="rearm_sym_exc4_cool6",
        symbol_excursion={"NZDUSD": 4, "GBPUSD": 1, "EURUSD": 1},
        min_level_idx=2,
        cooldown_bars=6,
    ),

    # 6. NZDUSD kill: skip rearm on NZDUSD, baseline-only
    Variant(
        name="rearm_nzd_baseline_only",
        excursion_levels=1,
        min_level_idx=2,
        skip_symbols={"NZDUSD"},
    ),

    # 7. Momentum gate: only enter rearm if bar closes in profitable direction
    Variant(
        name="rearm_momentum_gate",
        excursion_levels=1,
        min_level_idx=2,
        momentum_gate=True,
    ),
    # 7b. Momentum gate with 1-bar settlement delay (realistic)
    Variant(
        name="rearm_momentum_settle1",
        excursion_levels=1,
        min_level_idx=2,
        momentum_gate=True,
        settlement_bars=1,
    ),
    # 7c. Momentum gate with 6-bar cooldown (combo)
    Variant(
        name="rearm_momentum_cool6",
        excursion_levels=1,
        min_level_idx=2,
        momentum_gate=True,
        cooldown_bars=6,
    ),
    # 7d. Momentum gate with 12-bar cooldown (best cooldown variant combo)
    Variant(
        name="rearm_momentum_cool12",
        excursion_levels=1,
        min_level_idx=2,
        momentum_gate=True,
        cooldown_bars=12,
    ),
    # 7e. Momentum + 1-bar settle + 12-bar cooldown (triple combo)
    Variant(
        name="rearm_momentum_settle1_cool12",
        excursion_levels=1,
        min_level_idx=2,
        momentum_gate=True,
        settlement_bars=1,
        cooldown_bars=12,
    ),
    # 7f. Cooldown 12 + entry decay 50% (aggressive filtering)
    Variant(
        name="rearm_cool12_decay50",
        excursion_levels=1,
        min_level_idx=2,
        cooldown_bars=12,
        entry_decay_factor=0.50,
    ),

    # 8. Triple combo: sym-adaptive + decay + cooldown (aggressive filtering)
    Variant(
        name="rearm_sym_exc3_decay25_cool6",
        symbol_excursion={"NZDUSD": 3, "GBPUSD": 1, "EURUSD": 1},
        min_level_idx=2,
        entry_decay_factor=0.75,
        cooldown_bars=6,
    ),
    Variant(
        name="rearm_sym_exc4_decay25_cool6",
        symbol_excursion={"NZDUSD": 4, "GBPUSD": 1, "EURUSD": 1},
        min_level_idx=2,
        entry_decay_factor=0.75,
        cooldown_bars=6,
    ),

    # 9. Alpha combos: combining close extension with momentum/cooldown
    Variant(
        name="rearm_momentum_alpha25",
        excursion_levels=1,
        min_level_idx=2,
        momentum_gate=True,
        close_alpha=0.25,
    ),
    Variant(
        name="rearm_momentum_alpha50",
        excursion_levels=1,
        min_level_idx=2,
        momentum_gate=True,
        close_alpha=0.50,
    ),
    Variant(
        name="rearm_cool12_alpha25",
        excursion_levels=1,
        min_level_idx=2,
        cooldown_bars=12,
        close_alpha=0.25,
    ),
    Variant(
        name="rearm_cool12_alpha50",
        excursion_levels=1,
        min_level_idx=2,
        cooldown_bars=12,
        close_alpha=0.50,
    ),
    Variant(
        name="rearm_momentum_cool6_alpha25",
        excursion_levels=1,
        min_level_idx=2,
        momentum_gate=True,
        cooldown_bars=6,
        close_alpha=0.25,
    ),
    Variant(
        name="rearm_momentum_cool6_alpha50",
        excursion_levels=1,
        min_level_idx=2,
        momentum_gate=True,
        cooldown_bars=6,
        close_alpha=0.50,
    ),
    Variant(
        name="rearm_momentum_cool12_alpha25",
        excursion_levels=1,
        min_level_idx=2,
        momentum_gate=True,
        cooldown_bars=12,
        close_alpha=0.25,
    ),
    Variant(
        name="rearm_momentum_cool12_alpha50",
        excursion_levels=1,
        min_level_idx=2,
        momentum_gate=True,
        cooldown_bars=12,
        close_alpha=0.50,
    ),
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Sweep next-gen stateful rearm variants.")
    parser.add_argument("--symbols", nargs="*", default=DEFAULT_SYMBOLS)
    parser.add_argument("--days", type=int, default=60)
    parser.add_argument(
        "--output-csv",
        default=str(ROOT / "reports" / "stateful_rearm_v2_sweep.csv"),
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
    reuse_count: int = 0  # track how many times this level has been rearmed
    last_close_bar: int = 0  # bar index when token was created (for cooldown)
    cooldown_until: int = 0  # bar index when token becomes eligible again
    settle_until: int = 0  # bar index when settlement delay is satisfied


def _get_excursion_levels(variant: Variant, symbol: str) -> int:
    if variant.symbol_excursion and symbol in variant.symbol_excursion:
        return variant.symbol_excursion[symbol]
    return variant.excursion_levels


def _update_token_arming(
    tokens: list[RearmToken], bar: dict, base_step_px: float, variant: Variant, symbol: str, current_bar: int
) -> None:
    exc_levels = _get_excursion_levels(variant, symbol)
    for token in tokens:
        if token.armed:
            continue
        # Check cooldown — skip if still cooling down
        if variant.cooldown_bars > 0 and current_bar < token.cooldown_until:
            continue
        if token.direction == "SELL":
            away_trigger = token.level - (exc_levels * base_step_px)
            if bar["low"] <= away_trigger:
                token.armed = True
        else:
            away_trigger = token.level + (exc_levels * base_step_px)
            if bar["high"] >= away_trigger:
                token.armed = True


def _check_momentum_gate(bar: dict, direction: str, entry_price: float) -> bool:
    """Only allow rearm entry if bar closes in profitable direction."""
    if direction == "SELL":
        return bar["close"] < entry_price
    else:
        return bar["close"] > entry_price


def _consume_rearm_tokens(
    *,
    tokens: list[RearmToken],
    bar: dict,
    idx: int,
    tickets: list[Ticket],
    direction: str,
    max_open_per_side: int,
    variant: Variant,
    symbol: str,
) -> tuple[int, float]:
    """Returns (opened_count, effective_size_sum) for tracking."""
    open_count = _side_count(tickets, direction)
    opened = 0
    effective_size_sum = 0.0
    for token in list(tokens):
        if token.direction != direction or not token.armed:
            continue
        if open_count >= max_open_per_side:
            break
        # Check cooldown
        if idx < token.cooldown_until:
            continue
        # Check settlement delay
        if idx < token.settle_until:
            continue
        # Momentum gate: always check when entering (whether immediate or after settlement)
        if variant.momentum_gate:
            if not _check_momentum_gate(bar, direction, token.level):
                continue
        # Calculate effective size with decay
        decay = variant.entry_decay_factor ** token.reuse_count
        effective_size = max(0.1, decay)  # floor at 0.1x

        if direction == "SELL" and bar["high"] >= token.level:
            tickets.append(Ticket(direction="SELL", entry_price=token.level, opened_idx=idx))
            tokens.remove(token)
            open_count += 1
            opened += 1
            effective_size_sum += effective_size
        elif direction == "BUY" and bar["low"] <= token.level:
            tickets.append(Ticket(direction="BUY", entry_price=token.level, opened_idx=idx))
            tokens.remove(token)
            open_count += 1
            opened += 1
            effective_size_sum += effective_size
    return opened, effective_size_sum


def simulate_stateful_rearm_v2(
    symbol: str, bars: list[dict], symbol_info, cfg: RawConfig, variant: Variant
) -> dict:
    if not bars:
        return {}

    # Check if this symbol is skip-only (baseline)
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
    rearm_effective_size = 0.0  # sum of decay-adjusted sizes
    max_open = 0
    max_open_buy = 0
    max_open_sell = 0

    # Track level reuse counts for decay: level_price -> reuse_count
    level_reuse: dict[float, int] = defaultdict(int)

    for idx in range(1, len(bars)):
        bar = bars[idx]

        _update_token_arming(rearm_tokens, bar, base_step_px, variant, symbol, idx)

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

        opened, eff_size = _consume_rearm_tokens(
            tokens=rearm_tokens,
            bar=bar,
            idx=idx,
            tickets=open_tickets,
            direction="SELL",
            max_open_per_side=cfg.max_open_per_side,
            variant=variant,
            symbol=symbol,
        )
        rearm_opens += opened
        rearm_effective_size += eff_size

        opened, eff_size = _consume_rearm_tokens(
            tokens=rearm_tokens,
            bar=bar,
            idx=idx,
            tickets=open_tickets,
            direction="BUY",
            max_open_per_side=cfg.max_open_per_side,
            variant=variant,
            symbol=symbol,
        )
        rearm_opens += opened
        rearm_effective_size += eff_size

        gap = 1 if cfg.close_mode == "one_level" else 2

        sells = sorted((t for t in open_tickets if t.direction == "SELL"), key=lambda t: t.entry_price, reverse=True)
        while len(sells) > gap and bar["low"] <= sells[gap].entry_price:
            outer = sells[0]
            ref_level = sells[gap].entry_price
            close_ref = ref_level + (bar["low"] - ref_level) * variant.close_alpha
            realized_pnls.append(unit_pnl_usd(symbol, "SELL", outer.entry_price, close_ref, spread_px))
            open_tickets.remove(outer)
            level_idx = int(round((outer.entry_price - anchor) / base_step_px))
            if level_idx >= variant.min_level_idx:
                reuse = level_reuse[outer.entry_price]
                cooldown_end = idx + variant.cooldown_bars if variant.cooldown_bars > 0 else 0
                settle_end = idx + variant.settlement_bars if variant.settlement_bars > 0 else 0
                rearm_tokens.append(RearmToken(
                    direction="SELL",
                    level=outer.entry_price,
                    level_idx=level_idx,
                    reuse_count=reuse,
                    last_close_bar=idx,
                    cooldown_until=cooldown_end,
                    settle_until=settle_end,
                ))
                level_reuse[outer.entry_price] = reuse + 1
            sells = sorted((t for t in open_tickets if t.direction == "SELL"), key=lambda t: t.entry_price, reverse=True)

        buys = sorted((t for t in open_tickets if t.direction == "BUY"), key=lambda t: t.entry_price)
        while len(buys) > gap and bar["high"] >= buys[gap].entry_price:
            outer = buys[0]
            ref_level = buys[gap].entry_price
            close_ref = ref_level + (bar["high"] - ref_level) * variant.close_alpha
            realized_pnls.append(unit_pnl_usd(symbol, "BUY", outer.entry_price, close_ref, spread_px))
            open_tickets.remove(outer)
            level_idx = int(round((anchor - outer.entry_price) / base_step_px))
            if level_idx >= variant.min_level_idx:
                reuse = level_reuse[outer.entry_price]
                cooldown_end = idx + variant.cooldown_bars if variant.cooldown_bars > 0 else 0
                settle_end = idx + variant.settlement_bars if variant.settlement_bars > 0 else 0
                rearm_tokens.append(RearmToken(
                    direction="BUY",
                    level=outer.entry_price,
                    level_idx=level_idx,
                    reuse_count=reuse,
                    last_close_bar=idx,
                    cooldown_until=cooldown_end,
                    settle_until=settle_end,
                ))
                level_reuse[outer.entry_price] = reuse + 1
            buys = sorted((t for t in open_tickets if t.direction == "BUY"), key=lambda t:t.entry_price)

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
        "max_open_total": max_open,
        "rearm_opens": rearm_opens,
        "rearm_effective_size": round(rearm_effective_size, 2),
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
                result = simulate_stateful_rearm_v2(symbol, bars, info, raw_cfg, variant)
                if not result:
                    continue
                variant_totals[variant.name] += float(result["combined_net_usd"])
                variant_by_symbol[variant.name][symbol] = float(result["combined_net_usd"])

                # For skip-symbols, the "variant" is just baseline, so don't double-count
                if symbol not in variant.skip_symbols:
                    pass  # already counted above

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
                        "variant_max_open": result.get("max_open_total", ""),
                        "variant_rearm_opens": result.get("rearm_opens", ""),
                        "variant_rearm_effective_size": result.get("rearm_effective_size", ""),
                        "delta_combined_usd": round(result["combined_net_usd"] - baseline["combined_net_usd"], 3),
                    }
                )

        out_path = Path(args.output_csv)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        fieldnames = [
            "symbol", "variant", "days", "step_pips", "max_open_per_side",
            "baseline_combined_usd", "baseline_closes",
            "variant_combined_usd", "variant_realized_usd", "variant_floating_usd",
            "variant_closes", "variant_max_open", "variant_rearm_opens",
            "variant_rearm_effective_size", "delta_combined_usd",
        ]
        with out_path.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(rows)

        # Summary: totals per variant
        summary_path = out_path.with_name("stateful_rearm_v2_summary.csv")
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
        print(f"\n{'Variant':<35} {'Total':>12} {'Delta':>12} {'GBPUSD':>10} {'EURUSD':>10} {'NZDUSD':>10}")
        print("-" * 95)
        for variant in VARIANTS:
            gbp = variant_by_symbol[variant.name].get("GBPUSD", 0)
            eur = variant_by_symbol[variant.name].get("EURUSD", 0)
            nzd = variant_by_symbol[variant.name].get("NZDUSD", 0)
            delta = variant_totals[variant.name] - baseline_total
            print(
                f"{variant.name:<35} {variant_totals[variant.name]:>12.2f} {delta:>12.2f} "
                f"{gbp:>10.2f} {eur:>10.2f} {nzd:>10.2f}"
            )
        return 0
    finally:
        mt5.shutdown()


if __name__ == "__main__":
    raise SystemExit(main())
