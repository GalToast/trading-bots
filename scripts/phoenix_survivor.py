#!/usr/bin/env python3
"""
Survivor Mode — Can we recover a $5 account with 1 symbol, 2 positions max?

The theory: at 0.01 lot with cap=2 per side, worst floating is step_size × 1 position.
That's $0.02-$0.05. A $5 account laughs at that.
The tradeoff: fewer positions = fewer closes = slower grinding.
But it SURVIVES. And that's the point at $5.
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
    """Unit PnL at arbitrary volume."""
    order_type = mt5.ORDER_TYPE_BUY if direction == "BUY" else mt5.ORDER_TYPE_SELL
    gross = mt5.order_calc_profit(order_type, symbol, volume, entry_price, exit_price)
    if gross is None:
        return 0.0
    if direction == "BUY":
        spread_cost = mt5.order_calc_profit(order_type, symbol, volume, entry_price + spread_px, entry_price)
    else:
        spread_cost = mt5.order_calc_profit(order_type, symbol, volume, entry_price, entry_price + spread_px)
    return float(gross) - abs(float(spread_cost or 0.0))


VOLUME = 0.01


def micro_step(base_step: float, count: int) -> float:
    if count >= 20:
        return base_step * 2.0
    elif count >= 10:
        return base_step * 1.5
    return base_step


def simulate_micro(symbol: str, bars: list[dict], info, step_pips: float, cap: int) -> dict:
    """Raw penetration close2, but with micro cap."""
    if not bars:
        return {}

    pip = pip_size_for(info)
    spread_px = spread_price(info)
    base_step_px = step_pips * pip

    anchor = bars[0]["close"]
    next_sell = anchor + base_step_px
    next_buy = anchor - base_step_px

    open_tickets = []
    realized = []
    max_open = 0
    max_open_buy = 0
    max_open_sell = 0

    for idx in range(1, len(bars)):
        bar = bars[idx]
        ob = sum(1 for t in open_tickets if t.direction == "BUY")
        os_ = sum(1 for t in open_tickets if t.direction == "SELL")

        while bar["high"] >= next_sell and os_ < cap:
            open_tickets.append(type("T", (), {"direction": "SELL", "entry_price": next_sell, "opened_idx": idx})())
            os_ += 1
            next_sell += micro_step(base_step_px, os_)

        while bar["low"] <= next_buy and ob < cap:
            open_tickets.append(type("T", (), {"direction": "BUY", "entry_price": next_buy, "opened_idx": idx})())
            ob += 1
            next_buy -= micro_step(base_step_px, ob)

        # Two-level penetration close
        sells = sorted((t for t in open_tickets if t.direction == "SELL"), key=lambda t: t.entry_price, reverse=True)
        while len(sells) >= 2 and bar["low"] <= sells[1].entry_price:
            close_ref = sells[1].entry_price
            pnl = micro_pnl_usd(symbol, "SELL", sells[0].entry_price, close_ref, spread_px)
            if pnl <= 0:
                break
            realized.append(pnl)
            open_tickets.remove(sells[0])
            sells = sorted((t for t in open_tickets if t.direction == "SELL"), key=lambda t: t.entry_price, reverse=True)

        buys = sorted((t for t in open_tickets if t.direction == "BUY"), key=lambda t: t.entry_price)
        while len(buys) >= 2 and bar["high"] >= buys[1].entry_price:
            close_ref = buys[1].entry_price
            pnl = micro_pnl_usd(symbol, "BUY", buys[0].entry_price, close_ref, spread_px)
            if pnl <= 0:
                break
            realized.append(pnl)
            open_tickets.remove(buys[0])
            buys = sorted((t for t in open_tickets if t.direction == "BUY"), key=lambda t: t.entry_price)

        max_open = max(max_open, len(open_tickets))
        max_open_buy = max(max_open_buy, sum(1 for t in open_tickets if t.direction == "BUY"))
        max_open_sell = max(max_open_sell, sum(1 for t in open_tickets if t.direction == "SELL"))

    last_close = bars[-1]["close"]
    floating = [micro_pnl_usd(symbol, t.direction, t.entry_price, last_close, spread_px) for t in open_tickets]
    worst = min(floating) if floating else 0.0
    floating_net = sum(floating)
    realized_net = sum(realized)
    combined = realized_net + floating_net

    return {
        "combined": round(combined, 3),
        "realized": round(realized_net, 3),
        "floating": round(floating_net, 3),
        "worst": round(worst, 3),
        "max_open": max_open,
        "max_buy": max_open_buy,
        "max_sell": max_open_sell,
        "closes": len(realized),
    }


def main():
    if not mt5.initialize():
        print("MT5 init failed"); return 1

    print("=" * 90)
    print("SURVIVOR MODE — 1 symbol, max 2 positions. The $5 recovery plan.")
    print("=" * 90)

    days = 60
    symbols = ["GBPUSD", "EURUSD", "USDJPY", "USDCHF", "NZDUSD"]
    caps = [2, 3, 4, 5]
    steps = [1.0, 1.5, 2.0, 3.0, 5.0]

    rows = []

    for symbol in symbols:
        info = mt5.symbol_info(symbol)
        bars = load_bars(symbol, days)
        if not bars or info is None:
            continue

        print(f"\n=== {symbol} ===")
        best = None
        best_score = -999999

        for cap in caps:
            for step in steps:
                r = simulate_micro(symbol, bars, info, step, cap)
                daily = r["combined"] / days

                # Score: combined minus floating risk
                score = r["combined"] - abs(r["worst"])

                print(f"  cap={cap} step={step:<4.1f} combined=${r['combined']:+8.2f} realized=${r['realized']:+8.2f} "
                      f"worst=${r['worst']:+7.2f} closes={r['closes']:>5} daily=${daily:+.2f}")

                rows.append({
                    "symbol": symbol, "cap": cap, "step": step,
                    "combined": r["combined"], "realized": r["realized"],
                    "floating": r["floating"], "worst": r["worst"],
                    "max_open": r["max_open"], "closes": r["closes"],
                    "daily": daily, "score": score,
                })

                if score > best_score:
                    best_score = score
                    best = (cap, step, r)

        if best:
            cap, step, r = best
            print(f"  *** SURVIVOR APEX: cap={cap} step={step} combined=${r['combined']:+.2f} worst=${r['worst']:+.2f} ***")

    # Show the $5 recovery path
    print("\n" + "=" * 90)
    print("THE $5 SURVIVOR PATH")
    print("=" * 90)

    # Find best survivor config per symbol (lowest worst floating, decent combined)
    for symbol in symbols:
        sym_rows = [r for r in rows if r["symbol"] == symbol]
        if not sym_rows:
            continue

        # Best survivor: lowest worst floating while still positive
        survivors = [r for r in sym_rows if r["worst"] >= -5.0 and r["combined"] > 0]
        if survivors:
            best = max(survivors, key=lambda r: r["combined"])
        else:
            best = max(sym_rows, key=lambda r: r["score"])

        # Calculate time to climb from $5 to $30
        daily = best["daily"]
        needed = 25  # $30 - $5
        days_to_30 = needed / daily if daily > 0 else float('inf')

        print(f"\n  {symbol}: cap={best['cap']} step={best['step']}")
        print(f"    Daily: ${daily:+.2f} | Worst float: ${best['worst']:+.2f} | Closes: {best['closes']}/60d")
        print(f"    Days to climb $5 → $30: {days_to_30:.0f} days")
        print(f"    Equity after 60d: ${5 + daily * 60:.2f}")
        print(f"    Equity after 90d: ${5 + daily * 90:.2f}")

        # Now simulate the step-up path
        print(f"\n    SURVIVOR → PHOENIX PATH:")
        print(f"    $5   → cap={best['cap']} step={best['step']} 1 symbol     → ${daily:+.2f}/day")

        # At $30, switch to 2-symbol (GBPUSD+EURUSD) with cap based on survivor
        if symbol in ("GBPUSD", "EURUSD"):
            daily_2sym = sum(
                max([r for r in rows if r["symbol"] == s and r["cap"] == best["cap"]],
                    key=lambda r: r["combined"])["daily"]
                for s in ["GBPUSD", "EURUSD"]
            )
            days_to_50 = 20 / daily_2sym
            print(f"    $30  → 2 symbols (GBPUSD+EURUSD)         → ${daily_2sym:+.2f}/day → $50 in {days_to_50:.0f}d")

        if symbol == "GBPUSD":
            # At $50, full 5-symbol with cap based on their own apex
            full_rows = [r for r in rows if r["cap"] <= 4]  # reasonable cap for multi-symbol
            if full_rows:
                # Sum best per symbol with cap=4
                daily_5sym = 0
                for s in ["GBPUSD", "EURUSD", "NZDUSD"]:
                    s_rows = [r for r in rows if r["symbol"] == s and r["cap"] == 4]
                    if s_rows:
                        daily_5sym += max(s_rows, key=lambda r: r["combined"])["daily"]
                # Add V3 hostiles approx
                daily_5sym += 15  # approximate from earlier V3 results
                print(f"    $50  → 5 symbols (full basket)           → ~${daily_5sym:+.2f}/day → gatling gun")

    output = ROOT / "reports" / "phoenix_survivor.csv"
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)
    print(f"\nSaved {output}")

    mt5.shutdown()


if __name__ == "__main__":
    raise SystemExit(main())
