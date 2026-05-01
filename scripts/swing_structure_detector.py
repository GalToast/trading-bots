#!/usr/bin/env python3
"""
Swing Structure Detector — Price Action as the Leading Indicator

Detects HH/HL/LH/LL patterns, wedges, double tops/bottoms, and deeper
sub-structure: swing distance decay, pullback depth change, swing angle,
time compression — all in real-time from tick data.

This is the TRUE leading indicator. Not RSI divergence, not volume divergence.
The raw geometry of market swings tells you what's about to happen.

Architecture:
1. Real-time swing detection (ZigZag-like from tick stream)
2. Pattern classification: HH+HL, LH+LL, wedge, double top/bottom, etc.
3. Deep metrics: swing distance decay, pullback depth change, angle, time compression
4. Output: recommended HH geometry per symbol

Reads from: MT5 tick stream
Writes to: reports/swing_structure_state.json
Consumed by: HH runner (dynamic step adjustment), auto-flip, escape hatches
"""
import MetaTrader5 as mt5
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from collections import deque


def utc_now_iso():
    return datetime.now(timezone.utc).isoformat()


# ── Real-Time Swing Detector ───────────────────────────────────────────

class SwingDetector:
    """
    Detects swing highs and lows from a tick stream in real-time.

    A swing high is confirmed when:
    1. Price reaches a peak
    2. Price retraces by >= retrace_threshold from that peak

    A swing low is confirmed when:
    1. Price reaches a trough
    2. Price bounces by >= retrace_threshold from that trough

    The detector tracks the last N confirmed swings and their timestamps,
    enabling pattern classification and deep metric computation.
    """

    def __init__(self, symbol: str, retrace_threshold_pct: float = 0.05, max_swings: int = 10):
        self.symbol = symbol
        self.retrace_threshold = retrace_threshold_pct  # 0.05 = 0.05%
        self.max_swings = max_swings

        # State machine
        self.searching_for = "high"  # or "low"
        self.current_peak = None  # (price, time_msc)
        self.current_trough = None  # (price, time_msc)

        # Confirmed swings
        self.swings = deque(maxlen=max_swings)  # [(type, price, time_msc), ...]

        # Deep metrics
        self.swing_distances = []  # distance between consecutive HHs or LLs
        self.pullback_depths = []  # depth of each pullback from prior HH
        self.rally_depths = []  # depth of each rally from prior LL
        self.swing_times = []  # time between consecutive swings
        self.swing_angles = []  # price change / time (slope)

    def ingest_tick(self, price: float, time_msc: int):
        """Ingest a tick and check for swing confirmations."""
        if self.searching_for == "high":
            self._searching_high(price, time_msc)
        else:
            self._searching_low(price, time_msc)

    def _searching_high(self, price: float, time_msc: int):
        """Looking for a swing high — tracking upward move, waiting for retrace."""
        if self.current_peak is None or price > self.current_peak[0]:
            # New peak
            self.current_peak = (price, time_msc)
            return

        # Price is below peak — check for retrace
        retrace_pct = (self.current_peak[0] - price) / self.current_peak[0] * 100
        if retrace_pct >= self.retrace_threshold:
            # Swing high CONFIRMED
            swing_high = ("high", self.current_peak[0], self.current_peak[1])
            self.swings.append(swing_high)

            # Record the pullback depth (from prior swing low to this high)
            if self.current_trough is not None:
                rally_depth = self.current_peak[0] - self.current_trough[0]
                self.rally_depths.append(rally_depth)

            # Now search for swing low
            self.current_trough = (price, time_msc)
            self.searching_for = "low"

    def _searching_low(self, price: float, time_msc: int):
        """Looking for a swing low — tracking downward move, waiting for bounce."""
        if self.current_trough is None or price < self.current_trough[0]:
            # New trough
            self.current_trough = (price, time_msc)
            return

        # Price is above trough — check for bounce
        bounce_pct = (price - self.current_trough[0]) / self.current_trough[0] * 100
        if bounce_pct >= self.retrace_threshold:
            # Swing low CONFIRMED
            swing_low = ("low", self.current_trough[0], self.current_trough[1])
            self.swings.append(swing_low)

            # Record the pullback depth (from prior swing high to this low)
            if self.current_peak is not None:
                pullback_depth = self.current_peak[0] - self.current_trough[0]
                self.pullback_depths.append(pullback_depth)

                # Record time between swings
                if len(self.swings) >= 2:
                    prev_swing = self.swings[-2]
                    time_diff = swing_low[2] - prev_swing[2]  # ms
                    if time_diff > 0:
                        self.swing_times.append(time_diff)

                        # Compute swing angle (price change / time)
                        price_change = abs(swing_low[0] - prev_swing[0])
                        angle = price_change / (time_diff / 1000.0)  # price per second
                        self.swing_angles.append(angle)

            # Now search for swing high
            self.current_peak = (price, time_msc)
            self.searching_for = "high"

    def classify_pattern(self) -> dict:
        """
        Classify the current swing pattern and compute deep metrics.

        Returns:
        {
            "pattern": str,           # HH+HL, LH+LL, HH+LH, HL+LL, wedge, double_top, double_bottom, chop
            "momentum_score": float,  # -1.0 to +1.0 (negative = bearish, positive = bullish)
            "reversal_probability": float,  # 0.0 - 1.0
            "swing_distance_decay": float,  # ratio of recent swings (<1 = decaying)
            "pullback_depth_change": float, # ratio (>1 = deepening pullbacks)
            "angle_change": float,    # ratio (<1 = flattening swings)
            "time_compression": float,  # ratio (<1 = compressing = wedge)
            "recommended_geometry": dict,
        }
        """
        if len(self.swings) < 4:
            return {
                "pattern": "insufficient_data",
                "momentum_score": 0.0,
                "reversal_probability": 0.0,
                "swing_distance_decay": None,
                "pullback_depth_change": None,
                "angle_change": None,
                "time_compression": None,
                "recommended_geometry": {"action": "HOLD", "reason": "need more swings"},
            }

        swings = list(self.swings)

        # Extract highs and lows
        highs = [(s[1], s[2]) for s in swings if s[0] == "high"]
        lows = [(s[1], s[2]) for s in swings if s[0] == "low"]

        if len(highs) < 2 or len(lows) < 2:
            return {
                "pattern": "insufficient_structure",
                "momentum_score": 0.0,
                "reversal_probability": 0.0,
                "swing_distance_decay": None,
                "pullback_depth_change": None,
                "angle_change": None,
                "time_compression": None,
                "recommended_geometry": {"action": "HOLD", "reason": "need more structure"},
            }

        # Use last 2 highs and last 2 lows for classification
        hh1, hh2 = highs[-2], highs[-1]  # older, newer
        ll1, ll2 = lows[-2], lows[-1]

        hh_higher = hh2[0] > hh1[0]
        ll_higher = ll2[0] > ll1[0]

        # Pattern classification
        if hh_higher and ll_higher:
            pattern = "HH_HL"  # Higher highs + higher lows = uptrend
            momentum = min(1.0, (hh2[0] - hh1[0]) / hh1[0] * 100 + (ll2[0] - ll1[0]) / ll1[0] * 100)
            reversal_prob = 0.0
        elif not hh_higher and not ll_higher:
            pattern = "LH_LL"  # Lower highs + lower lows = downtrend
            momentum = -min(1.0, abs((hh2[0] - hh1[0]) / hh1[0] * 100) + abs((ll2[0] - ll1[0]) / ll1[0] * 100))
            reversal_prob = 0.0
        elif hh_higher and not ll_higher:
            pattern = "HH_LL"  # Higher high but lower low = breakdown
            momentum = 0.0
            reversal_prob = 0.6
        else:  # not hh_higher and ll_higher
            pattern = "LH_HL"  # Lower high but higher low = squeeze
            momentum = 0.2
            reversal_prob = 0.4

        # Check for wedges (time compression)
        if len(self.swing_times) >= 3:
            recent_times = self.swing_times[-3:]
            if recent_times[1] < recent_times[0] * 0.8 and recent_times[2] < recent_times[1] * 0.8:
                pattern = "WEDGE"
                reversal_prob = max(reversal_prob, 0.7)

        # Check for double top (HH2 ≈ HH1, within 0.1%)
        if hh_higher or abs(hh2[0] - hh1[0]) / hh1[0] < 0.001:
            if abs(hh2[0] - hh1[0]) / hh1[0] < 0.001:
                pattern = "DOUBLE_TOP"
                reversal_prob = max(reversal_prob, 0.8)

        # Check for double bottom
        if ll_higher or abs(ll2[0] - ll1[0]) / ll1[0] < 0.001:
            if abs(ll2[0] - ll1[0]) / ll1[0] < 0.001:
                pattern = "DOUBLE_BOTTOM"
                reversal_prob = max(reversal_prob, 0.8)

        # Deep metrics
        swing_distance_decay = self._compute_distance_decay(highs, lows)
        pullback_depth_change = self._compute_pullback_change()
        angle_change = self._compute_angle_change()
        time_compression = self._compute_time_compression()

        # Update reversal probability from deep metrics
        if swing_distance_decay is not None and swing_distance_decay < 0.8:
            reversal_prob = max(reversal_prob, 0.5 + (1.0 - swing_distance_decay) * 0.5)
        if pullback_depth_change is not None and pullback_depth_change > 1.3:
            reversal_prob = max(reversal_prob, 0.6 + (pullback_depth_change - 1.0) * 0.3)
        if angle_change is not None and angle_change < 0.7:
            reversal_prob = max(reversal_prob, 0.4 + (1.0 - angle_change) * 0.4)

        reversal_prob = min(1.0, reversal_prob)

        # Recommended geometry
        geometry = self._pattern_to_geometry(pattern, momentum, reversal_prob, swing_distance_decay, pullback_depth_change)

        return {
            "pattern": pattern,
            "momentum_score": round(momentum, 4),
            "reversal_probability": round(reversal_prob, 3),
            "swing_distance_decay": round(swing_distance_decay, 3) if swing_distance_decay is not None else None,
            "pullback_depth_change": round(pullback_depth_change, 3) if pullback_depth_change is not None else None,
            "angle_change": round(angle_change, 3) if angle_change is not None else None,
            "time_compression": round(time_compression, 3) if time_compression is not None else None,
            "recommended_geometry": geometry,
            "swings_analyzed": len(swings),
            "last_hh": round(hh2[0], 5),
            "prev_hh": round(hh1[0], 5),
            "last_ll": round(ll2[0], 5),
            "prev_ll": round(ll1[0], 5),
        }

    def _compute_distance_decay(self, highs, lows):
        """
        Swing distance decay: are the swings getting smaller?
        Ratio of (HH_n - HH_n-1) / (HH_n-1 - HH_n-2)
        < 1.0 = decaying momentum
        """
        if len(highs) < 3:
            return None
        d1 = highs[-2][0] - highs[-3][0]  # older swing distance
        d2 = highs[-1][0] - highs[-2][0]  # newer swing distance
        if d1 == 0:
            return None
        return d2 / d1

    def _compute_pullback_change(self):
        """
        Pullback depth change: are pullbacks getting deeper?
        Ratio of current_pullback_depth / previous_pullback_depth
        > 1.0 = pullbacks deepening = support weakening
        """
        if len(self.pullback_depths) < 2:
            return None
        prev = self.pullback_depths[-2]
        curr = self.pullback_depths[-1]
        if prev == 0:
            return None
        return curr / prev

    def _compute_angle_change(self):
        """
        Swing angle change: are swings getting flatter?
        Ratio of current_angle / previous_angle
        < 1.0 = flattening = momentum loss
        """
        if len(self.swing_angles) < 2:
            return None
        prev = self.swing_angles[-2]
        curr = self.swing_angles[-1]
        if prev == 0:
            return None
        return curr / prev

    def _compute_time_compression(self):
        """
        Time compression: are swings happening faster?
        Ratio of current_time / previous_time
        < 1.0 = compression = wedge building
        """
        if len(self.swing_times) < 2:
            return None
        prev = self.swing_times[-2]
        curr = self.swing_times[-1]
        if prev == 0:
            return None
        return curr / prev

    def _pattern_to_geometry(self, pattern, momentum, reversal_prob, distance_decay, pullback_change):
        """Map swing pattern to recommended HH geometry."""
        if pattern == "HH_HL":
            if reversal_prob < 0.3:
                return {"action": "BUY_TIGHT", "reason": "strong uptrend, catch pullbacks", "buy_step_coeff": 0.8, "sell_step_coeff": 1.3}
            else:
                return {"action": "BUY_TIGHT_PREPARE_FLIP", "reason": "uptrend but reversal risk high", "buy_step_coeff": 0.9, "sell_step_coeff": 1.1}

        elif pattern == "LH_LL":
            if reversal_prob < 0.3:
                return {"action": "SELL_TIGHT", "reason": "strong downtrend, catch rallies", "buy_step_coeff": 1.3, "sell_step_coeff": 0.8}
            else:
                return {"action": "SELL_TIGHT_PREPARE_FLIP", "reason": "downtrend but reversal risk high", "buy_step_coeff": 1.1, "sell_step_coeff": 0.9}

        elif pattern == "HH_LL":
            return {"action": "FLIP_TO_SELL", "reason": "breakdown detected", "buy_step_coeff": 1.5, "sell_step_coeff": 0.7}

        elif pattern == "LH_HL":
            return {"action": "SYMMETRIC_TIGHT", "reason": "squeeze — both sides tight", "buy_step_coeff": 0.7, "sell_step_coeff": 0.7}

        elif pattern == "WEDGE":
            return {"action": "WIDEN_BOTH", "reason": "compression — prepare for breakout", "buy_step_coeff": 1.5, "sell_step_coeff": 1.5}

        elif pattern == "DOUBLE_TOP":
            return {"action": "SELL_TIGHT_AT_LEVEL", "reason": "resistance confirmed", "buy_step_coeff": 1.5, "sell_step_coeff": 0.5}

        elif pattern == "DOUBLE_BOTTOM":
            return {"action": "BUY_TIGHT_AT_LEVEL", "reason": "support confirmed", "buy_step_coeff": 0.5, "sell_step_coeff": 1.5}

        else:
            return {"action": "HOLD", "reason": "insufficient structure", "buy_step_coeff": 1.0, "sell_step_coeff": 1.0}


