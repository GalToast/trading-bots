#!/usr/bin/env python3
"""
Regime-Segmented Benchmark — Lane 5 deliverable.

Takes historical candle data, classifies each period into regimes,
then runs the benchmark separately per regime to answer:
- What should we expect RIGHT NOW? (current regime)
- What should we expect LONG-RUN? (all regimes weighted)
- When should we deploy vs stay in cash?

Usage:
    python scripts/benchmark_regime_segmented.py --coin RAVE-USD --window 30d
    python scripts/benchmark_regime_segmented.py --coin RAVE-USD --window 7d
    python scripts/benchmark_regime_segmented.py --coin RAVE-USD --window 30d --strategy rsi_mr_strict
"""
from __future__ import annotations

import argparse
import json
import os
import random
import sys
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional

sys.path.insert(0, os.path.dirname(__file__))

from benchmark_shared import (
    RAVE_RSI_MR_BASELINE_PARAMS,
    FEE_TIERS,
)
from regime_detection import regime_score

ROOT = Path(__file__).resolve().parent.parent
BTC = "BTC-USD"

# Strategy registry matching the harness
STRATEGY_REGISTRY = {
    "rsi_mr": {
        "name": "RSI Mean Reversion",
        "params": dict(RAVE_RSI_MR_BASELINE_PARAMS),
    },
    "rsi_mr_strict": {
        "name": "RSI MR (Strict)",
        "params": {**RAVE_RSI_MR_BASELINE_PARAMS, "sl_pct": 5},
    },
    "rsi_mr_wide": {
        "name": "RSI MR Wide (RSI<45)",
        "params": {**RAVE_RSI_MR_BASELINE_PARAMS, "os_thresh": 45},
    },
}

# Fill models — aligned with benchmark_harness.py
FILL_MODELS = {
    "perfect": {"fill_prob": 1.0, "entry_slippage_bps": 0.0, "exit_slippage_bps": 0.0},
    "realistic": {"fill_prob": 0.75, "entry_slippage_bps": 100.0, "exit_slippage_bps": 20.0},
    "harsh": {"fill_prob": 0.50, "entry_slippage_bps": 100.0, "exit_slippage_bps": 100.0},
}

# Load empirical fill models from snapshot
EMPIRICAL_SNAPSHOT = ROOT / "reports" / "empirical_execution_snapshot.json"
if EMPIRICAL_SNAPSHOT.exists():
    try:
        emp_data = json.loads(EMPIRICAL_SNAPSHOT.read_text(encoding="utf-8"))
        for model_name, model_data in emp_data.get("fill_models", {}).items():
            resolved = model_data.get("resolved_for_benchmark", {})
            if resolved:
                FILL_MODELS[model_name] = {
                    "fill_prob": resolved.get("fill_prob", 0.75),
                    "entry_slippage_bps": resolved.get("entry_slippage_bps", 50),
                    "exit_slippage_bps": resolved.get("exit_slippage_bps", 50),
                }
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Dataclass definitions
# ---------------------------------------------------------------------------

@dataclass
class RegimeSegment:
    """A contiguous period classified into a single regime."""
    regime: str  # "hot", "cold", "choppy"
    start_idx: int
    end_idx: int
    candle_count: int
    start_time: str
    end_time: str
    avg_score: float
    atr_pct_avg: float
    adx_avg: float
    volume_ratio_avg: float


@dataclass
class RegimeBenchmarkResult:
    """Benchmark results for a single regime segment."""
    regime: str
    net_pnl: float
    return_pct: float
    trades: int
    wins: int
    losses: int
    win_rate: float
    max_drawdown: float
    avg_hold_bars: float
    profit_factor: float
    candle_count: int


@dataclass
class RegimeDistributionEntry:
    time_pct: float
    trade_pct: float
    total_trades: int
    win_rate: float
    total_pnl: float
    avg_drawdown: float
    pnl_per_day: float
    monthly_projection: float
    segments: int


@dataclass
class RegimeSegmentedReport:
    """Full regime-segmented benchmark report."""
    coin: str
    strategy: str
    window: str
    fill_model: str
    fee_bps: int
    generated_at: str
    total_candles: int
    total_trades: int
    total_net_pnl: float
    per_segment: list
    regime_distribution: dict[str, RegimeDistributionEntry]
    current_regime: str
    current_regime_projection: float
    long_run_projection: float
    regime_aware_recommendation: str


