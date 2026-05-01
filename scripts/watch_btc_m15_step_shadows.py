#!/usr/bin/env python3
"""BTC M15 Step Shadow Monitor — watches $15 and $20 shadows vs live $75.

Usage:
    python scripts/watch_btc_m15_step_shadows.py          # Single snapshot
    python scripts/watch_btc_m15_step_shadows.py --watch  # Polling mode (30s default)
    python scripts/watch_btc_m15_step_shadows.py --poll-seconds 60
"""
import argparse
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
REPORTS = ROOT / "reports"

SHADOWS = {
    "$15": REPORTS / "penetration_lattice_shadow_btcusd_m15_step15_state.json",
    "$20": REPORTS / "penetration_lattice_shadow_btcusd_m15_step20_state.json",
}

LIVE_PATHS = [
    REPORTS / "penetration_lattice_live_btcusd_m15_warp_state.json",
]

GATE = {
    "min_closes": 25,
    "min_net_usd": 0.0,
    "max_reset_ratio": 0.5,
}


def load_state(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return {}


def extract_metrics(state: dict) -> dict:
    btc = state.get("symbols", {}).get("BTCUSD", {})
    runner = state.get("runner", {})
    return {
        "anchor": btc.get("anchor"),
        "closes": btc.get("close_count"),
        "net_usd": btc.get("net_realized_usd"),
        "open": btc.get("open_count"),
        "floating": btc.get("floating_pnl_usd"),
        "resets": btc.get("reset_count"),
        "max_open": btc.get("max_open"),
        "started": runner.get("started_at"),
        "heartbeat": runner.get("heartbeat_at"),
    }


def compute_derived(m: dict) -> dict:
    closes = m.get("closes") or 0
    net = m.get("net_usd") or 0.0
    resets = m.get("resets") or 0
    started = m.get("started")
    
    per_close = net / closes if closes > 0 else None
    
    hours_running = None
    if started:
        try:
            start_dt = datetime.fromisoformat(started.replace("Z", "+00:00"))
            now = datetime.now(timezone.utc)
            hours_running = (now - start_dt).total_seconds() / 3600.0
        except (ValueError, TypeError):
            pass
    
    closes_per_hour = closes / hours_running if hours_running and hours_running > 0 else None
    reset_ratio = resets / closes if closes > 0 else None
    
    return {
        "per_close": per_close,
        "hours_running": hours_running,
        "closes_per_hour": closes_per_hour,
        "reset_ratio": reset_ratio,
    }


def gate_verdict(m: dict, d: dict) -> str:
    closes = m.get("closes") or 0
    net = m.get("net_usd") or 0.0
    reset_ratio = d.get("reset_ratio")
    
    if closes < GATE["min_closes"]:
        return f"AWAITING ({closes}/{GATE['min_closes']} closes)"
    if net < GATE["min_net_usd"]:
        return f"FAILED (net ${net:.2f} < 0)"
    if reset_ratio is not None and reset_ratio > GATE["max_reset_ratio"]:
        return f"FAILED (reset ratio {reset_ratio:.2f} > {GATE['max_reset_ratio']})"
    return "PASSED ✓"


def format_row(label: str, m: dict, d: dict) -> list[str]:
    closes = m.get("closes") or 0
    net = m.get("net_usd") or 0.0
    per_close = d.get("per_close")
    closes_hr = d.get("closes_per_hour")
    reset_ratio = d.get("reset_ratio")
    max_open = m.get("max_open") or 0
    heartbeat = m.get("heartbeat", "n/a")
    verdict = gate_verdict(m, d)
    
    return [
        f"  {label}:",
        f"    Closes: {closes}  |  Net: ${net:.2f}  |  $/close: ${per_close:.2f}" if per_close else f"    Closes: {closes}  |  Net: ${net:.2f}  |  $/close: n/a",
        f"    Closes/hr: {closes_hr:.1f}" if closes_hr else f"    Closes/hr: n/a",
        f"    Open: {m.get('open') or 0}  |  Max open: {max_open}  |  Floating: ${m.get('floating') or 0:.2f}",
        f"    Resets: {m.get('resets') or 0}  |  Reset ratio: {reset_ratio:.2f}" if reset_ratio else f"    Resets: {m.get('resets') or 0}",
        f"    Gate: {verdict}",
        f"    Heartbeat: {heartbeat}",
    ]


def snapshot():
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    lines = [f"\n{'='*60}", f"BTC M15 Step Shadow Monitor — {now}", f"{'='*60}"]
    
    # Shadows
    for label, path in SHADOWS.items():
        state = load_state(path)
        if not state:
            lines.append(f"\n  {label}: State file not found or empty ({path.name})")
            continue
        m = extract_metrics(state)
        d = compute_derived(m)
        lines.extend(format_row(label, m, d))
    
    # Live lane
    live_found = False
    for lp in LIVE_PATHS:
        state = load_state(lp)
        if state:
            live_found = True
            m = extract_metrics(state)
            d = compute_derived(m)
            lines.extend(format_row("$75 live", m, d))
            break
    if not live_found:
        lines.append(f"\n  $75 live: State file not found (tried {[p.name for p in LIVE_PATHS]})")
    
    lines.append(f"\n{'='*60}")
    print("\n".join(lines))
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description="BTC M15 Step Shadow Monitor")
    parser.add_argument("--watch", action="store_true", help="Polling mode")
    parser.add_argument("--poll-seconds", type=int, default=30, help="Poll interval (default: 30s)")
    args = parser.parse_args()
    
    if args.watch:
        print(f"Watching BTC M15 step shadows (every {args.poll-seconds}s). Ctrl+C to stop.")
        try:
            while True:
                snapshot()
                time.sleep(args.poll-seconds)
        except KeyboardInterrupt:
            print("\nMonitor stopped.")
    else:
        snapshot()


if __name__ == "__main__":
    main()
