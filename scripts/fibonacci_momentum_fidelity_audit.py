#!/usr/bin/env python3
"""
Fibonacci / Momentum Fidelity Audit
====================================

Audits fibonacci_breakout (NOM, GHST, SUP) and momentum (A8, CFG) strategies
across 4 fidelity modes:

  1. naive            -- mid-price fills, same-bar allowed, fee only
  2. spread_adjusted  -- entry pays ask, exit pays bid (spread cost on both sides)
  3. slippage_adjusted -- adds ATR-based slippage on top of spread
  4. no_same_bar      -- enforces 1-bar minimum between entry and exit

Key question: Does NOM fibonacci top-6 hours (+$164.30 naive) survive spread?
All-hours was -$18.70. Does +$164 survive after spread cost?

Usage:
    python scripts/fibonacci_momentum_fidelity_audit.py          # fetches live data
    python scripts/fibonacci_momentum_fidelity_audit.py --days 30
    python scripts/fibonacci_momentum_fidelity_audit.py --spread-multiplier 1.5
"""
from __future__ import annotations

import json
import math
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from coinbase_advanced_client import CoinbaseAdvancedClient

ROOT = Path(__file__).resolve().parent.parent
REPORTS = ROOT / "reports"
REPORTS.mkdir(exist_ok=True)

# ---------------------------------------------------------------------------
# Strategy configs (matching multi_coin_isolated_runner.py)
# ---------------------------------------------------------------------------
STRATEGIES = {
    "NOM-USD":  {"strategy": "fibonacci", "fib_lookback": 20, "tp_pct": 0.08, "sl_pct": 0.03, "max_hold": 24},
    "GHST-USD": {"strategy": "fibonacci", "fib_lookback": 10, "tp_pct": 0.08, "sl_pct": 0.03, "max_hold": 96},
    "SUP-USD":  {"strategy": "fibonacci", "fib_lookback": 20, "tp_pct": 0.08, "sl_pct": 0.03, "max_hold": 24},
    "A8-USD":   {"strategy": "momentum",  "lookback": 10,  "tp_pct": 0.15, "sl_pct": 0.00, "max_hold": 48},
    "CFG-USD":  {"strategy": "momentum",  "lookback": 50,  "tp_pct": 0.15, "sl_pct": 0.00, "max_hold": 48},
}

# Empirical spreads as % of mid (from Coinbase order book snapshots)
SPREAD_PCT = {
    "NOM-USD":  0.002,   # 0.2%
    "GHST-USD": 0.003,   # 0.3% (thinner)
    "SUP-USD":  0.002,   # 0.2%
    "A8-USD":   0.0015,  # 0.15%
    "CFG-USD":  0.0015,  # 0.15%
}

# Per-coin top-6 profitable hours (from session_hour_consolidation.py)
PER_COIN_HOURS = {
    "NOM-USD":  {1, 4, 5, 8, 10, 11},
    "GHST-USD": {2, 3, 4, 5, 7, 18},
    "SUP-USD":  {5, 15, 16, 18, 20, 23},
    "A8-USD":   {7, 11, 15, 17, 22, 23},
    "CFG-USD":  {1, 4, 8, 10, 13, 20},
}

FEE_RATE = 0.004
STARTING_CASH = 100.0
DEPLOY_FRACTION = 0.90


# ===================================================================
# Data fetching
# ===================================================================

def fetch_candles(client, coin: str, days: int) -> list[dict]:
    """Fetch 5-min candles from Coinbase. Try advanced trade API, fall back to exchange public."""
    end = int(time.time())
    start = end - days * 86400
    chunk_sec = 300 * 5 * 60  # ~25h
    all_candles = []
    cs = start
    while cs < end:
        ce = min(cs + chunk_sec, end)
        try:
            resp = client.market_candles(coin, start=cs, end=ce, granularity="FIVE_MINUTE")
            cands = resp.get("candles", [])
            if not cands:
                break
            all_candles.extend(cands)
            cs = ce
            time.sleep(0.15)
        except Exception as e:
            print(f"  WARN fetch error for {coin} at {cs}: {e}")
            cs = ce
            time.sleep(0.5)
    all_candles.sort(key=lambda c: int(c.get("start", c.get("time", 0))))
    return all_candles


