#!/usr/bin/env python3
"""Coin Discovery Scan — find coins where trading edges survive spread costs.

Fetches live bid-ask spreads for all Coinbase USD pairs, filters by spread < 0.5%,
then runs quick supertrend backtests (7-day lookback) on the top 15 tightest-spread coins.

Usage:
    python scripts/coin_discovery_scan.py
    python scripts/coin_discovery_scan.py --spread-threshold 0.3
    python scripts/coin_discovery_scan.py --days 14 --top-n 20

Output:
    reports/coin_discovery_scan.json
    reports/coin_discovery_scan.txt
"""
from __future__ import annotations

import json
import math
import os
import random
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = Path(__file__).resolve().parent
REPORTS = ROOT / "reports"

sys.path.insert(0, str(SCRIPTS_DIR))
from coinbase_advanced_client import CoinbaseAdvancedClient

PAIRS_FILE = ROOT / "coinbase_usd_pairs.txt"
SPREAD_THRESHOLD_PCT = 0.5  # max spread % to consider
TOP_N = 15
BACKTEST_DAYS = 7
FEE_RATE = 0.004
STARTING_CASH = 100.0

# Generic supertrend params for screening (not coin-specific tuning)
DEFAULT_ST_PARAMS = {
    "atr_period": 10,
    "atr_mult": 3.0,
    "tp_pct": 8.0,   # 8% take profit — wide enough for volatility
    "sl_pct": 3.0,   # 3% stop loss
    "max_hold": 48,  # 4 hours at 5-min bars
}

SESSION_DEAD_HOURS = {0, 6, 12, 19}


