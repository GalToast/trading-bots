#!/usr/bin/env python3
"""Spread-Adjusted Strategy Router — Find each coin's best strategy.

Tests ALL 9 Coinbase coins across ALL 3 strategies (fibonacci, momentum, supertrend)
with realistic spread costs, then produces an optimal routing table that assigns
each coin to the strategy with the highest spread-adjusted edge.

The impossible thing: Turning "none of these strategies work" into "THIS strategy
works on THIS coin but not THAT one" — meta-optimization without new strategy code.

Usage:
    python scripts/strategy_router_backtest.py
    python scripts/strategy_router_backtest.py --coins NOM-USD RAVE-USD --days 30
    python scripts/strategy_router_backtest.py --mode spread_adjusted
"""
from __future__ import annotations

import argparse
import json
import math
import os
import random
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
try:
    from coinbase_advanced_client import CoinbaseAdvancedClient
    HAS_CLIENT = True
except ImportError:
    HAS_CLIENT = False

ROOT = Path(__file__).resolve().parent.parent
REPORTS = ROOT / "reports"

# ---------------------------------------------------------------------------
# Strategy configs
# ---------------------------------------------------------------------------

# Fibonacci breakout (as used in multi_coin_isolated_runner.py)
FIB_CONFIGS = {
    "NOM-USD":  {"fib_lookback": 20, "fib_level": 0.618, "min_breakout_pct": 0.02, "tp_pct": 0.08, "sl_pct": 0.03, "max_hold": 24},
    "GHST-USD": {"fib_lookback": 10, "fib_level": 0.618, "min_breakout_pct": 0.02, "tp_pct": 0.08, "sl_pct": 0.03, "max_hold": 96},
    "SUP-USD":  {"fib_lookback": 20, "fib_level": 0.618, "min_breakout_pct": 0.02, "tp_pct": 0.08, "sl_pct": 0.03, "max_hold": 24},
}

# Supertrend (from crypto_supertrend_fidelity_audit.py)
ST_CONFIGS = {
    "RAVE-USD": {"atr_period": 10, "atr_mult": 3.0, "tp_pct": 0.10, "sl_pct": 0.05, "max_hold": 48},
    "IOTX-USD": {"atr_period": 10, "atr_mult": 3.0, "tp_pct": 0.10, "sl_pct": 0.03, "max_hold": 48},
    "TRU-USD":  {"atr_period": 10, "atr_mult": 3.0, "tp_pct": 0.10, "sl_pct": 0.03, "max_hold": 48},
    "BAL-USD":  {"atr_period": 10, "atr_mult": 3.0, "tp_pct": 0.10, "sl_pct": 0.05, "max_hold": 96},
    # Also test supertrend on fibonacci coins
    "NOM-USD":  {"atr_period": 10, "atr_mult": 3.0, "tp_pct": 0.10, "sl_pct": 0.03, "max_hold": 24},
    "GHST-USD": {"atr_period": 10, "atr_mult": 3.0, "tp_pct": 0.10, "sl_pct": 0.03, "max_hold": 96},
    "SUP-USD":  {"atr_period": 10, "atr_mult": 3.0, "tp_pct": 0.10, "sl_pct": 0.03, "max_hold": 24},
}

# Momentum breakout (from multi_coin_isolated_runner.py)
MOM_CONFIGS = {
    "A8-USD":   {"mom_lookback": 20, "mom_threshold": 0.005, "tp_pct": 0.08, "sl_pct": 0.03, "max_hold": 48},
    "CFG-USD":  {"mom_lookback": 20, "mom_threshold": 0.005, "tp_pct": 0.08, "sl_pct": 0.03, "max_hold": 48},
    # Also test on other coins
    "RAVE-USD": {"mom_lookback": 20, "mom_threshold": 0.005, "tp_pct": 0.10, "sl_pct": 0.05, "max_hold": 48},
    "NOM-USD":  {"mom_lookback": 20, "mom_threshold": 0.005, "tp_pct": 0.08, "sl_pct": 0.03, "max_hold": 24},
}

# Spread defaults (% of mid price)
DEFAULT_SPREADS = {
    "NOM-USD":  0.003,   # 0.3% — microcap, wide
    "GHST-USD": 0.002,   # 0.2%
    "SUP-USD":  0.002,   # 0.2%
    "RAVE-USD": 0.00285, # 0.285% — from live audit
    "IOTX-USD": 0.013,   # 1.3% — very wide
    "TRU-USD":  0.0127,  # 1.27% — very wide
    "BAL-USD":  0.002,   # 0.2%
    "A8-USD":   0.003,   # 0.3% — estimate
    "CFG-USD":  0.003,   # 0.3% — estimate
}

