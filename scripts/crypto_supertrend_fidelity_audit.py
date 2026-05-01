#!/usr/bin/env python3
"""Crypto Supertrend Fidelity Audit — RAVE, IOTX, TRU, BAL

Mirrors the MT5 fidelity-audit pattern (naive / spread_adjusted / slippage_adjusted /
no_same_bar) but runs against Coinbase altcoin data instead of MetaTrader5.

Why:  The SL=5% deployment for RAVE and BAL was tuned on *naive* backtest numbers
that under-count spread and allow same-bar round-trips.  If the edge disappears after
fidelity adjustment we need to know before compounding more capital.

Usage:
    python scripts/crypto_supertrend_fidelity_audit.py
    python scripts/crypto_supertrend_fidelity_audit.py --coins RAVE-USD BAL-USD --days 60
    python scripts/crypto_supertrend_fidelity_audit.py --spread-pct 0.3
"""
from __future__ import annotations

import argparse
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
# Supertrend coin configs  (matching live multi_coin_isolated_runner defaults)
# ---------------------------------------------------------------------------
COIN_CONFIGS = {
    "RAVE-USD": {"atr_period": 10, "atr_mult": 3.0, "tp_pct": 10.0, "sl_pct": 5.0, "max_hold": 48},
    "IOTX-USD": {"atr_period": 10, "atr_mult": 3.0, "tp_pct": 10.0, "sl_pct": 3.0, "max_hold": 48},
    "TRU-USD":  {"atr_period": 10, "atr_mult": 3.0, "tp_pct": 10.0, "sl_pct": 3.0, "max_hold": 48},
    "BAL-USD":  {"atr_period": 10, "atr_mult": 3.0, "tp_pct": 10.0, "sl_pct": 5.0, "max_hold": 96},
}

# Typical crypto spread as % of mid (Coinbase altcoins are wider than forex)
DEFAULT_SPREAD_PCT = {
    "RAVE-USD": 0.002,   # 0.2 %
    "IOTX-USD": 0.0015,  # 0.15 %
    "TRU-USD":  0.002,   # 0.2 %
    "BAL-USD":  0.0015,  # 0.15 %
}

SESSION_DEAD_HOURS = {0, 6, 12, 19}
FEE_RATE = 0.004
STARTING_CASH = 100.0


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


def supertrend_entry(candles_hist: list[dict], closes: list[float], _candle: dict, params: dict) -> bool:
    """Enter long when price closes above the supertrend line and is rising."""
    atr_period = params.get("atr_period", 10)
    atr_mult = params.get("atr_mult", 3.0)
    st = compute_supertrend_line(candles_hist, atr_period, atr_mult)
    if st is None:
        return False
    return len(closes) > 0 and closes[-1] > st


# ===================================================================
# Fidelity modes
# ===================================================================

