#!/usr/bin/env python3
"""Volatility-Adaptive Target Calculator.

Problem: All runners use fixed 5% targets regardless of product volatility.
- AUD-USD (forex) moves ~0.1%/hr → 5% target is impossible
- CQT-USD (microcap) moves ~2%/hr → 5% target is unlikely
- MOG-USD (ultra-microcap) moves ~7%/hr → 5% target is achievable

Solution: Scale targets and stops to each product's natural movement.

Usage in any runner:
    from volatility_targets import AdaptiveTargetCalculator

    calc = AdaptiveTargetCalculator(
        atr_multiplier=2.0,    # Target = 2x ATR
        stop_multiplier=1.0,    # Stop = 1x ATR
        min_target_pct=0.3,     # Floor: never below 0.3%
        max_target_pct=10.0,    # Ceiling: never above 10%
    )

    # Get ATR from price history (or use cached value)
    atr = calc.compute_atr(prices=[0.7146, 0.7147, 0.7145, ...], period=14)

    # Get adaptive targets
    target, stop = calc.get_targets(price=0.7146, atr=atr)
    # For AUD-USD: target=0.3%, stop=0.15% (floor-bound)
    # For CQT-USD: target=2.0%, stop=1.0% (ATR-based)
    # For MOG-USD: target=7.0%, stop=3.5% (ATR-based)
"""
import numpy as np
from typing import Tuple


class AdaptiveTargetCalculator:
    """Calculate volatility-adaptive targets and stops."""

    def __init__(
        self,
        atr_multiplier: float = 2.0,
        stop_multiplier: float = 1.0,
        trail_giveback_pct: float = 0.25,
        min_target_pct: float = 0.3,
        max_target_pct: float = 10.0,
        min_stop_pct: float = 0.1,
        max_stop_pct: float = 5.0,
    ):
        self.atr_multiplier = atr_multiplier
        self.stop_multiplier = stop_multiplier
        self.trail_giveback_pct = trail_giveback_pct
        self.min_target_pct = min_target_pct
        self.max_target_pct = max_target_pct
        self.min_stop_pct = min_stop_pct
        self.max_stop_pct = max_stop_pct

    @staticmethod
    def compute_atr(
        highs: list[float],
        lows: list[float],
        closes: list[float],
        period: int = 14,
    ) -> float:
        """Compute Average True Range from OHLC data.
        
        Returns ATR as an absolute price value.
        """
        if len(highs) < 2:
            return 0.0

        true_ranges = []
        for i in range(1, len(highs)):
            high = highs[i]
            low = lows[i]
            prev_close = closes[i - 1]

            tr = max(
                high - low,
                abs(high - prev_close),
                abs(low - prev_close),
            )
            true_ranges.append(tr)

        if not true_ranges:
            return 0.0

        # Use simple average for last `period` values
        recent = true_ranges[-period:] if len(true_ranges) >= period else true_ranges
        return float(np.mean(recent))

    @staticmethod
    def compute_atr_from_returns(
        returns: list[float],
        period: int = 14,
    ) -> float:
        """Compute ATR-equivalent from percentage returns.
        
        Useful when you only have close prices, not OHLC.
        Returns ATR as a percentage (e.g., 0.5 means 0.5% avg movement).
        """
        if len(returns) < 2:
            return 0.0

        abs_returns = [abs(r) for r in returns]
        recent = abs_returns[-period:] if len(abs_returns) >= period else abs_returns
        return float(np.mean(recent))

    def get_targets(
        self,
        price: float,
        atr: float,
    ) -> Tuple[float, float, float]:
        """Get adaptive target%, stop%, and trail giveback%.
        
        Args:
            price: Current entry price
            atr: Average True Range (absolute price value)
        
        Returns:
            (target_pct, stop_pct, trail_giveback_pct)
            All as percentages (e.g., 2.0 means 2%)
        """
        if price <= 0 or atr <= 0:
            # Fallback: use minimum targets
            return (
                self.min_target_pct,
                self.min_stop_pct,
                self.trail_giveback_pct,
            )

        # Convert ATR to percentage of price
        atr_pct = (atr / price) * 100.0

        # Scale targets
        raw_target = atr_pct * self.atr_multiplier
        raw_stop = atr_pct * self.stop_multiplier

        # Apply floors and ceilings
        target_pct = max(self.min_target_pct, min(self.max_target_pct, raw_target))
        stop_pct = max(self.min_stop_pct, min(self.max_stop_pct, raw_stop))

        # Trail giveback should be smaller than stop (otherwise trail triggers before stop)
        trail_pct = min(self.trail_giveback_pct, stop_pct * 0.5)

        return (round(target_pct, 2), round(stop_pct, 2), round(trail_pct, 2))

    def get_targets_for_product(
        self,
        product_id: str,
        price: float,
        prices: list[float],
        period: int = 14,
    ) -> Tuple[float, float, float]:
        """Get adaptive targets from a price series (convenience method).
        
        Args:
            product_id: Symbol (for logging)
            price: Current entry price
            prices: Recent close prices (at least `period + 1` values)
            period: ATR period length
        
        Returns:
            (target_pct, stop_pct, trail_giveback_pct)
        """
        if len(prices) < period + 1:
            # Not enough data — use minimum targets
            return (
                self.min_target_pct,
                self.min_stop_pct,
                self.trail_giveback_pct,
            )

        # Compute returns from prices
        returns = [
            (prices[i] - prices[i - 1]) / prices[i - 1] * 100
            for i in range(1, len(prices))
        ]

        atr_pct = self.compute_atr_from_returns(returns, period)
        atr_price = price * atr_pct / 100.0

        return self.get_targets(price, atr_price)


