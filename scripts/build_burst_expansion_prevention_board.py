"""Build burst-expansion regime prevention board.

Answers:
1. How many burst_expansion opens resulted in forced escapes vs natural closes?
2. What step widening factor would have prevented burst entries?
3. Which symbols/sessions produce the most burst_expansion regimes?

Feeds into adaptive controller's guarded_toxic_flow mode.
"""
import json
import glob
import os
from datetime import datetime, timezone

REPORTS_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "reports")

ENRICHED_FIELDS = {
    "regime_at_entry", "entry_context", "session_bucket", "spread_at_entry",
    "hold_seconds", "realized_pnl", "first_green_before_fail",
    "same_bar_open_burst_count_at_open", "same_tick_open_burst_count_at_open",
    "base_step_px_at_open", "max_adverse_excursion_pnl",
    "max_favorable_excursion_pnl", "same_bar_round_trip",
    "time_to_first_green_seconds", "peak_pnl_before_exit",
}


def analyze_event_file(filepath):
    """Analyze event log for burst_expansion regime patterns."""
    result = {
        "total_events": 0,
        "open_events": [],
        "close_events": [],
        "burst_expansion_opens": 0,
        "burst_expansion_escapes": 0,
        "burst_expansion_natural_closes": 0,
        "burst_expansion_pnl": 0.0,
        "non_burst_opens": 0,
        "non_burst_escapes": 0,
        "non_burst_natural_closes": 0,
        "non_burst_pnl": 0.0,
        "spread_at_burst_entry": [],
        "burst_counts": [],
        "tick_burst_counts": [],
    }

    lines = open(filepath, "r", encoding="utf-8").readlines()
    result["total_events"] = len(lines)

    for line in lines:
        try:
            ev = json.loads(line.strip())
        except Exception:
            continue
        action = ev.get("action", "")

        if action == "open_ticket":
            regime = ev.get("regime_at_entry", "")
            if "burst" in regime.lower() or regime == "burst_expansion":
                result["burst_expansion_opens"] += 1
                result["spread_at_burst_entry"].append(ev.get("spread_at_entry", 0))
                result["burst_counts"].append(ev.get("same_bar_open_burst_count_at_open", 1))
                result["tick_burst_counts"].append(ev.get("same_tick_open_burst_count_at_open", 1))
            else:
                result["non_burst_opens"] += 1

        if action in ("close_ticket", "forced_unwind", "escape_tier1", "escape_tier2_surgical"):
            pnl = ev.get("realized_pnl", 0.0) or 0.0
            regime = ev.get("regime_at_entry", "")
            is_burst = "burst" in regime.lower() or regime == "burst_expansion"
            is_natural = action == "close_ticket"
            is_escape = action != "close_ticket"

            if is_burst:
                if is_natural:
                    result["burst_expansion_natural_closes"] += 1
                else:
                    result["burst_expansion_escapes"] += 1
                result["burst_expansion_pnl"] += pnl
            else:
                if is_natural:
                    result["non_burst_natural_closes"] += 1
                else:
                    result["non_burst_escapes"] += 1
                result["non_burst_pnl"] += pnl

    # Average spread at burst entry
    if result["spread_at_burst_entry"]:
        result["avg_spread_at_burst_entry"] = sum(result["spread_at_burst_entry"]) / len(result["spread_at_burst_entry"])
    else:
        result["avg_spread_at_burst_entry"] = None

    # Median burst count
    if result["burst_counts"]:
        result["burst_counts"].sort()
        n = len(result["burst_counts"])
        result["median_bar_burst"] = result["burst_counts"][n // 2]
        result["max_bar_burst"] = max(result["burst_counts"])
    else:
        result["median_bar_burst"] = None
        result["max_bar_burst"] = None

    return result


def build_board():
    files = sorted(glob.glob(os.path.join(REPORTS_DIR, "*events.jsonl")),
                   key=os.path.getmtime, reverse=True)

    lanes = []
    total_burst_opens = 0
    total_burst_escapes = 0
    total_burst_natural = 0
    total_burst_pnl = 0.0
    total_non_burst_escapes = 0
    total_non_burst_natural = 0
    total_non_burst_pnl = 0.0
    all_burst_spreads = []
    all_bar_bursts = []

    for f in files:
        result = analyze_event_file(f)
        bn = os.path.basename(f)
        lane_name = bn.replace("penetration_lattice_", "").replace("_events.jsonl", "")

        if result["burst_expansion_opens"] == 0 and result["non_burst_opens"] == 0:
            continue

        total_burst_opens += result["burst_expansion_opens"]
        total_burst_escapes += result["burst_expansion_escapes"]
        total_burst_natural += result["burst_expansion_natural_closes"]
        total_burst_pnl += result["burst_expansion_pnl"]
        total_non_burst_escapes += result["non_burst_escapes"]
        total_non_burst_natural += result["non_burst_natural_closes"]
        total_non_burst_pnl += result["non_burst_pnl"]

        if result["spread_at_burst_entry"]:
            all_burst_spreads.extend(result["spread_at_burst_entry"])
        if result["burst_counts"]:
            all_bar_bursts.extend(result["burst_counts"])

        # Burst escape rate
        if result["burst_expansion_opens"] > 0:
            burst_escape_rate = result["burst_expansion_escapes"] / result["burst_expansion_opens"]
            burst_natural_rate = result["burst_expansion_natural_closes"] / result["burst_expansion_opens"]
        else:
            burst_escape_rate = 0
            burst_natural_rate = 0

        # Non-burst escape rate
        if result["non_burst_opens"] > 0:
            non_burst_escape_rate = result["non_burst_escapes"] / result["non_burst_opens"]
            non_burst_natural_rate = result["non_burst_natural_closes"] / result["non_burst_opens"]
        else:
            non_burst_escape_rate = 0
            non_burst_natural_rate = 0

        lanes.append({
            "lane": lane_name,
            "burst_expansion_opens": result["burst_expansion_opens"],
            "burst_expansion_escapes": result["burst_expansion_escapes"],
            "burst_expansion_natural_closes": result["burst_expansion_natural_closes"],
            "burst_expansion_pnl": round(result["burst_expansion_pnl"], 2),
            "burst_escape_rate": round(burst_escape_rate, 3),
            "burst_natural_rate": round(burst_natural_rate, 3),
            "non_burst_escapes": result["non_burst_escapes"],
            "non_burst_natural_closes": result["non_burst_natural_closes"],
            "non_burst_pnl": round(result["non_burst_pnl"], 2),
            "non_burst_escape_rate": round(non_burst_escape_rate, 3),
            "non_burst_natural_rate": round(non_burst_natural_rate, 3),
            "avg_spread_at_burst_entry": round(result["avg_spread_at_burst_entry"], 2) if result["avg_spread_at_burst_entry"] else None,
            "median_bar_burst": result["median_bar_burst"],
            "max_bar_burst": result["max_bar_burst"],
        })

    # Sort by burst expansion opens descending
    lanes.sort(key=lambda l: -l["burst_expansion_opens"])

    # Calculate what step widening would prevent
    # If burst count is N, widening step by N would prevent multi-fill
    if all_bar_bursts:
        all_bar_bursts.sort()
        prevent_2x = sum(1 for b in all_bar_bursts if b >= 2)
        prevent_3x = sum(1 for b in all_bar_bursts if b >= 3)
        prevent_5x = sum(1 for b in all_bar_bursts if b >= 5)
        total_burst_entries = len(all_bar_bursts)
    else:
        prevent_2x = prevent_3x = prevent_5x = total_burst_entries = 0

    board = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "summary": {
            "total_burst_expansion_opens": total_burst_opens,
            "total_burst_expansion_escapes": total_burst_escapes,
            "total_burst_expansion_natural": total_burst_natural,
            "total_burst_expansion_pnl": round(total_burst_pnl, 2),
            "total_non_burst_escapes": total_non_burst_escapes,
            "total_non_burst_natural": total_non_burst_natural,
            "total_non_burst_pnl": round(total_non_burst_pnl, 2),
            "avg_spread_at_burst_entry": round(sum(all_burst_spreads) / len(all_burst_spreads), 2) if all_burst_spreads else None,
            "prevent_with_2x_step": prevent_2x,
            "prevent_with_3x_step": prevent_3x,
            "prevent_with_5x_step": prevent_5x,
            "total_burst_entries": total_burst_entries,
        },
        "lanes": lanes,
    }

    # Write JSON
    json_path = os.path.join(REPORTS_DIR, "burst_expansion_prevention_board.json")
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(board, f, indent=2, default=str)

    # Write Markdown
    md_path = os.path.join(REPORTS_DIR, "burst_expansion_prevention_board.md")
    with open(md_path, "w", encoding="utf-8") as f:
        f.write("# Burst-Expansion Regime Prevention Board\n\n")
        f.write(f"> Generated: `{board['generated_at']}`\n\n")
        f.write(f"> Feeds into adaptive controller `guarded_toxic_flow` mode.\n\n")
        f.write(f"> Answers: When should the adaptive lattice widen steps or block entries?\n\n")

        s = board["summary"]
        f.write("## Summary\n\n")
        f.write(f"- Total burst-expansion opens: **{s['total_burst_expansion_opens']}**\n")
        f.write(f"- Burst-expansion escapes: **{s['total_burst_expansion_escapes']}**\n")
        f.write(f"- Burst-expansion natural closes: **{s['total_burst_expansion_natural']}**\n")
        f.write(f"- Burst-expansion total PnL: **${s['total_burst_expansion_pnl']:.2f}**\n")
        f.write(f"- Non-burst escapes: **{s['total_non_burst_escapes']}**\n")
        f.write(f"- Non-burst natural closes: **{s['total_non_burst_natural']}**\n")
        f.write(f"- Non-burst total PnL: **${s['total_non_burst_pnl']:.2f}**\n")

        if s['avg_spread_at_burst_entry']:
            f.write(f"- Average spread at burst entry: **{s['avg_spread_at_burst_entry']:.2f}**\n")

        f.write(f"\n## Step Widening Prevention\n\n")
        f.write(f"Of {s['total_burst_entries']} burst entries (same-bar multi-fill):\n")
        f.write(f"- **2x step widening** would have prevented: **{s['prevent_with_2x_step']}** ({100*s['prevent_with_2x_step']/max(s['total_burst_entries'],1):.1f}%)\n")
        f.write(f"- **3x step widening** would have prevented: **{s['prevent_with_3x_step']}** ({100*s['prevent_with_3x_step']/max(s['total_burst_entries'],1):.1f}%)\n")
        f.write(f"- **5x step widening** would have prevented: **{s['prevent_with_5x_step']}** ({100*s['prevent_with_5x_step']/max(s['total_burst_entries'],1):.1f}%)\n")

        # Lanes with burst expansion
        burst_lanes = [l for l in lanes if l["burst_expansion_opens"] > 0]
        if burst_lanes:
            f.write(f"\n## Lanes with burst-expansion opens ({len(burst_lanes)} lanes)\n\n")
            f.write("| Lane | Burst Opens | Burst Escapes | Burst Natural | Burst $ | Burst Escape% | Non-Burst Escape% | Avg Spread | Max Burst |\n")
            f.write("|---|---:|---:|---:|---:|---:|---:|---:|---:|\n")
            for lane in burst_lanes:
                f.write(f"| `{lane['lane']}` | {lane['burst_expansion_opens']} | {lane['burst_expansion_escapes']} | {lane['burst_expansion_natural_closes']} | ${lane['burst_expansion_pnl']:.2f} | {lane['burst_escape_rate']:.1%} | {lane['non_burst_escape_rate']:.1%} | {lane['avg_spread_at_burst_entry'] or 'N/A'} | {lane['max_bar_burst'] or 'N/A'} |\n")

        # Lanes with burst PnL
        burst_loss_lanes = sorted([l for l in lanes if l["burst_expansion_pnl"] < 0],
                                   key=lambda l: l["burst_expansion_pnl"])
        if burst_loss_lanes:
            f.write(f"\n## Top Burst-Expansion Losses\n\n")
            f.write("| Lane | Burst $ | Burst Opens | Escape% | Prevention |\n")
            f.write("|---|---:|---:|---:|---|\n")
            for lane in burst_loss_lanes[:10]:
                f.write(f"| `{lane['lane']}` | ${lane['burst_expansion_pnl']:.2f} | {lane['burst_expansion_opens']} | {lane['burst_escape_rate']:.1%} | Widen step during burst_expansion regime |\n")

        f.write(f"\n## Interpretation\n\n")
        f.write(f"If `burst_escape_rate` >> `non_burst_escape_rate`, the burst_expansion regime is a reliable predictor of escape risk.\n")
        f.write(f"If step widening would have prevented most burst entries, the adaptive controller should widen steps when spread/ATR exceeds normal.\n")
        f.write(f"The adaptive `guarded_toxic_flow` mode should activate when:\n")
        f.write(f"- spread_at_entry > 2x normal spread for this symbol\n")
        f.write(f"- regime = burst_expansion\n")
        f.write(f"- AND widen step by 2-3x to prevent same-bar multi-fill\n")

    print(f"Board generated: {len(lanes)} lanes")
    print(f"  Burst-expansion opens: {s['total_burst_expansion_opens']}")
    print(f"  Burst-expansion escapes: {s['total_burst_expansion_escapes']}")
    print(f"  Burst-expansion natural: {s['total_burst_expansion_natural']}")
    print(f"  Burst-expansion PnL: ${s['total_burst_expansion_pnl']:.2f}")
    print(f"  2x step prevents: {s['prevent_with_2x_step']}/{s['total_burst_entries']}")
    print(f"  3x step prevents: {s['prevent_with_3x_step']}/{s['total_burst_entries']}")
    print(f"Written: {json_path}")
    print(f"Written: {md_path}")


if __name__ == "__main__":
    build_board()
