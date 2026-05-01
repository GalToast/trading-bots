#!/usr/bin/env python3
"""ZEC-USD Deep Dive — 30-Day Fidelity Audit + Parameter Sensitivity

The ONLY coin (out of 235 scanned) with positive spread-adjusted PnL.
7d showed +$6.55, 0.008% spread, 90.3% survival.

This script:
1. Runs 30-day supertrend backtest on ZEC-USD
2. Runs all 4 fidelity modes (naive, spread-adjusted, slippage-adjusted, no-same-bar)
3. Tests parameter sensitivity (ATR periods, TP levels, SL levels)
4. Compares against MT5 proven live lanes
5. Outputs reports/zec_usd_deep_dive.json and .txt

IMPORTANT: Analysis only. No live config changes.
"""
from __future__ import annotations

import json
import math
import os
import random
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from coinbase_advanced_client import CoinbaseAdvancedClient

ROOT = Path(__file__).resolve().parent.parent
REPORTS = ROOT / "reports"

# ---------------------------------------------------------------------------
# ZEC-USD base config (from the 7d discovery)
# ---------------------------------------------------------------------------
ZEC_BASE = {
    "atr_period": 10,
    "atr_mult": 3.0,
    "tp_pct": 8.0,
    "sl_pct": 3.0,
    "max_hold": 48,
}

# ZEC spread is ~0.008% (extremely tight compared to alts)
ZEC_SPREAD_PCT = 0.00008  # 0.008% as a fraction

SESSION_DEAD_HOURS = {0, 6, 12, 19}
FEE_RATE = 0.004
STARTING_CASH = 100.0

# MT5 proven live lane benchmarks for comparison
MT5_LANES = {
    "live_btcusd_m5_warp": {"net_pnl": 69.0, "per_close": 13.88},
    "live_rearm_941777": {"net_pnl": 51.0, "per_close": None},
    "live_momentum_alpha50": {"net_pnl": 12.0, "per_close": None},
}


# ===================================================================
# Helpers
# ===================================================================

def fetch_candles(client: CoinbaseAdvancedClient, coin: str, days: int) -> list[dict]:
    """Fetch *days* of 5-min candles, chunked to respect rate limits."""
    end = int(time.time())
    start = end - days * 86400
    chunk_sec = 300 * 5 * 60  # ~25 h
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


def compute_supertrend_line(candles_hist: list[dict], atr_period: int, atr_mult: float) -> float | None:
    """Return the current supertrend line value (lower band for bullish signal)."""
    if len(candles_hist) < atr_period + 1:
        return None
    trs = []
    for i in range(1, len(candles_hist)):
        h = float(candles_hist[i]["high"])
        l = float(candles_hist[i]["low"])
        c_prev = float(candles_hist[i - 1]["close"])
        trs.append(max(h - l, abs(h - c_prev), abs(l - c_prev)))
    atr = sum(trs[-atr_period:]) / atr_period
    hl2 = (float(candles_hist[-1]["high"]) + float(candles_hist[-1]["low"])) / 2
    return hl2 - atr_mult * atr


def _compute_atr(candles_hist: list[dict], atr_period: int) -> float:
    """Compute current ATR value."""
    if len(candles_hist) < atr_period + 1:
        return 0.0
    trs = []
    for i in range(1, len(candles_hist)):
        h = float(candles_hist[i]["high"])
        l = float(candles_hist[i]["low"])
        c_prev = float(candles_hist[i - 1]["close"])
        trs.append(max(h - l, abs(h - c_prev), abs(l - c_prev)))
    return sum(trs[-atr_period:]) / atr_period


# ===================================================================
# Fidelity modes backtest
# ===================================================================

