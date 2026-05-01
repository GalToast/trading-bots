#!/usr/bin/env python3
"""
Strategy Risk Assessment + Correlation Matrix

For each validated strategy, computes:
- Max Drawdown %, Sharpe Ratio, Max Losing Streak
- Win/Loss Ratio, Profit Factor, Calmar Ratio
- Strategy Correlation Matrix (do strategies lose on same bars?)

This is the risk layer the governance board needs before deployment.
"""

import json
import math
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from coinbase_advanced_client import CoinbaseAdvancedClient
from strategy_library import backtest


# ==========================================
# RECREATE ENTRY FUNCTIONS (copied from sweeps)
# ==========================================

def _supertrend_entry(candles_hist, closes, candle, params):
    if len(candles_hist) < 25:
        return False
    period = params.get("st_period", 10)
    multiplier = params.get("st_multiplier", 3.0)
    trs = []
    for i in range(1, len(candles_hist)):
        h = float(candles_hist[i]["high"])
        l = float(candles_hist[i]["low"])
        c_prev = float(candles_hist[i-1]["close"])
        tr = max(h - l, abs(h - c_prev), abs(l - c_prev))
        trs.append(tr)
    if len(trs) < period:
        return False
    atr = sum(trs[-period:]) / period
    mid = (float(candles_hist[-1]["high"]) + float(candles_hist[-1]["low"])) / 2
    upper_band = mid + multiplier * atr
    lower_band = mid - multiplier * atr
    close = float(candles_hist[-1]["close"])
    if close > upper_band:
        trend = "bullish"
    elif close < lower_band:
        trend = "bearish"
    else:
        prev_close = float(candles_hist[-2]["close"])
        prev_mid = (float(candles_hist[-2]["high"]) + float(candles_hist[-2]["low"])) / 2
        prev_atr = sum(trs[-period-1:-1]) / period if len(trs) > period else atr
        prev_lower = prev_mid - multiplier * prev_atr
        prev_upper = prev_mid + multiplier * prev_atr
        if prev_close > prev_upper:
            trend = "bullish"
        elif prev_close < prev_lower:
            trend = "bearish"
        else:
            trend = "bullish" if close > prev_mid else "bearish"
    if trend == "bullish" and len(closes) > 1 and closes[-1] > closes[-2]:
        return True
    return False


def _fibonacci_entry(candles_hist, closes, candle, params):
    """Fibonacci level breakout."""
    if len(candles_hist) < 30:
        return False
    lookback = params.get("fib_lookback", 20)
    if len(closes) < lookback + 1:
        return False
    window = closes[-lookback-1:-1]
    fib_high = max(window)
    fib_level = params.get("fib_level", 0.618)
    fib_retrace = min(window) + (fib_high - min(window)) * fib_level
    current = closes[-1]
    if current > fib_high * 0.995 and current > closes[-2]:
        return True
    return False


def _momentum_entry(candles_hist, closes, candle, params):
    if len(candles_hist) < 15:
        return False
    lookback = params.get("lookback", 10)
    current_high = float(candle["high"])
    highest = max(float(c["high"]) for c in candles_hist[-(lookback + 1):-1])
    return current_high > highest


