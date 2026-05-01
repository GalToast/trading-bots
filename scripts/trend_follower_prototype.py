#!/usr/bin/env python3
"""
Trend-Follower Prototype — Breakout Capture Engine

When the lattice detects a breakout (price breaches boundary), instead of closing at a loss:
1. Reverse direction and FOLLOW the trend
2. Trail with 2x ATR stop
3. Exit when trend exhausts

This engine runs alongside the penetration lattice. When regime = TRENDING, 
the trend-follower activates. When regime = RANGING, the lattice activates.
"""
from __future__ import annotations

import argparse
import csv
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import MetaTrader5 as mt5


ROOT = Path(__file__).resolve().parent.parent


@dataclass
class TrendPosition:
    direction: str  # "BUY" or "SELL"
    entry_price: float
    entry_idx: int
    trail_stop: float
    highest_since_entry: float  # For BUY positions
    lowest_since_entry: float   # For SELL positions


def load_tf_bars(symbol: str, tf: int, days: int) -> list[dict]:
    rates = mt5.copy_rates_from_pos(symbol, tf, 0, 24 * days)
    if rates is None or len(rates) == 0:
        return []
    return [
        {"time": int(r[0]), "open": float(r[1]), "high": float(r[2]),
         "low": float(r[3]), "close": float(r[4]), "tick_volume": int(r[5])}
        for r in rates
    ]


def compute_atr(bars: list[dict], idx: int, window: int = 14) -> float:
    """Compute ATR at bar idx."""
    if idx < window + 1:
        return 0.0
    trs = []
    for i in range(idx - window, idx):
        h = bars[i]["high"]
        l = bars[i]["low"]
        pc = bars[i - 1]["close"]
        tr = max(h - l, abs(h - pc), abs(l - pc))
        trs.append(tr)
    return sum(trs) / len(trs) if trs else 0.0


def detect_regime(bars: list[dict], idx: int, donchian_period: int = 20, confirmation_bars: int = 2) -> str:
    """Regime detector using Donchian channels + breakout confirmation.
    
    Returns: 'RANGING', 'TRENDING_UP', 'TRENDING_DOWN'
    
    Logic:
    - Donchian channel: 20-bar high/low
    - Trending UP: price closes ABOVE 20-bar high for confirmation_bars consecutive bars
    - Trending DOWN: price closes BELOW 20-bar low for confirmation_bars consecutive bars
    - Otherwise: RANGING
    """
    if idx < donchian_period + confirmation_bars:
        return "RANGING"

    # Get Donchian channel levels (from BEFORE the potential breakout)
    donchian_start = idx - donchian_period - confirmation_bars
    donchian_end = idx - confirmation_bars
    donchian_high = max(bars[i]["high"] for i in range(donchian_start, donchian_end))
    donchian_low = min(bars[i]["low"] for i in range(donchian_start, donchian_end))

    # Check for confirmed breakout
    closes_above = all(bars[i]["close"] > donchian_high for i in range(idx - confirmation_bars, idx))
    closes_below = all(bars[i]["close"] < donchian_low for i in range(idx - confirmation_bars, idx))

    if closes_above:
        return "TRENDING_UP"
    elif closes_below:
        return "TRENDING_DOWN"
    else:
        return "RANGING"


