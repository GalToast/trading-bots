#!/usr/bin/env python3
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent.parent
OPTIMIZER_BOARD_PATH = ROOT / "reports" / "adaptive_optimizer_board.json"
BENCHMARK_RECON_PATH = ROOT / "reports" / "benchmark_engine_reconciliation.json"
BACKTEST_RECON_PATH = ROOT / "reports" / "backtest_reconciliation.json"
ALLOCATION_RECON_PATH = ROOT / "reports" / "allocation_optimizer_reconciliation.json"
OPTIMAL_PORTFOLIO_RECON_PATH = ROOT / "reports" / "optimal_portfolio_optimizer_reconciliation.json"
OUTPUT_JSON = ROOT / "reports" / "adaptive_optimizer_reconciliation_board.json"
OUTPUT_MD = ROOT / "reports" / "adaptive_optimizer_reconciliation_board.md"


SURFACE_RULES = {
    "allocation_optimizer": {
        "evidence_paths": [
            "reports/allocation_optimizer.json",
            "reports/fidelity_adjusted_optimizer.json",
            "reports/allocation_optimizer_reconciliation.json",
        ],
        "why": "Has naive plus post-hoc fidelity output. Canonical replay now exists and shows severe feasibility drift rather than alignment.",
        "next_action": "Constrain the optimizer to canonical min_entry_cash and deploy semantics, then replay again to see whether the projected portfolio still survives.",
    },
    "optimal_portfolio_optimizer": {
        "evidence_paths": [
            "reports/optimal_portfolio_optimizer.json",
            "reports/optimal_portfolio_optimizer_reconciliation.json",
        ],
        "why": "Strategy-assignment optimizer exists. Canonical replay now exists and shows strong simulator-to-canonical drift, not harness alignment.",
        "next_action": "Trace the remaining drift drivers, especially session gating and deploy fraction, before treating the strategy assignment as portfolio truth.",
    },
    "benchmark_engine_reference": {
        "evidence_paths": [
            "reports/benchmark_engine_reconciliation.json",
            "reports/backtest_reconciliation.json",
        ],
        "status": "aligned_reference",
        "why": "This surface proves a working reconciliation pattern already exists: framework and harness can be matched on the same candles and fee model.",
        "next_action": "Use this as the template for the reconcile-first optimizer surfaces instead of building a new semantics stack.",
    },
}


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def existing_paths(paths: list[str]) -> list[str]:
    found: list[str] = []
    for path in paths:
        if (ROOT / path).exists():
            found.append(path)
    return found


def benchmark_alignment_summary() -> dict[str, Any]:
    if not BENCHMARK_RECON_PATH.exists():
        return {"available": False}
    payload = load_json(BENCHMARK_RECON_PATH)
    models = payload.get("models") or {}
    deltas = {
        name: dict(result.get("delta") or {})
        for name, result in models.items()
    }
    all_zero = all(
        float(delta.get("net_pnl", 1.0) or 0.0) == 0.0
        and int(delta.get("trades", 1) or 0) == 0
        and float(delta.get("win_rate", 1.0) or 0.0) == 0.0
        and float(delta.get("max_drawdown", 1.0) or 0.0) == 0.0
        for delta in deltas.values()
    )
    return {
        "available": True,
        "symbol": payload.get("symbol"),
        "window_days": payload.get("window_days"),
        "all_zero_deltas": all_zero,
        "models": deltas,
    }


def allocation_reconciliation_summary() -> dict[str, Any]:
    if not ALLOCATION_RECON_PATH.exists():
        return {"available": False, "status": "unreconciled"}

    primary = load_json(ROOT / "reports" / "allocation_optimizer.json")
    payload = load_json(ALLOCATION_RECON_PATH)
    plans = list(((primary.get("canonical_reference") or {}).get("plans")) or list(payload.get("plans") or []))
    max_abs_delta = max(abs(float(plan.get("delta_vs_projected", 0.0) or 0.0)) for plan in plans) if plans else 0.0
    collapsed = [
        str(plan.get("plan_name"))
        for plan in plans
        if int(plan.get("feasible_count", 0) or 0) < int(plan.get("coin_count", 0) or 0)
    ]
    return {
        "available": True,
        "status": "reconciled_divergent",
        "source_mode": str(((primary.get("canonical_reference") or {}).get("source_mode")) or "sidecar_reconciliation"),
        "plan_count": len(plans),
        "max_abs_delta_vs_projected": round(max_abs_delta, 4),
        "collapsed_plans": collapsed,
    }


def optimal_portfolio_reconciliation_summary() -> dict[str, Any]:
    if not OPTIMAL_PORTFOLIO_RECON_PATH.exists():
        return {"available": False, "status": "unreconciled"}

    primary = load_json(ROOT / "reports" / "optimal_portfolio_optimizer.json")
    payload = load_json(OPTIMAL_PORTFOLIO_RECON_PATH)
    scenarios = list(((primary.get("canonical_reference") or {}).get("scenarios")) or list(payload.get("scenarios") or []))
    max_abs_delta = max(abs(float(row.get("delta_vs_projected", 0.0) or 0.0)) for row in scenarios) if scenarios else 0.0
    collapsed = [
        str(row.get("scenario_name"))
        for row in scenarios
        if int(row.get("feasible_count", 0) or 0) < int(row.get("coin_count", 0) or 0)
    ]
    return {
        "available": True,
        "status": "reconciled_divergent",
        "source_mode": str(((primary.get("canonical_reference") or {}).get("source_mode")) or "sidecar_reconciliation"),
        "scenario_count": len(scenarios),
        "max_abs_delta_vs_projected": round(max_abs_delta, 4),
        "collapsed_scenarios": collapsed,
    }


