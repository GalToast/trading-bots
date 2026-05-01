#!/usr/bin/env python3
"""Crypto shadow lane launcher — test all crypto symbols on H1 with rearm architecture.

Tests: BTCUSD, ETHUSD, SOLUSD, XRPUSD, DOGEUSD, ADAUSD, DOTUSD, LTCUSD
With: momentum gate + alpha=1.0 + rearm churn (the winning config)
Plus: alpha=0.75 baseline for comparison

Outputs per-symbol results to identify which cryptos have edge and which don't.
"""
from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path
import time

import MetaTrader5 as mt5

from penetration_lattice_lab_v2 import Ticket, dynamic_step, pip_size_for, spread_price, unit_pnl_usd


ROOT = Path(__file__).resolve().parent.parent
ALL_CRYPTO = ["BTCUSD", "ETHUSD", "SOLUSD", "XRPUSD", "DOGEUSD", "ADAUSD", "DOTUSD", "LTCUSD"]
DAYS = 90


@dataclass
class ChurnTicket:
    __slots__ = ("direction", "entry_price", "opened_idx")
    def __init__(self, d, e, o):
        self.direction = d; self.entry_price = e; self.opened_idx = o


def load_h1_bars(symbol: str, days: int) -> list[dict]:
    rates = mt5.copy_rates_from_pos(symbol, mt5.TIMEFRAME_H1, 0, 24 * days)
    if rates is None or len(rates) == 0:
        return []
    return [
        {"time": int(r[0]), "open": float(r[1]), "high": float(r[2]),
         "low": float(r[3]), "close": float(r[4]), "tick_volume": int(r[5])}
        for r in rates
    ]


def sim_crypto(sym, bars, info, step_price, mop, close_gap, alpha, momentum_gate):
    if not bars:
        return {}
    spread_px = spread_price(info)
    base_step = step_price
    anchor = bars[0]["close"]
    ns = anchor + base_step
    nb = anchor - base_step
    tk = []; rl = []; churn = []; crl = []

    for idx in range(1, len(bars)):
        bar = bars[idx]
        os_ = sum(1 for t in tk if t.direction == "SELL")
        ob = sum(1 for t in tk if t.direction == "BUY")

        while bar["high"] >= ns and os_ < mop:
            tk.append(Ticket(direction="SELL", entry_price=ns, opened_idx=idx))
            os_ += 1; ns += base_step
        while bar["low"] <= nb and ob < mop:
            tk.append(Ticket(direction="BUY", entry_price=nb, opened_idx=idx))
            ob += 1; nb -= base_step

        closed = []
        sl = sorted([t for t in tk if t.direction=="SELL"], key=lambda t:t.entry_price, reverse=True)
        while len(sl) > close_gap and bar["low"] <= sl[close_gap].entry_price:
            o = sl[0]; r = sl[close_gap].entry_price
            close_px = r + (bar["low"] - r) * alpha
            rl.append(unit_pnl_usd(sym, "SELL", o.entry_price, close_px, spread_px))
            closed.append(("SELL", o.entry_price)); tk.remove(o)
            sl = sorted([t for t in tk if t.direction=="SELL"], key=lambda t:t.entry_price, reverse=True)
        bl = sorted([t for t in tk if t.direction=="BUY"], key=lambda t:t.entry_price)
        while len(bl) > close_gap and bar["high"] >= bl[close_gap].entry_price:
            o = bl[0]; r = bl[close_gap].entry_price
            close_px = r + (bar["high"] - r) * alpha
            rl.append(unit_pnl_usd(sym, "BUY", o.entry_price, close_px, spread_px))
            closed.append(("BUY", o.entry_price)); tk.remove(o)
            bl = sorted([t for t in tk if t.direction=="BUY"], key=lambda t:t.entry_price)

        if not tk and abs(bar["close"] - anchor) >= base_step:
            anchor = bar["close"]; ns = anchor + base_step; nb = anchor - base_step

        # Churn entries
        cos_ = sum(1 for t in churn if t.direction=="SELL"); cob = sum(1 for t in churn if t.direction=="BUY")
        for d, cp in closed:
            c = cos_ if d=="SELL" else cob
            if c >= mop: continue
            if momentum_gate:
                if d=="SELL" and bar["close"] >= cp: continue
                if d=="BUY" and bar["close"] <= cp: continue
            churn.append(ChurnTicket(d, cp, idx))
            if d=="SELL": cos_+=1
            else: cob+=1

        # Churn closes
        cs = sorted([t for t in churn if t.direction=="SELL"], key=lambda t:t.entry_price, reverse=True)
        while len(cs) > close_gap and bar["low"] <= cs[close_gap].entry_price:
            o = cs[0]; r = cs[close_gap].entry_price
            close_px = r + (bar["low"] - r) * alpha
            crl.append(unit_pnl_usd(sym, "SELL", o.entry_price, close_px, spread_px))
            churn.remove(o); cs = sorted([t for t in churn if t.direction=="SELL"], key=lambda t:t.entry_price, reverse=True)
        cb = sorted([t for t in churn if t.direction=="BUY"], key=lambda t:t.entry_price)
        while len(cb) > close_gap and bar["high"] >= cb[close_gap].entry_price:
            o = cb[0]; r = cb[close_gap].entry_price
            close_px = r + (bar["high"] - r) * alpha
            crl.append(unit_pnl_usd(sym, "BUY", o.entry_price, close_px, spread_px))
            churn.remove(o); cb = sorted([t for t in churn if t.direction=="BUY"], key=lambda t:t.entry_price)

    fl = [unit_pnl_usd(sym, t.direction, t.entry_price, bars[-1]["close"], spread_px) for t in tk]
    cfl = [unit_pnl_usd(sym, t.direction, t.entry_price, bars[-1]["close"], spread_px) for t in churn]
    return {"combined": sum(rl)+sum(fl)+sum(crl)+sum(cfl), "bl": sum(rl)+sum(fl), "churn": sum(crl)+sum(cfl),
            "bl_closes": len(rl), "churn_closes": len(crl)}


