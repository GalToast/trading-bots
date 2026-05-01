#!/usr/bin/env python3
"""
EMA-BASED FIBONACCI CASCADE SWEEP

User's idea: Use EMA as a dynamic reference instead of fixed step size.
The space between entry price and EMA becomes the Fibonacci range.

For a SELL at $75,000 with 100EMA at $74,500:
  Distance to EMA: $500
  23.6% fib = $75,000 - 0.236 × $500 = $74,882
  61.8% fib = $75,000 - 0.618 × $500 = $74,691
  100% fib = $75,000 - 1.0 × $500 = $74,500 (at the EMA)

This gives MUCH larger targets than $15 step-based fib levels.
The EMA acts as a dynamic support/resistance level.

Also tests: different EMA periods (20, 50, 100, 200), different fib levels.
"""
import MetaTrader5 as mt5
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).parent))

from penetration_lattice_lab_v2 import dynamic_step, spread_price, unit_pnl_usd
from dataclasses import dataclass, field, asdict

mt5.initialize()

def compute_ema(bars: list, period: int) -> list[float]:
    """Compute EMA from bar close prices."""
    if len(bars) < period:
        return [0.0] * len(bars)
    
    ema = [0.0] * len(bars)
    multiplier = 2.0 / (period + 1)
    
    # Seed with SMA
    ema[period - 1] = sum(bars[i]["close"] for i in range(period)) / period
    
    for i in range(period, len(bars)):
        ema[i] = (bars[i]["close"] - ema[i-1]) * multiplier + ema[i-1]
    
    return ema

FIB_LEVELS = [0.236, 0.382, 0.500, 0.618, 0.786, 1.000]

@dataclass
class Ticket:
    direction: str
    entry_price: float
    opened_idx: int
    close_target: float  # EMA-based Fibonacci target
    from_rearm: bool = False

@dataclass 
class SymbolState:
    symbol: str
    realized_closes: int = 0
    realized_net_usd: float = 0.0
    anchor_resets: int = 0
    max_open_total: int = 0
    open_tickets: list = field(default_factory=list)


