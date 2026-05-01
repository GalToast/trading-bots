#!/usr/bin/env python3
"""
Regime Phase Classifier — Oscillation vs Trend Detection

Detects whether a market is in an oscillation phase (mean-reversion works)
or a trend phase (mean-reversion dies). Runs against real 30-day candle data
from reports/candle_cache/.

Metrics:
    1. ATR Expansion Rate: ATR over rolling windows. If current ATR > 1.5x the
       20-period average ATR, flag as potential trend phase.
    2. Level Penetration Coherence: For Fibonacci levels (swing high to swing
       low), count how often price returns to previous levels vs escapes beyond
       them. High return rate = oscillation. Low return rate = trend.
    3. Temporal Stability: Split data into equal time windows. Compute PnL of a
       mean-reversion strategy in each window. Consistent PnL = oscillation.
       Chaotic or one-direction PnL = trend.
    4. Composite Regime Score: Combine the above into a single score from -1
       (strong trend) to +1 (strong oscillation).

Output:
    Per-coin regime score, phase transition points, and correlation with
    mean-reversion strategy PnL. Saved to reports/regime_phase_classifier.json.

Usage:
    python scripts/regime_phase_classifier.py
"""
import json
import math
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CACHE_DIR = ROOT / "reports" / "candle_cache"
OUT_PATH = ROOT / "reports" / "regime_phase_classifier.json"

# ---- helpers ----------------------------------------------------------------

def load_candles(file_path):
    """Load candles from a cache JSON file. Returns list of dicts with
    keys: time, open, high, low, close, volume."""
    raw = json.loads(file_path.read_text())
    if isinstance(raw, dict):
        candles = raw.get("candles", [])
    else:
        candles = raw
    # Normalise key names
    out = []
    for c in candles:
        out.append({
            "time": c.get("time", c.get("t", 0)),
            "open": float(c.get("open", c.get("o", 0))),
            "high": float(c.get("high", c.get("h", 0))),
            "low": float(c.get("low", c.get("l", 0))),
            "close": float(c.get("close", c.get("c", 0))),
            "volume": float(c.get("volume", c.get("v", 0))),
        })
    return out


def compute_atr(bars, period=14):
    """Compute ATR series from OHLC bars. Returns list same length as bars
    (first `period` entries are None)."""
    n = len(bars)
    atr = [None] * n
    if n < period + 1:
        return atr
    trs = []
    for i in range(1, n):
        tr = max(
            bars[i]["high"] - bars[i]["low"],
            abs(bars[i]["high"] - bars[i - 1]["close"]),
            abs(bars[i]["low"] - bars[i - 1]["close"]),
        )
        trs.append(tr)
    # simple moving average of TR
    for i in range(len(trs)):
        if i < period - 1:
            continue
        window = trs[i - period + 1: i + 1]
        atr[i + 1] = sum(window) / period
    return atr


def fibonacci_levels(swing_high, swing_low, n_levels=8):
    """Return Fibonacci retracement levels between swing_low and swing_high."""
    diff = swing_high - swing_low
    ratios = [0.0, 0.236, 0.382, 0.5, 0.618, 0.786, 0.886, 1.0]
    return [swing_low + r * diff for r in ratios[:n_levels]]


def find_swings(bars, lookback=20):
    """Find approximate swing high and swing low over the full series."""
    highs = [b["high"] for b in bars]
    lows = [b["low"] for b in bars]
    sh = max(highs)
    sl = min(lows)
    return sh, sl


# ---- metric 1: ATR Expansion Rate -------------------------------------------

def atr_expansion_rate(bars, atr_period=14, avg_window=20, threshold=1.5):
    """Compute ATR expansion at each point.

    Returns:
        series: list of float ratios (current_atr / avg_atr), None where
                insufficient data.
        flagged: list of bool — True when current ATR > threshold * avg.
    """
    atr = compute_atr(bars, atr_period)
    series = []
    flagged = []
    for i in range(len(bars)):
        if atr[i] is None or i < avg_window:
            series.append(None)
            flagged.append(False)
            continue
        # average ATR over the trailing `avg_window` bars ending at i-1
        chunk = [atr[j] for j in range(i - avg_window, i) if atr[j] is not None]
        if not chunk:
            series.append(None)
            flagged.append(False)
            continue
        avg = sum(chunk) / len(chunk)
        if avg <= 0:
            series.append(None)
            flagged.append(False)
            continue
        ratio = atr[i] / avg
        series.append(ratio)
        flagged.append(ratio > threshold)
    return series, flagged


