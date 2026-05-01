#!/usr/bin/env python3
"""
Shape library profit-mode alignment audit.

Reads configs/adaptive_lattice_shape_library.json and audits every shape
against the profit-mode classifier's mode taxonomy. Reports:
  - Which profit_mode each shape matches
  - Which modes have NO strong shape candidate per symbol (gap analysis)
  - Monetization profiles that don't align with any mode (orphan profiles)
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

try:
    from profit_mode_classifier import score_shape_for_mode
except ImportError:
    from scripts.profit_mode_classifier import score_shape_for_mode

# All canonical profit modes from the classifier
ALL_MODES = [
    "micro_harvest",
    "trend_harvest",
    "cash_repair_harvest",
    "friction_survivor",
    "guarded_toxic_flow",
    "balanced_harvest",
]

# All known monetization profiles in the shape library
KNOWN_PROFILES = {
    "cash_harvest",
    "friction_survivor",
    "trend_harvest",
    "trend_extension",
    "breakout_extension",
    "balanced",
    "micro_harvest",
}

ROOT = Path(__file__).resolve().parent.parent
LIBRARY_PATH = ROOT / "configs" / "adaptive_lattice_shape_library.json"


def load_library() -> dict[str, Any]:
    with LIBRARY_PATH.open("r", encoding="utf-8") as f:
        return json.load(f)


def shape_mode_scores(shape: dict[str, Any]) -> dict[str, float]:
    """Score a shape against all profit modes."""
    scores = {}
    for mode in ALL_MODES:
        scores[mode] = score_shape_for_mode(shape, mode, mode_confidence=1.0)
    return scores


def best_mode_for_shape(shape: dict[str, Any]) -> tuple[str, float]:
    """Return the best-matching profit mode for a shape."""
    scores = shape_mode_scores(shape)
    best = max(scores, key=scores.get)  # type: ignore
    return best, scores[best]


def audit_library() -> dict[str, Any]:
    library = load_library()
    symbols = library.get("symbols", {})

    audit = {
        "library_version": library.get("version"),
        "symbol_audits": {},
        "global_gaps": [],
        "orphan_profiles": [],
        "summary": {},
    }

    # Track which modes have candidates across all symbols
    mode_coverage: dict[str, list[str]] = {mode: [] for mode in ALL_MODES}

    for symbol, symbol_data in sorted(symbols.items()):
        candidates = symbol_data.get("candidate_shapes", [])
        symbol_audit: dict[str, Any] = {
            "stage": symbol_data.get("stage"),
            "preferred_family": symbol_data.get("preferred_family"),
            "shape_count": len(candidates),
            "shapes": [],
            "mode_gaps": [],
        }

        covered_modes: set[str] = set()

        for shape in candidates:
            shape_id = shape.get("shape_id", "unknown")
            monetization = shape.get("monetization_profile", "")
            best_mode, best_score = best_mode_for_shape(shape)
            all_scores = shape_mode_scores(shape)

            # Track which modes have at least one strong candidate (score > 0)
            strong_modes = [m for m, s in all_scores.items() if s > 0]
            for mode in strong_modes:
                covered_modes.add(mode)
                mode_coverage[mode].append(f"{symbol}/{shape_id}")

            shape_info = {
                "shape_id": shape_id,
                "family": shape.get("family"),
                "monetization_profile": monetization,
                "best_profit_mode": best_mode,
                "best_mode_score": round(best_score, 2),
                "all_mode_scores": {m: round(s, 2) for m, s in all_scores.items()},
                "strong_modes": strong_modes,
                "is_orphan": monetization not in KNOWN_PROFILES and monetization != "",
            }
            symbol_audit["shapes"].append(shape_info)

            # Check for orphan profiles
            if shape_info["is_orphan"] and monetization:
                audit["orphan_profiles"].append({
                    "symbol": symbol,
                    "shape_id": shape_id,
                    "monetization_profile": monetization,
                })

        # Identify modes with NO strong candidate for this symbol
        for mode in ALL_MODES:
            if mode not in covered_modes:
                symbol_audit["mode_gaps"].append(mode)

        audit["symbol_audits"][symbol] = symbol_audit

    # Global gaps: modes that have NO candidates across ANY symbol
    for mode in ALL_MODES:
        if not mode_coverage[mode]:
            audit["global_gaps"].append(mode)

    # Summary statistics
    total_shapes = sum(s["shape_count"] for s in audit["symbol_audits"].values())
    audit["summary"] = {
        "total_symbols": len(symbols),
        "total_shapes": total_shapes,
        "orphan_profile_count": len(audit["orphan_profiles"]),
        "global_gap_modes": audit["global_gaps"],
        "mode_coverage": {mode: len(candidates) for mode, candidates in mode_coverage.items()},
    }

    return audit


def print_audit(audit: dict[str, Any]) -> None:
    print("=" * 80)
    print("SHAPE LIBRARY PROFIT-MODE ALIGNMENT AUDIT")
    print("=" * 80)
    print(f"Library version: {audit['library_version']}")
    print(f"Total symbols: {audit['summary']['total_symbols']}")
    print(f"Total shapes: {audit['summary']['total_shapes']}")
    print(f"Orphan profiles: {audit['summary']['orphan_profile_count']}")
    print()

    # Mode coverage summary
    print("MODE COVERAGE (shapes with score > 0):")
    for mode, count in sorted(audit["summary"]["mode_coverage"].items(), key=lambda x: -x[1]):
        print(f"  {mode}: {count} shape(s)")
    print()

    # Global gaps
    if audit["global_gaps"]:
        print(f"⚠️  GLOBAL GAP: No strong candidates for: {', '.join(audit['global_gaps'])}")
        print()

    # Per-symbol audit
    for symbol, sym_audit in audit["symbol_audits"].items():
        print(f"--- {symbol} (stage={sym_audit['stage']}, {sym_audit['shape_count']} shapes) ---")

        if sym_audit["mode_gaps"]:
            print(f"  ⚠️  MODE GAPS: {', '.join(sym_audit['mode_gaps'])}")

        for shape in sym_audit["shapes"]:
            orphan_tag = " [ORPHAN]" if shape["is_orphan"] else ""
            print(f"  {shape['shape_id']}:")
            print(f"    monetization={shape['monetization_profile']}{orphan_tag}")
            print(f"    best_mode={shape['best_profit_mode']} (score={shape['best_mode_score']})")
            print(f"    strong_modes={shape['strong_modes']}")
        print()


def main() -> None:
    audit = audit_library()
    print_audit(audit)

    # Write JSON report
    report_path = ROOT / "reports" / "shape_library_profit_mode_audit.json"
    with report_path.open("w", encoding="utf-8") as f:
        json.dump(audit, f, indent=2, default=str)
    print(f"\nWrote report: {report_path}")


if __name__ == "__main__":
    main()
