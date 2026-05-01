#!/usr/bin/env python3
"""
Penetration Lattice — Focused Sweep (step 0.5/0.75/1.0, stops -0.5 to -3.0)

Faster than the full grid. Tests the most promising region based on partial sweep.
"""
from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path
from statistics import mean
import time

import MetaTrader5 as mt5


ROOT = Path(__file__).resolve().parent.parent

SYMBOLS = [
    "EURUSD", "GBPUSD", "USDJPY", "USDCHF", "NZDUSD",
    "EURJPY", "AUDUSD", "CADJPY", "EURGBP",
]


@dataclass(frozen=True)
class Config:
    step_pips: float
    hard_stop_threshold_usd: float
    anchor_reset_pips: float = 3.0
    max_open_per_side: int = 50
    vwap_lookback: int = 20
    adaptive_threshold_1: int = 10
    adaptive_threshold_2: int = 20
    adaptive_mult_1: float = 1.5
    adaptive_mult_2: float = 2.0


def pip_size_for(si) -> float:
    p = float(si.point or 0.0)
    d = int(si.digits or 0)
    return p * 10.0 if d in (3, 5) else p


def spread_price(si) -> float:
    return float(si.spread or 0.0) * float(si.point or 0.0)


def unit_pnl(sym: str, direction: str, ep: float, xp: float, spx: float) -> float:
    ot = mt5.ORDER_TYPE_BUY if direction == "BUY" else mt5.ORDER_TYPE_SELL
    gross = mt5.order_calc_profit(ot, sym, 0.01, ep, xp)
    if gross is None:
        return 0.0
    if direction == "BUY":
        sc = mt5.order_calc_profit(ot, sym, 0.01, ep + spx, ep)
    else:
        sc = mt5.order_calc_profit(ot, sym, 0.01, ep, ep + spx)
    return float(gross) - abs(float(sc or 0.0))


def dyn_step(bs: float, oc: int) -> float:
    if oc >= 20: return bs * 2.0
    elif oc >= 10: return bs * 1.5
    return bs


def vwap(bars, idx, lb):
    s = max(0, idx - lb)
    w = bars[s:idx]
    if not w: return bars[idx-1]["close"]
    cv = sum(b["close"]*b["tick_volume"] for b in w)
    v = sum(b["tick_volume"] for b in w)
    return cv/v if v else w[-1]["close"]


@dataclass
class Tkt:
    direction: str
    entry_price: float
    opened_idx: int