def run_backtest(
    candles: list[dict],
    params: dict,
    *,
    mode: str,
    spread_pct: float,
    slippage_pct: float = 0.001,  # 0.1 % per trade default
    seed: int = 42,
) -> dict:
    """Backtest one coin under one fidelity mode.

    Modes:
        naive             — current backtest: spread from DEFAULT_SPREAD_PCT (already baked in),
                            same-bar round-trips allowed, no extra slippage.
        spread_adjusted   — double the spread cost on every round-trip (entry + exit both
                            pay the spread, not just one side).
        slippage_adjusted  — add a per-trade slippage penalty (ATR-fraction based).
        no_same_bar       — block entry/exit on the same bar index (enforce 1-bar separation).
    """
    rng = random.Random(seed)
    cash = STARTING_CASH
    pos: dict | None = None
    trades: list[float] = []
    equity_curve = [cash]
    peak_equity = cash
    max_dd = 0.0
    wins = 0
    losses = 0
    signals = 0
    signals_filtered_session = 0
    signals_filtered_fill = 0
    same_bar_blocked = 0
    total_spread_paid = 0.0
    total_slippage_paid = 0.0

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

            if high >= pos["tp"]:
                exit_price = pos["tp"]
            elif pos["sl"] > 0 and low <= pos["sl"]:
                exit_price = pos["sl"]
            elif pos["hold"] >= pos["max_hold"]:
                exit_price = close

            if exit_price is not None:
                # no_same_bar: block if entered on same bar
                if mode == "no_same_bar" and pos["entry_bar"] == i:
                    same_bar_blocked += 1
                    # Don't exit — keep position, try next bar
                    pos["hold"] -= 1  # undo the increment
                    continue

                # Spread-adjusted: exit fills one spread worse
                effective_exit = exit_price
                if mode == "spread_adjusted":
                    spread_deduction = effective_exit * spread_pct  # extra spread cost
                    effective_exit -= spread_deduction
                    total_spread_paid += spread_deduction * pos["units"]

                # Slippage-adjusted: extra slippage
                if mode == "slippage_adjusted":
                    # Estimate ATR-based slippage
                    atr_val = 0.0
                    if len(candles_hist) > atr_period:
                        trs = []
                        for j in range(max(1, len(candles_hist) - atr_period), len(candles_hist)):
                            ch = candles_hist[j]
                            cp = candles_hist[j - 1]
                            trs.append(max(
                                float(ch["high"]) - float(ch["low"]),
                                abs(float(ch["high"]) - float(cp["close"])),
                                abs(float(ch["low"]) - float(cp["close"])),
                            ))
                        atr_val = sum(trs) / len(trs) if trs else 0.0
                    slip_px = max(atr_val * 0.1, effective_exit * slippage_pct)
                    effective_exit -= slip_px
                    total_slippage_paid += slip_px * pos["units"]

                units = pos["units"]
                gross = (effective_exit - pos["ep"]) * units
                entry_fee = pos["entry_fee"]
                exit_fee = effective_exit * units * FEE_RATE
                net = gross - entry_fee - exit_fee

                cash += pos["q"] + net
                trades.append(net)
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
                # Deterministic fill
                if rng.random() < 0.9:
                    # Spread-adjusted: entry fills one spread worse
                    effective_entry = candle_open
                    if mode == "spread_adjusted":
                        effective_entry += effective_entry * spread_pct
                        total_spread_paid += effective_entry * spread_pct * (STARTING_CASH / effective_entry)

                    deploy = cash * 0.9
                    entry_fee = deploy * FEE_RATE
                    units = (deploy - entry_fee) / effective_entry
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
            elif not session_open:
                signals_filtered_session += 1

        # Equity curve
        if pos is not None:
            floating = (close - pos["ep"]) * pos["units"]
            equity_curve.append(cash + pos["q"] + floating)
        else:
            equity_curve.append(cash)

        # Max drawdown
        eq = equity_curve[-1]
        if eq > peak_equity:
            peak_equity = eq
        dd = (peak_equity - eq) / peak_equity if peak_equity > 0 else 0.0
        if dd > max_dd:
            max_dd = dd

    # Close remaining at last close
    if pos is not None:
        last_close = float(candles[-1]["close"])
        effective_exit = last_close
        if mode == "spread_adjusted":
            effective_exit -= effective_exit * spread_pct
            total_spread_paid += effective_exit * spread_pct * pos["units"]
        if mode == "slippage_adjusted":
            atr_val = 0.0
            if len(candles_hist) > atr_period:
                trs = []
                for j in range(max(1, len(candles_hist) - atr_period), len(candles_hist)):
                    ch = candles_hist[j]
                    cp = candles_hist[j - 1]
                    trs.append(max(
                        float(ch["high"]) - float(ch["low"]),
                        abs(float(ch["high"]) - float(cp["close"])),
                        abs(float(ch["low"]) - float(cp["close"])),
                    ))
                atr_val = sum(trs) / len(trs) if trs else 0.0
            slip_px = max(atr_val * 0.1, effective_exit * slippage_pct)
            effective_exit -= slip_px
            total_slippage_paid += slip_px * pos["units"]

        units = pos["units"]
        gross = (effective_exit - pos["ep"]) * units
        exit_fee = effective_exit * units * FEE_RATE
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
    if total_trades > 1 and len(trades) > 1:
        mean_ret = total_pnl / total_trades
        std_ret = math.sqrt(sum((t - mean_ret) ** 2 for t in trades) / total_trades)
        sharpe = mean_ret / std_ret if std_ret > 0 else 0.0
    else:
        sharpe = 0.0

    # Profit factor
    gross_profit = sum(t for t in trades if t > 0)
    gross_loss = abs(sum(t for t in trades if t < 0))
    profit_factor = gross_profit / gross_loss if gross_loss > 0 else float("inf")

    final_equity = cash
    roi_pct = (final_equity - STARTING_CASH) / STARTING_CASH * 100

    return {
        "mode": mode,
        "final_equity": round(final_equity, 2),
        "total_pnl": round(total_pnl, 2),
        "roi_pct": round(roi_pct, 2),
        "total_trades": total_trades,
        "wins": wins,
        "losses": losses,
        "win_rate": round(win_rate, 1),
        "avg_pnl": round(avg_pnl, 3),
        "sharpe": round(sharpe, 3),
        "profit_factor": round(profit_factor, 3) if profit_factor != float("inf") else 999.0,
        "max_drawdown_pct": round(max_dd * 100, 1),
        "signals": signals,
        "signals_filtered_fill": signals_filtered_fill,
        "signals_filtered_session": signals_filtered_session,
        "same_bar_blocked": same_bar_blocked,
        "total_spread_paid": round(total_spread_paid, 4),
        "total_slippage_paid": round(total_slippage_paid, 4),
    }


