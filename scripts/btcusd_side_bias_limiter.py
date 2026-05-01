#!/usr/bin/env python3
"""BTCUSD side-bias limiter — shadow replay of exposure caps on BTC H1.

Replays shadow BTCUSD events and tests:
  1. Side-bias ratio cap (max BUY:SELL or SELL:BUY ratio)
  2. Total exposure cap (max open positions across both sides)
  3. Per-side cap (max open per direction)

This is not broker-authoritative PnL. It is useful for exploratory shadow
replay only and must be reconciled against the broker scoreboard before any
live cap change is proposed.
"""
import csv
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
EVENT_PATH = ROOT / "reports" / "penetration_lattice_shadow_btcusd_exc2_tight_events.jsonl"
STATE_PATH = ROOT / "reports" / "penetration_lattice_shadow_btcusd_exc2_tight_state.json"
SCOREBOARD_PATH = ROOT / "reports" / "penetration_lattice_lane_scoreboard.csv"
LANE_ID = "live_btcusd_exc2_tight_941779"


def load_events():
    events = []
    with open(EVENT_PATH) as f:
        for line in f:
            line = line.strip()
            if line:
                events.append(json.loads(line))
    return events


def load_broker_total_row() -> dict[str, str] | None:
    if not SCOREBOARD_PATH.exists():
        return None
    with SCOREBOARD_PATH.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            if (
                row.get("lane_id") == LANE_ID
                and row.get("symbol") == "TOTAL"
                and row.get("realized_basis") == "broker"
            ):
                return row
    return None


def as_float(row: dict[str, str] | None, key: str, default: float = 0.0) -> float:
    if not row:
        return default
    try:
        return float(row.get(key, default) or default)
    except (TypeError, ValueError):
        return default


def as_int(row: dict[str, str] | None, key: str, default: int = 0) -> int:
    if not row:
        return default
    try:
        return int(float(row.get(key, default) or default))
    except (TypeError, ValueError):
        return default


def compare_replay_to_broker(
    replay_result: dict[str, float | int], broker_row: dict[str, str] | None
) -> dict[str, float | int | bool]:
    if not broker_row:
        return {
            "available": False,
            "realized_delta": 0.0,
            "closes_delta": 0,
            "open_count_delta": 0,
            "material_divergence": False,
        }
    realized_delta = float(replay_result["total_realized"]) - as_float(broker_row, "realized_usd")
    closes_delta = int(replay_result["total_closes"]) - as_int(broker_row, "closes")
    open_count_delta = int(replay_result["final_open_count"]) - as_int(broker_row, "open_count")
    material_divergence = (
        abs(realized_delta) > 25.0 or abs(closes_delta) > 1 or abs(open_count_delta) > 1
    )
    return {
        "available": True,
        "realized_delta": realized_delta,
        "closes_delta": closes_delta,
        "open_count_delta": open_count_delta,
        "material_divergence": material_divergence,
    }


