#!/usr/bin/env python3
from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent.parent
REPORTS = ROOT / "reports"

MD_PATH = REPORTS / "coinbase_momentum_validation_inbox.md"
JSON_PATH = REPORTS / "coinbase_momentum_validation_inbox.json"

REGISTRY_PATH = REPORTS / "master_deployment_registry.md"
EVIDENCE_MATRIX_PATH = REPORTS / "coinbase_spot_evidence_matrix.json"
MOMENTUM_RECON_RESULTS_PATH = REPORTS / "coinbase_momentum_reconciliation_results.json"
VALIDATION_RESULTS_PATH = REPORTS / "coinbase_momentum_validation_results.json"
NEXT_LAUNCH_WAVE_PATH = REPORTS / "coinbase_spot_next_launch_wave.json"


def load_json(path: Path) -> Any:
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def load_text(path: Path) -> str:
    if not path.exists():
        return ""
    return path.read_text(encoding="utf-8")


def to_float(value: Any) -> float:
    try:
        return float(value or 0.0)
    except (TypeError, ValueError):
        return 0.0


def parse_registry_sections(text: str) -> list[dict[str, Any]]:
    sections: list[dict[str, Any]] = []
    current_tier = ""
    current_coin: str | None = None
    current_strategy = ""
    current_lines: list[str] = []

    def flush() -> None:
        nonlocal current_coin, current_strategy, current_lines
        if current_coin is None:
            return
        body = "\n".join(current_lines)
        pnl_match = re.search(r"\*\*(?:30d|7d) Net PnL\*\* \| \*\*\+\$(?P<pnl>[\d.]+)\*\*", body)
        hit_rate_match = re.search(r"Param Hit Rate: (?P<hit>[\d.]+)%", body)
        sections.append(
            {
                "coin": current_coin,
                "strategy": current_strategy,
                "tier": current_tier,
                "net_pnl": float(pnl_match.group("pnl")) if pnl_match else 0.0,
                "param_hit_rate": float(hit_rate_match.group("hit")) if hit_rate_match else None,
                "body": body,
            }
        )
        current_coin = None
        current_strategy = ""
        current_lines = []

    for raw_line in text.splitlines():
        line = raw_line.strip()
        if line.startswith("## "):
            flush()
            current_tier = line.replace("## ", "", 1).strip()
            continue
        if line.startswith("### "):
            flush()
            match = re.match(r"### (?P<coin>[A-Z0-9-]+) — (?P<strategy>.+)", line)
            if match:
                current_coin = match.group("coin")
                current_strategy = match.group("strategy")
            continue
        if current_coin is not None:
            current_lines.append(raw_line)

    flush()

    # Parse B-tier table rows separately.
    b_tier_match = re.search(
        r"## B-TIER: 7d Positive, Not Fully Swept(?P<body>.*?)(?:\n---|\Z)",
        text,
        flags=re.S,
    )
    if b_tier_match:
        for line in b_tier_match.group("body").splitlines():
            if not line.startswith("|") or "Coin" in line or "---" in line:
                continue
            cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
            if len(cells) < 5:
                continue
            coin, strategy, pnl = cells[0], cells[1], cells[2]
            pnl_value = float(pnl.replace("+$", "").replace("$", "").strip())
            sections.append(
                {
                    "coin": coin,
                    "strategy": strategy,
                    "tier": "B-TIER: 7d Positive, Not Fully Swept",
                    "net_pnl": pnl_value,
                    "param_hit_rate": None,
                    "body": line,
                }
            )

    return sections


