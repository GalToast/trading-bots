#!/usr/bin/env python3
"""Independently verify qwen-main's $248K claim: sg=1, bg=1, α=1.0 + momentum gate."""
from __future__ import annotations

import csv
from pathlib import Path

import MetaTrader5 as mt5

from penetration_lattice_lab_v2 import Ticket, dynamic_step, load_bars, pip_size_for, spread_price, unit_pnl_usd

ROOT = Path(__file__).resolve().parent.parent
SYMBOLS = ["GBPUSD", "EURUSD", "NZDUSD"]


class ChurnTicket:
    __slots__ = ("direction", "entry_price", "opened_idx")
    def __init__(self, d, e, o):
        self.direction = d; self.entry_price = e; self.opened_idx = o


def sim_full(sym, bars, info, step_pips, sell_gap, buy_gap, alpha, momentum_gate, mop=30):
    pip_size = pip_size_for(info); spread_px = spread_price(info); bp = step_pips * pip_size
    a = bars[0]["close"]; ns = a+bp; nb = a-bp
    tk = []; rl = []; churn = []; crl = []

    for i in range(1, len(bars)):
        b = bars[i]
        os_ = sum(1 for t in tk if t.direction=="SELL"); ob = sum(1 for t in tk if t.direction=="BUY")
        ss = dynamic_step(bp, os_, type("Cfg",(),{"adaptive_step_threshold_1":10,"adaptive_step_threshold_2":20,"adaptive_step_multiplier_1":1.5,"adaptive_step_multiplier_2":2.0})())
        bs = dynamic_step(bp, ob, type("Cfg",(),{"adaptive_step_threshold_1":10,"adaptive_step_threshold_2":20,"adaptive_step_multiplier_1":1.5,"adaptive_step_multiplier_2":2.0})())
        while b["high"]>=ns and os_<mop: tk.append(Ticket(direction="SELL",entry_price=ns,opened_idx=i)); os_+=1; ss=dynamic_step(bp,os_,type("Cfg",(),{"adaptive_step_threshold_1":10,"adaptive_step_threshold_2":20,"adaptive_step_multiplier_1":1.5,"adaptive_step_multiplier_2":2.0})()); ns+=ss
        while b["low"]<=nb and ob<mop: tk.append(Ticket(direction="BUY",entry_price=nb,opened_idx=i)); ob+=1; bs=dynamic_step(bp,ob,type("Cfg",(),{"adaptive_step_threshold_1":10,"adaptive_step_threshold_2":20,"adaptive_step_multiplier_1":1.5,"adaptive_step_multiplier_2":2.0})()); nb-=bs

        closed = []
        sl = sorted([t for t in tk if t.direction=="SELL"], key=lambda t:t.entry_price, reverse=True)
        while len(sl)>sell_gap and b["low"]<=sl[sell_gap].entry_price:
            o=sl[0]; r=sl[sell_gap].entry_price
            rl.append(unit_pnl_usd(sym,"SELL",o.entry_price,r+(b["low"]-r)*alpha,spread_px))
            closed.append(("SELL",o.entry_price)); tk.remove(o); sl=sorted([t for t in tk if t.direction=="SELL"], key=lambda t:t.entry_price, reverse=True)
        bl = sorted([t for t in tk if t.direction=="BUY"], key=lambda t:t.entry_price)
        while len(bl)>buy_gap and b["high"]>=bl[buy_gap].entry_price:
            o=bl[0]; r=bl[buy_gap].entry_price
            rl.append(unit_pnl_usd(sym,"BUY",o.entry_price,r+(b["high"]-r)*alpha,spread_px))
            closed.append(("BUY",o.entry_price)); tk.remove(o); bl=sorted([t for t in tk if t.direction=="BUY"], key=lambda t:t.entry_price)

        if not tk and abs(b["close"]-a)>=bp: a=b["close"]; ns=a+bp; nb=a-bp

        # Churn entries at closed levels
        cos_=sum(1 for t in churn if t.direction=="SELL"); cob=sum(1 for t in churn if t.direction=="BUY")
        for d, cp in closed:
            c = cos_ if d=="SELL" else cob
            if c>=mop: continue
            if momentum_gate:
                if d=="SELL" and b["close"]>=cp: continue
                if d=="BUY" and b["close"]<=cp: continue
            churn.append(ChurnTicket(d, cp, i))
            if d=="SELL": cos_+=1
            else: cob+=1

        # Churn closes
        cs = sorted([t for t in churn if t.direction=="SELL"], key=lambda t:t.entry_price, reverse=True)
        while len(cs)>sell_gap and b["low"]<=cs[sell_gap].entry_price:
            o=cs[0]; r=cs[sell_gap].entry_price
            crl.append(unit_pnl_usd(sym,"SELL",o.entry_price,r+(b["low"]-r)*alpha,spread_px))
            churn.remove(o); cs=sorted([t for t in churn if t.direction=="SELL"], key=lambda t:t.entry_price, reverse=True)
        cb = sorted([t for t in churn if t.direction=="BUY"], key=lambda t:t.entry_price)
        while len(cb)>buy_gap and b["high"]>=cb[buy_gap].entry_price:
            o=cb[0]; r=cb[buy_gap].entry_price
            crl.append(unit_pnl_usd(sym,"BUY",o.entry_price,r+(b["high"]-r)*alpha,spread_px))
            churn.remove(o); cb=sorted([t for t in churn if t.direction=="BUY"], key=lambda t:t.entry_price)

    fl=[unit_pnl_usd(sym,t.direction,t.entry_price,bars[-1]["close"],spread_px) for t in tk]
    cfl=[unit_pnl_usd(sym,t.direction,t.entry_price,bars[-1]["close"],spread_px) for t in churn]
    return {"combined":sum(rl)+sum(fl)+sum(crl)+sum(cfl), "bl":sum(rl)+sum(fl), "churn":sum(crl)+sum(cfl),
            "bl_closes":len(rl), "churn_closes":len(crl)}


