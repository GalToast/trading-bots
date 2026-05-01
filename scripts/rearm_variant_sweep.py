#!/usr/bin/env python3
"""
Rearm variant sweep — test ALL 6 canonical rearm variants across GBPUSD, EURUSD, NZDUSD.

Variants:
  lvl1_exc1, lvl1_exc2, lvl2_exc1, lvl2_exc2, lvl3_exc1, lvl3_exc2

Each variant is defined by (min_level_idx, excursion_levels):
  lvl1_exc1: min_level_idx=1, excursion_levels=1
  lvl1_exc2: min_level_idx=1, excursion_levels=2
  lvl2_exc1: min_level_idx=2, excursion_levels=1
  lvl2_exc2: min_level_idx=2, excursion_levels=2  (current live default)
  lvl3_exc1: min_level_idx=3, excursion_levels=1
  lvl3_exc2: min_level_idx=3, excursion_levels=2

Uses the StatefulRearmRawEngine simulation pattern (M1 bars, 60 days, 0.01 lot)
with symbol-adaptive step_pips matching live configs.

Output: reports/rearm_variant_sweep.csv
"""
from __future__ import annotations

import argparse
import csv
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import MetaTrader5 as mt5

from penetration_lattice_hybrid_apex import RawConfig
from penetration_lattice_lab_v2 import (
    Ticket,
    dynamic_step,
    load_bars,
    pip_size_for,
    spread_price,
    unit_pnl_usd,
)


ROOT = Path(__file__).resolve().parent.parent

SYMBOLS = ["GBPUSD", "EURUSD", "NZDUSD"]

# Live-config-matching step_pips
SYMBOL_STEP_PIPS = {
    "GBPUSD": 2.0,
    "EURUSD": 3.0,
    "NZDUSD": 1.5,
}

# Max open per side matching live apex configs
SYMBOL_MAX_OPEN = {
    "GBPUSD": 20,
    "EURUSD": 20,
    "NZDUSD": 12,
}


@dataclass(frozen=True)
class RearmVariant:
    name: str
    min_level_idx: int
    excursion_levels: int


ALL_REARM_VARIANTS = [
    RearmVariant(name="lvl1_exc1", min_level_idx=1, excursion_levels=1),
    RearmVariant(name="lvl1_exc2", min_level_idx=1, excursion_levels=2),
    RearmVariant(name="lvl2_exc1", min_level_idx=2, excursion_levels=1),
    RearmVariant(name="lvl2_exc2", min_level_idx=2, excursion_levels=2),
    RearmVariant(name="lvl3_exc1", min_level_idx=3, excursion_levels=1),
    RearmVariant(name="lvl3_exc2", min_level_idx=3, excursion_levels=2),
]


@dataclass
class RearmToken:
    direction: str
    level: float
    level_idx: int
    armed: bool = False
    cooldown_until_time: int = 0


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


def _bar_reaches_price_level(
    direction: str,
    level_price: float,
    bar: dict[str, Any],
    *,
    spread_px: float,
    purpose: str,
) -> bool:
    """Intrabar reach check (simplified, no broker_touch realism for sweep)."""
    direction_norm = str(direction or "").upper()
    if direction_norm == "SELL":
        if purpose == "open":
            return float(bar["high"]) >= float(level_price)
        return float(bar["low"]) <= float(level_price)
    # BUY
    if purpose == "close":
        return float(bar["high"]) >= float(level_price)
    return float(bar["low"]) <= float(level_price)


def _update_token_arming(tokens: list[RearmToken], bar: dict, base_step_px: float, variant: RearmVariant) -> None:
    for token in tokens:
        if token.armed:
            continue
        if int(bar["time"]) < int(token.cooldown_until_time or 0):
            continue
        if token.direction == "SELL":
            away_trigger = token.level - (variant.excursion_levels * base_step_px)
            if bar["low"] <= away_trigger:
                token.armed = True
        else:
            away_trigger = token.level + (variant.excursion_levels * base_step_px)
            if bar["high"] >= away_trigger:
                token.armed = True


def _consume_rearm_tokens(
    *,
    tokens: list[RearmToken],
    bar: dict[str, Any],
    tickets: list[Ticket],
    direction: str,
    max_open_per_side: int,
    spread_px: float,
) -> list[Ticket]:
    rearm_count = sum(1 for t in tickets if t.direction == direction and getattr(t, 'from_rearm', False))
    opened: list[Ticket] = []
    for token in list(tokens):
        if token.direction != direction or not token.armed:
            continue
        if rearm_count >= max_open_per_side:
            break
        if _bar_reaches_price_level(direction, token.level, bar, spread_px=spread_px, purpose="open"):
            ticket = Ticket(direction=direction, entry_price=token.level, opened_idx=0)
            setattr(ticket, 'from_rearm', True)
            tickets.append(ticket)
            tokens.remove(token)
            rearm_count += 1
            opened.append(ticket)
    return opened