def simulate_trend_follower(symbol: str, bars: list[dict], info,
                           atr_mult: float = 2.0, max_hold_bars: int = 24,
                           donchian_period: int = 20, confirmation_bars: int = 2) -> dict:
    """Simulate the trend-follower engine on BTCUSD H1 data."""

    spread_px = float(info.spread or 0.0) * float(info.point or 1.0)

    realized_pnls = []
    positions: list[TrendPosition] = []
    max_concurrent = 0
    trends_captured = 0
    trends_exited_profit = 0
    trends_exited_loss = 0
    total_trail_stops = 0
    total_max_hold = 0
    total_false_breakouts = 0

    for idx in range(1, len(bars)):
        bar = bars[idx]
        regime = detect_regime(bars, idx, donchian_period, confirmation_bars)
        atr = compute_atr(bars, idx)
        trail_distance = atr * atr_mult

        # Update existing positions
        for pos in list(positions):
            if pos.direction == "BUY":
                pos.highest_since_entry = max(pos.highest_since_entry, bar["high"])
                pos.trail_stop = max(pos.trail_stop, pos.highest_since_entry - trail_distance)
                # Check exit conditions
                if bar["low"] <= pos.trail_stop:
                    # Trail hit — exit at trail stop
                    pnl = unit_pnl_usd(symbol, "BUY", pos.entry_price, pos.trail_stop, spread_px)
                    realized_pnls.append(pnl)
                    positions.remove(pos)
                    total_trail_stops += 1
                elif idx - pos.entry_idx >= max_hold_bars:
                    # Max hold reached — exit at current close
                    pnl = unit_pnl_usd(symbol, "BUY", pos.entry_price, bar["close"], spread_px)
                    realized_pnls.append(pnl)
                    positions.remove(pos)
                    total_max_hold += 1
                elif regime == "RANGING":
                    # Regime switched back to ranging — exit
                    pnl = unit_pnl_usd(symbol, "BUY", pos.entry_price, bar["close"], spread_px)
                    realized_pnls.append(pnl)
                    positions.remove(pos)
                    trends_exited_profit += 1
            else:  # SELL
                pos.lowest_since_entry = min(pos.lowest_since_entry, bar["low"])
                pos.trail_stop = min(pos.trail_stop, pos.lowest_since_entry + trail_distance)
                if bar["high"] >= pos.trail_stop:
                    pnl = unit_pnl_usd(symbol, "SELL", pos.entry_price, pos.trail_stop, spread_px)
                    realized_pnls.append(pnl)
                    positions.remove(pos)
                    total_trail_stops += 1
                elif idx - pos.entry_idx >= max_hold_bars:
                    pnl = unit_pnl_usd(symbol, "SELL", pos.entry_price, bar["close"], spread_px)
                    realized_pnls.append(pnl)
                    positions.remove(pos)
                    total_max_hold += 1
                elif regime == "RANGING":
                    pnl = unit_pnl_usd(symbol, "SELL", pos.entry_price, bar["close"], spread_px)
                    realized_pnls.append(pnl)
                    positions.remove(pos)
                    trends_exited_profit += 1

        # Enter new trend position if regime = TRENDING and no position
        if regime in ("TRENDING_UP", "TRENDING_DOWN") and not positions:
            if regime == "TRENDING_UP":
                # Breakout above confirmed — enter BUY
                positions.append(TrendPosition(
                    direction="BUY",
                    entry_price=bar["close"],
                    entry_idx=idx,
                    trail_stop=bar["close"] - trail_distance,
                    highest_since_entry=bar["high"],
                    lowest_since_entry=bar["low"],
                ))
                trends_captured += 1
            else:  # TRENDING_DOWN
                # Breakout below confirmed — enter SELL
                positions.append(TrendPosition(
                    direction="SELL",
                    entry_price=bar["close"],
                    entry_idx=idx,
                    trail_stop=bar["close"] + trail_distance,
                    highest_since_entry=bar["high"],
                    lowest_since_entry=bar["low"],
                ))
                trends_captured += 1

        max_concurrent = max(max_concurrent, len(positions))

    # Close remaining positions at end
    last_close = bars[-1]["close"]
    for pos in positions:
        pnl = unit_pnl_usd(symbol, pos.direction, pos.entry_price, last_close, spread_px)
        realized_pnls.append(pnl)

    realized_net = sum(realized_pnls)
    winning_pnls = [p for p in realized_pnls if p > 0]
    losing_pnls = [p for p in realized_pnls if p <= 0]

    return {
        "realized_net_usd": round(realized_net, 2),
        "total_trades": len(realized_pnls),
        "winning_trades": len(winning_pnls),
        "losing_trades": len(losing_pnls),
        "avg_win": round(sum(winning_pnls) / len(winning_pnls), 2) if winning_pnls else 0,
        "avg_loss": round(sum(losing_pnls) / len(losing_pnls), 2) if losing_pnls else 0,
        "win_rate": round(len(winning_pnls) / len(realized_pnls) * 100, 1) if realized_pnls else 0,
        "trends_captured": trends_captured,
        "trail_stops_hit": total_trail_stops,
        "max_hold_exits": total_max_hold,
        "max_concurrent": max_concurrent,
    }


