"""Build Phase 1 telemetry visibility board across all event log files."""
import json
import glob
import os
import sys
from datetime import datetime, timezone

REPORTS_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "reports")

# Phase 1 telemetry fields we expect to see in enriched events
PHASE1_FIELDS = {
    "time_to_first_green_seconds": "per_ticket_lifecycle",
    "max_favorable_excursion_pnl": "per_ticket_lifecycle",
    "max_adverse_excursion_pnl": "per_ticket_lifecycle",
    "peak_pnl_before_exit": "per_ticket_lifecycle",
    "first_green_before_fail": "per_ticket_lifecycle",
    "hold_seconds": "per_ticket_lifecycle",
    "spread_at_entry": "entry_context",
    "entry_context": "entry_context",
    "regime_at_entry": "entry_context",
    "session_bucket": "entry_context",
    "token_age_at_fire": "rearm_specific",
    "armed_duration_seconds": "rearm_specific",
    "rearm_to_first_green_seconds": "rearm_specific",
    "rearm_to_fail_seconds": "rearm_specific",
}

# Map from execution monitor lane names to event file names
def lane_to_event_files():
    """Get mapping from lane name to its event file(s)."""
    mapping = {}
    for f in glob.glob(os.path.join(REPORTS_DIR, "*events.jsonl")):
        bn = os.path.basename(f)
        mapping[bn] = f
    return mapping


def audit_event_file(filepath):
    """Audit a single event log file for Phase 1 enrichment."""
    result = {
        "total_events": 0,
        "has_open_ticket": False,
        "has_close": False,
        "enriched_event_count": 0,
        "present_fields": [],
        "field_categories": {},
        "last_event_ts": None,
        "sample_enriched_event": None,
    }
    try:
        lines = open(filepath, "r", encoding="utf-8").readlines()
        result["total_events"] = len(lines)
        enriched_count = 0
        present_fields = set()
        for line in lines:
            try:
                ev = json.loads(line.strip())
            except Exception:
                continue
            a = ev.get("action", "")
            if "open" in a:
                result["has_open_ticket"] = True
            if "close" in a:
                result["has_close"] = True
            ts = ev.get("ts_utc")
            if ts:
                result["last_event_ts"] = ts
            has_enrich = False
            for pf in PHASE1_FIELDS:
                if pf in ev:
                    present_fields.add(pf)
                    has_enrich = True
                    cat = PHASE1_FIELDS[pf]
                    if cat not in result["field_categories"]:
                        result["field_categories"][cat] = 0
                    result["field_categories"][cat] += 1
            if has_enrich:
                enriched_count += 1
                if result["sample_enriched_event"] is None:
                    result["sample_enriched_event"] = {k: v for k, v in ev.items() if k in PHASE1_FIELDS}
        result["enriched_event_count"] = enriched_count
        result["present_fields"] = sorted(present_fields)
    except Exception as e:
        result["error"] = str(e)
    return result


