"""Analyze escape/forced-close patterns across all enriched event logs.

Answers: Is premature escape a systemic pattern or just a shapeshifter quirk?
"""
import json
import glob
import os
from datetime import datetime, timezone

REPORTS_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "reports")

# Close-like event types
CLOSE_ACTIONS = {
    "close_ticket",          # natural lattice close
    "forced_unwind",         # safety forced exit (enriched)
    "escape_tier1",          # escape hatch tier 1
    "escape_tier2_surgical", # escape hatch tier 2 (shapeshifter)
    "offensive_close",       # offensive closure
}

# Enriched Phase 1 fields to check
ENRICHED_FIELDS = {
    "time_to_first_green_seconds",
    "max_favorable_excursion_pnl",
    "max_adverse_excursion_pnl",
    "peak_pnl_before_exit",
    "first_green_before_fail",
    "hold_seconds",
    "spread_at_entry",
    "entry_context",
    "regime_at_entry",
    "session_bucket",
    "token_age_at_fire",
    "armed_duration_seconds",
    "rearm_to_first_green_seconds",
    "rearm_to_fail_seconds",
    "same_bar_round_trip",
    "reclaimed_trigger_level_seen",
    "retraced_0_25x_step_seen",
    "retraced_0_5x_step_seen",
    "same_bar_open_burst_count_at_open",
    "same_tick_open_burst_count_at_open",
}


