#!/usr/bin/env python3
"""
Cross-Asset Signal Consensus Engine

Builds a consensus matrix from 3 independent strategies across 3 coins,
then backtests consensus-gated entries vs. ungated entries.

Key insight: individual strategies are noisy, but when multiple independent
strategies fire simultaneously, the agreement IS the signal.

Usage:
    python scripts/signal_consensus_engine.py
    python scripts/signal_consensus_engine.py --days 30 --coins NOM-USD RAVE-USD GHST-USD
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

COINS = ["NOM-USD", "RAVE-USD", "GHST-USD"]

# Strategy param defaults
FIB_LOOKBACK_NOM = 20
FIB_LOOKBACK_GHST = 10
FIB_LEVEL = 0.618
FIB_MIN_BREAKOUT_PCT = 0.02
FIB_VOLUME_MULT = 0.8

SUPERTREND_ATR_PERIOD = 10
SUPERTREND_ATR_MULT = 3.0

MOMENTUM_LOOKBACK = 20
MOMENTUM_THRESHOLD_PCT = 0.005  # 0.5%
MOMENTUM_VOLUME_MULT = 0.5

FEE_RATE = 0.004
BACKTEST_STARTING_CASH = 100.0
DEPLOY_PCT = 0.90
DEFAULT_TP_PCT = 0.08
DEFAULT_SL_PCT = 0.03
DEFAULT_MAX_HOLD = 48

RESONANCE_WINDOW = 3  # candles


# ===================================================================
# Data fetching
# ===================================================================

def fetch_candles(client: CoinbaseAdvancedClient, coin: str, days: int) -> list[dict]:
    """Fetch *days* of 5-min candles, chunked to respect rate limits."""
    end = int(time.time())
    start = end - days * 86400
    chunk_sec = 300 * 5 * 60
    all_candles: list[dict] = []
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
            print(f"  WARN fetch error for {coin} at {cs}: {e}", flush=True)
            cs += chunk_sec
    all_candles.sort(key=lambda c: int(c.get("start", c.get("time", 0))))
    return all_candles


# ===================================================================
# Strategy signal generators — pure functions returning bool per candle
# ===================================================================

def _parse_candle(c: dict) -> dict:
    return {
        "start": int(c.get("start", c.get("time", 0))),
        "open": float(c["open"]),
        "high": float(c["high"]),
        "low": float(c["low"]),
        "close": float(c["close"]),
        "volume": float(c.get("volume", 0)),
    }


def fibonacci_signal(candles: list[dict], lookback: int, fib_level: float = FIB_LEVEL,
                     min_breakout_pct: float = FIB_MIN_BREAKOUT_PCT,
                     volume_mult: float = FIB_VOLUME_MULT) -> bool:
    """Fibonacci breakout signal — mirrors multi_coin_isolated_runner logic."""
    if len(candles) < lookback + 5:
        return False

    recent = candles[-lookback:]
    highs = [c["high"] for c in recent]
    lows = [c["low"] for c in recent]
    period_high = max(highs)
    period_low = min(lows)

    fib_price = period_high - (period_high - period_low) * fib_level
    current = candles[-1]["close"]
    breakout_pct = (current - fib_price) / fib_price if fib_price > 0 else 0

    if breakout_pct < min_breakout_pct:
        return False

    # Volume gate
    if len(candles) >= 20:
        volumes = [c["volume"] for c in candles[-20:]]
        avg_volume = sum(volumes) / len(volumes)
        current_volume = candles[-1]["volume"]
        if avg_volume > 0 and current_volume < avg_volume * volume_mult:
            return False

    # Momentum gate: >= 2 of last 3 candles green
    if len(candles) >= 3:
        recent_3 = candles[-3:]
        green_count = sum(1 for c in recent_3 if c["close"] > c["open"])
        if green_count < 2:
            return False

    return True


def supertrend_signal(candles: list[dict], atr_period: int = SUPERTREND_ATR_PERIOD,
                      atr_mult: float = SUPERTREND_ATR_MULT) -> tuple[bool, float | None]:
    """Supertrend signal — mirrors crypto_supertrend_fidelity_audit logic."""
    if len(candles) < atr_period + 5:
        return False, None

    trs = []
    for i in range(1, len(candles)):
        tr = max(
            candles[i]["high"] - candles[i]["low"],
            abs(candles[i]["high"] - candles[i - 1]["close"]),
            abs(candles[i]["low"] - candles[i - 1]["close"]),
        )
        trs.append(tr)

    if len(trs) < atr_period:
        return False, None

    atr = sum(trs[-atr_period:]) / atr_period
    hl2 = (candles[-1]["high"] + candles[-1]["low"]) / 2
    lower = hl2 - atr_mult * atr

    is_uptrend = candles[-1]["close"] > lower
    return is_uptrend, lower


def momentum_breakout_signal(candles: list[dict], lookback: int = MOMENTUM_LOOKBACK,
                              threshold: float = MOMENTUM_THRESHOLD_PCT,
                              volume_mult: float = MOMENTUM_VOLUME_MULT) -> bool:
    """Momentum breakout — lookback-period high breakout with volume confirmation."""
    if len(candles) < lookback + 2:
        return False

    # lookback-bar high (excluding current candle)
    recent = candles[-(lookback + 1):-1]
    period_high = max(c["high"] for c in recent)

    current_close = candles[-1]["close"]
    current_high = candles[-1]["high"]
    breakout = (current_high - period_high) / period_high if period_high > 0 else 0

    if breakout < threshold:
        return False

    # Volume confirmation
    if len(candles) >= 20:
        volumes = [c["volume"] for c in candles[-20:]]
        avg_volume = sum(volumes) / len(volumes)
        current_volume = candles[-1]["volume"]
        if avg_volume > 0 and current_volume < avg_volume * volume_mult:
            return False

    return True


# ===================================================================
# Signal scanning — run all 3 strategies across all candles for one coin
# ===================================================================

def scan_coin_signals(candles: list[dict], coin: str) -> list[dict]:
    """For each candle index, compute which strategies fired.

    Returns a list of dicts:
      { "ts": int, "fib": bool, "st": bool, "mom": bool, "count": int }
    """
    parsed = [_parse_candle(c) for c in candles]
    lookback = FIB_LOOKBACK_GHST if coin == "GHST-USD" else FIB_LOOKBACK_NOM

    results = []
    for i in range(len(parsed)):
        window = parsed[:i + 1]
        fib = fibonacci_signal(window, lookback=lookback)
        st, _ = supertrend_signal(window)
        mom = momentum_breakout_signal(window)
        count = int(fib) + int(st) + int(mom)
        results.append({
            "ts": parsed[i]["start"],
            "close": parsed[i]["close"],
            "fib": fib,
            "st": st,
            "mom": mom,
            "count": count,
        })
    return results


# ===================================================================
# Consensus matrix — for each consensus level, compute subsequent returns
# ===================================================================

def build_consensus_matrix(signals: list[dict], horizons: list[int] = [10, 20, 50]) -> dict:
    """For each consensus level (0-3), compute avg return over forward horizons."""
    matrix = {}
    for level in range(4):
        matrix[str(level)] = {h: {"returns": [], "count": 0} for h in horizons}

    for i, sig in enumerate(signals):
        level = sig["count"]
        key = str(level)
        if key not in matrix:
            continue
        current_price = sig["close"]
        if current_price <= 0:
            continue
        for h in horizons:
            forward_idx = i + h
            if forward_idx < len(signals):
                fwd_price = signals[forward_idx]["close"]
                ret = (fwd_price - current_price) / current_price
                matrix[key][h]["returns"].append(ret)
                matrix[key][h]["count"] += 1

    # Aggregate
    summary = {}
    for level, horizons_data in matrix.items():
        summary[level] = {}
        for h, data in horizons_data.items():
            returns = data["returns"]
            count = data["count"]
            avg_ret = sum(returns) / len(returns) if returns else 0.0
            hit_rate = sum(1 for r in returns if r > 0) / len(returns) * 100 if returns else 0.0
            summary[level][str(h)] = {
                "avg_return_pct": round(avg_ret * 100, 6),
                "hit_rate_pct": round(hit_rate, 2),
                "sample_size": count,
            }
    return summary


# ===================================================================
# Consensus-gated backtest
# ===================================================================

def run_consensus_backtest(signals: list[dict], consensus_gate: int = 0,
                            tp_pct: float = DEFAULT_TP_PCT,
                            sl_pct: float = DEFAULT_SL_PCT,
                            max_hold: int = DEFAULT_MAX_HOLD) -> dict:
    """Backtest fibonacci entries gated by consensus threshold.

    consensus_gate=0: enter whenever fibonacci fires alone (no gate).
    consensus_gate=2: need fibonacci + at least one other strategy.
    consensus_gate=3: need all three strategies to agree.
    """
    cash = BACKTEST_STARTING_CASH
    pos = None
    trades: list[float] = []
    equity_curve = [cash]
    peak_equity = cash
    max_dd = 0.0
    wins = 0
    losses = 0
    entries_blocked_by_gate = 0

    for i, sig in enumerate(signals):
        close = sig["close"]
        ts = sig["ts"]

        # EXIT
        if pos is not None:
            pos["hold"] += 1
            exit_price = None
            exit_reason = None

            if close >= pos["tp"]:
                exit_price = pos["tp"]
                exit_reason = "tp"
            elif sl_pct > 0 and close <= pos["sl"]:
                exit_price = pos["sl"]
                exit_reason = "sl"
            elif pos["hold"] >= max_hold:
                exit_price = close
                exit_reason = "timeout"

            if exit_price is not None:
                units = pos["units"]
                gross = (exit_price - pos["ep"]) * units
                entry_fee = pos["entry_fee"]
                exit_fee = exit_price * units * FEE_RATE
                net = gross - entry_fee - exit_fee

                cash += pos["q"] + net
                trades.append(net)
                if net > 0:
                    wins += 1
                else:
                    losses += 1
                pos = None

        # ENTRY
        if pos is None and sig["fib"]:
            # Consensus gate: count must meet threshold
            if sig["count"] < consensus_gate:
                entries_blocked_by_gate += 1
            else:
                deploy = cash * DEPLOY_PCT
                entry_price = close
                if entry_price <= 0:
                    continue
                entry_fee = deploy * FEE_RATE
                units = (deploy - entry_fee) / entry_price
                tp = entry_price * (1 + tp_pct)
                sl = entry_price * (1 - sl_pct) if sl_pct > 0 else 0

                cash -= deploy
                pos = {
                    "ep": entry_price,
                    "q": deploy,
                    "units": units,
                    "tp": tp,
                    "sl": sl,
                    "hold": 0,
                    "entry_fee": entry_fee,
                }

        # Equity tracking
        if pos is not None:
            floating = (close - pos["ep"]) * pos["units"]
            equity_curve.append(cash + pos["q"] + floating)
        else:
            equity_curve.append(cash)

        eq = equity_curve[-1]
        if eq > peak_equity:
            peak_equity = eq
        dd = (peak_equity - eq) / peak_equity if peak_equity > 0 else 0.0
        if dd > max_dd:
            max_dd = dd

    # Close remaining
    if pos is not None and len(signals) > 0:
        last_close = signals[-1]["close"]
        units = pos["units"]
        gross = (last_close - pos["ep"]) * units
        exit_fee = last_close * units * FEE_RATE
        net = gross - pos["entry_fee"] - exit_fee
        cash += pos["q"] + net
        trades.append(net)
        if net > 0:
            wins += 1
        else:
            losses += 1

    total_trades = len(trades)
    win_rate = (wins / total_trades * 100) if total_trades > 0 else 0.0
    total_pnl = sum(trades)
    avg_pnl = (total_pnl / total_trades) if total_trades > 0 else 0.0

    # Sharpe
    if total_trades > 1:
        mean_ret = total_pnl / total_trades
        std_ret = math.sqrt(sum((t - mean_ret) ** 2 for t in trades) / total_trades)
        sharpe = mean_ret / std_ret if std_ret > 0 else 0.0
    else:
        sharpe = 0.0

    return {
        "consensus_gate": consensus_gate,
        "final_equity": round(cash, 4),
        "total_pnl": round(total_pnl, 4),
        "roi_pct": round((cash - BACKTEST_STARTING_CASH) / BACKTEST_STARTING_CASH * 100, 4),
        "total_trades": total_trades,
        "wins": wins,
        "losses": losses,
        "win_rate_pct": round(win_rate, 2),
        "avg_pnl": round(avg_pnl, 6),
        "sharpe_ratio": round(sharpe, 4),
        "max_drawdown_pct": round(max_dd * 100, 4),
        "entries_blocked_by_gate": entries_blocked_by_gate,
    }


# ===================================================================
# Cross-coin resonance detection
# ===================================================================

def detect_resonance(coin_signals: dict[str, list[dict]], window: int = RESONANCE_WINDOW) -> dict:
    """Detect when 2+ coins have ANY strategy fire in the same candle window.

    Returns:
      - resonance_timestamps: list of ts where resonance occurred
      - isolated_timestamps: list of ts where only 1 coin fired
      - resonance performance vs isolated performance
    """
    # Build a time-indexed map: ts -> {coin: fired_any}
    ts_map: dict[int, dict[str, bool]] = {}
    for coin, signals in coin_signals.items():
        for sig in signals:
            ts = sig["ts"]
            if ts not in ts_map:
                ts_map[ts] = {}
            fired_any = sig["count"] > 0
            ts_map[ts][coin] = fired_any

    # Also check within window (nearby timestamps)
    sorted_ts = sorted(ts_map.keys())
    resonance_entries = []  # (ts, coin_that_fired, is_resonance)

    for ts in sorted_ts:
        coins_at_ts = ts_map[ts]
        coins_firing = [c for c, fired in coins_at_ts.items() if fired]

        # Check nearby timestamps within window
        nearby_coins = set(coins_firing)
        for offset in range(1, window + 1):
            for direction in [-1, 1]:
                neighbor_ts = ts + direction * offset * 300  # 5-min candles
                if neighbor_ts in ts_map:
                    for c, fired in ts_map[neighbor_ts].items():
                        if fired:
                            nearby_coins.add(c)

        is_resonance = len(nearby_coins) >= 2
        for coin in coins_firing:
            resonance_entries.append({
                "ts": ts,
                "coin": coin,
                "is_resonance": is_resonance,
                "active_coins": sorted(nearby_coins),
            })

    return {
        "resonance_entries": resonance_entries,
        "total_entries": len(resonance_entries),
        "resonance_count": sum(1 for e in resonance_entries if e["is_resonance"]),
        "isolated_count": sum(1 for e in resonance_entries if not e["is_resonance"]),
    }


def resonance_backtest_comparison(coin_signals: dict[str, list[dict]],
                                   resonance_data: dict,
                                   window: int = RESONANCE_WINDOW,
                                   tp_pct: float = DEFAULT_TP_PCT,
                                   sl_pct: float = DEFAULT_SL_PCT,
                                   max_hold: int = DEFAULT_MAX_HOLD) -> dict:
    """Compare performance of signals during resonance windows vs isolated signals."""
    resonance_ts_set = set()
    isolated_ts_set = set()

    for entry in resonance_data["resonance_entries"]:
        if entry["is_resonance"]:
            resonance_ts_set.add((entry["ts"], entry["coin"]))
        else:
            isolated_ts_set.add((entry["ts"], entry["coin"]))

    def compute_returns_for_entries(target_set: set) -> dict:
        """Forward returns for entries in target_set."""
        returns_10 = []
        returns_20 = []
        returns_50 = []

        for coin, signals in coin_signals.items():
            ts_to_idx = {sig["ts"]: i for i, sig in enumerate(signals)}
            for (ts, c) in target_set:
                if c != coin:
                    continue
                if ts not in ts_to_idx:
                    continue
                idx = ts_to_idx[ts]
                if idx >= len(signals):
                    continue
                current_price = signals[idx]["close"]
                if current_price <= 0:
                    continue
                for h, returns_list in [(10, returns_10), (20, returns_20), (50, returns_50)]:
                    fwd_idx = idx + h
                    if fwd_idx < len(signals):
                        ret = (signals[fwd_idx]["close"] - current_price) / current_price
                        returns_list.append(ret)

        def summarize(returns_list):
            if not returns_list:
                return {"avg_return_pct": 0.0, "hit_rate_pct": 0.0, "sample_size": 0}
            avg = sum(returns_list) / len(returns_list)
            hit = sum(1 for r in returns_list if r > 0) / len(returns_list) * 100
            return {
                "avg_return_pct": round(avg * 100, 6),
                "hit_rate_pct": round(hit, 2),
                "sample_size": len(returns_list),
            }

        return {
            "forward_10": summarize(returns_10),
            "forward_20": summarize(returns_20),
            "forward_50": summarize(returns_50),
        }

    resonance_perf = compute_returns_for_entries(resonance_ts_set)
    isolated_perf = compute_returns_for_entries(isolated_ts_set)

    return {
        "resonance_performance": resonance_perf,
        "isolated_performance": isolated_perf,
        "total_resonance_signals": len(resonance_ts_set),
        "total_isolated_signals": len(isolated_ts_set),
    }


# ===================================================================
# Interference map — cross-signal correlation matrix
# ===================================================================

def compute_interference_map(coin_signals: dict[str, list[dict]]) -> dict:
    """Compute correlation between strategy signals across coins.

    For each (coin, strategy) pair, build a binary time series of signals,
    then compute pairwise correlation.
    """
    # Align all coins to a common timestamp set
    all_ts: dict[int, dict[str, dict]] = {}  # ts -> {coin: {fib, st, mom}}
    for coin, signals in coin_signals.items():
        for sig in signals:
            ts = sig["ts"]
            if ts not in all_ts:
                all_ts[ts] = {}
            all_ts[ts][coin] = {
                "fib": int(sig["fib"]),
                "st": int(sig["st"]),
                "mom": int(sig["mom"]),
            }

    sorted_ts = sorted(all_ts.keys())
    n = len(sorted_ts)

    # Build series for each (coin, strategy)
    strategy_names = ["fib", "st", "mom"]
    coins = sorted(coin_signals.keys())
    series: dict[str, list[int]] = {}
    for coin in coins:
        for strat in strategy_names:
            key = f"{coin}:{strat}"
            series[key] = [all_ts[ts].get(coin, {}).get(strat, 0) for ts in sorted_ts]

    # Compute pairwise correlations
    corr_matrix: dict[str, dict[str, float]] = {}
    keys = list(series.keys())
    for k1 in keys:
        corr_matrix[k1] = {}
        for k2 in keys:
            if k1 == k2:
                corr_matrix[k1][k2] = 1.0
                continue
            if k2 in corr_matrix and k1 in corr_matrix[k2]:
                corr_matrix[k1][k2] = corr_matrix[k2][k1]
                continue

            s1 = series[k1]
            s2 = series[k2]
            corr = _pearson_correlation(s1, s2)
            corr_matrix[k1][k2] = round(corr, 4)

    return {
        "correlation_matrix": corr_matrix,
        "keys": keys,
    }


def _pearson_correlation(x: list[int | float], y: list[int | float]) -> float:
    n = len(x)
    if n < 2:
        return 0.0
    mx = sum(x) / n
    my = sum(y) / n
    sx = sum((xi - mx) ** 2 for xi in x)
    sy = sum((yi - my) ** 2 for yi in y)
    if sx == 0 or sy == 0:
        return 0.0
    cov = sum((x[i] - mx) * (y[i] - my) for i in range(n))
    return cov / math.sqrt(sx * sy)


def format_correlation_heatmap(corr_matrix: dict, keys: list[str]) -> str:
    """Format correlation matrix as a text heatmap table."""
    # Truncate key labels for display
    def short(k: str) -> str:
        parts = k.split(":")
        coin_short = parts[0].replace("-USD", "")
        strat_map = {"fib": "FIB", "st": "ST", "mom": "MOM"}
        return f"{coin_short}:{strat_map.get(parts[1], parts[1])}"

    short_keys = [short(k) for k in keys]
    col_width = max(len(s) for s in short_keys) + 2

    lines = []
    header = " " * (col_width + 2) + "".join(f"{s:>{col_width}}" for s in short_keys)
    lines.append(header)
    lines.append("-" * len(header))

    for i, k in enumerate(keys):
        row_label = f"{short_keys[i]:>{col_width}}"
        vals = ""
        for j, k2 in enumerate(keys):
            val = corr_matrix.get(k, {}).get(k2, 0.0)
            vals += f"{val:>{col_width}.4f}"
        lines.append(f"{row_label} {vals}")

    return "\n".join(lines)


# ===================================================================
# Optimal consensus threshold recommendation
# ===================================================================

def recommend_optimal_consensus(backtest_results: list[dict]) -> dict:
    """Pick the best consensus gate based on Sharpe ratio, then PnL, then win rate."""
    if not backtest_results:
        return {"recommended_gate": 0, "reason": "No backtest results available"}

    # Score each gate: primary=sharpe, secondary=pnl, tertiary=win_rate
    scored = []
    for r in backtest_results:
        score = r["sharpe_ratio"] * 100 + r["total_pnl"] + r["win_rate_pct"]
        scored.append((score, r))

    scored.sort(key=lambda x: x[0], reverse=True)
    best = scored[0][1]

    return {
        "recommended_consensus_gate": best["consensus_gate"],
        "sharpe_ratio": best["sharpe_ratio"],
        "total_pnl": best["total_pnl"],
        "roi_pct": best["roi_pct"],
        "win_rate_pct": best["win_rate_pct"],
        "max_drawdown_pct": best["max_drawdown_pct"],
        "total_trades": best["total_trades"],
        "reason": f"Best risk-adjusted score (Sharpe*100 + PnL + WR) = {scored[0][0]:.2f}",
    }


# ===================================================================
# Main
# ===================================================================

def main():
    import argparse
    parser = argparse.ArgumentParser(description="Cross-Asset Signal Consensus Engine")
    parser.add_argument("--coins", nargs="*", default=None, help="Coins to analyze")
    parser.add_argument("--days", type=int, default=30, help="Days of historical data")
    args = parser.parse_args()

    coins = args.coins or COINS
    days = args.days

    print(f"\n{'=' * 100}")
    print(f"  CROSS-ASSET SIGNAL CONSENSUS ENGINE")
    print(f"  Coins: {', '.join(coins)}")
    print(f"  Days: {days}")
    print(f"  Started: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}")
    print(f"{'=' * 100}\n")

    client = CoinbaseAdvancedClient()

    # ------------------------------------------------------------------
    # Step 1: Fetch candles
    # ------------------------------------------------------------------
    print("--- Step 1: Fetching 5-min candles ---")
    all_candles: dict[str, list[dict]] = {}
    for coin in coins:
        print(f"  Fetching {coin} ({days}d)...", end=" ", flush=True)
        try:
            candles_raw = fetch_candles(client, coin, days)
            parsed = [_parse_candle(c) for c in candles_raw]
            all_candles[coin] = parsed
            print(f"{len(parsed)} candles", flush=True)
        except Exception as e:
            print(f"FAILED: {e}", flush=True)
            all_candles[coin] = []

    # ------------------------------------------------------------------
    # Step 2: Compute signals for each coin
    # ------------------------------------------------------------------
    print(f"\n--- Step 2: Computing signals (fib + supertrend + momentum) ---")
    coin_signals: dict[str, list[dict]] = {}
    for coin in coins:
        candles = all_candles.get(coin, [])
        if not candles:
            print(f"  Skipping {coin}: no candles")
            continue
        print(f"  Scanning {coin} ({len(candles)} candles)...", end=" ", flush=True)
        signals = scan_coin_signals(candles, coin)
        coin_signals[coin] = signals

        n_fib = sum(1 for s in signals if s["fib"])
        n_st = sum(1 for s in signals if s["st"])
        n_mom = sum(1 for s in signals if s["mom"])
        n_agree_2 = sum(1 for s in signals if s["count"] >= 2)
        n_agree_3 = sum(1 for s in signals if s["count"] == 3)
        print(f"FIB={n_fib} ST={n_st} MOM={n_mom} 2+={n_agree_2} 3={n_agree_3}", flush=True)

    if not coin_signals:
        print("ERROR: No signals computed. Exiting.")
        sys.exit(1)

    # ------------------------------------------------------------------
    # Step 3: Consensus matrix
    # ------------------------------------------------------------------
    print(f"\n--- Step 3: Building Consensus Matrix ---")
    consensus_results: dict[str, dict] = {}
    for coin in coins:
        if coin not in coin_signals:
            continue
        matrix = build_consensus_matrix(coin_signals[coin])
        consensus_results[coin] = matrix

        print(f"\n  {coin} — Forward Returns by Consensus Level:")
        print(f"  {'Level':>6s} | {'H10 ret%':>10s} {'H10 hit%':>10s} {'H10 n':>8s} | "
              f"{'H20 ret%':>10s} {'H20 hit%':>10s} {'H20 n':>8s} | "
              f"{'H50 ret%':>10s} {'H50 hit%':>10s} {'H50 n':>8s}")
        print(f"  {'-' * 100}")
        for level in ["0", "1", "2", "3"]:
            row = matrix.get(level, {})
            h10 = row.get("10", {})
            h20 = row.get("20", {})
            h50 = row.get("50", {})
            print(f"  {level:>6s} | "
                  f"{h10.get('avg_return_pct', 0):>10.4f} {h10.get('hit_rate_pct', 0):>10.2f} {h10.get('sample_size', 0):>8d} | "
                  f"{h20.get('avg_return_pct', 0):>10.4f} {h20.get('hit_rate_pct', 0):>10.2f} {h20.get('sample_size', 0):>8d} | "
                  f"{h50.get('avg_return_pct', 0):>10.4f} {h50.get('hit_rate_pct', 0):>10.2f} {h50.get('sample_size', 0):>8d}")

    # ------------------------------------------------------------------
    # Step 4: Consensus-gated backtest
    # ------------------------------------------------------------------
    print(f"\n--- Step 4: Consensus-Gated Backtest ---")
    backtest_all: dict[str, list[dict]] = {}
    for coin in coins:
        if coin not in coin_signals:
            continue
        sigs = coin_signals[coin]
        gates = [0, 2, 3]
        results = []
        print(f"\n  {coin}:")
        for gate in gates:
            r = run_consensus_backtest(sigs, consensus_gate=gate)
            results.append(r)
            label = {0: "No gate (fib alone)", 2: "Consensus >= 2", 3: "Consensus >= 3"}[gate]
            print(f"    {label:<25s}  PnL=${r['total_pnl']:>+10.4f}  "
                  f"WR={r['win_rate_pct']:>5.2f}%  "
                  f"Trades={r['total_trades']:>5d}  "
                  f"MaxDD={r['max_drawdown_pct']:>6.3f}%  "
                  f"Sharpe={r['sharpe_ratio']:>7.4f}")
        backtest_all[coin] = results

    # ------------------------------------------------------------------
    # Step 5: Cross-coin resonance detection
    # ------------------------------------------------------------------
    print(f"\n--- Step 5: Cross-Coin Resonance Detection ---")
    resonance_data = detect_resonance(coin_signals, window=RESONANCE_WINDOW)
    print(f"  Total signal entries: {resonance_data['total_entries']}")
    print(f"  Resonance (2+ coins): {resonance_data['resonance_count']}")
    print(f"  Isolated (1 coin):    {resonance_data['isolated_count']}")

    resonance_comparison = resonance_backtest_comparison(coin_signals, resonance_data)
    print(f"\n  Resonance vs Isolated Performance:")
    print(f"  {'Horizon':>10s} | {'Resonance ret%':>16s} {'Resonance hit%':>16s} {'Resonance n':>12s} | "
          f"{'Isolated ret%':>14s} {'Isolated hit%':>14s} {'Isolated n':>12s}")
    print(f"  {'-' * 100}")
    for h in ["forward_10", "forward_20", "forward_50"]:
        rp = resonance_comparison["resonance_performance"].get(h, {})
        ip = resonance_comparison["isolated_performance"].get(h, {})
        print(f"  {h:>10s} | "
              f"{rp.get('avg_return_pct', 0):>16.4f} {rp.get('hit_rate_pct', 0):>16.2f} {rp.get('sample_size', 0):>12d} | "
              f"{ip.get('avg_return_pct', 0):>14.4f} {ip.get('hit_rate_pct', 0):>14.2f} {ip.get('sample_size', 0):>12d}")

    # ------------------------------------------------------------------
    # Step 6: Interference map
    # ------------------------------------------------------------------
    print(f"\n--- Step 6: Cross-Signal Interference Map ---")
    interference = compute_interference_map(coin_signals)
    heatmap = format_correlation_heatmap(interference["correlation_matrix"], interference["keys"])
    print(heatmap)

    # ------------------------------------------------------------------
    # Step 7: Optimal recommendation
    # ------------------------------------------------------------------
    print(f"\n--- Step 7: Optimal Consensus Threshold ---")
    all_backtests = []
    for coin, results in backtest_all.items():
        all_backtests.extend(results)
    recommendation = recommend_optimal_consensus(all_backtests)
    print(f"  Recommended consensus gate: {recommendation['recommended_consensus_gate']}")
    print(f"  Expected Sharpe: {recommendation['sharpe_ratio']}")
    print(f"  Expected PnL: ${recommendation['total_pnl']:.4f}")
    print(f"  Expected Win Rate: {recommendation['win_rate_pct']}%")
    print(f"  Reason: {recommendation['reason']}")

    # ------------------------------------------------------------------
    # Step 8: Save results
    # ------------------------------------------------------------------
    print(f"\n--- Step 8: Saving results ---")
    REPORTS.mkdir(exist_ok=True)

    output = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "coins": coins,
        "days": days,
        "consensus_matrix": consensus_results,
        "backtest_comparison": backtest_all,
        "resonance": {
            "summary": {
                "total_entries": resonance_data["total_entries"],
                "resonance_count": resonance_data["resonance_count"],
                "isolated_count": resonance_data["isolated_count"],
            },
            "performance_comparison": resonance_comparison,
        },
        "interference_map": {
            "correlation_matrix": interference["correlation_matrix"],
            "heatmap_text": heatmap,
        },
        "optimal_recommendation": recommendation,
    }

    output_path = REPORTS / "signal_consensus_results.json"
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(output, f, indent=2, default=str)
    print(f"  Saved: {output_path}")

    print(f"\n{'=' * 100}")
    print(f"  DONE")
    print(f"{'=' * 100}\n")


if __name__ == "__main__":
    main()
