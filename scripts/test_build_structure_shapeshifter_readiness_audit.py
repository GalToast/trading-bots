from __future__ import annotations

import unittest

import scripts.build_structure_shapeshifter_readiness_audit as audit


class BuildStructureShapeshifterReadinessAuditTests(unittest.TestCase):
    def test_build_findings_flags_import_contract_gap(self) -> None:
        findings = audit.build_findings(
            detector_source="def analyze_symbol(symbol, rates):\n    return {}\n",
            bridge_source="from price_structure_detector import detect_structure, structure_to_geometry\n",
            runner_source="",
            bridge_import_error="ImportError: cannot import name 'detect_structure'",
        )
        indexed = {item["finding_id"]: item for item in findings}
        self.assertEqual(indexed["bridge_import_contract"]["status"], "fail")

    def test_build_findings_flags_runtime_field_mismatch(self) -> None:
        findings = audit.build_findings(
            detector_source=(
                "def analyze_symbol(symbol, rates):\n    return {}\n"
                "def detect_structure(symbol, rates):\n    return {}\n"
                "def structure_to_geometry(structure):\n    return {}\n"
            ),
            bridge_source=(
                "from price_structure_detector import detect_structure, structure_to_geometry\n"
                "engine.step_buy = geometry['step_buy']\n"
                "engine.step_sell = geometry['step_sell']\n"
                "engine.state.step_buy = geometry['step_buy']\n"
                "engine.state.step_sell = geometry['step_sell']\n"
            ),
            runner_source=(
                "self.base_step_sell_px = 1.0\n"
                "self.base_step_buy_px = 1.0\n"
            ),
            bridge_import_error=None,
        )
        indexed = {item["finding_id"]: item for item in findings}
        self.assertEqual(indexed["bridge_runtime_field_target"]["status"], "fail")

    def test_build_findings_flags_shared_counter_shadow(self) -> None:
        runner_source = "\n".join(
            [
                "self._structure_bar_count += 1",
                "if self.allow_dynamic_geometry and self._structure_bar_count >= self._structure_check_interval and hasattr(self, 'history') and self.history:",
                "    self._structure_bar_count = 0",
                "if self.allow_dynamic_geometry and self._structure_bar_count >= self._structure_check_interval and hasattr(self, 'symbol') and event_path:",
                "    pass",
            ]
        )
        findings = audit.build_findings(
            detector_source=(
                "def analyze_symbol(symbol, rates):\n    return {}\n"
                "def detect_structure(symbol, rates):\n    return {}\n"
                "def structure_to_geometry(structure):\n    return {}\n"
            ),
            bridge_source="",
            runner_source=runner_source,
            bridge_import_error=None,
        )
        indexed = {item["finding_id"]: item for item in findings}
        self.assertEqual(indexed["runner_shared_counter_shadow"]["status"], "fail")

    def test_build_findings_accepts_separate_box_counter(self) -> None:
        runner_source = "\n".join(
            [
                "self._structure_bar_count += 1",
                "if self.allow_dynamic_geometry and self._structure_bar_count >= self._structure_check_interval and hasattr(self, 'history') and self.history:",
                "    self._structure_bar_count = 0",
                "self._box_aware_bar_count += 1",
                "if self.allow_dynamic_geometry and self._box_aware_bar_count >= self._structure_check_interval and hasattr(self, 'symbol') and event_path:",
                "    self._box_aware_bar_count = 0",
            ]
        )
        findings = audit.build_findings(
            detector_source=(
                "def analyze_symbol(symbol, rates):\n    return {}\n"
                "def detect_structure(symbol, rates):\n    return {}\n"
                "def structure_to_geometry(structure):\n    return {}\n"
            ),
            bridge_source="",
            runner_source=runner_source,
            bridge_import_error=None,
        )
        indexed = {item["finding_id"]: item for item in findings}
        self.assertNotIn("runner_shared_counter_shadow", indexed)

    def test_build_findings_warns_on_double_gating(self) -> None:
        findings = audit.build_findings(
            detector_source=(
                "def analyze_symbol(symbol, rates):\n    return {}\n"
                "def detect_structure(symbol, rates):\n    return {}\n"
                "def structure_to_geometry(structure):\n    return {}\n"
            ),
            bridge_source=(
                'state["bar_count"] += 1\n'
                'if state["bar_count"] < check_interval_bars:\n'
                "    return {}\n"
            ),
            runner_source="_structure_bar_count >= self._structure_check_interval\n",
            bridge_import_error=None,
        )
        indexed = {item["finding_id"]: item for item in findings}
        self.assertEqual(indexed["double_gated_structure_schedule"]["status"], "warn")


if __name__ == "__main__":
    unittest.main()
