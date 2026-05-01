#!/usr/bin/env python3
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path


SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import live_kraken_spot_frontier_maker_machinegun_shadow as runner
from death_spiral_prevention import LossTracker


def make_engine(
    tmpdir: Path,
    reentry_cooldown_overrides: dict[str, int] | None = None,
    systemic_max_positions: int = 1,
    systemic_selection_limit: int = 1,
    allowed_quote_currencies: list[str] | None = None,
    systemic_exclude_products: list[str] | None = None,
    systemic_min_live_to_board_spread_ratio: float = 0.0,
    min_in_position_spread_bps: float = 0.0,
    min_harvest_age_seconds: float = 0.0,
    min_notional_by_product: dict[str, float] | None = None,
    enforce_min_notional: bool = False,
    min_entry_microfill_rate: float = 0.0,
    min_exit_microfill_rate: float = 0.0,
    systemic_rank_mode: str = "heat",
    systemic_preopen_selection_multiplier: int = 1,
    maker_exit_improve_after_seconds: float = 0.0,
    maker_exit_improve_spread_frac: float = 0.0,
    maker_exit_improve_min_net_pct: float = 0.0,
    maker_exit_improve_offset_fracs: list[float] | None = None,
    maker_exit_improve_min_offset_microfill_rate: float = 0.0,
    maker_exit_refresh_fill_boost: float = 0.0,
    maker_first_adverse_bid_stop: bool = False,
    require_bid_taker_green_for_maker_close: bool = False,
    bid_taker_green_min_net_pct: float = 0.0,
    enable_micro_cloud: bool = False,
) -> runner.MakerMachinegunEngine:
    engine = runner.MakerMachinegunEngine(
        starting_cash_usd=100.0,
        maker_fee_bps=25.0,
        reentry_cooldown_polls=60,
        reentry_cooldown_overrides=reentry_cooldown_overrides,
        max_loss_pct=3.0,
        no_mfe_stop_pct=0.35,
        no_mfe_stop_min_age_seconds=90.0,
        target_net_pct_per_hour=5.0,
        entry_confirmation_polls=1,
        min_quote_usd=5.0,
        rotation_buffer_pct=0.5,
        min_profit_to_trail_usd=0.01,
        min_rent_harvest_net_pct=0.10,
        systemic_max_positions=systemic_max_positions,
        idiosyncratic_max_positions=4,
        systemic_deploy_pct=0.10,
        idiosyncratic_deploy_pct=0.08,
        systemic_selection_limit=systemic_selection_limit,
        allowed_quote_currencies=allowed_quote_currencies,
        systemic_exclude_products=systemic_exclude_products,
        max_quote_usd=8.0,
        systemic_min_entry_spread_bps=100.0,
        systemic_min_entry_mer=3.5,
        systemic_min_live_spread_bps=10.0,
        systemic_min_live_to_board_spread_ratio=systemic_min_live_to_board_spread_ratio,
        min_notional_by_product=min_notional_by_product,
        enforce_min_notional=enforce_min_notional,
        min_in_position_spread_bps=min_in_position_spread_bps,
        min_harvest_age_seconds=min_harvest_age_seconds,
        min_entry_microfill_rate=min_entry_microfill_rate,
        min_exit_microfill_rate=min_exit_microfill_rate,
        systemic_rank_mode=systemic_rank_mode,
        systemic_preopen_selection_multiplier=systemic_preopen_selection_multiplier,
        maker_exit_improve_after_seconds=maker_exit_improve_after_seconds,
        maker_exit_improve_spread_frac=maker_exit_improve_spread_frac,
        maker_exit_improve_min_net_pct=maker_exit_improve_min_net_pct,
        maker_exit_improve_offset_fracs=maker_exit_improve_offset_fracs,
        maker_exit_improve_min_offset_microfill_rate=maker_exit_improve_min_offset_microfill_rate,
        maker_exit_refresh_fill_boost=maker_exit_refresh_fill_boost,
        maker_first_adverse_bid_stop=maker_first_adverse_bid_stop,
        require_bid_taker_green_for_maker_close=require_bid_taker_green_for_maker_close,
        bid_taker_green_min_net_pct=bid_taker_green_min_net_pct,
        enable_micro_cloud=enable_micro_cloud,
    )
    engine.tracker = LossTracker(max_consecutive_losses=2, cooldown_seconds=3600, state_path=tmpdir / "loss.json")
    return engine


