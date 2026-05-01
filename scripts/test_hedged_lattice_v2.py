#!/usr/bin/env python3
"""
HEDGED LATTICE — User's breakthrough concepts tested

CONCEPT 1: Locked Spread
- SELL at anchor, BUY at anchor+step
- Net PnL is FIXED regardless of price movement

CONCEPT 2: Lock-in-Profit Hedge
- When ANY position reaches profit threshold, open opposite
- Locks in the profit without closing (avoids transaction costs)

CONCEPT 3: Oscillation Harvesting
- Open positions on moves away from anchor
- Close on reversals back toward anchor
- Bounded risk, high frequency

COMBINED: Locked spread base + profit-locking + oscillation harvesting
- Base locked pair provides mathematical PnL floor
- Profit-locking captures gains as they happen
- Oscillation harvesting generates continuous income
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
    is_locked: bool = False
    is_profit_locked: bool = False
    locked_pnl: float = 0.0


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
    locked_pnls: float = 0.0


def run_hedged_lattice_v2(symbol: str, bars: list, cfg: dict) -> SymbolState:
    """
    Test hedged lattice concepts on real BTC data.
    """
    if not bars or len(bars) < 500:
        return SymbolState(symbol=symbol)

    info = mt5.symbol_info(symbol)
    if info is None:
        return SymbolState(symbol=symbol)

    spread_px = spread_price(info)
    pip_px = 0.01
    
    # Config
    step = cfg.get("step", 50.0)
    oscillation_trigger = cfg.get("oscillation_trigger", 2)
    oscillation_close = cfg.get("oscillation_close", 1)
    max_oscillation = cfg.get("max_oscillation", 10)
    mode = cfg.get("mode", "locked_spread")
    profit_lock_threshold = cfg.get("profit_lock_threshold", step)  # Lock profit at this level
    enable_profit_lock = cfg.get("enable_profit_lock", True)

    positions = []
    realized = 0.0
    closes = 0
    max_open_total = 0
    anchor_resets = 0
    max_floating = 0.0
    min_floating = 0.0
    locked_pnls = 0.0
    
    anchor = bars[0]["close"]
    
    # Open initial locked pair
    if mode == "full_hedge":
        positions.append(Position(direction="BUY", entry_price=anchor, opened_idx=0, is_locked=True))
        positions.append(Position(direction="SELL", entry_price=anchor, opened_idx=0, is_locked=True))
    else:  # locked_spread
        positions.append(Position(direction="SELL", entry_price=anchor, opened_idx=0, is_locked=True))
        positions.append(Position(direction="BUY", entry_price=anchor + step, opened_idx=0, is_locked=True))

    last_bar_time = int(bars[0]["time"])

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

        # === PROFIT LOCKING (User's Concept 2) ===
        if enable_profit_lock:
            for p in list(positions):
                if p.is_profit_locked or p.is_locked:
                    continue  # Skip already locked or locked pair positions
                
                if p.direction == "BUY":
                    pnl = unit_pnl_usd(symbol, "BUY", p.entry_price, bar["close"], spread_px)
                else:
                    pnl = unit_pnl_usd(symbol, "SELL", p.entry_price, bar["close"], spread_px)
                
                # If position is profitable enough, lock it with opposite order
                if pnl >= profit_lock_threshold:
                    # Open opposite position at current price to lock in profit
                    if p.direction == "BUY":
                        opp_direction = "SELL"
                    else:
                        opp_direction = "BUY"
                    
                    positions.append(Position(
                        direction=opp_direction,
                        entry_price=bar["close"],
                        opened_idx=idx,
                        is_profit_locked=True,
                        locked_pnl=pnl
                    ))
                    p.is_profit_locked = True
                    locked_pnls += pnl
                    closes += 1  # Count the lock as a "close"

        # === OSCILLATION HARVESTING ===
        sell_levels = [p.entry_price for p in positions if p.direction == "SELL" and not p.is_locked and not p.is_profit_locked]
        buy_levels = [p.entry_price for p in positions if p.direction == "BUY" and not p.is_locked and not p.is_profit_locked]
        
        # Price moved far above anchor → open SELL
        if bar["high"] >= anchor + (oscillation_trigger * step) and len(sell_levels) < max_oscillation:
            entry = anchor + (oscillation_trigger * step)
            if not sell_levels or entry > max(sell_levels):
                positions.append(Position(direction="SELL", entry_price=entry, opened_idx=idx))
                sell_levels.append(entry)
        
        # Price moved far below anchor → open BUY
        if bar["low"] <= anchor - (oscillation_trigger * step) and len(buy_levels) < max_oscillation:
            entry = anchor - (oscillation_trigger * step)
            if not buy_levels or entry < min(buy_levels):
                positions.append(Position(direction="BUY", entry_price=entry, opened_idx=idx))
                buy_levels.append(entry)

        # === Close oscillation positions on reversal ===
        if bar["low"] <= anchor + (oscillation_close * step):
            to_close = [p for p in positions if p.direction == "SELL" and not p.is_locked and not p.is_profit_locked]
            for p in to_close:
                pnl = unit_pnl_usd(symbol, "SELL", p.entry_price, bar["low"], spread_px)
                realized += pnl
                positions.remove(p)
                closes += 1
        
        if bar["high"] >= anchor - (oscillation_close * step):
            to_close = [p for p in positions if p.direction == "BUY" and not p.is_locked and not p.is_profit_locked]
            for p in to_close:
                pnl = unit_pnl_usd(symbol, "BUY", p.entry_price, bar["high"], spread_px)
                realized += pnl
                positions.remove(p)
                closes += 1

        # === Anchor rebalance (if price trends far enough) ===
        if abs(bar["close"] - anchor) >= step * 10:
            locked = [p for p in positions if p.is_locked]
            for p in locked:
                if p.direction == "SELL":
                    pnl = unit_pnl_usd(symbol, "SELL", p.entry_price, bar["close"], spread_px)
                else:
                    pnl = unit_pnl_usd(symbol, "BUY", p.entry_price, bar["close"], spread_px)
                realized += pnl
                positions.remove(p)
                closes += 1
            
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
        min_floating=round(min_floating, 3), locked_pnls=round(locked_pnls, 3),
    )


def main():
    symbol = "BTCUSD"
    days = 30
    bars_raw = mt5.copy_rates_from_pos(symbol, mt5.TIMEFRAME_M15, 0, 24 * 4 * days)
    bars = [{"time": int(r[0]), "open": float(r[1]), "high": float(r[2]),
             "low": float(r[3]), "close": float(r[4])} for r in bars_raw]
    total_hrs = len(bars) * 15 / 60
    print(f"Testing {symbol} M15, {days} days, {total_hrs:.0f} hours")
    print(f"HEDGED LATTICE v2 — Locked Spread + Profit Locking + Oscillation Harvesting")
    print()

    configs = []
    
    # Focused test on key combinations
    for mode in ["locked_spread"]:  # locked_spread is more promising than full_hedge
        for step in [50.0, 75.0, 100.0]:
            for trig in [2, 3]:
                for close in [1, 2]:
                    for max_osc in [5, 10]:
                        for lock_thresh_mult in [0.5, 1.0, 1.5]:  # Multiplier of step
                            lock_thresh = step * lock_thresh_mult
                            configs.append({
                                "label": f"step={step:.0f} trig={trig} close={close} max_osc={max_osc} lock={lock_thresh_mult:.1f}x",
                                "mode": mode, "step": step, "oscillation_trigger": trig,
                                "oscillation_close": close, "max_oscillation": max_osc,
                                "profit_lock_threshold": lock_thresh,
                                "enable_profit_lock": True,
                            })
    
    # Baseline without profit locking for comparison
    for step in [50.0, 75.0, 100.0]:
        configs.append({
            "label": f"step={step:.0f} NO profit locking (baseline)",
            "mode": "locked_spread", "step": step, "oscillation_trigger": 2,
            "oscillation_close": 1, "max_oscillation": 10,
            "profit_lock_threshold": step,
            "enable_profit_lock": False,
        })

    results = []
    for cfg in configs:
        state = run_hedged_lattice_v2(symbol, bars, cfg)
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
            "locked_pnls": state.locked_pnls,
        }))

    results.sort(key=lambda x: x[1]["per_hr"], reverse=True)

    print(f"{'Config':<65} {'$/hr':>9} {'Closes':>7} {'$/close':>9} {'Min Float':>11} {'Locked':>9}")
    print("-" * 120)
    for label, r in results[:25]:
        print(f"{label:<65} ${r['per_hr']:>8.2f} {r['closes']:>7} ${r['avg']:>8.2f} ${r['min_floating']:>10.2f} ${r['locked_pnls']:>8.2f}")
    if len(results) > 25:
        print(f"... +{len(results)-25} more")
    print("=" * 120)

    # Compare to baselines
    baseline_cascade = 45.71
    baseline_hedged_no_lock = [(l, r) for l, r in results if "NO profit locking" in l]
    if baseline_hedged_no_lock:
        bl = max(baseline_hedged_no_lock, key=lambda x: x[1]["per_hr"])
        print(f"\nBest hedged without profit locking: ${bl[1]['per_hr']:.2f}/hr")
    
    if results:
        best = results[0]
        print(f"\n🏆 BEST HEDGED: {best[0]}")
        print(f"  ${best[1]['per_hr']:.2f}/hr, {best[1]['closes']}c, ${best[1]['avg']:.2f}/close")
        print(f"  Min floating: ${best[1]['min_floating']:.2f}, Max open: {best[1]['max_open']}")
        print(f"  Locked PnL: ${best[1]['locked_pnls']:.2f}")
        print(f"  vs cascade baseline ($45.71/hr): {best[1]['per_hr']/baseline_cascade*100:.0f}%")

    mt5.shutdown()


if __name__ == "__main__":
    main()
