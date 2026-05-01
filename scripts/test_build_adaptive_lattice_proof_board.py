#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path


SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import build_adaptive_lattice_proof_board as proof_board


MINIMAL_LIBRARY = {
    "controller_defaults": {
        "fallback_regime": "mixed",
    },
    "blockers": [
        {
            "blocker_id": "bounded_close_style_runtime_fault",
            "status": "active",
            "applies_to_families": ["bounded"],
            "affected_symbols": ["USDJPY"],
        }
    ],
    "symbols": {
        "USDJPY": {
            "stage": "blocked_runtime",
            "preferred_family": "bounded",
            "candidate_shapes": [
                {
                    "shape_id": "usdjpy_bounded_survival_v1",
                    "family": "bounded",
                    "regime_targets": ["mixed", "ranging"],
                    "risk_profile": "conservative",
                    "portfolio_profile": "light",
                    "step_method": {"kind": "atr_multiple", "coeff": 1.0},
                    "close": {
                        "style": "all_profitable",
                        "bounded_close_gap": 2,
                        "same_bar_min_pnl": 0.0,
                    },
                    "evidence": {
                        "status": "blocked_runtime",
                        "note": "USDJPY bounded adaptive candidate.",
                    },
                }
            ],
        }
    },
}


class AdaptiveLatticeProofBoardTests(unittest.TestCase):
    def test_historical_err_logs_do_not_keep_bounded_blocker_active(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            config_path = tmp_path / "adaptive_lattice_shape_library.json"
            config_path.write_text(json.dumps(MINIMAL_LIBRARY), encoding="utf-8")

            regime_path = tmp_path / "regime_adaptive_steps.json"
            regime_path.write_text("[]", encoding="utf-8")

            watchdog_path = tmp_path / "shadow_watchdog_report.json"
            watchdog_path.write_text(json.dumps({"rows": []}), encoding="utf-8")

            gap2_err = tmp_path / "shadow_usdjpy_gap2.err.log"
            gap2_err.write_text("NameError: name 'close_style' is not defined", encoding="utf-8")
            shallow_err = tmp_path / "shadow_usdjpy_shallow03.err.log"
            shallow_err.write_text("", encoding="utf-8")

            core_path = tmp_path / "tick_penetration_lattice_core.py"
            core_path.write_text("# newer core\n", encoding="utf-8")

            now = time.time()
            os.utime(gap2_err, (now - 30.0, now - 30.0))
            os.utime(core_path, (now, now))

            old_paths = (
                proof_board.CONFIG_PATH,
                proof_board.REGIME_PATH,
                proof_board.SHADOW_WATCHDOG_PATH,
                proof_board.GAP2_ERR_PATH,
                proof_board.SHALLOW_ERR_PATH,
                proof_board.CORE_PATH,
            )
            proof_board.CONFIG_PATH = config_path
            proof_board.REGIME_PATH = regime_path
            proof_board.SHADOW_WATCHDOG_PATH = watchdog_path
            proof_board.GAP2_ERR_PATH = gap2_err
            proof_board.SHALLOW_ERR_PATH = shallow_err
            proof_board.CORE_PATH = core_path
            try:
                payload = proof_board.build_payload()
            finally:
                (
                    proof_board.CONFIG_PATH,
                    proof_board.REGIME_PATH,
                    proof_board.SHADOW_WATCHDOG_PATH,
                    proof_board.GAP2_ERR_PATH,
                    proof_board.SHALLOW_ERR_PATH,
                    proof_board.CORE_PATH,
                ) = old_paths

        blocker = payload["blockers"][0]
        row = payload["rows"][0]

        self.assertFalse(blocker["active"])
        self.assertIn("historical bounded close-style fault only", blocker["read"])
        self.assertEqual(row["symbol"], "USDJPY")
        self.assertEqual(row["status"], "ok")
        self.assertEqual(row["recommended_shape_id"], "usdjpy_bounded_survival_v1")
        self.assertEqual(row["stage"], "bounded_proof_pending")
        self.assertEqual(row["source_stage"], "blocked_runtime")
        self.assertEqual(row["profit_mode"], "balanced_harvest")


if __name__ == "__main__":
    unittest.main()
