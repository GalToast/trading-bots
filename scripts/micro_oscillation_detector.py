#!/usr/bin/env python3
"""
Tick-Level Micro-Oscillation Detector — Extract Every Red Penny

The bar-level regime detectors (M15, M5, M1) tell us MACRO direction.
This detector finds MICRO-oscillations WITHIN candles — the small retraces
that happen even during strong trends.

Core insight: Every candle has retraces. A $5 green candle might:
  Open $100 → rally to $103 → pull back to $101 → rally to $105 → close $105
  The $2 pullback at $101 is extractable if we detect it in real-time.

Leading signals (tick-level, NOT bar-level):
1. Price acceleration (2nd derivative) — when acceleration → 0, micro-reversal imminent
2. Tick velocity changes — when ticks per second spike then drop, momentum exhausted
3. Bid/ask micro-imbalance — when consecutive ticks all hit bid or all hit ask
4. Spread micro-widening — liquidity vacuum = reversal imminent

Output: micro_regime signal updated every N ticks
  - micro_rally: price accelerating upward → open BUY positions
  - micro_pullback: price decelerating downward → close BUY, prepare SELL
  - micro_dip: price accelerating downward → open SELL positions
  - micro_bounce: price decelerating upward → close SELL, prepare BUY
  - micro_chop: no clear direction → tighten both sides

The HH geometry responds in REAL-TIME:
  micro_pullback detected → tighten BUY step to 0.25×ATR (catch the dip)
  micro_bounce detected → tighten SELL step to 0.25×ATR (catch the rally)
  micro_chop detected → symmetric tight steps (harvest both sides)

Reads from: MT5 tick stream (copy_ticks_from_pos)
Writes to: reports/micro_oscillation_state.json
Consumed by: HH runner (dynamic step adjustment), auto-flip, escape hatches
"""
import MetaTrader5 as mt5
import json
import os
import time
import numpy as np
from datetime import datetime, timezone
from pathlib import Path
from collections import deque


def utc_now_iso():
    return datetime.now(timezone.utc).isoformat()


# ── Tick-Level Leading Indicators ───────────────────────────────────────

