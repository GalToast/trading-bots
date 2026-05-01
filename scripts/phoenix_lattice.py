#!/usr/bin/env python3
"""
Phoenix Lattice — self-scaling, self-recovering lattice bot.

Philosophy: The lattice NEVER stops. At $5 it trades pennies.
At $500 it trades dollars. At $5000 it trades hundreds.
It climbs out of drawdown like American Ninja Warrior — each
penetration close is a handhold. As equity grows, lot sizes
increase automatically. Gatling gun acceleration.

Tiers (equity-based):
  $1-10:   0.01 lot, 2 symbols (GBPUSD, EURUSD only)
  $10-25:  0.01 lot, 3 symbols (+ NZDUSD)
  $25-50:  0.01 lot, 4 symbols (+ USDJPY V3)
  $50-100: 0.01 lot, 5 symbols (+ USDCHF V3)
  $100+:   lot = floor(equity / 100) * 0.01, capped at 0.05 initially
  
Gatling acceleration: every $200 of profit, bump lot size by 0.01.
"""
from __future__ import annotations

import argparse
import csv
import math
from dataclasses import dataclass
from pathlib import Path
from statistics import mean

import MetaTrader5 as mt5


ROOT = Path(__file__).resolve().parent.parent

@dataclass
class Tier:
    min_equity: float
    max_equity: float
    lot_size: float
    symbols: list[str]
    symbol_modes: dict[str, str]  # symbol -> "raw" or "v3"

TIERS = [
    Tier(1,    10,   0.01, ["GBPUSD", "EURUSD"],
         {"GBPUSD": "raw", "EURUSD": "raw"}),
    Tier(10,   25,   0.01, ["GBPUSD", "EURUSD", "NZDUSD"],
         {"GBPUSD": "raw", "EURUSD": "raw", "NZDUSD": "raw"}),
    Tier(25,   50,   0.01, ["GBPUSD", "EURUSD", "NZDUSD", "USDJPY"],
         {"GBPUSD": "raw", "EURUSD": "raw", "NZDUSD": "raw", "USDJPY": "v3"}),
    Tier(50,   100,  0.01, ["GBPUSD", "EURUSD", "NZDUSD", "USDJPY", "USDCHF"],
         {"GBPUSD": "raw", "EURUSD": "raw", "NZDUSD": "raw", "USDJPY": "v3", "USDCHF": "v3"}),
    Tier(100,  200,  0.01, ["GBPUSD", "EURUSD", "NZDUSD", "USDJPY", "USDCHF"],
         {"GBPUSD": "raw", "EURUSD": "raw", "NZDUSD": "raw", "USDJPY": "v3", "USDCHF": "v3"}),
    Tier(200,  500,  0.02, ["GBPUSD", "EURUSD", "NZDUSD", "USDJPY", "USDCHF"],
         {"GBPUSD": "raw", "EURUSD": "raw", "NZDUSD": "raw", "USDJPY": "v3", "USDCHF": "v3"}),
    Tier(500,  1000, 0.03, ["GBPUSD", "EURUSD", "NZDUSD", "USDJPY", "USDCHF"],
         {"GBPUSD": "raw", "EURUSD": "raw", "NZDUSD": "raw", "USDJPY": "v3", "USDCHF": "v3"}),
    Tier(1000, 2000, 0.05, ["GBPUSD", "EURUSD", "NZDUSD", "USDJPY", "USDCHF"],
         {"GBPUSD": "raw", "EURUSD": "raw", "NZDUSD": "raw", "USDJPY": "v3", "USDCHF": "v3"}),
    Tier(2000, 5000, 0.10, ["GBPUSD", "EURUSD", "NZDUSD", "USDJPY", "USDCHF"],
         {"GBPUSD": "raw", "EURUSD": "raw", "NZDUSD": "raw", "USDJPY": "v3", "USDCHF": "v3"}),
    Tier(5000, 999999, 0.20, ["GBPUSD", "EURUSD", "NZDUSD", "USDJPY", "USDCHF"],
         {"GBPUSD": "raw", "EURUSD": "raw", "NZDUSD": "raw", "USDJPY": "v3", "USDCHF": "v3"}),
]

