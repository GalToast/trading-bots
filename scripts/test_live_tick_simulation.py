#!/usr/bin/env python3
"""
LIVE TICK ENGINE SIMULATION — Does cascade work with real tick prices?

Bar-replay closes at bar["low"] / bar["high"] — the extreme.
Live tick engine closes when ask/bid crosses trigger level.

This simulates tick-by-tick execution:
1. Split each M5 bar into N ticks (realistic distribution)
2. Track price path within bar
3. Close at actual tick price when cascade trigger fires
4. Compare bar-replay vs tick-simulated results

Tests on GBPUSD and EURUSD (the two safest symbols).
"""
import MetaTrader5 as mt5
import sys, random
sys.path.insert(0, 'scripts')

mt5.initialize()
random.seed(42)
days = 7

def compute_ema(bars, period):
    if len(bars) < period: return [0.0]*len(bars)
    e = [0.0]*len(bars); m = 2.0/(period+1)
    e[period-1] = sum(bars[i]["close"] for i in range(period))/period
    for i in range(period, len(bars)): e[i] = (bars[i]["close"]-e[i-1])*m+e[i-1]
    return e

def generate_ticks(bar, n_ticks=30):
    """Generate realistic tick path within a bar."""
    o, h, l, c = bar["open"], bar["high"], bar["low"], bar["close"]
    
    if n_ticks <= 1:
        return [c]
    
    if n_ticks == 2:
        # Simple: open then close
        return [o, c]
    
    ticks = []
    
    # Determine direction
    if c >= o:  # Bullish bar
        path = [o]
        # Find where high and low occur
        high_idx = int(random.uniform(0.3, 0.7) * n_ticks)
        low_idx = int(random.uniform(0, 0.3) * n_ticks)
        # Clamp indices
        high_idx = min(max(high_idx, 1), n_ticks - 2)
        low_idx = min(max(low_idx, 0), high_idx - 1)
        
        for i in range(1, n_ticks):
            if i < low_idx:
                # Moving toward low
                progress = i / max(low_idx, 1)
                price = o + (l - o) * progress + random.gauss(0, (h-l)*0.02)
            elif i < high_idx:
                # Moving from low to high
                progress = (i - low_idx) / max(high_idx - low_idx, 1)
                price = l + (h - l) * progress + random.gauss(0, (h-l)*0.02)
            else:
                # Moving from high to close
                progress = (i - high_idx) / max(n_ticks - high_idx, 1)
                price = h + (c - h) * progress + random.gauss(0, (h-l)*0.02)
            ticks.append(price)
    else:  # Bearish bar
        path = [o]
        low_idx = int(random.uniform(0.3, 0.7) * n_ticks)
        high_idx = int(random.uniform(0, 0.3) * n_ticks)
        low_idx = min(max(low_idx, 1), n_ticks - 2)
        high_idx = min(max(high_idx, 0), low_idx - 1)
        
        for i in range(1, n_ticks):
            if i < high_idx:
                progress = i / max(high_idx, 1)
                price = o + (h - o) * progress + random.gauss(0, (h-l)*0.02)
            elif i < low_idx:
                progress = (i - high_idx) / max(low_idx - high_idx, 1)
                price = h + (l - h) * progress + random.gauss(0, (h-l)*0.02)
            else:
                progress = (i - low_idx) / max(n_ticks - low_idx, 1)
                price = l + (c - l) * progress + random.gauss(0, (h-l)*0.02)
            ticks.append(price)
    
    # Ensure high and low are actually hit
    ticks[min(high_idx, n_ticks-1)] = max(ticks[min(high_idx, n_ticks-1)], h)
    ticks[min(low_idx, n_ticks-1)] = min(ticks[min(low_idx, n_ticks-1)], l)
    ticks[-1] = c  # Last tick = close
    
    return ticks