def run_ema_fib_cascade(symbol: str, bars: list, cfg: dict) -> SymbolState:
    if not bars or len(bars) < 200:
        return SymbolState(symbol=symbol)

    info = mt5.symbol_info(symbol)
    if info is None:
        return SymbolState(symbol=symbol)

    spread_px = spread_price(info)
    base_step = cfg["step"]
    max_open = cfg["max_open_per_side"]
    sell_gap = cfg.get("sell_gap", 0)
    buy_gap = cfg.get("buy_gap", 0)
    fib_level = cfg.get("fib_level", 0.618)
    ema_period = cfg.get("ema_period", 100)
    close_mode = cfg.get("close_mode", "ema_fib")  # "ema_fib" or "ema_fib_then_cascade"

    # Compute EMAs
    ema_sells = compute_ema(bars, ema_period)  # EMA for SELL close targets (below price)
    ema_buys = compute_ema(bars, ema_period)   # EMA for BUY close targets (above price)

    anchor = bars[0]["close"]
    next_sell = round(anchor + base_step, 5)
    next_buy = round(anchor - base_step, 5)
    tickets = []
    realized = 0.0
    closes = 0
    max_open_total = 0
    anchor_resets = 0

    adapt_cfg = type("Cfg", (), {
        "adaptive_step_threshold_1": 10, "adaptive_step_threshold_2": 20,
        "adaptive_step_multiplier_1": 1.5, "adaptive_step_multiplier_2": 2.0,
    })()

    last_bar_time = int(bars[0]["time"])

    for idx in range(1, len(bars)):
        bar = bars[idx]
        if int(bar["time"]) <= last_bar_time:
            continue
        last_bar_time = int(bar["time"])

        ema_val = ema_sells[idx]
        if ema_val == 0.0:
            continue

        # === Open SELLs ===
        os_main = sum(1 for t in tickets if t.direction == "SELL" and not t.from_rearm)
        while bar["high"] >= next_sell and os_main < max_open:
            # Compute EMA-based fib target for this SELL
            dist_to_ema = next_sell - ema_val  # Positive if price > EMA (uptrend)
            if dist_to_ema > 0:
                close_target = next_sell - dist_to_ema * fib_level
            else:
                # Price below EMA - use step-based target as fallback
                close_target = next_sell - base_step
            
            tickets.append(Ticket(direction="SELL", entry_price=next_sell, opened_idx=idx,
                                  close_target=close_target, from_rearm=False))
            os_main += 1
            cs = dynamic_step(base_step, os_main, adapt_cfg)
            next_sell = round(next_sell + cs, 5)

        # === Open BUYs ===
        ob_main = sum(1 for t in tickets if t.direction == "BUY" and not t.from_rearm)
        while bar["low"] <= next_buy and ob_main < max_open:
            dist_to_ema = ema_val - next_buy  # Positive if EMA > price (downtrend)
            if dist_to_ema > 0:
                close_target = next_buy + dist_to_ema * fib_level
            else:
                close_target = next_buy + base_step
            
            tickets.append(Ticket(direction="BUY", entry_price=next_buy, opened_idx=idx,
                                  close_target=close_target, from_rearm=False))
            ob_main += 1
            cs = dynamic_step(base_step, ob_main, adapt_cfg)
            next_buy = round(next_buy - cs, 5)

        # === EMA Fib per-position close: SELLs ===
        for t in list(tickets):
            if t.direction == "SELL" and bar["low"] <= t.close_target:
                close_px = min(t.close_target, bar["low"])
                pnl = unit_pnl_usd(symbol, "SELL", t.entry_price, close_px, spread_px)
                realized += pnl
                tickets.remove(t)
                closes += 1

        # === EMA Fib per-position close: BUYs ===
        for t in list(tickets):
            if t.direction == "BUY" and bar["high"] >= t.close_target:
                close_px = max(t.close_target, bar["high"])
                pnl = unit_pnl_usd(symbol, "BUY", t.entry_price, close_px, spread_px)
                realized += pnl
                tickets.remove(t)
                closes += 1

        # === Cascade fallback (for positions that haven't hit EMA fib target) ===
        if close_mode == "ema_fib_then_cascade":
            sl = sorted([t for t in tickets if t.direction == "SELL"], key=lambda t: t.entry_price, reverse=True)
            while len(sl) > sell_gap and bar["low"] <= sl[sell_gap].entry_price:
                outer = sl[0]
                close_px = bar["low"]
                pnl = unit_pnl_usd(symbol, "SELL", outer.entry_price, close_px, spread_px)
                realized += pnl
                tickets.remove(outer)
                closes += 1
                sl = sorted([t for t in tickets if t.direction == "SELL"], key=lambda t: t.entry_price, reverse=True)

            bl = sorted([t for t in tickets if t.direction == "BUY"], key=lambda t: t.entry_price)
            while len(bl) > buy_gap and bar["high"] >= bl[buy_gap].entry_price:
                outer = bl[0]
                close_px = bar["high"]
                pnl = unit_pnl_usd(symbol, "BUY", outer.entry_price, close_px, spread_px)
                realized += pnl
                tickets.remove(outer)
                closes += 1
                bl = sorted([t for t in tickets if t.direction == "BUY"], key=lambda t: t.entry_price)

        # === Anchor reset ===
        if not tickets and abs(bar["close"] - anchor) >= base_step:
            anchor = bar["close"]
            next_sell = round(anchor + base_step, 5)
            next_buy = round(anchor - base_step, 5)
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
    print(f"EMA-based Fibonacci: target = entry ± (distance_to_EMA × fib)")
    print()

    configs = []

    # EMA period sweep × Fibonacci level sweep (pure fib close)
    for ema_p in [20, 50, 100, 200]:
        for fib in FIB_LEVELS:
            configs.append({
                "label": f"EMA{ema_p} Fib {fib:.1%} (pure)",
                "ema_period": ema_p, "fib_level": fib, "close_mode": "ema_fib",
            })

    # EMA period sweep × Fibonacci level sweep (fib + cascade fallback)
    for ema_p in [20, 50, 100, 200]:
        for fib in [0.382, 0.618, 1.0]:
            configs.append({
                "label": f"EMA{ema_p} Fib {fib:.1%} + cascade",
                "ema_period": ema_p, "fib_level": fib, "close_mode": "ema_fib_then_cascade",
            })

    results = []
    for cfg in configs:
        c = {
            "step": 15.0, "max_open_per_side": 40,
            "sell_gap": 0, "buy_gap": 0,
            **cfg,
        }
        state = run_ema_fib_cascade(symbol, bars, c)
        closes = state.realized_closes
        net = state.realized_net_usd
        avg = net / closes if closes > 0 else 0
        per_hr = net / total_hrs
        results.append((cfg["label"], {
            "closes": closes, "net": net, "avg": avg, "per_hr": per_hr,
            "resets": state.anchor_resets, "max_open": state.max_open_total,
        }))

    # Sort by $/hr
    results.sort(key=lambda x: x[1]["per_hr"], reverse=True)

    print(f"{'Config':<50} {'$/hr':>9} {'Closes':>7} {'$/close':>9} {'Resets':>7}")
    print("-" * 85)
    for label, r in results[:20]:
        print(f"{label:<50} ${r['per_hr']:>8.2f} {r['closes']:>7} ${r['avg']:>8.2f} {r['resets']:>7}")
    if len(results) > 20:
        print(f"... and {len(results)-20} more configs (showing worst 5)")
        for label, r in results[-5:]:
            print(f"{label:<50} ${r['per_hr']:>8.2f} {r['closes']:>7} ${r['avg']:>8.2f} {r['resets']:>7}")
    print("=" * 85)

    # Best by EMA period
    for ema_p in [20, 50, 100, 200]:
        ema_results = [(l, r) for l, r in results if f"EMA{ema_p}" in l]
        if ema_results:
            best = ema_results[0]
            print(f"\nBest EMA{ema_p}: {best[0]} at ${best[1]['per_hr']:.2f}/hr")

    # Best overall
    if results:
        best = results[0]
        print(f"\nOVERALL BEST: {best[0]}")
        print(f"  ${best[1]['per_hr']:.2f}/hr, {best[1]['closes']}c, ${best[1]['avg']:.2f}/close")

    mt5.shutdown()


if __name__ == "__main__":
    main()
