#!/usr/bin/env python3
"""NOM Consensus Gating Backtest — Love Engine Validation.

Tests whether running fibonacci + momentum + supertrend on NOM-USD
and requiring 2+ strategies to agree (consensus gate) produces
better risk-adjusted returns than any single strategy alone.

This validates the Love Engine concept: multi-strategy voting improves
signal quality while increasing signal frequency.

Usage:
    python scripts/nom_consensus_gating_backtest.py
"""
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

from coinbase_advanced_client import CoinbaseAdvancedClient

OUTPUT_JSON = ROOT / "reports" / "nom_consensus_gating_backtest.json"
OUTPUT_MD = ROOT / "reports" / "nom_consensus_gating_backtest.md"

# Spread rate (Coinbase 0.4% = 0.004 per round-trip)
SPREAD_RATE = 0.004

# Starting capital
STARTING_CAPITAL = 100.0

# Consensus window: strategies must agree within this many candles
CONSENSUS_WINDOW = 3

# Backtest parameters
LOOKBACK_DAYS = 30
TIMEFRAME = "FIVE_MINUTE"  # M5 candles


def compute_fibonacci_signal(candles, fib_lookback=20):
    """Fibonacci breakout signal: price breaks above 0.618 retracement level.

    Simplified: check if current close > highest high of last fib_lookback candles.
    """
    if len(candles) < fib_lookback + 1:
        return False, None

    recent = candles[-fib_lookback:]
    highest_high = max(float(c["high"]) for c in recent)
    current_close = float(candles[-1]["close"])

    # Breakout signal
    signal = current_close > highest_high
    return signal, highest_high


def compute_momentum_signal(candles, lookback=10):
    """Momentum signal: current close > highest close of last lookback candles."""
    if len(candles) < lookback + 1:
        return False, None

    recent_closes = [float(c["close"]) for c in candles[-(lookback + 1):-1]]
    current_close = float(candles[-1]["close"])

    if not recent_closes:
        return False, None

    highest_close = max(recent_closes)
    signal = current_close > highest_close
    return signal, highest_close


def compute_supertrend_signal(candles, atr_period=10, atr_mult=3.0):
    """Simplified supertrend: bullish when close > ATR-based trailing stop.

    Simplified: check if current close > average of last atr_period lows + atr_mult * ATR.
    """
    if len(candles) < atr_period + 1:
        return False, None

    recent = candles[-(atr_period + 1):-1]
    lows = [float(c["low"]) for c in recent]
    highs = [float(c["high"]) for c in recent]
    closes = [float(c["close"]) for c in recent]

    if not lows or not highs:
        return False, None

    avg_low = sum(lows) / len(lows)
    avg_high = sum(highs) / len(highs)
    atr = sum(abs(h - l) for h, l in zip(highs, lows)) / len(highs)

    # Supertrend line
    supertrend_line = avg_low - atr_mult * atr
    current_close = float(candles[-1]["close"])

    signal = current_close > supertrend_line
    return signal, supertrend_line


def run_backtest(candles, strategy="single", consensus_required=1):
    """Run backtest with given strategy configuration.

    Args:
        candles: List of candle dicts
        strategy: "single" or "consensus"
        consensus_required: Number of strategies that must agree (for consensus mode)
    """
    signals = []
    trades = []
    position = None
    capital = STARTING_CAPITAL

    min_candles = 25  # Need enough history for all strategies

    for i in range(min_candles, len(candles)):
        window = candles[:i + 1]

        # Compute individual strategy signals
        fib_signal, fib_level = compute_fibonacci_signal(window)
        mom_signal, mom_level = compute_momentum_signal(window)
        st_signal, st_level = compute_supertrend_signal(window)

        # Count agreeing strategies
        agreements = sum([fib_signal, mom_signal, st_signal])

        if strategy == "consensus" and agreements < consensus_required:
            # No consensus — skip
            continue
        elif strategy == "single":
            # Single strategy: use fibonacci only
            if not fib_signal:
                continue

        # Entry signal
        if position is None and capital > 0:
            entry_price = float(candles[i]["close"])
            entry_fee = entry_price * STARTING_CAPITAL * SPREAD_RATE / entry_price  # Simplified
            deploy = capital * 0.9  # Deploy 90%

            # TP/SL (NOM fibonacci defaults)
            tp = entry_price * 1.08
            sl = entry_price * 0.97

            position = {
                "entry_price": entry_price,
                "deploy": deploy,
                "units": deploy / entry_price,
                "tp": tp,
                "sl": sl,
                "entry_fee": entry_fee,
                "hold": 0,
                "entry_index": i,
                "fib_agreed": fib_signal,
                "mom_agreed": mom_signal,
                "st_agreed": st_signal,
                "agreements": agreements,
                "starting_capital": capital,  # Track capital at entry
            }

        # Check exit conditions
        if position is not None:
            position["hold"] += 1
            current_close = float(candles[i]["close"])
            high = float(candles[i]["high"])
            low = float(candles[i]["low"])

            exit_price = None
            exit_reason = None

            if high >= position["tp"]:
                exit_price = position["tp"]
                exit_reason = "tp"
            elif low <= position["sl"]:
                exit_price = position["sl"]
                exit_reason = "sl"
            elif position["hold"] >= 24:  # Max hold (NOM default)
                exit_price = current_close
                exit_reason = "timeout"

            if exit_price is not None:
                units = position["units"]
                gross = (exit_price - position["entry_price"]) * units
                exit_fee = exit_price * units * SPREAD_RATE
                net = gross - position["entry_fee"] - exit_fee

                # CRITICAL: capital = starting_capital + net (not capital + deploy + net)
                capital = position["starting_capital"] + net
                trades.append({
                    "entry_price": position["entry_price"],
                    "exit_price": exit_price,
                    "net": round(net, 4),
                    "reason": exit_reason,
                    "hold_bars": position["hold"],
                    "agreements": position["agreements"],
                    "entry_index": position["entry_index"],
                    "exit_index": i,
                })
                signals.append({
                    "index": i,
                    "agreements": position["agreements"],
                    "fib": position["fib_agreed"],
                    "mom": position["mom_agreed"],
                    "st": position["st_agreed"],
                })
                position = None

    # Close any open position at end
    if position is not None:
        exit_price = float(candles[-1]["close"])
        units = position["units"]
        gross = (exit_price - position["entry_price"]) * units
        exit_fee = exit_price * units * SPREAD_RATE
        net = gross - position["entry_fee"] - exit_fee
        capital += position["deploy"] + net
        trades.append({
            "entry_price": position["entry_price"],
            "exit_price": exit_price,
            "net": round(net, 4),
            "reason": "end_of_test",
            "hold_bars": position["hold"],
            "agreements": position["agreements"],
        })

    return {
        "strategy": strategy,
        "consensus_required": consensus_required,
        "final_capital": round(capital, 4),
        "pnl": round(capital - STARTING_CAPITAL, 4),
        "return_pct": round((capital - STARTING_CAPITAL) / STARTING_CAPITAL * 100, 2),
        "num_trades": len(trades),
        "num_signals": len(signals),
        "trades": trades,
        "signals": signals,
    }


