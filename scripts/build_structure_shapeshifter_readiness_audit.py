#!/usr/bin/env python3
"""Audit the repo's current structure-shapeshifter readiness without changing runtime behavior."""
from __future__ import annotations

import importlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent.parent
DETECTOR_PATH = ROOT / "scripts" / "price_structure_detector.py"
BRIDGE_PATH = ROOT / "scripts" / "structure_shapeshifter_bridge.py"
RUNNER_PATH = ROOT / "scripts" / "tick_penetration_lattice_core.py"
OUTPUT_JSON = ROOT / "reports" / "structure_shapeshifter_readiness_audit.json"
OUTPUT_MD = ROOT / "reports" / "structure_shapeshifter_readiness_audit.md"


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def line_number_containing(source: str, needle: str, start_line: int = 1) -> int | None:
    for idx, line in enumerate(source.splitlines(), start=1):
        if idx < start_line:
            continue
        if needle in line:
            return idx
    return None


def relative_line_ref(path: Path, line_number: int | None) -> str:
    rel = path.relative_to(ROOT).as_posix()
    if line_number is None:
        return rel
    return f"{rel}:{line_number}"


def analyze_detector_exports(detector_source: str) -> dict[str, Any]:
    return {
        "has_analyze_symbol": "def analyze_symbol(" in detector_source,
        "has_detect_structure": "def detect_structure(" in detector_source,
        "has_structure_to_geometry": "def structure_to_geometry(" in detector_source,
        "analyze_symbol_line": line_number_containing(detector_source, "def analyze_symbol("),
        "detect_structure_line": line_number_containing(detector_source, "def detect_structure("),
        "structure_to_geometry_line": line_number_containing(detector_source, "def structure_to_geometry("),
    }


def analyze_bridge_targets(bridge_source: str) -> dict[str, Any]:
    return {
        "imports_detect_structure": "detect_structure" in bridge_source,
        "imports_structure_to_geometry": "structure_to_geometry" in bridge_source,
        "writes_base_step_buy_px": "base_step_buy_px" in bridge_source,
        "writes_base_step_sell_px": "base_step_sell_px" in bridge_source,
        "writes_engine_step_buy": "engine.step_buy" in bridge_source,
        "writes_engine_step_sell": "engine.step_sell" in bridge_source,
        "writes_state_step_buy": "state.step_buy" in bridge_source,
        "writes_state_step_sell": "state.step_sell" in bridge_source,
        "import_line": line_number_containing(bridge_source, "from price_structure_detector import"),
        "engine_step_buy_line": line_number_containing(bridge_source, "engine.step_buy"),
        "engine_step_sell_line": line_number_containing(bridge_source, "engine.step_sell"),
        "state_step_buy_line": line_number_containing(bridge_source, "state.step_buy"),
        "state_step_sell_line": line_number_containing(bridge_source, "state.step_sell"),
    }


def analyze_runner_scheduling(runner_source: str) -> dict[str, Any]:
    structure_gate_line = line_number_containing(
        runner_source,
        "if self.allow_dynamic_geometry and self._structure_bar_count >= self._structure_check_interval and hasattr(self, 'history') and self.history:",
    )
    reset_line = line_number_containing(
        runner_source,
        "self._structure_bar_count = 0",
        start_line=(structure_gate_line or 1),
    )
    shared_box_gate_line = line_number_containing(
        runner_source,
        "if self.allow_dynamic_geometry and self._structure_bar_count >= self._structure_check_interval and hasattr(self, 'symbol') and event_path:",
    )
    box_gate_line = line_number_containing(
        runner_source,
        "if self.allow_dynamic_geometry and self._box_aware_bar_count >= self._structure_check_interval and hasattr(self, 'symbol') and event_path:",
    )
    return {
        "outer_structure_gate": structure_gate_line is not None,
        "bridge_inner_gate": 'if state["bar_count"] < check_interval_bars:' in runner_source,
        "structure_gate_line": structure_gate_line,
        "reset_line": reset_line,
        "box_gate_line": box_gate_line,
        "shared_box_gate_line": shared_box_gate_line,
        "has_separate_box_counter": "_box_aware_bar_count" in runner_source,
        "shared_counter_shadowed": (
            structure_gate_line is not None
            and reset_line is not None
            and shared_box_gate_line is not None
            and structure_gate_line < reset_line < shared_box_gate_line
        ),
    }


def analyze_bridge_frequency(bridge_source: str, runner_source: str) -> dict[str, Any]:
    return {
        "runner_outer_gate": "_structure_bar_count >= self._structure_check_interval" in runner_source,
        "bridge_inner_gate": 'state["bar_count"] += 1' in bridge_source and 'if state["bar_count"] < check_interval_bars:' in bridge_source,
        "bridge_bar_count_line": line_number_containing(bridge_source, 'state["bar_count"] += 1'),
        "bridge_gate_line": line_number_containing(bridge_source, 'if state["bar_count"] < check_interval_bars:'),
    }


