#!/usr/bin/env python3
"""
FX Salvage Sweep — Alpha=1.0 Nuclear Option
==================================================
Run the alpha=1.0 + all_profitable gap=1 config across ALL problematic FX symbols
to see which ones come back from the dead.
"""
from __future__ import annotations

import csv
import json
from pathlib import Path

from penetration_lattice_hybrid_apex import RawConfig
from penetration_lattice_lab_v2 import load_bars, pip_size_for, spread_price

ROOT = Path(__file__).resolve().parent.parent
OUTPUT_MD = ROOT / "reports" / "fx_alpha1_resurrection_sweep.md"

SYMBOLS = ["EURUSD", "GBPUSD", "NZDUSD", "USDJPY"]
DAYS = 60

# The winning config from EURUSD salvage
ALPHA1_CONFIGS = [
    ("all_profitable", 1, 1.0),
    ("all_profitable", 1, 0.5),
    ("all_profitable", 2, 1.0),
    ("outer", 2, 1.0),
    ("outer", 2, 0.5),
    ("outer", 1, 1.0),
    ("outer", 1, 0.5),
]

STEP_SIZES = [0.5, 1.0, 1.5, 2.0, 3.0]


def run_sweep():
    import MetaTrader5 as mt5
    if not mt5.initialize():
        print("MT5 init failed")
        return

    results = []

    for symbol in SYMBOLS:
        print(f"\n{'='*60}")
        print(f"Testing {symbol}...")
        print(f"{'='*60}")

        bars = load_bars(symbol, days=DAYS)
        if not bars:
            print(f"  No bars for {symbol}")
            continue

        info = mt5.symbol_info(symbol)
        pip_size = pip_size_for(info)
        spread_px = spread_price(info)
        print(f"  pip={pip_size}, spread={spread_px}, bars={len(bars)}")

        for step_pips in STEP_SIZES:
            for close_style, close_gap, close_alpha in ALPHA1_CONFIGS:
                cfg = RawConfig(
                    step_pips=step_pips,
                    max_open_per_side=50,
                    close_mode="raw",
                )
                pnl = simulate_config(symbol, bars, info, cfg, close_style, close_gap, close_alpha)
                results.append({
                    "symbol": symbol,
                    "step_pips": step_pips,
                    "close_style": close_style,
                    "close_gap": close_gap,
                    "close_alpha": close_alpha,
                    "combined_net": pnl.get("combined_net_usd", 0),
                    "realized_net": pnl.get("realized_net_usd", 0),
                    "floating_net": pnl.get("floating_net_usd", 0),
                    "closes": pnl.get("realized_closes", 0),
                    "win_pct": pnl.get("win_pct", 0),
                })
            print(f"  Step {step_pips}: done")

    # Sort by combined net
    results.sort(key=lambda x: x["combined_net"], reverse=True)

    write_md(results)
    print(f"\n{'='*60}")
    print("TOP 20 SURVIVORS:")
    print(f"{'='*60}")
    for i, r in enumerate(results[:20]):
        status = "✅ ALIVE" if r["combined_net"] > 0 else "❌ DEAD"
        print(f"  {i+1:2d}. {r['symbol']:8s} step={r['step_pips']:.1f} {r['close_style']}_g{r['close_gap']}_a{r['close_alpha']} = ${r['combined_net']:+10.2f} ({r['closes']}c) {status}")

    mt5.shutdown()


