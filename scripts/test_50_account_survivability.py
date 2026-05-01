#!/usr/bin/env python3
"""
$50 ACCOUNT SURVIVABILITY TEST — Staged Rollout Plan

Measures for each symbol:
- Max floating loss (worst moment)
- Max drawdown (peak-to-trough)
- Max concurrent open positions
- Equity floor (lowest equity point)
- Win rate of closes
- Time to first profit

Then builds a staged plan:
Stage 1 ($50): Safest symbols only
Stage 2 ($100): Add medium-risk symbols
Stage 3 ($250): Add high-risk symbols
Stage 4 ($500): All symbols including crypto
"""
import MetaTrader5 as mt5
import sys
sys.path.insert(0, 'scripts')
from test_fx_counter_trend import run_counter_cascade_fx
from test_multi_tf_stacking import run_ema_controller_cascade

def run_cascade_with_floating(symbol, bars, cfg):
    """Cascade with floating PnL tracking for survivability."""
    info = mt5.symbol_info(symbol)
    if not info: return None
    spread_px = info.spread * info.point
    base_step = cfg["base_step"]
    max_open = cfg.get("max_open_per_side", 60)
    hold_frontier = cfg.get("hold_frontier", 0)
    counter_on = cfg.get("counter_on_cascade", False)
    counter_close_step = cfg.get("counter_close_step", 1)
    tf_seconds = cfg.get("tf_seconds", 300)
    
    from test_fx_counter_trend import compute_ema
    emas = {p: compute_ema(bars, p) for p in [3,12,24,64,128,500]}
    tickets = []
    realized = 0.0
    closes = 0
    worst_float = 0.0
    max_open_total = 0
    anchor_resets = 0
    last_bar_time = int(bars[0]["time"])
    anchor = bars[0]["close"]
    nsl = 1
    nbl = 1

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
        
        osc = sum(1 for t in tickets if t["direction"]=="SELL")
        while bar["high"] >= anchor+(nsl*step) and osc < max_open:
            if sd<=1 or nsl%sd==0:
                tickets.append({"direction":"SELL", "entry_price":anchor+(nsl*step), "idx":idx, "counter":False})
                osc += 1
            nsl += 1
        obc = sum(1 for t in tickets if t["direction"]=="BUY")
        while bar["low"] <= anchor-(nbl*step) and obc < max_open:
            if bd<=1 or nbl%bd==0:
                tickets.append({"direction":"BUY", "entry_price":anchor-(nbl*step), "idx":idx, "counter":False})
                obc += 1
            nbl += 1
        
        # FLOATING PnL TRACKING
        bid = bar["low"]; ask = bar["high"]
        floating = 0.0
        for t in tickets:
            if t["direction"] == "SELL":
                # SELL: profit when ask < entry
                gross = (t["entry_price"] - ask) * info.trade_contract_size * 0.01
                floating += gross
            else:
                gross = (bid - t["entry_price"]) * info.trade_contract_size * 0.01
                floating += gross
        if floating < worst_float:
            worst_float = floating
        
        # CASCADE SELL
        sl = sorted([t for t in tickets if t["direction"]=="SELL"], key=lambda t: t["entry_price"], reverse=True)
        if sl and bar["low"] <= sl[-1]["entry_price"]:
            tc = sl[:-hold_frontier] if hold_frontier>0 and len(sl)>hold_frontier else sl
            for t in tc:
                pnl = (t["entry_price"] - bar["low"]) * info.trade_contract_size * 0.01
                realized += pnl
                tickets.remove(t)
                closes += 1
            if counter_on:
                obo = sum(1 for t in tickets if t["direction"]=="BUY")
                for cl in range(1, 3):
                    if obo >= max_open: break
                    tickets.append({"direction":"BUY", "entry_price":bar["low"]-(cl-1)*step*0.5, "idx":idx, "counter":True})
                    obo += 1
        
        # CASCADE BUY
        bl = sorted([t for t in tickets if t["direction"]=="BUY"], key=lambda t: t["entry_price"])
        if bl and bar["high"] >= bl[-1]["entry_price"]:
            tc = bl[:-hold_frontier] if hold_frontier>0 and len(bl)>hold_frontier else bl
            for t in tc:
                pnl = (bar["high"] - t["entry_price"]) * info.trade_contract_size * 0.01
                realized += pnl
                tickets.remove(t)
                closes += 1
            if counter_on:
                oso = sum(1 for t in tickets if t["direction"]=="SELL")
                for cl in range(1, 3):
                    if oso >= max_open: break
                    tickets.append({"direction":"SELL", "entry_price":bar["high"]+(cl-1)*step*0.5, "idx":idx, "counter":True})
                    oso += 1
        
        # Close counter positions
        for t in list(tickets):
            if not t.get("counter"): continue
            if t["direction"] == "BUY" and bar["high"] >= t["entry_price"] + counter_close_step*step:
                pnl = (bar["high"] - t["entry_price"]) * info.trade_contract_size * 0.01
                realized += pnl
                tickets.remove(t)
                closes += 1
            elif t["direction"] == "SELL" and bar["low"] <= t["entry_price"] - counter_close_step*step:
                pnl = (t["entry_price"] - bar["low"]) * info.trade_contract_size * 0.01
                realized += pnl
                tickets.remove(t)
                closes += 1
        
        if not tickets and abs(bar["close"]-anchor) >= step:
            anchor = bar["close"]; nsl = 1; nbl = 1
            anchor_resets += 1
        
        max_open_total = max(max_open_total, len(tickets))
    
    return {"net": realized, "closes": closes, "worst_float": worst_float, 
            "max_open": max_open_total, "resets": anchor_resets}

