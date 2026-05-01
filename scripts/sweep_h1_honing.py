#!/usr/bin/env python3
"""H1 Micro-Optimization Honing Sweep — squeeze every last drop from BTCUSD H1."""
from __future__ import annotations

import csv
import itertools
from dataclasses import dataclass
from pathlib import Path

import MetaTrader5 as mt5

from penetration_lattice_lab_v2 import Ticket, pip_size_for, spread_price, unit_pnl_usd


ROOT = Path(__file__).resolve().parent.parent
SYMBOL = "BTCUSD"
DAYS = 90
TIMEFRAME = mt5.TIMEFRAME_H1


def load_h1_bars(symbol: str, days: int) -> list[dict]:
    rates = mt5.copy_rates_from_pos(symbol, TIMEFRAME, 0, 24 * days)
    if rates is None or len(rates) == 0:
        return []
    return [
        {"time": int(r[0]), "open": float(r[1]), "high": float(r[2]),
         "low": float(r[3]), "close": float(r[4]), "tick_volume": int(r[5])}
        for r in rates
    ]


@dataclass
class ChurnTicket:
    direction: str
    entry_price: float
    opened_idx: int


def simulate_crypto(sym, bars, info, step, max_open, gap, alpha, momentum_gate):
    if not bars:
        return {}
    spread_px = spread_price(info)
    anchor = bars[0]["close"]
    ns = anchor + step
    nb = anchor - step
    tk = []
    rl = []
    churn = []
    crl = []
    max_seen = 0

    for idx in range(1, len(bars)):
        bar = bars[idx]
        os_ = sum(1 for t in tk if t.direction == "SELL")
        ob = sum(1 for t in tk if t.direction == "BUY")

        while bar["high"] >= ns and os_ < max_open:
            tk.append(Ticket(direction="SELL", entry_price=ns, opened_idx=idx))
            os_ += 1
            ns += step
        while bar["low"] <= nb and ob < max_open:
            tk.append(Ticket(direction="BUY", entry_price=nb, opened_idx=idx))
            ob += 1
            nb -= step

        closed = []
        sl = sorted([t for t in tk if t.direction == "SELL"], key=lambda t: t.entry_price, reverse=True)
        while len(sl) > gap and bar["low"] <= sl[gap].entry_price:
            o = sl[0]
            r = sl[gap].entry_price
            close_px = r + (bar["low"] - r) * alpha
            rl.append(unit_pnl_usd(sym, "SELL", o.entry_price, close_px, spread_px))
            closed.append(("SELL", o.entry_price))
            tk.remove(o)
            sl = sorted([t for t in tk if t.direction == "SELL"], key=lambda t: t.entry_price, reverse=True)
        bl = sorted([t for t in tk if t.direction == "BUY"], key=lambda t: t.entry_price)
        while len(bl) > gap and bar["high"] >= bl[gap].entry_price:
            o = bl[0]
            r = bl[gap].entry_price
            close_px = r + (bar["high"] - r) * alpha
            rl.append(unit_pnl_usd(sym, "BUY", o.entry_price, close_px, spread_px))
            closed.append(("BUY", o.entry_price))
            tk.remove(o)
            bl = sorted([t for t in tk if t.direction == "BUY"], key=lambda t: t.entry_price)

        if not tk and abs(bar["close"] - anchor) >= step:
            anchor = bar["close"]
            ns = anchor + step
            nb = anchor - step

        cos_ = sum(1 for t in churn if t.direction == "SELL")
        cob = sum(1 for t in churn if t.direction == "BUY")
        for d, cp in closed:
            c = cos_ if d == "SELL" else cob
            if c >= max_open:
                continue
            if momentum_gate:
                if d == "SELL" and bar["close"] >= cp:
                    continue
                if d == "BUY" and bar["close"] <= cp:
                    continue
            churn.append(ChurnTicket(d, cp, idx))
            if d == "SELL":
                cos_ += 1
            else:
                cob += 1

        cs = sorted([t for t in churn if t.direction == "SELL"], key=lambda t: t.entry_price, reverse=True)
        while len(cs) > gap and bar["low"] <= cs[gap].entry_price:
            o = cs[0]
            r = cs[gap].entry_price
            close_px = r + (bar["low"] - r) * alpha
            crl.append(unit_pnl_usd(sym, "SELL", o.entry_price, close_px, spread_px))
            churn.remove(o)
            cs = sorted([t for t in churn if t.direction == "SELL"], key=lambda t: t.entry_price, reverse=True)
        cb = sorted([t for t in churn if t.direction == "BUY"], key=lambda t: t.entry_price)
        while len(cb) > gap and bar["high"] >= cb[gap].entry_price:
            o = cb[0]
            r = cb[gap].entry_price
            close_px = r + (bar["high"] - r) * alpha
            crl.append(unit_pnl_usd(sym, "BUY", o.entry_price, close_px, spread_px))
            churn.remove(o)
            cb = sorted([t for t in churn if t.direction == "BUY"], key=lambda t: t.entry_price)

        max_seen = max(max_seen, len(tk) + len(churn))

    fl = [unit_pnl_usd(sym, t.direction, t.entry_price, bars[-1]["close"], spread_px) for t in tk]
    cfl = [unit_pnl_usd(sym, t.direction, t.entry_price, bars[-1]["close"], spread_px) for t in churn]

    realized = sum(rl) + sum(crl)
    floating = sum(fl) + sum(cfl)
    combined = realized + floating

    return {
        "combined": combined,
        "realized": realized,
        "floating": floating,
        "closes": len(rl) + len(crl),
        "rearm_opens": len(crl),
        "max_seen": max_seen,
    }