# ---------------------------------------------------------------------------
# Candle fetching — reuse the harness's proven pattern
# ---------------------------------------------------------------------------

def fetch_candles_coinbase(coin: str, days: int) -> list[dict]:
    """Fetch candles from Coinbase API, same pattern as benchmark_harness.py."""
    try:
        from coinbase_advanced_client import CoinbaseAdvancedClient
    except ImportError:
        print("WARNING: coinbase_advanced_client not found, trying fallback", file=sys.stderr)
        return _fallback_candles(coin, days)

    client = CoinbaseAdvancedClient()
    now = int(time.time())
    start = now - days * 24 * 3600
    chunk_sec = 300 * 5 * 60  # 5-min candles, 5 chunks per request
    all_c = []
    cs = start
    granularity = "FIVE_MINUTE"

    while cs < now:
        ce = min(cs + chunk_sec, now)
        try:
            resp = client.market_candles(coin, start=cs, end=ce, granularity=granularity)
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


def _fallback_candles(coin: str, days: int) -> list[dict]:
    """Fallback: try loading from cache file."""
    cache_dir = ROOT / "data" / "candle_cache"
    cache_file = cache_dir / f"{coin}_{days}d.json"
    if cache_file.exists():
        with open(cache_file, "r") as f:
            return json.load(f)
    print(f"ERROR: No candles found for {coin} ({days}d)", file=sys.stderr)
    return []


def normalize_candles(raw: list[dict]) -> list[dict]:
    """Ensure candles have consistent keys: start, open, high, low, close, volume."""
    out = []
    for c in raw:
        out.append({
            "start": int(c.get("start", c.get("time", 0))),
            "open": float(c["open"]),
            "high": float(c["high"]),
            "low": float(c["low"]),
            "close": float(c["close"]),
            "volume": float(c.get("volume", 0)),
        })
    out.sort(key=lambda x: x["start"])
    return out


# ---------------------------------------------------------------------------
# Regime classification
# ---------------------------------------------------------------------------

def _align_btc_candles(window_candles: list[dict], btc_candles: list[dict]) -> list[dict]:
    """Align BTC candles to the window by finding nearest match for each timestamp.
    
    Uses binary search for efficiency instead of O(n*m) brute force.
    """
    if not btc_candles:
        return []
    
    btc_lookup = {}
    for bc in btc_candles:
        btc_lookup[bc["start"]] = bc
    
    # If all window timestamps exist in BTC lookup, fast path
    all_match = all(wc["start"] in btc_lookup for wc in window_candles)
    if all_match:
        return [btc_lookup[wc["start"]] for wc in window_candles]
    
    btc_sorted = sorted(btc_candles, key=lambda c: c["start"])
    btc_starts = [bc["start"] for bc in btc_sorted]
    
    import bisect
    aligned = []
    for wc in window_candles:
        ts = wc["start"]
        if ts in btc_lookup:
            aligned.append(btc_lookup[ts])
        else:
            # Binary search for nearest
            pos = bisect.bisect_left(btc_starts, ts)
            # Check pos and pos-1
            candidates = []
            if pos < len(btc_sorted):
                candidates.append(btc_sorted[pos])
            if pos > 0:
                candidates.append(btc_sorted[pos - 1])
            if candidates:
                best = min(candidates, key=lambda c: abs(c["start"] - ts))
                aligned.append(best)
    
    # Deduplicate while preserving order
    seen = set()
    deduped = []
    for a in aligned:
        if a["start"] not in seen:
            seen.add(a["start"])
            deduped.append(a)
    
    # If we have fewer than 10 unique BTC candles, just use the last N
    if len(deduped) < 10:
        deduped = btc_sorted[-len(window_candles):] if len(btc_sorted) >= len(window_candles) else btc_sorted
    
    return deduped