def compute_risk_metrics(trade_results, starting_cash=48.0):
    """Compute comprehensive risk metrics from trade results."""
    if not trade_results:
        return {}

    wins = [t for t in trade_results if t > 0]
    losses = [t for t in trade_results if t < 0]

    if not wins or not losses:
        return {
            "total_trades": len(trade_results),
            "total_pnl": round(sum(trade_results), 2),
            "wins": len(wins),
            "losses": len(losses),
            "win_rate": round(len(wins) / len(trade_results) * 100, 1) if trade_results else 0,
            "avg_win": round(sum(wins) / len(wins), 2) if wins else 0,
            "avg_loss": round(sum(losses) / len(losses), 2) if losses else 0,
            "max_drawdown_pct": 0,
            "sharpe_ratio": 0,
            "max_losing_streak": 0,
            "profit_factor": float("inf") if not losses else 0,
            "calmar_ratio": 0,
        }

    avg_win = sum(wins) / len(wins)
    avg_loss = abs(sum(losses) / len(losses))
    gross_profit = sum(wins)
    gross_loss = abs(sum(losses))

    # Max drawdown (running equity curve)
    equity = [starting_cash]
    for t in trade_results:
        equity.append(equity[-1] + t)
    peak = equity[0]
    max_dd = 0
    for e in equity:
        if e > peak:
            peak = e
        dd = (peak - e) / peak if peak > 0 else 0
        if dd > max_dd:
            max_dd = dd

    # Max losing streak
    max_streak = 0
    current_streak = 0
    for t in trade_results:
        if t < 0:
            current_streak += 1
            max_streak = max(max_streak, current_streak)
        else:
            current_streak = 0

    # Sharpe ratio (annualized, assuming 5min bars ≈ 105K/year, but use per-trade)
    if len(trade_results) > 1:
        mean_ret = sum(trade_results) / len(trade_results)
        std_ret = math.sqrt(sum((t - mean_ret) ** 2 for t in trade_results) / len(trade_results))
        sharpe = mean_ret / std_ret if std_ret > 0 else 0
    else:
        sharpe = 0

    # Calmar ratio (annual return / max drawdown)
    total_pnl = sum(trade_results)
    calmar = (total_pnl / starting_cash) / max_dd if max_dd > 0 else 0

    return {
        "total_trades": len(trade_results),
        "total_pnl": round(total_pnl, 2),
        "wins": len(wins),
        "losses": len(losses),
        "win_rate": round(len(wins) / len(trade_results) * 100, 1),
        "avg_win": round(avg_win, 2),
        "avg_loss": round(avg_loss, 2),
        "win_loss_ratio": round(avg_win / avg_loss, 2) if avg_loss > 0 else float("inf"),
        "max_drawdown_pct": round(max_dd * 100, 1),
        "sharpe_ratio": round(sharpe, 2),
        "max_losing_streak": max_streak,
        "profit_factor": round(gross_profit / gross_loss, 2) if gross_loss > 0 else float("inf"),
        "calmar_ratio": round(calmar, 2),
    }


def fetch_candles(client, pid, start, end):
    chunk_sec = 300 * 5 * 60
    all_c = []
    cs = start
    while cs < end:
        ce = min(cs + chunk_sec, end)
        try:
            resp = client.market_candles(pid, start=cs, end=ce, granularity="FIVE_MINUTE")
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