def simulate_with_caps(events, side_bias_ratio=None, total_cap=None, per_side_cap=None):
    """Replay events with exposure caps applied.
    
    Args:
        side_bias_ratio: Max ratio of one side vs other (e.g., 3.0 = max 3:1)
        total_cap: Max total open positions
        per_side_cap: Max open per direction
    """
    open_positions = []  # list of (direction, entry_price, entry_idx)
    realized_pnls = []
    blocked_opens = 0
    total_opens = 0

    for evt in events:
        action = evt.get("action")
        
        if action == "open_ticket":
            total_opens += 1
            direction = evt["direction"]
            entry_price = evt.get("entry_fill_price", evt.get("entry_price"))
            
            # Check caps
            buy_count = sum(1 for p in open_positions if p[0] == "BUY")
            sell_count = sum(1 for p in open_positions if p[0] == "SELL")
            total_open = len(open_positions)
            
            blocked = False
            
            if per_side_cap is not None:
                if direction == "BUY" and buy_count >= per_side_cap:
                    blocked = True
                if direction == "SELL" and sell_count >= per_side_cap:
                    blocked = True
            
            if total_cap is not None and total_open >= total_cap:
                blocked = True
            
            if side_bias_ratio is not None and open_positions:
                if direction == "BUY" and sell_count > 0:
                    if buy_count / sell_count >= side_bias_ratio:
                        blocked = True
                elif direction == "SELL" and buy_count > 0:
                    if sell_count / buy_count >= side_bias_ratio:
                        blocked = True
                elif direction == "BUY" and sell_count == 0 and buy_count >= side_bias_ratio:
                    # Allow only up to the ratio when no counterbalance exists
                    if buy_count >= side_bias_ratio:
                        blocked = True
                elif direction == "SELL" and buy_count == 0 and sell_count >= side_bias_ratio:
                    if sell_count >= side_bias_ratio:
                        blocked = True
            
            if blocked:
                blocked_opens += 1
            else:
                open_positions.append((direction, entry_price, len(realized_pnls)))
        
        elif action == "close_ticket":
            direction = evt["direction"]
            pnl = evt.get("realized_pnl", 0)
            
            # Find matching open position (FIFO)
            matched = None
            for i, pos in enumerate(open_positions):
                if pos[0] == direction:
                    matched = i
                    break
            
            if matched is not None:
                open_positions.pop(matched)
                realized_pnls.append(pnl)
            # else: close without open (shouldn't happen, but ignore)

    return {
        "realized_pnls": realized_pnls,
        "total_realized": sum(realized_pnls),
        "total_closes": len(realized_pnls),
        "blocked_opens": blocked_opens,
        "total_opens": total_opens,
        "final_open_count": len(open_positions),
        "final_buy_count": sum(1 for p in open_positions if p[0] == "BUY"),
        "final_sell_count": sum(1 for p in open_positions if p[0] == "SELL"),
        "win_rate": sum(1 for p in realized_pnls if p > 0) / max(1, len(realized_pnls)) * 100,
        "avg_pnl": sum(realized_pnls) / max(1, len(realized_pnls)),
    }


