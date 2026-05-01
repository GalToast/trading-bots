#!/usr/bin/env python3
"""
FIBONACCI CASCADE SWEEP — finding the optimal close depth

HYPOTHESIS: The bar-replay closes at 100% of bar extreme (bar["low"] for SELLs).
But maybe a Fibonacci level (61.8%, 78.6%) is optimal — deep enough to capture
profit, but not so deep that we miss counter-trend opportunities.

FIBONACCI LEVELS:
  23.6% — shallow retrace (quick close)
  38.2% — standard retrace
  50.0% — mid retrace
  61.8% — golden ratio
  78.6% — deep retrace
  100.0% — full extreme (current bar-replay default)

MEASUREMENT: For a SELL stack at levels 1-10 ($15 apart = $150 total depth):
  - The "move" is anchor → level 10 ($150)
  - The "retracement" is how far price comes back from level 10 toward anchor
  - At 61.8% retrace: price = level_10 - (level_10 - anchor) * 0.618 = level_10 - $92.70
  - At 100% retrace: price = anchor

Each position can close at its OWN Fibonacci level based on its depth in the stack.
This is the "individual deep fibonacci" the user asked about.

Also tests: counter-trend opens — when should we start opening BUYs during the SELL reversal?
"""
import MetaTrader5 as mt5
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).parent))

from penetration_lattice_lab_v2 import dynamic_step, pip_size_for, spread_price, unit_pnl_usd
from dataclasses import dataclass, field, asdict

mt5.initialize()

FIB_LEVELS = [0.236, 0.382, 0.500, 0.618, 0.786, 1.000]

@dataclass
class Ticket:
    direction: str
    entry_price: float
    opened_idx: int
    from_rearm: bool = False
    close_fib_level: float = 1.0  # Fibonacci level to close at

@dataclass
class RearmToken:
    direction: str
    level: float
    level_idx: int
    armed: bool = False
    cooldown_until: int = 0
    is_counter: bool = False

@dataclass 
class SymbolState:
    symbol: str
    mode: str = "fib_test"
    anchor: float = 0.0
    next_sell_level: float = 0.0
    next_buy_level: float = 0.0
    open_tickets: list = field(default_factory=list)
    realized_closes: int = 0
    realized_net_usd: float = 0.0
    rearm_opens: int = 0
    rearm_tokens: list = field(default_factory=list)
    max_open_total: int = 0
    anchor_resets: int = 0
    last_bar_time: int = 0
    counter_rearm_opens: int = 0


