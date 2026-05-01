#!/usr/bin/env python3
"""
Active vs Passive Lattice — head-to-head benchmark.

PASSIVE: Orders at fixed levels. Price must penetrate back to close.
ACTIVE:  Same fixed levels, BUT whenever any position is in profit
         by hedge_threshold pips, immediately place a hedge at current
         price. This creates additional close checkpoints that don't
         wait for the next fixed level.

Both use the same penetration close logic. The difference is:
active creates MORE hedge levels as price moves, giving MORE
close opportunities and SMALLER floating drawdown.
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


def pnl_usd(symbol, direction, entry, exit_px, spread_px, vol=VOLUME):
    ot = mt5.ORDER_TYPE_BUY if direction == "BUY" else mt5.ORDER_TYPE_SELL
    gross = mt5.order_calc_profit(ot, symbol, vol, entry, exit_px)
    if gross is None:
        return 0.0
    if direction == "BUY":
        sc = mt5.order_calc_profit(ot, symbol, vol, entry + spread_px, entry)
    else:
        sc = mt5.order_calc_profit(ot, symbol, vol, entry, entry + spread_px)
    return float(gross) - abs(float(sc or 0.0))


class Pos:
    __slots__ = ['direction', 'entry', 'hedge_level', 'id']
    def __init__(self, direction, entry, id_=0):
        self.direction = direction
        self.entry = entry
        self.hedge_level = None
        self.id = id_

_next_id = 0
def _new_pos(*a, **kw):
    global _next_id
    p = Pos(*a, **kw)
    p.id = _next_id
    _next_id += 1
    return p


def run_lattice(symbol, bars, info, step_pips, cap, active=False, hedge_thresh_pips=None):
    global _next_id
    _next_id = 0

    if not bars:
        return {}

    pip = pip_size_for(info)
    spread = spread_price(info)
    base_step = step_pips * pip
    hedge_thresh = (hedge_thresh_pips or step_pips) * pip

    anchor = bars[0]["close"]
    positions: list[Pos] = []
    realized: list[float] = []
    max_open = 0
    max_open_sell = 0
    max_open_buy = 0
    worst_floating_seen = 0.0

    # Place initial buy/sell
    sell_level = anchor + base_step
    buy_level = anchor - base_step
    positions.append(_new_pos("SELL", sell_level))
    positions.append(_new_pos("BUY", buy_level))

    for idx in range(1, len(bars)):
        bar = bars[idx]
        price = bar["close"]

        # --- Open new fixed-level orders ---
        obs = sum(1 for p in positions if p.direction == "BUY")
        oss = sum(1 for p in positions if p.direction == "SELL")

        while bar["high"] >= sell_level and oss < cap:
            positions.append(_new_pos("SELL", sell_level))
            oss += 1
            # Adaptive step
            if oss >= 20:
                sell_level += base_step * 2.0
            elif oss >= 10:
                sell_level += base_step * 1.5
            else:
                sell_level += base_step

        while bar["low"] <= buy_level and obs < cap:
            positions.append(_new_pos("BUY", buy_level))
            obs += 1
            if obs >= 20:
                buy_level -= base_step * 2.0
            elif obs >= 10:
                buy_level -= base_step * 1.5
            else:
                buy_level -= base_step

        # --- ACTIVE: proactively hedge profitable positions ---
        if active and hedge_thresh > 0:
            for pos in positions:
                if pos.hedge_level is not None:
                    continue
                if pos.direction == "BUY" and price >= pos.entry + hedge_thresh:
                    pos.hedge_level = price
                    positions.append(_new_pos("SELL", price))
                elif pos.direction == "SELL" and price <= pos.entry - hedge_thresh:
                    pos.hedge_level = price
                    positions.append(_new_pos("BUY", price))

        # --- Penetration close (two-level) ---
        # Sells: close outermost when price penetrates below the next sell level
        sells = sorted([p for p in positions if p.direction == "SELL"],
                       key=lambda p: p.entry, reverse=True)
        while len(sells) >= 2 and bar["low"] <= sells[1].entry:
            # Close at the penetration level
            close_ref = sells[1].entry
            pnl = pnl_usd(symbol, "SELL", sells[0].entry, close_ref, spread)
            if pnl <= 0:
                break
            realized.append(pnl)
            positions.remove(sells[0])
            sells = sorted([p for p in positions if p.direction == "SELL"],
                           key=lambda p: p.entry, reverse=True)

        buys = sorted([p for p in positions if p.direction == "BUY"],
                      key=lambda p: p.entry)
        while len(buys) >= 2 and bar["high"] >= buys[1].entry:
            close_ref = buys[1].entry
            pnl = pnl_usd(symbol, "BUY", buys[0].entry, close_ref, spread)
            if pnl <= 0:
                break
            realized.append(pnl)
            positions.remove(buys[0])
            buys = sorted([p for p in positions if p.direction == "BUY"],
                          key=lambda p: p.entry)

        # Cap total positions to prevent runaway
        while len(positions) > cap * 2:
            # Remove the furthest unhedged position
            unhedged = [p for p in positions if p.hedge_level is None]
            if unhedged:
                positions.remove(unhedged[0])
            else:
                break

        max_open = max(max_open, len(positions))
        max_open_buy = max(max_open_buy, sum(1 for p in positions if p.direction == "BUY"))
        max_open_sell = max(max_open_sell, sum(1 for p in positions if p.direction == "SELL"))

        # Track worst floating
        floating_now = [pnl_usd(symbol, p.direction, p.entry, price, spread) for p in positions]
        if floating_now:
            worst_floating_seen = max(worst_floating_seen, abs(min(floating_now)))

    # Final
    last_close = bars[-1]["close"]
    floating = [pnl_usd(symbol, p.direction, p.entry, last_close, spread) for p in positions]
    floating_net = sum(floating)
    worst_final = min(floating) if floating else 0.0
    realized_net = sum(realized)
    combined = realized_net + floating_net

    return {
        "combined": round(combined, 3),
        "realized": round(realized_net, 3),
        "floating": round(floating_net, 3),
        "worst_final": round(worst_final, 3),
        "worst_seen": round(worst_floating_seen, 3),
        "max_open": max_open,
        "closes": len(realized),
    }


def main():
    if not mt5.initialize():
        print("MT5 init failed"); return 1

    print("=" * 100)
    print("ACTIVE vs PASSIVE LATTICE — Head-to-head benchmark")
    print("PASSIVE: penetration closes only")
    print("ACTIVE:  penetration closes + proactive hedges on profitable positions")
    print("=" * 100)

    symbols = ["GBPUSD", "EURUSD", "NZDUSD"]
    days = 60
    rows = []

    for symbol in symbols:
        info = mt5.symbol_info(symbol)
        bars = load_bars(symbol, days)
        if not bars or info is None:
            continue

        print(f"\n{'='*80}")
        print(f"=== {symbol} ===")
        print(f"{'='*80}")

        for step in [1.5, 2.0, 3.0]:
            for cap in [2, 3, 5, 10, 20]:
                # PASSIVE
                r_passive = run_lattice(symbol, bars, info, step, cap, active=False)
                # ACTIVE (hedge threshold = half the step)
                r_active = run_lattice(symbol, bars, info, step, cap, active=True, hedge_thresh_pips=step/2)

                daily_p = r_passive["combined"] / days
                daily_a = r_active["combined"] / days

                print(f"  step={step:<4.1f} cap={cap:>2} | "
                      f"PASSIVE combined=${r_passive['combined']:+8.2f} closes={r_passive['closes']:>4} "
                      f"worst_seen=${r_passive['worst_seen']:+7.2f} daily=${daily_p:+6.2f} | "
                      f"ACTIVE combined=${r_active['combined']:+8.2f} closes={r_active['closes']:>4} "
                      f"worst_seen=${r_active['worst_seen']:+7.2f} daily=${daily_a:+6.2f}")

                rows.append({
                    "symbol": symbol, "step": step, "cap": cap,
                    "mode_passive_combined": r_passive["combined"],
                    "mode_passive_closes": r_passive["closes"],
                    "mode_passive_worst": r_passive["worst_seen"],
                    "mode_passive_daily": daily_p,
                    "mode_active_combined": r_active["combined"],
                    "mode_active_closes": r_active["closes"],
                    "mode_active_worst": r_active["worst_seen"],
                    "mode_active_daily": daily_a,
                })

    # Summary
    print(f"\n{'='*100}")
    print("DELTA SUMMARY: Active - Passive")
    print(f"{'='*100}")

    for symbol in symbols:
        sym_rows = [r for r in rows if r["symbol"] == symbol]
        if not sym_rows:
            continue
        print(f"\n  {symbol}:")
        for r in sym_rows:
            delta_combined = r["mode_active_combined"] - r["mode_passive_combined"]
            delta_closes = r["mode_active_closes"] - r["mode_passive_closes"]
            delta_worst = r["mode_active_worst"] - r["mode_passive_worst"]
            tag = "🔥" if delta_combined > 50 else "❄️" if delta_combined < -50 else "➡️"
            print(f"    {tag} step={r['step']:<4.1f} cap={r['cap']:>2} | "
                  f"Δcombined=${delta_combined:+8.2f} Δcloses={delta_closes:>+5} "
                  f"Δworst_seen=${delta_worst:+7.2f}")

    output = ROOT / "reports" / "active_vs_passive_lattice.csv"
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)
    print(f"\nSaved {output}")

    mt5.shutdown()


if __name__ == "__main__":
    raise SystemExit(main())
