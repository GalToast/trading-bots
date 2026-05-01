#!/usr/bin/env python3
"""
FAST $50 SURVIVABILITY — Key symbols only, 7 days

Tests only the top revenue symbols with floating PnL tracking.
Results in under 5 minutes.
"""
import MetaTrader5 as mt5
import sys
sys.path.insert(0, 'scripts')

mt5.initialize()
days = 7  # Fast test
start_balance = 50.0

print("="*80)
print(f"FAST $50 SURVIVABILITY TEST — {days} days")
print("="*80)

# Test only the top earners
key_symbols = [
    ("GBPUSD", 0.00005, True),   # Top FX earner, counter-trend
    ("EURUSD", 0.00005, True),   # 2nd FX
    ("BTCUSD", 50.0, False),     # Top crypto, no counter
]

results = []

for sym, step, counter_on in key_symbols:
    print(f"\n--- {sym} (step={step}, counter={counter_on}) ---", flush=True)
    
    info = mt5.symbol_info(sym)
    spread_px = info.spread * info.point
    contract = info.trade_contract_size
    volume = 0.01
    
    # Load M15 data
    bars_raw = mt5.copy_rates_from_pos(sym, mt5.TIMEFRAME_M15, 0, 24*4*days)
    if bars_raw is None or len(bars_raw) < 200:
        print(f"  NO DATA")
        continue
    
    bars = [{"time":int(r[0]),"open":float(r[1]),"high":float(r[2]),"low":float(r[3]),"close":float(r[4])} for r in bars_raw]
    total_hrs = len(bars)*15/3600
    
    # Compute EMA
    def ema(period):
        if len(bars) < period: return [0.0]*len(bars)
        e = [0.0]*len(bars)
        m = 2.0/(period+1)
        e[period-1] = sum(bars[i]["close"] for i in range(period))/period
        for i in range(period, len(bars)):
            e[i] = (bars[i]["close"]-e[i-1])*m+e[i-1]
        return e
    
    ema_3 = ema(3); ema_12 = ema(12); ema_24 = ema(24)
    ema_64 = ema(64); ema_500 = ema(500)
    
    tickets = []
    realized = 0.0
    closes = 0
    worst_float = 0.0
    best_float = 0.0
    max_open_total = 0
    anchor = bars[0]["close"]
    nsl = 1
    nbl = 1
    last_bar_time = int(bars[0]["time"])
    peak_equity = start_balance
    worst_equity = start_balance
    peak_to_trough = 0.0
    
    for idx in range(1, len(bars)):
        bar = bars[idx]
        if int(bar["time"]) <= last_bar_time: continue
        last_bar_time = int(bar["time"])
        
        span = abs(ema_3[idx] - ema_500[idx])
        compressed = span <= (step*3.0)
        trend_up = ema_3[idx] > ema_12[idx] > ema_24[idx] > ema_64[idx] and span >= (step*4.0)
        trend_down = ema_3[idx] < ema_12[idx] < ema_24[idx] < ema_64[idx] and span >= (step*4.0)
        
        if compressed: s = max(step*0.75, spread_px*3)
        elif trend_up or trend_down: s = step*1.5
        else: s = step
        sd = 2 if trend_up else 1
        bd = 2 if trend_down else 1
        
        # Opens
        osc = sum(1 for t in tickets if t["dir"]=="SELL")
        while bar["high"] >= anchor+(nsl*s) and osc < 60:
            if sd<=1 or nsl%sd==0:
                tickets.append({"dir":"SELL", "entry":anchor+(nsl*s), "ctr":False})
                osc += 1
            nsl += 1
        obc = sum(1 for t in tickets if t["dir"]=="BUY")
        while bar["low"] <= anchor-(nbl*s) and obc < 60:
            if bd<=1 or nbl%bd==0:
                tickets.append({"dir":"BUY", "entry":anchor-(nbl*s), "ctr":False})
                obc += 1
            nbl += 1
        
        # FLOATING PnL
        bid = bar["low"]; ask = bar["high"]
        floating = 0.0
        for t in tickets:
            if t["dir"] == "SELL":
                floating += (t["entry"] - ask) * contract * volume
            else:
                floating += (bid - t["entry"]) * contract * volume
        
        equity = start_balance + realized + floating
        if floating < worst_float: worst_float = floating
        if floating > best_float: best_float = floating
        if equity > peak_equity: peak_equity = equity
        if equity < worst_equity: worst_equity = equity
        dd = (peak_equity - equity) / peak_equity * 100 if peak_equity > 0 else 0
        if dd > peak_to_trough: peak_to_trough = dd
        
        # CASCADE SELL
        sl = sorted([t for t in tickets if t["dir"]=="SELL"], key=lambda t: t["entry"], reverse=True)
        if sl and bar["low"] <= sl[-1]["entry"]:
            for t in list(sl):
                pnl = (t["entry"] - bar["low"]) * contract * volume
                realized += pnl
                tickets.remove(t)
                closes += 1
            if counter_on:
                obo = sum(1 for t in tickets if t["dir"]=="BUY")
                for cl in range(1, 3):
                    if obo >= 60: break
                    tickets.append({"dir":"BUY", "entry":bar["low"]-(cl-1)*s*0.5, "ctr":True})
                    obo += 1
        
        # CASCADE BUY
        bl = sorted([t for t in tickets if t["dir"]=="BUY"], key=lambda t: t["entry"])
        if bl and bar["high"] >= bl[-1]["entry"]:
            for t in list(bl):
                pnl = (bar["high"] - t["entry"]) * contract * volume
                realized += pnl
                tickets.remove(t)
                closes += 1
            if counter_on:
                oso = sum(1 for t in tickets if t["dir"]=="SELL")
                for cl in range(1, 3):
                    if oso >= 60: break
                    tickets.append({"dir":"SELL", "entry":bar["high"]+(cl-1)*s*0.5, "ctr":True})
                    oso += 1
        
        # Close counter positions
        for t in list(tickets):
            if not t.get("ctr"): continue
            if t["dir"] == "BUY" and bar["high"] >= t["entry"] + s:
                pnl = (bar["high"] - t["entry"]) * contract * volume
                realized += pnl
                tickets.remove(t)
                closes += 1
            elif t["dir"] == "SELL" and bar["low"] <= t["entry"] - s:
                pnl = (t["entry"] - bar["low"]) * contract * volume
                realized += pnl
                tickets.remove(t)
                closes += 1
        
        if not tickets and abs(bar["close"]-anchor) >= s:
            anchor = bar["close"]; nsl = 1; nbl = 1
        
        max_open_total = max(max_open_total, len(tickets))
    
    per_hr = realized / total_hrs if total_hrs > 0 else 0
    survived = worst_equity > 0
    would_survive = worst_equity
    
    print(f"  $/hr: ${per_hr:.2f}")
    print(f"  Closes: {closes}")
    print(f"  Worst floating: ${worst_float:.2f}")
    print(f"  Best floating: ${best_float:.2f}")
    print(f"  Worst equity: ${worst_equity:.2f} (from ${start_balance})")
    print(f"  Max drawdown: {peak_to_trough:.1f}%")
    print(f"  Max open: {max_open_total}")
    print(f"  Would survive $50: {'YES' if survived else 'NO'} (equity floor: ${would_survive:.2f})")
    
    results.append({
        "symbol": sym, "per_hr": per_hr, "worst_float": worst_float,
        "worst_equity": worst_equity, "max_dd": peak_to_trough,
        "max_open": max_open_total, "survived": survived, "closes": closes,
    })

