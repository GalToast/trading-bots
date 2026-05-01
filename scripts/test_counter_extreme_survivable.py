#!/usr/bin/env python3
"""
COUNTER-TREND AT BAR EXTREME + SURVIVABILITY

Key fix: Counter-trend entries should be AT THE BAR EXTREME (reversal low/high),
not at the lattice level. This captures the reversal advantage.

Also: Enforce max_floating_loss_usd during backtest to measure survivability.

Selling at $75,000, bar low reaches $74,500 (reversal $500).
Counter BUY opens at $74,500 (bar low), not at $75,000.
After spread ($180), net advantage = $320.
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
    from_counter: bool = False  # True if opened as counter-trend

def compute_ema(bars, period):
    if len(bars) < period: return [0.0]*len(bars)
    ema = [0.0]*len(bars)
    m = 2.0/(period+1)
    ema[period-1] = sum(bars[i]["close"] for i in range(period))/period
    for i in range(period, len(bars)):
        ema[i] = (bars[i]["close"]-ema[i-1])*m+ema[i-1]
    return ema

def run_counter_survivable(symbol, bars, cfg):
    if not bars or len(bars)<500: return {"symbol": symbol}
    info = mt5.symbol_info(symbol)
    if not info: return {"symbol": symbol}
    spread_px = spread_price(info)
    base_step = cfg.get("base_step", 50.0)
    max_open = cfg.get("max_open_per_side", 60)
    hold_frontier = cfg.get("hold_frontier", 1)
    counter_on_cascade = cfg.get("counter_on_cascade", False)
    counter_levels = cfg.get("counter_levels", 1)
    max_floating_loss = cfg.get("max_floating_loss_usd", -3500.0)  # Enforce survivability
    
    emas = {p: compute_ema(bars, p) for p in [3,12,24,64,128,500]}
    tickets = []
    realized = 0.0
    closes = 0
    anchor_resets = 0
    counter_opens = 0
    counter_closes = 0
    last_bar_time = int(bars[0]["time"])
    anchor = bars[0]["close"]
    nsl = 1
    nbl = 1
    
    # Survivability tracking
    worst_floating = 0.0  # Most negative floating loss seen
    floating_at_each_bar = []
    forced_unwinds = 0
    total_floating_loss_realized = 0.0

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
        
        # === Opens ===
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
        
        # === FLOATING LOSS CHECK (survivability) ===
        bid = bar["low"]; ask = bar["high"]
        floating = 0.0
        for t in tickets:
            if t.direction == "SELL":
                floating += unit_pnl_usd(symbol, "SELL", t.entry_price, ask, spread_px)
            else:
                floating += unit_pnl_usd(symbol, "BUY", t.entry_price, bid, spread_px)
        if floating < worst_floating:
            worst_floating = floating
        floating_at_each_bar.append(floating)
        
        # Forced unwind if floating loss exceeds cap
        if floating <= max_floating_loss:
            forced_unwinds += 1
            for t in list(tickets):
                close_px = bid if t.direction=="SELL" else ask
                pnl = unit_pnl_usd(symbol, t.direction, t.entry_price, close_px, spread_px)
                realized += pnl
                total_floating_loss_realized += pnl
                tickets.remove(t)
                closes += 1
            # Don't reset anchor -- let it continue from current state
            if not tickets:
                anchor_resets += 1
                nsl = 1; nbl = 1
            continue  # Skip cascade for this bar -- we just unwound
        
        # === SELL CASCADE ===
        sl = sorted([t for t in tickets if t.direction=="SELL"], key=lambda t: t.entry_price, reverse=True)
        if sl and bar["low"] <= sl[-1].entry_price:
            tc = sl[:-hold_frontier] if hold_frontier>0 and len(sl)>hold_frontier else sl
            for t in tc:
                pnl = unit_pnl_usd(symbol, "SELL", t.entry_price, bar["low"], spread_px)
                realized += pnl
                tickets.remove(t)
                closes += 1
            # COUNTER-TREND: open BUYs AT BAR EXTREME during SELL cascade
            if counter_on_cascade:
                obo = sum(1 for t in tickets if t.direction=="BUY")
                for cl in range(1, counter_levels+1):
                    if obo >= max_open: break
                    # OPEN AT BAR LOW, not at lattice level!
                    entry = bar["low"]
                    tickets.append(Ticket(direction="BUY", entry_price=entry, opened_idx=idx, from_counter=True))
                    obo += 1
                    counter_opens += 1
        
        # === BUY CASCADE ===
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
                    # OPEN AT BAR HIGH, not at lattice level!
                    entry = bar["high"]
                    tickets.append(Ticket(direction="SELL", entry_price=entry, opened_idx=idx, from_counter=True))
                    oso += 1
                    counter_opens += 1
        
        # Counter-trend closes: close when price reverses back
        for t in list(tickets):
            if not t.from_counter: continue
            if t.direction == "BUY" and bar["high"] >= t.entry_price + base_step*0.5:
                pnl = unit_pnl_usd(symbol, "BUY", t.entry_price, bar["high"], spread_px)
                realized += pnl
                tickets.remove(t)
                closes += 1
                counter_closes += 1
            elif t.direction == "SELL" and bar["low"] <= t.entry_price - base_step*0.5:
                pnl = unit_pnl_usd(symbol, "SELL", t.entry_price, bar["low"], spread_px)
                realized += pnl
                tickets.remove(t)
                closes += 1
                counter_closes += 1
        
        if not tickets and abs(bar["close"]-anchor) >= step:
            anchor = bar["close"]; nsl = 1; nbl = 1
            anchor_resets += 1
    
    total_hrs = len(bars)*15/60
    net = realized
    per_hr = net/total_hrs
    avg = net/closes if closes>0 else 0
    
    return {
        "symbol": symbol, "net": round(net,2), "closes": closes, "per_hr": round(per_hr,2),
        "avg": round(avg,2), "counter_opens": counter_opens, "counter_closes": counter_closes,
        "worst_floating": round(worst_floating,2), "forced_unwinds": forced_unwinds,
        "floating_loss_realized": round(total_floating_loss_realized,2),
        "max_floating_pct": round(worst_floating/max(net,1)*100, 1) if net != 0 else 0,
    }

def main():
    symbol = "BTCUSD"; days = 30
    bars = [{"time":int(r[0]),"open":float(r[1]),"high":float(r[2]),"low":float(r[3]),"close":float(r[4])} 
            for r in mt5.copy_rates_from_pos(symbol, mt5.TIMEFRAME_M15, 0, 24*4*days)]
    total_hrs = len(bars)*15/60
    print(f"COUNTER-TREND AT BAR EXTREME + SURVIVABILITY: {symbol} M15, {days} days")
    print(f"Spread: ~$180, Max floating loss cap: -$3500\n")
    
    configs = []
    for step in [50, 75]:
        # Baseline (no counter)
        configs.append({"step": step, "counter": False, "levels": 0, "label": f"step={step} NO counter"})
        # Counter at bar extreme
        for cl in [1, 2, 3]:
            configs.append({"step": step, "counter": True, "levels": cl, 
                           "label": f"step={step} counter-Y levels={cl} (bar-extreme entry)"})
    
    results = []
    for cfg in configs:
        c = {"base_step": float(cfg["step"]), "counter_on_cascade": cfg["counter"],
             "counter_levels": cfg["levels"], "hold_frontier": 1, "max_open_per_side": 60,
             "max_floating_loss_usd": -3500.0}
        r = run_counter_survivable(symbol, bars, c)
        results.append((cfg["label"], r))
        print(f"  {cfg['label']}")
        print(f"    ${r['per_hr']:.2f}/hr, {r['closes']}c, ${r['avg']:.2f}/close")
        print(f"    Counter opens: {r['counter_opens']}, Counter closes: {r['counter_closes']}")
        print(f"    Worst floating: ${r['worst_floating']:.2f}, Forced unwinds: {r['forced_unwinds']}")
        print()
    
    print(f"{'Config':<50} {'$/hr':>8} {'Closes':>7} {'$/close':>8} {'Counter':>8} {'Worst $':>10} {'Forced':>7}")
    print("-" * 100)
    for label, r in results:
        print(f"{label:<50} ${r['per_hr']:>7.2f} {r['closes']:>7} ${r['avg']:>7.2f} {r['counter_opens']:>8} ${r['worst_floating']:>9.2f} {r['forced_unwinds']:>7}")
    
    mt5.shutdown()

if __name__ == "__main__":
    main()