def build_payload(*, now: datetime | None = None) -> dict[str, Any]:
    now = now or datetime.now(timezone.utc)
    registry_text = load_text(REGISTRY_PATH)
    evidence_rows = load_json(EVIDENCE_MATRIX_PATH).get("rows") or []
    recon_rows = load_json(MOMENTUM_RECON_RESULTS_PATH).get("results") or []
    validation_rows = load_json(VALIDATION_RESULTS_PATH).get("results") or []
    launch_rows = load_json(NEXT_LAUNCH_WAVE_PATH).get("rows") or []

    evidence_map = {str(row.get("coin") or ""): row for row in evidence_rows}
    recon_map = {str(row.get("coin") or ""): row for row in recon_rows}
    validation_map = {str(row.get("coin") or ""): row for row in validation_rows}
    launch_map = {str(row.get("coin") or ""): row for row in launch_rows}

    inbox: list[dict[str, Any]] = []
    covered: list[dict[str, Any]] = []

    for section in parse_registry_sections(registry_text):
        coin = str(section["coin"])
        evidence_row = evidence_map.get(coin) or {}
        recon_row = recon_map.get(coin) or {}
        validation_row = validation_map.get(coin) or {}
        launch_row = launch_map.get(coin) or {}

        if evidence_row or recon_row or validation_row or launch_row:
            covered.append(
                {
                    "coin": coin,
                    "tier": section["tier"],
                    "registry_net_pnl": round(to_float(section["net_pnl"]), 4),
                    "current_status": (
                        str(launch_row.get("launch_wave") or "")
                        or str(evidence_row.get("verdict") or "")
                        or str(recon_row.get("verdict") or "")
                        or str(validation_row.get("verdict") or "")
                    ),
                    "note": "already represented in the current board stack",
                }
            )
            continue

        tier = str(section["tier"])
        if tier.startswith("A-TIER"):
            action = "validate_30d_next"
            reason = "strong 7d claim, but not router-ready until 30d reconciliation exists"
        elif tier.startswith("B-TIER"):
            action = "optimize_then_validate"
            reason = "positive 7d claim exists, but parameter sweep and 30d confirmation are still missing"
        else:
            action = "archive_or_ignore"
            reason = "registry entry is not a current launch candidate"

        inbox.append(
            {
                "coin": coin,
                "strategy": section["strategy"],
                "tier": tier,
                "registry_net_pnl": round(to_float(section["net_pnl"]), 4),
                "param_hit_rate": section["param_hit_rate"],
                "action": action,
                "reason": reason,
            }
        )

    action_priority = {"validate_30d_next": 0, "optimize_then_validate": 1, "archive_or_ignore": 2}
    inbox.sort(
        key=lambda row: (
            action_priority[row["action"]],
            -(row["param_hit_rate"] or -1.0),
            -to_float(row["registry_net_pnl"]),
            row["coin"],
        )
    )
    covered.sort(key=lambda row: (-to_float(row["registry_net_pnl"]), row["coin"]))

    leadership_read = [
        "Registry headline PnL is not a router decision. The unboarded A-tier names belong in a 30d validation inbox, not in the launch wave.",
        "TRU, GHST, RED, and NOM are the serious incoming momentum claims because their 7d hit-rate evidence is unusually strong.",
        "The B-tier names are no longer a black box: SUP is the clear winner from the first 30d pass, MDT is positive but weaker, and TROLL is rejected.",
        "Anything already represented in the current board stack should stop being re-pitched as a fresh discovery.",
    ]

    return {
        "generated_at": now.isoformat(),
        "leadership_read": leadership_read,
        "validation_inbox": inbox,
        "already_covered": covered,
    }


def write_reports(payload: dict[str, Any], *, md_path: Path = MD_PATH, json_path: Path = JSON_PATH) -> None:
    json_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    lines = [
        "# Coinbase Momentum Validation Inbox",
        "",
        "## Leadership Read",
        "",
    ]
    for line in payload["leadership_read"]:
        lines.append(f"- {line}")
    lines.extend(
        [
            "",
            "## Validation Inbox",
            "",
            "| Coin | Strategy | Tier | Registry PnL | Hit Rate | Action | Reason |",
            "| --- | --- | --- | ---: | ---: | --- | --- |",
        ]
    )
    for row in payload["validation_inbox"]:
        hit_rate = "" if row["param_hit_rate"] is None else f"{float(row['param_hit_rate']):.1f}%"
        lines.append(
            "| {coin} | {strategy} | {tier} | {registry_net_pnl:.4f} | {hit_rate} | {action} | {reason} |".format(
                coin=row["coin"],
                strategy=row["strategy"],
                tier=row["tier"],
                registry_net_pnl=float(row["registry_net_pnl"]),
                hit_rate=hit_rate,
                action=row["action"],
                reason=row["reason"],
            )
        )
    lines.extend(
        [
            "",
            "## Already Covered",
            "",
            "| Coin | Tier | Registry PnL | Current Status | Note |",
            "| --- | --- | ---: | --- | --- |",
        ]
    )
    for row in payload["already_covered"]:
        lines.append(
            "| {coin} | {tier} | {registry_net_pnl:.4f} | {current_status} | {note} |".format(
                coin=row["coin"],
                tier=row["tier"],
                registry_net_pnl=float(row["registry_net_pnl"]),
                current_status=row["current_status"],
                note=row["note"],
            )
        )
    md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    payload = build_payload()
    write_reports(payload)
    print(f"wrote {MD_PATH}")
    print(f"wrote {JSON_PATH}")


if __name__ == "__main__":
    main()