def main():
    events = load_events()
    broker_row = load_broker_total_row()
    print(f"Loaded {len(events)} events")
    
    # Count opens and closes
    opens = [e for e in events if e.get("action") == "open_ticket"]
    closes = [e for e in events if e.get("action") == "close_ticket"]
    print(f"Open events: {len(opens)}, Close events: {len(closes)}")
    print("\n" + "=" * 70)
    print("  WARNING: SHADOW EVENT REPLAY ONLY")
    print("  Do not use this script alone for live BTC H1 cap decisions.")
    print("=" * 70)

    # Baseline (no caps)
    baseline = simulate_with_caps(events)
    comparison = compare_replay_to_broker(baseline, broker_row)
    if broker_row:
        broker_realized = as_float(broker_row, "realized_usd")
        broker_floating = as_float(broker_row, "floating_usd")
        broker_net = as_float(broker_row, "net_usd")
        broker_closes = as_int(broker_row, "closes")
        broker_open_count = as_int(broker_row, "open_count")
        print(f"\n{'='*70}")
        print("  BROKER SCOREBOARD TRUTH")
        print(f"{'='*70}")
        print(f"  Broker realized: ${broker_realized:+.2f} ({broker_closes} closes)")
        print(f"  Broker floating: ${broker_floating:+.2f}")
        print(f"  Broker net:      ${broker_net:+.2f}")
        print(f"  Broker open count: {broker_open_count}")
        print(f"  Replay realized delta: ${comparison['realized_delta']:+.2f}")
        print(f"  Replay closes delta:   {comparison['closes_delta']:+d}")
        print(f"  Replay open delta:     {comparison['open_count_delta']:+d}")
        if comparison["material_divergence"]:
            print("  MATERIAL DIVERGENCE: replay is not faithful enough for live cap recommendations.")
        else:
            print("  Replay is near broker truth on headline counts; still treat as exploratory only.")

    print(f"\n{'='*70}")
    print(f"  BASELINE (no caps)")
    print(f"  Realized: ${baseline['total_realized']:+.2f} ({baseline['total_closes']} closes)")
    print(f"  WR: {baseline['win_rate']:.1f}%")
    print(f"  Avg PnL: ${baseline['avg_pnl']:+.2f}")
    print(f"  Final open: {baseline['final_open_count']} ({baseline['final_buy_count']}B/{baseline['final_sell_count']}S)")
    print(f"{'='*70}")

    # Test various side-bias ratios
    print(f"\n{'='*70}")
    print(f"  SIDE-BIAS RATIO TESTS")
    print(f"{'='*70}")
    print(f"\n{'Ratio':<10} {'Realized':>12} {'WR':>8} {'Closes':>8} {'Blocked':>10} {'Final':>10}")
    print(f"{'-'*60}")
    
    for ratio in [1.5, 2.0, 3.0, 5.0, 10.0]:
        result = simulate_with_caps(events, side_bias_ratio=ratio)
        delta = result['total_realized'] - baseline['total_realized']
        print(f"{ratio:<10.1f} ${result['total_realized']:>10.2f} {result['win_rate']:>7.1f}% {result['total_closes']:>8} {result['blocked_opens']:>10} {result['final_open_count']:>5} ({result['final_buy_count']}B/{result['final_sell_count']}S)")

    # Test per-side caps
    print(f"\n{'='*70}")
    print(f"  PER-SIDE CAP TESTS")
    print(f"{'='*70}")
    print(f"\n{'PerSide':<10} {'Realized':>12} {'WR':>8} {'Closes':>8} {'Blocked':>10} {'Final':>10}")
    print(f"{'-'*60}")
    
    for cap in [3, 5, 8, 10, 15, 20, 30]:
        result = simulate_with_caps(events, per_side_cap=cap)
        print(f"{cap:<10} ${result['total_realized']:>10.2f} {result['win_rate']:>7.1f}% {result['total_closes']:>8} {result['blocked_opens']:>10} {result['final_open_count']:>5} ({result['final_buy_count']}B/{result['final_sell_count']}S)")

    # Test total caps
    print(f"\n{'='*70}")
    print(f"  TOTAL EXPOSURE CAP TESTS")
    print(f"{'='*70}")
    print(f"\n{'TotalCap':<10} {'Realized':>12} {'WR':>8} {'Closes':>8} {'Blocked':>10} {'Final':>10}")
    print(f"{'-'*60}")
    
    for cap in [5, 10, 15, 20, 30, 40, 50]:
        result = simulate_with_caps(events, total_cap=cap)
        print(f"{cap:<10} ${result['total_realized']:>10.2f} {result['win_rate']:>7.1f}% {result['total_closes']:>8} {result['blocked_opens']:>10} {result['final_open_count']:>5} ({result['final_buy_count']}B/{result['final_sell_count']}S)")

    # Combined cap test (recommended config)
    print(f"\n{'='*70}")
    print(f"  RECOMMENDED CONFIG: side_bias=3.0, per_side=8, total=15")
    print(f"{'='*70}")
    rec = simulate_with_caps(events, side_bias_ratio=3.0, per_side_cap=8, total_cap=15)
    delta = rec['total_realized'] - baseline['total_realized']
    print(f"  Realized: ${rec['total_realized']:+.2f} ({delta:+.2f} vs baseline)")
    print(f"  WR: {rec['win_rate']:.1f}%")
    print(f"  Closes: {rec['total_closes']}")
    print(f"  Blocked: {rec['blocked_opens']} of {rec['total_opens']} opens")
    print(f"  Final: {rec['final_open_count']} ({rec['final_buy_count']}B/{rec['final_sell_count']}S)")

    # Summary
    print(f"\n{'='*70}")
    print(f"  SHADOW-REPLAY TAKEAWAYS")
    print(f"{'='*70}")
    if comparison["material_divergence"]:
        print(
            """
  NO LIVE CONFIG RECOMMENDATION FROM THIS RUN.
  The baseline replay diverges materially from broker scoreboard truth, so these
  cap sweeps are exploratory only. Use them to generate hypotheses, not live
  parameter changes.
"""
        )
    else:
        print(
            f"""
  Replay-only observations:
  1. Side-bias ratio 3:1 blocks {simulate_with_caps(events, side_bias_ratio=3.0)['blocked_opens']} opens
     and changes replay realized by ${simulate_with_caps(events, side_bias_ratio=3.0)['total_realized'] - baseline['total_realized']:+.2f}.
  2. Per-side cap 8 would end at {simulate_with_caps(events, per_side_cap=8)['final_buy_count']} BUY /
     {simulate_with_caps(events, per_side_cap=8)['final_sell_count']} SELL in the replay.
  3. Combined ratio=3, per_side=8, total=15 ends at ${rec['total_realized']:+.2f}
     with {rec['final_open_count']} open positions in the replay.

  Even when replay tracks broker headline counts reasonably, keep this tool in
  hypothesis-generation mode until a broker-authoritative validation path exists.
"""
        )


if __name__ == "__main__":
    main()
