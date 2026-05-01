#!/usr/bin/env python3
"""ETHUSD Multi-Timeframe Stacking Test — M15+M5+H1 on ETHUSD."""
from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path

import MetaTrader5 as mt5

from penetration_lattice_lab_v2 import Ticket, pip_size_for, spread_price, unit_pnl_usd


ROOT = Path(__file__).resolve().parent.parent
SYMBOL = "ETHUSD"
DAYS = 90


@dataclass
class ChurnTicket:
    direction: str
    entry_price: float
    opened_idx: int


def load_bars(timeframe: int) -> list[dict]:
    bars_per_day = 96 if timeframe == mt5.TIMEFRAME_M15 else 288 if timeframe == mt5.TIMEFRAME_M5 else 24
    rates = mt5.copy_rates_from_pos(SYMBOL, timeframe, 0, bars_per_day * DAYS)
    if rates is None or len(rates) == 0:
        return []
    return [
        {"time": int(r[0]), "open": float(r[1]), "high": float(r[2]),
         "low": float(r[3]), "close": float(r[4]), "tick_volume": int(r[5])}
        for r in rates
    ]


def run_engine(bars, info, step, max_open, gap, alpha, momentum_gate):
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
            rl.append(unit_pnl_usd(SYMBOL, "SELL", o.entry_price, close_px, spread_px))
            closed.append(("SELL", o.entry_price))
            tk.remove(o)
            sl = sorted([t for t in tk if t.direction == "SELL"], key=lambda t: t.entry_price, reverse=True)
        bl = sorted([t for t in tk if t.direction == "BUY"], key=lambda t: t.entry_price)
        while len(bl) > gap and bar["high"] >= bl[gap].entry_price:
            o = bl[0]
            r = bl[gap].entry_price
            close_px = r + (bar["high"] - r) * alpha
            rl.append(unit_pnl_usd(SYMBOL, "BUY", o.entry_price, close_px, spread_px))
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
            crl.append(unit_pnl_usd(SYMBOL, "SELL", o.entry_price, close_px, spread_px))
            churn.remove(o)
            cs = sorted([t for t in churn if t.direction == "SELL"], key=lambda t: t.entry_price, reverse=True)
        cb = sorted([t for t in churn if t.direction == "BUY"], key=lambda t: t.entry_price)
        while len(cb) > gap and bar["high"] >= cb[gap].entry_price:
            o = cb[0]
            r = cb[gap].entry_price
            close_px = r + (bar["high"] - r) * alpha
            crl.append(unit_pnl_usd(SYMBOL, "BUY", o.entry_price, close_px, spread_px))
            churn.remove(o)
            cb = sorted([t for t in churn if t.direction == "BUY"], key=lambda t: t.entry_price)

        max_seen = max(max_seen, len(tk) + len(churn))

    fl = [unit_pnl_usd(SYMBOL, t.direction, t.entry_price, bars[-1]["close"], spread_px) for t in tk]
    cfl = [unit_pnl_usd(SYMBOL, t.direction, t.entry_price, bars[-1]["close"], spread_px) for t in churn]

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
    
    m15_bars = load_bars(mt5.TIMEFRAME_M15)
    m5_bars = load_bars(mt5.TIMEFRAME_M5)
    h1_bars = load_bars(mt5.TIMEFRAME_H1)
    
    print(f"\n{'='*100}")
    print(f"  ETHUSD MULTI-TIMEFRAME STACKING TEST — {SYMBOL} {DAYS}d")
    print(f"  M15: {len(m15_bars)} bars | M5: {len(m5_bars)} bars | H1: {len(h1_bars)} bars")
    print(f"{'='*100}")
    
    # M15 sweep: steps from $5 to $100, mom ON/OFF, MO=40/60/80
    print(f"\n{'─'*60}")
    print(f"  ETHUSD M15 Step Sweep")
    print(f"{'─'*60}")
    
    m15_results = []
    for step in [2, 3, 5, 7.5, 10, 15, 20, 25, 30, 40, 50]:
        for mom in [False, True]:
            r = run_engine(m15_bars, info, step, 60, 1, 1.0, mom)
            m15_results.append({"step": step, "mom": mom, **r})
    
    m15_results.sort(key=lambda x: x["combined"], reverse=True)
    print(f"  {'Step':>6} {'Mom':>4} {'Combined':>12} {'Realized':>12} {'Floating':>12} {'Closes':>7} {'MaxSeen':>7}")
    print("-" * 60)
    for r in m15_results[:10]:
        mom_str = "ON" if r["mom"] else "OFF"
        print(f" ${r['step']:>5.2f} {mom_str:>4} ${r['combined']:>11,.2f} ${r['realized']:>11,.2f} ${r['floating']:>11,.2f} {r['closes']:>7} {r['max_seen']:>7}")
    
    best_m15 = m15_results[0]
    
    # M5 sweep: steps from $1 to $20, mom OFF
    print(f"\n{'─'*60}")
    print(f"  ETHUSD M5 Step Sweep")
    print(f"{'─'*60}")
    
    m5_results = []
    for step in [1, 2, 3, 5, 7.5, 10, 15, 20]:
        r = run_engine(m5_bars, info, step, 60, 1, 1.0, False)
        m5_results.append({"step": step, **r})
    
    m5_results.sort(key=lambda x: x["combined"], reverse=True)
    print(f"  {'Step':>6} {'Combined':>12} {'Realized':>12} {'Floating':>12} {'Closes':>7}")
    print("-" * 60)
    for r in m5_results[:5]:
        print(f" ${r['step']:>5.2f} ${r['combined']:>11,.2f} ${r['realized']:>11,.2f} ${r['floating']:>11,.2f} {r['closes']:>7}")
    
    best_m5 = m5_results[0]
    
    # H1 sweep: steps from $5 to $50, mom OFF
    print(f"\n{'─'*60}")
    print(f"  ETHUSD H1 Step Sweep")
    print(f"{'─'*60}")
    
    h1_results = []
    for step in [5, 10, 15, 20, 25, 30, 50]:
        r = run_engine(h1_bars, info, step, 60, 1, 1.0, False)
        h1_results.append({"step": step, **r})
    
    h1_results.sort(key=lambda x: x["combined"], reverse=True)
    print(f"  {'Step':>6} {'Combined':>12} {'Realized':>12} {'Floating':>12} {'Closes':>7}")
    print("-" * 60)
    for r in h1_results[:5]:
        print(f" ${r['step']:>5.2f} ${r['combined']:>11,.2f} ${r['realized']:>11,.2f} ${r['floating']:>11,.2f} {r['closes']:>7}")
    
    best_h1 = h1_results[0]
    
    # Total
    m15_c = best_m15.get("combined", 0)
    m5_c = best_m5.get("combined", 0)
    h1_c = best_h1.get("combined", 0)
    total = m15_c + m5_c + h1_c
    
    mom_str = "ON" if best_m15["mom"] else "OFF"
    
    print(f"\n{'='*100}")
    print(f"  ETHUSD MULTI-TIMEFRAME SUMMARY")
    print(f"{'='*100}")
    print(f"  M15: step=${best_m15['step']:.2f}, mom={mom_str} → ${m15_c:,.2f}")
    print(f"  M5:  step=${best_m5['step']:.2f}, mom=OFF → ${m5_c:,.2f}")
    print(f"  H1:  step=${best_h1['step']:.2f}, mom=OFF → ${h1_c:,.2f}")
    print(f"  TOTAL: ${total:,.2f} = ${total/90:,.2f}/day = ${total/90*365:,.2f}/year")
    
    # Save to CSV
    results = [
        {"tf": "M15", "step": best_m15['step'], "momentum": mom_str, **{k: v for k, v in best_m15.items() if k not in ['step', 'mom']}},
        {"tf": "M5", "step": best_m5['step'], "momentum": "OFF", **{k: v for k, v in best_m5.items() if k not in ['step']}},
        {"tf": "H1", "step": best_h1['step'], "momentum": "OFF", **{k: v for k, v in best_h1.items() if k not in ['step']}},
        {"tf": "TOTAL", "step": 0, "momentum": "—", "combined": total, "realized": best_m15.get('realized',0)+best_m5.get('realized',0)+best_h1.get('realized',0), "floating": best_m15.get('floating',0)+best_m5.get('floating',0)+best_h1.get('floating',0), "closes": best_m15.get('closes',0)+best_m5.get('closes',0)+best_h1.get('closes',0), "rearm_opens": best_m15.get('rearm_opens',0)+best_m5.get('rearm_opens',0)+best_h1.get('rearm_opens',0), "max_seen": max(best_m15.get('max_seen',0), best_m5.get('max_seen',0), best_h1.get('max_seen',0))},
    ]
    
    out_path = ROOT / "reports" / "ethusd_multi_tf_stacking.csv"
    with out_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["tf", "step", "momentum", "combined", "realized", "floating", "closes", "rearm_opens", "max_seen"])
        writer.writeheader()
        writer.writerows(results)
    
    print(f"\nWrote {out_path}")
    
    mt5.shutdown()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
