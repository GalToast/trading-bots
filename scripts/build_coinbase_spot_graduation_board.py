#!/usr/bin/env python3
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent.parent
REPORTS = ROOT / "reports"

EVIDENCE_MATRIX_PATH = REPORTS / "coinbase_spot_evidence_matrix.json"
RUNTIME_BOARD_PATH = REPORTS / "coinbase_spot_runtime_board.json"
DEPLOYABILITY_BOARD_PATH = REPORTS / "coinbase_spot_deployability_board.json"
RSI_FORWARD_REVIEW_MD_PATH = REPORTS / "coinbase_spot_rsi_forward_review.md"
JSON_PATH = REPORTS / "coinbase_spot_graduation_board.json"
MD_PATH = REPORTS / "coinbase_spot_graduation_board.md"


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


def load_forward_review_rows() -> list[dict[str, Any]]:
    if not RSI_FORWARD_REVIEW_MD_PATH.exists():
        return []
    rows: list[dict[str, Any]] = []
    lines = RSI_FORWARD_REVIEW_MD_PATH.read_text(encoding="utf-8").splitlines()
    for line in lines:
        if not line.startswith("|") or line.startswith("| ---"):
            continue
        parts = [part.strip() for part in line.strip().strip("|").split("|")]
        if len(parts) != 13 or parts[0] in {"Product", "TOTAL"}:
            continue
        rows.append(
            {
                "product_id": parts[0],
                "lane": parts[1],
                "readiness": parts[2],
                "forward_status": parts[3],
                "baseline_72h_usd": to_float(parts[4]),
                "realized_usd": to_float(parts[5]),
                "delta_vs_baseline_usd": to_float(parts[6]),
                "ratio": None if parts[7] in {"-", ""} else to_float(parts[7]),
                "closes": int(float(parts[8] or 0)),
                "in_position": int(float(parts[9] or 0)),
                "cash_usd": to_float(parts[10]),
                "heartbeat_age_seconds": to_float(parts[11]),
                "note": parts[12],
            }
        )
    return rows


def build_runtime_map() -> dict[tuple[str, str], dict[str, Any]]:
    payload = load_json(RUNTIME_BOARD_PATH)
    runtime_map: dict[tuple[str, str], dict[str, Any]] = {}
    for section in ("key_lanes", "rsi_shadow_queue"):
        for row in list(payload.get(section) or []):
            runtime_map[(str(row.get("product_id") or ""), str(row.get("lane") or ""))] = row
    return runtime_map


def build_deployability_map() -> dict[tuple[str, str], dict[str, Any]]:
    payload = load_json(DEPLOYABILITY_BOARD_PATH)
    deploy_map: dict[tuple[str, str], dict[str, Any]] = {}
    for row in list(payload.get("candidates") or []):
        deploy_map[(str(row.get("product_id") or ""), str(row.get("lane") or ""))] = row
    return deploy_map


def select_candidate_rows(evidence_payload: dict[str, Any]) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    for row in list(evidence_payload.get("rows") or []):
        verdict = str(row.get("verdict") or "")
        if verdict in {"deployable_priority", "bench_positive_wait_runtime"}:
            candidates.append(row)
    return candidates


def infer_lane_name(row: dict[str, Any]) -> str:
    coin = str(row.get("coin") or "")
    strategy = str(row.get("strategy") or "")
    if coin == "RAVE-USD" and strategy == "rsi_mr":
        return "rave_rsi_mr_live_v2"
    return ""


def infer_forward_lane_name(row: dict[str, Any]) -> str:
    coin = str(row.get("coin") or "")
    strategy = str(row.get("strategy") or "")
    if coin == "RAVE-USD" and strategy == "rsi_mr":
        return "shadow_coinbase_raveusd_rsi7"
    return ""


