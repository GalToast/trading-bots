#!/usr/bin/env python3
"""
Monitor all experimental shadow lanes during good session (07-21 UTC).
Reports closes, net PnL, resets, open positions, and file freshness.
Supports both legacy flat state payloads and current nested symbol state payloads.
Use: python scripts/monitor_experimental_lanes.py
"""

import json
import os
import sys
import time
from datetime import datetime, timezone
from typing import Any

LANES = {
    # ETH ATR optimization
    "ETH M5 ATR (1.55x)": "reports/penetration_lattice_shadow_ethusd_m5_atr_opt_state.json",
    "ETH M15 ATR (1.55x)": "reports/penetration_lattice_shadow_ethusd_m15_atr_opt_state.json",
    "ETH M15 Asym (alpha=0.5)": "reports/penetration_lattice_shadow_ethusd_m15_asym_state.json",
    # Structure Shapeshifter
    "ETH M5 Shapeshifter": "reports/penetration_lattice_shadow_ethusd_m5_structure_shapeshifter_state.json",
    # M5 Warp expansion candidates
    "XAU M5 Warp": "reports/penetration_lattice_shadow_xauusd_m5_warp_state.json",
    "NAS100 M5 Warp": "reports/penetration_lattice_shadow_nas100_m5_warp_state.json",
    "US30 M5 Warp": "reports/penetration_lattice_shadow_us30_m5_warp_state.json",
    # Other experimental
    "XAU M15 Vacuum": "reports/penetration_lattice_shadow_xauusd_m15_consolidation_vacuum_state.json",
}


def _coerce_int(value: Any) -> int:
    try:
        return int(float(value or 0))
    except (TypeError, ValueError):
        return 0


def _coerce_float(value: Any) -> float:
    try:
        return float(value or 0.0)
    except (TypeError, ValueError):
        return 0.0


def _count_open_positions(value: Any) -> int:
    if isinstance(value, list):
        return len(value)
    if isinstance(value, dict):
        return len(value)
    return _coerce_int(value)


def _extract_symbol_payload(payload: dict[str, Any]) -> dict[str, Any]:
    symbols = payload.get("symbols")
    if isinstance(symbols, dict):
        for symbol_payload in symbols.values():
            if isinstance(symbol_payload, dict):
                return symbol_payload
    return {}


def _extract_floating_pnl(payload: dict[str, Any], symbol_payload: dict[str, Any]) -> float:
    if "floating_pnl" in symbol_payload:
        return _coerce_float(symbol_payload.get("floating_pnl"))
    if "floating_pnl" in payload:
        return _coerce_float(payload.get("floating_pnl"))
    tickets = symbol_payload.get("open_tickets")
    if isinstance(tickets, list):
        total = 0.0
        found = False
        for ticket in tickets:
            if not isinstance(ticket, dict):
                continue
            if "floating_pnl" in ticket:
                total += _coerce_float(ticket.get("floating_pnl"))
                found = True
            elif "pnl_usd" in ticket:
                total += _coerce_float(ticket.get("pnl_usd"))
                found = True
            elif "profit_usd" in ticket:
                total += _coerce_float(ticket.get("profit_usd"))
                found = True
        if found:
            return total
    return 0.0


def extract_lane_metrics(payload: dict[str, Any]) -> dict[str, float | int]:
    symbol_payload = _extract_symbol_payload(payload)
    if symbol_payload:
        closes = _coerce_int(symbol_payload.get("realized_closes"))
        opens = _count_open_positions(symbol_payload.get("open_tickets"))
        net = _coerce_float(symbol_payload.get("realized_net_usd"))
        resets = _coerce_int(symbol_payload.get("anchor_resets") or symbol_payload.get("reset_count"))
        floating = _extract_floating_pnl(payload, symbol_payload)
    else:
        closes = _coerce_int(payload.get("close_count") or payload.get("realized_closes"))
        opens = _count_open_positions(payload.get("open_positions") or payload.get("open_tickets"))
        net = _coerce_float(payload.get("total_realized_pnl") or payload.get("realized_net_usd"))
        resets = _coerce_int(payload.get("reset_count") or payload.get("anchor_resets"))
        floating = _coerce_float(payload.get("floating_pnl"))
    return {
        "closes": closes,
        "opens": opens,
        "net": net,
        "resets": resets,
        "floating": floating,
    }


