#!/usr/bin/env python3
"""
EMA RIBBON CONTROLLER + CASCADE — Synthesis of snake study and cascade close

Combines:
1. Snake study's EMA ribbon controller (dynamic step sizing, asymmetric opening)
2. Cascade close at bar extreme (maximum reversal profit)
3. Deep stacking for spread coverage on BTC

The EMA ribbon tells us:
- WHEN to widen/narrow steps (compression vs trend)
- WHICH direction to favor (asymmetric opening)
- WHEN to rebase the anchor (flat market)

The cascade close captures:
- Maximum profit per reversal (bar extreme)
- All positions close together (no waiting)
"""
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
    pip_px = 0.01  # BTCUSD pip size
    base_step = cfg.get("base_step", 15.0)
    max_open = cfg.get("max_open_per_side", 60)
    controller_mode = cfg.get("controller_mode", "ema_ribbon")  # "ema_ribbon", "ema_ribbon_aggressive", "static"
    hold_frontier = cfg.get("hold_frontier", 0)  # How many outermost positions to NOT close
    rebase_on_flat = cfg.get("rebase_on_flat", True)

    # Compute EMAs
    ema_periods = [3, 12, 24, 64, 128, 500]
    emas = {p: compute_ema(bars, p) for p in ema_periods}

    tickets = []
    realized = 0.0
    closes = 0
    max_open_total = 0
    anchor_resets = 0
    last_bar_time = int(bars[0]["time"])

    # Lattice state
    anchor = bars[0]["close"]
    next_sell_level = 1
    next_buy_level = 1

    for idx in range(1, len(bars)):
        bar = bars[idx]
        if int(bar["time"]) <= last_bar_time:
            continue
        last_bar_time = int(bar["time"])

        # Get EMA values
        ema_3 = emas[3][idx]
        ema_12 = emas[12][idx]
        ema_24 = emas[24][idx]
        ema_64 = emas[64][idx]
        ema_128 = emas[128][idx]
        ema_500 = emas[500][idx]

        if ema_500 == 0.0:
            continue

        # === EMA RIBBON CONTROLLER ===
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
        else:  # static
            step = base_step
            sell_divisor = 1
            buy_divisor = 1

        # === Open SELLs ===
        os_count = sum(1 for t in tickets if t.direction == "SELL")
        while bar["high"] >= anchor + (next_sell_level * step) and os_count < max_open:
            if sell_divisor <= 1 or next_sell_level % sell_divisor == 0:
                entry = anchor + (next_sell_level * step)
                tickets.append(Ticket(direction="SELL", entry_price=entry,
                                      opened_idx=idx, from_rearm=False))
                os_count += 1
            next_sell_level += 1

        # === Open BUYs ===
        ob_count = sum(1 for t in tickets if t.direction == "BUY")
        while bar["low"] <= anchor - (next_buy_level * step) and ob_count < max_open:
            if buy_divisor <= 1 or next_buy_level % buy_divisor == 0:
                entry = anchor - (next_buy_level * step)
                tickets.append(Ticket(direction="BUY", entry_price=entry,
                                      opened_idx=idx, from_rearm=False))
                ob_count += 1
            next_buy_level += 1

        # === CASCADE CLOSE at bar extreme ===
        # Close ALL profitable positions at bar extreme
        sl = sorted([t for t in tickets if t.direction == "SELL"], key=lambda t: t.entry_price, reverse=True)
        if sl and bar["low"] <= sl[-1].entry_price:  # Crossed below shallowest SELL
            if hold_frontier > 0:
                # Hold outermost N positions
                to_close = sl[:-hold_frontier] if len(sl) > hold_frontier else sl
            else:
                to_close = sl
            
            for t in to_close:
                close_px = bar["low"]
                pnl = unit_pnl_usd(symbol, "SELL", t.entry_price, close_px, spread_px)
                realized += pnl
                tickets.remove(t)
                closes += 1

        bl = sorted([t for t in tickets if t.direction == "BUY"], key=lambda t: t.entry_price)
        if bl and bar["high"] >= bl[-1].entry_price:  # Crossed above shallowest BUY
            if hold_frontier > 0:
                to_close = bl[:-hold_frontier] if len(bl) > hold_frontier else bl
            else:
                to_close = bl
            
            for t in to_close:
                close_px = bar["high"]
                pnl = unit_pnl_usd(symbol, "BUY", t.entry_price, close_px, spread_px)
                realized += pnl
                tickets.remove(t)
                closes += 1

        # === Anchor reset ===
        if not tickets and rebase_on_flat and abs(bar["close"] - anchor) >= step:
            anchor = bar["close"]
            next_sell_level = 1
            next_buy_level = 1
            anchor_resets += 1

        max_open_total = max(max_open_total, len(tickets))

    return SymbolState(
        symbol=symbol, realized_closes=closes, realized_net_usd=round(realized, 3),
        anchor_resets=anchor_resets, max_open_total=max_open_total,
        open_tickets=[asdict(t) for t in tickets],
    )


