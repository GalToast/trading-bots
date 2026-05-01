#!/usr/bin/env python3
"""Dynamic Target Optimizer — consumes Swarm Brain features + exit-fill calibration
to compute optimal exit targets per product based on current market regime.

Reads:
- reports/swarm_brain_features.json (global veto, regime score, candidates)
- reports/kraken_exit_fill_calibration.json (fill-rate curve per target)

Outputs:
- reports/dynamic_target_recommendations.json (per-product optimal exit target)
- reports/dynamic_target_recommendations.md (human-readable surface)
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

DEFAULT_SWARM_BRAIN_PATH = ROOT / "reports" / "swarm_brain_features.json"
DEFAULT_EXIT_FILL_PATH = ROOT / "reports" / "kraken_exit_fill_calibration.json"
DEFAULT_OUTPUT_JSON_PATH = ROOT / "reports" / "dynamic_target_recommendations.json"
DEFAULT_OUTPUT_MD_PATH = ROOT / "reports" / "dynamic_target_recommendations.md"

# Default exit targets to recommend
EXIT_TARGETS_PCT = [0.10, 0.15, 0.20, 0.25, 0.50]

# Regime score thresholds
VETO_THRESHOLD = 1.0  # global_veto_active == true → veto all
THIN_BOOK_THRESHOLD = 1.0  # regime_score < this → thin book, fast exit
THICK_BOOK_THRESHOLD = 1.5  # regime_score > this → thick book, patient exit


def utc_now_iso() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()


def load_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        return payload if isinstance(payload, dict) else {}
    except (json.JSONDecodeError, Exception):
        return {}


def compute_optimal_exit_target(
    regime_score: float,
    global_veto_active: bool,
    exit_fill_data: dict[str, Any] | None = None,
    product_id: str = "",
) -> dict[str, Any]:
    """Compute optimal exit target based on regime + fill calibration data."""
    result = {
        "product_id": product_id,
        "regime_score": regime_score,
        "global_veto_active": global_veto_active,
        "recommended_exit_target_pct": None,
        "recommended_exit_bps": None,
        "fill_rate_at_target": None,
        "expected_hold_sec": None,
        "expected_net_per_hour": None,
        "confidence": "low",
        "reason": "",
    }

    # Global veto → veto all entries
    if global_veto_active:
        result["reason"] = "GLOBAL_VETO_ACTIVE — all entries blocked"
        result["confidence"] = "high"
        return result

    # No exit-fill calibration data yet → use regime-based heuristic
    if exit_fill_data is None or not exit_fill_data.get("by_product_target"):
        if regime_score < THIN_BOOK_THRESHOLD:
            # Thin book → fast exit at 0.10% to maximize throughput
            result["recommended_exit_target_pct"] = 0.10
            result["recommended_exit_bps"] = 10.0
            result["reason"] = "THIN_BOOK regime (score < 1.0) → fast exit at 0.10%"
            result["confidence"] = "medium"
        elif regime_score > THICK_BOOK_THRESHOLD:
            # Thick book → patient exit at 0.25% to capture more spread
            result["recommended_exit_target_pct"] = 0.25
            result["recommended_exit_bps"] = 25.0
            result["reason"] = "THICK_BOOK regime (score > 1.5) → patient exit at 0.25%"
            result["confidence"] = "medium"
        else:
            # Neutral regime → interpolate between 0.10% and 0.25%
            # Linear interpolation: at 1.0 → 0.10%, at 1.5 → 0.25%
            t = (regime_score - THIN_BOOK_THRESHOLD) / (THICK_BOOK_THRESHOLD - THIN_BOOK_THRESHOLD)
            target = 0.10 + t * (0.25 - 0.10)
            result["recommended_exit_target_pct"] = round(target, 4)
            result["recommended_exit_bps"] = round(target * 100, 2)
            result["reason"] = f"NEUTRAL regime (score={regime_score:.3f}) → interpolated exit at {target:.2f}%"
            result["confidence"] = "medium"
        return result

    # With exit-fill calibration data → optimize based on fill-rate curve
    product_targets = exit_fill_data.get("by_product_target", {}).get(product_id, {})
    if not product_targets:
        # Fall back to regime-based heuristic
        return compute_optimal_exit_target(regime_score, global_veto_active, None, product_id)

    # Find the target with best expected $/hour
    best_target = None
    best_dollar_per_hour = -1.0

    for target_str, data in product_targets.items():
        target_pct = float(target_str)
        fill_rate = data.get("fill_rate", 0.0)
        avg_fill_sec = data.get("avg_exit_fill_sec")
        avg_net_pct = data.get("avg_net_pct")

        if avg_fill_sec is None or avg_fill_sec <= 0 or avg_net_pct is None:
            continue

        # Expected $/hour = (fill_rate * avg_net_pct * avg_cost) / (avg_fill_sec / 3600)
        # Simplified: fill_rate * avg_net_pct / avg_fill_sec * 3600
        expected_per_hour = fill_rate * abs(avg_net_pct) / avg_fill_sec * 3600
        if expected_per_hour > best_dollar_per_hour:
            best_dollar_per_hour = expected_per_hour
            best_target = {
                "target_pct": target_pct,
                "target_bps": round(target_pct * 100, 2),
                "fill_rate": fill_rate,
                "avg_fill_sec": avg_fill_sec,
                "avg_net_pct": avg_net_pct,
                "expected_per_hour": round(expected_per_hour, 4),
            }

    if best_target is None:
        # Fall back to regime-based heuristic
        return compute_optimal_exit_target(regime_score, global_veto_active, None, product_id)

    result["recommended_exit_target_pct"] = best_target["target_pct"]
    result["recommended_exit_bps"] = best_target["target_bps"]
    result["fill_rate_at_target"] = best_target["fill_rate"]
    result["expected_hold_sec"] = best_target["avg_fill_sec"]
    result["expected_net_per_hour"] = best_target["expected_per_hour"]
    result["reason"] = f"Calibrated optimum: {best_target['target_pct']}% target, {best_target['fill_rate']:.1%} fill rate, {best_target['avg_fill_sec']:.1s} avg fill"
    result["confidence"] = "high"

    return result


def run_optimizer(
    swarm_brain_path: Path = DEFAULT_SWARM_BRAIN_PATH,
    exit_fill_path: Path = DEFAULT_EXIT_FILL_PATH,
    output_json_path: Path = DEFAULT_OUTPUT_JSON_PATH,
    output_md_path: Path = DEFAULT_OUTPUT_MD_PATH,
    products: list[str] | None = None,
) -> dict[str, Any]:
    """Run the Dynamic Target Optimizer."""
    swarm_data = load_json(swarm_brain_path)
    exit_fill_data = load_json(exit_fill_path) if exit_fill_path.exists() else None

    regime_score = swarm_data.get("global_regime_score", 1.0)
    global_veto_active = swarm_data.get("global_veto_active", False)
    active_candidates = swarm_data.get("active_candidates", 0)
    lead_leaders = swarm_data.get("lead_leaders", [])

    # Default products to optimize
    if products is None:
        products = ["HOUSE-USD", "BTR-USD", "FOLKS-USD"]
        # Add products from exit-fill data if available
        if exit_fill_data and "by_product_target" in exit_fill_data:
            products = list(exit_fill_data["by_product_target"].keys())

    recommendations = []
    for product in products:
        rec = compute_optimal_exit_target(
            regime_score=regime_score,
            global_veto_active=global_veto_active,
            exit_fill_data=exit_fill_data,
            product_id=product,
        )
        recommendations.append(rec)

    summary = {
        "generated_at": utc_now_iso(),
        "swarm_brain_source": str(swarm_brain_path),
        "exit_fill_source": str(exit_fill_path) if exit_fill_path and exit_fill_path.exists() else "not_available",
        "regime_score": regime_score,
        "global_veto_active": global_veto_active,
        "active_candidates": active_candidates,
        "lead_leaders": lead_leaders,
        "recommendations": recommendations,
        "summary": {
            "total_products": len(recommendations),
            "vetoed": sum(1 for r in recommendations if r["global_veto_active"]),
            "high_confidence": sum(1 for r in recommendations if r["confidence"] == "high"),
            "medium_confidence": sum(1 for r in recommendations if r["confidence"] == "medium"),
            "low_confidence": sum(1 for r in recommendations if r["confidence"] == "low"),
        },
    }

    # Write JSON output
    output_json_path.parent.mkdir(parents=True, exist_ok=True)
    output_json_path.write_text(json.dumps(summary, indent=2, sort_keys=True), encoding="utf-8")

    # Write Markdown output
    lines = [
        "# Dynamic Target Recommendations",
        "",
        f"- Generated: `{summary['generated_at']}`",
        f"- Regime score: `{regime_score:.3f}`",
        f"- Global veto active: `{global_veto_active}`",
        f"- Active candidates: `{active_candidates}`",
        f"- Lead leaders: `{', '.join(lead_leaders)}`",
        f"- Exit-fill calibration: `{'available' if exit_fill_data else 'not_available'}`",
        "",
        "## Recommendations",
        "",
        "| Product | Exit Target % | Exit Bps | Fill Rate | Hold Sec | $/Hour | Confidence | Reason |",
        "| --- | ---: | ---: | ---: | ---: | ---: | --- | --- |",
    ]
    for rec in recommendations:
        lines.append(
            f"| {rec['product_id']} | {rec['recommended_exit_target_pct']} | {rec['recommended_exit_bps']} | "
            f"{rec['fill_rate_at_target'] or 'N/A'} | {rec['expected_hold_sec'] or 'N/A'} | "
            f"{rec['expected_net_per_hour'] or 'N/A'} | {rec['confidence']} | {rec['reason']} |"
        )

    output_md_path.parent.mkdir(parents=True, exist_ok=True)
    output_md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    return summary


def parse_args():
    import argparse
    parser = argparse.ArgumentParser(description="Dynamic Target Optimizer for Kraken maker lane")
    parser.add_argument("--swarm-brain-path", type=Path, default=DEFAULT_SWARM_BRAIN_PATH)
    parser.add_argument("--exit-fill-path", type=Path, default=DEFAULT_EXIT_FILL_PATH)
    parser.add_argument("--json-path", type=Path, default=DEFAULT_OUTPUT_JSON_PATH)
    parser.add_argument("--md-path", type=Path, default=DEFAULT_OUTPUT_MD_PATH)
    parser.add_argument("--products", default="", help="Comma-separated product IDs to optimize")
    return parser.parse_args()


def main():
    args = parse_args()
    products = [p.strip().upper() for p in args.products.split(",") if p.strip()] if args.products.strip() else None
    summary = run_optimizer(
        swarm_brain_path=args.swarm_brain_path,
        exit_fill_path=args.exit_fill_path,
        output_json_path=args.json_path,
        output_md_path=args.md_path,
        products=products,
    )
    print(f"Generated recommendations: {args.json_path}")
    print(f"  Regime score: {summary['regime_score']:.3f}")
    print(f"  Global veto: {summary['global_veto_active']}")
    print(f"  Products: {summary['summary']['total_products']}")
    print(f"  High confidence: {summary['summary']['high_confidence']}")
    print(f"  Medium confidence: {summary['summary']['medium_confidence']}")
    print(f"Markdown: {args.md_path}")


if __name__ == "__main__":
    main()
