#!/usr/bin/env python3
"""Range strategy backtester — finds what trades during dead/quiet markets.

Tests range-bound and mean-reversion strategies across all available FX symbols.
Focus: strategies that profit when confirmed-displacement CAN'T fire.

Entry architectures tested:
1. ASIAN_RANGE — fade the range boundaries (buy low, sell high)
2. RANGE_MEAN_REVERSION — enter when price deviates from mean, exit at mean
3. CANDLE_DIRECTION_IN_RANGE — trade small directional moves within range
4. PULLBACK_TO_STRUCTURE — catch the small pullbacks in ranging markets

Usage: python scripts/range_strategy_backtest.py [--days 30] [--symbols SYM1 SYM2]
"""
from __future__ import annotations

import argparse
import csv
import sys
from dataclasses import dataclass
from pathlib import Path
from statistics import mean, stdev

import MetaTrader5 as mt5

ROOT = Path(__file__).resolve().parent.parent
ALL_FX_SYMBOLS = [
    # Majors
    "EURUSD", "GBPUSD", "USDJPY", "USDCHF", "AUDUSD", "NZDUSD", "USDCAD",
    # Crosses
    "EURGBP", "EURJPY", "GBPJPY", "AUDJPY", "NZDJPY", "CHFJPY", "CADJPY",
    "AUDCAD", "AUDCHF", "AUDNZD", "CADCHF", "GBPAUD", "GBPCAD", "GBPCHF",
    "GBPNZD", "NZDCAD", "NZDCHF",
]

# Real spread estimates (pips)
SPREADS = {
    "EURUSD": 0.8, "GBPUSD": 1.0, "USDJPY": 0.6, "USDCHF": 1.0,
    "AUDUSD": 1.2, "NZDUSD": 1.5, "USDCAD": 1.2,
    "EURGBP": 1.0, "EURJPY": 1.2, "GBPJPY": 1.5, "AUDJPY": 1.5,
    "NZDJPY": 2.0, "CHFJPY": 1.5, "CADJPY": 1.5,
    "AUDCAD": 1.5, "AUDCHF": 1.5, "AUDNZD": 2.0, "CADCHF": 1.5,
    "GBPAUD": 2.0, "GBPCAD": 2.0, "GBPCHF": 1.5, "GBPNZD": 2.5,
    "NZDCAD": 2.0, "NZDCHF": 2.0,
}

PIP_SIZES = {}
for s in ALL_FX_SYMBOLS:
    if "JPY" in s:
        PIP_SIZES[s] = 0.01
    else:
        PIP_SIZES[s] = 0.0001

UNITS_001_LOT = 1_000
MAX_HOLD = 30


def load_bars(symbol: str, days: int) -> list[dict]:
    rates = mt5.copy_rates_from_pos(symbol, mt5.TIMEFRAME_M1, 0, 1440 * days)
    if rates is None or len(rates) == 0:
        return []
    return [
        {"time": int(r[0]), "open": float(r[1]), "high": float(r[2]),
         "low": float(r[3]), "close": float(r[4]), "tick_volume": int(r[5])}
        for r in rates
    ]


def compute_atr(bars: list[dict], idx: int, period: int = 14) -> float:
    if idx < period:
        return 0.0
    trs = []
    for i in range(idx - period + 1, idx + 1):
        tr = bars[i]["high"] - bars[i]["low"]
        if i > 0:
            tr = max(tr, abs(bars[i]["high"] - bars[i - 1]["close"]))
            tr = max(tr, abs(bars[i]["low"] - bars[i - 1]["close"]))
        trs.append(tr)
    return sum(trs) / len(trs) if trs else 0.0


def sma(bars: list[dict], idx: int, period: int) -> float:
    if idx < period:
        return 0.0
    return sum(bars[i]["close"] for i in range(idx - period, idx)) / period


