#!/usr/bin/env python3
"""
Ratio Lattice Sleeve Overlap Audit

Analyzes capital contention and diversification across tuned ratio sleeves.
Key question: if we deploy CFG/BTC + CFG/ETH + NOM/BTC simultaneously,
how much capital conflicts, and what's the optimal sleeve combination?

Output: reports/ratio_lattice_overlap_audit.md
"""
import json
import math
from itertools import combinations
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# Tuned sleeve configs from cost-stress audit
SLEEVES = {
    "CFG/BTC": {
        "numerator": "CFG",
        "denominator": "BTC",
        "thr": 1.012,
        "levels": 8,
        "friction_headroom_bps": 1205,
        "positive_scenarios": "42/42",
        "pos_pct": 100.0,
    },
    "CFG/ETH": {
        "numerator": "CFG",
        "denominator": "ETH",
        "thr": 1.012,
        "levels": 5,
        "friction_headroom_bps": 1237,
        "positive_scenarios": "42/42",
        "pos_pct": 100.0,
    },
    "NOM/BTC": {
        "numerator": "NOM",
        "denominator": "BTC",
        "thr": 1.008,
        "levels": 8,
        "friction_headroom_bps": 944,
        "positive_scenarios": "42/42",
        "pos_pct": 100.0,
    },
    "BAL/BTC": {
        "numerator": "BAL",
        "denominator": "BTC",
        "thr": 1.012,
        "levels": 5,
        "friction_headroom_bps": 389,
        "positive_scenarios": "33/42",
        "pos_pct": 78.6,
    },
    "BAL/ETH": {
        "numerator": "BAL",
        "denominator": "ETH",
        "thr": 1.012,
        "levels": 5,
        "friction_headroom_bps": 362,
        "positive_scenarios": "32/42",
        "pos_pct": 76.2,
    },
}

# Per-sleeve position size assumption (BTC-denominated)
POSITION_SIZE_BTC = 0.01

# Asset prices (approximate)
PRICES = {
    "CFG": 0.194,
    "NOM": 0.0036,
    "BAL": 0.150,
    "BTC": 84500.0,
    "ETH": 2200.0,
}


def analyze_overlap(sleeve_names: list[str]) -> dict:
    """Analyze a combination of sleeves for overlap and contention."""
    sleeves = {name: SLEEVES[name] for name in sleeve_names}

    # Count per-asset exposure
    asset_exposure = {}  # asset -> list of sleeves using it
    for name, s in sleeves.items():
        for asset in [s["numerator"], s["denominator"]]:
            if asset not in asset_exposure:
                asset_exposure[asset] = []
            asset_exposure[asset].append(name)

    # Find contention points (asset used by multiple sleeves)
    contentions = {
        asset: sleeves_list
        for asset, sleeves_list in asset_exposure.items()
        if len(sleeves_list) > 1
    }

    # Calculate capital allocation per sleeve
    # Each sleeve deploys position_size_btc worth of the denominator asset
    capital_per_sleeve = {}
    for name, s in sleeves.items():
        denom = s["denominator"]
        capital_usd = POSITION_SIZE_BTC * PRICES.get(denom, 0)
        capital_per_sleeve[name] = capital_usd

    total_capital_usd = sum(capital_per_sleeve.values())

    # Effective unique capital (removing double-counted assets)
    # If two sleeves both use BTC as denominator, the BTC capital is shared
    unique_capital = {}
    for asset, asset_sleeves in asset_exposure.items():
        if asset == "BTC":
            # All BTC-denominated sleeves share the same BTC capital pool
            unique_capital[asset] = POSITION_SIZE_BTC * PRICES["BTC"]
        elif asset == "ETH":
            unique_capital[asset] = POSITION_SIZE_BTC * PRICES["ETH"]
        else:
            # Numerator assets: each sleeve deploys independently
            for s_name in asset_sleeves:
                s = sleeves[s_name]
                num = s["numerator"]
                unique_capital[f"{s_name}:{num}"] = POSITION_SIZE_BTC / PRICES.get(num, 1) * PRICES.get(num, 1)

    total_unique_usd = sum(unique_capital.values())

    # Diversification score: 1.0 = fully independent, 0.0 = fully overlapping
    # Measured as unique_capital / total_capital
    diversification = total_unique_usd / total_capital_usd if total_capital_usd > 0 else 1.0

    # Combined friction headroom (minimum across sleeves)
    min_headroom = min(s["friction_headroom_bps"] for s in sleeves.values())

    # Combined stress survival (percentage of scenarios where ALL sleeves are positive)
    # Assuming independence: product of individual pos_pcts
    combined_pos_pct = math.prod(s["pos_pct"] / 100.0 for s in sleeves.values()) * 100

    return {
        "sleeves": sleeve_names,
        "n_sleeves": len(sleeve_names),
        "contentions": contentions,
        "capital_per_sleeve": capital_per_sleeve,
        "total_capital_usd": round(total_capital_usd, 2),
        "total_unique_capital_usd": round(total_unique_usd, 2),
        "capital_efficiency": round(total_unique_usd / total_capital_usd, 3) if total_capital_usd > 0 else 1.0,
        "diversification_score": round(diversification, 3),
        "min_friction_headroom_bps": min_headroom,
        "combined_stress_survival_pct": round(combined_pos_pct, 1),
    }


