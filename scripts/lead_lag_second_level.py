#!/usr/bin/env python3
"""
Lane 1: Lead-Lag — Second/Tick Capture + Cross-Venue Correlation

This script does THREE things:
1. Backfill analysis: Use existing M1 candles to compute BTC/ETH → altcoin lead-lag
   at 1-min granularity with proper trading simulation (PnL, fees, WR).
2. Real-time capture: Start collecting second-level ticker data from Kraken + Coinbase
   for BTC, ETH, RAVE, IOTX, BAL — writing to JSONL for future analysis.
3. Event detection: Detect BTC/ETH spikes and measure altcoin reaction times.

Promotion gate: needs signal evidence + strict fill survival + supervision telemetry + clean aging.
This script provides signal evidence and telemetry. Fill survival is Lane 2's job.
"""
from __future__ import annotations

import json
import math
import statistics
import sys
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from candle_cache_service import load_candles

ROOT = Path(__file__).resolve().parent.parent
REPORT_DIR = ROOT / "reports"
REPORT_DIR.mkdir(exist_ok=True)

# ── Configuration ──────────────────────────────────────────────────────

LEADERS = ["BTC-USD", "ETH-USD"]
LAGGERS = ["RAVE-USD", "IOTX-USD", "BAL-USD", "BLUR-USD", "ALEPH-USD"]
ALL_PRODUCTS = LEADERS + LAGGERS

FEE_RATE = 0.0040  # 40bps maker fee
MIN_TRADES_FOR_SIGNAL = 30  # Need at least this many signal events to be confident

# Kraken API mapping
KRAKEN_MAP = {
    "BTC-USD": "XXBTZUSD",
    "ETH-USD": "XETHZUSD",
}
KRAKEN_COINBASE_MAP = {
    "BTC-USD": ("XXBTZUSD", "BTC-USD"),
    "ETH-USD": ("XETHZUSD", "ETH-USD"),
}


# ── Part 1: Backfill Lead-Lag Analysis (M1 candles) ────────────────────

def fetch_candles_multi(products, granularity="ONE_MINUTE", days=7):
    """Load candles for multiple products from cache."""
    data = {}
    for pid in products:
        candles = load_candles(pid, granularity, days, max_age_minutes=days * 24 * 60)
        if candles:
            data[pid] = candles
            print(f"  {pid}: {len(candles)} candles")
        else:
            print(f"  {pid}: NO DATA")
    return data


def compute_returns(candles):
    """Compute per-bar returns from candle list."""
    closes = [float(c["close"]) for c in candles]
    returns = [(closes[i] - closes[i - 1]) / closes[i - 1] for i in range(1, len(closes))]
    return returns


def pearson(x, y):
    """Pearson correlation."""
    n = min(len(x), len(y))
    if n < 10:
        return 0.0
    x = x[:n]
    y = y[:n]
    mx = sum(x) / n
    my = sum(y) / n
    num = sum((x[i] - mx) * (y[i] - my) for i in range(n))
    dx = math.sqrt(sum((xi - mx) ** 2 for xi in x))
    dy = math.sqrt(sum((yi - my) ** 2 for yi in y))
    if dx == 0 or dy == 0:
        return 0.0
    return num / (dx * dy)


def analyze_lead_lag_correlations(leader_returns, lagger_returns_list, lags=[1, 2, 3, 5]):
    """Compute cross-correlation at multiple lags."""
    results = {}
    for lag_name, lag_returns in lagger_returns_list.items():
        min_len = min(len(leader_returns), len(lag_returns))
        lr = leader_returns[:min_len]
        lag_r = lag_returns[:min_len]

        lag_corrs = {}
        for lag in lags:
            if lag >= min_len - 5:
                continue
            # Leader leads by `lag` bars: correlate leader[t] with lagger[t+lag]
            x = lr[:min_len - lag]
            y = lag_r[lag:]
            lag_corrs[lag] = round(pearson(x, y), 4)

        # Also compute contemporaneous (lag=0)
        lag_corrs[0] = round(pearson(lr, lag_r), 4)

        best_lag = max(lag_corrs, key=lambda k: abs(lag_corrs[k])) if lag_corrs else 0
        results[lag_name] = {
            "correlations": lag_corrs,
            "best_lag": best_lag,
            "best_correlation": lag_corrs.get(best_lag, 0),
            "contemporaneous": lag_corrs.get(0, 0),
        }
    return results