# ── Main ───────────────────────────────────────────────────────────────

DEFAULT_SYMBOLS = [
    "GBPUSD", "EURUSD", "USDJPY", "NZDUSD",
    "NAS100", "US30",
    "BTCUSD", "ETHUSD",
    "XAUUSD",
]

# Per-symbol retrace thresholds (adapt to symbol volatility)
RETRACE_THRESHOLDS = {
    "GBPUSD": 0.03,
    "EURUSD": 0.03,
    "USDJPY": 0.02,
    "NZDUSD": 0.03,
    "NAS100": 0.05,
    "US30": 0.05,
    "BTCUSD": 0.08,
    "ETHUSD": 0.10,
    "XAUUSD": 0.06,
}


def probe_symbol(symbol: str, tick_count: int = 500) -> dict:
    """Probe a symbol's tick stream for swing structure."""
    mt5.initialize()

    threshold = RETRACE_THRESHOLDS.get(symbol, 0.05)
    detector = SwingDetector(symbol, retrace_threshold_pct=threshold, max_swings=12)

    ticks = mt5.copy_ticks_from_pos(symbol, 0, tick_count, mt5.COPY_TICKS_ALL)
    if ticks is None or len(ticks) == 0:
        return {"error": f"No ticks for {symbol}"}

    for tick in ticks:
        mid = (float(tick["bid"]) + float(tick["ask"])) / 2.0
        detector.ingest_tick(mid, int(tick["time_msc"]))

    result = detector.classify_pattern()
    result["symbol"] = symbol
    result["ticks_analyzed"] = len(ticks)
    result["confirmed_swings"] = len(list(detector.swings))
    result["retrace_threshold_pct"] = threshold

    mt5.shutdown()
    return result


