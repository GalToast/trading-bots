#!/usr/bin/env python3
"""
State Auditor — Compares isolated runner state file vs actual Coinbase exchange positions.

Flags mismatches (orphaned positions on exchange not in state, or stale state positions
that no longer exist on exchange). Also checks heartbeat freshness.

Usage:
    python scripts/state_auditor.py
    python scripts/state_auditor.py --state-path reports/multi_coin_isolated_state.json
"""
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from coinbase_advanced_client import CoinbaseAdvancedClient

ROOT = Path(__file__).resolve().parent.parent
STATE_PATH = ROOT / "reports" / "multi_coin_isolated_state.json"
HEARTBEAT_PATH = ROOT / "reports" / "multi_coin_isolated_heartbeat.json"

MAX_HEARTBEAT_AGE_SECONDS = 120  # 2 minutes — runner cycles every ~30s


def utc_now_iso():
    return datetime.now(timezone.utc).isoformat()


def load_state(path):
    if not path.exists():
        return None
    try:
        with open(path) as f:
            return json.load(f)
    except Exception as e:
        print(f"  [ERROR] Failed to load state: {e}")
        return None


def load_heartbeat(path):
    if not path.exists():
        return None
    try:
        with open(path) as f:
            return json.load(f)
    except Exception:
        return None


def audit_state(state, client):
    """Compare state file positions vs exchange open orders."""
    issues = []

    # 1. Check for active positions in state
    state_active = {}
    for coin, ledger in state.get("ledgers", {}).items():
        if ledger.get("position") == "active":
            state_active[coin] = {
                "entry": ledger.get("position_entry"),
                "tp": ledger.get("position_tp"),
                "sl": ledger.get("position_sl"),
                "hold": ledger.get("position_hold"),
                "max_hold": ledger.get("position_max_hold"),
                "units": ledger.get("position_units"),
                "deploy": ledger.get("position_deploy"),
            }

    if state_active:
        print(f"\n📋 State shows {len(state_active)} active position(s):")
        for coin, pos in state_active.items():
            hold_status = f"hold={pos['hold']}/{pos['max_hold']}" if pos.get('max_hold') else "no max"
            print(f"  {coin}: entry=${pos['entry']:.6f} TP=${pos['tp']:.6f} SL=${pos['sl']:.6f} | {hold_status}")
    else:
        print("\n📋 State shows NO active positions (all flat)")

    # 2. Fetch open orders from exchange
    print("\n🔍 Checking exchange for open orders...", flush=True)
    try:
        open_orders = client.list_orders(order_status="OPEN", limit=100)
        open_order_list = open_orders.get("orders", []) if isinstance(open_orders, dict) else []
    except Exception as e:
        print(f"  [ERROR] Failed to fetch open orders: {e}")
        print("  Cannot complete audit — check API connectivity.")
        return issues

    exchange_positions = {}
    for order in open_order_list:
        pid = order.get("product_id", "").upper()
        side = order.get("side", "").upper()
        size = float(order.get("base_size", 0) or order.get("quote_size", 0) or 0)
        status = order.get("status", "")
        order_type = order.get("order_type", "")

        if status != "OPEN":
            continue
        if side != "BUY":
            continue

        if pid not in exchange_positions:
            exchange_positions[pid] = []
        exchange_positions[pid].append({
            "order_id": order.get("order_id", ""),
            "side": side,
            "size": size,
            "type": order_type,
        })

    if exchange_positions:
        print(f"  Exchange has {len(open_order_list)} open order(s) across {len(exchange_positions)} coin(s):")
        for coin, orders in exchange_positions.items():
            print(f"    {coin}: {len(orders)} open order(s)")
    else:
        print("  Exchange has NO open orders")

    # 3. Compare: state active vs exchange
    state_coins = set(state_active.keys())
    exchange_coins = set(exchange_positions.keys())

    # Orphaned on exchange (open order but not in state)
    orphaned_exchange = exchange_coins - state_coins
    if orphaned_exchange:
        for coin in orphaned_exchange:
            issues.append({
                "severity": "HIGH",
                "type": "orphaned_on_exchange",
                "coin": coin,
                "detail": f"Open order on exchange but NOT in state file",
            })
        print(f"\n🚨 ORPHANED on exchange (not in state): {orphaned_exchange}")
        print("  These have open orders on Coinbase but the runner doesn't track them.")
        print("  Action: Close manually or add to runner config.")
    else:
        print("\n✅ No orphaned positions on exchange")

    # Stale in state (in state but not on exchange — may have been closed manually)
    stale_in_state = state_coins - exchange_coins
    if stale_in_state:
        for coin in stale_in_state:
            issues.append({
                "severity": "MEDIUM",
                "type": "stale_in_state",
                "coin": coin,
                "detail": f"State shows active position but no open order on exchange",
            })
        print(f"\n⚠️  Stale in state (not on exchange): {stale_in_state}")
        print("  These show as active in state but have no exchange orders.")
        print("  Action: Runner may have missed the close event. Reset position to flat.")
    else:
        print("✅ No stale positions in state file")

    # Matched (in both state and exchange)
    matched = state_coins & exchange_coins
    if matched:
        print(f"\n✅ Matched (state + exchange): {matched}")
        for coin in matched:
            pos = state_active[coin]
            orders = exchange_positions[coin]
            total_units = sum(o["size"] for o in orders)
            state_units = pos.get("units", 0)
            if abs(total_units - state_units) > state_units * 0.01:  # 1% tolerance
                issues.append({
                    "severity": "MEDIUM",
                    "type": "unit_mismatch",
                    "coin": coin,
                    "state_units": state_units,
                    "exchange_units": total_units,
                    "detail": f"State: {state_units:.6f} vs Exchange: {total_units:.6f}",
                })
                print(f"  ⚠️  {coin}: Unit mismatch — state={state_units:.6f} exchange={total_units:.6f}")
            else:
                print(f"  ✅ {coin}: Units match (state={state_units:.6f}, exchange={total_units:.6f})")

    return issues


