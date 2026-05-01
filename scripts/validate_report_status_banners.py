#!/usr/bin/env python3
from __future__ import annotations

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
HEAD_LINE_LIMIT = 8

CURRENT_RUNTIME = "current_runtime"
HISTORICAL = "historical"

CURRENT_MARKERS = (
    "current runtime landing page",
    "current runtime authority board",
    "current theory authority board",
    "current runtime generated board",
)

HISTORICAL_MARKERS = (
    "historical snapshot only",
    "historical incident snapshot only",
    "historical decision snapshot only",
    "superseded for runtime use",
)

FILE_EXPECTATIONS: dict[str, str] = {
    "reports/README.md": CURRENT_RUNTIME,
    "reports/operator_authority_stack_board.md": CURRENT_RUNTIME,
    "reports/theory_authority_stack_board.md": CURRENT_RUNTIME,
    "reports/eth_atr_runtime_status_board.md": CURRENT_RUNTIME,
    "reports/mt5_user_visibility_board.md": CURRENT_RUNTIME,
    "reports/organism_state.md": CURRENT_RUNTIME,
    "reports/live_btcusd_concentration_board.md": CURRENT_RUNTIME,
    "reports/live_m5_portfolio_board.md": CURRENT_RUNTIME,
    "reports/eth_decommission_packet.md": HISTORICAL,
    "reports/operator_decision_board.md": HISTORICAL,
    "reports/capacity_cleanup_report.md": HISTORICAL,
    "reports/organism_state_2026-04-14.md": HISTORICAL,
    "reports/organism_dashboard.md": HISTORICAL,
}


def head_text(path: Path, *, line_limit: int = HEAD_LINE_LIMIT) -> str:
    lines = path.read_text(encoding="utf-8", errors="ignore").splitlines()
    return "\n".join(lines[:line_limit]).lower()


def classify_banner(text: str) -> str | None:
    normalized = str(text or "").lower()
    if any(marker in normalized for marker in CURRENT_MARKERS):
        return CURRENT_RUNTIME
    if any(marker in normalized for marker in HISTORICAL_MARKERS):
        return HISTORICAL
    return None


def validate_targets(root: Path = ROOT, expectations: dict[str, str] | None = None) -> dict[str, object]:
    expected = expectations or FILE_EXPECTATIONS
    errors: list[str] = []
    checked: list[dict[str, str]] = []

    for relative_path, wanted in expected.items():
        path = root / relative_path
        if not path.exists():
            errors.append(f"missing_file: {relative_path}")
            continue
        found = classify_banner(head_text(path))
        checked.append({"path": relative_path, "expected": wanted, "found": found or "missing"})
        if found != wanted:
            errors.append(f"missing_or_wrong_banner: {relative_path} expected={wanted} found={found or 'missing'}")

    return {
        "ok": not errors,
        "checked_count": len(checked),
        "checked": checked,
        "errors": errors,
    }


def main() -> int:
    result = validate_targets()
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if bool(result.get("ok")) else 1


if __name__ == "__main__":
    raise SystemExit(main())
