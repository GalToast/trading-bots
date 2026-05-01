#!/usr/bin/env python3
"""
THE $300+/HR TEST — Every winning config from all tests combined

From all-symbol sweep + FX multi-TF cascade + BTC multi-TF stacking:

FX (M5+M15 cascade, hf=0):
  GBPUSD: M5=0.5p, M15=0.5p → $45.68/hr
  EURUSD: M5=0.5p, M15=0.5p → $28.02/hr
  USDJPY: M5=1.0p, M15=0.5p → $19.47/hr
  AUDUSD: M5=0.5p, M15=0.5p → $11.95/hr
  NZDUSD: M5=0.5p, M15=3.0p → $6.96/hr
  USDCAD: M5=0.5p, M15=0.5p → $6.17/hr

Crypto (EMA ribbon cascade, hf=1):
  BTC M5: $150 → $43.21/hr
  BTC M15: $50 → $46.54/hr
  BTC H1: $200 → $9.05/hr
  ETH M15: $3 → $25.72/hr

Now testing CRAZY STUFF:
1. Add M1 micro-snake for FX (Gemini's 0.05p step, retrace=1 close)
2. Add M30/H1 for FX (capture deeper reversals)
3. Test 3-timeframe stacking on FX (M1+M5+M15)
4. Test max_open=120 on FX (absorb deeper moves without cascade killing spread)
5. Counter-trend opens during cascade on FX (spread is tiny, should work!)
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
    """EMA ribbon cascade for any symbol/timeframe."""
    if not bars or len(bars) < 500: return None
    info = mt5.symbol_info(symbol)
    if not info: return None
    spread_px = spread_price(info)
    if spread_px <= 0: return None
    
    base_step = cfg["base_step"]
    max_open = cfg.get("max_open_per_side", 60)
    hold_frontier = cfg.get("hold_frontier", 1)
    tf_seconds = cfg.get("tf_seconds", 900)
    
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
        
        if compressed: step = max(base_step*0.75, spread_px*3)
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
        
        # CASCADE CLOSE
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
    
    total_hrs = len(bars)*tf_seconds/3600
    net = realized
    per_hr = net/total_hrs if total_hrs > 0 else 0
    avg = net/closes if closes>0 else 0
    
    return {
        "net": round(net,2), "closes": closes, 
        "per_hr": round(per_hr,2), "avg": round(avg,4),
        "resets": anchor_resets, "max_open": max_open_total,
    }

def main():
    days = 30
    print(f"=== THE $300+ HR TEST: ALL SYMBOLS, ALL TIMEFRAMES ===\n")
    
    # Load ALL data for all symbols
    print("Loading all data...", flush=True)
    
    # Crypto
    btc5 = [{"time":int(r[0]),"open":float(r[1]),"high":float(r[2]),"low":float(r[3]),"close":float(r[4])} for r in mt5.copy_rates_from_pos("BTCUSD", mt5.TIMEFRAME_M5, 0, 24*12*days)]
    btc15 = [{"time":int(r[0]),"open":float(r[1]),"high":float(r[2]),"low":float(r[3]),"close":float(r[4])} for r in mt5.copy_rates_from_pos("BTCUSD", mt5.TIMEFRAME_M15, 0, 24*4*days)]
    btc60 = [{"time":int(r[0]),"open":float(r[1]),"high":float(r[2]),"low":float(r[3]),"close":float(r[4])} for r in mt5.copy_rates_from_pos("BTCUSD", mt5.TIMEFRAME_H1, 0, 24*days)]
    eth15 = [{"time":int(r[0]),"open":float(r[1]),"high":float(r[2]),"low":float(r[3]),"close":float(r[4])} for r in mt5.copy_rates_from_pos("ETHUSD", mt5.TIMEFRAME_M15, 0, 24*4*days)]
    
    # FX
    fx_data = {}
    for sym, tf in [("GBPUSD", mt5.TIMEFRAME_M5), ("EURUSD", mt5.TIMEFRAME_M5), 
                    ("USDJPY", mt5.TIMEFRAME_M5), ("AUDUSD", mt5.TIMEFRAME_M5),
                    ("NZDUSD", mt5.TIMEFRAME_M5), ("USDCAD", mt5.TIMEFRAME_M5)]:
        raw = mt5.copy_rates_from_pos(sym, tf, 0, 24*12*days)
        fx_data[f"{sym}_M5"] = [{"time":int(r[0]),"open":float(r[1]),"high":float(r[2]),"low":float(r[3]),"close":float(r[4])} for r in raw]
        
        raw15 = mt5.copy_rates_from_pos(sym, mt5.TIMEFRAME_M15, 0, 24*4*days)
        fx_data[f"{sym}_M15"] = [{"time":int(r[0]),"open":float(r[1]),"high":float(r[2]),"low":float(r[3]),"close":float(r[4])} for r in raw15]
        
        # Also load M1 for micro-snake comparison
        raw1 = mt5.copy_rates_from_pos(sym, mt5.TIMEFRAME_M1, 0, 24*60*days)
        fx_data[f"{sym}_M1"] = [{"time":int(r[0]),"open":float(r[1]),"high":float(r[2]),"low":float(r[3]),"close":float(r[4])} for r in raw1]
    
    total_hrs = len(btc15)*15/60
    print(f"Total hours: {total_hrs:.0f}")
    print(f"Data loaded: BTC({len(btc5)}M5,{len(btc15)}M15,{len(btc60)}H1), ETH({len(eth15)}M15)")
    print()
    
    # ===== KNOWN WINNERS (from previous tests) =====
    lanes = {
        "BTC M5 ($150)": (run_ema_cascade("BTCUSD", btc5, {"base_step":150,"hold_frontier":1,"max_open_per_side":60,"tf_seconds":300}), "BTCUSD"),
        "BTC M15 ($50)": (run_ema_cascade("BTCUSD", btc15, {"base_step":50,"hold_frontier":1,"max_open_per_side":60,"tf_seconds":900}), "BTCUSD"),
        "BTC H1 ($200)": (run_ema_cascade("BTCUSD", btc60, {"base_step":200,"hold_frontier":1,"max_open_per_side":60,"tf_seconds":3600}), "BTCUSD"),
        "ETH M15 ($3)": (run_ema_cascade("ETHUSD", eth15, {"base_step":3,"hold_frontier":1,"max_open_per_side":60,"tf_seconds":900}), "ETHUSD"),
    }
    
    # FX cascade lanes (from FX multi-TF sweep)
    fx_configs = [
        ("GBPUSD M5 (0.5p)", "GBPUSD_M5", {"base_step":0.00005,"hold_frontier":0,"max_open_per_side":60,"tf_seconds":300}),
        ("GBPUSD M15 (0.5p)", "GBPUSD_M15", {"base_step":0.00005,"hold_frontier":0,"max_open_per_side":60,"tf_seconds":900}),
        ("EURUSD M5 (0.5p)", "EURUSD_M5", {"base_step":0.00005,"hold_frontier":0,"max_open_per_side":60,"tf_seconds":300}),
        ("EURUSD M15 (0.5p)", "EURUSD_M15", {"base_step":0.00005,"hold_frontier":0,"max_open_per_side":60,"tf_seconds":900}),
        ("USDJPY M5 (1.0p)", "USDJPY_M5", {"base_step":0.001,"hold_frontier":0,"max_open_per_side":60,"tf_seconds":300}),
        ("USDJPY M15 (0.5p)", "USDJPY_M15", {"base_step":0.0005,"hold_frontier":0,"max_open_per_side":60,"tf_seconds":900}),
        ("AUDUSD M5 (0.5p)", "AUDUSD_M5", {"base_step":0.00005,"hold_frontier":0,"max_open_per_side":60,"tf_seconds":300}),
        ("AUDUSD M15 (0.5p)", "AUDUSD_M15", {"base_step":0.00005,"hold_frontier":0,"max_open_per_side":60,"tf_seconds":900}),
        ("NZDUSD M5 (0.5p)", "NZDUSD_M5", {"base_step":0.00005,"hold_frontier":0,"max_open_per_side":60,"tf_seconds":300}),
        ("NZDUSD M15 (3.0p)", "NZDUSD_M15", {"base_step":0.0003,"hold_frontier":0,"max_open_per_side":60,"tf_seconds":900}),
        ("USDCAD M5 (0.5p)", "USDCAD_M5", {"base_step":0.00005,"hold_frontier":0,"max_open_per_side":60,"tf_seconds":300}),
        ("USDCAD M15 (0.5p)", "USDCAD_M15", {"base_step":0.00005,"hold_frontier":0,"max_open_per_side":60,"tf_seconds":900}),
    ]
    
    for name, data_key, cfg in fx_configs:
        r = run_ema_cascade(name.split()[0], fx_data[data_key], cfg)
        lanes[name] = (r, name.split()[0])
    
    # Print results
    print(f"{'Lane':<25} {'$/hr':>9} {'Closes':>7} {'$/close':>10} {'Resets':>7}")
    print("-" * 65)
    
    total_net = 0
    total_closes = 0
    
    for name, (r, sym) in lanes.items():
        if r:
            print(f"{name:<25} ${r['per_hr']:>8.2f} {r['closes']:>7} ${r['avg']:>9.4f} {r['resets']:>7}")
            total_net += r["net"]
            total_closes += r["closes"]
    
    combined_hr = total_net / total_hrs
    combined_avg = total_net / total_closes if total_closes > 0 else 0
    
    print("=" * 65)
    print(f"{'TOTAL':<25} ${combined_hr:>8.2f} {total_closes:>7} ${combined_avg:>9.4f}")
    print(f"\nPer day (24h):  ${combined_hr*24:,.2f}")
    print(f"Per month (720h): ${combined_hr*720:,.2f}")
    
    # Group by symbol
    print(f"\n{'='*50}")
    print(f"BY SYMBOL:")
    print(f"{'='*50}")
    sym_totals = {}
    for name, (r, sym) in lanes.items():
        if r:
            if sym not in sym_totals:
                sym_totals[sym] = {"hr": 0, "closes": 0}
            sym_totals[sym]["hr"] += r["per_hr"]
            sym_totals[sym]["closes"] += r["closes"]
    
    for sym, totals in sorted(sym_totals.items(), key=lambda x: x[1]["hr"], reverse=True):
        print(f"  {sym:<10} ${totals['hr']:>8.2f}/hr  ({totals['closes']} closes)")
    
    mt5.shutdown()

if __name__ == "__main__":
    main()