def simulate_config(symbol, bars, symbol_info, cfg, close_style, close_gap, close_alpha):
    from benchmark_fx_fixed_step_close_policy import (
        ClosePolicy,
        select_close_positions,
        _interp_close,
        Ticket,
        dynamic_step,
        unit_pnl_usd,
    )

    pip_size = pip_size_for(symbol_info)
    spread_px = spread_price(symbol_info)
    base_step_px = cfg.step_pips * pip_size

    anchor = bars[0]["close"]
    next_sell = anchor + base_step_px
    next_buy = anchor - base_step_px
    open_tickets = []
    realized = []

    policy = ClosePolicy(name="x", close_gap=close_gap, close_alpha=close_alpha, close_style=close_style)
    adapt = type("A", (), {"adaptive_step_threshold_1": 10, "adaptive_step_threshold_2": 20,
                           "adaptive_step_multiplier_1": 1.5, "adaptive_step_multiplier_2": 2.0})()

    for idx in range(1, len(bars)):
        bar = bars[idx]
        ob = sum(1 for t in open_tickets if t.direction == "BUY")
        os_ = sum(1 for t in open_tickets if t.direction == "SELL")
        cs = dynamic_step(base_step_px, os_, adapt)
        cb = dynamic_step(base_step_px, ob, adapt)

        while bar["high"] >= next_sell and os_ < cfg.max_open_per_side:
            open_tickets.append(Ticket(direction="SELL", entry_price=next_sell, opened_idx=idx))
            os_ += 1
            cs = dynamic_step(base_step_px, os_, adapt)
            next_sell += cs

        while bar["low"] <= next_buy and ob < cfg.max_open_per_side:
            open_tickets.append(Ticket(direction="BUY", entry_price=next_buy, opened_idx=idx))
            ob += 1
            cb = dynamic_step(base_step_px, ob, adapt)
            next_buy -= cb

        # Close sells
        sells = sorted((t for t in open_tickets if t.direction == "SELL"), key=lambda t: t.entry_price, reverse=True)
        while len(sells) > policy.close_gap and bar["low"] <= sells[policy.close_gap].entry_price:
            lp = sells[policy.close_gap].entry_price
            cr = _interp_close(lp, bar["low"], "SELL", policy.close_alpha)
            pp = [p for p, t in enumerate(sells) if unit_pnl_usd(symbol, "SELL", t.entry_price, cr, spread_px) > 0]
            cp = select_close_positions(len(sells), policy.close_gap, policy.close_style, pp)
            if not cp:
                break
            ci = sorted(set(cp), reverse=True)
            ok = False
            for p in ci:
                pnl = unit_pnl_usd(symbol, "SELL", sells[p].entry_price, cr, spread_px)
                if pnl <= 0:
                    continue
                realized.append(pnl)
                open_tickets.remove(sells[p])
                ok = True
            if not ok:
                break
            sells = sorted((t for t in open_tickets if t.direction == "SELL"), key=lambda t: t.entry_price, reverse=True)

        # Close buys
        buys = sorted((t for t in open_tickets if t.direction == "BUY"), key=lambda t: t.entry_price)
        while len(buys) > policy.close_gap and bar["high"] >= buys[policy.close_gap].entry_price:
            lp = buys[policy.close_gap].entry_price
            cr = _interp_close(lp, bar["high"], "BUY", policy.close_alpha)
            pp = [p for p, t in enumerate(buys) if unit_pnl_usd(symbol, "BUY", t.entry_price, cr, spread_px) > 0]
            cp = select_close_positions(len(buys), policy.close_gap, policy.close_style, pp)
            if not cp:
                break
            ci = sorted(set(cp), reverse=True)
            ok = False
            for p in ci:
                pnl = unit_pnl_usd(symbol, "BUY", buys[p].entry_price, cr, spread_px)
                if pnl <= 0:
                    continue
                realized.append(pnl)
                open_tickets.remove(buys[p])
                ok = True
            if not ok:
                break
            buys = sorted((t for t in open_tickets if t.direction == "BUY"), key=lambda t: t.entry_price)

        if not open_tickets and abs(bar["close"] - anchor) >= base_step_px:
            anchor = bar["close"]
            next_sell = anchor + base_step_px
            next_buy = anchor - base_step_px

    lc = bars[-1]["close"]
    fp = [unit_pnl_usd(symbol, t.direction, t.entry_price, lc, spread_px) for t in open_tickets]
    rn = sum(realized)
    fn = sum(fp)
    wp = sum(1 for p in realized if p > 0) / len(realized) * 100 if realized else 0
    return {"combined_net_usd": round(rn + fn, 2), "realized_net_usd": round(rn, 2),
            "floating_net_usd": round(fn, 2), "realized_closes": len(realized), "win_pct": round(wp, 1)}


def write_md(results):
    by_sym = {}
    for r in results:
        by_sym.setdefault(r["symbol"], []).append(r)

    with open(OUTPUT_MD, "w") as f:
        f.write("# FX Alpha=1.0 Resurrection Sweep\n\n")
        f.write(f"Symbols: {', '.join(SYMBOLS)}, Days: {DAYS}, Total configs per symbol: {len(STEP_SIZES)*len(ALPHA1_CONFIGS)}\n\n")

        for symbol in SYMBOLS:
            sym_results = sorted(by_sym.get(symbol, []), key=lambda x: x["combined_net"], reverse=True)
            f.write(f"## {symbol}\n\n")
            if not sym_results:
                f.write("No data.\n\n")
                continue
            top = sym_results[0]
            status = "✅ **ALIVE**" if top["combined_net"] > 0 else "❌ **DEAD**"
            f.write(f"Best: {status} — step={top['step_pips']}, {top['close_style']}_g{top['close_gap']}_a{top['close_alpha']} = **${top['combined_net']:+,.2f}** ({top['closes']} closes)\n\n")

            f.write("| Step | Close | Gap | Alpha | Combined | Realized | Floating | Closes | WR |\n")
            f.write("|------|-------|-----|-------|----------|----------|----------|--------|----|\n")
            for r in sym_results[:10]:
                s = "✅" if r["combined_net"] > 0 else "❌"
                f.write(f"| {r['step_pips']} | {r['close_style']} | {r['close_gap']} | {r['close_alpha']} | ${r['combined_net']:+,.2f} | ${r['realized_net']:+,.2f} | ${r['floating_net']:+,.2f} | {r['closes']} | {r['win_pct']:.0f}% {s} |\n")
            f.write("\n")

        f.write("\n## Overall Survivors (Combined Net > 0)\n\n")
        survivors = [r for r in results if r["combined_net"] > 0]
        survivors.sort(key=lambda x: x["combined_net"], reverse=True)
        f.write("| Rank | Symbol | Step | Close | Gap | Alpha | Combined | Closes | WR |\n")
        f.write("|------|--------|------|-------|-----|-------|----------|--------|----|\n")
        for i, r in enumerate(survivors[:25]):
            f.write(f"| {i+1} | {r['symbol']} | {r['step_pips']} | {r['close_style']} | {r['close_gap']} | {r['close_alpha']} | ${r['combined_net']:+,.2f} | {r['closes']} | {r['win_pct']:.0f}% |\n")


if __name__ == "__main__":
    run_sweep()