# Apex configs per symbol
SYMBOL_CONFIGS = {
    "GBPUSD": {"raw_step": 1.75, "raw_cap": 20},
    "EURUSD": {"raw_step": 2.50, "raw_cap": 20},
    "NZDUSD": {"raw_step": 1.50, "raw_cap": 12},
    "USDJPY": {"v3_step": 0.50, "v3_cap": 20, "v3_range": 24.0, "v3_buf": 5.0, "v3_win": 240, "v3_cool": 60},
    "USDCHF": {"v3_step": 0.50, "v3_cap": 20, "v3_range": 24.0, "v3_buf": 5.0, "v3_win": 240, "v3_cool": 60},
}


def get_tier(equity: float) -> Tier:
    for tier in TIERS:
        if tier.min_equity <= equity < tier.max_equity:
            return tier
    return TIERS[-1]


def gatling_lot(equity: float, base_lot: float = 0.01) -> float:
    """Gatling gun scaling: every $200 above $100, bump lot by 0.01."""
    if equity <= 100:
        return base_lot
    bumps = int((equity - 100) / 200)
    return min(base_lot + bumps * 0.01, 1.0)  # cap at 1.0 lot for sanity


# Minimal lattice simulation for multi-tier analysis
def pip_size_for(symbol_info) -> float:
    point = float(symbol_info.point or 0.0)
    digits = int(symbol_info.digits or 0)
    return point * 10.0 if digits in (3, 5) else point


def load_bars(symbol: str, days: int) -> list[dict]:
    rates = mt5.copy_rates_from_pos(symbol, mt5.TIMEFRAME_M1, 0, 1440 * days)
    if rates is None or len(rates) == 0:
        return []
    return [
        {"time": int(r[0]), "open": float(r[1]), "high": float(r[2]),
         "low": float(r[3]), "close": float(r[4]), "tick_volume": int(r[5])}
        for r in rates
    ]


def spread_price(symbol_info) -> float:
    return float(symbol_info.spread or 0.0) * float(symbol_info.point or 0.0)


def unit_pnl_usd(symbol: str, direction: str, entry_price: float, exit_price: float,
                 spread_px: float, volume: float) -> float:
    order_type = mt5.ORDER_TYPE_BUY if direction == "BUY" else mt5.ORDER_TYPE_SELL
    gross = mt5.order_calc_profit(order_type, symbol, volume, entry_price, exit_price)
    if gross is None:
        return 0.0
    if direction == "BUY":
        spread_cost = mt5.order_calc_profit(order_type, symbol, volume, entry_price + spread_px, entry_price)
    else:
        spread_cost = mt5.order_calc_profit(order_type, symbol, volume, entry_price, entry_price + spread_px)
    return float(gross) - abs(float(spread_cost or 0.0))


@dataclass
class Ticket:
    direction: str
    entry_price: float
    opened_idx: int


def dynamic_step(base_step: float, open_count: int) -> float:
    if open_count >= 20:
        return base_step * 2.0
    elif open_count >= 10:
        return base_step * 1.5
    return base_step


