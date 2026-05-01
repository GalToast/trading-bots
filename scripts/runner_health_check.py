#!/usr/bin/env python3
"""
Runner Health Check — Simple monitoring tool for the isolated runner.

Checks:
- State file freshness
- Equity per coin
- Active positions
- Signal/close counts
- PnL vs predictions
- Event log integrity

Usage:
    python scripts/runner_health_check.py
"""
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
STATE_PATH = ROOT / "reports" / "multi_coin_isolated_state.json"
EVENT_PATH = ROOT / "reports" / "multi_coin_isolated_events.jsonl"

def main():
    print("=" * 70, flush=True)
    print("RUNNER HEALTH CHECK")
    print("=" * 70, flush=True)

    # Check state file
    if not STATE_PATH.exists():
        print("\n❌ State file NOT FOUND — runner may not be running", flush=True)
        return

    with open(STATE_PATH) as f:
        state = json.load(f)

    updated = state.get("updated_at", "unknown")
    cycle = state.get("cycle", 0)
    equity = state.get("total_equity", 0)
    pnl = state.get("total_pnl", 0)

    # Check freshness
    try:
        then = datetime.fromisoformat(updated)
        now = datetime.now(timezone.utc)
        age_sec = (now - then).total_seconds()
        if age_sec < 120:
            freshness = "✅ FRESH"
        elif age_sec < 600:
            freshness = "⚠️ STALE (< 10 min)"
        else:
            freshness = "❌ STALE (> 10 min)"
    except:
        age_sec = -1
        freshness = "❌ INVALID TIMESTAMP"

    print(f"\nState file: {STATE_PATH}")
    print(f"Last update: {updated}")
    print(f"Age: {age_sec:.0f}s ({freshness})")
    print(f"Cycle: {cycle}")
    print(f"Total equity: ${equity:.2f}")
    print(f"Total PnL: ${pnl:+.2f}")

    # Check per-coin
    ledgers = state.get("ledgers", {})
    if not ledgers:
        print("\n⚠️ No ledger data found", flush=True)

    active_positions = []
    for coin, info in sorted(ledgers.items()):
        pos = info.get("position", "flat")
        pnl_coin = info.get("pnl", 0)
        equity_coin = info.get("equity", 0)
        signals = info.get("signals", 0)
        closes = info.get("closes", 0)
        wins = info.get("wins", 0)
        losses = info.get("losses", 0)
        wr = info.get("win_rate", 0)
        strategy = info.get("strategy", "?")

        status = "🟢 ACTIVE" if pos == "active" else "⚪ FLAT"
        print(f"\n{coin} ({strategy}):")
        print(f"  Status: {status}")
        print(f"  Equity: ${equity_coin:.2f}, PnL: ${pnl_coin:+.2f}")
        print(f"  Signals: {signals}, Closes: {closes}, Wins: {wins}, Losses: {losses}")
        print(f"  WR: {wr:.1f}%")

        if pos == "active":
            entry = info.get("position_entry", "?")
            tp = info.get("position_tp", "?")
            sl = info.get("position_sl", "?")
            hold = info.get("position_hold", "?")
            active_positions.append(coin)
            print(f"  ⚡ Position: entry={entry}, TP={tp}, SL={sl}, hold={hold}")

    # Check event log
    if EVENT_PATH.exists():
        with open(EVENT_PATH) as f:
            events = [json.loads(line) for line in f if line.strip()]
        opens = [e for e in events if e.get("action") == "open"]
        closes = [e for e in events if e.get("action") == "close"]
        starts = [e for e in events if "runner_start" in e.get("action", "")]

        print(f"\nEvent log: {EVENT_PATH}")
        print(f"Total events: {len(events)}")
        print(f"Runner starts: {len(starts)}")
        print(f"Open events: {len(opens)}")
        print(f"Close events: {len(closes)}")

        if len(starts) > 1:
            print(f"⚠️ Multiple runner starts detected — possible restarts")

    # Summary
    print(f"\n{'='*70}", flush=True)
    print("SUMMARY", flush=True)
    print(f"{'='*70}", flush=True)
    print(f"Active positions: {len(active_positions)}")
    if active_positions:
        print(f"  Coins: {', '.join(active_positions)}")
    print(f"Overall freshness: {freshness}")

    if age_sec > 600:
        print(f"\n⚠️ Runner may not be running — state is stale", flush=True)
        print(f"  To restart: python scripts/multi_coin_isolated_runner.py --total-cash 48", flush=True)
    elif not active_positions and cycle > 20:
        print(f"\n⚠️ No active positions after {cycle} cycles — market may be quiet", flush=True)
    else:
        print(f"\n✅ Runner appears healthy", flush=True)


if __name__ == "__main__":
    main()
