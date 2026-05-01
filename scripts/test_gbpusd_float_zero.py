#!/usr/bin/env python3
import MetaTrader5 as mt5
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).parent))

from penetration_lattice_lab_v2 import spread_price, unit_pnl_usd
from dataclasses import dataclass, field, asdict

mt5.initialize()

@dataclass
class Ticket:
    direction: str
    entry_price: float
    opened_idx: int

@dataclass 
class SymbolState:
    symbol: str
    realized_closes: int = 0
    realized_net_usd: float = 0.0
    anchor_resets: int = 0
    max_open_total: int = 0
    float_zero_closes: int = 0
    max_adverse_excursion: float = 0.0

def compute_ema(bars: list, period: int) -> list[float]:
    if len(bars) < period:
        return [0.0] * len(bars)
    ema_arr = [0.0] * len(bars)
    m = 2.0 / (period + 1)
    ema_arr[period - 1] = sum(bars[i]["close"] for i in range(period)) / period
    for i in range(period, len(bars)):
        ema_arr[i] = (bars[i]["close"] - ema_arr[i-1]) * m + ema_arr[i-1]
    return ema_arr

def run_hybrid_controller(symbol: str, bars: list, cfg: dict) -> SymbolState:
    if not bars or len(bars) < 100:
        return SymbolState(symbol=symbol)

    info = mt5.symbol_info(symbol)
    spread_px = spread_price(info) if info else 0.00010
    base_step = cfg.get("base_step", 0.00150)
    max_open = cfg.get("max_open", 5)
    hold_frontier = cfg.get("hold_frontier", 0)
    float_zero = cfg.get("float_zero", True)
    controller = cfg.get("controller", "ema_ribbon")

    emas = {p: compute_ema(bars, p) for p in [3, 12, 24, 64, 128, 200]}
    tickets = []
    realized = 0.0
    closes = 0
    float_zero_closes = 0
    max_open_total = 0
    anchor_resets = 0
    mae = 0.0

    anchor = bars[0]["close"]
    next_sell_level = 1
    next_buy_level = 1

    last_bar_time = int(bars[0]["time"])

    for idx in range(1, len(bars)):
        bar = bars[idx]
        if int(bar["time"]) <= last_bar_time:
            continue
        last_bar_time = int(bar["time"])

        e3, e12, e24, e64, e200 = emas[3][idx], emas[12][idx], emas[24][idx], emas[64][idx], emas[200][idx]
        if e200 == 0.0: continue

        span = abs(e3 - e200)
        compressed = span <= (base_step * 3.0)
        trend_up = e3 > e12 > e24 > e64 and span >= (base_step * 4.0)
        trend_down = e3 < e12 < e24 < e64 and span >= (base_step * 4.0)

        step = base_step
        sell_divisor, buy_divisor = 1, 1

        if controller == "ema_ribbon":
            if compressed: step = max(base_step * 0.75, 0.00010)
            elif trend_up or trend_down: step = base_step * 1.5
            sell_divisor = 2 if trend_up else 1
            buy_divisor = 2 if trend_down else 1
        elif controller == "ema_ribbon_aggressive":
            if compressed: step = max(base_step * 0.5, 0.00010)
            elif trend_up or trend_down: step = base_step * 2.0
            sell_divisor = 3 if trend_up else 1
            buy_divisor = 3 if trend_down else 1

        os_count = sum(1 for t in tickets if t.direction == "SELL")
        while bar["high"] >= anchor + (next_sell_level * step) and os_count < max_open:
            if sell_divisor <= 1 or next_sell_level % sell_divisor == 0:
                tickets.append(Ticket("SELL", anchor + (next_sell_level * step), idx))
                os_count += 1
            next_sell_level += 1

        ob_count = sum(1 for t in tickets if t.direction == "BUY")
        while bar["low"] <= anchor - (next_buy_level * step) and ob_count < max_open:
            if buy_divisor <= 1 or next_buy_level % buy_divisor == 0:
                tickets.append(Ticket("BUY", anchor - (next_buy_level * step), idx))
                ob_count += 1
            next_buy_level += 1

        max_open_total = max(max_open_total, len(tickets))

        current_float = 0.0
        for t in tickets:
            current_float += unit_pnl_usd(symbol, t.direction, t.entry_price, bar["close"], spread_px)
        mae = min(mae, current_float)

        if float_zero and current_float >= 0.0 and tickets:
            to_remove = []
            for t in tickets:
                pnl = unit_pnl_usd(symbol, t.direction, t.entry_price, bar["close"], spread_px)
                if pnl > 0:
                    realized += pnl
                    closes += 1
                    float_zero_closes += 1
                    to_remove.append(t)
            for t in to_remove:
                tickets.remove(t)

        to_remove = []
        sl = sorted([t for t in tickets if t.direction == "SELL"], key=lambda t: t.entry_price, reverse=True)
        if sl and bar["low"] <= sl[-1].entry_price - spread_px:
            to_close = sl[:-hold_frontier] if hold_frontier > 0 and len(sl) > hold_frontier else sl
            for t in to_close:
                pnl = unit_pnl_usd(symbol, "SELL", t.entry_price, bar["low"], spread_px)
                if pnl > 0:
                    realized += pnl
                    to_remove.append(t)
                    closes += 1

        bl = sorted([t for t in tickets if t.direction == "BUY"], key=lambda t: t.entry_price)
        if bl and bar["high"] >= bl[-1].entry_price + spread_px:
            to_close = bl[:-hold_frontier] if hold_frontier > 0 and len(bl) > hold_frontier else bl
            for t in to_close:
                pnl = unit_pnl_usd(symbol, "BUY", t.entry_price, bar["high"], spread_px)
                if pnl > 0:
                    realized += pnl
                    to_remove.append(t)
                    closes += 1
                    
        for t in to_remove:
            if t in tickets:
                tickets.remove(t)

        if not tickets and abs(bar["close"] - anchor) >= step:
            anchor = bar["close"]
            next_sell_level = 1
            next_buy_level = 1
            anchor_resets += 1

    return SymbolState(symbol, closes, realized, anchor_resets, max_open_total, float_zero_closes, mae)