def decide_graduation_status(
    evidence_row: dict[str, Any],
    runtime_row: dict[str, Any] | None,
    forward_row: dict[str, Any] | None,
    deploy_row: dict[str, Any] | None,
) -> tuple[str, str]:
    verdict = str(evidence_row.get("verdict") or "")
    runtime_realized = to_float((runtime_row or {}).get("realized_net_usd"))
    runtime_closes = int((runtime_row or {}).get("closes") or 0)
    runtime_status = str((runtime_row or {}).get("status") or "")
    forward_readiness = str((forward_row or {}).get("readiness") or "")
    forward_status = str((forward_row or {}).get("forward_status") or "")
    forward_closes = int((forward_row or {}).get("closes") or 0)

    if (
        verdict == "deployable_priority"
        and runtime_realized > 0.0
        and runtime_closes >= 20
        and forward_readiness == "graduation_ready"
    ):
        return "clear_real_cap_today", "clears bench, runtime, and forward-review bar"

    if (
        verdict == "deployable_priority"
        and runtime_realized > 0.0
        and (
            (forward_readiness == "probationary" and forward_status.startswith("holding_up") and forward_closes >= 20)
            or (runtime_status == "active" and runtime_closes >= 10 and str((deploy_row or {}).get("action") or "") == "promote_small_live")
        )
    ):
        return "micro_allocation_candidate", "strongest current candidate, but still below full graduation bar"

    if verdict == "deployable_priority":
        return "needs_forward_proof", "bench and runtime are positive, but forward evidence is still incomplete or too thin"

    if verdict == "bench_positive_wait_runtime":
        return "shadow_only", "bench-positive only; runtime proof is missing or too weak"

    return "do_not_graduate", "does not clear current evidence bar"


def build_candidate_row(
    evidence_row: dict[str, Any],
    runtime_map: dict[tuple[str, str], dict[str, Any]],
    deploy_map: dict[tuple[str, str], dict[str, Any]],
    forward_rows: list[dict[str, Any]],
) -> dict[str, Any]:
    coin = str(evidence_row.get("coin") or "")
    strategy = str(evidence_row.get("strategy") or "")
    runtime_lane = infer_lane_name(evidence_row)
    forward_lane = infer_forward_lane_name(evidence_row)
    runtime_row = runtime_map.get((coin, runtime_lane)) if runtime_lane else None
    deploy_row = deploy_map.get((coin, runtime_lane)) if runtime_lane else None
    if deploy_row is None and forward_lane:
        deploy_row = deploy_map.get((coin, forward_lane))
    forward_row = (
        next((row for row in forward_rows if row["product_id"] == coin and row["lane"] == forward_lane), None)
        if forward_lane
        else None
    )

    status, reason = decide_graduation_status(evidence_row, runtime_row, forward_row, deploy_row)
    runtime_realized = (
        to_float((runtime_row or {}).get("realized_net_usd"))
        if runtime_row is not None
        else to_float(evidence_row.get("runtime_realized_usd"))
    )
    runtime_closes = (
        int((runtime_row or {}).get("closes") or 0)
        if runtime_row is not None
        else int(evidence_row.get("runtime_closes") or 0)
    )
    recon = to_float(evidence_row.get("reconciliation_net_30d_usd"))
    sweep = to_float(evidence_row.get("library_sweep_partial_14d_net_usd"))
    forward_realized = to_float((forward_row or {}).get("realized_usd"))
    forward_ratio = (forward_row or {}).get("ratio")
    score = round(recon + runtime_realized + max(sweep, 0.0) + max(forward_realized, 0.0), 2)

    return {
        "combo_id": str(evidence_row.get("combo_id") or ""),
        "coin": coin,
        "strategy": strategy,
        "family": str(evidence_row.get("family") or ""),
        "lane": runtime_lane or forward_lane,
        "runtime_lane": runtime_lane,
        "forward_lane": forward_lane,
        "verdict": str(evidence_row.get("verdict") or ""),
        "graduation_status": status,
        "reason": reason,
        "reconciliation_net_30d_usd": recon,
        "reconciliation_closes_30d": int(evidence_row.get("reconciliation_closes_30d") or 0),
        "library_sweep_partial_14d_net_usd": sweep,
        "library_sweep_partial_14d_closes": int(evidence_row.get("library_sweep_partial_14d_closes") or 0),
        "runtime_realized_usd": runtime_realized,
        "runtime_closes": runtime_closes,
        "runtime_status": str((runtime_row or {}).get("status") or ""),
        "forward_readiness": str((forward_row or {}).get("readiness") or ""),
        "forward_status": str((forward_row or {}).get("forward_status") or ""),
        "forward_realized_usd": forward_realized if forward_row else None,
        "forward_ratio": forward_ratio,
        "forward_closes": int((forward_row or {}).get("closes") or 0),
        "deployability_action": str((deploy_row or {}).get("action") or evidence_row.get("deployability_action") or ""),
        "graduation_score": score,
    }


def choose_forced_nominee(rows: list[dict[str, Any]]) -> dict[str, Any] | None:
    if not rows:
        return None
    ordered = sorted(
        rows,
        key=lambda row: (
            row["graduation_status"] != "micro_allocation_candidate",
            row["graduation_status"] != "needs_forward_proof",
            -to_float(row["graduation_score"]),
            row["coin"],
        ),
    )
    return ordered[0]