class TickMicroDetector:
    """
    Detects micro-oscillations from tick stream in real-time.

    Tracks:
    - Price velocity (1st derivative: price change per tick)
    - Price acceleration (2nd derivative: velocity change per tick)
    - Tick frequency (ticks per second — momentum gauge)
    - Bid/ask micro-imbalance (consecutive bid hits vs ask hits)
    - Spread micro-changes (liquidity vacuum detection)
    """

    def __init__(self, symbol: str, tick_window: int = 100, accel_window: int = 20):
        self.symbol = symbol
        self.tick_window = tick_window
        self.accel_window = accel_window

        # Rolling buffers
        self.prices = deque(maxlen=tick_window)  # mid prices
        self.times = deque(maxlen=tick_window)  # tick timestamps (ms)
        self.bid_prices = deque(maxlen=tick_window)
        self.ask_prices = deque(maxlen=tick_window)
        self.volumes = deque(maxlen=tick_window)

        # Derived signals
        self.velocities = deque(maxlen=accel_window)  # price change per tick
        self.accelerations = deque(maxlen=accel_window)  # velocity change per tick
        self.tick_intervals = deque(maxlen=accel_window)  # ms between ticks

    def ingest_tick(self, tick: dict):
        """Ingest a single tick and update all rolling buffers.

        tick format: {"time_msc": int, "bid": float, "ask": float, "last": float, "volume": int}
        """
        bid = float(tick["bid"])
        ask = float(tick["ask"])
        mid = (bid + ask) / 2.0
        time_msc = int(tick["time_msc"])
        volume = int(tick.get("volume", 0))

        self.prices.append(mid)
        self.times.append(time_msc)
        self.bid_prices.append(bid)
        self.ask_prices.append(ask)
        self.volumes.append(volume)

        # Compute velocity (price change from previous tick)
        if len(self.prices) >= 2:
            velocity = self.prices[-1] - self.prices[-2]
            self.velocities.append(velocity)

        # Compute acceleration (velocity change)
        if len(self.velocities) >= 2:
            acceleration = self.velocities[-1] - self.velocities[-2]
            self.accelerations.append(acceleration)

        # Compute tick interval (ms between ticks)
        if len(self.times) >= 2:
            interval = self.times[-1] - self.times[-2]
            if interval > 0:
                self.tick_intervals.append(interval)

    def get_velocity(self) -> float:
        """Current price velocity (average of recent velocities)."""
        if not self.velocities:
            return 0.0
        recent = list(self.velocities)[-10:]
        return sum(recent) / len(recent)

    def get_acceleration(self) -> float:
        """Current price acceleration (average of recent accelerations)."""
        if not self.accelerations:
            return 0.0
        recent = list(self.accelerations)[-10:]
        return sum(recent) / len(recent)

    def get_tick_frequency(self) -> float:
        """Ticks per second — momentum gauge."""
        if len(self.tick_intervals) < 2:
            return 0.0
        recent = list(self.tick_intervals)[-20:]
        avg_interval_ms = sum(recent) / len(recent)
        if avg_interval_ms <= 0:
            return 0.0
        return 1000.0 / avg_interval_ms  # ticks per second

    def get_bid_ask_imbalance(self) -> float:
        """
        Bid/ask micro-imbalance: -1.0 (all bids hit) to +1.0 (all asks hit).
        When consecutive ticks all hit the bid → selling pressure.
        When consecutive ticks all hit the ask → buying pressure.
        """
        if len(self.prices) < 10:
            return 0.0

        recent_bids = list(self.bid_prices)[-20:]
        recent_asks = list(self.ask_prices)[-20:]
        recent_prices = list(self.prices)[-20:]

        if len(recent_prices) < 2:
            return 0.0

        # Count how many ticks closed near bid vs near ask
        bid_hits = 0
        ask_hits = 0
        for i in range(1, len(recent_prices)):
            spread = recent_asks[i] - recent_bids[i]
            if spread == 0:
                continue
            # Where did price close within the spread?
            position = (recent_prices[i] - recent_bids[i]) / spread
            if position < 0.3:
                bid_hits += 1  # Price near bid = selling
            elif position > 0.7:
                ask_hits += 1  # Price near ask = buying

        total = bid_hits + ask_hits
        if total == 0:
            return 0.0

        return (ask_hits - bid_hits) / total  # +1 = all ask, -1 = all bid

    def get_spread_change(self) -> float:
        """
        Spread micro-change: current spread vs recent average.
        Widening spread = liquidity vacuum = reversal signal.
        Returns ratio: current_spread / avg_spread (>1 means widening)
        """
        if len(self.bid_prices) < 20:
            return 1.0

        recent_spreads = []
        for i in range(-20, 0):
            spread = self.ask_prices[i] - self.bid_prices[i]
            recent_spreads.append(spread)

        if not recent_spreads:
            return 1.0

        avg_spread = sum(recent_spreads[:-5]) / (len(recent_spreads) - 5)
        current_spread = recent_spreads[-1]

        if avg_spread <= 0:
            return 1.0

        return current_spread / avg_spread

    def detect_micro_regime(self) -> dict:
        """
        Classify the current micro-regime from tick-level signals.

        Returns:
        {
            "micro_regime": str,  # micro_rally, micro_pullback, micro_dip, micro_bounce, micro_chop
            "confidence": float,  # 0.0 - 1.0
            "velocity": float,
            "acceleration": float,
            "tick_frequency": float,
            "bid_ask_imbalance": float,
            "spread_ratio": float,
            "recommended_action": str,
        }
        """
        velocity = self.get_velocity()
        acceleration = self.get_acceleration()
        tick_freq = self.get_tick_frequency()
        imbalance = self.get_bid_ask_imbalance()
        spread_ratio = self.get_spread_change()

        # Classification logic
        regime = "micro_chop"
        confidence = 0.0

        # Strong signals
        abs_vel = abs(velocity)
        abs_accel = abs(acceleration)

        # Acceleration direction matters more than velocity
        # If price is going up but acceleration is negative → pullback imminent
        # If price is going down but acceleration is positive → bounce imminent

        if acceleration > 0 and velocity > 0:
            # Price going up AND accelerating up → micro rally
            regime = "micro_rally"
            confidence = min(1.0, abs_accel * 1000 + abs_vel * 100)
        elif acceleration < 0 and velocity > 0:
            # Price going up but decelerating → micro pullback imminent
            regime = "micro_pullback"
            confidence = min(1.0, abs_accel * 1000 + abs_vel * 50)
        elif acceleration < 0 and velocity < 0:
            # Price going down AND accelerating down → micro dip
            regime = "micro_dip"
            confidence = min(1.0, abs_accel * 1000 + abs_vel * 100)
        elif acceleration > 0 and velocity < 0:
            # Price going down but decelerating → micro bounce imminent
            regime = "micro_bounce"
            confidence = min(1.0, abs_accel * 1000 + abs_vel * 50)

        # Confirm with bid/ask imbalance
        if regime in ("micro_rally", "micro_bounce") and imbalance > 0.3:
            confidence = min(1.0, confidence * 1.2)  # Boost confidence
        elif regime in ("micro_pullback", "micro_dip") and imbalance < -0.3:
            confidence = min(1.0, confidence * 1.2)

        # Spread widening reduces confidence (uncertainty)
        if spread_ratio > 1.5:
            confidence *= 0.7  # Wide spread = less reliable signals

        # Low tick frequency reduces confidence
        if tick_freq < 2.0:
            confidence *= 0.5  # Thin market = noisy signals

        # Map to recommended action for HH geometry
        action = self._map_regime_to_action(regime, confidence)

        return {
            "micro_regime": regime,
            "confidence": round(confidence, 3),
            "velocity": round(velocity, 6),
            "acceleration": round(acceleration, 6),
            "tick_frequency": round(tick_freq, 1),
            "bid_ask_imbalance": round(imbalance, 3),
            "spread_ratio": round(spread_ratio, 3),
            "recommended_action": action,
            "detected_at": utc_now_iso(),
        }

    def _map_regime_to_action(self, regime: str, confidence: float) -> str:
        """Map micro-regime to HH geometry action."""
        if confidence < 0.2:
            return "HOLD"  # Too uncertain, don't change geometry

        if regime == "micro_rally":
            return "TIGHTEN_SELL"  # Catch the rally with tight SELL step
        elif regime == "micro_pullback":
            return "TIGHTEN_BUY"  # Catch the dip with tight BUY step
        elif regime == "micro_dip":
            return "TIGHTEN_BUY"  # Catch the falling knife with tight BUY step
        elif regime == "micro_bounce":
            return "TIGHTEN_SELL"  # Catch the bounce with tight SELL step
        elif regime == "micro_chop":
            return "SYMMETRIC_TIGHT"  # Harvest both sides

        return "HOLD"