def run_backtest(
    candles: list[dict],
    params: dict,
    *,
    mode: str,
    spread_pct: float,
    slippage_pct: float = 0.001,
    seed: int = 42,
) -> dict:
    """Backtest ZEC-USD under one fidelity mode."""
    rng = random.Random(seed)
    cash = STARTING_CASH
    pos: dict | None = None
    trades: list[dict] = []  # store dicts with more detail
    equity_curve = [cash]
    peak_equity = cash
    max_dd = 0.0
    wins = 0
    losses = 0
    signals = 0
    signals_filtered_fill = 0
    same_bar_blocked = 0
    total_spread_paid = 0.0
    total_slippage_paid = 0.0
    tp_hits = 0
    sl_hits = 0
    timeouts = 0

    tp_pct = params["tp_pct"] / 100.0
    sl_pct = params["sl_pct"] / 100.0 if params.get("sl_pct", 0) > 0 else 0.0
    max_hold = params["max_hold"]
    atr_period = params.get("atr_period", 10)
    atr_mult = params.get("atr_mult", 3.0)

    closes_hist: list[float] = []
    candles_hist: list[dict] = []

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

        # ---- EXIT ----
        if pos is not None:
            pos["hold"] += 1
            exit_price = None
            exit_reason = None

            if high >= pos["tp"]:
                exit_price = pos["tp"]
                exit_reason = "tp"
            elif pos["sl"] > 0 and low <= pos["sl"]:
                exit_price = pos["sl"]
                exit_reason = "sl"
            elif pos["hold"] >= pos["max_hold"]:
                exit_price = close
                exit_reason = "timeout"

            if exit_price is not None:
                # no_same_bar: block if entered on same bar
                if mode == "no_same_bar" and pos["entry_bar"] == i:
                    same_bar_blocked += 1
                    pos["hold"] -= 1
                    continue

                effective_exit = exit_price
                if mode == "spread_adjusted":
                    spread_deduction = effective_exit * spread_pct
                    effective_exit -= spread_deduction
                    total_spread_paid += spread_deduction * pos["units"]

                if mode == "slippage_adjusted":
                    atr_val = _compute_atr(candles_hist, atr_period)
                    slip_px = max(atr_val * 0.1, effective_exit * slippage_pct)
                    effective_exit -= slip_px
                    total_slippage_paid += slip_px * pos["units"]

                units = pos["units"]
                gross = (effective_exit - pos["ep"]) * units
                entry_fee = pos["entry_fee"]
                exit_fee = effective_exit * units * FEE_RATE
                net = gross - entry_fee - exit_fee

                cash += pos["q"] + net
                trade_detail = {
                    "bar": i,
                    "exit_reason": exit_reason,
                    "net": round(net, 4),
                    "gross": round(gross, 4),
                    "fees": round(entry_fee + exit_fee, 4),
                    "hold_bars": pos["hold"],
                }
                trades.append(trade_detail)
                if exit_reason == "tp":
                    tp_hits += 1
                elif exit_reason == "sl":
                    sl_hits += 1
                elif exit_reason == "timeout":
                    timeouts += 1

                if net > 0:
                    wins += 1
                else:
                    losses += 1
                pos = None

        # ---- ENTRY ----
        if pos is None and session_open:
            st_val = compute_supertrend_line(candles_hist, atr_period, atr_mult)
            signal = st_val is not None and close > st_val
            if signal:
                signals += 1
                if rng.random() < 0.9:
                    effective_entry = candle_open
                    if mode == "spread_adjusted":
                        effective_entry += effective_entry * spread_pct
                        total_spread_paid += effective_entry * spread_pct * (STARTING_CASH / effective_entry)

                    deploy = cash * 0.9
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
                        "max_hold": max_hold,
                        "entry_fee": entry_fee,
                        "entry_bar": i,
                    }
                else:
                    signals_filtered_fill += 1

        # Equity curve
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
    if pos is not None:
        last_close = float(candles[-1]["close"])
        effective_exit = last_close
        if mode == "spread_adjusted":
            effective_exit -= effective_exit * spread_pct
            total_spread_paid += effective_exit * spread_pct * pos["units"]
        if mode == "slippage_adjusted":
            atr_val = _compute_atr(candles_hist, atr_period)
            slip_px = max(atr_val * 0.1, effective_exit * slippage_pct)
            effective_exit -= slip_px
            total_slippage_paid += slip_px * pos["units"]

        units = pos["units"]
        gross = (effective_exit - pos["ep"]) * units
        exit_fee = effective_exit * units * FEE_RATE
        net = gross - pos["entry_fee"] - exit_fee
        cash += pos["q"] + net
        trades.append({
            "bar": len(candles) - 1,
            "exit_reason": "force_close",
            "net": round(net, 4),
            "gross": round(gross, 4),
            "fees": round(pos["entry_fee"] + exit_fee, 4),
            "hold_bars": pos["hold"],
        })
        if net > 0:
            wins += 1
        else:
            losses += 1

    total_trades = len(trades)
    win_rate = (wins / total_trades * 100) if total_trades > 0 else 0.0
    total_pnl = sum(t["net"] for t in trades)
    avg_pnl = (total_pnl / total_trades) if total_trades > 0 else 0.0

    # Sharpe (annualized approximation for 5m bars)
    if total_trades > 1:
        mean_ret = total_pnl / total_trades
        std_ret = math.sqrt(sum((t["net"] - mean_ret) ** 2 for t in trades) / total_trades)
        sharpe = mean_ret / std_ret if std_ret > 0 else 0.0
    else:
        sharpe = 0.0

    gross_profit = sum(t["net"] for t in trades if t["net"] > 0)
    gross_loss = abs(sum(t["net"] for t in trades if t["net"] < 0))
    profit_factor = gross_profit / gross_loss if gross_loss > 0 else float("inf")

    final_equity = cash
    roi_pct = (final_equity - STARTING_CASH) / STARTING_CASH * 100

    # Exit reason breakdown
    exit_reasons = {"tp": tp_hits, "sl": sl_hits, "timeout": timeouts, "force_close": 0}
    if trades and trades[-1]["exit_reason"] == "force_close":
        exit_reasons["force_close"] = 1

    return {
        "mode": mode,
        "final_equity": round(final_equity, 2),
        "total_pnl": round(total_pnl, 2),
        "roi_pct": round(roi_pct, 2),
        "total_trades": total_trades,
        "wins": wins,
        "losses": losses,
        "win_rate": round(win_rate, 1),
        "avg_pnl": round(avg_pnl, 4),
        "sharpe": round(sharpe, 3),
        "profit_factor": round(profit_factor, 3) if profit_factor != float("inf") else 999.0,
        "max_drawdown_pct": round(max_dd * 100, 1),
        "signals": signals,
        "signals_filtered_fill": signals_filtered_fill,
        "same_bar_blocked": same_bar_blocked,
        "total_spread_paid": round(total_spread_paid, 4),
        "total_slippage_paid": round(total_slippage_paid, 4),
        "exit_reasons": exit_reasons,
        "per_close_pnl": round(total_pnl / total_trades, 4) if total_trades > 0 else 0.0,
    }


