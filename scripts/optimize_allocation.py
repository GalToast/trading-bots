#!/usr/bin/env python3
"""
Allocation Optimizer — Per-coin cash allocation for Coinbase isolated runner.
=============================================================================

Problem: The runner currently splits total cash equally across all coins
($48 / 9 = $5.33 each). But coins have dramatically different PnL profiles.
NOM earns ~$2,019/mo at $100 while CFG earns ~$24/mo. Capital should flow
to the strongest earners.

What it does:
1. Loads 30-day candle data for all 9 coins from reports/candle_cache/.
2. Backtests each coin at multiple cash levels ($2..$100) using the live
   runner's strategy assignments and parameters.
3. Builds a PnL-per-dollar curve for each coin (linear interpolation).
4. Runs a grid-search allocation optimizer:
   - Constraint: total allocation = total_budget (default $48)
   - Constraint: each coin >= min_allocation (default $2)
   - Objective: maximize total portfolio monthly PnL
5. Compares optimized vs equal-split allocation.

Usage:
    python scripts/optimize_allocation.py
    python scripts/optimize_allocation.py --total-budget 48
    python scripts/optimize_allocation.py --total-budget 100 --min-allocation 5
    python scripts/optimize_allocation.py --grid-step 5  # coarser grid for speed

Output:
    - Terminal table: equal split vs optimized allocation
    - reports/allocation_optimizer.json: full results with per-coin curves
"""
import json
import math
import sys
from datetime import datetime, timezone
from itertools import product as iter_product
from pathlib import Path

# ---------------------------------------------------------------------------
# Constants (mirror the live runner)
# ---------------------------------------------------------------------------
FEE_RATE = 0.004
MIN_CASH = 2.0
DEPLOY_FRACTION = 0.90
SESSION_DEAD = {0, 6, 12, 19}
CANONICAL_MIN_CASH = 10.0
CANONICAL_DEPLOY_FRACTION = 0.95

# Coins and their live-runner strategy assignments
# Strategy params come from DEFAULT_COIN_CONFIGS in multi_coin_isolated_runner.py
COIN_STRATEGIES = {
    "NOM-USD":  {"strategy": "fibonacci_breakout", "params": {"fib_lookback": 20}},
    "GHST-USD": {"strategy": "fibonacci_breakout", "params": {"fib_lookback": 10}},
    "SUP-USD":  {"strategy": "fibonacci_breakout", "params": {"fib_lookback": 20}},
    "RAVE-USD": {"strategy": "supertrend", "params": {}},
    "TRU-USD":  {"strategy": "supertrend", "params": {}},
    "BAL-USD":  {"strategy": "supertrend", "params": {}},
    "IOTX-USD": {"strategy": "supertrend", "params": {}},
    "A8-USD":   {"strategy": "momentum", "params": {}},
    "CFG-USD":  {"strategy": "momentum", "params": {}},
}

# TP/SL/max_hold from the runner's DEFAULT_COIN_CONFIGS
STRATEGY_TRADE_PARAMS = {
    "fibonacci_breakout": {"tp_pct": 0.08, "sl_pct": 0.03, "max_hold": 24},
    "supertrend": {"tp_pct": 0.10, "sl_pct": 0.03, "max_hold": 48},
    "momentum": {"tp_pct": 0.15, "sl_pct": 0.0, "max_hold": 48},
}

# Cash levels to backtest at (covers the full range from min to meaningful)
CASH_LEVELS = [2.0, 3.0, 5.33, 10.0, 20.0, 30.0, 50.0, 100.0]

# Cache file naming: COIN_USD_FIVE_MINUTE_30d.json
CACHE_DIR = Path(__file__).resolve().parent.parent / "reports" / "candle_cache"
OUTPUT_PATH = Path(__file__).resolve().parent.parent / "reports" / "allocation_optimizer.json"

ALL_COINS = list(COIN_STRATEGIES.keys())


def native_assumptions_payload() -> dict:
    return {
        "fee_rate": FEE_RATE,
        "min_cash": MIN_CASH,
        "deploy_fraction": DEPLOY_FRACTION,
        "session_gate": True,
        "session_dead_hours_utc": sorted(SESSION_DEAD),
        "entry": "candle_open",
        "fills": "100%",
        "slippage_bps": 0.0,
    }