def main():
    start_time = time.time()
    print(f"\n{'='*70}")
    print(f"STRATEGY RISK ASSESSMENT + CORRELATION MATRIX")
    print(f"Started: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}")
    print(f"{'='*70}\n")

    client = CoinbaseAdvancedClient()
    coins = ["RAVE-USD", "NOM-USD", "GHST-USD", "TRU-USD", "SUP-USD"]
    print(f"Testing on {len(coins)} coins (30d)\n")

    now = int(time.time())
    start_ts = now - 30 * 86400
    all_candles = {}
    for coin in coins:
        try:
            candles = fetch_candles(client, coin, start_ts, now)
            if candles:
                all_candles[coin] = candles
        except Exception as e:
            print(f"  {coin}: ERROR")
        time.sleep(0.2)

    strategies = {
        "supertrend": {"entry": _supertrend_entry, "params": {"st_period": 10, "st_multiplier": 3.0, "tp_pct": 10, "sl_pct": 3, "max_hold": 24}},
        "fibonacci": {"entry": _fibonacci_entry, "params": {"fib_lookback": 20, "fib_level": 0.618, "tp_pct": 10, "sl_pct": 3, "max_hold": 24}},
        "momentum": {"entry": _momentum_entry, "params": {"lookback": 10, "tp_pct": 10, "sl_pct": 3, "max_hold": 24}},
    }

    all_risk_metrics = {}
    all_signal_logs = {}  # For correlation: {strategy: {coin: [True/False per bar]}}

    for strat_name, strat_info in strategies.items():
        print(f"\n{'='*50}")
        print(f"  {strat_name.upper()}")
        print(f"{'='*50}\n")

        coin_risks = {}
        coin_signals = {}

        for coin, candles in all_candles.items():
            # Run backtest with detailed tracking
            results = []
            signals_per_bar = []

            # Simulate to get per-trade results
            cash = 48.0
            pos = None
            for i, c in enumerate(candles):
                close = float(c["close"])
                high = float(c["high"])
                low = float(c["low"])
                candle_open = float(c["open"])

                if candle_open <= 0:
                    signals_per_bar.append(False)
                    continue

                closes_so_far = [float(candles[j]["close"]) for j in range(i+1)]
                candles_so_far = candles[:i+1]

                signal = strat_info["entry"](candles_so_far, closes_so_far, c, strat_info["params"])
                signals_per_bar.append(signal)

                if pos:
                    pos["hold"] += 1
                    exit_price = None
                    tp = pos["tp"]
                    sl = pos["sl"]
                    if high >= tp:
                        exit_price = tp
                    elif sl > 0 and low <= sl:
                        exit_price = sl
                    elif pos["hold"] >= pos["max_hold"]:
                        exit_price = close

                    if exit_price:
                        net = (exit_price - pos["ep"]) * pos["units"] - pos["entry_fee"] - exit_price * pos["units"] * 0.004
                        cash += pos["q"] + net
                        results.append(net)
                        pos = None

                if pos is None and signal:
                    entry_fee = cash * 0.004
                    units = (cash - entry_fee) / candle_open
                    tp = candle_open * (1 + strat_info["params"].get("tp_pct", 10) / 100)
                    sl = candle_open * (1 - strat_info["params"].get("sl_pct", 3) / 100)
                    cash -= candle_open * units + entry_fee
                    pos = {"ep": candle_open, "q": candle_open * units, "hold": 0, "tp": tp, "sl": sl, "units": units, "entry_fee": entry_fee, "max_hold": strat_info["params"].get("max_hold", 24)}

            # Close remaining position
            if pos and candles:
                last_close = float(candles[-1]["close"])
                net = (last_close - pos["ep"]) * pos["units"] - pos["entry_fee"] - last_close * pos["units"] * 0.004
                cash += pos["q"] + net
                results.append(net)

            risk = compute_risk_metrics(results)
            risk["coin"] = coin
            risk["final_equity"] = round(cash, 2)
            coin_risks[coin] = risk
            coin_signals[coin] = signals_per_bar

            print(f"  {coin}:")
            print(f"    Trades: {risk.get('total_trades', 0)} | PnL: ${risk.get('total_pnl', 0):.2f} | WR: {risk.get('win_rate', 0):.1f}%")
            print(f"    Max DD: {risk.get('max_drawdown_pct', 0):.1f}% | Sharpe: {risk.get('sharpe_ratio', 0):.2f} | Profit Factor: {risk.get('profit_factor', 0):.2f}")

        all_risk_metrics[strat_name] = coin_risks
        all_signal_logs[strat_name] = coin_signals

    # === CORRELATION MATRIX ===
    print(f"\n{'='*70}")
    print(f"  SIGNAL CORRELATION MATRIX (Jaccard similarity)")
    print(f"{'='*70}\n")

    correlation_matrix = {}
    for coin in coins:
        print(f"  {coin}:")
        coin_corr = {}
        strat_names = list(strategies.keys())
        for i, s1 in enumerate(strat_names):
            for s2 in strat_names[i+1:]:
                sigs1 = set(idx for idx, v in enumerate(all_signal_logs[s1].get(coin, [])) if v)
                sigs2 = set(idx for idx, v in enumerate(all_signal_logs[s2].get(coin, [])) if v)
                if sigs1 or sigs2:
                    intersection = len(sigs1 & sigs2)
                    union = len(sigs1 | sigs2)
                    jaccard = intersection / union if union > 0 else 0
                    print(f"    {s1:<15} vs {s2:<15}: {jaccard:.1%} overlap ({intersection} shared signals)")
                    coin_corr[f"{s1}_vs_{s2}"] = round(jaccard, 3)
                else:
                    coin_corr[f"{s1}_vs_{s2}"] = 0
        correlation_matrix[coin] = coin_corr

    # Save report
    report = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "elapsed_seconds": round(time.time() - start_time, 1),
        "coins_tested": list(all_candles.keys()),
        "risk_metrics": all_risk_metrics,
        "correlation_matrix": correlation_matrix,
        "summary": {},
    }

    # Summary: best strategy per coin
    for coin in coins:
        best_strat = None
        best_pnl = -999
        for strat, coin_risks in all_risk_metrics.items():
            if coin in coin_risks:
                pnl = coin_risks[coin].get("total_pnl", -999)
                if pnl > best_pnl:
                    best_pnl = pnl
                    best_strat = strat
        report["summary"][coin] = {"best_strategy": best_strat, "best_pnl": best_pnl}

    out_path = Path(__file__).parent.parent / "reports" / "strategy_risk_assessment.json"
    out_path.parent.mkdir(exist_ok=True)
    with open(out_path, "w") as f:
        json.dump(report, f, indent=2)

    print(f"\n{'='*70}")
    print(f"RISK ASSESSMENT COMPLETE in {time.time() - start_time:.0f}s")
    print(f"Report: {out_path}")

    print(f"\n  SUMMARY — Best Strategy Per Coin:")
    for coin, info in report["summary"].items():
        print(f"    {coin}: {info['best_strategy']} (${info['best_pnl']:.2f})")

    print(f"\n{'='*70}\n")


if __name__ == "__main__":
    main()
