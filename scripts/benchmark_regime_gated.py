#!/usr/bin/env python3
"""
Regime-Gated Benchmark — RAVE RSI MR Strategy (30d)

Simulates the RSI mean-reversion strategy with regime-gated position sizing:
  - Score < 40 (CHOPPY)  → SKIP entry
  - Score 40-70 (COLD)   → HALF size (50% of cash)
  - Score >= 70 (HOT)    → FULL size (100% of cash)

Compares gated vs ungated baseline across all fee tiers.

Usage:
    python scripts/benchmark_regime_gated.py
"""
import json
import os
import sys
import time
import random
import statistics
import math
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from coinbase_advanced_client import CoinbaseAdvancedClient
from regime_detection import regime_score
from benchmark_shared import FEE_TIERS, BUILTIN_FILL_MODELS, RAVE_RSI_MR_BASELINE_PARAMS

ROOT = Path(__file__).resolve().parent.parent
REPORT_PATH = ROOT / "reports" / "regime_gated_benchmark_30d.json"

PRODUCT = "RAVE-USD"
BTC = "BTC-USD"
GRANULARITY = "FIVE_MINUTE"

# Strategy params
RSI_PERIOD = RAVE_RSI_MR_BASELINE_PARAMS["rsi_period"]
OS_THRESH = RAVE_RSI_MR_BASELINE_PARAMS["os_thresh"]
TP_PCT = RAVE_RSI_MR_BASELINE_PARAMS["tp_pct"]
MAX_HOLD = RAVE_RSI_MR_BASELINE_PARAMS["max_hold"]
SL_PCT = RAVE_RSI_MR_BASELINE_PARAMS["sl_pct"]

# Regime gate thresholds
REGIME_SKIP = 40
REGIME_HALF = 70

# Execution
STARTING_CASH = 48.0
SESSION_DEAD_HOURS = {0, 6, 12, 19}
FILL_PROB = 1.0  # measured_forward
ENTRY_SLIPPAGE_BPS = 6.2
EXIT_SLIPPAGE_BPS = 0.0
REGIME_WARMUP = 20  # minimum candles before regime scoring is reliable


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def fetch_candles(client, pid, start, end, granularity=GRANULARITY):
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


def compute_rsi(closes, period=RSI_PERIOD):
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


def get_fee_rate(total_volume):
    """Coinbase maker fee schedule."""
    if total_volume >= 50000:
        return 0.0015
    elif total_volume >= 10000:
        return 0.0025
    return 0.0040


def classify_regime(score):
    if score < REGIME_SKIP:
        return "CHOPPY"
    elif score < REGIME_HALF:
        return "COLD"
    return "HOT"


def _compute_atr_pct(candles_subset):
    """Quick ATR% for regime warmup fallback."""
    highs = [float(c["high"]) for c in candles_subset]
    lows = [float(c["low"]) for c in candles_subset]
    closes = [float(c["close"]) for c in candles_subset]
    period = 14
    if len(highs) < period + 1:
        return 0.0
    trs = []
    for i in range(1, len(highs)):
        tr = max(highs[i] - lows[i], abs(highs[i] - closes[i-1]), abs(lows[i] - closes[i-1]))
        trs.append(tr)
    if len(trs) < period:
        return 0.0
    atr = statistics.mean(trs[-period:])
    avg_price = statistics.mean(closes[-period:])
    return (atr / avg_price * 100) if avg_price > 0 else 0.0


# ---------------------------------------------------------------------------
# Core backtest engine
# ---------------------------------------------------------------------------

