#!/usr/bin/env python3
"""
FINAL 500 STRATEGIES REPORT — Complete synthesis of all sweep batches.
This is the definitive permanent artifact for the 500-strategy initiative.
Generated: 2026-04-12
"""

import json
from pathlib import Path
from datetime import datetime, timezone

REPORTS_DIR = Path(__file__).parent.parent / "reports"
REGISTRY_PATH = Path(__file__).parent.parent / "experiment_registry.json"

SWEEP_FILES = {
    "volatility": REPORTS_DIR / "volatility_50_sweep_7d.json",
    "volume": REPORTS_DIR / "volume_50_sweep_7d.json",
    "candle_patterns": REPORTS_DIR / "candle_pattern_50_sweep_7d.json",
    "statistical": REPORTS_DIR / "statistical_50_sweep_7d.json",
    "time_based": REPORTS_DIR / "time_based_50_sweep_7d.json",
    "cross_asset": REPORTS_DIR / "cross_asset_50_sweep_7d.json",
    "hybrid": REPORTS_DIR / "hybrid_50_sweep_7d.json",
    "mean_reversion": REPORTS_DIR / "mean_reversion_50_sweep_7d.json",
    "momentum": REPORTS_DIR / "momentum_50_sweep_7d.json",
    "breakout": REPORTS_DIR / "breakout_50_sweep_7d.json",
}


def load_all():
    sweeps = {}
    for cat, path in SWEEP_FILES.items():
        if path.exists():
            with open(path) as f:
                sweeps[cat] = json.load(f)
    return sweeps


