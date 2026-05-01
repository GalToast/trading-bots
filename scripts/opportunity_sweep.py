#!/usr/bin/env python3
"""
Coinbase Opportunity Sweep — Using strategy_library.py as single source of truth.

Sweeps all available coins across multiple strategies with consistent semantics.
Reports top profitable coin-strategy combos for portfolio deployment.

Usage:
    python scripts/opportunity_sweep.py --strategies bb_reversion momentum vol_squeeze range_breakout --window 30d
    python scripts/opportunity_sweep.py --coins RAVE-USD IOTX-USD BAL-USD --strategies rsi_mr momentum
    python scripts/opportunity_sweep.py --top 20  # report top 20 profitable combos

Params per strategy (defaults, tunable via --params json):
    rsi_mr:         rsi_period=3, os_thresh=30, tp_pct=25, sl_pct=0, max_hold=48
    momentum:       lookback=10, tp_pct=10, sl_pct=5, max_hold=48
    bb_reversion:   bb_period=20, rsi_period=3, rsi_thresh=30, proximity_pct=3, sl_pct=5, max_hold=24
    vol_squeeze:    bb_period=20, squeeze_thresh=2.0, tp_pct=5, sl_pct=3, max_hold=48
    ema_pullback:   ema_period=200, rsi_period=3, rsi_thresh=40, tp_pct=5, sl_pct=5, max_hold=48
    range_breakout: range_lookback=20, tp_pct=5, sl_pct=3, max_hold=48
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(__file__))

try:
    from candle_cache_service import load_candles
except ImportError:
    load_candles = None

from strategy_library import (
    rsi_mr, momentum, bb_reversion, vol_squeeze, ema_pullback, range_breakout,
)

STRATEGIES = {
    "rsi_mr": rsi_mr,
    "momentum": momentum,
    "bb_reversion": bb_reversion,
    "vol_squeeze": vol_squeeze,
    "ema_pullback": ema_pullback,
    "range_breakout": range_breakout,
}

DEFAULT_PARAMS = {
    "rsi_mr": {"rsi_period": 3, "os_thresh": 30, "tp_pct": 25.0, "sl_pct": 0.0, "max_hold": 48},
    "momentum": {"lookback": 10, "tp_pct": 10.0, "sl_pct": 5.0, "max_hold": 48},
    "bb_reversion": {"bb_period": 20, "rsi_period": 3, "rsi_thresh": 30, "proximity_pct": 3.0, "sl_pct": 5.0, "max_hold": 24},
    "vol_squeeze": {"bb_period": 20, "squeeze_thresh": 2.0, "tp_pct": 5.0, "sl_pct": 3.0, "max_hold": 48},
    "ema_pullback": {"ema_period": 200, "rsi_period": 3, "rsi_thresh": 40, "tp_pct": 5.0, "sl_pct": 5.0, "max_hold": 48},
    "range_breakout": {"range_lookback": 20, "tp_pct": 5.0, "sl_pct": 3.0, "max_hold": 48},
}

# Coins already tested in prior scans (from mass_coin scan)
ALREADY_TESTED = {
    "RAVE-USD", "MOG-USD", "BAL-USD", "IOTX-USD", "BLUR-USD",
    "DOGE-USD", "XRP-USD", "SOL-USD", "BTC-USD", "ETH-USD",
}

REPORT_DIR = os.path.join(os.path.dirname(__file__), "..", "reports")


def get_coinbase_coins():
    """Get list of USD-trading coins from Coinbase."""
    try:
        import coinbase_advanced_client
        products = coinbase_advanced_client.get_products()
        return [p["product_id"] for p in products if p.get("quote_currency_id") == "USD" and p.get("status") == "online"]
    except Exception:
        # Fallback: known coin list from prior scans
        return []


CACHE_DIR = os.path.join(os.path.dirname(__file__), "..", "reports", "candle_cache")

def load_coin_candles(coin: str, window_days: int = 30):
    """Load candles for a coin, preferring local cache files."""
    # Try local cache first (fast, no API)
    suffix = f"{window_days}d"
    coin_file = coin.replace("-USD", "_USD")
    cache_path = os.path.join(CACHE_DIR, f"{coin_file}_FIVE_MINUTE_{suffix}.json")
    
    if os.path.exists(cache_path):
        try:
            with open(cache_path) as f:
                data = json.load(f)
            # Handle both direct list and wrapped format
            if isinstance(data, list):
                raw = data
            elif isinstance(data, dict) and "candles" in data:
                raw = data["candles"]
            elif isinstance(data, dict) and "coins" in data:
                raw = data["coins"].get(coin, {}).get("candles", [])
            else:
                raw = []
            return normalize_candles(raw)
        except Exception:
            pass

    # Try reconciliation candles
    recon_path = os.path.join(REPORT_DIR, "reconciliation_candles.json")
    if os.path.exists(recon_path):
        try:
            with open(recon_path) as f:
                data = json.load(f)
            if coin in data.get("coins", {}):
                raw = data["coins"][coin]["candles"]
                return normalize_candles(raw)
        except Exception:
            pass

    # Last resort: try cache service
    if load_candles:
        try:
            return load_candles(coin, window_days)
        except Exception:
            pass

    return []


def normalize_candles(raw):
    """Ensure candles have proper float/int types."""
    if not raw:
        return []
    candles = []
    for c in raw:
        candles.append({
            "open": float(c.get("open", c.get("o", 0))),
            "high": float(c.get("high", c.get("h", 0))),
            "low": float(c.get("low", c.get("l", 0))),
            "close": float(c.get("close", c.get("c", 0))),
            "start": int(c.get("start", c.get("t", c.get("time", 0)))),
            "volume": float(c.get("volume", c.get("v", 0))),
        })
    return candles


def sweep(coin: str, strategy_name: str, params: dict, candles: list[dict]) -> dict:
    """Run one strategy on one coin."""
    fn = STRATEGIES[strategy_name]
    kwargs = dict(params)
    kwargs["starting_cash"] = 48.0
    kwargs["fee_rate"] = 0.004

    if strategy_name == "momentum":
        kwargs["entry_slip"] = 0.0
        kwargs["exit_slip"] = 0.0
        kwargs["fill_prob"] = 1.0
    elif strategy_name == "bb_reversion":
        kwargs["entry_slip"] = 0.0
        kwargs["exit_slip"] = 0.0
        kwargs["fill_prob"] = 1.0
    else:
        kwargs["entry_slip"] = 0.0
        kwargs["exit_slip"] = 0.0
        kwargs["fill_prob"] = 1.0

    result = fn(candles, **kwargs)
    result["coin"] = coin
    result["strategy"] = strategy_name
    return result


def main():
    parser = argparse.ArgumentParser(description="Coinbase Opportunity Sweep")
    parser.add_argument("--coins", nargs="*", help="Specific coins to sweep")
    parser.add_argument("--strategies", nargs="*", default=list(STRATEGIES.keys()),
                        help="Strategies to sweep")
    parser.add_argument("--window", default="30d", help="Candle window (7d, 30d)")
    parser.add_argument("--top", type=int, default=20, help="Report top N profitable combos")
    parser.add_argument("--min-candles", type=int, default=500, help="Minimum candles required")
    parser.add_argument("--include-tested", action="store_true", help="Include already-tested coins")
    parser.add_argument("--output", help="Output JSON path (default: reports/opportunity_sweep.json)")
    args = parser.parse_args()

    window_days = 30 if "30" in args.window else 7

    # Get coins
    if args.coins:
        coins = args.coins
    else:
        coins = get_coinbase_coins()
        if not coins:
            print("ERROR: No coin list available. Pass --coins explicitly.")
            sys.exit(1)

    if not args.include_tested:
        coins = [c for c in coins if c not in ALREADY_TESTED]

    print(f"=" * 70)
    print(f"OPPORTUNITY SWEEP — {len(coins)} coins × {len(args.strategies)} strategies")
    print(f"Window: {window_days}d | Min candles: {args.min_candles}")
    print(f"Strategies: {', '.join(args.strategies)}")
    print(f"=" * 70)

    results = []
    total = len(coins) * len(args.strategies)
    done = 0
    start = time.time()

    for coin in coins:
        candles = load_coin_candles(coin, window_days)
        if candles is None:
            continue
        candles = normalize_candles(candles)
        if len(candles) < args.min_candles:
            print(f"  SKIP {coin}: only {len(candles)} candles (need {args.min_candles})")
            continue

        for strat in args.strategies:
            params = dict(DEFAULT_PARAMS.get(strat, {}))
            try:
                result = sweep(coin, strat, params, candles)
                results.append(result)
            except Exception as e:
                print(f"  ERROR {coin}/{strat}: {e}")

            done += 1
            if done % 20 == 0:
                elapsed = time.time() - start
                rate = done / elapsed if elapsed > 0 else 0
                eta = (total - done) / rate if rate > 0 else 0
                print(f"  Progress: {done}/{total} ({done/total*100:.0f}%) — {rate:.1f}/s — ETA: {eta:.0f}s")

    elapsed = time.time() - start
    print(f"\nSweep complete: {len(results)} results in {elapsed:.1f}s")

    # Sort by net PnL descending
    results.sort(key=lambda r: r.get("net_pnl", 0), reverse=True)

    # Report top N
    print(f"\n{'=' * 70}")
    print(f"TOP {args.top} PROFITABLE COMBOS:")
    print(f"{'Rank':<5} {'Coin':<15} {'Strategy':<20} {'Net PnL':>10} {'WR':>7} {'Trades':>7} {'Signals':>8} {'DD':>7}")
    print(f"{'-' * 70}")

    profitable = [r for r in results if r["net_pnl"] > 0]
    for i, r in enumerate(results[:args.top], 1):
        print(f"{i:<5} {r['coin']:<15} {r['strategy']:<20} "
              f"${r['net_pnl']:>8.2f} {r['win_rate']:>6.1f}% {r['trades']:>7} "
              f"{r['signals']:>8} {r['max_drawdown']:>6.1f}%")

    print(f"\n{'=' * 70}")
    print(f"SUMMARY:")
    print(f"  Total combos tested: {len(results)}")
    print(f"  Profitable: {len(profitable)} ({len(profitable)/max(len(results),1)*100:.1f}%)")
    if results:
        print(f"  Top combo: {results[0]['coin']} {results[0]['strategy']} ${results[0]['net_pnl']:.2f}")
        print(f"  Avg profitable PnL: ${sum(r['net_pnl'] for r in profitable)/max(len(profitable),1):.2f}")
    else:
        print(f"  No results — check candle availability and min-candles threshold.")

    # Save
    output = args.output or os.path.join(REPORT_DIR, "opportunity_sweep.json")
    os.makedirs(REPORT_DIR, exist_ok=True)
    with open(output, "w") as f:
        json.dump({
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "window_days": window_days,
            "strategies": args.strategies,
            "coins_tested": len(coins),
            "total_combos": len(results),
            "profitable_count": len(profitable),
            "top_combos": results[:args.top],
            "all_results": results,
        }, f, indent=2)
    print(f"\nResults saved: {output}")


if __name__ == "__main__":
    main()