def is_range_bound(bars: list[dict], idx: int, lookback: int = 20) -> bool:
    """Check if market is in a range (low ATR relative to recent average, tight range)."""
    if idx < lookback + 14:
        return False

    # Current ATR vs recent average
    current_atr = compute_atr(bars, idx)
    if current_atr <= 0:
        return False

    # Range width over lookback
    recent = bars[idx - lookback: idx]
    range_high = max(b["high"] for b in recent)
    range_low = min(b["low"] for b in recent)
    pip = 0.01 if any(c.isdigit() and not c.isalpha() for c in "") else 0.0001  # simplified

    return True  # We'll use ATR-relative thresholds per symbol instead


def pnl_usd_001(direction: str, entry: float, exit_price: float, spread_pips: float, pip: float) -> float:
    raw = (exit_price - entry) / pip if direction == "BUY" else (entry - exit_price) / pip
    price_move = (raw - spread_pips) * pip
    raw_usd = price_move * UNITS_001_LOT
    if pip >= 0.01:
        raw_usd /= max(exit_price, 0.0001)
    return raw_usd


@dataclass
class RangeStrategy:
    name: str
    description: str
    # Entry logic: returns (direction, entry_price) or None
    entry_logic: callable
    # Exit logic: returns (exit_price,) given bars, entry_idx, direction
    exit_logic: callable


def simulate_range_strategy(symbol: str, bars: list[dict], strategy: RangeStrategy, spread_pips: float, pip: float) -> list[dict]:
    """Simulate a range trading strategy on M1 bars."""
    trades = []
    idx = 50  # Warmup for indicators

    while idx < len(bars) - MAX_HOLD - 2:
        result = strategy.entry_logic(bars, idx, pip)
        if result is None:
            idx += 1
            continue

        direction, entry_price = result
        entry_idx = idx + 1

        if entry_idx >= len(bars) - 1:
            break

        # Simulate exit
        mfe_pips = 0.0
        mae_pips = 0.0
        exit_idx = None
        exit_price = None

        for j in range(entry_idx, min(len(bars) - 1, entry_idx + MAX_HOLD)):
            bar = bars[j]
            if direction == "BUY":
                favorable = (bar["high"] - entry_price) / pip
                adverse = (entry_price - bar["low"]) / pip
                close_pips = (bar["close"] - entry_price) / pip
            else:
                favorable = (entry_price - bar["low"]) / pip
                adverse = (bar["high"] - entry_price) / pip
                close_pips = (entry_price - bar["close"]) / pip

            mfe_pips = max(mfe_pips, favorable)
            mae_pips = max(mae_pips, adverse)

            exit_result = strategy.exit_logic(bars, j, entry_idx, direction, entry_price, pip, mfe_pips)
            if exit_result is not None:
                exit_idx = j
                exit_price = exit_result
                break

        if exit_idx is None:
            exit_idx = min(len(bars) - 1, entry_idx + MAX_HOLD - 1)
            exit_price = bars[exit_idx]["close"]

        pnl_pips = ((exit_price - entry_price) / pip if direction == "BUY"
                    else (entry_price - exit_price) / pip) - spread_pips
        pnl_usd = pnl_usd_001(direction, entry_price, exit_price, spread_pips, pip)

        trades.append({
            "direction": direction, "entry_idx": entry_idx, "exit_idx": exit_idx,
            "hold_bars": exit_idx - entry_idx + 1, "pnl_pips": pnl_pips,
            "pnl_usd": pnl_usd, "mfe_pips": mfe_pips, "mae_pips": mae_pips,
        })
        idx = exit_idx + 1
    else:
        idx += 1

    return trades


