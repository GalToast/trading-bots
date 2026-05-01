#!/usr/bin/env python3
"""
TRAILING CASCADE CLOSE — bridging the bar-replay gap

The bar-replay ($8,767/hr) closes ALL positions at bar extremes.
The live engine ($5/close) closes at trigger levels.

This simulates the live engine with a trailing cascade close:
1. When cascade trigger fires (gap=0 level crossed), DON'T close yet
2. Track the reversal extreme (lowest ask for SELL cascade, highest bid for BUY)
3. When price retraces N steps back from the extreme, close ALL positions
4. The trailing distance N is the key parameter to sweep

This is the closest live-executable approximation of "close at bar extreme."
"""
import MetaTrader5 as mt5
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).parent))

from penetration_lattice_lab_v2 import dynamic_step, pip_size_for, spread_price, unit_pnl_usd
from dataclasses import dataclass, field, asdict

mt5.initialize()

@dataclass
class Ticket:
    direction: str
    entry_price: float
    opened_idx: int
    from_rearm: bool = False

@dataclass
class CascadeState:
    """Tracks the active cascade reversal."""
    active: bool = False
    direction: str = ""  # "SELL" or "BUY" — the side being cascade-closed
    extreme_price: float = 0.0  # Best reversal price seen (low for SELL cascade, high for BUY)
    trigger_bar_idx: int = 0  # Bar index where cascade triggered

@dataclass 
class SymbolState:
    symbol: str
    realized_closes: int = 0
    realized_net_usd: float = 0.0
    anchor_resets: int = 0
    max_open_total: int = 0
    open_tickets: list = field(default_factory=list)


