#!/usr/bin/env python3
"""Validate the full 10-symbol canonical portfolio.

FX M1: GBPUSD, EURUSD, NZDUSD (alpha=0.50, step=1.0/0.5, gap=3, momentum)
Crypto H1: BTCUSD, ETHUSD, XRPUSD, SOLUSD, DOGEUSD, ADAUSD, DOTUSD (alpha=1.0, gap=1, momentum)

This produces the single combined number for the full basket.
"""
from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path
import time

import MetaTrader5 as mt5

from penetration_lattice_lab_v2 import Ticket, pip_size_for, spread_price, unit_pnl_usd


ROOT = Path(__file__).resolve().parent.parent

FX_SYMBOLS = ["GBPUSD", "EURUSD", "NZDUSD"]
CRYPTO_SYMBOLS = ["BTCUSD", "ETHUSD", "XRPUSD", "SOLUSD", "DOGEUSD", "ADAUSD", "DOTUSD"]

FX_CONFIGS = {
    "GBPUSD": {"step": 1.0, "gap": 3, "alpha": 0.5, "mom": True},
    "EURUSD": {"step": 0.5, "gap": 3, "alpha": 0.5, "mom": True},
    "NZDUSD": {"step": 0.5, "gap": 3, "alpha": 0.5, "mom": True},
}

CRYPTO_CONFIGS = {
    "BTCUSD": {"step": 50.0, "gap": 1, "alpha": 1.0, "mom": True},
    "ETHUSD": {"step": 10.0, "gap": 1, "alpha": 1.0, "mom": True},
    "XRPUSD": {"step": 0.01, "gap": 1, "alpha": 1.0, "mom": True},
    "SOLUSD": {"step": 0.50, "gap": 1, "alpha": 1.0, "mom": True},
    "DOGEUSD": {"step": 0.001, "gap": 1, "alpha": 1.0, "mom": True},
    "ADAUSD": {"step": 0.005, "gap": 1, "alpha": 1.0, "mom": True},
    "DOTUSD": {"step": 0.05, "gap": 1, "alpha": 1.0, "mom": True},
}


def load_m1_bars(symbol, days):
    # MT5 has a limit on bars per call, load in chunks
    max_bars_per_call = 100000
    total_bars = 1440 * days
    all_rates = []
    offset = 0
    while offset < total_bars:
        count = min(max_bars_per_call, total_bars - offset)
        rates = mt5.copy_rates_from_pos(symbol, mt5.TIMEFRAME_M1, offset, count)
        if rates is None or len(rates) == 0:
            break
        all_rates.extend(rates)
        offset += count
    if not all_rates:
        return []
    return [{"time": int(r[0]), "open": float(r[1]), "high": float(r[2]),
             "low": float(r[3]), "close": float(r[4]), "tick_volume": int(r[5])} for r in all_rates]


def load_h1_bars(symbol, days):
    rates = mt5.copy_rates_from_pos(symbol, mt5.TIMEFRAME_H1, 0, 24 * days)
    if rates is None or len(rates) == 0:
        return []
    return [{"time": int(r[0]), "open": float(r[1]), "high": float(r[2]),
             "low": float(r[3]), "close": float(r[4]), "tick_volume": int(r[5])} for r in rates]


@dataclass
class ChurnTicket:
    __slots__ = ("direction", "entry_price", "opened_idx")
    def __init__(self, d, e, o):
        self.direction = d; self.entry_price = e; self.opened_idx = o


def sim(sym, bars, info, step, mop, gap, alpha, mom):
    if not bars:
        return {}
    spread_px = spread_price(info)
    anchor = bars[0]["close"]
    ns = anchor + step
    nb = anchor - step
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


