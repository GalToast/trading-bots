"""BTC M15 Warp adaptation damage analysis.

The single box_geometry_adjust event at 2026-04-15T20:57:50 widened:
  step_buy:  75.0 -> 324.12  (4.3x wider)
  step_sell: 75.0 -> 108.04  (1.44x wider, later adapted to 259)

Before adaptation: BTC M15 $75 step = +$22.37/close (validated)
After adaptation:  BTC M15 $75->324 step = +$4.69/close (current)

This script computes the exact damage from the state file.
"""

import json
from pathlib import Path

STATE = Path("reports/penetration_lattice_live_btcusd_m15_warp_state.json")

def main():
    with open(STATE) as f:
        state = json.load(f)

    sym = state["symbols"]["BTCUSD"]
    meta = state["metadata"]

    realized_closes = sym["realized_closes"]
    realized_net = sym["realized_net_usd"]
    anchor_resets = sym["anchor_resets"]
    anchor = sym["anchor"]
    base_step = meta["step"]
    adapted_step_buy = sym["base_step_buy_px"]
    adapted_step_sell = sym["base_step_sell_px"]
    open_count = len(sym["open_tickets"])
    next_buy = sym["next_buy_level"]
    next_sell = sym["next_sell_level"]

    # Get latest tick
    latest_tick_ms = state["runner"].get("latest_tick_source_counts", {})
    pid = state["runner"]["pid"]
    started = state["runner"]["started_at"]
    heartbeat = state["runner"]["heartbeat_at"]

    print("=" * 70)
    print("BTC M15 WARP — ADAPTATION DAMAGE ANALYSIS")
    print("=" * 70)

    print(f"\n--- RUNNER STATUS ---")
    print(f"PID: {pid}")
    print(f"Started: {started}")
    print(f"Heartbeat: {heartbeat}")
    print(f"Direct live: {meta['direct_live']}")

    print(f"\n--- PERFORMANCE ---")
    print(f"Realized closes: {realized_closes}")
    print(f"Realized net: ${realized_net:.2f}")
    print(f"$/close: ${realized_net / max(realized_closes, 1):.2f}")
    print(f"Anchor resets: {anchor_resets}")
    print(f"Reset/close ratio: {anchor_resets / max(realized_closes, 1):.2f}")

    print(f"\n--- VALIDATED BASELINE (pre-adaptation) ---")
    print(f"Proven step: ${base_step:.0f}")
    print(f"Validated $/close: $22.37 (clean forward from validated-edges.md)")
    print(f"Expected net at {realized_closes}c: ${22.37 * realized_closes:.2f}")

    print(f"\n--- ACTUAL PERFORMANCE ---")
    print(f"Actual net: ${realized_net:.2f}")
    shortfall = (22.37 * realized_closes) - realized_net
    print(f"Shortfall vs baseline: ${shortfall:.2f}")
    print(f"Edge degradation: {(1 - realized_net / (22.37 * realized_closes)) * 100:.1f}%")

    print(f"\n--- ADAPTIVE GEOMETRY ---")
    print(f"Base step: ${base_step:.0f}")
    print(f"Adapted step_buy: ${adapted_step_buy:.2f} ({adapted_step_buy/base_step:.1f}x base)")
    print(f"Adapted step_sell: ${adapted_step_sell:.2f} ({adapted_step_sell/base_step:.1f}x base)")
    print(f"Dynamic geometry: {meta['dynamic_geometry_enabled']}")

    print(f"\n--- CURRENT GRID STATE ---")
    print(f"Anchor: ${anchor:.2f}")
    print(f"Next BUY level: ${next_buy:.2f}")
    print(f"Next SELL level: ${next_sell:.2f}")
    print(f"Grid span: ${next_sell - next_buy:.2f}")
    print(f"Open positions: {open_count}")

    # Analyze open positions
    for tkt in sym["open_tickets"][:3]:
        fill = tkt["fill_price"]
        direction = tkt["direction"]
        from_rearm = tkt.get("from_rearm", False)
        level = tkt["level_idx"]
        dist = tkt.get("anchor_distance_px_at_open", 0)
        print(f"  {direction} L{level}: fill=${fill:.2f} rearm={from_rearm} dist=${dist:.0f}")

    print(f"\n--- CONCLUSION ---")
    if adapted_step_buy > base_step * 2:
        print(f"⚠️  Adaptive geometry has widened steps {adapted_step_buy/base_step:.0f}x beyond proven optimum")
        print(f"   This is COUNTERPRODUCTIVE adaptation: edge dropped 79%")
        print(f"   The box_geometry_adjust at 2026-04-15T20:57:50 widened from $75 -> $324")
        print(f"   RECOMMENDATION: Add proven-ceiling constraint to adaptive geometry")
        print(f"   Never widen a forward-proven step beyond its validated coefficient")
    else:
        print(f"✓  Adaptive geometry within bounds")

    print(f"\n--- THE ADAPTIVE LATTICE LESSON ---")
    print(f"A 'perfect' adaptive lattice must know when NOT to adapt.")
    print(f"The current system adapted a PROVEN geometry ($75) to an")
    print(f"unproven adaptive rule (box consolidation detector), destroying")
    print(f"79% of the edge. The controller needs:")
    print(f"  1. Proven-ceiling: never widen beyond validated step")
    print(f"  2. Anchor-reset immunity: resets aren't always step-width signal")
    print(f"  3. Adaptation cost accounting: track $/close before/after each adapt")

if __name__ == "__main__":
    main()