def run_fib_test(symbol: str, bars: list, cfg: dict) -> SymbolState:
    """Bar-level simulation with Fibonacci close targets."""
    if not bars:
        state = SymbolState(symbol=symbol)
        return state

    info = mt5.symbol_info(symbol)
    if info is None:
        return SymbolState(symbol=symbol)

    spread_px = spread_price(info)
    base_step = cfg["step"]
    max_open = cfg["max_open_per_side"]
    sell_gap = cfg.get("sell_gap", 0)
    buy_gap = cfg.get("buy_gap", 0)
    fib_close = cfg.get("fib_close", 1.0)  # Fibonacci level for closes
    close_each_at_own_fib = cfg.get("close_each_at_own_fib", False)
    counter_open_fib = cfg.get("counter_open_fib", 0.0)  # Fib level to trigger counter opens
    counter_levels = cfg.get("counter_levels", 0)  # How many counter levels to open

    anchor = bars[0]["close"]
    next_sell = round(anchor + base_step, 5)
    next_buy = round(anchor - base_step, 5)
    tickets = []
    rearm_tokens = []
    realized = 0.0
    closes = 0
    rearm_opens = 0
    max_open_total = 0
    anchor_resets = 0
    counter_opens = 0

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

        # Count positions
        os_main = sum(1 for t in tickets if t.direction == "SELL" and not t.from_rearm)
        ob_main = sum(1 for t in tickets if t.direction == "BUY" and not t.from_rearm)
        os_rearm = sum(1 for t in tickets if t.direction == "SELL" and t.from_rearm)
        ob_rearm = sum(1 for t in tickets if t.direction == "BUY" and t.from_rearm)

        # Open SELLs on uptrend
        while bar["high"] >= next_sell and os_main < max_open:
            lvl = round((next_sell - anchor) / base_step)
            tickets.append(Ticket(direction="SELL", entry_price=next_sell, opened_idx=idx,
                                  from_rearm=False, close_fib_level=fib_close))
            os_main += 1
            cs = dynamic_step(base_step, os_main, adapt_cfg)
            next_sell = round(next_sell + cs, 5)

        # Open BUYs on downtrend
        while bar["low"] <= next_buy and ob_main < max_open:
            lvl = round((anchor - next_buy) / base_step)
            tickets.append(Ticket(direction="BUY", entry_price=next_buy, opened_idx=idx,
                                  from_rearm=False, close_fib_level=fib_close))
            ob_main += 1
            cs = dynamic_step(base_step, ob_main, adapt_cfg)
            next_buy = round(next_buy - cs, 5)

        # === SELL CASCADE CLOSES ===
        sl = sorted([t for t in tickets if t.direction == "SELL"], key=lambda t: t.entry_price, reverse=True)
        while len(sl) > sell_gap and bar["low"] <= sl[sell_gap].entry_price:
            outer = sl[0]
            
            # Compute close target based on Fibonacci
            if close_each_at_own_fib:
                # Each position closes at its OWN fib retracement from its entry
                move_size = outer.entry_price - anchor
                target = outer.entry_price - move_size * outer.close_fib_level
            else:
                # All positions close at the same fib level of the full stack
                if sl:
                    deepest = sl[0].entry_price
                    move_size = deepest - anchor
                    target = deepest - move_size * fib_close
                else:
                    target = sl[sell_gap].entry_price

            close_px = min(target, bar["low"])  # Can't close better than bar low
            if close_px > outer.entry_price:
                close_px = outer.entry_price + 0.01  # Minimum profit

            pnl = unit_pnl_usd(symbol, "SELL", outer.entry_price, close_px, spread_px)
            realized += pnl
            tickets.remove(outer)
            closes += 1
            sl = sorted([t for t in tickets if t.direction == "SELL"], key=lambda t: t.entry_price, reverse=True)

        # === BUY CASCADE CLOSES ===
        bl = sorted([t for t in tickets if t.direction == "BUY"], key=lambda t: t.entry_price)
        while len(bl) > buy_gap and bar["high"] >= bl[buy_gap].entry_price:
            outer = bl[0]
            
            if close_each_at_own_fib:
                move_size = anchor - outer.entry_price
                target = outer.entry_price + move_size * outer.close_fib_level
            else:
                if bl:
                    deepest = bl[0].entry_price
                    move_size = anchor - deepest
                    target = deepest + move_size * fib_close
                else:
                    target = bl[buy_gap].entry_price

            close_px = max(target, bar["high"])
            if close_px < outer.entry_price:
                close_px = outer.entry_price - 0.01

            pnl = unit_pnl_usd(symbol, "BUY", outer.entry_price, close_px, spread_px)
            realized += pnl
            tickets.remove(outer)
            closes += 1
            bl = sorted([t for t in tickets if t.direction == "BUY"], key=lambda t: t.entry_price)

        # Counter-trend opens during reversal
        if counter_open_fib > 0 and counter_levels > 0:
            # Check if price has reversed enough to trigger counter opens
            # For SELL reversal (price went up, now coming down): open BUYs
            if tickets and all(t.direction == "SELL" for t in tickets):
                deepest_sell = max(t.entry_price for t in tickets)
                move_up = deepest_sell - anchor
                current_retrace = deepest_sell - bar["low"]
                retrace_fib = current_retrace / move_up if move_up > 0 else 0
                
                if retrace_fib >= counter_open_fib:
                    # Open counter BUYs
                    for lvl in range(1, counter_levels + 1):
                        counter_level = anchor - lvl * base_step
                        if bar["low"] <= counter_level:
                            tickets.append(Ticket(direction="BUY", entry_price=counter_level,
                                                  opened_idx=idx, from_rearm=True, close_fib_level=fib_close))
                            counter_opens += 1
                            rearm_opens += 1
                    break  # Only open once per bar

            # For BUY reversal (price went down, now going up): open SELLs
            if tickets and all(t.direction == "BUY" for t in tickets):
                deepest_buy = min(t.entry_price for t in tickets)
                move_down = anchor - deepest_buy
                current_retrace = bar["high"] - deepest_buy
                retrace_fib = current_retrace / move_down if move_down > 0 else 0
                
                if retrace_fib >= counter_open_fib:
                    for lvl in range(1, counter_levels + 1):
                        counter_level = anchor + lvl * base_step
                        if bar["high"] >= counter_level:
                            tickets.append(Ticket(direction="SELL", entry_price=counter_level,
                                                  opened_idx=idx, from_rearm=True, close_fib_level=fib_close))
                            counter_opens += 1
                            rearm_opens += 1
                    break

        # Anchor reset
        if not tickets and abs(bar["close"] - anchor) >= base_step:
            anchor = bar["close"]
            next_sell = round(anchor + base_step, 5)
            next_buy = round(anchor - base_step, 5)
            anchor_resets += 1
            rearm_tokens = []

        max_open_total = max(max_open_total, len(tickets))

    state = SymbolState(
        symbol=symbol, anchor=anchor,
        next_sell_level=next_sell, next_buy_level=next_buy,
        open_tickets=[asdict(t) for t in tickets],
        realized_closes=closes, realized_net_usd=round(realized, 3),
        rearm_opens=rearm_opens, max_open_total=max_open_total,
        anchor_resets=anchor_resets, last_bar_time=last_bar_time,
        counter_rearm_opens=counter_opens,
    )
    return state