def analyze_event_file(filepath):
    """Analyze one event log file for escape/close patterns."""
    result = {
        "total_events": 0,
        "open_count": 0,
        "close_events": [],
        "escape_events": [],
        "forced_events": [],
        "natural_close_events": [],
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
            result["open_count"] += 1

        if action in CLOSE_ACTIONS:
            close_info = {
                "action": action,
                "direction": ev.get("direction", ""),
                "realized_pnl": ev.get("realized_pnl", ev.get("realized_pnl_usd", 0.0)),
                "hold_seconds": ev.get("hold_seconds"),
                "first_green_before_fail": ev.get("first_green_before_fail"),
                "max_favorable_excursion_pnl": ev.get("max_favorable_excursion_pnl"),
                "max_adverse_excursion_pnl": ev.get("max_adverse_excursion_pnl"),
                "peak_pnl_before_exit": ev.get("peak_pnl_before_exit"),
                "same_bar_round_trip": ev.get("same_bar_round_trip"),
                "reclaimed_trigger_level_seen": ev.get("reclaimed_trigger_level_seen"),
                "retraced_0_25x_step_seen": ev.get("retraced_0_25x_step_seen"),
                "retraced_0_5x_step_seen": ev.get("retraced_0_5x_step_seen"),
                "same_bar_open_burst_count_at_open": ev.get("same_bar_open_burst_count_at_open"),
                "same_tick_open_burst_count_at_open": ev.get("same_tick_open_burst_count_at_open"),
                "spread_at_entry": ev.get("spread_at_entry"),
                "entry_context": ev.get("entry_context"),
                "regime_at_entry": ev.get("regime_at_entry"),
                "session_bucket": ev.get("session_bucket"),
                "has_enrichment": any(f in ev for f in ENRICHED_FIELDS),
                "ts_utc": ev.get("ts_utc", ""),
            }
            result["close_events"].append(close_info)

            if "escape" in action:
                result["escape_events"].append(close_info)
            elif action == "forced_unwind":
                result["forced_events"].append(close_info)
            elif action == "close_ticket":
                result["natural_close_events"].append(close_info)

    return result


def classify_close(close_info):
    """Classify a close event by its pattern."""
    pnl = close_info.get("realized_pnl", 0.0) or 0.0
    hold = close_info.get("hold_seconds")
    first_green = close_info.get("first_green_before_fail")
    round_trip = close_info.get("same_bar_round_trip")
    burst = close_info.get("same_bar_open_burst_count_at_open", 1) or 1
    action = close_info.get("action", "")

    # Burst + instant + loss = premature escape candidate
    is_burst = burst >= 2
    is_instant = hold is not None and hold == 0
    is_loss = pnl < 0
    never_green = first_green is False
    is_escape = "escape" in action or action == "forced_unwind"

    if is_burst and is_instant and is_loss and never_green and is_escape:
        return "burst_premature_escape"
    elif is_instant and is_loss and never_green and is_escape:
        return "instant_escape"
    elif is_escape and is_loss:
        return "escape_loss"
    elif is_escape and pnl >= 0:
        return "escape_profit"
    elif action == "forced_unwind" and is_loss:
        return "forced_loss"
    elif action == "close_ticket" and pnl >= 0:
        return "natural_profit"
    elif action == "close_ticket" and is_loss:
        return "natural_loss"
    else:
        return "other"


def build_board():
    files = sorted(glob.glob(os.path.join(REPORTS_DIR, "*events.jsonl")),
                   key=os.path.getmtime, reverse=True)

    lanes = []
    total_burst_premature_escapes = 0
    total_instant_escapes = 0
    total_escape_losses = 0
    total_natural_profits = 0
    total_natural_losses = 0
    total_forced_losses = 0
    total_escape_profits = 0

    for f in files:
        result = analyze_event_file(f)
        bn = os.path.basename(f)
        lane_name = bn.replace("penetration_lattice_", "").replace("_events.jsonl", "")

        if not result["close_events"]:
            continue

        # Classify all closes
        classifications = {}
        burst_escapes = []
        instant_escapes = []
        escape_losses = []

        for c in result["close_events"]:
            cls = classify_close(c)
            classifications[cls] = classifications.get(cls, 0) + 1
            if cls == "burst_premature_escape":
                burst_escapes.append(c)
                total_burst_premature_escapes += 1
            elif cls == "instant_escape":
                instant_escapes.append(c)
                total_instant_escapes += 1
            elif cls == "escape_loss":
                escape_losses.append(c)
                total_escape_losses += 1
            elif cls == "escape_profit":
                total_escape_profits += 1
            elif cls == "natural_profit":
                total_natural_profits += 1
            elif cls == "natural_loss":
                total_natural_losses += 1
            elif cls == "forced_loss":
                total_forced_losses += 1

        # Calculate totals
        total_pnl = sum(c.get("realized_pnl", 0.0) or 0.0 for c in result["close_events"])
        escape_pnl = sum(c.get("realized_pnl", 0.0) or 0.0 for c in result["escape_events"] + result["forced_events"])
        natural_pnl = sum(c.get("realized_pnl", 0.0) or 0.0 for c in result["natural_close_events"])

        # Has enrichment on closes?
        enriched_closes = sum(1 for c in result["close_events"] if c.get("has_enrichment"))

        lanes.append({
            "lane": lane_name,
            "total_events": result["total_events"],
            "opens": result["open_count"],
            "closes": len(result["close_events"]),
            "escape_closes": len(result["escape_events"]),
            "forced_closes": len(result["forced_events"]),
            "natural_closes": len(result["natural_close_events"]),
            "enriched_closes": enriched_closes,
            "total_pnl": round(total_pnl, 2),
            "escape_pnl": round(escape_pnl, 2),
            "natural_pnl": round(natural_pnl, 2),
            "burst_premature_escapes": len(burst_escapes),
            "instant_escapes": len(instant_escapes),
            "escape_losses": len(escape_losses),
            "classifications": classifications,
            "burst_escape_details": burst_escapes[:5],  # First 5 for detail
        })

    # Sort by burst premature escapes descending
    lanes.sort(key=lambda l: -l["burst_premature_escapes"])

    board = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "summary": {
            "total_lanes_with_closes": len(lanes),
            "total_burst_premature_escapes": total_burst_premature_escapes,
            "total_instant_escapes": total_instant_escapes,
            "total_escape_losses": total_escape_losses,
            "total_escape_profits": total_escape_profits,
            "total_natural_profits": total_natural_profits,
            "total_natural_losses": total_natural_losses,
            "total_forced_losses": total_forced_losses,
        },
        "lanes": lanes,
    }

    # Write JSON
    json_path = os.path.join(REPORTS_DIR, "escape_pattern_analysis_board.json")
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(board, f, indent=2, default=str)

    # Write Markdown
    md_path = os.path.join(REPORTS_DIR, "escape_pattern_analysis_board.md")
    with open(md_path, "w", encoding="utf-8") as f:
        f.write("# Escape Pattern Analysis Board\n\n")
        f.write(f"> Generated: `{board['generated_at']}`\n\n")
        f.write(f"> Answers: Is premature escape a systemic pattern or just a shapeshifter quirk?\n\n")

        s = board["summary"]
        f.write("## Summary\n\n")
        f.write(f"- Lanes with close events: **{s['total_lanes_with_closes']}**\n")
        f.write(f"- Burst premature escapes (burst + instant + loss + never green + escape): **{s['total_burst_premature_escapes']}**\n")
        f.write(f"- Instant escapes (0s hold, loss, never green, escape): **{s['total_instant_escapes']}**\n")
        f.write(f"- Escape losses (other): **{s['total_escape_losses']}**\n")
        f.write(f"- Escape profits: **{s['total_escape_profits']}**\n")
        f.write(f"- Natural profits (close_ticket, pnl>0): **{s['total_natural_profits']}**\n")
        f.write(f"- Natural losses (close_ticket, pnl<0): **{s['total_natural_losses']}**\n")
        f.write(f"- Forced losses: **{s['total_forced_losses']}**\n\n")

        # Burst premature escape lanes
        burst_lanes = [l for l in lanes if l["burst_premature_escapes"] > 0]
        if burst_lanes:
            f.write(f"\n## Lanes with burst premature escapes ({len(burst_lanes)} lanes)\n\n")
            f.write("| Lane | Burst Escapes | Escape $ | Natural $ | Enriched Closes | Total Closes |\n")
            f.write("|---|---:|---:|---:|---:|---:|\n")
            for lane in burst_lanes:
                f.write(f"| `{lane['lane']}` | {lane['burst_premature_escapes']} | ${lane['escape_pnl']:.2f} | ${lane['natural_pnl']:.2f} | {lane['enriched_closes']} | {lane['closes']} |\n")

        # All lanes summary
        f.write(f"\n## All lanes with close events ({len(lanes)} lanes)\n\n")
        f.write("| Lane | Opens | Closes | Escape | Forced | Natural | Enriched | Burst Premature | Escape $ | Natural $ |\n")
        f.write("|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|\n")
        for lane in lanes:
            f.write(f"| `{lane['lane']}` | {lane['opens']} | {lane['closes']} | {lane['escape_closes']} | {lane['forced_closes']} | {lane['natural_closes']} | {lane['enriched_closes']} | {lane['burst_premature_escapes']} | ${lane['escape_pnl']:.2f} | ${lane['natural_pnl']:.2f} |\n")

        # Burst escape details
        detail_lanes = [l for l in lanes if l["burst_premature_escapes"] > 0 and l["burst_escape_details"]]
        if detail_lanes:
            f.write(f"\n## Burst Premature Escape Details\n\n")
            for lane in detail_lanes:
                f.write(f"\n### `{lane['lane']}` ({lane['burst_premature_escapes']} burst escapes)\n\n")
                f.write("| Direction | PnL | Hold | Burst Count | First Green | Round Trip | Reclaimed | Retraced 0.25 | Retraced 0.5 | Regime |\n")
                f.write("|---|---:|---:|---:|---|---|---|---|---|---|\n")
                for d in lane["burst_escape_details"]:
                    f.write(f"| {d.get('direction','?')} | ${d.get('realized_pnl',0):.2f} | {d.get('hold_seconds','?')}s | {d.get('same_bar_open_burst_count_at_open','?')} | {d.get('first_green_before_fail')} | {d.get('same_bar_round_trip')} | {d.get('reclaimed_trigger_level_seen')} | {d.get('retraced_0_25x_step_seen')} | {d.get('retraced_0_5x_step_seen')} | {d.get('regime_at_entry','?')} |\n")

        f.write(f"\n## Interpretation\n\n")
        f.write(f"A `burst_premature_escape` means the lane opened a burst of 2+ positions on the same tick, ")
        f.write(f"never went green, held for 0 seconds, and was force-escaped at a loss.\n")
        f.write(f"If `reclaimed_trigger_level_seen=true` after escape, price came back to our entry level — ")
        f.write(f"proving the escape was premature.\n")
        f.write(f"If `retraced_0_5x_step_seen=true` after escape, price retraced halfway — ")
        f.write(f"the lattice would have self-healed if we'd held.\n")

    print(f"Board generated: {len(lanes)} lanes")
    print(f"  Burst premature escapes: {s['total_burst_premature_escapes']}")
    print(f"  Instant escapes: {s['total_instant_escapes']}")
    print(f"  Escape losses: {s['total_escape_losses']}")
    print(f"  Natural profits: {s['total_natural_profits']}")
    print(f"  Natural losses: {s['total_natural_losses']}")
    print(f"Written: {json_path}")
    print(f"Written: {md_path}")


if __name__ == "__main__":
    build_board()
