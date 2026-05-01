#!/usr/bin/env python3
from __future__ import annotations

from benchmark_fx_unified_basket import LadderRow, mixed_package, unified_candidates


def test_practical_winners() -> None:
    rows = [
        LadderRow("EURUSD", "outer_gap2_alpha50", 0.5, 100.0, 220.0, 120.0),
        LadderRow("EURUSD", "allprof_gap1_alpha50", 0.5, 100.0, 180.0, 80.0),
        LadderRow("GBPUSD", "outer_gap2_alpha50", 0.5, 150.0, 240.0, 90.0),
        LadderRow("GBPUSD", "allprof_gap1_alpha50", 0.5, 150.0, 310.0, 160.0),
        LadderRow("EURUSD", "outer_gap2_alpha100", 1.0, 100.0, 260.0, 160.0),
        LadderRow("GBPUSD", "allprof_gap1_alpha100", 1.0, 150.0, 360.0, 210.0),
    ]

    unified = unified_candidates(rows, ["EURUSD", "GBPUSD"], 0.5)
    assert unified[0].policy == "allprof_gap1_alpha50"
    assert round(unified[0].combined_usd, 2) == 490.00
    assert round(unified[0].delta_vs_baseline_usd, 2) == 240.00

    mixed = mixed_package(rows, ["EURUSD", "GBPUSD"], 0.5)
    assert mixed.by_symbol_policy == {
        "EURUSD": "outer_gap2_alpha50",
        "GBPUSD": "allprof_gap1_alpha50",
    }
    assert round(mixed.combined_usd, 2) == 530.00
    assert round(mixed.delta_vs_baseline_usd, 2) == 280.00


if __name__ == "__main__":
    test_practical_winners()
    print("ok")
