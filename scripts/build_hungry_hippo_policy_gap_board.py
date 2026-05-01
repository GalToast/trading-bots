#!/usr/bin/env python3
from __future__ import annotations

import csv
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent.parent
REPORTS = ROOT / "reports"
PORTABILITY_PATH = REPORTS / "hungry_hippo_symbol_portability_board.json"
APEX_DOUBLER_PATH = REPORTS / "apex_doubler.csv"
BUCKET_SPLIT_PATH = REPORTS / "bucket_split_analysis.md"
OUT_JSON = REPORTS / "hungry_hippo_policy_gap_board.json"
OUT_MD = REPORTS / "hungry_hippo_policy_gap_board.md"


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def normalize_symbol(symbol: Any) -> str:
    return str(symbol or "").strip().upper()


def to_float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def to_int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def parse_apex_doubler(path: Path) -> dict[str, dict[str, Any]]:
    rows: dict[str, dict[str, Any]] = {}
    if not path.exists():
        return rows
    with path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        for raw in reader:
            symbol = normalize_symbol(raw.get("symbol"))
            if not symbol:
                continue
            combined = to_float(raw.get("combined"))
            closes = to_int(raw.get("closes"))
            candidate = {
                "source": "apex_doubler",
                "mode": str(raw.get("mode") or ""),
                "combined_net_usd": combined,
                "closes": closes,
                "worst_drawdown_usd": to_float(raw.get("worst")),
            }
            current = rows.get(symbol)
            if current is None or (combined or float("-inf")) > (current.get("combined_net_usd") or float("-inf")):
                rows[symbol] = candidate
    return rows


def parse_bucket_split(path: Path) -> dict[str, dict[str, Any]]:
    rows: dict[str, dict[str, Any]] = {}
    if not path.exists():
        return rows
    text: str
    try:
        text = path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        text = path.read_text(encoding="cp1252")
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line.startswith("|") or line.startswith("| Lane |") or line.startswith("|------"):
            continue
        cells = [cell.strip() for cell in line.strip("|").split("|")]
        if len(cells) != 7:
            continue
        symbol = normalize_symbol(cells[1])
        net_usd = to_float(cells[4].replace("+", ""))
        closes = to_int(cells[3])
        if not symbol or net_usd is None or closes is None:
            continue
        candidate = {
            "source": "bucket_split_analysis",
            "mode": cells[0],
            "combined_net_usd": net_usd,
            "closes": closes,
            "status": cells[6],
        }
        current = rows.get(symbol)
        if current is None or net_usd > (current.get("combined_net_usd") or float("-inf")):
            rows[symbol] = candidate
    return rows


def pick_evidence(symbol: str, apex_rows: dict[str, dict[str, Any]], bucket_rows: dict[str, dict[str, Any]]) -> dict[str, Any]:
    apex = apex_rows.get(symbol)
    bucket = bucket_rows.get(symbol)
    if apex and bucket:
        apex_net = apex.get("combined_net_usd") or float("-inf")
        bucket_net = bucket.get("combined_net_usd") or float("-inf")
        return apex if apex_net >= bucket_net else bucket
    return dict(apex or bucket or {})


def classify_priority(
    deployment_verdict: str,
    hard_block_reasons: list[str],
    evidence: dict[str, Any],
) -> tuple[str, int]:
    score = 0
    if deployment_verdict == "cleared_for_shadow_discussion":
        score += 50
    elif deployment_verdict == "hard_block" and set(hard_block_reasons) <= {"uncovered", "micro_step_without_20_forward_closes"}:
        score += 20

    net_usd = evidence.get("combined_net_usd")
    closes = evidence.get("closes")
    if net_usd is not None and net_usd > 0:
        score += 20
    elif net_usd is not None and net_usd < 0:
        score -= 20
    if closes:
        score += min(int(closes), 4000) // 200
        if int(closes) < 20:
            score -= 15

    if score >= 70:
        return "policy_seed_now", score
    if score >= 30:
        return "policy_seed_next", score
    if score >= 10:
        return "policy_research_queue", score
    return "policy_defer", score


