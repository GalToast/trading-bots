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
    lookback: int = 40
    atr_period: int = 14
    band_atr_mult: float = 1.0
    step_atr_mult: float = 0.25
    max_levels: int = 4
    breakout_buffer_atr: float = 0.35


@dataclass
class GridPosition:
    direction: str
    entry_price: float
    target_price: float
    level_idx: int
    opened_idx: int


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "ATR-bounded neutral range grid. Only trades inside a tight range band, "
            "opens opposite at grid levels, banks on one-step retrace, and force-closes on breakout."
        )
    )
    parser.add_argument("--symbols", nargs="*", default=DEFAULT_SYMBOLS)
    parser.add_argument("--days", type=int, default=20)
    parser.add_argument("--lookback", type=int, default=40)
    parser.add_argument("--atr-period", type=int, default=14)
    parser.add_argument("--band-atr-mult", type=float, default=1.0)
    parser.add_argument("--step-atr-mult", type=float, default=0.25)
    parser.add_argument("--max-levels", type=int, default=4)
    parser.add_argument("--breakout-buffer-atr", type=float, default=0.35)
    parser.add_argument(
        "--output-csv",
        default=str(ROOT / "reports" / "range_grid_lab.csv"),
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


def compute_atr(bars: list[dict], idx: int, period: int) -> float:
    if idx < period:
        return 0.0
    trs = []
    for i in range(idx - period + 1, idx + 1):
        tr = bars[i]["high"] - bars[i]["low"]
        if i > 0:
            tr = max(tr, abs(bars[i]["high"] - bars[i - 1]["close"]))
            tr = max(tr, abs(bars[i]["low"] - bars[i - 1]["close"]))
        trs.append(tr)
    return sum(trs) / len(trs) if trs else 0.0


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
    spread_px = spread_price(symbol_info)
    closed_pnls: list[float] = []
    breakout_losses: list[float] = []
    closed_count = 0
    breakout_events = 0
    open_positions: list[GridPosition] = []
    max_open_positions = 0
    regime_bars = 0

    idx = max(cfg.lookback, cfg.atr_period) + 2
    while idx < len(bars):
        atr = compute_atr(bars, idx, cfg.atr_period)
        if atr <= 0:
            idx += 1
            continue

        recent = bars[idx - cfg.lookback : idx]
        center = sum(bar["close"] for bar in recent) / len(recent)
        range_high = max(bar["high"] for bar in recent)
        range_low = min(bar["low"] for bar in recent)
        range_width = range_high - range_low

        band = atr * cfg.band_atr_mult
        step = max(atr * cfg.step_atr_mult, float(symbol_info.point or 0.0) * 10.0)
        in_range = range_width <= band * 2.0

        bar = bars[idx]
        breakout = (
            bar["close"] > center + band + atr * cfg.breakout_buffer_atr
            or bar["close"] < center - band - atr * cfg.breakout_buffer_atr
        )

        # Bank any existing positions that retraced one step toward center.
        survivors: list[GridPosition] = []
        for pos in open_positions:
            target_hit = bar["high"] >= pos.target_price if pos.direction == "BUY" else bar["low"] <= pos.target_price
            if target_hit:
                closed_pnls.append(unit_pnl_usd(symbol, pos.direction, pos.entry_price, pos.target_price, spread_px))
                closed_count += 1
            else:
                survivors.append(pos)
        open_positions = survivors

        # Breakout kills the grid and realizes remaining inventory.
        if open_positions and breakout:
            for pos in open_positions:
                pnl = unit_pnl_usd(symbol, pos.direction, pos.entry_price, bar["close"], spread_px)
                closed_pnls.append(pnl)
                breakout_losses.append(pnl)
                closed_count += 1
            open_positions = []
            breakout_events += 1

        if in_range:
            regime_bars += 1
            # Open sells above center and buys below center, one per level.
            for level_idx in range(1, cfg.max_levels + 1):
                upper = center + step * level_idx
                lower = center - step * level_idx

                if bar["high"] >= upper and not any(
                    pos.direction == "SELL" and pos.level_idx == level_idx for pos in open_positions
                ):
                    open_positions.append(
                        GridPosition(
                            direction="SELL",
                            entry_price=upper,
                            target_price=upper - step,
                            level_idx=level_idx,
                            opened_idx=idx,
                        )
                    )

                if bar["low"] <= lower and not any(
                    pos.direction == "BUY" and pos.level_idx == level_idx for pos in open_positions
                ):
                    open_positions.append(
                        GridPosition(
                            direction="BUY",
                            entry_price=lower,
                            target_price=lower + step,
                            level_idx=level_idx,
                            opened_idx=idx,
                        )
                    )

        max_open_positions = max(max_open_positions, len(open_positions))
        idx += 1

    if open_positions:
        last_close = bars[-1]["close"]
        for pos in open_positions:
            pnl = unit_pnl_usd(symbol, pos.direction, pos.entry_price, last_close, spread_px)
            closed_pnls.append(pnl)
            breakout_losses.append(pnl)
            closed_count += 1
        breakout_events += 1

    wins = [pnl for pnl in closed_pnls if pnl > 0]
    return {
        "symbol": symbol,
        "closed_positions": closed_count,
        "per_day": round(closed_count / max(len(bars) / 1440.0, 1e-9), 2),
        "wr_pct": round((len(wins) / closed_count * 100.0) if closed_count else 0.0, 1),
        "net_usd": round(sum(closed_pnls), 3),
        "exp_usd": round(mean(closed_pnls), 3) if closed_pnls else 0.0,
        "breakout_events": breakout_events,
        "breakout_net_usd": round(sum(breakout_losses), 3),
        "worst_closed_usd": round(min(closed_pnls), 3) if closed_pnls else 0.0,
        "max_open_positions": max_open_positions,
        "range_bar_pct": round(regime_bars / max(len(bars), 1) * 100.0, 1),
    }


def main() -> int:
    args = parse_args()
    cfg = Config(
        lookback=args.lookback,
        atr_period=args.atr_period,
        band_atr_mult=args.band_atr_mult,
        step_atr_mult=args.step_atr_mult,
        max_levels=args.max_levels,
        breakout_buffer_atr=args.breakout_buffer_atr,
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
                f"{symbol:<7} closed={row['closed_positions']:>4} exp={row['exp_usd']:+.3f} "
                f"net={row['net_usd']:+.2f} wr={row['wr_pct']:>5.1f}% "
                f"breakouts={row['breakout_events']:>3} breakout_net={row['breakout_net_usd']:+.2f} "
                f"max_open={row['max_open_positions']:>2}"
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
