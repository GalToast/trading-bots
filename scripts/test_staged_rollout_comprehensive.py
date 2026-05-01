#!/usr/bin/env python3
"""
COMPREHENSIVE $50 STAGED ROLLOUT TEST — All FX, all max_open levels

Tests EVERY combination to find the EXACT safe parameters for each stage.
"""
import MetaTrader5 as mt5
import sys
sys.path.insert(0, 'scripts')

mt5.initialize()
days = 7
print("="*90)
print(f"COMPREHENSIVE $50 SURVIVABILITY — ALL FX, ALL MAX_OPEN — {days} days")
print("="*90)

fx_symbols = ["GBPUSD", "EURUSD", "AUDUSD", "USDCAD", "NZDUSD", "USDJPY"]
max_open_tests = [5, 10, 20, 30, 60]
step_map = {
    "GBPUSD": 0.00005, "EURUSD": 0.00005, "AUDUSD": 0.00005,
    "USDCAD": 0.00005, "NZDUSD": 0.00005, "USDJPY": 0.001,
}

def compute_ema(bars, period):
    if len(bars) < period: return [0.0]*len(bars)
    e = [0.0]*len(bars); m = 2.0/(period+1)
    e[period-1] = sum(bars[i]["close"] for i in range(period))/period
    for i in range(period, len(bars)): e[i] = (bars[i]["close"]-e[i-1])*m+e[i-1]
    return e

all_results = {}

for sym in fx_symbols:
    print(f"\n{'='*60}")
    print(f"=== {sym} ===")
    print(f"{'='*60}")
    
    info = mt5.symbol_info(sym)
    spread_pips = info.spread * (10 if info.digits in [3,5] else 1)
    contract = info.trade_contract_size
    volume = 0.01
    step = step_map[sym]
    
    # Load M5 and M15
    b5_raw = mt5.copy_rates_from_pos(sym, mt5.TIMEFRAME_M5, 0, 24*12*days)
    b15_raw = mt5.copy_rates_from_pos(sym, mt5.TIMEFRAME_M15, 0, 24*4*days)
    
    if b5_raw is None or b15_raw is None:
        print(f"  NO DATA")
        continue
    
    b5 = [{"time":int(r[0]),"open":float(r[1]),"high":float(r[2]),"low":float(r[3]),"close":float(r[4])} for r in b5_raw]
    b15 = [{"time":int(r[0]),"open":float(r[1]),"high":float(r[2]),"low":float(r[3]),"close":float(r[4])} for r in b15_raw]
    total_hrs = len(b15)*15/3600
    
    sym_results = []
    
    for mo in max_open_tests:
        # Test M5 + M15 combined with counter-trend cascade
        for tf_name, bars, tf_sec in [("M5", b5, 300), ("M15", b15, 900)]:
            emas = {p: compute_ema(bars, p) for p in [3,12,24,64,128,500]}
            tickets = []
            realized = 0.0
            closes = 0
            worst_float = 0.0
            peak_equity = 50.0
            worst_equity = 50.0
            max_open_seen = 0
            anchor = bars[0]["close"]
            nsl = 1; nbl = 1
            last_bar_time = int(bars[0]["time"])
            
            for idx in range(1, len(bars)):
                bar = bars[idx]
                if int(bar["time"]) <= last_bar_time: continue
                last_bar_time = int(bar["time"])
                
                span = abs(emas[3][idx] - emas[500][idx])
                compressed = span <= (step*3.0)
                trend_up = emas[3][idx] > emas[12][idx] > emas[24][idx] > emas[64][idx] and span >= (step*4.0)
                trend_down = emas[3][idx] < emas[12][idx] < emas[24][idx] < emas[64][idx] and span >= (step*4.0)
                
                if compressed: s = max(step*0.75, info.spread*info.point*3)
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
                
                # FLOATING
                bid = bar["low"]; ask = bar["high"]
                floating = 0.0
                for t in tickets:
                    if t["dir"]=="SELL": floating += (t["entry"]-ask)*contract*volume
                    else: floating += (bid-t["entry"])*contract*volume
                if floating < worst_float: worst_float = floating
                equity = 50.0 + realized + floating
                if equity > peak_equity: peak_equity = equity
                if equity < worst_equity: worst_equity = equity
                
                # CASCADE SELL
                sl = sorted([t for t in tickets if t["dir"]=="SELL"], key=lambda t: t["entry"], reverse=True)
                if sl and bar["low"] <= sl[-1]["entry"]:
                    for t in list(sl):
                        pnl = (t["entry"]-bar["low"])*contract*volume
                        realized += pnl
                        tickets.remove(t)
                        closes += 1
                    # COUNTER
                    obo = sum(1 for t in tickets if t["dir"]=="BUY")
                    for cl in range(1,3):
                        if obo >= mo: break
                        tickets.append({"dir":"BUY","entry":bar["low"]-(cl-1)*s*0.5,"ctr":True})
                        obo += 1
                
                # CASCADE BUY
                bl = sorted([t for t in tickets if t["dir"]=="BUY"], key=lambda t: t["entry"])
                if bl and bar["high"] >= bl[-1]["entry"]:
                    for t in list(bl):
                        pnl = (bar["high"]-t["entry"])*contract*volume
                        realized += pnl
                        tickets.remove(t)
                        closes += 1
                    obo = sum(1 for t in tickets if t["dir"]=="SELL")
                    for cl in range(1,3):
                        if obo >= mo: break
                        tickets.append({"dir":"SELL","entry":bar["high"]+(cl-1)*s*0.5,"ctr":True})
                        obo += 1
                
                # Close counter
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
                
                max_open_seen = max(max_open_seen, len(tickets))
            
            tf_hrs = len(bars)*tf_sec/3600
            per_hr = realized / tf_hrs if tf_hrs > 0 else 0
            survived = worst_equity > 0
            
            sym_results.append({
                "tf": tf_name, "mo": mo, "per_hr": per_hr, "closes": closes,
                "worst_float": worst_float, "worst_equity": worst_equity,
                "peak_equity": peak_equity, "max_open": max_open_seen, "survived": survived,
            })
            
            flag = "[SAFE]" if survived else "[BLOWUP]"
            print(f"  {tf_name} mo={mo:>2}: ${per_hr:>8.2f}/hr  {closes:>6}c  worst_eq=${worst_equity:>7.2f}  max_open={max_open_seen:>2}  {flag}")
    
    all_results[sym] = sym_results