def probe_bridge_import() -> str | None:
    scripts_dir = str((ROOT / "scripts").resolve())
    if scripts_dir not in sys.path:
        sys.path.insert(0, scripts_dir)
    sys.modules.pop("structure_shapeshifter_bridge", None)
    try:
        importlib.import_module("structure_shapeshifter_bridge")
    except Exception as exc:  # pragma: no cover - exercised in live audit
        return f"{exc.__class__.__name__}: {exc}"
    return None


def build_finding(
    finding_id: str,
    status: str,
    severity: str,
    title: str,
    detail: str,
    evidence: list[str],
) -> dict[str, Any]:
    return {
        "finding_id": finding_id,
        "status": status,
        "severity": severity,
        "title": title,
        "detail": detail,
        "evidence": evidence,
    }


def build_findings(
    detector_source: str,
    bridge_source: str,
    runner_source: str,
    bridge_import_error: str | None,
) -> list[dict[str, Any]]:
    detector = analyze_detector_exports(detector_source)
    bridge = analyze_bridge_targets(bridge_source)
    scheduling = analyze_runner_scheduling(runner_source)
    frequency = analyze_bridge_frequency(bridge_source, runner_source)
    findings: list[dict[str, Any]] = []

    if bridge_import_error or not detector["has_detect_structure"] or not detector["has_structure_to_geometry"]:
        findings.append(
            build_finding(
                "bridge_import_contract",
                "fail",
                "high",
                "Bridge import contract does not match detector exports",
                "The live runner imports structure_shapeshifter_bridge, but that bridge expects detector symbols that the current detector file does not expose.",
                [
                    f"{relative_line_ref(BRIDGE_PATH, bridge['import_line'])}: bridge imports detect_structure/structure_to_geometry",
                    f"{relative_line_ref(DETECTOR_PATH, detector['analyze_symbol_line'])}: detector exposes analyze_symbol",
                    (
                        f"{relative_line_ref(DETECTOR_PATH, detector['detect_structure_line'])}: detect_structure present"
                        if detector["has_detect_structure"]
                        else f"{relative_line_ref(DETECTOR_PATH, detector['analyze_symbol_line'])}: no detect_structure export"
                    ),
                    (
                        f"{relative_line_ref(DETECTOR_PATH, detector['structure_to_geometry_line'])}: structure_to_geometry present"
                        if detector["has_structure_to_geometry"]
                        else f"{relative_line_ref(DETECTOR_PATH, detector['analyze_symbol_line'])}: no structure_to_geometry export"
                    ),
                    f"import probe: {bridge_import_error or 'ok'}",
                ],
            )
        )

    if (
        not bridge["writes_base_step_buy_px"]
        and not bridge["writes_base_step_sell_px"]
        and (
            bridge["writes_engine_step_buy"]
            or bridge["writes_engine_step_sell"]
            or bridge["writes_state_step_buy"]
            or bridge["writes_state_step_sell"]
        )
    ):
        findings.append(
            build_finding(
                "bridge_runtime_field_target",
                "fail",
                "high",
                "Bridge mutates non-runtime step fields",
                "The runner consumes base_step_buy_px/base_step_sell_px, but the bridge writes legacy step fields instead.",
                [
                    f"{relative_line_ref(BRIDGE_PATH, bridge['engine_step_buy_line'])}: bridge writes engine.step_buy",
                    f"{relative_line_ref(BRIDGE_PATH, bridge['engine_step_sell_line'])}: bridge writes engine.step_sell",
                    f"{relative_line_ref(BRIDGE_PATH, bridge['state_step_buy_line'])}: bridge writes engine.state.step_buy",
                    f"{relative_line_ref(BRIDGE_PATH, bridge['state_step_sell_line'])}: bridge writes engine.state.step_sell",
                    f"{relative_line_ref(RUNNER_PATH, line_number_containing(runner_source, 'self.base_step_buy_px ='))}: runner stores live buy geometry on base_step_buy_px",
                    f"{relative_line_ref(RUNNER_PATH, line_number_containing(runner_source, 'self.base_step_sell_px ='))}: runner stores live sell geometry on base_step_sell_px",
                ],
            )
        )

    if scheduling["shared_counter_shadowed"] and not scheduling["has_separate_box_counter"]:
        findings.append(
            build_finding(
                "runner_shared_counter_shadow",
                "fail",
                "medium",
                "Shared counter reset shadows the later box-aware check",
                "The same structure counter gates both structure and box-aware geometry, but it is reset before the later box-aware block evaluates.",
                [
                    f"{relative_line_ref(RUNNER_PATH, scheduling['structure_gate_line'])}: structure-aware gate uses _structure_bar_count",
                    f"{relative_line_ref(RUNNER_PATH, scheduling['reset_line'])}: counter reset inside structure-aware block",
                    f"{relative_line_ref(RUNNER_PATH, scheduling['shared_box_gate_line'])}: later box-aware block reuses the same counter",
                ],
            )
        )

    if frequency["runner_outer_gate"] and frequency["bridge_inner_gate"]:
        findings.append(
            build_finding(
                "double_gated_structure_schedule",
                "warn",
                "medium",
                "Structure checks are gated in both runner and bridge",
                "Even after the import contract is repaired, the current scheduling shape would still call into a second bar-count gate inside the bridge.",
                [
                    f"{relative_line_ref(RUNNER_PATH, line_number_containing(runner_source, '_structure_bar_count >= self._structure_check_interval'))}: runner only calls bridge every N bars",
                    f"{relative_line_ref(BRIDGE_PATH, frequency['bridge_bar_count_line'])}: bridge maintains its own bar counter",
                    f"{relative_line_ref(BRIDGE_PATH, frequency['bridge_gate_line'])}: bridge applies a second interval gate",
                ],
            )
        )

    if "from structure_shapeshifter_bridge import check_and_adapt" in runner_source:
        findings.append(
            build_finding(
                "live_runner_bridge_surface",
                "info",
                "low",
                "Live runner is wired to the lightweight structure bridge",
                "The current live path imports structure_shapeshifter_bridge, not the broader shapeshifter_bridge regime stack.",
                [
                    f"{relative_line_ref(RUNNER_PATH, line_number_containing(runner_source, 'from structure_shapeshifter_bridge import check_and_adapt'))}: active runner import",
                    f"{relative_line_ref(ROOT / 'scripts' / 'shapeshifter_bridge.py', line_number_containing(read_text(ROOT / 'scripts' / 'shapeshifter_bridge.py'), 'def check_and_adapt('))}: broader bridge exists separately",
                ],
            )
        )

    return findings


