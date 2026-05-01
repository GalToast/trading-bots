#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
from dataclasses import dataclass
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


@dataclass
class Ticket:
    direction: str
    entry_price: float
    opened_idx: int


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Penetration lattice lab: open opposite .01 tickets as price extends and "
            "close the furthest profitable runner first when price penetrates back "
            "through the next open order level."
        )
    )
    parser.add_argument("--symbols", nargs="*", default=DEFAULT_SYMBOLS)
    parser.add_argument("--days", type=int, default=20)
    parser.add_argument("--step-pips", type=float, default=1.0)
    parser.add_argument("--anchor-reset-pips", type=float, default=3.0)
    parser.add_argument("--max-open-per-side", type=int, default=50)
    parser.add_argument(
        "--output-csv",
        default=str(ROOT / "reports" / "penetration_lattice_lab.csv"),
    )
    return parser.parse_args()


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


def simulate_symbol(symbol: str, bars: list[dict], symbol_info, cfg: Config) -> dict:
    if not bars:
        return {}

    pip_size = pip_size_for(symbol_info)
    spread_px = spread_price(symbol_info)
    step_px = cfg.step_pips * pip_size
    reset_px = cfg.anchor_reset_pips * pip_size

    anchor = bars[0]["close"]
    next_sell_level = anchor + step_px
    next_buy_level = anchor - step_px

    open_tickets: list[Ticket] = []
    realized_pnls: list[float] = []
    max_open = 0
    max_open_buy = 0
    max_open_sell = 0
    reset_count = 0

    for idx in range(1, len(bars)):
        bar = bars[idx]

        open_buy = sum(1 for ticket in open_tickets if ticket.direction == "BUY")
        open_sell = sum(1 for ticket in open_tickets if ticket.direction == "SELL")

        while bar["high"] >= next_sell_level and open_sell < cfg.max_open_per_side:
            open_tickets.append(Ticket(direction="SELL", entry_price=next_sell_level, opened_idx=idx))
            next_sell_level += step_px
            open_sell += 1

        while bar["low"] <= next_buy_level and open_buy < cfg.max_open_per_side:
            open_tickets.append(Ticket(direction="BUY", entry_price=next_buy_level, opened_idx=idx))
            next_buy_level -= step_px
            open_buy += 1

        # Close outermost profitable sells first as price penetrates back down through lower sell levels.
        sells = sorted((ticket for ticket in open_tickets if ticket.direction == "SELL"), key=lambda t: t.entry_price, reverse=True)
        while len(sells) >= 2 and bar["low"] <= sells[1].entry_price:
            outer = sells[0]
            close_ref = sells[1].entry_price
            realized_pnls.append(unit_pnl_usd(symbol, "SELL", outer.entry_price, close_ref, spread_px))
            open_tickets.remove(outer)
            sells = sorted((ticket for ticket in open_tickets if ticket.direction == "SELL"), key=lambda t: t.entry_price, reverse=True)

        # Close outermost profitable buys first as price penetrates back up through higher buy levels.
        buys = sorted((ticket for ticket in open_tickets if ticket.direction == "BUY"), key=lambda t: t.entry_price)
        while len(buys) >= 2 and bar["high"] >= buys[1].entry_price:
            outer = buys[0]
            close_ref = buys[1].entry_price
            realized_pnls.append(unit_pnl_usd(symbol, "BUY", outer.entry_price, close_ref, spread_px))
            open_tickets.remove(outer)
            buys = sorted((ticket for ticket in open_tickets if ticket.direction == "BUY"), key=lambda t: t.entry_price)

        max_open = max(max_open, len(open_tickets))
        max_open_buy = max(max_open_buy, sum(1 for ticket in open_tickets if ticket.direction == "BUY"))
        max_open_sell = max(max_open_sell, sum(1 for ticket in open_tickets if ticket.direction == "SELL"))

        if abs(bar["close"] - anchor) >= reset_px and not open_tickets:
            anchor = bar["close"]
            next_sell_level = anchor + step_px
            next_buy_level = anchor - step_px
            reset_count += 1

    last_close = bars[-1]["close"]
    floating_pnls = [
        unit_pnl_usd(symbol, ticket.direction, ticket.entry_price, last_close, spread_px)
        for ticket in open_tickets
    ]

    realized_net = sum(realized_pnls)
    floating_net = sum(floating_pnls)
    combined_net = realized_net + floating_net

    return {
        "symbol": symbol,
        "realized_closes": len(realized_pnls),
        "realized_wr_pct": round(sum(1 for pnl in realized_pnls if pnl > 0) / len(realized_pnls) * 100.0, 1) if realized_pnls else 0.0,
        "realized_net_usd": round(realized_net, 3),
        "realized_exp_usd": round(mean(realized_pnls), 3) if realized_pnls else 0.0,
        "open_tickets_left": len(open_tickets),
        "floating_net_usd": round(floating_net, 3),
        "combined_net_usd": round(combined_net, 3),
        "worst_floating_ticket_usd": round(min(floating_pnls), 3) if floating_pnls else 0.0,
        "max_open_total": max_open,
        "max_open_buy": max_open_buy,
        "max_open_sell": max_open_sell,
        "anchor_resets": reset_count,
    }


def main() -> int:
    args = parse_args()
    cfg = Config(
        step_pips=args.step_pips,
        anchor_reset_pips=args.anchor_reset_pips,
        max_open_per_side=args.max_open_per_side,
    )

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
                f"{symbol:<7} realized={row['realized_closes']:>4} "
                f"banked={row['realized_net_usd']:+.2f} left={row['open_tickets_left']:>3} "
                f"float={row['floating_net_usd']:+.2f} combined={row['combined_net_usd']:+.2f} "
                f"max_open={row['max_open_total']:>3}"
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