class KrakenMakerRunnerTests(unittest.TestCase):
    def test_explicit_reentry_cooldown_overrides_survive_default_staggering(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            engine = make_engine(
                Path(tmp),
                reentry_cooldown_overrides={
                    "HOUSE-USD": 5,
                    "FOLKS-USD": 10,
                    "BTR-USD": 10,
                },
            )

            self.assertEqual(engine.reentry_cooldown_for("HOUSE-USD"), 5)
            self.assertEqual(engine.reentry_cooldown_for("FOLKS-USD"), 10)
            self.assertEqual(engine.reentry_cooldown_for("BTR-USD"), 10)

    def test_default_staggered_reentry_cooldowns_apply_without_overrides(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            engine = make_engine(Path(tmp))

            self.assertEqual(engine.reentry_cooldown_for("HOUSE-USD"), 15)
            self.assertEqual(engine.reentry_cooldown_for("FOLKS-USD"), 20)
            self.assertEqual(engine.reentry_cooldown_for("BTR-USD"), 25)
            self.assertEqual(engine.reentry_cooldown_for("OTHER-USD"), 60)

    def test_microfill_calibration_caps_fill_probability_when_enough_samples_exist(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            engine = make_engine(Path(tmp))
            engine.enable_microfill_calibration = True
            engine.microfill_min_trials = 4
            engine.microfill_summary = {
                "by_product_side": {
                    "HOUSE-USD|buy": {
                        "probable_queue_depletion_fill_proxy": 1,
                        "unfilled_timeout": 3,
                    }
                }
            }

            calibrated, rate, samples = engine.calibrate_fill_prob("HOUSE-USD", "buy", 0.95)

            self.assertEqual(samples, 4)
            self.assertEqual(rate, 0.25)
            self.assertEqual(calibrated, 0.25)

    def test_microfill_calibration_ignores_under_sampled_products(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            engine = make_engine(Path(tmp))
            engine.enable_microfill_calibration = True
            engine.microfill_min_trials = 6
            engine.microfill_summary = {
                "by_product_side": {
                    "HOUSE-USD|sell": {
                        "probable_queue_depletion_fill_proxy": 1,
                        "unfilled_timeout": 1,
                    }
                }
            }

            calibrated, rate, samples = engine.calibrate_fill_prob("HOUSE-USD", "sell", 0.95)

            self.assertEqual(samples, 2)
            self.assertIsNone(rate)
            self.assertEqual(calibrated, 0.95)

    def test_microfill_calibration_falls_back_to_product_samples(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            engine = make_engine(Path(tmp))
            engine.enable_microfill_calibration = True
            engine.microfill_min_trials = 4
            engine.microfill_summary = {
                "by_product_side": {
                    "HOUSE-USD|buy": {
                        "unfilled_timeout": 2,
                    }
                },
                "by_product": {
                    "HOUSE-USD": {
                        "unfilled_timeout": 4,
                    }
                },
            }

            calibrated, rate, samples = engine.calibrate_fill_prob("HOUSE-USD", "buy", 0.95)

            self.assertEqual(samples, 4)
            self.assertEqual(rate, 0.0)
            self.assertEqual(calibrated, 0.05)

    def test_loss_exit_realizes_taker_net_not_maker_mark(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            event_path = root / "events.jsonl"
            engine = make_engine(root)
            opened_at = (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat()
            engine.cash_usd = 0.0
            engine.active_positions["TEST-USD"] = runner.MachinegunPosition(
                product_id="TEST-USD",
                playbook="maker_harvest",
                entry_price=100.0,
                quantity=0.9975,
                cost_usd=100.0,
                entry_fee=0.25,
                opened_at=opened_at,
                highest_bid=100.0,
                trail_giveback_pct=2.5,
                entry_edge_over_hurdle_pct=0.0,
                max_net_pnl=1.0,
                max_net_pct_on_cost=1.0,
                entry_mer=0.0,
            )
            original_random = runner.random.random
            runner.random.random = lambda: 0.0
            try:
                engine.maybe_close_positions(
                    {"TEST-USD": {"bid": 99.0, "ask": 99.5, "ts": 1}},
                    event_path=event_path,
                )
            finally:
                runner.random.random = original_random

            gross = 0.9975 * 99.0
            expected_net_proceeds = gross - (gross * 0.004)
            expected_net = expected_net_proceeds - 100.0
            self.assertAlmostEqual(engine.cash_usd, expected_net_proceeds, places=9)
            self.assertAlmostEqual(engine.realized_net_usd, expected_net, places=9)
            self.assertEqual(engine.realized_wins_count, 0)
            self.assertNotIn("TEST-USD", engine.active_positions)
            close_event = json.loads(event_path.read_text(encoding="utf-8").splitlines()[-1])
            self.assertEqual(close_event["exit_fee_bps"], 40.0)
            self.assertAlmostEqual(close_event["net"], expected_net, places=6)
            self.assertGreater(close_event["maker_mark_net"], close_event["net"])

    def test_adverse_bid_stop_can_escape_as_maker_when_mark_is_green(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            event_path = root / "events.jsonl"
            engine = make_engine(root, maker_first_adverse_bid_stop=True)
            opened_at = (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat()
            engine.cash_usd = 0.0
            engine.active_positions["ESC-USD"] = runner.MachinegunPosition(
                product_id="ESC-USD",
                playbook="maker_harvest",
                entry_price=100.0,
                quantity=1.0,
                cost_usd=100.0,
                entry_fee=0.25,
                opened_at=opened_at,
                highest_bid=100.15,
                trail_giveback_pct=2.5,
                entry_edge_over_hurdle_pct=0.0,
                max_net_pnl=1.0,
                max_net_pct_on_cost=1.0,
                min_net_pct_on_cost=-0.25,
                peak_net_seen_at=opened_at,
                entry_mer=0.0,
            )
            original_random = runner.random.random
            runner.random.random = lambda: 0.0
            try:
                engine.maybe_close_positions(
                    {"ESC-USD": {"bid": 96.0, "ask": 100.6, "ts": 1}},
                    event_path=event_path,
                )
            finally:
                runner.random.random = original_random

            close_event = json.loads(event_path.read_text(encoding="utf-8").splitlines()[-1])
            self.assertEqual(close_event["reason"], "maker_adverse_bid_escape")
            self.assertEqual(close_event["exit_type"], "maker_fill")
            self.assertEqual(close_event["exit_fee_bps"], 25.0)
            self.assertGreater(close_event["net_pct"], 0.0)
            self.assertLess(close_event["bid_taker_net_pct"], 0.0)

    def test_green_then_red_insurance_closes_before_no_mfe_stop(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            event_path = root / "events.jsonl"
            engine = make_engine(root)
            opened_at = (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat()
            engine.cash_usd = 0.0
            engine.active_positions["ROLL-USD"] = runner.MachinegunPosition(
                product_id="ROLL-USD",
                playbook="maker_harvest",
                entry_price=100.0,
                quantity=1.0,
                cost_usd=100.0,
                entry_fee=0.25,
                opened_at=opened_at,
                highest_bid=100.15,
                trail_giveback_pct=2.5,
                entry_edge_over_hurdle_pct=0.0,
                max_net_pnl=1.0,
                max_net_pct_on_cost=1.0,
                min_net_pct_on_cost=-0.25,
                peak_net_seen_at=opened_at,
                entry_mer=0.0,
            )
            original_random = runner.random.random
            runner.random.random = lambda: 0.0
            try:
                engine.maybe_close_positions(
                    {"ROLL-USD": {"bid": 99.8, "ask": 100.5, "ts": 1}},
                    event_path=event_path,
                )
            finally:
                runner.random.random = original_random

            self.assertNotIn("ROLL-USD", engine.active_positions)
            close_event = json.loads(event_path.read_text(encoding="utf-8").splitlines()[-1])
            self.assertEqual(close_event["reason"], "maker_green_then_red_insurance")
            self.assertEqual(close_event["exit_type"], "maker_fill")
            self.assertEqual(close_event["exit_fee_bps"], 25.0)
            self.assertGreater(close_event["net_pct"], 0.0)
            self.assertLess(close_event["bid_taker_net_pct"], 0.0)
            self.assertAlmostEqual(close_event["green_insurance_activation_pct"], 0.0)
            self.assertAlmostEqual(close_event["green_insurance_giveback_pct"], 0.05)

    def test_live_equivalence_gate_holds_maker_close_when_bid_taker_is_red(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            event_path = root / "events.jsonl"
            engine = make_engine(root, require_bid_taker_green_for_maker_close=True)
            opened_at = (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat()
            engine.cash_usd = 0.0
            engine.active_positions["EQ-USD"] = runner.MachinegunPosition(
                product_id="EQ-USD",
                playbook="maker_harvest",
                entry_price=100.0,
                quantity=1.0,
                cost_usd=100.0,
                entry_fee=0.25,
                opened_at=opened_at,
                highest_bid=100.15,
                trail_giveback_pct=2.5,
                entry_edge_over_hurdle_pct=0.0,
                max_net_pnl=1.0,
                max_net_pct_on_cost=1.0,
                min_net_pct_on_cost=-0.25,
                peak_net_seen_at=opened_at,
                entry_mer=0.0,
            )
            original_random = runner.random.random
            runner.random.random = lambda: 0.0
            try:
                engine.maybe_close_positions(
                    {"EQ-USD": {"bid": 99.8, "ask": 100.5, "ts": 1}},
                    event_path=event_path,
                )
            finally:
                runner.random.random = original_random

            self.assertIn("EQ-USD", engine.active_positions)
            event = json.loads(event_path.read_text(encoding="utf-8").splitlines()[-1])
            self.assertEqual(event["action"], "maker_exit_live_equivalence_hold")
            self.assertLess(event["bid_taker_net_pct"], 0.0)

    def test_live_equivalence_gate_allows_maker_close_when_bid_taker_is_green(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            event_path = root / "events.jsonl"
            engine = make_engine(
                root,
                require_bid_taker_green_for_maker_close=True,
                bid_taker_green_min_net_pct=0.10,
            )
            opened_at = (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat()
            engine.cash_usd = 0.0
            engine.active_positions["SAFE-USD"] = runner.MachinegunPosition(
                product_id="SAFE-USD",
                playbook="maker_harvest",
                entry_price=100.0,
                quantity=1.0,
                cost_usd=100.0,
                entry_fee=0.25,
                opened_at=opened_at,
                highest_bid=100.9,
                trail_giveback_pct=2.5,
                entry_edge_over_hurdle_pct=0.0,
                max_net_pnl=0.0,
                max_net_pct_on_cost=0.0,
                entry_mer=0.0,
            )
            original_random = runner.random.random
            runner.random.random = lambda: 0.0
            try:
                engine.maybe_close_positions(
                    {"SAFE-USD": {"bid": 101.0, "ask": 101.5, "ts": 1}},
                    event_path=event_path,
                )
            finally:
                runner.random.random = original_random

            self.assertNotIn("SAFE-USD", engine.active_positions)
            close_event = json.loads(event_path.read_text(encoding="utf-8").splitlines()[-1])
            self.assertEqual(close_event["action"], "close_maker_shadow")
            self.assertEqual(close_event["exit_type"], "maker_fill")
            self.assertGreaterEqual(close_event["bid_taker_net_pct"], 0.10)
            self.assertFalse(close_event["maker_exit_dependent"])

    def test_green_insurance_does_not_force_taker_loss_when_maker_mark_is_red(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            event_path = root / "events.jsonl"
            engine = make_engine(root)
            opened_at = (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat()
            engine.cash_usd = 0.0
            engine.active_positions["FAST-USD"] = runner.MachinegunPosition(
                product_id="FAST-USD",
                playbook="maker_harvest",
                entry_price=100.0,
                quantity=1.0,
                cost_usd=100.0,
                entry_fee=0.25,
                opened_at=opened_at,
                highest_bid=100.15,
                trail_giveback_pct=2.5,
                entry_edge_over_hurdle_pct=0.0,
                max_net_pnl=1.0,
                max_net_pct_on_cost=1.0,
                min_net_pct_on_cost=-0.25,
                peak_net_seen_at=opened_at,
                entry_mer=0.0,
            )
            original_random = runner.random.random
            runner.random.random = lambda: 1.0
            try:
                engine.maybe_close_positions(
                    {"FAST-USD": {"bid": 99.8, "ask": 99.81, "ts": 1}},
                    event_path=event_path,
                )
            finally:
                runner.random.random = original_random

            self.assertIn("FAST-USD", engine.active_positions)
            self.assertFalse(event_path.exists())

    def test_maker_exit_miss_locks_profit_with_bid_side_taker_net(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            event_path = root / "events.jsonl"
            engine = make_engine(root)
            engine.maker_exit_taker_fallback_seconds = 0.0
            engine.taker_profit_lock_min_net_pct = 0.10
            opened_at = (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat()
            engine.cash_usd = 0.0
            engine.active_positions["LOCK-USD"] = runner.MachinegunPosition(
                product_id="LOCK-USD",
                playbook="maker_harvest",
                entry_price=100.0,
                quantity=1.0,
                cost_usd=100.0,
                entry_fee=0.25,
                opened_at=opened_at,
                highest_bid=100.0,
                trail_giveback_pct=2.5,
                entry_edge_over_hurdle_pct=0.0,
                max_net_pnl=0.0,
                max_net_pct_on_cost=0.0,
                entry_mer=0.0,
            )
            original_random = runner.random.random
            runner.random.random = lambda: 1.0
            try:
                engine.maybe_close_positions(
                    {"LOCK-USD": {"bid": 101.0, "ask": 101.5, "ts": 1}},
                    event_path=event_path,
                )
            finally:
                runner.random.random = original_random

            self.assertNotIn("LOCK-USD", engine.active_positions)
            close_event = json.loads(event_path.read_text(encoding="utf-8").splitlines()[-1])
            expected_net = (101.0 - (101.0 * 0.004)) - 100.0
            self.assertEqual(close_event["reason"], "maker_exit_miss_taker_profit_lock")
            self.assertEqual(close_event["exit_type"], "taker_insurance")
            self.assertEqual(close_event["exit_fee_bps"], 40.0)
            self.assertAlmostEqual(close_event["net"], expected_net, places=6)
            self.assertGreater(close_event["net_pct"], 0.10)
            self.assertAlmostEqual(close_event["bid_taker_net_pct"], close_event["net_pct"], places=4)

    def test_maker_exit_miss_does_not_lock_when_bid_side_taker_net_is_red(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            event_path = root / "events.jsonl"
            engine = make_engine(root)
            engine.maker_exit_taker_fallback_seconds = 0.0
            engine.taker_profit_lock_min_net_pct = 0.10
            opened_at = (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat()
            engine.cash_usd = 0.0
            engine.active_positions["WIDE-USD"] = runner.MachinegunPosition(
                product_id="WIDE-USD",
                playbook="maker_harvest",
                entry_price=100.0,
                quantity=1.0,
                cost_usd=100.0,
                entry_fee=0.25,
                opened_at=opened_at,
                highest_bid=100.0,
                trail_giveback_pct=2.5,
                entry_edge_over_hurdle_pct=0.0,
                max_net_pnl=1.0,
                max_net_pct_on_cost=1.0,
                peak_net_seen_at=opened_at,
                entry_mer=0.0,
            )
            original_random = runner.random.random
            runner.random.random = lambda: 1.0
            try:
                engine.maybe_close_positions(
                    {"WIDE-USD": {"bid": 99.7, "ask": 101.5, "ts": 1}},
                    event_path=event_path,
                )
            finally:
                runner.random.random = original_random

            self.assertIn("WIDE-USD", engine.active_positions)
            events = [json.loads(line) for line in event_path.read_text(encoding="utf-8").splitlines()]
            self.assertEqual(events[-1]["action"], "maker_exit_miss")
            self.assertLess(events[-1]["bid_taker_net_pct"], 0.0)
            self.assertEqual(events[-1]["taker_profit_lock_min_net_pct"], 0.10)

    def test_stale_maker_exit_can_post_inside_spread_when_profit_survives(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            event_path = root / "events.jsonl"
            engine = make_engine(
                root,
                maker_exit_improve_after_seconds=10.0,
                maker_exit_improve_spread_frac=0.50,
                maker_exit_improve_min_net_pct=0.10,
            )
            opened_at = (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat()
            attempted_at = (datetime.now(timezone.utc) - timedelta(seconds=20)).isoformat()
            engine.cash_usd = 0.0
            engine.active_positions["MID-USD"] = runner.MachinegunPosition(
                product_id="MID-USD",
                playbook="maker_harvest",
                entry_price=100.0,
                quantity=1.0,
                cost_usd=100.0,
                entry_fee=0.25,
                opened_at=opened_at,
                highest_bid=100.0,
                trail_giveback_pct=2.5,
                entry_edge_over_hurdle_pct=0.0,
                max_net_pnl=0.5,
                max_net_pct_on_cost=0.5,
                peak_net_seen_at=opened_at,
                entry_mer=0.0,
                exit_attempted_at=attempted_at,
            )
            original_random = runner.random.random
            runner.random.random = lambda: 0.0
            try:
                engine.maybe_close_positions(
                    {"MID-USD": {"bid": 100.0, "ask": 101.5, "ts": 1}},
                    event_path=event_path,
                )
            finally:
                runner.random.random = original_random

            close_event = json.loads(event_path.read_text(encoding="utf-8").splitlines()[-1])
            self.assertNotIn("MID-USD", engine.active_positions)
            self.assertEqual(close_event["exit_type"], "maker_fill")
            self.assertTrue(close_event["maker_exit_price_improved"])
            self.assertAlmostEqual(close_event["exit_price"], 100.75, places=6)
            self.assertGreater(close_event["net_pct"], 0.10)

    def test_maker_exit_improvement_does_not_cross_profit_floor(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            event_path = root / "events.jsonl"
            engine = make_engine(
                root,
                maker_exit_improve_after_seconds=10.0,
                maker_exit_improve_spread_frac=0.90,
                maker_exit_improve_min_net_pct=0.10,
            )
            opened_at = (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat()
            attempted_at = (datetime.now(timezone.utc) - timedelta(seconds=20)).isoformat()
            engine.cash_usd = 0.0
            engine.active_positions["FLOOR-USD"] = runner.MachinegunPosition(
                product_id="FLOOR-USD",
                playbook="maker_harvest",
                entry_price=100.0,
                quantity=1.0,
                cost_usd=100.0,
                entry_fee=0.25,
                opened_at=opened_at,
                highest_bid=100.0,
                trail_giveback_pct=2.5,
                entry_edge_over_hurdle_pct=0.0,
                max_net_pnl=0.5,
                max_net_pct_on_cost=0.5,
                peak_net_seen_at=opened_at,
                entry_mer=0.0,
                exit_attempted_at=attempted_at,
            )
            original_random = runner.random.random
            runner.random.random = lambda: 0.0
            try:
                engine.maybe_close_positions(
                    {"FLOOR-USD": {"bid": 99.8, "ask": 100.8, "ts": 1}},
                    event_path=event_path,
                )
            finally:
                runner.random.random = original_random

            close_event = json.loads(event_path.read_text(encoding="utf-8").splitlines()[-1])
            self.assertFalse(close_event["maker_exit_price_improved"])
            self.assertAlmostEqual(close_event["exit_price"], 100.8, places=6)
            self.assertLess(close_event["maker_exit_projected_net_pct"], 0.10)

    def test_maker_exit_improvement_uses_calibrated_offset_gate(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            event_path = root / "events.jsonl"
            engine = make_engine(
                root,
                maker_exit_improve_after_seconds=10.0,
                maker_exit_improve_offset_fracs=[0.25, 0.50],
                maker_exit_improve_min_net_pct=0.10,
                maker_exit_improve_min_offset_microfill_rate=0.30,
            )
            engine.enable_microfill_calibration = True
            engine.microfill_min_trials = 6
            engine.microfill_summary = {
                "by_product_side_offset": {
                    "CAL-USD|sell|0.2500": {"hard_cross_fill_proxy": 3, "unfilled_timeout": 7},
                    "CAL-USD|sell|0.5000": {"hard_cross_fill_proxy": 4, "unfilled_timeout": 6},
                }
            }
            opened_at = (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat()
            attempted_at = (datetime.now(timezone.utc) - timedelta(seconds=20)).isoformat()
            engine.cash_usd = 0.0
            engine.active_positions["CAL-USD"] = runner.MachinegunPosition(
                product_id="CAL-USD",
                playbook="maker_harvest",
                entry_price=100.0,
                quantity=1.0,
                cost_usd=100.0,
                entry_fee=0.25,
                opened_at=opened_at,
                highest_bid=100.0,
                trail_giveback_pct=2.5,
                entry_edge_over_hurdle_pct=0.0,
                max_net_pnl=1.0,
                max_net_pct_on_cost=1.0,
                peak_net_seen_at=opened_at,
                entry_mer=0.0,
                exit_attempted_at=attempted_at,
            )
            original_random = runner.random.random
            runner.random.random = lambda: 0.0
            try:
                engine.maybe_close_positions(
                    {"CAL-USD": {"bid": 100.0, "ask": 102.0, "ts": 1}},
                    event_path=event_path,
                )
            finally:
                runner.random.random = original_random

            close_event = json.loads(event_path.read_text(encoding="utf-8").splitlines()[-1])
            self.assertTrue(close_event["maker_exit_price_improved"])
            self.assertEqual(close_event["maker_exit_improve_offset_frac"], 0.5)
            self.assertEqual(close_event["maker_exit_improve_offset_microfill_rate"], 0.4)
            self.assertAlmostEqual(close_event["exit_price"], 101.0, places=6)

    def test_priority_refresh_fill_boost_is_not_applied_by_default(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            event_path = root / "events.jsonl"
            engine = make_engine(root)
            engine.maker_exit_taker_fallback_seconds = 60.0
            opened_at = (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat()
            attempted_at = (datetime.now(timezone.utc) - timedelta(seconds=20)).isoformat()
            engine.cash_usd = 0.0
            engine.active_positions["NOBST-USD"] = runner.MachinegunPosition(
                product_id="NOBST-USD",
                playbook="maker_harvest",
                entry_price=100.0,
                quantity=1.0,
                cost_usd=100.0,
                entry_fee=0.25,
                opened_at=opened_at,
                highest_bid=100.0,
                trail_giveback_pct=2.5,
                entry_edge_over_hurdle_pct=0.0,
                max_net_pnl=1.0,
                max_net_pct_on_cost=1.0,
                peak_net_seen_at=opened_at,
                entry_mer=0.0,
                exit_attempted_at=attempted_at,
            )
            original_random = runner.random.random
            runner.random.random = lambda: 0.90
            try:
                engine.maybe_close_positions(
                    {"NOBST-USD": {"bid": 99.7, "ask": 101.5, "ts": 1}},
                    event_path=event_path,
                )
            finally:
                runner.random.random = original_random

            self.assertIn("NOBST-USD", engine.active_positions)
            miss_event = json.loads(event_path.read_text(encoding="utf-8").splitlines()[-1])
            self.assertEqual(miss_event["action"], "maker_exit_miss")
            self.assertEqual(miss_event["maker_exit_refresh_fill_boost_applied"], 0.0)
            self.assertLess(miss_event["fill_prob"], 0.90)

    def test_min_harvest_age_delays_profit_harvest_but_not_protection(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            event_path = root / "events.jsonl"
            engine = make_engine(root, min_harvest_age_seconds=60.0)
            opened_at = (datetime.now(timezone.utc) - timedelta(seconds=20)).isoformat()
            engine.cash_usd = 0.0
            engine.active_positions["HOLD-USD"] = runner.MachinegunPosition(
                product_id="HOLD-USD",
                playbook="maker_harvest",
                entry_price=100.0,
                quantity=1.0,
                cost_usd=100.0,
                entry_fee=0.25,
                opened_at=opened_at,
                highest_bid=100.9,
                trail_giveback_pct=2.5,
                entry_edge_over_hurdle_pct=0.0,
                max_net_pnl=0.0,
                max_net_pct_on_cost=0.0,
                entry_mer=0.0,
            )

            engine.maybe_close_positions(
                {"HOLD-USD": {"bid": 100.9, "ask": 101.0, "ts": 1}},
                event_path=event_path,
            )

            self.assertIn("HOLD-USD", engine.active_positions)
            self.assertFalse(event_path.exists())

            engine.active_positions["HOLD-USD"].opened_at = (
                datetime.now(timezone.utc) - timedelta(seconds=90)
            ).isoformat()
            original_random = runner.random.random
            runner.random.random = lambda: 0.0
            try:
                engine.maybe_close_positions(
                    {"HOLD-USD": {"bid": 100.9, "ask": 101.0, "ts": 2}},
                    event_path=event_path,
                )
            finally:
                runner.random.random = original_random

            self.assertNotIn("HOLD-USD", engine.active_positions)
            close_event = json.loads(event_path.read_text(encoding="utf-8").splitlines()[-1])
            self.assertIn(close_event["reason"], {"maker_rent_harvest", "maker_min_profit_harvest"})
            self.assertIsNotNone(close_event["bid_taker_net_pct"])
            self.assertLess(close_event["bid_taker_net_pct"], close_event["net_pct"])

    def test_in_position_spread_collapse_exit_is_opt_in(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            event_path = root / "events.jsonl"
            engine = make_engine(root, min_in_position_spread_bps=50.0)
            opened_at = (datetime.now(timezone.utc) - timedelta(seconds=10)).isoformat()
            engine.cash_usd = 0.0
            engine.active_positions["THIN-USD"] = runner.MachinegunPosition(
                product_id="THIN-USD",
                playbook="maker_harvest",
                entry_price=100.0,
                quantity=1.0,
                cost_usd=100.0,
                entry_fee=0.25,
                opened_at=opened_at,
                highest_bid=100.0,
                trail_giveback_pct=2.5,
                entry_edge_over_hurdle_pct=0.0,
                max_net_pnl=-0.25,
                max_net_pct_on_cost=-0.25,
                entry_mer=0.0,
            )

            original_random = runner.random.random
            runner.random.random = lambda: 0.0
            try:
                engine.maybe_close_positions(
                    {"THIN-USD": {"bid": 99.7, "ask": 100.1, "ts": 1}},
                    event_path=event_path,
                )
            finally:
                runner.random.random = original_random

            self.assertNotIn("THIN-USD", engine.active_positions)
            close_event = json.loads(event_path.read_text(encoding="utf-8").splitlines()[-1])
            self.assertEqual(close_event["reason"], "maker_spread_collapse_exit")
            self.assertLess(close_event["spread_bps"], 50.0)

    def test_in_position_spread_collapse_exit_default_disabled(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            event_path = root / "events.jsonl"
            engine = make_engine(root)
            opened_at = (datetime.now(timezone.utc) - timedelta(seconds=10)).isoformat()
            engine.cash_usd = 0.0
            engine.active_positions["THIN-USD"] = runner.MachinegunPosition(
                product_id="THIN-USD",
                playbook="maker_harvest",
                entry_price=100.0,
                quantity=1.0,
                cost_usd=100.0,
                entry_fee=0.25,
                opened_at=opened_at,
                highest_bid=100.0,
                trail_giveback_pct=2.5,
                entry_edge_over_hurdle_pct=0.0,
                max_net_pnl=-0.25,
                max_net_pct_on_cost=-0.25,
                entry_mer=0.0,
            )

            engine.maybe_close_positions(
                {"THIN-USD": {"bid": 99.7, "ask": 100.1, "ts": 1}},
                event_path=event_path,
            )

            self.assertIn("THIN-USD", engine.active_positions)
            self.assertFalse(event_path.exists())

    def test_profitable_close_schedules_and_marks_post_close_ghost(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            event_path = root / "events.jsonl"
            engine = make_engine(root)
            opened_at = (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat()
            pos = runner.MachinegunPosition(
                product_id="GHOST-USD",
                playbook="maker_harvest",
                entry_price=100.0,
                quantity=1.0,
                cost_usd=100.0,
                entry_fee=0.25,
                opened_at=opened_at,
                highest_bid=101.0,
                trail_giveback_pct=2.5,
                entry_edge_over_hurdle_pct=0.0,
                max_net_pnl=0.5,
                max_net_pct_on_cost=0.5,
                min_net_pct_on_cost=-0.25,
                peak_net_seen_at=opened_at,
                entry_mer=4.0,
            )

            engine.schedule_post_close_ghosts(
                pos,
                closed_at="2026-04-25T00:00:00+00:00",
                exit_reason="maker_min_profit_harvest",
                exit_price=101.0,
                exit_fee_bps=25.0,
                actual_net=0.7475,
                actual_net_pct=0.7475,
            )
            self.assertEqual(len(engine.pending_close_ghosts), 4)
            engine.pending_close_ghosts = [engine.pending_close_ghosts[0]]
            engine.pending_close_ghosts[0]["due_epoch"] = 0.0

            engine.mark_due_close_ghosts(
                {"GHOST-USD": {"bid": 102.0, "ask": 102.2, "ts": 1}},
                event_path=event_path,
            )

            self.assertEqual(engine.pending_close_ghosts, [])
            event = json.loads(event_path.read_text(encoding="utf-8").splitlines()[-1])
            self.assertEqual(event["action"], "post_close_ghost_mark")
            self.assertEqual(event["product_id"], "GHOST-USD")
            self.assertEqual(event["horizon_seconds"], 30)
            self.assertEqual(event["close_reason"], "maker_min_profit_harvest")
            self.assertGreater(event["ghost_net"], event["actual_net"])
            self.assertGreater(event["delta_net_vs_actual"], 0.0)

    def test_snapshot_does_not_override_launch_risk_parameters(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            engine = make_engine(Path(tmp))
            engine.load_snapshot(
                {
                    "state": {
                        "max_loss_pct": 99.0,
                        "no_mfe_stop_pct": 9.9,
                        "no_mfe_stop_min_age_seconds": 9999.0,
                    }
                }
            )
            self.assertEqual(engine.max_loss_pct, 3.0)
            self.assertEqual(engine.no_mfe_stop_pct, 0.35)
            self.assertEqual(engine.no_mfe_stop_min_age_seconds, 90.0)

    def test_existing_loss_count_blocks_when_threshold_tightens(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            state_path = Path(tmp) / "loss.json"
            state_path.write_text(
                json.dumps({"consecutive_losses": {"CRV-USD": 2}, "blocked_until": {}}),
                encoding="utf-8",
            )
            tracker = LossTracker(max_consecutive_losses=2, cooldown_seconds=3600, state_path=state_path)
            self.assertTrue(tracker.is_blocked("CRV-USD"))

    def test_manifest_size_boost_cannot_exceed_quote_cap(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            event_path = Path(tmp) / "events.jsonl"
            engine = make_engine(Path(tmp))
            engine.cash_usd = 100.0
            engine.alpha_manifest["BOOST-USD"] = {"suggested_size_mult": 5.0, "heat_score": 100}
            engine.maker_opportunities["BOOST-USD"] = {"mer": 2.0}
            original_random = runner.random.random
            runner.random.random = lambda: 0.0
            try:
                engine.open_position(
                    {"product_id": "BOOST-USD", "playbook": "maker_harvest"},
                    {"bid": 10.0, "ask": 10.1, "ts": 1},
                    event_path=event_path,
                )
            finally:
                runner.random.random = original_random

            self.assertIn("BOOST-USD", engine.active_positions)
            self.assertAlmostEqual(engine.active_positions["BOOST-USD"].cost_usd, 8.0)

    def test_multi_burst_entry_aggregates_filled_quantity_and_cost(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            event_path = Path(tmp) / "events.jsonl"
            engine = make_engine(Path(tmp), systemic_max_positions=3, systemic_selection_limit=3)
            engine.current_cluster_size = 89
            engine.max_quote_usd = 25.0
            engine.systemic_deploy_pct = 0.25
            engine.maker_opportunities["BURST-USD"] = {"mer": 4.0}
            original_random = runner.random.random
            runner.random.random = lambda: 0.0
            try:
                engine.open_position(
                    {"product_id": "BURST-USD", "playbook": "maker_harvest", "spread_bps": 120.0},
                    {"bid": 1.0, "ask": 1.02, "ts": 1},
                    event_path=event_path,
                )
            finally:
                runner.random.random = original_random

            pos = engine.active_positions["BURST-USD"]
            self.assertAlmostEqual(pos.cost_usd, 25.0)
            self.assertAlmostEqual(pos.entry_fee, 0.0625)
            self.assertAlmostEqual(pos.quantity, 24.9375)
            event = json.loads(event_path.read_text(encoding="utf-8").splitlines()[-1])
            self.assertEqual(event["quote_usd"], 25.0)
            self.assertEqual(event["planned_bursts"], 2)
            self.assertEqual(event["filled_bursts"], 2)

    def test_multi_burst_entry_logs_micro_cloud_prep_telemetry_without_changing_fill_price(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            event_path = Path(tmp) / "events.jsonl"
            engine = make_engine(
                Path(tmp),
                systemic_max_positions=3,
                systemic_selection_limit=3,
                min_notional_by_product={"BURST-USD": 10.0},
                enforce_min_notional=True,
            )
            engine.current_cluster_size = 89
            engine.max_quote_usd = 25.0
            engine.systemic_deploy_pct = 0.25
            engine.maker_opportunities["BURST-USD"] = {"mer": 4.0}
            engine.tick_size_by_product["BURST-USD"] = 0.01
            engine.enable_microfill_calibration = True
            engine.microfill_min_trials = 4
            engine.microfill_summary = {
                "by_product_side_offset": {
                    "BURST-USD|buy|0.0000": {
                        "probable_queue_depletion_fill_proxy": 3,
                        "unfilled_timeout": 1,
                    }
                }
            }
            original_random = runner.random.random
            runner.random.random = lambda: 0.0
            try:
                engine.open_position(
                    {"product_id": "BURST-USD", "playbook": "maker_harvest", "spread_bps": 120.0},
                    {"bid": 1.007, "ask": 1.029, "ts": 1},
                    event_path=event_path,
                )
            finally:
                runner.random.random = original_random

            pos = engine.active_positions["BURST-USD"]
            self.assertAlmostEqual(pos.entry_price, 1.007)
            event = json.loads(event_path.read_text(encoding="utf-8").splitlines()[-1])
            bursts = event["entry_burst_telemetry"]
            self.assertEqual(len(bursts), 2)
            self.assertEqual({row["status"] for row in bursts}, {"maker_burst_fill"})
            self.assertEqual(bursts[0]["entry_offset_frac"], 0.0)
            self.assertEqual(bursts[0]["entry_tick_size"], 0.01)
            self.assertEqual(bursts[0]["entry_price_legal"], 1.0)
            self.assertEqual(bursts[0]["burst_min_notional_usd"], 10.0)
            self.assertTrue(bursts[0]["burst_min_notional_valid"])
            self.assertEqual(bursts[0]["entry_offset_microfill_rate"], 0.75)
            self.assertEqual(bursts[0]["entry_offset_microfill_samples"], 4)

    def test_micro_cloud_entry_uses_explicit_tickback_offsets_only_when_enabled(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            event_path = Path(tmp) / "events.jsonl"
            engine = make_engine(
                Path(tmp),
                min_notional_by_product={"BURST-USD": 1.0},
                enforce_min_notional=True,
                enable_micro_cloud=True,
            )
            engine.max_quote_usd = 25.0
            engine.systemic_deploy_pct = 0.25
            engine.maker_opportunities["BURST-USD"] = {"mer": 4.0}
            engine.tick_size_by_product["BURST-USD"] = 0.01
            original_random = runner.random.random
            runner.random.random = lambda: 0.0
            try:
                engine.open_position(
                    {"product_id": "BURST-USD", "playbook": "maker_harvest", "spread_bps": 120.0},
                    {"bid": 1.007, "ask": 1.029, "ts": 1},
                    event_path=event_path,
                )
            finally:
                runner.random.random = original_random

            event = json.loads(event_path.read_text(encoding="utf-8").splitlines()[-1])
            bursts = event["entry_burst_telemetry"]
            self.assertEqual(len(bursts), 5)
            self.assertEqual([row["entry_price_legal"] for row in bursts], [1.0, 0.99, 0.98, 0.99, 1.0])
            self.assertLess(bursts[1]["entry_offset_frac"], 0.0)

    def test_systemic_gate_blocks_high_mer_low_spread_trap(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            engine = make_engine(Path(tmp))
            engine.current_cluster_size = 89
            engine.candidate_streaks = {
                "GRASS-USD": 1,
                "BTR-USD": 1,
            }
            rows = [
                {
                    "product_id": "GRASS-USD",
                    "playbook": "maker_harvest",
                    "mer": 4.1,
                    "spread_bps": 55.0,
                    "pulse_score": 0.0,
                },
                {
                    "product_id": "BTR-USD",
                    "playbook": "maker_harvest",
                    "mer": 4.3,
                    "spread_bps": 115.0,
                    "pulse_score": 0.0,
                },
            ]

            eligible = engine.eligible_rows(rows)

            self.assertEqual([row["product_id"] for row in eligible], ["BTR-USD"])

    def test_systemic_open_vetoes_stale_board_when_live_spread_is_tight(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            event_path = Path(tmp) / "events.jsonl"
            engine = make_engine(Path(tmp))
            engine.current_cluster_size = 89
            engine.cash_usd = 100.0
            engine.maker_opportunities["FUN-USD"] = {"mer": 6.5}

            engine.open_position(
                {
                    "product_id": "FUN-USD",
                    "playbook": "maker_harvest",
                    "spread_bps": 150.0,
                },
                {"bid": 0.04755, "ask": 0.04756, "ts": 1},
                event_path=event_path,
            )

            self.assertNotIn("FUN-USD", engine.active_positions)
            event = json.loads(event_path.read_text(encoding="utf-8").splitlines()[-1])
            self.assertEqual(event["action"], "maker_entry_veto")
            self.assertEqual(event["reason"], "systemic_live_spread_below_gate")
            self.assertLess(event["live_spread_bps"], 10.0)
            self.assertEqual(event["board_spread_bps"], 150.0)

    def test_systemic_selection_limit_allows_parallel_ab_top_n(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            engine = make_engine(
                Path(tmp),
                systemic_max_positions=3,
                systemic_selection_limit=2,
            )
            engine.current_cluster_size = 89
            engine.candidate_streaks = {
                "HOUSE-USD": 1,
                "FOLKS-USD": 1,
                "BTR-USD": 1,
            }
            engine.alpha_manifest = {
                "HOUSE-USD": {"heat_score": 37.0},
                "FOLKS-USD": {"heat_score": 96.0},
                "BTR-USD": {"heat_score": 38.0},
            }
            rows = [
                {"product_id": "HOUSE-USD", "playbook": "maker_harvest", "mer": 3.8, "spread_bps": 600, "pulse_score": 0},
                {"product_id": "FOLKS-USD", "playbook": "maker_harvest", "mer": 9.6, "spread_bps": 140, "pulse_score": 0},
                {"product_id": "BTR-USD", "playbook": "maker_harvest", "mer": 3.7, "spread_bps": 105, "pulse_score": 0},
            ]

            eligible = engine.eligible_rows(rows)

            self.assertEqual([row["product_id"] for row in eligible], ["FOLKS-USD", "BTR-USD"])

    def test_systemic_selection_limit_respects_available_slots(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            engine = make_engine(
                Path(tmp),
                systemic_max_positions=2,
                systemic_selection_limit=3,
            )
            engine.current_cluster_size = 89
            engine.active_positions["HOUSE-USD"] = runner.MachinegunPosition(
                product_id="HOUSE-USD",
                playbook="maker_harvest",
                entry_price=1.0,
                quantity=1.0,
                cost_usd=8.0,
                entry_fee=0.02,
                opened_at=datetime.now(timezone.utc).isoformat(),
                highest_bid=1.0,
                trail_giveback_pct=2.5,
                entry_edge_over_hurdle_pct=0.0,
                max_net_pnl=-0.02,
                max_net_pct_on_cost=-0.25,
            )
            engine.candidate_streaks = {"FOLKS-USD": 1, "BTR-USD": 1}
            engine.alpha_manifest = {
                "FOLKS-USD": {"heat_score": 96.0},
                "BTR-USD": {"heat_score": 38.0},
            }
            rows = [
                {"product_id": "FOLKS-USD", "playbook": "maker_harvest", "mer": 9.6, "spread_bps": 140, "pulse_score": 0},
                {"product_id": "BTR-USD", "playbook": "maker_harvest", "mer": 3.7, "spread_bps": 105, "pulse_score": 0},
            ]

            eligible = engine.eligible_rows(rows)

            self.assertEqual([row["product_id"] for row in eligible], ["FOLKS-USD"])

    def test_systemic_exclude_products_blocks_eligible_row(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            engine = make_engine(
                Path(tmp),
                systemic_max_positions=3,
                systemic_selection_limit=3,
                systemic_exclude_products=["HOUSE-USD", "FOLKS-USD"],
            )
            engine.current_cluster_size = 89
            engine.candidate_streaks = {"HOUSE-USD": 1, "FOLKS-USD": 1, "BTR-USD": 1}
            engine.alpha_manifest = {
                "HOUSE-USD": {"heat_score": 96.0},
                "FOLKS-USD": {"heat_score": 96.0},
                "BTR-USD": {"heat_score": 38.0},
            }
            rows = [
                {"product_id": "HOUSE-USD", "playbook": "maker_harvest", "mer": 9.6, "spread_bps": 140, "pulse_score": 0},
                {"product_id": "FOLKS-USD", "playbook": "maker_harvest", "mer": 9.6, "spread_bps": 140, "pulse_score": 0},
                {"product_id": "BTR-USD", "playbook": "maker_harvest", "mer": 3.7, "spread_bps": 105, "pulse_score": 0},
            ]

            eligible = engine.eligible_rows(rows)

            self.assertEqual([row["product_id"] for row in eligible], ["BTR-USD"])

    def test_allowed_quote_currencies_blocks_non_usd_rows(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            engine = make_engine(
                Path(tmp),
                systemic_max_positions=3,
                systemic_selection_limit=3,
                allowed_quote_currencies=["USD"],
            )
            engine.current_cluster_size = 89
            engine.candidate_streaks = {"BIO-USD": 1, "DAI-USDT": 1, "CC-USDC": 1}
            engine.alpha_manifest = {
                "BIO-USD": {"heat_score": 96.0},
                "DAI-USDT": {"heat_score": 96.0},
                "CC-USDC": {"heat_score": 96.0},
            }
            rows = [
                {"product_id": "DAI-USDT", "playbook": "maker_harvest", "mer": 9.6, "spread_bps": 140, "pulse_score": 0},
                {"product_id": "CC-USDC", "playbook": "maker_harvest", "mer": 9.6, "spread_bps": 140, "pulse_score": 0},
                {"product_id": "BIO-USD", "playbook": "maker_harvest", "mer": 3.7, "spread_bps": 105, "pulse_score": 0},
            ]

            eligible = engine.eligible_rows(rows)

            self.assertEqual([row["product_id"] for row in eligible], ["BIO-USD"])

    def test_systemic_selection_skips_exit_microfill_vetoes_before_top_n_cut(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            engine = make_engine(
                Path(tmp),
                systemic_max_positions=3,
                systemic_selection_limit=3,
            )
            engine.current_cluster_size = 89
            engine.systemic_min_entry_mer = 1.0
            engine.systemic_min_entry_spread_bps = 25.0
            engine.enable_microfill_calibration = True
            engine.microfill_min_trials = 4
            engine.min_exit_microfill_rate = 0.28
            engine.microfill_summary = {
                "by_product_side": {
                    "FOLKS-USD|sell": {
                        "probable_queue_depletion_fill_proxy": 1,
                        "unfilled_timeout": 9,
                    },
                    "ENS-USD|sell": {
                        "unfilled_timeout": 10,
                    },
                    "KSM-USD|sell": {
                        "unfilled_timeout": 10,
                    },
                    "GLMR-USD|sell": {
                        "probable_queue_depletion_fill_proxy": 5,
                        "unfilled_timeout": 5,
                    },
                    "BASED-USD|sell": {
                        "probable_queue_depletion_fill_proxy": 4,
                        "unfilled_timeout": 5,
                    },
                    "ICNT-USD|sell": {
                        "probable_queue_depletion_fill_proxy": 2,
                        "unfilled_timeout": 4,
                    },
                }
            }
            engine.candidate_streaks = {
                "FOLKS-USD": 1,
                "ENS-USD": 1,
                "KSM-USD": 1,
                "GLMR-USD": 1,
                "BASED-USD": 1,
                "ICNT-USD": 1,
            }
            engine.alpha_manifest = {
                "FOLKS-USD": {"heat_score": 100.0},
                "ENS-USD": {"heat_score": 90.0},
                "KSM-USD": {"heat_score": 80.0},
                "GLMR-USD": {"heat_score": 10.0},
                "BASED-USD": {"heat_score": 9.0},
                "ICNT-USD": {"heat_score": 8.0},
            }
            rows = [
                {"product_id": "FOLKS-USD", "playbook": "maker_harvest", "mer": 9.6, "spread_bps": 140, "pulse_score": 0},
                {"product_id": "ENS-USD", "playbook": "maker_harvest", "mer": 6.0, "spread_bps": 32, "pulse_score": 0},
                {"product_id": "KSM-USD", "playbook": "maker_harvest", "mer": 4.8, "spread_bps": 41, "pulse_score": 0},
                {"product_id": "GLMR-USD", "playbook": "maker_harvest", "mer": 2.0, "spread_bps": 89, "pulse_score": 0},
                {"product_id": "BASED-USD", "playbook": "maker_harvest", "mer": 1.9, "spread_bps": 34, "pulse_score": 0},
                {"product_id": "ICNT-USD", "playbook": "maker_harvest", "mer": 1.2, "spread_bps": 30, "pulse_score": 0},
            ]

            eligible = engine.eligible_rows(rows)

            self.assertEqual([row["product_id"] for row in eligible], ["GLMR-USD", "BASED-USD", "ICNT-USD"])

    def test_systemic_selection_skips_entry_microfill_vetoes_before_top_n_cut(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            engine = make_engine(
                Path(tmp),
                systemic_max_positions=3,
                systemic_selection_limit=3,
                min_entry_microfill_rate=0.10,
                min_exit_microfill_rate=0.28,
            )
            engine.current_cluster_size = 89
            engine.systemic_min_entry_mer = 1.0
            engine.systemic_min_entry_spread_bps = 25.0
            engine.enable_microfill_calibration = True
            engine.microfill_min_trials = 4
            engine.microfill_summary = {
                "by_product_side": {
                    "GLMR-USD|buy": {
                        "probable_queue_depletion_fill_proxy": 1,
                        "unfilled_timeout": 11,
                    },
                    "GLMR-USD|sell": {
                        "probable_queue_depletion_fill_proxy": 5,
                        "unfilled_timeout": 5,
                    },
                    "CPOOL-USD|buy": {
                        "probable_queue_depletion_fill_proxy": 3,
                        "unfilled_timeout": 6,
                    },
                    "CPOOL-USD|sell": {
                        "probable_queue_depletion_fill_proxy": 3,
                        "unfilled_timeout": 6,
                    },
                    "BASED-USD|buy": {
                        "probable_queue_depletion_fill_proxy": 2,
                        "unfilled_timeout": 7,
                    },
                    "BASED-USD|sell": {
                        "probable_queue_depletion_fill_proxy": 4,
                        "unfilled_timeout": 5,
                    },
                    "ICNT-USD|buy": {
                        "probable_queue_depletion_fill_proxy": 1,
                        "unfilled_timeout": 5,
                    },
                    "ICNT-USD|sell": {
                        "probable_queue_depletion_fill_proxy": 2,
                        "unfilled_timeout": 4,
                    },
                }
            }
            engine.candidate_streaks = {
                "GLMR-USD": 1,
                "CPOOL-USD": 1,
                "BASED-USD": 1,
                "ICNT-USD": 1,
            }
            engine.alpha_manifest = {
                "GLMR-USD": {"heat_score": 100.0},
                "CPOOL-USD": {"heat_score": 30.0},
                "BASED-USD": {"heat_score": 20.0},
                "ICNT-USD": {"heat_score": 10.0},
            }
            rows = [
                {"product_id": "GLMR-USD", "playbook": "maker_harvest", "mer": 2.0, "spread_bps": 89, "pulse_score": 0},
                {"product_id": "CPOOL-USD", "playbook": "maker_harvest", "mer": 1.9, "spread_bps": 34, "pulse_score": 0},
                {"product_id": "BASED-USD", "playbook": "maker_harvest", "mer": 1.6, "spread_bps": 32, "pulse_score": 0},
                {"product_id": "ICNT-USD", "playbook": "maker_harvest", "mer": 1.2, "spread_bps": 30, "pulse_score": 0},
            ]

            eligible = engine.eligible_rows(rows)

            self.assertEqual([row["product_id"] for row in eligible], ["CPOOL-USD", "BASED-USD", "ICNT-USD"])

    def test_microfill_adjusted_rank_prefers_fillable_round_trip_over_raw_heat(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            engine = make_engine(
                Path(tmp),
                systemic_max_positions=3,
                systemic_selection_limit=2,
                min_entry_microfill_rate=0.10,
                min_exit_microfill_rate=0.25,
                systemic_rank_mode="microfill_adjusted",
            )
            engine.current_cluster_size = 89
            engine.systemic_min_entry_mer = 1.0
            engine.systemic_min_entry_spread_bps = 25.0
            engine.enable_microfill_calibration = True
            engine.microfill_min_trials = 4
            engine.microfill_summary = {
                "by_product_side": {
                    "FOLKS-USD|buy": {
                        "probable_queue_depletion_fill_proxy": 1,
                        "unfilled_timeout": 9,
                    },
                    "FOLKS-USD|sell": {
                        "probable_queue_depletion_fill_proxy": 3,
                        "unfilled_timeout": 7,
                    },
                    "BTR-USD|buy": {
                        "probable_queue_depletion_fill_proxy": 3,
                        "unfilled_timeout": 7,
                    },
                    "BTR-USD|sell": {
                        "probable_queue_depletion_fill_proxy": 3,
                        "unfilled_timeout": 7,
                    },
                    "BASED-USD|buy": {
                        "probable_queue_depletion_fill_proxy": 2,
                        "unfilled_timeout": 8,
                    },
                    "BASED-USD|sell": {
                        "probable_queue_depletion_fill_proxy": 5,
                        "unfilled_timeout": 5,
                    },
                }
            }
            engine.candidate_streaks = {"FOLKS-USD": 1, "BTR-USD": 1, "BASED-USD": 1}
            engine.alpha_manifest = {
                "FOLKS-USD": {"heat_score": 96.0},
                "BTR-USD": {"heat_score": 37.0},
                "BASED-USD": {"heat_score": 20.0},
            }
            rows = [
                {"product_id": "FOLKS-USD", "playbook": "maker_harvest", "mer": 9.6, "spread_bps": 140, "pulse_score": 0},
                {"product_id": "BTR-USD", "playbook": "maker_harvest", "mer": 3.7, "spread_bps": 104, "pulse_score": 0},
                {"product_id": "BASED-USD", "playbook": "maker_harvest", "mer": 2.0, "spread_bps": 34, "pulse_score": 0},
            ]

            eligible = engine.eligible_rows(rows)

            self.assertEqual([row["product_id"] for row in eligible], ["BTR-USD", "FOLKS-USD"])

    def test_preopen_selection_multiplier_returns_ranked_alternates_for_veto_fallthrough(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            engine = make_engine(
                Path(tmp),
                systemic_max_positions=2,
                systemic_selection_limit=2,
                systemic_preopen_selection_multiplier=2,
            )
            engine.current_cluster_size = 89
            engine.systemic_min_entry_mer = 1.0
            engine.systemic_min_entry_spread_bps = 25.0
            engine.candidate_streaks = {
                "BTR-USD": 1,
                "FOLKS-USD": 1,
                "BASED-USD": 1,
                "ICNT-USD": 1,
                "GLMR-USD": 1,
            }
            engine.alpha_manifest = {
                "BTR-USD": {"heat_score": 50.0},
                "FOLKS-USD": {"heat_score": 40.0},
                "BASED-USD": {"heat_score": 30.0},
                "ICNT-USD": {"heat_score": 20.0},
                "GLMR-USD": {"heat_score": 10.0},
            }
            rows = [
                {"product_id": "BTR-USD", "playbook": "maker_harvest", "mer": 3.7, "spread_bps": 104, "pulse_score": 0},
                {"product_id": "FOLKS-USD", "playbook": "maker_harvest", "mer": 9.6, "spread_bps": 140, "pulse_score": 0},
                {"product_id": "BASED-USD", "playbook": "maker_harvest", "mer": 2.0, "spread_bps": 34, "pulse_score": 0},
                {"product_id": "ICNT-USD", "playbook": "maker_harvest", "mer": 1.2, "spread_bps": 30, "pulse_score": 0},
                {"product_id": "GLMR-USD", "playbook": "maker_harvest", "mer": 2.0, "spread_bps": 89, "pulse_score": 0},
            ]

            eligible = engine.eligible_rows(rows)

            self.assertEqual(
                [row["product_id"] for row in eligible],
                ["BTR-USD", "FOLKS-USD", "BASED-USD", "ICNT-USD"],
            )

    def test_open_position_vetoes_collapsed_live_to_board_spread_ratio(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            event_path = root / "events.jsonl"
            engine = make_engine(root, systemic_min_live_to_board_spread_ratio=0.50)
            engine.current_cluster_size = 89
            engine.maker_opportunities = {"FOLKS-USD": {"mer": 9.6}}

            engine.open_position(
                {
                    "product_id": "FOLKS-USD",
                    "playbook": "maker_harvest",
                    "spread_bps": 140.0,
                    "mer": 9.6,
                    "edge_over_hurdle_pct": 0.0,
                },
                {"bid": 1.3952, "ask": 1.4014},
                event_path=event_path,
            )

            events = [
                json.loads(line)
                for line in event_path.read_text(encoding="utf-8").splitlines()
            ]
            self.assertEqual(engine.active_positions, {})
            self.assertEqual(events[0]["reason"], "systemic_live_to_board_spread_ratio_below_gate")
            self.assertLess(events[0]["live_to_board_spread_ratio"], 0.50)

    def test_open_position_vetoes_quote_below_exchange_min_notional(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            event_path = root / "events.jsonl"
            engine = make_engine(
                root,
                min_notional_by_product={"HOUSE-USD": 8.97},
                enforce_min_notional=True,
            )
            engine.current_cluster_size = 89
            engine.maker_opportunities = {"HOUSE-USD": {"mer": 9.6}}

            engine.open_position(
                {
                    "product_id": "HOUSE-USD",
                    "playbook": "maker_harvest",
                    "spread_bps": 600.0,
                    "mer": 9.6,
                },
                {"bid": 0.089, "ask": 0.095},
                event_path=event_path,
            )

            events = [
                json.loads(line)
                for line in event_path.read_text(encoding="utf-8").splitlines()
            ]
            self.assertEqual(engine.active_positions, {})
            self.assertEqual(events[0]["reason"], "quote_below_min_notional")
            self.assertEqual(events[0]["quote_usd"], 8.0)
            self.assertEqual(events[0]["estimated_order_notional_usd"], 7.98)
            self.assertEqual(events[0]["min_notional_usd"], 8.97)
            self.assertEqual(engine.reentry_blocks["HOUSE-USD"], 15)
            self.assertEqual(events[0]["cooldown_polls"], 15)

    def test_min_notional_guard_accounts_for_entry_fee_drag(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            event_path = root / "events.jsonl"
            engine = make_engine(
                root,
                min_notional_by_product={"HOUSE-USD": 9.99},
                enforce_min_notional=True,
            )
            engine.current_cluster_size = 89
            engine.max_quote_usd = 10.0
            engine.maker_opportunities = {"HOUSE-USD": {"mer": 9.6}}

            engine.open_position(
                {
                    "product_id": "HOUSE-USD",
                    "playbook": "maker_harvest",
                    "spread_bps": 600.0,
                    "mer": 9.6,
                },
                {"bid": 0.089, "ask": 0.095},
                event_path=event_path,
            )

            event = json.loads(event_path.read_text(encoding="utf-8").splitlines()[-1])
            self.assertEqual(engine.active_positions, {})
            self.assertEqual(event["reason"], "quote_below_min_notional")
            self.assertEqual(event["quote_usd"], 10.0)
            self.assertEqual(event["estimated_order_notional_usd"], 9.975)
            self.assertEqual(engine.reentry_blocks["HOUSE-USD"], 15)

    def test_open_position_vetoes_unknown_min_notional_when_enforced(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            event_path = root / "events.jsonl"
            engine = make_engine(root, enforce_min_notional=True)
            engine.current_cluster_size = 89
            engine.maker_opportunities = {"NEW-USD": {"mer": 9.6}}

            engine.open_position(
                {
                    "product_id": "NEW-USD",
                    "playbook": "maker_harvest",
                    "spread_bps": 600.0,
                    "mer": 9.6,
                },
                {"bid": 1.0, "ask": 1.2},
                event_path=event_path,
            )

            event = json.loads(event_path.read_text(encoding="utf-8").splitlines()[-1])
            self.assertEqual(engine.active_positions, {})
            self.assertEqual(event["reason"], "min_notional_unknown")
            self.assertEqual(engine.reentry_blocks["NEW-USD"], 60)

    def test_open_position_vetoes_low_exit_microfill_when_required(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            event_path = root / "events.jsonl"
            engine = make_engine(root)
            engine.current_cluster_size = 89
            engine.enable_microfill_calibration = True
            engine.microfill_min_trials = 4
            engine.min_exit_microfill_rate = 0.30
            engine.microfill_summary = {
                "by_product_side": {
                    "FOLKS-USD|sell": {
                        "probable_queue_depletion_fill_proxy": 1,
                        "unfilled_timeout": 3,
                    }
                }
            }
            engine.maker_opportunities = {"FOLKS-USD": {"mer": 9.6}}

            engine.open_position(
                {
                    "product_id": "FOLKS-USD",
                    "playbook": "maker_harvest",
                    "spread_bps": 140.0,
                    "mer": 9.6,
                },
                {"bid": 1.40, "ask": 1.42},
                event_path=event_path,
            )

            event = json.loads(event_path.read_text(encoding="utf-8").splitlines()[-1])
            self.assertEqual(engine.active_positions, {})
            self.assertEqual(event["action"], "maker_entry_veto")
            self.assertEqual(event["reason"], "exit_microfill_rate_below_min")
            self.assertEqual(event["exit_microfill_rate"], 0.25)
            self.assertEqual(event["min_exit_microfill_rate"], 0.30)

    def test_open_position_vetoes_low_entry_microfill_when_required(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            event_path = root / "events.jsonl"
            engine = make_engine(root, min_entry_microfill_rate=0.30)
            engine.current_cluster_size = 89
            engine.enable_microfill_calibration = True
            engine.microfill_min_trials = 4
            engine.microfill_summary = {
                "by_product_side": {
                    "FOLKS-USD|buy": {
                        "probable_queue_depletion_fill_proxy": 1,
                        "unfilled_timeout": 3,
                    }
                }
            }
            engine.maker_opportunities = {"FOLKS-USD": {"mer": 9.6}}

            engine.open_position(
                {
                    "product_id": "FOLKS-USD",
                    "playbook": "maker_harvest",
                    "spread_bps": 140.0,
                    "mer": 9.6,
                },
                {"bid": 1.40, "ask": 1.42},
                event_path=event_path,
            )

            event = json.loads(event_path.read_text(encoding="utf-8").splitlines()[-1])
            self.assertEqual(engine.active_positions, {})
            self.assertEqual(event["action"], "maker_entry_veto")
            self.assertEqual(event["reason"], "entry_microfill_rate_below_min")
            self.assertEqual(event["entry_microfill_rate"], 0.25)
            self.assertEqual(event["min_entry_microfill_rate"], 0.30)

    def test_load_min_notional_by_product_prefers_radar_min_notional(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "radar.json"
            path.write_text(
                json.dumps(
                    {
                        "rows": [
                            {"product_id": "HOUSE-USD", "min_notional_usd": 8.97, "cost_min": 5.0},
                            {"product_id": "FOLKS-USD", "cost_min": 6.64},
                        ]
                    }
                ),
                encoding="utf-8",
            )

            self.assertEqual(
                runner.load_min_notional_by_product(path),
                {"HOUSE-USD": 8.97, "FOLKS-USD": 6.64},
            )

    def test_product_scoped_reentry_cooldown_override_leaves_default_intact(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            event_path = Path(tmp) / "events.jsonl"
            engine = make_engine(Path(tmp), {"HOUSE-USD": 20})

            engine.block_reentry("HOUSE-USD", reason="maker_rent_harvest", event_path=event_path)
            engine.block_reentry("FOLKS-USD", reason="maker_rent_harvest", event_path=event_path)

            events = [
                json.loads(line)
                for line in event_path.read_text(encoding="utf-8").splitlines()
            ]
            self.assertEqual(engine.reentry_blocks["HOUSE-USD"], 20)
            self.assertEqual(engine.reentry_blocks["FOLKS-USD"], 20)
            self.assertEqual(events[0]["cooldown_polls"], 20)
            self.assertEqual(events[0]["configured_cooldown_polls"], 20)
            self.assertEqual(events[0]["base_cooldown_polls"], 60)
            self.assertEqual(events[1]["cooldown_polls"], 20)
            self.assertEqual(events[1]["configured_cooldown_polls"], 20)
            self.assertEqual(events[1]["base_cooldown_polls"], 60)

    def test_parse_reentry_cooldown_overrides_rejects_bad_values(self) -> None:
        self.assertEqual(
            runner.parse_reentry_cooldown_overrides("HOUSE-USD=20,FOLKS-USD=30"),
            {"HOUSE-USD": 20, "FOLKS-USD": 30},
        )
        with self.assertRaises(ValueError):
            runner.parse_reentry_cooldown_overrides("HOUSE-USD")
        with self.assertRaises(ValueError):
            runner.parse_reentry_cooldown_overrides("HOUSE-USD=0")

    def test_save_state_creates_custom_parent_and_uses_custom_loss_tracker(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            state_path = root / "ab" / "state.json"
            loss_path = root / "ab" / "loss.json"
            engine = runner.MakerMachinegunEngine(
                starting_cash_usd=100.0,
                maker_fee_bps=25.0,
                reentry_cooldown_polls=60,
                max_loss_pct=3.0,
                no_mfe_stop_pct=0.35,
                no_mfe_stop_min_age_seconds=90.0,
                target_net_pct_per_hour=5.0,
                entry_confirmation_polls=1,
                min_quote_usd=5.0,
                rotation_buffer_pct=0.5,
                min_profit_to_trail_usd=0.01,
                min_rent_harvest_net_pct=0.10,
                loss_tracker_state_path=loss_path,
            )

            runner.save_state(state_path, engine)

            self.assertTrue(state_path.exists())
            self.assertTrue(loss_path.exists())
            self.assertEqual(engine.tracker.state_path, loss_path)


if __name__ == "__main__":
    unittest.main()
