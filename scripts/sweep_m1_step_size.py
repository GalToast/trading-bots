#!/usr/bin/env python3
"""M1 step size sweep for BTCUSD — find the optimal step for 1-minute timeframe."""
from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path

import MetaTrader5 as mt5

from penetration_lattice_lab_v2 import Ticket, pip_size_for, spread_price, unit_pnl_usd


ROOT = Path(__file__).resolve().parent.parent
SYMBOL = "BTCUSD"
DAYS = 30  # M1 data may be limited, start with 30 days
TIMEFRAME = mt5.TIMEFRAME_M1


def load_m1_bars(symbol: str, days: int) -> list[dict]:
    rates = mt5.copy_rates_from_pos(symbol, TIMEFRAME, 0, 1440 * days)
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


@dataclass
class M1Variant:
    name: str
    step: float
    max_open: int
    gap: int
    alpha: float
    momentum_gate: bool


# Test wider range since M1 bars are tiny
VARIANTS = [
    M1Variant("step10_mo30", 10.0, 30, 1, 1.0, True),
    M1Variant("step20_mo30", 20.0, 30, 1, 1.0, True),
    M1Variant("step50_mo30", 50.0, 30, 1, 1.0, True),
    M1Variant("step100_mo30", 100.0, 30, 1, 1.0, True),
    M1Variant("step200_mo30", 200.0, 30, 1, 1.0, True),
    M1Variant("step500_mo30", 500.0, 30, 1, 1.0, True),
    # max_open sweep at step=$50 (likely sweet spot)
    M1Variant("step50_mo20", 50.0, 20, 1, 1.0, True),
    M1Variant("step50_mo40", 50.0, 40, 1, 1.0, True),
    M1Variant("step50_mo50", 50.0, 50, 1, 1.0, True),
    M1Variant("step50_mo60", 50.0, 60, 1, 1.0, True),
    # max_open sweep at step=$100
    M1Variant("step100_mo20", 100.0, 20, 1, 1.0, True),
    M1Variant("step100_mo40", 100.0, 40, 1, 1.0, True),
    M1Variant("step100_mo60", 100.0, 60, 1, 1.0, True),
]


def main() -> int:
    mt5.initialize()
    
    info = mt5.symbol_info(SYMBOL)
    bars = load_m1_bars(SYMBOL, DAYS)
    
    if not bars:
        print(f"No M1 bars loaded for {SYMBOL}")
        return 1
    
    print(f"\n{'='*100}")
    print(f"  BTCUSD M1 STEP SIZE SWEEP — {len(bars)} bars, {DAYS} days")
    print(f"{'='*100}")
    
    results = []
    for v in VARIANTS:
        r = simulate_crypto(SYMBOL, bars, info, v.step, v.max_open, v.gap, v.alpha, v.momentum_gate)
        results.append({
            "name": v.name,
            "step": v.step,
            "max_open": v.max_open,
            "combined": r.get("combined", 0),
            "realized": r.get("realized", 0),
            "floating": r.get("floating", 0),
            "closes": r.get("closes", 0),
            "rearm_opens": r.get("rearm_opens", 0),
            "max_seen": r.get("max_seen", 0),
        })
    
    results.sort(key=lambda r: r["combined"], reverse=True)
    
    print(f"\n{'Name':<16} {'Step':>6} {'MO':>4} {'Combined':>12} {'Realized':>12} {'Floating':>12} {'Closes':>7} {'Rearm':>6} {'MaxSeen':>7}")
    print("-" * 100)
    for r in results:
        print(f"{r['name']:<16} ${r['step']:>5.0f} {r['max_open']:>4} ${r['combined']:>11,.2f} ${r['realized']:>11,.2f} ${r['floating']:>11,.2f} {r['closes']:>7} {r['rearm_opens']:>6} {r['max_seen']:>7}")
    
    out_path = ROOT / "reports" / "m1_step_sweep.csv"
    with out_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=results[0].keys())
        writer.writeheader()
        writer.writerows(results)
    
    print(f"\nWrote {out_path}")
    
    best = results[0]
    print(f"\n🏆 Best: {best['name']} — ${best['combined']:,.2f}")
    
    mt5.shutdown()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