def main():
    symbol = "EURUSD"
    days = 5
    # Testing M1 instead of M15 for tighter bounds
    bars_raw = mt5.copy_rates_from_pos(symbol, mt5.TIMEFRAME_M1, 0, 24 * 60 * days)
    bars = [{"time": int(r[0]), "open": float(r[1]), "high": float(r[2]),
             "low": float(r[3]), "close": float(r[4])} for r in bars_raw]
    total_hrs = len(bars) * 1 / 60
    print(f"Testing {symbol} M1, {days} days, {total_hrs:.0f} hours")
    print(f"FLOAT ZERO CASCADE HYBRID V2 on GBPUSD")
    print()

    configs = []
    # Steps for GBPUSD: 0.00050 (5 pips), 0.00100 (10 pips), 0.00200 (20 pips), 0.00025 (2.5 pips)
    for mode in ["ema_ribbon", "ema_ribbon_aggressive", "static"]:
        for step in [0.00025, 0.00050, 0.00100, 0.00200]:
            for hf in [0, 1]:
                for fz in [True, False]:
                    configs.append({
                        "label": f"{mode} step={step*10000:.1f}pips hf={hf} fz={fz}",
                        "controller": mode, "base_step": step, "hold_frontier": hf, "float_zero": fz,
                        "max_open": 5
                    })

    results = []
    for cfg in configs:
        state = run_hybrid_controller(symbol, bars, cfg)
        closes = state.realized_closes
        net = state.realized_net_usd
        per_hr = net / total_hrs
        mae = state.max_adverse_excursion
        # Filter for strict survivability!
        if mae >= -50.0:
            results.append((cfg["label"], {
                "per_hr": per_hr, "closes": closes, "resets": state.anchor_resets,
                "max_open": state.max_open_total, "mae": mae,
                "fz_closes": state.float_zero_closes
            }))

    results.sort(key=lambda x: x[1]["per_hr"], reverse=True)

    print(f"{ 'Config':<45} {'$/hr':>9} {'Closes':>7} {'Resets':>7} {'MaxOp':>7} {'MAE':>9}")
    print("-" * 90)
    for label, r in results[:15]:
        print(f"{label:<45} ${r['per_hr']:>8.2f} {r['closes']:>7} {r['resets']:>7} {r['max_open']:>7} ${r['mae']:>8.2f}")

    if results:
        best = results[0]
        print(f"\nOVERALL BEST: {best[0]}")
        print(f"  ${best[1]['per_hr']:.2f}/hr | MAE: ${best[1]['mae']:.2f} | FZ Closes: {best[1]['fz_closes']}")
    else:
        print("\nNo configurations passed the strict MAE < 50.0 filter.")

    mt5.shutdown()

if __name__ == "__main__":
    main()
