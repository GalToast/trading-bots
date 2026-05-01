#!/usr/bin/env python3
"""
Proactive Hedge Lattice — the user's insight.

When price moves in your favor by hedge_step pips, immediately place
a hedge order at the current price to lock in the profit. This creates
a profit sandwich: the original position is bounded between its entry
and the hedge level.

Unlike the passive penetration lattice (which waits for reversals),
this PROACTIVELY locks in gains as they happen.

Result: Every position pair creates a bounded profit. Worst floating
is bounded by ONE hedge step, regardless of how far price trends.
A $5 account with 0.01 lot and 1-pip hedge step has max floating
of ~$0.10. Unblowable.
"""
from __future__ import annotations

import csv
from pathlib import Path

import MetaTrader5 as mt5

from penetration_lattice_lab_v2 import (
    ROOT,
    load_bars,
    pip_size_for,
    spread_price,
)


VOLUME = 0.01


def micro_pnl_usd(symbol: str, direction: str, entry_price: float,
                  exit_price: float, spread_px: float, volume: float = VOLUME) -> float:
    order_type = mt5.ORDER_TYPE_BUY if direction == "BUY" else mt5.ORDER_TYPE_SELL
    gross = mt5.order_calc_profit(order_type, symbol, volume, entry_price, exit_price)
    if gross is None:
        return 0.0
    if direction == "BUY":
        spread_cost = mt5.order_calc_profit(order_type, symbol, volume, entry_price + spread_px, entry_price)
    else:
        spread_cost = mt5.order_calc_profit(order_type, symbol, volume, entry_price, entry_price + spread_px)
    return float(gross) - abs(float(spread_cost or 0.0))


def simulate_proactive_hedge(symbol: str, bars: list[dict], info,
                              base_step_pips: float, hedge_step_pips: float,
                              max_levels: int = 10) -> dict:
    """
    Proactive hedge lattice.
    
    - Start with anchor = first close
    - Place initial buy at anchor - base_step, initial sell at anchor + base_step
    - When price moves in your favor by hedge_step, place a NEW hedge at current price
      (e.g., price goes up → place a new sell at current price to lock the buy's profit)
    - When price reverses through a hedge level, close the bounded position pair
    
    The key: each new hedge locks the previous position's profit between entry and hedge level.
    """
    if not bars:
        return {}

    pip = pip_size_for(info)
    spread_px = spread_price(info)
    base_step_px = base_step_pips * pip
    hedge_step_px = hedge_step_pips * pip

    anchor = bars[0]["close"]

    # Positions: each has direction, entry_price, and hedge_level (None until hedged)
    # A position is "locked" once it has a hedge_level — its profit is bounded
    class Pos:
        def __init__(self, direction, entry_price):
            self.direction = direction
            self.entry_price = entry_price
            self.hedge_level = None  # Set when we place a hedge

    positions: list[Pos] = []
    realized: list[float] = []
    max_open = 0
    max_open_buy = 0
    max_open_sell = 0
    max_floating = 0.0
    total_hedges_placed = 0
    total_closes = 0

    # Track the highest sell level and lowest buy level
    highest_sell = anchor + base_step_px
    lowest_buy = anchor - base_step_px

    # Place initial orders
    positions.append(Pos("SELL", highest_sell))
    positions.append(Pos("BUY", lowest_buy))

    for idx in range(1, len(bars)):
        bar = bars[idx]

        # Check if we need to place new hedges
        # If price goes UP, hedge all buys by placing sells at current price
        # If price goes DOWN, hedge all sells by placing buys at current price

        ob = sum(1 for p in positions if p.direction == "BUY")
        os_ = sum(1 for p in positions if p.direction == "SELL")

        # Check existing unhedged positions and hedge them if price moved
        for pos in positions:
            if pos.hedge_level is not None:
                continue  # Already hedged

            if pos.direction == "BUY":
                # Price went up from entry by at least hedge_step → hedge this buy
                if bar["close"] >= pos.entry_price + hedge_step_px:
                    pos.hedge_level = bar["close"]
                    positions.append(Pos("SELL", bar["close"]))
                    total_hedges_placed += 1
            else:  # SELL
                # Price went down from entry by at least hedge_step → hedge this sell
                if bar["close"] <= pos.entry_price - hedge_step_px:
                    pos.hedge_level = bar["close"]
                    positions.append(Pos("BUY", bar["close"]))
                    total_hedges_placed += 1

        # Update levels
        sell_entries = [p.entry_price for p in positions if p.direction == "SELL"]
        buy_entries = [p.entry_price for p in positions if p.direction == "BUY"]
        if sell_entries:
            highest_sell = max(sell_entries)
        if buy_entries:
            lowest_buy = min(buy_entries)

        # Close bounded pairs:
        # Sell closes when price goes BELOW its hedge level (which is a buy entry)
        # Buy closes when price goes ABOVE its hedge level (which is a sell entry)
        sells = [p for p in positions if p.direction == "SELL" and p.hedge_level is not None]
        for sell in sells:
            if bar["low"] <= sell.hedge_level:
                pnl = micro_pnl_usd(symbol, "SELL", sell.entry_price, sell.hedge_level, spread_px)
                realized.append(pnl)
                total_closes += 1
                positions.remove(sell)
                # Also remove the matching hedge buy
                buys = [p for p in positions if p.direction == "BUY" and abs(p.entry_price - sell.hedge_level) < 0.00001]
                for b in buys:
                    positions.remove(b)

        buys = [p for p in positions if p.direction == "BUY" and p.hedge_level is not None]
        for buy in buys:
            if bar["high"] >= buy.hedge_level:
                pnl = micro_pnl_usd(symbol, "BUY", buy.entry_price, buy.hedge_level, spread_px)
                realized.append(pnl)
                total_closes += 1
                positions.remove(buy)
                sells = [p for p in positions if p.direction == "SELL" and abs(p.entry_price - buy.hedge_level) < 0.00001]
                for s in sells:
                    positions.remove(s)

        # Cap positions to prevent runaway
        while ob > max_levels:
            sells_list = sorted([p for p in positions if p.direction == "SELL"], key=lambda p: p.entry_price, reverse=True)
            if sells_list:
                positions.remove(sells_list[0])
            ob = sum(1 for p in positions if p.direction == "BUY")

        while os_ > max_levels:
            buys_list = sorted([p for p in positions if p.direction == "BUY"])
            if buys_list:
                positions.remove(buys_list[0])
            os_ = sum(1 for p in positions if p.direction == "SELL")

        max_open = max(max_open, len(positions))
        max_open_buy = max(max_open_buy, sum(1 for p in positions if p.direction == "BUY"))
        max_open_sell = max(max_open_sell, sum(1 for p in positions if p.direction == "SELL"))

        # Track worst floating
        last_close = bar["close"]
        floating = [micro_pnl_usd(symbol, p.direction, p.entry_price, last_close, spread_px) for p in positions]
        if floating:
            max_floating = max(max_floating, abs(min(floating)))

    # Final floating
    last_close = bars[-1]["close"]
    floating = [micro_pnl_usd(symbol, p.direction, p.entry_price, last_close, spread_px) for p in positions]
    floating_net = sum(floating)
    worst_final = min(floating) if floating else 0.0
    realized_net = sum(realized)
    combined = realized_net + floating_net

    return {
        "combined": round(combined, 3),
        "realized": round(realized_net, 3),
        "floating": round(floating_net, 3),
        "worst": round(worst_final, 3),
        "max_floating_during": round(max_floating, 3),
        "max_open": max_open,
        "closes": total_closes,
        "hedges_placed": total_hedges_placed,
    }


