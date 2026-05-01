#!/usr/bin/env python3
"""Monitor BTC M15 Warp live vs shadow comparison.

Tracks the live execution quality against the shadow benchmark.
Polls every 30 seconds, writes comparison report.

Usage: python scripts/monitor_m15_warp_live_vs_shadow.py [--poll-seconds 30]
"""
import json
import time
from pathlib import Path
from datetime import datetime, timezone

ROOT = Path(__file__).resolve().parent.parent
LIVE_STATE = ROOT / "reports" / "penetration_lattice_live_btcusd_m15_warp_state.json"
SHADOW_STATE = ROOT / "reports" / "penetration_lattice_shadow_btcusd_m15_warp_state.json"
REPORT_FILE = ROOT / "reports" / "m15_warp_live_vs_shadow_report.json"

def utc_now_iso():
    return datetime.now(timezone.utc).isoformat()

def load_state(path):
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except:
        return None

def summarize(d, label):
    if d is None:
        return {label: "NOT FOUND"}
    btc = d.get("symbols", {}).get("BTCUSD", {})
    runner = d.get("runner", {})
    closes = btc.get("realized_closes", 0)
    net = btc.get("realized_net_usd", 0)
    opens = len(btc.get("open_tickets", []))
    resets = btc.get("anchor_resets", 0)
    per_close = net / closes if closes > 0 else 0
    return {
        label: "OK",
        "closes": closes,
        "net_usd": round(net, 2),
        "open_tickets": opens,
        "resets": resets,
        "$/close": round(per_close, 2) if closes > 0 else 0,
        "heartbeat": runner.get("heartbeat_at", "?"),
        "max_open": btc.get("max_open_total", 0),
    }

def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--poll-seconds", type=int, default=30)
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()

    print(f"BTC M15 Warp: LIVE vs SHADOW comparison")
    print(f"Polling every {args.poll_seconds}s")
    print(f"{'=' * 70}")

    cycle = 0
    while True:
        cycle += 1
        live = load_state(LIVE_STATE)
        shadow = load_state(SHADOW_STATE)

        live_sum = summarize(live, "LIVE")
        shadow_sum = summarize(shadow, "SHADOW")

        # Comparison
        comparison = {}
        if live_sum.get("LIVE") == "OK" and shadow_sum.get("SHADOW") == "OK":
            live_close = live_sum.get("$/close", 0)
            shadow_close = shadow_sum.get("$/close", 0)
            ratio = live_close / shadow_close if shadow_close > 0 else 0
            comparison = {
                "live_close_rate": live_close,
                "shadow_close_rate": shadow_close,
                "live/shadow_ratio": round(ratio, 2),
                "live_net": live_sum.get("net_usd", 0),
                "shadow_net": shadow_sum.get("net_usd", 0),
                "status": "🟢 LIVE RUNNING" if live_sum["net_usd"] >= -3500 else "🔴 CIRCUIT BREAKER",
            }

        report = {
            "ts_utc": utc_now_iso(),
            "cycle": cycle,
            "live": live_sum,
            "shadow": shadow_sum,
            "comparison": comparison,
        }

        # Write report
        REPORT_FILE.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")

        # Print summary
        print(f"\n--- Cycle {cycle} ({utc_now_iso()}) ---")
        print(f"  LIVE:   {live_sum.get('closes', '?')}c, ${live_sum.get('net_usd', '?'):+.2f}, {live_sum.get('open_tickets', '?')} open, ${live_sum.get('$/close', 0):+.2f}/close")
        print(f"  SHADOW: {shadow_sum.get('closes', '?')}c, ${shadow_sum.get('net_usd', '?'):+.2f}, {shadow_sum.get('open_tickets', '?')} open, ${shadow_sum.get('$/close', 0):+.2f}/close")
        if comparison:
            print(f"  RATIO:  live/shadow = {comparison['live/shadow_ratio']:.2f}x")
            print(f"  STATUS: {comparison['status']}")

        if args.once:
            return 0

        time.sleep(args.poll_seconds)

if __name__ == "__main__":
    import sys
    sys.exit(main())
