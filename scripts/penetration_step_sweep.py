#!/usr/bin/env python3
"""
Penetration Lattice — Step Size & Symbol Sweep

2D sweep: step_pips × hard_stop across all tradeable FX symbols.
Tests whether the optimal configuration varies by symbol or is universal.
"""
from __future__ import annotations

import argparse
import csv
from dataclasses import dataclass, replace
from pathlib import Path
from statistics import mean

import MetaTrader5 as mt5


ROOT = Path(__file__).resolve().parent.parent

# All MT5 FX symbols that are commonly available
ALL_SYMBOLS = [
    # Majors
    "EURUSD", "GBPUSD", "USDJPY", "USDCHF", "AUDUSD", "NZDUSD", "USDCAD",
    # Minors / Crosses
    "EURGBP", "EURJPY", "GBPJPY", "EURAUD", "EURCHF", "EURNZD", "EURCAD",
    "GBPAUD", "GBPNZD", "GBPCHF", "GBPCAD", "CHFJPY", "AUDJPY", "CADJPY",
    "NZDJPY", "AUDNZD", "AUDCAD", "AUDCHF", "NZDCAD", "NZDCHF",
]


@dataclass(frozen=True)
class Config:
    step_pips: float
    hard_stop_threshold_usd: float
    anchor_reset_pips: float = 3.0
    max_open_per_side: int = 50
    vwap_lookback: int = 20
    adaptive_threshold_1: int = 10
    adaptive_threshold_2: int = 20
    adaptive_mult_1: float = 1.5
    adaptive_mult_2: float = 2.0


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
    gross = mt5.order_calc_profit(order_type, symbol, 0.01, entry_price, exit_price)
    if gross is None:
        return 0.0
    if direction == "BUY":
        spread_cost = mt5.order_calc_profit(order_type, symbol, 0.01, entry_price + spread_px, entry_price)
    else:
        spread_cost = mt5.order_calc_profit(order_type, symbol, 0.01, entry_price, entry_price + spread_px)
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
    unwind_pnls: list[float] = []
    max_open = 0
    anchor_resets = 0
    unwind_fires = 0
    worst_floating_seen = 0.0

    for idx in range(1, len(bars)):
        bar = bars[idx]

        open_buy = sum(1 for t in open_tickets if t.direction == "BUY")
        open_sell = sum(1 for t in open_tickets if t.direction == "SELL")

        current_sell_step = dynamic_step(base_step_px, open_sell, cfg)
        current_buy_step = dynamic_step(base_step_px, open_buy, cfg)

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

        # Penetration close: sell side
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

        # Penetration close: buy side
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

        # Hard stop
        if open_tickets:
            floating_pnls = [
                unit_pnl_usd(symbol, t.direction, t.entry_price, bar["close"], spread_px)
                for t in open_tickets
            ]
            worst_pnl = min(floating_pnls)
            worst_floating_seen = min(worst_floating_seen, worst_pnl)

            if worst_pnl <= cfg.hard_stop_threshold_usd:
                for ticket in list(open_tickets):
                    pnl = unit_pnl_usd(symbol, ticket.direction, ticket.entry_price, bar["close"], spread_px)
                    unwind_pnls.append(pnl)
                    open_tickets.remove(ticket)
                unwind_fires += 1
                anchor = bar["close"]
                next_sell_level = anchor + base_step_px
                next_buy_level = anchor - base_step_px

        # VWAP anchor reset
        if not open_tickets:
            candidate_anchor = vwap_anchor(bars, idx, cfg.vwap_lookback)
            if abs(bar["close"] - anchor) >= reset_px:
                anchor = candidate_anchor
                next_sell_level = anchor + base_step_px
                next_buy_level = anchor - base_step_px
                anchor_resets += 1

        max_open = max(max_open, len(open_tickets))

    last_close = bars[-1]["close"]
    floating_pnls = [
        unit_pnl_usd(symbol, t.direction, t.entry_price, last_close, spread_px)
        for t in open_tickets
    ]

    realized_net = sum(realized_pnls)
    unwind_net = sum(unwind_pnls)
    floating_net = sum(floating_pnls)
    combined_net = realized_net + unwind_net + floating_net

    return {
        "symbol": symbol,
        "step_pips": cfg.step_pips,
        "hard_stop_usd": cfg.hard_stop_threshold_usd,
        "realized_closes": len(realized_pnls),
        "unwind_closes": len(unwind_pnls),
        "wr_pct": round(sum(1 for p in realized_pnls if p > 0) / len(realized_pnls) * 100.0, 1) if realized_pnls else 0.0,
        "realized_net_usd": round(realized_net, 3),
        "unwind_net_usd": round(unwind_net, 3),
        "open_tickets_left": len(open_tickets),
        "floating_net_usd": round(floating_net, 3),
        "worst_floating_seen_usd": round(worst_floating_seen, 3),
        "combined_net_usd": round(combined_net, 3),
        "max_open_total": max_open,
        "unwind_fires": unwind_fires,
        "bars_count": len(bars),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--symbols", nargs="*", default=ALL_SYMBOLS)
    parser.add_argument("--days", type=int, default=20)
    parser.add_argument("--output-csv", default=str(ROOT / "reports" / "penetration_step_sweep.csv"))
    args = parser.parse_args()

    # Sweep grid
    step_configs = [0.3, 0.5, 0.75, 1.0, 1.5, 2.0]
    stop_configs = [-0.75, -1.0, -1.5, -2.0, -3.0, -5.0]

    if not mt5.initialize():
        print("MetaTrader5 initialize() failed")
        return 1

    try:
        all_rows: list[dict] = []
        for step_pips in step_configs:
            for stop_usd in stop_configs:
                cfg = Config(step_pips=step_pips, hard_stop_threshold_usd=stop_usd)
                print(f"\n=== step={step_pips} pips, stop=${stop_usd} ===")
                sym_rows = []
                for symbol in args.symbols:
                    info = mt5.symbol_info(symbol)
                    if info is None:
                        continue
                    bars = load_bars(symbol, args.days)
                    if not bars:
                        continue
                    row = simulate_symbol(symbol, bars, info, cfg)
                    sym_rows.append(row)
                    print(
                        f"  {symbol:<7} banked={row['realized_net_usd']:+.2f} "
                        f"unwind={row['unwind_net_usd']:+.2f} float={row['floating_net_usd']:+.2f} "
                        f"combined={row['combined_net_usd']:+.2f} fires={row['unwind_fires']:>3}"
                    )
                total = sum(r["combined_net_usd"] for r in sym_rows)
                print(f"  COMBINED: ${total:+.2f}")
                all_rows.extend(sym_rows)

        output_path = Path(args.output_csv)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        if all_rows:
            with output_path.open("w", newline="", encoding="utf-8") as handle:
                writer = csv.DictWriter(handle, fieldnames=list(all_rows[0].keys()))
                writer.writeheader()
                writer.writerows(all_rows)
            print(f"\nSaved {output_path}")
        return 0
    finally:
        mt5.shutdown()


if __name__ == "__main__":
    raise SystemExit(main())