# ===================================================================
# Signal generators
# ===================================================================

def signal_fibonacci(candles_hist, closes, fib_lookback):
    """Fibonacci breakout signal -- matches multi_coin_isolated_runner.py logic."""
    if len(candles_hist) < fib_lookback + 5:
        return False
    recent = candles_hist[-fib_lookback:]
    highs = [float(c["high"]) for c in recent]
    lows = [float(c["low"]) for c in recent]
    period_high = max(highs)
    period_low = min(lows)
    fib_price = period_high - (period_high - period_low) * 0.618
    current = float(candles_hist[-1]["close"])
    breakout_pct = (current - fib_price) / fib_price if fib_price > 0 else 0
    if breakout_pct < 0.02:  # min breakout threshold
        return False
    # Volume confirmation
    if len(candles_hist) >= 20:
        volumes = [float(c.get("volume", 0)) for c in candles_hist[-20:]]
        avg_vol = sum(volumes) / len(volumes) if volumes else 0
        cur_vol = float(candles_hist[-1].get("volume", 0))
        if avg_vol > 0 and cur_vol < avg_vol * 0.8:
            return False
    # Momentum: 2 of last 3 green
    if len(candles_hist) >= 3:
        green = sum(1 for c in candles_hist[-3:] if float(c["close"]) > float(c["open"]))
        if green < 2:
            return False
    return True


def signal_momentum(candles_hist, lookback):
    """Momentum breakout -- price breaks above recent high."""
    if len(candles_hist) < lookback + 2:
        return False
    recent = candles_hist[-(lookback):-1]
    if not recent:
        return False
    recent_high = max(float(c["high"]) for c in recent)
    return float(candles_hist[-1]["high"]) > recent_high


# ===================================================================
# Backtest engine
# ===================================================================

def compute_atr(candles_hist, period):
    if len(candles_hist) < period + 1:
        return 0.0
    trs = []
    for i in range(max(1, len(candles_hist) - period), len(candles_hist)):
        c = candles_hist[i]
        cp = candles_hist[i - 1]
        trs.append(max(
            float(c["high"]) - float(c["low"]),
            abs(float(c["high"]) - float(cp["close"])),
            abs(float(c["low"]) - float(cp["close"])),
        ))
    return sum(trs) / len(trs) if trs else 0.0


