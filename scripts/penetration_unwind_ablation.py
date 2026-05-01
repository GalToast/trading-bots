#!/usr/bin/env python3
"""
Penetration Lattice — Unwind Cost Ablation Study

Tests strategies to blunt the cost of disposing straggler tickets.
All variants share: pair-wise unwind + VWAP anchor + adaptive step (v2 core).

Variants tested:
  1. hard_stop_10   — V2 baseline: -$10 per-ticket triggers full book flush
  2. hard_stop_5    — Tighter: -$5 per-ticket triggers full book flush
  3. hard_stop_3    — Even tighter: -$3 per-ticket triggers full book flush
  4. hard_stop_1p5  — Very tight: -$1.50 per-ticket triggers full book flush
  5. partial_unwind — Close only worst 50% of tickets, keep book alive
  6. graduated_3    — 3-tier: close worst 1 ticket, then worst 3, then all
  7. hedge_unwind   — Open 1x opposite hedge instead of closing losers
  8. no_unwind      — No forced unwind at all (pure floating book, honest risk)
"""
from __future__ import annotations

import argparse
import csv
from dataclasses import dataclass, replace
from pathlib import Path
from statistics import mean

import MetaTrader5 as mt5


ROOT = Path(__file__).resolve().parent.parent
DEFAULT_SYMBOLS = ["USDJPY", "GBPUSD", "EURUSD", "USDCHF", "NZDUSD"]
VOLUME = 0.01


@dataclass(frozen=True)
class Config:
    step_pips: float = 1.0
    anchor_reset_pips: float = 3.0
    max_open_per_side: int = 50
    vwap_lookback: int = 20
    max_hold_bars: int = 0  # disabled by default
    adaptive_threshold_1: int = 10
    adaptive_threshold_2: int = 20
    adaptive_mult_1: float = 1.5
    adaptive_mult_2: float = 2.0
    # Unwind strategy
    unwind_strategy: str = "hard_stop_10"  # one of the variant names
    hard_stop_threshold_usd: float = -10.0
    hedge_lot_size: float = 0.01


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Penetration lattice unwind cost ablation study."
    )
    parser.add_argument("--symbols", nargs="*", default=DEFAULT_SYMBOLS)
    parser.add_argument("--days", type=int, default=20)
    parser.add_argument(
        "--variant",
        choices=[
            "hard_stop_10", "hard_stop_5", "hard_stop_3", "hard_stop_1p5",
            "partial_unwind", "graduated_3", "hedge_unwind", "no_unwind",
        ],
        default="hard_stop_10",
    )
    parser.add_argument(
        "--output-csv",
        default=str(ROOT / "reports" / "penetration_unwind_ablation.csv"),
    )
    return parser.parse_args()


def config_for_variant(variant: str) -> Config:
    base = Config(unwind_strategy=variant)
    if variant == "hard_stop_10":
        return replace(base, hard_stop_threshold_usd=-10.0)
    elif variant == "hard_stop_5":
        return replace(base, hard_stop_threshold_usd=-5.0)
    elif variant == "hard_stop_3":
        return replace(base, hard_stop_threshold_usd=-3.0)
    elif variant == "hard_stop_1p5":
        return replace(base, hard_stop_threshold_usd=-1.5)
    elif variant == "partial_unwind":
        return replace(base, hard_stop_threshold_usd=-5.0)
    elif variant == "graduated_3":
        return replace(base, hard_stop_threshold_usd=-3.0)
    elif variant == "hedge_unwind":
        return replace(base, hard_stop_threshold_usd=-5.0)
    elif variant == "no_unwind":
        return replace(base, hard_stop_threshold_usd=-999.0)
    return base


def pip_size_for(symbol_info) -> float:
    point = float(symbol_info.point or 0.0)
    digits = int(symbol_info.digits or 0)
    return point * 10.0 if digits in (3, 5) else point


def load_bars(symbol: str, days: int) -> list[dict]:
    rates = mt5.copy_rates_from_pos(symbol, mt5.TIMEFRAME_M1, 0, 1440 * days)
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


def spread_price(symbol_info) -> float:
    return float(symbol_info.spread or 0.0) * float(symbol_info.point or 0.0)


