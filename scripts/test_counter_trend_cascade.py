#!/usr/bin/env python3
"""COUNTER-TREND DURING CASCADE on EMA ribbon"""
import MetaTrader5 as mt5
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).parent))
from penetration_lattice_lab_v2 import spread_price, unit_pnl_usd
from dataclasses import dataclass, field

mt5.initialize()

@dataclass
class Ticket:
    direction: str
    entry_price: float
    opened_idx: int
    from_rearm: bool = False

@dataclass 
class SymbolState:
    symbol: str
    realized_closes: int = 0
    realized_net_usd: float = 0.0
    anchor_resets: int = 0
    max_open_total: int = 0
    counter_opens: int = 0

def compute_ema(bars, period):
    if len(bars) < period: return [0.0]*len(bars)
    ema = [0.0]*len(bars)
    m = 2.0/(period+1)
    ema[period-1] = sum(bars[i]["close"] for i in range(period))/period
    for i in range(period, len(bars)):
        ema[i] = (bars[i]["close"]-ema[i-1])*m+ema[i-1]
    return ema

def run_counter_cascade(symbol, bars, cfg):
    if not bars or len(bars)<500: return SymbolState(symbol=symbol)
    info = mt5.symbol_info(symbol)
    if not info: return SymbolState(symbol=symbol)
    spread_px = spread_price(info)
    base_step = cfg.get("base_step", 50.0)
    max_open = cfg.get("max_open_per_side", 60)
    hold_frontier = cfg.get("hold_frontier", 1)
    counter_on_cascade = cfg.get("counter_on_cascade", False)
    counter_levels = cfg.get("counter_levels", 1)
    
    emas = {p: compute_ema(bars, p) for p in [3,12,24,64,128,500]}
    tickets = []
    realized = 0.0
    closes = 0
    max_open_total = 0
    anchor_resets = 0
    last_bar_time = int(bars[0]["time"])
    anchor = bars[0]["close"]
    nsl = 1
    nbl = 1
    counter_opens = 0
    
    for idx in range(1, len(bars)):
        bar = bars[idx]
        if int(bar["time"]) <= last_bar_time: continue
        last_bar_time = int(bar["time"])
        
        ema_3 = emas[3][idx]; ema_12 = emas[12][idx]; ema_24 = emas[24][idx]
        ema_64 = emas[64][idx]; ema_500 = emas[500][idx]
        if ema_500 == 0.0: continue
        
        span = abs(ema_3 - ema_500)
        compressed = span <= (base_step*3.0)
        trend_up = ema_3 > ema_12 > ema_24 > ema_64 and span >= (base_step*4.0)
        trend_down = ema_3 < ema_12 < ema_24 < ema_64 and span >= (base_step*4.0)
        
        if compressed: step = max(base_step*0.75, 0.01)
        elif trend_up or trend_down: step = base_step*1.5
        else: step = base_step
        sd = 2 if trend_up else 1
        bd = 2 if trend_down else 1
        
        # Opens
        osc = sum(1 for t in tickets if t.direction=="SELL")
        while bar["high"] >= anchor+(nsl*step) and osc < max_open:
            if sd<=1 or nsl%sd==0:
                tickets.append(Ticket(direction="SELL", entry_price=anchor+(nsl*step), opened_idx=idx))
                osc += 1
            nsl += 1
        obc = sum(1 for t in tickets if t.direction=="BUY")
        while bar["low"] <= anchor-(nbl*step) and obc < max_open:
            if bd<=1 or nbl%bd==0:
                tickets.append(Ticket(direction="BUY", entry_price=anchor-(nbl*step), opened_idx=idx))
                obc += 1
            nbl += 1
        
        # SELL CASCADE
        sl = sorted([t for t in tickets if t.direction=="SELL"], key=lambda t: t.entry_price, reverse=True)
        if sl and bar["low"] <= sl[-1].entry_price:
            tc = sl[:-hold_frontier] if hold_frontier>0 and len(sl)>hold_frontier else sl
            for t in tc:
                pnl = unit_pnl_usd(symbol, "SELL", t.entry_price, bar["low"], spread_px)
                realized += pnl
                tickets.remove(t)
                closes += 1
            # COUNTER-TREND: open BUYs during SELL cascade
            if counter_on_cascade:
                obo = sum(1 for t in tickets if t.direction=="BUY")
                for cl in range(1, counter_levels+1):
                    if obo >= max_open: break
                    entry = anchor - cl*step
                    if bar["low"] <= entry:
                        tickets.append(Ticket(direction="BUY", entry_price=entry, opened_idx=idx))
                        obo += 1
                        counter_opens += 1
        
        # BUY CASCADE
        bl = sorted([t for t in tickets if t.direction=="BUY"], key=lambda t: t.entry_price)
        if bl and bar["high"] >= bl[-1].entry_price:
            tc = bl[:-hold_frontier] if hold_frontier>0 and len(bl)>hold_frontier else bl
            for t in tc:
                pnl = unit_pnl_usd(symbol, "BUY", t.entry_price, bar["high"], spread_px)
                realized += pnl
                tickets.remove(t)
                closes += 1
            if counter_on_cascade:
                oso = sum(1 for t in tickets if t.direction=="SELL")
                for cl in range(1, counter_levels+1):
                    if oso >= max_open: break
                    entry = anchor + cl*step
                    if bar["high"] >= entry:
                        tickets.append(Ticket(direction="SELL", entry_price=entry, opened_idx=idx))
                        oso += 1
                        counter_opens += 1
        
        if not tickets and abs(bar["close"]-anchor) >= step:
            anchor = bar["close"]; nsl = 1; nbl = 1
            anchor_resets += 1
        
        max_open_total = max(max_open_total, len(tickets))
    
    return SymbolState(symbol=symbol, realized_closes=closes, realized_net_usd=round(realized,3),
                       anchor_resets=anchor_resets, max_open_total=max_open_total, counter_opens=counter_opens)

