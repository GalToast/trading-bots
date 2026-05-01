#!/usr/bin/env python3
"""
Unified Organism Status Dashboard
====================================
One script to check ALL runners, shadow lanes, and live lanes.
Produces a comprehensive status report in markdown and JSON.

Checks:
1. Kelly Shadow (kelly_shadow_state.json)
2. Rotation Lattice Shadow (rotation_shadow_state.json)
3. Live lanes from event logs
4. Runner heartbeat status (alive/dead/stale)
5. Recent events across all systems

Usage:
    python scripts/organism_status_dashboard.py
    python scripts/organism_status_dashboard.py --json  # JSON only
"""
import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# State files to check
STATE_FILES = {
    "kelly_shadow": ROOT / "reports" / "kelly_shadow_state.json",
    "rotation_shadow": ROOT / "reports" / "rotation_shadow_state.json",
}

# Event files to check
EVENT_FILES = {
    "kelly_shadow": ROOT / "reports" / "kelly_shadow_events.jsonl",
    "rotation_shadow": ROOT / "reports" / "rotation_shadow_events.jsonl",
}

# Heartbeat files
HEARTBEAT_FILES = {
    "kelly_shadow": ROOT / "reports" / "kelly_shadow_heartbeat.json",
    "rotation_shadow": ROOT / "reports" / "rotation_shadow_heartbeat.json",
}

STALE_THRESHOLD_SECONDS = 120  # 2 minutes


def check_heartbeat(name, hb_path):
    """Check if a runner is alive based on heartbeat."""
    if not hb_path.exists():
        return {"status": "NO_HEARTBEAT", "age_s": None}
    
    try:
        hb = json.loads(hb_path.read_text(encoding="utf-8"))
        updated = hb.get("updated_at", "")
        if updated:
            dt = datetime.fromisoformat(updated)
            age_s = (datetime.now(timezone.utc) - dt).total_seconds()
        else:
            age_s = None
        
        if age_s is not None:
            if age_s > STALE_THRESHOLD_SECONDS:
                return {"status": "STALE", "age_s": round(age_s), "cycle": hb.get("cycle", "?")}
            else:
                return {"status": "ALIVE", "age_s": round(age_s), "cycle": hb.get("cycle", "?")}
        return {"status": "UNKNOWN", "age_s": None}
    except Exception as e:
        return {"status": f"ERROR: {e}", "age_s": None}


def check_state(name, state_path):
    """Check a state file."""
    if not state_path.exists():
        return {"status": "NOT_FOUND"}
    
    try:
        data = json.loads(state_path.read_text(encoding="utf-8"))
        age_s = time.time() - state_path.stat().st_mtime
        data["_age_s"] = round(age_s)
        return data
    except Exception as e:
        return {"status": f"ERROR: {e}"}


def check_events(name, event_path, tail=5):
    """Get recent events."""
    if not event_path.exists():
        return []
    
    events = []
    try:
        lines = event_path.read_text(encoding="utf-8").splitlines()
        for line in lines[-tail:]:
            line = line.strip()
            if line:
                try:
                    events.append(json.loads(line))
                except Exception:
                    pass
    except Exception:
        pass
    
    return events


def count_real_trades(events, coin_or_pair=None):
    """Count unique trades from event log (deduplicated by timestamp)."""
    opens = set()
    closes = set()
    
    for evt in events:
        action = evt.get("action", "")
        ts = evt.get("ts_utc", "")
        coin = evt.get("coin", "")
        
        if coin_or_pair and coin != coin_or_pair:
            continue
        
        if action == "open":
            # Deduplicate: same coin + same entry_price + same timestamp (within 1s)
            key = (coin, evt.get("entry_price"), ts[:19])
            opens.add(key)
        elif action == "close":
            key = (coin, evt.get("exit_price"), ts[:19])
            closes.add(key)
    
    return len(opens), len(closes)


