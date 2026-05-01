#!/usr/bin/env python3
"""Hungry Hippo v2 Shapeshifter Backtest — Continuous vs Static.

Tests whether continuous regime-based personality switching beats a
static personality locked at config generation time.

Simulates HH lattice behavior on M1/M5 bars using the 5 personality
profiles defined in hungry_hippo_shapeshifter.json:
  - chop: tight symmetric steps, fast closes
  - chop_aggressive: even tighter, hyper-fast closes
  - breakout: asymmetric, trailing anchor
  - trend: extreme asymmetry, one-sided
  - defensive: minimal exposure, survive uncertainty

Usage:
    python scripts/backtest_shapeshifter_v2.py --symbol NAS100 --timeframe M15 --window 7d
    python scripts/backtest_shapeshifter_v2.py --symbol EURUSD --timeframe M15 --window 7d
    python scripts/backtest_shapeshifter_v2.py --symbol GBPUSD --timeframe M15 --window 7d
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

from regime_detection import regime_score, _compute_adx
from benchmark_regime_segmented import (
    fetch_candles_coinbase,
    normalize_candles,
    _align_btc_candles,
)

ROOT = Path(__file__).resolve().parent.parent
CONFIGS = ROOT / "configs"
REPORTS = ROOT / "reports"

# === Shapeshifter Personality Definitions ===
# Matches hungry_hippo_shapeshifter.json structure
# Each personality: {step_ratio, asymmetry, max_open, alpha, close_style, anchor_mode, escape_bars, escape_threshold}

PERSONALITIES = {
    "chop": {
        "step_ratio": 0.8,       # Tight steps (0.8x ATR)
        "asymmetry": 1.0,        # Symmetric
        "max_open_per_side": 12,
        "close_alpha": 0.2,      # Fast closes
        "close_style": "all_profitable",
        "anchor_mode": "fixed",
        "escape_bars": 8,
        "escape_threshold_usd": 5,
    },
    "chop_aggressive": {
        "step_ratio": 0.7,       # Even tighter
        "asymmetry": 1.0,
        "max_open_per_side": 12,
        "close_alpha": 0.1,      # Hyper-fast closes
        "close_style": "all_profitable",
        "anchor_mode": "fixed",
        "escape_bars": 5,
        "escape_threshold_usd": 3,
    },
    "breakout": {
        "step_ratio": 1.0,       # 1x ATR base
        "asymmetry": 3.0,        # 3:1 asymmetric
        "max_open_per_side": 8,
        "close_alpha": 0.5,
        "close_style": "all_profitable",
        "anchor_mode": "trailing",
        "escape_bars": 10,
        "escape_threshold_usd": 8,
    },
    "trend": {
        "step_ratio": 1.2,       # Wider steps
        "asymmetry": 8.0,        # Extreme asymmetry
        "max_open_per_side": 4,
        "close_alpha": 0.8,      # Close outer positions
        "close_style": "outer",
        "anchor_mode": "trailing",
        "escape_bars": 15,
        "escape_threshold_usd": 10,
    },
    "defensive": {
        "step_ratio": 2.0,       # Very wide steps
        "asymmetry": 1.0,
        "max_open_per_side": 3,
        "close_alpha": 0.05,     # Hyper-close everything
        "close_style": "outer",
        "anchor_mode": "fixed",
        "escape_bars": 3,
        "escape_threshold_usd": 2,
    },
}


@dataclass
class HHPosition:
    side: str  # "BUY" or "SELL"
    entry_price: float
    volume: float
    entry_bar: int
    pnl: float = 0.0


@dataclass
class HHEngineState:
    """Simplified HH engine state for backtesting."""
    anchor: float = 0.0
    next_buy_level: float = 0.0
    next_sell_level: float = 0.0
    positions: list = field(default_factory=list)
    realized_pnl: float = 0.0
    realized_closes: int = 0
    floating_pnl: float = 0.0
    max_floating: float = 0.0
    resets: int = 0
    total_opened: int = 0
    personality: str = "chop"


def compute_atr(candles: list[dict], period: int = 14) -> float:
    """Compute ATR from candles."""
    if len(candles) < period + 1:
        return 0.0
    trs = []
    for i in range(1, len(candles)):
        tr = max(
            candles[i]["high"] - candles[i]["low"],
            abs(candles[i]["high"] - candles[i-1]["close"]),
            abs(candles[i]["low"] - candles[i-1]["close"]),
        )
        trs.append(tr)
    return sum(trs[-period:]) / period


def classify_regime_simple(candles: list[dict], window: int = 30) -> str:
    """Simplified regime classification for backtesting.
    
    Returns one of: "chop", "trend", "breakout", "defensive"
    Based on ADX and ATR%.
    """
    if len(candles) < window:
        return "defensive"
    
    closes = [float(c["close"]) for c in candles[-window:]]
    highs = [float(c["high"]) for c in candles[-window:]]
    lows = [float(c["low"]) for c in candles[-window:]]
    
    adx = _compute_adx(highs, lows, closes, 14)
    atr = compute_atr(candles[-window:], 14)
    avg_price = sum(closes) / len(closes)
    atr_pct = (atr / avg_price * 100) if avg_price > 0 else 0
    
    # Price range compression
    high_range = max(highs) - min(lows)
    price = closes[-1]
    position_in_range = (price - min(lows)) / high_range if high_range > 0 else 0.5
    
    # Classification logic
    if adx < 20:
        # Low ADX = chop
        return "chop_aggressive" if atr_pct < 1.0 else "chop"
    elif adx < 30:
        # Moderate ADX = breakout potential
        return "breakout"
    elif adx >= 30:
        # Strong trend
        return "trend"
    else:
        return "defensive"


def run_shapeshifter_backtest(
    candles: list[dict],
    btc_candles: list[dict] | None,
    atr: float,
    symbol: str,
    continuous: bool = True,
    fixed_personality: str | None = None,
    volume: float = 0.01,
    spread_cost: float = 0.0,
    regime_window: int = 30,
    hysteresis_bars: int = 3,
) -> dict:
    """Run a simplified HH backtest with shapeshifter personalities.
    
    Args:
        candles: Bar data
        btc_candles: BTC bar data (for regime detection)
        atr: Base ATR for the symbol
        symbol: Symbol name
        continuous: If True, switch personality every bar. If False, lock to fixed_personality.
        fixed_personality: Personality to use when continuous=False
        volume: Position size (lots)
        spread_cost: Spread cost per position
        regime_window: Lookback window for regime classification
    
    Returns:
        Dict with backtest results
    """
    if not candles:
        return {"error": "no candles"}
    
    state = HHEngineState()
    personality_changes = []
    bar_results = []
    
    # Hysteresis tracking
    pending_personality = None
    pending_bars = 0
    
    # Initialize anchor at first candle close
    state.anchor = float(candles[0]["close"])
    state.next_buy_level = state.anchor - atr
    state.next_sell_level = state.anchor + atr
    
    # Determine initial personality
    if continuous:
        personality = classify_regime_simple(candles[:regime_window])
    else:
        personality = fixed_personality or "chop"
    
    state.personality = personality
    p = PERSONALITIES[personality]
    
    # Compute effective steps based on personality
    step_base = atr * p["step_ratio"]
    if p["asymmetry"] > 1.0:
        # Asymmetric: tight side = step_base / asymmetry_ratio_sqrt, wide side = step_base * asymmetry_ratio_sqrt
        asym_sqrt = math.sqrt(p["asymmetry"])
        step_buy = step_base / asym_sqrt
        step_sell = step_base * asym_sqrt
    else:
        step_buy = step_base
        step_sell = step_base
    
    max_open = p["max_open_per_side"]
    
    for i in range(regime_window, len(candles)):
        bar = candles[i]
        price = float(bar["close"])
        high = float(bar["high"])
        low = float(bar["low"])
        
        bar_pnl = 0.0
        bar_action = "none"
        bar_positions_opened = 0
        bar_positions_closed = 0
        
        # === Continuous regime check with hysteresis ===
        if continuous:
            new_personality = classify_regime_simple(candles[:i+1], regime_window)
            
            if new_personality == state.personality:
                # Same regime, reset any pending switch
                pending_personality = None
                pending_bars = 0
            elif new_personality == pending_personality:
                # Same pending personality, increment counter
                pending_bars += 1
                if pending_bars >= hysteresis_bars:
                    # Confirmed! Execute the flip
                    personality_changes.append({
                        "bar": i,
                        "from": state.personality,
                        "to": new_personality,
                        "price": price,
                        "confirmation_bars": pending_bars,
                    })
                    state.personality = new_personality
                    p = PERSONALITIES[new_personality]
                    
                    # Recompute steps
                    step_base = atr * p["step_ratio"]
                    if p["asymmetry"] > 1.0:
                        asym_sqrt = math.sqrt(p["asymmetry"])
                        step_buy = step_base / asym_sqrt
                        step_sell = step_base * asym_sqrt
                    else:
                        step_buy = step_base
                        step_sell = step_base
                    max_open = p["max_open_per_side"]
                    
                    # If anchor mode changed to trailing, update anchor
                    if p["anchor_mode"] == "trailing":
                        state.anchor = price
                        state.next_buy_level = state.anchor - step_buy
                        state.next_sell_level = state.anchor + step_sell
                    
                    pending_personality = None
                    pending_bars = 0
            else:
                # New personality detected, start hysteresis counter
                pending_personality = new_personality
                pending_bars = 0
        
        # === Check for position opens ===
        # BUY: price drops to or below next_buy_level
        buy_count = sum(1 for pos in state.positions if pos.side == "BUY")
        if low <= state.next_buy_level and buy_count < max_open:
            entry = min(price, state.next_buy_level)  # Fill at triggered level
            pos = HHPosition(
                side="BUY",
                entry_price=entry,
                volume=volume,
                entry_bar=i,
            )
            state.positions.append(pos)
            state.total_opened += 1
            bar_positions_opened += 1
            # Move next buy level down
            state.next_buy_level -= step_buy
        
        # SELL: price rises to or above next_sell_level
        sell_count = sum(1 for pos in state.positions if pos.side == "SELL")
        if high >= state.next_sell_level and sell_count < max_open:
            entry = max(price, state.next_sell_level)
            pos = HHPosition(
                side="SELL",
                entry_price=entry,
                volume=volume,
                entry_bar=i,
            )
            state.positions.append(pos)
            state.total_opened += 1
            bar_positions_opened += 1
            # Move next sell level up
            state.next_sell_level += step_sell
        
        # === Update position PnL ===
        buy_positions = [pos for pos in state.positions if pos.side == "BUY"]
        sell_positions = [pos for pos in state.positions if pos.side == "SELL"]
        
        for pos in buy_positions:
            pos.pnl = (price - pos.entry_price) * pos.volume * 100000 / 100
        for pos in sell_positions:
            pos.pnl = (pos.entry_price - price) * pos.volume * 100000 / 100
        
        state.floating_pnl = sum(pos.pnl for pos in state.positions)
        state.max_floating = min(state.max_floating, state.floating_pnl)
        
        # === Check for closes (all_profitable style) ===
        if p["close_style"] == "all_profitable" and state.positions:
            if state.floating_pnl > 0:
                # Close all positions
                for pos in state.positions:
                    close_pnl = pos.pnl - spread_cost  # Deduct spread
                    state.realized_pnl += close_pnl
                    state.realized_closes += 1
                    bar_pnl += close_pnl
                    bar_positions_closed += 1
                state.positions = []
                # Reset grid to current price
                state.anchor = price
                state.next_buy_level = state.anchor - step_buy
                state.next_sell_level = state.anchor + step_sell
        
        elif p["close_style"] == "outer":
            # Only close outermost profitable positions
            remaining = []
            for pos in state.positions:
                if pos.pnl > 0:
                    close_pnl = pos.pnl - spread_cost
                    state.realized_pnl += close_pnl
                    state.realized_closes += 1
                    bar_pnl += close_pnl
                    bar_positions_closed += 1
                else:
                    remaining.append(pos)
            state.positions = remaining
        
        # === Escape hatch check ===
        if p["escape_bars"] > 0:
            survivors = []
            for pos in state.positions:
                age_bars = i - pos.entry_bar
                if age_bars > p["escape_bars"] and pos.pnl < -p["escape_threshold_usd"]:
                    # Escape this position
                    escape_pnl = pos.pnl - spread_cost
                    state.realized_pnl += escape_pnl
                    state.realized_closes += 1
                    bar_pnl += escape_pnl
                    bar_positions_closed += 1
                else:
                    survivors.append(pos)
            state.positions = survivors
        
        # === Full kill (max floating loss) ===
        if state.floating_pnl < -15.0 and state.positions:
            # Kill all
            for pos in state.positions:
                kill_pnl = pos.pnl - spread_cost
                state.realized_pnl += kill_pnl
                state.realized_closes += 1
                bar_pnl += kill_pnl
                bar_positions_closed += 1
                state.resets += 1
            state.positions = []
            state.anchor = price
            state.next_buy_level = state.anchor - step_buy
            state.next_sell_level = state.anchor + step_sell
        
        # === Trailing anchor update ===
        if p["anchor_mode"] == "trailing" and not state.positions:
            # Anchor follows price, no positions open
            state.anchor = price
            state.next_buy_level = state.anchor - step_buy
            state.next_sell_level = state.anchor + step_sell
        elif p["anchor_mode"] == "trailing" and state.positions:
            # Trail anchor in trend direction for open positions
            if any(pos.side == "BUY" for pos in state.positions):
                state.anchor = max(state.anchor, price)
            if any(pos.side == "SELL" for pos in state.positions):
                state.anchor = min(state.anchor, price)
        
        bar_results.append({
            "bar": i,
            "price": price,
            "personality": state.personality,
            "positions_open": len(state.positions),
            "floating_pnl": round(state.floating_pnl, 2),
            "realized_pnl_bar": round(bar_pnl, 2),
            "positions_opened": bar_positions_opened,
            "positions_closed": bar_positions_closed,
        })
    
    return {
        "mode": "continuous" if continuous else f"static_{fixed_personality}",
        "symbol": symbol,
        "total_bars": len(candles) - regime_window,
        "realized_pnl": round(state.realized_pnl, 2),
        "realized_closes": state.realized_closes,
        "avg_pnl_per_close": round(state.realized_pnl / max(1, state.realized_closes), 2),
        "total_opened": state.total_opened,
        "max_floating_pnl": round(state.max_floating, 2),
        "final_floating_pnl": round(state.floating_pnl, 2),
        "resets": state.resets,
        "personality_changes": len(personality_changes),
        "personality_changes_detail": personality_changes[:20],  # First 20
        "personality_distribution": _count_personalities(bar_results),
        "bar_results": bar_results,
    }


def _count_personalities(bar_results: list[dict]) -> dict[str, int]:
    counts = {}
    for bar in bar_results:
        p = bar["personality"]
        counts[p] = counts.get(p, 0) + 1
    return counts


def load_shapeshifter_config() -> dict:
    """Load the master shapeshifter config for symbol-personality mappings."""
    config_path = CONFIGS / "hungry_hippo_shapeshifter.json"
    if config_path.exists():
        with open(config_path) as f:
            return json.load(f)
    return {}


def main():
    parser = argparse.ArgumentParser(description="HH v2 Shapeshifter Backtest")
    parser.add_argument("--symbol", default="NAS100", help="Symbol to test")
    parser.add_argument("--timeframe", default="M15", help="Timeframe")
    parser.add_argument("--window", default="7d", help="Lookback window (7d, 30d)")
    parser.add_argument("--volume", type=float, default=0.01, help="Position size in lots")
    parser.add_argument("--spread", type=float, default=0.0, help="Spread cost per position")
    parser.add_argument("--regime-window", type=int, default=30, help="Regime classification lookback")
    parser.add_argument("--atr-override", type=float, default=None, help="Override ATR value")
    parser.add_argument("--hysteresis", type=int, default=3, help="Hysteresis bars (confirm regime before switching)")
    parser.add_argument("--btc-fallback", action="store_true", help="Use synthetic BTC candles if unavailable")
    parser.add_argument("--output", type=str, default=None, help="Output JSON file path")
    args = parser.parse_args()
    
    print(f"=== HH v2 Shapeshifter Backtest: {args.symbol} {args.timeframe} ({args.window}) ===")
    print()
    
    # For FX/index symbols, we use synthetic candles since fetch_candles_coinbase only does crypto
    # In production, this would read from MT5 bar data
    # For now, let's use a simplified approach: read from existing bar data files
    
    # Try to load bar data from disk
    bar_data_path = REPORTS / f"{args.symbol.lower()}_{args.timeframe.lower()}_bars.json"
    
    if bar_data_path.exists():
        with open(bar_data_path) as f:
            raw_data = json.load(f)
        candles = raw_data.get("candles", raw_data) if isinstance(raw_data, dict) else raw_data
        btc_candles = raw_data.get("btc_candles")
    else:
        # For crypto symbols, try to fetch from Coinbase
        if args.symbol.upper() in ["BTCUSD", "ETHUSD"]:
            print(f"Fetching {args.symbol} candles from Coinbase...")
            candles_raw = fetch_candles_coinbase(args.symbol.upper().replace("USD", "-USD"), args.window)
            candles = normalize_candles(candles_raw)
            btc_candles = None
            if args.symbol.upper() != "BTCUSD":
                btc_raw = fetch_candles_coinbase("BTC-USD", args.window)
                btc_candles = _align_btc_candles(candles, btc_raw)
        else:
            print(f"ERROR: No bar data found for {args.symbol} {args.timeframe}")
            print(f"Expected path: {bar_data_path}")
            print("For FX/index symbols, bar data must be exported from MT5 first.")
            print("Run: python scripts/export_mt5_bars.py --symbol {args.symbol} --timeframe {args.timeframe} --window {args.window}")
            sys.exit(1)
    
    if not candles or len(candles) < 60:
        print(f"ERROR: Insufficient bar data ({len(candles) if candles else 0} candles, need >= 60)")
        sys.exit(1)
    
    print(f"Loaded {len(candles)} candles")
    print(f"Date range: {candles[0].get('start', 'N/A')} to {candles[-1].get('start', 'N/A')}")
    print()
    
    # Compute ATR
    atr = compute_atr(candles, 14)
    if args.atr_override:
        atr = args.atr_override
    
    symbol = args.symbol.upper()
    
    # Load shapeshifter config for symbol-personality mapping
    shapeshifter_config = load_shapeshifter_config()
    symbol_config = None
    for sym in shapeshifter_config.get("symbols", []):
        if sym.get("symbol") == symbol:
            symbol_config = sym
            break
    
    # === Run continuous shapeshifter ===
    print(f"--- Running CONTINUOUS shapeshifter (hysteresis={args.hysteresis} bars) ---")
    continuous_result = run_shapeshifter_backtest(
        candles=candles,
        btc_candles=btc_candles,
        atr=atr,
        symbol=symbol,
        continuous=True,
        volume=args.volume,
        spread_cost=args.spread,
        regime_window=args.regime_window,
        hysteresis_bars=args.hysteresis,
    )
    
    print(f"  Realized PnL: ${continuous_result['realized_pnl']:.2f}")
    print(f"  Closes: {continuous_result['realized_closes']}")
    print(f"  Avg $/close: ${continuous_result['avg_pnl_per_close']:.2f}")
    print(f"  Max floating: ${continuous_result['max_floating_pnl']:.2f}")
    print(f"  Resets: {continuous_result['resets']}")
    print(f"  Personality changes: {continuous_result['personality_changes']}")
    print(f"  Personality distribution: {continuous_result['personality_distribution']}")
    print()
    
    # === Run each static personality for comparison ===
    print("--- Running STATIC baselines (personality locked) ---")
    static_results = {}
    for personality in PERSONALITIES:
        result = run_shapeshifter_backtest(
            candles=candles,
            btc_candles=btc_candles,
            atr=atr,
            symbol=symbol,
            continuous=False,
            fixed_personality=personality,
            volume=args.volume,
            spread_cost=args.spread,
            regime_window=args.regime_window,
        )
        static_results[personality] = result
        print(f"  {personality:20s}: PnL=${result['realized_pnl']:+.2f}  closes={result['realized_closes']}  $/c=${result['avg_pnl_per_close']:+.2f}  max_float=${result['max_floating_pnl']:+.2f}  resets={result['resets']}")
    
    print()
    
    # === Comparison summary ===
    print("=== COMPARISON SUMMARY ===")
    best_static = max(static_results.values(), key=lambda r: r["realized_pnl"])
    best_static_name = max(static_results.keys(), key=lambda k: static_results[k]["realized_pnl"])
    
    continuous_pnl = continuous_result["realized_pnl"]
    best_static_pnl = best_static["realized_pnl"]
    
    if best_static_pnl != 0:
        improvement = (continuous_pnl - best_static_pnl) / abs(best_static_pnl) * 100
    else:
        improvement = float("inf") if continuous_pnl > 0 else 0
    
    print(f"  Best static:    {best_static_name} at ${best_static_pnl:+.2f}")
    print(f"  Continuous:     ${continuous_pnl:+.2f}")
    print(f"  Improvement:    {improvement:+.1f}%")
    print()
    
    # Floating PnL comparison
    print("=== FLOATING PnL COMPARISON ===")
    continuous_max_float = continuous_result["max_floating_pnl"]
    best_static_max_float = best_static["max_floating_pnl"]
    print(f"  Best static max floating: ${best_static_max_float:+.2f}")
    print(f"  Continuous max floating:  ${continuous_max_float:+.2f}")
    if abs(best_static_max_float) > 0:
        float_improvement = (abs(continuous_max_float) - abs(best_static_max_float)) / abs(best_static_max_float) * 100
        print(f"  Floating PnL change:    {float_improvement:+.1f}% (positive = worse)")
    print()
    
    # Resets comparison
    print("=== RESETS COMPARISON ===")
    print(f"  Best static resets:    {best_static['resets']}")
    print(f"  Continuous resets:     {continuous_result['resets']}")
    print()
    
    # === Output results ===
    output = {
        "symbol": symbol,
        "timeframe": args.timeframe,
        "window": args.window,
        "atr": atr,
        "candles_count": len(candles),
        "continuous": continuous_result,
        "static": static_results,
        "best_static": best_static_name,
        "best_static_pnl": best_static_pnl,
        "continuous_pnl": continuous_pnl,
        "improvement_pct": round(improvement, 1),
    }
    
    # Save to file
    if args.output:
        output_path = Path(args.output)
    else:
        output_path = REPORTS / f"shapeshifter_v2_{symbol.lower()}_{args.timeframe.lower()}_{args.window}.json"
    
    # Remove bar_results for file output (too large)
    output["continuous"]["bar_results"] = []
    for k in output["static"]:
        output["static"][k]["bar_results"] = []
    
    with open(output_path, "w") as f:
        json.dump(output, f, indent=2)
    
    print(f"Results saved to: {output_path}")
    
    # Verdict
    print()
    if continuous_pnl > best_static_pnl and continuous_pnl > 0:
        print(f"✅ VERDICT: Continuous shapeshifter BEATS best static by {improvement:+.1f}%")
        print(f"   Continuous: ${continuous_pnl:+.2f} vs Best static ({best_static_name}): ${best_static_pnl:+.2f}")
        if continuous_max_float > best_static_max_float:
            print(f"   ⚠️  But floating PnL is worse: ${continuous_max_float:+.2f} vs ${best_static_max_float:+.2f}")
        else:
            print(f"   ✅ AND floating PnL is better: ${continuous_max_float:+.2f} vs ${best_static_max_float:+.2f}")
        print(f"   → RECOMMENDATION: Launch as shadow for live validation")
    elif continuous_pnl > 0:
        print(f"⚠️  VERDICT: Continuous is positive (${continuous_pnl:+.2f}) but trails best static ({best_static_name} at ${best_static_pnl:+.2f})")
        print(f"   → RECOMMENDATION: Investigate why switching underperforms; may need hysteresis on regime transitions")
    else:
        print(f"❌ VERDICT: Continuous is negative (${continuous_pnl:+.2f})")
        print(f"   → RECOMMENDATION: Do not proceed. Static configs are superior for this symbol/timeframe.")


if __name__ == "__main__":
    main()