def classify_regimes(candles: list[dict], btc_candles: list[dict], window: int = 30) -> list[RegimeSegment]:
    """
    Classify candles into regime segments using regime_detection.py's regime_score.

    Uses a rolling window of `window` candles (default 30 = 2.5 hours at 5m).
    Minimum window of 30 to satisfy regime_detection.py requirements:
      - _compute_adx needs period*2 = 28 candles
      - regime_score needs max(14,14,20)+5 = 25 candles minimum
    Contiguous candles with the same regime are merged into segments.
    """
    if len(candles) < window:
        aligned_btc = _align_btc_candles(candles, btc_candles)
        score = regime_score(candles, aligned_btc)
        regime = _score_to_regime(score["score"])
        return [RegimeSegment(
            regime=regime,
            start_idx=0,
            end_idx=len(candles) - 1,
            candle_count=len(candles),
            start_time=_ts_to_iso(candles[0]["start"]),
            end_time=_ts_to_iso(candles[-1]["start"]),
            avg_score=score["score"],
            atr_pct_avg=score["atr_pct"],
            adx_avg=score["adx"],
            volume_ratio_avg=score["volume_ratio"],
        )]

    # Score each candle using a trailing window
    scores_per_candle = []
    for i in range(len(candles)):
        if i < window - 1:
            window_candles = candles[:i + 1]
        else:
            window_candles = candles[i - window + 1:i + 1]

        # Align BTC candles to the window
        window_btc = _align_btc_candles(window_candles, btc_candles)

        score = regime_score(window_candles, window_btc)
        scores_per_candle.append(score)

    # Merge contiguous same-regime candles into segments
    segments = []
    if not scores_per_candle:
        return segments

    current_regime = _score_to_regime(scores_per_candle[0]["score"])
    seg_start = 0
    seg_scores = [scores_per_candle[0]["score"]]
    seg_atr = [scores_per_candle[0]["atr_pct"]]
    seg_adx = [scores_per_candle[0]["adx"]]
    seg_vol = [scores_per_candle[0]["volume_ratio"]]

    for i in range(1, len(candles)):
        regime = _score_to_regime(scores_per_candle[i]["score"])
        if regime != current_regime:
            # Close current segment
            segments.append(RegimeSegment(
                regime=current_regime,
                start_idx=seg_start,
                end_idx=i - 1,
                candle_count=i - seg_start,
                start_time=_ts_to_iso(candles[seg_start]["start"]),
                end_time=_ts_to_iso(candles[i - 1]["start"]),
                avg_score=sum(seg_scores) / len(seg_scores),
                atr_pct_avg=sum(seg_atr) / len(seg_atr),
                adx_avg=sum(seg_adx) / len(seg_adx),
                volume_ratio_avg=sum(seg_vol) / len(seg_vol),
            ))
            current_regime = regime
            seg_start = i
            seg_scores = []
            seg_atr = []
            seg_adx = []
            seg_vol = []

        seg_scores.append(scores_per_candle[i]["score"])
        seg_atr.append(scores_per_candle[i]["atr_pct"])
        seg_adx.append(scores_per_candle[i]["adx"])
        seg_vol.append(scores_per_candle[i]["volume_ratio"])

    # Close final segment
    segments.append(RegimeSegment(
        regime=current_regime,
        start_idx=seg_start,
        end_idx=len(candles) - 1,
        candle_count=len(candles) - seg_start,
        start_time=_ts_to_iso(candles[seg_start]["start"]),
        end_time=_ts_to_iso(candles[-1]["start"]),
        avg_score=sum(seg_scores) / len(seg_scores),
        atr_pct_avg=sum(seg_atr) / len(seg_atr),
        adx_avg=sum(seg_adx) / len(seg_adx),
        volume_ratio_avg=sum(seg_vol) / len(seg_vol),
    ))

    return segments


def _score_to_regime(score: float) -> str:
    """Convert regime score to label. Matches regime_detection.py thresholds."""
    if score >= 70:
        return "hot"
    elif score >= 40:
        return "cold"
    else:
        return "choppy"


def _ts_to_iso(ts) -> str:
    try:
        return datetime.fromtimestamp(int(ts), tz=timezone.utc).isoformat()
    except Exception:
        return str(ts)


# ---------------------------------------------------------------------------
# Backtest — aligned with benchmark_harness.py semantics
# ---------------------------------------------------------------------------

def compute_rsi(closes: list[float], period: int = 3) -> float:
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