def run_backtest(candles, cfg, *, mode, spread_pct, top_hours=None, seed=42):
    """Run backtest in one fidelity mode.

    Returns dict with: pnl, trades, wins, losses, win_rate, max_dd,
                       total_spread_cost, total_slippage_cost, total_fees,
                       same_bar_blocked, signals, per_hour_pnl
    """
    import random
    rng = random.Random(seed)

    cash = STARTING_CASH
    pos = None
    trades = []
    equity_curve = [cash]
    peak_eq = cash
    max_dd = 0.0
    wins = 0
    losses = 0
    signals = 0
    same_bar_blocked = 0
    total_spread_cost = 0.0
    total_slippage_cost = 0.0
    total_fees = 0.0
    per_hour_pnl = {}

    strat = cfg["strategy"]
    lookback = cfg.get("fib_lookback", cfg.get("lookback", 20))
    tp_pct = cfg["tp_pct"]
    sl_pct = cfg["sl_pct"]
    max_hold = cfg["max_hold"]

    closes_hist = []
    candles_hist = []

    for i in range(len(candles)):
        c = candles[i]
        close = float(c["close"])
        high = float(c["high"])
        low = float(c["low"])
        open_px = float(c["open"])
        if open_px <= 0 or close <= 0 or high <= 0 or low <= 0:
            continue

        ts = int(c.get("start", c.get("time", 0)))
        hour = datetime.fromtimestamp(ts, tz=timezone.utc).hour

        closes_hist.append(close)
        candles_hist.append(dict(c))
        if len(closes_hist) > 500:
            closes_hist = closes_hist[-500:]
            candles_hist = candles_hist[-500:]

        # Session gate
        if top_hours is not None:
            session_open = hour in top_hours
        else:
            session_open = True  # all hours

        # ---- EXIT ----
        if pos is not None:
            pos["hold"] += 1
            exit_px = None

            if high >= pos["tp"]:
                exit_px = pos["tp"]
            elif sl_pct > 0 and low <= pos["sl"]:
                exit_px = pos["sl"]
            elif pos["hold"] >= max_hold:
                exit_px = close

            if exit_px is not None:
                # no_same_bar mode
                if mode == "no_same_bar" and pos["entry_bar"] == i:
                    same_bar_blocked += 1
                    pos["hold"] -= 1
                    continue

                effective_exit = exit_px

                # Spread-adjusted: exit fills one spread worse
                if mode == "spread_adjusted":
                    spread_ded = effective_exit * spread_pct
                    effective_exit -= spread_ded
                    total_spread_cost += spread_ded * pos["units"]

                # Slippage-adjusted: ATR-based slippage
                if mode == "slippage_adjusted":
                    atr_val = compute_atr(candles_hist, 14)
                    slip_px = max(atr_val * 0.1, effective_exit * 0.001)
                    effective_exit -= slip_px
                    total_slippage_cost += slip_px * pos["units"]

                units = pos["units"]
                gross = (effective_exit - pos["ep"]) * units
                exit_fee = effective_exit * units * FEE_RATE
                net = gross - pos["entry_fee"] - exit_fee
                total_fees += pos["entry_fee"] + exit_fee

                cash += pos["q"] + net
                trades.append(net)
                if net > 0:
                    wins += 1
                else:
                    losses += 0
                    losses = losses  # just track
                losses = wins + losses  # recalc properly

                # Track per-hour PnL (by entry hour)
                eh = pos.get("entry_hour", hour)
                if eh not in per_hour_pnl:
                    per_hour_pnl[eh] = []
                per_hour_pnl[eh].append(net)

                pos = None

        # ---- ENTRY ----
        if pos is None and session_open:
            signal = False
            if strat == "fibonacci":
                signal = signal_fibonacci(candles_hist, closes_hist, lookback)
            elif strat == "momentum":
                signal = signal_momentum(candles_hist, lookback)

            if signal:
                signals += 1
                # Deterministic fill probability
                if rng.random() < 0.9:
                    effective_entry = open_px
                    entry_fee = STARTING_CASH * DEPLOY_FRACTION * FEE_RATE

                    if mode == "spread_adjusted":
                        effective_entry += effective_entry * spread_pct
                        total_spread_cost += effective_entry * spread_pct * (STARTING_CASH * DEPLOY_FRACTION / effective_entry)

                    if mode == "slippage_adjusted":
                        atr_val = compute_atr(candles_hist, 14)
                        slip_px = max(atr_val * 0.1, effective_entry * 0.001)
                        effective_entry += slip_px
                        total_slippage_cost += slip_px * (STARTING_CASH * DEPLOY_FRACTION / effective_entry)

                    deploy = cash * DEPLOY_FRACTION
                    entry_fee = deploy * FEE_RATE
                    units = (deploy - entry_fee) / effective_entry if effective_entry > 0 else 0
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
                        "entry_fee": entry_fee,
                        "entry_bar": i,
                        "entry_hour": hour,
                    }

        # Equity
        if pos is not None:
            floating = (close - pos["ep"]) * pos["units"]
            equity_curve.append(cash + pos["q"] + floating)
        else:
            equity_curve.append(cash)

        eq = equity_curve[-1]
        if eq > peak_eq:
            peak_eq = eq
        dd = (peak_eq - eq) / peak_eq if peak_eq > 0 else 0.0
        if dd > max_dd:
            max_dd = dd

    # Close remaining
    if pos is not None:
        last_close = float(candles[-1]["close"])
        effective_exit = last_close
        if mode == "spread_adjusted":
            effective_exit -= effective_exit * spread_pct
            total_spread_cost += effective_exit * spread_pct * pos["units"]
        if mode == "slippage_adjusted":
            atr_val = compute_atr(candles_hist, 14)
            slip_px = max(atr_val * 0.1, effective_exit * 0.001)
            effective_exit -= slip_px
            total_slippage_cost += slip_px * pos["units"]
        gross = (effective_exit - pos["ep"]) * pos["units"]
        exit_fee = effective_exit * pos["units"] * FEE_RATE
        net = gross - pos["entry_fee"] - exit_fee
        total_fees += pos["entry_fee"] + exit_fee
        cash += pos["q"] + net
        trades.append(net)
        eh = pos.get("entry_hour", 0)
        if eh not in per_hour_pnl:
            per_hour_pnl[eh] = []
        per_hour_pnl[eh].append(net)
        if net > 0:
            wins += 1

    total_trades = len(trades)
    total_wins = sum(1 for t in trades if t > 0)
    total_losses = total_trades - total_wins
    pnl = cash - STARTING_CASH
    avg_pnl = pnl / total_trades if total_trades > 0 else 0

    # Per-hour summary
    hour_summary = {}
    for h, pnls in per_hour_pnl.items():
        hour_summary[h] = {
            "trades": len(pnls),
            "total_pnl": round(sum(pnls), 4),
            "avg_pnl": round(sum(pnls) / len(pnls), 4) if pnls else 0,
        }

    return {
        "mode": mode,
        "pnl": round(pnl, 4),
        "total_trades": total_trades,
        "wins": total_wins,
        "losses": total_losses,
        "win_rate": round(total_wins / max(1, total_trades) * 100, 1),
        "avg_pnl_per_trade": round(avg_pnl, 4),
        "max_drawdown_pct": round(max_dd * 100, 2),
        "signals": signals,
        "total_spread_cost": round(total_spread_cost, 4),
        "total_slippage_cost": round(total_slippage_cost, 4),
        "total_fees": round(total_fees, 4),
        "same_bar_blocked": same_bar_blocked,
        "hourly_summary": {str(k): v for k, v in sorted(hour_summary.items())},
    }