def report_semantics_payload() -> dict:
    return {
        "surface_kind": "native_gated_simulator",
        "comparable_to_canonical_without_reconciliation": False,
        "intended_use": "relative ranking inside the native Coinbase isolated-runner assumptions",
        "warning": "Do not compare these totals directly to canonical portfolio truth without using the reconciliation artifacts.",
    }


def comparison_artifacts_payload() -> dict:
    return {
        "canonical_reconciliation_report": "reports/allocation_optimizer_reconciliation.json",
        "drift_board": "reports/adaptive_optimizer_reconciliation_board.json",
    }


def canonical_assumptions_payload() -> dict:
    return {
        "fee_rate": FEE_RATE,
        "min_cash": CANONICAL_MIN_CASH,
        "deploy_fraction": CANONICAL_DEPLOY_FRACTION,
        "session_gate": False,
        "session_dead_hours_utc": sorted(SESSION_DEAD),
        "entry": "candle_open",
        "fills": "100%",
        "slippage_bps": 0.0,
    }


def canonical_reference_payload(plan_summaries: list[dict]) -> dict:
    return {
        "available": True,
        "status": "reconciled_divergent",
        "source_mode": "native_inline_replay",
        "assumptions": canonical_assumptions_payload(),
        "plans": plan_summaries,
    }


# ---------------------------------------------------------------------------
# Candle loading
# ---------------------------------------------------------------------------
def load_candles(coin: str) -> list[dict]:
    """Load 5-minute candles from the cache for a given coin symbol."""
    # Convert "NOM-USD" -> "NOM_USD_FIVE_MINUTE_30d.json"
    safe = coin.replace("-", "_")
    cache_file = CACHE_DIR / f"{safe}_FIVE_MINUTE_30d.json"
    if not cache_file.exists():
        print(f"  [WARN] Cache file not found: {cache_file}", flush=True)
        return []
    data = json.loads(cache_file.read_text(encoding="utf-8"))
    candles = data.get("candles", [])
    # Normalize: ensure each candle has "time" key (some files use "start")
    for c in candles:
        if "time" not in c and "start" in c:
            c["time"] = int(c["start"])
        elif "time" in c:
            c["time"] = int(c["time"])
    return candles


# ---------------------------------------------------------------------------
# Entry signal functions (simplified versions from the live runner)
# ---------------------------------------------------------------------------
def _fibonacci_entry(candle_history, closes, params):
    """Fibonacci breakout with volume + momentum confirmation (from runner)."""
    lookback = params.get("fib_lookback", 20)
    if len(candle_history) < lookback + 5:
        return False

    recent = candle_history[-lookback:]
    highs = [float(c["high"]) for c in recent]
    lows = [float(c["low"]) for c in recent]
    period_high = max(highs)
    period_low = min(lows)

    fib_price = period_high - (period_high - period_low) * 0.618
    current = float(candle_history[-1]["close"])
    breakout_pct = (current - fib_price) / fib_price if fib_price > 0 else 0

    # Minimum breakout threshold (2%)
    if breakout_pct < 0.02:
        return False

    # Volume confirmation: current candle volume > 80% of 20-period avg
    if len(candle_history) >= 20:
        volumes = [float(c.get("volume", 0)) for c in candle_history[-20:]]
        avg_volume = sum(volumes) / len(volumes) if volumes else 0
        current_volume = float(candle_history[-1].get("volume", 0))
        if avg_volume > 0 and current_volume < avg_volume * 0.8:
            return False

    # Momentum: at least 2 of last 3 candles must be green
    if len(candle_history) >= 3:
        recent_candles = candle_history[-3:]
        green_count = sum(1 for c in recent_candles if float(c["close"]) > float(c["open"]))
        if green_count < 2:
            return False

    return True