# ---- metric 2: Level Penetration Coherence ----------------------------------

def level_penetration_coherence(bars, n_levels=8, window=100):
    """For each sliding window, count how often price returns to Fibonacci
    levels vs escapes beyond the swing range.

    Returns a series of coherence values in [0, 1]:
        high  = mostly returns (oscillation)
        low   = mostly escapes (trend)
    """
    n = len(bars)
    if n < window + 2:
        return [None] * n

    series = [None] * (window - 1)
    closes = [b["close"] for b in bars]

    for end in range(window, n + 1):
        seg = bars[end - window: end]
        seg_closes = closes[end - window: end]
        sh, sl = find_swings(seg)
        if sh == sl:
            series.append(0.5)
            continue
        levels = fibonacci_levels(sh, sl, n_levels)

        # Count returns: price touches a level then comes back inside
        returns = 0
        escapes = 0
        for i in range(1, len(seg_closes)):
            prev, cur = seg_closes[i - 1], seg_closes[i]
            # check if price crossed any fib level
            crossed = False
            for lev in levels:
                if (prev <= lev < cur) or (prev >= lev > cur):
                    crossed = True
                    break
            if crossed:
                # Did price subsequently return toward the middle of the range?
                mid = (sh + sl) / 2
                later = seg_closes[i:]
                # if any later candle is closer to mid than the crossing candle
                dist_at_cross = abs(cur - mid)
                came_back = any(abs(p - mid) < dist_at_cross for p in later[1:])
                if came_back:
                    returns += 1
                else:
                    escapes += 1

        total = returns + escapes
        if total == 0:
            series.append(0.5)
        else:
            series.append(returns / total)

    return series


# ---- metric 3: Temporal Stability -------------------------------------------

