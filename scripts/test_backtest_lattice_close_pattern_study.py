#!/usr/bin/env python3
from __future__ import annotations

import sys
import unittest
from pathlib import Path


SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import backtest_lattice_close_pattern_study as study


class ClosePatternStudyTests(unittest.TestCase):
    def test_target_signed_level_maps_user_question_correctly(self) -> None:
        ticket = study.StudyTicket(direction="SELL", entry_price=5.0, opened_time=1, level_idx=5)

        stepback = study.CloseStudyContract(
            symbol="BTCUSD",
            timeframe="M15",
            shape_id="shape",
            step_buy_px=1.0,
            step_sell_px=1.0,
            max_open_per_side=10,
            rearm_variant="rearm_lvl2_exc1",
            rearm_cooldown_bars=0,
            momentum_gate=False,
            base_variant_label="base",
            policy_name="independent_stepback1_exact",
            policy_mode="independent",
            policy_description="",
            close_style="outer",
            close_alpha=0.0,
            sell_gap=1,
            buy_gap=1,
            retrace_steps=1,
            cross_anchor_steps=None,
            mirror_depth=False,
            hybrid_profile=None,
        )
        anchor_zero = study.CloseStudyContract(
            **{**stepback.__dict__, "policy_name": "independent_anchor_zero_exact", "retrace_steps": None, "cross_anchor_steps": 0}
        )
        through_zero = study.CloseStudyContract(
            **{**stepback.__dict__, "policy_name": "independent_through_zero_1_exact", "retrace_steps": None, "cross_anchor_steps": 1}
        )
        far_side = study.CloseStudyContract(
            **{**stepback.__dict__, "policy_name": "independent_far_side_mirror_exact", "retrace_steps": None, "cross_anchor_steps": None, "mirror_depth": True}
        )

        self.assertEqual(study._target_signed_level(ticket, stepback), 4)
        self.assertEqual(study._target_signed_level(ticket, anchor_zero), 0)
        self.assertEqual(study._target_signed_level(ticket, through_zero), -1)
        self.assertEqual(study._target_signed_level(ticket, far_side), -5)

    def test_signed_level_price_respects_asymmetric_steps(self) -> None:
        price_at_positive_three = study._signed_level_price(anchor=10.0, sell_step_px=2.0, buy_step_px=1.0, signed_level=3)
        price_at_negative_two = study._signed_level_price(anchor=10.0, sell_step_px=2.0, buy_step_px=1.0, signed_level=-2)
        self.assertEqual(price_at_positive_three, 16.0)
        self.assertEqual(price_at_negative_two, 8.0)

    def test_build_contract_variants_includes_current_and_question_policies(self) -> None:
        base = study.BestContract(
            symbol="GBPUSD",
            timeframe="M15",
            shape_id="gbpusd_trend_harvest_v1",
            step_buy_px=0.0011,
            step_sell_px=0.00055,
            max_open_per_side=15,
            close_style="outer",
            close_alpha=0.5,
            sell_gap=2,
            buy_gap=3,
            rearm_variant="rearm_lvl2_exc1",
            rearm_cooldown_bars=0,
            momentum_gate=False,
            variant_label="outer_guarded_step0.75_cap+3",
        )
        variants = study.build_contract_variants(base)
        names = {variant.policy_name for variant in variants}
        self.assertIn("current_contract", names)
        self.assertIn("penetration_outer_gap1_exact", names)
        self.assertIn("independent_anchor_zero_exact", names)
        self.assertIn("independent_through_zero_1_exact", names)
        self.assertIn("independent_far_side_mirror_exact", names)
        self.assertIn("inner_fast", names)
        self.assertIn("hybrid_stepback_zero_cross", names)
        self.assertEqual(len(variants), len(study.POLICIES))

    def test_hybrid_profiles_map_depth_bands(self) -> None:
        ticket_shallow = study.StudyTicket(direction="SELL", entry_price=2.0, opened_time=1, level_idx=2)
        ticket_mid = study.StudyTicket(direction="SELL", entry_price=4.0, opened_time=1, level_idx=4)
        ticket_deep = study.StudyTicket(direction="SELL", entry_price=7.0, opened_time=1, level_idx=7)
        contract = study.CloseStudyContract(
            symbol="GBPUSD",
            timeframe="M15",
            shape_id="shape",
            step_buy_px=1.0,
            step_sell_px=1.0,
            max_open_per_side=10,
            rearm_variant="rearm_lvl2_exc1",
            rearm_cooldown_bars=0,
            momentum_gate=False,
            base_variant_label="base",
            policy_name="hybrid_stepback_zero_cross",
            policy_mode="hybrid",
            policy_description="",
            close_style="outer",
            close_alpha=0.0,
            sell_gap=1,
            buy_gap=1,
            retrace_steps=None,
            cross_anchor_steps=None,
            mirror_depth=False,
            hybrid_profile="stepback_zero_cross",
        )
        self.assertEqual(study._target_signed_level(ticket_shallow, contract), 1)
        self.assertEqual(study._target_signed_level(ticket_mid, contract), 0)
        self.assertEqual(study._target_signed_level(ticket_deep, contract), -1)


if __name__ == "__main__":
    unittest.main()
