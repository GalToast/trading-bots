#!/usr/bin/env python3
"""
GBPUSD Closure Bucket Autopsy
Parse shadow_gbpusd_tick_forward_events.jsonl and produce a per-bucket,
per-close time-series analysis showing when closure tax became dominant
and what specific closure policy change would fix it.
"""

import json
import sys
from collections import defaultdict
from datetime import datetime, timezone as dt_timezone

EVENT_PATH = "reports/shadow_gbpusd_tick_forward_events.jsonl"
STATE_PATH = "reports/shadow_gbpusd_tick_forward_state.json"
OUTPUT_MD = "reports/gbpusd_closure_bucket_autopsy.md"

def load_events(path):
    events = []
    with open(path, "r") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                events.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return events

def load_state(path):
    with open(path, "r") as f:
        return json.load(f)

def main():
    events = load_events(EVENT_PATH)
    state = load_state(STATE_PATH)

    # Extract close events by bucket
    close_events = [e for e in events if e.get("action") in ("close_ticket", "escape_tier0_offensive", "forced_unwind", "reset")]
    
    print(f"Total events: {len(events)}")
    print(f"Close-related events: {len(close_events)}")
    
    bucket_totals = defaultdict(lambda: {"count": 0, "pnl": 0.0, "pnl_list": []})
    bucket_by_depth = defaultdict(lambda: defaultdict(lambda: {"count": 0, "pnl": 0.0}))
    
    # Also track close tickets separately for harvest
    harvest_pnl = 0.0
    harvest_count = 0
    escape_pnl = 0.0
    escape_count = 0
    forced_pnl = 0.0
    forced_count = 0
    reset_count = 0
    
    # Per-close rolling analysis
    all_closes = []  # list of (index, action, pnl, ts_utc, order_index)
    
    for e in close_events:
        action = e.get("action", "")
        pnl = e.get("realized_pnl", 0.0)
        ts = e.get("ts_utc", e.get("time", ""))
        order_index = e.get("order_index", e.get("level_index", e.get("open_index", -1)))
        
        all_closes.append({
            "action": action,
            "pnl": pnl,
            "ts_utc": ts,
            "order_index": order_index,
        })
        
        if action == "close_ticket":
            harvest_pnl += pnl
            harvest_count += 1
            bucket_totals["harvest"]["count"] += 1
            bucket_totals["harvest"]["pnl"] += pnl
            bucket_totals["harvest"]["pnl_list"].append(pnl)
        elif action == "escape_tier0_offensive":
            escape_pnl += pnl
            escape_count += 1
            bucket_totals["escape_tier0"]["count"] += 1
            bucket_totals["escape_tier0"]["pnl"] += pnl
            bucket_totals["escape_tier0"]["pnl_list"].append(pnl)
        elif action == "forced_unwind":
            forced_pnl += pnl
            forced_count += 1
            bucket_totals["forced_unwind"]["count"] += 1
            bucket_totals["forced_unwind"]["pnl"] += pnl
            bucket_totals["forced_unwind"]["pnl_list"].append(pnl)
        elif action == "reset":
            reset_count += 1
            bucket_totals["reset"]["count"] += 1
            bucket_totals["reset"]["pnl"] += pnl
            bucket_totals["reset"]["pnl_list"].append(pnl)
    
    # Rolling closure_tax_share over windows of 500 closes
    WINDOW = 500
    rolling = []
    for i in range(0, len(all_closes), WINDOW):
        window = all_closes[i:i+WINDOW]
        w_harvest = sum(c["pnl"] for c in window if c["action"] == "close_ticket")
        w_escape = sum(c["pnl"] for c in window if c["action"] == "escape_tier0_offensive")
        w_forced = sum(c["pnl"] for c in window if c["action"] == "forced_unwind")
        w_closure_tax = abs(w_escape) + abs(w_forced)
        w_closure_share = w_closure_tax / w_harvest if w_harvest > 0 else float('inf')
        w_net = w_harvest + w_escape + w_forced
        rolling.append({
            "start_idx": i,
            "end_idx": i + len(window) - 1,
            "close_count": len(window),
            "harvest": round(w_harvest, 2),
            "escape": round(w_escape, 2),
            "forced": round(w_forced, 2),
            "closure_tax": round(w_closure_tax, 2),
            "closure_share": round(w_closure_share, 4) if w_closure_share != float('inf') else "inf",
            "net": round(w_net, 2),
        })
    
    # Escape tier0 analysis: what order_index (depth) does it fire at?
    escape_depths = [c["order_index"] for c in all_closes if c["action"] == "escape_tier0_offensive" and isinstance(c["order_index"], (int, float))]
    forced_depths = [c["order_index"] for c in all_closes if c["action"] == "forced_unwind" and isinstance(c["order_index"], (int, float))]
    
    # Time analysis: when do closures dominate?
    # Group by hour
    by_hour = defaultdict(lambda: {"harvest": 0.0, "escape": 0.0, "forced": 0.0, "count": 0})
    for c in all_closes:
        ts = c.get("ts_utc", "")
        try:
            if isinstance(ts, str):
                dt = datetime.fromisoformat(ts)
            else:
                dt = datetime.fromtimestamp(ts, tz=dt_timezone.utc)
            hour = dt.hour
            by_hour[hour]["count"] += 1
            if c["action"] == "close_ticket":
                by_hour[hour]["harvest"] += c["pnl"]
            elif c["action"] == "escape_tier0_offensive":
                by_hour[hour]["escape"] += c["pnl"]
            elif c["action"] == "forced_unwind":
                by_hour[hour]["forced"] += c["pnl"]
        except:
            pass
    
    # State file read
    runner_started = state.get("runner", {}).get("started_at", "unknown")
    total_closes_state = state.get("realized_closes", len(all_closes))
    total_net_state = state.get("realized_net_usd", "unknown")
    
    # Build the report
    lines = []
    lines.append("# GBPUSD Closure Bucket Autopsy\n")
    lines.append(f"- Generated at: `{datetime.now(dt_timezone.utc).strftime('%Y-%m-%dT%H:%M:%S UTC')}`")
    lines.append(f"- Purpose: identify exactly which closure policy is bleeding, when it started, and what fix would recover profit.")
    lines.append(f"- Source: `shadow_gbpusd_tick_forward_events.jsonl` ({len(events)} events, {len(close_events)} close-related)")
    lines.append(f"- State: `runner.started_at={runner_started}`, `realized_closes={total_closes_state}`, `realized_net_usd={total_net_state}`\n")
    
    lines.append("## Whole-File Bucket Summary\n")
    lines.append("| Bucket | Count | Total PnL | Avg PnL | % of Closes |")
    lines.append("|--------|-------|-----------|---------|--------------|")
    total_pnl = harvest_pnl + escape_pnl + forced_pnl
    total_count = harvest_count + escape_count + forced_count
    for bucket_name in ["harvest", "escape_tier0", "forced_unwind", "reset"]:
        b = bucket_totals[bucket_name]
        avg = b["pnl"] / b["count"] if b["count"] > 0 else 0
        pct = b["count"] / total_count * 100 if total_count > 0 else 0
        lines.append(f"| {bucket_name} | {b['count']} | ${b['pnl']:.2f} | ${avg:.4f} | {pct:.1f}% |")
    
    lines.append(f"\n**Closure tax share:** {abs(escape_pnl) + abs(forced_pnl):.2f} / {harvest_pnl:.2f} = **{(abs(escape_pnl) + abs(forced_pnl)) / harvest_pnl:.2f}**" if harvest_pnl > 0 else "")
    lines.append(f"**Net:** ${total_pnl:.2f} over {total_count} closes\n")
    
    lines.append("## Rolling Closure Tax Share (per 500 closes)\n")
    lines.append("| Window | Closes | Harvest | Escape | Forced | Closure Tax | Closure Share | Net |")
    lines.append("|--------|--------|---------|--------|--------|-------------|---------------|-----|")
    for r in rolling:
        lines.append(f"| {r['start_idx']}-{r['end_idx']} | {r['close_count']} | ${r['harvest']:.2f} | ${r['escape']:.2f} | ${r['forced']:.2f} | ${r['closure_tax']:.2f} | {r['closure_share']} | ${r['net']:.2f} |")
    
    lines.append("\n## Escape Depth Analysis\n")
    if escape_depths:
        lines.append(f"**Escape tier0 fires at order indices:** min={min(escape_depths)}, max={max(escape_depths)}, mean={sum(escape_depths)/len(escape_depths):.1f}, median={sorted(escape_depths)[len(escape_depths)//2]}")
        # Histogram
        depth_buckets = defaultdict(int)
        for d in escape_depths:
            depth_buckets[d] += 1
        lines.append("\n| Order Index | Count |")
        lines.append("|-------------|-------|")
        for depth in sorted(depth_buckets.keys())[:20]:
            lines.append(f"| {depth} | {depth_buckets[depth]} |")
    else:
        lines.append("No order_index data available for escape events.\n")
    
    if forced_depths:
        lines.append(f"\n**Forced unwind fires at order indices:** min={min(forced_depths)}, max={max(forced_depths)}, mean={sum(forced_depths)/len(forced_depths):.1f}")
    else:
        lines.append("\nNo order_index data available for forced unwind events.\n")
    
    lines.append("## Hourly Bucket Analysis\n")
    lines.append("| Hour (UTC) | Closes | Harvest | Escape | Forced | Closure Share | Net |")
    lines.append("|------------|--------|---------|--------|--------|---------------|-----|")
    for hour in sorted(by_hour.keys()):
        h = by_hour[hour]
        closure_tax = abs(h["escape"]) + abs(h["forced"])
        closure_share = closure_tax / h["harvest"] if h["harvest"] > 0 else float('inf')
        net = h["harvest"] + h["escape"] + h["forced"]
        cs_str = f"{closure_share:.2f}" if closure_share != float('inf') else "no harvest"
        lines.append(f"| {hour:02d}:00 | {h['count']} | ${h['harvest']:.2f} | ${h['escape']:.2f} | ${h['forced']:.2f} | {cs_str} | ${net:.2f} |")
    
    lines.append("\n## Key Findings\n")
    
    # Determine the dominant bleeder
    if abs(escape_pnl) > abs(forced_pnl):
        dominant = "escape_tier0_offensive"
        dominant_pnl = escape_pnl
        dominant_count = escape_count
    else:
        dominant = "forced_unwind"
        dominant_pnl = forced_pnl
        dominant_count = forced_count
    
    lines.append(f"1. **Dominant bleeder:** `{dominant}` at ${dominant_pnl:.2f} over {dominant_count} exits ({dominant_count}/{total_count} = {dominant_count/total_count*100:.1f}% of all closes)")
    lines.append(f"2. **Harvest per close:** ${harvest_pnl/harvest_count:.4f} vs {dominant} per close: ${dominant_pnl/dominant_count:.4f}")
    
    # Rolling trend
    if len(rolling) >= 2:
        first_share = rolling[0]["closure_share"]
        last_share = rolling[-1]["closure_share"]
        if isinstance(first_share, (int, float)) and isinstance(last_share, (int, float)):
            if last_share > first_share:
                lines.append(f"3. **Closure tax is WORSENING:** {first_share:.2f} -> {last_share:.2f}")
            elif last_share < first_share:
                lines.append(f"3. **Closure tax is IMPROVING:** {first_share:.2f} -> {last_share:.2f}")
            else:
                lines.append(f"3. **Closure tax is stable:** {first_share:.2f}")
    
    lines.append(f"\n## Recommended Fix\n")
    
    if dominant == "escape_tier0_offensive":
        if escape_depths:
            mean_depth = sum(escape_depths) / len(escape_depths)
            lines.append(f"1. **Escape tier0 fires at mean depth {mean_depth:.1f}.** Consider raising `escape_max_loss` or `escape_max_bars` to reduce premature cuts.")
            lines.append(f"2. **Disable escape_tier0 for shallow positions (depth < {int(mean_depth)}).** These are likely being cut before they can mean-revert.")
        lines.append(f"3. **Current escape_tier0 cost:** ${escape_pnl:.2f}. If eliminated, net would be ${total_pnl - escape_pnl:.2f} (+${abs(escape_pnl):.2f} recovery).")
    elif dominant == "forced_unwind":
        lines.append(f"1. **Forced unwinds cost ${forced_pnl:.2f} over {forced_count} exits.** This suggests positions are aging out without reaching closure geometry.")
        lines.append(f"2. **Widen the forced_unwind threshold or tighten the entry geometry** so positions close naturally before forced unwind triggers.")
        lines.append(f"3. **If forced_unwind eliminated, net would be ${total_pnl - forced_pnl:.2f} (+${abs(forced_pnl):.2f} recovery).")
    
    lines.append(f"\n## Sources\n")
    lines.append(f"- `{EVENT_PATH}`")
    lines.append(f"- `{STATE_PATH}`")
    lines.append(f"- `reports/closure_firewall_board.md`")
    lines.append(f"- `reports/fresh_window_bucket_board.md`")
    
    report = "\n".join(lines)
    
    with open(OUTPUT_MD, "w", encoding="utf-8") as f:
        f.write(report)
    
    print(f"\nAutopsy written to {OUTPUT_MD}")
    print(f"\n=== SUMMARY ===")
    print(f"Harvest: ${harvest_pnl:.2f} / {harvest_count} closes (${harvest_pnl/harvest_count:.4f}/close)")
    print(f"Escape tier0: ${escape_pnl:.2f} / {escape_count} closes")
    print(f"Forced unwind: ${forced_pnl:.2f} / {forced_count} closes")
    print(f"Closure tax share: {(abs(escape_pnl) + abs(forced_pnl)) / harvest_pnl:.2f}" if harvest_pnl > 0 else "No harvest to compare")
    print(f"Dominant bleeder: {dominant} (${dominant_pnl:.2f})")
    print(f"Net: ${total_pnl:.2f} over {total_count} closes")

if __name__ == "__main__":
    main()