def run_backtest(candles, btc_candles, fee_bps, *,
                 regime_gated=False, fill_prob=FILL_PROB,
                 entry_slippage_bps=ENTRY_SLIPPAGE_BPS,
                 exit_slippage_bps=EXIT_SLIPPAGE_BPS,
                 starting_cash=STARTING_CASH):
    """
    Run backtest with optional regime gating.

    Returns detailed results dict with per-trade history and regime breakdown.
    """
    rng = random.Random(42)

    cash = starting_cash
    pos = None
    closes_count = 0
    wins = 0
    losses = 0
    total_volume = 0.0
    total_fees = 0.0
    price_history = []
    candle_history = []   # full candles for regime detection
    btc_candle_history = []
    peak = starting_cash
    max_dd = 0.0
    peak_equity = starting_cash

    signals_total = 0
    filled_total = 0
    session_filtered = 0
    regime_filtered = 0
    regime_skipped_trades = []  # what would have happened if we took them

    # Per-regime stats
    regime_stats = {
        "CHOPPY": {"trades": 0, "wins": 0, "losses": 0, "pnl": 0.0},
        "COLD":   {"trades": 0, "wins": 0, "losses": 0, "pnl": 0.0},
        "HOT":    {"trades": 0, "wins": 0, "losses": 0, "pnl": 0.0},
    }

    entry_slip = entry_slippage_bps / 10000.0
    exit_slip = exit_slippage_bps / 10000.0

    trades = []

    for i in range(len(candles)):
        c = candles[i]
        ts = int(c.get("start", c.get("time", 0)))
        close = float(c["close"])
        high = float(c["high"])
        low = float(c["low"])
        candle_open = float(c["open"])

        price_history.append(close)
        if len(price_history) > 500:
            price_history = price_history[-500:]

        candle_history.append({
            "start": ts, "open": candle_open, "high": high,
            "low": low, "close": close,
            "volume": float(c.get("volume", 0))
        })
        if len(candle_history) > 50:
            candle_history = candle_history[-50:]

        # BTC candle for this timestamp
        btc_c = None
        for bc in btc_candles:
            if int(bc["start"]) == ts:
                btc_c = {
                    "start": ts, "open": float(bc["open"]), "high": float(bc["high"]),
                    "low": float(bc["low"]), "close": float(bc["close"]),
                    "volume": float(bc.get("volume", 0))
                }
                break
        if btc_c:
            btc_candle_history.append(btc_c)
            if len(btc_candle_history) > 50:
                btc_candle_history = btc_candle_history[-50:]

        # Session gate
        hour = datetime.fromtimestamp(ts, tz=timezone.utc).hour
        session_open = hour not in SESSION_DEAD_HOURS

        # Use the passed fee_bps (not volume-dependent) for clean tier comparison
        fee_rate = fee_bps

        # --- EXIT ---
        if pos:
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
                actual_exit = exit_price * (1 - exit_slip)
                units = pos["units"]
                gross = (actual_exit - pos["ep"]) * units
                entry_fee = pos["entry_fee"]
                exit_fee = actual_exit * units * fee_rate
                net = gross - entry_fee - exit_fee

                cash += pos["q"] + net
                closes_count += 1
                total_volume += pos["q"] + (actual_exit * units)
                total_fees += entry_fee + exit_fee

                if net > 0:
                    wins += 1
                else:
                    losses += 1

                # Track regime stats
                regime = pos.get("regime", "UNKNOWN")
                if regime in regime_stats:
                    regime_stats[regime]["trades"] += 1
                    if net > 0:
                        regime_stats[regime]["wins"] += 1
                    else:
                        regime_stats[regime]["losses"] += 1
                    regime_stats[regime]["pnl"] += net

                equity = cash  # no unrealized PnL after close
                peak_equity = max(peak_equity, equity)
                dd = (peak_equity - equity) / peak_equity if peak_equity > 0 else 0
                max_dd = max(max_dd, dd)

                trade_record = {
                    "entry_ts": pos["entry_ts"],
                    "exit_ts": ts,
                    "entry_price": pos["ep"],
                    "exit_price": round(actual_exit, 6),
                    "units": round(units, 4),
                    "deploy": round(pos["q"], 4),
                    "regime": regime,
                    "regime_score": pos.get("regime_score", 0),
                    "net": round(net, 4),
                    "reason": exit_reason,
                    "hold_bars": pos["hold"],
                }
                trades.append(trade_record)

                pos = None

        # --- ENTRY ---
        if pos is None and cash >= 10.0 and session_open and len(price_history) >= RSI_PERIOD + 2:
            rsi_val = compute_rsi(price_history[:-1], RSI_PERIOD)

            if rsi_val <= OS_THRESH:
                signals_total += 1

                # Fill probability
                if rng.random() > fill_prob:
                    session_filtered += 1
                    continue

                # --- Regime gate ---
                r_score = 50  # neutral default
                deploy_fraction = 1.0
                regime_label = "COLD"  # default

                if len(candle_history) >= REGIME_WARMUP and len(btc_candle_history) >= 10:
                    try:
                        r_result = regime_score(candle_history, btc_candle_history)
                        r_score = r_result.get("score", 50)
                    except Exception:
                        r_score = 50

                    regime_label = classify_regime(r_score)

                    if regime_gated:
                        if r_score < REGIME_SKIP:
                            # CHOPPY: skip
                            regime_filtered += 1
                            # Record what would have happened: store the signal
                            regime_skipped_trades.append({
                                "ts": ts,
                                "rsi": round(rsi_val, 2),
                                "regime_score": r_score,
                                "regime": regime_label,
                                "entry_price": candle_open,
                                "note": "skipped_choppy",
                            })
                            continue
                        elif r_score < REGIME_HALF:
                            deploy_fraction = 0.5
                        else:
                            deploy_fraction = 1.0
                elif not regime_gated:
                    # Ungated: always trade, neutral regime
                    regime_label = "HOT"
                    r_score = 100

                # Entry with slippage
                actual_entry = candle_open * (1 + entry_slip)
                deploy = cash * deploy_fraction
                entry_fee = deploy * fee_rate
                units = (deploy - entry_fee) / actual_entry
                tp = actual_entry * (1 + TP_PCT / 100.0)
                sl = actual_entry * (1 - SL_PCT / 100.0) if SL_PCT > 0 else 0

                cash -= deploy
                pos = {
                    "ep": actual_entry,
                    "q": deploy,
                    "hold": 0,
                    "tp": tp,
                    "sl": sl,
                    "units": units,
                    "entry_fee": entry_fee,
                    "max_hold": MAX_HOLD,
                    "entry_ts": ts,
                    "regime": regime_label,
                    "regime_score": r_score,
                }
                filled_total += 1

    # Close any remaining position at close price (conservative)
    if pos:
        cash += pos["q"]

    net_pnl = cash - starting_cash
    wr = wins / max(1, closes_count) * 100

    # Monthly projection (30d = ~8640 M5 candles ≈ 30 days)
    days = 30
    monthly_proj = (net_pnl / days) * 30 if days > 0 else 0

    return {
        "net": round(net_pnl, 2),
        "return_pct": round(net_pnl / starting_cash * 100, 1),
        "closes": closes_count,
        "wins": wins,
        "losses": losses,
        "win_rate": round(wr, 1),
        "total_volume": round(total_volume, 2),
        "total_fees": round(total_fees, 2),
        "max_dd": round(max_dd * 100, 1),
        "signals": signals_total,
        "filled": filled_total,
        "session_filtered": session_filtered,
        "regime_filtered": regime_filtered,
        "fill_rate": round(filled_total / max(1, signals_total) * 100, 1),
        "regime_stats": regime_stats,
        "regime_skipped": regime_skipped_trades,
        "trades": trades,
        "monthly_projection": round(monthly_proj, 2),
    }


