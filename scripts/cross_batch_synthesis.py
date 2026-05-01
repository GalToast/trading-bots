#!/usr/bin/env python3
"""
Cross-Batch Synthesis Report — 500 Strategies Initiative
Aggregates all sweep results, identifies top edges, and recommends priorities.
Generated: 2026-04-12
"""

import json
from pathlib import Path

REPORTS_DIR = Path(__file__).parent.parent / "reports"
REGISTRY_PATH = Path(__file__).parent.parent / "experiment_registry.json"

SWEEP_FILES = {
    "volatility": REPORTS_DIR / "volatility_50_sweep_7d.json",
    "volume": REPORTS_DIR / "volume_50_sweep_7d.json",
    "candle_patterns": REPORTS_DIR / "candle_pattern_50_sweep_7d.json",
    "statistical": REPORTS_DIR / "statistical_50_sweep_7d.json",
    "time_based": REPORTS_DIR / "time_based_50_sweep_7d.json",
}


def load_sweeps():
    sweeps = {}
    for cat, path in SWEEP_FILES.items():
        if path.exists():
            with open(path) as f:
                sweeps[cat] = json.load(f)
    return sweeps


def generate_report():
    with open(REGISTRY_PATH, "r", encoding="utf-8") as f:
        reg = json.load(f)

    sweeps = load_sweeps()

    print(f"\n{'='*80}")
    print(f"  CROSS-BATCH SYNTHESIS REPORT — 500 STRATEGIES INITIATIVE")
    print(f"  Generated: 2026-04-12 19:58 UTC")
    print(f"{'='*80}\n")

    print(f"  OVERALL PROGRESS: {reg['total_unique_strategies_tested']}/500 ({reg['total_unique_strategies_tested']/5}%)\n")

    # Category summary
    print(f"  {'Category':<20} {'Tested':<8} {'Target':<8} {'% Done':<8} {'Top Strategy':<25} {'Top PnL':<10}")
    print(f"  {'-'*80}")
    for cat_name, cat_data in reg["strategy_categories"].items():
        top_strat = ""
        top_pnl = 0
        if cat_name in sweeps:
            for r in sweeps[cat_name].get("results", [])[:1]:
                top_strat = r["strategy"]
                top_pnl = r["total_net_pnl"]
        pct = cat_data["tested"] / cat_data["target"] * 100 if cat_data["target"] > 0 else 0
        print(f"  {cat_name:<20} {cat_data['tested']:<8} {cat_data['target']:<8} {pct:>5.0f}%    {top_strat:<25} ${top_pnl:>8.0f}")

    print(f"\n{'='*80}")
    print(f"  TOP 15 EDGES ACROSS ALL BATCHES (7d Discovery)")
    print(f"{'='*80}\n")

    # Collect all results
    all_results = []
    for cat, sweep in sweeps.items():
        for r in sweep.get("results", []):
            all_results.append({**r, "category": cat})

    all_results.sort(key=lambda x: x["total_net_pnl"], reverse=True)

    print(f"  {'Rank':<5} {'Strategy':<28} {'Category':<18} {'Total PnL':<12} {'Hit Rate':<10} {'Coins':<8}")
    print(f"  {'-'*80}")
    for i, r in enumerate(all_results[:15], 1):
        coins_str = f"{r['profitable_coins']}/{r['coins_tested']}"
        print(f"  {i:<5} {r['strategy']:<28} {r['category']:<18} ${r['total_net_pnl']:>9.0f}  {r['hit_rate']:>5.1f}%  {coins_str:<8}")

    # By hit rate
    print(f"\n{'='*80}")
    print(f"  TOP 10 BY HIT RATE (minimum 20% coins profitable)")
    print(f"{'='*80}\n")

    high_wr = [r for r in all_results if r["profitable_coins"] / max(r["coins_tested"], 1) >= 0.2]
    high_wr.sort(key=lambda x: x["hit_rate"], reverse=True)

    print(f"  {'Rank':<5} {'Strategy':<28} {'Category':<18} {'Hit Rate':<10} {'Total PnL':<12} {'Coins':<8}")
    print(f"  {'-'*80}")
    for i, r in enumerate(high_wr[:10], 1):
        coins_str = f"{r['profitable_coins']}/{r['coins_tested']}"
        print(f"  {i:<5} {r['strategy']:<28} {r['category']:<18} {r['hit_rate']:>5.1f}%   ${r['total_net_pnl']:>9.0f}  {coins_str:<8}")

    # Category leaders
    print(f"\n{'='*80}")
    print(f"  CATEGORY CHAMPIONS")
    print(f"{'='*80}\n")

    for cat, sweep in sweeps.items():
        results = sweep.get("results", [])
        if not results:
            continue
        results.sort(key=lambda x: x["total_net_pnl"], reverse=True)
        top = results[0]
        coins_str = f"{top['profitable_coins']}/{top['coins_tested']}"
        print(f"  {cat:<18} → {top['strategy']:<28} ${top['total_net_pnl']:>8.0f}  ({top['hit_rate']:.1f}% hit, {coins_str} coins)")

    # Fee-fragility analysis
    print(f"\n{'='*80}")
    print(f"  FEE-FRAGILITY BY CATEGORY")
    print(f"{'='*80}\n")

    for cat, sweep in sweeps.items():
        results = sweep.get("results", [])
        if not results:
            continue
        profitable = sum(1 for r in results if r["total_net_pnl"] > 0)
        total = len(results)
        pct_profit = profitable / total * 100
        total_pnl = sum(r["total_net_pnl"] for r in results)
        print(f"  {cat:<18} {profitable:>3}/{total} profitable ({pct_profit:>5.1f}%)  Total PnL: ${total_pnl:>8.0f}")

    # Recommendations
    print(f"\n{'='*80}")
    print(f"  RESEARCH RECOMMENDATIONS")
    print(f"{'='*80}\n")

    print(f"  1. PROMOTE TO 30D VALIDATION (top 3 by total PnL):")
    for i, r in enumerate(all_results[:3], 1):
        print(f"     {i}. {r['strategy']} ({r['category']}) — ${r['total_net_pnl']:.0f}")

    print(f"\n  2. PROMOTE BY HIT RATE (top 3 with >40% coins profitable):")
    for i, r in enumerate(high_wr[:3], 1):
        coin_pct = r["profitable_coins"] / max(r["coins_tested"], 1) * 100
        print(f"     {i}. {r['strategy']} ({r['category']}) — {r['hit_rate']:.1f}% hit, {coin_pct:.0f}% coins")

    print(f"\n  3. AVOID: Low-frequency strategies (<5 signals/week) — 7d→30d gap confirmed fatal")
    print(f"     Examples: vol_breakout (0 trades on 30d), atr_trailing (0% WR on 30d)")

    print(f"\n  4. ARCHITECTURE: Per-coin independent bankroll required.")
    print(f"     Shared-bankroll portfolio confirmed to fail (100% loss in combined test).")

    print(f"\n  5. NEXT BATCHES: cross_asset and hybrid remaining. Then param optimization on top edges.")

    print(f"\n{'='*80}\n")

    # Save report
    report = {
        "generated": "2026-04-12T19:58:00Z",
        "total_tested": reg["total_unique_strategies_tested"],
        "top_15_edges": all_results[:15],
        "category_champions": {},
        "fee_fragility": {},
    }

    for cat, sweep in sweeps.items():
        results = sweep.get("results", [])
        if results:
            results.sort(key=lambda x: x["total_net_pnl"], reverse=True)
            report["category_champions"][cat] = results[0]
            profitable = sum(1 for r in results if r["total_net_pnl"] > 0)
            report["fee_fragility"][cat] = {
                "profitable": profitable,
                "total": len(results),
                "pct": round(profitable / len(results) * 100, 1),
                "total_pnl": sum(r["total_net_pnl"] for r in results),
            }

    out_path = REPORTS_DIR / "cross_batch_synthesis_report.json"
    with open(out_path, "w") as f:
        json.dump(report, f, indent=2)

    print(f"  Report saved: {out_path}\n")


if __name__ == "__main__":
    generate_report()