SESSION_DEAD_HOURS = {0, 6, 12, 19}
FEE_RATE = 0.004
STARTING_CASH = 100.0


def compute_atr(candles_hist, period=14):
    """Compute ATR from candle history."""
    if len(candles_hist) < period + 1:
        return 0.0
    trs = []
    for i in range(max(1, len(candles_hist) - period), len(candles_hist)):
        h = float(candles_hist[i]["high"])
        l = float(candles_hist[i]["low"])
        cp = float(candles_hist[i - 1]["close"])
        trs.append(max(h - l, abs(h - cp), abs(l - cp)))
    return sum(trs) / len(trs) if trs else 0.0


# ===================================================================
# Strategy Signal Functions
# ===================================================================

def fibonacci_signal(candles_hist, closes, params):
    """Fibonacci breakout signal."""
    lookback = params.get("fib_lookback", 20)
    if len(candles_hist) < lookback + 5:
        return False, 0.0

    recent = candles_hist[-lookback:]
    highs = [float(c["high"]) for c in recent]
    lows = [float(c["low"]) for c in recent]
    period_high = max(highs)
    period_low = min(lows)

    fib_level = params.get("fib_level", 0.618)
    fib_price = period_high - (period_high - period_low) * fib_level
    current = closes[-1]
    breakout_pct = (current - fib_price) / fib_price if fib_price > 0 else 0

    min_breakout = params.get("min_breakout_pct", 0.02)
    if breakout_pct < min_breakout:
        return False, breakout_pct

    # Volume gate
    if len(candles_hist) >= 20:
        volumes = [float(c.get("volume", 0)) for c in candles_hist[-20:]]
        avg_vol = sum(volumes) / len(volumes) if volumes else 0
        cur_vol = float(candles_hist[-1].get("volume", 0))
        if avg_vol > 0 and cur_vol < avg_vol * 0.8:
            return False, breakout_pct

    # Momentum gate
    if len(candles_hist) >= 3:
        green = sum(1 for c in candles_hist[-3:] if float(c["close"]) > float(c["open"]))
        if green < 2:
            return False, breakout_pct

    return True, breakout_pct


def momentum_signal(candles_hist, closes, params):
    """Momentum breakout: buy when price breaks above recent high with strength."""
    lookback = params.get("mom_lookback", 20)
    if len(closes) < lookback + 1:
        return False, 0.0

    recent = closes[-lookback:]
    period_high = max(recent)
    current = closes[-1]
    breakout_pct = (current - period_high) / period_high if period_high > 0 else 0

    threshold = params.get("mom_threshold", 0.005)
    if breakout_pct < threshold:
        return False, breakout_pct

    # Volume confirmation
    if len(candles_hist) >= 20:
        volumes = [float(c.get("volume", 0)) for c in candles_hist[-20:]]
        avg_vol = sum(volumes) / len(volumes) if volumes else 0
        cur_vol = float(candles_hist[-1].get("volume", 0))
        if avg_vol > 0 and cur_vol < avg_vol * 0.5:
            return False, breakout_pct

    return True, breakout_pct


def supertrend_signal(candles_hist, closes, params):
    """Supertrend: buy when close > ST line."""
    atr_period = params.get("atr_period", 10)
    atr_mult = params.get("atr_mult", 3.0)
    if len(candles_hist) < atr_period + 1:
        return False, 0.0

    atr = compute_atr(candles_hist, atr_period)
    hl2 = (float(candles_hist[-1]["high"]) + float(candles_hist[-1]["low"])) / 2
    st = hl2 - atr_mult * atr
    if st is None:
        return False, 0.0

    return closes[-1] > st, (closes[-1] - st) / st if st > 0 else 0.0


SIGNAL_FUNCTIONS = {
    "fibonacci": fibonacci_signal,
    "momentum": momentum_signal,
    "supertrend": supertrend_signal,
}


# ===================================================================
# Backtest Engine
# ===================================================================

