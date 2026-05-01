#!/usr/bin/env python3
"""Bar-level exit simulation — validates counterfactual exit experiments against real M1 bars.

Our current exit backtests are counterfactuals on trade-log peak/realized pairs:
  exit = max(realized, peak * 0.75)

This assumes price smoothly retraces through the trail level. But real M1 data
may gap through it, or reverse without bar closes below the trail.

This script:
1. Loads M1 bars via MT5
2. Detects confirmed-displacement signals (1.5pip, 2.5x ATR, 1 bar window)
3. For each signal, simulates entry and applies 75% retain trail bar-by-bar
4. Compares bar-level exit vs counterfactual exit for each trade

Output: side-by-side table with delta, showing if 75% retain is realizable.

Author: local AI-assisted research pass
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass
from pathlib import Path
from statistics import mean
from typing import Literal

import MetaTrader5 as mt5


SYMBOL = "USDJPY"
PIP = 0.01
UNITS_001_LOT = 1_000

Direction = Literal["BUY", "SELL"]

# ── Signal detection (confirmed displacement) ────────────────────────


@dataclass
class SignalParams:
    confirm_pips: float
    expansion_ratio: float
    confirm_window_bars: int
    lookback: int = 8
    min_body_pips: float = 4.0
    min_body_ratio: float = 0.75
    volume_burst_ratio: float = 1.10


@dataclass
class BarTrade:
    trade_idx: int
    direction: Direction
    entry_idx: int
    entry_price: float
    exit_idx: int
    exit_price: float
    hold_bars: int
    peak_price: float
    adverse_price: float
    pnl_pips: float
    pnl_usd: float
    mfe_pips: float
    mae_pips: float
    counterfactual_exit_usd: float  # What 75% retain counterfactual says
    bar_level_exit_usd: float  # What bar-by-bar trail actually achieves
    delta_usd: float  # counterfactual - bar_level


def load_bars(days: int) -> list[dict]:
    rates = mt5.copy_rates_from_pos(SYMBOL, mt5.TIMEFRAME_M1, 0, 1440 * days)
    if rates is None or len(rates) == 0:
        return []
    return [
        {
            "time": int(r[0]),
            "open": float(r[1]),
            "high": float(r[2]),
            "low": float(r[3]),
            "close": float(r[4]),
            "tick_volume": int(r[5]),
        }
        for r in rates
    ]


def body_pips(bar: dict) -> float:
    return abs(bar["close"] - bar["open"]) / PIP


def range_pips(bar: dict) -> float:
    return max((bar["high"] - bar["low"]) / PIP, 0.01)


def avg_volume(bars: list[dict], start: int, end: int) -> float:
    window = bars[max(0, start):end]
    return mean(bar["tick_volume"] for bar in window) if window else 0.0


def avg_range(bars: list[dict], start: int, end: int) -> float:
    window = bars[max(0, start):end]
    return mean(range_pips(bar) for bar in window) if window else 0.0


def detect_displacement_signal(bars: list[dict], idx: int, params: SignalParams) -> Direction | None:
    """Detect strict displacement signal (no confirmation yet — just the breakout bar)."""
    if idx < params.lookback:
        return None
    cur = bars[idx]
    prior = bars[idx - params.lookback:idx]
    prior_high = max(b["high"] for b in prior)
    prior_low = min(b["low"] for b in prior)
    body = body_pips(cur)
    ratio = body / range_pips(cur)
    vol = avg_volume(bars, idx - params.lookback, idx)
    avg_rng = avg_range(bars, idx - params.lookback, idx)
    burst = cur["tick_volume"] >= vol * params.volume_burst_ratio if vol > 0 else False
    expanded = (range_pips(cur) / avg_rng) >= params.expansion_ratio if avg_rng > 0 else True
    if body < params.min_body_pips or ratio < params.min_body_ratio or not burst or not expanded:
        return None
    if cur["close"] > prior_high:
        return "BUY"
    if cur["close"] < prior_low:
        return "SELL"
    return None


def find_confirmed_entry(bars: list[dict], idx: int, direction: Direction, params: SignalParams) -> tuple[int, float] | None:
    """Look for confirmation within window bars."""
    signal_close = bars[idx]["close"]
    target = signal_close + params.confirm_pips * PIP if direction == "BUY" else signal_close - params.confirm_pips * PIP
    end_idx = min(len(bars), idx + 1 + params.confirm_window_bars)
    for entry_idx in range(idx + 1, end_idx):
        bar = bars[entry_idx]
        if direction == "BUY" and bar["high"] >= target:
            return entry_idx, target
        if direction == "SELL" and bar["low"] <= target:
            return entry_idx, target
    return None


def pnl_usd_001(direction: Direction, entry: float, exit_price: float, spread_pips: float) -> float:
    move = (exit_price - entry) if direction == "BUY" else (entry - exit_price)
    net = move - spread_pips * PIP
    raw_jpy = net * UNITS_001_LOT
    return raw_jpy / max(exit_price, 0.0001)


def simulate_bar_level_exit(
    bars: list[dict],
    entry_idx: int,
    entry_price: float,
    direction: Direction,
    max_hold_bars: int,
    retain_ratio: float,
    floor_pips: float,
    min_mfe_pips: float,
    spread_pips: float,
) -> BarTrade | None:
    """Simulate entry and 75% retain trail bar-by-bar."""
    mfe_pips = 0.0
    mae_pips = 0.0
    peak_price = entry_price
    adverse_price = entry_price
    exit_idx = None
    exit_price = None
    trail_fired_at_idx = None
    trail_fired_at_price = None

    for j in range(entry_idx, min(len(bars) - 1, entry_idx + max_hold_bars + 1)):
        bar = bars[j]

        # Track favorable/adverse
        if direction == "BUY":
            favorable = (bar["high"] - entry_price) / PIP
            adverse = -(bar["low"] - entry_price) / PIP
            if bar["high"] > peak_price:
                peak_price = bar["high"]
            if bar["low"] < adverse_price:
                adverse_price = bar["low"]
        else:
            favorable = (entry_price - bar["low"]) / PIP
            adverse = -(entry_price - bar["high"]) / PIP
            if bar["low"] < peak_price:
                peak_price = bar["low"]
            if bar["high"] > adverse_price:
                adverse_price = bar["high"]

        mfe_pips = max(mfe_pips, favorable)
        mae_pips = max(mae_pips, adverse)

        # Counterfactual: what would max(realized, peak * retain) be?
        # This is computed at the end

        # Bar-level trail: check if bar's LOW (for BUY) or HIGH (for SELL)
        # crosses below the trail level. The trail fires when price CLOSES
        # at or below the trail level.
        if mfe_pips >= min_mfe_pips:
            floor = max(floor_pips, mfe_pips * retain_ratio)
            if direction == "BUY":
                # Trail level: entry + floor pips
                trail_level = entry_price + floor * PIP
                # Fire if bar's LOW crosses below trail (we can't exit at close
                # if the low already went below — we'd exit at the trail level)
                if bar["low"] <= trail_level:
                    exit_idx = j
                    exit_price = trail_level
                    trail_fired_at_idx = j
                    trail_fired_at_price = trail_level
                    break
            else:
                trail_level = entry_price - floor * PIP
                if bar["high"] >= trail_level:
                    exit_idx = j
                    exit_price = trail_level
                    trail_fired_at_idx = j
                    trail_fired_at_price = trail_level
                    break

    # If trail never fired, exit at last bar close (time exit)
    if exit_idx is None:
        exit_idx = min(len(bars) - 1, entry_idx + max_hold_bars)
        exit_price = bars[exit_idx]["close"]

    pnl_pips = ((exit_price - entry_price) / PIP if direction == "BUY" else (entry_price - exit_price) / PIP) - spread_pips
    pnl_usd = pnl_usd_001(direction, entry_price, exit_price, spread_pips)

    # Counterfactual: what if we had exit = max(realized_at_last_bar, peak * retain)?
    last_close = bars[min(len(bars) - 1, entry_idx + max_hold_bars)]["close"]
    realized_from_close = ((last_close - entry_price) / PIP if direction == "BUY" else (entry_price - last_close) / PIP) - spread_pips
    realized_from_close_usd = pnl_usd_001(direction, entry_price, last_close, spread_pips)

    if mfe_pips > 0:
        counterfactual_pips = max(realized_from_close, mfe_pips * retain_ratio)
        # Convert counterfactual pips to USD
        cf_price = entry_price + counterfactual_pips * PIP if direction == "BUY" else entry_price - counterfactual_pips * PIP
        counterfactual_usd = pnl_usd_001(direction, entry_price, cf_price, spread_pips)
    else:
        counterfactual_usd = realized_from_close_usd

    delta = counterfactual_usd - pnl_usd

    return BarTrade(
        trade_idx=0,  # filled later
        direction=direction,
        entry_idx=entry_idx,
        entry_price=entry_price,
        exit_idx=exit_idx,
        exit_price=exit_price,
        hold_bars=exit_idx - entry_idx + 1,
        peak_price=peak_price,
        adverse_price=adverse_price,
        pnl_pips=pnl_pips,
        pnl_usd=pnl_usd,
        mfe_pips=max(0.0, mfe_pips),
        mae_pips=max(0.0, mae_pips),
        counterfactual_exit_usd=counterfactual_usd,
        bar_level_exit_usd=pnl_usd,
        delta_usd=delta,
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Bar-level exit simulation vs counterfactual")
    parser.add_argument("--days", type=int, default=20)
    parser.add_argument("--spread-pips", type=float, default=0.6)
    parser.add_argument("--retain", type=float, default=0.75)
    parser.add_argument("--confirm-pips", type=float, default=1.5)
    parser.add_argument("--expansion", type=float, default=2.5)
    args = parser.parse_args()

    if not mt5.initialize():
        print("MetaTrader5 initialize() failed")
        return 1

    try:
        bars = load_bars(args.days)
        if not bars:
            print("No bars loaded")
            return 1

        params = SignalParams(
            confirm_pips=args.confirm_pips,
            expansion_ratio=args.expansion,
            confirm_window_bars=1,
        )

        print("=" * 72)
        print(f"BAR-LEVEL EXIT SIMULATION ({args.days} days, {SYMBOL})")
        print(f"Signal: confirmed displacement {args.confirm_pips}pip / {args.expansion}x ATR / 1 bar")
        print(f"Exit: {args.retain:.0%} retain trail, floor 0.5 pips, max 6 bars")
        print("=" * 72)
        print()

        # Find all signals and simulate
        trades: list[BarTrade] = []
        idx = params.lookback + 2
        trade_num = 0

        while idx < len(bars) - 2:
            direction = detect_displacement_signal(bars, idx, params)
            if direction:
                entry_plan = find_confirmed_entry(bars, idx, direction, params)
                if entry_plan:
                    entry_idx, entry_price = entry_plan
                    trade = simulate_bar_level_exit(
                        bars, entry_idx, entry_price, direction,
                        max_hold_bars=6,
                        retain_ratio=args.retain,
                        floor_pips=0.5,
                        min_mfe_pips=1.0,
                        spread_pips=args.spread_pips,
                    )
                    if trade:
                        trade_num += 1
                        trade.trade_idx = trade_num
                        trades.append(trade)
                    idx = entry_idx + 1
                    continue
            idx += 1

        if not trades:
            print("No trades found")
            return 0

        # Print results
        print(f"{'#':>3} {'Dir':>4} {'Entry':>8} {'Exit':>8} {'Hold':>5} {'Peak':>8} {'MFE':>7} {'Counter':>9} {'Bar-level':>9} {'Delta':>9}")
        print("-" * 82)

        for t in trades:
            peak_pips = ((t.peak_price - t.entry_price) / PIP if t.direction == "BUY" else (t.entry_price - t.peak_price) / PIP)
            print(
                f"{t.trade_idx:>3} {t.direction:>4} {t.entry_price:>8.5f} {t.exit_price:>8.5f} "
                f"{t.hold_bars:>5d} {peak_pips:>7.1f}p {t.mfe_pips:>6.1f}p "
                f"${t.counterfactual_exit_usd:+8.2f} ${t.bar_level_exit_usd:+8.2f} ${t.delta_usd:+8.2f}"
            )

        print()

        # Summary
        net_cf = sum(t.counterfactual_exit_usd for t in trades)
        net_bar = sum(t.bar_level_exit_usd for t in trades)
        exp_cf = mean(t.counterfactual_exit_usd for t in trades)
        exp_bar = mean(t.bar_level_exit_usd for t in trades)
        wins = sum(1 for t in trades if t.bar_level_exit_usd > 0)
        avg_delta = mean(t.delta_usd for t in trades)
        capture_pct = (net_bar / net_cf * 100.0) if net_cf > 0 else 0.0

        print("─" * 72)
        print("SUMMARY")
        print("─" * 72)
        print(f"  Trades: {len(trades)}")
        print(f"  Counterfactual net: ${net_cf:+.2f} | exp: ${exp_cf:+.2f}/trade")
        print(f"  Bar-level net:      ${net_bar:+.2f} | exp: ${exp_bar:+.2f}/trade")
        print(f"  Win rate (bar-level): {wins}/{len(trades)} ({wins/len(trades)*100:.0f}%)")
        print(f"  Avg delta (counterfactual - bar): ${avg_delta:+.2f}")
        print(f"  Bar-level captures {capture_pct:.0f}% of counterfactual value")
        print()

        # Viability assessment
        if capture_pct >= 80:
            print(f"  🟢 VIABLE: Bar-level trail captures {capture_pct:.0f}% of counterfactual value")
            print(f"     The 75% retain exit is REALIZABLE at bar-level resolution.")
        elif capture_pct >= 50:
            print(f"  🟡 PARTIALLY VIABLE: Bar-level trail captures {capture_pct:.0f}% of counterfactual")
            print(f"     The 75% retain exit loses some value at bar-level — price gaps through the trail.")
            print(f"     Consider using a tighter trail (80% retain) or a close-based exit instead of low/high based.")
        else:
            print(f"  🔴 NOT VIABLE: Bar-level trail captures only {capture_pct:.0f}% of counterfactual")
            print(f"     The 75% retain exit is largely theoretical — price rarely closes at the trail level.")
            print(f"     Consider: (a) using stop-loss orders at trail level instead of bar-close,")
            print(f"     (b) a different exit strategy entirely.")

        print()
        print("=" * 72)
        return 0

    finally:
        mt5.shutdown()


if __name__ == "__main__":
    raise SystemExit(main())
