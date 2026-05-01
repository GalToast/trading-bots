#!/usr/bin/env python3
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent.parent
ALLOCATION_PATH = ROOT / "reports" / "allocation_optimizer.json"
PORTFOLIO_PATH = ROOT / "reports" / "optimal_portfolio_optimizer.json"
RECON_PATH = ROOT / "reports" / "adaptive_optimizer_reconciliation_board.json"
OUTPUT_JSON = ROOT / "reports" / "adaptive_optimizer_decision_board.json"
OUTPUT_MD = ROOT / "reports" / "adaptive_optimizer_decision_board.md"


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def build_payload() -> dict[str, Any]:
    allocation = load_json(ALLOCATION_PATH)
    portfolio = load_json(PORTFOLIO_PATH)
    recon = load_json(RECON_PATH)
    recon_rows = {row["surface_id"]: row for row in recon.get("rows", [])}

    allocation_plans = list((allocation.get("canonical_reference") or {}).get("plans") or [])
    portfolio_scenarios = list((portfolio.get("canonical_reference") or {}).get("scenarios") or [])
    portfolio_drift = dict(((portfolio.get("canonical_reference") or {}).get("drift_attribution") or {}).get("component_effects") or {})

    rows = [
        {
            "surface_id": "allocation_optimizer",
            "status": recon_rows["allocation_optimizer"]["status"],
            "source_mode": (allocation.get("canonical_reference") or {}).get("source_mode", ""),
            "primary_read": "Useful for allocation hypotheses, but current native plans collapse to one- or two-sleeve canonical survivors.",
            "decision": "Use canonical plan rows only; do not treat the native $48 allocation recommendations as deployment truth.",
            "highlights": [
                {
                    "label": str(row.get("plan_name")),
                    "feasible": f"{int(row.get('feasible_count', 0) or 0)}/{int(row.get('coin_count', 0) or 0)}",
                    "projected_total_pnl": float(row.get("projected_total_pnl", 0.0) or 0.0),
                    "canonical_total_pnl": float(row.get("canonical_total_pnl", 0.0) or 0.0),
                    "delta_vs_projected": float(row.get("delta_vs_projected", 0.0) or 0.0),
                }
                for row in allocation_plans
            ],
        },
        {
            "surface_id": "optimal_portfolio_optimizer",
            "status": recon_rows["optimal_portfolio_optimizer"]["status"],
            "source_mode": (portfolio.get("canonical_reference") or {}).get("source_mode", ""),
            "primary_read": "The best-strategy assignment survives only in the $100/coin case, and most of its native-vs-canonical gap is session gating.",
            "decision": "Use the assignment as a research prior, but compare only against the inline canonical scenario and drift block.",
            "highlights": [
                {
                    "label": str(row.get("scenario_name")),
                    "feasible": f"{int(row.get('feasible_count', 0) or 0)}/{int(row.get('coin_count', 0) or 0)}",
                    "projected_total_pnl": float(row.get("projected_total_pnl", 0.0) or 0.0),
                    "canonical_total_pnl": float(row.get("canonical_total_pnl", 0.0) or 0.0),
                    "delta_vs_projected": float(row.get("delta_vs_projected", 0.0) or 0.0),
                }
                for row in portfolio_scenarios
            ],
            "drift_attribution": portfolio_drift,
        },
    ]

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "summary": {
            "dual_mode_surfaces": sum(1 for row in rows if row["source_mode"] == "native_inline_replay"),
            "decision_ready_surfaces": len(rows),
        },
        "rows": rows,
    }


def write_markdown(payload: dict[str, Any]) -> None:
    lines = [
        "# Adaptive Optimizer Decision Board",
        "",
        "This board compresses the current optimizer truth into one operator-facing read now that both primary optimizer reports emit native and canonical results together.",
        "",
        "## Current Read",
        "",
        f"- dual-mode surfaces: `{payload['summary']['dual_mode_surfaces']}`",
        f"- decision-ready surfaces: `{payload['summary']['decision_ready_surfaces']}`",
        "",
    ]

    for row in payload["rows"]:
        lines.append(f"## {row['surface_id']}")
        lines.append(f"- status: `{row['status']}`")
        lines.append(f"- source mode: `{row['source_mode']}`")
        lines.append(f"- read: {row['primary_read']}")
        lines.append(f"- decision: {row['decision']}")
        lines.append("")
        lines.append("| Scenario | Feasible | Projected | Canonical | Delta |")
        lines.append("|---|---|---:|---:|---:|")
        for item in row["highlights"]:
            lines.append(
                f"| `{item['label']}` | `{item['feasible']}` | {item['projected_total_pnl']} | "
                f"{item['canonical_total_pnl']} | {item['delta_vs_projected']} |"
            )
        drift = row.get("drift_attribution") or {}
        if drift:
            lines.append("")
            lines.append("Drift attribution:")
            for key, value in drift.items():
                lines.append(f"- `{key}` = `{value}`")
        lines.append("")

    OUTPUT_MD.write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    payload = build_payload()
    OUTPUT_JSON.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    write_markdown(payload)
    print(f"Wrote {OUTPUT_JSON}")
    print(f"Wrote {OUTPUT_MD}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
