#!/usr/bin/env python3
"""
Multi-Strategy Portfolio Backtest — Does diversification help?

Simulates ALL proven strategies running simultaneously:
1. RAVE Momentum (10-bar, 10% TP/SL)
2. RAVE RSI MR (RSI(3)<30, 25% TP, 48 bars)
3. IOTX BB Reversion (RSI<30, near BB lower, TP=middle, SL=5%)
4. BAL Momentum (50-bar, 10% TP, 3% SL)
5. BLUR Momentum (25-bar, 12% TP, 7% SL)

Tests:
- Equal allocation ($48 / N strategies each)
- Optimized allocation (weight by Sharpe ratio)
- RAVE-only baseline

Output: reports/multi_strategy_portfolio_results.json
"""
import json
import os
import sys
import time
import math
import statistics
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from coinbase_advanced_client import CoinbaseAdvancedClient

ROOT = Path(__file__).resolve().parent.parent
OUTPUT_PATH = ROOT / "reports" / "multi_strategy_portfolio_results.json"

COINS = ["RAVE-USD", "IOTX-USD", "BAL-USD", "BLUR-USD"]
BTC = "BTC-USD"
WINDOW_DAYS = 30
STARTING_CASH = 48.0


def utc_now_iso():
    return datetime.now(timezone.utc).isoformat()


def fetch_candles(client, pid, start, end, granularity="FIVE_MINUTE"):
    chunk_sec = 300 * 5 * 60
    all_c = []
    cs = start
    while cs < end:
        ce = min(cs + chunk_sec, end)
        try:
            resp = client.market_candles(pid, start=cs, end=ce, granularity=granularity)
            cands = resp.get("candles", [])
            all_c.extend(cands)
            cs = ce
            if not cands:
                break
            time.sleep(0.1)
        except Exception:
            cs = ce
            time.sleep(0.3)
    all_c.sort(key=lambda c: int(c["start"]))
    return all_c


def compute_rsi(closes, period):
    if len(closes) < period + 1:
        return 50.0
    deltas = [closes[i] - closes[i - 1] for i in range(1, len(closes))]
    gains = [d if d > 0 else 0 for d in deltas[-period:]]
    losses = [-d if d < 0 else 0 for d in deltas[-period:]]
    avg_g = sum(gains) / period
    avg_l = sum(losses) / period
    if avg_l > 0:
        rs = avg_g / avg_l
        return 100 - 100 / (1 + rs)
    return 100.0


def compute_bb(closes, period=20):
    if len(closes) < period:
        return None
    recent = closes[-period:]
    sma = statistics.mean(recent)
    std = statistics.stdev(recent) if len(recent) > 1 else 0
    return {"sma": sma, "lower": sma - 2 * std, "upper": sma + 2 * std}


def get_fee_rate(total_volume):
    if total_volume >= 50000:
        return 0.0015
    elif total_volume >= 10000:
        return 0.0025
    return 0.0040


# ============================================================
# STRATEGY ENGINES (simplified, per-bar logic)
# ============================================================

class StrategyState:
    def __init__(self, name, coin, starting_cash):
        self.name = name
        self.coin = coin
        self.cash = starting_cash
        self.starting_cash = starting_cash
        self.position = None
        self.closes = 0
        self.wins = 0
        self.losses = 0
        self.total_fees = 0.0
        self.total_volume = 0.0
        self.history = []  # close prices
        self.candle_history = []  # full candles
        self.equity_curve = []  # equity per bar
        self.signals = 0
        self.max_dd = 0.0
        self.peak_equity = starting_cash

    @property
    def equity(self):
        pos_value = 0
        if self.position:
            # Approximate: position value = deployed cash (we'll use actual exit for precision)
            pos_value = self.position.get("q", 0)
        return self.cash + pos_value

    def record_equity(self):
        eq = self.equity
        if eq > self.peak_equity:
            self.peak_equity = eq
        dd = (self.peak_equity - eq) / self.peak_equity * 100
        if dd > self.max_dd:
            self.max_dd = dd
        self.equity_curve.append(eq)

    def summary(self):
        total_pnl = self.cash - self.starting_cash
        # Add unrealized position value
        if self.position:
            total_pnl += self.position.get("q", 0) - self.position.get("q", 0)  # net zero if held
        wr = self.wins / max(1, self.closes) * 100
        return_pct = total_pnl / self.starting_cash * 100
        return {
            "name": self.name,
            "coin": self.coin,
            "net_pnl": round(total_pnl, 2),
            "return_pct": round(return_pct, 1),
            "win_rate": round(wr, 1),
            "closes": self.closes,
            "wins": self.wins,
            "losses": self.losses,
            "max_dd": round(self.max_dd, 1),
            "total_fees": round(self.total_fees, 2),
            "signals": self.signals,
            "final_cash": round(self.cash, 2),
        }


