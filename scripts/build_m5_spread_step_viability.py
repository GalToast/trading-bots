#!/usr/bin/env python3
"""Spread/Step viability audit for ALL M5 Warp lanes.

Determines which symbols are structurally viable for M5 Warp on this broker
based on spread/step ratio.

Thresholds:
  < 15%: Excellent (BTC M5 territory)
  15-30%: Good (FX-friendly)
  30-60%: Marginal (needs coefficient tuning)
  60-100%: Bad (spread blocks fills, causes resets)
  > 100%: Unfixable (spread wider than step — SOL/XRP territory)

Usage: python scripts/build_m5_spread_step_viability.py
Output: reports/m5_warp_spread_step_viability.md
"""

import json
import os
from datetime import datetime, timezone

REPORTS = os.path.join(os.path.dirname(os.path.dirname(__file__)), "reports")


def load_portfolio():
    path = os.path.join(REPORTS, "live_m5_portfolio_board.json")
    if not os.path.exists(path):
        return {}
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def viability_label(ratio):
    pct = ratio * 100
    if pct < 15:
        return "Excellent"
    elif pct < 30:
        return "Good"
    elif pct < 60:
        return "Marginal"
    elif pct < 100:
        return "Bad"
    else:
        return "Unfixable"


def viability_status(ratio):
    pct = ratio * 100
    if pct < 30:
        return "viable"
    elif pct < 100:
        return "degraded"
    else:
        return "non-viable"