def temporal_stability(bars, n_windows=6):
    """Split the data into `n_windows` equal windows. In each window,
    simulate a simple mean-reversion strategy (z-score based) and record PnL.

    Returns:
        window_pnls: list of floats
        stability_score: float in [-1, 1]
            positive = consistent PnL (oscillation)
            negative = chaotic / one-direction (trend)
    """
    n = len(bars)
    closes = [b["close"] for b in bars]
    if n < 60:
        return [], 0.0

    window_size = n // n_windows
    if window_size < 30:
        window_size = 30
        n_windows = n // window_size
        if n_windows < 2:
            return [0.0], 0.0

    window_pnls = []
    for w in range(n_windows):
        start = w * window_size
        end = start + window_size
        seg = closes[start: end]

        # mean-reversion: compute rolling z-score, buy when z < -1, sell when z > 1
        lookback = min(20, len(seg) // 3)
        if lookback < 5:
            window_pnls.append(0.0)
            continue

        pnl = 0.0
        position = 0.0
        for i in range(lookback, len(seg)):
            window = seg[i - lookback: i]
            mu = sum(window) / len(window)
            var = sum((x - mu) ** 2 for x in window) / len(window)
            sigma = math.sqrt(var) if var > 0 else 1e-9
            z = (seg[i] - mu) / sigma

            if position == 0 and z < -1.5:
                position = seg[i]  # buy
            elif position > 0 and z > 0.5:
                pnl += seg[i] - position
                position = 0.0
        # if still holding at end, mark to close
        if position > 0:
            pnl += seg[-1] - position

        window_pnls.append(round(pnl, 6))

    # Stability score: coefficient-of-variation based
    # If all PnLs have the same sign and similar magnitude => high oscillation
    # If signs differ wildly or one dominates => trend
    if not window_pnls:
        return [], 0.0

    mean_pnl = sum(window_pnls) / len(window_pnls)
    if mean_pnl == 0:
        return window_pnls, 0.0

    variance = sum((p - mean_pnl) ** 2 for p in window_pnls) / len(window_pnls)
    std_pnl = math.sqrt(variance)
    cv = std_pnl / abs(mean_pnl) if abs(mean_pnl) > 1e-12 else 0.0

    # Map cv to [-1, 1]: cv=0 -> +1 (perfect stability), cv>=2 -> -1 (chaotic)
    stability = max(-1.0, min(1.0, 1.0 - cv))

    return window_pnls, round(stability, 4)


# ---- metric 4: Composite Regime Score ---------------------------------------

def composite_regime_score(atr_flagged_series, lp_coherence, stability_score):
    """Combine the three metric streams into a single score in [-1, +1].

        +1  = strong oscillation
         0  = ambiguous / transition
        -1  = strong trend

    Each component is normalised to [-1, +1] and averaged.
    """
    n = min(
        len([x for x in atr_flagged_series if x is not None]),
        len([x for x in lp_coherence if x is not None]),
    )
    if n < 10:
        return 0.0, []

    # Align on the latest n valid points
    atr_valid = [x for x in atr_flagged_series if x is not None]
    lp_valid = [x for x in lp_coherence if x is not None]

    # ATR component: fraction of bars NOT flagged => mapped to [-1, 1]
    flagged_count = sum(1 for f in atr_valid[-n:] if f)
    atr_norm = 1.0 - 2.0 * (flagged_count / n)  # 0 flagged => +1, all => -1

    # Level penetration: mean coherence already in [0,1] -> map to [-1,1]
    lp_mean = sum(lp_valid[-n:]) / n
    lp_norm = 2.0 * lp_mean - 1.0

    # Stability already in [-1, 1]
    s_norm = stability_score

    composite = round((atr_norm + lp_norm + s_norm) / 3.0, 4)

    # Also build a per-bar rolling composite for transition detection
    rolling = []
    for i in range(n):
        a_flag = atr_valid[-n + i]
        a_norm_i = 1.0 - 2.0 * float(a_flag)
        lp_i = lp_valid[-n + i] if i < len(lp_valid) else 0.5
        lp_norm_i = 2.0 * lp_i - 1.0
        # stability is per-window, so we spread it evenly
        rc = round((a_norm_i + lp_norm_i + s_norm) / 3.0, 4)
        rolling.append(rc)

    return composite, rolling


def detect_transitions(rolling_series, bars, threshold=0.25):
    """Detect where the rolling composite score crosses the threshold between
    oscillation and trend regimes. Returns list of transition dicts."""
    transitions = []
    for i in range(1, len(rolling_series)):
        prev = rolling_series[i - 1]
        cur = rolling_series[i]
        # crossing from >= threshold to < -threshold or vice versa
        if (prev >= threshold and cur < -threshold) or \
           (prev <= -threshold and cur > threshold):
            bar_idx = len(bars) - len(rolling_series) + i
            transitions.append({
                "bar_index": bar_idx,
                "time": bars[bar_idx]["time"] if bar_idx < len(bars) else None,
                "from_regime": "oscillation" if prev >= threshold else "trend",
                "to_regime": "oscillation" if cur >= threshold else "trend",
                "score_before": round(prev, 4),
                "score_after": round(cur, 4),
            })
    return transitions


# ---- per-coin analysis ------------------------------------------------------

def analyse_coin(file_path):
    """Run all four metrics on a single candle file and return results dict."""
    bars = load_candles(file_path)
    if not bars:
        return None

    coin = file_path.stem  # e.g. BTC_USD_FIVE_MINUTE_30d
    closes = [b["close"] for b in bars]

    # 1. ATR Expansion
    atr_series, atr_flagged = atr_expansion_rate(bars)

    # 2. Level Penetration Coherence
    lp_coherence = level_penetration_coherence(bars)

    # 3. Temporal Stability
    window_pnls, stability_score = temporal_stability(bars)

    # 4. Composite Regime Score
    composite, rolling = composite_regime_score(atr_flagged, lp_coherence, stability_score)

    # Phase transitions
    transitions = detect_transitions(rolling, bars)

    # Determine final regime
    if composite > 0.25:
        regime = "oscillation"
    elif composite < -0.25:
        regime = "trend"
    else:
        regime = "transition"

    # Mean-reversion total PnL over full series for correlation check
    total_mr_pnl = sum(window_pnls) if window_pnls else 0.0

    # Summary stats
    flagged_total = sum(1 for f in atr_flagged if f)
    flagged_valid = sum(1 for f in atr_flagged if f is not None)
    lp_valid = [x for x in lp_coherence if x is not None]

    return {
        "coin": coin,
        "num_candles": len(bars),
        "composite_regime_score": composite,
        "regime": regime,
        "components": {
            "atr_flagged_fraction": round(flagged_total / max(flagged_valid, 1), 4),
            "level_penetration_mean": round(sum(lp_valid) / max(len(lp_valid), 1), 4),
            "temporal_stability": stability_score,
        },
        "transitions": transitions,
        "mean_reversion_window_pnls": window_pnls,
        "mean_reversion_total_pnl": round(total_mr_pnl, 6),
        "rolling_composite_sample": rolling[-20:],  # last 20 for brevity
    }


# ---- main -------------------------------------------------------------------

def main():
    print("=" * 72)
    print("REGIME PHASE CLASSIFIER — Oscillation vs Trend Detection")
    print("=" * 72)
    print()
    print("Metrics:")
    print("  1. ATR Expansion Rate          (current ATR vs 20-period avg)")
    print("  2. Level Penetration Coherence (Fib returns vs escapes)")
    print("  3. Temporal Stability          (MR strategy PnL consistency)")
    print("  4. Composite Regime Score      (-1 trend  ..  +1 oscillation)")
    print()

    # Discover 30d files
    files_30d = sorted(CACHE_DIR.glob("*_30d.json"))
    if not files_30d:
        print("No *_30d.json files found in", CACHE_DIR)
        sys.exit(1)

    print(f"Found {len(files_30d)} 30-day candle file(s).")
    print()

    results = []
    for fpath in files_30d:
        try:
            res = analyse_coin(fpath)
        except Exception as e:
            print(f"  ERROR processing {fpath.name}: {e}")
            continue
        if res is None:
            continue
        results.append(res)
        coin_label = res["coin"]
        score = res["composite_regime_score"]
        regime = res["regime"]
        mr_pnl = res["mean_reversion_total_pnl"]
        n_trans = len(res["transitions"])
        print(f"  {coin_label:<45s}  score={score:+.4f}  regime={regime:<12s}  "
              f"MR_PnL={mr_pnl:+.4f}  transitions={n_trans}")

    print()

    # USDJPY check
    usdjpy = [r for r in results if "USDJPY" in r["coin"].upper() or "USD_JPY" in r["coin"].upper()]
    if usdjpy:
        print("--- USDJPY ---")
        for r in usdjpy:
            print(f"  Score: {r['composite_regime_score']:+.4f}")
            print(f"  Regime: {r['regime']}")
            print(f"  Transitions: {len(r['transitions'])}")
            for t in r["transitions"]:
                print(f"    {t}")
    else:
        print("NOTE: No USDJPY data found in candle cache (crypto-only dataset).")
        print("  Closest proxy: BTC-USD as the largest, most liquid asset.")
        btc = [r for r in results if "BTC" in r["coin"]]
        if btc:
            r = btc[0]
            print(f"  BTC-USD score: {r['composite_regime_score']:+.4f}, regime: {r['regime']}")

    print()

    # Correlation analysis: does composite score correlate with MR PnL?
    scores = [r["composite_regime_score"] for r in results]
    pnls = [r["mean_reversion_total_pnl"] for r in results]

    if len(scores) >= 3:
        n = len(scores)
        mean_s = sum(scores) / n
        mean_p = sum(pnls) / n
        cov = sum((scores[i] - mean_s) * (pnls[i] - mean_p) for i in range(n)) / n
        std_s = math.sqrt(sum((s - mean_s) ** 2 for s in scores) / n)
        std_p = math.sqrt(sum((p - mean_p) ** 2 for p in pnls) / n)
        if std_s > 1e-9 and std_p > 1e-9:
            corr = cov / (std_s * std_p)
        else:
            corr = 0.0
        print(f"Correlation (regime score vs MR PnL): {corr:+.4f}")
        if corr > 0.3:
            print("  -> Positive: higher oscillation scores correlate with better MR PnL.")
        elif corr < -0.3:
            print("  -> Negative: surprising — MR works better in trend regimes?")
        else:
            print("  -> Weak: regime score and MR PnL are loosely related across coins.")
    else:
        corr = None
        print("Not enough coins for correlation analysis.")

    # Save
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    output = {
        "generated_for": [r["coin"] for r in results],
        "num_coins": len(results),
        "correlation_regime_vs_mr_pnl": round(corr, 4) if corr is not None else None,
        "coins": results,
    }
    OUT_PATH.write_text(json.dumps(output, indent=2, default=str))
    print()
    print(f"Results saved to: {OUT_PATH}")


if __name__ == "__main__":
    main()