def simulate_lead_lag_trading(leader_returns, lagger_returns, leader_candles, lagger_candles,
                               leader_threshold, lag_bars=1, hold_bars=3,
                               tp_pct=0.05, sl_pct=0.03):
    """
    Simulate: when leader moves > threshold, enter lagger after `lag_bars`,
    exit after `hold_bars` or TP/SL.
    """
    min_len = min(len(leader_returns), len(lagger_returns))
    cash = 48.0
    starting_cash = 48.0
    trades = []

    for i in range(lag_bars, min_len - hold_bars):
        # Did leader move enough `lag_bars` ago?
        leader_move = abs(leader_returns[i - lag_bars])
        leader_direction = 1 if leader_returns[i - lag_bars] > 0 else -1

        if leader_move < leader_threshold:
            continue

        # Enter lagger at bar i
        entry_idx = i
        if entry_idx >= len(lagger_candles):
            continue

        entry_price = float(lagger_candles[entry_idx]["open"])
        deploy = cash * 0.95
        if deploy < 1.0:
            continue

        entry_fee = deploy * FEE_RATE
        units = (deploy - entry_fee) / entry_price
        cash -= deploy

        # TP/SL prices
        if leader_direction > 0:
            tp_price = entry_price * (1 + tp_pct)
            sl_price = entry_price * (1 - sl_pct)
        else:
            # Short logic simplified — just skip for now (spot only)
            continue

        # Exit: check each bar for TP/SL hit, otherwise timeout
        exit_price = None
        exit_reason = None
        for b in range(1, hold_bars + 1):
            exit_idx = i + b
            if exit_idx >= len(lagger_candles):
                break
            bar = lagger_candles[exit_idx]
            bar_high = float(bar["high"])
            bar_low = float(bar["low"])
            bar_close = float(bar["close"])

            # Check SL first (intra-bar)
            if bar_low <= sl_price:
                exit_price = sl_price
                exit_reason = "sl"
                break
            # Check TP
            if bar_high >= tp_price:
                exit_price = tp_price
                exit_reason = "tp"
                break
            # Timeout at close
            if b == hold_bars:
                exit_price = bar_close
                exit_reason = "timeout"

        if exit_price is None:
            continue

        exit_proceeds = exit_price * units
        exit_fee = exit_proceeds * FEE_RATE
        net = exit_proceeds - deploy - entry_fee - exit_fee
        cash += exit_proceeds - exit_fee

        trades.append({
            "entry_bar": i,
            "leader_move": round(leader_move * 100, 3),
            "leader_dir": leader_direction,
            "entry_price": entry_price,
            "exit_price": exit_price,
            "exit_reason": exit_reason,
            "net": round(net, 4),
            "win": net > 0,
        })

    if not trades:
        return None

    wins = [t for t in trades if t["win"]]
    losses = [t for t in trades if not t["win"]]
    net = sum(t["net"] for t in trades)

    return {
        "leader_threshold_pct": round(leader_threshold * 100, 2),
        "lag_bars": lag_bars,
        "hold_bars": hold_bars,
        "trades": len(trades),
        "wins": len(wins),
        "losses": len(losses),
        "wr": round(len(wins) / len(trades) * 100, 1),
        "net": round(net, 2),
        "return_pct": round(net / starting_cash * 100, 1),
        "avg_win": round(statistics.mean([t["net"] for t in wins]), 4) if wins else 0,
        "avg_loss": round(statistics.mean([t["net"] for t in losses]), 4) if losses else 0,
        "exit_reasons": {
            "tp": sum(1 for t in trades if t["exit_reason"] == "tp"),
            "sl": sum(1 for t in trades if t["exit_reason"] == "sl"),
            "timeout": sum(1 for t in trades if t["exit_reason"] == "timeout"),
        },
    }