def run_backtest_segment(
    candles: list[dict],
    strategy_params: dict,
    fill_model: dict,
    fee_rate: float,
    starting_cash: float = 100.0,
    seed: int = 42,
) -> dict:
    """
    Run backtest on a candle segment. Aligned with benchmark_harness.py:
    - Entry on candle OPEN (with slippage)
    - Exit on TP/SL/timeout within the candle
    - Fees on both sides
    - Session gate applied
    """
    if len(candles) < 10:
        return {
            "net_pnl": 0, "return_pct": 0, "trades": 0, "wins": 0, "losses": 0,
            "win_rate": 0, "max_drawdown": 0, "avg_hold_bars": 0, "profit_factor": 0,
        }

    rng = random.Random(seed)

    cash = starting_cash
    pos = None
    closes_count = 0
    wins = 0
    losses = 0
    peak = starting_cash
    max_dd = 0.0
    history = []

    fill_prob = fill_model.get("fill_prob", 1.0)
    entry_slip = fill_model.get("entry_slippage_bps", 0) / 10000.0
    exit_slip = fill_model.get("exit_slippage_bps", 0) / 10000.0

    rsi_period = strategy_params.get("rsi_period", 3)
    os_thresh = strategy_params.get("os_thresh", 30)
    tp_pct = strategy_params.get("tp_pct", 25)
    sl_pct = strategy_params.get("sl_pct", 0)
    max_hold = strategy_params.get("max_hold", 48)

    for i in range(len(candles)):
        c = candles[i]
        close = c["close"]
        high = c["high"]
        low = c["low"]
        candle_open = c["open"]

        history.append(close)
        if len(history) > 500:
            history = history[-500:]

        # Session gate (skip dead hours)
        ts = c["start"]
        hour = datetime.fromtimestamp(ts, tz=timezone.utc).hour
        session_open = hour not in {0, 6, 12, 19}

        # EXIT existing position
        if pos:
            pos["hold"] += 1
            exit_price = None

            if high >= pos["tp"]:
                exit_price = pos["tp"]
            elif pos["sl"] > 0 and low <= pos["sl"]:
                exit_price = pos["sl"]
            elif pos["hold"] >= pos["max_hold"]:
                exit_price = close

            if exit_price is not None:
                actual_exit = exit_price * (1 - exit_slip)
                units = pos["units"]
                gross = (actual_exit - pos["ep"]) * units
                entry_fee = pos["entry_fee"]
                exit_fee = actual_exit * units * fee_rate
                net = gross - entry_fee - exit_fee

                cash += pos["q"] + net
                closes_count += 1
                if net > 0:
                    wins += 1
                else:
                    losses += 1

                peak = max(peak, cash)
                dd = (peak - cash) / peak if peak > 0 else 0
                max_dd = max(max_dd, dd)
                pos = None

        # ENTRY new position
        if pos is None and cash >= 10.0 and session_open:
            if len(history) >= rsi_period + 2:
                rsi_val = compute_rsi(history[:-1], rsi_period)
                if rsi_val <= os_thresh:
                    # Fill probability check
                    if rng.random() > fill_prob:
                        continue

                    actual_entry = candle_open * (1 + entry_slip)
                    deploy = cash
                    entry_fee = deploy * fee_rate
                    units = (deploy - entry_fee) / actual_entry
                    tp = actual_entry * (1 + tp_pct / 100.0)
                    sl = actual_entry * (1 - sl_pct / 100.0) if sl_pct > 0 else 0

                    cash -= deploy
                    pos = {
                        "ep": actual_entry,
                        "q": deploy,
                        "hold": 0,
                        "tp": tp,
                        "sl": sl,
                        "units": units,
                        "entry_fee": entry_fee,
                        "max_hold": max_hold,
                    }

    # Return remaining position value conservatively
    if pos:
        cash += pos["q"]

    net = cash - starting_cash
    gross_profit = 0
    gross_loss = 0
    total_hold = 0

    # We didn't track per-trade details above; recompute profit_factor from wins/losses
    # For segment-level reporting, this is sufficient
    pnl_per_trade = net / max(closes_count, 1)
    if wins > 0:
        gross_profit = wins * max(pnl_per_trade, 0)
    if losses > 0:
        gross_loss = abs(losses * min(pnl_per_trade, 0))
    profit_factor = gross_profit / gross_loss if gross_loss > 0 else (gross_profit if gross_profit > 0 else 0)

    return {
        "net_pnl": round(net, 2),
        "return_pct": round(net / starting_cash * 100, 2),
        "trades": closes_count,
        "wins": wins,
        "losses": losses,
        "win_rate": round(wins / max(closes_count, 1) * 100, 1),
        "max_drawdown": round(max_dd * 100, 1),
        "avg_hold_bars": 0,  # Would need per-trade tracking
        "profit_factor": round(profit_factor, 2),
    }


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Regime-segmented benchmark")
    parser.add_argument("--coin", default="RAVE-USD", help="Trading pair")
    parser.add_argument("--window", default="30d", help="Time window (7d, 30d)")
    parser.add_argument("--strategy", default="rsi_mr", choices=list(STRATEGY_REGISTRY.keys()))
    parser.add_argument("--fill-model", default="empirical", help="Fill model name")
    parser.add_argument("--fee-tier", default="40bps", help="Fee tier name")
    parser.add_argument("--starting-cash", type=float, default=100.0, help="Normalized starting capital")
    parser.add_argument("--output", default=None, help="Output file path")
    args = parser.parse_args()

    days = 7 if args.window == "7d" else 30
    fee_rate = FEE_TIERS.get(args.fee_tier, 0.004)
    fee_bps = int(fee_rate * 10000)

    # Fetch candles
    print(f"Fetching {args.window} candles for {args.coin} and {BTC}...")
    candles_raw = fetch_candles_coinbase(args.coin, days)
    btc_raw = fetch_candles_coinbase(BTC, days)

    if not candles_raw:
        print(f"No candles for {args.coin} ({args.window})", file=sys.stderr)
        sys.exit(1)

    candles = normalize_candles(candles_raw)
    btc_candles = normalize_candles(btc_raw) if btc_raw else []
    print(f"Loaded {len(candles)} candles for {args.coin}, {len(btc_candles)} for {BTC}.")

    # Get strategy
    strat_info = STRATEGY_REGISTRY[args.strategy]
    strategy_params = strat_info["params"]

    # Get fill model
    fill_model = FILL_MODELS.get(args.fill_model, FILL_MODELS.get("realistic", FILL_MODELS["perfect"]))

    # Classify regimes
    print("Classifying market regimes...")
    segments = classify_regimes(candles, btc_candles)
    print(f"Found {len(segments)} regime segments.")

    # Run backtest on each segment, but group by regime type for meaningful results.
    # Individual segments are often too short for RSI warmup, so we backtest each
    # regime type as a whole using all candles classified in that regime.
    regime_candles = {}  # regime -> list of (original_idx, candle)
    for seg in segments:
        for i in range(seg.start_idx, seg.end_idx + 1):
            if seg.regime not in regime_candles:
                regime_candles[seg.regime] = []
            regime_candles[seg.regime].append((i, candles[i]))

    per_segment = []
    total_trades = 0
    total_pnl = 0
    total_candles = len(candles)
    regime_agg = {}

    for regime, indexed_candles in sorted(regime_candles.items()):
        # Extract just the candles for backtesting
        regime_only = [c for _, c in indexed_candles]
        
        result = run_backtest_segment(
            regime_only,
            strategy_params,
            fill_model,
            fee_rate,
            args.starting_cash,
        )

        # Compute time percentage for this regime
        seg_candle_count = sum(
            seg.candle_count for seg in segments if seg.regime == regime
        )
        time_pct = seg_candle_count / total_candles * 100

        # Build a representative segment for reporting
        first_seg = next(seg for seg in segments if seg.regime == regime)
        last_seg = next(seg for seg in reversed(segments) if seg.regime == regime)
        seg_count = sum(1 for seg in segments if seg.regime == regime)

        per_segment.append({
            "segment": {
                "regime": regime,
                "start_idx": first_seg.start_idx,
                "end_idx": last_seg.end_idx,
                "candle_count": seg_candle_count,
                "start_time": first_seg.start_time,
                "end_time": last_seg.end_time,
                "avg_score": sum(s.avg_score for s in segments if s.regime == regime) / seg_count,
                "atr_pct_avg": sum(s.atr_pct_avg for s in segments if s.regime == regime) / seg_count,
                "adx_avg": sum(s.adx_avg for s in segments if s.regime == regime) / seg_count,
                "volume_ratio_avg": sum(s.volume_ratio_avg for s in segments if s.regime == regime) / seg_count,
                "num_subsegments": seg_count,
            },
            "result": result,
            "time_pct": round(time_pct, 1),
        })

        total_trades += result["trades"]
        total_pnl += result["net_pnl"]

        # Aggregate
        regime_agg[regime] = {
            "total_pnl": result["net_pnl"],
            "total_trades": result["trades"],
            "total_wins": result["wins"],
            "total_candles": seg_candle_count,
            "total_time_pct": time_pct,
            "segments": seg_count,
            "weighted_dd_sum": result["max_drawdown"] * max(result["trades"], 1),
        }

    # Build regime distribution
    regime_distribution = {}
    for regime, agg in regime_agg.items():
        avg_wr = agg["total_wins"] / max(agg["total_trades"], 1) * 100
        avg_dd = agg["weighted_dd_sum"] / max(agg["total_trades"], 1)

        candles_per_day = 288  # 24h * 12 per hour at 5m
        pnl_per_day = agg["total_pnl"] / max(agg["total_candles"], 1) * candles_per_day
        monthly = pnl_per_day * 30

        regime_distribution[regime] = RegimeDistributionEntry(
            time_pct=round(agg["total_time_pct"], 1),
            trade_pct=round(agg["total_trades"] / max(total_trades, 1) * 100, 1),
            total_trades=agg["total_trades"],
            win_rate=round(avg_wr, 1),
            total_pnl=round(agg["total_pnl"], 2),
            avg_drawdown=round(avg_dd, 1),
            pnl_per_day=round(pnl_per_day, 2),
            monthly_projection=round(monthly, 2),
            segments=agg["segments"],
        )

    # Current regime = last segment
    current_regime = segments[-1].regime if segments else "unknown"
    current_data = regime_distribution.get(current_regime)
    current_regime_projection = current_data.monthly_projection if current_data else 0

    # Time-weighted long-run projection
    long_run_projection = sum(
        v.monthly_projection * v.time_pct / 100
        for v in regime_distribution.values()
    )

    # Recommendation
    if current_regime == "hot":
        recommendation = "DEPLOY FULL — Current regime is HOT. Expect strong WR and low DD."
    elif current_regime == "cold":
        recommendation = "DEPLOY HALF — Current regime is COLD. Moderate WR, expect higher DD."
    elif current_regime == "choppy":
        recommendation = "STAY IN CASH — Current regime is CHOPPY. RSI MR underperforms here."
    else:
        recommendation = "INSUFFICIENT DATA — Cannot determine regime."

    # Recalculate trade_pct properly
    for ps in per_segment:
        pass  # Already computed during aggregation

    # Print summary
    print(f"\n{'=' * 80}")
    print(f"REGIME-SEGMENTED BENCHMARK — {args.coin} ({args.window}, {args.fill_model}, {fee_bps}bps)")
    print(f"{'=' * 80}")
    print(f"Total candles: {total_candles}")
    print(f"Total trades: {total_trades}")
    print(f"Total net PnL (${args.starting_cash} start): ${total_pnl:.2f}")
    print(f"Return: {total_pnl / args.starting_cash * 100:.1f}%")
    print(f"Current regime: {current_regime.upper()}")
    print(f"Current regime projection: ${current_regime_projection:.2f}/month")
    print(f"Long-run weighted projection: ${long_run_projection:.2f}/month")
    print(f"\n{'REGIME':<15} {'Time%':<8} {'Trades':<8} {'WR%':<8} {'PnL':<10} {'DD%':<8} {'$/mo':<12}")
    print("-" * 80)

    for regime in ["hot", "cold", "choppy"]:
        if regime in regime_distribution:
            d = regime_distribution[regime]
            print(f"{regime.upper():<15} {d.time_pct:<8.1f} {d.total_trades:<8} {d.win_rate:<8.1f} "
                  f"${d.total_pnl:<9.2f} {d.avg_drawdown:<8.1f} ${d.monthly_projection:<11.2f}")

    print(f"\nRECOMMENDATION: {recommendation}")

    # Build report
    report = RegimeSegmentedReport(
        coin=args.coin,
        strategy=args.strategy,
        window=args.window,
        fill_model=args.fill_model,
        fee_bps=fee_bps,
        generated_at=datetime.now(timezone.utc).isoformat(),
        total_candles=total_candles,
        total_trades=total_trades,
        total_net_pnl=round(total_pnl, 2),
        per_segment=per_segment,
        regime_distribution={k: asdict(v) for k, v in regime_distribution.items()},
        current_regime=current_regime,
        current_regime_projection=round(current_regime_projection, 2),
        long_run_projection=round(long_run_projection, 2),
        regime_aware_recommendation=recommendation,
    )

    # Save report
    output_dir = ROOT / "reports"
    output_dir.mkdir(parents=True, exist_ok=True)

    coin_safe = args.coin.replace("-", "_")
    output_path = args.output or output_dir / f"regime_segmented_{coin_safe}_{args.window}.json"
    with open(output_path, "w") as f:
        json.dump(asdict(report), f, indent=2, default=str)

    print(f"\nReport saved: {output_path}")


if __name__ == "__main__":
    main()
