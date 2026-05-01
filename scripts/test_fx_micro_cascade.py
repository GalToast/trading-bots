#!/usr/bin/env python3
"""
FX MICRO CASCADE — M1 + M5 + M15 cascade on GBPUSD and EURUSD

Testing Gemini's claim: $16.79/hr GBPUSD M1, $12.57/hr EURUSD M1
Our counter: cascade at bar extreme should beat micro-snake retrace.

Also testing CRAZY stuff:
- max_open up to 300 (absorb deep moves)
- Steps: 0.05p, 0.1p, 0.2p, 0.5p, 1.0p
- Hold frontier: 0, 1, 2, 3
- Multi-timeframe: M1 + M5 + M15 stacked
- SURVIVABILITY: max_floating_loss_usd = -$50 enforced
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

def run_fx_micro_cascade(symbol, bars, cfg):
    """FX micro cascade with survivability enforcement."""
    if not bars or len(bars) < 500: return None
    info = mt5.symbol_info(symbol)
    if not info: return None
    spread_px = spread_price(info)
    if spread_px <= 0: return None
    
    base_step = cfg["base_step"]
    max_open = cfg.get("max_open_per_side", 60)
    hold_frontier = cfg.get("hold_frontier", 1)
    max_floating_loss = cfg.get("max_floating_loss_usd", -50.0)
    
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
    worst_floating = 0.0
    forced_unwinds = 0
    forced_loss = 0.0

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
        
        # FLOATING LOSS CHECK
        bid = bar["low"]; ask = bar["high"]
        floating = 0.0
        for t in tickets:
            if t.direction == "SELL":
                floating += unit_pnl_usd(symbol, "SELL", t.entry_price, ask, spread_px)
            else:
                floating += unit_pnl_usd(symbol, "BUY", t.entry_price, bid, spread_px)
        if floating < worst_floating:
            worst_floating = floating
        
        if floating <= max_floating_loss:
            forced_unwinds += 1
            for t in list(tickets):
                close_px = bid if t.direction=="SELL" else ask
                pnl = unit_pnl_usd(symbol, t.direction, t.entry_price, close_px, spread_px)
                realized += pnl
                forced_loss += pnl
                tickets.remove(t)
                closes += 1
            if not tickets:
                anchor_resets += 1
                nsl = 1; nbl = 1
            continue
        
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
    
    tf_seconds = {"M1":60, "M5":300, "M15":900}.get(cfg.get("tf_label","M15"), 900)
    total_hrs = len(bars)*tf_seconds/3600
    net = realized
    per_hr = net/total_hrs if total_hrs > 0 else 0
    avg = net/closes if closes>0 else 0
    
    return {
        "symbol": symbol, "net": round(net,2), "closes": closes, 
        "per_hr": round(per_hr,2), "avg": round(avg,4),
        "resets": anchor_resets, "max_open": max_open_total,
        "worst_floating": round(worst_floating,2), "forced_unwinds": forced_unwinds,
        "forced_loss": round(forced_loss,2),
    }

def main():
    days = 5  # Match codex 5-day test window
    
    symbols = {
        "GBPUSD": {"tf": mt5.TIMEFRAME_M1, "tf_label": "M1", "pip": 0.0001},
        "EURUSD": {"tf": mt5.TIMEFRAME_M1, "tf_label": "M1", "pip": 0.0001},
    }
    
    print(f"=== FX MICRO CASCADE CHALLENGE: {days} days ===")
    print(f"Target: Beat Gemini's $16.79/hr GBPUSD and $12.57/hr EURUSD\n")
    
    # Step sizes in pips
    steps_pips = [0.05, 0.1, 0.2, 0.5, 1.0, 2.0]
    max_opens = [60, 150, 300]
    hf_values = [0, 1, 2]
    
    for sym, cfg_info in symbols.items():
        print(f"\n{'='*80}")
        print(f"=== {sym} {cfg_info['tf_label']} ===")
        print(f"{'='*80}")
        
        bars_raw = mt5.copy_rates_from_pos(sym, cfg_info["tf"], 0, 24*60*days)
        if bars_raw is None or len(bars_raw) < 500:
            print(f"  NO DATA")
            continue
        
        bars = [{"time":int(r[0]),"open":float(r[1]),"high":float(r[2]),"low":float(r[3]),"close":float(r[4])} for r in bars_raw]
        pip = cfg_info["pip"]
        total_hrs = len(bars)*60/3600
        print(f"  {len(bars)} bars, {total_hrs:.0f} hours, pip={pip}")
        print()
        
        best_result = None
        best_label = None
        
        # Test matrix
        test_configs = []
        for sp in steps_pips:
            for mo in max_opens:
                for hf in hf_values:
                    step_px = sp * pip
                    label = f"step={sp}p mo={mo} hf={hf}"
                    test_configs.append((label, {"base_step": step_px, "max_open_per_side": mo, 
                                                  "hold_frontier": hf, "tf_label": cfg_info["tf_label"],
                                                  "max_floating_loss_usd": -50.0}))
        
        for label, c in test_configs:
            r = run_fx_micro_cascade(sym, bars, c)
            if r:
                flag = " ***" if r["per_hr"] > (16.79 if sym=="GBPUSD" else 12.57) else ""
                print(f"  {label}: ${r['per_hr']:.2f}/hr, {r['closes']}c, ${r['avg']:.4f}/close, float=${r['worst_floating']:.2f}, forced={r['forced_unwinds']}{flag}")
                if best_result is None or r["per_hr"] > best_result["per_hr"]:
                    best_result = r
                    best_label = label
        
        if best_result:
            target = 16.79 if sym=="GBPUSD" else 12.57
            beat = "BEATS" if best_result["per_hr"] > target else "LOSES to"
            print(f"\n  BEST: {best_label} → ${best_result['per_hr']:.2f}/hr ({beat} Gemini's ${target:.2f})")
            print(f"  Worst floating: ${best_result['worst_floating']:.2f}, Forced unwinds: {best_result['forced_unwinds']}")
    
    mt5.shutdown()

if __name__ == "__main__":
    main()
