#!/usr/bin/env python3
"""Targeted fine-tune sweep around known optima for each symbol."""
from __future__ import annotations

import csv
from pathlib import Path
import time

import MetaTrader5 as mt5

from penetration_lattice_lab_v2 import Ticket, dynamic_step, load_bars, pip_size_for, spread_price, unit_pnl_usd

ROOT = Path(__file__).resolve().parent.parent


def sim(sym, bars, info, step, gap, alpha, mop):
    if not bars: return {}
    ps = pip_size_for(info); sp = spread_price(info); bp = step * ps
    a = bars[0]["close"]; ns = a+bp; nb = a-bp
    tk = []; rl = []
    for i in range(1, len(bars)):
        b = bars[i]
        os_=sum(1 for t in tk if t.direction=="SELL"); ob=sum(1 for t in tk if t.direction=="BUY")
        ss=dynamic_step(bp,os_,type("Cfg",(),{"adaptive_step_threshold_1":10,"adaptive_step_threshold_2":20,"adaptive_step_multiplier_1":1.5,"adaptive_step_multiplier_2":2.0})())
        bs=dynamic_step(bp,ob,type("Cfg",(),{"adaptive_step_threshold_1":10,"adaptive_step_threshold_2":20,"adaptive_step_multiplier_1":1.5,"adaptive_step_multiplier_2":2.0})())
        while b["high"]>=ns and os_<mop: tk.append(Ticket(direction="SELL",entry_price=ns,opened_idx=i)); os_+=1; ss=dynamic_step(bp,os_,type("Cfg",(),{"adaptive_step_threshold_1":10,"adaptive_step_threshold_2":20,"adaptive_step_multiplier_1":1.5,"adaptive_step_multiplier_2":2.0})()); ns+=ss
        while b["low"]<=nb and ob<mop: tk.append(Ticket(direction="BUY",entry_price=nb,opened_idx=i)); ob+=1; bs=dynamic_step(bp,ob,type("Cfg",(),{"adaptive_step_threshold_1":10,"adaptive_step_threshold_2":20,"adaptive_step_multiplier_1":1.5,"adaptive_step_multiplier_2":2.0})()); nb-=bs
        sl=sorted([t for t in tk if t.direction=="SELL"],key=lambda t:t.entry_price,reverse=True)
        while len(sl)>gap and b["low"]<=sl[gap].entry_price: o=sl[0]; r=sl[gap].entry_price; rl.append(unit_pnl_usd(sym,"SELL",o.entry_price,r+(b["low"]-r)*alpha,sp)); tk.remove(o); sl=sorted([t for t in tk if t.direction=="SELL"],key=lambda t:t.entry_price,reverse=True)
        bl=sorted([t for t in tk if t.direction=="BUY"],key=lambda t:t.entry_price)
        while len(bl)>gap and b["high"]>=bl[gap].entry_price: o=bl[0]; r=bl[gap].entry_price; rl.append(unit_pnl_usd(sym,"BUY",o.entry_price,r+(b["high"]-r)*alpha,sp)); tk.remove(o); bl=sorted([t for t in tk if t.direction=="BUY"],key=lambda t:t.entry_price)
        if not tk and abs(b["close"]-a)>=bp: a=b["close"]; ns=a+bp; nb=a-bp
    fl=[unit_pnl_usd(sym,t.direction,t.entry_price,bars[-1]["close"],sp) for t in tk]
    return {"combined":sum(rl)+sum(fl),"realized":sum(rl),"closes":len(rl)}


def main():
    mt5.initialize()
    configs = [
        ("GBPUSD", 1.0, 3, 0.5, 20),
        ("GBPUSD", 0.75, 3, 0.5, 20),
        ("GBPUSD", 1.0, 4, 0.5, 20),
        ("GBPUSD", 1.0, 3, 0.5, 15),
        ("GBPUSD", 1.0, 3, 0.5, 25),
        ("GBPUSD", 1.0, 3, 0.75, 20),
        ("GBPUSD", 0.75, 3, 0.75, 20),
        ("GBPUSD", 1.0, 4, 0.75, 20),
        ("GBPUSD", 1.25, 3, 0.5, 20),
        ("GBPUSD", 0.5, 3, 0.5, 20),
        ("EURUSD", 0.5, 3, 0.5, 20),
        ("EURUSD", 0.75, 3, 0.5, 20),
        ("EURUSD", 1.0, 3, 0.5, 20),
        ("EURUSD", 1.0, 4, 0.5, 20),
        ("EURUSD", 1.0, 3, 0.5, 15),
        ("EURUSD", 1.0, 3, 0.75, 20),
        ("EURUSD", 0.75, 4, 0.5, 20),
        ("EURUSD", 0.5, 3, 0.5, 20),
        ("NZDUSD", 0.5, 3, 0.5, 12),
        ("NZDUSD", 0.75, 3, 0.5, 12),
        ("NZDUSD", 1.0, 3, 0.5, 12),
        ("NZDUSD", 1.0, 3, 0.5, 15),
        ("NZDUSD", 1.0, 4, 0.5, 12),
        ("NZDUSD", 0.75, 3, 0.75, 12),
        ("NZDUSD", 0.5, 3, 0.75, 12),
        ("NZDUSD", 1.0, 3, 0.75, 12),
    ]
    
    print(f"\n{'='*70}")
    print(f"  Targeted Fine-Tune Sweep — 26 configs around known optima")
    print(f"{'='*70}")
    
    rows = []
    for sym, step, gap, alpha, mop in configs:
        info = mt5.symbol_info(sym)
        bars = load_bars(sym, 60)
        t0 = time.time()
        r = sim(sym, bars, info, step, gap, alpha, mop)
        elapsed = time.time() - t0
        rows.append({"symbol":sym,"step":step,"gap":gap,"alpha":alpha,"mop":mop,"combined":r["combined"],"realized":r["realized"],"closes":r["closes"]})
        print(f"  {sym} step={step} gap={gap} α={alpha} mop={mop}: ${r['combined']:>10,.2f} ({r['closes']}c) [{elapsed:.1f}s]")
    
    print(f"\n{'='*70}")
    for sym in ["GBPUSD","EURUSD","NZDUSD"]:
        sr = [r for r in rows if r["symbol"]==sym]
        best = max(sr, key=lambda r: r["combined"])
        print(f"\n  {sym} BEST: step={best['step']} gap={best['gap']} α={best['alpha']} mop={best['mop']}: ${best['combined']:,.2f} ({best['closes']}c)")
    
    total_best = sum(max((r for r in rows if r["symbol"]==s), key=lambda r: r["combined"])["combined"] for s in ["GBPUSD","EURUSD","NZDUSD"])
    print(f"\n  COMBINED BEST: ${total_best:,.2f}")
    
    out = ROOT / "reports" / "fine_tune_sweep.csv"
    with out.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=rows[0].keys())
        w.writeheader(); w.writerows(rows)
    
    mt5.shutdown()


if __name__ == "__main__":
    raise SystemExit(main())