def generate_markdown(status_data):
    """Generate a markdown status report."""
    lines = []
    lines.append("# 🧬 Organism Status Dashboard")
    lines.append(f"\nGenerated: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}")
    lines.append(f"\n## Runners")
    lines.append("")
    lines.append("| Runner | Status | Age (s) | Cycle | Notes |")
    lines.append("|--------|--------|---------|-------|-------|")
    
    for name, hb in status_data.get("heartbeats", {}).items():
        status = hb.get("status", "?")
        age = hb.get("age_s", "?")
        cycle = hb.get("cycle", "?")
        icon = "🟢" if status == "ALIVE" else ("🟡" if status == "STALE" else "🔴")
        lines.append(f"| {icon} {name} | {status} | {age} | {cycle} | |")
    
    lines.append("")
    lines.append("## Kelly Shadow")
    lines.append("")
    
    ks = status_data.get("kelly_shadow", {})
    if isinstance(ks, dict) and "ledgers" in ks:
        lines.append(f"**Cycle:** {ks.get('cycle', '?')} | **Equity:** ${ks.get('total_equity', 0):.2f} | **PnL:** ${ks.get('total_pnl', 0):.2f} | **State age:** {ks.get('_age_s', '?')}s")
        lines.append("")
        lines.append("| Coin | Signals | Closes | Position | PnL |")
        lines.append("|------|---------|--------|----------|-----|")
        for coin, ledger in ks.get("ledgers", {}).items():
            pos_icon = "🔴" if ledger.get("position") == "active" else "⚪"
            lines.append(f"| {coin} | {ledger.get('signals', 0)} | {ledger.get('closes', 0)} | {pos_icon} {ledger.get('position', '?')} | ${ledger.get('pnl', 0):.2f} |")
    
    lines.append("")
    lines.append("## Rotation Lattice Shadow")
    lines.append("")
    
    rs = status_data.get("rotation_shadow", {})
    if isinstance(rs, dict) and "pairs" in rs:
        lines.append(f"**Cycle:** {rs.get('cycle', '?')} | **PnL:** ${rs.get('total_pnl', 0):.2f} | **State age:** {rs.get('_age_s', '?')}s")
        lines.append("")
        lines.append("| Pair | Signals | Closes | Position | PnL |")
        lines.append("|------|---------|--------|----------|-----|")
        for pair, ps in rs.get("pairs", {}).items():
            pos_icon = "🔴" if ps.get("position") else "⚪"
            lines.append(f"| {pair} | {ps.get('signals', 0)} | {ps.get('closes', 0)} | {pos_icon} {ps.get('position', {}).get('entry_rs', 'flat') if ps.get('position') else 'flat'} | ${ps.get('total_pnl', 0):.2f} |")
    
    lines.append("")
    lines.append("## Recent Events")
    lines.append("")
    
    for name, events in status_data.get("recent_events", {}).items():
        if events:
            lines.append(f"### {name}")
            for evt in events[-3:]:
                action = evt.get("action", "?")
                coin = evt.get("coin", evt.get("pair", "?"))
                pnl = evt.get("net", evt.get("pnl", ""))
                reason = evt.get("reason", evt.get("exit_reason", ""))
                ts = evt.get("ts_utc", "?")[:19]
                
                if action == "open":
                    lines.append(f"  - 🟢 OPEN {coin} @ {evt.get('entry_price', '?')} ({ts})")
                elif action == "close":
                    lines.append(f"  - 🔴 CLOSE {coin}: ${pnl} ({reason}) ({ts})")
                elif action == "runner_start":
                    lines.append(f"  - 🔄 Runner started ({ts})")
    
    return "\n".join(lines)


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Organism Status Dashboard")
    parser.add_argument("--json", action="store_true", help="Output JSON only")
    args = parser.parse_args()
    
    status_data = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "heartbeats": {},
        "recent_events": {},
    }
    
    # Check heartbeats
    for name, hb_path in HEARTBEAT_FILES.items():
        status_data["heartbeats"][name] = check_heartbeat(name, hb_path)
    
    # Check states
    for name, state_path in STATE_FILES.items():
        status_data[name] = check_state(name, state_path)
    
    # Check recent events
    for name, event_path in EVENT_FILES.items():
        status_data["recent_events"][name] = check_events(name, event_path, tail=10)
    
    # Print JSON
    if args.json:
        print(json.dumps(status_data, indent=2))
        return
    
    # Print markdown
    md = generate_markdown(status_data)
    print(md)
    
    # Save markdown
    md_path = ROOT / "reports" / "organism_status_dashboard.md"
    md_path.parent.mkdir(parents=True, exist_ok=True)
    md_path.write_text(md, encoding="utf-8")
    
    # Save JSON
    json_path = ROOT / "reports" / "organism_status_dashboard.json"
    json_path.write_text(json.dumps(status_data, indent=2), encoding="utf-8")
    
    print(f"\nDashboard saved: {md_path}")
    print(f"JSON: {json_path}")


if __name__ == "__main__":
    main()
