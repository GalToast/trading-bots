"""Compare M5 Warp step=$100 vs step=$200 shadows in real-time."""
import json
import time
from pathlib import Path
from datetime import datetime, timezone

REPO = Path(__file__).resolve().parent.parent
STATE_100 = REPO / "reports" / "penetration_lattice_shadow_btcusd_m5_warp_state.json"
STATE_200 = REPO / "reports" / "penetration_lattice_shadow_btcusd_m5_warp_step200_state.json"
OUTPUT = REPO / "reports" / "m5_warp_step100_vs_step200_comparison.md"


def load_state(path):
    if not path.exists():
        return None
    try:
        return json.load(open(path))
    except (json.JSONDecodeError, Exception):
        return None


def get_symbol(state):
    if not state:
        return {}
    symbols = state.get("symbols", {})
    return symbols.get("BTCUSD", {})


def main():
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

    ts_100 = s100.get("updated_at", "?") if s100 else "?"
    ts_200 = s200.get("updated_at", "?") if s200 else "?"

    print(f"M5 Warp Step Comparison — {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}")
    print(f"{'='*70}")
    print(f"| Metric | Step=$100 | Step=$200 | Advantage |")
    print(f"|--------|-----------|-----------|-----------|")
    print(f"| Closes | {closes100} | {closes200} | {'$100' if closes100 > closes200 else '$200'} |")
    print(f"| Net PnL | ${net100:.2f} | ${net200:.2f} | {'$100' if net100 > net200 else '$200'} |")
    print(f"| $/close | ${per_close100:.2f} | ${per_close200:.2f} | {'$100' if per_close100 > per_close200 else '$200'} |")
    print(f"| Opens | {opens100} | {opens200} | {'$100' if opens100 < opens200 else '$200'} (fewer=better) |")
    print(f"| Resets | {resets100} | {resets200} | {'$100' if resets100 < resets200 else '$200'} (fewer=better) |")
    print(f"| Updated | {ts_100[:19]} | {ts_200[:19]} | |")

    # Write report
    lines = [
        f"# M5 Warp Step Comparison: $100 vs $200",
        f"",
        f"**Updated:** {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}",
        f"",
        f"| Metric | Step=$100 | Step=$200 | Advantage |",
        f"|--------|-----------|-----------|-----------|",
        f"| Closes | {closes100} | {closes200} | {'$100' if closes100 > closes200 else '$200'} |",
        f"| Net PnL | ${net100:.2f} | ${net200:.2f} | {'$100' if net100 > net200 else '$200'} |",
        f"| $/close | ${per_close100:.2f} | ${per_close200:.2f} | {'$100' if per_close100 > per_close200 else '$200'} |",
        f"| Open positions | {opens100} | {opens200} | {'$100' if opens100 < opens200 else '$200'} (fewer=lower risk) |",
        f"| Anchor resets | {resets100} | {resets200} | {'$100' if resets100 < resets200 else '$200'} (fewer=better) |",
        f"| Last updated | {ts_100[:19]} | {ts_200[:19]} | |",
        f"",
        f"## Interpretation",
        f"",
    ]

    if closes200 >= 10:
        ratio = per_close200 / max(per_close100, 0.01)
        lines.append(f"- Step=$200 is {ratio:.2f}x more efficient per close than step=$100")
        lines.append(f"- With {closes200} closes, the forward evidence is meaningful")
    elif closes200 > 0:
        lines.append(f"- Step=$200 has {closes200} closes so far — too few for statistical significance")
        lines.append(f"- Check back when both have 20+ closes")
    else:
        lines.append(f"- Step=$200 has {closes200} closes — waiting for forward evidence")

    lines.append(f"- Step=$100 has {closes100} closes, ${per_close100:.2f}/close")
    lines.append(f"- Step=$200 has {closes200} closes, ${per_close200:.2f}/close")
    lines.append(f"")

    with open(OUTPUT, "w") as f:
        f.write("\n".join(lines))

    print(f"\nReport: {OUTPUT}")


if __name__ == "__main__":
    main()