def main():
    symbol = "BTCUSD"; days = 30
    bars = [{"time":int(r[0]),"open":float(r[1]),"high":float(r[2]),"low":float(r[3]),"close":float(r[4])} 
            for r in mt5.copy_rates_from_pos(symbol, mt5.TIMEFRAME_M15, 0, 24*4*days)]
    total_hrs = len(bars)*15/60
    print(f"COUNTER-TREND DURING CASCADE: {symbol} M15, {days} days\n")
    
    configs = []
    for step in [50, 75]:
        for ctr in [False, True]:
            for cl in [1, 2, 3] if ctr else [0]:
                label = f"step={step} counter={'Y' if ctr else 'N'} levels={cl}"
                configs.append({"label": label, "base_step": float(step), "counter_on_cascade": ctr, 
                               "counter_levels": cl, "hold_frontier": 1, "max_open_per_side": 60})
    
    results = []
    for cfg in configs:
        s = run_counter_cascade(symbol, bars, cfg)
        net = s.realized_net_usd; closes = s.realized_closes
        per_hr = net/total_hrs; avg = net/closes if closes>0 else 0
        results.append((cfg["label"], {"net":net,"closes":closes,"per_hr":per_hr,"avg":avg,"counter":s.counter_opens}))
        print(f"  {cfg['label']}: ${per_hr:.2f}/hr, {closes}c, ${avg:.2f}/close, {s.counter_opens} counter-opens")
    
    results.sort(key=lambda x: x[1]["per_hr"], reverse=True)
    print(f"\n{'Config':<40} {'$/hr':>9} {'Closes':>7} {'$/close':>9} {'Counter':>8}")
    print("-" * 75)
    for label, r in results:
        print(f"{label:<40} ${r['per_hr']:>8.2f} {r['closes']:>7} ${r['avg']:>8.2f} {r['counter']:>8}")
    
    mt5.shutdown()

if __name__ == "__main__":
    main()
