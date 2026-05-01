#!/usr/bin/env python3
"""
Anchor Latency Analysis — measures first-entry latency and hold times
for the FX rearm lanes.

Analyzes event logs to understand:
1. How long between signal and first entry (anchor latency)
2. Hold time distribution per symbol
3. Whether anchor latency cascades into rearm delays

Usage:
    python scripts/anchor_latency_analysis.py
"""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
EVENT_LOGS = [
    ROOT / "reports" / "direct_live_cutover_backup_20260410-073614" / "penetration_lattice_live_source_events.jsonl",
    ROOT / "reports" / "penetration_lattice_live_btcusd_exc2_tight_exec_events.jsonl",
]

M15_BAR_SECONDS = 900  # M15 bars
H1_BAR_SECONDS = 3600  # H1 bars


def analyze_log(path, bar_seconds=M15_BAR_SECONDS):
    if not path.exists():
        return None

    opens = []
    closes = []
    fresh_start = None

    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            evt = json.loads(line)
        except Exception:
            continue
        action = evt.get("action", "")
        if action == "fresh_start_prime":
            fresh_start = evt
        elif action == "open_ticket":
            opens.append(evt)
        elif action == "close_ticket":
            closes.append(evt)

    if not opens:
        return None

    # First open per symbol
    first_opens = {}
    for o in opens:
        sym = o["symbol"]
        if sym not in first_opens or o["bar_time"] < first_opens[sym]["bar_time"]:
            first_opens[sym] = o

    # Hold time analysis (match opens to closes by entry price)
    hold_times_by_symbol = {}
    for c in closes:
        sym = c["symbol"]
        entry_price = c.get("entry_price", 0)
        if sym not in hold_times_by_symbol:
            hold_times_by_symbol[sym] = []
        # Find matching open
        matched = False
        for o in opens:
            if o["symbol"] == sym and abs(o["entry_price"] - entry_price) < 0.0001:
                hold_bars = (c["bar_time"] - o["bar_time"]) / bar_seconds
                hold_minutes = (c["bar_time"] - o["bar_time"]) / 60
                hold_times_by_symbol[sym].append({
                    "hold_bars": round(hold_bars, 1),
                    "hold_minutes": round(hold_minutes, 1),
                    "pnl": c.get("realized_pnl", 0),
                    "direction": o["direction"],
                })
                matched = True
                break
        if not matched:
            hold_times_by_symbol[sym].append({
                "hold_bars": None,
                "hold_minutes": None,
                "pnl": c.get("realized_pnl", 0),
                "direction": c.get("direction", "?"),
            })

    return {
        "file": str(path.name),
        "fresh_start": fresh_start,
        "total_opens": len(opens),
        "total_closes": len(closes),
        "first_opens": {sym: {"direction": o["direction"], "entry": o["entry_price"],
                              "bar_time": o["bar_time"], "ts": o["ts_utc"], "mode": o["mode"]}
                        for sym, o in first_opens.items()},
        "hold_times": hold_times_by_symbol,
    }


def main():
    print("=" * 70)
    print("  ANCHOR LATENCY ANALYSIS")
    print("=" * 70)

    all_results = {}
    for path in EVENT_LOGS:
        result = analyze_log(path)
        if result:
            all_results[result["file"]] = result

    for fname, r in all_results.items():
        print(f"\n{'─' * 50}")
        print(f"  {fname}")
        print(f"  Opens: {r['total_opens']}, Closes: {r['total_closes']}")
        if r["fresh_start"]:
            print(f"  Started: {r['fresh_start']['ts_utc']}")

        print(f"\n  First entry (anchor) per symbol:")
        for sym, fo in sorted(r["first_opens"].items()):
            print(f"    {sym}: {fo['direction']} @ {fo['entry']} mode={fo['mode']}")
            print(f"      bar_time={fo['bar_time']} ts={fo['ts']}")

        print(f"\n  Hold time distribution:")
        for sym, times in sorted(r["hold_times"].items()):
            valid = [t for t in times if t["hold_bars"] is not None]
            if valid:
                avg_bars = sum(t["hold_bars"] for t in valid) / len(valid)
                avg_min = sum(t["hold_minutes"] for t in valid) / len(valid)
                avg_pnl = sum(t["pnl"] for t in valid) / len(valid)
                mn = min(t["hold_bars"] for t in valid)
                mx = max(t["hold_bars"] for t in valid)
                print(f"    {sym}: avg={avg_bars:.1f} bars ({avg_min:.1f}m), "
                      f"range=[{mn},{mx}], n={len(valid)}, avg_pnl=${avg_pnl:.2f}")
            else:
                print(f"    {sym}: no matched holds")

    # Save results
    output_path = ROOT / "reports" / "anchor_latency_analysis.json"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(all_results, indent=2), encoding="utf-8")
    print(f"\n  Results saved to: {output_path}")


if __name__ == "__main__":
    main()