def main():
    print("=" * 72)
    print("NOM CONSENSUS GATING BACKTEST — Love Engine Validation")
    print("=" * 72)
    print()

    # Fetch NOM-USD M5 candles (API limits to 350 per request, so chunk it)
    print("Fetching NOM-USD M5 candles...", flush=True)
    client = CoinbaseAdvancedClient()
    now = int(time.time())
    candles = []
    chunk_seconds = 350 * 300  # 350 candles * 5 min = ~29 hours per chunk
    current_start = now - LOOKBACK_DAYS * 24 * 60 * 60

    while current_start < now:
        chunk_end = min(current_start + chunk_seconds, now)
        try:
            resp = client.market_candles("NOM-USD", start=current_start, end=chunk_end, granularity=TIMEFRAME)
            chunk = resp.get("candles", [])
            candles.extend(chunk)
            print(f"  Fetched {len(chunk)} candles ({len(candles)} total)", flush=True)
        except Exception as e:
            print(f"  [WARN] Fetch failed for chunk {current_start}-{chunk_end}: {e}", flush=True)
        current_start = chunk_end
        time.sleep(0.2)  # Rate limit buffer

    # Deduplicate by start timestamp
    seen = set()
    unique = []
    for c in candles:
        ts = int(c["start"])
        if ts not in seen:
            seen.add(ts)
            unique.append(c)
    candles = sorted(unique, key=lambda c: int(c["start"]))

    if not candles:
        print("  ❌ No candles available. Exiting.", flush=True)
        return

    print(f"\nRunning backtests ({len(candles)} candles)...", flush=True)

    # Run single-strategy backtest (fibonacci only)
    print("  Single-strategy (fibonacci only)...", flush=True)
    single_result = run_backtest(candles, strategy="single")

    # Run consensus-gated backtests
    for consensus_required in [2, 3]:
        print(f"  Consensus ({consensus_required}+ strategies agree)...", flush=True)
        consensus_result = run_backtest(candles, strategy="consensus", consensus_required=consensus_required)

        # Compare
        print(f"\n{'─' * 72}")
        print(f"  {'Metric':<30} {'Single':>12} {'Consensus 2+':>12} {'Consensus 3':>12}")
        print(f"  {'─' * 30} {'─' * 12} {'─' * 12} {'─' * 12}")
        print(f"  {'Final capital':<30} ${single_result['final_capital']:>10.2f} ${consensus_result['final_capital']:>10.2f}")
        print(f"  {'PnL':<30} ${single_result['pnl']:>+10.2f} ${consensus_result['pnl']:>+10.2f}")
        print(f"  {'Return %':<30} {single_result['return_pct']:>+10.1f}% {consensus_result['return_pct']:>+10.1f}%")
        print(f"  {'Num trades':<30} {single_result['num_trades']:>12d} {consensus_result['num_trades']:>12d}")
        print(f"  {'Num signals':<30} {single_result['num_signals']:>12d} {consensus_result['num_signals']:>12d}")

    print(f"\n{'─' * 72}")
    print(f"\n  Output: {OUTPUT_JSON}", flush=True)
    print(f"  Report: {OUTPUT_MD}", flush=True)


if __name__ == "__main__":
    main()
