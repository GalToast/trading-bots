"""Build telemetry enforcement priority board — rank unenriched lanes by importance."""
import json
import os
import sys
from datetime import datetime, timezone

REPORTS_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "reports")

# Phase 1 telemetry fields we expect
PHASE1_FIELDS = {
    "time_to_first_green_seconds", "max_favorable_excursion_pnl",
    "max_adverse_excursion_pnl", "peak_pnl_before_exit",
    "first_green_before_fail", "hold_seconds",
    "spread_at_entry", "entry_context", "regime_at_entry",
    "session_bucket", "token_age_at_fire", "armed_duration_seconds",
    "rearm_to_first_green_seconds", "rearm_to_fail_seconds",
}


def load_json(path):
    if not os.path.exists(path):
        return None
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def audit_event_file(filepath):
    """Audit event log file for Phase 1 enrichment — sample only for speed."""
    result = {"total_events": 0, "has_close": False, "enriched_event_count": 0, "present_fields": []}
    try:
        # First pass: count total lines and check last 200 for enrichment (most recent)
        with open(filepath, "r", encoding="utf-8") as f:
            # Count total lines efficiently
            total = sum(1 for _ in f)
        result["total_events"] = total

        # Sample last 500 lines for enrichment analysis
        sample_size = min(500, total)
        with open(filepath, "r", encoding="utf-8") as f:
            lines = f.readlines()
        sample = lines[-sample_size:] if len(lines) > sample_size else lines

        present_fields = set()
        enriched = 0
        for line in sample:
            try:
                ev = json.loads(line.strip())
            except Exception:
                continue
            if "close" in ev.get("action", ""):
                result["has_close"] = True
            has_enrich = False
            for pf in PHASE1_FIELDS:
                if pf in ev:
                    present_fields.add(pf)
                    has_enrich = True
            if has_enrich:
                enriched += 1
        result["present_fields"] = sorted(present_fields)
        # Extrapolate enriched count
        if sample_size > 0 and total > sample_size:
            result["enriched_event_count"] = int(enriched * (total / sample_size))
        else:
            result["enriched_event_count"] = enriched
    except Exception as e:
        result["error"] = str(e)
    return result


def lane_name_from_event_file(bn):
    """Convert event filename to a readable lane name."""
    name = bn.replace("_events.jsonl", "").replace("penetration_lattice_", "")
    return name


def kind_priority(kind):
    """Priority order for lane kinds — live lanes first, then active shadows."""
    order = {
        "live_crypto": 0,
        "live_fx": 1,
        "live_commodity": 2,
        "live_index": 3,
        "shadow_crypto": 4,
        "shadow_crypto_candidate": 5,
        "shadow_fx": 6,
        "shadow_fx_m15_bar": 7,
        "shadow_commodity": 8,
        "shadow_coinbase_spot": 9,
        "shadow_coinbase_futures": 10,
        "shadow_unified": 11,
        "infrastructure": 12,
    }
    return order.get(kind, 99)


def watchdog_priority(status):
    """OK lanes matter more than quarantined/stale."""
    order = {"ok": 0, "paused": 1, "stale": 2, "stale_recurrence": 3, "quarantined": 4, "missing": 5}
    return order.get(status, 99)