# ---------------------------------------------------------------------------
# Fee-tier sensitivity wrapper
# ---------------------------------------------------------------------------

def run_fee_sensitivity(candles, btc_candles, regime_gated=False):
    """Run backtest across all fee tiers."""
    results = {}
    for tier_name, fee_bps in FEE_TIERS.items():
        r = run_backtest(candles, btc_candles, fee_bps, regime_gated=regime_gated)
        results[tier_name] = r
    return results


# ---------------------------------------------------------------------------
# Skipped trade analysis
# ---------------------------------------------------------------------------

def analyze_skipped(gated_result, candles, btc_candles):
    """
    Replay the skipped signals with full size (no gate) to show
    what would have happened.
    """
    skipped = gated_result.get("regime_skipped", [])
    if not skipped:
        return {"count": 0, "would_have_won": 0, "would_have_lost": 0, "would_have_pnl": 0.0}

    won = 0
    lost = 0
    total_pnl = 0.0

    for skip in skipped:
        entry_price = skip["entry_price"]
        tp = entry_price * (1 + TP_PCT / 100.0)

        # Scan forward candles for exit
        entry_ts = skip["ts"]
        exit_price = None
        hold_bars = 0
        found_entry = False

        for c in candles:
            c_ts = int(c.get("start", 0))
            if c_ts <= entry_ts:
                continue
            if not found_entry:
                found_entry = True

            high = float(c["high"])
            low = float(c["low"])
            close = float(c["close"])
            hold_bars += 1

            if high >= tp:
                exit_price = tp
                break
            if hold_bars >= MAX_HOLD:
                exit_price = close
                break

        if exit_price is None:
            # No exit found within data — assume timeout at last candle
            if candles:
                exit_price = float(candles[-1]["close"])
                hold_bars = MAX_HOLD
            else:
                continue

        # PnL calculation at 40bps (worst case)
        fee_rate = 0.0040
        entry_slip = ENTRY_SLIPPAGE_BPS / 10000.0
        actual_entry = entry_price * (1 + entry_slip)
        actual_exit = exit_price  # 0bps exit slippage

        deploy = STARTING_CASH  # full size
        entry_fee = deploy * fee_rate
        units = (deploy - entry_fee) / actual_entry
        exit_fee = actual_exit * units * fee_rate
        gross = (actual_exit - actual_entry) * units
        net = gross - entry_fee - exit_fee

        total_pnl += net
        if net > 0:
            won += 1
        else:
            lost += 1

    count = won + lost
    return {
        "count": count,
        "would_have_won": won,
        "would_have_lost": lost,
        "win_rate": round(won / max(1, count) * 100, 1),
        "would_have_pnl": round(total_pnl, 2),
        "would_have_return_pct": round(total_pnl / STARTING_CASH * 100, 1),
    }


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def print_table(title, rows, headers):
    """Print a simple aligned table."""
    widths = [len(h) for h in headers]
    for row in rows:
        for i, cell in enumerate(row):
            widths[i] = max(widths[i], len(str(cell)))

    fmt = "  ".join(f"{{:<{w}}}" for w in widths)
    print(f"\n{title}")
    print("  " + "-" * (sum(widths) + 2 * (len(headers) - 1)))
    print(fmt.format(*headers))
    print("  " + "-" * (sum(widths) + 2 * (len(headers) - 1)))
    for row in rows:
        print(fmt.format(*[str(c) for c in row]))


