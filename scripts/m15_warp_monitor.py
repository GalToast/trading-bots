#!/usr/bin/env python3
"""
M15 Warp Persistent Monitor
=============================
Monitors the BTC M15 Warp shadow lane and posts alerts to the switchboard
when significant events occur:
- New closes (every 10 closes)
- Floating PnL changes > $100
- Process death detection
- Hourly status summaries

Usage:
  python scripts/m15_warp_monitor.py --interval 300  # Check every 5 minutes
"""
import json
import sys
import time
import argparse
from pathlib import Path
from datetime import datetime, timezone

ROOT = Path(__file__).resolve().parent.parent
STATE_PATH = ROOT / "reports" / "penetration_lattice_shadow_btcusd_m15_warp_state.json"
ALERT_PATH = ROOT / "reports" / "m15_warp_monitor_alerts.jsonl"

def utc_now_iso():
    return datetime.now(timezone.utc).isoformat()

def read_state():
    if not STATE_PATH.exists():
        return None
    with open(STATE_PATH) as f:
        return json.load(f)

def log_alert(alert_type, message, data=None):
    alert = {
        "timestamp": utc_now_iso(),
        "type": alert_type,
        "message": message,
    }
    if data:
        alert["data"] = data
    ALERT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(ALERT_PATH, "a") as f:
        f.write(json.dumps(alert) + "\n")
    print(f"[{alert_type}] {message}")

def monitor(interval_seconds=300):
    """Monitor M15 Warp state and alert on changes."""
    last_closes = None
    last_realized = None
    last_floating = None
    last_heartbeat = None
    consecutive_dead = 0
    
    print(f"M15 Warp Monitor started at {utc_now_iso()}")
    print(f"Checking every {interval_seconds}s")
    print(f"State file: {STATE_PATH}")
    print()
    
    while True:
        state = read_state()
        
        if state is None:
            consecutive_dead += 1
            if consecutive_dead == 3:
                log_alert("PROCESS_DEAD", "M15 Warp state file missing - process may have crashed!")
            time.sleep(interval_seconds)
            continue
        
        consecutive_dead = 0
        
        symbols = state.get("symbols", {})
        btc = symbols.get("BTCUSD", {})
        runner = state.get("runner", {})
        
        realized_closes = int(btc.get("realized_closes", 0))
        realized_net = float(btc.get("realized_net_usd", 0))
        open_tickets = btc.get("open_tickets", [])
        open_count = len(open_tickets)
        heartbeat = runner.get("heartbeat_at", "unknown")
        
        # Calculate floating PnL (approximate)
        floating = 0
        current_price = btc.get("last_price", 0)
        for ticket in open_tickets:
            entry = float(ticket.get("entry_price", 0))
            direction = ticket.get("direction", "BUY")
            if direction == "BUY":
                floating += (current_price - entry) * 0.01
            else:
                floating += (entry - current_price) * 0.01
        
        # Alert on new closes (every 10)
        if last_closes is not None and realized_closes > last_closes:
            new_closes = realized_closes - last_closes
            if new_closes >= 10 or (last_closes // 10) != (realized_closes // 10):
                pnl_since_last = realized_net - (last_realized or 0)
                log_alert("CLOSES_MILESTONE", 
                         f"Reached {realized_closes} closes (+{new_closes} new). Realized: ${realized_net:.2f}. PnL since last check: ${pnl_since_last:.2f}",
                         {"closes": realized_closes, "realized_net": realized_net})
        
        # Alert on floating PnL changes
        if last_floating is not None and abs(floating - last_floating) > 100:
            log_alert("FLOATING_CHANGE",
                     f"Floating PnL changed: ${last_floating:.2f} → ${floating:.2f} (Δ${floating - last_floating:+.2f})",
                     {"floating": floating, "open_positions": open_count})
        
        # Hourly summary
        if last_heartbeat is None or heartbeat != last_heartbeat:
            hours_running = None
            started = runner.get("started_at")
            if started:
                try:
                    start_dt = datetime.fromisoformat(started.replace("+00:00", "+00:00"))
                    now = datetime.now(timezone.utc)
                    hours_running = (now - start_dt).total_seconds() / 3600
                except:
                    pass
            
            pnl_per_hour = realized_net / hours_running if hours_running and hours_running > 0 else 0
            
            log_alert("HOURLY_SUMMARY",
                     f"M15 Warp: ${realized_net:.2f} net, {realized_closes} closes, {open_count} open, floating ${floating:.2f}, "
                     f"{'%.1f' % hours_running}h running, ${pnl_per_hour:.2f}/hour",
                     {"realized_net": realized_net, "closes": realized_closes, "open_count": open_count,
                      "floating": floating, "hours_running": hours_running, "pnl_per_hour": pnl_per_hour})
        
        # Update last values
        last_closes = realized_closes
        last_realized = realized_net
        last_floating = floating
        last_heartbeat = heartbeat
        
        time.sleep(interval_seconds)

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Monitor M15 Warp shadow lane")
    parser.add_argument("--interval", type=int, default=300, help="Check interval in seconds (default: 300)")
    args = parser.parse_args()
    monitor(args.interval)
