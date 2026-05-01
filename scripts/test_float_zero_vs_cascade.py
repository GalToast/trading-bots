#!/usr/bin/env python3
"""
FLOAT-ZERO + CASCADE vs CASCADE ALONE on EMA Ribbon

Challenges:
1. Does float-zero close MORE OFTEN than cascade? (fires when portfolio >= 0, not all reversed)
2. Does combining float-zero + cascade beat cascade alone?
3. Is the $45/hr number real or bar-extreme fantasy?

This tests the exact same EMA ribbon controller but with different close mechanics.
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
    final_open: int = 0
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


def run_ema_controller(symbol: str, bars: list, cfg: dict) -> SymbolState:
    if not bars or len(bars) < 500:
        return SymbolState(symbol=symbol)

    info = mt5.symbol_info(symbol)
    if info is None:
        return SymbolState(symbol=symbol)

    spread_px = spread_price(info)
    pip_px = 0.01
    base_step = cfg.get("base_step", 50.0)
    max_open = cfg.get("max_open_per_side", 60)
    controller_mode = cfg.get("controller_mode", "ema_ribbon")
    hold_frontier = cfg.get("hold_frontier", 1)
    close_mode = cfg.get("close_mode", "cascade")  # "cascade", "float_zero", "both"
    rebase_on_flat = cfg.get("rebase_on_flat", True)

    ema_periods = [3, 12, 24, 64, 128, 500]
    emas = {p: compute_ema(bars, p) for p in ema_periods}

    tickets = []
    realized = 0.0
    closes = 0
    max_open_total = 0
    anchor_resets = 0
    last_bar_time = int(bars[0]["time"])
    float_zero_fires = 0
    cascade_fires = 0

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
        else:
            step = base_step
            sell_divisor = 1
            buy_divisor = 1

        # === Open SELLs ===
        os_count = sum(1 for t in tickets if t.direction == "SELL")
        while bar["high"] >= anchor + (next_sell_level * step) and os_count < max_open:
            if sell_divisor <= 1 or next_sell_level % sell_divisor == 0:
                entry = anchor + (next_sell_level * step)
                tickets.append(Ticket(direction="SELL", entry_price=entry, opened_idx=idx, from_rearm=False))
                os_count += 1
            next_sell_level += 1

        # === Open BUYs ===
        ob_count = sum(1 for t in tickets if t.direction == "BUY")
        while bar["low"] <= anchor - (next_buy_level * step) and ob_count < max_open:
            if buy_divisor <= 1 or next_buy_level % buy_divisor == 0:
                entry = anchor - (next_buy_level * step)
                tickets.append(Ticket(direction="BUY", entry_price=entry, opened_idx=idx, from_rearm=False))
                ob_count += 1
            next_buy_level += 1

        # === Calculate floating PnL ===
        total_floating = 0.0
        for t in tickets:
            if t.direction == "SELL":
                total_floating += unit_pnl_usd(symbol, "SELL", t.entry_price, bar["close"], spread_px)
            else:
                total_floating += unit_pnl_usd(symbol, "BUY", t.entry_price, bar["close"], spread_px)

        # === FLOAT-ZERO CLOSE: fires when total floating >= 0 ===
        if close_mode in ("float_zero", "both") and tickets and total_floating >= 0:
            float_zero_fires += 1
            # Close ALL profitable positions
            to_close = []
            for t in tickets:
                if t.direction == "SELL":
                    pnl = unit_pnl_usd(symbol, "SELL", t.entry_price, bar["close"], spread_px)
                    if pnl > 0:
                        to_close.append((t, bar["close"], pnl))
                else:
                    pnl = unit_pnl_usd(symbol, "BUY", t.entry_price, bar["close"], spread_px)
                    if pnl > 0:
                        to_close.append((t, bar["close"], pnl))

            for t, close_px, pnl in to_close:
                realized += pnl
                tickets.remove(t)
                closes += 1

        # === CASCADE CLOSE: fires when all positions reversed ===
        if close_mode in ("cascade", "both"):
            sl = sorted([t for t in tickets if t.direction == "SELL"], key=lambda t: t.entry_price, reverse=True)
            if sl and bar["low"] <= sl[-1].entry_price:
                cascade_fires += 1
                if hold_frontier > 0:
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
            if bl and bar["high"] >= bl[-1].entry_price:
                cascade_fires += 1
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

    state = SymbolState(
        symbol=symbol, realized_closes=closes, realized_net_usd=round(realized, 3),
        anchor_resets=anchor_resets, max_open_total=max_open_total,
        final_open=len(tickets),
        open_tickets=[asdict(t) for t in tickets],
    )
    state._float_zero_fires = float_zero_fires
    state._cascade_fires = cascade_fires
    return state


def main():
    symbol = "BTCUSD"
    days = 30
    bars_raw = mt5.copy_rates_from_pos(symbol, mt5.TIMEFRAME_M15, 0, 24 * 4 * days)
    bars = [{"time": int(r[0]), "open": float(r[1]), "high": float(r[2]),
             "low": float(r[3]), "close": float(r[4])} for r in bars_raw]
    total_hrs = len(bars) * 15 / 60
    print(f"Testing {symbol} M15, {days} days, {total_hrs:.0f} hours")
    print(f"Float-Zero vs Cascade vs Combined on EMA Ribbon")
    print()

    # Best config from prior test: ema_ribbon step=50 hf=1
    base_cfg = {
        "controller_mode": "ema_ribbon",
        "base_step": 50.0,
        "hold_frontier": 1,
        "max_open_per_side": 60,
        "rebase_on_flat": True,
    }

    results = []
    for close_mode in ["cascade", "float_zero", "both"]:
        c = {**base_cfg, "close_mode": close_mode}
        state = run_ema_controller(symbol, bars, c)
        closes = state.realized_closes
        net = state.realized_net_usd
        avg = net / closes if closes > 0 else 0
        per_hr = net / total_hrs
        results.append((f"{close_mode} (fz={getattr(state, '_float_zero_fires', 0)}, casc={getattr(state, '_cascade_fires', 0)})", {
            "closes": closes, "net": net, "avg": avg, "per_hr": per_hr,
            "resets": state.anchor_resets, "max_open": state.max_open_total,
            "final_open": state.final_open,
        }))

    # Also test different hold_frontier values for each close mode
    for close_mode in ["cascade", "float_zero", "both"]:
        for hf in [0, 1, 2, 3]:
            c = {**base_cfg, "close_mode": close_mode, "hold_frontier": hf}
            state = run_ema_controller(symbol, bars, c)
            closes = state.realized_closes
            net = state.realized_net_usd
            avg = net / closes if closes > 0 else 0
            per_hr = net / total_hrs
            results.append((f"{close_mode} step=50 hf={hf}", {
                "closes": closes, "net": net, "avg": avg, "per_hr": per_hr,
                "resets": state.anchor_resets, "max_open": state.max_open_total,
                "final_open": state.final_open,
            }))

    results.sort(key=lambda x: x[1]["per_hr"], reverse=True)

    print(f"{'Config':<50} {'$/hr':>9} {'Closes':>7} {'$/close':>9} {'Resets':>7} {'Final':>6}")
    print("-" * 95)
    for label, r in results[:20]:
        print(f"{label:<50} ${r['per_hr']:>8.2f} {r['closes']:>7} ${r['avg']:>8.2f} {r['resets']:>7} {r['final_open']:>6}")
    print("=" * 95)

    if results:
        best = results[0]
        print(f"\n🏆 OVERALL BEST: {best[0]}")
        print(f"  ${best[1]['per_hr']:.2f}/hr, {best[1]['closes']}c, ${best[1]['avg']:.2f}/close, {best[1]['final_open']} stranded")

        # Challenge: is the cascade number real?
        cascade_only = [(l, r) for l, r in results if "cascade" in l and "hf=1" in l and "both" not in l]
        float_only = [(l, r) for l, r in results if "float_zero" in l and "hf=1" in l and "both" not in l]
        both = [(l, r) for l, r in results if "both" in l and "hf=1" in l]

        if cascade_only and float_only:
            c = cascade_only[0][1]
            f = float_only[0][1]
            b = both[0][1] if both else None
            print(f"\n=== CLOSE MODE COMPARISON (hf=1) ===")
            print(f"  Cascade only:  ${c['per_hr']:.2f}/hr, {c['closes']}c, ${c['avg']:.2f}/close")
            print(f"  Float-Zero only: ${f['per_hr']:.2f}/hr, {f['closes']}c, ${f['avg']:.2f}/close")
            if b:
                print(f"  Both combined:  ${b['per_hr']:.2f}/hr, {b['closes']}c, ${b['avg']:.2f}/close")

    mt5.shutdown()


if __name__ == "__main__":
    main()