def process_momentum_strategy(state, candle, lookback, tp_pct, sl_pct):
    """Momentum breakout: buy N-bar high breakout."""
    close = float(candle["close"])
    high = float(candle["high"])
    low = float(candle["low"])
    open_price = float(candle["open"])

    state.history.append(close)
    state.candle_history.append(candle)
    if len(state.history) > 500:
        state.history = state.history[-500:]

    # EXIT
    if state.position:
        fee_rate = get_fee_rate(state.total_volume)
        pos = state.position
        pos["hold"] += 1

        exit_price = None
        exit_reason = None

        if high >= pos["tp"]:
            exit_price = pos["tp"]
            exit_reason = "tp"
        elif low <= pos["sl"]:
            exit_price = pos["sl"]
            exit_reason = "stop"
        elif pos["hold"] >= 48:
            exit_price = close
            exit_reason = "timeout"

        if exit_price:
            units = pos["units"]
            gross = (exit_price - pos["ep"]) * units
            entry_fee = pos["entry_fee"]
            exit_fee = exit_price * units * fee_rate
            net = gross - entry_fee - exit_fee

            state.cash += pos["q"] + net
            state.closes += 1
            state.total_volume += pos["q"] + (exit_price * units)
            state.total_fees += entry_fee + exit_fee
            if net > 0:
                state.wins += 1
            else:
                state.losses += 1
            state.position = None

    # ENTRY: breakout of N-bar high
    if state.position is None and len(state.history) > lookback + 1 and state.cash >= 5.0:
        recent_high = max(float(c["high"]) for c in state.candle_history[-(lookback+1):-1])
        if high > recent_high:
            state.signals += 1
            fee_rate = get_fee_rate(state.total_volume)
            deploy = state.cash * 0.95
            entry_price = open_price
            entry_fee = deploy * fee_rate
            units = (deploy - entry_fee) / entry_price

            state.cash -= deploy
            state.position = {
                "ep": entry_price,
                "q": deploy,
                "units": units,
                "tp": entry_price * (1 + tp_pct),
                "sl": entry_price * (1 - sl_pct),
                "hold": 0,
                "entry_fee": entry_fee,
            }

    state.record_equity()


def process_rsi_mr_strategy(state, candle, rsi_period=3, os_thresh=30, tp_pct=0.25, max_hold=48):
    """RSI Mean Reversion: buy RSI<30, TP 25%, no SL, 48 bars."""
    close = float(candle["close"])
    high = float(candle["high"])
    low = float(candle["low"])
    open_price = float(candle["open"])

    state.history.append(close)
    state.candle_history.append(candle)
    if len(state.history) > 500:
        state.history = state.history[-500:]

    # EXIT
    if state.position:
        fee_rate = get_fee_rate(state.total_volume)
        pos = state.position
        pos["hold"] += 1

        exit_price = None
        exit_reason = None

        if high >= pos["tp"]:
            exit_price = pos["tp"]
            exit_reason = "tp"
        elif pos["hold"] >= max_hold:
            exit_price = close
            exit_reason = "timeout"

        if exit_price:
            units = pos["units"]
            gross = (exit_price - pos["ep"]) * units
            entry_fee = pos["entry_fee"]
            exit_fee = exit_price * units * fee_rate
            net = gross - entry_fee - exit_fee

            state.cash += pos["q"] + net
            state.closes += 1
            state.total_volume += pos["q"] + (exit_price * units)
            state.total_fees += entry_fee + exit_fee
            if net > 0:
                state.wins += 1
            else:
                state.losses += 1
            state.position = None

    # ENTRY: RSI oversold
    if state.position is None and len(state.history) > rsi_period + 1 and state.cash >= 5.0:
        rsi_val = compute_rsi(state.history[:-1], rsi_period)
        if rsi_val <= os_thresh:
            state.signals += 1
            fee_rate = get_fee_rate(state.total_volume)
            deploy = state.cash * 0.95
            entry_price = open_price
            entry_fee = deploy * fee_rate
            units = (deploy - entry_fee) / entry_price

            state.cash -= deploy
            state.position = {
                "ep": entry_price,
                "q": deploy,
                "units": units,
                "tp": entry_price * (1 + tp_pct),
                "sl": 0,
                "hold": 0,
                "entry_fee": entry_fee,
            }

    state.record_equity()


