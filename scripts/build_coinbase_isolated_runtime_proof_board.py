#!/usr/bin/env python3
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent.parent
REPORTS = ROOT / "reports"

REMEDIATION_PATH = REPORTS / "coinbase_runner_remediation_queue.json"
DEGRADATION_PATH = REPORTS / "coinbase_shared_pool_degradation_board.json"
ALIGNMENT_PATH = REPORTS / "coinbase_primary_runner_alignment_board.json"

JSON_PATH = REPORTS / "coinbase_isolated_runtime_proof_board.json"
MD_PATH = REPORTS / "coinbase_isolated_runtime_proof_board.md"

PHASE_RANK = {
    "artifact_cleanup_then_runtime": 0,
    "launch_isolated_runtime_now": 1,
    "launch_isolated_runtime_next": 2,
    "replace_legacy_runtime": 3,
}


def load_json(path: Path) -> Any:
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def save_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def to_float(value: Any) -> float:
    try:
        return float(value or 0.0)
    except (TypeError, ValueError):
        return 0.0


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def build_rows() -> list[dict[str, Any]]:
    remediation = load_json(REMEDIATION_PATH)
    degradation = load_json(DEGRADATION_PATH)
    alignment = load_json(ALIGNMENT_PATH)

    degradation_rows = {str(row.get("coin") or ""): row for row in list(degradation.get("rows") or []) if row.get("coin")}
    alignment_rows = {str(row.get("coin") or ""): row for row in list(alignment.get("rows") or []) if row.get("coin")}

    rows: list[dict[str, Any]] = []
    for row in list(remediation.get("rows") or []):
        coin = str(row.get("coin") or "")
        if row.get("launch_wave") == "reserve" or coin == "RAVE-USD":
            continue

        degradation_row = dict(degradation_rows.get(coin) or {})
        alignment_row = dict(alignment_rows.get(coin) or {})
        remediation_phase = str(row.get("remediation_phase") or "")
        proof_phase = "launch_isolated_runtime_next"
        if remediation_phase == "clear_wave_1_blocker":
            proof_phase = "artifact_cleanup_then_runtime"
        elif remediation_phase in {"clear_wave_1_persist_gap", "clear_wave_2_runtime_gap"}:
            proof_phase = "launch_isolated_runtime_now" if str(row.get("launch_wave") or "") == "launch_now" else "launch_isolated_runtime_next"
        elif remediation_phase == "clear_wave_2_runtime_refresh":
            proof_phase = "replace_legacy_runtime"

        degradation_status = str(degradation_row.get("degradation_status") or "")
        architecture_read = str(degradation_row.get("deployment_read") or "")
        if not architecture_read:
            architecture_read = "shared-pool inference unavailable"

        if proof_phase == "artifact_cleanup_then_runtime":
            recommended_action = "retire stale artifact, then persist the first clean isolated runtime state"
        elif proof_phase == "replace_legacy_runtime":
            recommended_action = "persist a breakout runtime artifact and stop citing the old runtime lane"
        elif degradation_status in {"shared_pool_negative", "shared_pool_flattened"}:
            recommended_action = "collect isolated runtime proof next; shared-runner behavior is not an honest gate here"
        else:
            recommended_action = "persist runtime proof next so the config-aligned lane graduates from bench-only to saved live evidence"

        rows.append(
            {
                "coin": coin,
                "strategy": str(row.get("board_primary_lane") or ""),
                "launch_wave": str(row.get("launch_wave") or ""),
                "sleeve_rank": int(row.get("sleeve_rank") or 0),
                "fix_order": int(row.get("fix_order") or 0),
                "proof_phase": proof_phase,
                "alignment_status": str(row.get("alignment_status") or alignment_row.get("alignment_status") or ""),
                "saved_runner_strategy": str(row.get("saved_runner_strategy") or ""),
                "current_saved_runtime_summary": str(row.get("current_saved_runtime_summary") or ""),
                "shared_degradation_status": degradation_status,
                "isolated_30d_net_usd": round(to_float(degradation_row.get("isolated_30d_net_usd")), 2),
                "shared_runner_30d_net_usd": round(to_float(degradation_row.get("shared_runner_30d_net_usd")), 2),
                "shared_retention_pct": round(to_float(degradation_row.get("shared_retention_pct")), 2),
                "proof_why_now": architecture_read,
                "success_gate": str(row.get("success_gate") or ""),
                "recommended_action": recommended_action,
            }
        )

    rows.sort(
        key=lambda row: (
            PHASE_RANK.get(str(row.get("proof_phase") or ""), 99),
            int(row.get("fix_order") or 99),
            int(row.get("sleeve_rank") or 99),
            str(row.get("coin") or ""),
        )
    )
    return rows