def build_board():
    event_files = lane_to_event_files()
    lanes = []
    enriched_count = 0
    no_enrichment_count = 0
    no_closes_count = 0
    fully_enriched = 0

    for bn, filepath in sorted(event_files.items(), key=lambda x: os.path.getmtime(x[1]), reverse=True):
        audit = audit_event_file(filepath)
        if audit.get("error"):
            continue

        # Categorize
        has_enrichment = audit["enriched_event_count"] > 0
        has_closes = audit["has_close"]

        if has_enrichment:
            enriched_count += 1
            if has_closes:
                fully_enriched += 1
        else:
            no_enrichment_count += 1
        if not has_closes:
            no_closes_count += 1

        # Verdict
        if not has_enrichment and has_closes:
            verdict = "no_enrichment"
        elif has_enrichment and not has_closes:
            verdict = "enriched_no_closes"
        elif has_enrichment and has_closes:
            # Check completeness
            n_fields = len(audit["present_fields"])
            if n_fields >= 10:
                verdict = "fully_enriched"
            else:
                verdict = "partially_enriched"
        else:
            verdict = "no_trades"

        lanes.append({
            "event_file": bn,
            "verdict": verdict,
            "total_events": audit["total_events"],
            "has_open_ticket": audit["has_open_ticket"],
            "has_close": audit["has_close"],
            "enriched_event_count": audit["enriched_event_count"],
            "present_field_count": len(audit["present_fields"]),
            "present_fields": audit["present_fields"],
            "field_categories": audit["field_categories"],
            "last_event_ts": audit["last_event_ts"],
            "sample_enriched_event": audit.get("sample_enriched_event"),
        })

    board = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "summary": {
            "total_event_files": len(lanes),
            "fully_enriched": fully_enriched,
            "partially_enriched": sum(1 for l in lanes if l["verdict"] == "partially_enriched"),
            "enriched_no_closes": sum(1 for l in lanes if l["verdict"] == "enriched_no_closes"),
            "no_enrichment_with_closes": sum(1 for l in lanes if l["verdict"] == "no_enrichment"),
            "no_trades": sum(1 for l in lanes if l["verdict"] == "no_trades"),
        },
        "lanes": lanes,
    }

    # Write JSON
    json_path = os.path.join(REPORTS_DIR, "phase1_telemetry_visibility_board.json")
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(board, f, indent=2, default=str)

    # Write Markdown
    md_path = os.path.join(REPORTS_DIR, "phase1_telemetry_visibility_board.md")
    with open(md_path, "w", encoding="utf-8") as f:
        f.write("# Phase 1 Telemetry Visibility Board\n\n")
        f.write(f"> Generated: `{board['generated_at']}`\n\n")
        s = board["summary"]
        f.write(f"## Summary\n\n")
        f.write(f"- Total event files: **{s['total_event_files']}**\n")
        f.write(f"- Fully enriched (10+ fields, has closes): **{s['fully_enriched']}**\n")
        f.write(f"- Partially enriched (has closes): **{s['partially_enriched']}**\n")
        f.write(f"- Enriched but no closes: **{s['enriched_no_closes']}**\n")
        f.write(f"- Has closes but NO enrichment: **{s['no_enrichment_with_closes']}**\n")
        f.write(f"- No trades recorded: **{s['no_trades']}**\n\n")

        # Group by verdict
        for verdict, label in [
            ("fully_enriched", "FULLY ENRICHED (10+ fields, has closes)"),
            ("partially_enriched", "PARTIALLY ENRICHED (has closes)"),
            ("enriched_no_closes", "ENRICHED but no closes yet"),
            ("no_enrichment", "NO ENRICHMENT (has closes, 0 Phase 1 fields)"),
            ("no_trades", "NO TRADES"),
        ]:
            group = [l for l in lanes if l["verdict"] == verdict]
            if not group:
                continue
            f.write(f"\n## {label} ({len(group)} files)\n\n")
            f.write("| Event File | Events | Enriched | Fields | Last Event |\n")
            f.write("|---|---:|---:|---:|---|\n")
            for lane in group:
                fields_str = ", ".join(lane["present_fields"][:5])
                if len(lane["present_fields"]) > 5:
                    fields_str += f" (+{len(lane['present_fields'])-5})"
                f.write(f"| `{lane['event_file']}` | {lane['total_events']} | {lane['enriched_event_count']} | {lane['present_field_count']} | {lane['last_event_ts'] or 'N/A'} |\n")

        f.write(f"\n## Interpretation\n\n")
        f.write(f"A `fully_enriched` lane has 10+ Phase 1 fields and has produced closes with enriched events.\n")
        f.write(f"A `partially_enriched` lane has enrichment but fewer than 10 fields.\n")
        f.write(f"An `enriched_no_closes` lane has enriched events but no close events yet (needs time).\n")
        f.write(f"A `no_enrichment` lane has close events but zero Phase 1 telemetry fields — these lanes are blind to trade path quality.\n")
        f.write(f"A `no_trades` lane has not fired any trades yet.\n")

    print(f"Board generated: {len(lanes)} lanes audited")
    print(f"  Fully enriched: {s['fully_enriched']}")
    print(f"  Partially enriched: {s['partially_enriched']}")
    print(f"  Enriched, no closes: {s['enriched_no_closes']}")
    print(f"  No enrichment, has closes: {s['no_enrichment_with_closes']}")
    print(f"  No trades: {s['no_trades']}")
    print(f"Written: {json_path}")
    print(f"Written: {md_path}")


if __name__ == "__main__":
    build_board()