def check_heartbeat(heartbeat_path):
    """Check if runner is alive based on heartbeat freshness."""
    hb = load_heartbeat(heartbeat_path)
    if hb is None:
        print(f"\n🚨 HEARTBEAT: File not found at {heartbeat_path}")
        print("  Runner may not be running, or heartbeat path is misconfigured.")
        return False

    updated_at = hb.get("updated_at", "")
    cycle = hb.get("cycle", "?")
    equity = hb.get("total_equity", "?")

    # Parse timestamp
    try:
        ts = datetime.fromisoformat(updated_at)
        age_seconds = (datetime.now(timezone.utc) - ts).total_seconds()
    except Exception:
        print(f"\n⚠️  HEARTBEAT: Could not parse timestamp: {updated_at}")
        return False

    status = "ALIVE" if age_seconds < MAX_HEARTBEAT_AGE_SECONDS else "STALE/DEAD"
    emoji = "💚" if age_seconds < MAX_HEARTBEAT_AGE_SECONDS else "💀"
    print(f"\n{emoji} HEARTBEAT: {status}")
    print(f"  Cycle: {cycle}")
    print(f"  Equity: ${equity}")
    print(f"  Last update: {updated_at}")
    print(f"  Age: {age_seconds:.0f}s (threshold: {MAX_HEARTBEAT_AGE_SECONDS}s)")

    if age_seconds >= MAX_HEARTBEAT_AGE_SECONDS:
        print(f"\n  Runner appears dead. Last heartbeat was {age_seconds/60:.1f} minutes ago.")
        print(f"  Action: Restart runner with: python scripts/multi_coin_isolated_runner.py --total-cash 48")
        return False

    return True


def main():
    print("=" * 60, flush=True)
    print("  STATE AUDITOR — Coinbase Isolated Runner", flush=True)
    print("=" * 60, flush=True)

    state_path = STATE_PATH
    if len(sys.argv) > 2 and sys.argv[1] == "--state-path":
        state_path = Path(sys.argv[2])

    # Load state
    state = load_state(state_path)
    if state is None:
        print(f"\n[ERROR] State file not found: {state_path}")
        print("  Runner has never written state, or path is wrong.")
        return 1

    print(f"\n📁 State file: {state_path}")
    print(f"  Cycle: {state.get('cycle', '?')}")
    print(f"  Total equity: ${state.get('total_equity', '?')}")
    print(f"  Total PnL: ${state.get('total_pnl', 0):+.4f}")
    print(f"  Updated at: {state.get('updated_at', '?')}")

    # Heartbeat check
    is_alive = check_heartbeat(HEARTBEAT_PATH)

    # Exchange audit
    client = CoinbaseAdvancedClient()
    issues = audit_state(state, client)

    # Summary
    print(f"\n{'=' * 60}")
    print(f"  AUDIT SUMMARY")
    print(f"{'=' * 60}")
    high_issues = [i for i in issues if i["severity"] == "HIGH"]
    medium_issues = [i for i in issues if i["severity"] == "MEDIUM"]

    if high_issues:
        print(f"\n🚨 {len(high_issues)} HIGH severity issue(s):")
        for issue in high_issues:
            print(f"  - [{issue['type']}] {issue['coin']}: {issue['detail']}")

    if medium_issues:
        print(f"\n⚠️  {len(medium_issues)} MEDIUM severity issue(s):")
        for issue in medium_issues:
            print(f"  - [{issue['type']}] {issue.get('coin', 'N/A')}: {issue['detail']}")

    if not issues and is_alive:
        print(f"\n✅ All clear — state matches exchange, runner is alive")
    elif not issues and not is_alive:
        print(f"\n⚠️  State is clean but runner appears dead — safe to restart")

    # Output JSON for programmatic use
    report = {
        "ts_utc": utc_now_iso(),
        "state_path": str(state_path),
        "runner_alive": is_alive,
        "state_cycle": state.get("cycle"),
        "state_equity": state.get("total_equity"),
        "state_updated": state.get("updated_at"),
        "issues": issues,
        "high_count": len(high_issues),
        "medium_count": len(medium_issues),
    }
    report_path = ROOT / "reports" / "state_audit_report.json"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(f"\n📄 Full report saved to: {report_path}")

    return 1 if high_issues else 0


if __name__ == "__main__":
    sys.exit(main())