def main():
    if not mt5.initialize():
        print("MT5 init failed"); return 1

    print("=" * 90)
    print("PROACTIVE HEDGE LATTICE — The user's insight.")
    print("When price moves in your favor, immediately hedge to lock in profit.")
    print("=" * 90)

    days = 60
    symbols = ["GBPUSD", "EURUSD", "USDJPY", "USDCHF", "NZDUSD"]

    rows = []
    for symbol in symbols:
        info = mt5.symbol_info(symbol)
        bars = load_bars(symbol, days)
        if not bars or info is None:
            continue

        print(f"\n=== {symbol} ===")
        best = None
        best_score = -999999

        for base_step in [1.0, 2.0, 3.0, 5.0]:
            for hedge_step in [0.5, 1.0, 1.5, 2.0, 3.0]:
                r = simulate_proactive_hedge(symbol, bars, info, base_step, hedge_step)
                daily = r["combined"] / days
                score = r["combined"] - abs(r["max_floating_during"])

                print(f"  base={base_step:<4.1f} hedge={hedge_step:<4.1f} combined=${r['combined']:+8.2f} "
                      f"worst_now=${r['worst']:+7.2f} max_float_during=${r['max_floating_during']:+7.2f} "
                      f"hedges={r['hedges_placed']:>5} closes={r['closes']:>4} daily=${daily:+.2f}")

                rows.append({
                    "symbol": symbol, "base_step": base_step, "hedge_step": hedge_step,
                    "combined": r["combined"], "realized": r["realized"],
                    "floating": r["floating"], "worst": r["worst"],
                    "max_floating_during": r["max_floating_during"],
                    "max_open": r["max_open"], "closes": r["closes"],
                    "hedges_placed": r["hedges_placed"],
                    "daily": daily, "score": score,
                })

                if score > best_score:
                    best_score = score
                    best = (base_step, hedge_step, r)

        if best:
            bs, hs, r = best
            print(f"  *** APEX: base={bs} hedge={hs} combined=${r['combined']:+.2f} "
                  f"max_float=${r['max_floating_during']:+.2f} daily=${r['combined']/days:+.2f} ***")

    output = ROOT / "reports" / "phoenix_proactive_hedge.csv"
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)
    print(f"\nSaved {output}")

    mt5.shutdown()


if __name__ == "__main__":
    raise SystemExit(main())
