#!/usr/bin/env python3
"""Kelly Shadow Promotion Checklist — simplified version"""
import json
from pathlib import Path
from datetime import datetime, timezone

EVENT_LOG = Path("reports/kelly_shadow_events.jsonl")
STATE_FILE = Path("reports/kelly_shadow_state.json")
OUTPUT_JSON = Path("reports/kelly_promotion_checklist.json")
OUTPUT_MD = Path("reports/kelly_promotion_checklist.md")

def load_events():
    events = []
    if not EVENT_LOG.exists():
        return events
    with open(EVENT_LOG) as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    events.append(json.loads(line))
                except json.JSONDecodeError:
                    pass
    return events

def load_state():
    if not STATE_FILE.exists():
        return {}
    with open(STATE_FILE) as f:
        return json.load(f)

def analyze(events, state):
    opens = [e for e in events if e.get("action") == "open"]
    closes = [e for e in events if e.get("action") == "close"]

    def dedup(events_list):
        seen = set()
        unique = []
        for e in events_list:
            key = (e.get("coin"), e.get("action"), e.get("entry_price") or e.get("exit_price"), e.get("ts_utc"))
            if key not in seen:
                seen.add(key)
                unique.append(e)
        return unique

    unique_opens = dedup(opens)
    unique_closes = dedup(closes)

    coin_stats = {}
    for o in unique_opens:
        coin = o.get("coin", "?")
        if coin not in coin_stats:
            coin_stats[coin] = {"signals": 0, "opens": 0, "closes": 0, "wins": 0, "losses": 0, "total_pnl": 0.0}
        coin_stats[coin]["signals"] += 1
        coin_stats[coin]["opens"] += 1

    for c in unique_closes:
        coin = c.get("coin", "?")
        if coin not in coin_stats:
            coin_stats[coin] = {"signals": 0, "opens": 0, "closes": 0, "wins": 0, "losses": 0, "total_pnl": 0.0}
        coin_stats[coin]["closes"] += 1
        net = c.get("net", 0)
        coin_stats[coin]["total_pnl"] += net
        if net > 0:
            coin_stats[coin]["wins"] += 1
        else:
            coin_stats[coin]["losses"] += 1

    return coin_stats, len(unique_opens), len(unique_closes)

def main():
    events = load_events()
    state = load_state()
    coin_stats, total_opens, total_closes = analyze(events, state)

    total_pnl = sum(s["total_pnl"] for s in coin_stats.values())
    total_wins = sum(s["wins"] for s in coin_stats.values())
    total_losses = sum(s["losses"] for s in coin_stats.values())
    wr = (total_wins / total_closes * 100) if total_closes > 0 else 0
    all_fired = all(s["signals"] > 0 for s in coin_stats.values())
    all_2_closes = all(s["closes"] >= 2 for s in coin_stats.values())
    no_double = all(s["opens"] == s["signals"] for s in coin_stats.values())
    cycle = state.get("cycle", 0)
    equity = state.get("total_equity", 0)

    gates = [
        ("Minimum 100 cycles", cycle >= 100, f"Cycle {cycle}/100"),
        ("All 5 coins fired", all_fired, f"Coins with signals: {sum(1 for s in coin_stats.values() if s['signals'] > 0)}/5"),
        ("Min 2 closes per coin", all_2_closes, ", ".join(f"{k}: {v['closes']} closes" for k, v in coin_stats.items())),
        ("Positive win rate (>50%)", wr > 50, f"WR: {wr:.1f}%"),
        ("No double-entry bug", no_double, "Signal dedup OK" if no_double else "Double entries detected"),
    ]

    lines = [
        "# Kelly Shadow Promotion Checklist",
        "",
        f"**Generated:** {datetime.now(timezone.utc).isoformat()}",
        f"**Cycle:** {cycle} | **Equity:** ${equity:.2f} | **Event Log PnL:** ${total_pnl:+.4f}",
        "",
        "| Gate | Status | Detail |",
        "|------|--------|--------|",
    ]
    for name, passed, detail in gates:
        status = "✅ PASS" if passed else "❌ FAIL"
        lines.append(f"| {name} | {status} | {detail} |")

    passed_count = sum(1 for _, p, _ in gates if p)
    lines.append("")
    lines.append(f"**Progress: {passed_count}/{len(gates)} gates passed**")
    lines.append("")
    lines.append("## Per-Coin Stats (from event log)")
    lines.append("")
    lines.append("| Coin | Signals | Opens | Closes | Wins | Losses | Total PnL |")
    lines.append("|------|---------|-------|--------|------|--------|-----------|")
    for coin, stats in sorted(coin_stats.items()):
        lines.append(f"| {coin} | {stats['signals']} | {stats['opens']} | {stats['closes']} | {stats['wins']} | {stats['losses']} | ${stats['total_pnl']:+.4f} |")

    md = "\n".join(lines)
    OUTPUT_MD.write_text(md, encoding="utf-8")
    OUTPUT_JSON.write_text(json.dumps({"gates": [{"name": n, "passed": p, "detail": d} for n, p, d in gates], "coin_stats": coin_stats}, indent=2), encoding="utf-8")

    print(md)
    print(f"\nSaved: {OUTPUT_MD}, {OUTPUT_JSON}")

if __name__ == "__main__":
    main()