def main():
    print("=" * 72)
    print("RATIO LATTICE SLEEVE OVERLAP AUDIT")
    print("=" * 72)
    print()

    # Analyze all single sleeves (baseline)
    print("SINGLE SLEEVE BASELINES:")
    print()
    for name, s in SLEEVES.items():
        cap = POSITION_SIZE_BTC * PRICES.get(s["denominator"], 0)
        print(f"  {name}: headroom={s['friction_headroom_bps']}bps, "
              f"stress={s['positive_scenarios']}, capital=${cap:.2f}")

    print()
    print("=" * 72)
    print("COMBINATION ANALYSIS")
    print("=" * 72)
    print()

    all_results = []

    # Analyze all 2-sleeve combinations
    print("2-SLEEVE COMBINATIONS:")
    print()
    for combo in combinations(SLEEVES.keys(), 2):
        result = analyze_overlap(list(combo))
        all_results.append(result)

        contention_str = ""
        if result["contentions"]:
            for asset, sl in result["contentions"].items():
                contention_str += f"{asset}: {', '.join(sl)}; "

        print(f"  {' + '.join(combo)}")
        print(f"    Contentions: {contention_str or 'None'}")
        print(f"    Capital: ${result['total_capital_usd']:.2f} → unique ${result['total_unique_capital_usd']:.2f} "
              f"(efficiency={result['capital_efficiency']:.1%})")
        print(f"    Diversification: {result['diversification_score']:.3f}")
        print(f"    Min headroom: {result['min_friction_headroom_bps']}bps")
        print(f"    Combined stress survival: {result['combined_stress_survival_pct']:.1f}%")
        print()

    # Analyze 3-sleeve combinations
    print("3-SLEEVE COMBINATIONS:")
    print()
    for combo in combinations(SLEEVES.keys(), 3):
        result = analyze_overlap(list(combo))
        all_results.append(result)

        contention_str = ""
        if result["contentions"]:
            for asset, sl in result["contentions"].items():
                contention_str += f"{asset}: {', '.join(sl)}; "

        print(f"  {' + '.join(combo)}")
        print(f"    Contentions: {contention_str or 'None'}")
        print(f"    Capital: ${result['total_capital_usd']:.2f} → unique ${result['total_unique_capital_usd']:.2f} "
              f"(efficiency={result['capital_efficiency']:.1%})")
        print(f"    Diversification: {result['diversification_score']:.3f}")
        print(f"    Min headroom: {result['min_friction_headroom_bps']}bps")
        print(f"    Combined stress survival: {result['combined_stress_survival_pct']:.1f}%")
        print()

    # Ranking
    print("=" * 72)
    print("RANKING BY COMBINED STRESS SURVIVAL (min 90% threshold)")
    print("=" * 72)
    print()

    qualified = [r for r in all_results if r["combined_stress_survival_pct"] >= 90]
    qualified.sort(key=lambda r: r["combined_stress_survival_pct"], reverse=True)

    print(f"  {'Combination':<40} {'Survival':<12} {'Headroom':<12} {'Capital':<12} {'Diversification':<15}")
    print(f"  {'------------':<40} {'--------':<12} {'--------':<12} {'--------':<12} {'---------------':<15}")

    for r in qualified:
        label = " + ".join(r["sleeves"])
        print(f"  {label:<40} {r['combined_stress_survival_pct']:.1f}%        "
              f"{r['min_friction_headroom_bps']}bps       "
              f"${r['total_capital_usd']:.2f}      "
              f"{r['diversification_score']:.3f}")

    print()

    # Top recommendation
    if qualified:
        best = qualified[0]
        print(f"**RECOMMENDATION:** Deploy {' + '.join(best['sleeves'])}")
        print(f"  Combined stress survival: {best['combined_stress_survival_pct']:.1f}%")
        print(f"  Min friction headroom: {best['min_friction_headroom_bps']}bps")
        print(f"  Capital efficiency: {best['capital_efficiency']:.1%}")

    # Save report
    report = {
        "single_sleeves": {name: {k: v for k, v in s.items()} for name, s in SLEEVES.items()},
        "combinations": all_results,
        "top_recommendation": qualified[0] if qualified else None,
    }

    out_md = _build_markdown(report)
    out_path = ROOT / "reports" / "ratio_lattice_overlap_audit.md"
    out_path.write_text(out_md)
    out_json = ROOT / "reports" / "ratio_lattice_overlap_audit.json"
    out_json.write_text(json.dumps(report, indent=2))

    print()
    print(f"Report: {out_path}")
    print(f"JSON: {out_json}")