def _supertrend_entry(candle_history, params):
    """Supertrend with 200-EMA regime filter (from runner)."""
    atr_period = 10
    atr_mult = 3.0
    if len(candle_history) < atr_period + 5:
        return False

    trs = []
    for i in range(1, len(candle_history)):
        c = candle_history[i]
        cp = candle_history[i - 1]
        tr = max(
            float(c["high"]) - float(c["low"]),
            abs(float(c["high"]) - float(cp["close"])),
            abs(float(c["low"]) - float(cp["close"])),
        )
        trs.append(tr)

    if len(trs) < atr_period:
        return False

    atr = sum(trs[-atr_period:]) / atr_period
    last = candle_history[-1]
    hl2 = (float(last["high"]) + float(last["low"])) / 2
    lower = hl2 - atr_mult * atr

    is_uptrend = float(last["close"]) > lower

    # 200 EMA regime filter
    if is_uptrend and len(candle_history) >= 200:
        closes_200 = [float(c["close"]) for c in candle_history[-200:]]
        ema_200 = sum(closes_200) / 200
        if float(last["close"]) < ema_200:
            return False

    return is_uptrend


def _momentum_entry(candle_history, params):
    """Momentum: current high exceeds lookback-period highest high."""
    lookback = 10  # A8 uses 10; CFG uses 50 but we simplify for speed
    if len(candle_history) < lookback + 2:
        return False
    current_high = float(candle_history[-1]["high"])
    highest = max(float(c["high"]) for c in candle_history[-(lookback + 1):-1])
    return current_high > highest


# ---------------------------------------------------------------------------
# Simulation engine (mirrors CoinLedger.process_candles in backtest mode)
# ---------------------------------------------------------------------------
def simulate(
    candles,
    strategy_name,
    strategy_params,
    starting_cash,
    *,
    min_cash=MIN_CASH,
    deploy_fraction=DEPLOY_FRACTION,
    session_gate=True,
):
    """Run a backtest simulation for a single coin at a given cash level.

    Returns a dict with net_pnl, trades, win_rate, signals, total_fees.
    """
    from datetime import datetime as dt, timezone as tz

    trade_params = STRATEGY_TRADE_PARAMS[strategy_name]
    tp_pct = trade_params["tp_pct"]
    sl_pct = trade_params["sl_pct"]
    max_hold = trade_params["max_hold"]

    cash = starting_cash
    position = None
    candle_history = []
    closes = []
    signals = 0
    trades = 0
    wins = 0
    losses = 0
    total_fees = 0.0

    for candle in candles:
        ts = int(candle.get("time", candle.get("start", 0)))
        close = float(candle["close"])
        high = float(candle["high"])
        low = float(candle["low"])
        open_price = float(candle["open"])

        if open_price <= 0 or close <= 0:
            continue

        closes.append(close)
        candle_history.append(candle)
        if len(candle_history) > 500:
            candle_history = candle_history[-500:]
            closes = closes[-500:]

        hour = dt.fromtimestamp(ts, tz=tz.utc).hour
        session_open = hour not in SESSION_DEAD if session_gate else True

        # EXIT logic
        if position:
            position["hold"] += 1
            exit_price = None
            exit_reason = None

            if high >= position["tp"]:
                exit_price = position["tp"]
                exit_reason = "tp"
            elif position["sl"] > 0 and low <= position["sl"]:
                exit_price = position["sl"]
                exit_reason = "sl"
            elif position["hold"] >= max_hold:
                exit_price = close
                exit_reason = "timeout"

            if exit_price is not None:
                units = position["units"]
                gross = (exit_price - position["ep"]) * units
                net = gross - position["entry_fee"] - (exit_price * units * FEE_RATE)
                cash += position["deploy"] + net
                trades += 1
                if net > 0:
                    wins += 1
                else:
                    losses += 1
                total_fees += position["entry_fee"] + exit_price * units * FEE_RATE
                position = None

        # ENTRY logic
        if position is None and cash >= min_cash and session_open:
            signal = False
            if strategy_name == "fibonacci_breakout":
                signal = _fibonacci_entry(candle_history, closes, strategy_params)
            elif strategy_name == "supertrend":
                signal = _supertrend_entry(candle_history, strategy_params)
            elif strategy_name == "momentum":
                signal = _momentum_entry(candle_history, strategy_params)

            if signal:
                signals += 1
                deploy = cash * deploy_fraction
                entry_price = open_price
                if entry_price <= 0:
                    continue

                entry_fee = deploy * FEE_RATE
                units = (deploy - entry_fee) / entry_price
                tp = entry_price * (1 + tp_pct)
                sl = entry_price * (1 - sl_pct) if sl_pct > 0 else 0

                cash -= deploy
                position = {
                    "ep": entry_price,
                    "deploy": deploy,
                    "units": units,
                    "tp": tp,
                    "sl": sl,
                    "hold": 0,
                    "entry_fee": entry_fee,
                }

    # Close remaining position at last close
    if position:
        last_close = float(candles[-1]["close"])
        gross = (last_close - position["ep"]) * position["units"]
        net = gross - position["entry_fee"] - (last_close * position["units"] * FEE_RATE)
        cash += position["deploy"] + net
        trades += 1
        total_fees += position["entry_fee"] + last_close * position["units"] * FEE_RATE
        if net > 0:
            wins += 1
        else:
            losses += 1

    pnl = cash - starting_cash
    win_rate = wins / max(1, trades) * 100 if trades > 0 else 0

    return {
        "net_pnl": round(pnl, 4),
        "win_rate": round(win_rate, 1),
        "trades": trades,
        "wins": wins,
        "losses": losses,
        "signals": signals,
        "total_fees": round(total_fees, 4),
    }