def probe_all_symbols(symbols=None, tick_count: int = 500) -> dict:
    """Probe all symbols for swing structure."""
    if symbols is None:
        symbols = list(DEFAULT_SYMBOLS)

    mt5.initialize()
    results = {}

    for sym in symbols:
        threshold = RETRACE_THRESHOLDS.get(sym, 0.05)
        detector = SwingDetector(sym, retrace_threshold_pct=threshold, max_swings=12)

        ticks = mt5.copy_ticks_from_pos(sym, 0, tick_count, mt5.COPY_TICKS_ALL)
        if ticks is None or len(ticks) == 0:
            results[sym] = {"error": f"No ticks for {sym}"}
            continue

        for tick in ticks:
            mid = (float(tick["bid"]) + float(tick["ask"])) / 2.0
            detector.ingest_tick(mid, int(tick["time_msc"]))

        result = detector.classify_pattern()
        result["symbol"] = sym
        result["ticks_analyzed"] = len(ticks)
        result["confirmed_swings"] = len(list(detector.swings))
        result["retrace_threshold_pct"] = threshold
        result["detected_at"] = utc_now_iso()

        results[sym] = result

    mt5.shutdown()
    return results


# ── CLI ─────────────────────────────────────────────────────────────────

def _action_emoji(action: str) -> str:
    emojis = {
        "BUY_TIGHT": "🟢 BUY-tight",
        "BUY_TIGHT_PREPARE_FLIP": "🟡 BUY + watch",
        "SELL_TIGHT": "🔴 SELL-tight",
        "SELL_TIGHT_PREPARE_FLIP": "🟠 SELL + watch",
        "FLIP_TO_SELL": "🔴→ FLIP to SELL",
        "SYMMETRIC_TIGHT": "⚪ Sym tight",
        "WIDEN_BOTH": "📐 Widen both",
        "SELL_TIGHT_AT_LEVEL": "🔴 SELL at resistance",
        "BUY_TIGHT_AT_LEVEL": "🟢 BUY at support",
        "HOLD": "⏸️ Hold",
    }
    return emojis.get(action, action)