# ── Symbol-level detector ──────────────────────────────────────────────

DEFAULT_SYMBOLS = [
    "GBPUSD", "EURUSD", "USDJPY", "NZDUSD",
    "NAS100", "US30",
    "BTCUSD", "ETHUSD",
    "XAUUSD",
]


def probe_symbol(symbol: str, tick_count: int = 200) -> dict:
    """
    Probe a symbol's tick stream and detect micro-oscillation regime.

    Grabs N ticks from MT5, runs them through the detector, returns current state.
    """
    mt5.initialize()

    # Get tick history
    ticks = mt5.copy_ticks_from_pos(symbol, 0, tick_count, mt5.COPY_TICKS_ALL)
    if ticks is None or len(ticks) == 0:
        return {"error": f"No ticks for {symbol}"}

    detector = TickMicroDetector(symbol, tick_window=tick_count, accel_window=50)

    for tick in ticks:
        detector.ingest_tick({
            "time_msc": int(tick["time_msc"]),
            "bid": float(tick["bid"]),
            "ask": float(tick["ask"]),
            "last": float(tick["last"]),
            "volume": int(tick["volume"]),
        })

    regime = detector.detect_micro_regime()

    # Add context
    current_price = detector.prices[-1] if detector.prices else 0
    price_range = max(detector.prices) - min(detector.prices) if detector.prices else 0

    regime["symbol"] = symbol
    regime["current_price"] = round(current_price, 5)
    regime["tick_range"] = round(price_range, 5)
    regime["ticks_analyzed"] = len(ticks)

    mt5.shutdown()
    return regime