# ---------------------------------------------------------------------------
# PnL-per-dollar curve (interpolation between tested cash levels)
# ---------------------------------------------------------------------------
def build_pnl_curve(cash_levels, pnl_values):
    """Build an interpolator from (cash_level -> pnl) data.

    Returns a function that estimates PnL for any cash amount within range.
    Uses piecewise linear interpolation with clamping at boundaries.
    """
    if not cash_levels or not pnl_values:
        return lambda x: 0.0

    points = sorted(zip(cash_levels, pnl_values))

    def interpolate(cash_amount):
        # Clamp to range
        if cash_amount <= points[0][0]:
            return points[0][1] * (cash_amount / points[0][0]) if points[0][0] > 0 else 0.0
        if cash_amount >= points[-1][0]:
            # Extrapolate from last two points (or use last point's slope)
            if len(points) >= 2:
                slope = (points[-1][1] - points[-2][1]) / (points[-1][0] - points[-2][0])
                return points[-1][1] + slope * (cash_amount - points[-1][0])
            return points[-1][1] * (cash_amount / points[-1][0]) if points[-1][0] > 0 else 0.0

        # Find bracketing points
        for i in range(len(points) - 1):
            if points[i][0] <= cash_amount <= points[i + 1][0]:
                x0, y0 = points[i]
                x1, y1 = points[i + 1]
                if x1 == x0:
                    return y0
                t = (cash_amount - x0) / (x1 - x0)
                return y0 + t * (y1 - y0)
        return points[-1][1]

    return interpolate