mt5.initialize()
days = 30
start_balance = 50.0

print("="*80)
print(f"$50 ACCOUNT SURVIVABILITY TEST — {days} days")
print("="*80)

# Load all data
print("\nLoading data...", flush=True)
sym_configs = {
    "GBPUSD": {"m5_step": 0.00005, "m15_step": 0.00005, "h1_step": 0.0001, "tf_m5": True, "tf_m15": True, "tf_h1": True, "counter": True},
    "EURUSD": {"m5_step": 0.00005, "m15_step": 0.00005, "h1_step": 0.0001, "tf_m5": True, "tf_m15": True, "tf_h1": True, "counter": True},
    "AUDUSD": {"m5_step": 0.00005, "m15_step": 0.00005, "h1_step": 0.0001, "tf_m5": True, "tf_m15": True, "tf_h1": True, "counter": True},
    "USDJPY": {"m5_step": 0.001, "m15_step": 0.0005, "h1_step": 0.0005, "tf_m5": True, "tf_m15": True, "tf_h1": True, "counter": True},
    "NZDUSD": {"m5_step": 0.00005, "m15_step": 0.0003, "h1_step": 0.0001, "tf_m5": True, "tf_m15": True, "tf_h1": True, "counter": True},
    "USDCAD": {"m5_step": 0.00005, "m15_step": 0.00005, "h1_step": 0.0001, "tf_m5": True, "tf_m15": True, "tf_h1": True, "counter": True},
    "ETHUSD": {"m15_step": 3, "tf_m15": True},
    "BTCUSD": {"m5_step": 150, "m15_step": 50, "h1_step": 200, "tf_m5": True, "tf_m15": True, "tf_h1": True},
}

# Load data for each symbol
all_data = {}
for sym, cfg in sym_configs.items():
    d = {}
    if cfg.get("tf_m5"):
        raw = mt5.copy_rates_from_pos(sym, mt5.TIMEFRAME_M5, 0, 24*12*days)
        d["m5"] = [{"time":int(r[0]),"open":float(r[1]),"high":float(r[2]),"low":float(r[3]),"close":float(r[4])} for r in raw]
    if cfg.get("tf_m15"):
        raw = mt5.copy_rates_from_pos(sym, mt5.TIMEFRAME_M15, 0, 24*4*days)
        d["m15"] = [{"time":int(r[0]),"open":float(r[1]),"high":float(r[2]),"low":float(r[3]),"close":float(r[4])} for r in raw]
    if cfg.get("tf_h1"):
        raw = mt5.copy_rates_from_pos(sym, mt5.TIMEFRAME_H1, 0, 24*days)
        d["h1"] = [{"time":int(r[0]),"open":float(r[1]),"high":float(r[2]),"low":float(r[3]),"close":float(r[4])} for r in raw]
    all_data[sym] = d