def unit_pnl_usd(symbol: str, direction: str, entry_price: float, exit_price: float, spread_px: float) -> float:
    order_type = mt5.ORDER_TYPE_BUY if direction == "BUY" else mt5.ORDER_TYPE_SELL
    gross = mt5.order_calc_profit(order_type, symbol, 0.01, entry_price, exit_price)
    if gross is None:
        return 0.0
    if direction == "BUY":
        spread_cost = mt5.order_calc_profit(order_type, symbol, 0.01, entry_price + spread_px, entry_price)
    else:
        spread_cost = mt5.order_calc_profit(order_type, symbol, 0.01, entry_price, entry_price + spread_px)
    if spread_cost is None:
        spread_cost = 0.0
    return float(gross) - abs(float(spread_cost))


def main() -> int:
    parser = argparse.ArgumentParser(description="Trend-Follower Prototype")
    parser.add_argument("--symbol", default="BTCUSD")
    parser.add_argument("--days", type=int, default=90)
    parser.add_argument("--atr-mult", type=float, default=2.0, help="ATR multiplier for trail stop")
    parser.add_argument("--max-hold", type=int, default=24, help="Maximum bars to hold a position")
    parser.add_argument("--adx-threshold", type=int, default=25, help="ADX threshold for trending regime")
    parser.add_argument("--output-csv", default=str(ROOT / "reports" / "trend_follower_results.csv"))
    args = parser.parse_args()

    if not mt5.initialize():
        print("MetaTrader5 initialize() failed")
        return 1

    try:
        symbol = args.symbol
        info = mt5.symbol_info(symbol)
        if info is None:
            print(f"Symbol info not found for {symbol}")
            return 1

        bars = load_tf_bars(symbol, mt5.TIMEFRAME_H1, args.days)
        if not bars:
            print(f"No bars for {symbol}")
            return 1

        print(f"\n{'='*100}")
        print(f"  TREND-FOLLOWER PROTOTYPE — {symbol}, {args.days}d H1")
        print(f"  ATR mult: {args.atr_mult}, Max hold: {args.max_hold}, ADX threshold: {args.adx_threshold}")
        print(f"{'='*100}\n")

        result = simulate_trend_follower(symbol, bars, info,
                                        atr_mult=args.atr_mult,
                                        max_hold_bars=args.max_hold,
                                        donchian_period=50,
                                        confirmation_bars=3)

        print(f"  Realized Net: ${result['realized_net_usd']:,.2f}")
        print(f"  Total Trades: {result['total_trades']}")
        print(f"  Winning: {result['winning_trades']} ({result['win_rate']}%)")
        print(f"  Losing: {result['losing_trades']}")
        print(f"  Avg Win: ${result['avg_win']:.2f}")
        print(f"  Avg Loss: ${result['avg_loss']:.2f}")
        print(f"  Trends Captured: {result['trends_captured']}")
        print(f"  Trail Stops Hit: {result['trail_stops_hit']}")
        print(f"  Max Hold Exits: {result['max_hold_exits']}")
        print(f"  Max Concurrent: {result['max_concurrent']}")

        # Write CSV
        out_path = Path(args.output_csv)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        with out_path.open("w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=result.keys())
            writer.writeheader()
            writer.writerow(result)
        print(f"\n  Wrote {out_path}")

        return 0
    finally:
        mt5.shutdown()


if __name__ == "__main__":
    raise SystemExit(main())