def load_pairs_file() -> list[str]:
    """Load coin pairs from the existing pairs file."""
    if not PAIRS_FILE.exists():
        print(f"WARNING: Pairs file not found at {PAIRS_FILE}")
        return []
    pairs = []
    with open(PAIRS_FILE, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and line.endswith("-USD"):
                pairs.append(line)
    return pairs


def fetch_spreads_curl(pairs: list[str], batch_size: int = 10) -> dict[str, dict]:
    """Fetch live spreads using curl via subprocess (urllib hangs on this machine)."""
    results: dict[str, dict] = {}
    total = len(pairs)

    for i in range(0, total, batch_size):
        batch = pairs[i:i + batch_size]
        for pair in batch:
            url = f"https://api.exchange.coinbase.com/products/{pair}/ticker"
            try:
                proc = subprocess.run(
                    ["curl", "-s", "--max-time", "8", url],
                    capture_output=True, text=True, timeout=12
                )
                if proc.returncode == 0 and proc.stdout.strip():
                    data = json.loads(proc.stdout)
                    bid = float(data.get("bid", 0))
                    ask = float(data.get("ask", 0))
                    price = float(data.get("price", 0))
                    if bid > 0 and ask > 0 and price > 0:
                        spread_pct = ((ask - bid) / price) * 100.0
                        results[pair] = {
                            "bid": bid,
                            "ask": ask,
                            "price": price,
                            "spread": ask - bid,
                            "spread_pct": round(spread_pct, 4),
                            "volume_24h": float(data.get("volume", 0)),
                        }
                    else:
                        results[pair] = {"error": "invalid prices"}
                else:
                    results[pair] = {"error": f"curl failed: {proc.stderr[:100]}"}
            except subprocess.TimeoutExpired:
                results[pair] = {"error": "timeout"}
            except Exception as e:
                results[pair] = {"error": str(e)}
            time.sleep(0.2)

        done = min(i + batch_size, total)
        print(f"  Spread fetch: {done}/{total} pairs done", flush=True)

    return results


def fetch_spreads_public(pairs: list[str]) -> dict[str, dict]:
    """Fetch live spreads using the public Coinbase ticker endpoint."""
    return fetch_spreads_curl(pairs)


def fetch_candles(client: CoinbaseAdvancedClient, coin: str, days: int) -> list[dict]:
    """Fetch days of 5-min candles via authenticated API."""
    end = int(time.time())
    start = end - days * 86400
    chunk_sec = 300 * 5 * 60  # ~25 hours per chunk
    all_candles: list[dict] = []
    cs = start
    retries = 0
    while cs < end and retries < 3:
        ce = min(cs + chunk_sec, end)
        try:
            resp = client.market_candles(coin, start=cs, end=ce, granularity="FIVE_MINUTE")
            cands = resp.get("candles", [])
            all_candles.extend(cands)
            cs = ce
            retries = 0
            if not cands:
                break
            time.sleep(0.2)
        except Exception as e:
            retries += 1
            print(f"  WARN fetch error for {coin} at {cs}: {e}, retry {retries}", flush=True)
            time.sleep(1.0)
    all_candles.sort(key=lambda c: int(c.get("start", c.get("time", 0))))
    return all_candles


def compute_supertrend_line(candles_hist: list[dict], atr_period: int, atr_mult: float) -> float | None:
    """Return the current supertrend line value."""
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


def run_backtest(
    candles: list[dict],
    params: dict,
    *,
    mode: str,
    spread_pct: float,
    slippage_pct: float = 0.001,
    seed: int = 42,
) -> dict:
    """Quick backtest with fidelity modes."""
    rng = random.Random(seed)
    cash = STARTING_CASH
    pos: dict | None = None
    trades: list[float] = []
    peak_equity = cash
    max_dd = 0.0
    wins = 0
    losses = 0
    signals = 0
    total_spread_paid = 0.0
    same_bar_blocked = 0

    tp_pct = params["tp_pct"] / 100.0
    sl_pct = params["sl_pct"] / 100.0
    max_hold = params["max_hold"]
    atr_period = params["atr_period"]
    atr_mult = params["atr_mult"]

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

        # EXIT
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

        # ENTRY
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

    # Close remaining
    if pos is not None:
        last_close = float(candles[-1]["close"])
        effective_exit = last_close
        if mode == "spread_adjusted":
            effective_exit -= effective_exit * spread_pct
            total_spread_paid += effective_exit * spread_pct * pos["units"]

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

    if total_trades > 1 and len(trades) > 1:
        mean_ret = total_pnl / total_trades
        std_ret = math.sqrt(sum((t - mean_ret) ** 2 for t in trades) / total_trades)
        sharpe = mean_ret / std_ret if std_ret > 0 else 0.0
    else:
        sharpe = 0.0

    gross_profit = sum(t for t in trades if t > 0)
    gross_loss = abs(sum(t for t in trades if t < 0))
    profit_factor = gross_profit / gross_loss if gross_loss > 0 else 999.0

    final_equity = cash
    roi_pct = (final_equity - STARTING_CASH) / STARTING_CASH * 100

    # Drawdown tracking
    eq = final_equity
    if eq > peak_equity:
        peak_equity = eq
    dd = (peak_equity - eq) / peak_equity if peak_equity > 0 else 0.0
    if dd > max_dd:
        max_dd = dd

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
        "profit_factor": round(profit_factor, 3),
        "max_drawdown_pct": round(max_dd * 100, 1),
        "signals": signals,
        "total_spread_paid": round(total_spread_paid, 4),
        "same_bar_blocked": same_bar_blocked,
    }


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Coin discovery scan — tight spread + backtest")
    parser.add_argument("--spread-threshold", type=float, default=SPREAD_THRESHOLD_PCT,
                        help=f"Max spread %% to consider (default: {SPREAD_THRESHOLD_PCT})")
    parser.add_argument("--top-n", type=int, default=TOP_N,
                        help=f"Number of top-spread coins to backtest (default: {TOP_N})")
    parser.add_argument("--days", type=int, default=BACKTEST_DAYS,
                        help=f"Days of candles for backtest (default: {BACKTEST_DAYS})")
    args = parser.parse_args()

    spread_threshold = args.spread_threshold
    top_n = args.top_n
    backtest_days = args.days

    print(f"\n{'=' * 80}")
    print(f"  COIN DISCOVERY SCAN")
    print(f"  Spread threshold: {spread_threshold}%")
    print(f"  Top N for backtest: {top_n}")
    print(f"  Backtest lookback: {backtest_days} days")
    print(f"  Started: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}")
    print(f"{'=' * 80}\n")

    # Step 1: Load pairs
    print("Step 1: Loading Coinbase USD pairs...", flush=True)
    pairs = load_pairs_file()
    print(f"  Found {len(pairs)} pairs in {PAIRS_FILE.name}\n", flush=True)

    if not pairs:
        print("ERROR: No pairs found. Exiting.")
        return 1

    # Step 2: Fetch live spreads
    print(f"Step 2: Fetching live spreads for {len(pairs)} pairs...", flush=True)
    spread_data = fetch_spreads_public(pairs)

    # Parse results
    valid_spreads: list[tuple[str, float, dict]] = []
    errors = 0
    for pair, data in spread_data.items():
        if "error" in data:
            errors += 1
            continue
        valid_spreads.append((pair, data["spread_pct"], data))

    valid_spreads.sort(key=lambda x: x[1])
    print(f"  Valid: {len(valid_spreads)}, Errors: {errors}\n", flush=True)

    # Step 3: Filter by spread threshold
    print(f"Step 3: Filtering spread < {spread_threshold}%...", flush=True)
    tight_spreads = [(p, sp, d) for p, sp, d in valid_spreads if sp < spread_threshold]
    print(f"  Coins with spread < {spread_threshold}%: {len(tight_spreads)}\n", flush=True)

    # Print top 25 tightest spreads
    print("  Top 25 tightest spreads:")
    print(f"  {'Rank':>4}  {'Pair':<16}  {'Spread%':>8}  {'Price':>12}  {'Volume24h':>14}")
    print(f"  {'-' * 60}")
    for i, (pair, sp, d) in enumerate(valid_spreads[:25], 1):
        print(f"  {i:>4}  {pair:<16}  {sp:>7.4f}%  ${d['price']:>10.4f}  ${d['volume_24h']:>12,.0f}")
    print()

    # Step 4: Backtest top N tightest-spread coins
    backtest_candidates = tight_spreads[:top_n]
    if not backtest_candidates:
        print("ERROR: No candidates below spread threshold. Exiting.")
        return 1

    print(f"Step 4: Backtesting top {len(backtest_candidates)} candidates ({backtest_days}d, supertrend)...", flush=True)

    client = CoinbaseAdvancedClient()
    backtest_results: list[dict] = []

    for pair, spread_pct, data in backtest_candidates:
        print(f"\n  --- {pair} (spread={spread_pct:.4f}%, price=${data['price']:.4f}) ---", flush=True)
        candles = fetch_candles(client, pair, backtest_days)
        if len(candles) < 50:
            print(f"  SKIP: only {len(candles)} candles, need >= 50", flush=True)
            backtest_results.append({
                "pair": pair,
                "spread_pct": spread_pct,
                "price": data["price"],
                "volume_24h": data["volume_24h"],
                "candles_fetched": len(candles),
                "error": f"insufficient data: {len(candles)} candles",
                "naive": None,
                "spread_adjusted": None,
                "slippage_adjusted": None,
                "no_same_bar": None,
            })
            continue

        print(f"  Fetched {len(candles)} candles, running backtests...", flush=True)

        # Compute real spread as fraction for backtest
        spread_fraction = spread_pct / 100.0

        modes = ["naive", "spread_adjusted", "slippage_adjusted", "no_same_bar"]
        mode_results: dict[str, dict] = {}
        for mode in modes:
            r = run_backtest(candles, DEFAULT_ST_PARAMS, mode=mode, spread_pct=spread_fraction)
            mode_results[mode] = r
            marker = ""
            if mode == "spread_adjusted":
                marker = f"  (spread cost: ${r['total_spread_paid']:.2f})"
            elif mode == "slippage_adjusted":
                marker = f"  (slippage cost extra)"
            elif mode == "no_same_bar":
                marker = f"  (blocked: {r['same_bar_blocked']})"
            print(
                f"    {mode:<22s}  PnL=${r['total_pnl']:>+8.2f}  "
                f"ROI={r['roi_pct']:>+7.2f}%  "
                f"trades={r['total_trades']:>4}  "
                f"WR={r['win_rate']:>5.1f}%  "
                f"Sharpe={r['sharpe']:>6.3f}{marker}"
            )

        naive_r = mode_results.get("naive")
        spread_r = mode_results.get("spread_adjusted")
        edge_survival = 0.0
        if naive_r and spread_r and naive_r["total_pnl"] != 0:
            edge_survival = (spread_r["total_pnl"] / naive_r["total_pnl"]) * 100.0

        verdict = "NO EDGE"
        if naive_r and spread_r:
            if naive_r["total_pnl"] > 0 and spread_r["total_pnl"] > 0:
                verdict = "EDGE SURVIVES"
            elif naive_r["total_pnl"] > 0 and spread_r["total_pnl"] <= 0:
                verdict = "EDGE ERASED"
            elif naive_r["total_pnl"] <= 0 and spread_r["total_pnl"] > 0:
                verdict = "EDGE IMPROVES (anomalous)"
            elif spread_r["total_pnl"] > 0:
                verdict = "MARGINAL POSITIVE"

        backtest_results.append({
            "pair": pair,
            "spread_pct": spread_pct,
            "price": data["price"],
            "volume_24h": data["volume_24h"],
            "candles_fetched": len(candles),
            "naive": naive_r,
            "spread_adjusted": spread_r,
            "slippage_adjusted": mode_results.get("slippage_adjusted"),
            "no_same_bar": mode_results.get("no_same_bar"),
            "edge_survival_pct": round(edge_survival, 1),
            "verdict": verdict,
        })

    # Step 5: Rank and output
    print(f"\n{'=' * 80}")
    print(f"  RANKINGS BY SPREAD-ADJUSTED PnL")
    print(f"{'=' * 80}\n")

    # Sort by spread-adjusted PnL
    ranked = [r for r in backtest_results if r.get("spread_adjusted") is not None]
    ranked.sort(key=lambda r: r["spread_adjusted"]["total_pnl"], reverse=True)

    print(f"  {'Rank':>4}  {'Pair':<16}  {'Spread%':>8}  {'Naive PnL':>11}  {'Spread-Adj PnL':>14}  "
          f"{'WR%':>5}  {'Trades':>6}  {'Survival%':>10}  {'Verdict'}")
    print(f"  {'-' * 105}")
    for i, r in enumerate(ranked, 1):
        naive_pnl = r["naive"]["total_pnl"] if r.get("naive") else 0
        sp_pnl = r["spread_adjusted"]["total_pnl"]
        wr = r["spread_adjusted"]["win_rate"]
        trades = r["spread_adjusted"]["total_trades"]
        surv = r.get("edge_survival_pct", 0)
        print(
            f"  {i:>4}  {r['pair']:<16}  {r['spread_pct']:>7.4f}%  "
            f"${naive_pnl:>+10.2f}  ${sp_pnl:>+13.2f}  "
            f"{wr:>4.1f}%  {trades:>6}  {surv:>9.1f}%  {r['verdict']}"
        )

    # Flags
    print(f"\n  {'=' * 80}")
    print(f"  FLAGS")
    print(f"{'=' * 80}\n")

    positive_edge = [r for r in ranked if r["spread_adjusted"]["total_pnl"] > 0]
    ultra_tight = [r for r in ranked if r["spread_pct"] < 0.1]

    if positive_edge:
        print(f"  Coins with spread-adjusted PnL > 0 (genuine edge): {len(positive_edge)}")
        for r in positive_edge:
            print(f"    + {r['pair']}: ${r['spread_adjusted']['total_pnl']:+.2f} "
                  f"(spread={r['spread_pct']:.4f}%, trades={r['spread_adjusted']['total_trades']})")
    else:
        print(f"  No coins with spread-adjusted PnL > 0 at this lookback/threshold.")

    print()
    if ultra_tight:
        print(f"  Ultra-tight spreads (< 0.1%): {len(ultra_tight)}")
        for r in ultra_tight:
            print(f"    * {r['pair']}: spread={r['spread_pct']:.4f}%")
    print()

    # Save reports
    REPORTS.mkdir(exist_ok=True)

    output = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "spread_threshold_pct": spread_threshold,
        "top_n": top_n,
        "backtest_days": backtest_days,
        "total_pairs_scanned": len(pairs),
        "valid_spreads": len(valid_spreads),
        "tight_spread_count": len(tight_spreads),
        "all_spreads": [{"pair": p, "spread_pct": sp, "price": d["price"], "volume_24h": d["volume_24h"]}
                         for p, sp, d in valid_spreads],
        "backtest_results": backtest_results,
        "ranked_by_spread_adj_pnl": [
            {
                "rank": i + 1,
                "pair": r["pair"],
                "spread_pct": r["spread_pct"],
                "price": r["price"],
                "volume_24h": r["volume_24h"],
                "naive_pnl": r["naive"]["total_pnl"] if r.get("naive") else None,
                "naive_roi": r["naive"]["roi_pct"] if r.get("naive") else None,
                "spread_adj_pnl": r["spread_adjusted"]["total_pnl"] if r.get("spread_adjusted") else None,
                "spread_adj_roi": r["spread_adjusted"]["roi_pct"] if r.get("spread_adjusted") else None,
                "spread_adj_win_rate": r["spread_adjusted"]["win_rate"] if r.get("spread_adjusted") else None,
                "spread_adj_trades": r["spread_adjusted"]["total_trades"] if r.get("spread_adjusted") else None,
                "spread_adj_sharpe": r["spread_adjusted"]["sharpe"] if r.get("spread_adjusted") else None,
                "edge_survival_pct": r.get("edge_survival_pct"),
                "verdict": r["verdict"],
            }
            for i, r in enumerate(ranked)
        ],
        "flags": {
            "positive_edge_coins": [r["pair"] for r in positive_edge],
            "ultra_tight_coins": [r["pair"] for r in ultra_tight],
        },
    }

    json_path = REPORTS / "coin_discovery_scan.json"
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(output, f, indent=2, default=str)
    print(f"  JSON report: {json_path}")

    # Text report
    txt_lines = []
    txt_lines.append("=" * 80)
    txt_lines.append("  COIN DISCOVERY SCAN")
    txt_lines.append(f"  Timestamp: {output['timestamp']}")
    txt_lines.append(f"  Spread threshold: {spread_threshold}%")
    txt_lines.append(f"  Top N backtested: {top_n}")
    txt_lines.append(f"  Lookback: {backtest_days} days")
    txt_lines.append(f"  Total pairs scanned: {len(pairs)}")
    txt_lines.append(f"  Valid spreads fetched: {len(valid_spreads)}")
    txt_lines.append(f"  Coins with spread < {spread_threshold}%: {len(tight_spreads)}")
    txt_lines.append("=" * 80)
    txt_lines.append("")

    # All spreads sorted
    txt_lines.append(f"  ALL SPREADS (sorted tightest first, top 50)")
    txt_lines.append(f"  {'Rank':>4}  {'Pair':<16}  {'Spread%':>8}  {'Price':>12}  {'Volume24h':>14}")
    txt_lines.append(f"  {'-' * 60}")
    for i, (p, sp, d) in enumerate(valid_spreads[:50], 1):
        txt_lines.append(f"  {i:>4}  {p:<16}  {sp:>7.4f}%  ${d['price']:>10.4f}  ${d['volume_24h']:>12,.0f}")
    txt_lines.append("")

    # Backtest rankings
    txt_lines.append(f"  RANKINGS BY SPREAD-ADJUSTED PnL")
    txt_lines.append(f"  {'Rank':>4}  {'Pair':<16}  {'Spread%':>8}  {'Naive PnL':>11}  "
                     f"{'Spread-Adj PnL':>14}  {'WR%':>5}  {'Trades':>6}  {'Survival%':>10}  {'Verdict'}")
    txt_lines.append(f"  {'-' * 105}")
    for i, r in enumerate(ranked, 1):
        naive_pnl = r["naive"]["total_pnl"] if r.get("naive") else 0
        sp_pnl = r["spread_adjusted"]["total_pnl"]
        wr = r["spread_adjusted"]["win_rate"]
        trades = r["spread_adjusted"]["total_trades"]
        surv = r.get("edge_survival_pct", 0)
        txt_lines.append(
            f"  {i:>4}  {r['pair']:<16}  {r['spread_pct']:>7.4f}%  "
            f"${naive_pnl:>+10.2f}  ${sp_pnl:>+13.2f}  "
            f"{wr:>4.1f}%  {trades:>6}  {surv:>9.1f}%  {r['verdict']}"
        )
    txt_lines.append("")

    # Flags
    txt_lines.append(f"  FLAGS")
    txt_lines.append(f"  {'-' * 60}")
    if positive_edge:
        txt_lines.append(f"  Coins with spread-adjusted PnL > 0 (genuine edge):")
        for r in positive_edge:
            txt_lines.append(f"    + {r['pair']}: ${r['spread_adjusted']['total_pnl']:+.2f} "
                             f"(spread={r['spread_pct']:.4f}%, trades={r['spread_adjusted']['total_trades']})")
    else:
        txt_lines.append(f"  No coins with spread-adjusted PnL > 0 at this lookback/threshold.")
    txt_lines.append("")
    if ultra_tight:
        txt_lines.append(f"  Ultra-tight spreads (< 0.1%):")
        for r in ultra_tight:
            txt_lines.append(f"    * {r['pair']}: spread={r['spread_pct']:.4f}%")
    txt_lines.append("")

    # Known failures cross-reference
    known_failures = {"RAVE-USD", "IOTX-USD", "TRU-USD", "BAL-USD"}
    txt_lines.append(f"  KNOWN FAILURES CROSS-REFERENCE")
    txt_lines.append(f"  {'-' * 60}")
    for pair in known_failures:
        match = next((r for r in valid_spreads if r[0] == pair), None)
        if match:
            _, sp, d = match
            status = "PASS" if sp < spread_threshold else "FAIL"
            txt_lines.append(f"  {pair:<16}  spread={sp:.4f}%  [{status}]  price=${d['price']:.4f}")
        else:
            txt_lines.append(f"  {pair:<16}  spread=UNKNOWN  (could not fetch)")
    txt_lines.append("")

    txt_path = REPORTS / "coin_discovery_scan.txt"
    with open(txt_path, "w", encoding="utf-8") as f:
        f.write("\n".join(txt_lines) + "\n")
    print(f"  Text report: {txt_path}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
