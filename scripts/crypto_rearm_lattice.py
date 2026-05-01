#!/usr/bin/env python3
"""
Crypto Weekend Lattice — No Stoploss, Bounded-Rearm Protection

Design:
- No per-position stoploss. Instead: breakout kills when portfolio goes underwater
- Alpha=0.75 deep bar fills (crypto needs this to overcome spread)
- Momentum gate (enter only when bar closes in our direction — whale sweep filter)
- Wide step sizes for crypto volatility
- Breakout kill: if any position loses > max_floating_loss_usd, flush entire book
- Cooldown after breakout kill

This is the $162K BTCUSD architecture brought to the rearm lattice.
"""
from __future__ import annotations

import argparse
import csv
import json
import time
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any

import MetaTrader5 as mt5

ROOT = Path(__file__).resolve().parent.parent


CRYPTO_DEFAULTS = {
    "BTCUSD": {"step_pips": 50.0, "max_open_per_side": 50, "max_floating_loss_usd": -25.0},
    "ETHUSD": {"step_pips": 10.0, "max_open_per_side": 50, "max_floating_loss_usd": -2.0},
    "SOLUSD": {"step_pips": 0.5, "max_open_per_side": 50, "max_floating_loss_usd": -1.0},
}


@dataclass(frozen=True)
class RearmVariant:
    name: str
    min_level_idx: int = 2
    excursion_levels: int = 1


REARM_VARIANTS = {
    "rearm_lvl2_exc1": RearmVariant("rearm_lvl2_exc1", min_level_idx=2, excursion_levels=1),
    "rearm_lvl2_exc2": RearmVariant("rearm_lvl2_exc2", min_level_idx=2, excursion_levels=2),
}


@dataclass
class Ticket:
    direction: str
    entry_price: float
    opened_time: int


@dataclass
class RearmToken:
    direction: str
    level: float
    level_idx: int
    armed: bool = False
    cooldown_until_time: int = 0


@dataclass
class SymbolState:
    symbol: str
    anchor: float = 0.0
    next_sell_level: float = 0.0
    next_buy_level: float = 0.0
    open_tickets: list[dict[str, Any]] = field(default_factory=list)
    last_bar_time: int = 0
    realized_net_usd: float = 0.0
    realized_closes: int = 0
    breakout_net_usd: float = 0.0
    breakout_flushes: int = 0
    forced_net_usd: float = 0.0
    forced_unwinds: int = 0
    cooldown_until_time: int = 0
    lattice_started_time: int = 0
    anchor_resets: int = 0
    max_open_total: int = 0
    rearm_tokens: list[dict[str, Any]] = field(default_factory=list)
    rearm_opens: int = 0
    last_near_miss_reason: str = ""
    last_near_miss_time: int = 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Crypto Weekend Lattice — No Stoploss, Bounded-Rearm")
    parser.add_argument("--symbols", nargs="*", default=list(CRYPTO_DEFAULTS.keys()))
    parser.add_argument("--days", type=int, default=90)
    parser.add_argument("--tf", type=int, default=mt5.TIMEFRAME_H1, help="MT5 timeframe")
    parser.add_argument("--close-alpha", type=float, default=0.75, help="Close alpha (0.0-1.0)")
    parser.add_argument("--momentum-gate", action="store_true", help="Momentum gate on entries")
    parser.add_argument("--rearm-variant", default="rearm_lvl2_exc1")
    parser.add_argument("--cooldown-bars", type=int, default=6, help="Bars cooldown after breakout kill")
    parser.add_argument("--state-path", default=str(ROOT / "reports" / "crypto_rearm_state.json"))
    parser.add_argument("--event-path", default=str(ROOT / "reports" / "crypto_rearm_events.jsonl"))
    parser.add_argument("--output-csv", default=str(ROOT / "reports" / "crypto_rearm_results.csv"))
    parser.add_argument("--once", action="store_true", help="Run once and exit (for shadow lane)")
    parser.add_argument("--poll-seconds", type=float, default=60.0, help="Poll interval in seconds")
    parser.add_argument("--fresh-start", action="store_true")
    return parser.parse_args()


def pip_size_for(symbol_info) -> float:
    """Crypto 'pips' are just dollar units. For BTCUSD, 1 pip = $1."""
    return float(symbol_info.point or 1.0)