def simulate_raw(symbol: str, bars: list[dict], info, step_pips: float, cap: int, volume: float) -> dict:
    if not bars:
        return {}
    pip = pip_size_for(info)
    spread_px = spread_price(info)
    base_step_px = step_pips * pip
    anchor = bars[0]["close"]
    next_sell = anchor + base_step_px
    next_buy = anchor - base_step_px
    open_tickets: list[Ticket] = []
    realized: list[float] = []
    max_open = 0
    worst_floating = 0.0

    for idx in range(1, len(bars)):
        bar = bars[idx]
        ob = sum(1 for t in open_tickets if t.direction == "BUY")
        os_ = sum(1 for t in open_tickets if t.direction == "SELL")

        while bar["high"] >= next_sell and os_ < cap:
            open_tickets.append(Ticket("SELL", next_sell, idx))
            os_ += 1
            next_sell += dynamic_step(base_step_px, os_)
        while bar["low"] <= next_buy and ob < cap:
            open_tickets.append(Ticket("BUY", next_buy, idx))
            ob += 1
            next_buy -= dynamic_step(base_step_px, ob)

        # Two-level close: close outermost at next penetration level
        sells = sorted((t for t in open_tickets if t.direction == "SELL"), key=lambda t: t.entry_price, reverse=True)
        while len(sells) >= 2 and bar["low"] <= sells[1].entry_price:
            close_ref = sells[1].entry_price  # Penetration level, not bar extremum
            pnl = unit_pnl_usd(symbol, "SELL", sells[0].entry_price, close_ref, spread_px, volume)
            if pnl <= 0:
                break
            realized.append(pnl)
            open_tickets.remove(sells[0])
            sells = sorted((t for t in open_tickets if t.direction == "SELL"), key=lambda t: t.entry_price, reverse=True)

        buys = sorted((t for t in open_tickets if t.direction == "BUY"), key=lambda t: t.entry_price)
        while len(buys) >= 2 and bar["high"] >= buys[1].entry_price:
            close_ref = buys[1].entry_price  # Penetration level, not bar extremum
            pnl = unit_pnl_usd(symbol, "BUY", buys[0].entry_price, close_ref, spread_px, volume)
            if pnl <= 0:
                break
            realized.append(pnl)
            open_tickets.remove(buys[0])
            buys = sorted((t for t in open_tickets if t.direction == "BUY"), key=lambda t: t.entry_price)

        max_open = max(max_open, len(open_tickets))

    last_close = bars[-1]["close"]
    floating = [unit_pnl_usd(symbol, t.direction, t.entry_price, last_close, spread_px, volume) for t in open_tickets]
    worst_floating = min(floating) if floating else 0.0
    realized_net = sum(realized)
    floating_net = sum(floating)

    return {
        "combined": realized_net + floating_net,
        "realized": realized_net,
        "floating": floating_net,
        "worst": worst_floating,
        "max_open": max_open,
        "closes": len(realized),
    }


def simulate_v3(symbol: str, bars: list[dict], info, cfg) -> dict:
    """Minimal V3 simulation using imported function, scaled by volume."""
    from penetration_lattice_lab_v3_bounded import simulate_symbol as sim_v3, Config as V3Config
    # We can't easily scale V3 volume here without modifying the module
    # For now, return the 0.01 lot result and scale linearly
    v3_cfg = V3Config(
        step_pips=cfg["v3_step"], max_open_per_side=cfg["v3_cap"],
        max_floating_loss_usd=-10.0, vwap_lookback=20,
        regime_lookback_bars=60, max_range_pips=cfg["v3_range"],
        breakout_buffer_pips=cfg["v3_buf"], max_lattice_window_bars=cfg["v3_win"],
        cooldown_bars=cfg["v3_cool"],
    )
    result = sim_v3(symbol, bars, info, v3_cfg)
    return {
        "combined": result["combined_net_usd"],
        "realized": result["realized_net_usd"],
        "floating": result["floating_net_usd"],
        "worst": result.get("worst_floating_usd", 0),
        "max_open": result["max_open_total"],
        "closes": result["total_closes"],
    }