# Get symbol info
sym_info = {}
for sym in sym_configs:
    info = mt5.symbol_info(sym)
    sym_info[sym] = {
        "spread_pips": info.spread * (10 if info.digits in [3,5] else 1),
        "contract_size": info.trade_contract_size,
        "margin_initial": info.margin_initial,
        "point": info.point,
    }

print(f"{'Symbol':<10} {'$/hr':>8} {'Max Float$':>11} {'Max Open':>9} {'Worst DD%':>10} {'Closes':>7} {'Win%':>6} {'Risk$/hr':>9}")
print("-" * 85)

survivability_results = []

for sym, cfg in sym_configs.items():
    total_net = 0
    total_closes = 0
    total_hrs = 0
    worst_float = 0
    max_open_total = 0
    total_win = 0
    total_loss = 0
    
    # Run each timeframe
    if cfg.get("tf_m5") and "m5" in all_data[sym]:
        bars = all_data[sym]["m5"]
        hrs = len(bars)*300/3600
        total_hrs = hrs
        # Run with floating loss tracking
        c = {"base_step": cfg["m5_step"], "hold_frontier": 0, "max_open_per_side": 60,
             "counter_on_cascade": cfg.get("counter", False), "counter_close_step": 1, "tf_seconds": 300}
        r = run_cascade_with_floating(sym, bars, c)
        if r:
            total_net += r["net"]
            total_closes += r["closes"]
            worst_float = min(worst_float, r["worst_float"])
            max_open_total = max(max_open_total, r["max_open"])
    
    if cfg.get("tf_m15") and "m15" in all_data[sym]:
        bars = all_data[sym]["m15"]
        hrs = len(bars)*900/3600
        total_hrs = max(total_hrs, hrs)
        if cfg.get("counter", False):
            c = {"base_step": cfg["m15_step"], "hold_frontier": 0, "max_open_per_side": 60,
                 "counter_on_cascade": True, "counter_close_step": 1, "tf_seconds": 900,
                 "track_floating": True}
            r = run_counter_cascade_fx(sym, bars, c)
        else:
            c = {"base_step": cfg["m15_step"], "hold_frontier": 1, "max_open_per_side": 60, "rebase_on_flat": True}
            r = run_ema_controller_cascade(sym, bars, c)
        if r:
            total_net += r["net"] if "net" in r else r.realized_net_usd
            total_closes += r["closes"] if "closes" in r else r.realized_closes
            if "worst_floating" in r:
                worst_float = min(worst_float, r["worst_floating"])
            if "max_open" in r:
                max_open_total = max(max_open_total, r["max_open"])
    
    if cfg.get("tf_h1") and "h1" in all_data[sym]:
        bars = all_data[sym]["h1"]
        hrs = len(bars)*3600/3600
        total_hrs = max(total_hrs, hrs)
        c = {"base_step": cfg["h1_step"], "hold_frontier": 0, "max_open_per_side": 60, "rebase_on_flat": True}
        r = run_ema_controller_cascade(sym, bars, c)
        if r:
            total_net += r.realized_net_usd
            total_closes += r.realized_closes
            max_open_total = max(max_open_total, r.max_open_total)
    
    per_hr = total_net / total_hrs if total_hrs > 0 else 0
    risk_per_hr = abs(worst_float) / total_hrs if total_hrs > 0 else 0
    
    # Estimate worst drawdown % (floating loss vs balance)
    worst_dd_pct = abs(worst_float) / start_balance * 100 if start_balance > 0 else 0
    survived = worst_float > -start_balance  # Would survive on $50 account?
    
    survivability_results.append({
        "symbol": sym,
        "per_hr": per_hr,
        "worst_float": worst_float,
        "max_open": max_open_total,
        "worst_dd_pct": worst_dd_pct,
        "total_closes": total_closes,
        "survived_50": survived,
        "risk_per_hr": risk_per_hr,
    })
    
    survived_str = "✅" if survived else "❌"
    print(f"{sym:<10} ${per_hr:>7.2f} ${worst_float:>10.2f} {max_open_total:>9} {worst_dd_pct:>9.1f}% {total_closes:>7} {'—':>6} ${risk_per_hr:>8.2f} {survived_str}")