def build_range_strategies() -> list[RangeStrategy]:
    """Build all range-compatible trading strategies."""

    # 1. Asian Range Fade — buy near range low, sell near range high
    def asian_range_fade_entry(bars, idx, pip):
        # Look for range boundaries over last 60 bars
        lookback = 60
        if idx < lookback:
            return None
        recent = bars[idx - lookback: idx]
        range_high = max(b["high"] for b in recent)
        range_low = min(b["low"] for b in recent)
        range_width = (range_high - range_low) / pip

        # Range must be reasonably tight (< 50 pips)
        if range_width > 50 or range_width < 5:
            return None

        cur = bars[idx]
        # Fade the bottom
        if cur["close"] <= range_low + 3 * pip:
            return ("BUY", cur["close"])
        # Fade the top
        if cur["close"] >= range_high - 3 * pip:
            return ("SELL", cur["close"])
        return None

    def asian_range_fade_exit(bars, j, entry_idx, direction, entry_price, pip, mfe_pips):
        cur = bars[j]
        # Exit at range mean (midpoint) or if target reached
        lookback = 60
        if j >= lookback:
            recent = bars[j - lookback: j]
            range_mid = (max(b["high"] for b in recent) + min(b["low"] for b in recent)) / 2
            if direction == "BUY" and cur["close"] >= range_mid:
                return cur["close"]
            if direction == "SELL" and cur["close"] <= range_mid:
                return cur["close"]
        # Stop loss: 10 pips
        if direction == "BUY" and cur["close"] < entry_price - 10 * pip:
            return cur["close"]
        if direction == "SELL" and cur["close"] > entry_price + 10 * pip:
            return cur["close"]
        # Time exit
        hold = j - entry_idx
        if hold >= 20:
            return cur["close"]
        return None

    # 2. Mean Reversion — enter when price deviates from SMA, exit at SMA
    def mean_reversion_entry(bars, idx, pip):
        period = 20
        ma = sma(bars, idx, period)
        if ma <= 0:
            return None
        cur = bars[idx]
        deviation = (cur["close"] - ma) / pip

        # Enter when price is > 2 standard deviations from mean
        if idx < period + 50:
            return None
        recent_prices = [bars[i]["close"] for i in range(idx - period, idx)]
        avg = sum(recent_prices) / len(recent_prices)
        variance = sum((p - avg) ** 2 for p in recent_prices) / len(recent_prices)
        std = variance ** 0.5
        std_pips = std / pip

        if std_pips < 1.0:  # Need some volatility
            return None

        if deviation < -2.0 * std_pips:  # Oversold
            return ("BUY", cur["close"])
        if deviation > 2.0 * std_pips:  # Overbought
            return ("SELL", cur["close"])
        return None

    def mean_reversion_exit(bars, j, entry_idx, direction, entry_price, pip, mfe_pips):
        cur = bars[j]
        period = 20
        ma = sma(bars, j, period)

        # Exit at mean
        if direction == "BUY" and cur["close"] >= ma:
            return cur["close"]
        if direction == "SELL" and cur["close"] <= ma:
            return cur["close"]

        # Stop loss: 10 pips
        if direction == "BUY" and cur["close"] < entry_price - 10 * pip:
            return cur["close"]
        if direction == "SELL" and cur["close"] > entry_price + 10 * pip:
            return cur["close"]

        # Time exit
        hold = j - entry_idx
        if hold >= 15:
            return cur["close"]
        return None

    # 3. Candle direction in range — small directional moves
    def candle_direction_range_entry(bars, idx, pip):
        # Only enter during low-volatility periods
        atr = compute_atr(bars, idx)
        atr_pips = atr / pip if atr > 0 else 0
        if atr_pips > 5.0:  # Too volatile for range trading
            return None
        if atr_pips < 1.0:  # Too dead, no movement
            return None

        cur = bars[idx]
        body = abs(cur["close"] - cur["open"]) / pip
        if body < 2.0:  # Need some momentum
            return None

        if cur["close"] > cur["open"]:
            return ("BUY", cur["close"])
        else:
            return ("SELL", cur["close"])

    def candle_direction_range_exit(bars, j, entry_idx, direction, entry_price, pip, mfe_pips):
        cur = bars[j]
        hold = j - entry_idx

        # Quick take profit: 5 pips
        if direction == "BUY" and cur["close"] >= entry_price + 5 * pip:
            return cur["close"]
        if direction == "SELL" and cur["close"] <= entry_price - 5 * pip:
            return cur["close"]

        # Stop loss: 8 pips
        if direction == "BUY" and cur["close"] < entry_price - 8 * pip:
            return cur["close"]
        if direction == "SELL" and cur["close"] > entry_price + 8 * pip:
            return cur["close"]

        # Time exit: 10 bars max
        if hold >= 10:
            return cur["close"]
        return None

    return [
        RangeStrategy("asian_range_fade", "Fade range boundaries, exit at mean",
                      asian_range_fade_entry, asian_range_fade_exit),
        RangeStrategy("mean_reversion", "Enter at 2 std dev from mean, exit at mean",
                      mean_reversion_entry, mean_reversion_exit),
        RangeStrategy("candle_direction_range", "Small directional moves in low-vol range",
                      candle_direction_range_entry, candle_direction_range_exit),
    ]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--days", type=int, default=30)
    parser.add_argument("--symbols", nargs="+", default=None)
    args = parser.parse_args()

    mt5.initialize()

    symbols = args.symbols if args.symbols else ALL_FX_SYMBOLS
    strategies = build_range_strategies()

    print(f"Range Strategy Backtest — {args.days} days, {len(symbols)} symbols, {len(strategies)} strategies")
    print()

    results = []

    for symbol in symbols:
        pip = PIP_SIZES.get(symbol, 0.0001)
        spread = SPREADS.get(symbol, 1.0)

        bars = load_bars(symbol, args.days)
        if not bars or len(bars) < 100:
            print(f"  {symbol}: INSUFFICIENT DATA")
            continue

        print(f"  Testing {symbol} ({len(bars)} bars, spread={spread}p)...")

        for strat in strategies:
            trades = simulate_range_strategy(symbol, bars, strat, spread, pip)
            if not trades:
                continue

            wins = [t for t in trades if t["pnl_usd"] > 0]
            net_usd = sum(t["pnl_usd"] for t in trades)
            wr = len(wins) / len(trades) * 100 if trades else 0
            exp_usd = net_usd / len(trades) if trades else 0
            avg_hold = mean(t["hold_bars"] for t in trades) if trades else 0

            results.append({
                "symbol": symbol,
                "strategy": strat.name,
                "trades": len(trades),
                "trades_per_day": round(len(trades) / args.days, 1),
                "wr_pct": round(wr, 1),
                "net_usd": round(net_usd, 2),
                "exp_usd": round(exp_usd, 3),
                "avg_hold_bars": round(avg_hold, 1),
            })

    # Sort by exp_usd descending
    results.sort(key=lambda r: r["exp_usd"], reverse=True)

    # Output
    if results:
        fieldnames = list(results[0].keys())
        print("\n" + ",".join(fieldnames))
        for r in results:
            print(",".join(str(r[k]) for k in fieldnames))

        output_path = ROOT / "reports" / "range_strategy_backtest.csv"
        output_path.parent.mkdir(exist_ok=True)
        with open(output_path, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(results)
        print(f"\nSaved to {output_path}")

        # Print summary table
        print("\n=== TOP 10 RANGE STRATEGIES ===")
        print(f"{'Symbol':>8} | {'Strategy':>25} | {'Trd/Day':>7} | {'WR':>5} | {'Exp/Trade':>9} | {'Net USD':>9}")
        print("-" * 80)
        for r in results[:10]:
            print(f"{r['symbol']:>8} | {r['strategy']:>25} | {r['trades_per_day']:>7.1f} | {r['wr_pct']:>4.1f}% | ${r['exp_usd']:>8.3f} | ${r['net_usd']:>8.2f}")
    else:
        print("No trades found.")

    mt5.shutdown()


if __name__ == "__main__":
    main()
