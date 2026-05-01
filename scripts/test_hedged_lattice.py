#!/usr/bin/env python3
"""
HEDGED LATTICE — User's concept: locked PnL base + oscillation harvesting

Concept 1: Full Hedge (BUY+SELL at same level)
- Net PnL ≈ 0 always (minus spread cost)
- Harvest oscillations around the hedge

Concept 2: Locked Spread (SELL below BUY by step size)
- Net PnL is FIXED at -step regardless of price
- Harvest oscillations around the locked pair

Concept 3: Oscillation harvesting around locked base
- Keep locked pair as anchor
- Open new positions on moves, close on reversals
- Bounded floating PnL, high close frequency
"""
import MetaTrader5 as mt5
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).parent))

from penetration_lattice_lab_v2 import spread_price, unit_pnl_usd
from dataclasses import dataclass, field

mt5.initialize()


@dataclass
class Position:
    direction: str
    entry_price: float
    opened_idx: int
    is_locked: bool = False  # Part of the locked pair


@dataclass
class SymbolState:
    symbol: str
    realized_closes: int = 0
    realized_net_usd: float = 0.0
    anchor_resets: int = 0
    max_open_total: int = 0
    final_open: int = 0
    max_floating: float = 0.0
    min_floating: float = 0.0


def run_hedged_lattice(symbol: str, bars: list, cfg: dict) -> SymbolState:
    """
    Test hedged lattice concepts on real BTC data.
    
    Strategy:
    1. Open locked pair (SELL at anchor, BUY at anchor+step)
    2. When price moves X steps away, open new position in direction of move
    3. When price reverses Y steps, close the oscillation position
    4. Locked pair stays forever (or rebalances on major trend change)
    """
    if not bars or len(bars) < 500:
        return SymbolState(symbol=symbol)

    info = mt5.symbol_info(symbol)
    if info is None:
        return SymbolState(symbol=symbol)

    spread_px = spread_price(info)
    pip_px = 0.01  # BTCUSD pip size
    
    # Config
    step = cfg.get("step", 50.0)
    oscillation_trigger = cfg.get("oscillation_trigger", 2)  # Open after N steps away
    oscillation_close = cfg.get("oscillation_close", 1)  # Close after N steps reversal
    max_oscillation_positions = cfg.get("max_oscillation", 10)  # Max oscillation positions per side
    mode = cfg.get("mode", "locked_spread")  # "full_hedge" or "locked_spread"

    positions = []
    realized = 0.0
    closes = 0
    max_open_total = 0
    anchor_resets = 0
    max_floating = 0.0
    min_floating = 0.0
    
    anchor = bars[0]["close"]
    
    # Open initial locked pair
    if mode == "full_hedge":
        # BUY+SELL at same price
        positions.append(Position(direction="BUY", entry_price=anchor, opened_idx=0, is_locked=True))
        positions.append(Position(direction="SELL", entry_price=anchor, opened_idx=0, is_locked=True))
    else:  # locked_spread
        # SELL at anchor, BUY at anchor+step (locked net = -step)
        positions.append(Position(direction="SELL", entry_price=anchor, opened_idx=0, is_locked=True))
        positions.append(Position(direction="BUY", entry_price=anchor + step, opened_idx=0, is_locked=True))

    last_bar_time = int(bars[0]["time"])
    last_oscillation_sell = None
    last_oscillation_buy = None
    sell_distance = 0
    buy_distance = 0

    for idx in range(1, len(bars)):
        bar = bars[idx]
        if int(bar["time"]) <= last_bar_time:
            continue
        last_bar_time = int(bar["time"])

        # Calculate floating PnL
        total_floating = 0.0
        for p in positions:
            if p.direction == "BUY":
                total_floating += unit_pnl_usd(symbol, "BUY", p.entry_price, bar["close"], spread_px)
            else:
                total_floating += unit_pnl_usd(symbol, "SELL", p.entry_price, bar["close"], spread_px)
        
        max_floating = max(max_floating, total_floating)
        min_floating = min(min_floating, total_floating)

        # Track distances from anchor for oscillation logic
        sell_levels = [p.entry_price for p in positions if p.direction == "SELL" and not p.is_locked]
        buy_levels = [p.entry_price for p in positions if p.direction == "BUY" and not p.is_locked]
        
        # === Open oscillation positions ===
        # Price moved far above anchor → open SELL
        if bar["high"] >= anchor + (oscillation_trigger * step) and len(sell_levels) < max_oscillation_positions:
            # Open at the trigger level
            entry = anchor + (oscillation_trigger * step)
            if not sell_levels or entry > max(sell_levels):
                positions.append(Position(direction="SELL", entry_price=entry, opened_idx=idx, is_locked=False))
                sell_levels.append(entry)
        
        # Price moved far below anchor → open BUY
        if bar["low"] <= anchor - (oscillation_trigger * step) and len(buy_levels) < max_oscillation_positions:
            entry = anchor - (oscillation_trigger * step)
            if not buy_levels or entry < min(buy_levels):
                positions.append(Position(direction="BUY", entry_price=entry, opened_idx=idx, is_locked=False))
                buy_levels.append(entry)

        # === Close oscillation positions on reversal ===
        # Price reversed back toward anchor → close SELLs
        if bar["low"] <= anchor + (oscillation_close * step):
            to_close = [p for p in positions if p.direction == "SELL" and not p.is_locked]
            for p in to_close:
                pnl = unit_pnl_usd(symbol, "SELL", p.entry_price, bar["low"], spread_px)
                realized += pnl
                positions.remove(p)
                closes += 1
        
        # Price reversed back toward anchor → close BUYs
        if bar["high"] >= anchor - (oscillation_close * step):
            to_close = [p for p in positions if p.direction == "BUY" and not p.is_locked]
            for p in to_close:
                pnl = unit_pnl_usd(symbol, "BUY", p.entry_price, bar["high"], spread_px)
                realized += pnl
                positions.remove(p)
                closes += 1

        # === Anchor rebalance (if price trends far enough, move locked pair) ===
        if abs(bar["close"] - anchor) >= step * 10:
            # Close old locked pair, open new one at current price
            locked = [p for p in positions if p.is_locked]
            for p in locked:
                if p.direction == "SELL":
                    pnl = unit_pnl_usd(symbol, "SELL", p.entry_price, bar["close"], spread_px)
                else:
                    pnl = unit_pnl_usd(symbol, "BUY", p.entry_price, bar["close"], spread_px)
                realized += pnl
                positions.remove(p)
                closes += 1
            
            # Open new locked pair
            anchor = bar["close"]
            if mode == "full_hedge":
                positions.append(Position(direction="BUY", entry_price=anchor, opened_idx=idx, is_locked=True))
                positions.append(Position(direction="SELL", entry_price=anchor, opened_idx=idx, is_locked=True))
            else:
                positions.append(Position(direction="SELL", entry_price=anchor, opened_idx=idx, is_locked=True))
                positions.append(Position(direction="BUY", entry_price=anchor + step, opened_idx=idx, is_locked=True))
            anchor_resets += 1

        max_open_total = max(max_open_total, len(positions))

    return SymbolState(
        symbol=symbol, realized_closes=closes, realized_net_usd=round(realized, 3),
        anchor_resets=anchor_resets, max_open_total=max_open_total,
        final_open=len(positions), max_floating=round(max_floating, 3),
        min_floating=round(min_floating, 3),
    )


