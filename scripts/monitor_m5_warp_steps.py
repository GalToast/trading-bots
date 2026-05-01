"""Looping monitor for M5 Warp step=$100 vs $200 comparison.
Updates reports/m5_warp_step100_vs_step200_comparison.md every 30 seconds.
"""
import json
import time
from pathlib import Path
from datetime import datetime, timezone

from process_singleton import acquire_singleton

REPO = Path(__file__).resolve().parent.parent
STATE_100 = REPO / "reports" / "penetration_lattice_shadow_btcusd_m5_warp_state.json"
STATE_200 = REPO / "reports" / "penetration_lattice_shadow_btcusd_m5_warp_step200_state.json"
OUTPUT = REPO / "reports" / "m5_warp_step100_vs_step200_comparison.md"
HISTORY = REPO / "reports" / "m5_warp_step_comparison_history.jsonl"
LOCK_PATH = REPO / "reports" / "locks" / "monitor_m5_warp_steps.lock"


def load_state(path):
    if not path.exists():
        return None
    try:
        return json.load(open(path))
    except Exception:
        return None


def get_symbol(state):
    if not state:
        return {}
    return state.get("symbols", {}).get("BTCUSD", {})


def main():
    with acquire_singleton(
        LOCK_PATH,
        scope="monitor_m5_warp_steps",
        metadata={"output": str(OUTPUT), "history": str(HISTORY)},
    ) as lease:
        if not lease.acquired:
            print(f"M5 warp step monitor already running (pid={lease.owner_pid})")
            return

        print("M5 Warp Step Comparison Monitor — polling every 30s")
        print("Press Ctrl+C to stop\n")

        while True:
            try:
                s100 = load_state(STATE_100)
                s200 = load_state(STATE_200)
                btc100 = get_symbol(s100)
                btc200 = get_symbol(s200)

                closes100 = btc100.get("realized_closes", 0)
                closes200 = btc200.get("realized_closes", 0)
                net100 = btc100.get("realized_net_usd", 0)
                net200 = btc200.get("realized_net_usd", 0)
                opens100 = len(btc100.get("open_tickets", []))
                opens200 = len(btc200.get("open_tickets", []))
                resets100 = btc100.get("anchor_resets", 0)
                resets200 = btc200.get("anchor_resets", 0)
                per_close100 = net100 / max(closes100, 1)
                per_close200 = net200 / max(closes200, 1)

                ts_100 = s100.get("updated_at", "?")[:19] if s100 else "?"
                ts_200 = s200.get("updated_at", "?")[:19] if s200 else "?"

                now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")

                lines = [
                    "# M5 Warp Step Comparison: $100 vs $200",
                    "",
                    f"**Updated:** {now}",
                    "",
                    "| Metric | Step=$100 | Step=$200 | Advantage |",
                    "|--------|-----------|-----------|-----------|",
                    f"| Closes | {closes100} | {closes200} | {'$100' if closes100 > closes200 else '$200'} |",
                    f"| Net PnL | ${net100:.2f} | ${net200:.2f} | {'$100' if net100 > net200 else '$200'} |",
                    f"| $/close | ${per_close100:.2f} | ${per_close200:.2f} | {'$100' if per_close100 > per_close200 else '$200'} |",
                    f"| Open positions | {opens100} | {opens200} | {'$100' if opens100 < opens200 else '$200'} (fewer=lower risk) |",
                    f"| Anchor resets | {resets100} | {resets200} | {'$100' if resets100 < resets200 else '$200'} (fewer=better) |",
                    f"| Last updated | {ts_100} | {ts_200} | |",
                    "",
                ]

                if closes200 >= 10:
                    ratio = per_close200 / max(per_close100, 0.01)
                    lines.append(
                        f"- **Step=$200 is {ratio:.2f}x more efficient per close** "
                        f"({closes200} closes - statistically meaningful)"
                    )
                elif closes200 > 0:
                    ratio = per_close200 / max(per_close100, 0.01)
                    lines.append(
                        f"- Step=$200 has {closes200} closes, {ratio:.2f}x $/close - accumulating evidence"
                    )
                else:
                    lines.append(f"- Step=$200 has {closes200} closes - waiting for forward evidence")

                lines.append(f"- Step=$100 baseline: {closes100} closes, ${per_close100:.2f}/close, {opens100} open")
                lines.append("")

                with open(OUTPUT, "w", encoding="utf-8") as f:
                    f.write("\n".join(lines))

                history_entry = {
                    "ts": now,
                    "closes_100": closes100,
                    "closes_200": closes200,
                    "net_100": round(net100, 2),
                    "net_200": round(net200, 2),
                    "per_close_100": round(per_close100, 2),
                    "per_close_200": round(per_close200, 2),
                    "opens_100": opens100,
                    "opens_200": opens200,
                    "resets_100": resets100,
                    "resets_200": resets200,
                }
                with open(HISTORY, "a", encoding="utf-8") as f:
                    f.write(json.dumps(history_entry) + "\n")

                print(
                    f"[{now}] $100: {closes100}c/${net100:.2f}/${per_close100:.2f} | "
                    f"$200: {closes200}c/${net200:.2f}/${per_close200:.2f}"
                )

                time.sleep(30)
            except KeyboardInterrupt:
                print("\nMonitor stopped.")
                break
            except Exception as e:
                print(f"Error: {e}")
                time.sleep(30)


if __name__ == "__main__":
    main()
