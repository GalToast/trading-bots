#!/usr/bin/env python3
"""
Parallel RSI Mean-Reversion Scan — Chunk 100-149
=================================================
Scans coins at lines 100-149 (0-indexed) of coinbase_usd_pairs.txt.
Grid: RSI periods x TP levels x max hold = 500 combos per coin.
Entry: RSI(period) <= 30. Exit: TP hit or max_hold bars.
95% cash deployed, fee 0.004/side, start $48.
"""
from __future__ import annotations

import json
import sys
import time
from itertools import product
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from candle_cache_service import load_candles


RSI_PERIODS = [2, 3, 4, 5, 6, 7, 8, 10, 12, 14]
TP_LEVELS = [0.05, 0.08, 0.10, 0.15, 0.20, 0.25, 0.30, 0.35, 0.40, 0.50]
MAX_HOLDS = [12, 24, 36, 48, 72]

STARTING_CASH = 48.0
FEE_RATE = 0.004  # per side
DEPLOY_PCT = 0.95
RSI_ENTRY_THRESH = 30.0

ROOT = Path(__file__).resolve().parent.parent
COIN_LIST_PATH = ROOT / "coinbase_usd_pairs.txt"
REPORT_PATH = ROOT / "reports" / "parallel_scan_chunk_100_149.json"