def build_leadership_read(rows: list[dict[str, Any]], forced_nominee: dict[str, Any] | None) -> list[str]:
    lines: list[str] = []
    cleared = [row for row in rows if row["graduation_status"] == "clear_real_cap_today"]
    if cleared:
        top = cleared[0]
        lines.append(
            f"{top['coin'].replace('-USD', '')} {top['strategy']} clears the current real-cap bar on bench, runtime, and forward evidence."
        )
    else:
        lines.append("No Coinbase spot lane clears the full real-cap graduation bar today.")
    if forced_nominee is not None:
        lines.append(
            f"If forced to nominate exactly one lane for the first tiny real-cap trial, it is {forced_nominee['coin'].replace('-USD', '')} {forced_nominee['strategy']}."
        )
        lines.append(
            f"{forced_nominee['coin'].replace('-USD', '')} {forced_nominee['strategy']} is the strongest current candidate, but still below the full graduation bar."
        )
    bench_only = [row["coin"].replace("-USD", "") for row in rows if row["graduation_status"] == "shadow_only"]
    if bench_only:
        lines.append(f"{', '.join(bench_only)} stay shadow-only because runtime or forward proof is still missing.")
    return lines


def build_payload() -> dict[str, Any]:
    evidence_payload = load_json(EVIDENCE_MATRIX_PATH)
    runtime_map = build_runtime_map()
    deploy_map = build_deployability_map()
    forward_rows = load_forward_review_rows()

    rows = [
        build_candidate_row(row, runtime_map, deploy_map, forward_rows)
        for row in select_candidate_rows(evidence_payload)
    ]
    rows.sort(
        key=lambda row: (
            row["graduation_status"] != "clear_real_cap_today",
            row["graduation_status"] != "micro_allocation_candidate",
            row["graduation_status"] != "needs_forward_proof",
            -to_float(row["graduation_score"]),
            row["coin"],
        )
    )
    forced_nominee = choose_forced_nominee(rows)
    return {
        "generated_at": utc_now_iso(),
        "leadership_read": build_leadership_read(rows, forced_nominee),
        "forced_nominee": forced_nominee,
        "rows": rows,
    }


def write_reports(payload: dict[str, Any]) -> None:
    save_json(JSON_PATH, payload)
    lines = [
        "# Coinbase Spot Graduation Board",
        "",
        "## Leadership Read",
        "",
    ]
    for line in payload["leadership_read"]:
        lines.append(f"- {line}")
    if payload.get("forced_nominee"):
        nominee = payload["forced_nominee"]
        lines.extend(
            [
                "",
                "## Forced Nominee",
                "",
                f"- Coin: `{nominee['coin']}`",
                f"- Strategy: `{nominee['strategy']}`",
                f"- Runtime Lane: `{nominee['runtime_lane'] or '-'}`",
                f"- Forward Lane: `{nominee['forward_lane'] or '-'}`",
                f"- Status: `{nominee['graduation_status']}`",
                f"- Reason: {nominee['reason']}",
            ]
        )
    lines.extend(
        [
            "",
            "## Rows",
            "",
            "| Coin | Strategy | Runtime Lane | Forward Lane | Graduation Status | Reconciliation 30d $ | Runtime $ | Runtime Closes | Forward Status | Forward $ | Forward Ratio | Action | Score | Reason |",
            "| --- | --- | --- | --- | --- | ---: | ---: | ---: | --- | ---: | ---: | --- | ---: | --- |",
        ]
    )
    for row in payload["rows"]:
        lines.append(
            "| {coin} | {strategy} | {runtime_lane} | {forward_lane} | {graduation_status} | {reconciliation_net_30d_usd:.2f} | {runtime_realized_usd:.4f} | {runtime_closes} | {forward_status} | {forward_realized_usd} | {forward_ratio} | {deployability_action} | {graduation_score:.2f} | {reason} |".format(
                coin=row["coin"],
                strategy=row["strategy"],
                runtime_lane=row["runtime_lane"] or "-",
                forward_lane=row["forward_lane"] or "-",
                graduation_status=row["graduation_status"],
                reconciliation_net_30d_usd=row["reconciliation_net_30d_usd"],
                runtime_realized_usd=row["runtime_realized_usd"],
                runtime_closes=row["runtime_closes"],
                forward_status=row["forward_status"] or "-",
                forward_realized_usd="" if row["forward_realized_usd"] is None else f"{row['forward_realized_usd']:.4f}",
                forward_ratio="" if row["forward_ratio"] is None else f"{to_float(row['forward_ratio']):.4f}",
                deployability_action=row["deployability_action"] or "-",
                graduation_score=row["graduation_score"],
                reason=row["reason"],
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
