#!/usr/bin/env python3
"""Monitor for FX M15 Micro first new-bar forward close.

Watches all 3 FX M15 Micro bar-level shadow runners.
Alerts on switchboard when the first NEW forward close occurs
(after bootstrap). This is the validation milestone.

Usage: python scripts/watch_fx_m15_first_close.py [--poll-seconds 30]
"""
import json
import time
from pathlib import Path
from datetime import datetime, timezone

ROOT = Path(__file__).resolve().parent.parent
ALERT_PATH = ROOT / "reports" / "fx_m15_first_close_alert.json"

SYMBOLS = ["gbpusd", "eurusd", "nzdusd"]
BOOTSTRAP_CLOSES = {"gbpusd": 3978, "eurusd": 3559, "nzdusd": 2626}


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def get_close_count(sym: str) -> int:
    state_path = ROOT / "reports" / f"shadow_fx_m15_micro_{sym}_bar_state.json"
    if not state_path.exists():
        return -1
    d = json.loads(state_path.read_text(encoding="utf-8"))
    return d.get("realized_closes", 0)


def check():
    """Check for new forward closes and alert."""
    alerts = []
    for sym in SYMBOLS:
        current = get_close_count(sym)
        bootstrap = BOOTSTRAP_CLOSES[sym]
        new_closes = current - bootstrap
        if new_closes > 0:
            alerts.append({
                "symbol": sym.upper(),
                "bootstrap_closes": bootstrap,
                "current_closes": current,
                "new_forward_closes": new_closes,
            })
    return alerts


def main():
    poll_seconds = 30
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--poll-seconds", type=int, default=30)
    args = parser.parse_args()
    poll_seconds = args.poll_seconds

    print(f"FX M15 Micro First-Close Monitor — polling every {poll_seconds}s")
    print(f"Bootstrap: GBPUSD={BOOTSTRAP_CLOSES['gbpusd']}, EURUSD={BOOTSTRAP_CLOSES['eurusd']}, NZDUSD={BOOTSTRAP_CLOSES['nzdusd']}")
    print(f"Waiting for first NEW forward close (beyond bootstrap)...")

    seen_forward = set()
    cycle = 0

    while True:
        cycle += 1
        alerts = check()

        if alerts:
            for a in alerts:
                key = f"{a['symbol']}_{a['new_forward_closes']}"
                if key not in seen_forward:
                    seen_forward.add(key)
                    msg = (f"🎉 FX M15 Micro FORWARD CLOSE MILESTONE! "
                           f"{a['symbol']}: {a['new_forward_closes']} new forward close(s)! "
                           f"Total: {a['current_closes']} (bootstrap: {a['bootstrap_closes']}). "
                           f"Forward validation accumulating!")
                    print(f"\n{msg}")

                    # Write alert file
                    alert = {
                        "ts_utc": utc_now_iso(),
                        "symbol": a["symbol"],
                        "bootstrap_closes": a["bootstrap_closes"],
                        "current_closes": a["current_closes"],
                        "new_forward_closes": a["new_forward_closes"],
                        "message": msg,
                    }
                    ALERT_PATH.write_text(json.dumps(alert, indent=2) + "\n", encoding="utf-8")
        else:
            # Quick status
            status = []
            for sym in SYMBOLS:
                current = get_close_count(sym)
                bootstrap = BOOTSTRAP_CLOSES[sym]
                new_closes = current - bootstrap
                status.append(f"{sym.upper()}: {current}c (bootstrap={bootstrap}, new={new_closes})")
            if cycle % 10 == 0:
                print(f"  [{utc_now_iso()}] {', '.join(status)}")

        time.sleep(poll_seconds)


if __name__ == "__main__":
    raise SystemExit(main())