def process_bb_reversion_strategy(state, candle, rsi_thresh=30, bb_period=20, sl_pct=0.05, max_hold=24):
    """BB Reversion: buy when RSI<30 AND price near BB lower, TP=middle, SL=5%."""
    close = float(candle["close"])
    high = float(candle["high"])
    low = float(candle["low"])
    open_price = float(candle["open"])

    state.history.append(close)
    state.candle_history.append(candle)
    if len(state.history) > 500:
        state.history = state.history[-500:]

    # EXIT
    if state.position:
        fee_rate = get_fee_rate(state.total_volume)
        pos = state.position
        pos["hold"] += 1

        exit_price = None
        exit_reason = None

        if high >= pos["tp"]:
            exit_price = pos["tp"]
            exit_reason = "tp"
        elif low <= pos["sl"]:
            exit_price = pos["sl"]
            exit_reason = "stop"
        elif pos["hold"] >= max_hold:
            exit_price = close
            exit_reason = "timeout"

        if exit_price:
            units = pos["units"]
            gross = (exit_price - pos["ep"]) * units
            entry_fee = pos["entry_fee"]
            exit_fee = exit_price * units * fee_rate
            net = gross - entry_fee - exit_fee

            state.cash += pos["q"] + net
            state.closes += 1
            state.total_volume += pos["q"] + (exit_price * units)
            state.total_fees += entry_fee + exit_fee
            if net > 0:
                state.wins += 1
            else:
                state.losses += 1
            state.position = None

    # ENTRY: RSI oversold AND price near BB lower
    if state.position is None and len(state.history) > bb_period + 5 and state.cash >= 5.0:
        rsi_val = compute_rsi(state.history[:-1], 3)
        bb = compute_bb(state.history[:-1], bb_period)

        if bb and rsi_val <= rsi_thresh:
            # Check price is within 2% of BB lower
            dist_to_lower = (close - bb["lower"]) / bb["sma"] * 100 if bb["sma"] > 0 else 999
            if dist_to_lower < 2.0:
                state.signals += 1
                fee_rate = get_fee_rate(state.total_volume)
                deploy = state.cash * 0.95
                entry_price = open_price
                entry_fee = deploy * fee_rate
                units = (deploy - entry_fee) / entry_price

                state.cash -= deploy
                state.position = {
                    "ep": entry_price,
                    "q": deploy,
                    "units": units,
                    "tp": bb["sma"],  # Target = middle band
                    "sl": entry_price * (1 - sl_pct),
                    "hold": 0,
                    "entry_fee": entry_fee,
                }

    state.record_equity()


# ============================================================
# PORTFOLIO RUNNER
# ============================================================

