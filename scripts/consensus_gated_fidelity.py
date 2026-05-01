#!/usr/bin/env python3
"""
Consensus-Gated Fidelity Audit
===============================

Answers: Does gating fibonacci entries on fibonacci+momentum consensus make
the edge MORE resilient to spread costs?

Compares:
  1. Single-strategy fibonacci (ungated) -- spread-adjusted
  2. Consensus-gated fibonacci (fib+momentum must agree, gate=2) -- spread-adjusted

For NOM-USD and GHST-USD.

The consensus gate is a QUALITY filter: only enter when fib fires AND
momentum also fires at the same candle. This should reduce false signals
and improve spread-adjusted retention.

Usage:
    python scripts/consensus_gated_fidelity.py
    python scripts/consensus_gated_fidelity.py --days 30
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
REPORTS.mkdir(exist_ok=True)

# ---------------------------------------------------------------------------
# Strategy configs
# ---------------------------------------------------------------------------
COIN_CONFIG = {
    "NOM-USD": {
        "strategy": "fibonacci",
        "fib_lookback": 20,
        "tp_pct": 0.08,
        "sl_pct": 0.03,
        "max_hold": 24,
        "spread_pct": 0.002,
    },
    "GHST-USD": {
        "strategy": "fibonacci",
        "fib_lookback": 10,
        "tp_pct": 0.08,
        "sl_pct": 0.03,
        "max_hold": 96,
        "spread_pct": 0.003,
    },
}

FEE_RATE = 0.004
STARTING_CASH = 100.0
DEPLOY_FRACTION = 0.90

# Per-coin top-6 profitable hours
PER_COIN_HOURS = {
    "NOM-USD":  {1, 4, 5, 8, 10, 11},
    "GHST-USD": {2, 3, 4, 5, 7, 18},
}

# Momentum config
MOMENTUM_LOOKBACK = 20
MOMENTUM_THRESHOLD_PCT = 0.005
MOMENTUM_VOLUME_MULT = 0.5


# ===================================================================
# Data fetching
# ===================================================================

def fetch_candles(client, coin: str, days: int) -> list[dict]:
    """Fetch 5-min candles from Coinbase."""
    end = int(time.time())
    start = end - days * 86400
    chunk_sec = 300 * 5 * 60
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

def signal_fibonacci(candles_hist, fib_lookback):
    """Fibonacci breakout signal -- matches consensus engine + fidelity audit."""
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
    if breakout_pct < 0.02:
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


def signal_momentum(candles_hist, lookback=MOMENTUM_LOOKBACK):
    """Momentum breakout -- matches consensus engine."""
    if len(candles_hist) < lookback + 2:
        return False
    recent = candles_hist[-(lookback):-1]
    if not recent:
        return False
    recent_high = max(float(c["high"]) for c in recent)
    current_high = float(candles_hist[-1]["high"])
    breakout = (current_high - recent_high) / recent_high if recent_high > 0 else 0
    if breakout < MOMENTUM_THRESHOLD_PCT:
        return False
    # Volume confirmation
    if len(candles_hist) >= 20:
        volumes = [float(c.get("volume", 0)) for c in candles_hist[-20:]]
        avg_vol = sum(volumes) / len(volumes) if volumes else 0
        cur_vol = float(candles_hist[-1].get("volume", 0))
        if avg_vol > 0 and cur_vol < avg_vol * MOMENTUM_VOLUME_MULT:
            return False
    return True


# ===================================================================
# Backtest engine with consensus gate and spread adjustment
# ===================================================================

def run_backtest(candles, cfg, *, consensus_gate=0, spread_pct=0.0,
                 top_hours=None, seed=42):
    """Run backtest with optional consensus gating and spread adjustment.

    Args:
        candles: list of candle dicts
        cfg: coin config dict
        consensus_gate: 0 = no gate (fib alone), 2 = fib+momentum must agree
        spread_pct: spread as fraction of mid price
        top_hours: set of UTC hours to trade (None = all hours)
        seed: random seed for fill simulation

    Returns:
        dict with full results
    """
    rng = random.Random(seed)

    cash = STARTING_CASH
    pos = None
    trades = []
    equity_curve = [cash]
    peak_eq = cash
    max_dd = 0.0
    signals_fired = 0
    signals_gated = 0
    entries_taken = 0
    total_spread_cost = 0.0
    total_fees = 0.0
    per_hour_pnl = {}

    strat = cfg["strategy"]
    fib_lookback = cfg.get("fib_lookback", 20)
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
            session_open = True

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
                effective_exit = exit_px

                # Spread-adjusted: exit fills one spread worse (bid side)
                if spread_pct > 0:
                    spread_ded = effective_exit * spread_pct
                    effective_exit -= spread_ded
                    total_spread_cost += spread_ded * pos["units"]

                units = pos["units"]
                gross = (effective_exit - pos["ep"]) * units
                exit_fee = effective_exit * units * FEE_RATE
                net = gross - pos["entry_fee"] - exit_fee
                total_fees += pos["entry_fee"] + exit_fee

                cash += pos["q"] + net
                trades.append(net)

                eh = pos.get("entry_hour", hour)
                if eh not in per_hour_pnl:
                    per_hour_pnl[eh] = []
                per_hour_pnl[eh].append(net)

                pos = None

        # ---- ENTRY ----
        if pos is None and session_open:
            fib_fires = signal_fibonacci(candles_hist, fib_lookback)
            mom_fires = signal_momentum(candles_hist)

            if fib_fires:
                signals_fired += 1

                # Consensus gate check
                if consensus_gate > 0:
                    # Need fib + momentum to agree
                    if not mom_fires:
                        signals_gated += 1
                        continue

                # Deterministic fill
                if rng.random() < 0.9:
                    entries_taken += 1
                    effective_entry = open_px
                    deploy = cash * DEPLOY_FRACTION

                    # Spread-adjusted: entry fills one spread worse (ask side)
                    if spread_pct > 0:
                        effective_entry += effective_entry * spread_pct
                        entry_spread_cost = effective_entry * spread_pct * (deploy / effective_entry if effective_entry > 0 else 0)
                        total_spread_cost += entry_spread_cost

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

        # Equity tracking
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

    # Close remaining position
    if pos is not None and len(candles) > 0:
        last_close = float(candles[-1]["close"])
        effective_exit = last_close
        if spread_pct > 0:
            spread_ded = effective_exit * spread_pct
            effective_exit -= spread_ded
            total_spread_cost += spread_ded * pos["units"]
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

    total_trades = len(trades)
    total_wins = sum(1 for t in trades if t > 0)
    total_losses = total_trades - total_wins
    pnl = cash - STARTING_CASH
    avg_pnl = pnl / total_trades if total_trades > 0 else 0

    # Sharpe
    if total_trades > 1:
        mean_ret = pnl / total_trades
        std_ret = math.sqrt(sum((t - mean_ret) ** 2 for t in trades) / total_trades)
        sharpe = mean_ret / std_ret if std_ret > 0 else 0.0
    else:
        sharpe = 0.0

    # Per-hour summary
    hour_summary = {}
    for h, pnls in sorted(per_hour_pnl.items()):
        hour_summary[str(h)] = {
            "trades": len(pnls),
            "total_pnl": round(sum(pnls), 4),
            "avg_pnl": round(sum(pnls) / len(pnls), 4) if pnls else 0,
        }

    return {
        "consensus_gate": consensus_gate,
        "spread_pct": spread_pct,
        "pnl": round(pnl, 4),
        "final_equity": round(cash, 4),
        "roi_pct": round((cash - STARTING_CASH) / STARTING_CASH * 100, 4),
        "total_trades": total_trades,
        "wins": total_wins,
        "losses": total_losses,
        "win_rate_pct": round(total_wins / max(1, total_trades) * 100, 1),
        "avg_pnl_per_trade": round(avg_pnl, 4),
        "sharpe_ratio": round(sharpe, 4),
        "max_drawdown_pct": round(max_dd * 100, 2),
        "signals_fired": signals_fired,
        "signals_gated": signals_gated,
        "entries_taken": entries_taken,
        "gate_reduction_pct": round(signals_gated / max(1, signals_fired) * 100, 1),
        "total_spread_cost": round(total_spread_cost, 4),
        "total_fees": round(total_fees, 4),
        "hourly_summary": hour_summary,
    }


# ===================================================================
# Main
# ===================================================================

def main():
    import argparse
    parser = argparse.ArgumentParser(description="Consensus-Gated Fidelity Audit")
    parser.add_argument("--coins", nargs="+", default=list(COIN_CONFIG.keys()),
                        help="Coins to audit")
    parser.add_argument("--days", type=int, default=30, help="Days of data")
    parser.add_argument("--spread-multiplier", type=float, default=1.0,
                        help="Multiply default spreads")
    args = parser.parse_args()

    print("=" * 100)
    print("  CONSENSUS-GATED FIDELITY AUDIT")
    print("  Does fib+momentum consensus make the edge MORE resilient to spread costs?")
    print("=" * 100)
    print(f"  Coins: {', '.join(args.coins)}")
    print(f"  Days: {args.days}")
    print(f"  Spread multiplier: {args.spread_multiplier}x")
    print()

    client = CoinbaseAdvancedClient()
    has_api = client.has_auth()

    all_results = {}

    for coin in args.coins:
        cfg = COIN_CONFIG[coin]
        spread = cfg["spread_pct"] * args.spread_multiplier
        top_hours = PER_COIN_HOURS.get(coin)

        print(f"\n{'=' * 100}")
        print(f"  {coin} -- fibonacci (spread={spread*100:.2f}%, top hours={sorted(top_hours) if top_hours else 'ALL'})")
        print(f"{'=' * 100}")

        # Fetch candles
        candles = []
        if has_api:
            print(f"  Fetching {args.days}d candles from Coinbase...", flush=True)
            candles = fetch_candles(client, coin, args.days)
            print(f"  Got {len(candles)} candles.", flush=True)
        else:
            cache_file = ROOT / "data" / "candle_cache" / f"{coin.replace('-', '_')}_{args.days}d.json"
            if cache_file.exists():
                data = json.loads(cache_file.read_text())
                candles = data.get("candles", [])
                print(f"  Loaded {len(candles)} candles from cache.")
            else:
                print(f"  [WARN] No data for {coin}. Skipping.")
                all_results[coin] = {"error": "no_data"}
                continue

        if len(candles) < 200:
            print(f"  [WARN] Insufficient candles ({len(candles)}). Skipping.")
            all_results[coin] = {"error": f"insufficient_data: {len(candles)}"}
            continue

        # --- Run 4 scenarios per coin ---
        # 1. Single-strategy naive (no spread, no gate)
        print(f"\n  [1] Single-strategy NAIVE (no spread, no gate, top hours)")
        r1 = run_backtest(candles, cfg, consensus_gate=0, spread_pct=0.0, top_hours=top_hours)
        print(f"      PnL: ${r1['pnl']:+.2f} | Trades: {r1['total_trades']} | "
              f"WR: {r1['win_rate_pct']}% | Signals: {r1['signals_fired']}")

        # 2. Single-strategy spread-adjusted (no gate)
        print(f"\n  [2] Single-strategy SPREAD-ADJUSTED (no gate, top hours)")
        r2 = run_backtest(candles, cfg, consensus_gate=0, spread_pct=spread, top_hours=top_hours)
        print(f"      PnL: ${r2['pnl']:+.2f} | Trades: {r2['total_trades']} | "
              f"WR: {r2['win_rate_pct']}% | Spread cost: ${r2['total_spread_cost']:.2f}")

        # 3. Consensus-gated naive (gate=2, no spread)
        print(f"\n  [3] Consensus-gated NAIVE (fib+momentum, no spread, top hours)")
        r3 = run_backtest(candles, cfg, consensus_gate=2, spread_pct=0.0, top_hours=top_hours)
        print(f"      PnL: ${r3['pnl']:+.2f} | Trades: {r3['total_trades']} | "
              f"WR: {r3['win_rate_pct']}% | Gated: {r3['signals_gated']}/{r3['signals_fired']} "
              f"({r3['gate_reduction_pct']}% filtered)")

        # 4. Consensus-gated spread-adjusted (fib+momentum gate=2, with spread)
        print(f"\n  [4] Consensus-gated SPREAD-ADJUSTED (fib+momentum, top hours)")
        r4 = run_backtest(candles, cfg, consensus_gate=2, spread_pct=spread, top_hours=top_hours)
        print(f"      PnL: ${r4['pnl']:+.2f} | Trades: {r4['total_trades']} | "
              f"WR: {r4['win_rate_pct']}% | Spread cost: ${r4['total_spread_cost']:.2f}")

        # --- All-hours baseline for context ---
        print(f"\n  [5] Single-strategy SPREAD-ADJUSTED (ALL HOURS, no gate)")
        r5 = run_backtest(candles, cfg, consensus_gate=0, spread_pct=spread, top_hours=None)
        print(f"      PnL: ${r5['pnl']:+.2f} | Trades: {r5['total_trades']} | "
              f"WR: {r5['win_rate_pct']}%")

        # Also run consensus-gated all-hours
        print(f"\n  [6] Consensus-gated SPREAD-ADJUSTED (ALL HOURS, fib+momentum)")
        r6 = run_backtest(candles, cfg, consensus_gate=2, spread_pct=spread, top_hours=None)
        print(f"      PnL: ${r6['pnl']:+.2f} | Trades: {r6['total_trades']} | "
              f"WR: {r6['win_rate_pct']}% | Gated: {r6['signals_gated']}/{r6['signals_fired']} "
              f"({r6['gate_reduction_pct']}% filtered)")

        # --- Key analysis ---
        print(f"\n  {'=' * 70}")
        print(f"  KEY ANALYSIS: {coin}")
        print(f"  {'=' * 70}")

        # Spread retention: naive vs spread-adjusted
        naive_pnl = r1["pnl"]
        single_spread_pnl = r2["pnl"]
        consensus_naive_pnl = r3["pnl"]
        consensus_spread_pnl = r4["pnl"]
        all_hours_single_spread = r5["pnl"]
        all_hours_consensus_spread = r6["pnl"]

        if naive_pnl != 0:
            single_retention = single_spread_pnl / naive_pnl * 100
        else:
            single_retention = 0.0

        if consensus_naive_pnl != 0:
            consensus_retention = consensus_spread_pnl / consensus_naive_pnl * 100
        else:
            consensus_retention = 0.0

        print(f"  Single-strategy naive:           ${naive_pnl:+.2f}")
        print(f"  Single-strategy spread-adjusted:  ${single_spread_pnl:+.2f}  (retention: {single_retention:.0f}%)")
        print(f"  Spread cost delta:                ${single_spread_pnl - naive_pnl:+.2f}")
        print()
        print(f"  Consensus-gated naive:            ${consensus_naive_pnl:+.2f}")
        print(f"  Consensus-gated spread-adjusted:  ${consensus_spread_pnl:+.2f}  (retention: {consensus_retention:.0f}%)")
        print(f"  Spread cost delta:                ${consensus_spread_pnl - consensus_naive_pnl:+.2f}")
        print()
        print(f"  All-hours single spread-adjusted: ${all_hours_single_spread:+.2f}")
        print(f"  All-hours consensus spread-adj:   ${all_hours_consensus_spread:+.2f}")
        print()

        # The key comparison
        improvement = consensus_spread_pnl - single_spread_pnl
        if improvement > 0:
            print(f"  >> CONSENSUS GATE IMPROVES SPREAD-ADJUSTED PnL by ${improvement:+.2f}")
        else:
            print(f"  >> CONSENSUS GATE LOWERS ABSOLUTE SPREAD-ADJUSTED PnL by ${improvement:+.2f}")

        # Trade quality
        if r2["total_trades"] > 0:
            single_avg = r2["avg_pnl_per_trade"]
        else:
            single_avg = 0
        if r4["total_trades"] > 0:
            consensus_avg = r4["avg_pnl_per_trade"]
        else:
            consensus_avg = 0

        print(f"  Avg PnL/trade single:    ${single_avg:+.4f}")
        print(f"  Avg PnL/trade consensus: ${consensus_avg:+.4f}")
        print(f"  Win rate single:         {r2['win_rate_pct']}%")
        print(f"  Win rate consensus:      {r4['win_rate_pct']}%")
        print(f"  Sharpe single:           {r2['sharpe_ratio']:.4f}")
        print(f"  Sharpe consensus:        {r4['sharpe_ratio']:.4f}")
        print(f"  Max DD single:           {r2['max_drawdown_pct']:.2f}%")
        print(f"  Max DD consensus:        {r4['max_drawdown_pct']:.2f}%")
        print(f"  Trade count: {r2['total_trades']} -> {r4['total_trades']} "
              f"(reduction: {r2['total_trades'] - r4['total_trades']})")

        coin_results = {
            "coin": coin,
            "spread_pct": spread,
            "top_hours": sorted(top_hours) if top_hours else None,
            "scenarios": {
                "single_naive_top_hours": r1,
                "single_spread_top_hours": r2,
                "consensus_naive_top_hours": r3,
                "consensus_spread_top_hours": r4,
                "single_spread_all_hours": r5,
                "consensus_spread_all_hours": r6,
            },
            "key_comparison": {
                "single_naive_pnl": naive_pnl,
                "single_spread_pnl": single_spread_pnl,
                "single_spread_retention_pct": round(single_retention, 1),
                "consensus_naive_pnl": consensus_naive_pnl,
                "consensus_spread_pnl": consensus_spread_pnl,
                "consensus_spread_retention_pct": round(consensus_retention, 1),
                "consensus_improvement_vs_single_spread": round(improvement, 4),
                "single_avg_pnl_per_trade": single_avg,
                "consensus_avg_pnl_per_trade": consensus_avg,
                "single_win_rate_pct": r2["win_rate_pct"],
                "consensus_win_rate_pct": r4["win_rate_pct"],
                "single_sharpe": r2["sharpe_ratio"],
                "consensus_sharpe": r4["sharpe_ratio"],
                "single_max_dd_pct": r2["max_drawdown_pct"],
                "consensus_max_dd_pct": r4["max_drawdown_pct"],
                "trade_count_reduction": r2["total_trades"] - r4["total_trades"],
                "all_hours_single_spread": all_hours_single_spread,
                "all_hours_consensus_spread": all_hours_consensus_spread,
            },
            "verdict": "consensus_improves_spread_adjusted" if improvement > 0 else "consensus_worsens_spread_adjusted",
        }
        all_results[coin] = coin_results

    # ===================================================================
    # Summary table
    # ===================================================================
    print(f"\n\n{'=' * 100}")
    print(f"  SUMMARY TABLE")
    print(f"{'=' * 100}")
    print(f"  {'Coin':<10} {'Scenario':<35} {'PnL':>10} {'Trades':>7} {'WR':>7} "
          f"{'Sharpe':>8} {'Spread$':>9} {'Retention':>10}")
    print(f"  {'-' * 100}")

    for coin, cr in all_results.items():
        if isinstance(cr, dict) and "error" in cr:
            print(f"  {coin:<10} [ERROR: {cr['error']}]")
            continue
        scenarios = cr["scenarios"]
        for label, key in [
            ("single_naive_top", "single_naive_top_hours"),
            ("single_spread_top", "single_spread_top_hours"),
            ("consensus_naive_top", "consensus_naive_top_hours"),
            ("consensus_spread_top", "consensus_spread_top_hours"),
            ("single_spread_all", "single_spread_all_hours"),
            ("consensus_spread_all", "consensus_spread_all_hours"),
        ]:
            r = scenarios[key]
            kc = cr["key_comparison"]
            # Retention only meaningful for spread rows
            if "spread" in label:
                naive_key = key.replace("spread", "naive")
                naive_r = scenarios.get(naive_key, {})
                naive_p = naive_r.get("pnl", 0)
                ret = r["pnl"] / naive_p * 100 if naive_p != 0 else 0.0
                ret_str = f"{ret:.0f}%"
            else:
                ret_str = "n/a"
            print(f"  {coin:<10} {label:<35} ${r['pnl']:>8.2f} {r['total_trades']:>7} "
                  f"{r['win_rate_pct']:>6.1f}% {r['sharpe_ratio']:>8.4f} "
                  f"${r['total_spread_cost']:>7.2f} {ret_str:>10}")

    # ===================================================================
    # Final verdict
    # ===================================================================
    print(f"\n{'=' * 100}")
    print(f"  FINAL VERDICT")
    print(f"{'=' * 100}")

    for coin, cr in all_results.items():
        if isinstance(cr, dict) and "error" in cr:
            continue
        kc = cr["key_comparison"]
        verdict = cr["verdict"]
        improvement = kc["consensus_improvement_vs_single_spread"]
        print(f"\n  {coin}:")
        print(f"    Single-strategy spread-adjusted (top hours):  ${kc['single_spread_pnl']:+.2f}")
        print(f"    Consensus-gated spread-adjusted (top hours):  ${kc['consensus_spread_pnl']:+.2f}")
        print(f"    Delta: ${improvement:+.2f}")
        print(f"    Verdict: {verdict}")
        print(f"    Sharpe: {kc['single_sharpe']:.4f} -> {kc['consensus_sharpe']:.4f}")
        print(f"    Win rate: {kc['single_win_rate_pct']}% -> {kc['consensus_win_rate_pct']}%")
        print(f"    Max DD: {kc['single_max_dd_pct']}% -> {kc['consensus_max_dd_pct']}%")
        print(f"    Trade reduction: {kc['trade_count_reduction']} fewer trades")

    # Save results
    output = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "days": args.days,
        "spread_multiplier": args.spread_multiplier,
        "methodology": (
            "Compares single-strategy fibonacci spread-adjusted PnL vs "
            "consensus-gated (fib+momentum agree) spread-adjusted PnL. "
            "Consensus gate=2 means fibonacci must fire AND momentum must fire "
            "at the same candle for entry. Spread cost applied on both entry (ask) "
            "and exit (bid). Top-hours only for primary comparison."
        ),
        "results": all_results,
    }
    out_path = REPORTS / "consensus_gated_fidelity.json"
    out_path.write_text(json.dumps(output, indent=2), encoding="utf-8")
    print(f"\n  Results saved to: {out_path}")


if __name__ == "__main__":
    main()