# ---------------------------------------------------------------------------
# Grid-search allocation optimizer
# ---------------------------------------------------------------------------
def optimize_allocation(pnl_curves, total_budget, min_alloc, grid_step=2.0):
    """Find the allocation that maximizes total portfolio PnL.

    Uses a two-phase approach:
    1. Identify coins that are profitable at the minimum allocation.
       Unprofitable coins get exactly min_alloc (we can't go below it).
    2. Greedily allocate remaining budget to coins with highest marginal PnL.

    Args:
        pnl_curves: dict of coin -> interpolated PnL function
        total_budget: total cash to allocate (e.g., $48)
        min_alloc: minimum per-coin allocation (e.g., $2)
        grid_step: granularity of the grid search (e.g., $2)

    Returns:
        dict with optimized allocation, per-coin PnL, total PnL.
    """
    coins = list(pnl_curves.keys())
    n = len(coins)

    # Phase 1: Give everyone the minimum, check profitability
    allocation = {coin: min_alloc for coin in coins}
    remaining = total_budget - n * min_alloc

    if remaining < 0:
        # Budget too small to give everyone the minimum
        # Allocate to most profitable coins first
        coin_pnl = [(coin, pnl_curves[coin](min_alloc)) for coin in coins]
        coin_pnl.sort(key=lambda x: x[1], reverse=True)
        allocation = {}
        budget_left = total_budget
        for coin, pnl in coin_pnl:
            if budget_left >= min_alloc:
                allocation[coin] = min_alloc
                budget_left -= min_alloc
            else:
                allocation[coin] = 0
        # Redistribute leftover to top earner
        if budget_left > 0.01 and allocation:
            top = max(allocation, key=lambda c: allocation[c])
            allocation[top] += budget_left
        return _compute_allocation_pnl(pnl_curves, allocation)

    if remaining <= 0.01:
        return _compute_allocation_pnl(pnl_curves, allocation)

    # Phase 2: Greedy — allocate in grid_step increments to the coin
    # with the highest marginal PnL per additional dollar
    step = grid_step
    while remaining >= step:
        best_coin = None
        best_marginal_pnl = -1e9

        for coin in coins:
            current = allocation[coin]
            pnl_now = pnl_curves[coin](current)
            pnl_next = pnl_curves[coin](current + step)
            marginal = pnl_next - pnl_now

            if marginal > best_marginal_pnl:
                best_marginal_pnl = marginal
                best_coin = coin

        if best_coin is None:
            break

        allocation[best_coin] += step
        remaining -= step

    # Phase 3: Allocate remaining fractional dollars to top marginal earner
    if remaining > 0.01:
        best_coin = None
        best_marginal_pnl = -1e9
        for coin in coins:
            current = allocation[coin]
            pnl_now = pnl_curves[coin](current)
            pnl_next = pnl_curves[coin](current + remaining)
            marginal = pnl_next - pnl_now
            if marginal > best_marginal_pnl:
                best_marginal_pnl = marginal
                best_coin = coin
        if best_coin:
            allocation[best_coin] = round(allocation[best_coin] + remaining, 2)

    return _compute_allocation_pnl(pnl_curves, allocation)


def _compute_allocation_pnl(pnl_curves, allocation):
    """Compute PnL for a given allocation dict."""
    result = {"allocation": {}, "per_coin_pnl": {}, "total_pnl": 0.0}
    for coin, alloc in allocation.items():
        pnl = pnl_curves[coin](alloc)
        result["allocation"][coin] = round(alloc, 2)
        result["per_coin_pnl"][coin] = round(pnl, 4)
        result["total_pnl"] += pnl
    result["total_pnl"] = round(result["total_pnl"], 4)
    return result


def replay_allocation_plan(candles_by_coin, allocation, *, projected_per_coin_pnl, active_coins, assumptions):
    per_coin = {}
    feasible_count = 0
    projected_total = 0.0
    canonical_total = 0.0

    for coin in active_coins:
        strategy_name = COIN_STRATEGIES[coin]["strategy"]
        strategy_params = COIN_STRATEGIES[coin]["params"]
        cash = float(allocation.get(coin, 0.0) or 0.0)
        projected = float(projected_per_coin_pnl.get(coin, 0.0) or 0.0)
        projected_total += projected

        if cash < float(assumptions["min_cash"]):
            replay = {
                "net_pnl": 0.0,
                "trades": 0,
                "win_rate": None,
                "feasible": False,
            }
        else:
            replay = simulate(
                candles_by_coin[coin],
                strategy_name,
                strategy_params,
                cash,
                min_cash=float(assumptions["min_cash"]),
                deploy_fraction=float(assumptions["deploy_fraction"]),
                session_gate=bool(assumptions["session_gate"]),
            )
            replay["feasible"] = True

        if replay["feasible"]:
            feasible_count += 1

        canonical_total += float(replay["net_pnl"])
        per_coin[coin] = {
            "allocation": round(cash, 2),
            "projected_net_pnl": round(projected, 4),
            "canonical_net_pnl": round(float(replay["net_pnl"]), 4),
            "delta_vs_projected": round(float(replay["net_pnl"]) - projected, 4),
            "feasible": bool(replay["feasible"]),
            "canonical_trades": int(replay["trades"]),
            "canonical_win_rate": replay["win_rate"],
        }

    return {
        "feasible_count": feasible_count,
        "coin_count": len(active_coins),
        "projected_total_pnl": round(projected_total, 4),
        "canonical_total_pnl": round(canonical_total, 4),
        "delta_vs_projected": round(canonical_total - projected_total, 4),
        "per_coin": per_coin,
    }