# Crypto step sizes (from breakthrough research)
CRYPTO_STEPS = {
    "BTCUSD": 50.0,
    "ETHUSD": 10.0,
    "SOLUSD": 0.50,
    "XRPUSD": 0.01,
    "DOGEUSD": 0.001,
    "ADAUSD": 0.005,
    "DOTUSD": 0.05,
    "LTCUSD": 5.0,
}


def main():
    mt5.initialize()

    variants = [
        # Baseline: alpha=0.0, no momentum (should lose on crypto)
        ("baseline_a0", 0.0, False, 1),
        # Alpha=0.75 (minimum required for crypto)
        ("alpha75", 0.75, False, 1),
        # Alpha=1.0 + momentum (the $248K config)
        ("alpha100_mom", 1.0, True, 1),
    ]

    print(f"\n{'='*100}")
    print(f"  CRYPTO SHADOW LANE — Full Symbol Sweep, {DAYS}d H1, {len(ALL_CRYPTO)} symbols")
    print(f"{'='*100}")

    all_rows = []
    available = []

    for sym in ALL_CRYPTO:
        info = mt5.symbol_info(sym)
        if info is None:
            print(f"  ⚠️  {sym} not available in MT5")
            continue
        bars = load_h1_bars(sym, DAYS)
        if not bars:
            print(f"  ⚠️  {sym} has no H1 data")
            continue
        available.append((sym, info, bars))
        print(f"\n  {'─'*80}")
        print(f"  **{sym}** — {len(bars)} H1 bars, spread={spread_price(info):.6f}")
        print(f"  {'─'*80}")

        step = CRYPTO_STEPS.get(sym, 1.0)
        for name, alpha, mom, gap in variants:
            t0 = time.time()
            r = sim_crypto(sym, bars, info, step, 20, gap, alpha, mom)
            elapsed = time.time() - t0
            if not r:
                print(f"    ⚠️  {name}: empty result")
                continue
            bl_total = r["bl"]  # baseline for this variant
            churn_add = r["churn"]
            mom_str = " +mom" if mom else ""
            print(f"    {name:20s}: ${r['combined']:>12,.2f}  (bl=${r['bl']:>10,.2f}, churn=${churn_add:>+10,.2f}, {r['bl_closes']+r['churn_closes']}c) [{elapsed:.1f}s]{mom_str}")

            all_rows.append({
                "symbol": sym, "name": name, "alpha": alpha, "mom": mom, "gap": gap,
                "combined": round(r["combined"], 2), "bl": round(r["bl"], 2),
                "churn": round(r["churn"], 2), "bl_closes": r["bl_closes"],
                "churn_closes": r["churn_closes"], "total_closes": r["bl_closes"]+r["churn_closes"],
                "step": step, "bars": len(bars),
            })

    # Summary tables
    print(f"\n{'='*100}")
    print(f"  SUMMARY: Best Config Per Symbol")
    print(f"{'='*100}")

    sym_best = {}
    for sym, info, bars in available:
        step = CRYPTO_STEPS.get(sym, 1.0)
        sym_rows = [r for r in all_rows if r["symbol"] == sym]
        if not sym_rows:
            continue
        best = max(sym_rows, key=lambda r: r["combined"])
        worst = min(sym_rows, key=lambda r: r["combined"])
        sym_best[sym] = best
        status = "✅ EDGE" if best["combined"] > 0 else "❌ NO EDGE"
        print(f"\n  {sym:12s} {status:12s} Best: ${best['combined']:>12,.2f} ({best['name']})  "
              f"Worst: ${worst['combined']:>12,.2f} ({worst['name']})  Delta: ${best['combined']-worst['combined']:>12,.2f}")

    # Total portfolio rankings
    print(f"\n{'='*100}")
    print(f"  TOTAL PORTFOLIO RANKINGS")
    print(f"{'='*100}")

    for name, alpha, mom, gap in variants:
        total = sum(r["combined"] for r in all_rows if r["name"] == name)
        n_positive = sum(1 for r in all_rows if r["name"] == name and r["combined"] > 0)
        n_total = sum(1 for r in all_rows if r["name"] == name)
        mom_str = " +mom" if mom else ""
        print(f"  {name:20s}: ${total:>14,.2f}  ({n_positive}/{n_total} symbols profitable){mom_str}")

    # Edge ranking
    print(f"\n{'='*100}")
    print(f"  SYMBOLS RANKED BY BEST CONFIG (alpha100_mom)")
    print(f"{'='*100}")

    alpha100_rows = [r for r in all_rows if r["name"] == "alpha100_mom"]
    alpha100_sorted = sorted(alpha100_rows, key=lambda r: r["combined"], reverse=True)
    for i, r in enumerate(alpha100_sorted, 1):
        status = "✅" if r["combined"] > 0 else "❌"
        churn_pct = (r["churn"] / r["bl"] * 100) if r["bl"] != 0 else 0
        print(f"  {i}. {status} {r['symbol']:12s} ${r['combined']:>12,.2f}  "
              f"bl=${r['bl']:>10,.2f}  churn=${r['churn']:>+10,.2f} ({churn_pct:+.0f}%)  "
              f"{r['total_closes']} closes")

    # Save full results
    out = ROOT / "reports" / "crypto_full_shadow_sweep.csv"
    with out.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=all_rows[0].keys() if all_rows else [])
        w.writeheader(); w.writerows(all_rows)

    print(f"\n  Wrote {out}")

    # Edge/no-edge summary
    edge_symbols = [r for r in alpha100_sorted if r["combined"] > 0]
    no_edge_symbols = [r for r in alpha100_sorted if r["combined"] <= 0]

    print(f"\n{'='*100}")
    print(f"  FINAL VERDICT")
    print(f"{'='*100}")
    print(f"\n  ✅ HAVE EDGE ({len(edge_symbols)} symbols):")
    for r in edge_symbols:
        print(f"    {r['symbol']:12s} ${r['combined']:>12,.2f}  ({r['total_closes']} closes, churn=${r['churn']:>+,.2f})")
    print(f"\n  ❌ NO EDGE ({len(no_edge_symbols)} symbols):")
    for r in no_edge_symbols:
        print(f"    {r['symbol']:12s} ${r['combined']:>12,.2f}  ({r['total_closes']} closes, churn=${r['churn']:>+,.2f})")

    total_edge = sum(r["combined"] for r in edge_symbols)
    total_no_edge = sum(r["combined"] for r in no_edge_symbols)
    print(f"\n  Edge symbols total: ${total_edge:,.2f}")
    print(f"  No-edge symbols total: ${total_no_edge:,.2f}")
    print(f"  Full basket total: ${total_edge + total_no_edge:,.2f}")

    mt5.shutdown()


if __name__ == "__main__":
    raise SystemExit(main())