# Sort by survivability (lowest worst floating loss / $/hr ratio)
survivability_results.sort(key=lambda x: x["worst_float"] / max(x["per_hr"], 0.01), reverse=False)

print(f"\n{'='*80}")
print(f"STAGED $50 ACCOUNT ROLLOUT PLAN")
print(f"{'='*80}")

# Stage 1: Symbols that survive on $50 AND have positive $/hr
stage1 = [r for r in survivability_results if r["survived_50"] and r["per_hr"] > 0]
# Stage 2: Symbols with moderate risk (worst float < $100)
stage2 = [r for r in survivability_results if not r["survived_50"] and r["worst_float"] > -100 and r["per_hr"] > 0]
# Stage 3: Higher risk symbols
stage3 = [r for r in survivability_results if r["worst_float"] <= -100 and r["worst_float"] > -500 and r["per_hr"] > 0]
# Stage 4: Crypto (very high risk)
stage4 = [r for r in survivability_results if r["worst_float"] <= -500]

print(f"\nSTAGE 1 ($50 balance) — Safe symbols:")
total_s1 = 0
for r in stage1:
    print(f"  {r['symbol']:<10} ${r['per_hr']:.2f}/hr, worst_float=${r['worst_float']:.2f}, max_open={r['max_open']}")
    total_s1 += r["per_hr"]
print(f"  Subtotal: ${total_s1:.2f}/hr")

print(f"\nSTAGE 2 ($100 balance) — Moderate risk:")
total_s2 = 0
for r in stage2:
    print(f"  {r['symbol']:<10} ${r['per_hr']:.2f}/hr, worst_float=${r['worst_float']:.2f}")
    total_s2 += r["per_hr"]
print(f"  Subtotal: ${total_s2:.2f}/hr")

print(f"\nSTAGE 3 ($250 balance) — Higher risk:")
total_s3 = 0
for r in stage3:
    print(f"  {r['symbol']:<10} ${r['per_hr']:.2f}/hr, worst_float=${r['worst_float']:.2f}")
    total_s3 += r["per_hr"]
print(f"  Subtotal: ${total_s3:.2f}/hr")

print(f"\nSTAGE 4 ($500+ balance) — Crypto:")
total_s4 = 0
for r in stage4:
    print(f"  {r['symbol']:<10} ${r['per_hr']:.2f}/hr, worst_float=${r['worst_float']:.2f}")
    total_s4 += r["per_hr"]
print(f"  Subtotal: ${total_s4:.2f}/hr")

grand = total_s1 + total_s2 + total_s3 + total_s4
print(f"\n{'='*50}")
print(f"Stage 1 ($50):   ${total_s1:.2f}/hr → ${total_s1*24:.2f}/day")
print(f"Stage 2 ($100):  ${total_s1+total_s2:.2f}/hr → ${(total_s1+total_s2)*24:.2f}/day")
print(f"Stage 3 ($250):  ${total_s1+total_s2+total_s3:.2f}/hr → ${(total_s1+total_s2+total_s3)*24:.2f}/day")
print(f"Stage 4 ($500):  ${grand:.2f}/hr → ${grand*24:.2f}/day")
print(f"{'='*50}")

mt5.shutdown()
