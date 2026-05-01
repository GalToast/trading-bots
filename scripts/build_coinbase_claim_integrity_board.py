#!/usr/bin/env python3
from __future__ import annotations

import csv
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent.parent
REPORTS = ROOT / "reports"
SCRIPTS = ROOT / "scripts"

JSON_PATH = REPORTS / "coinbase_claim_integrity_board.json"
MD_PATH = REPORTS / "coinbase_claim_integrity_board.md"

VOL_30D_PATH = REPORTS / "vol_strategy_30d_validation.json"
VOLUME_7D_PATH = REPORTS / "volume_50_sweep_7d.json"
EXPERIMENT_REGISTRY_PATH = REPORTS / "experiment_registry.json"
NOM_OVERLAP_PATH = REPORTS / "nom_strategy_overlap_analysis.json"
SUP_OVERLAP_JSON_PATH = REPORTS / "sup_overlap_analysis.json"
SUP_OVERLAP_MD_PATH = REPORTS / "sup_overlap_analysis.md"
SUP_OVERLAP_SCRIPT_PATH = SCRIPTS / "sup_overlap_analysis.py"
RUNTIME_BOARD_PATH = REPORTS / "coinbase_spot_runtime_board.json"
FORWARD_REVIEW_PATH = REPORTS / "coinbase_spot_rsi_forward_review.csv"
RAVE_LIVE_STATE_PATH = REPORTS / "rave_rsi_mr_live_v2_state.json"

STATUS_RANK = {
    "contradicted_by_artifact": 0,
    "script_without_saved_report": 1,
    "discovery_only_7d": 2,
    "stale_incomplete": 3,
    "superseded_by_fresher_sources": 4,
    "artifact_backed": 5,
}