def build_rows() -> list[dict[str, Any]]:
    optimizer_board = load_json(OPTIMIZER_BOARD_PATH)
    optimizer_rows = {row["surface_id"]: row for row in optimizer_board.get("rows", [])}
    bench_summary = benchmark_alignment_summary()
    allocation_summary = allocation_reconciliation_summary()
    optimal_summary = optimal_portfolio_reconciliation_summary()

    rows: list[dict[str, Any]] = []
    for surface_id, rule in SURFACE_RULES.items():
        if surface_id == "benchmark_engine_reference":
            rows.append(
                {
                    "surface_id": surface_id,
                    "trust_level": "reference",
                    "status": rule["status"],
                    "evidence_found": existing_paths(rule["evidence_paths"]),
                    "why": rule["why"],
                    "next_action": rule["next_action"],
                    "alignment_summary": bench_summary,
                }
            )
            continue

        reconciliation_summary = allocation_summary if surface_id == "allocation_optimizer" else optimal_summary
        optimizer_row = optimizer_rows.get(surface_id, {})
        rows.append(
            {
                "surface_id": surface_id,
                "trust_level": optimizer_row.get("trust_level", ""),
                "status": reconciliation_summary["status"],
                "evidence_found": existing_paths(rule["evidence_paths"]),
                "why": rule["why"],
                "next_action": rule["next_action"],
                "optimizer_read": optimizer_row.get("read", ""),
                "reconciliation_summary": reconciliation_summary,
            }
        )
    return rows


def build_payload() -> dict[str, Any]:
    rows = build_rows()
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "summary": {
            "surface_count": len(rows),
            "unreconciled_count": sum(1 for row in rows if row["status"] == "unreconciled"),
            "reconciled_divergent_count": sum(1 for row in rows if row["status"] == "reconciled_divergent"),
            "aligned_reference_count": sum(1 for row in rows if row["status"] == "aligned_reference"),
        },
        "rows": rows,
    }


def write_markdown(payload: dict[str, Any]) -> None:
    lines = [
        "# Adaptive Optimizer Reconciliation Board",
        "",
        "This board answers which reconcile-first optimizer surfaces still lack harness confirmation and which existing reconciliation artifact should be copied as the template.",
        "",
        "## Current Read",
        "",
        f"- unreconciled surfaces: `{payload['summary']['unreconciled_count']}`",
        f"- replayed but divergent surfaces: `{payload['summary']['reconciled_divergent_count']}`",
        f"- aligned references: `{payload['summary']['aligned_reference_count']}`",
        "",
        "## Rows",
        "",
        "| Surface | Trust | Status | Evidence | Read |",
        "|---|---|---|---|---|",
    ]

    for row in payload["rows"]:
        evidence_count = len(row.get("evidence_found") or [])
        read = row.get("optimizer_read") or row.get("why") or "-"
        lines.append(
            f"| `{row['surface_id']}` | `{row['trust_level']}` | `{row['status']}` | {evidence_count} | {read} |"
        )

    lines.extend(["", "## Next Actions", ""])
    for row in payload["rows"]:
        lines.append(f"### {row['surface_id']}")
        lines.append(f"- why: {row['why']}")
        lines.append(f"- next action: {row['next_action']}")
        if row.get("evidence_found"):
            lines.append("- evidence found: " + ", ".join(f"`{item}`" for item in row["evidence_found"]))
        reconciliation_summary = row.get("reconciliation_summary") or {}
        if reconciliation_summary.get("available"):
            if row["surface_id"] == "allocation_optimizer":
                lines.append(
                    f"- reconciliation read: status `{reconciliation_summary.get('status')}`, "
                    f"source_mode=`{reconciliation_summary.get('source_mode')}`, "
                    f"collapsed_plans=`{reconciliation_summary.get('collapsed_plans')}`, "
                    f"max_abs_delta_vs_projected=`{reconciliation_summary.get('max_abs_delta_vs_projected')}`"
                )
            elif row["surface_id"] == "optimal_portfolio_optimizer":
                lines.append(
                    f"- reconciliation read: status `{reconciliation_summary.get('status')}`, "
                    f"source_mode=`{reconciliation_summary.get('source_mode')}`, "
                    f"collapsed_scenarios=`{reconciliation_summary.get('collapsed_scenarios')}`, "
                    f"max_abs_delta_vs_projected=`{reconciliation_summary.get('max_abs_delta_vs_projected')}`"
                )
        alignment_summary = row.get("alignment_summary") or {}
        if alignment_summary.get("available"):
            lines.append(
                f"- benchmark reference: symbol `{alignment_summary.get('symbol')}`, "
                f"window `{alignment_summary.get('window_days')}`d, "
                f"all_zero_deltas=`{alignment_summary.get('all_zero_deltas')}`"
            )
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
