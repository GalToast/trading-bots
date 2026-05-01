#!/usr/bin/env python3
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent.parent
REPORTS_DIR = ROOT / "reports"
LAUNCH_SAFETY_PATH = REPORTS_DIR / "hungry_hippo_launch_safety_validation.json"
OUTPUT_JSON_PATH = REPORTS_DIR / "hungry_hippo_launch_contract_triage_board.json"
OUTPUT_MD_PATH = REPORTS_DIR / "hungry_hippo_launch_contract_triage_board.md"


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def normalize_reason_list(value: Any) -> list[str]:
    return [str(item or "").strip() for item in list(value or []) if str(item or "").strip()]


def infer_triage_category(row: dict[str, Any], metadata: dict[str, Any]) -> tuple[str, str, str]:
    hard_fail_reasons = set(normalize_reason_list(row.get("hard_fail_reasons")))
    validation_status = str(metadata.get("validation_status") or "").strip().lower()
    runner_family = str(row.get("runner_family") or "")
    symbol = str(row.get("symbol") or "")

    if "handoff" in validation_status:
        return (
            "retire_historical",
            "Shadow handoff artifact from an older ETH retune branch; keep disabled and stop treating it as an active HH launch candidate.",
            "Keep disabled, preserve only as handoff history, and route fresh ETH work through the current normalized-control surfaces instead.",
        )

    if "legacy_bar_runner_not_current_escape_contract" in hard_fail_reasons or runner_family == "legacy_bar_shadow":
        return (
            "retire_historical",
            "Legacy bar-runner contract no longer matches the current Hungry Hippo escape path, so patching it would revive the wrong runtime family.",
            "Leave disabled, treat as historical reference only, and do not spend repair effort on the legacy bar contract.",
        )

    if hard_fail_reasons == {"atr_micro_step_without_forward_proof"}:
        return (
            "keep_blocked_research",
            f"{symbol} is blocked by current micro-step proof policy, not by a broken launch contract; it is still a research candidate once forward evidence exists.",
            "Keep disabled as research-only, do not relaunch until the micro-step proof gate is cleared or a new family-level authority explicitly reopens it.",
        )

    if "alpha_below_floor" in hard_fail_reasons:
        return (
            "retune_or_demote",
            "The current contract fails the hard launch floor on alpha, which is a live launch-geometry problem rather than a stale historical artifact.",
            "Either retune the contract back above the alpha floor with a fresh rationale or demote it out of the current HH candidate set.",
        )

    return (
        "manual_review",
        "The current fail pattern does not map cleanly to stale-historical vs blocked-research vs retune-only heuristics.",
        "Review the config manually before it re-enters any HH planning or launch queue.",
    )


def build_payload(launch_safety_payload: dict[str, Any]) -> dict[str, Any]:
    fail_rows = [
        row for row in list(launch_safety_payload.get("rows") or [])
        if isinstance(row, dict) and str(row.get("verdict") or "") == "fail"
    ]

    triage_rows: list[dict[str, Any]] = []
    category_counts: dict[str, int] = {}
    for row in fail_rows:
        config_path = ROOT / str(row.get("config_path") or "")
        config_payload = load_json(config_path)
        metadata = dict(config_payload.get("hungry_hippo_metadata") or {})
        triage_category, rationale, recommended_action = infer_triage_category(row, metadata)
        category_counts[triage_category] = category_counts.get(triage_category, 0) + 1
        triage_rows.append(
            {
                "config_path": str(row.get("config_path") or ""),
                "name": str(row.get("name") or ""),
                "symbol": str(row.get("symbol") or ""),
                "timeframe": str(row.get("timeframe") or ""),
                "runner_family": str(row.get("runner_family") or ""),
                "triage_category": triage_category,
                "validation_status": str(metadata.get("validation_status") or ""),
                "deploy_priority": metadata.get("deploy_priority"),
                "hard_fail_reasons": normalize_reason_list(row.get("hard_fail_reasons")),
                "advisory_reasons": normalize_reason_list(row.get("advisory_reasons")),
                "rationale": rationale,
                "recommended_action": recommended_action,
            }
        )

    triage_rows.sort(key=lambda row: (str(row["triage_category"]), str(row["config_path"])))

    return {
        "generated_at": utc_now_iso(),
        "sources": [
            str(LAUNCH_SAFETY_PATH.relative_to(ROOT)),
        ],
        "leadership_read": [
            "The remaining Hungry Hippo launch-contract fails are no longer one blob. Some are stale historical artifacts, some are valid research candidates blocked by current micro-step policy, and one is a current contract that needs retune-or-demote treatment.",
            "Historical contract debt should be retired, not patched. Proof-blocked research candidates should stay disabled but discoverable. Contract-floor failures should stay in the room only if someone is explicitly willing to retune them.",
        ],
        "summary": {
            "launch_contract_fail_count": len(triage_rows),
            "triage_category_counts": category_counts,
            "retire_historical": [row["config_path"] for row in triage_rows if row["triage_category"] == "retire_historical"],
            "keep_blocked_research": [row["config_path"] for row in triage_rows if row["triage_category"] == "keep_blocked_research"],
            "retune_or_demote": [row["config_path"] for row in triage_rows if row["triage_category"] == "retune_or_demote"],
        },
        "rows": triage_rows,
    }


def render_markdown(payload: dict[str, Any]) -> str:
    summary = dict(payload.get("summary") or {})
    lines = [
        "# Hungry Hippo Launch Contract Triage Board",
        "",
        f"- Generated at: `{payload.get('generated_at', '')}`",
        "- Purpose: split the remaining Hungry Hippo launch-contract fails into retire-historical vs keep-blocked-research vs retune-or-demote so the room stops treating every fail as the same kind of work.",
        "",
        "## Leadership Read",
        "",
    ]
    for item in list(payload.get("leadership_read") or []):
        lines.append(f"- {item}")

    lines.extend(
        [
            "",
            "## Summary",
            "",
            f"- Launch-contract fail count: `{summary.get('launch_contract_fail_count', 0)}`",
            f"- Triage category counts: `{summary.get('triage_category_counts', {})}`",
            f"- Retire historical: `{summary.get('retire_historical', [])}`",
            f"- Keep blocked research: `{summary.get('keep_blocked_research', [])}`",
            f"- Retune or demote: `{summary.get('retune_or_demote', [])}`",
            "",
            "## Rows",
            "",
            "| Config | Category | Symbol | Runner | Validation Status | Hard Fails | Recommended Action |",
            "|---|---|---|---|---|---|---|",
        ]
    )
    for row in list(payload.get("rows") or []):
        hard_fails = ", ".join(list(row.get("hard_fail_reasons") or [])) or "none"
        lines.append(
            f"| `{row['config_path']}` | `{row['triage_category']}` | `{row['symbol']}` | "
            f"`{row['runner_family']}` | `{row['validation_status'] or 'missing'}` | `{hard_fails}` | {row['recommended_action']} |"
        )

    return "\n".join(lines).rstrip() + "\n"


def write_outputs(payload: dict[str, Any]) -> None:
    OUTPUT_JSON_PATH.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    OUTPUT_MD_PATH.write_text(render_markdown(payload), encoding="utf-8")


def main() -> int:
    payload = build_payload(load_json(LAUNCH_SAFETY_PATH))
    write_outputs(payload)
    print(f"Wrote {OUTPUT_JSON_PATH}")
    print(f"Wrote {OUTPUT_MD_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