def unit_pnl_usd(symbol: str, direction: str, entry_price: float, exit_price: float, spread_px: float) -> float:
    order_type = mt5.ORDER_TYPE_BUY if direction == "BUY" else mt5.ORDER_TYPE_SELL
    gross = mt5.order_calc_profit(order_type, symbol, VOLUME, entry_price, exit_price)
    if gross is None:
        return 0.0
    if direction == "BUY":
        spread_cost = mt5.order_calc_profit(order_type, symbol, VOLUME, entry_price + spread_px, entry_price)
    else:
        spread_cost = mt5.order_calc_profit(order_type, symbol, VOLUME, entry_price, entry_price + spread_px)
    return float(gross) - abs(float(spread_cost or 0.0))


def dynamic_step(base_step: float, open_count: int, cfg: Config) -> float:
    if open_count >= cfg.adaptive_threshold_2:
        return base_step * cfg.adaptive_mult_2
    elif open_count >= cfg.adaptive_threshold_1:
        return base_step * cfg.adaptive_mult_1
    return base_step


def vwap_anchor(bars: list[dict], idx: int, lookback: int) -> float:
    start = max(0, idx - lookback)
    window = bars[start:idx]
    if not window:
        return bars[idx - 1]["close"]
    cum_cv = sum(b["close"] * b["tick_volume"] for b in window)
    cum_v = sum(b["tick_volume"] for b in window)
    return cum_cv / cum_v if cum_v > 0 else window[-1]["close"]


@dataclass
class Ticket:
    direction: str
    entry_price: float
    opened_idx: int