def main():
    data = load_portfolio()
    rows = data.get("expansion_watch_rows", []) + data.get("rows", [])
    
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M UTC")
    
    results = []
    seen = set()
    
    for r in rows:
        sym = r.get("symbol", "?")
        if sym in seen:
            continue
        seen.add(sym)
        
        bid = r.get("quote_bid", 0)
        ask = r.get("quote_ask", 0)
        step = r.get("runtime_step", r.get("configured_step", 0))
        spread = ask - bid if ask and bid else 0
        ratio = spread / step if step > 0 else 0
        
        lane = r.get("lane", "?")
        kind = r.get("kind", "?")
        closes = r.get("realized_closes", 0)
        pnl = r.get("realized_net_usd", 0) or 0
        resets = r.get("anchor_resets", 0)
        
        results.append({
            "symbol": sym,
            "lane": lane,
            "kind": kind,
            "spread": spread,
            "step": step,
            "ratio": ratio,
            "ratio_pct": ratio * 100,
            "label": viability_label(ratio),
            "status": viability_status(ratio),
            "closes": closes,
            "net_usd": pnl,
            "resets": resets,
        })
    
    # Sort by spread/step ratio ascending
    results.sort(key=lambda x: x["ratio"])
    
    lines = [
        f"# M5 Warp Spread/Step Viability Audit — {now}",
        "",
        "Symbols ranked by spread/step ratio. Lower is better.",
        "Thresholds: <15% Excellent, 15-30% Good, 30-60% Marginal, 60-100% Bad, >100% Unfixable",
        "",
        "| Rank | Symbol | Spread | Step | Ratio | Label | Closes | Net $ | Resets | Status |",
        "|------|--------|--------|------|-------|-------|--------|-------|--------|--------|",
    ]
    
    for i, r in enumerate(results, 1):
        sym_label = "LIVE" if r["kind"].startswith("live") else "shadow"
        lines.append(
            f"| {i} | {r['symbol']} ({sym_label}) | {r['spread']:.6f} | {r['step']:.6f} | "
            f"{r['ratio_pct']:.1f}% | {r['label']} | {r['closes']} | ${r['net_usd']:+.2f} | "
            f"{r['resets']} | {r['status']} |"
        )
    
    lines.append("")
    lines.append("## Viable Symbols (spread/step < 30%)")
    viable = [r for r in results if r["status"] == "viable"]
    if viable:
        for r in viable:
            lines.append(f"- **{r['symbol']}** ({r['label']}, {r['ratio_pct']:.1f}%): "
                        f"spread={r['spread']:.6f}, step={r['step']:.6f}")
    else:
        lines.append("- None")
    
    lines.append("")
    lines.append("## Degraded Symbols (spread/step 30-100%)")
    degraded = [r for r in results if r["status"] == "degraded"]
    if degraded:
        for r in degraded:
            lines.append(f"- **{r['symbol']}** ({r['label']}, {r['ratio_pct']:.1f}%): "
                        f"spread={r['spread']:.6f}, step={r['step']:.6f}")
            if r["resets"] > 0:
                lines.append(f"  - Reset count: {r['resets']} (spread causes anchor chasing)")
    else:
        lines.append("- None")
    
    lines.append("")
    lines.append("## Non-Viable Symbols (spread/step > 100%)")
    nonviable = [r for r in results if r["status"] == "non-viable"]
    if nonviable:
        for r in nonviable:
            lines.append(f"- **{r['symbol']}** ({r['label']}, {r['ratio_pct']:.1f}%): "
                        f"spread={r['spread']:.6f}, step={r['step']:.6f}")
            lines.append(f"  - **Recommendation: Kill M5 probe. Spread is wider than step — fills are blocked.**")
    else:
        lines.append("- None")
    
    lines.append("")
    lines.append("## By Asset Class")
    lines.append("")
    for asset_class in ["fx", "crypto", "crypto_shadow"]:
        subset = [r for r in results if r["kind"].startswith("shadow_fx") or 
                  (asset_class == "fx" and r["kind"].startswith("shadow_fx"))]
    # Better grouping
    fx = [r for r in results if "fx" in r["kind"] or r["symbol"] in ["GBPUSD","USDJPY","AUDUSD","EURUSD","NZDUSD","USDCAD"]]
    crypto_live = [r for r in results if r["kind"].startswith("live")]
    crypto_shadow = [r for r in results if "crypto" in r["kind"] and not r["kind"].startswith("live")]
    index = [r for r in results if r["symbol"] in ["NAS100","US30","XAUUSD"]]
    
    for label, subset in [("FX", fx), ("Crypto LIVE", crypto_live), ("Crypto Shadow", crypto_shadow), ("Indices/Commodities", index)]:
        if subset:
            avg_ratio = sum(r["ratio_pct"] for r in subset) / len(subset)
            viable_count = sum(1 for r in subset if r["status"] == "viable")
            lines.append(f"**{label}:** {len(subset)} symbols, avg spread/step {avg_ratio:.1f}%, "
                        f"{viable_count}/{len(subset)} viable")
    
    lines.append("")
    lines.append("## Recommendation")
    lines.append("")
    
    nonviable = [r for r in results if r["status"] == "non-viable"]
    degraded = [r for r in results if r["status"] == "degraded"]
    
    if nonviable:
        syms = ", ".join(r["symbol"] for r in nonviable)
        lines.append(f"1. **Investigate non-viable symbols:** {syms}")
        lines.append(f"   - If spread is stale/wrong (e.g., stale quote), refresh the board")
        lines.append(f"   - If spread is real, consider widening steps or killing the probe")
    
    if degraded:
        syms = ", ".join(r["symbol"] for r in degraded)
        lines.append(f"2. **Retune degraded symbols wider:** {syms}")
        lines.append(f"   - Current step is too tight relative to spread")
        lines.append(f"   - Try 2.0x ATR to push spread/step below 30%")
    
    viable = [r for r in results if r["status"] == "viable"]
    if viable:
        syms = ", ".join(r["symbol"] for r in viable)
        lines.append(f"3. **Scale up viable symbols:** {syms}")
        lines.append(f"   - Spread/step ratio is healthy — focus on coefficient optimization")
    
    lines.append("")
    
    md_path = os.path.join(REPORTS, "m5_warp_spread_step_viability.md")
    with open(md_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    
    print("\n".join(lines))
    print(f"\nSaved: {md_path}")


if __name__ == "__main__":
    main()
