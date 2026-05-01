#!/usr/bin/env python3
"""Build an optimizer trust board for the adaptive lattice research program."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
REGISTRY_PATH = ROOT / "configs" / "adaptive_optimizer_registry.json"
OUTPUT_MD = ROOT / "reports" / "adaptive_optimizer_board.md"
OUTPUT_JSON = ROOT / "reports" / "adaptive_optimizer_board.json"


def load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def report_artifacts_exist(evidence_paths: list[str]) -> list[str]:
    existing: list[str] = []
    for evidence in evidence_paths:
        path = ROOT / evidence
        if path.exists():
            existing.append(evidence)
    return existing


def build_payload() -> dict:
    registry = load_json(REGISTRY_PATH)
    rows = []
    for surface in registry.get("surfaces", []):
        evidence_paths = surface.get("evidence_paths", [])
        existing_evidence = report_artifacts_exist(evidence_paths)
        rows.append(
            {
                "surface_id": surface["surface_id"],
                "script_path": surface["script_path"],
                "domain": surface["domain"],
                "search_method": surface["search_method"],
                "trust_level": surface["trust_level"],
                "read": surface["read"],
                "constraints": surface.get("constraints", []),
                "evidence_paths": evidence_paths,
                "existing_evidence": existing_evidence,
                "evidence_count": len(existing_evidence),
            }
        )

    counts = {
        level: sum(1 for row in rows if row["trust_level"] == level)
        for level in sorted({row["trust_level"] for row in rows})
    }
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "version": registry["version"],
        "trust_levels": registry["trust_levels"],
        "summary": {
            "surface_count": len(rows),
            "counts_by_trust_level": counts,
        },
        "rows": rows,
    }


def write_markdown(payload: dict) -> None:
    lines = [
        "# Adaptive Optimizer Trust Board",
        "",
        "This board classifies optimizer/search surfaces by how safely they can inform the adaptive lattice program.",
        "",
        "## Current Read",
        "",
    ]
    counts = payload["summary"]["counts_by_trust_level"]
    for level, count in counts.items():
        lines.append(f"- `{level}`: `{count}`")
    lines.extend(["", "## Trust Levels", ""])
    for level, read in payload["trust_levels"].items():
        lines.append(f"- `{level}`: {read}")
    lines.extend(["", "## Rows", "", "| Surface | Domain | Method | Trust | Evidence | Read |", "|---|---|---|---|---|---|"])

    for row in payload["rows"]:
        lines.append(
            f"| `{row['surface_id']}` | {row['domain']} | {row['search_method']} | "
            f"`{row['trust_level']}` | {row['evidence_count']} | {row['read']} |"
        )

    lines.extend(["", "## Constraints", ""])
    for row in payload["rows"]:
        lines.append(f"### {row['surface_id']}")
        lines.append(f"- script: `{row['script_path']}`")
        for item in row["constraints"]:
            lines.append(f"- {item}")
        if row["existing_evidence"]:
            lines.append(f"- evidence found: {', '.join(f'`{item}`' for item in row['existing_evidence'])}")
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
