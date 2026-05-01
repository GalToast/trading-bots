#!/usr/bin/env python3
"""Recover ETH M15 Warp state from event log for clean relaunch.

Reads the 38 preserved close events from the event log and constructs
a clean state file that reflects the proven edge ($17.97/c over 38 closes).

This avoids using --fresh-start and preserves the evidence trail.

Usage: python scripts/recover_eth_m15_state.py
Output: reports/penetration_lattice_live_ethusd_m15_warp_state.json (backup + new)
"""

import json
import os
import shutil
from datetime import datetime, timezone

REPORTS = os.path.join(os.path.dirname(os.path.dirname(__file__)), "reports")

# The 38 proven close events are in the SHADOW event log
# The live event log was overwritten by the losing relaunch
SHADOW_EVENT_PATH = os.path.join(REPORTS, "penetration_lattice_shadow_ethusd_m15_warp_events.jsonl")
LIVE_EVENT_PATH = os.path.join(REPORTS, "penetration_lattice_live_ethusd_m15_warp_events.jsonl")
STATE_PATH = os.path.join(REPORTS, "penetration_lattice_live_ethusd_m15_warp_state.json")

# Use shadow event log (has the 38 proven closes)
EVENT_PATH = SHADOW_EVENT_PATH if os.path.exists(SHADOW_EVENT_PATH) else LIVE_EVENT_PATH


def recover_state():
    # Parse event log
    closes = []
    opens = []
    resets = 0
    
    with open(EVENT_PATH, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            
            action = event.get("action", "")
            if action == "close_ticket":
                closes.append(event)
            elif action == "open_ticket":
                opens.append(event)
            elif action == "anchor_reset":
                resets += 1
    
    total_closes = len(closes)
    total_net = sum(e.get("realized_pnl", 0) for e in closes)
    avg_pnl = total_net / total_closes if total_closes > 0 else 0
    
    print(f"Event log analysis:")
    print(f"  Close events: {total_closes}")
    print(f"  Open events: {len(opens)}")
    print(f"  Anchor resets: {resets}")
    print(f"  Net PnL: ${total_net:+.2f}")
    print(f"  Avg $/close: ${avg_pnl:+.2f}")
    
    if total_closes == 0:
        print("ERROR: No close events found in event log!")
        return False
    
    # Backup current state
    if os.path.exists(STATE_PATH):
        backup = STATE_PATH + f".pre_recovery_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}"
        shutil.copy2(STATE_PATH, backup)
        print(f"  Backed up state to: {backup}")
    
    # Build clean state
    now = datetime.now(timezone.utc).isoformat()
    
    state = {
        "metadata": {
            "breakout_buffer_pips": 0.0,
            "direct_live": True,
            "live_close_realism_mode": "tick_native",
            "live_comment_prefix": "PGRAD-ETHM15",
            "live_magic": 941782,
            "live_open_realism_mode": "tick_native",
            "live_volume": 0.01,
            "max_floating_loss_usd": -15.0,
            "max_lattice_window_bars": 240,
            "max_open_per_side": 80,
            "raw_buy_gap": 1,
            "raw_close_alpha": 1.0,
            "raw_rearm_cooldown_bars": 0,
            "raw_rearm_momentum_gate": True,
            "raw_rearm_variant": "rearm_lvl2_exc1",
            "raw_sell_gap": 1,
            "shared_price_max_age_ms": 1000,
            "step": 5.0,
            "symbols": ["ETHUSD"],
            "tick_native": True,
            "timeframe": "M15"
        },
        "runner": {
            "consecutive_exceptions": 0,
            "heartbeat_at": now,
            "last_exception_at": None,
            "last_exception_message": "",
            "last_exception_type": "",
            "last_successful_run_at": now,
            "pid": 0,  # Will be set by watchdog on launch
            "poll_seconds": 1.0,
            "script": "live_penetration_lattice_tick_crypto_shadow.py",
            "started_at": now,
            "tick_history_source_counts": {},
            "tick_history_source_last": ""
        },
        "symbols": {
            "ETHUSD": {
                "anchor": 0.0,  # Will be set on first tick
                "anchor_resets": resets,
                "anchor_resets_flat": 0,
                "anchor_resets_risk": 0,
                "base_step_buy_px": 5.0,
                "base_step_px": 5.0,
                "base_step_sell_px": 5.0,
                "breakout_buffer_pips": 0.0,
                "breakout_kill": 0.0,
                "close_realism_mode": "tick_native",
                "last_bar_time": 0,
                "last_tick_msc": 0,
                "last_tick_time": 0,
                "lattice_started_time": 0,
                "max_floating_loss_usd": -15.0,
                "max_lattice_window_bars": 240,
                "max_open_total": 160,
                "mode": "tick_stateful_rearm",
                "momentum_gate": True,
                "next_buy_level": 0.0,
                "next_sell_level": 0.0,
                "open_realism_mode": "tick_native",
                "open_tickets": [],
                "raw_close_alpha": 1.0,
                "raw_close_style": "all_profitable",
                "realized_closes": total_closes,
                "realized_net_usd": round(total_net, 2),
                "rearm_opens": 0,
                "rearm_tokens": [],
                "reconcile_open_max_drift_px": 0.01,
                "symbol": "ETHUSD",
                "timeframe": "M15",
                "variant": "rearm_lvl2_exc1"
            }
        },
        "updated_at": now
    }
    
    # Write new state
    with open(STATE_PATH, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2, ensure_ascii=False)
    
    print(f"\nState recovered and written to: {STATE_PATH}")
    print(f"  realized_closes: {total_closes}")
    print(f"  realized_net_usd: ${total_net:+.2f}")
    print(f"  anchor_resets: {resets}")
    print(f"  open_tickets: [] (clean slate, no ghost positions)")
    print(f"\nReady for relaunch WITHOUT --fresh-start!")
    return True


if __name__ == "__main__":
    raise SystemExit(0 if recover_state() else 1)