def simulate_rearm_variant(
    symbol: str,
    bars: list[dict],
    symbol_info,
    cfg: RawConfig,
    variant: RearmVariant,
) -> dict:
    """Simulate a single symbol + rearm variant combination using StatefulRearmRawEngine pattern."""
    if not bars:
        return {}

    pip_size = pip_size_for(symbol_info)
    spread_px = spread_price(symbol_info)
    base_step_px = cfg.step_pips * pip_size
    adapt_cfg = _make_adapt_cfg()

    anchor = bars[0]["close"]
    next_sell_level = anchor + base_step_px
    next_buy_level = anchor - base_step_px

    open_tickets: list[Ticket] = []
    realized_pnls: list[float] = []
    rearm_tokens: list[RearmToken] = []
    rearm_opens = 0
    fires = 0  # total closes
    max_open_total = 0
    anchor_resets = 0

    for idx in range(1, len(bars)):
        bar = bars[idx]

        # Update rearm token arming
        _update_token_arming(rearm_tokens, bar, base_step_px, variant)

        # Count main lattice positions (exclude rearm-origin)
        open_sell_main = sum(1 for t in open_tickets if t.direction == "SELL" and not getattr(t, 'from_rearm', False))
        open_buy_main = sum(1 for t in open_tickets if t.direction == "BUY" and not getattr(t, 'from_rearm', False))

        current_sell_step = dynamic_step(base_step_px, open_sell_main, adapt_cfg)
        current_buy_step = dynamic_step(base_step_px, open_buy_main, adapt_cfg)

        # Open main lattice SELL orders
        while bar["high"] >= next_sell_level and open_sell_main < cfg.max_open_per_side:
            ticket = Ticket(direction="SELL", entry_price=next_sell_level, opened_idx=idx)
            setattr(ticket, 'from_rearm', False)
            open_tickets.append(ticket)
            open_sell_main += 1
            current_sell_step = dynamic_step(base_step_px, open_sell_main, adapt_cfg)
            next_sell_level += current_sell_step

        # Open main lattice BUY orders
        while bar["low"] <= next_buy_level and open_buy_main < cfg.max_open_per_side:
            ticket = Ticket(direction="BUY", entry_price=next_buy_level, opened_idx=idx)
            setattr(ticket, 'from_rearm', False)
            open_tickets.append(ticket)
            open_buy_main += 1
            current_buy_step = dynamic_step(base_step_px, open_buy_main, adapt_cfg)
            next_buy_level -= current_buy_step

        # Consume rearm tokens
        rearm_sell_opens = _consume_rearm_tokens(
            tokens=rearm_tokens, bar=bar, tickets=open_tickets, direction="SELL",
            max_open_per_side=cfg.max_open_per_side, spread_px=spread_px,
        )
        rearm_buy_opens = _consume_rearm_tokens(
            tokens=rearm_tokens, bar=bar, tickets=open_tickets, direction="BUY",
            max_open_per_side=cfg.max_open_per_side, spread_px=spread_px,
        )
        rearm_opens += len(rearm_sell_opens) + len(rearm_buy_opens)

        # Close logic: two_level penetration (gap=2)
        gap = 2  # close_mode="two_level" for all three symbols

        # Close SELL side
        sells = sorted((t for t in open_tickets if t.direction == "SELL"), key=lambda t: t.entry_price, reverse=True)
        while len(sells) > gap and bar["low"] <= sells[gap].entry_price:
            outer = sells[0]
            close_ref = sells[gap].entry_price  # penetration-level close (no alpha extension)
            pnl = unit_pnl_usd(symbol, "SELL", outer.entry_price, close_ref, spread_px)
            realized_pnls.append(pnl)
            open_tickets.remove(outer)
            fires += 1

            # Create rearm token if level qualifies
            level_idx = int(round((outer.entry_price - anchor) / base_step_px))
            if level_idx >= variant.min_level_idx:
                rearm_tokens.append(
                    RearmToken(
                        direction="SELL",
                        level=outer.entry_price,
                        level_idx=level_idx,
                        cooldown_until_time=0,
                    )
                )
            sells = sorted((t for t in open_tickets if t.direction == "SELL"), key=lambda t: t.entry_price, reverse=True)

        # Close BUY side
        buys = sorted((t for t in open_tickets if t.direction == "BUY"), key=lambda t: t.entry_price)
        while len(buys) > gap and bar["high"] >= buys[gap].entry_price:
            outer = buys[0]
            close_ref = buys[gap].entry_price
            pnl = unit_pnl_usd(symbol, "BUY", outer.entry_price, close_ref, spread_px)
            realized_pnls.append(pnl)
            open_tickets.remove(outer)
            fires += 1

            level_idx = int(round((anchor - outer.entry_price) / base_step_px))
            if level_idx >= variant.min_level_idx:
                rearm_tokens.append(
                    RearmToken(
                        direction="BUY",
                        level=outer.entry_price,
                        level_idx=level_idx,
                        cooldown_until_time=0,
                    )
                )
            buys = sorted((t for t in open_tickets if t.direction == "BUY"), key=lambda t: t.entry_price)

        # Track max open
        max_open_total = max(max_open_total, len(open_tickets))

        # Anchor reset when book is empty
        if not open_tickets and abs(bar["close"] - anchor) >= base_step_px:
            anchor = bar["close"]
            next_sell_level = anchor + base_step_px
            next_buy_level = anchor - base_step_px
            anchor_resets += 1
            rearm_tokens = []  # clear stale tokens on anchor reset

    # End-of-sample: mark remaining open at last close
    last_close = bars[-1]["close"]
    floating_pnls = [
        unit_pnl_usd(symbol, t.direction, t.entry_price, last_close, spread_px)
        for t in open_tickets
    ]

    realized_net = sum(realized_pnls)
    floating_net = sum(floating_pnls)
    combined_net = realized_net + floating_net

    wins = sum(1 for p in realized_pnls if p > 0)
    total_closes = len(realized_pnls)
    wr_pct = round(wins / total_closes * 100.0, 1) if total_closes else 0.0
    avg_usd_per_close = round(realized_net / total_closes, 4) if total_closes else 0.0

    return {
        "symbol": symbol,
        "rearm_variant": variant.name,
        "combined_usd": round(combined_net, 3),
        "closes": total_closes,
        "fires": fires,
        "wr_pct": wr_pct,
        "avg_usd_per_close": avg_usd_per_close,
        "max_open_total": max_open_total,
        "anchor_resets": anchor_resets,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Rearm variant sweep for penetration lattice.")
    parser.add_argument("--symbols", nargs="*", default=SYMBOLS)
    parser.add_argument("--days", type=int, default=60)
    parser.add_argument(
        "--output-csv",
        default=str(ROOT / "reports" / "rearm_variant_sweep.csv"),
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()

    if not mt5.initialize():
        print("ERROR: MetaTrader5 initialize() failed. Ensure MT5 terminal is installed and accessible.")
        return 1

    try:
        rows: list[dict] = []

        for symbol in args.symbols:
            info = mt5.symbol_info(symbol)
            if info is None:
                print(f"WARNING: symbol_info({symbol}) returned None, skipping.")
                continue

            bars = load_bars(symbol, args.days)
            if not bars:
                print(f"WARNING: No bars loaded for {symbol} (days={args.days}), skipping.")
                continue

            step_pips = SYMBOL_STEP_PIPS.get(symbol, 1.0)
            max_open = SYMBOL_MAX_OPEN.get(symbol, 20)

            print(f"\n{'='*80}")
            print(f"  {symbol}  step_pips={step_pips}  max_open={max_open}  bars={len(bars)}")
            print(f"{'='*80}")

            for variant in ALL_REARM_VARIANTS:
                cfg = RawConfig(
                    step_pips=step_pips,
                    max_open_per_side=max_open,
                    close_mode="two_level",
                )

                result = simulate_rearm_variant(symbol, bars, info, cfg, variant)
                if not result:
                    continue

                rows.append(result)

                print(
                    f"  {variant.name:<14}  combined={result['combined_usd']:>+8.2f}  "
                    f"closes={result['closes']:>5}  fires={result['fires']:>5}  "
                    f"wr={result['wr_pct']:>5.1f}%  "
                    f"avg=${result['avg_usd_per_close']:+.4f}  "
                    f"max_open={result['max_open_total']:>3}  "
                    f"resets={result['anchor_resets']:>4}"
                )

        # Write output CSV
        out_path = Path(args.output_csv)
        out_path.parent.mkdir(parents=True, exist_ok=True)

        fieldnames = [
            "symbol", "rearm_variant", "combined_usd", "closes", "fires",
            "wr_pct", "avg_usd_per_close", "max_open_total", "anchor_resets",
        ]
        with out_path.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(rows)

        print(f"\n{'='*80}")
        print(f"Saved {out_path} ({len(rows)} rows)")

        # Print a compact summary table
        print(f"\n{'Symbol':<10} {'Variant':<14} {'Combined $':>12} {'Closes':>7} {'WR%':>6} {'Avg $/close':>12} {'Max Open':>9}")
        print("-" * 80)
        for row in rows:
            print(
                f"{row['symbol']:<10} {row['rearm_variant']:<14} "
                f"{row['combined_usd']:>+12.2f} {row['closes']:>7} "
                f"{row['wr_pct']:>5.1f}% {row['avg_usd_per_close']:>+12.4f} "
                f"{row['max_open_total']:>9}"
            )

        # Highlight best variant per symbol
        print(f"\n{'='*80}")
        print("BEST VARIANT PER SYMBOL (by combined_usd):")
        print(f"{'='*80}")
        for symbol in args.symbols:
            symbol_rows = [r for r in rows if r["symbol"] == symbol]
            if not symbol_rows:
                continue
            best = max(symbol_rows, key=lambda r: r["combined_usd"])
            print(f"  {symbol}: {best['rearm_variant']:>14}  combined=${best['combined_usd']:+.2f}  closes={best['closes']}  wr={best['wr_pct']:.1f}%")

        return 0
    finally:
        mt5.shutdown()


if __name__ == "__main__":
    raise SystemExit(main())