def run_trailing_cascade(symbol: str, bars: list, cfg: dict) -> SymbolState:
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
    trail_steps = cfg.get("trail_steps", 0.0)  # Steps to retrace before closing
    use_bar_close = cfg.get("use_bar_close", False)  # Close at end of bar instead of trailing

    anchor = bars[0]["close"]
    next_sell = round(anchor + base_step, 5)
    next_buy = round(anchor - base_step, 5)
    tickets = []
    realized = 0.0
    closes = 0
    max_open_total = 0
    anchor_resets = 0
    cascade = CascadeState()
    close_buffer = []  # Positions queued for close at bar end

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

        # === Process close buffer (bar-close mode) ===
        if use_bar_close and close_buffer:
            for ticket, close_px in close_buffer:
                pnl = unit_pnl_usd(symbol, ticket.direction, ticket.entry_price, close_px, spread_px)
                realized += pnl
                tickets.remove(ticket)
                closes += 1
            close_buffer = []

        # === Deactivate cascade at new bar (bar-close mode) ===
        if use_bar_close and cascade.active:
            cascade.active = False

        # === Count positions ===
        os_main = sum(1 for t in tickets if t.direction == "SELL" and not t.from_rearm)
        ob_main = sum(1 for t in tickets if t.direction == "BUY" and not t.from_rearm)

        # === Open SELLs on uptrend ===
        while bar["high"] >= next_sell and os_main < max_open:
            tickets.append(Ticket(direction="SELL", entry_price=next_sell, opened_idx=idx, from_rearm=False))
            os_main += 1
            cs = dynamic_step(base_step, os_main, adapt_cfg)
            next_sell = round(next_sell + cs, 5)

        # === Open BUYs on downtrend ===
        while bar["low"] <= next_buy and ob_main < max_open:
            tickets.append(Ticket(direction="BUY", entry_price=next_buy, opened_idx=idx, from_rearm=False))
            ob_main += 1
            cs = dynamic_step(base_step, ob_main, adapt_cfg)
            next_buy = round(next_buy - cs, 5)

        # === SELL CASCADE ===
        sl = sorted([t for t in tickets if t.direction == "SELL"], key=lambda t: t.entry_price, reverse=True)
        
        if not cascade.active and len(sl) > sell_gap and bar["low"] <= sl[sell_gap].entry_price:
            # Cascade triggered
            cascade.active = True
            cascade.direction = "SELL"
            cascade.extreme_price = bar["low"]  # Start tracking extreme
            cascade.trigger_bar_idx = idx

        if cascade.active and cascade.direction == "SELL":
            # Update extreme
            if bar["low"] < cascade.extreme_price:
                cascade.extreme_price = bar["low"]

            # Check if we should close
            if use_bar_close:
                # Queue all SELLs for close at bar end, at bar extreme
                for t in sl:
                    close_buffer.append((t, cascade.extreme_price))
            else:
                # Trailing stop: close when price retraces trail_steps from extreme
                trail_px = cascade.extreme_price + trail_steps * base_step
                if bar["high"] >= trail_px:
                    # Close ALL SELLs at the extreme (or current price, whichever is worse)
                    close_px = max(cascade.extreme_price, bar["high"])
                    for t in list(sl):
                        pnl = unit_pnl_usd(symbol, "SELL", t.entry_price, close_px, spread_px)
                        realized += pnl
                        tickets.remove(t)
                        closes += 1
                    cascade.active = False

        sl = sorted([t for t in tickets if t.direction == "SELL"], key=lambda t: t.entry_price, reverse=True)

        # === BUY CASCADE ===
        bl = sorted([t for t in tickets if t.direction == "BUY"], key=lambda t: t.entry_price)
        
        if not cascade.active and len(bl) > buy_gap and bar["high"] >= bl[buy_gap].entry_price:
            cascade.active = True
            cascade.direction = "BUY"
            cascade.extreme_price = bar["high"]
            cascade.trigger_bar_idx = idx

        if cascade.active and cascade.direction == "BUY":
            if bar["high"] > cascade.extreme_price:
                cascade.extreme_price = bar["high"]

            if use_bar_close:
                for t in bl:
                    close_buffer.append((t, cascade.extreme_price))
            else:
                trail_px = cascade.extreme_price - trail_steps * base_step
                if bar["low"] <= trail_px:
                    close_px = min(cascade.extreme_price, bar["low"])
                    for t in list(bl):
                        pnl = unit_pnl_usd(symbol, "BUY", t.entry_price, close_px, spread_px)
                        realized += pnl
                        tickets.remove(t)
                        closes += 1
                    cascade.active = False

        bl = sorted([t for t in tickets if t.direction == "BUY"], key=lambda t: t.entry_price)

        # === Anchor reset ===
        if not tickets and abs(bar["close"] - anchor) >= base_step:
            anchor = bar["close"]
            next_sell = round(anchor + base_step, 5)
            next_buy = round(anchor - base_step, 5)
            anchor_resets += 1
            cascade.active = False
            close_buffer = []

        max_open_total = max(max_open_total, len(tickets))

    # Process any remaining close buffer
    if use_bar_close and close_buffer:
        for ticket, close_px in close_buffer:
            pnl = unit_pnl_usd(symbol, ticket.direction, ticket.entry_price, close_px, spread_px)
            realized += pnl
            if ticket in tickets:
                tickets.remove(ticket)
            closes += 1

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
    print()

    configs = []

    # Trailing cascade sweep
    for ts in [0.0, 0.5, 1.0, 1.5, 2.0, 3.0, 5.0, 8.0, 10.0]:
        configs.append({
            "label": f"Trail {ts} steps",
            "trail_steps": ts, "use_bar_close": False,
        })

    # Bar-close mode (closes at end of bar, at extreme)
    configs.append({
        "label": "Bar-close mode (close at extreme)",
        "trail_steps": 0, "use_bar_close": True,
    })

    # Baseline: immediate close (current live behavior)
    configs.append({
        "label": "BASELINE: immediate (trigger level)",
        "trail_steps": 0, "use_bar_close": False,
        "_immediate": True,  # Flag to use different logic
    })

    results = []
    for cfg in configs:
        c = {
            "step": 15.0, "max_open_per_side": 60,
            "sell_gap": 0, "buy_gap": 0,
            **cfg,
        }

        if cfg.get("_immediate"):
            # Use the original bar-replay engine with gap=0 (closes at trigger levels)
            sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))
            from live_penetration_lattice_unified_shadow import process_symbol as shadow_process, init_symbol_state as shadow_init
            cc = {"step": 15.0, "max_open_per_side": 60, "close_alpha": 1.0, "close_gap": 0,
                  "momentum_gate": False, "rearm_variant": "rearm_lvl2_exc1", "rearm_cooldown_bars": 0, "timeframe": "M15"}
            s = shadow_init(symbol, cc, bars)
            s = shadow_process(symbol, cc, bars, s)
            closes = s.realized_closes
            net = s.realized_net_usd
            resets = s.anchor_resets
            max_open = s.max_open_total
        else:
            state = run_trailing_cascade(symbol, bars, c)
            closes = state.realized_closes
            net = state.realized_net_usd
            resets = state.anchor_resets
            max_open = state.max_open_total

        avg = net / closes if closes > 0 else 0
        per_hr = net / total_hrs
        results.append((cfg["label"], {
            "closes": closes, "net": net, "avg": avg, "per_hr": per_hr,
            "resets": resets, "max_open": max_open,
        }))

    # Sort by $/hr
    results.sort(key=lambda x: x[1]["per_hr"], reverse=True)

    print(f"{'Config':<50} {'$/hr':>10} {'Closes':>7} {'$/close':>9} {'Resets':>7}")
    print("-" * 85)
    for label, r in results:
        print(f"{label:<50} ${r['per_hr']:>9.2f} {r['closes']:>7} ${r['avg']:>8.2f} {r['resets']:>7}")
    print("=" * 85)

    best = results[0]
    baseline = [r for l, r in results if "BASELINE" in l][0]
    bar_replay = 8767.0  # From original test
    print(f"\nBEST TRAILING: {best[0]} at ${best[1]['per_hr']:.2f}/hr")
    print(f"BASELINE (immediate): ${baseline['per_hr']:.2f}/hr")
    print(f"BAR-REPLAY UPPER BOUND: ${bar_replay:.2f}/hr")
    print(f"Gap to bar-replay: {best[1]['per_hr']/bar_replay*100:.1f}%")
    print(f"Improvement over baseline: {(best[1]['per_hr'] - baseline['per_hr'])/abs(baseline['per_hr'])*100:.0f}%")

    mt5.shutdown()


if __name__ == "__main__":
    main()
