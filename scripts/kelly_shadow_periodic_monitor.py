#!/usr/bin/env python3
"""Kelly Shadow Periodic Monitor — reads events/state and posts summary updates."""

import json
import time
from pathlib import Path
from datetime import datetime, timezone

STATE_PATH = Path("reports/kelly_shadow_state.json")
EVENTS_PATH = Path("reports/kelly_shadow_events.jsonl")
MONITOR_OUTPUT = Path("reports/kelly_shadow_monitor.json")

def load_state():
    if not STATE_PATH.exists():
        return None
    with open(STATE_PATH) as f:
        return json.load(f)

def load_events():
    events = []
    if not EVENTS_PATH.exists():
        return events
    with open(EVENTS_PATH) as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    events.append(json.loads(line))
                except json.JSONDecodeError:
                    pass
    return events

def summarize(state, events):
    if not state:
        return {"status": "no_state", "message": "Kelly shadow state file not found"}

    summary = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "cycle": state.get("cycle", "?"),
        "total_equity": state.get("total_equity", 0),
        "total_pnl": state.get("total_pnl", 0),
        "return_pct": state.get("return_pct", 0),
        "coins": {},
        "recent_events": [],
    }

    # Per-coin summary
    ledgers = state.get("ledgers", {})
    for coin, ledger in ledgers.items():
        summary["coins"][coin] = {
            "strategy": ledger.get("strategy", "?"),
            "position": ledger.get("position", "flat"),
            "signals": ledger.get("signals", 0),
            "closes": ledger.get("closes", 0),
            "win_rate": ledger.get("win_rate", 0),
            "pnl": ledger.get("pnl", 0),
            "equity": ledger.get("equity", 0),
            "candles": ledger.get("history_len", 0),
        }
        if ledger.get("position_entry"):
            summary["coins"][coin]["entry_price"] = ledger["position_entry"]
            summary["coins"][coin]["tp"] = ledger.get("position_tp")
            summary["coins"][coin]["sl"] = ledger.get("position_sl")

    # Recent events (last 5)
    for evt in events[-5:]:
        summary["recent_events"].append({
            "coin": evt.get("coin", "?"),
            "action": evt.get("action", "?"),
            "price": evt.get("entry_price") or evt.get("exit_price", "?"),
            "net": evt.get("net", 0),
        })

    return summary

def main():
    state = load_state()
    events = load_events()
    summary = summarize(state, events)

    # Save monitor output
    with open(MONITOR_OUTPUT, "w") as f:
        json.dump(summary, f, indent=2)

    # Print summary
    print(f"\n=== Kelly Shadow Monitor — {summary['timestamp']} ===")
    print(f"Cycle: {summary['cycle']} | Equity: ${summary['total_equity']:.2f} | PnL: ${summary['total_pnl']:.2f} ({summary['return_pct']:+.2f}%)")
    print()
    for coin, data in summary["coins"].items():
        pos = data.get("position", "flat")
        entry = data.get("entry_price", "")
        signals = data.get("signals", 0)
        closes = data.get("closes", 0)
        pnl = data.get("pnl", 0)
        candles = data.get("candles", 0)
        entry_str = f" @ {entry}" if entry else ""
        print(f"  {coin} ({data['strategy']}): {pos}{entry_str} | signals={signals} closes={closes} pnl=${pnl:+.2f} candles={candles}")

    if summary["recent_events"]:
        print("\nRecent events:")
        for evt in summary["recent_events"]:
            print(f"  {evt['coin']} {evt['action']} @ {evt['price']} net=${evt['net']:+.2f}")

    print()
    return summary

if __name__ == "__main__":
    main()
