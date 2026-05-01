"""Audit Phase 1 telemetry enrichment across all event log files."""
import json
import glob
import os

REPORTS_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "reports")

PHASE1_FIELDS = [
    "time_to_first_green_seconds",
    "max_favorable_excursion_pnl",
    "max_adverse_excursion_pnl",
    "peak_pnl_before_exit",
    "first_green_before_fail",
    "spread_at_entry",
    "entry_context",
    "regime_at_entry",
    "session_bucket",
    "token_age_at_fire",
    "armed_duration_seconds",
    "hold_seconds",
    "rearm_to_first_green_seconds",
    "rearm_to_fail_seconds",
]

files = sorted(glob.glob(os.path.join(REPORTS_DIR, "*events.jsonl")), key=os.path.getmtime, reverse=True)

results = []
for f in files:
    bn = os.path.basename(f)
    try:
        lines = open(f, "r", encoding="utf-8").readlines()
        total = len(lines)
        has_close = False
        has_open = False
        enriched = 0
        present_fields = set()
        for line in lines:
            try:
                ev = json.loads(line.strip())
            except Exception:
                continue
            a = ev.get("action", "")
            if "open" in a:
                has_open = True
            if "close" in a:
                has_close = True
            for pf in PHASE1_FIELDS:
                if pf in ev:
                    present_fields.add(pf)
        # Count enriched events (any event with at least 1 Phase 1 field)
        enriched_events = 0
        for line in lines:
            try:
                ev = json.loads(line.strip())
            except Exception:
                continue
            if any(pf in ev for pf in PHASE1_FIELDS):
                enriched_events += 1
        results.append((bn, total, has_open, has_close, enriched_events, len(present_fields), sorted(present_fields)))
    except Exception as e:
        results.append((bn, 0, False, False, 0, 0, [f"ERROR: {e}"]))

# Print header
print(f"{'File':60s} {'T':>6} {'Open':>4} {'Close':>5} {'EnrEv':>6} {'Flds':>4} Enriched Fields")
print("=" * 160)
for bn, total, has_open, has_close, enriched, n_fields, field_list in results:
    top5 = ", ".join(field_list[:5]) if field_list else "NONE"
    print(f"{bn:60s} {total:>6} {'Y' if has_open else 'N':>4} {'Y' if has_close else 'N':>5} {enriched:>6} {n_fields:>4} {top5}")

# Summary
has_enrichment = sum(1 for r in results if r[4] > 0)
has_closes = sum(1 for r in results if r[3])
print(f"\nTotal event files: {len(results)}")
print(f"With Phase 1 enriched events: {has_enrichment}")
print(f"With close events: {has_closes}")
print(f"Files with enrichment but NO closes: {sum(1 for r in results if r[4] > 0 and not r[3])}")
print(f"Files with closes but NO enrichment: {sum(1 for r in results if r[3] and r[4] == 0)}")