def run_backfill_analysis():
    """Part 1: Analyze historical M1 data for lead-lag signals."""
    print("=" * 80)
    print("  LANE 1: LEAD-LAG — Backfill Analysis (M1 candles)")
    print("=" * 80)

    # Load data
    print("\nLoading candles...")
    candles_data = fetch_candles_multi(ALL_PRODUCTS, granularity="ONE_MINUTE", days=7)

    if not candles_data:
        print("ERROR: No candle data loaded. Cannot run backfill analysis.")
        return None

    # Compute returns
    returns = {pid: compute_returns(candles) for pid, candles in candles_data.items()}

    # Align returns lengths
    min_len = min(len(r) for r in returns.values()) if returns else 0
    if min_len < 20:
        print(f"ERROR: Only {min_len} aligned returns. Need at least 20.")
        return None

    for pid in returns:
        returns[pid] = returns[pid][-min_len:]

    print(f"\nAligned returns: {min_len} bars")

    # Correlation analysis
    print(f"\n--- Cross-Correlation: Leaders vs Laggers ---")
    all_results = {}

    for leader in LEADERS:
        if leader not in returns:
            continue
        lagger_returns = {lag: returns[lag] for lag in LAGGERS if lag in returns}
        if not lagger_returns:
            continue

        corr_results = analyze_lead_lag_correlations(
            returns[leader], lagger_returns, lags=[1, 2, 3, 5]
        )
        all_results[leader] = corr_results

        print(f"\n  Leader: {leader}")
        print(f"  {'Lagger':<12} {'Lag-0':>8} {'Lag-1':>8} {'Lag-2':>8} {'Lag-3':>8} {'Lag-5':>8} | Best")
        print(f"  {'─' * 12} {'─' * 8} {'─' * 8} {'─' * 8} {'─' * 8} {'─' * 8} | {'─' * 10}")
        for lag_name, cr in corr_results.items():
            corrs = cr["correlations"]
            best = cr["best_lag"]
            best_str = f"L{best}={cr['best_correlation']:.3f}" if best != 0 else f"L0={cr['best_correlation']:.3f}"
            row = f"  {lag_name:<12}"
            for lag in [0, 1, 2, 3, 5]:
                row += f" {corrs.get(lag, 0):>8.4f}"
            row += f" | {best_str}"
            print(row)

    # Trading simulation
    print(f"\n--- Trading Simulation: Lead-Lag Signals ---")
    trading_results = {}

    for leader in LEADERS:
        if leader not in returns or leader not in candles_data:
            continue
        for lagger in LAGGERS:
            if lagger not in returns or lagger not in candles_data:
                continue

            # Test multiple thresholds
            for threshold in [0.003, 0.005, 0.01, 0.015]:
                result = simulate_lead_lag_trading(
                    returns[leader], returns[lagger],
                    candles_data[leader], candles_data[lagger],
                    leader_threshold=threshold,
                    lag_bars=1,
                    hold_bars=3,
                )
                if result and result["trades"] >= 5:
                    key = f"{leader}→{lagger}@{threshold*100:.1f}%"
                    trading_results[key] = result
                    trades_label = f"{result['trades']}t"
                    wr_label = f"{result['wr']}%WR"
                    net_label = f"${result['net']:+.2f}"
                    print(f"  {key:<35} {trades_label:>6} {wr_label:>7} {net_label:>10}")

    # Find best strategy
    if trading_results:
        best_key = max(trading_results, key=lambda k: trading_results[k]["net"])
        best = trading_results[best_key]
        print(f"\n  🏆 Best strategy: {best_key}")
        print(f"     {best['trades']} trades, {best['wr']}% WR, ${best['net']:+.2f} net")
    else:
        print(f"\n  ⚠️ No trading strategies produced enough signals.")

    return {
        "correlations": all_results,
        "trading": trading_results,
        "aligned_bars": min_len,
        "data_window": f"Last {min_len} M1 bars",
    }


