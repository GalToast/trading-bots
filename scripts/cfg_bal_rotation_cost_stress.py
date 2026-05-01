#!/usr/bin/env python3
"""CFG/BAL Rotation Lattice Cost-Stress Audit

Tests whether the CFG/BAL rotation edge survives spread/fee widening.
Uses the 30d benchmark results and applies increasing cost multipliers.

Output: reports/cfg_bal_rotation_cost_stress.md + .json
"""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BENCHMARK_JSON = ROOT / "reports" / "rotation_lattice_benchmark.json"
OUTPUT_MD = ROOT / "reports" / "cfg_bal_rotation_cost_stress.md"
OUTPUT_JSON = ROOT / "reports" / "cfg_bal_rotation_cost_stress.json"

def load_benchmark():
    if not BENCHMARK_JSON.exists():
        return None
    with open(BENCHMARK_JSON) as f:
        return json.load(f)

def cost_stress_audit(pair_results, base_cost_bps=40):
    """Apply increasing cost multipliers to test edge robustness."""
    cfg_bal = None
    for p in pair_results:
        if p.get("pair") == "CFG/BAL":
            cfg_bal = p
            break

    if not cfg_bal:
        return {"error": "CFG/BAL not found in benchmark results"}

    lattice_pnl = cfg_bal.get("lattice_pnl", 0)
    closes = cfg_bal.get("closes", 0)
    avg_pnl_per_close = lattice_pnl / closes if closes > 0 else 0

    # Cost scenarios (in basis points, round-trip)
    cost_scenarios = [1, 2, 5, 10, 20, 30, 50]

    results = []
    for mult in cost_scenarios:
        cost_bps = base_cost_bps * mult
        cost_per_close = cost_bps / 10000  # Convert bps to decimal
        # Estimate cost impact: each close pays spread on both legs
        # Assuming ~$0.01 avg price for ratio, cost per close ≈ cost_per_close * avg_ratio_value
        # Simplified: cost_per_close * notional_value_per_trade
        notional = 8.64  # Kelly deploy per coin
        total_cost = cost_per_close * notional * closes
        net_pnl = lattice_pnl - total_cost
        headroom_bps = (avg_pnl_per_close / notional) * 10000  # How much BPS the edge can absorb

        results.append({
            "cost_multiplier": mult,
            "round_trip_bps": cost_bps,
            "total_cost_usd": round(total_cost, 2),
            "net_pnl_usd": round(net_pnl, 2),
            "edge_survives": net_pnl > 0,
            "friction_headroom_bps": round(headroom_bps, 1),
        })

    return {
        "pair": "CFG/BAL",
        "base_lattice_pnl": round(lattice_pnl, 2),
        "closes": closes,
        "avg_pnl_per_close": round(avg_pnl_per_close, 4),
        "closure_rate": cfg_bal.get("closure_rate", 0),
        "autocorr": cfg_bal.get("autocorr", 0),
        "half_life": cfg_bal.get("half_life", 0),
        "attractors": cfg_bal.get("attractors", 0),
        "base_cost_bps": base_cost_bps,
        "cost_scenarios": results,
        "max_survivable_multiplier": max(
            [r["cost_multiplier"] for r in results if r["edge_survives"]] or [0]
        ),
    }

def build_markdown(report):
    lines = [
        "# CFG/BAL Rotation Lattice — Cost-Stress Audit",
        "",
        "## Summary",
        "",
        f"- **Pair:** CFG/BAL",
        f"- **30d Lattice PnL:** ${report['base_lattice_pnl']:+.2f}",
        f"- **Closes:** {report['closes']}",
        f"- **Closure Rate:** {report['closure_rate']:.1f}%",
        f"- **Autocorr:** {report['autocorr']:.4f}",
        f"- **Half-Life:** {report['half_life']:.1f} bars",
        f"- **Attractors:** {report['attractors']}",
        "",
        "## Cost-Stress Results",
        "",
        f"Base cost: {report['base_cost_bps']}bps round-trip",
        "",
        "| Mult | RT BPS | Total Cost | Net PnL | Survives? | Headroom |",
        "|------|--------|------------|---------|-----------|----------|",
    ]
    for s in report["cost_scenarios"]:
        survives = "✅" if s["edge_survives"] else "❌"
        lines.append(
            f"| {s['cost_multiplier']}x | {s['round_trip_bps']}bps | "
            f"${s['total_cost_usd']:.2f} | ${s['net_pnl_usd']:+.2f} | "
            f"{survives} | {s['friction_headroom_bps']:.1f}bps |"
        )

    max_mult = report["max_survivable_multiplier"]
    lines.append("")
    if max_mult > 0:
        lines.append(f"**Edge survives up to {max_mult}x base cost ({max_mult * report['base_cost_bps']}bps round-trip).**")
    else:
        lines.append("**Edge does NOT survive even at 1x base cost.**")

    lines.append("")
    lines.append("## Verdict")
    lines.append("")
    if max_mult >= 10:
        lines.append("✅ **ROBUST** — Edge survives 10x+ cost widening. Durable enough for live deployment.")
    elif max_mult >= 3:
        lines.append("🟡 **MODERATE** — Edge survives moderate cost widening. Monitor spread closely.")
    elif max_mult >= 1:
        lines.append("🟠 **FRAGILE** — Edge barely survives at base cost. Only viable with tight spreads.")
    else:
        lines.append("❌ **DEAD** — Edge does not survive spread costs at any multiplier.")

    lines.append("")
    return "\n".join(lines)

def main():
    bench = load_benchmark()
    if not bench:
        print("ERROR: No benchmark results found. Run the 30d rotation benchmark first.")
        return

    pair_results = bench.get("pairs", [])
    report = cost_stress_audit(pair_results)

    if "error" in report:
        print(f"ERROR: {report['error']}")
        return

    md = build_markdown(report)
    OUTPUT_MD.write_text(md, encoding="utf-8")
    OUTPUT_JSON.write_text(json.dumps(report, indent=2), encoding="utf-8")

    print(md)
    print(f"\nReport: {OUTPUT_MD}")
    print(f"JSON: {OUTPUT_JSON}")

if __name__ == "__main__":
    main()
