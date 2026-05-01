#!/usr/bin/env python3
"""
MULTI-TIMEFRAME STACKING — M5 + M15 cascades running in parallel

Run the EMA controller + cascade on both M5 and M15 data for same BTC period.
Each timeframe captures reversals at its own scale:
- M5: micro-reversals (more frequent, smaller per-close)
- M15: macro-reversals (less frequent, larger per-close)

Combined $/hr should be sum of both (they're independent lattices).
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
    from_rearm: bool = False

@dataclass 
class SymbolState:
    symbol: str
    realized_closes: int = 0
    realized_net_usd: float = 0.0
    anchor_resets: int = 0
    max_open_total: int = 0
    open_tickets: list = field(default_factory=list)


def compute_ema(bars: list, period: int) -> list[float]:
    if len(bars) < period:
        return [0.0] * len(bars)
    ema_arr = [0.0] * len(bars)
    m = 2.0 / (period + 1)
    ema_arr[period - 1] = sum(bars[i]["close"] for i in range(period)) / period
    for i in range(period, len(bars)):
        ema_arr[i] = (bars[i]["close"] - ema_arr[i-1]) * m + ema_arr[i-1]
    return ema_arr


def run_ema_controller_cascade(symbol: str, bars: list, cfg: dict) -> SymbolState:
    if not bars or len(bars) < 500:
        return SymbolState(symbol=symbol)

    info = mt5.symbol_info(symbol)
    if info is None:
        return SymbolState(symbol=symbol)

    spread_px = spread_price(info)
    pip_px = 0.01
    base_step = cfg.get("base_step", 15.0)
    max_open = cfg.get("max_open_per_side", 60)
    controller_mode = cfg.get("controller_mode", "ema_ribbon")
    hold_frontier = cfg.get("hold_frontier", 0)
    rebase_on_flat = cfg.get("rebase_on_flat", True)

    ema_periods = [3, 12, 24, 64, 128, 500]
    emas = {p: compute_ema(bars, p) for p in ema_periods}

    tickets = []
    realized = 0.0
    closes = 0
    max_open_total = 0
    anchor_resets = 0
    last_bar_time = int(bars[0]["time"])
    anchor = bars[0]["close"]
    next_sell_level = 1
    next_buy_level = 1

    for idx in range(1, len(bars)):
        bar = bars[idx]
        if int(bar["time"]) <= last_bar_time:
            continue
        last_bar_time = int(bar["time"])

        ema_3 = emas[3][idx]
        ema_12 = emas[12][idx]
        ema_24 = emas[24][idx]
        ema_64 = emas[64][idx]
        ema_500 = emas[500][idx]

        if ema_500 == 0.0:
            continue

        span = abs(ema_3 - ema_500)
        compressed = span <= (base_step * 3.0)
        trend_up = ema_3 > ema_12 > ema_24 > ema_64 and span >= (base_step * 4.0)
        trend_down = ema_3 < ema_12 < ema_24 < ema_64 and span >= (base_step * 4.0)

        if controller_mode == "ema_ribbon":
            if compressed:
                step = max(base_step * 0.75, pip_px)
            elif trend_up or trend_down:
                step = base_step * 1.5
            else:
                step = base_step
            sell_divisor = 2 if trend_up else 1
            buy_divisor = 2 if trend_down else 1
        elif controller_mode == "ema_ribbon_aggressive":
            if compressed:
                step = max(base_step * 0.5, pip_px)
            elif trend_up or trend_down:
                step = base_step * 2.0
            else:
                step = base_step * 1.1
            sell_divisor = 3 if trend_up else 1
            buy_divisor = 3 if trend_down else 1
        else:
            step = base_step
            sell_divisor = 1
            buy_divisor = 1

        # Open SELLs
        os_count = sum(1 for t in tickets if t.direction == "SELL")
        while bar["high"] >= anchor + (next_sell_level * step) and os_count < max_open:
            if sell_divisor <= 1 or next_sell_level % sell_divisor == 0:
                entry = anchor + (next_sell_level * step)
                tickets.append(Ticket(direction="SELL", entry_price=entry, opened_idx=idx, from_rearm=False))
                os_count += 1
            next_sell_level += 1

        # Open BUYs
        ob_count = sum(1 for t in tickets if t.direction == "BUY")
        while bar["low"] <= anchor - (next_buy_level * step) and ob_count < max_open:
            if buy_divisor <= 1 or next_buy_level % buy_divisor == 0:
                entry = anchor - (next_buy_level * step)
                tickets.append(Ticket(direction="BUY", entry_price=entry, opened_idx=idx, from_rearm=False))
                ob_count += 1
            next_buy_level += 1

        # CASCADE CLOSE
        sl = sorted([t for t in tickets if t.direction == "SELL"], key=lambda t: t.entry_price, reverse=True)
        if sl and bar["low"] <= sl[-1].entry_price:
            to_close = sl[:-hold_frontier] if hold_frontier > 0 and len(sl) > hold_frontier else sl
            for t in to_close:
                close_px = bar["low"]
                pnl = unit_pnl_usd(symbol, "SELL", t.entry_price, close_px, spread_px)
                realized += pnl
                tickets.remove(t)
                closes += 1

        bl = sorted([t for t in tickets if t.direction == "BUY"], key=lambda t: t.entry_price)
        if bl and bar["high"] >= bl[-1].entry_price:
            to_close = bl[:-hold_frontier] if hold_frontier > 0 and len(bl) > hold_frontier else bl
            for t in to_close:
                close_px = bar["high"]
                pnl = unit_pnl_usd(symbol, "BUY", t.entry_price, close_px, spread_px)
                realized += pnl
                tickets.remove(t)
                closes += 1

        # Anchor reset
        if not tickets and rebase_on_flat and abs(bar["close"] - anchor) >= step:
            anchor = bar["close"]
            next_sell_level = 1
            next_buy_level = 1
            anchor_resets += 1

        max_open_total = max(max_open_total, len(tickets))

    return SymbolState(
        symbol=symbol, realized_closes=closes, realized_net_usd=round(realized, 3),
        anchor_resets=anchor_resets, max_open_total=max_open_total,
        open_tickets=[{"direction": t.direction, "entry_price": t.entry_price} for t in tickets],
    )


def main():
    symbol = "BTCUSD"
    days = 30

    print(f"Testing {symbol} M5 + M15 stacking, {days} days")
    print(f"Each timeframe runs independently, results combine")
    print()

    configs = []
    m5_steps = [50.0, 100.0, 150.0]
    m15_steps = [25.0, 50.0, 75.0]
    hf_values = [0, 1, 2]

    for m5_step in m5_steps:
        for m15_step in m15_steps:
            for hf in hf_values:
                label = f"M5=${m5_step:.0f} M15=${m15_step:.0f} hf={hf}"
                cfg_m5 = {"base_step": m5_step, "controller_mode": "ema_ribbon", "hold_frontier": hf, "max_open_per_side": 60, "rebase_on_flat": True}
                cfg_m15 = {"base_step": m15_step, "controller_mode": "ema_ribbon", "hold_frontier": hf, "max_open_per_side": 60, "rebase_on_flat": True}
                configs.append((label, cfg_m5, cfg_m15))

    results = []
    for label, cfg_m5, cfg_m15 in configs:
        # M5
        bars5_raw = mt5.copy_rates_from_pos(symbol, mt5.TIMEFRAME_M5, 0, 24 * 12 * days)
        bars5 = [{"time":int(r[0]),"open":float(r[1]),"high":float(r[2]),"low":float(r[3]),"close":float(r[4])} for r in bars5_raw]
        state5 = run_ema_controller_cascade(symbol, bars5, cfg_m5)

        # M15
        bars15_raw = mt5.copy_rates_from_pos(symbol, mt5.TIMEFRAME_M15, 0, 24 * 4 * days)
        bars15 = [{"time":int(r[0]),"open":float(r[1]),"high":float(r[2]),"low":float(r[3]),"close":float(r[4])} for r in bars15_raw]
        state15 = run_ema_controller_cascade(symbol, bars15, cfg_m15)

        total_hrs = len(bars15) * 15 / 60
        m5_per_hr = state5.realized_net_usd / total_hrs
        m15_per_hr = state15.realized_net_usd / total_hrs
        combined_net = state5.realized_net_usd + state15.realized_net_usd
        combined_closes = state5.realized_closes + state15.realized_closes
        combined_per_hr = combined_net / total_hrs
        combined_avg = combined_net / combined_closes if combined_closes > 0 else 0

        results.append((label, {
            "m5_net": state5.realized_net_usd, "m5_closes": state5.realized_closes, "m5_per_hr": m5_per_hr,
            "m15_net": state15.realized_net_usd, "m15_closes": state15.realized_closes, "m15_per_hr": m15_per_hr,
            "combined_net": combined_net, "combined_closes": combined_closes,
            "combined_per_hr": combined_per_hr, "combined_avg": combined_avg,
        }))
        print(f"  {label}: M5=${m5_per_hr:.2f}/hr, M15=${m15_per_hr:.2f}/hr, COMBINED=${combined_per_hr:.2f}/hr")

    results.sort(key=lambda x: x[1]["combined_per_hr"], reverse=True)

    print()
    print(f"{'Config':<35} {'M5 $/hr':>9} {'M15 $/hr':>9} {'Combined':>9} {'Closes':>7} {'Avg $/c':>9}")
    print("-" * 85)
    for label, r in results[:15]:
        print(f"{label:<35} ${r['m5_per_hr']:>8.2f} ${r['m15_per_hr']:>8.2f} ${r['combined_per_hr']:>8.2f} {r['combined_closes']:>7} ${r['combined_avg']:>8.2f}")
    if len(results) > 15:
        print(f"... +{len(results)-15} more")
    print("=" * 85)

    if results:
        best = results[0]
        print(f"\nBEST COMBINATION: {label}")
        print(f"  Combined: ${best['combined_per_hr']:.2f}/hr")
        print(f"  M5 alone: ${best['m5_per_hr']:.2f}/hr ({best['m5_closes']} closes)")
        print(f"  M15 alone: ${best['m15_per_hr']:.2f}/hr ({best['m15_closes']} closes)")
        m15_only = max(results, key=lambda x: x[1]["m15_per_hr"])
        print(f"  vs M15-only best (${m15_only[1]['m15_per_hr']:.2f}/hr): +{(best['combined_per_hr']/abs(m15_only[1]['m15_per_hr'])*100 - 100):+.0f}%")

    mt5.shutdown()


if __name__ == "__main__":
    main()