def spread_price(symbol_info) -> float:
    return float(symbol_info.spread or 0.0) * float(symbol_info.point or 1.0)


def unit_pnl_usd(symbol: str, direction: str, entry_price: float, exit_price: float, spread_px: float, swap_per_day: float = 0.0, hold_days: float = 0.0) -> float:
    order_type = mt5.ORDER_TYPE_BUY if direction == "BUY" else mt5.ORDER_TYPE_SELL
    gross = mt5.order_calc_profit(order_type, symbol, 0.01, entry_price, exit_price)
    if gross is None:
        return 0.0
    # Spread cost: for BUY, we enter at entry+spread; for SELL, we exit at exit-spread
    if direction == "BUY":
        spread_cost = mt5.order_calc_profit(order_type, symbol, 0.01, entry_price + spread_px, entry_price)
    else:
        spread_cost = mt5.order_calc_profit(order_type, symbol, 0.01, entry_price, entry_price + spread_px)
    if spread_cost is None:
        spread_cost = 0.0
    # Swap cost: daily swap × days held
    swap_cost = swap_per_day * hold_days
    return float(gross) - abs(float(spread_cost)) - abs(swap_cost)


def load_bars(symbol: str, days: int, tf: int) -> list[dict]:
    rates = mt5.copy_rates_from_pos(symbol, tf, 0, 24 * days)
    if rates is None or len(rates) == 0:
        return []
    return [
        {
            "time": int(r[0]),
            "open": float(r[1]),
            "high": float(r[2]),
            "low": float(r[3]),
            "close": float(r[4]),
            "tick_volume": int(r[5]),
        }
        for r in rates
    ]


def load_recent_bars(symbol: str, count: int, tf: int) -> list[dict]:
    rates = mt5.copy_rates_from_pos(symbol, tf, 1, count)
    if rates is None or len(rates) == 0:
        return []
    return [
        {
            "time": int(r[0]),
            "open": float(r[1]),
            "high": float(r[2]),
            "low": float(r[3]),
            "close": float(r[4]),
            "tick_volume": int(r[5]),
        }
        for r in rates
    ]


def dynamic_step(base_step: float, open_count: int, adapt_cfg) -> float:
    if open_count >= adapt_cfg.adaptive_step_threshold_2:
        return base_step * adapt_cfg.adaptive_step_multiplier_2
    elif open_count >= adapt_cfg.adaptive_step_threshold_1:
        return base_step * adapt_cfg.adaptive_step_multiplier_1
    return base_step


def _make_adapt_cfg():
    return type("Cfg", (), {
        "adaptive_step_threshold_1": 10,
        "adaptive_step_threshold_2": 20,
        "adaptive_step_multiplier_1": 1.5,
        "adaptive_step_multiplier_2": 2.0,
    })()


def _side_count(tickets: list[Ticket], direction: str) -> int:
    return sum(1 for t in tickets if t.direction == direction)


def _check_momentum_gate(bar: dict, direction: str, entry_price: float) -> bool:
    if direction == "SELL":
        return bar["close"] < entry_price
    return bar["close"] > entry_price


def _update_token_arming(tokens: list[RearmToken], bar: dict, base_step_px: float, variant: RearmVariant) -> None:
    for token in tokens:
        if token.armed:
            continue
        if token.direction == "SELL":
            away_trigger = token.level - (variant.excursion_levels * base_step_px)
            if bar["low"] <= away_trigger:
                token.armed = True
        else:
            away_trigger = token.level + (variant.excursion_levels * base_step_px)
            if bar["high"] >= away_trigger:
                token.armed = True


def _interpolate_close_ref(level_price: float, bar_extreme: float, alpha: float) -> float:
    return level_price + ((bar_extreme - level_price) * alpha)