def build_row(
    portability_row: dict[str, Any],
    apex_rows: dict[str, dict[str, Any]],
    bucket_rows: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    symbol = str(portability_row.get("symbol") or "")
    evidence = pick_evidence(symbol, apex_rows, bucket_rows)
    deployment_verdict = str(portability_row.get("deployment_verdict") or "")
    hard_block_reasons = [str(item) for item in list(portability_row.get("hard_block_reasons") or []) if str(item)]
    priority, score = classify_priority(deployment_verdict, hard_block_reasons, evidence)

    evidence_net = evidence.get("combined_net_usd")
    evidence_closes = evidence.get("closes")
    if priority == "policy_seed_now":
        rationale = "Policy coverage is the main blocker; symbol already has a relatively strong gate/evidence posture."
    elif priority == "policy_seed_next":
        rationale = "Portable candidate with enough positive evidence to justify policy coverage after the immediate seed-now set."
    elif priority == "policy_research_queue":
        rationale = "Policy is missing, but the evidence is still thin or mixed enough that it should stay queued behind stronger candidates."
    else:
        rationale = "Policy is missing, but current evidence is weak or negative enough that coverage should wait."

    return {
        "symbol": symbol,
        "asset_class": str(portability_row.get("asset_class") or ""),
        "priority": priority,
        "priority_score": score,
        "deployment_verdict": deployment_verdict,
        "hard_block_reasons": hard_block_reasons,
        "evidence_source": str(evidence.get("source") or ""),
        "evidence_mode": str(evidence.get("mode") or ""),
        "evidence_net_usd": evidence_net,
        "evidence_closes": evidence_closes,
        "evidence_status": str(evidence.get("status") or ""),
        "rationale": rationale,
    }


def build_payload(
    portability_payload: dict[str, Any],
    apex_rows: dict[str, dict[str, Any]],
    bucket_rows: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    missing_policy_rows = [
        dict(row)
        for row in list(portability_payload.get("rows") or [])
        if str(row.get("generalization_status") or "") == "portable_missing_policy"
    ]
    rows = [build_row(row, apex_rows, bucket_rows) for row in missing_policy_rows]
    rows.sort(key=lambda row: (-int(row["priority_score"]), str(row["symbol"])))

    priority_counts: dict[str, int] = {}
    for row in rows:
        priority_counts[row["priority"]] = priority_counts.get(row["priority"], 0) + 1

    seed_now = [row["symbol"] for row in rows if row["priority"] == "policy_seed_now"]
    seed_next = [row["symbol"] for row in rows if row["priority"] == "policy_seed_next"]
    defer = [row["symbol"] for row in rows if row["priority"] == "policy_defer"]

    leadership_read = [
        f"The portability bottleneck is now policy coverage, and the missing-policy set is not flat debt: `policy_seed_now={seed_now or ['none']}`.",
        f"Best current seed-next queue is `{seed_next or ['none']}`; those symbols already show enough positive offline evidence that policy coverage is a better use of time than more universalization talk.",
        f"`policy_defer={defer or ['none']}` are still missing policy too, but their current evidence does not justify first-priority coverage.",
    ]

    return {
        "generated_at": utc_now_iso(),
        "sources": [
            "reports/hungry_hippo_symbol_portability_board.json",
            "reports/apex_doubler.csv",
            "reports/bucket_split_analysis.md",
        ],
        "summary": {
            "missing_policy_symbol_count": len(rows),
            "priority_counts": priority_counts,
            "policy_seed_now_symbols": seed_now,
            "policy_seed_next_symbols": seed_next,
            "policy_defer_symbols": defer,
        },
        "leadership_read": leadership_read,
        "rows": rows,
        "notes": [
            "This board ranks only the current `portable_missing_policy` set from the portability board.",
            "The evidence score is an inference from current offline/runtime-adjacent surfaces; it is a prioritization aid, not a profitability guarantee.",
        ],
    }


def render_markdown(payload: dict[str, Any]) -> str:
    summary = dict(payload.get("summary") or {})
    lines = [
        "# Hungry Hippo Policy Gap Board",
        "",
        f"- Generated at: `{payload.get('generated_at', '')}`",
        "- Purpose: rank the current missing-policy symbols so the room can cover the highest-leverage symbols first.",
        "",
        "## Leadership Read",
        "",
    ]
    for line in list(payload.get("leadership_read") or []):
        lines.append(f"- {line}")
    lines.extend(
        [
            "",
            "## Summary",
            "",
            f"- Missing-policy symbol count: `{summary.get('missing_policy_symbol_count', 0)}`",
            f"- Seed now: `{summary.get('policy_seed_now_symbols', [])}`",
            f"- Seed next: `{summary.get('policy_seed_next_symbols', [])}`",
            f"- Defer: `{summary.get('policy_defer_symbols', [])}`",
            "",
            "## Rows",
            "",
            "| Symbol | Asset | Priority | Gate | Evidence | Net USD | Closes |",
            "| --- | --- | --- | --- | --- | --- | --- |",
        ]
    )
    for row in list(payload.get("rows") or []):
        evidence = str(row.get("evidence_source") or "-")
        mode = str(row.get("evidence_mode") or "")
        if mode:
            evidence = f"{evidence}:{mode}"
        net_value = row.get("evidence_net_usd")
        net_text = "-" if net_value is None else f"{float(net_value):+.2f}"
        closes_value = row.get("evidence_closes")
        closes_text = "-" if closes_value is None else str(closes_value)
        lines.append(
            "| "
            + " | ".join(
                [
                    str(row.get("symbol") or ""),
                    str(row.get("asset_class") or ""),
                    str(row.get("priority") or ""),
                    str(row.get("deployment_verdict") or ""),
                    evidence,
                    net_text,
                    closes_text,
                ]
            )
            + " |"
        )
    lines.extend(["", "## Notes", ""])
    for note in list(payload.get("notes") or []):
        lines.append(f"- {note}")
    return "\n".join(lines) + "\n"


def main() -> None:
    portability_payload = load_json(PORTABILITY_PATH)
    apex_rows = parse_apex_doubler(APEX_DOUBLER_PATH)
    bucket_rows = parse_bucket_split(BUCKET_SPLIT_PATH)
    payload = build_payload(portability_payload, apex_rows, bucket_rows)
    OUT_JSON.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    OUT_MD.write_text(render_markdown(payload), encoding="utf-8")
    print(f"Wrote {OUT_JSON}")
    print(f"Wrote {OUT_MD}")


if __name__ == "__main__":
    main()