def main():
    symbol = "BTCUSD"
    days = 30
    bars_raw = mt5.copy_rates_from_pos(symbol, mt5.TIMEFRAME_M15, 0, 24 * 4 * days)
    bars = [{"time": int(r[0]), "open": float(r[1]), "high": float(r[2]),
             "low": float(r[3]), "close": float(r[4])} for r in bars_raw]
    total_hrs = len(bars) * 15 / 60
    print(f"Testing {symbol} M15, {days} days, {total_hrs:.0f} hours")
    print(f"EMA Ribbon Controller + Cascade Close at Bar Extreme")
    print()

    configs = []
    
    # Controller mode × base step × hold_frontier sweep
    for mode in ["static", "ema_ribbon", "ema_ribbon_aggressive"]:
        for step in [15.0, 25.0, 50.0]:
            for hf in [0, 1, 2]:
                configs.append({
                    "label": f"{mode} step={step:.0f} hf={hf}",
                    "controller_mode": mode, "base_step": step, "hold_frontier": hf,
                })

    results = []
    for cfg in configs:
        c = {"max_open_per_side": 60, "rebase_on_flat": True, **cfg}
        state = run_ema_controller_cascade(symbol, bars, c)
        closes = state.realized_closes
        net = state.realized_net_usd
        avg = net / closes if closes > 0 else 0
        per_hr = net / total_hrs
        results.append((cfg["label"], {
            "closes": closes, "net": net, "avg": avg, "per_hr": per_hr,
            "resets": state.anchor_resets, "max_open": state.max_open_total,
        }))

    results.sort(key=lambda x: x[1]["per_hr"], reverse=True)

    print(f"{'Config':<45} {'$/hr':>9} {'Closes':>7} {'$/close':>9} {'Resets':>7}")
    print("-" * 80)
    for label, r in results[:15]:
        print(f"{label:<45} ${r['per_hr']:>8.2f} {r['closes']:>7} ${r['avg']:>8.2f} {r['resets']:>7}")
    if len(results) > 15:
        print(f"... +{len(results)-15} more")
        for label, r in results[-3:]:
            print(f"{label:<45} ${r['per_hr']:>8.2f} {r['closes']:>7} ${r['avg']:>8.2f} {r['resets']:>7}")
    print("=" * 80)

    if results:
        best = results[0]
        print(f"\nOVERALL BEST: {best[0]}")
        print(f"  ${best[1]['per_hr']:.2f}/hr, {best[1]['closes']}c, ${best[1]['avg']:.2f}/close")

        # Compare to baselines
        baseline = [(l, r) for l, r in results if "static" in l and "hf=0" in l]
        if baseline:
            bl = max(baseline, key=lambda x: x[1]["per_hr"])
            print(f"\nBest static: ${bl[1]['per_hr']:.2f}/hr")
            print(f"Best EMA controller: ${best[1]['per_hr']:.2f}/hr")
            print(f"Improvement: {(best[1]['per_hr']/abs(bl[1]['per_hr'])*100 - 100):+.0f}%")

    mt5.shutdown()


if __name__ == "__main__":
    main()