def main():
    symbols = list(DEFAULT_SYMBOLS)

    results = probe_all_symbols(symbols, tick_count=800)

    print(f"{'Symbol':<10} {'Pattern':<18} {'Mom':>5} {'Rev%':>5} {'Decay':>6} {'PBΔ':>5} {'Angle':>6} {'Time':>5} {'Action'}")
    print("-" * 120)

    for sym, data in sorted(results.items()):
        if "error" in data:
            print(f"{sym:<10} {'ERROR':<18} {'N/A':>5} {'N/A':>5} {data['error']}")
            continue

        pattern = data["pattern"]
        mom = data["momentum_score"]
        rev = data["reversal_probability"]
        decay = data["swing_distance_decay"]
        pb = data["pullback_depth_change"]
        angle = data["angle_change"]
        time_c = data["time_compression"]
        geom = data["recommended_geometry"]
        action = geom["action"]
        swings = data["confirmed_swings"]

        action_lbl = f"{_action_emoji(action)} ({geom['reason'][:30]})"
        decay_str = f"{decay:.2f}" if decay is not None else "—"
        pb_str = f"{pb:.2f}" if pb is not None else "—"
        angle_str = f"{angle:.2f}" if angle is not None else "—"
        time_str = f"{time_c:.2f}" if time_c is not None else "—"

        print(f"{sym:<10} {pattern:<18} {mom:>+5.3f} {rev:>5.1%} {decay_str:>6} {pb_str:>5} {angle_str:>6} {time_str:>5} [{swings}🔄] {action_lbl}")

    # Save report
    report_dir = Path(__file__).parent.parent / "reports"
    report_dir.mkdir(exist_ok=True)
    report_path = report_dir / "swing_structure_state.json"
    with open(report_path, "w") as f:
        json.dump(results, f, indent=2)

    print(f"\nSaved to {report_path}")

    # Summary
    patterns = {}
    for sym, data in results.items():
        if "error" not in data:
            p = data["pattern"]
            patterns[p] = patterns.get(p, 0) + 1

    print(f"\nPattern summary:")
    for p, count in sorted(patterns.items(), key=lambda x: -x[1]):
        print(f"  {p}: {count} symbols")


if __name__ == "__main__":
    main()
