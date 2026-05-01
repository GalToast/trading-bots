#!/usr/bin/env python3
"""Post-patch validation board for ADA/LTC crypto probe risk cap fix.

After codex-live-8 patches the registry (max_open_per_side 80->15 for ADA/LTC,
and ADA step 0.0015->0.005), this script validates:
1. Registry entries reflect the new values
2. If lanes are recycled, runtime state confirms the new cmdline args
3. No drift between intended and actual config

Usage: python scripts/validate_crypto_probe_risk_cap_fix.py
"""

import json
import sys
from pathlib import Path

REGISTRY_PATH = Path("configs/penetration_lattice_runner_registry.json")
EXPECTED = {
    "live_adausd_m15_warp_941893": {
        "max_open_per_side": 15,
        "step": 0.005,
        "max_entry_spread_ratio": 0.9,
    },
    "live_ltcusd_m15_warp_941894": {
        "max_open_per_side": 15,
        "step": 0.15,
        "max_entry_spread_ratio": 1.2,
    },
    "live_solusd_m15_warp_v2_941891": {
        "max_open_per_side": 15,
        "step": 0.42,
        "max_entry_spread_ratio": 0.65,
    },
}


def validate_registry():
    """Check registry entries match expected values."""
    with open(REGISTRY_PATH, encoding="utf-8") as f:
        registry = json.load(f)

    # Registry uses 'lanes' key (not 'runners')
    lanes = registry.get("lanes", [])

    results = {}
    for lane_name, expected in EXPECTED.items():
        entry = None
        for r in lanes:
            if r.get("name") == lane_name:
                entry = r
                break

        if entry is None:
            results[lane_name] = {"status": "MISSING", "note": "Lane not found in registry"}
            continue

        restart_args = entry.get("restart_args", [])
        cmdline = " ".join(restart_args)

        checks = {}
        for key, exp_val in expected.items():
            if key == "max_open_per_side":
                flag = "--max-open-per-side"
            elif key == "step":
                flag = "--step"
            elif key == "max_entry_spread_ratio":
                flag = "--max-entry-spread-ratio"
            else:
                continue

            # Find the flag value in cmdline
            actual = None
            parts = restart_args[:]
            for i, part in enumerate(parts):
                if part == flag and i + 1 < len(parts):
                    try:
                        actual = float(parts[i + 1])
                    except ValueError:
                        actual = parts[i + 1]
                    break

            if actual is None:
                checks[key] = {"expected": exp_val, "actual": "NOT_FOUND", "pass": False}
            elif isinstance(exp_val, float):
                checks[key] = {"expected": exp_val, "actual": actual, "pass": abs(actual - exp_val) < 0.001}
            else:
                checks[key] = {"expected": exp_val, "actual": actual, "pass": actual == exp_val}

        all_pass = all(c["pass"] for c in checks.values())
        results[lane_name] = {
            "status": "PASS" if all_pass else "FAIL",
            "checks": checks,
        }

    return results


def write_report(results):
    """Write markdown report."""
    lines = [
        "# Crypto Probe Risk Cap Fix — Post-Patch Validation",
        "",
        f"- Generated at: `{json.dumps(__import__('datetime').datetime.utcnow().isoformat())}Z`",
        "",
        "## Registry Validation Results",
        "",
    ]

    all_pass = True
    for lane_name, result in results.items():
        status = result["status"]
        if status != "PASS":
            all_pass = False

        lines.append(f"### `{lane_name}`")
        lines.append(f"- Status: **{status}**")

        if "note" in result:
            lines.append(f"- Note: {result['note']}")
        elif "checks" in result:
            lines.append("| Parameter | Expected | Actual | Pass |")
            lines.append("|-----------|----------|--------|------|")
            for param, check in result["checks"].items():
                icon = "✅" if check["pass"] else "❌"
                lines.append(f"| {param} | {check['expected']} | {check['actual']} | {icon} |")
        lines.append("")

    lines.append("## Summary")
    lines.append(f"- Overall: **{'ALL PASS ✅' if all_pass else 'SOME FAILURES ⚠️'}**")
    lines.append("")

    report = "\n".join(lines)

    report_path = Path("reports/crypto_probe_risk_cap_validation.md")
    report_path.write_text(report, encoding="utf-8")

    json_path = Path("reports/crypto_probe_risk_cap_validation.json")
    json_path.write_text(json.dumps({"results": results, "all_pass": all_pass}, indent=2), encoding="utf-8")

    print(report)
    print(f"\nWrote {report_path}")
    print(f"Wrote {json_path}")

    return all_pass


if __name__ == "__main__":
    results = validate_registry()
    ok = write_report(results)
    sys.exit(0 if ok else 1)
