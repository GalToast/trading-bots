#!/usr/bin/env python3
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent.parent
REPORTS = ROOT / "reports"

GRADUATION_BOARD_PATH = REPORTS / "coinbase_spot_graduation_board.json"
JSON_PATH = REPORTS / "coinbase_spot_graduation_gap_board.json"
MD_PATH = REPORTS / "coinbase_spot_graduation_gap_board.md"

FULL_GRAD_RUNTIME_CLOSES = 20
FULL_GRAD_FORWARD_CLOSES = 30
MICRO_TRIAL_RUNTIME_CLOSES = 10


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


def combo_label(row: dict[str, Any]) -> str:
    return f"{str(row.get('coin') or '').replace('-USD', '')} {str(row.get('strategy') or '')}"


def plural_verb(items: list[Any], singular: str, plural: str) -> str:
    return singular if len(items) == 1 else plural


def collect_missing_proofs(row: dict[str, Any]) -> list[str]:
    gaps: list[str] = []
    runtime_closes = int(row.get("runtime_closes") or 0)
    forward_closes = int(row.get("forward_closes") or 0)
    forward_readiness = str(row.get("forward_readiness") or "")
    forward_lane = str(row.get("forward_lane") or row.get("lane") or "")
    runtime_realized = to_float(row.get("runtime_realized_usd"))

    if runtime_closes < MICRO_TRIAL_RUNTIME_CLOSES:
        gaps.append(f"grow_runtime_closes_to_{MICRO_TRIAL_RUNTIME_CLOSES}")
    if runtime_closes < FULL_GRAD_RUNTIME_CLOSES:
        gaps.append(f"grow_runtime_closes_to_{FULL_GRAD_RUNTIME_CLOSES}")
    if not forward_lane:
        gaps.append("create_forward_supervision_lane")
    if forward_closes == 0:
        gaps.append(f"collect_forward_closes_to_{FULL_GRAD_FORWARD_CLOSES}")
    elif forward_closes < FULL_GRAD_FORWARD_CLOSES:
        gaps.append(f"extend_forward_closes_to_{FULL_GRAD_FORWARD_CLOSES}")
    if forward_readiness != "graduation_ready":
        gaps.append("raise_forward_readiness_to_graduation_ready")
    if runtime_realized <= 0.0:
        gaps.append("recover_positive_runtime_realized")
    return gaps


def build_row(row: dict[str, Any]) -> dict[str, Any]:
    graduation_status = str(row.get("graduation_status") or "")
    missing_proofs = collect_missing_proofs(row)
    runtime_closes = int(row.get("runtime_closes") or 0)
    forward_closes = int(row.get("forward_closes") or 0)
    urgency_rank = 0
    if graduation_status == "micro_allocation_candidate":
        urgency_rank = 1
    elif graduation_status == "needs_forward_proof":
        urgency_rank = 2
    elif graduation_status == "shadow_only":
        urgency_rank = 3
    else:
        urgency_rank = 4

    return {
        "coin": str(row.get("coin") or ""),
        "strategy": str(row.get("strategy") or ""),
        "graduation_status": graduation_status,
        "lane": str(row.get("lane") or ""),
        "runtime_lane": str(row.get("runtime_lane") or ""),
        "forward_lane": str(row.get("forward_lane") or row.get("lane") or ""),
        "runtime_closes": runtime_closes,
        "forward_closes": forward_closes,
        "forward_readiness": str(row.get("forward_readiness") or ""),
        "runtime_realized_usd": round(to_float(row.get("runtime_realized_usd")), 4),
        "forward_realized_usd": None if row.get("forward_realized_usd") is None else round(to_float(row.get("forward_realized_usd")), 4),
        "reconciliation_net_30d_usd": round(to_float(row.get("reconciliation_net_30d_usd")), 2),
        "missing_proof_count": len(missing_proofs),
        "missing_proofs": missing_proofs,
        "next_gate": "full_graduation" if graduation_status in {"micro_allocation_candidate", "needs_forward_proof"} else "runtime_then_forward",
        "priority": "now" if urgency_rank == 1 else ("next" if urgency_rank == 2 else "later"),
        "urgency_rank": urgency_rank,
    }


def build_leadership_read(rows: list[dict[str, Any]]) -> list[str]:
    if not rows:
        return ["No graduation-gap rows were available."]
    lines: list[str] = []
    top = rows[0]
    lines.append(
        f"{top['coin'].replace('-USD', '')} {top['strategy']} is the cleanest path to a real graduation decision, with {top['missing_proof_count']} explicit proof gaps left."
    )
    missing_forward_lane = [combo_label(row) for row in rows if "create_forward_supervision_lane" in row["missing_proofs"]]
    if missing_forward_lane:
        lines.append(
            f"{', '.join(missing_forward_lane)} {plural_verb(missing_forward_lane, 'cannot', 'cannot')} graduate honestly until a forward supervision lane exists; bench evidence alone is not enough."
        )
    forward_extension = [combo_label(row) for row in rows if "extend_forward_closes_to_30" in row["missing_proofs"]]
    if forward_extension:
        lines.append(
            f"{', '.join(forward_extension)} {plural_verb(forward_extension, 'already has', 'already have')} forward data, but {plural_verb(forward_extension, 'it still needs', 'they still need')} a longer supervised window before the board should call {plural_verb(forward_extension, 'it', 'them')} graduation-ready."
        )
    return lines


def build_payload() -> dict[str, Any]:
    payload = load_json(GRADUATION_BOARD_PATH)
    rows = [build_row(row) for row in list(payload.get("rows") or [])]
    rows.sort(key=lambda row: (row["urgency_rank"], row["missing_proof_count"], -to_float(row["reconciliation_net_30d_usd"]), row["coin"]))
    return {
        "generated_at": utc_now_iso(),
        "leadership_read": build_leadership_read(rows),
        "rows": rows,
    }


def write_reports(payload: dict[str, Any]) -> None:
    save_json(JSON_PATH, payload)
    lines = [
        "# Coinbase Spot Graduation Gap Board",
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
            "| Coin | Strategy | Status | Priority | Runtime Closes | Forward Closes | Forward Readiness | Missing Proof Count | Missing Proofs |",
            "| --- | --- | --- | --- | ---: | ---: | --- | ---: | --- |",
        ]
    )
    for row in payload["rows"]:
        lines.append(
            "| {coin} | {strategy} | {graduation_status} | {priority} | {runtime_closes} | {forward_closes} | {forward_readiness} | {missing_proof_count} | {missing_proofs} |".format(
                coin=row["coin"],
                strategy=row["strategy"],
                graduation_status=row["graduation_status"],
                priority=row["priority"],
                runtime_closes=row["runtime_closes"],
                forward_closes=row["forward_closes"],
                forward_readiness=row["forward_readiness"] or "-",
                missing_proof_count=row["missing_proof_count"],
                missing_proofs=", ".join(row["missing_proofs"]),
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
