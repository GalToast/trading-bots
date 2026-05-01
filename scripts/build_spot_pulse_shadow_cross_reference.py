#!/usr/bin/env python3
"""Cross-reference Coinbase spot pulse board, shadow forensics, and hot capital router.

Finds:
1. Hot-pulse products with NO shadow lane (missed shadow candidates)
2. Existing shadow lanes that are negative despite hot pulse (kill candidates)
3. Products where pulse score + shadow forensics align for promotion review

Reads:
- reports/coinbase_spot_pulse_board.json (or .md fallback)
- reports/coinbase_spot_shadow_trade_forensics.md
- reports/coinbase_spot_hot_capital_router.md

Writes:
- reports/coinbase_spot_pulse_shadow_cross_reference.md
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
REPORTS = ROOT / "reports"

PULSE_MD = REPORTS / "coinbase_spot_pulse_board.md"
FORENSICS_MD = REPORTS / "coinbase_spot_shadow_trade_forensics.md"
ROUTER_MD = REPORTS / "coinbase_spot_hot_capital_router.md"
OUTPUT_MD = REPORTS / "coinbase_spot_pulse_shadow_cross_reference.md"


def parse_pulse_products(md_path: Path) -> dict[str, dict]:
    """Parse hot/pulse rows from the pulse board."""
    products = {}
    text = md_path.read_text(encoding="utf-8")
    # Find the "Hot momentum rows" or "Top Pulse Rows" table
    in_table = False
    header_map = {}
    for line in text.splitlines():
        if line.startswith("| Product") and "Quote" in line:
            in_table = True
            cols = [c.strip() for c in line.strip().strip("|").split("|")]
            header_map = {c: i for i, c in enumerate(cols)}
            continue
        if in_table:
            if not line.startswith("|"):
                in_table = False
                continue
            if re.match(r"\|\s*[-:]+", line):
                continue
            cells = [c.strip() for c in line.strip().strip("|").split("|")]
            if len(cells) < 3:
                continue
            product = cells[header_map.get("Product", 0)]
            state = cells[header_map.get("State", 5)]
            score = float(cells[header_map.get("Score", 7)])
            spread_bps = float(cells[header_map.get("Spread bps", 9)])
            candles = int(cells[header_map.get("Candles", 12)])
            products[product] = {
                "state": state,
                "score": score,
                "spread_bps": spread_bps,
                "candles": candles,
            }
    return products


def parse_shadow_forensics(md_path: Path) -> dict[str, dict]:
    """Parse lane summary from shadow trade forensics."""
    lanes = {}
    text = md_path.read_text(encoding="utf-8")
    in_table = False
    header_map = {}
    for line in text.splitlines():
        if line.startswith("| Product") and "Family" in line:
            in_table = True
            cols = [c.strip() for c in line.strip().strip("|").split("|")]
            header_map = {c: i for i, c in enumerate(cols)}
            continue
        if in_table:
            if not line.startswith("|"):
                in_table = False
                continue
            if re.match(r"\|\s*[-:]+", line):
                continue
            cells = [c.strip() for c in line.strip().strip("|").split("|")]
            if len(cells) < 3:
                continue
            product = cells[header_map.get("Product", 0)]
            state_net = float(cells[header_map.get("State Net $", 3)])
            state_closes = int(cells[header_map.get("State Closes", 4)])
            all_net = float(cells[header_map.get("All Net $", 5)])
            all_closes = int(cells[header_map.get("All Closes", 6)])
            wr = float(cells[header_map.get("WR %", 7)])
            fees = float(cells[header_map.get("Fees $", 9)])
            avg_net = float(cells[header_map.get("Avg Net $", 10)])
            open_pos = int(cells[header_map.get("Open", 12)])
            lanes[product] = {
                "state_net": state_net,
                "state_closes": state_closes,
                "all_net": all_net,
                "all_closes": all_closes,
                "wr": wr,
                "fees": fees,
                "avg_net": avg_net,
                "open_pos": open_pos,
            }
    return lanes


def parse_router_hot_rows(md_path: Path) -> dict[str, dict]:
    """Parse hot rows from hot capital router."""
    rows = {}
    text = md_path.read_text(encoding="utf-8")
    in_table = False
    header_map = {}
    # Find the "Hot Rows" table
    for line in text.splitlines():
        if line.startswith("| Product") and "Family" in line and "Lane" in line:
            in_table = True
            cols = [c.strip() for c in line.strip().strip("|").split("|")]
            header_map = {c: i for i, c in enumerate(cols)}
            continue
        if in_table:
            if not line.startswith("|"):
                in_table = False
                continue
            if re.match(r"\|\s*[-:]+", line):
                continue
            cells = [c.strip() for c in line.strip().strip("|").split("|")]
            if len(cells) < 3:
                continue
            product = cells[header_map.get("Product", 0)]
            alloc_state = cells[header_map.get("Allocation State", 4)]
            score = float(cells[header_map.get("Score", 5)])
            realized = float(cells[header_map.get("Realized $", 6)])
            closes = int(cells[header_map.get("Closes", 8)])
            rows[product] = {
                "alloc_state": alloc_state,
                "score": score,
                "realized": realized,
                "closes": closes,
            }
    return rows


def main():
    pulse = parse_pulse_products(PULSE_MD)
    forensics = parse_shadow_forensics(FORENSICS_MD)
    router = parse_router_hot_rows(ROUTER_MD)

    all_pulse_products = set(pulse.keys())
    all_shadow_products = set(forensics.keys())
    all_router_products = set(router.keys())

    # 1. Hot-pulse products with NO shadow lane
    pulse_no_shadow = {p for p in all_pulse_products if p not in all_shadow_products}

    # 2. Shadow lanes that are negative despite being eligible_shadow_hot
    negative_hot = []
    for p in all_shadow_products:
        f = forensics[p]
        r = router.get(p, {})
        if r.get("alloc_state") == "eligible_shadow_hot" and f["state_net"] < 0:
            negative_hot.append(p)

    # 3. Products where pulse score + shadow forensics align
    aligned = []
    for p in all_pulse_products & all_shadow_products:
        pulse_score = pulse[p]["score"]
        state_net = forensics[p]["state_net"]
        closes = forensics[p]["state_closes"]
        spread_bps = pulse[p]["spread_bps"]
        # Alignment: hot_momentum pulse + positive state net + enough closes (>30) + reasonable spread (<200bps)
        state = pulse[p]["state"]
        is_aligned = (
            state == "hot_momentum"
            and state_net > 0
            and closes >= 30
            and spread_bps < 200
        )
        if is_aligned:
            aligned.append((p, pulse_score, state_net, closes, spread_bps))
    aligned.sort(key=lambda x: x[1], reverse=True)

    # 4. Shadow lanes running but product NOT currently hot on pulse
    shadow_not_hot = []
    for p in all_shadow_products:
        if p not in all_pulse_products:
            shadow_not_hot.append(p)
        elif pulse[p]["state"] not in ("hot_momentum", "warming"):
            shadow_not_hot.append(p)

    # Build report
    lines = [
        "# Coinbase Spot Pulse × Shadow Cross-Reference",
        "",
        "## Leadership Read",
        "",
        "- Cross-references pulse board momentum scores with shadow trade forensics and hot capital router allocation states.",
        "- Use this to find: (1) hot products missing shadow lanes, (2) negative shadow lanes that should be killed, (3) strongest promotion candidates.",
        "- Generated: auto-refreshed from current report surfaces.",
        "",
    ]

    # Section 1: Hot-pulse products without shadow lanes
    lines.append("## Hot-Pulse Products Missing Shadow Lanes")
    lines.append("")
    if pulse_no_shadow:
        lines.append(f"**{len(pulse_no_shadow)} products** are hot on pulse but have no shadow lane running.")
        lines.append("")
        lines.append("| Product | State | Pulse Score | Spread bps | Candles | Recommendation |")
        lines.append("| --- | --- | ---: | ---: | ---: | --- |")
        for p in sorted(pulse_no_shadow, key=lambda x: pulse[x]["score"], reverse=True):
            pp = pulse[p]
            rec = "shadow_candidate" if pp["spread_bps"] < 200 else "watch_high_spread"
            lines.append(f"| {p} | {pp['state']} | {pp['score']:.4f} | {pp['spread_bps']:.2f} | {pp['candles']} | {rec} |")
        lines.append("")
    else:
        lines.append("All hot-pulse products have shadow lanes. ✅")
        lines.append("")

    # Section 2: Negative shadow lanes (eligible_shadow_hot)
    lines.append("## Negative Shadow Lanes (eligible_shadow_hot — Kill Candidates)")
    lines.append("")
    if negative_hot:
        for p in negative_hot:
            f = forensics[p]
            r = router[p]
            lines.append(f"- **{p}**: state_net=${f['state_net']:.4f} across {f['state_closes']} closes, WR={f['wr']:.1f}%, fees=${f['fees']:.4f}. Router score={r['score']:.4f}. **Recommendation: demote to reject_negative or kill.**")
        lines.append("")
    else:
        lines.append("No eligible_shadow_hot lanes are currently negative. ✅")
        lines.append("")

    # Section 3: Aligned promotion candidates
    lines.append("## Aligned Promotion Candidates (hot pulse + positive shadow)")
    lines.append("")
    if aligned:
        lines.append("Products where momentum pulse score, shadow P/L, closes, and spread all align:")
        lines.append("")
        lines.append("| Product | Pulse Score | State Net $ | Closes | Spread bps | WR % |")
        lines.append("| --- | ---: | ---: | ---: | ---: | ---: |")
        for p, score, net, closes, spread in aligned:
            f = forensics[p]
            lines.append(f"| {p} | {score:.4f} | {net:.4f} | {closes} | {spread:.2f} | {f['wr']:.1f} |")
        lines.append("")
    else:
        lines.append("No products meet all alignment criteria.")
        lines.append("")

    # Section 4: Shadow lanes on cold/chop products
    lines.append("## Shadow Lanes on Cold/Chop Products (Monitor)")
    lines.append("")
    if shadow_not_hot:
        lines.append(f"**{len(shadow_not_hot)} shadow lanes** are running on products that are not currently hot or warming on the pulse board:")
        lines.append("")
        lines.append("| Product | Pulse State | State Net $ | Closes | WR % |")
        lines.append("| --- | --- | ---: | ---: | ---: |")
        for p in sorted(shadow_not_hot):
            f = forensics[p]
            pp = pulse.get(p, {})
            pstate = pp.get("state", "not_on_pulse_board")
            lines.append(f"| {p} | {pstate} | {f['state_net']:.4f} | {f['state_closes']} | {f['wr']:.1f} |")
        lines.append("")
    else:
        lines.append("All active shadow lanes are on hot or warming products. ✅")
        lines.append("")

    # Section 5: Summary
    lines.append("## Summary")
    lines.append("")
    lines.append(f"- Pulse products scanned: {len(all_pulse_products)}")
    lines.append(f"- Shadow lanes active: {len(all_shadow_products)}")
    lines.append(f"- Hot-pulse products without shadow: {len(pulse_no_shadow)}")
    lines.append(f"- Negative shadow lanes (hot): {len(negative_hot)}")
    lines.append(f"- Aligned promotion candidates: {len(aligned)}")
    lines.append(f"- Shadow lanes on cold products: {len(shadow_not_hot)}")
    lines.append("")

    report = "\n".join(lines)
    OUTPUT_MD.write_text(report, encoding="utf-8")
    print(f"Wrote {OUTPUT_MD}")
    print(report)


if __name__ == "__main__":
    main()
