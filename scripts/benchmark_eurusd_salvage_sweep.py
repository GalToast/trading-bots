#!/usr/bin/env python3
"""
EURUSD Salvage Sweep — Squeeze the Neighborhood
==================================================
Before declaring EURUSD dead (forward-shadow failed), we try:
1. Different step spacings around the validated 1.0-3.0 range
2. All close policy variants (allprof, outer, inner × gap 1-2 × alpha 0-1)
3. Asymmetric side geometry (buy vs sell spacing)

Output: reports/eurusd_salvage_sweep.md — ranked by combined net PnL.
"""
from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path

from penetration_lattice_hybrid_apex import RawConfig
from penetration_lattice_lab_v2 import load_bars, pip_size_for, spread_price

ROOT = Path(__file__).resolve().parent.parent
OUTPUT_CSV = ROOT / "reports" / "eurusd_salvage_sweep.csv"
OUTPUT_MD = ROOT / "reports" / "eurusd_salvage_sweep.md"

SYMBOL = "EURUSD"
DAYS = 60

STEP_COMBOS = [
    (1.0, 1.0),
    (1.5, 1.5),
    (2.0, 2.0),
    (3.0, 3.0),
    (0.5, 0.5),
    (1.0, 1.5),
    (1.5, 1.0),
    (0.5, 1.0),
    (1.0, 0.5),
]

CLOSE_POLICIES = [
    ("all_profitable", 1, 0.5),
    ("all_profitable", 1, 1.0),
    ("all_profitable", 2, 0.5),
    ("outer", 1, 0.5),
    ("outer", 2, 0.5),
    ("outer", 2, 1.0),
    ("inner", 1, 0.5),
    ("inner", 2, 0.5),
]


def run_sweep():
    import MetaTrader5 as mt5
    if not mt5.initialize():
        print(f"MT5 init failed")
        return

    bars = load_bars(SYMBOL, days=DAYS)
    print(f"Loaded {len(bars)} bars for {SYMBOL}")
    if not bars:
        return

    import MetaTrader5 as mt5
    mt5.initialize()
    symbol_info = mt5.symbol_info(SYMBOL)
    pip_size = pip_size_for(symbol_info)
    spread_px = spread_price(symbol_info)
    print(f"pip_size={pip_size}, spread={spread_px}")

    results = []
    total = len(STEP_COMBOS) * len(CLOSE_POLICIES)
    print(f"Testing {total} configs...")

    for idx, (step_buy, step_sell) in enumerate(STEP_COMBOS):
        for close_style, close_gap, close_alpha in CLOSE_POLICIES:
            cfg = RawConfig(
                step_pips=step_buy,
                max_open_per_side=50,
                close_mode="raw",
            )
            pnl = simulate_close_policy(SYMBOL, bars, symbol_info, cfg, close_style, close_gap, close_alpha, step_buy, step_sell)
            results.append({
                "step_buy": step_buy,
                "step_sell": step_sell,
                "close_style": close_style,
                "close_gap": close_gap,
                "close_alpha": close_alpha,
                "combined_net": pnl.get("combined_net_usd", 0),
                "realized_net": pnl.get("realized_net_usd", 0),
                "floating_net": pnl.get("floating_net_usd", 0),
                "closes": pnl.get("realized_closes", 0),
                "win_rate": pnl.get("win_pct", 0),
            })
            if (idx * len(CLOSE_POLICIES) + len(CLOSE_POLICIES)) % 20 == 0:
                print(f"  ... {len(results)}/{total}")

    results.sort(key=lambda x: x["combined_net"], reverse=True)

    with open(OUTPUT_CSV, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=results[0].keys())
        writer.writeheader()
        writer.writerows(results)

    write_md(results)

    print(f"\nTop 5:")
    for r in results[:5]:
        print(f"  step={r['step_buy']}/{r['step_sell']}, {r['close_style']}_gap{r['close_gap']}_a{r['close_alpha']}: ${r['combined_net']:+.2f} ({r['closes']}c)")