def run_backtest(candles, strategy, params, *, mode="spread_adjusted", spread_pct=0.0, seed=42):
    """Generic backtest that works for any strategy."""
    rng = random.Random(seed)
    cash = STARTING_CASH
    pos = None
    trades = []
    wins = 0
    losses = 0
    signals = 0
    same_bar_blocked = 0
    total_spread_paid = 0.0
    total_fees_paid = 0.0

    closes_hist = []
    candles_hist = []

    signal_fn = SIGNAL_FUNCTIONS[strategy]
    tp_pct = params["tp_pct"]
    sl_pct = params.get("sl_pct", 0)
    max_hold = params["max_hold"]

    for i in range(len(candles)):
        c = candles[i]
        close = float(c["close"])
        high = float(c["high"])
        low = float(c["low"])
        candle_open = float(c["open"])

        if candle_open <= 0 or close <= 0:
            continue

        closes_hist.append(close)
        candles_hist.append(dict(c))
        if len(closes_hist) > 500:
            closes_hist = closes_hist[-500:]
            candles_hist = candles_hist[-500:]

        ts = int(c.get("start", c.get("time", 0)))
        hour = datetime.fromtimestamp(ts, tz=timezone.utc).hour
        session_open = hour not in SESSION_DEAD_HOURS

        # EXIT
        if pos is not None:
            pos["hold"] += 1
            exit_price = None

            if high >= pos["tp"]:
                exit_price = pos["tp"]
            elif pos["sl"] > 0 and low <= pos["sl"]:
                exit_price = pos["sl"]
            elif pos["hold"] >= pos["max_hold"]:
                exit_price = close

            if exit_price is not None:
                if mode == "no_same_bar" and pos["entry_bar"] == i:
                    same_bar_blocked += 1
                    pos["hold"] -= 1
                    continue

                effective_exit = exit_price
                if mode == "spread_adjusted":
                    effective_exit -= effective_exit * spread_pct
                    total_spread_paid += spread_pct * pos["units"] * effective_exit

                units = pos["units"]
                gross = (effective_exit - pos["ep"]) * units
                entry_fee = pos["entry_fee"]
                exit_fee = effective_exit * units * FEE_RATE
                total_fees_paid += entry_fee + exit_fee
                net = gross - entry_fee - exit_fee

                cash += pos["q"] + net
                trades.append(net)
                if net > 0:
                    wins += 1
                else:
                    losses += 1
                pos = None

        # ENTRY
        if pos is None and session_open:
            signal, strength = signal_fn(candles_hist, closes_hist, params)
            if signal:
                signals += 1
                if rng.random() < 0.9:  # 90% fill rate
                    effective_entry = candle_open
                    if mode == "spread_adjusted":
                        effective_entry += effective_entry * spread_pct
                        total_spread_paid += spread_pct * (STARTING_CASH / effective_entry) * effective_entry

                    deploy = cash * 0.9
                    entry_fee = deploy * FEE_RATE
                    units = (deploy - entry_fee) / effective_entry
                    tp = effective_entry * (1 + tp_pct)
                    sl = effective_entry * (1 - sl_pct) if sl_pct > 0 else 0.0
                    cash -= deploy
                    pos = {
                        "ep": effective_entry,
                        "q": deploy,
                        "units": units,
                        "tp": tp,
                        "sl": sl,
                        "hold": 0,
                        "max_hold": max_hold,
                        "entry_fee": entry_fee,
                        "entry_bar": i,
                    }

    total_pnl = sum(trades)
    wr = wins / len(trades) * 100 if trades else 0
    avg_pnl = total_pnl / len(trades) if trades else 0

    return {
        "total_pnl": round(total_pnl, 2),
        "wr_pct": round(wr, 1),
        "trades": len(trades),
        "wins": wins,
        "losses": losses,
        "avg_pnl_per_trade": round(avg_pnl, 4),
        "signals": signals,
        "same_bar_blocked": same_bar_blocked,
        "total_spread_paid": round(total_spread_paid, 2),
        "total_fees_paid": round(total_fees_paid, 2),
        "final_cash": round(cash, 2),
        "return_pct": round((cash - STARTING_CASH) / STARTING_CASH * 100, 2),
    }


