#!/usr/bin/env python3
"""
FIBONACCI PER-POSITION CLOSE SWEEP

User's idea: Each position tracks profit from ITS OWN ENTRY.
When price moves N fib levels away from entry in profit, close.

For a SELL at $75,000 (step = $15):
  23.6% fib = entry - 0.236*step = $75,000 - $3.54 = $74,996.46
  61.8% fib = entry - 0.618*step = $75,000 - $9.27 = $74,990.73
  100% fib  = entry - 1.0*step   = $75,000 - $15.00 = $74,985

Every position gets its own close target. No waiting for cascade.
Close as soon as price hits that level.

This is fundamentally different from cascade (reversal-based close).
This is a PROFIT-TARGET close — each position takes profit at its own level.
"""
import MetaTrader5 as mt5
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).parent))

from penetration_lattice_lab_v2 import dynamic_step, pip_size_for, spread_price, unit_pnl_usd
from dataclasses import dataclass, field, asdict

mt5.initialize()

FIB_LEVELS = [0.236, 0.382, 0.500, 0.618, 0.786, 1.000, 1.618, 2.000, 2.618]

@dataclass
class Ticket:
    direction: str
    entry_price: float
    opened_idx: int
    close_target: float  # Price at which this position closes
    from_rearm: bool = False

@dataclass
class SymbolState:
    symbol: str
    realized_closes: int = 0
    realized_net_usd: float = 0.0
    anchor_resets: int = 0
    max_open_total: int = 0
    open_tickets: list = field(default_factory=list)


def run_fib_per_position(symbol: str, bars: list, cfg: dict) -> SymbolState:
    if not bars:
        return SymbolState(symbol=symbol)

    info = mt5.symbol_info(symbol)
    if info is None:
        return SymbolState(symbol=symbol)

    spread_px = spread_price(info)
    base_step = cfg["step"]
    max_open = cfg["max_open_per_side"]
    sell_gap = cfg.get("sell_gap", 0)
    buy_gap = cfg.get("buy_gap", 0)
    fib_level = cfg.get("fib_level", 1.0)  # Fibonacci multiplier of step
    close_mode = cfg.get("close_mode", "fib")  # "fib" or "cascade" or "fib_then_cascade"

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

        # === Open SELLs ===
        os_main = sum(1 for t in tickets if t.direction == "SELL" and not t.from_rearm)
        while bar["high"] >= next_sell and os_main < max_open:
            close_target = next_sell - base_step * fib_level
            tickets.append(Ticket(direction="SELL", entry_price=next_sell, opened_idx=idx,
                                  close_target=close_target, from_rearm=False))
            os_main += 1
            cs = dynamic_step(base_step, os_main, adapt_cfg)
            next_sell = round(next_sell + cs, 5)

        # === Open BUYs ===
        ob_main = sum(1 for t in tickets if t.direction == "BUY" and not t.from_rearm)
        while bar["low"] <= next_buy and ob_main < max_open:
            close_target = next_buy + base_step * fib_level
            tickets.append(Ticket(direction="BUY", entry_price=next_buy, opened_idx=idx,
                                  close_target=close_target, from_rearm=False))
            ob_main += 1
            cs = dynamic_step(base_step, ob_main, adapt_cfg)
            next_buy = round(next_buy - cs, 5)

        # === Fib per-position close: SELLs ===
        # Close when bar["low"] <= close_target (price dropped enough)
        for t in list(tickets):
            if t.direction == "SELL" and bar["low"] <= t.close_target:
                pnl = unit_pnl_usd(symbol, "SELL", t.entry_price, t.close_target, spread_px)
                realized += pnl
                tickets.remove(t)
                closes += 1

        # === Fib per-position close: BUYs ===
        for t in list(tickets):
            if t.direction == "BUY" and bar["high"] >= t.close_target:
                pnl = unit_pnl_usd(symbol, "BUY", t.entry_price, t.close_target, spread_px)
                realized += pnl
                tickets.remove(t)
                closes += 1

        # === Cascade close (fallback for positions that haven't hit fib target) ===
        if close_mode == "fib_then_cascade":
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
    print(f"Step = $15, so each Fib level = step × Fib")
    print()

    configs = []

    # Pure fib per-position close (no cascade fallback)
    for fib in FIB_LEVELS:
        configs.append({
            "label": f"Fib {fib:.1%} of step (pure fib)",
            "fib_level": fib, "close_mode": "fib",
        })

    # Fib + cascade fallback (positions that don't hit fib target get cascade-closed)
    for fib in FIB_LEVELS:
        configs.append({
            "label": f"Fib {fib:.1%} + cascade fallback",
            "fib_level": fib, "close_mode": "fib_then_cascade",
        })

    # Baseline: pure cascade (gap=0)
    configs.append({
        "label": "BASELINE: pure cascade (gap=0)",
        "fib_level": 1.0, "close_mode": "cascade",
    })

    results = []
    for cfg in configs:
        c = {
            "step": 15.0, "max_open_per_side": 40,
            "sell_gap": 0, "buy_gap": 0,
            **cfg,
        }
        state = run_fib_per_position(symbol, bars, c)
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
    for label, r in results[:25]:
        print(f"{label:<50} ${r['per_hr']:>8.2f} {r['closes']:>7} ${r['avg']:>8.2f} {r['resets']:>7}")
    print("=" * 85)

    # Find best fib-only
    fib_only = [(l, r) for l, r in results if "pure fib" in l and "BASELINE" not in l]
    if fib_only:
        best_fib = max(fib_only, key=lambda x: x[1]["per_hr"])
        print(f"\nBest Fib-only: {best_fib[0]} at ${best_fib[1]['per_hr']:.2f}/hr")

    # Find best fib+cascade
    fib_cascade = [(l, r) for l, r in results if "cascade fallback" in l]
    if fib_cascade:
        best_fc = max(fib_cascade, key=lambda x: x[1]["per_hr"])
        print(f"Best Fib+cascade: {best_fc[0]} at ${best_fc[1]['per_hr']:.2f}/hr")

    baseline = [r for l, r in results if "BASELINE" in l][0]
    print(f"\nBaseline cascade: ${baseline['per_hr']:.2f}/hr")

    best = results[0]
    print(f"\nOVERALL BEST: {best[0]}")
    print(f"  ${best[1]['per_hr']:.2f}/hr, {best[1]['closes']}c, ${best[1]['avg']:.2f}/close")
    print(f"  vs baseline: {(best[1]['per_hr']/abs(baseline['per_hr'])*100 - 100):+.0f}%")

    # Show fib level dollar values
    print(f"\nFib level → dollar target per $15 step:")
    for fib in FIB_LEVELS:
        print(f"  {fib:.1%} × $15 = ${fib*15:.2f}")

    mt5.shutdown()


if __name__ == "__main__":
    main()