def main():
    print("=" * 80)
    print("  REGIME-GATED BENCHMARK — RAVE RSI MR (30d)")
    print("=" * 80, flush=True)

    client = CoinbaseAdvancedClient()
    now = int(time.time())
    start = now - 30 * 24 * 3600

    print(f"\nFetching 30d M5 candles for {PRODUCT} and {BTC}...", flush=True)
    candles = fetch_candles(client, PRODUCT, start, now)
    btc_candles = fetch_candles(client, BTC, start, now)
    print(f"  {PRODUCT}: {len(candles)} candles", flush=True)
    print(f"  {BTC}: {len(btc_candles)} candles", flush=True)

    if len(candles) < 100:
        print("ERROR: Insufficient candle data. Aborting.", flush=True)
        return

    # ----- GATED -----
    print("\n[1/3] Running REGIME-GATED backtest across fee tiers...", flush=True)
    gated_results = run_fee_sensitivity(candles, btc_candles, regime_gated=True)

    # ----- UNGATED (baseline) -----
    print("[2/3] Running UNGATED baseline across fee tiers...", flush=True)
    ungated_results = run_fee_sensitivity(candles, btc_candles, regime_gated=False)

    # ----- Skipped trade analysis -----
    print("[3/3] Analyzing skipped trades...", flush=True)
    # Use 40bps gated result for skip analysis
    gated_40bps = gated_results.get("40bps", {})
    skipped_analysis = analyze_skipped(gated_40bps, candles, btc_candles)

    # ----- Build report -----
    report = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "product": PRODUCT,
        "window": "30d",
        "candles_received": len(candles),
        "btc_candles_received": len(btc_candles),
        "strategy": "RSI Mean Reversion",
        "params": {
            "rsi_period": RSI_PERIOD,
            "os_thresh": OS_THRESH,
            "tp_pct": TP_PCT,
            "max_hold": MAX_HOLD,
        },
        "regime_gate": {
            "skip_below": REGIME_SKIP,
            "half_below": REGIME_HALF,
            "warmup_candles": REGIME_WARMUP,
        },
        "execution": {
            "entry_slippage_bps": ENTRY_SLIPPAGE_BPS,
            "exit_slippage_bps": EXIT_SLIPPAGE_BPS,
            "fill_prob": FILL_PROB,
            "starting_cash": STARTING_CASH,
        },
        "gated": {tier: {k: v for k, v in r.items() if k != "trades"}
                  for tier, r in gated_results.items()},
        "ungated": {tier: {k: v for k, v in r.items() if k != "trades"}
                    for tier, r in ungated_results.items()},
        "skipped_trades_analysis": skipped_analysis,
        "gated_regime_stats_40bps": gated_results.get("40bps", {}).get("regime_stats", {}),
        "skipped_count": len(gated_40bps.get("regime_skipped", [])),
    }

    # Save report
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")

    # ====== PRINT SUMMARY ======

    # 1. Gated vs Ungated (40bps)
    g = gated_results.get("40bps", {})
    u = ungated_results.get("40bps", {})

    print("\n" + "=" * 80)
    print("  RESULTS — 40bps (current tier)")
    print("=" * 80)

    comparison_rows = [
        ["Net PnL",          f"${g['net']:.2f}",      f"${u['net']:.2f}",      f"${g['net'] - u['net']:+.2f}"],
        ["Return %",         f"{g['return_pct']}%",    f"{u['return_pct']}%",    f"{g['return_pct'] - u['return_pct']:+.1f}%"],
        ["Win Rate",         f"{g['win_rate']}%",      f"{u['win_rate']}%",      f"{g['win_rate'] - u['win_rate']:+.1f}%"],
        ["Max DD",           f"{g['max_dd']}%",        f"{u['max_dd']}%",        f"{g['max_dd'] - u['max_dd']:+.1f}%"],
        ["Trades Closed",    g['closes'],              u['closes'],              f"{g['closes'] - u['closes']:+d}"],
        ["Signals",          g['signals'],             u['signals'],             f"{g['signals'] - u['signals']:+d}"],
        ["Regime Filtered",  g['regime_filtered'],     "N/A",                   ""],
        ["Total Fees",       f"${g['total_fees']:.2f}", f"${u['total_fees']:.2f}", f"${g['total_fees'] - u['total_fees']:+.2f}"],
        ["Monthly Proj",     f"${g['monthly_projection']:.2f}", f"${u['monthly_projection']:.2f}", f"${g['monthly_projection'] - u['monthly_projection']:+.2f}"],
    ]
    print_table("  GATED vs UNGATED (40bps)", comparison_rows,
                ["Metric", "Gated", "Ungated", "Delta"])

    # 2. Fee tier sensitivity — gated
    print("\n" + "-" * 80)
    print("  FEE TIER SENSITIVITY — REGIME-GATED")
    print("-" * 80)
    tier_rows = []
    for tier_name in ["40bps", "25bps", "15bps", "10bps"]:
        r = gated_results.get(tier_name, {})
        tier_rows.append([
            tier_name,
            f"${r.get('net', 0):.2f}",
            f"{r.get('return_pct', 0)}%",
            f"{r.get('win_rate', 0)}%",
            f"{r.get('max_dd', 0)}%",
            r.get('closes', 0),
            f"${r.get('monthly_projection', 0):.2f}",
        ])
    print_table("  Gated", tier_rows,
                ["Fee", "Net PnL", "Return", "WR", "Max DD", "Trades", "Monthly Proj"])

    # 3. Fee tier sensitivity — ungated
    print("\n" + "-" * 80)
    print("  FEE TIER SENSITIVITY — UNGATED")
    print("-" * 80)
    tier_rows = []
    for tier_name in ["40bps", "25bps", "15bps", "10bps"]:
        r = ungated_results.get(tier_name, {})
        tier_rows.append([
            tier_name,
            f"${r.get('net', 0):.2f}",
            f"{r.get('return_pct', 0)}%",
            f"{r.get('win_rate', 0)}%",
            f"{r.get('max_dd', 0)}%",
            r.get('closes', 0),
            f"${r.get('monthly_projection', 0):.2f}",
        ])
    print_table("  Ungated", tier_rows,
                ["Fee", "Net PnL", "Return", "WR", "Max DD", "Trades", "Monthly Proj"])

    # 4. Per-regime breakdown (gated, 40bps)
    regime_stats = gated_results.get("40bps", {}).get("regime_stats", {})
    print("\n" + "-" * 80)
    print("  PER-REGIME BREAKDOWN — GATED (40bps)")
    print("-" * 80)
    regime_rows = []
    for regime_name in ["HOT", "COLD", "CHOPPY"]:
        rs = regime_stats.get(regime_name, {})
        t = rs.get("trades", 0)
        w = rs.get("wins", 0)
        l = rs.get("losses", 0)
        pnl = rs.get("pnl", 0.0)
        wr = round(w / max(1, t) * 100, 1) if t > 0 else 0.0
        regime_rows.append([
            regime_name,
            t,
            f"{wr}%",
            f"${pnl:.2f}",
        ])
    print_table("  Regime", regime_rows, ["Regime", "Trades", "WR", "Net PnL"])

    # 5. Skipped trades analysis
    print("\n" + "-" * 80)
    print("  SKIPPED TRADES — What would have happened (at 40bps)")
    print("-" * 80)
    sk = skipped_analysis
    print(f"  Signals skipped (CHOPPY):  {sk['count']}")
    print(f"  Would have won:            {sk['would_have_won']}")
    print(f"  Would have lost:           {sk['would_have_lost']}")
    if sk['count'] > 0:
        print(f"  Skipped WR:                {sk['win_rate']}%")
        print(f"  Skipped PnL:               ${sk['would_have_pnl']:.2f}")
        print(f"  Skipped Return:            {sk['would_have_return_pct']}%")
    else:
        print("  No trades were skipped.")

    # 6. Verdict
    print("\n" + "=" * 80)
    gated_net = gated_results.get("40bps", {}).get("net", 0)
    ungated_net = ungated_results.get("40bps", {}).get("net", 0)
    improvement = gated_net - ungated_net
    gated_dd = gated_results.get("40bps", {}).get("max_dd", 0)
    ungated_dd = ungated_results.get("40bps", {}).get("max_dd", 0)

    print("  VERDICT")
    print("=" * 80)
    if improvement > 0:
        print(f"  REGIME GATING IMPROVES PnL by ${improvement:+.2f} at 40bps", flush=True)
    else:
        print(f"  REGIME GATING REDUCES PnL by ${improvement:+.2f} at 40bps", flush=True)
    print(f"  Max DD: {gated_dd}% (gated) vs {ungated_dd}% (ungated) = {gated_dd - ungated_dd:+.1f}pp", flush=True)
    print(f"  Trades: {gated_results.get('40bps', {}).get('closes', 0)} gated vs {ungated_results.get('40bps', {}).get('closes', 0)} ungated", flush=True)
    print(f"  Report saved to: {REPORT_PATH}", flush=True)
    print("=" * 80, flush=True)


if __name__ == "__main__":
    main()