def simulate_symbol(symbol: str, bars: list[dict], symbol_info, cfg: Config) -> dict:
    if not bars:
        return {}

    pip_size = pip_size_for(symbol_info)
    spread_px = spread_price(symbol_info)
    base_step_px = cfg.step_pips * pip_size
    reset_px = cfg.anchor_reset_pips * pip_size

    anchor = bars[0]["close"]
    next_sell_level = anchor + base_step_px
    next_buy_level = anchor - base_step_px

    open_tickets: list[Ticket] = []
    realized_pnls: list[float] = []
    unwind_pnls: list[float] = []  # forced/graduated/partial unwinds
    hedge_pnls: list[float] = []   # hedge unwind P&L
    max_open = 0
    max_open_buy = 0
    max_open_sell = 0
    anchor_resets = 0
    unwind_fires = 0
    # Track worst floating depth
    worst_floating_seen = 0.0

    for idx in range(1, len(bars)):
        bar = bars[idx]

        open_buy = sum(1 for t in open_tickets if t.direction == "BUY")
        open_sell = sum(1 for t in open_tickets if t.direction == "SELL")

        current_sell_step = dynamic_step(base_step_px, open_sell, cfg)
        current_buy_step = dynamic_step(base_step_px, open_buy, cfg)

        # --- Open new lattice orders ---
        while bar["high"] >= next_sell_level and open_sell < cfg.max_open_per_side:
            open_tickets.append(Ticket(direction="SELL", entry_price=next_sell_level, opened_idx=idx))
            next_sell_level += current_sell_step
            open_sell += 1
            current_sell_step = dynamic_step(base_step_px, open_sell, cfg)

        while bar["low"] <= next_buy_level and open_buy < cfg.max_open_per_side:
            open_tickets.append(Ticket(direction="BUY", entry_price=next_buy_level, opened_idx=idx))
            next_buy_level -= current_buy_step
            open_buy += 1
            current_buy_step = dynamic_step(base_step_px, open_buy, cfg)

        # --- Penetration close: sell side (pair-wise) ---
        sells = sorted((t for t in open_tickets if t.direction == "SELL"), key=lambda t: t.entry_price, reverse=True)
        while len(sells) >= 2 and bar["low"] <= sells[1].entry_price:
            close_ref = bar["low"]
            profitable = [t for t in sells if unit_pnl_usd(symbol, "SELL", t.entry_price, close_ref, spread_px) > 0]
            if not profitable:
                break
            for ticket in profitable:
                pnl = unit_pnl_usd(symbol, "SELL", ticket.entry_price, close_ref, spread_px)
                realized_pnls.append(pnl)
                open_tickets.remove(ticket)
            sells = sorted((t for t in open_tickets if t.direction == "SELL"), key=lambda t: t.entry_price, reverse=True)

        # --- Penetration close: buy side (pair-wise) ---
        buys = sorted((t for t in open_tickets if t.direction == "BUY"), key=lambda t: t.entry_price)
        while len(buys) >= 2 and bar["high"] >= buys[1].entry_price:
            close_ref = bar["high"]
            profitable = [t for t in buys if unit_pnl_usd(symbol, "BUY", t.entry_price, close_ref, spread_px) > 0]
            if not profitable:
                break
            for ticket in profitable:
                pnl = unit_pnl_usd(symbol, "BUY", ticket.entry_price, close_ref, spread_px)
                realized_pnls.append(pnl)
                open_tickets.remove(ticket)
            buys = sorted((t for t in open_tickets if t.direction == "BUY"), key=lambda t: t.entry_price)

        # --- Forced unwind logic ---
        if open_tickets:
            floating_pnls = [
                unit_pnl_usd(symbol, t.direction, t.entry_price, bar["close"], spread_px)
                for t in open_tickets
            ]
            worst_pnl = min(floating_pnls)
            worst_floating_seen = min(worst_floating_seen, worst_pnl)

            if worst_pnl <= cfg.hard_stop_threshold_usd:
                strat = cfg.unwind_strategy

                if strat == "no_unwind":
                    pass  # intentionally do nothing
                elif strat == "hard_stop_10" or strat == "hard_stop_5" or strat == "hard_stop_3" or strat == "hard_stop_1p5":
                    # Full book flush
                    for ticket in list(open_tickets):
                        pnl = unit_pnl_usd(symbol, ticket.direction, ticket.entry_price, bar["close"], spread_px)
                        unwind_pnls.append(pnl)
                        open_tickets.remove(ticket)
                    unwind_fires += 1
                    anchor = bar["close"]
                    next_sell_level = anchor + base_step_px
                    next_buy_level = anchor - base_step_px
                elif strat == "partial_unwind":
                    # Close only the worst 50% of tickets
                    sorted_by_pnl = sorted(
                        zip(open_tickets, floating_pnls), key=lambda x: x[1]
                    )
                    n_to_close = max(1, len(sorted_by_pnl) // 2)
                    for ticket, _ in sorted_by_pnl[:n_to_close]:
                        pnl = unit_pnl_usd(symbol, ticket.direction, ticket.entry_price, bar["close"], spread_px)
                        unwind_pnls.append(pnl)
                        open_tickets.remove(ticket)
                    unwind_fires += 1
                elif strat == "graduated_3":
                    # 3-tier graduated: close worst 1, then worst 3, then all
                    sorted_by_pnl = sorted(
                        zip(open_tickets, floating_pnls), key=lambda x: x[1]
                    )
                    if worst_pnl <= -10.0:
                        n_to_close = len(sorted_by_pnl)  # all
                    elif worst_pnl <= -5.0:
                        n_to_close = min(3, len(sorted_by_pnl))
                    else:
                        n_to_close = 1  # tier 1: close only the worst
                    for ticket, _ in sorted_by_pnl[:n_to_close]:
                        pnl = unit_pnl_usd(symbol, ticket.direction, ticket.entry_price, bar["close"], spread_px)
                        unwind_pnls.append(pnl)
                        open_tickets.remove(ticket)
                    unwind_fires += 1
                elif strat == "hedge_unwind":
                    # Open opposite-direction hedge tickets (capped at 5).
                    # These behave like normal tickets but offset the floating loss.
                    # Determine worst side
                    sell_pnls = [unit_pnl_usd(symbol, "SELL", t.entry_price, bar["close"], spread_px)
                                 for t in open_tickets if t.direction == "SELL"]
                    buy_pnls = [unit_pnl_usd(symbol, "BUY", t.entry_price, bar["close"], spread_px)
                                for t in open_tickets if t.direction == "BUY"]
                    avg_sell_pnl = mean(sell_pnls) if sell_pnls else 0.0
                    avg_buy_pnl = mean(buy_pnls) if buy_pnls else 0.0
                    hedge_dir = "BUY" if avg_sell_pnl < avg_buy_pnl else "SELL"
                    n_hedge = min(5, len(open_tickets))
                    for _ in range(n_hedge):
                        open_tickets.append(Ticket(
                            direction=hedge_dir,
                            entry_price=bar["close"],
                            opened_idx=idx,
                        ))
                    unwind_fires += 1

        # --- Anchor reset with VWAP ---
        if not open_tickets:
            candidate_anchor = vwap_anchor(bars, idx, cfg.vwap_lookback)
            if abs(bar["close"] - anchor) >= reset_px:
                anchor = candidate_anchor
                next_sell_level = anchor + base_step_px
                next_buy_level = anchor - base_step_px
                anchor_resets += 1

        max_open = max(max_open, len(open_tickets))
        max_open_buy = max(max_open_buy, sum(1 for t in open_tickets if t.direction == "BUY"))
        max_open_sell = max(max_open_sell, sum(1 for t in open_tickets if t.direction == "SELL"))

    # --- End-of-sample ---
    last_close = bars[-1]["close"]
    floating_pnls = [
        unit_pnl_usd(symbol, t.direction, t.entry_price, last_close, spread_px)
        for t in open_tickets
    ]

    realized_net = sum(realized_pnls)
    unwind_net = sum(unwind_pnls)
    hedge_net = sum(hedge_pnls)
    floating_net = sum(floating_pnls)
    combined_net = realized_net + unwind_net + hedge_net + floating_net

    all_closes = realized_pnls + unwind_pnls
    total_closes = len(all_closes)
    wins = sum(1 for p in all_closes if p > 0)

    return {
        "symbol": symbol,
        "variant": cfg.unwind_strategy,
        "realized_closes": len(realized_pnls),
        "unwind_closes": len(unwind_pnls),
        "total_closes": total_closes,
        "wr_pct": round(wins / total_closes * 100.0, 1) if total_closes else 0.0,
        "realized_net_usd": round(realized_net, 3),
        "unwind_net_usd": round(unwind_net, 3),
        "unwind_cost_ratio": round(abs(unwind_net) / realized_net, 3) if realized_net > 0 else 0.0,
        "open_tickets_left": len(open_tickets),
        "floating_net_usd": round(floating_net, 3),
        "worst_floating_usd": round(min(floating_pnls), 3) if floating_pnls else 0.0,
        "worst_floating_seen_usd": round(worst_floating_seen, 3),
        "combined_net_usd": round(combined_net, 3),
        "max_open_total": max_open,
        "max_open_buy": max_open_buy,
        "max_open_sell": max_open_sell,
        "anchor_resets": anchor_resets,
        "unwind_fires": unwind_fires,
    }


def main() -> int:
    args = parse_args()
    cfg = config_for_variant(args.variant)

    if not mt5.initialize():
        print("MetaTrader5 initialize() failed")
        return 1

    try:
        rows: list[dict] = []
        for symbol in args.symbols:
            info = mt5.symbol_info(symbol)
            if info is None:
                continue
            bars = load_bars(symbol, args.days)
            if not bars:
                continue
            row = simulate_symbol(symbol, bars, info, cfg)
            rows.append(row)
            print(
                f"{symbol:<7} realized={row['realized_closes']:>4} unwind={row['unwind_closes']:>3} "
                f"banked={row['realized_net_usd']:+.2f} unwind_cost={row['unwind_net_usd']:+.2f} "
                f"ratio={row['unwind_cost_ratio']:.2f} left={row['open_tickets_left']:>3} "
                f"float={row['floating_net_usd']:+.2f} combined={row['combined_net_usd']:+.2f} "
                f"worst_seen={row['worst_floating_seen_usd']:+.2f} fires={row['unwind_fires']:>3}"
            )

        output_path = Path(args.output_csv)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        if rows:
            with output_path.open("w", newline="", encoding="utf-8") as handle:
                writer = csv.DictWriter(handle, fieldnames=list(rows[0].keys()))
                writer.writeheader()
                writer.writerows(rows)
            print(f"Saved {output_path}")
        return 0
    finally:
        mt5.shutdown()


if __name__ == "__main__":
    raise SystemExit(main())