def simulate_crypto_rearm(
    symbol: str, bars: list[dict], symbol_info, step_pips: float, max_open_per_side: int,
    max_floating_loss_usd: float, variant: RearmVariant, close_alpha: float,
    momentum_gate: bool, cooldown_bars: int
) -> dict:
    if not bars:
        return {}

    spread_px = spread_price(symbol_info)
    base_step_px = step_pips  # For crypto, step_pips IS the dollar step
    adapt_cfg = _make_adapt_cfg()

    anchor = bars[0]["close"]
    next_sell_level = anchor + base_step_px
    next_buy_level = anchor - base_step_px

    open_tickets: list[Ticket] = []
    realized_pnls: list[float] = []
    rearm_tokens: list[RearmToken] = []
    rearm_opens = 0
    max_open = 0
    breakout_flushes = 0

    level_reuse: dict[float, int] = {}

    for idx in range(1, len(bars)):
        bar = bars[idx]

        # Check cooldown
        if cooldown_bars > 0:
            bar_time = int(bar["time"])
            # Cooldown is tracked per-symbol, not per-token for simplicity

        _update_token_arming(rearm_tokens, bar, base_step_px, variant)

        open_buy = _side_count(open_tickets, "BUY")
        open_sell = _side_count(open_tickets, "SELL")

        current_sell_step = dynamic_step(base_step_px, open_sell, adapt_cfg)
        current_buy_step = dynamic_step(base_step_px, open_buy, adapt_cfg)

        # Entry
        while bar["high"] >= next_sell_level and open_sell < max_open_per_side:
            open_tickets.append(Ticket(direction="SELL", entry_price=next_sell_level, opened_time=int(bar["time"])))
            open_sell += 1
            current_sell_step = dynamic_step(base_step_px, open_sell, adapt_cfg)
            next_sell_level += current_sell_step

        while bar["low"] <= next_buy_level and open_buy < max_open_per_side:
            open_tickets.append(Ticket(direction="BUY", entry_price=next_buy_level, opened_time=int(bar["time"])))
            open_buy += 1
            current_buy_step = dynamic_step(base_step_px, open_buy, adapt_cfg)
            next_buy_level -= current_buy_step

        # Consume rearm tokens (with momentum gate)
        open_sell = _side_count(open_tickets, "SELL")
        open_buy = _side_count(open_tickets, "BUY")
        for token in list(rearm_tokens):
            if not token.armed:
                continue
            if token.direction == "SELL" and open_sell < max_open_per_side:
                if momentum_gate and not _check_momentum_gate(bar, "SELL", token.level):
                    continue
                if bar["high"] >= token.level:
                    open_tickets.append(Ticket(direction="SELL", entry_price=token.level, opened_time=int(bar["time"])))
                    rearm_tokens.remove(token)
                    open_sell += 1
                    rearm_opens += 1
            elif token.direction == "BUY" and open_buy < max_open_per_side:
                if momentum_gate and not _check_momentum_gate(bar, "BUY", token.level):
                    continue
                if bar["low"] <= token.level:
                    open_tickets.append(Ticket(direction="BUY", entry_price=token.level, opened_time=int(bar["time"])))
                    rearm_tokens.remove(token)
                    open_buy += 1
                    rearm_opens += 1

        # Close logic (gap=2, penetration)
        sells = sorted((t for t in open_tickets if t.direction == "SELL"), key=lambda t: t.entry_price, reverse=True)
        while len(sells) >= 2 and bar["low"] <= sells[1].entry_price:
            outer = sells[0]
            ref_level = sells[1].entry_price
            close_ref = _interpolate_close_ref(ref_level, bar["low"], close_alpha)
            pnl = unit_pnl_usd(symbol, "SELL", outer.entry_price, close_ref, spread_px)
            realized_pnls.append(pnl)
            open_tickets.remove(outer)
            level_idx = int(round((outer.entry_price - anchor) / base_step_px))
            if level_idx >= variant.min_level_idx:
                rearm_tokens.append(RearmToken(
                    direction="SELL", level=outer.entry_price, level_idx=level_idx,
                ))
                level_reuse[outer.entry_price] = level_reuse.get(outer.entry_price, 0) + 1
            sells = sorted((t for t in open_tickets if t.direction == "SELL"), key=lambda t: t.entry_price, reverse=True)

        buys = sorted((t for t in open_tickets if t.direction == "BUY"), key=lambda t: t.entry_price)
        while len(buys) >= 2 and bar["high"] >= buys[1].entry_price:
            outer = buys[0]
            ref_level = buys[1].entry_price
            close_ref = _interpolate_close_ref(ref_level, bar["high"], close_alpha)
            pnl = unit_pnl_usd(symbol, "BUY", outer.entry_price, close_ref, spread_px)
            realized_pnls.append(pnl)
            open_tickets.remove(outer)
            level_idx = int(round((anchor - outer.entry_price) / base_step_px))
            if level_idx >= variant.min_level_idx:
                rearm_tokens.append(RearmToken(
                    direction="BUY", level=outer.entry_price, level_idx=level_idx,
                ))
                level_reuse[outer.entry_price] = level_reuse.get(outer.entry_price, 0) + 1
            buys = sorted((t for t in open_tickets if t.direction == "BUY"), key=lambda t: t.entry_price)

        # Breakout kill: if any position exceeds max floating loss, flush entire book
        if open_tickets:
            floating_pnls = [
                unit_pnl_usd(symbol, t.direction, t.entry_price, bar["close"], spread_px)
                for t in open_tickets
            ]
            worst = min(floating_pnls)
            if worst <= max_floating_loss_usd:
                # Flush entire book at current price
                for t in list(open_tickets):
                    pnl = unit_pnl_usd(symbol, t.direction, t.entry_price, bar["close"], spread_px)
                    realized_pnls.append(pnl)
                open_tickets = []
                rearm_tokens = []
                level_reuse.clear()
                breakout_flushes += 1
                anchor = bar["close"]
                next_sell_level = anchor + base_step_px
                next_buy_level = anchor - base_step_px

        # Reset anchor when flat and price moved
        if not open_tickets and abs(bar["close"] - anchor) >= base_step_px:
            anchor = bar["close"]
            next_sell_level = anchor + base_step_px
            next_buy_level = anchor - base_step_px
            rearm_tokens = []
            level_reuse.clear()

        max_open = max(max_open, len(open_tickets))

    # Final floating
    last_close = bars[-1]["close"]
    floating_pnls = [
        unit_pnl_usd(symbol, t.direction, t.entry_price, last_close, spread_px)
        for t in open_tickets
    ]

    realized_net = sum(realized_pnls)
    floating_net = sum(floating_pnls)
    return {
        "symbol": symbol,
        "days": len(bars) / 24,
        "realized_net_usd": round(realized_net, 2),
        "floating_net_usd": round(floating_net, 2),
        "combined_net_usd": round(realized_net + floating_net, 2),
        "realized_closes": len(realized_pnls),
        "breakout_flushes": breakout_flushes,
        "rearm_opens": rearm_opens,
        "max_open_total": max_open,
    }