def build_board():
    em = load_json(os.path.join(REPORTS_DIR, "execution_monitor_report.json"))
    if not em:
        print("ERROR: Cannot load execution monitor report")
        sys.exit(1)
    em_rows = {r.get("lane"): r for r in em.get("rows", em.get("lanes", []))}

    # Scan all event files
    event_files = {}
    import glob as glob_mod
    for f in glob_mod.glob(os.path.join(REPORTS_DIR, "*events.jsonl")):
        bn = os.path.basename(f)
        event_files[bn] = f

    lanes = []
    for bn, filepath in event_files.items():
        audit = audit_event_file(filepath)
        if audit.get("error"):
            continue

        lane_name = lane_name_from_event_file(bn)
        em_row = em_rows.get(lane_name, {})

        # Only care about lanes that have closes but no enrichment
        has_enrichment = audit["enriched_event_count"] > 0
        has_closes = audit["has_close"]

        if has_enrichment:
            continue  # Skip already enriched lanes
        if not has_closes:
            continue  # Skip no-trade lanes

        kind = em_row.get("kind", "")
        watchdog = em_row.get("watchdog_status", "")

        # P/L from execution monitor
        inherited_closes = int(em_row.get("broker_sync_inherited_closes", 0) or 0) + int(em_row.get("pre_start_state_carry_closes", 0) or 0)
        active_closes = int(em_row.get("clean_forward_new_closes", 0) or 0)
        if active_closes == 0:
            active_closes = int(em_row.get("runner_session_trade_closes", 0) or 0)
        total_closes = int(em_row.get("close_count", 0) or 0)

        # Scoring
        kind_prio = kind_priority(kind)
        wd_prio = watchdog_priority(watchdog)

        # Higher score = higher priority for enforcement
        # Live lanes: score 100, active shadow: 50-80, stale: 20-40, quarantined: 10
        if kind_prio <= 3:  # live lanes
            base_score = 100
        elif kind_prio <= 5:  # active crypto shadows
            base_score = 80
        elif kind_prio <= 7:  # FX shadows
            base_score = 60
        elif kind_prio <= 10:  # coinbase/other shadows
            base_score = 40
        else:
            base_score = 20

        # Adjust by watchdog status
        if wd_prio == 0:  # ok
            score = base_score + 20
        elif wd_prio == 1:  # paused
            score = base_score + 10
        else:
            score = base_score

        # Boost by close volume (more closes = more telemetry data = higher priority)
        if total_closes > 100:
            score += 10
        elif total_closes > 20:
            score += 5

        lanes.append({
            "event_file": bn,
            "lane_name": lane_name,
            "kind": kind,
            "watchdog_status": watchdog,
            "total_events": audit["total_events"],
            "total_closes": total_closes,
            "active_closes": active_closes,
            "inherited_closes": inherited_closes,
            "enrichment_score": score,
            "enrichment_verdict": "needs_enrichment",
        })

    # Sort by enrichment score descending
    lanes.sort(key=lambda l: (-l["enrichment_score"], -l["total_closes"]))

    board = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "summary": {
            "total_needing_enrichment": len(lanes),
            "top_priority_count": sum(1 for l in lanes if l["enrichment_score"] >= 100),
            "high_priority_count": sum(1 for l in lanes if 80 <= l["enrichment_score"] < 100),
            "medium_priority_count": sum(1 for l in lanes if 40 <= l["enrichment_score"] < 80),
            "low_priority_count": sum(1 for l in lanes if l["enrichment_score"] < 40),
        },
        "lanes": lanes,
    }

    # Write JSON
    json_path = os.path.join(REPORTS_DIR, "telemetry_enforcement_priority_board.json")
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(board, f, indent=2, default=str)

    # Write Markdown
    md_path = os.path.join(REPORTS_DIR, "telemetry_enforcement_priority_board.md")
    with open(md_path, "w", encoding="utf-8") as f:
        f.write("# Telemetry Enforcement Priority Board\n\n")
        f.write(f"> Generated: `{board['generated_at']}`\n\n")
        f.write(f"## Summary\n\n")
        s = board["summary"]
        f.write(f"- Total lanes needing enrichment: **{s['total_needing_enrichment']}**\n")
        f.write(f"- Top priority (live lanes): **{s['top_priority_count']}**\n")
        f.write(f"- High priority (active crypto shadows): **{s['high_priority_count']}**\n")
        f.write(f"- Medium priority (FX/other shadows): **{s['medium_priority_count']}**\n")
        f.write(f"- Low priority (stale/quarantined): **{s['low_priority_count']}**\n\n")

        # Group by priority tier
        tiers = [
            ("TOP PRIORITY — Live lanes (score >= 100)", [l for l in lanes if l["enrichment_score"] >= 100]),
            ("HIGH PRIORITY — Active crypto shadows (score 80-99)", [l for l in lanes if 80 <= l["enrichment_score"] < 100]),
            ("MEDIUM PRIORITY — FX and commodity shadows (score 40-79)", [l for l in lanes if 40 <= l["enrichment_score"] < 80]),
            ("LOW PRIORITY — Stale/quarantined (score < 40)", [l for l in lanes if l["enrichment_score"] < 40]),
        ]

        for label, group in tiers:
            if not group:
                continue
            f.write(f"\n## {label} ({len(group)} lanes)\n\n")
            f.write("| Lane | Closes | Active Closes | Kind | Watchdog | Score |\n")
            f.write("|---|---:|---:|---|---|---:|\n")
            for lane in group:
                f.write(f"| `{lane['lane_name']}` | {lane['total_closes']} | {lane['active_closes']} | {lane['kind']} | {lane['watchdog_status']} | {lane['enrichment_score']} |\n")

        f.write(f"\n## Next Action\n\n")
        f.write(f"Start telemetry enforcement with the TOP PRIORITY tier — these are live lanes producing closes with zero path-shape telemetry.\n")
        f.write(f"The enforcement means adding `spread_at_entry`, `entry_context`, `regime_at_entry`, `session_bucket`, `time_to_first_green_seconds`, `max_favorable_excursion_pnl`, `max_adverse_excursion_pnl`, `peak_pnl_before_exit`, `first_green_before_fail`, and `hold_seconds` to open_ticket and close events.\n")

    print(f"Board generated: {len(lanes)} lanes needing enrichment")
    print(f"  Top priority (live): {s['top_priority_count']}")
    print(f"  High priority (active shadows): {s['high_priority_count']}")
    print(f"  Medium priority (other shadows): {s['medium_priority_count']}")
    print(f"  Low priority (stale/quarantined): {s['low_priority_count']}")
    print(f"Written: {json_path}")
    print(f"Written: {md_path}")


if __name__ == "__main__":
    build_board()
