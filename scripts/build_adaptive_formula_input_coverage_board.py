#!/usr/bin/env python3
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent.parent
REPORTS = ROOT / "reports"
CONFIGS = ROOT / "configs"

PROOF_BOARD_PATH = REPORTS / "adaptive_lattice_proof_board.json"
REGIME_LIVE_PATH = REPORTS / "regime_classification_live.json"
SHAPE_LIBRARY_PATH = CONFIGS / "adaptive_lattice_shape_library.json"
OUTPUT_JSON_PATH = REPORTS / "adaptive_formula_input_coverage_board.json"
OUTPUT_MD_PATH = REPORTS / "adaptive_formula_input_coverage_board.md"


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def load_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8-sig"))
    return payload if isinstance(payload, dict) else {}


def relative_path_text(path: Path) -> str:
    try:
        return str(path.relative_to(ROOT))
    except ValueError:
        return str(path)


def find_symbol_row(rows: list[dict[str, Any]], symbol: str) -> dict[str, Any] | None:
    clean_symbol = str(symbol or "").upper()
    for row in rows:
        if str(row.get("symbol") or "").upper() == clean_symbol:
            return dict(row)
    return None


def find_shape(library: dict[str, Any], symbol: str, shape_id: str) -> dict[str, Any] | None:
    symbol_payload = dict((library.get("symbols") or {}).get(symbol) or {})
    for shape in list(symbol_payload.get("candidate_shapes") or []):
        if str(shape.get("shape_id") or "") == str(shape_id or ""):
            return dict(shape)
    return None


def required_fields_for_step_kind(kind: str) -> list[str]:
    if kind == "range_atr_formula":
        return ["current_atr", "avg_range", "range_atr_ratio"]
    if kind in {"atr_multiple", "atr_multiple_asymmetric"}:
        return ["current_atr"]
    return []


def present_field_names(row: dict[str, Any]) -> list[str]:
    present = []
    for key, value in row.items():
        if value not in (None, "", [], {}):
            present.append(str(key))
    return sorted(present)


def parse_iso_datetime(value: str) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed


def age_hours_read(generated_at: str) -> float | None:
    parsed = parse_iso_datetime(generated_at)
    if parsed is None:
        return None
    delta = datetime.now(timezone.utc) - parsed.astimezone(timezone.utc)
    return round(max(delta.total_seconds(), 0.0) / 3600.0, 2)


def source_detail(surface_id: str, path: Path, payload: dict[str, Any]) -> dict[str, Any]:
    generated_at = str(payload.get("generated_at") or "")
    age_hours = age_hours_read(generated_at)
    read = "missing generated_at"
    if generated_at:
        if age_hours is None:
            read = f"generated_at unreadable ({generated_at})"
        else:
            read = f"fresh snapshot ({age_hours}h old)"
    return {
        "surface_id": surface_id,
        "path": relative_path_text(path),
        "generated_at": generated_at,
        "age_hours": age_hours,
        "read": read,
    }


def classify_formula_readiness(kind: str, required: list[str], present: list[str], row: dict[str, Any]) -> tuple[str, str]:
    present_set = set(present)
    missing = [field for field in required if field not in present_set]
    if kind == "range_atr_formula":
        if not missing:
            return (
                "true_range_atr_ready",
                "The live regime surface exposes the full range/ATR input set required by the advertised formula.",
            )
        if "current_atr" in present_set and "step_coeff" in present_set:
            return (
                "fallback_only_current_atr_step_coeff",
                "The advertised range/ATR formula is missing `avg_range` and/or `range_atr_ratio`, so current control can only degrade to an ATR-based fallback.",
            )
        return (
            "formula_input_debt",
            "The advertised range/ATR formula is missing even the basic live inputs needed for an honest fallback.",
        )
    if kind in {"atr_multiple", "atr_multiple_asymmetric"}:
        if missing:
            return (
                "formula_input_debt",
                "This ATR-based adaptive step still lacks `current_atr` in the live regime surface.",
            )
        return (
            "atr_ready",
            "The live regime surface exposes the ATR input required by this step method.",
        )
    return (
        "manual_review_unknown_step_kind",
        "This step method is not yet covered by the formula-input board and needs manual review.",
    )


