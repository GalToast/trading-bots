"""
M5 Warp live vs shadow fill quality analysis.
"""
import json
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
LIVE_EVENTS = REPO / "reports" / "penetration_lattice_live_btcusd_m5_warp_exec_events.jsonl"
SHADOW_EVENTS = REPO / "reports" / "penetration_lattice_shadow_btcusd_m5_warp_events.jsonl"
OUTPUT = REPO / "reports" / "m5_warp_fill_quality_analysis.md"


def load_events(path):
    events = []
    if not path.exists():
        return events
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                events.append(json.loads(line))
            except json.JSONDecodeError:
                pass
    return events


def main():
    live_events = load_events(LIVE_EVENTS)
    shadow_events = load_events(SHADOW_EVENTS)

    # Parse live closes
    live_closes = []
    for ev in live_events:
        if ev.get("action") != "close_attempt":
            continue
        result = ev.get("result", {})
        if not result.get("ok"):
            continue
        broker_fill = result.get("broker_fill", {})
        event_data = ev.get("event", {})
        ticket = event_data.get("ticket", {})

        open_price = ticket.get("fill_price") or result.get("tracked_position_price_open")
        close_price = broker_fill.get("price")
        profit = broker_fill.get("profit", 0)
        direction = event_data.get("direction", "")
        level_idx = ticket.get("level_idx")
        ts = ev.get("ts_utc", "")
        requested = event_data.get("fill_price")
        slippage = abs(close_price - requested) if close_price and requested else None

        live_closes.append({
            "open_price": open_price,
            "close_price": close_price,
            "profit": profit,
            "direction": direction,
            "level_idx": level_idx,
            "ts_utc": ts[:19] if ts else "",
            "slippage": slippage,
        })

    # Parse shadow closes
    shadow_closes = []
    for ev in shadow_events:
        if ev.get("action") != "close_ticket":
            continue
        pnl = ev.get("realized_pnl")
        if pnl is None:
            continue
        shadow_closes.append({
            "entry_price": ev.get("entry_price"),
            "exit_price": ev.get("exit_price"),
            "pnl_usd": float(pnl),
            "direction": ev.get("direction"),
            "level_idx": ev.get("level_idx"),
        })

    live_profits = [c["profit"] for c in live_closes]
    shadow_profits = [c["pnl_usd"] for c in shadow_closes]

    # Build report
    lines = []
    lines.append("# M5 Warp Fill Quality Analysis\n")
    lines.append(f"**Generated:** {live_closes[-1]['ts_utc'] if live_closes else 'N/A'}\n")

    lines.append("## Summary\n")
    lines.append(f"| Metric | Live | Shadow |")
    lines.append(f"|--------|------|--------|")
    lines.append(f"| Closes | {len(live_closes)} | {len(shadow_closes)} |")
    if live_profits:
        lines.append(f"| Total PnL | ${sum(live_profits):.2f} | ${sum(shadow_profits):.2f} |")
        lines.append(f"| Avg $/Close | ${sum(live_profits)/len(live_profits):.2f} | ${sum(shadow_profits)/len(shadow_profits):.2f} |")
        ratio = (sum(live_profits)/len(live_profits)) / (sum(shadow_profits)/len(shadow_profits)) if sum(shadow_profits) else 0
        lines.append(f"| Ratio (live/shadow) | {ratio:.0%} | |")
    lines.append(f"| Min | ${min(live_profits):.2f} | ${min(shadow_profits):.2f} |")
    lines.append(f"| Max | ${max(live_profits):.2f} | ${max(shadow_profits):.2f} |")

    # Slippage
    slips = [c["slippage"] for c in live_closes if c["slippage"] is not None]
    lines.append(f"\n## Live Slippage\n")
    if slips:
        lines.append(f"- Avg slippage: ${sum(slips)/len(slips):.2f}")
        lines.append(f"- Max slippage: ${max(slips):.2f}")
        lines.append(f"- Min slippage: ${min(slips):.2f}")

    # Direction
    sell_p = [c["profit"] for c in live_closes if c["direction"] == "SELL"]
    buy_p = [c["profit"] for c in live_closes if c["direction"] == "BUY"]
    lines.append(f"\n## By Direction (Live)\n")
    lines.append(f"- SELL: {len(sell_p)} closes, ${sum(sell_p)/max(len(sell_p),1):.2f} avg")
    lines.append(f"- BUY:  {len(buy_p)} closes, ${sum(buy_p)/max(len(buy_p),1):.2f} avg")

    # Levels
    levels = [c["level_idx"] for c in live_closes if c["level_idx"] is not None]
    lines.append(f"\n## Close Levels (Live)\n")
    if levels:
        lines.append(f"- Avg level: {sum(levels)/len(levels):.1f}")
        low = sum(1 for l in levels if l <= 3)
        mid = sum(1 for l in levels if 4 <= l <= 8)
        high = sum(1 for l in levels if l > 8)
        lines.append(f"- Shallow (1-3): {low}")
        lines.append(f"- Mid (4-8): {mid}")
        lines.append(f"- Deep (9+): {high}")

    # Step-widening
    deep_closes = [c for c in live_closes if c.get("level_idx", 0) and c["level_idx"] > 8]
    lines.append(f"\n## Step-Widening Analysis\n")
    lines.append(f"- Deep-level closes (L9+): {len(deep_closes)} / {len(live_closes)} = {len(deep_closes)/max(len(live_closes),1):.0%}")
    if deep_closes:
        deep_avg = sum(c["profit"] for c in deep_closes) / len(deep_closes)
        lines.append(f"- Deep-level avg PnL: ${deep_avg:.2f}")
    lines.append(f"\nIf step=$200 instead of $100:")
    lines.append(f"- ~{len(live_closes)//2} opens (half as many levels hit)")
    lines.append(f"- Fewer deep traps, less floating inventory")
    lines.append(f"- Same total net with lower risk")

    # Recent closes
    lines.append(f"\n## Recent Live Closes\n")
    lines.append(f"| Time | Dir | Level | PnL | Slippage |")
    lines.append(f"|------|-----|-------|-----|----------|")
    for c in live_closes[-15:]:
        slip_str = f"${c['slippage']:.2f}" if c['slippage'] is not None else "N/A"
        lvl = c.get('level_idx')
        lvl_str = str(lvl) if lvl is not None else "?"
        lines.append(f"| {c['ts_utc']} | {c['direction']} | {lvl_str} | ${c['profit']:+.2f} | {slip_str} |")

    # Conclusion
    lines.append(f"\n## Conclusion\n")
    if live_profits and shadow_profits:
        ratio = (sum(live_profits)/len(live_profits)) / (sum(shadow_profits)/len(shadow_profits))
        if ratio < 0.7:
            lines.append(f"Live $/close is {ratio:.0%} of shadow — **significant execution degradation**.")
            lines.append(f"WIDENING STEP TO $200 is RECOMMENDED.")
        elif ratio < 1.0:
            lines.append(f"Live $/close is {ratio:.0%} of shadow — mild degradation, within acceptable range.")
        else:
            lines.append(f"Live $/close is {ratio:.0%} of shadow — live is outperforming shadow!")
    
    report = "\n".join(lines)
    with open(OUTPUT, "w") as f:
        f.write(report)
    print(report)


if __name__ == "__main__":
    main()