# ===================================================================
# Main
# ===================================================================

def main():
    parser = argparse.ArgumentParser(description="Crypto supertrend fidelity audit")
    parser.add_argument("--coins", nargs="*", default=None, help="Specific coins to audit")
    parser.add_argument("--days", type=int, default=60, help="Days of historical data")
    parser.add_argument("--spread-pct", type=float, default=None, help="Override spread %% for all coins")
    args = parser.parse_args()

    coins = args.coins or list(COIN_CONFIGS.keys())
    days = args.days
    modes = ["naive", "spread_adjusted", "slippage_adjusted", "no_same_bar"]

    client = CoinbaseAdvancedClient()

    print(f"\n{'=' * 100}")
    print(f"  CRYPTO SUPERTREND FIDELITY AUDIT")
    print(f"  Coins: {', '.join(coins)}")
    print(f"  Days of data: {days}")
    print(f"  Modes: {', '.join(modes)}")
    print(f"  Started: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}")
    print(f"{'=' * 100}\n")

    # Fetch candles
    all_candles: dict[str, list[dict]] = {}
    for coin in coins:
        print(f"  Fetching {coin} ({days}d)...", end=" ", flush=True)
        candles = fetch_candles(client, coin, days)
        all_candles[coin] = candles
        print(f"{len(candles)} candles", flush=True)

    # Get live spread info
    live_spreads: dict[str, float] = {}
    for coin in coins:
        try:
            ticker = client.public_exchange_ticker(coin)
            mid = ticker.price
            bid = ticker.bid_price
            ask = ticker.ask_price
            spread = ask - bid
            spread_pct_live = (spread / mid * 100) if mid > 0 else 0
            live_spreads[coin] = spread_pct_live
            print(f"  {coin} live spread: {spread:.6f} ({spread_pct_live:.3f}%)  bid={bid} ask={ask}", flush=True)
        except Exception as e:
            spread_pct_default = DEFAULT_SPREAD_PCT.get(coin, 0.002) * 100
            live_spreads[coin] = spread_pct_default
            print(f"  {coin} spread: could not fetch live ticker ({e}), using default {spread_pct_default:.3f}%", flush=True)

    print()

    # Run audit
    all_results: dict[str, list[dict]] = {}
    fidelity_gaps: list[dict] = []

    for coin in coins:
        candles = all_candles[coin]
        cfg = COIN_CONFIGS[coin]
        # spread_pct_val should be a fraction (e.g. 0.002 = 0.2%), not a percentage
        if args.spread_pct is not None:
            # If user passes --spread-pct 0.2, treat as percentage and convert
            spread_pct_val = args.spread_pct / 100.0 if args.spread_pct > 1 else args.spread_pct
        else:
            # Use live spread percentage from ticker, convert to fraction
            live_spread_pct = live_spreads.get(coin, 0)
            # If live spread seems reasonable (< 10%), use it; otherwise fall back to default
            if live_spread_pct > 0 and live_spread_pct < 10:
                spread_pct_val = live_spread_pct / 100.0
            else:
                spread_pct_val = DEFAULT_SPREAD_PCT.get(coin, 0.002)

        print(f"  {'=' * 100}")
        print(f"  {coin}  bars={len(candles)}  spread={spread_pct_val * 100:.3f}%  params={cfg}")
        print(f"  {'=' * 100}")

        coin_results: list[dict] = []
        naive_result = None

        for mode in modes:
            r = run_backtest(candles, cfg, mode=mode, spread_pct=spread_pct_val)
            coin_results.append(r)

            marker = ""
            if mode == "spread_adjusted":
                marker = f"  (spread cost: ${r['total_spread_paid']:.2f})"
            elif mode == "slippage_adjusted":
                marker = f"  (slippage cost: ${r['total_slippage_paid']:.2f})"
            elif mode == "no_same_bar":
                marker = f"  (same-bar blocked: {r['same_bar_blocked']})"

            print(
                f"    {mode:<22s}  equity=${r['final_equity']:>+10.2f}  "
                f"PnL=${r['total_pnl']:>+10.2f}  "
                f"ROI={r['roi_pct']:>+7.2f}%  "
                f"trades={r['total_trades']:>5}  "
                f"WR={r['win_rate']:>5.1f}%  "
                f"Sharpe={r['sharpe']:>6.3f}  "
                f"MaxDD={r['max_drawdown_pct']:>5.1f}%{marker}"
            )

            if mode == "naive":
                naive_result = r

        all_results[coin] = coin_results

        # Compute gaps
        if naive_result is None:
            continue
        for r in coin_results:
            if r["mode"] == "naive":
                continue
            pnl_gap = r["total_pnl"] - naive_result["total_pnl"]
            pnl_gap_pct = (pnl_gap / abs(naive_result["total_pnl"]) * 100) if naive_result["total_pnl"] != 0 else 0.0
            wr_gap = r["win_rate"] - naive_result["win_rate"]
            edge_survival = (r["total_pnl"] / naive_result["total_pnl"] * 100) if naive_result["total_pnl"] != 0 else 0.0
            dd_gap = r["max_drawdown_pct"] - naive_result["max_drawdown_pct"]

            fidelity_gaps.append({
                "coin": coin,
                "mode": r["mode"],
                "naive_pnl": naive_result["total_pnl"],
                "adjusted_pnl": r["total_pnl"],
                "naive_wr": naive_result["win_rate"],
                "adjusted_wr": r["win_rate"],
                "naive_sharpe": naive_result["sharpe"],
                "adjusted_sharpe": r["sharpe"],
                "naive_roi": naive_result["roi_pct"],
                "adjusted_roi": r["roi_pct"],
                "pnl_gap_usd": round(pnl_gap, 2),
                "pnl_gap_pct": round(pnl_gap_pct, 1),
                "wr_gap_pp": round(wr_gap, 1),
                "dd_gap_pp": round(dd_gap, 1),
                "edge_survival_pct": round(edge_survival, 1),
                "spread_cost": r["total_spread_paid"],
                "slippage_cost": r["total_slippage_paid"],
                "same_bar_blocked": r["same_bar_blocked"],
            })

    # Edge verdict
    print(f"\n  {'=' * 100}")
    print(f"  EDGE VERDICTS (naive vs spread-adjusted)")
    print(f"  {'=' * 100}\n")

    rankings: list[dict] = []
    for coin in coins:
        naive_r = next((r for r in all_results.get(coin, []) if r["mode"] == "naive"), None)
        spread_r = next((r for r in all_results.get(coin, []) if r["mode"] == "spread_adjusted"), None)
        if naive_r is None or spread_r is None:
            continue

        naive_pos = naive_r["total_pnl"] > 0
        spread_pos = spread_r["total_pnl"] > 0
        sharpe_positive = spread_r["sharpe"] > 0

        if naive_pos and spread_pos and sharpe_positive:
            verdict = "EDGE SURVIVES — positive PnL and Sharpe after spread adjustment"
        elif naive_pos and not spread_pos:
            verdict = "EDGE ERASED — naive positive PnL turns negative after spread adjustment"
        elif naive_pos and spread_pos and not sharpe_positive:
            verdict = "MARGINAL — PnL positive but risk-adjusted Sharpe is negative"
        else:
            verdict = "NO EDGE — naive was already negative or near-zero"

        gap = next((g for g in fidelity_gaps if g["coin"] == coin and g["mode"] == "spread_adjusted"), None)
        edge_surv = gap["edge_survival_pct"] if gap else 0.0

        print(f"  {coin:<12s}  naive=${naive_r['total_pnl']:>+10.2f}  spread-adj=${spread_r['total_pnl']:>+10.2f}  "
              f"survival={edge_surv:>6.1f}%  {verdict}")

        rankings.append({
            "coin": coin,
            "naive_pnl": naive_r["total_pnl"],
            "spread_adj_pnl": spread_r["total_pnl"],
            "naive_sharpe": naive_r["sharpe"],
            "spread_adj_sharpe": spread_r["sharpe"],
            "edge_survival_pct": edge_surv,
            "verdict": verdict,
        })

    rankings.sort(key=lambda x: x["edge_survival_pct"], reverse=True)

    print(f"\n  {'=' * 100}")
    print(f"  RANKINGS by edge survival (spread-adjusted)")
    print(f"  {'=' * 100}\n")
    print(f"  {'Rank':>4s}  {'Coin':<12s}  {'Naive PnL':>12s}  {'Spread-Adj PnL':>14s}  {'Survival%':>10s}")
    print(f"  {'-' * 60}")
    for i, r in enumerate(rankings, 1):
        print(f"  {i:>4}  {r['coin']:<12s}  ${r['naive_pnl']:>+11.2f}  ${r['spread_adj_pnl']:>+13.2f}  {r['edge_survival_pct']:>9.1f}%")

    # Save reports
    output = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "days": days,
        "coins": coins,
        "live_spreads_pct": {c: round(live_spreads.get(c, 0), 4) for c in coins},
        "all_results": all_results,
        "fidelity_gaps": fidelity_gaps,
        "rankings": rankings,
    }

    json_path = REPORTS / "crypto_supertrend_fidelity_audit.json"
    REPORTS.mkdir(exist_ok=True)
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(output, f, indent=2, default=str)
    print(f"\n  JSON report: {json_path}")

    # Text report
    txt_lines = []
    txt_lines.append("=" * 100)
    txt_lines.append("  CRYPTO SUPERTREND FIDELITY AUDIT")
    txt_lines.append(f"  Timestamp: {output['timestamp']}")
    txt_lines.append(f"  Days: {days}")
    txt_lines.append(f"  Coins: {', '.join(coins)}")
    txt_lines.append(f"  Live Spreads: {', '.join(f'{c}: {live_spreads.get(c, 0):.3f}%' for c in coins)}")
    txt_lines.append("=" * 100)
    txt_lines.append("")

    # Per-coin detail
    for coin in coins:
        results = all_results.get(coin, [])
        txt_lines.append(f"  {'=' * 100}")
        txt_lines.append(f"  {coin}")
        txt_lines.append(f"  {'=' * 100}")
        txt_lines.append("")
        txt_lines.append(
            f"  {'Mode':<22s}  {'Equity':>12s}  {'Total PnL':>12s}  {'ROI%':>8s}  "
            f"{'Trades':>7s}  {'WR%':>5s}  {'Sharpe':>8s}  {'MaxDD%':>7s}  {'PF':>6s}"
        )
        txt_lines.append("-" * 100)
        for r in results:
            extras = ""
            if r["mode"] == "spread_adjusted" and r["total_spread_paid"] != 0:
                extras = f"  spread=${r['total_spread_paid']:.2f}"
            elif r["mode"] == "slippage_adjusted" and r["total_slippage_paid"] != 0:
                extras = f"  slip=${r['total_slippage_paid']:.2f}"
            elif r["mode"] == "no_same_bar" and r["same_bar_blocked"] > 0:
                extras = f"  blocked={r['same_bar_blocked']}"
            txt_lines.append(
                f"  {r['mode']:<22s}  ${r['final_equity']:>+11.2f}  ${r['total_pnl']:>+11.2f}  "
                f"{r['roi_pct']:>+7.2f}%  {r['total_trades']:>7}  "
                f"{r['win_rate']:>4.1f}%  {r['sharpe']:>7.3f}  "
                f"{r['max_drawdown_pct']:>6.1f}%  {r['profit_factor']:>5.1f}{extras}"
            )
        txt_lines.append("")

    # Fidelity gaps
    txt_lines.append(f"  {'=' * 100}")
    txt_lines.append("  FIDELITY GAPS (naive -> adjusted)")
    txt_lines.append(f"  {'=' * 100}")
    txt_lines.append("")
    txt_lines.append(
        f"  {'Coin':<12s}  {'Mode':<20s}  {'Naive $':>10s}  {'Adj $':>10s}  "
        f"{'Gap $':>10s}  {'Gap %':>6s}  {'Edge Surv%':>10s}  {'Verdict':>30s}"
    )
    txt_lines.append("-" * 100)
    for g in fidelity_gaps:
        coin = g["coin"]
        rank_info = next((r for r in rankings if r["coin"] == coin), None)
        verdict = rank_info["verdict"][:30] if rank_info else ""
        txt_lines.append(
            f"  {g['coin']:<12s}  {g['mode']:<20s}  ${g['naive_pnl']:>+9.2f}  "
            f"${g['adjusted_pnl']:>+9.2f}  ${g['pnl_gap_usd']:>+9.2f}  "
            f"{g['pnl_gap_pct']:>+5.1f}%  {g['edge_survival_pct']:>9.1f}%  {verdict}"
        )
    txt_lines.append("")

    # Rankings
    txt_lines.append(f"  {'=' * 100}")
    txt_lines.append("  RANKINGS by edge survival (spread-adjusted)")
    txt_lines.append(f"  {'=' * 100}")
    txt_lines.append("")
    txt_lines.append(
        f"  {'Rank':>4s}  {'Coin':<12s}  {'Naive PnL':>12s}  {'Spread-Adj PnL':>14s}  "
        f"{'Survival%':>10s}  {'Naive Sharpe':>12s}  {'Adj Sharpe':>10s}  {'Verdict'}"
    )
    txt_lines.append("-" * 120)
    for i, r in enumerate(rankings, 1):
        txt_lines.append(
            f"  {i:>4}  {r['coin']:<12s}  ${r['naive_pnl']:>+11.2f}  ${r['spread_adj_pnl']:>+13.2f}  "
            f"{r['edge_survival_pct']:>9.1f}%  {r['naive_sharpe']:>11.3f}  "
            f"{r['spread_adj_sharpe']:>9.3f}  {r['verdict']}"
        )
    txt_lines.append("")
    txt_lines.append("=" * 100)
    txt_lines.append("  AUDIT COMPLETE")
    txt_lines.append("=" * 100)
    txt_lines.append("")

    txt_path = REPORTS / "crypto_supertrend_fidelity_audit.txt"
    with open(txt_path, "w", encoding="utf-8") as f:
        f.write("\n".join(txt_lines))
    print(f"  Text report: {txt_path}")

    print(f"\n  Done. {'=' * 100}\n")


if __name__ == "__main__":
    main()