def demo():
    """Demonstrate adaptive targets for different products."""
    calc = AdaptiveTargetCalculator(
        atr_multiplier=2.0,
        stop_multiplier=1.0,
        min_target_pct=0.3,
        max_target_pct=10.0,
    )

    # Simulate different products with their typical ATR
    products = [
        # (product, price, typical_atr_pct)
        ("AUD-USD (forex)", 0.7146, 0.08),       # 0.08% avg movement
        ("SPX-USD (mid-cap)", 0.388, 0.5),         # 0.5% avg movement
        ("CQT-USD (microcap)", 0.00049, 1.0),      # 1.0% avg movement
        ("MOG-USD (ultra-micro)", 1.5e-07, 3.5),   # 3.5% avg movement
        ("BASED-USD (small-cap)", 0.128, 0.8),      # 0.8% avg movement
        ("AERO-USD (mid-cap)", 0.427, 0.6),         # 0.6% avg movement
    ]

    print("=" * 70)
    print("VOLATILITY-ADAPTIVE TARGETS — Demo")
    print("=" * 70)
    print(
        f"{'Product':>25} {'Price':>12} {'ATR%':>6} "
        f"{'Target%':>8} {'Stop%':>6} {'Trail%':>7}"
    )
    print("-" * 70)

    for product, price, atr_pct in products:
        atr_price = price * atr_pct / 100.0
        target, stop, trail = calc.get_targets(price, atr_price)
        print(
            f"{product:>25} {price:>12.6f} {atr_pct:>5.2f}% "
            f"{target:>7.2f}% {stop:>5.2f}% {trail:>6.2f}%"
        )

    print(
        f"\nCompare to FIXED 5% target / 1% stop / 1.5% trail:"
    )
    print(f"  AUD-USD: 5% target is {5/0.16:.0f}x its typical movement!")
    print(f"  MOG-USD: 5% target is achievable (3.5% ATR × 2 = 7%)")
    print(f"  CQT-USD: 5% target is 5x its typical movement")


if __name__ == "__main__":
    demo()
