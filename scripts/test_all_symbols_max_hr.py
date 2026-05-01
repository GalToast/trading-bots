#!/usr/bin/env python3
"""
ALL-SYMBOL $/HR MAXIMIZATION — EMA Ribbon + Cascade on EVERY symbol

Winning architecture from BTC/ETH tests:
- EMA ribbon controller (dynamic step 0.75x-1.5x)
- Cascade close at bar extreme
- Hold frontier=1

Testing ALL symbols:
FX: EURUSD, GBPUSD, USDJPY, AUDUSD, NZDUSD, USDCAD
Crypto: BTCUSD, ETHUSD, SOLUSD, XRPUSD

For each symbol: sweep step sizes to find optimal $/hr
"""
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

def compute_ema(bars, period):
    if len(bars) < period: return [0.0]*len(bars)
    ema = [0.0]*len(bars)
    m = 2.0/(period+1)
    ema[period-1] = sum(bars[i]["close"] for i in range(period))/period
    for i in range(period, len(bars)):
        ema[i] = (bars[i]["close"]-ema[i-1])*m+ema[i-1]
    return ema

def run_ema_cascade(symbol, bars, cfg):
    if not bars or len(bars)<500: return None
    info = mt5.symbol_info(symbol)
    if not info: return None
    spread_px = spread_price(info)
    if spread_px <= 0: return None
    
    base_step = cfg["base_step"]
    max_open = cfg.get("max_open_per_side", 60)
    hold_frontier = cfg.get("hold_frontier", 1)
    
    emas = {p: compute_ema(bars, p) for p in [3,12,24,64,128,500]}
    tickets = []
    realized = 0.0
    closes = 0
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
        
        if compressed: step = max(base_step*0.75, spread_px*2)
        elif trend_up or trend_down: step = base_step*1.5
        else: step = base_step
        sd = 2 if trend_up else 1
        bd = 2 if trend_down else 1
        
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
        
        sl = sorted([t for t in tickets if t.direction=="SELL"], key=lambda t: t.entry_price, reverse=True)
        if sl and bar["low"] <= sl[-1].entry_price:
            tc = sl[:-hold_frontier] if hold_frontier>0 and len(sl)>hold_frontier else sl
            for t in tc:
                pnl = unit_pnl_usd(symbol, "SELL", t.entry_price, bar["low"], spread_px)
                realized += pnl
                tickets.remove(t)
                closes += 1
        
        bl = sorted([t for t in tickets if t.direction=="BUY"], key=lambda t: t.entry_price)
        if bl and bar["high"] >= bl[-1].entry_price:
            tc = bl[:-hold_frontier] if hold_frontier>0 and len(bl)>hold_frontier else bl
            for t in tc:
                pnl = unit_pnl_usd(symbol, "BUY", t.entry_price, bar["high"], spread_px)
                realized += pnl
                tickets.remove(t)
                closes += 1
        
        if not tickets and abs(bar["close"]-anchor) >= step:
            anchor = bar["close"]; nsl = 1; nbl = 1
            anchor_resets += 1
        
        max_open_total = max(max_open_total, len(tickets))
    
    total_hrs = len(bars)*15/60
    net = realized
    per_hr = net/total_hrs
    avg = net/closes if closes>0 else 0
    
    return {
        "symbol": symbol, "net": round(net,2), "closes": closes, 
        "per_hr": round(per_hr,2), "avg": round(avg,4),
        "resets": anchor_resets, "max_open": max_open_total,
        "spread_px": round(spread_px, 6),
    }

def main():
    days = 30
    
    symbols = {
        "FX": ["EURUSD", "GBPUSD", "USDJPY", "AUDUSD", "NZDUSD", "USDCAD"],
        "Crypto": ["BTCUSD", "ETHUSD", "SOLUSD", "XRPUSD"],
    }
    
    # Step sizes per symbol type (in pips/points)
    fx_step_pips = [0.5, 1.0, 1.5, 2.0, 3.0, 5.0]
    crypto_steps = [3.0, 5.0, 7.0, 10.0, 15.0, 20.0, 50.0]
    
    all_results = []
    
    for group, sym_list in symbols.items():
        print(f"\n{'='*60}")
        print(f"=== {group.upper()} ===")
        print(f"{'='*60}")
        
        for sym in sym_list:
            print(f"\n--- {sym} ---", flush=True)
            bars_raw = mt5.copy_rates_from_pos(sym, mt5.TIMEFRAME_M15, 0, 24*4*days)
            if bars_raw is None or len(bars_raw) < 500:
                print(f"  NO DATA")
                continue
            
            bars = [{"time":int(r[0]),"open":float(r[1]),"high":float(r[2]),"low":float(r[3]),"close":float(r[4])} for r in bars_raw]
            total_hrs = len(bars)*15/60
            
            info = mt5.symbol_info(sym)
            pip = info.point * 10 if info.digits in [3,5] else info.point  # pip size
            spread_px = spread_price(info)
            
            if group == "FX":
                # Convert pip steps to price steps
                steps = [s * pip for s in fx_step_pips]
                step_labels = [f"{s}p" for s in fx_step_pips]
            else:
                steps = crypto_steps
                step_labels = [f"${s:.0f}" for s in crypto_steps]
            
            sym_results = []
            for step, label in zip(steps, step_labels):
                cfg = {"base_step": step, "hold_frontier": 1, "max_open_per_side": 60}
                r = run_ema_cascade(sym, bars, cfg)
                if r:
                    sym_results.append((label, r))
                    all_results.append((sym, label, r))
                    print(f"  step={label}: ${r['per_hr']:.2f}/hr, {r['closes']}c, ${r['avg']:.4f}/close, spread={r['spread_px']:.4f}")
            
            if sym_results:
                best = max(sym_results, key=lambda x: x[1]["per_hr"])
                print(f"  BEST: {best[0]} at ${best[1]['per_hr']:.2f}/hr")
    
    # Summary
    print(f"\n{'='*80}")
    print(f"ALL-SYMBOL SUMMARY (sorted by $/hr)")
    print(f"{'='*80}")
    print(f"{'Symbol':<10} {'Step':<10} {'$/hr':>9} {'Closes':>7} {'$/close':>10} {'Spread':>8} {'Resets':>7}")
    print("-" * 80)
    
    all_results.sort(key=lambda x: x[2]["per_hr"], reverse=True)
    for sym, step, r in all_results:
        print(f"{sym:<10} {step:<10} ${r['per_hr']:>8.2f} {r['closes']:>7} ${r['avg']:>9.4f} {r['spread_px']:>8.4f} {r['resets']:>7}")
    
    # Total if we ran ALL winners simultaneously
    print(f"\n{'='*60}")
    print(f"COMBINED $/HR IF ALL WINNERS RUN IN PARALLEL:")
    total_hr = 0
    for sym in set(s for s, _, _ in all_results):
        sym_best = max([(s, st, r) for s, st, r in all_results if s == sym], key=lambda x: x[2]["per_hr"])
        total_hr += sym_best[2]["per_hr"]
        print(f"  {sym_best[0]} ({sym_best[1]}): ${sym_best[2]['per_hr']:.2f}/hr")
    print(f"  {'='*40}")
    print(f"  TOTAL: ${total_hr:.2f}/hr")
    print(f"  Per day (24h): ${total_hr*24:.2f}")
    print(f"  Per month (720h): ${total_hr*720:.2f}")
    
    mt5.shutdown()

if __name__ == "__main__":
    main()