def main():
    if not mt5.initialize():
        print("MT5 init failed"); return 1

    days = 60
    rows = []

    print("=" * 90)
    print("PHOENIX LATTICE — Self-Scaling Recovery Simulation")
    print("Each row shows what happens at that equity level over 60d")
    print("=" * 90)

    for tier in TIERS:
        lot = tier.lot_size
        # For tiers above $100, apply gatling scaling
        if tier.min_equity >= 100:
            lot = gatling_lot(tier.min_equity)

        total_combined = 0
        total_worst = 0
        total_closes = 0
        total_max_open = 0
        symbol_details = []

        for symbol in tier.symbols:
            info = mt5.symbol_info(symbol)
            bars = load_bars(symbol, days)
            if not bars or info is None:
                continue

            mode = tier.symbol_modes.get(symbol, "raw")
            scfg = SYMBOL_CONFIGS.get(symbol, {})

            if mode == "raw":
                r = simulate_raw(symbol, bars, info, scfg["raw_step"], scfg["raw_cap"], lot)
            else:
                r = simulate_v3(symbol, bars, info, scfg)
                # Scale V3 results by lot ratio
                ratio = lot / 0.01
                r["combined"] *= ratio
                r["realized"] *= ratio
                r["floating"] *= ratio
                r["worst"] *= ratio

            total_combined += r["combined"]
            total_worst += r["worst"]
            total_closes += r["closes"]
            total_max_open += r["max_open"]
            symbol_details.append((symbol, mode, r))

        daily = total_combined / days
        drawdown_pct = abs(total_worst) / tier.min_equity * 100 if tier.min_equity > 0 else 0
        survival = "💀 BLOWN" if abs(total_worst) > tier.min_equity else "✅ SURVIVES"

        print(f"\n💰 Equity ${tier.min_equity:>6.0f} → ${tier.max_equity:<6.0f} | Lot: {lot:.3f} | {len(tier.symbols)} symbols | {survival}")
        for sym, mode, r in symbol_details:
            print(f"  {sym:<7} {mode:<3} combined=${r['combined']:+8.2f} worst=${r['worst']:+7.2f} closes={r['closes']:>5} max_open={r['max_open']:>3}")
        print(f"  TOTAL: ${total_combined:+.2f}/60d  ${daily:+.2f}/day  worst_float=${total_worst:+.2f}  drawdown={drawdown_pct:.0f}% of equity")
        print(f"  → At end of 60d: ${tier.min_equity + total_combined:.2f}")
        print(f"  → Next tier reached: ", end="")
        end_equity = tier.min_equity + total_combined
        next_tier = get_tier(end_equity)
        if next_tier.min_equity > tier.min_equity:
            print(f"${next_tier.min_equity} lot={next_tier.lot_size} ({next_tier.max_equity - next_tier.min_equity} more to go)")
        else:
            print(f"Already here!")

        rows.append({
            "start_equity": tier.min_equity,
            "end_equity": tier.min_equity + total_combined,
            "lot_size": lot,
            "num_symbols": len(tier.symbols),
            "symbols": ",".join(tier.symbols),
            "combined_60d": round(total_combined, 2),
            "daily": round(daily, 2),
            "worst_floating": round(total_worst, 2),
            "drawdown_pct": round(drawdown_pct, 1),
            "total_closes": total_closes,
            "survival": survival,
        })

    # The Phoenix Path: simulate climbing from $5
    print("\n" + "=" * 90)
    print("THE PHOENIX PATH — $5 → $10,000")
    print("=" * 90)

    equity = 5.0
    day = 0
    path_rows = []

    while equity < 10000 and day < 3650:  # max 10 years
        tier = get_tier(equity)
        lot = tier.lot_size
        if tier.min_equity >= 100:
            lot = gatling_lot(equity)

        daily_rate = 0
        for symbol in tier.symbols:
            info = mt5.symbol_info(symbol)
            bars = load_bars(symbol, 60)
            if not bars or info is None:
                continue
            mode = tier.symbol_modes.get(symbol, "raw")
            scfg = SYMBOL_CONFIGS.get(symbol, {})
            if mode == "raw":
                r = simulate_raw(symbol, bars, info, scfg["raw_step"], scfg["raw_cap"], lot)
            else:
                r = simulate_v3(symbol, bars, info, scfg)
                ratio = lot / 0.01
                r["combined"] *= ratio

            daily_rate += r["combined"] / 60

        # Simulate 30 days at this tier
        period_days = 30
        gain = daily_rate * period_days
        equity += gain
        day += period_days

        path_rows.append({"day": day, "equity": equity, "daily_rate": daily_rate, "tier": f"${tier.min_equity}-${tier.max_equity}", "lot": lot})
        print(f"  Day {day:>4}: ${equity:>10.2f}  (${daily_rate:+.2f}/day)  tier=${tier.min_equity}-${tier.max_equity} lot={lot:.3f}")

    print(f"\n  🏆 REACHED ${equity:.2f} on day {day}")

    # Save CSVs
    output1 = ROOT / "reports" / "phoenix_lattice_tiers.csv"
    output2 = ROOT / "reports" / "phoenix_path_5_to_10k.csv"
    for output_path, data in [(output1, rows), (output2, path_rows)]:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        if data:
            with output_path.open("w", newline="", encoding="utf-8") as f:
                writer = csv.DictWriter(f, fieldnames=list(data[0].keys()))
                writer.writeheader()
                writer.writerows(data)
            print(f"\nSaved {output_path}")

    mt5.shutdown()


if __name__ == "__main__":
    raise SystemExit(main())