def check_lane(name: str, path: str) -> dict:
    """Check a single lane's state file."""
    result = {"name": name, "status": "unknown"}

    if not os.path.exists(path):
        result["status"] = "no_state_file"
        return result

    try:
        mtime = os.path.getmtime(path)
        age = time.time() - mtime
        result["file_age_s"] = age

        if age > 300:
            result["status"] = "stale"
        elif age > 120:
            result["status"] = "aging"
        else:
            result["status"] = "fresh"

        with open(path) as f:
            d = json.load(f)

        result.update(extract_lane_metrics(d))

        # Compute $/close if we have closes
        if result["closes"] > 0:
            result["per_close"] = result["net"] / result["closes"]

    except Exception as e:
        result["status"] = f"error: {e}"

    return result


def main():
    now = datetime.now(timezone.utc)
    good_session = 7 <= now.hour < 21

    print(f"\n{'='*80}")
    print(f"Experimental Lane Monitor - {now.strftime('%Y-%m-%d %H:%M UTC')}")
    print(f"Session: {'GOOD (07-21 UTC)' if good_session else 'OFF-SESSION (21-07 UTC)'}")
    print(f"{'='*80}\n")

    results = []
    for name, path in LANES.items():
        results.append(check_lane(name, path))

    # Print table
    print(f"{'Lane':<30} {'Status':<10} {'Closes':>7} {'Net $':>10} {'$/c':>8} {'Opens':>6} {'Resets':>7} {'Age':>6}")
    print("-" * 90)

    total_closes = 0
    total_net = 0
    fresh_count = 0
    stale_count = 0

    for r in results:
        status = r.get("status", "?")
        if status == "fresh":
            fresh_count += 1
        elif status in ("stale", "aging"):
            stale_count += 1

        closes = r.get("closes", 0)
        total_closes += closes
        total_net += r.get("net", 0)

        net_str = f"${r.get('net', 0):.2f}" if "net" in r else "-"
        per_close_str = f"${r.get('per_close', 0):.2f}" if "per_close" in r else "-"
        age_str = f"{r.get('file_age_s', 0):.0f}s" if "file_age_s" in r else "-"

        print(
            f"{r['name']:<30} {status:<10} {closes:>7} {net_str:>10} {per_close_str:>8} {r.get('opens', '-'):>6} {r.get('resets', '-'):>7} {age_str:>6}"
        )

    print("-" * 90)
    print(f"{'TOTALS':<30} {'':<10} {total_closes:>7} ${total_net:>9.2f}")
    print(f"\nFresh: {fresh_count} | Stale/Aging: {stale_count} | No file: {len(results) - fresh_count - stale_count}")

    if not good_session:
        print("\nWARNING: OFF-SESSION - lanes are idling. First closes expected at 07:00 UTC.")

    # Alert on concerning patterns
    print("\n--- Alerts ---")
    alerts = []
    for r in results:
        if r.get("status") == "stale":
            alerts.append(f"ALERT {r['name']}: stale state file ({r.get('file_age_s', 0):.0f}s old)")
        if r.get("closes", 0) > 0 and r.get("per_close", 0) < -5:
            alerts.append(f"ALERT {r['name']}: toxic at ${r['per_close']:.2f}/close ({r['closes']} closes)")
        if r.get("resets", 0) > 5:
            alerts.append(f"WARN {r['name']}: {r['resets']} resets (elevated)")

    if alerts:
        for a in alerts:
            print(a)
    else:
        print("No alerts.")

    print(f"\n{'='*80}\n")

    return 0


if __name__ == "__main__":
    sys.exit(main())