def build_summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "runtime_proof_items": len(rows),
        "artifact_cleanup_items": sum(1 for row in rows if row["proof_phase"] == "artifact_cleanup_then_runtime"),
        "isolated_launch_now_items": sum(1 for row in rows if row["proof_phase"] == "launch_isolated_runtime_now"),
        "isolated_launch_next_items": sum(1 for row in rows if row["proof_phase"] == "launch_isolated_runtime_next"),
        "legacy_runtime_refresh_items": sum(1 for row in rows if row["proof_phase"] == "replace_legacy_runtime"),
    }


def build_leadership_read(rows: list[dict[str, Any]], summary: dict[str, Any]) -> list[str]:
    immediate = [row["coin"] for row in rows if row["proof_phase"] in {"artifact_cleanup_then_runtime", "launch_isolated_runtime_now"}]
    architecture_sensitive = [
        row["coin"]
        for row in rows
        if row["shared_degradation_status"] in {"shared_pool_negative", "shared_pool_flattened"}
    ]
    return [
        "Runtime proof is now the constraining layer for the non-RAVE primaries: source config and saved backfill are already aligned, but saved live evidence is still missing or stale.",
        f"Immediate execution order is {', '.join(immediate)}: clear A8's stale artifact, then persist first isolated runtime proof for the wave-1 launches.",
        f"{', '.join(architecture_sensitive)} should be judged with isolated runtime proof, not pooled-runner behavior, because the shared pool either flattens or reverses them.",
        "TRU can keep shared monitoring visibility because it survives pooled contention, but it still needs saved runtime proof before the board should call it operationally mature.",
    ]


def build_payload() -> dict[str, Any]:
    rows = build_rows()
    summary = build_summary(rows)
    return {
        "generated_at": utc_now_iso(),
        "leadership_read": build_leadership_read(rows, summary),
        "summary": summary,
        "rows": rows,
    }


def write_reports(payload: dict[str, Any]) -> None:
    save_json(JSON_PATH, payload)
    lines = [
        "# Coinbase Isolated Runtime Proof Board",
        "",
        "## Leadership Read",
        "",
    ]
    for line in payload["leadership_read"]:
        lines.append(f"- {line}")
    lines.extend(
        [
            "",
            "## Summary",
            "",
            f"- Runtime-proof items: `{payload['summary']['runtime_proof_items']}`",
            f"- Artifact cleanup first: `{payload['summary']['artifact_cleanup_items']}`",
            f"- Launch isolated runtime now: `{payload['summary']['isolated_launch_now_items']}`",
            f"- Launch isolated runtime next: `{payload['summary']['isolated_launch_next_items']}`",
            f"- Replace legacy runtime: `{payload['summary']['legacy_runtime_refresh_items']}`",
            "",
            "## Rows",
            "",
            "| Order | Coin | Strategy | Proof Phase | Launch Wave | Isolated 30d Net $ | Shared 30d Net $ | Retention % | Alignment | Recommended Action |",
            "| ---: | --- | --- | --- | --- | ---: | ---: | ---: | --- | --- |",
        ]
    )
    for row in payload["rows"]:
        lines.append(
            "| {fix_order} | {coin} | {strategy} | {proof_phase} | {launch_wave} | {isolated_30d_net_usd:.2f} | {shared_runner_30d_net_usd:.2f} | {shared_retention_pct:.2f} | {alignment_status} | {recommended_action} |".format(
                **row
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
