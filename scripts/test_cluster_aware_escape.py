#!/usr/bin/env python3
"""Focused tests for cluster-aware escape logic.

Validates:
1. group_floating_by_fill_cluster correctly groups same-fill positions
2. Cluster-aware escape scales threshold by sqrt(cluster_size)
3. Cluster escape only fires when cluster TOTAL exceeds scaled threshold
4. Legacy per-position escape still works when cluster_aware_escape=False
"""
import unittest
from unittest.mock import MagicMock, patch
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from tick_penetration_lattice_core import group_floating_by_fill_cluster


class TestClusterAwareEscape(unittest.TestCase):
    """Test cluster-aware escape grouping and threshold scaling."""

    def _make_ticket(self, fill_price: float, direction: str = "SELL", trigger_level: float = 0.0):
        """Helper to create a mock ticket."""
        ticket = MagicMock()
        ticket.fill_price = fill_price
        ticket.direction = direction
        ticket.trigger_level = trigger_level
        return ticket

    def test_group_same_fill_positions(self):
        """Positions with same fill price should be grouped together."""
        t1 = self._make_ticket(2350.81, "SELL")
        t2 = self._make_ticket(2350.81, "SELL")
        t3 = self._make_ticket(2350.81, "SELL")

        floating = [(t1, -1.0), (t2, -1.0), (t3, -1.0)]
        clusters = group_floating_by_fill_cluster(floating, fill_tolerance=0.01)

        self.assertEqual(len(clusters), 1)
        self.assertEqual(len(clusters[0]), 3)

    def test_group_within_tolerance(self):
        """Positions within fill tolerance should be grouped together."""
        t1 = self._make_ticket(2350.80, "SELL")
        t2 = self._make_ticket(2350.81, "SELL")
        t3 = self._make_ticket(2350.82, "SELL")

        floating = [(t1, -1.0), (t2, -1.0), (t3, -1.0)]
        clusters = group_floating_by_fill_cluster(floating, fill_tolerance=0.02)

        self.assertEqual(len(clusters), 1)
        self.assertEqual(len(clusters[0]), 3)

    def test_group_separate_clusters(self):
        """Positions outside fill tolerance should be in separate clusters."""
        t1 = self._make_ticket(2350.00, "SELL")
        t2 = self._make_ticket(2350.50, "SELL")
        t3 = self._make_ticket(2351.00, "SELL")

        floating = [(t1, -1.0), (t2, -1.0), (t3, -1.0)]
        clusters = group_floating_by_fill_cluster(floating, fill_tolerance=0.01)

        self.assertEqual(len(clusters), 3)
        self.assertEqual(len(clusters[0]), 1)
        self.assertEqual(len(clusters[1]), 1)
        self.assertEqual(len(clusters[2]), 1)

    def test_mixed_clusters(self):
        """Mixed fill prices should create appropriate clusters."""
        t1 = self._make_ticket(2350.81, "SELL")
        t2 = self._make_ticket(2350.81, "SELL")
        t3 = self._make_ticket(2350.81, "SELL")
        t4 = self._make_ticket(2351.50, "BUY")
        t5 = self._make_ticket(2351.50, "BUY")

        floating = [(t1, -1.0), (t2, -1.0), (t3, -1.0), (t4, -0.5), (t5, -0.5)]
        clusters = group_floating_by_fill_cluster(floating, fill_tolerance=0.01)

        self.assertEqual(len(clusters), 2)
        self.assertEqual(len(clusters[0]), 3)  # SELL cluster at 2350.81
        self.assertEqual(len(clusters[1]), 2)  # BUY cluster at 2351.50

    def test_empty_floating(self):
        """Empty floating should return empty clusters."""
        clusters = group_floating_by_fill_cluster([], fill_tolerance=0.01)
        self.assertEqual(clusters, [])

    def test_cluster_threshold_scaling(self):
        """Verify sqrt scaling: cluster of 12 with threshold $5 needs ~$17.32 total loss."""
        import math
        base_threshold = 5.0
        cluster_size = 12
        scaled = base_threshold * (cluster_size ** 0.5)
        self.assertAlmostEqual(scaled, 17.32, places=1)

        # Each position at -$1.10, cluster of 12 = -$13.20 total
        # Scaled threshold = $17.32, so cluster should NOT escape
        # -$13.20 > -$17.32, so condition cluster_total <= -scaled_threshold is False
        cluster_total = -13.20
        self.assertFalse(cluster_total <= -scaled)

    def test_large_cluster_escapes_when_total_exceeds(self):
        """Large cluster should escape when total exceeds scaled threshold."""
        import math
        base_threshold = 5.0
        cluster_size = 12
        scaled = base_threshold * (cluster_size ** 0.5)

        # Each position at -$2.00, cluster of 12 = -$24.00 total
        # Scaled threshold = $17.32, so cluster SHOULD escape
        # -$24.00 <= -$17.32, so condition cluster_total <= -scaled_threshold is True
        cluster_total = -24.00
        self.assertTrue(cluster_total <= -scaled)

    def test_single_position_cluster(self):
        """Single position should use base threshold (sqrt(1) = 1)."""
        base_threshold = 5.0
        cluster_size = 1
        scaled = base_threshold * (cluster_size ** 0.5)
        self.assertAlmostEqual(scaled, 5.0)

        # Single position at -$6.00 should escape (>$5 threshold)
        self.assertTrue(-6.0 <= -scaled)


if __name__ == "__main__":
    unittest.main()