def fetch_candles_for_coin(coin, days=30):
    """Fetch candles for a coin."""
    if not HAS_CLIENT:
        print(f"  ⚠️  No Coinbase client available, skipping {coin}")
        return []

    client = CoinbaseAdvancedClient()
    end = int(time.time())
    start = end - days * 86400
    chunk_sec = 300 * 5 * 60
    all_candles = []
    cs = start
    while cs < end:
        ce = min(cs + chunk_sec, end)
        try:
            resp = client.market_candles(coin, start=cs, end=ce, granularity="FIVE_MINUTE")
            cands = resp.get("candles", [])
            all_candles.extend(cands)
            cs = ce
            if not cands:
                break
            time.sleep(0.15)
        except Exception as e:
            print(f"  WARN fetch error for {coin} at {cs}: {e}")
            cs += chunk_sec
    all_candles.sort(key=lambda c: int(c.get("start", c.get("time", 0))))
    return all_candles


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--coins", nargs="+", default=None)
    parser.add_argument("--days", type=int, default=30)
    parser.add_argument("--mode", default="spread_adjusted",
                       choices=["naive", "spread_adjusted", "slippage_adjusted", "no_same_bar"])
    args = parser.parse_args()

    coins = args.coins or list(DEFAULT_SPREADS.keys())
    strategies = ["fibonacci", "supertrend", "momentum"]

    print("=" * 80)
    print(f"  SPREAD-ADJUSTED STRATEGY ROUTER — {args.mode.upper()} MODE, {args.days}d")
    print("=" * 80)

    results = {}

    for coin in coins:
        print(f"\n{'─' * 80}")
        print(f"  {coin}")
        print(f"{'─' * 80}")

        candles = fetch_candles_for_coin(coin, args.days)
        if not candles:
            print(f"  ⚠️  No candles for {coin} — skipping")
            continue

        print(f"  Candles: {len(candles)}")
        spread = DEFAULT_SPREADS.get(coin, 0.002)
        print(f"  Spread: {spread*100:.2f}%")

        coin_results = {"coin": coin, "spread_pct": spread, "strategies": {}}
        best_strategy = None
        best_pnl = -float("inf")

        for strategy in strategies:
            # Get config for this coin+strategy
            config = None
            if strategy == "fibonacci" and coin in FIB_CONFIGS:
                config = FIB_CONFIGS[coin]
            elif strategy == "supertrend" and coin in ST_CONFIGS:
                config = ST_CONFIGS[coin]
            elif strategy == "momentum" and coin in MOM_CONFIGS:
                config = MOM_CONFIGS[coin]

            if config is None:
                coin_results["strategies"][strategy] = {"status": "no_config"}
                continue

            result = run_backtest(candles, strategy, config, mode=args.mode, spread_pct=spread)
            coin_results["strategies"][strategy] = result

            if result["total_pnl"] > best_pnl:
                best_pnl = result["total_pnl"]
                best_strategy = strategy

            status = "✅" if result["total_pnl"] > 0 else "❌"
            print(f"  {status} {strategy:12s}: PnL=${result['total_pnl']:+7.2f} | "
                  f"WR={result['wr_pct']:5.1f}% | Trades={result['trades']:4d} | "
                  f"Signals={result['signals']:4d} | Return={result['return_pct']:+6.2f}%")

        coin_results["best_strategy"] = best_strategy
        coin_results["best_pnl"] = best_pnl
        results[coin] = coin_results

        if best_strategy:
            print(f"  🏆 BEST: {best_strategy} (${best_pnl:+.2f})")

    # Summary table
    print(f"\n{'=' * 80}")
    print(f"  OPTIMAL ROUTING TABLE ({args.mode})")
    print(f"{'=' * 80}")
    print(f"\n{'Coin':<12} {'Current':<14} {'Best':<14} {'Best PnL':>10} {'Current PnL':>12} {'Delta':>10}")
    print(f"{'─' * 80}")

    for coin, data in results.items():
        if not data.get("best_strategy"):
            continue

        # Find current strategy PnL
        current_strategy = None
        # Default assignments from runner
        if coin in FIB_CONFIGS:
            current_strategy = "fibonacci"
        elif coin in ST_CONFIGS and coin not in FIB_CONFIGS:
            current_strategy = "supertrend"
        elif coin in MOM_CONFIGS and coin not in FIB_CONFIGS:
            current_strategy = "momentum"

        current_pnl = 0.0
        if current_strategy and current_strategy in data["strategies"]:
            current_pnl = data["strategies"][current_strategy].get("total_pnl", 0.0)

        best = data["best_strategy"]
        best_pnl = data["best_pnl"]
        delta = best_pnl - current_pnl if isinstance(current_pnl, (int, float)) else best_pnl

        print(f"{coin:<12} {current_strategy or 'N/A':<14} {best:<14} ${best_pnl:>8.2f}  ${current_pnl:>10.2f}  ${delta:>+8.2f}")

    # Save results
    output = REPORTS / f"strategy_router_{args.mode}_{args.days}d.json"
    REPORTS.mkdir(parents=True, exist_ok=True)
    with open(output, "w") as f:
        json.dump(results, f, indent=2, default=str)
    print(f"\nResults saved: {output}")


if __name__ == "__main__":
    main()
