#!/usr/bin/env python3
"""Test: does max_open=40 explain the $229K vs $115K discrepancy?"""
from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path

import MetaTrader5 as mt5

from penetration_lattice_lab_v2 import Ticket, dynamic_step, pip_size_for, spread_price, unit_pnl_usd

ROOT = Path(__file__).resolve().parent.parent
CRYPTO = ["BTCUSD", "ETHUSD", "SOLUSD", "XRPUSD"]


@dataclass
class ChurnTicket:
    __slots__ = ("direction", "entry_price", "opened_idx")
    def __init__(self, d, e, o):
        self.direction = d; self.entry_price = e; self.opened_idx = o


def load_h1_bars(symbol, days):
    rates = mt5.copy_rates_from_pos(symbol, mt5.TIMEFRAME_H1, 0, 24 * days)
    if rates is None or len(rates) == 0:
        return []
    return [{"time": int(r[0]), "open": float(r[1]), "high": float(r[2]),
             "low": float(r[3]), "close": float(r[4]), "tick_volume": int(r[5])} for r in rates]


def sim(sym, bars, info, step, mop, gap, alpha, mom):
    if not bars:
        return {}
    spread_px = spread_price(info)
    anchor = bars[0]["close"]
    ns = anchor + step; nb = anchor - step
    tk = []; rl = []; churn = []; crl = []

    for idx in range(1, len(bars)):
        bar = bars[idx]
        os_ = sum(1 for t in tk if t.direction == "SELL")
        ob = sum(1 for t in tk if t.direction == "BUY")

        while bar["high"] >= ns and os_ < mop:
            tk.append(Ticket(direction="SELL", entry_price=ns, opened_idx=idx))
            os_ += 1; ns += step
        while bar["low"] <= nb and ob < mop:
            tk.append(Ticket(direction="BUY", entry_price=nb, opened_idx=idx))
            ob += 1; nb -= step

        closed = []
        sl = sorted([t for t in tk if t.direction=="SELL"], key=lambda t:t.entry_price, reverse=True)
        while len(sl) > gap and bar["low"] <= sl[gap].entry_price:
            o = sl[0]; r = sl[gap].entry_price
            close_px = r + (bar["low"] - r) * alpha
            rl.append(unit_pnl_usd(sym, "SELL", o.entry_price, close_px, spread_px))
            closed.append(("SELL", o.entry_price)); tk.remove(o)
            sl = sorted([t for t in tk if t.direction=="SELL"], key=lambda t:t.entry_price, reverse=True)
        bl = sorted([t for t in tk if t.direction=="BUY"], key=lambda t:t.entry_price)
        while len(bl) > gap and bar["high"] >= bl[gap].entry_price:
            o = bl[0]; r = bl[gap].entry_price
            close_px = r + (bar["high"] - r) * alpha
            rl.append(unit_pnl_usd(sym, "BUY", o.entry_price, close_px, spread_px))
            closed.append(("BUY", o.entry_price)); tk.remove(o)
            bl = sorted([t for t in tk if t.direction=="BUY"], key=lambda t:t.entry_price)

        if not tk and abs(bar["close"] - anchor) >= step:
            anchor = bar["close"]; ns = anchor + step; nb = anchor - step

        cos_ = sum(1 for t in churn if t.direction=="SELL"); cob = sum(1 for t in churn if t.direction=="BUY")
        for d, cp in closed:
            c = cos_ if d=="SELL" else cob
            if c >= mop: continue
            if mom:
                if d=="SELL" and bar["close"] >= cp: continue
                if d=="BUY" and bar["close"] <= cp: continue
            churn.append(ChurnTicket(d, cp, idx))
            if d=="SELL": cos_+=1
            else: cob+=1

        cs = sorted([t for t in churn if t.direction=="SELL"], key=lambda t:t.entry_price, reverse=True)
        while len(cs) > gap and bar["low"] <= cs[gap].entry_price:
            o = cs[0]; r = cs[gap].entry_price
            close_px = r + (bar["low"] - r) * alpha
            crl.append(unit_pnl_usd(sym, "SELL", o.entry_price, close_px, spread_px))
            churn.remove(o); cs = sorted([t for t in churn if t.direction=="SELL"], key=lambda t:t.entry_price, reverse=True)
        cb = sorted([t for t in churn if t.direction=="BUY"], key=lambda t:t.entry_price)
        while len(cb) > gap and bar["high"] >= cb[gap].entry_price:
            o = cb[0]; r = cb[gap].entry_price
            close_px = r + (bar["high"] - r) * alpha
            crl.append(unit_pnl_usd(sym, "BUY", o.entry_price, close_px, spread_px))
            churn.remove(o); cb = sorted([t for t in churn if t.direction=="BUY"], key=lambda t:t.entry_price)

    fl = [unit_pnl_usd(sym, t.direction, t.entry_price, bars[-1]["close"], spread_px) for t in tk]
    cfl = [unit_pnl_usd(sym, t.direction, t.entry_price, bars[-1]["close"], spread_px) for t in churn]
    return {"combined": sum(rl)+sum(fl)+sum(crl)+sum(cfl), "bl": sum(rl)+sum(fl), "churn": sum(crl)+sum(cfl),
            "bl_closes": len(rl), "churn_closes": len(crl), "total_closes": len(rl)+len(crl)}


STEPS = {"BTCUSD": 50.0, "ETHUSD": 10.0, "SOLUSD": 0.50, "XRPUSD": 0.01}


def main():
    mt5.initialize()

    print(f"\n{'='*100}")
    print(f"  max_open=40 vs max_open=30 — Does this explain the $229K vs $115K?")
    print(f"{'='*100}")

    for mop in [30, 40, 50]:
        total = 0.0
        details = []
        for sym in CRYPTO:
            info = mt5.symbol_info(sym)
            bars = load_h1_bars(sym, 90)
            if not bars:
                continue
            step = STEPS[sym]
            r = sim(sym, bars, info, step, mop, 1, 1.0, True)
            total += r["combined"]
            details.append(f"{sym}: ${r['combined']:,.2f} ({r['total_closes']}c)")

        print(f"\n  max_open={mop}: ${total:>12,.2f}")
        for d in details:
            print(f"    {d}")

    mt5.shutdown()


if __name__ == "__main__":
    raise SystemExit(main())