def build_payload(
    proof_payload: dict[str, Any],
    regime_payload: dict[str, Any],
    shape_library: dict[str, Any],
) -> dict[str, Any]:
    proof_rows = list(proof_payload.get("rows") or [])
    regime_rows = list(regime_payload.get("symbols") or [])

    rows: list[dict[str, Any]] = []
    verdict_counts: dict[str, int] = {}
    for proof_row in proof_rows:
        symbol = str(proof_row.get("symbol") or "")
        shape_id = str(proof_row.get("recommended_shape_id") or "")
        shape = find_shape(shape_library, symbol, shape_id) or {}
        step_method = dict(shape.get("step_method") or {})
        kind = str(step_method.get("kind") or "")
        regime_row = find_symbol_row(regime_rows, symbol) or {}
        present = present_field_names(regime_row)
        required = required_fields_for_step_kind(kind)
        missing = [field for field in required if field not in set(present)]
        stage = str(proof_row.get("stage") or "").lower()
        status = str(proof_row.get("status") or "").lower()
        if "blocked" in stage or status == "blocked":
            verdict, rationale = (
                "blocked_runtime_family",
                "This adaptive row is blocked before formula-input honesty matters; runtime-family repair is still the first gate.",
            )
        else:
            verdict, rationale = classify_formula_readiness(kind, required, present, regime_row)
        verdict_counts[verdict] = verdict_counts.get(verdict, 0) + 1
        rows.append(
            {
                "symbol": symbol,
                "stage": str(proof_row.get("stage") or ""),
                "shape_id": shape_id,
                "family": str(proof_row.get("family") or shape.get("family") or ""),
                "step_kind": kind,
                "step_read": str(proof_row.get("step_read") or ""),
                "formula_basis": str(step_method.get("basis") or ""),
                "required_fields": required,
                "present_fields": present,
                "missing_fields": missing,
                "fallback_fields_present": [field for field in ("current_atr", "step_coeff", "regime") if field in set(present)],
                "verdict": verdict,
                "rationale": rationale,
            }
        )

    stale_formula_symbols = [row["symbol"] for row in rows if row["verdict"] in {"fallback_only_current_atr_step_coeff", "formula_input_debt"}]
    proof_detail = source_detail("adaptive_lattice_proof_board", PROOF_BOARD_PATH, proof_payload)
    regime_detail = source_detail("regime_classification_live", REGIME_LIVE_PATH, regime_payload)
    library_detail = source_detail("adaptive_lattice_shape_library", SHAPE_LIBRARY_PATH, shape_library)
    source_details = [proof_detail, regime_detail, library_detail]
    return {
        "generated_at": utc_now_iso(),
        "sources": [
            relative_path_text(PROOF_BOARD_PATH),
            relative_path_text(REGIME_LIVE_PATH),
            relative_path_text(SHAPE_LIBRARY_PATH),
        ],
        "source_details": source_details,
        "summary": {
            "symbol_count": len(rows),
            "verdict_counts": verdict_counts,
            "formula_input_debt_symbols": stale_formula_symbols,
        },
        "leadership_read": [
            (
                f"Current adaptive formula debt is concentrated in `{stale_formula_symbols}`."
                if stale_formula_symbols
                else "Current adaptive proof rows all have the inputs their advertised step methods require."
            ),
            f"Source freshness: proof=`{proof_detail['read']}`, regime=`{regime_detail['read']}`, library=`{library_detail['read']}`.",
            "ATR-based adaptive shapes are only as honest as `current_atr` coverage; range/ATR formulas require `avg_range` and `range_atr_ratio`, not just `step_coeff`.",
            "Use this board to separate true formula readiness from silent fallback readiness before calling a lane genuinely adaptive.",
        ],
        "rows": rows,
        "notes": [
            "This board is passive. It audits formula-input honesty only; it does not patch controller or runner logic.",
            "A `fallback_only_current_atr_step_coeff` verdict means the live regime surface can still drive an ATR-based step, but not the richer range/ATR formula advertised by the shape library.",
            "A `blocked_runtime_family` verdict is keyed off the proof stage/status, not whether a shape row happened to resolve cleanly.",
        ],
    }


def render_markdown(payload: dict[str, Any]) -> str:
    summary = dict(payload.get("summary") or {})
    lines = [
        "# Adaptive Formula Input Coverage Board",
        "",
        "> Passive audit of whether current adaptive-lattice formulas have the live inputs they claim to use.",
        "",
        f"- generated_at: `{payload.get('generated_at')}`",
        "",
        "## Source Details",
        "",
    ]
    for detail in list(payload.get("source_details") or []):
        lines.append(
            f"- `{detail.get('surface_id')}`: `{detail.get('read')}` "
            f"(generated_at=`{detail.get('generated_at')}`, path=`{detail.get('path')}`)"
        )
    lines.extend(
        [
            "",
        "## Leadership Read",
        "",
        ]
    )
    for item in list(payload.get("leadership_read") or []):
        lines.append(f"- {item}")
    lines.extend(
        [
            "",
            "## Summary",
            "",
            f"- symbol_count: `{summary.get('symbol_count')}`",
            f"- verdict_counts: `{summary.get('verdict_counts')}`",
            f"- formula_input_debt_symbols: `{summary.get('formula_input_debt_symbols')}`",
            "",
            "## Rows",
            "",
            "| Symbol | Shape | Step Kind | Required | Missing | Verdict |",
            "| --- | --- | --- | --- | --- | --- |",
        ]
    )
    for row in list(payload.get("rows") or []):
        lines.append(
            f"| `{row.get('symbol')}` | `{row.get('shape_id')}` | `{row.get('step_kind')}` | "
            f"`{row.get('required_fields')}` | `{row.get('missing_fields')}` | `{row.get('verdict')}` |"
        )
    lines.extend(["", "## Detail", ""])
    for row in list(payload.get("rows") or []):
        lines.extend(
            [
                f"### {row.get('symbol')}",
                "",
                f"- stage: `{row.get('stage')}`",
                f"- shape_id: `{row.get('shape_id')}`",
                f"- step_kind: `{row.get('step_kind')}`",
                f"- step_read: `{row.get('step_read')}`",
                f"- formula_basis: `{row.get('formula_basis')}`",
                f"- required_fields: `{row.get('required_fields')}`",
                f"- present_fields: `{row.get('present_fields')}`",
                f"- missing_fields: `{row.get('missing_fields')}`",
                f"- fallback_fields_present: `{row.get('fallback_fields_present')}`",
                f"- verdict: `{row.get('verdict')}`",
                f"- rationale: {row.get('rationale')}",
                "",
            ]
        )
    lines.extend(["## Notes", ""])
    for note in list(payload.get("notes") or []):
        lines.append(f"- {note}")
    lines.append("")
    return "\n".join(lines)


def main() -> None:
    payload = build_payload(
        load_json(PROOF_BOARD_PATH),
        load_json(REGIME_LIVE_PATH),
        load_json(SHAPE_LIBRARY_PATH),
    )
    OUTPUT_JSON_PATH.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    OUTPUT_MD_PATH.write_text(render_markdown(payload), encoding="utf-8")


if __name__ == "__main__":
    main()
