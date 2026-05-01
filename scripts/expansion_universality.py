#!/usr/bin/env python3
"""Signal expansion test — does the 2.5x ATR expansion wall generalize?

The confirmed-displacement sweep showed that >=2.0x ATR expansion is the cleanest
parameter boundary — below it, everything bleeds. Above it, everything profits.

This tests whether the expansion filter works as a UNIVERSAL signal quality gate
applied to OTHER entry architectures:
1. Stop-run reclaim
2. Failed continuation fade
3. Two-bar momentum
4. Three-bar momentum
5. Acceleration

If expansion works universally, we can add it as a filter to ALL strategies,
not just confirmed-displacement.

Usage: python scripts/expansion_universality.py [--days 20] [--expansion 2.5]
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass
from statistics import mean

import MetaTrader5 as mt5


SYMBOL = "USDJPY"
PIP = 0.01
UNITS_001_LOT = 1_000


@dataclass
class TradeResult:
    entry_type: str
    direction: str
    entry_idx: int
    hold_bars: int
    pnl_usd: float
    mfe_pips: float
    mae_pips: float


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


def bar_dir(bar: dict) -> str | None:
    if bar["close"] > bar["open"]:
        return "BUY"
    if bar["close"] < bar["open"]:
        return "SELL"
    return None


def avg_volume(bars: list[dict], start: int, end: int) -> float:
    window = bars[max(0, start):end]
    return mean(bar["tick_volume"] for bar in window) if window else 0.0


def avg_range(bars: list[dict], start: int, end: int) -> float:
    window = bars[max(0, start):end]
    return mean(range_pips(bar) for bar in window) if window else 0.0


def pnl_usd_001(direction: str, entry: float, exit_price: float, spread_pips: float) -> float:
    move = (exit_price - entry) if direction == "BUY" else (entry - exit_price)
    net = move - spread_pips * PIP
    raw_jpy = net * UNITS_001_LOT
    return raw_jpy / max(exit_price, 0.0001)


def detect_stop_run_reclaim(bars: list[dict], idx: int, lookback: int, sweep_pips: float) -> str | None:
    if idx < lookback:
        return None
    cur = bars[idx]
    prior = bars[idx - lookback:idx]
    prior_high = max(b["high"] for b in prior)
    prior_low = min(b["low"] for b in prior)
    d = bar_dir(cur)
    if d == "BUY":
        swept = cur["low"] <= (prior_low - sweep_pips * PIP)
        reclaimed = cur["close"] >= prior_low
        if swept and reclaimed and body_pips(cur) >= 1.5:
            return "BUY"
    if d == "SELL":
        swept = cur["high"] >= (prior_high + sweep_pips * PIP)
        reclaimed = cur["close"] <= prior_high
        if swept and reclaimed and body_pips(cur) >= 1.5:
            return "SELL"
    return None


def detect_failed_continuation(bars: list[dict], idx: int, lookback: int) -> str | None:
    if idx < lookback + 1:
        return None
    prev = bars[idx - 1]
    cur = bars[idx]
    prior = bars[idx - 1 - lookback:idx - 1]
    if len(prior) < lookback:
        return None
    prior_high = max(b["high"] for b in prior)
    prior_low = min(b["low"] for b in prior)
    prev_body = body_pips(prev)
    prev_ratio = prev_body / range_pips(prev)
    if prev_body < 4.0 or prev_ratio < 0.75:
        return None
    if prev["close"] > prior_high:
        if cur["close"] < prior_high and cur["close"] < prev["open"]:
            return "SELL"
    if prev["close"] < prior_low:
        if cur["close"] > prior_low and cur["close"] > prev["open"]:
            return "BUY"
    return None


def detect_two_bar_momentum(bars: list[dict], idx: int, min_total_pips: float) -> str | None:
    if idx < 2:
        return None
    cur = bars[idx]
    prev1 = bars[idx - 1]
    d1 = bar_dir(cur)
    d2 = bar_dir(prev1)
    if d1 and d2 and d1 == d2:
        total = body_pips(cur) + body_pips(prev1)
        if total >= min_total_pips:
            return d1
    return None


def detect_three_bar_momentum(bars: list[dict], idx: int, min_total_pips: float) -> str | None:
    if idx < 3:
        return None
    cur = bars[idx]
    prev1 = bars[idx - 1]
    prev2 = bars[idx - 2]
    d1 = bar_dir(cur)
    d2 = bar_dir(prev1)
    d3 = bar_dir(prev2)
    if d1 and d2 and d3 and d1 == d2 == d3:
        total = body_pips(cur) + body_pips(prev1) + body_pips(prev2)
        if total >= min_total_pips:
            return d1
    return None


def detect_acceleration(bars: list[dict], idx: int, min_total_pips: float) -> str | None:
    if idx < 4:
        return None
    cur = bars[idx]
    prev1 = bars[idx - 1]
    prev2 = bars[idx - 2]
    prev3 = bars[idx - 3]
    d1 = bar_dir(cur)
    d2 = bar_dir(prev1)
    if d1 and d2 and d1 == d2:
        recent_avg = (body_pips(prev1) + body_pips(prev2) + body_pips(prev3)) / 3.0
        if recent_avg > 0 and body_pips(cur) >= recent_avg * 1.4:
            if body_pips(cur) + body_pips(prev1) >= min_total_pips:
                return d1
    return None


def simulate_entry(
    bars: list[dict],
    idx: int,
    direction: str,
    expansion_ratio: float,
    max_hold_bars: int,
    spread_pips: float,
    exit_type: str,
) -> TradeResult | None:
    """Simulate an entry with optional expansion filter."""
    # Check expansion filter
    if expansion_ratio > 0:
        lookback = 8
        prior_bars = bars[max(0, idx - lookback):idx]
        avg_rng = avg_range(bars, max(0, idx - lookback), idx)
        cur_rng = range_pips(bars[idx])
        if avg_rng > 0 and (cur_rng / avg_rng) < expansion_ratio:
            return None  # Filtered out by expansion

    entry_idx = idx + 1
    if entry_idx >= len(bars):
        return None
    entry_price = bars[entry_idx]["open"]

    mfe_pips = 0.0
    mae_pips = 0.0
    peak_price = entry_price
    exit_idx = None
    exit_price = None

    for j in range(entry_idx, min(len(bars) - 1, entry_idx + max_hold_bars + 1)):
        bar = bars[j]
        if direction == "BUY":
            favorable = (bar["high"] - entry_price) / PIP
            adverse = -(bar["low"] - entry_price) / PIP
            if bar["high"] > peak_price:
                peak_price = bar["high"]
        else:
            favorable = (entry_price - bar["low"]) / PIP
            adverse = -(entry_price - bar["high"]) / PIP
            if bar["low"] < peak_price:
                peak_price = bar["low"]

        mfe_pips = max(mfe_pips, favorable)
        mae_pips = max(mae_pips, adverse)

        # Simple exit: opposite close or time
        d = bar_dir(bar)
        if exit_type == "opp_close" and d and d != direction:
            exit_idx = j
            exit_price = bar["close"]
            break
        if exit_type == "time_3" and (j - entry_idx + 1) >= 3:
            exit_idx = j
            exit_price = bar["close"]
            break

    if exit_idx is None:
        exit_idx = min(len(bars) - 1, entry_idx + max_hold_bars)
        exit_price = bars[exit_idx]["close"]

    pnl = pnl_usd_001(direction, entry_price, exit_price, spread_pips)

    return TradeResult(
        entry_type="",
        direction=direction,
        entry_idx=entry_idx,
        hold_bars=exit_idx - entry_idx + 1,
        pnl_usd=pnl,
        mfe_pips=max(0.0, mfe_pips),
        mae_pips=max(0.0, mae_pips),
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Test expansion filter universality")
    parser.add_argument("--days", type=int, default=20)
    parser.add_argument("--spread-pips", type=float, default=0.6)
    args = parser.parse_args()

    if not mt5.initialize():
        print("MetaTrader5 initialize() failed")
        return 1

    try:
        bars = load_bars(args.days)
        if not bars:
            print("No bars loaded")
            return 1

        print("=" * 72)
        print(f"EXPANSION UNIVERSALITY TEST ({args.days} days, {SYMBOL})")
        print("=" * 72)
        print()

        # Test each entry type with and without expansion filter
        entry_configs = [
            ("stop_run_reclaim", lambda i: detect_stop_run_reclaim(bars, i, 8, 0.8)),
            ("failed_continuation", lambda i: detect_failed_continuation(bars, i, 8)),
            ("two_bar_momentum", lambda i: detect_two_bar_momentum(bars, i, 2.0)),
            ("three_bar_momentum", lambda i: detect_three_bar_momentum(bars, i, 3.0)),
            ("acceleration", lambda i: detect_acceleration(bars, i, 2.5)),
        ]

        expansion_ratios = [0.0, 1.5, 2.0, 2.5, 3.0]

        print(f"{'Entry Type':<22} {'Expansion':>10} {'Trades':>7} {'WR':>6} {'Net':>9} {'Exp':>9}")
        print("-" * 68)

        for entry_name, detect_fn in entry_configs:
            for exp_ratio in expansion_ratios:
                trades: list[TradeResult] = []
                idx = 10
                while idx < len(bars) - 2:
                    direction = detect_fn(idx)
                    if direction:
                        result = simulate_entry(bars, idx, direction, exp_ratio, 6, args.spread_pips, "opp_close")
                        if result:
                            result.entry_type = entry_name
                            trades.append(result)
                            idx = result.entry_idx + 1
                            continue
                    idx += 1

                if trades:
                    wins = sum(1 for t in trades if t.pnl_usd > 0)
                    net = sum(t.pnl_usd for t in trades)
                    exp = mean(t.pnl_usd for t in trades)
                    wr = wins / len(trades) * 100
                    exp_label = f"{exp_ratio:.1f}x" if exp_ratio > 0 else "none"
                    print(
                        f"{entry_name:<22} {exp_label:>10} {len(trades):>7d} {wr:>5.0f}% "
                        f"${net:+8.2f} ${exp:+8.3f}"
                    )

            print()

        # Find the best expansion for each entry type
        print("─" * 72)
        print("BEST EXPANSION PER ENTRY TYPE")
        print("─" * 72)
        print()

        for entry_name, detect_fn in entry_configs:
            best_exp = 0.0
            best_exp_val = -999
            for exp_ratio in expansion_ratios:
                trades = []
                idx = 10
                while idx < len(bars) - 2:
                    direction = detect_fn(idx)
                    if direction:
                        result = simulate_entry(bars, idx, direction, exp_ratio, 6, args.spread_pips, "opp_close")
                        if result:
                            trades.append(result)
                            idx = result.entry_idx + 1
                            continue
                    idx += 1
                if trades:
                    exp = mean(t.pnl_usd for t in trades)
                    if exp > best_exp_val and len(trades) >= 5:
                        best_exp_val = exp
                        best_exp = exp_ratio

            if best_exp_val > -999:
                exp_label = f"{best_exp:.1f}x" if best_exp > 0 else "no filter"
                print(f"  {entry_name:<22} Best: {exp_label:>6} (exp=${best_exp_val:+.3f})")
            else:
                print(f"  {entry_name:<22} No viable configuration")

        print()
        print("=" * 72)
        return 0

    finally:
        mt5.shutdown()


if __name__ == "__main__":
    raise SystemExit(main())