def main():
    mt5.initialize()
    
    all_results = []
    grand_total = 0.0
    grand_bl = 0.0
    grand_churn = 0.0
    grand_closes = 0

    print(f"\n{'='*110}")
    print(f"  UNIVERSAL 10-SYMBOL PORTFOLIO VALIDATION")
    print(f"{'='*110}")

    # FX M1
    print(f"\n  {'─'*110}")
    print(f"  FX M1 (alpha=0.50, gap=3, momentum gate)")
    print(f"  {'─'*110}")
    for sym in FX_SYMBOLS:
        info = mt5.symbol_info(sym)
        bars = load_m1_bars(sym, 60)  # 60d for FX (validated in previous sweeps)
        cfg = FX_CONFIGS[sym]
        pip_size = pip_size_for(info)
        step_price = cfg["step"] * pip_size
        r = sim(sym, bars, info, step_price, 20, cfg["gap"], cfg["alpha"], cfg["mom"])
        if not r:
            print(f"  ⚠️  {sym} returned empty result")
            continue
        all_results.append({"asset_class": "FX_M1", "symbol": sym, **r, **cfg})
        grand_total += r["combined"]; grand_bl += r["bl"]; grand_churn += r["churn"]; grand_closes += r["total_closes"]
        print(f"  {sym:12s} ${r['combined']:>12,.2f}  (bl=${r['bl']:>10,.2f}, churn=${r['churn']:>+10,.2f}, {r['total_closes']}c)")

    # Crypto H1
    print(f"\n  {'─'*110}")
    print(f"  Crypto H1 (alpha=1.0, gap=1, momentum gate)")
    print(f"  {'─'*110}")
    for sym in CRYPTO_SYMBOLS:
        info = mt5.symbol_info(sym)
        bars = load_h1_bars(sym, 90)
        cfg = CRYPTO_CONFIGS[sym]
        r = sim(sym, bars, info, cfg["step"], 20, cfg["gap"], cfg["alpha"], cfg["mom"])
        if not r:
            print(f"  ⚠️  {sym} returned empty result")
            continue
        all_results.append({"asset_class": "Crypto_H1", "symbol": sym, **r, **cfg})
        grand_total += r["combined"]; grand_bl += r["bl"]; grand_churn += r["churn"]; grand_closes += r["total_closes"]
        print(f"  {sym:12s} ${r['combined']:>12,.2f}  (bl=${r['bl']:>10,.2f}, churn=${r['churn']:>+10,.2f}, {r['total_closes']}c)")

    # Summary
    print(f"\n{'='*110}")
    print(f"  GRAND TOTAL")
    print(f"{'='*110}")
    print(f"\n  Baseline total:  ${grand_bl:>12,.2f}")
    print(f"  Churn add:       ${grand_churn:>12,.2f}  (+{grand_churn/grand_bl*100:.0f}%)")
    print(f"  COMBINED:        ${grand_total:>12,.2f}  ({grand_total/grand_bl:.1f}x baseline)")
    print(f"  Total closes:    {grand_closes}")

    # Per-asset-class
    fx_total = sum(r["combined"] for r in all_results if r["asset_class"] == "FX_M1")
    crypto_total = sum(r["combined"] for r in all_results if r["asset_class"] == "Crypto_H1")
    print(f"\n  FX M1 total:     ${fx_total:>12,.2f}")
    print(f"  Crypto H1 total: ${crypto_total:>12,.2f}")

    # Rank by PnL
    print(f"\n{'='*110}")
    print(f"  SYMBOLS RANKED BY PNL")
    print(f"{'='*110}")
    sorted_results = sorted(all_results, key=lambda r: r["combined"], reverse=True)
    for i, r in enumerate(sorted_results, 1):
        pct_of_total = r["combined"] / grand_total * 100
        print(f"  {i:2d}. {r['symbol']:12s} ${r['combined']:>12,.2f}  ({pct_of_total:.1f}% of total)  "
              f"churn=${r['churn']:>+10,.2f}  {r['total_closes']} closes")

    # Save
    out = ROOT / "reports" / "universal_10symbol_validation.csv"
    with out.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=all_results[0].keys())
        w.writeheader(); w.writerows(all_results)

    print(f"\n  Wrote {out}")

    mt5.shutdown()


if __name__ == "__main__":
    raise SystemExit(main())