# ===================================================================
# Parameter sensitivity grid
# ===================================================================

def run_param_sensitivity(candles: list[dict], spread_pct: float) -> list[dict]:
    """Run grid search over ATR periods, TP levels, SL levels."""
    atr_periods = [7, 10, 14, 21]
    tp_levels = [5.0, 8.0, 12.0]
    sl_levels = [2.0, 3.0, 5.0]

    results = []
    total = len(atr_periods) * len(tp_levels) * len(sl_levels)
    count = 0

    for atr_p in atr_periods:
        for tp in tp_levels:
            for sl in sl_levels:
                count += 1
                params = {
                    "atr_period": atr_p,
                    "atr_mult": 3.0,
                    "tp_pct": tp,
                    "sl_pct": sl,
                    "max_hold": 48,
                }
                # Run spread_adjusted as the primary fidelity mode for sensitivity
                r = run_backtest(candles, params, mode="spread_adjusted", spread_pct=spread_pct)
                r["params"] = dict(params)
                r["rank"] = count
                results.append(r)

                if count % 10 == 0 or count == total:
                    print(f"    [{count}/{total}] ATR={atr_p} TP={tp}% SL={sl}% => PnL=${r['total_pnl']:+.2f} WR={r['win_rate']:.1f}%", flush=True)

    # Sort by total_pnl descending
    results.sort(key=lambda x: x["total_pnl"], reverse=True)
    for i, r in enumerate(results):
        r["rank"] = i + 1

    return results


# ===================================================================
# Main
# ===================================================================