def proportional_allocation(pnl_curves, total_budget, min_alloc):
    """Allocate proportional to each coin's PnL-per-dollar at $5.33 baseline.

    This is the 'proportional edge' approach — simpler than grid search.
    """
    coins = list(pnl_curves.keys())

    # Compute PnL/dollar at $5.33 baseline
    pnl_per_dollar = {}
    for coin in coins:
        pnl = pnl_curves[coin](5.33)
        pnl_per_dollar[coin] = max(pnl / 5.33, 0) if pnl > 0 else 0

    total_edge = sum(pnl_per_dollar.values())
    if total_edge <= 0:
        # Equal split fallback
        return {coin: round(total_budget / len(coins), 2) for coin in coins}

    allocation = {}
    remaining = total_budget

    # Proportional allocation
    for coin in coins:
        share = (pnl_per_dollar[coin] / total_edge) * total_budget
        allocation[coin] = max(share, min_alloc)
        remaining -= allocation[coin]

    # Redistribute any shortfall proportionally
    if remaining > 0.01:
        # Give remaining to the top earner
        top_coin = max(pnl_per_dollar, key=pnl_per_dollar.get)
        allocation[top_coin] += remaining

    return _compute_allocation_pnl(pnl_curves, allocation)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main():
    import argparse

    parser = argparse.ArgumentParser(
        description="Optimize per-coin cash allocation for the Coinbase isolated runner."
    )
    parser.add_argument("--total-budget", type=float, default=48.0, help="Total cash to allocate (default: $48)")
    parser.add_argument("--min-allocation", type=float, default=2.0, help="Minimum per-coin allocation (default: $2)")
    parser.add_argument("--grid-step", type=float, default=2.0, help="Grid search step size (default: $2)")
    parser.add_argument("--cache-dir", type=str, default=None, help="Override candle cache directory")
    args = parser.parse_args()

    if args.cache_dir:
        global CACHE_DIR, OUTPUT_PATH
        CACHE_DIR = Path(args.cache_dir)
        OUTPUT_PATH = CACHE_DIR.parent / "allocation_optimizer.json"

    total_budget = args.total_budget
    min_alloc = args.min_allocation
    grid_step = args.grid_step

    print("=" * 80, flush=True)
    print("  COINBASE ISOLATED RUNNER — ALLOCATION OPTIMIZER", flush=True)
    print("=" * 80, flush=True)
    print(f"  Total budget:   ${total_budget:.2f}", flush=True)
    print(f"  Min allocation: ${min_alloc:.2f}/coin", flush=True)
    print(f"  Grid step:      ${grid_step:.2f}", flush=True)
    print(f"  Coins:          {len(ALL_COINS)}", flush=True)
    print(f"  Backtest pts:   {len(ALL_COINS)} coins x {len(CASH_LEVELS)} cash levels = {len(ALL_COINS) * len(CASH_LEVELS)}", flush=True)
    print()

    # ---- Load candle data ----
    print("Loading candle data...", flush=True)
    candles_by_coin = {}
    for coin in ALL_COINS:
        candles = load_candles(coin)
        if candles:
            candles_by_coin[coin] = candles
            print(f"  {coin}: {len(candles)} candles", flush=True)
        else:
            print(f"  {coin}: NO DATA (skipping)", flush=True)

    print()
    if not candles_by_coin:
        print("ERROR: No candle data found. Cannot optimize.", flush=True)
        return 1

    active_coins = [c for c in ALL_COINS if c in candles_by_coin]

    # ---- Backtest at multiple cash levels ----
    print("Backtesting at multiple cash levels...", flush=True)
    backtest_results = {}  # coin -> {cash_level -> result}

    for coin in active_coins:
        cfg = COIN_STRATEGIES[coin]
        strategy_name = cfg["strategy"]
        strategy_params = cfg["params"]
        candles = candles_by_coin[coin]

        print(f"\n  {coin} ({strategy_name}):", flush=True)
        backtest_results[coin] = {}

        for cash in CASH_LEVELS:
            r = simulate(candles, strategy_name, strategy_params, cash)
            backtest_results[coin][cash] = r
            pnl_per_dollar = r["net_pnl"] / cash if cash > 0 else 0
            print(
                f"    ${cash:>6.2f}: PnL=${r['net_pnl']:+8.4f}  "
                f"trades={r['trades']:>3}  WR={r['win_rate']:.1f}%  "
                f"PnL/$={pnl_per_dollar:+.4f}",
                flush=True,
            )

    print()

    # ---- Build PnL-per-dollar curves ----
    print("Building PnL-per-dollar curves...", flush=True)
    pnl_curves = {}
    for coin in active_coins:
        levels = sorted(backtest_results[coin].keys())
        pnl_values = [backtest_results[coin][c]["net_pnl"] for c in levels]
        pnl_curves[coin] = build_pnl_curve(levels, pnl_values)

    # ---- Equal-split baseline ----
    equal_alloc = total_budget / len(active_coins)
    equal_results = {}
    equal_total_pnl = 0.0
    for coin in active_coins:
        pnl = pnl_curves[coin](equal_alloc)
        equal_results[coin] = {"allocation": round(equal_alloc, 2), "pnl": round(pnl, 4)}
        equal_total_pnl += pnl

    # ---- Optimized allocation (greedy grid search) ----
    print("Running allocation optimizer (greedy grid search)...", flush=True)
    optimized = optimize_allocation(pnl_curves, total_budget, min_alloc, grid_step)

    # ---- Proportional allocation ----
    print("Computing proportional allocation...", flush=True)
    proportional = proportional_allocation(pnl_curves, total_budget, min_alloc)

    # ---- Print comparison table ----
    print()
    print("=" * 80, flush=True)
    print("  ALLOCATION COMPARISON", flush=True)
    print("=" * 80, flush=True)
    print()

    header = (
        f"  {'Coin':<10} | {'Strategy':<22} | {'Equal $':>7} | "
        f"{'Equal PnL':>9} | {'Opt $':>7} | {'Opt PnL':>9} | "
        f"{'Prop $':>7} | {'Prop PnL':>9}"
    )
    print(header, flush=True)
    print(f"  {'-'*10}-+-{'-'*22}-+-{'-'*7}-+-{'-'*9}-+-{'-'*7}-+-{'-'*9}-+-{'-'*7}-+-{'-'*9}", flush=True)

    for coin in active_coins:
        strategy = COIN_STRATEGIES[coin]["strategy"]
        eq = equal_results[coin]
        opt_alloc = optimized["allocation"].get(coin, 0)
        opt_pnl = optimized["per_coin_pnl"].get(coin, 0)
        prop_alloc = proportional["allocation"].get(coin, 0)
        prop_pnl = proportional["per_coin_pnl"].get(coin, 0)

        print(
            f"  {coin:<10} | {strategy:<22} | ${eq['allocation']:>5.2f} | "
            f"${eq['pnl']:>+7.2f} | ${opt_alloc:>5.2f} | ${opt_pnl:>+7.2f} | "
            f"${prop_alloc:>5.2f} | ${prop_pnl:>+7.2f}",
            flush=True,
        )

    print(f"  {'-'*10}-+-{'-'*22}-+-{'-'*7}-+-{'-'*9}-+-{'-'*7}-+-{'-'*9}-+-{'-'*7}-+-{'-'*9}", flush=True)

    # Totals
    opt_total = optimized["total_pnl"]
    prop_total = proportional["total_pnl"]

    print(
        f"  {'TOTAL':<10} | {'':<22} | {'':>7} | "
        f"${equal_total_pnl:>+7.2f} | {'':>7} | ${opt_total:>+7.2f} | "
        f"{'':>7} | ${prop_total:>+7.2f}",
        flush=True,
    )
    print()

    # ---- Edge multiplier ----
    edge_multiplier = opt_total / equal_total_pnl if equal_total_pnl != 0 else 1.0
    edge_improvement = (opt_total - equal_total_pnl) / abs(equal_total_pnl) * 100 if equal_total_pnl != 0 else 0

    print("=" * 80, flush=True)
    print("  EDGE ANALYSIS", flush=True)
    print("=" * 80, flush=True)
    print(f"  Equal-split total PnL:    ${equal_total_pnl:+.2f}/mo", flush=True)
    print(f"  Optimized total PnL:      ${opt_total:+.2f}/mo", flush=True)
    print(f"  Proportional total PnL:   ${prop_total:+.2f}/mo", flush=True)
    print(f"  Edge multiplier:          {edge_multiplier:.2f}x", flush=True)
    print(f"  Improvement over equal:   {edge_improvement:+.1f}%", flush=True)
    print()

    # ---- Per-coin recommendations ----
    print("=" * 80, flush=True)
    print("  PER-COIN ALLOCATION RECOMMENDATIONS", flush=True)
    print("=" * 80, flush=True)
    print()
    print(f"  {'Coin':<10} | {'Current $':>9} | {'Recommended $':>13} | {'Change':>8} | {'Reason'}", flush=True)
    print(f"  {'-'*10}-+-{'-'*9}-+-{'-'*13}-+-{'-'*8}-+-{'-'*30}", flush=True)

    for coin in active_coins:
        current = round(equal_alloc, 2)
        recommended = optimized["allocation"].get(coin, current)
        change = recommended - current
        reason = ""
        if change > 0.5:
            reason = "Strong earner — increase capital"
        elif change < -0.5:
            reason = "Weak earner — reduce capital"
        else:
            reason = "Near-optimal at current level"

        direction = "+" if change >= 0 else ""
        print(
            f"  {coin:<10} | ${current:>7.2f} | ${recommended:>11.2f} | "
            f"{direction}${change:.2f} | {reason}",
            flush=True,
        )

    print()

    # ---- Save results ----
    canonical_assumptions = canonical_assumptions_payload()
    canonical_equal = replay_allocation_plan(
        candles_by_coin,
        {coin: round(equal_alloc, 2) for coin in active_coins},
        projected_per_coin_pnl={coin: equal_results[coin]["pnl"] for coin in active_coins},
        active_coins=active_coins,
        assumptions=canonical_assumptions,
    )
    canonical_optimized = replay_allocation_plan(
        candles_by_coin,
        optimized["allocation"],
        projected_per_coin_pnl=optimized["per_coin_pnl"],
        active_coins=active_coins,
        assumptions=canonical_assumptions,
    )
    canonical_proportional = replay_allocation_plan(
        candles_by_coin,
        proportional["allocation"],
        projected_per_coin_pnl=proportional["per_coin_pnl"],
        active_coins=active_coins,
        assumptions=canonical_assumptions,
    )

    report = {
        "run_at": datetime.now(timezone.utc).isoformat(),
        "report_semantics": report_semantics_payload(),
        "native_assumptions": native_assumptions_payload(),
        "comparison_artifacts": comparison_artifacts_payload(),
        "canonical_reference": canonical_reference_payload(
            [
                {"plan_name": "equal_split", **canonical_equal},
                {"plan_name": "optimized", **canonical_optimized},
                {"plan_name": "proportional", **canonical_proportional},
            ]
        ),
        "total_budget": total_budget,
        "min_allocation": min_alloc,
        "grid_step": grid_step,
        "active_coins": active_coins,
        "cash_levels_tested": CASH_LEVELS,
        "backtest_results": {
            coin: {
                str(cash): backtest_results[coin][cash]
                for cash in sorted(backtest_results[coin].keys())
            }
            for coin in active_coins
        },
        "equal_split": {
            "per_coin": round(equal_alloc, 2),
            "per_coin_pnl": {coin: equal_results[coin]["pnl"] for coin in active_coins},
            "total_pnl": round(equal_total_pnl, 4),
        },
        "optimized": {
            "allocation": {coin: optimized["allocation"][coin] for coin in active_coins},
            "per_coin_pnl": {coin: optimized["per_coin_pnl"][coin] for coin in active_coins},
            "total_pnl": optimized["total_pnl"],
        },
        "proportional": {
            "allocation": {coin: proportional["allocation"][coin] for coin in active_coins},
            "per_coin_pnl": {coin: proportional["per_coin_pnl"][coin] for coin in active_coins},
            "total_pnl": proportional["total_pnl"],
        },
        "edge_analysis": {
            "multiplier": round(edge_multiplier, 4),
            "improvement_pct": round(edge_improvement, 2),
        },
    }

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2, sort_keys=False)

    print(f"Results saved to: {OUTPUT_PATH}", flush=True)
    print()
    print("Done.", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