def main():
    mt5.initialize()

    configs = [
        ("sg1_bg1_a100_mom", 1.0, 1, 1, 1.0, True),
        ("sg1_bg1_a100", 1.0, 1, 1, 1.0, False),
        ("sg1_bg1_a50_mom", 1.0, 1, 1, 0.5, True),
        ("sg2_bg1_a100_mom", 1.0, 2, 1, 1.0, True),
        ("sg3_bg1_a100_mom", 1.0, 3, 1, 1.0, True),
    ]

    print(f"\n{'='*80}")
    print(f"  Independent Verification: sg=1,bg=1,α=1.0+momentum")
    print(f"{'='*80}")

    for name, step, sg, bg, alpha, mom in configs:
        total = 0.0
        details = []
        for sym in SYMBOLS:
            info = mt5.symbol_info(sym)
            bars = load_bars(sym, 60)
            r = sim_full(sym, bars, info, step, sg, bg, alpha, mom)
            total += r["combined"]
            details.append(f"{sym}: ${r['combined']:.2f} (bl=${r['bl']:.2f}, churn=${r['churn']:+.2f})")

        bl_total = 0
        for sym in SYMBOLS:
            info = mt5.symbol_info(sym)
            bars = load_bars(sym, 60)
            from penetration_lattice_hybrid_apex import RawConfig, simulate_raw_close2
            cfg = RawConfig(step_pips={"GBPUSD":2.0,"EURUSD":3.0,"NZDUSD":1.5}[sym], max_open_per_side=20, close_mode="two_level")
            bl = simulate_raw_close2(sym, bars, info, cfg)
            bl_total += float(bl["combined_net_usd"])

        delta = total - bl_total
        mult = total / bl_total
        mom_str = " +mom" if mom else ""
        print(f"\n  {name}: ${total:>12,.2f} ({mult:.1f}x) Δ=${delta:+,.2f}{mom_str}")
        for d in details:
            print(f"    {d}")

    mt5.shutdown()


if __name__ == "__main__":
    raise SystemExit(main())
