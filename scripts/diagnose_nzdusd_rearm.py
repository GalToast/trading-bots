#!/usr/bin/env python3
"""Diagnose why NZDUSD re-arm fails: analyze per-close PnL distribution, oscillation patterns, and re-arm token behavior."""
from __future__ import annotations

import argparse
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

import MetaTrader5 as mt5

from benchmark_inside_geometry_churn import default_raw_configs
from penetration_lattice_hybrid_apex import RawConfig, simulate_raw_close2
from penetration_lattice_lab_v2 import Ticket, dynamic_step, load_bars, pip_size_for, spread_price, unit_pnl_usd


ROOT = Path(__file__).resolve().parent.parent


@dataclass
class RearmToken:
    direction: str
    level: float
    level_idx: int
    armed: bool = False


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


def diagnose_nzdusd(symbol: str, bars: list[dict], symbol_info, cfg: RawConfig, min_level_idx: int, excursion_levels: int) -> dict:
    if not bars:
        return {}

    pip_size = pip_size_for(symbol_info)
    spread_px = spread_price(symbol_info)
    base_step_px = cfg.step_pips * pip_size

    print(f"\n{'='*60}")
    print(f"  NZDUSD Diagnostics — step={cfg.step_pips}pips, spread={spread_px:.5f}, pip={pip_size:.5f}")
    print(f"  base_step_px = {base_step_px:.5f}, excursion = {excursion_levels} levels = {excursion_levels * base_step_px:.5f}")
    print(f"{'='*60}")

    # Analyze bar ranges
    highs = [b["high"] for b in bars]
    lows = [b["low"] for b in bars]
    closes = [b["close"] for b in bars]
    bar_ranges = [(h - l) for h, l in zip(highs, lows)]
    avg_range = sum(bar_ranges) / len(bar_ranges) if bar_ranges else 0
    avg_range_pips = avg_range / pip_size

    print(f"\nBar stats (60d):")
    print(f"  Total bars: {len(bars)}")
    print(f"  Avg bar range: {avg_range:.5f} ({avg_range_pips:.1f} pips)")
    print(f"  Max bar range: {max(bar_ranges):.5f} ({max(bar_ranges)/pip_size:.1f} pips)")
    print(f"  Price range: {min(lows):.5f} – {max(highs):.5f} ({(max(highs)-min(lows))/pip_size:.0f} pips)")

    # Simulate with detailed tracing
    anchor = bars[0]["close"]
    next_sell_level = anchor + base_step_px
    next_buy_level = anchor - base_step_px
    adapt_cfg = _make_adapt_cfg()

    open_tickets: list[Ticket] = []
    realized_pnls: list[float] = []
    rearm_tokens: list[RearmToken] = []
    rearm_pnls: list[float] = []  # track PnL of rearm closes separately
    baseline_closes_pnls: list[float] = []  # closes that are not from rearm
    rearm_opens = 0
    rearm_closes = 0
    tokens_created = 0
    tokens_armed = 0
    tokens_consumed = 0
    tokens_expired = 0

    # Track: when a close happens, was it a rearm reopen or baseline?
    close_types = Counter()

    for idx in range(1, len(bars)):
        bar = bars[idx]

        _update_token_arming(rearm_tokens, bar, base_step_px, excursion_levels)

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
            pnl = unit_pnl_usd(symbol, "SELL", outer.entry_price, close_ref, spread_px)
            realized_pnls.append(pnl)
            is_rearm = any(t.level == outer.entry_price and not t.armed for t in [])  # can't tell from ticket alone
            open_tickets.remove(outer)
            level_idx = int(round((outer.entry_price - anchor) / base_step_px))
            if level_idx >= min_level_idx:
                rearm_tokens.append(RearmToken(direction="SELL", level=outer.entry_price, level_idx=level_idx))
                tokens_created += 1
            sells = sorted((t for t in open_tickets if t.direction == "SELL"), key=lambda t: t.entry_price, reverse=True)

        buys = sorted((t for t in open_tickets if t.direction == "BUY"), key=lambda t: t.entry_price)
        while len(buys) > gap and bar["high"] >= buys[gap].entry_price:
            outer = buys[0]
            close_ref = buys[gap].entry_price
            pnl = unit_pnl_usd(symbol, "BUY", outer.entry_price, close_ref, spread_px)
            realized_pnls.append(pnl)
            open_tickets.remove(outer)
            level_idx = int(round((anchor - outer.entry_price) / base_step_px))
            if level_idx >= min_level_idx:
                rearm_tokens.append(RearmToken(direction="BUY", level=outer.entry_price, level_idx=level_idx))
                tokens_created += 1
            buys = sorted((t for t in open_tickets if t.direction == "BUY"), key=lambda t: t.entry_price)

        if not open_tickets and abs(bar["close"] - anchor) >= base_step_px:
            anchor = bar["close"]
            next_sell_level = anchor + base_step_px
            next_buy_level = anchor - base_step_px
            tokens_expired += len(rearm_tokens)
            rearm_tokens = []

    # Count token arming rate
    # Re-run simplified just for token arming stats
    anchor = bars[0]["close"]
    base_step_px2 = cfg.step_pips * pip_size_for(symbol_info)
    rearm_tokens2: list[RearmToken] = []
    armed_count = 0
    for idx in range(1, len(bars)):
        bar = bars[idx]
        for token in list(rearm_tokens2):
            if not token.armed:
                if token.direction == "SELL":
                    away = token.level - (excursion_levels * base_step_px2)
                    if bar["low"] <= away:
                        token.armed = True
                        armed_count += 1
                else:
                    away = token.level + (excursion_levels * base_step_px2)
                    if bar["high"] >= away:
                        token.armed = True
                        armed_count += 1
        # Simulate token creation (simplified)
        # Just count what we have

    # Analyze PnL distribution
    wins = [p for p in realized_pnls if p > 0]
    losses = [p for p in realized_pnls if p <= 0]
    avg_win = sum(wins) / len(wins) if wins else 0
    avg_loss = sum(losses) / len(losses) if losses else 0

    print(f"\nPnL distribution ({len(realized_pnls)} closes):")
    print(f"  Wins: {len(wins)} ({len(wins)/len(realized_pnls)*100:.1f}%), avg ${avg_win:.4f}")
    print(f"  Losses: {len(losses)} ({len(losses)/len(realized_pnls)*100:.1f}%), avg ${avg_loss:.4f}")
    print(f"  Total realized: ${sum(realized_pnls):.2f}")
    print(f"  Expectancy per close: ${sum(realized_pnls)/len(realized_pnls):.4f}")

    # Compare to baseline
    baseline = simulate_raw_close2(symbol, bars, symbol_info, cfg)
    bl_closes = baseline["realized_closes"]
    bl_realized = baseline["realized_net_usd"]
    bl_expectancy = bl_realized / bl_closes if bl_closes else 0

    print(f"\nBaseline comparison:")
    print(f"  Baseline: {bl_closes} closes, ${bl_realized:.2f}, expectancy ${bl_expectancy:.4f}/close")
    print(f"  Rearm:    {len(realized_pnls)} closes, ${sum(realized_pnls):.2f}, expectancy ${sum(realized_pnls)/len(realized_pnls):.4f}/close")
    print(f"  Extra closes from rearm: {len(realized_pnls) - bl_closes}")
    print(f"  Extra PnL from rearm: ${sum(realized_pnls) - bl_realized:.2f}")
    print(f"  Expectancy delta: ${sum(realized_pnls)/len(realized_pnls) - bl_expectancy:.4f}/close")

    # The key question: does NZDUSD re-arm create too many extra closes with negative expectancy?
    print(f"\nToken stats:")
    print(f"  Tokens created: {tokens_created}")
    print(f"  Tokens expired (anchor reset): {tokens_expired}")
    print(f"  Rearm opens: {rearm_opens}")
    print(f"  Consumed / Created ratio: {rearm_opens}/{tokens_created} = {rearm_opens/tokens_created*100:.1f}%" if tokens_created else "  No tokens created")

    # Spread cost analysis
    spread_cost_per_close = spread_px * unit_pnl_usd(symbol, "SELL", 1.0, 1.0 - spread_px, spread_px)  # rough
    print(f"\nSpread analysis:")
    print(f"  Spread: {spread_px:.5f} ({spread_px/pip_size:.1f} pips)")
    print(f"  Avg close PnL (gross): ${sum(realized_pnls)/len(realized_pnls):.4f}")
    print(f"  Spread cost per unit: ~${spread_px * 100000 * 0.0001:.4f}")  # rough per standard lot

    return {
        "tokens_created": tokens_created,
        "rearm_opens": rearm_opens,
        "total_closes": len(realized_pnls),
        "realized_pnl": sum(realized_pnls),
        "baseline_closes": bl_closes,
        "baseline_pnl": bl_realized,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--symbol", default="NZDUSD")
    parser.add_argument("--days", type=int, default=60)
    parser.add_argument("--min-level-idx", type=int, default=2)
    parser.add_argument("--excursion-levels", type=int, default=2)
    args = parser.parse_args()

    cfg_map = default_raw_configs()

    if not mt5.initialize():
        print("MetaTrader5 initialize() failed")
        return 1

    try:
        symbol = args.symbol
        info = mt5.symbol_info(symbol)
        if info is None:
            print(f"Symbol {symbol} not found")
            return 1
        bars = load_bars(symbol, args.days)
        raw_cfg = RawConfig(
            step_pips=cfg_map[symbol].step_pips,
            max_open_per_side=cfg_map[symbol].max_open_per_side,
            close_mode=cfg_map[symbol].close_mode,
        )

        diagnose_nzdusd(symbol, bars, info, raw_cfg, args.min_level_idx, args.excursion_levels)

        return 0
    finally:
        mt5.shutdown()


if __name__ == "__main__":
    raise SystemExit(main())