def load_json(path: Path) -> Any:
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def load_csv(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open("r", newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def save_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def to_float(value: Any) -> float:
    try:
        return float(value or 0.0)
    except (TypeError, ValueError):
        return 0.0


def to_int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def relpath(path: Path) -> str:
    return path.relative_to(ROOT).as_posix()


def mtime_iso(path: Path) -> str | None:
    if not path.exists():
        return None
    return datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc).isoformat()


def summarize_volatility_validation(payload: dict[str, Any]) -> dict[str, Any]:
    results = list(payload.get("results") or [])
    vol_breakout_total = round(sum(to_float((row.get("vol_breakout_best") or {}).get("pnl")) for row in results), 2)
    atr_trailing_total = round(sum(to_float((row.get("atr_trailing_best") or {}).get("pnl")) for row in results), 2)
    non_positive_coins = [
        str(row.get("coin") or "")
        for row in results
        if to_float((row.get("vol_breakout_best") or {}).get("pnl")) <= 0.0
        and to_float((row.get("atr_trailing_best") or {}).get("pnl")) <= 0.0
    ]
    return {
        "coins_tested": len(results),
        "vol_breakout_total_pnl": vol_breakout_total,
        "atr_trailing_total_pnl": atr_trailing_total,
        "fully_non_positive_coins": non_positive_coins,
    }


def volume_strategy_names(payload: dict[str, Any]) -> list[str]:
    return [str(row.get("strategy") or "") for row in list(payload.get("results") or []) if row.get("strategy")]


def build_volatility_row() -> dict[str, Any]:
    payload = load_json(VOL_30D_PATH)
    summary = summarize_volatility_validation(payload)
    return {
        "claim_id": "volatility_30d_launch_claims",
        "subject": "vol_breakout + atr_trailing",
        "claim": "Volatility promotion claims have 30d-positive launch evidence.",
        "integrity_status": "contradicted_by_artifact",
        "evidence_class": "saved_30d_validation",
        "source_paths": [relpath(VOL_30D_PATH)],
        "source_freshness": [mtime_iso(VOL_30D_PATH)],
        "summary": (
            f"Saved 30d validation covers {summary['coins_tested']} coins. "
            f"vol_breakout totals {summary['vol_breakout_total_pnl']:+.2f} and "
            f"atr_trailing totals {summary['atr_trailing_total_pnl']:+.2f}; no coin is positive on both."
        ),
        "governance_action": "keep_volatility_families_out_of_launch_and_graduation_boards",
    }


def build_volume_row() -> dict[str, Any]:
    sweep = load_json(VOLUME_7D_PATH)
    registry = load_json(EXPERIMENT_REGISTRY_PATH)
    promoted = list(sweep.get("promoted_for_30d") or [])
    registry_strategies = set(volume_strategy_names(registry))
    top = list(sweep.get("results") or [])[:3]
    top_summary = ", ".join(
        f"{str(row.get('strategy') or '')} {to_float(row.get('total_net_pnl')):+.2f}" for row in top if row.get("strategy")
    )
    sweep_winners = [str(row.get("strategy") or "") for row in top if row.get("strategy")]
    logged_winners = sorted(name for name in sweep_winners if name in registry_strategies)
    return {
        "claim_id": "volume_family_30d_validation",
        "subject": "volume-family strategies",
        "claim": "Volume-family strategies are already 30d-validated and governance-safe.",
        "integrity_status": "discovery_only_7d",
        "evidence_class": "saved_7d_discovery_only",
        "source_paths": [
            relpath(VOLUME_7D_PATH),
            relpath(EXPERIMENT_REGISTRY_PATH),
        ],
        "source_freshness": [mtime_iso(VOLUME_7D_PATH), mtime_iso(EXPERIMENT_REGISTRY_PATH)],
        "summary": (
            f"Saved volume sweep is 7d discovery across {to_int(sweep.get('coins_tested'))} coins and "
            f"{to_int(sweep.get('strategies_tested'))} strategies; top rows are {top_summary or 'n/a'}. "
            f"`promoted_for_30d` is {len(promoted)}, and the registry logs "
            f"{', '.join(logged_winners) if logged_winners else 'none of those winners'} as saved 30d-tested families."
        ),
        "governance_action": "require_saved_30d_validation_artifacts_before_any_volume_family_promotion",
    }


def build_nom_overlap_row() -> dict[str, Any]:
    payload = load_json(NOM_OVERLAP_PATH)
    overlap = ((payload.get("overlap_analysis") or {}).get("1bar_5min") or {})
    range_pnl = to_float((payload.get("range_breakout") or {}).get("total_pnl"))
    combined_pnl = to_float((payload.get("combined") or {}).get("total_pnl"))
    additive_uplift = combined_pnl - range_pnl
    return {
        "claim_id": "nom_same_coin_overlap",
        "subject": "NOM momentum + range_breakout overlap",
        "claim": "NOM same-coin overlap/additivity is artifact-backed.",
        "integrity_status": "artifact_backed",
        "evidence_class": "saved_30d_overlap_report",
        "source_paths": [relpath(NOM_OVERLAP_PATH)],
        "source_freshness": [mtime_iso(NOM_OVERLAP_PATH)],
        "summary": (
            f"Saved 30d overlap report shows {to_float(overlap.get('overlap_pct')):.1f}% overlap on the 5-minute window "
            f"and {additive_uplift:+.2f} additive uplift versus the best single lane."
        ),
        "governance_action": "treat_nom_as_the_benchmark_completed_same_coin_stack_case",
    }


def build_sup_overlap_row() -> dict[str, Any]:
    has_report = SUP_OVERLAP_JSON_PATH.exists() or SUP_OVERLAP_MD_PATH.exists()
    status = "artifact_backed" if has_report else "script_without_saved_report"
    evidence_class = "saved_overlap_report" if has_report else "script_only"
    source_paths = [relpath(SUP_OVERLAP_SCRIPT_PATH)]
    freshness = [mtime_iso(SUP_OVERLAP_SCRIPT_PATH)]
    if SUP_OVERLAP_JSON_PATH.exists():
        source_paths.append(relpath(SUP_OVERLAP_JSON_PATH))
        freshness.append(mtime_iso(SUP_OVERLAP_JSON_PATH))
    if SUP_OVERLAP_MD_PATH.exists():
        source_paths.append(relpath(SUP_OVERLAP_MD_PATH))
        freshness.append(mtime_iso(SUP_OVERLAP_MD_PATH))
    summary = (
        "A local script exists for SUP overlap, but no saved report is present in reports/, so the lane is not board-safe yet."
        if not has_report
        else "SUP overlap has a saved report and can be evaluated as a normal same-coin overlap artifact."
    )
    return {
        "claim_id": "sup_same_coin_overlap",
        "subject": "SUP momentum + range_breakout overlap",
        "claim": "SUP same-coin overlap/additivity is artifact-backed.",
        "integrity_status": status,
        "evidence_class": evidence_class,
        "source_paths": source_paths,
        "source_freshness": freshness,
        "summary": summary,
        "governance_action": (
            "do_not_use_sup_for_stack_admission_until_a_saved_overlap_report_exists"
            if not has_report
            else "evaluate_sup_with_the_same_overlap_standard_as_nom"
        ),
    }


def build_registry_row() -> dict[str, Any]:
    registry = load_json(EXPERIMENT_REGISTRY_PATH)
    unique = list(registry.get("unique_strategies") or [])
    untested = list(registry.get("untested_strategies") or [])
    missing_volume_families = [name for name in ("volume_spike_reversion", "vwap_reversion") if name in untested]
    return {
        "claim_id": "experiment_registry_completeness",
        "subject": "experiment registry coverage",
        "claim": "The experiment registry already reflects the latest strategy-family claims in the room.",
        "integrity_status": "stale_incomplete",
        "evidence_class": "saved_registry_snapshot",
        "source_paths": [relpath(EXPERIMENT_REGISTRY_PATH)],
        "source_freshness": [mtime_iso(EXPERIMENT_REGISTRY_PATH)],
        "summary": (
            f"Registry currently logs {to_int(registry.get('total_experiments'))} experiments across "
            f"{len(unique)} strategies. Later-room families like volume/statistical lanes are not represented; "
            f"the registry still marks {', '.join(missing_volume_families) if missing_volume_families else 'multiple families'} as untested."
        ),
        "governance_action": "update_registry_before_treating_new_family_claims_as_canonical",
    }


def build_rave_freshness_row() -> dict[str, Any]:
    runtime = load_json(RUNTIME_BOARD_PATH)
    live_state = load_json(RAVE_LIVE_STATE_PATH)
    forward_rows = load_csv(FORWARD_REVIEW_PATH)
    rave_forward = next((row for row in forward_rows if str(row.get("product_id") or "") == "RAVE-USD"), {})
    runtime_rave = next(
        (row for row in list(runtime.get("key_lanes") or []) if str(row.get("lane") or "") == "rave_rsi_mr_live_v2"),
        {},
    )
    forward_mtime = mtime_iso(FORWARD_REVIEW_PATH)
    live_mtime = mtime_iso(RAVE_LIVE_STATE_PATH)
    runtime_mtime = mtime_iso(RUNTIME_BOARD_PATH)
    source_order = [
        f"forward_review {forward_mtime}",
        f"live_state {live_mtime}",
        f"runtime_board {runtime_mtime}",
    ]
    runtime_is_latest = bool(runtime_mtime) and runtime_mtime >= max(m for m in (forward_mtime, live_mtime, runtime_mtime) if m)
    if runtime_is_latest:
        claim = "The runtime board has been refreshed and is currently the newest board-level source for RAVE governance."
        integrity_status = "artifact_backed"
        governance_action = "use_runtime_board_for_board_views_and_keep_raw_forward_review_and_live_state_for_lane_specific_debugging"
    else:
        claim = "The runtime board is the freshest source for current RAVE graduation governance."
        integrity_status = "superseded_by_fresher_sources"
        governance_action = "trust_forward_review_first_then_live_state_then_refresh_runtime_board"
    return {
        "claim_id": "rave_runtime_freshness_order",
        "subject": "RAVE governance freshness",
        "claim": claim,
        "integrity_status": integrity_status,
        "evidence_class": "freshness_precedence",
        "source_paths": [
            relpath(FORWARD_REVIEW_PATH),
            relpath(RAVE_LIVE_STATE_PATH),
            relpath(RUNTIME_BOARD_PATH),
        ],
        "source_freshness": [forward_mtime, live_mtime, runtime_mtime],
        "summary": (
            f"Freshness order is {' > '.join(source_order)}. "
            f"Forward review shows {to_int(rave_forward.get('realized_closes'))} supervised closes and "
            f"readiness `{str(rave_forward.get('readiness_verdict') or '')}`; live state shows "
            f"{to_int((live_state.get('state') or {}).get('closes'))} live closes; runtime board still reports "
            f"{to_int(runtime_rave.get('closes'))} closes."
        ),
        "governance_action": governance_action,
    }


def build_rows() -> list[dict[str, Any]]:
    rows = [
        build_volatility_row(),
        build_volume_row(),
        build_sup_overlap_row(),
        build_registry_row(),
        build_rave_freshness_row(),
        build_nom_overlap_row(),
    ]
    rows.sort(key=lambda row: (STATUS_RANK.get(str(row.get("integrity_status") or ""), 99), str(row.get("claim_id") or "")))
    return rows


def build_leadership_read(rows: list[dict[str, Any]]) -> list[str]:
    contradicted = [row["subject"] for row in rows if row["integrity_status"] == "contradicted_by_artifact"]
    script_only = [row["subject"] for row in rows if row["integrity_status"] == "script_without_saved_report"]
    discovery_only = [row["subject"] for row in rows if row["integrity_status"] == "discovery_only_7d"]
    refreshed_runtime = any(
        row["claim_id"] == "rave_runtime_freshness_order" and row["integrity_status"] == "artifact_backed"
        for row in rows
    )
    lines: list[str] = []
    if contradicted:
        lines.append(
            f"{', '.join(contradicted)} should stay out of promotion and graduation boards because saved 30d artifacts contradict the positive story."
        )
    if discovery_only or script_only:
        pending = script_only + discovery_only
        lines.append(
            f"{', '.join(pending)} are still weaker evidence classes than a saved 30d report, so they should not change governance yet."
        )
    if refreshed_runtime:
        lines.append(
            "The runtime board has now been refreshed, so board-level views can use it again; keep the raw forward review and live state visible for lane-specific debugging."
        )
    else:
        lines.append(
            "For the RAVE graduation lane, trust the fresh forward review first, the live state second, and the runtime board only after it is refreshed."
        )
    lines.append(
        "NOM remains the clean benchmark overlap case; SUP does not earn the same status until a saved overlap report exists."
    )
    return lines


def build_payload() -> dict[str, Any]:
    rows = build_rows()
    return {
        "generated_at": utc_now_iso(),
        "leadership_read": build_leadership_read(rows),
        "rows": rows,
    }


def write_reports(payload: dict[str, Any]) -> None:
    save_json(JSON_PATH, payload)
    lines = [
        "# Coinbase Claim Integrity Board",
        "",
        "## Leadership Read",
        "",
    ]
    for line in payload["leadership_read"]:
        lines.append(f"- {line}")
    lines.extend(
        [
            "",
            "## Rows",
            "",
            "| Subject | Status | Evidence Class | Claim | Summary | Governance Action | Sources |",
            "| --- | --- | --- | --- | --- | --- | --- |",
        ]
    )
    for row in payload["rows"]:
        lines.append(
            "| {subject} | {integrity_status} | {evidence_class} | {claim} | {summary} | {governance_action} | {sources} |".format(
                subject=row["subject"],
                integrity_status=row["integrity_status"],
                evidence_class=row["evidence_class"],
                claim=row["claim"],
                summary=row["summary"].replace("|", "/"),
                governance_action=row["governance_action"],
                sources=", ".join(row["source_paths"]),
            )
        )
    MD_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    payload = build_payload()
    write_reports(payload)
    print(f"wrote {MD_PATH}")
    print(f"wrote {JSON_PATH}")


if __name__ == "__main__":
    main()