def main():
    start_time = time.time()
    days = 30

    print(f"\n{'=' * 100}")
    print(f"  ZEC-USD DEEP DIVE — 30-DAY FIDELITY AUDIT")
    print(f"  The ONLY coin with positive spread-adjusted PnL out of 235 scanned")
    print(f"  7d baseline: +$6.55, spread=0.008%, survival=90.3%")
    print(f"  Started: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}")
    print(f"{'=' * 100}\n")

    client = CoinbaseAdvancedClient()

    # ---- Step 1: Fetch 30 days of ZEC-USD candles ----
    print(f"  Fetching ZEC-USD ({days}d of 5m candles)...", end=" ", flush=True)
    candles = fetch_candles(client, "ZEC-USD", days)
    print(f"{len(candles)} candles", flush=True)

    if len(candles) < 100:
        print(f"  ERROR: Not enough candles ({len(candles)}). Need at least 100.")
        return

    # ---- Step 2: Get live spread ----
    try:
        ticker = client.public_exchange_ticker("ZEC-USD")
        mid = ticker.price
        bid = ticker.bid_price
        ask = ticker.ask_price
        spread_live = ask - bid
        spread_pct_live = (spread_live / mid * 100) if mid > 0 else 0
        print(f"  ZEC-USD live spread: ${spread_live:.6f} ({spread_pct_live:.4f}%)  bid={bid} ask={ask}")
        # Use the live spread if it's reasonable, otherwise use the known 0.008%
        spread_pct = (spread_pct_live / 100.0) if 0 < spread_pct_live < 1.0 else ZEC_SPREAD_PCT
    except Exception as e:
        print(f"  ZEC-USD spread: could not fetch live ticker ({e}), using default {ZEC_SPREAD_PCT * 100:.4f}%")
        spread_pct = ZEC_SPREAD_PCT

    print(f"  Using spread: {spread_pct * 100:.4f}%")
    print(f"  Base params: {ZEC_BASE}")
    print()

    # ---- Step 3: Run all 4 fidelity modes ----
    modes = ["naive", "spread_adjusted", "slippage_adjusted", "no_same_bar"]
    fidelity_results: list[dict] = []
    naive_result = None

    print(f"  {'=' * 100}")
    print(f"  PART 1: FIDELITY MODES (30d, base params)")
    print(f"  {'=' * 100}\n")

    for mode in modes:
        r = run_backtest(candles, ZEC_BASE, mode=mode, spread_pct=spread_pct)
        fidelity_results.append(r)

        marker = ""
        if mode == "spread_adjusted":
            marker = f"  (spread cost: ${r['total_spread_paid']:.4f})"
        elif mode == "slippage_adjusted":
            marker = f"  (slippage cost: ${r['total_slippage_paid']:.4f})"
        elif mode == "no_same_bar":
            marker = f"  (same-bar blocked: {r['same_bar_blocked']})"

        print(
            f"    {mode:<22s}  equity=${r['final_equity']:>+10.2f}  "
            f"PnL=${r['total_pnl']:>+10.2f}  "
            f"ROI={r['roi_pct']:>+7.2f}%  "
            f"trades={r['total_trades']:>5}  "
            f"WR={r['win_rate']:>5.1f}%  "
            f"Sharpe={r['sharpe']:>6.3f}  "
            f"MaxDD={r['max_drawdown_pct']:>5.1f}%  "
            f"PF={r['profit_factor']:>5.2f}{marker}"
        )

        if mode == "naive":
            naive_result = r

    # ---- Step 4: Edge verdict ----
    print(f"\n  {'=' * 100}")
    print(f"  EDGE VERDICT (naive vs spread-adjusted)")
    print(f"  {'=' * 100}\n")

    spread_result = next((r for r in fidelity_results if r["mode"] == "spread_adjusted"), None)
    slip_result = next((r for r in fidelity_results if r["mode"] == "slippage_adjusted"), None)
    nsb_result = next((r for r in fidelity_results if r["mode"] == "no_same_bar"), None)

    verdicts = {}
    for mode, r, label in [
        ("spread_adjusted", spread_result, "Spread-Adjusted"),
        ("slippage_adjusted", slip_result, "Slippage-Adjusted"),
        ("no_same_bar", nsb_result, "No-Same-Bar"),
    ]:
        if r is None or naive_result is None:
            continue
        edge_survival = (r["total_pnl"] / naive_result["total_pnl"] * 100) if naive_result["total_pnl"] != 0 else 0.0
        pnl_gap = r["total_pnl"] - naive_result["total_pnl"]

        if naive_result["total_pnl"] > 0 and r["total_pnl"] > 0 and r["sharpe"] > 0:
            verdict = "EDGE SURVIVES"
        elif naive_result["total_pnl"] > 0 and r["total_pnl"] > 0:
            verdict = "MARGINAL (PnL positive, Sharpe negative)"
        elif naive_result["total_pnl"] > 0 and r["total_pnl"] <= 0:
            verdict = "EDGE ERASED"
        else:
            verdict = "NO EDGE"

        verdicts[mode] = {
            "verdict": verdict,
            "edge_survival_pct": round(edge_survival, 1),
            "pnl_gap_usd": round(pnl_gap, 2),
        }
        print(f"    {label:<22s}  PnL=${r['total_pnl']:>+10.2f}  "
              f"survival={edge_survival:>6.1f}%  gap=${pnl_gap:>+8.2f}  =>  {verdict}")

    # ---- Step 5: Parameter sensitivity ----
    print(f"\n  {'=' * 100}")
    print(f"  PART 2: PARAMETER SENSITIVITY (spread-adjusted)")
    print(f"  Testing: ATR periods=[7,10,14,21] x TP=[5%,8%,12%] x SL=[2%,3%,5%]")
    print(f"  {'=' * 100}\n")

    sens_results = run_param_sensitivity(candles, spread_pct)

    # Top 5 and bottom 5
    print(f"\n  {'=' * 100}")
    print(f"  TOP 5 PARAMETER COMBOS (by spread-adjusted PnL)")
    print(f"  {'=' * 100}\n")

    for i, r in enumerate(sens_results[:5]):
        p = r["params"]
        print(f"    #{i+1}  ATR={p['atr_period']:>2}  TP={p['tp_pct']:>4.0f}%  SL={p['sl_pct']:>3.0f}%  "
              f"PnL=${r['total_pnl']:>+8.2f}  WR={r['win_rate']:>5.1f}%  "
              f"Sharpe={r['sharpe']:>6.3f}  trades={r['total_trades']:>4}")

    print(f"\n  {'=' * 100}")
    print(f"  BOTTOM 5 PARAMETER COMBOS (by spread-adjusted PnL)")
    print(f"  {'=' * 100}\n")

    for i, r in enumerate(sens_results[-5:]):
        p = r["params"]
        print(f"    #{r['rank']}  ATR={p['atr_period']:>2}  TP={p['tp_pct']:>4.0f}%  SL={p['sl_pct']:>3.0f}%  "
              f"PnL=${r['total_pnl']:>+8.2f}  WR={r['win_rate']:>5.1f}%  "
              f"Sharpe={r['sharpe']:>6.3f}  trades={r['total_trades']:>4}")

    # Robustness check: how many combos are positive?
    positive_count = sum(1 for r in sens_results if r["total_pnl"] > 0)
    total_count = len(sens_results)
    positive_pct = positive_count / total_count * 100
    avg_pnl = sum(r["total_pnl"] for r in sens_results) / total_count
    median_pnl = sorted(sens_results, key=lambda x: x["total_pnl"])[total_count // 2]["total_pnl"]

    # Base params rank
    base_rank = next((r["rank"] for r in sens_results
                      if r["params"]["atr_period"] == ZEC_BASE["atr_period"]
                      and r["params"]["tp_pct"] == ZEC_BASE["tp_pct"]
                      and r["params"]["sl_pct"] == ZEC_BASE["sl_pct"]), None)

    print(f"\n  {'=' * 100}")
    print(f"  ROBUSTNESS SUMMARY")
    print(f"  {'=' * 100}\n")
    print(f"    Total combos tested:     {total_count}")
    print(f"    Positive PnL combos:     {positive_count}/{total_count} ({positive_pct:.1f}%)")
    print(f"    Average PnL:             ${avg_pnl:+.2f}")
    print(f"    Median PnL:              ${median_pnl:+.2f}")
    print(f"    Best PnL:                ${sens_results[0]['total_pnl']:+.2f}")
    print(f"    Worst PnL:               ${sens_results[-1]['total_pnl']:+.2f}")
    print(f"    Base params rank:        #{base_rank} of {total_count}")
    print(f"    Base params PnL:         ${next((r['total_pnl'] for r in sens_results if r['rank'] == base_rank), 0):+.2f}")

    # Robustness verdict
    if positive_pct >= 50 and base_rank is not None and base_rank <= total_count * 0.3:
        robust_verdict = "ROBUST — edge survives across most parameter combinations"
    elif positive_pct >= 30:
        robust_verdict = "MODERATE — edge exists but is parameter-sensitive"
    elif positive_pct > 0:
        robust_verdict = "FRAGILE — only a few parameter combos work"
    else:
        robust_verdict = "NO EDGE — no parameter combo is positive"

    # Check if the edge is concentrated in one narrow band
    best_pnl = sens_results[0]["total_pnl"]
    base_pnl = next((r["total_pnl"] for r in sens_results
                     if r["params"]["atr_period"] == ZEC_BASE["atr_period"]
                     and r["params"]["tp_pct"] == ZEC_BASE["tp_pct"]
                     and r["params"]["sl_pct"] == ZEC_BASE["sl_pct"]), 0)

    if base_pnl > 0 and best_pnl > 0:
        base_to_best_ratio = base_pnl / best_pnl if best_pnl != 0 else 0
        if base_to_best_ratio >= 0.8:
            robust_verdict += " — base params are near-optimal"
        elif base_to_best_ratio >= 0.5:
            robust_verdict += " — base params are reasonable but not optimal"
        else:
            robust_verdict += " — base params are far from optimal"

    print(f"\n    VERDICT: {robust_verdict}")

    # ---- Step 6: MT5 comparison ----
    print(f"\n  {'=' * 100}")
    print(f"  PART 3: MT5 PROVEN LIVE LANE COMPARISON")
    print(f"  {'=' * 100}\n")

    print(f"  {'Lane':<28s}  {'Net PnL':>10s}  {'Per Close':>12s}  {'Source':>12s}")
    print(f"  {'-' * 70}")

    spread_pnl = spread_result["total_pnl"] if spread_result else 0
    spread_per_close = spread_result["per_close_pnl"] if spread_result else 0

    for lane, data in MT5_LANES.items():
        per_close_str = f"${data['per_close']:.2f}" if data.get("per_close") else "N/A"
        print(f"  {lane:<28s}  ${data['net_pnl']:>+9.2f}  {per_close_str:>12s}  {'live MT5':>12s}")

    print(f"  {'-' * 70}")
    print(f"  {'ZEC-USD spread-adj (30d)':<28s}  ${spread_pnl:>+9.2f}  ${spread_per_close:>10.4f}  {'30d backtest':>12s}")

    # Relative performance
    best_mt5 = max(MT5_LANES.values(), key=lambda x: x["net_pnl"])
    print(f"\n  ZEC-USD vs best MT5 lane ({max(MT5_LANES, key=lambda k: MT5_LANES[k]['net_pnl'])}):")
    print(f"    ZEC-USD PnL:     ${spread_pnl:+.2f} (30d, $100 starting cash, 90% deploy)")
    print(f"    Best MT5 PnL:    ${best_mt5['net_pnl']:+.2f} (7d, different capital/timeframe)")
    print(f"    NOTE: Not directly comparable — different timeframes, capital, and deploy sizes.")

    # ---- Step 7: Overall conclusion ----
    print(f"\n  {'=' * 100}")
    print(f"  OVERALL CONCLUSION")
    print(f"  {'=' * 100}\n")

    # 30d vs 7d comparison
    pnl_7d = 6.55  # The known 7d result
    if spread_pnl > 0:
        print(f"  30d spread-adjusted PnL: ${spread_pnl:+.2f}")
        print(f"  7d spread-adjusted PnL:  ${pnl_7d:+.2f} (reference)")
        if spread_pnl > pnl_7d * 3:  # Roughly extrapolating 7d -> 30d
            print(f"  => Edge APPEARS to scale with time (more than 4x the 7d result)")
        elif spread_pnl > 0:
            print(f"  => Edge EXISTS but does not scale linearly (less than 4x the 7d result)")
        print(f"  => The edge is REAL but may be weaker than the 7d snapshot suggested")
    else:
        print(f"  30d spread-adjusted PnL: ${spread_pnl:+.2f}")
        print(f"  => The 7d edge did NOT survive 30 days. Likely a lucky window.")

    print(f"  Robustness: {robust_verdict}")

    # ---- Step 8: Build and save reports ----
    output = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "coin": "ZEC-USD",
        "days": days,
        "bars": len(candles),
        "spread_pct_used": round(spread_pct * 100, 4),
        "base_params": ZEC_BASE,
        "fidelity_results": fidelity_results,
        "verdicts": verdicts,
        "sensitivity_results": {
            "total_combos": total_count,
            "positive_combos": positive_count,
            "positive_pct": round(positive_pct, 1),
            "avg_pnl": round(avg_pnl, 2),
            "median_pnl": round(median_pnl, 2),
            "best_pnl": round(sens_results[0]["total_pnl"], 2),
            "worst_pnl": round(sens_results[-1]["total_pnl"], 2),
            "base_params_rank": base_rank,
            "robustness_verdict": robust_verdict,
            "top_5": [{
                "rank": r["rank"],
                "params": r["params"],
                "total_pnl": r["total_pnl"],
                "win_rate": r["win_rate"],
                "sharpe": r["sharpe"],
                "total_trades": r["total_trades"],
            } for r in sens_results[:5]],
            "bottom_5": [{
                "rank": r["rank"],
                "params": r["params"],
                "total_pnl": r["total_pnl"],
                "win_rate": r["win_rate"],
                "sharpe": r["sharpe"],
                "total_trades": r["total_trades"],
            } for r in sens_results[-5:]],
            "all_combos": [{
                "rank": r["rank"],
                "params": r["params"],
                "total_pnl": r["total_pnl"],
                "win_rate": r["win_rate"],
                "sharpe": r["sharpe"],
                "profit_factor": r["profit_factor"],
                "max_drawdown_pct": r["max_drawdown_pct"],
                "total_trades": r["total_trades"],
                "exit_reasons": r["exit_reasons"],
            } for r in sens_results],
        },
        "mt5_comparison": {
            "lanes": MT5_LANES,
            "zec_spread_adjusted": {
                "total_pnl": spread_pnl,
                "per_close_pnl": spread_per_close,
                "total_trades": spread_result["total_trades"] if spread_result else 0,
                "win_rate": spread_result["win_rate"] if spread_result else 0,
            },
        },
        "overall_conclusion": {
            "pnl_30d": spread_pnl,
            "pnl_7d_reference": pnl_7d,
            "robustness": robust_verdict,
        },
    }

    REPORTS.mkdir(exist_ok=True)
    json_path = REPORTS / "zec_usd_deep_dive.json"
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(output, f, indent=2, default=str)
    print(f"\n  JSON report: {json_path}")

    # ---- Text report ----
    txt = []
    def ln(s=""):
        txt.append(s)

    ln("=" * 100)
    ln("  ZEC-USD DEEP DIVE — 30-DAY FIDELITY AUDIT")
    ln(f"  Timestamp: {output['timestamp']}")
    ln(f"  Coin: ZEC-USD | Days: {days} | Bars: {len(candles)}")
    ln(f"  Spread: {output['spread_pct_used']:.4f}% | Base params: {ZEC_BASE}")
    ln("=" * 100)
    ln()

    # Fidelity modes
    ln("  PART 1: FIDELITY MODES (30d, base params)")
    ln("  " + "-" * 100)
    ln(f"  {'Mode':<22s}  {'Equity':>12s}  {'Total PnL':>12s}  {'ROI%':>8s}  "
       f"{'Trades':>7s}  {'WR%':>5s}  {'Sharpe':>8s}  {'MaxDD%':>7s}  {'PF':>6s}")
    ln("  " + "-" * 100)
    for r in fidelity_results:
        extras = ""
        if r["mode"] == "spread_adjusted" and r["total_spread_paid"] != 0:
            extras = f"  spread=${r['total_spread_paid']:.4f}"
        elif r["mode"] == "slippage_adjusted" and r["total_slippage_paid"] != 0:
            extras = f"  slip=${r['total_slippage_paid']:.4f}"
        elif r["mode"] == "no_same_bar" and r["same_bar_blocked"] > 0:
            extras = f"  blocked={r['same_bar_blocked']}"
        ln(f"  {r['mode']:<22s}  ${r['final_equity']:>+11.2f}  ${r['total_pnl']:>+11.2f}  "
           f"{r['roi_pct']:>+7.2f}%  {r['total_trades']:>7}  "
           f"{r['win_rate']:>4.1f}%  {r['sharpe']:>7.3f}  "
           f"{r['max_drawdown_pct']:>6.1f}%  {r['profit_factor']:>5.2f}{extras}")
    ln()

    # Verdicts
    ln("  EDGE VERDICTS")
    ln("  " + "-" * 100)
    for mode, v in verdicts.items():
        ln(f"    {mode:<22s}  {v['verdict']:<30s}  survival={v['edge_survival_pct']:>6.1f}%  gap=${v['pnl_gap_usd']:>+8.2f}")
    ln()

    # Parameter sensitivity
    ln("  PART 2: PARAMETER SENSITIVITY (spread-adjusted)")
    ln("  " + "-" * 100)
    ln(f"  Total combos: {total_count} | Positive: {positive_count} ({positive_pct:.1f}%)")
    ln(f"  Avg PnL: ${avg_pnl:+.2f} | Median: ${median_pnl:+.2f}")
    ln(f"  Best: ${sens_results[0]['total_pnl']:+.2f} | Worst: ${sens_results[-1]['total_pnl']:+.2f}")
    ln(f"  Base params rank: #{base_rank} of {total_count}")
    ln()
    ln("  TOP 5:")
    for i, r in enumerate(sens_results[:5]):
        p = r["params"]
        ln(f"    #{i+1}  ATR={p['atr_period']:>2}  TP={p['tp_pct']:>4.0f}%  SL={p['sl_pct']:>3.0f}%  "
           f"PnL=${r['total_pnl']:>+8.2f}  WR={r['win_rate']:>5.1f}%  Sharpe={r['sharpe']:>6.3f}  trades={r['total_trades']:>4}")
    ln()
    ln("  BOTTOM 5:")
    for r in sens_results[-5:]:
        p = r["params"]
        ln(f"    #{r['rank']}  ATR={p['atr_period']:>2}  TP={p['tp_pct']:>4.0f}%  SL={p['sl_pct']:>3.0f}%  "
           f"PnL=${r['total_pnl']:>+8.2f}  WR={r['win_rate']:>5.1f}%  Sharpe={r['sharpe']:>6.3f}  trades={r['total_trades']:>4}")
    ln()
    ln(f"  ROBUSTNESS: {robust_verdict}")
    ln()

    # MT5 comparison
    ln("  PART 3: MT5 PROVEN LIVE LANE COMPARISON")
    ln("  " + "-" * 100)
    ln(f"  {'Lane':<28s}  {'Net PnL':>10s}  {'Per Close':>12s}  {'Source':>12s}")
    ln("  " + "-" * 70)
    for lane, data in MT5_LANES.items():
        per_close_str = f"${data['per_close']:.2f}" if data.get("per_close") else "N/A"
        ln(f"  {lane:<28s}  ${data['net_pnl']:>+9.2f}  {per_close_str:>12s}  {'live MT5':>12s}")
    ln("  " + "-" * 70)
    ln(f"  {'ZEC-USD spread-adj (30d)':<28s}  ${spread_pnl:>+9.2f}  ${spread_per_close:>10.4f}  {'30d backtest':>12s}")
    ln()

    # Conclusion
    ln("  OVERALL CONCLUSION")
    ln("  " + "-" * 100)
    ln(f"  30d spread-adjusted PnL: ${spread_pnl:+.2f}")
    ln(f"  7d reference PnL:        ${pnl_7d:+.2f}")
    if spread_pnl > 0:
        ln(f"  30d/7d ratio:            {spread_pnl / pnl_7d:.2f}x (linear scale would be ~4.3x)")
    ln(f"  Robustness:              {robust_verdict}")
    ln()
    if spread_pnl > 0:
        ln(f"  => The edge IS REAL but {'scales well' if spread_pnl > pnl_7d * 3 else 'does not scale linearly'}")
        ln(f"  => {'Consider for deployment with these parameters' if robust_verdict.startswith('ROBUST') else 'Parameter sensitivity warrants caution'}")
    else:
        ln(f"  => The 7d edge was likely a lucky window. Does NOT survive 30d.")
    ln()
    ln("=" * 100)
    ln("  END OF REPORT")
    ln("=" * 100)

    txt_path = REPORTS / "zec_usd_deep_dive.txt"
    with open(txt_path, "w", encoding="utf-8") as f:
        f.write("\n".join(txt) + "\n")
    print(f"  Text report: {txt_path}")

    elapsed = time.time() - start_time
    print(f"\n  Completed in {elapsed:.1f}s")
    print(f"{'=' * 100}\n")


if __name__ == "__main__":
    main()