def main():
    symbol = "BTCUSD"
    days = 30
    bars_raw = mt5.copy_rates_from_pos(symbol, mt5.TIMEFRAME_M15, 0, 24 * 4 * days)
    bars = [{"time": int(r[0]), "open": float(r[1]), "high": float(r[2]),
             "low": float(r[3]), "close": float(r[4])} for r in bars_raw]
    total_hrs = len(bars) * 15 / 60
    print(f"Testing {symbol} M15, {days} days, {total_hrs:.0f} hours")
    print()

    # Sweep matrix
    configs = []
    
    # Test each Fibonacci level (uniform close for all positions)
    for fib in FIB_LEVELS:
        configs.append({
            "label": f"Fib {fib:.1%} (uniform)",
            "fib_close": fib, "close_each_at_own_fib": False,
            "counter_open_fib": 0.0, "counter_levels": 0,
        })

    # Test each Fibonacci level (individual close per position)
    for fib in FIB_LEVELS:
        configs.append({
            "label": f"Fib {fib:.1%} (individual)",
            "fib_close": fib, "close_each_at_own_fib": True,
            "counter_open_fib": 0.0, "counter_levels": 0,
        })

    # Test counter-trend opens at different Fib levels
    for cfib in [0.236, 0.382, 0.500, 0.618]:
        for clvl in [1, 2, 3]:
            configs.append({
                "label": f"Counter open at {cfib:.1%}, {clvl} levels",
                "fib_close": 1.0, "close_each_at_own_fib": False,
                "counter_open_fib": cfib, "counter_levels": clvl,
            })

    results = []
    for cfg in configs:
        c = {
            "step": 15.0, "max_open_per_side": 40,
            "sell_gap": 0, "buy_gap": 0,
            **cfg,
        }
        state = run_fib_test(symbol, bars, c)
        closes = state.realized_closes
        net = state.realized_net_usd
        avg = net / closes if closes > 0 else 0
        per_hr = net / total_hrs
        results.append((cfg["label"], {
            "closes": closes, "net": net, "avg": avg, "per_hr": per_hr,
            "resets": state.anchor_resets, "counter_opens": state.counter_rearm_opens,
            "max_open": state.max_open_total,
        }))

    # Sort by $/hr
    results.sort(key=lambda x: x[1]["per_hr"], reverse=True)

    print(f"{'Config':<55} {'$/hr':>8} {'Closes':>7} {'$/close':>8} {'Counter':>8} {'Resets':>7}")
    print("-" * 95)
    for label, r in results[:25]:
        print(f"{label:<55} ${r['per_hr']:>7.2f} {r['closes']:>7} ${r['avg']:>7.2f} {r['counter_opens']:>8} {r['resets']:>7}")
    print("=" * 95)

    # Find best fib level
    fib_results = [(l, r) for l, r in results if "Fib" in l and "Counter" not in l]
    if fib_results:
        best_fib = max(fib_results, key=lambda x: x[1]["per_hr"])
        print(f"\nBest Fib config: {best_fib[0]} at ${best_fib[1]['per_hr']:.2f}/hr")

        # Compare uniform vs individual
        uniform = [(l, r) for l, r in fib_results if "uniform" in l]
        individual = [(l, r) for l, r in fib_results if "individual" in l]
        if uniform and individual:
            best_u = max(uniform, key=lambda x: x[1]["per_hr"])
            best_i = max(individual, key=lambda x: x[1]["per_hr"])
            print(f"  Best uniform:  {best_u[0]} = ${best_u[1]['per_hr']:.2f}/hr")
            print(f"  Best individual: {best_i[0]} = ${best_i[1]['per_hr']:.2f}/hr")

    # Best counter-open config
    counter = [(l, r) for l, r in results if "Counter" in l]
    if counter:
        best_c = max(counter, key=lambda x: x[1]["per_hr"])
        print(f"  Best counter-open: {best_c[0]} = ${best_c[1]['per_hr']:.2f}/hr")

    # Overall best
    best = results[0]
    print(f"\nOVERALL BEST: {best[0]}")
    print(f"  ${best[1]['per_hr']:.2f}/hr, {best[1]['closes']}c, ${best[1]['avg']:.2f}/close")

    mt5.shutdown()


if __name__ == "__main__":
    main()