def main() -> int:
    mt5.initialize()
    
    info = mt5.symbol_info(SYMBOL)
    bars = load_h1_bars(SYMBOL, DAYS)
    
    if not bars:
        print(f"No H1 bars loaded for {SYMBOL}")
        return 1
    
    print(f"\n{'='*110}")
    print(f"  H1 MICRO-OPTIMIZATION HONING SWEEP — {SYMBOL} {DAYS}d, {len(bars)} bars")
    print(f"{'='*110}")
    
    # H1 honing: step=$50 baseline, sweep gap/alpha/momentum/max_open
    steps = [25, 35, 50, 75, 100]
    max_opens = [40, 50, 60, 70, 80, 100]
    gaps = [1, 2]
    alphas = [0.75, 1.00]
    momentum_gates = [True, False]
    
    # Focused sweep around $50 step sweet spot
    primary_sweep = []
    
    # Step=$50 full sweep
    for gap, alpha, mom, mo in itertools.product(gaps, alphas, momentum_gates, max_opens):
        primary_sweep.append({
            "step": 50.0,
            "max_open": mo,
            "gap": gap,
            "alpha": alpha,
            "momentum_gate": mom,
        })
    
    # Step size sweep at optimal params
    for step in steps:
        primary_sweep.append({
            "step": step,
            "max_open": 60,
            "gap": 1,
            "alpha": 1.0,
            "momentum_gate": False,
        })
    
    results = []
    total = len(primary_sweep)
    
    for i, p in enumerate(primary_sweep):
        r = simulate_crypto(SYMBOL, bars, info, p["step"], p["max_open"], p["gap"], p["alpha"], p["momentum_gate"])
        results.append({
            "step": p["step"],
            "max_open": p["max_open"],
            "gap": p["gap"],
            "alpha": p["alpha"],
            "momentum": p["momentum_gate"],
            "combined": r.get("combined", 0),
            "realized": r.get("realized", 0),
            "floating": r.get("floating", 0),
            "closes": r.get("closes", 0),
            "rearm_opens": r.get("rearm_opens", 0),
            "max_seen": r.get("max_seen", 0),
        })
        if (i + 1) % 20 == 0:
            print(f"  Progress: {i+1}/{total} variants tested...")
    
    results.sort(key=lambda r: r["combined"], reverse=True)
    
    print(f"\n{'Step':>6} {'MO':>4} {'Gap':>4} {'α':>5} {'Mom':>4} {'Combined':>12} {'Realized':>12} {'Floating':>12} {'Closes':>7} {'Rearm':>6} {'MaxSeen':>7}")
    print("-" * 110)
    for r in results[:30]:
        mom_str = "ON" if r["momentum"] else "OFF"
        print(f"${r['step']:>5.0f} {r['max_open']:>4} {r['gap']:>4} {r['alpha']:>5.2f} {mom_str:>4} ${r['combined']:>11,.2f} ${r['realized']:>11,.2f} ${r['floating']:>11,.2f} {r['closes']:>7} {r['rearm_opens']:>6} {r['max_seen']:>7}")
    
    out_path = ROOT / "reports" / "h1_honing_sweep.csv"
    with out_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=results[0].keys())
        writer.writeheader()
        writer.writerows(results)
    
    print(f"\nWrote {out_path}")
    
    best = results[0]
    mom_str = "ON" if best["momentum"] else "OFF"
    print(f"\n🏆 Best: step=${best['step']:.0f}, MO={best['max_open']}, gap={best['gap']}, α={best['alpha']:.2f}, mom={mom_str}")
    print(f"   Combined: ${best['combined']:,.2f}")
    
    mt5.shutdown()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
