#!/usr/bin/env python3
"""Penetration lattice — incremental saver sweep. Saves after each config."""
from __future__ import annotations
import csv, time, os
from dataclasses import dataclass
import MetaTrader5 as mt5

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "reports", "penetration_focused_sweep.csv")

SYMBOLS = ["EURUSD","GBPUSD","USDJPY","USDCHF","NZDUSD","EURJPY","AUDUSD","CADJPY"]

@dataclass(frozen=True)
class Cfg:
    step_pips: float; hard_stop_threshold_usd: float; anchor_reset_pips: float = 3.0
    max_open_per_side: int = 50; vwap_lookback: int = 20
    adaptive_threshold_1: int = 10; adaptive_threshold_2: int = 20
    adaptive_mult_1: float = 1.5; adaptive_mult_2: float = 2.0

def pip_sz(si):
    p = float(si.point or 0.0); d = int(si.digits or 0)
    return p*10.0 if d in (3,5) else p
def spx(si): return float(si.spread or 0.0)*float(si.point or 0.0)
def upnl(sym, d, ep, xp, s):
    ot = mt5.ORDER_TYPE_BUY if d=="BUY" else mt5.ORDER_TYPE_SELL
    g = mt5.order_calc_profit(ot, sym, 0.01, ep, xp)
    if g is None: return 0.0
    if d=="BUY": sc = mt5.order_calc_profit(ot, sym, 0.01, ep+s, ep)
    else: sc = mt5.order_calc_profit(ot, sym, 0.01, ep, ep+s)
    return float(g)-abs(float(sc or 0.0))
def ds(bs, oc):
    if oc>=20: return bs*2.0
    elif oc>=10: return bs*1.5
    return bs
def vw(bars, idx, lb):
    s=max(0,idx-lb); w=bars[s:idx]
    if not w: return bars[idx-1]["close"]
    cv=sum(b["close"]*b["tick_volume"] for b in w); v=sum(b["tick_volume"] for b in w)
    return cv/v if v else w[-1]["close"]

@dataclass
class T: d:str; e:float; i:int

def sim(sym, bars, info, cfg):
    pip=pip_sz(info); sp=spx(info); bsp=cfg.step_pips*pip; rpx=cfg.anchor_reset_pips*pip
    a=bars[0]["close"]; nsl=a+bsp; nbl=a-bsp
    ot=[]; rp=[]; up=[]; mo=0; ar=0; uf=0; wfs=0.0
    for idx in range(1, len(bars)):
        bar=bars[idx]
        ob=sum(1 for t in ot if t.d=="BUY"); os_=sum(1 for t in ot if t.d=="SELL")
        css=ds(bsp, os_); cbs=ds(bsp, ob)
        while bar["high"]>=nsl and os_<cfg.max_open_per_side:
            ot.append(T("SELL", nsl, idx)); nsl+=css; os_+=1; css=ds(bsp, os_)
        while bar["low"]<=nbl and ob<cfg.max_open_per_side:
            ot.append(T("BUY", nbl, idx)); nbl-=cbs; ob+=1; cbs=ds(bsp, ob)
        sls=sorted((t for t in ot if t.d=="SELL"), key=lambda t: t.e, reverse=True)
        while len(sls)>=2 and bar["low"]<=sls[1].e:
            cr=bar["low"]; pr=[t for t in sls if upnl(sym,"SELL",t.e,cr,sp)>0]
            if not pr: break
            for t in pr: rp.append(upnl(sym,"SELL",t.e,cr,sp)); ot.remove(t)
            sls=sorted((t for t in ot if t.d=="SELL"), key=lambda t: t.e, reverse=True)
        bys=sorted((t for t in ot if t.d=="BUY"), key=lambda t: t.e)
        while len(bys)>=2 and bar["high"]>=bys[1].e:
            cr=bar["high"]; pr=[t for t in bys if upnl(sym,"BUY",t.e,cr,sp)>0]
            if not pr: break
            for t in pr: rp.append(upnl(sym,"BUY",t.e,cr,sp)); ot.remove(t)
            bys=sorted((t for t in ot if t.d=="BUY"), key=lambda t: t.e)
        if ot:
            fps=[upnl(sym,t.d,t.e,bar["close"],sp) for t in ot]
            wp=min(fps); wfs=min(wfs, wp)
            if wp<=cfg.hard_stop_threshold_usd:
                for t in list(ot): up.append(upnl(sym,t.d,t.e,bar["close"],sp)); ot.remove(t)
                uf+=1; a=bar["close"]; nsl=a+bsp; nbl=a-bsp
        if not ot:
            ca=vw(bars,idx,cfg.vwap_lookback)
            if abs(bar["close"]-a)>=rpx: a=ca; nsl=a+bsp; nbl=a-bsp; ar+=1
        mo=max(mo, len(ot))
    lc=bars[-1]["close"]; fp=[upnl(sym,t.d,t.e,lc,sp) for t in ot]
    rn=sum(rp); un=sum(up); fn=sum(fp)
    return {"symbol":sym,"step":cfg.step_pips,"stop":cfg.hard_stop_threshold_usd,
            "rc":len(rp),"uc":len(up),
            "wr":round(sum(1 for p in rp if p>0)/len(rp)*100,1) if rp else 0.0,
            "rn":round(rn,3),"un":round(un,3),"left":len(ot),"fn":round(fn,3),
            "wfs":round(wfs,3),"cn":round(rn+un+fn,3),"mo":mo,"uf":uf,"ar":ar}

def save_rows(rows, is_first):
    mode = "w" if is_first else "a"
    with open(OUT, mode, newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        if is_first: w.writeheader()
        w.writerows(rows)

def main():
    steps = [0.75, 1.0, 0.25]
    stops = [-1.0, -1.5]  # missing from previous run
    days = 20
    if not mt5.initialize(): print("MT5 init failed"); return 1
    try:
        first = not os.path.exists(OUT)
        for step_p in steps:
            for stop_u in stops:
                t0=time.time(); cfg=Cfg(step_pips=step_p, hard_stop_threshold_usd=stop_u)
                print(f"=== step={step_p} stop={stop_u} ===", flush=True)
                rows=[]; ct=0.0
                for sym in SYMBOLS:
                    info=mt5.symbol_info(sym)
                    if info is None: continue
                    rates=mt5.copy_rates_from_pos(sym, mt5.TIMEFRAME_M1, 0, 1440*days)
                    if rates is None or len(rates)==0: continue
                    bars=[{"time":int(r[0]),"open":float(r[1]),"high":float(r[2]),"low":float(r[3]),"close":float(r[4]),"tick_volume":int(r[5])} for r in rates]
                    row=sim(sym, bars, info, cfg); rows.append(row); ct+=row["cn"]
                    print(f"  {sym:<7} banked={row['rn']:+.2f} unwind={row['un']:+.2f} combined={row['cn']:+.2f} fires={row['uf']:>3}", flush=True)
                print(f"  COMBINED: ${ct:+.2f} ({time.time()-t0:.1f}s)", flush=True)
                save_rows(rows, first); first=False
    finally:
        mt5.shutdown()
    return 0

if __name__=="__main__": raise SystemExit(main())