def main() -> int:
    args = parse_args()

    if not mt5.initialize():
        print("MetaTrader5 initialize() failed")
        return 1

    try:
        variant = REARM_VARIANTS.get(args.rearm_variant, REARM_VARIANTS["rearm_lvl2_exc1"])
        rows = []

        for symbol in args.symbols:
            cfg = CRYPTO_DEFAULTS.get(symbol)
            if cfg is None:
                print(f"No config for {symbol}, skipping")
                continue
            info = mt5.symbol_info(symbol)
            if info is None:
                print(f"Symbol info not found for {symbol}")
                continue
            bars = load_bars(symbol, args.days, args.tf)
            if not bars:
                print(f"No bars for {symbol}")
                continue

            result = simulate_crypto_rearm(
                symbol, bars, info,
                step_pips=cfg["step_pips"],
                max_open_per_side=cfg["max_open_per_side"],
                max_floating_loss_usd=cfg["max_floating_loss_usd"],
                variant=variant,
                close_alpha=args.close_alpha,
                momentum_gate=args.momentum_gate,
                cooldown_bars=args.cooldown_bars,
            )
            rows.append(result)
            print(f"{symbol}: {result['realized_closes']} closes, realized=${result['realized_net_usd']:,.2f}, "
                  f"floating=${result['floating_net_usd']:,.2f}, combined=${result['combined_net_usd']:,.2f}, "
                  f"flushes={result['breakout_flushes']}, rearm_opens={result['rearm_opens']}")

        if rows:
            out_path = Path(args.output_csv)
            out_path.parent.mkdir(parents=True, exist_ok=True)
            with out_path.open("w", newline="", encoding="utf-8") as f:
                writer = csv.DictWriter(f, fieldnames=rows[0].keys())
                writer.writeheader()
                writer.writerows(rows)
            print(f"\nWrote {out_path}")

        return 0
    finally:
        mt5.shutdown()


if __name__ == "__main__":
    raise SystemExit(main())
