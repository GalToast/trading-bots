#!/usr/bin/env python3
"""Build a compact cross-symbol transfer board for adaptive lattice geometry."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
TRANSFER_PATH = ROOT / "configs" / "adaptive_lattice_transfer_library.json"
SHAPE_LIBRARY_PATH = ROOT / "configs" / "adaptive_lattice_shape_library.json"
PROOF_BOARD_PATH = ROOT / "reports" / "adaptive_lattice_proof_board.json"
OUTPUT_MD = ROOT / "reports" / "adaptive_transfer_board.md"
OUTPUT_JSON = ROOT / "reports" / "adaptive_transfer_board.json"


def load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def build_rows(transfer_library: dict, shape_library: dict, proof_board: dict) -> list[dict]:
    symbols = shape_library.get("symbols", {})
    proof_rows = {row["symbol"]: row for row in proof_board.get("rows", [])}
    rows: list[dict] = []

    for target in transfer_library.get("targets", []):
        symbol = target["symbol"]
        symbol_data = symbols.get(symbol, {})
        proof_row = proof_rows.get(symbol, {})
        blockers = list(proof_row.get("blockers") or [])
        verdict = target["verdict"]
        rationale = target["rationale"]
        constraints = list(target.get("constraints", []))
        if verdict == "blocked" and proof_row.get("status") != "blocked" and not blockers:
            verdict = "adapt_first"
            rationale = (
                "The old bounded-family close_style runtime fault is no longer active in current proof surfaces, "
                "so USDJPY should move back to bounded-proof evaluation instead of staying blocked on archival crash residue."
            )
            constraints = [
                "Require fresh bounded forward proof before any live claim.",
                "Do not treat archival USDJPY err logs as a current blocker unless they are newer than the current core code.",
            ]
        shape_id = target.get("recommended_shape_id", "")
        candidate = next(
            (item for item in symbol_data.get("candidate_shapes", []) if item.get("shape_id") == shape_id),
            None,
        )
        rows.append(
            {
                "symbol": symbol,
                "verdict": verdict,
                "stage": proof_row.get("stage", symbol_data.get("stage", "")),
                "source_stage": symbol_data.get("stage", ""),
                "family": candidate.get("family", proof_row.get("family", "")) if candidate else proof_row.get("family", ""),
                "recommended_shape_id": shape_id,
                "observed_regime": proof_row.get("observed_regime", "mixed"),
                "step_read": format_step(candidate, proof_row),
                "close_read": format_close(candidate, proof_row),
                "rationale": rationale,
                "constraints": constraints,
                "blockers": blockers,
                "evidence_paths": target.get("evidence_paths", []),
            }
        )
    return rows


def format_step(candidate: dict | None, proof_row: dict) -> str:
    if candidate:
        step_method = candidate.get("step_method", {})
        kind = step_method.get("kind")
        if kind == "atr_multiple_asymmetric":
            return f"ATR sell={step_method.get('sell_coeff')} buy={step_method.get('buy_coeff')}"
        if kind == "atr_multiple":
            return f"ATR coeff={step_method.get('coeff')}"
        if kind == "range_atr_formula":
            return "range/ATR adaptive formula"
    return proof_row.get("step_read", "-")


def format_close(candidate: dict | None, proof_row: dict) -> str:
    if candidate:
        close = candidate.get("close", {})
        if "sell_gap" in close:
            return (
                f"style={close.get('style')} alpha={close.get('alpha')} "
                f"sell_gap={close.get('sell_gap')} buy_gap={close.get('buy_gap')}"
            )
        if "bounded_close_gap" in close:
            return (
                f"style={close.get('style')} bounded_close_gap={close.get('bounded_close_gap')} "
                f"same_bar_min_pnl={close.get('same_bar_min_pnl')}"
            )
    return proof_row.get("close_read", "-")


def build_payload() -> dict:
    transfer_library = load_json(TRANSFER_PATH)
    shape_library = load_json(SHAPE_LIBRARY_PATH)
    proof_board = load_json(PROOF_BOARD_PATH)
    rows = build_rows(transfer_library, shape_library, proof_board)

    summary = {
        "donor_symbol": transfer_library["donor"]["symbol"],
        "donor_shape_id": transfer_library["donor"]["shape_id"],
        "counts_by_verdict": {
            verdict: sum(1 for row in rows if row["verdict"] == verdict)
            for verdict in sorted({row["verdict"] for row in rows})
        },
    }
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "version": transfer_library["version"],
        "summary": summary,
        "donor": transfer_library["donor"],
        "rows": rows,
    }


def write_markdown(payload: dict) -> None:
    lines = [
        "# Adaptive Lattice Transfer Board",
        "",
        "This board turns the strongest current FX donor geometry into explicit symbol-level transfer guidance.",
        "",
        "## Donor",
        "",
        f"- `{payload['donor']['symbol']}` / `{payload['donor']['shape_id']}`",
        f"- {payload['donor']['read']}",
        "",
        "## Current Read",
        "",
    ]

    verdict_counts = payload["summary"]["counts_by_verdict"]
    lines.extend(
        [
            f"- `donor_reference`: `{verdict_counts.get('donor_reference', 0)}`",
            f"- `adapt_first`: `{verdict_counts.get('adapt_first', 0)}`",
            f"- `reject_for_now`: `{verdict_counts.get('reject_for_now', 0)}`",
            f"- `blocked`: `{verdict_counts.get('blocked', 0)}`",
            "",
            "## Rows",
            "",
            "| Symbol | Verdict | Stage | Shape | Step | Close | Read |",
            "|---|---|---|---|---|---|---|",
        ]
    )

    for row in payload["rows"]:
        lines.append(
            f"| {row['symbol']} | {row['verdict']} | {row['stage']} | "
            f"`{row['recommended_shape_id']}` | {row['step_read']} | {row['close_read']} | {row['rationale']} |"
        )

    lines.extend(["", "## Constraints", ""])
    for row in payload["rows"]:
        if not row["constraints"] and not row["blockers"]:
            continue
        lines.append(f"### {row['symbol']}")
        for item in row["constraints"]:
            lines.append(f"- {item}")
        for blocker in row["blockers"]:
            lines.append(f"- active blocker: `{blocker}`")
        lines.append("")

    OUTPUT_MD.write_text("\n".join(lines), encoding="utf-8")


def main() -> None:
    payload = build_payload()
    OUTPUT_JSON.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    write_markdown(payload)
    print(f"Wrote {OUTPUT_MD}")
    print(f"Wrote {OUTPUT_JSON}")


if __name__ == "__main__":
    main()
