#!/usr/bin/env python3
"""Crypto H1 penetration lattice with rearm + momentum gate + alpha.

Applies the FX rearm architecture to crypto on H1 timeframe.
Key parameters from crypto breakthrough research:
- BTCUSD: step=$50, alpha=0.75 (required to overcome $171 spread)
- ETHUSD: step=$10, alpha=0.75
- SOLUSD: step=$0.50, alpha=0.75

Testing: momentum gate + rearm_lvl2_exc1 + alpha=0.75 on H1 crypto
"""
from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path

import MetaTrader5 as mt5

from penetration_lattice_lab_v2 import Ticket, dynamic_step, load_bars, pip_size_for, spread_price, unit_pnl_usd

ROOT = Path(__file__).resolve().parent.parent
CRYPTO_SYMBOLS = ["BTCUSD", "ETHUSD", "SOLUSD"]
DAYS = 90  # Max H1 data available


@dataclass
class ChurnTicket:
    __slots__ = ("direction", "entry_price", "opened_idx")
    def __init__(self, d, e, o):
        self.direction = d; self.entry_price = e; self.opened_idx = o


def load_h1_bars(symbol: str, days: int) -> list[dict]:
    """Load H1 bars for crypto."""
    rates = mt5.copy_rates_from_pos(symbol, mt5.TIMEFRAME_H1, 0, 24 * days)
    if rates is None or len(rates) == 0:
        return []
    return [
        {
            "time": int(r[0]),
            "open": float(r[1]),
            "high": float(r[2]),
            "low": float(r[3]),
            "close": float(r[4]),
            "tick_volume": int(r[5]),
        }
        for r in rates
    ]


def sim_crypto(sym, bars, info, step_price, mop, close_gap, alpha, momentum_gate):
    """H1 crypto penetration lattice with rearm churn."""
    if not bars:
        return {}

    spread_px = spread_price(info)

    # For crypto, step is in price units, not pips
    base_step = step_price

    anchor = bars[0]["close"]
    ns = anchor + base_step
    nb = anchor - base_step
    tk = []; rl = []; churn = []; crl = []

    for idx in range(1, len(bars)):
        bar = bars[idx]
        os_ = sum(1 for t in tk if t.direction == "SELL")
        ob = sum(1 for t in tk if t.direction == "BUY")

        # No adaptive stepping for crypto H1 (different from FX M1)
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

        # Churn entries at closed levels
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


def main():
    mt5.initialize()

    # Crypto configs from breakthrough research
    crypto_configs = {
        "BTCUSD": {"step": 50.0, "mop": 20},
        "ETHUSD": {"step": 10.0, "mop": 20},
        "SOLUSD": {"step": 0.50, "mop": 20},
    }

    variants = [
        # Baseline (alpha=0.0, no momentum, no rearm)
        ("baseline_a0", 0.0, False, 1),
        # Alpha=0.75 (required for crypto)
        ("alpha75", 0.75, False, 1),
        # Alpha=0.75 + momentum gate
        ("alpha75_mom", 0.75, True, 1),
        # Alpha=0.75 + momentum + gap=2
        ("alpha75_mom_gap2", 0.75, True, 2),
        # Alpha=1.00 + momentum (the $248K config applied to crypto)
        ("alpha100_mom", 1.0, True, 1),
        # Alpha=1.00 + momentum + gap=2
        ("alpha100_mom_gap2", 1.0, True, 2),
    ]

    print(f"\n{'='*80}")
    print(f"  CRYPTO H1 REARM SWEEP — {DAYS}d, 3 symbols")
    print(f"{'='*80}")

    all_rows = []
    for name, alpha, mom, gap in variants:
        total = 0.0
        details = []
        for sym in CRYPTO_SYMBOLS:
            info = mt5.symbol_info(sym)
            if info is None:
                print(f"  ⚠️  {sym} not available")
                continue
            bars = load_h1_bars(sym, DAYS)
            if not bars:
                print(f"  ⚠️  {sym} has no H1 data")
                continue
            cfg = crypto_configs[sym]
            r = sim_crypto(sym, bars, info, cfg["step"], cfg["mop"], gap, alpha, mom)
            if not r:
                print(f"  ⚠️  {sym} returned empty result")
                continue
            total += r["combined"]
            details.append(f"{sym}: ${r['combined']:,.2f} (bl=${r['bl']:,.2f}, churn=${r['churn']:+,.2f})")

        bl_total = 0
        for sym in CRYPTO_SYMBOLS:
            info = mt5.symbol_info(sym)
            if info is None: continue
            bars = load_h1_bars(sym, DAYS)
            if not bars: continue
            cfg = crypto_configs[sym]
            r = sim_crypto(sym, bars, info, cfg["step"], cfg["mop"], 1, 0.0, False)
            if not r: continue
            bl_total += r["combined"]

        delta = total - bl_total
        mult = total / bl_total if bl_total > 0 else 0
        all_rows.append({"name": name, "alpha": alpha, "mom": mom, "gap": gap,
                         "total": total, "delta": delta, "mult": mult})
        mom_str = " +mom" if mom else ""
        print(f"\n  {name}: ${total:>14,.2f} ({mult:.1f}x baseline) Δ=${delta:+,.2f}{mom_str}")
        for d in details:
            print(f"    {d}")

    print(f"\n{'='*80}")
    for r in sorted(all_rows, key=lambda x: x["total"], reverse=True):
        print(f"  {r['name']:25s} ${r['total']:>14,.2f}  {r['mult']:.1f}x  Δ=${r['delta']:>+11,.2f}")

    best = max(all_rows, key=lambda r: r["total"])
    print(f"\n🏆 Best: {best['name']} → ${best['total']:,.2f} ({best['mult']:.1f}x baseline)")

    out = ROOT / "reports" / "crypto_h1_rearm_sweep.csv"
    with out.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=all_rows[0].keys())
        w.writeheader(); w.writerows(all_rows)

    mt5.shutdown()


if __name__ == "__main__":
    raise SystemExit(main())