def main():
    symbol = "BTCUSD"
    days = 30
    bars_raw = mt5.copy_rates_from_pos(symbol, mt5.TIMEFRAME_M15, 0, 24 * 4 * days)
    bars = [{"time": int(r[0]), "open": float(r[1]), "high": float(r[2]),
             "low": float(r[3]), "close": float(r[4])} for r in bars_raw]
    total_hrs = len(bars) * 15 / 60
    print(f"Testing {symbol} M15, {days} days, {total_hrs:.0f} hours")
    print(f"HEDGED LATTICE — Locked PnL + Oscillation Harvesting")
    print()

    configs = []
    
    # Test different oscillation triggers and close thresholds
    for mode in ["locked_spread", "full_hedge"]:
        for step in [50.0, 75.0, 100.0]:
            for trig in [2, 3, 4]:
                for close in [1, 2]:
                    for max_osc in [5, 10, 20]:
                        configs.append({
                            "label": f"{mode} step={step:.0f} trig={trig} close={close} max_osc={max_osc}",
                            "mode": mode, "step": step, "oscillation_trigger": trig,
                            "oscillation_close": close, "max_oscillation": max_osc,
                        })

    results = []
    for cfg in configs:
        state = run_hedged_lattice(symbol, bars, cfg)
        closes = state.realized_closes
        net = state.realized_net_usd
        avg = net / closes if closes > 0 else 0
        per_hr = net / total_hrs
        
        results.append((cfg["label"], {
            "closes": closes, "net": net, "avg": avg, "per_hr": per_hr,
            "resets": state.anchor_resets, "max_open": state.max_open_total,
            "final_open": state.final_open,
            "max_floating": state.max_floating,
            "min_floating": state.min_floating,
        }))

    results.sort(key=lambda x: x[1]["per_hr"], reverse=True)

    print(f"{'Config':<60} {'$/hr':>9} {'Closes':>7} {'$/close':>9} {'Min Float':>11} {'MaxOpen':>7}")
    print("-" * 110)
    for label, r in results[:20]:
        print(f"{label:<60} ${r['per_hr']:>8.2f} {r['closes']:>7} ${r['avg']:>8.2f} ${r['min_floating']:>10.2f} {r['max_open']:>7}")
    if len(results) > 20:
        print(f"... +{len(results)-20} more")
        for label, r in results[-3:]:
            print(f"{label:<60} ${r['per_hr']:>8.2f} {r['closes']:>7} ${r['avg']:>8.2f} ${r['min_floating']:>10.2f} {r['max_open']:>7}")
    print("=" * 110)

    # Compare to baseline
    baseline_cascade = 45.71  # From earlier EMA ribbon cascade test
    if results:
        best = results[0]
        print(f"\n🏆 BEST HEDGED: {best[0]}")
        print(f"  ${best[1]['per_hr']:.2f}/hr, {best[1]['closes']}c, ${best[1]['avg']:.2f}/close")
        print(f"  Min floating: ${best[1]['min_floating']:.2f}, Max open: {best[1]['max_open']}")
        print(f"  vs cascade baseline ($45.71/hr): {best[1]['per_hr']/baseline_cascade*100:.0f}%")

    mt5.shutdown()


if __name__ == "__main__":
    main()