def generate():
    with open(REGISTRY_PATH, "r", encoding="utf-8") as f:
        reg = json.load(f)

    sweeps = load_all()

    # Collect ALL results
    all_results = []
    for cat, sweep in sweeps.items():
        for r in sweep.get("results", []):
            all_results.append({**r, "category": cat})

    all_results.sort(key=lambda x: x["total_net_pnl"], reverse=True)

    # Count total strategies
    total_tested = sum(len(s.get("results", [])) for s in sweeps.values())

    # Category stats
    cat_stats = {}
    for cat, sweep in sweeps.items():
        results = sweep.get("results", [])
        profitable = sum(1 for r in results if r["total_net_pnl"] > 0)
        total_pnl = sum(r["total_net_pnl"] for r in results)
        champion = results[0] if results else None
        cat_stats[cat] = {
            "tested": len(results),
            "profitable": profitable,
            "pct_profitable": round(profitable / max(len(results), 1) * 100, 1),
            "total_pnl": round(total_pnl, 2),
            "champion": champion["strategy"] if champion else "—",
            "champion_pnl": champion["total_net_pnl"] if champion else 0,
        }

    # === BUILD FINAL REPORT ===
    report = {
        "title": "500 Strategies Initiative — Final Report",
        "generated": datetime.now(timezone.utc).isoformat(),
        "total_strategies_tested": total_tested,
        "total_backtests": sum(s.get("total_backtests", s.get("coins_tested", 0) * len(s.get("results", []))) for s in sweeps.values()),
        "category_summary": cat_stats,
        "top_50_edges": all_results[:50],
        "top_10_by_hit_rate": sorted(
            [r for r in all_results if r["profitable_coins"] / max(r["coins_tested"], 1) >= 0.2],
            key=lambda x: float(str(x["hit_rate"]).replace("%", "")),
            reverse=True
        )[:10],
        "key_findings": [
            "Supertrend ($3,406) is the single best strategy discovered across all 500",
            "Momentum and Breakout categories dominate — 8 of top 10 edges",
            "Hybrid strategies have highest profitability rate (66%)",
            "Volume strategies are most consistent (64% profitable)",
            "Mean reversion is fee-fragile (only 20% profitable)",
            "Per-coin independent bankroll required — shared pool destroys 99.6% of edge",
            "momentum + robust_regression = optimal pair (only 17.9% signal overlap)",
            "Low-frequency strategies fail 7d→30d validation — need 30d minimum",
            "Fibonacci breakout ($2,180) and ma_atr ($1,954) are top hybrid/breakout edges",
            "fractal_momentum has 51.4% hit rate — highest of any major strategy",
        ],
        "deployment_recommendations": [
            "Use shared bankroll runner at current bankroll levels (<$200)",
            "Switch to per-coin isolated runner at $200+ bankroll",
            "Deploy supertrend, fibonacci_breakout, or ma_atr as primary strategies",
            "Combine momentum + robust_regression on different coins for additive alpha",
            "Avoid mean reversion, candle patterns, and volatility strategies (fee-fragile)",
            "Use volume strategies as consistent secondary signals",
        ],
    }

    out_path = REPORTS_DIR / "final_500_strategies_report.json"
    with open(out_path, "w") as f:
        json.dump(report, f, indent=2)

    # === BUILD LEADERBOARD MARKDOWN ===
    md_lines = []
    md_lines.append("# 🏆 500 Strategies Initiative — Final Leaderboard\n")
    md_lines.append(f"**Generated:** {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}")
    md_lines.append(f"**Total Strategies Tested:** {total_tested}")
    md_lines.append(f"**Total Backtests:** {report['total_backtests']}\n")

    md_lines.append("## Category Summary\n")
    md_lines.append("| Category | Tested | Profitable | % Profitable | Total PnL | Champion | Champion PnL |")
    md_lines.append("|----------|--------|-----------|-------------|-----------|----------|-------------|")
    for cat in ["hybrid", "breakout", "momentum", "volume", "cross_asset", "statistical", "time_based", "mean_reversion", "candle_patterns", "volatility"]:
        s = cat_stats.get(cat, {})
        md_lines.append(f"| {cat} | {s.get('tested', 0)} | {s.get('profitable', 0)} | {s.get('pct_profitable', 0)}% | ${s.get('total_pnl', 0):,.0f} | {s.get('champion', '—')} | ${s.get('champion_pnl', 0):,.0f} |")

    md_lines.append(f"\n## Top 50 Edges (All Strategies)\n")
    md_lines.append("| Rank | Strategy | Category | Total PnL | Hit Rate | Coins Profitable |")
    md_lines.append("|------|----------|----------|-----------|----------|-----------------|")
    for i, r in enumerate(all_results[:50], 1):
        coins_str = f"{r['profitable_coins']}/{r['coins_tested']}"
        md_lines.append(f"| {i} | **{r['strategy']}** | {r['category']} | **${r['total_net_pnl']:,.0f}** | {r['hit_rate']}% | {coins_str} |")

    md_lines.append(f"\n## Top 10 by Hit Rate (min 20% coins profitable)\n")
    md_lines.append("| Rank | Strategy | Category | Hit Rate | Total PnL | Coins |")
    md_lines.append("|------|----------|----------|----------|-----------|-------|")
    high_wr = sorted(
        [r for r in all_results if r["profitable_coins"] / max(r["coins_tested"], 1) >= 0.2],
        key=lambda x: float(str(x["hit_rate"]).replace("%", "")),
        reverse=True
    )[:10]
    for i, r in enumerate(high_wr, 1):
        coins_str = f"{r['profitable_coins']}/{r['coins_tested']}"
        md_lines.append(f"| {i} | **{r['strategy']}** | {r['category']} | **{r['hit_rate']}%** | ${r['total_net_pnl']:,.0f} | {coins_str} |")

    md_lines.append(f"\n## Key Findings\n")
    for i, finding in enumerate(report["key_findings"], 1):
        md_lines.append(f"{i}. {finding}")

    md_lines.append(f"\n## Deployment Recommendations\n")
    for i, rec in enumerate(report["deployment_recommendations"], 1):
        md_lines.append(f"{i}. {rec}")

    out_md = REPORTS_DIR / "strategy_leaderboard.md"
    with open(out_md, "w", encoding="utf-8") as f:
        f.write("\n".join(md_lines))

    print(f"\n{'='*80}")
    print(f"  FINAL 500 STRATEGIES REPORT GENERATED")
    print(f"{'='*80}\n")
    print(f"  Total strategies tested: {total_tested}")
    print(f"  Total backtests: {report['total_backtests']}")
    print(f"  Categories covered: {len(cat_stats)}")
    print(f"\n  Top 5 edges:")
    for i, r in enumerate(all_results[:5], 1):
        print(f"    {i}. {r['strategy']} ({r['category']}) — ${r['total_net_pnl']:,.0f}")
    print(f"\n  Reports saved:")
    print(f"    JSON: {out_path}")
    print(f"    Markdown: {out_md}")
    print(f"\n{'='*80}\n")


if __name__ == "__main__":
    generate()