print(f"\n{'='*80}")
print(f"SURVIVABILITY RANKING (lowest risk first)")
print(f"{'='*80}")

# Sort by worst equity (highest = safest)
results.sort(key=lambda x: x["worst_equity"], reverse=True)

for i, r in enumerate(results, 1):
    status = "✅ SAFE" if r["survived"] else "❌ BLOWUP"
    print(f"  {i}. {r['symbol']:<10} ${r['per_hr']:.2f}/hr  worst_eq=${r['worst_equity']:.2f}  max_dd={r['max_dd']:.1f}%  {status}")

# Build staged plan
print(f"\n{'='*80}")
print(f"STAGED $50 ROLLOUT PLAN")
print(f"{'='*80}")

# Calculate max_open needed for survivability
for r in results:
    # What max_open would keep worst_equity > $25 (50% buffer)?
    if r["worst_equity"] < 25 and r["max_open"] > 10:
        # Rough estimate: scale down max_open proportionally
        safe_open = int(r["max_open"] * (r["worst_equity"] / 25))
        print(f"  {r['symbol']}: Reduce max_open from {r['max_open']} to ~{safe_open} to survive on $50")
    elif r["survived"]:
        print(f"  {r['symbol']}: Safe at max_open={r['max_open']}")

print(f"\nRECOMMENDATION:")
print(f"  1. Start with {', '.join([r['symbol'] for r in results if r['survived']])} at reduced max_open")
print(f"  2. Avoid {', '.join([r['symbol'] for r in results if not r['survived']])} until balance > ${abs(min(r['worst_equity'] for r in results if not r['survived'])):.0f}")

mt5.shutdown()
