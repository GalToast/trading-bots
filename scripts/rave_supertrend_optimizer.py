#!/usr/bin/env python3
"""
RAVE Supertrend Param Optimization — The $3,505 Crown Jewel

Full param sweep on RAVE 30d data to find optimal supertrend params.
Optimizes for Sharpe ratio (risk-adjusted return), not just PnL.

Params tested:
- Period: 5, 7, 10, 14, 20, 25
- Multiplier: 1.0, 1.5, 2.0, 2.5, 3.0, 3.5, 4.0
- TP: 5%, 8%, 10%, 15%, 20%
- SL: 0%, 2%, 3%, 5%
- Max Hold: 12, 24, 36, 48

Total: 6 * 7 * 5 * 4 * 4 = 3,360 combos
"""

import json
import math
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from itertools import product

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from coinbase_advanced_client import CoinbaseAdvancedClient


def compute_supertrend(candles, period, multiplier):
    """Compute supertrend for all candles. Returns list of (line, trend)."""
    if len(candles) < period + 1:
        return []

    trs = []
    for i in range(1, len(candles)):
        h = float(candles[i]["high"])
        l = float(candles[i]["low"])
        c_prev = float(candles[i-1]["close"])
        tr = max(h - l, abs(h - c_prev), abs(l - c_prev))
        trs.append(tr)

    results = []
    trend = "bullish"
    trend_line = 0

    for i in range(len(trs)):
        if i < period - 1:
            results.append((None, None))
            continue

        atr = sum(trs[i-period+1:i+1]) / period
        mid = (float(candles[i+1]["high"]) + float(candles[i+1]["low"])) / 2
        upper = mid + multiplier * atr
        lower = mid - multiplier * atr
        close = float(candles[i+1]["close"])

        if close > upper:
            trend = "bullish"
            trend_line = lower
        elif close < lower:
            trend = "bearish"
            trend_line = upper

        results.append((trend_line, trend))

    return results