def sim(sym, bars, info, cfg):
    if not bars:
        return {}
    pip = pip_size_for(info)
    spx = spread_price(info)
    bsp = cfg.step_pips * pip
    rpx = cfg.anchor_reset_pips * pip

    anchor = bars[0]["close"]
    nsl = anchor + bsp
    nbl = anchor - bsp
    ot: list[Tkt] = []
    rp: list[float] = []
    up: list[float] = []
    mo = 0
    ar = 0
    uf = 0
    wfs = 0.0

    for idx in range(1, len(bars)):
        bar = bars[idx]
        ob = sum(1 for t in ot if t.direction=="BUY")
        os_ = sum(1 for t in ot if t.direction=="SELL")
        css = dyn_step(bsp, os_)
        cbs = dyn_step(bsp, ob)

        while bar["high"] >= nsl and os_ < cfg.max_open_per_side:
            ot.append(Tkt("SELL", nsl, idx)); nsl += css; os_ += 1; css = dyn_step(bsp, os_)
        while bar["low"] <= nbl and ob < cfg.max_open_per_side:
            ot.append(Tkt("BUY", nbl, idx)); nbl -= cbs; ob += 1; cbs = dyn_step(bsp, ob)

        sells = sorted((t for t in ot if t.direction=="SELL"), key=lambda t: t.entry_price, reverse=True)
        while len(sells) >= 2 and bar["low"] <= sells[1].entry_price:
            cr = bar["low"]
            prof = [t for t in sells if unit_pnl(sym, "SELL", t.entry_price, cr, spx) > 0]
            if not prof: break
            for t in prof:
                rp.append(unit_pnl(sym, "SELL", t.entry_price, cr, spx))
                ot.remove(t)
            sells = sorted((t for t in ot if t.direction=="SELL"), key=lambda t: t.entry_price, reverse=True)

        buys = sorted((t for t in ot if t.direction=="BUY"), key=lambda t: t.entry_price)
        while len(buys) >= 2 and bar["high"] >= buys[1].entry_price:
            cr = bar["high"]
            prof = [t for t in buys if unit_pnl(sym, "BUY", t.entry_price, cr, spx) > 0]
            if not prof: break
            for t in prof:
                rp.append(unit_pnl(sym, "BUY", t.entry_price, cr, spx))
                ot.remove(t)
            buys = sorted((t for t in ot if t.direction=="BUY"), key=lambda t: t.entry_price)

        if ot:
            fps = [unit_pnl(sym, t.direction, t.entry_price, bar["close"], spx) for t in ot]
            wp = min(fps)
            wfs = min(wfs, wp)
            if wp <= cfg.hard_stop_threshold_usd:
                for t in list(ot):
                    up.append(unit_pnl(sym, t.direction, t.entry_price, bar["close"], spx))
                    ot.remove(t)
                uf += 1
                anchor = bar["close"]; nsl = anchor + bsp; nbl = anchor - bsp

        if not ot:
            ca = vwap(bars, idx, cfg.vwap_lookback)
            if abs(bar["close"] - anchor) >= rpx:
                anchor = ca; nsl = anchor + bsp; nbl = anchor - bsp; ar += 1

        mo = max(mo, len(ot))

    lc = bars[-1]["close"]
    fp = [unit_pnl(sym, t.direction, t.entry_price, lc, spx) for t in ot]
    rn = sum(rp); un = sum(up); fn = sum(fp)
    return {
        "symbol": sym, "step_pips": cfg.step_pips, "hard_stop_usd": cfg.hard_stop_threshold_usd,
        "realized_closes": len(rp), "unwind_closes": len(up),
        "wr_pct": round(sum(1 for p in rp if p>0)/len(rp)*100,1) if rp else 0.0,
        "realized_net_usd": round(rn,3), "unwind_net_usd": round(un,3),
        "open_tickets_left": len(ot), "floating_net_usd": round(fn,3),
        "worst_floating_seen_usd": round(wfs,3), "combined_net_usd": round(rn+un+fn,3),
        "max_open_total": mo, "unwind_fires": uf, "anchor_resets": ar,
    }


def main():
    steps = [0.5, 0.75, 1.0]
    stops = [-0.5, -0.75, -1.0, -1.5, -2.0, -3.0]
    days = 20

    if not mt5.initialize():
        print("MT5 init failed"); return 1

    try:
        all_rows = []
        for step_p in steps:
            for stop_u in stops:
                t0 = time.time()
                cfg = Config(step_pips=step_p, hard_stop_threshold_usd=stop_u)
                print(f"\n=== step={step_p} stop=${stop_u} ===", flush=True)
                sym_total = 0.0
                for sym in SYMBOLS:
                    info = mt5.symbol_info(sym)
                    if info is None: continue
                    rates = mt5.copy_rates_from_pos(sym, mt5.TIMEFRAME_M1, 0, 1440*days)
                    if rates is None or len(rates)==0: continue
                    bars = [{"time":int(r[0]),"open":float(r[1]),"high":float(r[2]),"low":float(r[3]),"close":float(r[4]),"tick_volume":int(r[5])} for r in rates]
                    row = sim(sym, bars, info, cfg)
                    all_rows.append(row)
                    print(f"  {sym:<7} banked={row['realized_net_usd']:+.2f} unwind={row['unwind_net_usd']:+.2f} combined={row['combined_net_usd']:+.2f} fires={row['unwind_fires']:>3}", flush=True)
                    sym_total += row["combined_net_usd"]
                print(f"  COMBINED: ${sym_total:+.2f} ({time.time()-t0:.1f}s)", flush=True)

        out = ROOT / "reports" / "penetration_focused_sweep.csv"
        out.parent.mkdir(parents=True, exist_ok=True)
        if all_rows:
            with out.open("w", newline="", encoding="utf-8") as f:
                w = csv.DictWriter(f, fieldnames=list(all_rows[0].keys()))
                w.writeheader(); w.writerows(all_rows)
            print(f"\nSaved {out}")
        return 0
    finally:
        mt5.shutdown()


if __name__ == "__main__":
    raise SystemExit(main())