# ===================================================================
# Main
# ===================================================================

def main():
    import argparse
    parser = argparse.ArgumentParser(description="Fibonacci/Momentum Fidelity Audit")
    parser.add_argument("--coins", nargs="+", default=list(STRATEGIES.keys()),
                        help="Coins to audit (default: all 5)")
    parser.add_argument("--days", type=int, default=30, help="Days of data (default: 30)")
    parser.add_argument("--spread-multiplier", type=float, default=1.0,
                        help="Multiply default spreads (default: 1.0)")
    parser.add_argument("--no-fetch", action="store_true",
                        help="Skip live fetch; use cached data if available")
    args = parser.parse_args()

    print("=" * 70)
    print("  Fibonacci / Momentum Fidelity Audit")
    print("=" * 70)
    print(f"  Coins: {', '.join(args.coins)}")
    print(f"  Days: {args.days}")
    print(f"  Spread multiplier: {args.spread_multiplier}x")
    print()

    # Initialize client
    client = CoinbaseAdvancedClient()
    has_api = client.has_auth()

    all_results = {}

    for coin in args.coins:
        cfg = STRATEGIES[coin]
        spread = SPREAD_PCT.get(coin, 0.002) * args.spread_multiplier
        top_hours = PER_COIN_HOURS.get(coin)

        print(f"\n{'=' * 70}")
        print(f"  {coin} -- {cfg['strategy']} (spread={spread*100:.2f}%)")
        print(f"  Top hours: {sorted(top_hours) if top_hours else 'ALL'}")
        print(f"{'=' * 70}")

        # Fetch candles
        candles = []
        if not args.no_fetch and has_api:
            print(f"  Fetching {args.days}d candles from Coinbase...", flush=True)
            candles = fetch_candles(client, coin, args.days)
            print(f"  Got {len(candles)} candles.")
        else:
            # Try cached data
            cache_file = ROOT / "data" / "candle_cache" / f"{coin.replace('-', '_')}_{args.days}d.json"
            if cache_file.exists():
                data = json.loads(cache_file.read_text())
                candles = data.get("candles", [])
                print(f"  Loaded {len(candles)} candles from cache.")
            elif not has_api:
                print(f"  [WARN] No Coinbase auth and no cached data for {coin}. Skipping.")
                all_results[coin] = {"error": "no_data"}
                continue

        if len(candles) < 200:
            print(f"  [WARN] Insufficient candles ({len(candles)} < 200). Results may be unreliable.")
            if len(candles) < 50:
                all_results[coin] = {"error": f"insufficient_data: {len(candles)} candles"}
                continue

        # Run all 4 fidelity modes
        modes = ["naive", "spread_adjusted", "slippage_adjusted", "no_same_bar"]
        coin_results = {}

        for mode in modes:
            print(f"\n  [{mode}]", flush=True)
            result = run_backtest(candles, cfg, mode=mode, spread_pct=spread, top_hours=top_hours)
            coin_results[mode] = result
            print(f"    PnL: ${result['pnl']:+.2f} | Trades: {result['total_trades']} | "
                  f"WR: {result['win_rate']}% | MaxDD: {result['max_drawdown_pct']}%")
            if result['total_spread_cost'] > 0:
                print(f"    Spread cost: ${result['total_spread_cost']:.2f}")
            if result['total_slippage_cost'] > 0:
                print(f"    Slippage cost: ${result['total_slippage_cost']:.2f}")

        # Also run naive all-hours for comparison
        print(f"\n  [naive, ALL-HOURS]", flush=True)
        all_hours_result = run_backtest(candles, cfg, mode="naive", spread_pct=spread, top_hours=None)
        coin_results["naive_all_hours"] = all_hours_result
        print(f"    PnL: ${all_hours_result['pnl']:+.2f} | Trades: {all_hours_result['total_trades']} | "
              f"WR: {all_hours_result['win_rate']}%")

        all_results[coin] = coin_results

        # Key finding
        naive_top = coin_results["naive"]["pnl"]
        spread_top = coin_results["spread_adjusted"]["pnl"]
        slipp_top = coin_results["slippage_adjusted"]["pnl"]
        naive_all = all_hours_result["pnl"]

        print(f"\n  --- KEY FINDINGS ---")
        print(f"  Naive all-hours:     ${naive_all:+.2f}")
        print(f"  Naive top-hours:     ${naive_top:+.2f}")
        print(f"  Spread top-hours:    ${spread_top:+.2f}  (delta: ${spread_top - naive_top:+.2f})")
        print(f"  Slippage top-hours:  ${slipp_top:+.2f}  (delta: ${slipp_top - naive_top:+.2f})")

        if naive_top > 0 and spread_top < 0:
            print(f"  >> EDGE ERASED: Top-hours profit {naive_top:+.2f} wiped out by spread cost")
        elif naive_top > 0 and spread_top > 0:
            retention = spread_top / naive_top * 100
            print(f"  >> EDGE SURVIVES: {retention:.0f}% of naive PnL retained after spread")
        elif naive_all < 0 and naive_top > 0:
            print(f"  >> HOUR FILTER CREATES EDGE: all-hours {naive_all:+.2f} -> top-hours {naive_top:+.2f}")

    # Summary table
    print(f"\n\n{'=' * 70}")
    print(f"  SUMMARY TABLE")
    print(f"{'=' * 70}")
    print(f"  {'Coin':<10} {'Mode':<20} {'PnL':>10} {'Trades':>7} {'WR':>7} {'Spread$':>9} {'Slippage$':>10}")
    print(f"  {'-' * 75}")
    for coin, results in all_results.items():
        if isinstance(results, dict) and "error" in results:
            print(f"  {coin:<10} [ERROR: {results['error']}]")
            continue
        for mode in ["naive", "spread_adjusted", "slippage_adjusted", "no_same_bar"]:
            r = results.get(mode, {})
            if not r:
                continue
            print(f"  {coin:<10} {mode:<20} ${r['pnl']:>8.2f} {r['total_trades']:>7} {r['win_rate']:>6.1f}% "
                  f"${r['total_spread_cost']:>7.2f} ${r['total_slippage_cost']:>8.2f}")
        # All-hours naive
        r_all = results.get("naive_all_hours", {})
        if r_all:
            print(f"  {coin:<10} {'naive_all_hours':<20} ${r_all['pnl']:>8.2f} {r_all['total_trades']:>7} "
                  f"{r_all['win_rate']:>6.1f}% ${'0':>7} ${'0':>8}")

    # Save results
    output = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "days": args.days,
        "spread_multiplier": args.spread_multiplier,
        "results": all_results,
    }
    out_path = REPORTS / "fibonacci_momentum_fidelity_audit.json"
    out_path.write_text(json.dumps(output, indent=2), encoding="utf-8")
    print(f"\n  Results saved to: {out_path}")


if __name__ == "__main__":
    main()
