#!/usr/bin/env python3
"""
VOLUME SWEEP — Can we increase from 0.01 to 0.02, 0.05 lots safely?

Tests: 0.01, 0.02, 0.03, 0.05, 0.10 lots on GBPUSD M5 cascade
Measures: $/hr scaling, worst equity, max floating loss
"""
import MetaTrader5 as mt5
import sys
sys.path.insert(0, 'scripts')

mt5.initialize()
days = 7

def compute_ema(bars, period):
    if len(bars) < period: return [0.0]*len(bars)
    e = [0.0]*len(bars); m = 2.0/(period+1)
    e[period-1] = sum(bars[i]["close"] for i in range(period))/period
    for i in range(period, len(bars)): e[i] = (bars[i]["close"]-e[i-1])*m+e[i-1]
    return e

def run_with_volume(symbol, bars, step, mo, volume, counter_on):
    info = mt5.symbol_info(symbol)
    spread_px = info.spread * info.point
    contract = info.trade_contract_size
    
    emas = {p: compute_ema(bars, p) for p in [3,12,24,64,128,500]}
    tickets = []
    realized = 0.0
    closes = 0
    worst_float = 0.0
    worst_equity = 50.0
    peak_equity = 50.0
    anchor = bars[0]["close"]
    nsl = 1; nbl = 1
    
    for idx in range(1, len(bars)):
        bar = bars[idx]
        span = abs(emas[3][idx] - emas[500][idx])
        compressed = span <= (step*3.0)
        trend_up = emas[3][idx] > emas[12][idx] > emas[24][idx] > emas[64][idx] and span >= (step*4.0)
        trend_down = emas[3][idx] < emas[12][idx] < emas[24][idx] < emas[64][idx] and span >= (step*4.0)
        
        if compressed: s = max(step*0.75, spread_px*3)
        elif trend_up or trend_down: s = step*1.5
        else: s = step
        sd = 2 if trend_up else 1
        bd = 2 if trend_down else 1
        
        osc = sum(1 for t in tickets if t["dir"]=="SELL")
        while bar["high"] >= anchor+(nsl*s) and osc < mo:
            if sd<=1 or nsl%sd==0:
                tickets.append({"dir":"SELL","entry":anchor+(nsl*s),"ctr":False})
                osc += 1
            nsl += 1
        obc = sum(1 for t in tickets if t["dir"]=="BUY")
        while bar["low"] <= anchor-(nbl*s) and obc < mo:
            if bd<=1 or nbl%bd==0:
                tickets.append({"dir":"BUY","entry":anchor-(nbl*s),"ctr":False})
                obc += 1
            nbl += 1
        
        bid = bar["low"]; ask = bar["high"]
        floating = 0.0
        for t in tickets:
            if t["dir"]=="SELL": floating += (t["entry"]-ask)*contract*volume
            else: floating += (bid-t["entry"])*contract*volume
        if floating < worst_float: worst_float = floating
        equity = 50.0 + realized + floating
        if equity > peak_equity: peak_equity = equity
        if equity < worst_equity: worst_equity = equity
        
        sl = sorted([t for t in tickets if t["dir"]=="SELL"], key=lambda t: t["entry"], reverse=True)
        if sl and bar["low"] <= sl[-1]["entry"]:
            for t in list(sl):
                pnl = (t["entry"]-bar["low"])*contract*volume
                realized += pnl
                tickets.remove(t)
                closes += 1
            if counter_on:
                obo = sum(1 for t in tickets if t["dir"]=="BUY")
                for cl in range(1,3):
                    if obo >= mo: break
                    tickets.append({"dir":"BUY","entry":bar["low"]-(cl-1)*s*0.5,"ctr":True})
                    obo += 1
        
        bl = sorted([t for t in tickets if t["dir"]=="BUY"], key=lambda t: t["entry"])
        if bl and bar["high"] >= bl[-1]["entry"]:
            for t in list(bl):
                pnl = (bar["high"]-t["entry"])*contract*volume
                realized += pnl
                tickets.remove(t)
                closes += 1
            if counter_on:
                oso = sum(1 for t in tickets if t["dir"]=="SELL")
                for cl in range(1,3):
                    if oso >= mo: break
                    tickets.append({"dir":"SELL","entry":bar["high"]+(cl-1)*s*0.5,"ctr":True})
                    oso += 1
        
        for t in list(tickets):
            if not t.get("ctr"): continue
            if t["dir"]=="BUY" and bar["high"] >= t["entry"]+s:
                realized += (bar["high"]-t["entry"])*contract*volume
                tickets.remove(t); closes += 1
            elif t["dir"]=="SELL" and bar["low"] <= t["entry"]-s:
                realized += (t["entry"]-bar["low"])*contract*volume
                tickets.remove(t); closes += 1
        
        if not tickets and abs(bar["close"]-anchor) >= s:
            anchor = bar["close"]; nsl = 1; nbl = 1
    
    total_hrs = len(bars)*300/3600
    per_hr = realized / total_hrs if total_hrs > 0 else 0
    return {"per_hr": per_hr, "closes": closes, "worst_eq": worst_equity,
            "peak_eq": peak_equity, "worst_float": worst_float, "peak_eq": peak_equity}

def main():
    print("="*80)
    print("VOLUME SWEEP — GBPUSD M5 cascade, counter-trend")
    print("="*80)
    
    sym = "GBPUSD"
    step = 0.00005
    bars_raw = mt5.copy_rates_from_pos(sym, mt5.TIMEFRAME_M5, 0, 24*12*days)
    if bars_raw is None:
        print("NO DATA")
        mt5.shutdown()
        return
    bars = [{"time":int(r[0]),"open":float(r[1]),"high":float(r[2]),"low":float(r[3]),"close":float(r[4])} for r in bars_raw]
    
    volumes = [0.01, 0.02, 0.03, 0.05, 0.10]
    
    print(f"\n{'Volume':>7} {'$/hr':>9} {'Closes':>7} {'Worst Eq':>9} {'Peak Eq':>9} {'Worst $':>10} {'Status':>7}")
    print("-" * 65)
    
    for vol in volumes:
        for mo in [30, 60]:
            r = run_with_volume(sym, bars, step, mo, vol, counter_on=True)
            survived = "[SAFE]" if r["worst_eq"] > 0 else "[BLOWUP]"
            print(f"  {vol:>5.2f}  mo={mo:>2}  ${r['per_hr']:>8.2f} {r['closes']:>7} ${r['worst_eq']:>8.2f} ${r['peak_eq']:>8.2f} ${r['worst_float']:>9.2f}  {survived}")
    
    mt5.shutdown()

if __name__ == "__main__":
    main()
