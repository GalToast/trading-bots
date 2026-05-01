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
    streak_min: int = 5
    wick_ratio: float = 1.2
    wick_min_pips: float = 1.0
    step_pips: float = 1.0
    max_units: int = 20


@dataclass
class Basket:
    direction: str
    start_idx: int
    entries: list[float]
    next_add_trigger: float
    max_heat_pips: float = 0.0
    max_units_reached: int = 1


@dataclass
class BasketTrade:
    symbol: str
    direction: str
    units: int
    pnl_usd: float
    hold_bars: int
    start_idx: int
    exit_idx: int
    max_units_reached: int
    max_heat_pips: float
    exit_reason: str


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Test a no-stop lattice approximation: open opposite an exhaustion streak, "
            "keep layering every fixed distance as price keeps stretching, and only close "
            "the basket on an opposite exhaustion or end-of-sample mark."
        )
    )
    parser.add_argument("--symbols", nargs="*", default=DEFAULT_SYMBOLS)
    parser.add_argument("--days", type=int, default=20)
    parser.add_argument("--streak-min", type=int, default=5)
    parser.add_argument("--wick-ratio", type=float, default=1.2)
    parser.add_argument("--wick-min-pips", type=float, default=1.0)
    parser.add_argument("--step-pips", type=float, default=1.0)
    parser.add_argument("--max-units", type=int, default=20)
    parser.add_argument(
        "--output-csv",
        default=str(ROOT / "reports" / "lattice_extremes_lab.csv"),
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


def bar_dir(bar: dict) -> str | None:
    if bar["close"] > bar["open"]:
        return "UP"
    if bar["close"] < bar["open"]:
        return "DOWN"
    return None


def body_pips(bar: dict, pip_size: float) -> float:
    return abs(bar["close"] - bar["open"]) / pip_size


def upper_wick_pips(bar: dict, pip_size: float) -> float:
    return max(bar["high"] - max(bar["open"], bar["close"]), 0.0) / pip_size


def lower_wick_pips(bar: dict, pip_size: float) -> float:
    return max(min(bar["open"], bar["close"]) - bar["low"], 0.0) / pip_size


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


def favorable_pips(direction: str, start: float, end: float, pip_size: float) -> float:
    move = (end - start) / pip_size
    return move if direction == "BUY" else -move


def adverse_pips(direction: str, start: float, end: float, pip_size: float) -> float:
    return -favorable_pips(direction, start, end, pip_size)


def detect_exhaustion(bars: list[dict], idx: int, cfg: Config, pip_size: float) -> str | None:
    if idx < cfg.streak_min - 1:
        return None
    current_dir = bar_dir(bars[idx])
    if current_dir is None:
        return None

    streak_bars = []
    j = idx
    while j >= 0 and bar_dir(bars[j]) == current_dir:
        streak_bars.append(bars[j])
        j -= 1
    if len(streak_bars) < cfg.streak_min:
        return None

    streak_bars.reverse()
    final_bar = streak_bars[-1]
    avg_body = mean(body_pips(bar, pip_size) for bar in streak_bars)
    if avg_body <= 0:
        return None

    if current_dir == "UP":
        wick = upper_wick_pips(final_bar, pip_size)
        if wick < cfg.wick_min_pips or wick < avg_body * cfg.wick_ratio:
            return None
        return "SELL"

    wick = lower_wick_pips(final_bar, pip_size)
    if wick < cfg.wick_min_pips or wick < avg_body * cfg.wick_ratio:
        return None
    return "BUY"


def average_entry(entries: list[float]) -> float:
    return sum(entries) / len(entries)


def apply_lattice_adds(basket: Basket, bar: dict, cfg: Config, pip_size: float) -> None:
    while len(basket.entries) < cfg.max_units:
        if basket.direction == "BUY" and bar["low"] <= basket.next_add_trigger:
            basket.entries.append(basket.next_add_trigger)
            basket.next_add_trigger -= cfg.step_pips * pip_size
            basket.max_units_reached = max(basket.max_units_reached, len(basket.entries))
            continue
        if basket.direction == "SELL" and bar["high"] >= basket.next_add_trigger:
            basket.entries.append(basket.next_add_trigger)
            basket.next_add_trigger += cfg.step_pips * pip_size
            basket.max_units_reached = max(basket.max_units_reached, len(basket.entries))
            continue
        break


def basket_heat_pips(basket: Basket, bar: dict, pip_size: float) -> float:
    avg_entry = average_entry(basket.entries)
    worst_price = bar["low"] if basket.direction == "BUY" else bar["high"]
    return adverse_pips(basket.direction, avg_entry, worst_price, pip_size)


def close_basket(
    symbol: str,
    basket: Basket,
    exit_idx: int,
    exit_price: float,
    spread_px: float,
    exit_reason: str,
) -> BasketTrade:
    pnl_usd = sum(
        unit_pnl_usd(symbol, basket.direction, entry_price, exit_price, spread_px)
        for entry_price in basket.entries
    )
    return BasketTrade(
        symbol=symbol,
        direction=basket.direction,
        units=len(basket.entries),
        pnl_usd=pnl_usd,
        hold_bars=exit_idx - basket.start_idx + 1,
        start_idx=basket.start_idx,
        exit_idx=exit_idx,
        max_units_reached=basket.max_units_reached,
        max_heat_pips=basket.max_heat_pips,
        exit_reason=exit_reason,
    )


def simulate_symbol(symbol: str, bars: list[dict], symbol_info, cfg: Config) -> list[BasketTrade]:
    pip_size = pip_size_for(symbol_info)
    spread_px = spread_price(symbol_info)
    trades: list[BasketTrade] = []
    basket: Basket | None = None

    idx = cfg.streak_min + 1
    while idx < len(bars) - 1:
        bar = bars[idx]

        if basket is not None:
            apply_lattice_adds(basket, bar, cfg, pip_size)
            basket.max_heat_pips = max(basket.max_heat_pips, basket_heat_pips(basket, bar, pip_size))

        exhaustion_dir = detect_exhaustion(bars, idx, cfg, pip_size)
        if basket is None and exhaustion_dir:
            entry_idx = idx + 1
            entry_price = bars[entry_idx]["open"]
            next_add_trigger = entry_price - cfg.step_pips * pip_size if exhaustion_dir == "BUY" else entry_price + cfg.step_pips * pip_size
            basket = Basket(
                direction=exhaustion_dir,
                start_idx=entry_idx,
                entries=[entry_price],
                next_add_trigger=next_add_trigger,
            )
            idx += 1
            continue

        if basket is not None and exhaustion_dir and exhaustion_dir != basket.direction:
            exit_idx = idx + 1
            if exit_idx >= len(bars):
                exit_idx = len(bars) - 1
            exit_price = bars[exit_idx]["open"]
            trades.append(close_basket(symbol, basket, exit_idx, exit_price, spread_px, "opposite_exhaustion"))

            next_add_trigger = exit_price - cfg.step_pips * pip_size if exhaustion_dir == "BUY" else exit_price + cfg.step_pips * pip_size
            basket = Basket(
                direction=exhaustion_dir,
                start_idx=exit_idx,
                entries=[exit_price],
                next_add_trigger=next_add_trigger,
            )
            idx += 1
            continue

        idx += 1

    if basket is not None:
        exit_idx = len(bars) - 1
        exit_price = bars[exit_idx]["close"]
        trades.append(close_basket(symbol, basket, exit_idx, exit_price, spread_px, "end_mark"))

    return trades


def summarize(symbol: str, trades: list[BasketTrade], days: int) -> dict:
    wins = [trade for trade in trades if trade.pnl_usd > 0]
    pnl_values = [trade.pnl_usd for trade in trades]
    end_mark_count = sum(1 for trade in trades if trade.exit_reason == "end_mark")
    return {
        "symbol": symbol,
        "trades": len(trades),
        "per_day": round(len(trades) / max(days, 1), 2),
        "wr_pct": round((len(wins) / len(trades) * 100.0) if trades else 0.0, 1),
        "net_usd": round(sum(pnl_values), 3),
        "exp_usd": round(mean(pnl_values), 3) if pnl_values else 0.0,
        "avg_hold_bars": round(mean(trade.hold_bars for trade in trades), 1) if trades else 0.0,
        "avg_units": round(mean(trade.units for trade in trades), 2) if trades else 0.0,
        "max_units_seen": max((trade.max_units_reached for trade in trades), default=0),
        "avg_heat_pips": round(mean(trade.max_heat_pips for trade in trades), 2) if trades else 0.0,
        "worst_basket_usd": round(min(pnl_values), 3) if pnl_values else 0.0,
        "end_mark_exits": end_mark_count,
    }


def main() -> int:
    args = parse_args()
    cfg = Config(
        streak_min=args.streak_min,
        wick_ratio=args.wick_ratio,
        wick_min_pips=args.wick_min_pips,
        step_pips=args.step_pips,
        max_units=args.max_units,
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
            trades = simulate_symbol(symbol, bars, info, cfg)
            row = summarize(symbol, trades, args.days)
            rows.append(row)
            print(
                f"{symbol:<7} trades={row['trades']:>4} exp={row['exp_usd']:+.3f} "
                f"net={row['net_usd']:+.2f} wr={row['wr_pct']:>5.1f}% "
                f"avg_units={row['avg_units']:.2f} max_units={row['max_units_seen']:>2} "
                f"worst={row['worst_basket_usd']:+.2f} end_marks={row['end_mark_exits']}"
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