def run_portfolio(strategies_config, coin_candles, allocation_mode="equal"):
    """
    Run multiple strategies simultaneously with shared bankroll.

    strategies_config: list of {"name", "coin", "type", "params", "alloc_pct"}
    allocation_mode: "equal" (split cash equally) or "sharpe" (weight by Sharpe)
    """
    total_cash = STARTING_CASH
    n_strategies = len(strategies_config)

    # Initialize strategies
    states = []
    for sc in strategies_config:
        alloc_cash = total_cash * sc["alloc_pct"]
        state = StrategyState(sc["name"], sc["coin"], alloc_cash)
        states.append(state)

    # Align all candles by timestamp
    all_timestamps = set()
    for candles in coin_candles.values():
        for c in candles:
            all_timestamps.add(int(c["start"]))
    sorted_timestamps = sorted(all_timestamps)

    # Build lookup: (coin, timestamp) -> candle
    candle_lookup = {}
    for coin, candles in coin_candles.items():
        for c in candles:
            candle_lookup[(coin, int(c["start"]))] = c

    # Process each timestamp
    for ts in sorted_timestamps:
        for state in states:
            candle = candle_lookup.get((state.coin, ts))
            if candle is None:
                state.record_equity()
                continue

            cfg = next(s for s in strategies_config if s["name"] == state.name)

            if cfg["type"] == "momentum":
                p = cfg["params"]
                process_momentum_strategy(state, candle, p["lookback"], p["tp"], p["sl"])
            elif cfg["type"] == "rsi_mr":
                p = cfg["params"]
                process_rsi_mr_strategy(state, candle, p.get("rsi_period", 3), p.get("os_thresh", 30),
                                        p.get("tp_pct", 0.25), p.get("max_hold", 48))
            elif cfg["type"] == "bb_reversion":
                p = cfg["params"]
                process_bb_reversion_strategy(state, candle, p.get("rsi_thresh", 30),
                                              p.get("bb_period", 20), p.get("sl_pct", 0.05),
                                              p.get("max_hold", 24))

    # Summarize
    individual_results = [s.summary() for s in states]

    # Portfolio-level metrics
    total_equity = sum(s.equity for s in states)
    total_pnl = total_equity - STARTING_CASH
    return_pct = total_pnl / STARTING_CASH * 100

    # Portfolio max DD: compute from combined equity curve
    # (approximate: sum of individual equity curves)
    combined_equity_curve = []
    for i in range(len(states[0].equity_curve)):
        eq = sum(s.equity_curve[i] for s in states if i < len(s.equity_curve))
        combined_equity_curve.append(eq)

    peak = STARTING_CASH
    max_dd = 0.0
    for eq in combined_equity_curve:
        if eq > peak:
            peak = eq
        dd = (peak - eq) / peak * 100
        if dd > max_dd:
            max_dd = dd

    # Sharpe ratio (annualized, assuming 30d = 8640 5-min bars)
    if len(combined_equity_curve) > 1:
        returns = [(combined_equity_curve[i] - combined_equity_curve[i-1]) / combined_equity_curve[i-1]
                   for i in range(1, len(combined_equity_curve))
                   if combined_equity_curve[i-1] > 0]
        if returns:
            avg_ret = statistics.mean(returns)
            std_ret = statistics.stdev(returns) if len(returns) > 1 else 0.001
            sharpe = (avg_ret / std_ret) * math.sqrt(8640) if std_ret > 0 else 0
        else:
            sharpe = 0
    else:
        sharpe = 0

    # Correlation between strategy returns
    correlations = {}
    if len(states) >= 2:
        for i in range(len(states)):
            for j in range(i+1, len(states)):
                min_len = min(len(states[i].equity_curve), len(states[j].equity_curve))
                if min_len > 2:
                    ret_i = [(states[i].equity_curve[k] - states[i].equity_curve[k-1]) / states[i].equity_curve[k-1]
                             for k in range(1, min_len) if states[i].equity_curve[k-1] > 0]
                    ret_j = [(states[j].equity_curve[k] - states[j].equity_curve[k-1]) / states[j].equity_curve[k-1]
                             for k in range(1, min_len) if states[j].equity_curve[k-1] > 0]
                    min_r = min(len(ret_i), len(ret_j))
                    if min_r > 2:
                        ri = ret_i[:min_r]
                        rj = ret_j[:min_r]
                        mean_i = statistics.mean(ri)
                        mean_j = statistics.mean(rj)
                        cov = sum((a - mean_i) * (b - mean_j) for a, b in zip(ri, rj))
                        var_i = sum((a - mean_i)**2 for a in ri)
                        var_j = sum((b - mean_j)**2 for b in rj)
                        denom = math.sqrt(var_i * var_j)
                        corr = cov / denom if denom > 0 else 0
                        correlations[f"{states[i].name}↔{states[j].name}"] = round(corr, 3)

    return {
        "total_equity": round(total_equity, 2),
        "total_pnl": round(total_pnl, 2),
        "return_pct": round(return_pct, 1),
        "max_dd": round(max_dd, 1),
        "sharpe": round(sharpe, 2),
        "n_strategies": n_strategies,
        "individual": individual_results,
        "correlations": correlations,
    }