# ===== BUILD THE STAGED PLAN =====
print(f"\n{'='*90}")
print(f"STAGED $50 ROLLOUT — OPTIMAL PARAMETERS")
print(f"{'='*90}")

stages = [
    {"name": "STAGE 1 ($50)", "balance": 50, "min_eq": 25.0},
    {"name": "STAGE 2 ($100)", "balance": 100, "min_eq": 50.0},
    {"name": "STAGE 3 ($250)", "balance": 250, "min_eq": 125.0},
]

for stage in stages:
    print(f"\n{stage['name']} — Min equity floor: ${stage['min_eq']:.0f}")
    print(f"  {'Symbol':<10} {'TF':<4} {'max_open':>8} {'$/hr':>8} {'Closes':>7} {'Worst Eq':>9} {'Status':>7}")
    print(f"  {'-'*60}")
    
    for sym in fx_symbols:
        if sym not in all_results: continue
        for r in all_results[sym]:
            # Scale worst_equity for this balance
            scale = stage["balance"] / 50.0
            scaled_worst_eq = 50.0 + (r["worst_equity"] - 50.0) * scale
            # Actually: worst_equity is absolute, scales linearly with balance
            # If $50 → $28.76 worst, then $100 → ~$57.52 worst
            scaled_worst_eq = r["worst_equity"] * scale
            
            if scaled_worst_eq >= stage["min_eq"]:
                print(f"  {sym:<10} {r['tf']:<4} {r['mo']:>8} ${r['per_hr']*scale:>7.2f} {r['closes']:>7} ${scaled_worst_eq:>8.2f} {'SAFE':>7}")

# Summary
print(f"\n{'='*90}")
print(f"SUMMARY — MAX SAFE $/hr PER STAGE")
print(f"{'='*90}")

for stage in stages:
    scale = stage["balance"] / 50.0
    total_hr = 0
    for sym in fx_symbols:
        if sym not in all_results: continue
        # Find best safe config for this symbol at this stage
        best_safe = None
        best_hr = 0
        for r in all_results[sym]:
            scaled_worst = r["worst_equity"] * scale
            if scaled_worst >= stage["min_eq"]:
                if r["per_hr"] > best_hr:
                    best_hr = r["per_hr"]
                    best_safe = r
        if best_safe:
            total_hr += best_safe["per_hr"] * scale
            print(f"  {stage['name']}: {sym} {best_safe['tf']} mo={best_safe['mo']} → ${best_safe['per_hr']*scale:.2f}/hr")
    
    print(f"  {'='*50}")
    print(f"  {stage['name']} TOTAL: ${total_hr:.2f}/hr → ${total_hr*24:.2f}/day → ${total_hr*720:.2f}/month")

mt5.shutdown()