def simulate(candles, period, multiplier, tp_pct, sl_pct, max_hold):
    """Simulate supertrend on candles with given params. Returns trade results and metrics."""
    st_results = compute_supertrend(candles, period, multiplier)

    cash = 48.0
    pos = None
    trades = []
    equity_curve = [cash]

    for i in range(len(candles)):
        close = float(candles[i]["close"])
        high = float(candles[i]["high"])
        low = float(candles[i]["low"])
        candle_open = float(candles[i]["open"])

        if candle_open <= 0:
            continue

        # Check supertrend signal
        signal = False
        if i < len(st_results) and st_results[i][1] is not None:
            current_trend = st_results[i][1]
            # Check for trend flip or continuation
            if current_trend == "bullish":
                # Check if flipped recently
                for j in range(max(0, i-3), i):
                    if j < len(st_results) and st_results[j][1] == "bearish":
                        signal = True
                        break
                if not signal and i > 0:
                    prev_close = float(candles[i-1]["close"])
                    if close > prev_close:
                        signal = True

        # Exit position
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

            if exit_price is not None:
                net = (exit_price - pos["ep"]) * pos["units"] - pos["entry_fee"] - exit_price * pos["units"] * 0.004
                cash += pos["q"] + net
                trades.append(net)
                equity_curve.append(cash)
                pos = None

        # Enter position
        if pos is None and signal:
            entry_fee = cash * 0.004
            deploy = cash * 0.9  # 90% deploy
            if deploy < 2:
                continue
            units = (deploy - entry_fee) / candle_open
            tp = candle_open * (1 + tp_pct / 100)
            sl = candle_open * (1 - sl_pct / 100) if sl_pct > 0 else 0
            cash -= deploy
            pos = {
                "ep": candle_open, "q": deploy, "hold": 0,
                "tp": tp, "sl": sl, "units": units,
                "entry_fee": entry_fee, "max_hold": max_hold,
            }

    # Close remaining
    if pos and candles:
        last_close = float(candles[-1]["close"])
        net = (last_close - pos["ep"]) * pos["units"] - pos["entry_fee"] - last_close * pos["units"] * 0.004
        cash += pos["q"] + net
        trades.append(net)
        equity_curve.append(cash)

    # Compute metrics
    if not trades:
        return {
            "total_trades": 0, "total_pnl": 0, "win_rate": 0,
            "max_dd_pct": 0, "sharpe": 0, "profit_factor": 0,
            "final_equity": cash, "calmar": 0,
        }

    wins = [t for t in trades if t > 0]
    losses = [t for t in trades if t < 0]
    win_rate = len(wins) / len(trades) * 100

    # Max drawdown
    peak = 48.0
    max_dd = 0
    for e in equity_curve:
        if e > peak:
            peak = e
        dd = (peak - e) / peak if peak > 0 else 0
        max_dd = max(max_dd, dd)

    # Sharpe
    if len(trades) > 1:
        mean_ret = sum(trades) / len(trades)
        std_ret = math.sqrt(sum((t - mean_ret)**2 for t in trades) / len(trades))
        sharpe = mean_ret / std_ret if std_ret > 0 else 0
    else:
        sharpe = 0

    # Profit factor
    gross_profit = sum(wins) if wins else 0
    gross_loss = abs(sum(losses)) if losses else 0
    profit_factor = gross_profit / gross_loss if gross_loss > 0 else float("inf")

    # Calmar
    total_pnl = sum(trades)
    calmar = (total_pnl / 48) / max_dd if max_dd > 0 else 0

    return {
        "total_trades": len(trades),
        "total_pnl": round(total_pnl, 2),
        "win_rate": round(win_rate, 1),
        "max_dd_pct": round(max_dd * 100, 1),
        "sharpe": round(sharpe, 2),
        "profit_factor": round(profit_factor, 2) if profit_factor != float("inf") else 999,
        "final_equity": round(cash, 2),
        "calmar": round(calmar, 2),
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
    print(f"RAVE SUPERTREND PARAM OPTIMIZATION")
    print(f"Started: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}")
    print(f"{'='*70}\n")

    client = CoinbaseAdvancedClient()
    now = int(time.time())
    start_ts = now - 30 * 86400

    print("Fetching RAVE-USD 30d candles...")
    candles = fetch_candles(client, "RAVE-USD", start_ts, now)
    print(f"  RAVE-USD: {len(candles)} candles (30d)\n")

    periods = [5, 7, 10, 14, 20, 25]
    multipliers = [1.0, 1.5, 2.0, 2.5, 3.0, 3.5, 4.0]
    tps = [5, 8, 10, 15, 20]
    sls = [0, 2, 3, 5]
    max_holds = [12, 24, 36, 48]

    combos = list(product(periods, multipliers, tps, sls, max_holds))
    print(f"Testing {len(combos)} param combos on RAVE 30d...\n")

    results = []
    for idx, (p, m, tp, sl, mh) in enumerate(combos):
        metrics = simulate(candles, p, m, tp, sl, mh)
        metrics["params"] = {
            "period": p, "multiplier": m, "tp_pct": tp, "sl_pct": sl, "max_hold": mh,
        }
        results.append(metrics)

        if (idx + 1) % 500 == 0:
            elapsed = time.time() - start_time
            print(f"  Progress: {idx+1}/{len(combos)} ({elapsed:.0f}s)")

    # Sort by Sharpe (risk-adjusted)
    results_by_sharpe = sorted(results, key=lambda x: x["sharpe"], reverse=True)
    results_by_pnl = sorted(results, key=lambda x: x["total_pnl"], reverse=True)
    results_by_calmar = sorted(results, key=lambda x: x["calmar"], reverse=True)

    print(f"\n{'='*70}")
    print(f"OPTIMIZATION COMPLETE in {time.time() - start_time:.0f}s")
    print(f"{'='*70}\n")

    print(f"  TOP 5 BY SHARPE RATIO (Risk-Adjusted Return):")
    print(f"  {'Period':<8} {'Mult':<6} {'TP%':<5} {'SL%':<5} {'MH':<5} {'PnL':<10} {'WR%':<6} {'DD%':<6} {'Sharpe':<8} {'PF':<6}")
    print(f"  {'-'*70}")
    for r in results_by_sharpe[:5]:
        p = r["params"]
        print(f"  {p['period']:<8} {p['multiplier']:<6} {p['tp_pct']:<5} {p['sl_pct']:<5} {p['max_hold']:<5} ${r['total_pnl']:>8.0f}  {r['win_rate']:>5.1f}%  {r['max_dd_pct']:>5.1f}%  {r['sharpe']:>6.2f}  {r['profit_factor']:>5.1f}")

    print(f"\n  TOP 5 BY TOTAL PnL:")
    for r in results_by_pnl[:5]:
        p = r["params"]
        print(f"  {p['period']:<8} {p['multiplier']:<6} {p['tp_pct']:<5} {p['sl_pct']:<5} {p['max_hold']:<5} ${r['total_pnl']:>8.0f}  {r['win_rate']:>5.1f}%  {r['max_dd_pct']:>5.1f}%  {r['sharpe']:>6.2f}  {r['profit_factor']:>5.1f}")

    print(f"\n  TOP 5 BY CALMAR RATIO (Return / Max DD):")
    for r in results_by_calmar[:5]:
        p = r["params"]
        print(f"  {p['period']:<8} {p['multiplier']:<6} {p['tp_pct']:<5} {p['sl_pct']:<5} {p['max_hold']:<5} ${r['total_pnl']:>8.0f}  {r['win_rate']:>5.1f}%  {r['max_dd_pct']:>5.1f}%  {r['calmar']:>6.2f}")

    # Best overall (balanced: high PnL + low DD + good Sharpe)
    # Score = PnL * Sharpe / max_dd (higher is better)
    for r in results:
        if r["max_dd_pct"] > 0:
            r["score"] = r["total_pnl"] * r["sharpe"] / r["max_dd_pct"]
        else:
            r["score"] = 0
    results_by_score = sorted(results, key=lambda x: x["score"], reverse=True)

    print(f"\n  BEST BALANCED (PnL * Sharpe / DD):")
    for r in results_by_score[:5]:
        p = r["params"]
        print(f"  {p['period']:<8} {p['multiplier']:<6} {p['tp_pct']:<5} {p['sl_pct']:<5} {p['max_hold']:<5} ${r['total_pnl']:>8.0f}  {r['win_rate']:>5.1f}%  {r['max_dd_pct']:>5.1f}%  {r['sharpe']:>6.2f}  Score: {r['score']:.1f}")

    # Save results
    output = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "elapsed_seconds": round(time.time() - start_time, 1),
        "coin": "RAVE-USD",
        "candles": len(candles),
        "combos_tested": len(combos),
        "top_by_sharpe": results_by_sharpe[:10],
        "top_by_pnl": results_by_pnl[:10],
        "top_by_calmar": results_by_calmar[:10],
        "top_balanced": results_by_score[:10],
        "recommendation": {
            "by_sharpe": results_by_sharpe[0]["params"] if results_by_sharpe else None,
            "by_pnl": results_by_pnl[0]["params"] if results_by_pnl else None,
            "balanced": results_by_score[0]["params"] if results_by_score else None,
        }
    }

    out_path = Path(__file__).parent.parent / "reports" / "rave_supertrend_optimization.json"
    out_path.parent.mkdir(exist_ok=True)
    with open(out_path, "w") as f:
        json.dump(output, f, indent=2)

    print(f"\n  Report saved: {out_path}\n")
    print(f"{'='*70}\n")


if __name__ == "__main__":
    main()