def build_payload() -> dict[str, Any]:
    detector_source = read_text(DETECTOR_PATH)
    bridge_source = read_text(BRIDGE_PATH)
    runner_source = read_text(RUNNER_PATH)
    bridge_import_error = probe_bridge_import()
    findings = build_findings(
        detector_source=detector_source,
        bridge_source=bridge_source,
        runner_source=runner_source,
        bridge_import_error=bridge_import_error,
    )
    status_counts = {
        status: sum(1 for finding in findings if finding["status"] == status)
        for status in ("fail", "warn", "info")
    }
    verdict = "not_shadow_ready" if status_counts["fail"] else ("caution" if status_counts["warn"] else "ready_for_shadow_review")
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "verdict": verdict,
        "source_paths": {
            "detector": str(DETECTOR_PATH.relative_to(ROOT)),
            "bridge": str(BRIDGE_PATH.relative_to(ROOT)),
            "runner": str(RUNNER_PATH.relative_to(ROOT)),
        },
        "summary": {
            "status_counts": status_counts,
            "recommended_next_step": (
                "Keep structure-shapeshifter on an audit-only path. Repair the import contract, map geometry onto base_step_* fields, split scheduling counters, then prove the repaired path in shadow before any live activation."
                if verdict == "not_shadow_ready"
                else (
                    "Shadow-grade repair is complete. Keep the dedicated shadow lane running, watch for repeated "
                    "structure-driven base_step_* mutations and stable restore behavior, and keep live claims blocked "
                    "until the forward sample is honest."
                )
            ),
        },
        "findings": findings,
        "notes": [
            "This audit is a governance surface only. It does not enable or disable runtime geometry.",
            "The generated verdict should be used before describing structure-shapeshifter as live-ready or broadly adaptive.",
        ],
    }


def write_markdown(payload: dict[str, Any]) -> None:
    lines = [
        "# Structure Shapeshifter Readiness Audit",
        "",
        "This audit checks whether the repo's current structure-aware adaptive path is actually wired well enough to deserve shadow-ready language.",
        "",
        "## Current Read",
        "",
        f"- verdict: `{payload['verdict']}`",
        f"- counts: `{payload['summary']['status_counts']}`",
        f"- next step: {payload['summary']['recommended_next_step']}",
        "",
        "## Findings",
        "",
        "| ID | Status | Severity | Title |",
        "|---|---|---|---|",
    ]

    for finding in payload["findings"]:
        lines.append(
            f"| `{finding['finding_id']}` | `{finding['status']}` | `{finding['severity']}` | {finding['title']} |"
        )

    lines.extend(["", "## Evidence", ""])
    for finding in payload["findings"]:
        lines.append(f"### {finding['finding_id']}")
        lines.append("")
        lines.append(f"- status: `{finding['status']}`")
        lines.append(f"- severity: `{finding['severity']}`")
        lines.append(f"- detail: {finding['detail']}")
        for item in finding["evidence"]:
            lines.append(f"- evidence: `{item}`")
        lines.append("")

    lines.extend(["## Notes", ""])
    for note in payload["notes"]:
        lines.append(f"- {note}")

    OUTPUT_MD.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    payload = build_payload()
    OUTPUT_JSON.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    write_markdown(payload)
    print(f"Wrote {OUTPUT_JSON}")
    print(f"Wrote {OUTPUT_MD}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