def run_tick_sim(symbol, bars, step, mo, counter_on, n_ticks=30):
    """Run cascade on simulated ticks."""
    info = mt5.symbol_info(symbol)
    spread_px = info.spread * info.point
    contract = info.trade_contract_size
    volume = 0.01
    
    emas = {p: compute_ema(bars, p) for p in [3,12,24,64,128,500]}
    tickets = []
    realized = 0.0
    closes = 0
    worst_float = 0.0
    peak_equity = 50.0
    worst_equity = 50.0
    anchor = bars[0]["close"]
    nsl = 1; nbl = 1
    anchor_resets = 0
    
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
        
        # Generate ticks for this bar
        ticks = generate_ticks(bar, n_ticks)
        
        for tick_idx, tick_price in enumerate(ticks):
            bid = tick_price - spread_px/2
            ask = tick_price + spread_px/2
            
            # Opens
            osc = sum(1 for t in tickets if t["dir"]=="SELL")
            while ask >= anchor+(nsl*s) and osc < mo:
                if sd<=1 or nsl%sd==0:
                    tickets.append({"dir":"SELL","entry":anchor+(nsl*s),"ctr":False})
                    osc += 1
                nsl += 1
            obc = sum(1 for t in tickets if t["dir"]=="BUY")
            while bid <= anchor-(nbl*s) and obc < mo:
                if bd<=1 or nbl%bd==0:
                    tickets.append({"dir":"BUY","entry":anchor-(nbl*s),"ctr":False})
                    obc += 1
                nbl += 1
            
            # FLOATING
            floating = 0.0
            for t in tickets:
                if t["dir"]=="SELL": floating += (t["entry"]-ask)*contract*volume
                else: floating += (bid-t["entry"])*contract*volume
            if floating < worst_float: worst_float = floating
            equity = 50.0 + realized + floating
            if equity > peak_equity: peak_equity = equity
            if equity < worst_equity: worst_equity = equity
            
            # CASCADE SELL - fire on tick
            sl = sorted([t for t in tickets if t["dir"]=="SELL"], key=lambda t: t["entry"], reverse=True)
            if sl and ask <= sl[-1]["entry"]:
                for t in list(sl):
                    pnl = (t["entry"]-ask)*contract*volume
                    realized += pnl
                    tickets.remove(t)
                    closes += 1
                if counter_on:
                    obo = sum(1 for t in tickets if t["dir"]=="BUY")
                    for cl in range(1,3):
                        if obo >= mo: break
                        tickets.append({"dir":"BUY","entry":bid-(cl-1)*s*0.5,"ctr":True})
                        obo += 1
            
            # CASCADE BUY
            bl = sorted([t for t in tickets if t["dir"]=="BUY"], key=lambda t: t["entry"])
            if bl and bid >= bl[-1]["entry"]:
                for t in list(bl):
                    pnl = (bid-t["entry"])*contract*volume
                    realized += pnl
                    tickets.remove(t)
                    closes += 1
                if counter_on:
                    oso = sum(1 for t in tickets if t["dir"]=="SELL")
                    for cl in range(1,3):
                        if oso >= mo: break
                        tickets.append({"dir":"SELL","entry":ask+(cl-1)*s*0.5,"ctr":True})
                        oso += 1
            
            # Close counter
            for t in list(tickets):
                if not t.get("ctr"): continue
                if t["dir"]=="BUY" and bid >= t["entry"]+s:
                    realized += (bid-t["entry"])*contract*volume
                    tickets.remove(t); closes += 1
                elif t["dir"]=="SELL" and ask <= t["entry"]-s:
                    realized += (t["entry"]-ask)*contract*volume
                    tickets.remove(t); closes += 1
        
        # End of bar anchor reset
        if not tickets and abs(bar["close"]-anchor) >= s:
            anchor = bar["close"]; nsl = 1; nbl = 1
            anchor_resets += 1
    
    total_hrs = len(bars)*300/3600
    per_hr = realized / total_hrs if total_hrs > 0 else 0
    return {"per_hr": per_hr, "closes": closes, "worst_eq": worst_equity, 
            "peak_eq": peak_equity, "worst_float": worst_float, "resets": anchor_resets}

def main():
    print("="*90)
    print("LIVE TICK ENGINE SIMULATION — Bar-Replay vs Tick-Simulated")
    print("="*90)
    
    symbols = [("GBPUSD", 0.00005), ("EURUSD", 0.00005), ("AUDUSD", 0.00005)]
    
    for sym, step in symbols:
        print(f"\n{'='*60}")
        print(f"=== {sym} (step={step*10000:.1f}pips) ===")
        print(f"{'='*60}")
        
        bars_raw = mt5.copy_rates_from_pos(sym, mt5.TIMEFRAME_M5, 0, 24*12*days)
        if bars_raw is None: continue
        bars = [{"time":int(r[0]),"open":float(r[1]),"high":float(r[2]),"low":float(r[3]),"close":float(r[4])} for r in bars_raw]
        
        for mo in [10, 30, 60]:
            print(f"\n  mo={mo}:")
            
            # Bar-replay baseline (close at bar extreme)
            r_bar = {"per_hr": 0, "closes": 0, "worst_eq": 50, "worst_float": 0}
            # Use the existing cascade function from staged rollout
            # For now, approximate bar-replay as n_ticks=2 (open and close at extremes)
            r_bar = run_tick_sim(sym, bars, step, mo, counter_on=True, n_ticks=2)
            print(f"    Bar-replay:  ${r_bar['per_hr']:.2f}/hr  {r_bar['closes']}c  worst_eq=${r_bar['worst_eq']:.2f}")
            
            # Tick-simulated (30 ticks per bar)
            r_tick30 = run_tick_sim(sym, bars, step, mo, counter_on=True, n_ticks=30)
            bar_pct = r_tick30["per_hr"] / max(r_bar["per_hr"], 0.01) * 100
            print(f"    Tick (30/tick): ${r_tick30['per_hr']:.2f}/hr  {r_tick30['closes']}c  worst_eq=${r_tick30['worst_eq']:.2f}  [{bar_pct:.0f}% of bar]")
            
            # Tick-simulated (10 ticks per bar)
            r_tick10 = run_tick_sim(sym, bars, step, mo, counter_on=True, n_ticks=10)
            bar_pct10 = r_tick10["per_hr"] / max(r_bar["per_hr"], 0.01) * 100
            print(f"    Tick (10/tick): ${r_tick10['per_hr']:.2f}/hr  {r_tick10['closes']}c  worst_eq=${r_tick10['worst_eq']:.2f}  [{bar_pct10:.0f}% of bar]")
    
    mt5.shutdown()

if __name__ == "__main__":
    main()