def simulate_close_policy(symbol, bars, symbol_info, cfg, close_style, close_gap, close_alpha, step_buy_pips, step_sell_pips):
    from benchmark_fx_fixed_step_close_policy import (
        ClosePolicy,
        select_close_positions,
        _interp_close,
        Ticket,
        dynamic_step,
        unit_pnl_usd,
        spread_price,
    )

    pip_size = pip_size_for(symbol_info)
    spread_px = spread_price(symbol_info)
    base_step_sell_px = step_sell_pips * pip_size
    base_step_buy_px = step_buy_pips * pip_size

    anchor = bars[0]["close"]
    next_sell_level = anchor + base_step_sell_px
    next_buy_level = anchor - base_step_buy_px

    open_tickets = []
    realized_pnls = []
    close_events = 0
    tickets_closed = 0

    policy = ClosePolicy(name="test", close_gap=close_gap, close_alpha=close_alpha, close_style=close_style)

    adapt_cfg = type("Cfg", (), {
        "adaptive_step_threshold_1": 10,
        "adaptive_step_threshold_2": 20,
        "adaptive_step_multiplier_1": 1.5,
        "adaptive_step_multiplier_2": 2.0,
    })()

    for idx in range(1, len(bars)):
        bar = bars[idx]
        open_buy = sum(1 for t in open_tickets if t.direction == "BUY")
        open_sell = sum(1 for t in open_tickets if t.direction == "SELL")

        current_sell_step = dynamic_step(base_step_sell_px, open_sell, adapt_cfg)
        current_buy_step = dynamic_step(base_step_buy_px, open_buy, adapt_cfg)

        while bar["high"] >= next_sell_level and open_sell < cfg.max_open_per_side:
            open_tickets.append(Ticket(direction="SELL", entry_price=next_sell_level, opened_idx=idx))
            open_sell += 1
            current_sell_step = dynamic_step(base_step_sell_px, open_sell, adapt_cfg)
            next_sell_level += current_sell_step

        while bar["low"] <= next_buy_level and open_buy < cfg.max_open_per_side:
            open_tickets.append(Ticket(direction="BUY", entry_price=next_buy_level, opened_idx=idx))
            open_buy += 1
            current_buy_step = dynamic_step(base_step_buy_px, open_buy, adapt_cfg)
            next_buy_level -= current_buy_step

        # Close sells
        sells = sorted((t for t in open_tickets if t.direction == "SELL"), key=lambda t: t.entry_price, reverse=True)
        while len(sells) > policy.close_gap and bar["low"] <= sells[policy.close_gap].entry_price:
            level_price = sells[policy.close_gap].entry_price
            close_ref = _interp_close(level_price, bar["low"], "SELL", policy.close_alpha)
            profitable_positions = [
                pos for pos, ticket in enumerate(sells)
                if unit_pnl_usd(symbol, "SELL", ticket.entry_price, close_ref, spread_px) > 0
            ]
            close_positions = select_close_positions(len(sells), policy.close_gap, policy.close_style, profitable_positions)
            if not close_positions:
                break
            close_indices = sorted(set(close_positions), reverse=True)
            closed_any = False
            for pos in close_indices:
                ticket = sells[pos]
                pnl = unit_pnl_usd(symbol, "SELL", ticket.entry_price, close_ref, spread_px)
                if pnl <= 0:
                    continue
                realized_pnls.append(pnl)
                open_tickets.remove(ticket)
                tickets_closed += 1
                closed_any = True
            if not closed_any:
                break
            close_events += 1
            sells = sorted((t for t in open_tickets if t.direction == "SELL"), key=lambda t: t.entry_price, reverse=True)

        # Close buys
        buys = sorted((t for t in open_tickets if t.direction == "BUY"), key=lambda t: t.entry_price)
        while len(buys) > policy.close_gap and bar["high"] >= buys[policy.close_gap].entry_price:
            level_price = buys[policy.close_gap].entry_price
            close_ref = _interp_close(level_price, bar["high"], "BUY", policy.close_alpha)
            profitable_positions = [
                pos for pos, ticket in enumerate(buys)
                if unit_pnl_usd(symbol, "BUY", ticket.entry_price, close_ref, spread_px) > 0
            ]
            close_positions = select_close_positions(len(buys), policy.close_gap, policy.close_style, profitable_positions)
            if not close_positions:
                break
            close_indices = sorted(set(close_positions), reverse=True)
            closed_any = False
            for pos in close_indices:
                ticket = buys[pos]
                pnl = unit_pnl_usd(symbol, "BUY", ticket.entry_price, close_ref, spread_px)
                if pnl <= 0:
                    continue
                realized_pnls.append(pnl)
                open_tickets.remove(ticket)
                tickets_closed += 1
                closed_any = True
            if not closed_any:
                break
            close_events += 1
            buys = sorted((t for t in open_tickets if t.direction == "BUY"), key=lambda t: t.entry_price)

        if not open_tickets and (
            bar["close"] >= anchor + base_step_sell_px
            or bar["close"] <= anchor - base_step_buy_px
        ):
            anchor = bar["close"]
            next_sell_level = anchor + base_step_sell_px
            next_buy_level = anchor - base_step_buy_px

    last_close = bars[-1]["close"]
    floating_pnls = [
        unit_pnl_usd(symbol, t.direction, t.entry_price, last_close, spread_px)
        for t in open_tickets
    ]

    realized_net = sum(realized_pnls)
    floating_net = sum(floating_pnls)
    combined_net = realized_net + floating_net
    wins = sum(1 for p in realized_pnls if p > 0)
    win_pct = (wins / len(realized_pnls) * 100) if realized_pnls else 0

    return {
        "combined_net_usd": round(combined_net, 2),
        "realized_net_usd": round(realized_net, 2),
        "floating_net_usd": round(floating_net, 2),
        "realized_closes": len(realized_pnls),
        "win_pct": round(win_pct, 1),
    }


def write_md(results):
    with open(OUTPUT_MD, "w") as f:
        f.write("# EURUSD Salvage Sweep\n\n")
        f.write(f"Symbol: {SYMBOL}, Days: {DAYS}, Total configs: {len(results)}\n\n")
        f.write("## Top 15 Configs\n\n")
        f.write("| Rank | Step B | Step S | Close | Gap | Alpha | Combined | Realized | Floating | Closes | WR |\n")
        f.write("|------|--------|--------|-------|-----|-------|----------|----------|----------|--------|----|\n")
        for i, r in enumerate(results[:15]):
            f.write(f"| {i+1} | {r['step_buy']} | {r['step_sell']} | {r['close_style']} | {r['close_gap']} | {r['close_alpha']} | ${r['combined_net']:+.2f} | ${r['realized_net']:+.2f} | ${r['floating_net']:+.2f} | {r['closes']} | {r['win_rate']:.0f}% |\n")

        f.write("\n## Verdict\n\n")
        top = results[0]
        if top["combined_net"] > 0:
            f.write(f"**SURVIVOR:** {top['step_buy']}/{top['step_sell']} {top['close_style']}_gap{top['close_gap']}_a{top['close_alpha']} = ${top['combined_net']:+.2f} ({top['closes']}c)\n")
        else:
            f.write(f"**NO SURVIVOR:** Best = ${top['combined_net']:+.2f}\n")


if __name__ == "__main__":
    run_sweep()
