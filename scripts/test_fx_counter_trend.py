#!/usr/bin/env python3
"""
FX COUNTER-TREND CASCADE — Opening opposite positions during cascade

On FX, spread = $0.0001 (essentially free). 
When SELLs cascade-close at bar low, immediately open BUYs at bar low.
These BUYs capture the reversal continuation.

Previous test on BTC: FAILED (spread=$180, step=$50, spread 3.6x step)
On FX: spread=$0.0001, step=0.00005, spread 2x step — might work!
"""
import MetaTrader5 as mt5
import sys
sys.path.insert(0, 'scripts')
from penetration_lattice_lab_v2 import spread_price, unit_pnl_usd
from dataclasses import dataclass

mt5.initialize()

@dataclass
class Ticket:
    direction: str
    entry_price: float
    opened_idx: int
    from_counter: bool = False

def compute_ema(bars, period):
    if len(bars) < period: return [0.0]*len(bars)
    ema = [0.0]*len(bars)
    m = 2.0/(period+1)
    ema[period-1] = sum(bars[i]["close"] for i in range(period))/period
    for i in range(period, len(bars)):
        ema[i] = (bars[i]["close"]-ema[i-1])*m+ema[i-1]
    return ema

def run_counter_cascade_fx(symbol, bars, cfg):
    if not bars or len(bars)<500: return None
    info = mt5.symbol_info(symbol)
    if not info: return None
    spread_px = spread_price(info)
    base_step = cfg["base_step"]
    max_open = cfg.get("max_open_per_side", 60)
    hold_frontier = cfg.get("hold_frontier", 0)
    counter_on = cfg.get("counter_on_cascade", False)
    counter_close_step = cfg.get("counter_close_step", 1)  # Steps for counter close
    tf_seconds = cfg.get("tf_seconds", 300)
    
    emas = {p: compute_ema(bars, p) for p in [3,12,24,64,128,500]}
    tickets = []
    realized = 0.0
    closes = 0
    counter_closes = 0
    anchor_resets = 0
    last_bar_time = int(bars[0]["time"])
    anchor = bars[0]["close"]
    nsl = 1
    nbl = 1
    max_open_total = 0

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
        
        if compressed: step = max(base_step*0.75, spread_px*3)
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
        
        # CASCADE SELL CLOSE + COUNTER BUY OPEN
        sl = sorted([t for t in tickets if t.direction=="SELL"], key=lambda t: t.entry_price, reverse=True)
        if sl and bar["low"] <= sl[-1].entry_price:
            tc = sl[:-hold_frontier] if hold_frontier>0 and len(sl)>hold_frontier else sl
            for t in tc:
                pnl = unit_pnl_usd(symbol, "SELL", t.entry_price, bar["low"], spread_px)
                realized += pnl
                tickets.remove(t)
                closes += 1
            # COUNTER: open BUYs at bar low during SELL cascade
            if counter_on:
                obo = sum(1 for t in tickets if t.direction=="BUY")
                for cl in range(1, 3):  # Up to 2 counter levels
                    if obo >= max_open: break
                    entry = bar["low"] - (cl-1)*step*0.5  # Slightly below bar low
                    tickets.append(Ticket(direction="BUY", entry_price=entry, opened_idx=idx, from_counter=True))
                    obo += 1
        
        # CASCADE BUY CLOSE + COUNTER SELL OPEN
        bl = sorted([t for t in tickets if t.direction=="BUY"], key=lambda t: t.entry_price)
        if bl and bar["high"] >= bl[-1].entry_price:
            tc = bl[:-hold_frontier] if hold_frontier>0 and len(bl)>hold_frontier else bl
            for t in tc:
                pnl = unit_pnl_usd(symbol, "BUY", t.entry_price, bar["high"], spread_px)
                realized += pnl
                tickets.remove(t)
                closes += 1
            if counter_on:
                oso = sum(1 for t in tickets if t.direction=="SELL")
                for cl in range(1, 3):
                    if oso >= max_open: break
                    entry = bar["high"] + (cl-1)*step*0.5
                    tickets.append(Ticket(direction="SELL", entry_price=entry, opened_idx=idx, from_counter=True))
                    oso += 1
        
        # Close counter positions after N steps of profit
        for t in list(tickets):
            if not t.from_counter: continue
            if t.direction == "BUY" and bar["high"] >= t.entry_price + counter_close_step*step:
                pnl = unit_pnl_usd(symbol, "BUY", t.entry_price, bar["high"], spread_px)
                realized += pnl
                tickets.remove(t)
                closes += 1
                counter_closes += 1
            elif t.direction == "SELL" and bar["low"] <= t.entry_price - counter_close_step*step:
                pnl = unit_pnl_usd(symbol, "SELL", t.entry_price, bar["low"], spread_px)
                realized += pnl
                tickets.remove(t)
                closes += 1
                counter_closes += 1
        
        if not tickets and abs(bar["close"]-anchor) >= step:
            anchor = bar["close"]; nsl = 1; nbl = 1
            anchor_resets += 1
        
        max_open_total = max(max_open_total, len(tickets))
    
    total_hrs = len(bars)*tf_seconds/3600
    net = realized
    per_hr = net/total_hrs if total_hrs > 0 else 0
    avg = net/closes if closes>0 else 0
    
    return {"net": round(net,2), "closes": closes, "per_hr": round(per_hr,2), 
            "avg": round(avg,4), "resets": anchor_resets, "max_open": max_open_total,
            "counter_closes": counter_closes}

def main():
    days = 30
    print("FX COUNTER-TREND CASCADE TEST\n")
    
    symbols = ["GBPUSD", "EURUSD", "USDJPY"]
    
    for sym in symbols:
        print(f"--- {sym} ---", flush=True)
        b5 = [{"time":int(r[0]),"open":float(r[1]),"high":float(r[2]),"low":float(r[3]),"close":float(r[4])} for r in mt5.copy_rates_from_pos(sym, mt5.TIMEFRAME_M5, 0, 24*12*days)]
        b15 = [{"time":int(r[0]),"open":float(r[1]),"high":float(r[2]),"low":float(r[3]),"close":float(r[4])} for r in mt5.copy_rates_from_pos(sym, mt5.TIMEFRAME_M15, 0, 24*4*days)]
        
        info = mt5.symbol_info(sym)
        pip = info.point * 10 if info.digits in [3,5] else info.point
        spread_px = spread_price(info)
        
        print(f"  Spread: {spread_px:.6f} ({spread_px/pip:.1f} pips)")
        
        # Test with and without counter-trend
        for tf_name, bars, tf_sec in [("M5", b5, 300), ("M15", b15, 900)]:
            for counter in [False, True]:
                for step_pips in [0.5, 1.0] if sym != "USDJPY" else [1.0, 2.0]:
                    step = step_pips * pip
                    cfg = {"base_step": step, "hold_frontier": 0, "max_open_per_side": 60,
                           "counter_on_cascade": counter, "counter_close_step": 1, "tf_seconds": tf_sec}
                    r = run_counter_cascade_fx(sym, bars, cfg)
                    if r:
                        flag = " [COUNTER]" if counter else ""
                        print(f"  {tf_name} {step_pips}p{flag}: ${r['per_hr']:.2f}/hr, {r['closes']}c, ${r['avg']:.4f}/close, counter_closes={r['counter_closes']}")
        print()
    
    mt5.shutdown()

if __name__ == "__main__":
    main()