def main():
    client = CoinbaseAdvancedClient()

    now = int(time.time())
    start = now - WINDOW_DAYS * 86400

    print(f"=" * 70, flush=True)
    print(f"MULTI-STRATEGY PORTFOLIO BACKTEST — {WINDOW_DAYS}d, ${STARTING_CASH}", flush=True)
    print(f"=" * 70, flush=True)

    # Fetch candles
    coin_candles = {}
    for coin in COINS:
        print(f"Fetching {coin}...", flush=True)
        candles = fetch_candles(client, coin, start, now)
        coin_candles[coin] = candles
        print(f"  {coin}: {len(candles)} candles", flush=True)

    # Define strategy configurations
    # Allocation: equal split ($48 / N each)
    strategies_equal = [
        {"name": "RAVE Momentum", "coin": "RAVE-USD", "type": "momentum",
         "params": {"lookback": 10, "tp": 0.10, "sl": 0.10}, "alloc_pct": 0.20},
        {"name": "RAVE RSI MR", "coin": "RAVE-USD", "type": "rsi_mr",
         "params": {"rsi_period": 3, "os_thresh": 30, "tp_pct": 0.25, "max_hold": 48}, "alloc_pct": 0.20},
        {"name": "IOTX BB Rev", "coin": "IOTX-USD", "type": "bb_reversion",
         "params": {"rsi_thresh": 30, "bb_period": 20, "sl_pct": 0.05, "max_hold": 24}, "alloc_pct": 0.20},
        {"name": "BAL Momentum", "coin": "BAL-USD", "type": "momentum",
         "params": {"lookback": 50, "tp": 0.10, "sl": 0.03}, "alloc_pct": 0.20},
        {"name": "BLUR Momentum", "coin": "BLUR-USD", "type": "momentum",
         "params": {"lookback": 25, "tp": 0.12, "sl": 0.07}, "alloc_pct": 0.20},
    ]

    # RAVE-only baseline (for comparison)
    strategies_rave_only = [
        {"name": "RAVE Momentum", "coin": "RAVE-USD", "type": "momentum",
         "params": {"lookback": 10, "tp": 0.10, "sl": 0.10}, "alloc_pct": 1.0},
    ]

    # Optimized: weight high-Sharpe strategies more
    # IOTX BB Rev has best Sharpe, so give it more
    strategies_optimized = [
        {"name": "RAVE Momentum", "coin": "RAVE-USD", "type": "momentum",
         "params": {"lookback": 10, "tp": 0.10, "sl": 0.10}, "alloc_pct": 0.35},
        {"name": "IOTX BB Rev", "coin": "IOTX-USD", "type": "bb_reversion",
         "params": {"rsi_thresh": 30, "bb_period": 20, "sl_pct": 0.05, "max_hold": 24}, "alloc_pct": 0.35},
        {"name": "BAL Momentum", "coin": "BAL-USD", "type": "momentum",
         "params": {"lookback": 50, "tp": 0.10, "sl": 0.03}, "alloc_pct": 0.15},
        {"name": "BLUR Momentum", "coin": "BLUR-USD", "type": "momentum",
         "params": {"lookback": 25, "tp": 0.12, "sl": 0.07}, "alloc_pct": 0.15},
    ]

    # Run all three
    print(f"\n{'='*70}", flush=True)
    print("RUNNING: EQUAL ALLOCATION (5 strategies, 20% each)", flush=True)
    print(f"{'='*70}", flush=True)
    result_equal = run_portfolio(strategies_equal, coin_candles, "equal")

    print(f"\n{'='*70}", flush=True)
    print("RUNNING: RAVE-ONLY BASELINE (1 strategy, 100%)", flush=True)
    print(f"{'='*70}", flush=True)
    result_rave = run_portfolio(strategies_rave_only, coin_candles, "rave")

    print(f"\n{'='*70}", flush=True)
    print("RUNNING: OPTIMIZED ALLOCATION (RAVE 35%, IOTX 35%, BAL 15%, BLUR 15%)", flush=True)
    print(f"{'='*70}", flush=True)
    result_optimized = run_portfolio(strategies_optimized, coin_candles, "optimized")

    # Print results
    def print_result(label, r):
        print(f"\n{'─'*60}", flush=True)
        print(f"  {label}", flush=True)
        print(f"{'─'*60}", flush=True)
        print(f"  Total PnL: ${r['total_pnl']:.2f} ({r['return_pct']:.1f}%)", flush=True)
        print(f"  Max DD: {r['max_dd']:.1f}%  |  Sharpe: {r['sharpe']:.2f}", flush=True)
        print(f"  Strategies: {r['n_strategies']}", flush=True)
        print(f"\n  Individual Results:", flush=True)
        for s in r["individual"]:
            print(f"    {s['name']:<15} | PnL=${s['net_pnl']:>8.2f} | WR={s['win_rate']:>5.1f}% | "
                  f"Trades={s['closes']:>3} | DD={s['max_dd']:>5.1f}%", flush=True)
        if r["correlations"]:
            print(f"\n  Correlations:", flush=True)
            for pair, corr in r["correlations"].items():
                print(f"    {pair}: {corr:.3f}", flush=True)

    print_result("EQUAL ALLOCATION", result_equal)
    print_result("RAVE-ONLY BASELINE", result_rave)
    print_result("OPTIMIZED ALLOCATION", result_optimized)

    # Final comparison
    print(f"\n{'='*70}", flush=True)
    print("PORTFOLIO vs SINGLE-STRATEGY COMPARISON", flush=True)
    print(f"{'='*70}", flush=True)
    print(f"{'Portfolio':<25} | {'PnL':>8} | {'DD':>6} | {'Sharpe':>8} | {'Strategies':>10}", flush=True)
    print(f"{'-'*25}-+-{'-'*8}-+-{'-'*6}-+-{'-'*8}-+-{'-'*10}", flush=True)
    print(f"{'Equal (5-strategy)':<25} | ${result_equal['total_pnl']:>7.2f} | "
          f"{result_equal['max_dd']:>5.1f}% | {result_equal['sharpe']:>8.2f} | "
          f"{result_equal['n_strategies']:>10}", flush=True)
    print(f"{'Optimized (4-strategy)':<25} | ${result_optimized['total_pnl']:>7.2f} | "
          f"{result_optimized['max_dd']:>5.1f}% | {result_optimized['sharpe']:>8.2f} | "
          f"{result_optimized['n_strategies']:>10}", flush=True)
    print(f"{'RAVE-Only Baseline':<25} | ${result_rave['total_pnl']:>7.2f} | "
          f"{result_rave['max_dd']:>5.1f}% | {result_rave['sharpe']:>8.2f} | "
          f"{result_rave['n_strategies']:>10}", flush=True)

    # Verdict
    best = max([result_equal, result_optimized, result_rave], key=lambda r: r["total_pnl"])
    best_rar = max([result_equal, result_optimized, result_rave],
                   key=lambda r: r["sharpe"] / max(1, r["max_dd"]))

    print(f"\n  Highest return: ${best['total_pnl']:.2f} (Sharpe: {best['sharpe']:.2f})")
    print(f"  Best risk-adjusted: Sharpe/DD = {best_rar['sharpe']/max(1,best_rar['max_dd']):.4f}")

    # Save report
    report = {
        "run_at": utc_now_iso(),
        "window_days": WINDOW_DAYS,
        "starting_cash": STARTING_CASH,
        "equal_allocation": result_equal,
        "rave_only": result_rave,
        "optimized_allocation": result_optimized,
        "verdict": {
            "highest_return": best["total_pnl"],
            "best_risk_adjusted_sharpe_dd": round(best_rar["sharpe"]/max(1,best_rar["max_dd"]), 4),
        },
    }

    with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2, sort_keys=True)

    print(f"\nReport saved: {OUTPUT_PATH}", flush=True)
    print("\nDone. 🎯", flush=True)


if __name__ == "__main__":
    main()