def probe_all_symbols(symbols=None, tick_count: int = 200) -> dict:
    """Probe all symbols and return micro-oscillation state."""
    if symbols is None:
        symbols = list(DEFAULT_SYMBOLS)

    mt5.initialize()
    results = {}

    for sym in symbols:
        ticks = mt5.copy_ticks_from_pos(sym, 0, tick_count, mt5.COPY_TICKS_ALL)
        if ticks is None or len(ticks) == 0:
            results[sym] = {"error": f"No ticks for {sym}"}
            continue

        detector = TickMicroDetector(sym, tick_window=tick_count, accel_window=50)
        for tick in ticks:
            detector.ingest_tick({
                "time_msc": int(tick["time_msc"]),
                "bid": float(tick["bid"]),
                "ask": float(tick["ask"]),
                "last": float(tick["last"]),
                "volume": int(tick["volume"]),
            })

        regime = detector.detect_micro_regime()
        current_price = detector.prices[-1] if detector.prices else 0
        price_range = max(detector.prices) - min(detector.prices) if detector.prices else 0

        regime["symbol"] = sym
        regime["current_price"] = round(current_price, 5)
        regime["tick_range"] = round(price_range, 5)
        regime["ticks_analyzed"] = len(ticks)
        regime["detected_at"] = utc_now_iso()

        results[sym] = regime

    mt5.shutdown()
    return results


# ── CLI ─────────────────────────────────────────────────────────────────

def _action_emoji(action: str) -> str:
    emojis = {
        "TIGHTEN_BUY": "🟢 Tight BUY",
        "TIGHTEN_SELL": "🔴 Tight SELL",
        "SYMMETRIC_TIGHT": "⚪ Sym tight",
        "HOLD": "⏸️ Hold",
    }
    return emojis.get(action, action)


def main():
    symbols = list(DEFAULT_SYMBOLS)

    results = probe_all_symbols(symbols, tick_count=300)

    print(f"{'Symbol':<10} {'Micro-Regime':<20} {'Conf':>5} {'Action':<16} {'Velocity':>10} {'Accel':>10} {'Ticks/s':>8} {'B/A Imb':>8} {'Sprd':>6}")
    print("-" * 110)

    for sym, data in sorted(results.items()):
        if "error" in data:
            print(f"{sym:<10} {'ERROR':<20} {'N/A':>5} {'':<16} {data['error']}")
            continue

        regime = data["micro_regime"]
        conf = data["confidence"]
        action = data["recommended_action"]
        vel = data["velocity"]
        accel = data["acceleration"]
        freq = data["tick_frequency"]
        imb = data["bid_ask_imbalance"]
        sprd = data["spread_ratio"]

        action_lbl = _action_emoji(action)
        print(f"{sym:<10} {regime:<20} {conf:>5.2f} {action_lbl:<16} {vel:>+10.6f} {accel:>+10.6f} {freq:>8.1f} {imb:>+8.3f} {sprd:>6.2f}x")

    # Save report
    report_dir = Path(__file__).parent.parent / "reports"
    report_dir.mkdir(exist_ok=True)
    report_path = report_dir / "micro_oscillation_state.json"
    with open(report_path, "w") as f:
        json.dump(results, f, indent=2)

    print(f"\nSaved to {report_path}")

    # Summary
    actions = {}
    for sym, data in results.items():
        if "error" not in data:
            action = data["recommended_action"]
            actions[action] = actions.get(action, 0) + 1

    print(f"\nAction summary:")
    for action, count in sorted(actions.items(), key=lambda x: -x[1]):
        print(f"  {_action_emoji(action)}: {count} symbols")


if __name__ == "__main__":
    main()
