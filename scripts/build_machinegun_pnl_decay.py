#!/usr/bin/env python3
"""Build PnL decay tracker from the machinegun opportunity tape.

Tracks how the held position's unrealized PnL, bid price, and momentum
change across scans. Identifies if momentum is accelerating or fading.

Writes: reports/coinbase_spot_machinegun_pnl_decay.md
"""
import json
import statistics
from pathlib import Path
from datetime import datetime

ROOT = Path(__file__).resolve().parent.parent
TAPE_PATH = ROOT / "reports" / "coinbase_spot_machinegun_opportunity_tape.jsonl"
MD_PATH = ROOT / "reports" / "coinbase_spot_machinegun_pnl_decay.md"


def main():
    scans = []
    with open(TAPE_PATH, "r") as f:
        for line in f:
            if line.strip():
                scans.append(json.loads(line))

    if not scans:
        print("No scans found.")
        return

    # Extract PnL trajectory
    trajectory = []
    for s in scans:
        pos = s["current_position_mark"]
        dec = s["decision"]
        trajectory.append({
            "ts": s["ts_utc"],
            "bid": pos["bid"],
            "entry_price": pos["entry_price"],
            "highest_bid": pos["highest_bid"],
            "trail_stop": pos["trail_stop"],
            "gross_pnl": pos["gross_pnl"],
            "net_pnl": pos["net_pnl"],
            "net_pct": pos["net_pct_on_cost"],
            "loss_pct": pos["loss_pct"],
            "distance_to_trail": pos["distance_to_trail_pct"],
            "decision": dec["decision"],
            "edge": dec.get("current_edge_over_hurdle_pct", dec.get("edge_over_hurdle_pct", 0)),
        })

    first = trajectory[0]
    last = trajectory[-1]
    peak_bid = max(t["highest_bid"] for t in trajectory)
    min_net_pct = min(t["net_pct"] for t in trajectory)
    current_net_pct = last["net_pct"]

    # Momentum analysis - bid price trajectory
    bids = [t["bid"] for t in trajectory]
    net_pcts = [t["net_pct"] for t in trajectory]

    # Calculate rate of change
    pnl_changes = []
    for i in range(1, len(trajectory)):
        delta = trajectory[i]["net_pnl"] - trajectory[i-1]["net_pnl"]
        pnl_changes.append(delta)

    # Find the recovery zone
    peak_loss_idx = min(range(len(trajectory)), key=lambda i: trajectory[i]["net_pnl"])
    peak_loss = trajectory[peak_loss_idx]

    # Best recovery point
    best_recovery_idx = max(range(len(trajectory)), key=lambda i: trajectory[i]["net_pnl"])
    best_recovery = trajectory[best_recovery_idx]

    # Build report
    lines = [
        "# Coinbase Spot Machinegun PnL Decay Tracker",
        "",
        "## Leadership Read",
        "",
        f"- Held product: `{last['bid']}` → `{first['entry_price']}` entry",
        f"- Current: **{last['net_pct']:.2f}%** net (was **{min_net_pct:.2f}%** at worst)",
        f"- Peak bid reached: **{peak_bid:.5f}** (entry was {first['entry_price']:.5f}, need {(peak_bid / first['entry_price'] - 1) * 100:.2f}% to break even on gross)",
        f"- Trail stop: **{last['trail_stop']:.5f}** (bid needs to drop {(last['trail_stop'] / last['bid'] - 1) * 100:.2f}% from current to trigger exit)",
        f"- Edge over hurdle: **{last['edge']:.1f}%** (still clears fast hurdle)",
        f"- Scans: {len(trajectory)} | Duration: {trajectory[0]['ts'][:19]} → {last['ts'][:19]}",
        f"- Decisions: hold={sum(1 for t in trajectory if t['decision'] == 'hold_challenger_not_fee_clear')}, open={sum(1 for t in trajectory if 'open' in t['decision'])}",
        "",
    ]

    # PnL trajectory table (every 5th scan for readability)
    lines.append("## PnL Trajectory (every 5th scan)")
    lines.append("")
    lines.append("| Scan | Time | Bid | Entry | Gross PnL | Net PnL | Net % | Distance to Trail |")
    lines.append("| ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |")
    for i, t in enumerate(trajectory):
        if i % 5 == 0 or i == len(trajectory) - 1:
            lines.append(f"| {i+1} | {t['ts'][11:23]} | {t['bid']:.5f} | {t['entry_price']:.5f} | ${t['gross_pnl']:.4f} | ${t['net_pnl']:.4f} | {t['net_pct']:.2f}% | {t['distance_to_trail']:.2f}% |")
    lines.append("")

    # Momentum direction
    lines.append("## Momentum Direction")
    lines.append("")

    # Split trajectory into thirds
    third = len(trajectory) // 3
    if third > 0:
        early_bids = [t["bid"] for t in trajectory[:third]]
        mid_bids = [t["bid"] for t in trajectory[third:2*third]]
        late_bids = [t["bid"] for t in trajectory[2*third:]]

        early_net = [t["net_pct"] for t in trajectory[:third]]
        mid_net = [t["net_pct"] for t in trajectory[third:2*third]]
        late_net = [t["net_pct"] for t in trajectory[2*third:]]

        lines.append(f"| Phase | Avg Bid | Avg Net % | Momentum |")
        lines.append(f"| --- | ---: | ---: | --- |")
        lines.append(f"| Early ({len(early_bids)} scans) | {statistics.mean(early_bids):.5f} | {statistics.mean(early_net):.2f}% | {'📈 Recovering' if early_bids[-1] > early_bids[0] else '📉 Declining'} |")
        lines.append(f"| Middle ({len(mid_bids)} scans) | {statistics.mean(mid_bids):.5f} | {statistics.mean(mid_net):.2f}% | {'📈 Recovering' if mid_bids[-1] > mid_bids[0] else '📉 Declining'} |")
        lines.append(f"| Late ({len(late_bids)} scans) | {statistics.mean(late_bids):.5f} | {statistics.mean(late_net):.2f}% | {'📈 Recovering' if late_bids[-1] > late_bids[0] else '📉 Declining'} |")
        lines.append("")

        # Recovery analysis
        recovery_rate = (late_net[-1] - early_net[0]) / len(trajectory) if len(trajectory) > 0 else 0
        lines.append(f"**Recovery rate**: {recovery_rate:.4f}% net improvement per scan")
        lines.append(f"**Estimated scans to breakeven**: {abs(last['net_pct']) / abs(recovery_rate) if recovery_rate > 0 else 'N/A (no recovery)'} scans at current rate")
        lines.append("")

    # Fee recovery projection
    lines.append("## Fee Recovery Projection")
    lines.append("")
    lines.append(f"Current position:")
    lines.append(f"- Entry: ${first['entry_price']:.5f} (ask)")
    lines.append(f"- Current bid: ${last['bid']:.5f}")
    lines.append(f"- Gross move needed to breakeven: {(first['entry_price'] / last['bid'] - 1) * 100:.2f}%")
    lines.append(f"- Round-trip fee at taker: 240bps = 2.40%")
    lines.append(f"- Round-trip fee at maker: 120bps = 1.20%")
    lines.append(f"- Bid needed for taker breakeven: ${first['entry_price'] * 1.024:.5f}")
    lines.append(f"- Bid needed for maker breakeven: ${first['entry_price'] * 1.012:.5f}")
    lines.append(f"- Current bid: ${last['bid']:.5f}")
    lines.append(f"- Gap to taker breakeven: {((first['entry_price'] * 1.024) / last['bid'] - 1) * 100:.2f}%")
    lines.append(f"- Gap to maker breakeven: {((first['entry_price'] * 1.012) / last['bid'] - 1) * 100:.2f}%")
    lines.append("")

    # Recommendations
    lines.append("## Assessment")
    lines.append("")
    if last["net_pct"] < -5:
        lines.append("⚠️ **Deep drawdown** — position is more than 5% underwater. Trail stop has significant buffer but the position has not recovered.")
    elif last["net_pct"] < -2:
        lines.append("⚡ **Moderate drawdown** — position recovering but still 2-5% underwater. Trail stop is active and providing protection.")
    else:
        lines.append("✅ **Shallow drawdown** — position near breakeven. Trail stop is close but position momentum is holding.")

    lines.append("")
    lines.append(f"Generated: {datetime.utcnow().isoformat()}Z")
    lines.append("")

    report = "\n".join(lines)
    MD_PATH.write_text(report, encoding="utf-8")
    print(report)


if __name__ == "__main__":
    main()