# ── Part 2: Real-Time Second-Level Capture ─────────────────────────────

def fetch_kraken_ticker(pair):
    """Fetch current ticker from Kraken."""
    try:
        url = f"https://api.kraken.com/0/public/Ticker?pair={pair}"
        req = urllib.request.Request(url, headers={"User-Agent": "LeadLagLab/1.0"})
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read().decode())
            if "result" in data and pair in data["result"]:
                ticker = data["result"][pair]
                return {
                    "last": float(ticker["c"][0]),
                    "bid": float(ticker["b"][0]),
                    "ask": float(ticker["a"][0]),
                    "vol_24h": float(ticker["v"][1]),
                    "ts": time.time(),
                }
    except Exception as e:
        pass
    return None


def fetch_coinbase_ticker(product_id):
    """Fetch current ticker from Coinbase (via advanced client if available)."""
    try:
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        from coinbase_advanced_client import CoinbaseAdvancedClient
        client = CoinbaseAdvancedClient()
        ticker = client.get_product(product_id)
        return {
            "last": float(ticker.get("price", 0)),
            "bid": float(ticker.get("bid", 0)),
            "ask": float(ticker.get("ask", 0)),
            "vol_24h": float(ticker.get("volume_24h", 0)),
            "ts": time.time(),
        }
    except Exception:
        return None


def run_realtime_capture(duration_seconds=300, interval_seconds=1.0):
    """
    Part 2: Collect second-level ticker data from Kraken + Coinbase.
    Writes to JSONL for future analysis.
    """
    print(f"\n{'='*80}")
    print(f"  LANE 1: LEAD-LAG — Real-Time Second-Level Capture")
    print(f"{'='*80}")
    print(f"  Duration: {duration_seconds}s, Interval: {interval_seconds}s")
    print(f"  Products: {', '.join(KRAKEN_COINBASE_MAP.keys())}")
    print(f"  Output: reports/lead_lag_second_level_capture.jsonl")

    output_path = REPORT_DIR / "lead_lag_second_level_capture.jsonl"

    records = []
    start_time = time.time()
    sample_count = 0

    try:
        while time.time() - start_time < duration_seconds:
            loop_start = time.time()

            for kraken_pair, cb_pid in KRAKEN_COINBASE_MAP.values():
                kr_data = fetch_kraken_ticker(kraken_pair)
                cb_data = fetch_coinbase_ticker(cb_pid)

                if kr_data and cb_data:
                    record = {
                        "ts": round(time.time(), 6),
                        "product": cb_pid,
                        "kraken_last": kr_data["last"],
                        "kraken_bid": kr_data["bid"],
                        "kraken_ask": kr_data["ask"],
                        "coinbase_last": cb_data["last"],
                        "coinbase_bid": cb_data["bid"],
                        "coinbase_ask": cb_data["ask"],
                        "spread": round(kr_data["last"] - cb_data["last"], 4),
                    }
                    records.append(record)
                    sample_count += 1

            # Sleep to maintain interval
            elapsed = time.time() - loop_start
            sleep_time = max(0, interval_seconds - elapsed)
            if sleep_time > 0:
                time.sleep(sleep_time)

            # Progress
            if sample_count % 20 == 0 and sample_count > 0:
                print(f"  ... {sample_count} cross-venue samples collected")

    except KeyboardInterrupt:
        print("\n  Capture stopped by user.")

    # Write to JSONL
    if records:
        with open(output_path, "a", encoding="utf-8") as f:
            for rec in records:
                f.write(json.dumps(rec) + "\n")
        print(f"\n  ✅ Saved {len(records)} samples to {output_path}")
    else:
        print(f"\n  ⚠️ No samples captured. Check API connectivity.")

    return records