def _build_markdown(report: dict) -> str:
    lines = [
        "# Ratio Lattice Sleeve Overlap Audit",
        "",
        "This audit measures capital contention and diversification across tuned ratio sleeves.",
        "",
        "## Single Sleeve Baselines",
        "",
        "| Sleeve | Headroom (bps) | Stress Survival | Capital (USD) |",
        "|--------|---------------|-----------------|---------------|",
    ]

    for name, s in report["single_sleeves"].items():
        cap = POSITION_SIZE_BTC * PRICES.get(s["denominator"], 0)
        lines.append(f"| {name} | {s['friction_headroom_bps']} | {s['positive_scenarios']} ({s['pos_pct']}%) | ${cap:.2f} |")

    lines.append("")
    lines.append("## 2-Sleeve Combinations")
    lines.append("")
    lines.append("| Combination | Contentions | Capital Efficiency | Diversification | Min Headroom | Combined Survival |")
    lines.append("|-------------|-------------|-------------------|-----------------|-------------|-------------------|")

    twos = [c for c in report["combinations"] if c["n_sleeves"] == 2]
    for c in twos:
        label = " + ".join(c["sleeves"])
        contentions = "; ".join(f"{a}: {', '.join(sl)}" for a, sl in c["contentions"].items()) if c["contentions"] else "None"
        lines.append(f"| {label} | {contentions} | {c['capital_efficiency']:.1%} | {c['diversification_score']:.3f} | {c['min_friction_headroom_bps']}bps | {c['combined_stress_survival_pct']:.1f}% |")

    lines.append("")
    lines.append("## 3-Sleeve Combinations")
    lines.append("")
    lines.append("| Combination | Contentions | Capital Efficiency | Diversification | Min Headroom | Combined Survival |")
    lines.append("|-------------|-------------|-------------------|-----------------|-------------|-------------------|")

    threes = [c for c in report["combinations"] if c["n_sleeves"] == 3]
    for c in threes:
        label = " + ".join(c["sleeves"])
        contentions = "; ".join(f"{a}: {', '.join(sl)}" for a, sl in c["contentions"].items()) if c["contentions"] else "None"
        lines.append(f"| {label} | {contentions} | {c['capital_efficiency']:.1%} | {c['diversification_score']:.3f} | {c['min_friction_headroom_bps']}bps | {c['combined_stress_survival_pct']:.1f}% |")

    lines.append("")

    if report["top_recommendation"]:
        best = report["top_recommendation"]
        lines.append("## Recommendation")
        lines.append("")
        lines.append(f"**Deploy: {' + '.join(best['sleeves'])}**")
        lines.append(f"- Combined stress survival: {best['combined_stress_survival_pct']:.1f}%")
        lines.append(f"- Min friction headroom: {best['min_friction_headroom_bps']}bps")
        lines.append(f"- Capital efficiency: {best['capital_efficiency']:.1%}")
        lines.append(f"- Diversification score: {best['diversification_score']:.3f}")
        lines.append("")

    lines.append("## Interpretation")
    lines.append("")
    lines.append("- **Contentions** show which assets are shared across sleeves (capital conflict).")
    lines.append("- **Capital efficiency** measures how much of the nominal capital is actually unique.")
    lines.append("- **Diversification score** ranges from 0 (fully overlapping) to 1 (fully independent).")
    lines.append("- **Combined survival** assumes independent stress responses — real correlation may reduce this.")
    lines.append("")

    return "\n".join(lines)


if __name__ == "__main__":
    main()