def rsi(closes: list[float], period: int) -> list[float]:
    """Wilder-smoothed RSI. Returns list same length as closes, padded with 50.0."""
    n = len(closes)
    if n < period + 1:
        return [50.0] * n

    deltas = [closes[i] - closes[i - 1] for i in range(1, n)]
    gains = [d if d > 0 else 0.0 for d in deltas]
    losses = [-d if d < 0 else 0.0 for d in deltas]

    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period

    result = [50.0] * period
    if avg_loss > 0:
        rs = avg_gain / avg_loss
        result.append(100.0 - 100.0 / (1.0 + rs))
    else:
        result.append(100.0)

    for i in range(period, len(deltas)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period
        if avg_loss > 0:
            rs = avg_gain / avg_loss
            result.append(100.0 - 100.0 / (1.0 + rs))
        else:
            result.append(100.0)

    return result


def backtest_combo(candles, rsi_period, tp, max_hold):
    """
    Backtest one (rsi_period, tp, max_hold) combo on a candle list.
    Entry when RSI <= 30. Exit at TP or max_hold bars. No SL.
    Candle key is "time", "close", "high".
    Returns dict with net_pnl, gross_pnl, trades, win_rate, etc.
    """
    if len(candles) < rsi_period + 20:
        return None

    closes = [float(c["close"]) for c in candles]
    rsi_vals = rsi(closes, rsi_period)

    cash = STARTING_CASH
    position = None  # {"entry_price", "entry_bar", "qty", "entry_fee"}
    total_net = 0.0
    total_gross = 0.0
    trade_count = 0
    wins = 0

    for i in range(rsi_period + 1, len(candles)):
        if position is not None:
            bar_held = i - position["entry_bar"]
            tp_price = position["entry_price"] * (1.0 + tp)
            bar_high = float(candles[i]["high"])
            exit_price = None

            if bar_high >= tp_price:
                exit_price = tp_price
            elif bar_held >= max_hold:
                exit_price = closes[i]

            if exit_price is not None:
                exit_fee = exit_price * position["qty"] * FEE_RATE
                gross = (exit_price - position["entry_price"]) * position["qty"]
                net = gross - position["entry_fee"] - exit_fee

                total_gross += gross
                total_net += net
                trade_count += 1
                if net > 0:
                    wins += 1

                position = None
        else:
            if rsi_vals[i] <= RSI_ENTRY_THRESH:
                deploy = cash * DEPLOY_PCT
                entry_price = closes[i]
                entry_fee = entry_price * (deploy / entry_price) * FEE_RATE
                qty = (deploy - entry_fee) / entry_price

                position = {
                    "entry_price": entry_price,
                    "entry_bar": i,
                    "qty": qty,
                    "entry_fee": entry_fee,
                }
                cash -= deploy

    if position is not None:
        exit_price = closes[-1]
        exit_fee = exit_price * position["qty"] * FEE_RATE
        gross = (exit_price - position["entry_price"]) * position["qty"]
        net = gross - position["entry_fee"] - exit_fee
        total_gross += gross
        total_net += net
        trade_count += 1
        if net > 0:
            wins += 1

    if trade_count == 0:
        return None

    return {
        "net_pnl": round(total_net, 4),
        "gross_pnl": round(total_gross, 4),
        "trades": trade_count,
        "wins": wins,
        "win_rate": round(wins / trade_count, 4),
    }


def scan_coin(coin: str):
    """Scan one coin across all 500 combos. Return list of passing results."""
    t0 = time.time()
    print(f"  [{coin}] Loading 7-day M5 candles...")
    candles = load_candles(coin, "FIVE_MINUTE", 7)
    if not candles or len(candles) < 100:
        print(f"  [{coin}] Skip: insufficient candles ({len(candles) if candles else 0})")
        return []
    print(f"  [{coin}] {len(candles)} candles loaded. Running 500 combos...")

    passing = []
    combo_count = 0

    for rsi_p, tp, mh in product(RSI_PERIODS, TP_LEVELS, MAX_HOLDS):
        combo_count += 1
        result = backtest_combo(candles, rsi_p, tp, mh)
        if result and result["net_pnl"] > 0 and result["trades"] >= 5:
            passing.append({
                "coin": coin,
                "rsi_period": rsi_p,
                "tp": tp,
                "max_hold": mh,
                **result,
            })

    elapsed = time.time() - t0
    print(f"  [{coin}] Done in {elapsed:.1f}s — {len(passing)}/{combo_count} combos passed")
    return passing


def main():
    print("=" * 70)
    print("  RSI Mean-Reversion Scan — Chunk 100-149")
    print(f"  RSI periods: {RSI_PERIODS}")
    print(f"  TP levels:   {TP_LEVELS}")
    print(f"  Max holds:   {MAX_HOLDS}")
    print(f"  Combos/coin: {len(RSI_PERIODS) * len(TP_LEVELS) * len(MAX_HOLDS)}")
    print(f"  Start: ${STARTING_CASH}, Fee: {FEE_RATE}/side, Deploy: {DEPLOY_PCT*100}%")
    print("=" * 70)

    # Read coin list
    lines = COIN_LIST_PATH.read_text(encoding="utf-8").splitlines()
    coins = [
        l.strip() for l in lines
        if l.strip() and not l.strip().startswith("#") and not l.strip().startswith("Total:")
    ]

    slice_coins = coins[100:150]  # indices 100-149 inclusive (50 coins)
    print(f"\nCoins to scan: {len(slice_coins)} (indices 100-149)")
    for c in slice_coins[:3]:
        print(f"  ... {c}")
    print(f"  ...")
    for c in slice_coins[-2:]:
        print(f"  ... {c}")
    print()

    all_results = []
    for idx, coin in enumerate(slice_coins):
        results = scan_coin(coin)
        all_results.extend(results)
        if (idx + 1) % 10 == 0 or idx + 1 == len(slice_coins):
            print(f"\n  Progress: {idx + 1}/{len(slice_coins)} coins done, {len(all_results)} passing combos so far\n")

    all_results.sort(key=lambda r: r["net_pnl"], reverse=True)

    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text(
        json.dumps(all_results, indent=2),
        encoding="utf-8",
    )
    print(f"\nWrote {len(all_results)} passing combos to {REPORT_PATH}")

    # Top 10
    print(f"\n{'=' * 70}")
    print(f"  TOP 10 Most Profitable Combos")
    print(f"{'=' * 70}")
    for i, r in enumerate(all_results[:10], 1):
        print(f"  #{i:2d}  {r['coin']:16s}  RSI={r['rsi_period']:2d}  "
              f"TP={r['tp']:.2%}  Hold={r['max_hold']:3d}  "
              f"Net=${r['net_pnl']:>9.2f}  Trades={r['trades']:4d}  "
              f"WR={r['win_rate']:.1%}")


if __name__ == "__main__":
    main()