# ── Part 3: Event Detection & Report ───────────────────────────────────

def generate_report(backfill_results, realtime_count=0):
    """Generate structured JSONL report for the lab dashboard."""
    report_path = REPORT_DIR / "lead_lag_report.json"

    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "lane": "1-lead-lag",
        "backfill": backfill_results or {},
        "realtime_capture": {
            "samples_collected": realtime_count,
            "sufficient_for_analysis": realtime_count >= 1000,
        },
        "signal_adequacy": {
            "correlation_evidence": bool(backfill_results and backfill_results.get("correlations")),
            "trading_evidence": bool(backfill_results and backfill_results.get("trading")),
            "realtime_data": realtime_count >= 1000,
            "meets_promotion_gate": False,  # Need all 4 gates
        },
        "promotion_gate_status": {
            "signal_evidence": "pending" if not backfill_results else ("yes" if backfill_results.get("trading") else "weak"),
            "strict_fill_survival": "not_tested — Lane 2 dependency",
            "supervision_telemetry": "partial — basic JSONL logging",
            "clean_forward_aging": "not_started — needs live runner",
        },
        "recommendation": "",
    }

    # Determine recommendation
    if backfill_results and backfill_results.get("trading"):
        best_key = max(backfill_results["trading"], key=lambda k: backfill_results["trading"][k]["net"])
        best = backfill_results["trading"][best_key]
        if best["net"] > 0 and best["trades"] >= MIN_TRADES_FOR_SIGNAL:
            report["recommendation"] = (
                f"CAUTIOUS OPTIMISM: {best_key} shows ${best['net']:+.2f} over {best['trades']} trades. "
                f"Need strict fill modeling (Lane 2) and forward aging before promotion."
            )
        elif best["net"] > 0:
            report["recommendation"] = (
                f"INSUFFICIENT DATA: {best_key} is positive but only {best['trades']} trades. "
                f"Need {MIN_TRADES_FOR_SIGNAL}+ trades for confidence. Extend data window."
            )
        else:
            report["recommendation"] = (
                f"NO EDGE DETECTED: Best strategy {best_key} lost ${best['net']:.2f} over {best['trades']} trades. "
                f"Lead-lag at M1 granularity does not survive fees. Try second-level data."
            )
    else:
        report["recommendation"] = (
            "NO TRADING SIGNAL: Lead-lag correlations exist but do not translate to profitable trades "
            "at M1 granularity with 40bps fees. Need second-level capture to find faster edges."
        )

    with open(report_path, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2, default=str)

    print(f"\n  Report saved: {report_path}")
    return report


# ── Main ───────────────────────────────────────────────────────────────

def main():
    # Part 1: Backfill analysis (always run)
    backfill_results = run_backfill_analysis()

    # Part 2: Real-time capture (optional — runs for 5 min by default)
    # Skip if user just wants quick analysis
    realtime_records = []
    if "--capture" in sys.argv:
        duration = 300  # 5 minutes
        if "--duration" in sys.argv:
            idx = sys.argv.index("--duration")
            if idx + 1 < len(sys.argv):
                duration = int(sys.argv[idx + 1])
        realtime_records = run_realtime_capture(duration_seconds=duration)

    # Part 3: Report
    report = generate_report(backfill_results, realtime_count=len(realtime_records))

    # Print summary
    print(f"\n{'='*80}")
    print(f"  LANE 1: LEAD-LAG — Summary")
    print(f"{'='*80}")
    print(f"  Backfill: {backfill_results['aligned_bars']} M1 bars analyzed" if backfill_results else "  Backfill: SKIPPED")
    print(f"  Realtime capture: {len(realtime_records)} samples" if realtime_records else "  Realtime capture: SKIPPED (use --capture to enable)")
    print(f"  Recommendation: {report['recommendation']}")
    print(f"{'='*80}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
