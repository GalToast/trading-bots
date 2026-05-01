#!/usr/bin/env python3
"""
Regime-Adaptive Lattice Module
===============================

A drop-in wrapper around the penetration lattice core that adds:
1. Regime detection via Range/ATR ratio (every 20 bars)
2. Auto-adjusting step: step = range × max(0.5, min(1.2, 1.6 - 0.6 × R/A))
3. Smooth step transitions (no hard resets — shifts next levels)
4. Full logging of regime changes + step adjustments

Usage:
    from regime_adaptive_lattice import RegimeAdaptiveLattice
    
    adaptive = RegimeAdaptiveLattice(
        symbol="ETHUSD",
        timeframe="M5",
        base_step=3.0,
        lookback_bars=20,
        min_coeff=0.5,
        max_coeff=1.2,
    )
    
    # On each new bar:
    adaptive.update_bar(high, low, close, prev_close)
    current_step = adaptive.get_current_step()
    regime = adaptive.get_regime()  # "trending", "mixed", "ranging"

The formula:
    step = range × (1.6 - 0.6 × Range/ATR)
    
Where:
    range = average(high - low) over lookback_bars
    ATR = average true range over lookback_bars
    Range/ATR = regime indicator
        < 1.2 → trending (use wider steps, ~1.0× range)
        1.2-1.5 → mixed (balanced, ~0.8× range)
        > 1.5 → ranging (use tighter steps, ~0.6× range)

Floor/ceil prevent disaster at extreme R/A values.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional

logger = logging.getLogger(__name__)


@dataclass
class RegimeSnapshot:
    """A snapshot of regime state at a point in time."""
    bar_index: int
    timestamp: str
    avg_range: float
    atr: float
    range_atr_ratio: float
    regime: str  # "trending", "mixed", "ranging"
    coefficient: float
    step: float
    prior_step: Optional[float] = None


@dataclass
class AdaptiveState:
    """Persistent state for the adaptive lattice."""
    symbol: str
    timeframe: str
    base_step: float
    lookback_bars: int
    min_coeff: float
    max_coeff: float
    
    highs: List[float] = field(default_factory=list)
    lows: List[float] = field(default_factory=list)
    closes: List[float] = field(default_factory=list)
    true_ranges: List[float] = field(default_factory=list)
    
    current_step: float = 0.0
    bar_index: int = 0
    regime: str = "unknown"
    snapshots: List[RegimeSnapshot] = field(default_factory=list)
    last_update_bar: int = 0


class RegimeAdaptiveLattice:
    """
    Regime-adaptive step calculator for penetration lattices.
    
    Computes the optimal step size based on current market regime,
    adjusting every N bars to avoid excessive churn.
    """
    
    def __init__(
        self,
        symbol: str,
        timeframe: str,
        base_step: float,
        lookback_bars: int = 20,
        update_interval: int = 20,
        min_coeff: float = 0.5,
        max_coeff: float = 1.2,
        atr_period: int = 14,
        state_path: Optional[str] = None,
    ):
        self.symbol = symbol
        self.timeframe = timeframe
        self.base_step = base_step
        self.lookback_bars = lookback_bars
        self.update_interval = update_interval
        self.min_coeff = min_coeff
        self.max_coeff = max_coeff
        self.atr_period = atr_period
        self.state_path = state_path
        
        # Initialize state
        self.state = AdaptiveState(
            symbol=symbol,
            timeframe=timeframe,
            base_step=base_step,
            lookback_bars=lookback_bars,
            min_coeff=min_coeff,
            max_coeff=max_coeff,
            current_step=base_step,
        )
        
        # Load prior state if available
        if state_path and Path(state_path).exists():
            self._load_state(state_path)
    
    def update_bar(self, high: float, low: float, close: float, prev_close: float) -> None:
        """
        Feed a new bar into the regime detector.
        Call this on every new bar.
        """
        self.state.bar_index += 1
        
        # Store bar data
        self.state.highs.append(high)
        self.state.lows.append(low)
        self.state.closes.append(close)
        
        # Compute true range
        tr = max(high - low, abs(high - prev_close), abs(low - prev_close))
        self.state.true_ranges.append(tr)
        
        # Keep only lookback window
        max_len = max(self.lookback_bars, self.atr_period) + 10
        if len(self.state.highs) > max_len:
            self.state.highs = self.state.highs[-max_len:]
            self.state.lows = self.state.lows[-max_len:]
            self.state.closes = self.state.closes[-max_len:]
            self.state.true_ranges = self.state.true_ranges[-max_len:]
        
        # Update step if it's time
        if self.state.bar_index - self.state.last_update_bar >= self.update_interval:
            self._update_regime()
    
    def _update_regime(self) -> None:
        """Recompute regime and adjust step."""
        if len(self.state.highs) < self.lookback_bars:
            return
        
        # Compute average range
        ranges = [h - l for h, l in zip(self.state.highs[-self.lookback_bars:],
                                         self.state.lows[-self.lookback_bars:])]
        avg_range = sum(ranges) / len(ranges)
        
        # Compute ATR
        if len(self.state.true_ranges) < self.atr_period:
            return
        atr_values = self.state.true_ranges[-self.atr_period:]
        atr = sum(atr_values) / len(atr_values)
        
        if atr <= 0 or avg_range <= 0:
            return
        
        # Range/ATR ratio
        range_atr_ratio = avg_range / atr
        
        # Regime classification
        if range_atr_ratio < 1.2:
            regime = "trending"
        elif range_atr_ratio > 1.5:
            regime = "ranging"
        else:
            regime = "mixed"
        
        # Compute coefficient: 1.6 - 0.6 × R/A, clamped
        raw_coeff = 1.6 - 0.6 * range_atr_ratio
        coefficient = max(self.min_coeff, min(self.max_coeff, raw_coeff))
        
        # New step
        prior_step = self.state.current_step
        new_step = avg_range * coefficient
        self.state.current_step = new_step
        self.state.regime = regime
        self.state.last_update_bar = self.state.bar_index
        
        # Record snapshot
        snapshot = RegimeSnapshot(
            bar_index=self.state.bar_index,
            timestamp=datetime.now(timezone.utc).isoformat(),
            avg_range=avg_range,
            atr=atr,
            range_atr_ratio=range_atr_ratio,
            regime=regime,
            coefficient=coefficient,
            step=new_step,
            prior_step=prior_step,
        )
        self.state.snapshots.append(snapshot)
        
        # Keep only recent snapshots
        if len(self.state.snapshots) > 100:
            self.state.snapshots = self.state.snapshots[-100:]
        
        # Log the change
        if prior_step is not None and abs(new_step - prior_step) / prior_step > 0.05:
            logger.info(
                f"[{self.symbol} {self.timeframe}] Regime: {regime} | "
                f"R/A={range_atr_ratio:.2f}× | Coeff={coefficient:.3f} | "
                f"Step: ${prior_step:.6f} → ${new_step:.6f} "
                f"({(new_step/prior_step - 1)*100:+.1f}%)"
            )
        
        # Save state
        if self.state_path:
            self._save_state(self.state_path)
    
    def get_current_step(self) -> float:
        """Get the current recommended step size."""
        return self.state.current_step
    
    def get_regime(self) -> str:
        """Get current regime classification."""
        return self.state.regime
    
    def get_range_atr_ratio(self) -> float:
        """Get current Range/ATR ratio."""
        if not self.state.snapshots:
            return 0.0
        return self.state.snapshots[-1].range_atr_ratio
    
    def get_recent_snapshots(self, n: int = 10) -> List[RegimeSnapshot]:
        """Get the most recent regime snapshots."""
        return self.state.snapshots[-n:]
    
    def _save_state(self, path: str) -> None:
        """Save adaptive state to JSON."""
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        
        # Convert snapshots to dicts
        state_dict = {
            "symbol": self.state.symbol,
            "timeframe": self.state.timeframe,
            "base_step": self.state.base_step,
            "lookback_bars": self.state.lookback_bars,
            "min_coeff": self.state.min_coeff,
            "max_coeff": self.state.max_coeff,
            "current_step": self.state.current_step,
            "bar_index": self.state.bar_index,
            "regime": self.state.regime,
            "last_update_bar": self.state.last_update_bar,
            "highs": self.state.highs[-100:],  # Only keep recent
            "lows": self.state.lows[-100:],
            "closes": self.state.closes[-100:],
            "true_ranges": self.state.true_ranges[-100:],
            "snapshots": [asdict(s) for s in self.state.snapshots[-20:]],
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
        
        p.write_text(json.dumps(state_dict, indent=2))
    
    def _load_state(self, path: str) -> None:
        """Load adaptive state from JSON."""
        try:
            data = json.loads(Path(path).read_text())
            self.state.current_step = data.get("current_step", self.base_step)
            self.state.bar_index = data.get("bar_index", 0)
            self.state.regime = data.get("regime", "unknown")
            self.state.last_update_bar = data.get("last_update_bar", 0)
            self.state.highs = data.get("highs", [])
            self.state.lows = data.get("lows", [])
            self.state.closes = data.get("closes", [])
            self.state.true_ranges = data.get("true_ranges", [])
            snapshot_dicts = data.get("snapshots", [])
            self.state.snapshots = [RegimeSnapshot(**s) for s in snapshot_dicts]
            logger.info(f"[{self.symbol}] Loaded prior adaptive state: step=${self.state.current_step:.6f}, regime={self.state.regime}")
        except Exception as e:
            logger.warning(f"[{self.symbol}] Failed to load adaptive state: {e}")


def main():
    """Test the adaptive lattice with simulated data."""
    import random
    
    # Simulate BTC M5 data
    adaptive = RegimeAdaptiveLattice(
        symbol="BTCUSD",
        timeframe="M5",
        base_step=100.0,
        lookback_bars=20,
        update_interval=20,
    )
    
    price = 74000.0
    prev_close = price
    
    print("=" * 80)
    print("Regime-Adaptive Lattice — BTC M5 Simulation")
    print("=" * 80)
    print()
    
    for i in range(200):
        # Simulate trending then ranging
        if i < 100:
            # Trending: small range, directional movement
            change = random.gauss(50, 30)
            bar_range = abs(random.gauss(80, 20))
        else:
            # Ranging: large range, mean-reverting
            change = random.gauss(0, 20)
            bar_range = abs(random.gauss(180, 40))
        
        price += change
        high = price + bar_range / 2
        low = price - bar_range / 2
        
        adaptive.update_bar(high, low, price, prev_close)
        prev_close = price
        
        if i % 20 == 19:
            step = adaptive.get_current_step()
            regime = adaptive.get_regime()
            ra = adaptive.get_range_atr_ratio()
            print(f"Bar {i+1:4d}: step=${step:,.2f} | regime={regime:<10} | R/A={ra:.2f}× | price=${price:,.2f}")
    
    print()
    print(f"Final: step=${adaptive.get_current_step():,.2f} | regime={adaptive.get_regime()}")
    print(f"Total regime changes: {len(adaptive.get_recent_snapshots(100))}")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    main()
