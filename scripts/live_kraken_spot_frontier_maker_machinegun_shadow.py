#!/usr/bin/env python3
"""Kraken Frontier Maker Machinegun Shadow Runner.

Executes as MAKER (entry at bid, exit at ask by shadow assumption) with a
configurable Kraken maker-fee model. The current proof run uses 25bps.
The runner is designed to test whether spread capture remains viable after
fees, adverse selection, cooldowns, and exit constraints.

Hardenings:
1. Reentry Cooldown: Prevents rapid-fire buy-backs (Death Spiral Fix).
2. Dynamic ATR Trail: Adaptive stops based on 1.5x local noise.
3. Neural-Guided Limit Pricing: Aggressive queue jumping for high-heat signals.
4. Solitary Mycelium Filter: Systemic protection—switch to top-1 signal if cluster > 20.
5. Bear Velocity Veto: Blocks falling knives.
6. Fleet-Wide Loss Tracker: Blocks products after 3 consecutive losses.
7. Rent Harvesting: Automated profit booking once enough spread or minimum
   fee-paid net profit is captured.
"""
import argparse
import json
import os
import random
import sys
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
if str(Path(__file__).resolve().parent) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parent))

from kraken_spot_client import KrakenSpotClient
from live_penetration_lattice_shadow import append_jsonl, log_runner_exception, utc_now_iso
from mfe_capture_tracker import MFETracker
from process_singleton import acquire_singleton
from toxicity_filter import ToxicityFilter
from death_spiral_prevention import LossTracker

DEFAULT_STATE_PATH = ROOT / "reports" / "kraken_spot_maker_machinegun_shadow_state.json"
DEFAULT_EVENT_PATH = ROOT / "reports" / "kraken_spot_maker_machinegun_shadow_events.jsonl"
DEFAULT_OPPORTUNITY_TAPE_PATH = ROOT / "reports" / "kraken_spot_maker_machinegun_opportunity_tape.jsonl"
DEFAULT_LOCK_PATH = ROOT / "reports" / "locks" / "kraken_spot_maker_machinegun.lock"
STRATEGY_BOARD_PATH = ROOT / "reports" / "kraken_spot_frontier_strategy_board.json"
DEFAULT_MIN_NOTIONAL_PATH = ROOT / "reports" / "kraken_spot_live_radar.json"
BEAR_VELOCITY_PATH = ROOT / "reports" / "kraken_spot_bear_velocity_board.json"
MFE_TRACKER_PATH = ROOT / "reports" / "kraken_spot_maker_machinegun_mfe_tracker.json"
LIVE_FOUNDRY_PATH = ROOT / "reports" / "kraken_spot_live_foundry_features.json"
MAKER_OPPORTUNITY_PATH = ROOT / "reports" / "kraken_maker_opportunity_board.json"
MICROFILL_CALIBRATION_SUMMARY_PATH = ROOT / "reports" / "kraken_maker_microfill_calibration_summary.json"
SHADOW_LOG_PATH = ROOT / "reports" / "neural_harpoon_shadow_log.jsonl"
MANIFEST_PATH = ROOT / "reports" / "structural_alpha_manifest.json"
LOSS_TRACKER_STATE_PATH = ROOT / "reports" / "kraken_maker_loss_tracker_state.json"

def to_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default

@dataclass
class MachinegunPosition:
    product_id: str
    playbook: str
    entry_price: float
    quantity: float
    cost_usd: float
    entry_fee: float
    opened_at: str
    highest_bid: float
    trail_giveback_pct: float
    entry_edge_over_hurdle_pct: float
    max_net_pnl: float
    max_net_pct_on_cost: float
    min_net_pct_on_cost: float = 0.0
    peak_net_seen_at: str = ""
    entry_ml_survival_prob: float = 0.0
    entry_fast_green_prob: float = 0.0
    entry_tail_prob: float = 0.0
    entry_bubble_capture_net_pct_per_hour: float = 0.0
    entry_mer: float = 0.0
    entry_bid_depth_usd: float = 0.0
    exit_attempted_at: str | None = None

class MakerMachinegunEngine:
    def __init__(
        self,
        *,
        starting_cash_usd: float,
        deploy_pct: float = 0.15,
        maker_fee_bps: float = 25.0,
        min_quote_usd: float,
        max_loss_pct: float,
        no_mfe_stop_pct: float,
        no_mfe_stop_min_age_seconds: float,
        min_profit_to_trail_usd: float,
        min_rent_harvest_net_pct: float,
        rotation_buffer_pct: float,
        reentry_cooldown_polls: int,
        reentry_cooldown_overrides: dict[str, int] | None = None,
        target_net_pct_per_hour: float,
        entry_confirmation_polls: int,
        require_cluster_size_threshold: int = 20,
        systemic_max_positions: int = 1,
        idiosyncratic_max_positions: int = 15,
        systemic_deploy_pct: float = 0.1,
        idiosyncratic_deploy_pct: float = 0.15,
        systemic_selection_limit: int = 1,
        allowed_quote_currencies: list[str] | None = None,
        systemic_exclude_products: list[str] | None = None,
        max_quote_usd: float = 8.0,
        systemic_min_entry_spread_bps: float = 100.0,
        systemic_min_entry_mer: float = 3.5,
        systemic_min_live_spread_bps: float = 10.0,
        systemic_min_live_to_board_spread_ratio: float = 0.0,
        min_notional_by_product: dict[str, float] | None = None,
        enforce_min_notional: bool = False,
        green_insurance_activation_pct: float = 0.0,
        green_insurance_giveback_pct: float = 0.05,
        post_close_ghost_horizons: list[int] | None = None,
        post_close_ghost_max_age_seconds: float = 900.0,
        min_in_position_spread_bps: float = 0.0,
        min_harvest_age_seconds: float = 0.0,
        loss_tracker_state_path: Path | None = None,
        enable_dds: bool = False,
        dds_depth_pct: float = 0.15,
        enable_post_only_simulation: bool = False,
        post_only_reject_prob: float = 0.10,
        enable_hidden: bool = False,
        enable_microfill_calibration: bool = False,
        microfill_calibration_summary_path: Path | None = None,
        microfill_min_trials: int = 6,
        maker_exit_taker_fallback_seconds: float = 40.0,
        taker_profit_lock_min_net_pct: float = 0.50,
        min_entry_microfill_rate: float = 0.0,
        min_exit_microfill_rate: float = 0.0,
        systemic_rank_mode: str = "heat",
        systemic_preopen_selection_multiplier: int = 1,
        enable_dynamic_hurdle: bool = False,
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
        enable_multipolar_alpha: bool = False,
        swarm_brain_path: Path | None = None,
        dynamic_target_recommendations_path: Path | None = None,
    ) -> None:
        self.starting_cash_usd = float(starting_cash_usd)
        self.enable_multipolar_alpha = bool(enable_multipolar_alpha)
        self.swarm_brain_path = swarm_brain_path
        self.dynamic_target_recommendations_path = dynamic_target_recommendations_path
        self.enable_dds = enable_dds
        self.dds_depth_pct = dds_depth_pct
        self.enable_post_only_simulation = enable_post_only_simulation
        self.post_only_reject_prob = post_only_reject_prob
        self.enable_hidden = enable_hidden
        self.enable_microfill_calibration = bool(enable_microfill_calibration)
        self.microfill_calibration_summary_path = microfill_calibration_summary_path or MICROFILL_CALIBRATION_SUMMARY_PATH
        self.microfill_min_trials = max(1, int(microfill_min_trials))
        self.microfill_summary: dict[str, Any] = {}
        self.maker_exit_taker_fallback_seconds = max(0.0, float(maker_exit_taker_fallback_seconds))
        self.taker_profit_lock_min_net_pct = max(0.0, float(taker_profit_lock_min_net_pct))
        self.min_entry_microfill_rate = max(0.0, min(1.0, float(min_entry_microfill_rate)))
        self.min_exit_microfill_rate = max(0.0, min(1.0, float(min_exit_microfill_rate)))
        self.systemic_rank_mode = str(systemic_rank_mode or "heat").strip().lower()
        if self.systemic_rank_mode not in {"heat", "microfill_adjusted"}:
            self.systemic_rank_mode = "heat"
        self.systemic_preopen_selection_multiplier = max(1, int(systemic_preopen_selection_multiplier))
        self.enable_dynamic_hurdle = bool(enable_dynamic_hurdle)
        self.maker_exit_improve_after_seconds = max(0.0, float(maker_exit_improve_after_seconds))
        self.maker_exit_improve_spread_frac = max(0.0, min(0.99, float(maker_exit_improve_spread_frac)))
        self.maker_exit_improve_min_net_pct = max(0.0, float(maker_exit_improve_min_net_pct))
        self.maker_exit_improve_offset_fracs = [
            max(0.0, min(0.99, float(value)))
            for value in (maker_exit_improve_offset_fracs or [])
        ]
        self.maker_exit_improve_min_offset_microfill_rate = max(
            0.0,
            min(1.0, float(maker_exit_improve_min_offset_microfill_rate)),
        )
        self.maker_exit_refresh_fill_boost = max(0.0, min(0.25, float(maker_exit_refresh_fill_boost)))
        self.maker_first_adverse_bid_stop = bool(maker_first_adverse_bid_stop)
        self.require_bid_taker_green_for_maker_close = bool(require_bid_taker_green_for_maker_close)
        self.bid_taker_green_min_net_pct = max(0.0, float(bid_taker_green_min_net_pct))
        self.enable_micro_cloud = bool(enable_micro_cloud)
        self.deploy_pct = float(deploy_pct)
        self.maker_fee_bps = float(maker_fee_bps)
        self.min_quote_usd = float(min_quote_usd)
        self.max_loss_pct = float(max_loss_pct)
        self.no_mfe_stop_pct = max(0.0, float(no_mfe_stop_pct))
        self.no_mfe_stop_min_age_seconds = max(0.0, float(no_mfe_stop_min_age_seconds))
        self.min_profit_to_trail_usd = float(min_profit_to_trail_usd)
        self.min_rent_harvest_net_pct = max(0.0, float(min_rent_harvest_net_pct))
        self.min_harvest_age_seconds = max(0.0, float(min_harvest_age_seconds))
        self.rotation_buffer_pct = float(rotation_buffer_pct)
        self.reentry_cooldown_polls = max(1, int(reentry_cooldown_polls))
        self.reentry_cooldown_overrides = {
            str(product_id): max(1, int(polls))
            for product_id, polls in (reentry_cooldown_overrides or {}).items()
        }
        self.target_net_pct_per_hour = max(0.0, float(target_net_pct_per_hour))
        self.target_started_at = utc_now_iso()
        self.entry_confirmation_polls = max(1, int(entry_confirmation_polls))
        self.require_cluster_size_threshold = int(require_cluster_size_threshold)
        self.systemic_max_positions = max(1, int(systemic_max_positions))
        self.idiosyncratic_max_positions = max(1, int(idiosyncratic_max_positions))
        self.systemic_deploy_pct = max(0.0, min(1.0, float(systemic_deploy_pct)))
        self.idiosyncratic_deploy_pct = max(0.0, min(1.0, float(idiosyncratic_deploy_pct)))
        self.systemic_selection_limit = max(1, int(systemic_selection_limit))
        self.allowed_quote_currencies = {
            str(quote).strip().upper()
            for quote in (allowed_quote_currencies or [])
            if str(quote).strip()
        }
        self.systemic_exclude_products = set(p.upper() for p in (systemic_exclude_products or []))
        self.max_quote_usd = max(0.0, float(max_quote_usd))
        self.systemic_min_entry_spread_bps = max(0.0, float(systemic_min_entry_spread_bps))
        self.systemic_min_entry_mer = max(0.0, float(systemic_min_entry_mer))
        self.systemic_min_live_spread_bps = max(0.0, float(systemic_min_live_spread_bps))
        self.systemic_min_live_to_board_spread_ratio = max(0.0, float(systemic_min_live_to_board_spread_ratio))
        self.min_notional_by_product = {
            str(product_id).upper(): max(0.0, float(min_notional))
            for product_id, min_notional in (min_notional_by_product or {}).items()
        }
        self.enforce_min_notional = bool(enforce_min_notional)
        self.min_in_position_spread_bps = max(0.0, float(min_in_position_spread_bps))
        self.green_insurance_activation_pct = max(0.0, float(green_insurance_activation_pct))
        self.green_insurance_giveback_pct = max(0.0, float(green_insurance_giveback_pct))
        self.post_close_ghost_horizons = sorted(
            {
                int(horizon)
                for horizon in (post_close_ghost_horizons or [30, 60, 180, 300])
                if int(horizon) > 0
            }
        )
        self.post_close_ghost_max_age_seconds = max(
            max(self.post_close_ghost_horizons, default=0) + 60.0,
            float(post_close_ghost_max_age_seconds),
        )
        self.cash_usd = float(starting_cash_usd)
        self.reentry_cooldown_polls = max(1, int(reentry_cooldown_polls))
        self.reentry_cooldown_overrides = {
            str(product_id): max(1, int(polls))
            for product_id, polls in (reentry_cooldown_overrides or {}).items()
        }
        self.target_net_pct_per_hour = max(0.0, float(target_net_pct_per_hour))
        self.target_started_at = utc_now_iso()
        self.entry_confirmation_polls = max(1, int(entry_confirmation_polls))
        self.require_cluster_size_threshold = int(require_cluster_size_threshold)
        self.systemic_max_positions = max(1, int(systemic_max_positions))
        self.idiosyncratic_max_positions = max(1, int(idiosyncratic_max_positions))
        self.systemic_deploy_pct = max(0.0, min(1.0, float(systemic_deploy_pct)))
        self.idiosyncratic_deploy_pct = max(0.0, min(1.0, float(idiosyncratic_deploy_pct)))
        self.systemic_selection_limit = max(1, int(systemic_selection_limit))
        self.cash_usd = float(starting_cash_usd)
        self.active_positions: dict[str, MachinegunPosition] = {}
        self.reentry_blocks: dict[str, int] = {}
        self.candidate_streaks: dict[str, int] = {}
        self.bear_veto_products: set[str] = set()
        self.mfe_tracker = MFETracker(default_fee_bps=maker_fee_bps * 2.0)
        self.current_cluster_size = 0
        self.pending_close_ghosts: list[dict[str, Any]] = []
        self.realized_net_usd = 0.0
        self.realized_closes = 0
        self.realized_wins_sum = 0.0
        self.realized_wins_count = 0
        self.total_fees = 0.0
        self.product_win_streaks: dict[str, int] = {}

        self.last_action = ""
        self.fee_source = "static_kraken_maker"
        self.fee_tier = "pro"
        self.poll_count = 0
        
        # Mad Scientist Data
        self.foundry_features: dict[str, dict[str, Any]] = {}
        self.maker_opportunities: dict[str, dict[str, Any]] = {}
        self.harpoon_triggers: dict[str, list[dict[str, Any]]] = {}
        self.alpha_manifest: dict[str, dict[str, Any]] = {}
        self.toxicity = ToxicityFilter(SHADOW_LOG_PATH)
        self.pair_map: dict[str, str] = {} # product_id -> rest_pair
        self.tick_size_by_product: dict[str, float] = {}
        
        # Default staggered cooldowns should not override explicit A/B contracts.
        for product_id, polls in {
            "HOUSE-USD": 15,
            "FOLKS-USD": 20,
            "BTR-USD": 25,
        }.items():
            self.reentry_cooldown_overrides.setdefault(product_id, polls)
        
        self.tracker = LossTracker(
            max_consecutive_losses=2,
            cooldown_seconds=3600,
            state_path=loss_tracker_state_path or LOSS_TRACKER_STATE_PATH
        )

    def refresh_microfill_summary(self) -> None:
        if not self.enable_microfill_calibration:
            self.microfill_summary = {}
            return
        self.microfill_summary = load_json(self.microfill_calibration_summary_path)

    def microfill_rate(self, product_id: str, side: str) -> tuple[float | None, int]:
        if not self.enable_microfill_calibration or not self.microfill_summary:
            return None, 0
        fill_like = {"hard_cross_fill_proxy", "probable_queue_depletion_fill_proxy"}
        key = f"{str(product_id).upper()}|{str(side).lower()}"
        counters = self.microfill_summary.get("by_product_side") or {}
        row = counters.get(key)
        product_row = (self.microfill_summary.get("by_product") or {}).get(str(product_id).upper())
        if not isinstance(row, dict):
            row = product_row
        if not isinstance(row, dict):
            return None, 0
        total = sum(int(value) for value in row.values() if isinstance(value, int))
        if total < self.microfill_min_trials and isinstance(product_row, dict):
            product_total = sum(int(value) for value in product_row.values() if isinstance(value, int))
            if product_total >= self.microfill_min_trials:
                row = product_row
                total = product_total
        if total < self.microfill_min_trials:
            return None, total
        fills = sum(int(row.get(result, 0)) for result in fill_like)
        return max(0.0, min(1.0, fills / total)), total

    def microfill_offset_rate(self, product_id: str, side: str, offset_frac: float) -> tuple[float | None, int]:
        if not self.enable_microfill_calibration or not self.microfill_summary:
            return None, 0
        fill_like = {"hard_cross_fill_proxy", "probable_queue_depletion_fill_proxy"}
        key = f"{str(product_id).upper()}|{str(side).lower()}|{float(offset_frac):.4f}"
        counters = self.microfill_summary.get("by_product_side_offset") or {}
        row = counters.get(key)
        if not isinstance(row, dict):
            return None, 0
        total = sum(int(value) for value in row.values() if isinstance(value, int))
        if total < self.microfill_min_trials:
            return None, total
        fills = sum(int(row.get(result, 0)) for result in fill_like)
        return max(0.0, min(1.0, fills / total)), total

    def legal_price(self, product_id: str, price: float, *, side: str) -> float:
        tick_size = self.tick_size_by_product.get(str(product_id).upper(), 0.0)
        if tick_size <= 0.0 or price <= 0.0:
            return price
        steps = price / tick_size
        if side.lower() == "buy":
            return max(tick_size, int(steps) * tick_size)
        return max(tick_size, (int(steps + 0.999999999) * tick_size))

    def entry_burst_telemetry(
        self,
        product_id: str,
        *,
        bid: float,
        ask: float,
        burst_idx: int,
        burst_quote_usd: float,
        min_notional: float,
        offset_frac: float = 0.0,
        raw_price_override: float | None = None,
    ) -> dict[str, Any]:
        spread = max(0.0, ask - bid)
        raw_price = (
            float(raw_price_override)
            if raw_price_override is not None
            else min(ask - (spread * 0.01), bid + (spread * max(0.0, min(0.99, offset_frac)))) if spread > 0.0 else bid
        )
        legal_entry_price = self.legal_price(product_id, raw_price, side="buy")
        estimated_order_notional_usd = burst_quote_usd * (1.0 - self.fee_rate())
        offset_rate, offset_samples = self.microfill_offset_rate(product_id, "buy", offset_frac)
        return {
            "burst_idx": burst_idx,
            "entry_offset_frac": round(float(offset_frac), 6),
            "entry_price_raw": round(raw_price, 12),
            "entry_price_legal": round(legal_entry_price, 12),
            "entry_tick_size": round(self.tick_size_by_product.get(str(product_id).upper(), 0.0), 12),
            "burst_quote_usd": round(burst_quote_usd, 6),
            "burst_estimated_order_notional_usd": round(estimated_order_notional_usd, 6),
            "burst_min_notional_usd": round(min_notional, 6),
            "burst_min_notional_valid": (min_notional <= 0.0 or estimated_order_notional_usd >= min_notional),
            "entry_offset_microfill_rate": round(offset_rate, 6) if offset_rate is not None else None,
            "entry_offset_microfill_samples": offset_samples,
        }

    def micro_cloud_telemetry(
        self,
        product_id: str,
        *,
        bid: float,
        ask: float,
        side: str,
        total_quote_usd: float,
        min_notional: float,
    ) -> list[dict[str, Any]]:
        """Ghost telemetry for the proposed 5x micro-cloud burst execution."""
        tick_size = self.tick_size_by_product.get(str(product_id).upper(), 0.0)
        num_bursts = 5
        burst_quote_usd = total_quote_usd / num_bursts
        
        telemetry = []
        # Proposed distribution: 2x L1, 2x L1-1, 1x L1-2
        if side.lower() == "buy":
            offsets = [0, -1, -2, -1, 0]
        else:
            offsets = [0, 1, 2, 1, 0]
            
        for i, tick_offset in enumerate(offsets):
            base_price = bid if side.lower() == "buy" else ask
            raw_price = base_price + (tick_offset * tick_size)
            legal_price = self.legal_price(product_id, raw_price, side=side)
            
            spread = max(0.0, ask - bid)
            offset_frac = 0.0
            if spread > 0.0:
                if side.lower() == "buy":
                    offset_frac = (legal_price - bid) / spread
                else:
                    offset_frac = (ask - legal_price) / spread
            
            # Clamp offset_frac for microfill lookup if it's outside [0, 0.99]
            lookup_offset = max(0.0, min(0.99, offset_frac))
            offset_rate, offset_samples = self.microfill_offset_rate(product_id, side, lookup_offset)
            
            estimated_order_notional_usd = burst_quote_usd * (1.0 - self.fee_rate())
            
            telemetry.append({
                "burst_idx": i,
                "tick_offset": tick_offset,
                "price": round(legal_price, 12),
                "offset_frac": round(offset_frac, 6),
                "quote_usd": round(burst_quote_usd, 6),
                "estimated_order_notional_usd": round(estimated_order_notional_usd, 6),
                "min_notional_valid": (min_notional <= 0.0 or estimated_order_notional_usd >= min_notional),
                "microfill_rate": round(offset_rate, 6) if offset_rate is not None else None,
                "microfill_samples": offset_samples,
            })
        return telemetry

    def calibrate_fill_prob(self, product_id: str, side: str, fill_prob: float) -> tuple[float, float | None, int]:
        rate, samples = self.microfill_rate(product_id, side)
        if rate is None:
            return fill_prob, None, samples
        # Keep a tiny floor so a sparse but bad calibration does not make the shadow impossible to explore.
        calibrated = min(fill_prob, max(0.05, rate))
        return calibrated, rate, samples

    def microfill_admission(
        self,
        product_id: str,
        side: str,
        min_rate: float,
        reason_prefix: str,
    ) -> tuple[bool, str, float | None, int]:
        if min_rate <= 0.0:
            return True, "", None, 0
        microfill_rate, microfill_samples = self.microfill_rate(product_id, side)
        if microfill_rate is None:
            return False, f"{reason_prefix}_microfill_samples_below_min", None, microfill_samples
        if microfill_rate < min_rate:
            return False, f"{reason_prefix}_microfill_rate_below_min", microfill_rate, microfill_samples
        return True, "", microfill_rate, microfill_samples

    def entry_microfill_admission(self, product_id: str) -> tuple[bool, str, float | None, int]:
        return self.microfill_admission(
            product_id,
            "buy",
            self.min_entry_microfill_rate,
            "entry",
        )

    def exit_microfill_admission(self, product_id: str) -> tuple[bool, str, float | None, int]:
        return self.microfill_admission(
            product_id,
            "sell",
            self.min_exit_microfill_rate,
            "exit",
        )

    def systemic_rank_score(self, row: dict[str, Any]) -> float:
        product_id = str(row.get("product_id") or "")
        heat_score = self.alpha_manifest.get(product_id, {}).get("heat_score", 0.0)
        score = to_float(heat_score, default=0.0)
        if self.systemic_rank_mode == "microfill_adjusted":
            entry_rate, _entry_samples = self.microfill_rate(product_id, "buy")
            exit_rate, _exit_samples = self.microfill_rate(product_id, "sell")
            if entry_rate is None or exit_rate is None:
                score = 0.0
            else:
                score *= max(0.0, entry_rate) * max(0.0, exit_rate)
        return score + self.harpoon_signal(product_id) * 50.0

    def refresh_pair_map(self, client: KrakenSpotClient):
        if self.pair_map: return
        try:
            payload = client.asset_pairs()
            for rest_pair, row in payload.items():
                wsname = row.get("wsname") # e.g. 'BTC/USD'
                if wsname and "/" in wsname:
                    pid = wsname.replace("/", "-")
                    self.pair_map[pid] = rest_pair
                    self.tick_size_by_product[pid.upper()] = 10 ** (-int(to_float(row.get("pair_decimals"), 8)))
        except Exception as e:
            print(f"Error refreshing pair map: {e}")

    def refresh_harpoon_triggers(self, path: Path):
        self.harpoon_triggers = {}
        if not path.exists(): return
        
        now = datetime.now(timezone.utc)
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                try:
                    d = json.loads(line.strip())
                    ts = datetime.fromisoformat(d["ts_utc"].replace("Z", "+00:00"))
                    if (now - ts).total_seconds() < 300: # 5 minute window
                        pid = d["product_id"]
                        if pid not in self.harpoon_triggers: self.harpoon_triggers[pid] = []
                        self.harpoon_triggers[pid].append(d)
                except Exception:
                    continue

    def fee_rate(self) -> float:
        return self.maker_fee_bps / 10000.0

    def snapshot(self) -> dict[str, Any]:
        return {
            "mode": "kraken_spot_maker_machinegun_shadow_v3",
            "starting_cash_usd": self.starting_cash_usd,
            "cash_usd": round(self.cash_usd, 6),
            "realized_net_usd": round(self.realized_net_usd, 6),
            "realized_closes": self.realized_closes,
            "realized_wins_sum": round(self.realized_wins_sum, 6),
            "realized_wins_count": self.realized_wins_count,
            "total_fees": round(self.total_fees, 6),
            "maker_fee_bps": round(self.maker_fee_bps, 4),
            "max_loss_pct": round(self.max_loss_pct, 4),
            "no_mfe_stop_pct": round(self.no_mfe_stop_pct, 4),
            "no_mfe_stop_min_age_seconds": round(self.no_mfe_stop_min_age_seconds, 4),
            "min_rent_harvest_net_pct": round(self.min_rent_harvest_net_pct, 4),
            "min_harvest_age_seconds": round(self.min_harvest_age_seconds, 4),
            "current_cluster_size": self.current_cluster_size,
            "max_quote_usd": round(self.max_quote_usd, 6),
            "systemic_min_entry_spread_bps": round(self.systemic_min_entry_spread_bps, 6),
            "systemic_min_entry_mer": round(self.systemic_min_entry_mer, 6),
            "systemic_min_live_spread_bps": round(self.systemic_min_live_spread_bps, 6),
            "systemic_min_live_to_board_spread_ratio": round(self.systemic_min_live_to_board_spread_ratio, 6),
            "enforce_min_notional": self.enforce_min_notional,
            "known_min_notional_products": sorted(self.min_notional_by_product.keys()),
            "min_in_position_spread_bps": round(self.min_in_position_spread_bps, 4),
            "systemic_selection_limit": self.systemic_selection_limit,
            "allowed_quote_currencies": sorted(self.allowed_quote_currencies),
            "systemic_exclude_products": sorted(self.systemic_exclude_products),
            "green_insurance_activation_pct": round(self.green_insurance_activation_pct, 6),
            "green_insurance_giveback_pct": round(self.green_insurance_giveback_pct, 6),
            "post_close_ghost_horizons": self.post_close_ghost_horizons,
            "post_close_ghost_max_age_seconds": round(self.post_close_ghost_max_age_seconds, 6),
            "maker_exit_taker_fallback_seconds": round(self.maker_exit_taker_fallback_seconds, 6),
            "taker_profit_lock_min_net_pct": round(self.taker_profit_lock_min_net_pct, 6),
            "min_entry_microfill_rate": round(self.min_entry_microfill_rate, 6),
            "min_exit_microfill_rate": round(self.min_exit_microfill_rate, 6),
            "systemic_rank_mode": self.systemic_rank_mode,
            "systemic_preopen_selection_multiplier": self.systemic_preopen_selection_multiplier,
            "enable_dynamic_hurdle": self.enable_dynamic_hurdle,
            "maker_exit_improve_after_seconds": round(self.maker_exit_improve_after_seconds, 6),
            "maker_exit_improve_spread_frac": round(self.maker_exit_improve_spread_frac, 6),
            "maker_exit_improve_min_net_pct": round(self.maker_exit_improve_min_net_pct, 6),
            "maker_exit_improve_offset_fracs": [round(value, 6) for value in self.maker_exit_improve_offset_fracs],
            "maker_exit_improve_min_offset_microfill_rate": round(self.maker_exit_improve_min_offset_microfill_rate, 6),
            "maker_exit_refresh_fill_boost": round(self.maker_exit_refresh_fill_boost, 6),
            "maker_first_adverse_bid_stop": self.maker_first_adverse_bid_stop,
            "require_bid_taker_green_for_maker_close": self.require_bid_taker_green_for_maker_close,
            "bid_taker_green_min_net_pct": round(self.bid_taker_green_min_net_pct, 6),
            "reentry_cooldown_overrides": self.reentry_cooldown_overrides,
            "poll_count": self.poll_count,
            "active_positions": {p: asdict(pos) for p, pos in self.active_positions.items()},
            "pending_close_ghosts": self.pending_close_ghosts,
            "reentry_blocks": self.reentry_blocks,
            "product_win_streaks": self.product_win_streaks,
            "exit_attempted_at": {p: pos.exit_attempted_at for p, pos in self.active_positions.items()},
            "mfe_stats": self.mfe_tracker.get_stats(),
        }

    def load_snapshot(self, payload: dict[str, Any]) -> None:
        state = payload.get("state") or {}
        if state.get("cash_usd") is not None:
            self.cash_usd = float(state.get("cash_usd"))
        if state.get("realized_net_usd") is not None:
            self.realized_net_usd = float(state.get("realized_net_usd"))
        if state.get("realized_closes") is not None:
            self.realized_closes = int(state.get("realized_closes"))
        if state.get("realized_wins_sum") is not None:
            self.realized_wins_sum = float(state.get("realized_wins_sum"))
        if state.get("realized_wins_count") is not None:
            self.realized_wins_count = int(state.get("realized_wins_count"))
        if state.get("total_fees") is not None:
            self.total_fees = float(state.get("total_fees"))
        if state.get("current_cluster_size") is not None:
            self.current_cluster_size = int(state.get("current_cluster_size"))
        if state.get("poll_count") is not None:
            self.poll_count = int(state.get("poll_count"))
        blocks = state.get("reentry_blocks")
        if isinstance(blocks, dict):
            self.reentry_blocks = {str(k): int(v) for k, v in blocks.items()}
        active = state.get("active_positions")
        if isinstance(active, dict):
            for pid, pos in active.items():
                pos.setdefault("min_net_pct_on_cost", to_float(pos.get("max_net_pct_on_cost"), 0.0))
                pos.setdefault("peak_net_seen_at", "")
                self.active_positions[pid] = MachinegunPosition(**pos)
        pending_ghosts = state.get("pending_close_ghosts")
        if isinstance(pending_ghosts, list):
            self.pending_close_ghosts = [ghost for ghost in pending_ghosts if isinstance(ghost, dict)]
        
        streaks = state.get("product_win_streaks")
        if isinstance(streaks, dict):
            self.product_win_streaks = {str(k): int(v) for k, v in streaks.items()}
        
        exit_attempts = state.get("exit_attempted_at")
        if isinstance(exit_attempts, dict):
            for pid, ts in exit_attempts.items():
                if pid in self.active_positions:
                    self.active_positions[pid].exit_attempted_at = ts

    def repair_flat_cash_invariant(self, *, event_path: Path) -> None:
        repaired_cash = self.starting_cash_usd + self.realized_net_usd
        active_cost = sum(max(0.0, pos.cost_usd) for pos in self.active_positions.values())
        if active_cost > repaired_cash + 0.01:
            sorted_positions = sorted(self.active_positions.values(), key=lambda pos: str(pos.opened_at))
            kept: dict[str, MachinegunPosition] = {}
            kept_cost = 0.0
            dropped: list[str] = []
            for pos in sorted_positions:
                if kept_cost + pos.cost_usd <= repaired_cash + 0.01:
                    kept[pos.product_id] = pos
                    kept_cost += pos.cost_usd
                else:
                    dropped.append(pos.product_id)
            old_cash = self.cash_usd
            self.active_positions = kept
            self.cash_usd = max(0.0, repaired_cash - kept_cost)
            append_jsonl(
                event_path,
                {
                    "ts_utc": utc_now_iso(),
                    "action": "state_repair_overdeployed_active_positions",
                    "dropped_products": dropped,
                    "kept_products": sorted(kept.keys()),
                    "old_cash_usd": round(old_cash, 6),
                    "repaired_cash_usd": round(self.cash_usd, 6),
                    "active_cost_usd_before": round(active_cost, 6),
                    "active_cost_usd_after": round(kept_cost, 6),
                    "equity_basis_usd": round(repaired_cash, 6),
                    "reason": "shadow active position costs exceeded starting cash plus realized net",
                },
            )
            return
        if self.active_positions and self.cash_usd + active_cost > repaired_cash + 0.01:
            old_cash = self.cash_usd
            self.cash_usd = max(0.0, repaired_cash - active_cost)
            append_jsonl(
                event_path,
                {
                    "ts_utc": utc_now_iso(),
                    "action": "state_repair_active_cash_over_equity",
                    "old_cash_usd": round(old_cash, 6),
                    "repaired_cash_usd": round(self.cash_usd, 6),
                    "active_cost_usd": round(active_cost, 6),
                    "equity_basis_usd": round(repaired_cash, 6),
                    "reason": "cash plus active cost exceeded starting cash plus realized net",
                },
            )
            return
        if self.active_positions:
            return
        if self.cash_usd <= 0.0 and repaired_cash > 0.0:
            old_cash = self.cash_usd
            self.cash_usd = repaired_cash
            append_jsonl(
                event_path,
                {
                    "ts_utc": utc_now_iso(),
                    "action": "state_repair_flat_cash_no_active_positions",
                    "old_cash_usd": round(old_cash, 6),
                    "repaired_cash_usd": round(self.cash_usd, 6),
                    "realized_net_usd": round(self.realized_net_usd, 6),
                    "reason": "flat shadow state cannot have zero cash; duplicate runner produced orphan open evidence",
                },
            )

    def harpoon_signal(self, pid: str) -> float:
        """Returns a bias score from -1.0 to 1.0 based on recent Harpoon triggers."""
        triggers = self.harpoon_triggers.get(pid, [])
        if not triggers: return 0.0
        
        bias = 0.0
        # Decay factor: older triggers count less
        now = datetime.now(timezone.utc)
        for t in triggers:
            try:
                ts = datetime.fromisoformat(t.get("ts_utc").replace("Z", "+00:00"))
                age_min = (now - ts).total_seconds() / 60.0
                if age_min > 30: continue # Ignore triggers older than 30 mins
                
                decay = max(0.0, 1.0 - (age_min / 30.0))
                prob = float(t.get("warp_probability", 0.5))
                action = t.get("harpoon_action", "SHADOW_LONG")
                weight = (prob - 0.5) * 2.0 * decay
                
                if action == "SHADOW_SHORT":
                    bias -= weight
                else:
                    bias += weight
            except:
                continue
        
        return max(-1.0, min(1.0, bias))

    def execution_mode(self) -> str:
        if self.current_cluster_size < self.require_cluster_size_threshold:
            return "idiosyncratic"
        return "systemic"

    def is_god_tier(self, pid: str) -> bool:
        """Promote products with extreme win-streaks and zero ghosts."""
        # RECURSIVE ALPHA FEEDBACK (Horizon 6.0)
        # Check for 10 consecutive wins with zero ghosts in the current run.
        streak = self.candidate_streaks.get(pid, 0) # Using candidate_streaks as a proxy for win-streak logic
        # Actually, let's use a real win streak count
        if not hasattr(self, "product_win_streaks"):
            self.product_win_streaks = {}
        
        streak = self.product_win_streaks.get(pid, 0)
        if streak >= 10:
            return True
        return False

    def calculate_dynamic_hurdle_bps(self) -> float:
        """Calculate the real-world friction hurdle based on observed fill types."""
        # RECURSIVE FRICTION MODEL (Horizon 10.0)
        # Default to a safe 250bps hurdle if no history.
        if self.realized_closes < 5:
            return 250.0
            
        # Use the global sell-side microfill rate as a proxy for Maker/Taker ratio
        rate, samples = self.microfill_rate("GLOBAL", "sell")
        if rate is None:
            # If no calibration yet, use historical realized logic if possible
            # For simplicity in shadow, we fallback to 250bps
            return 250.0
            
        taker_rate = 1.0 - rate
        # Kraken: 25bps Maker, 40bps Taker. Round-trip simulation.
        # Entry is always Maker (25). Exit is rate*Maker(25) + taker_rate*Taker(40)
        avg_rt_friction = (25.0) + (rate * 25.0) + (taker_rate * 40.0)
        target_margin = 150.0 # We want 1.5% clear profit above friction
        
        return avg_rt_friction + target_margin

    def eligible_rows(self, rows: list[dict[str, Any]], *, client: KrakenSpotClient | None = None) -> list[dict[str, Any]]:
        eligible = []
        mode = self.execution_mode()
        active_products = set(self.active_positions.keys())
        
        # DYNAMIC HURDLE SCALING (Horizon 10.0)
        # Calculates the real-world friction hurdle based on observed fill rates.
        dynamic_hurdle = self.calculate_dynamic_hurdle_bps() if self.enable_dynamic_hurdle else 0.0
        
        for row in rows:
            pid = str(row.get("product_id") or "")
            if str(row.get("playbook") or "") != "maker_harvest":
                continue
            if self.allowed_quote_currencies:
                quote_currency = str(row.get("quote_currency") or "")
                if not quote_currency and "-" in pid:
                    quote_currency = pid.rsplit("-", 1)[-1]
                if quote_currency.upper() not in self.allowed_quote_currencies:
                    continue
            if pid in active_products or pid in self.bear_veto_products:
                continue
            
            # GOD-TIER BYPASS (Horizon 6.0)
            god_tier = self.is_god_tier(pid)
            if god_tier:
                 eligible.append(row)
                 continue

            if self.toxicity.is_toxic(pid):
                print(f"  VETO (TOXICITY): {pid}")
                continue

            if self.tracker.is_blocked(pid):
                print(f"  VETO (DEATH SPIRAL): {pid}")
                continue

            if self.reentry_blocks.get(pid, 0) > 0:
                continue
            if self.candidate_streaks.get(pid, 0) < self.entry_confirmation_polls:
                continue
            # NEW: Pulse Score Veto (protects against idiosyncratic dumping)
            pulse = to_float(row.get("pulse_score"), default=0.0)
            if pulse < 0.0:
                print(f"  VETO (NEGATIVE PULSE): {pid} (score: {pulse:.2f})")
                continue

            entry_ok, entry_reason, entry_rate, entry_samples = self.entry_microfill_admission(pid)
            if not entry_ok:
                if entry_rate is None:
                    print(f"  VETO (ENTRY MICROFILL SAMPLE): {pid} (samples: {entry_samples})")
                else:
                    print(f"  VETO (ENTRY MICROFILL RATE): {pid} (rate: {entry_rate:.3f}, samples: {entry_samples})")
                continue

            exit_ok, exit_reason, exit_rate, exit_samples = self.exit_microfill_admission(pid)
            if not exit_ok:
                if exit_rate is None:
                    print(f"  VETO (EXIT MICROFILL SAMPLE): {pid} (samples: {exit_samples})")
                else:
                    print(f"  VETO (EXIT MICROFILL RATE): {pid} (rate: {exit_rate:.3f}, samples: {exit_samples})")
                continue

            if mode == "systemic":
                # Product exclusion filter (isolated experiment lanes)
                if pid.upper() in self.systemic_exclude_products:
                    continue

                mer = to_float(row.get("mer"), default=0.0)
                spread_bps = to_float(row.get("spread_bps"), default=0.0)
                
                # Apply Dynamic Hurdle
                if self.enable_dynamic_hurdle and spread_bps < dynamic_hurdle:
                    if self.poll_count % 60 == 0:
                        print(f"  VETO (DYNAMIC HURDLE): {pid} {spread_bps:.1f}bps < {dynamic_hurdle:.1f}bps")
                    continue

                if mer < self.systemic_min_entry_mer:
                    print(f"  VETO (SYSTEMIC LOW MER): {pid} (mer: {mer:.2f})")
                    continue
                if spread_bps < self.systemic_min_entry_spread_bps:
                    print(f"  VETO (SYSTEMIC LOW SPREAD): {pid} (spread_bps: {spread_bps:.2f})")
                    continue
                    
                # DDS-AWARE SELECTION (Horizon 10.1): Pre-check depth to avoid unexecutable orders
                if self.enable_dds and client is not None:
                    l2_data = fetch_kraken_l2_data(client, pid, self.pair_map)
                    bid_depth = l2_data["bid_depth_usd"]
                    min_notional = self.min_notional_by_product.get(pid.upper(), 5.0)
                    
                    # Estimate the DDS-scaled quote
                    manifest = self.alpha_manifest.get(pid, {})
                    size_mult = to_float(manifest.get("suggested_size_mult"), default=1.0)
                    base_quote = min(self.cash_usd * self.systemic_deploy_pct * size_mult, self.max_quote_usd)
                    dds_quote = min(base_quote, bid_depth * self.dds_depth_pct)
                    
                    if dds_quote < min_notional:
                        if self.poll_count % 60 == 0:
                            print(f"  VETO (DDS SELECTION): {pid} scaled ${dds_quote:.2f} < min ${min_notional:.2f}")
                        continue

            bias = self.harpoon_signal(pid)
            if bias < -0.4:
                print(f"  VETO (HARPOON SHORT BIAS): {pid} (bias: {bias:.2f})")
                continue

            eligible.append(row)
            
        if mode == "systemic" and eligible:
            # Systemic mode defaults to top-1; opt-in A/B lanes can test top-N.
            eligible.sort(key=self.systemic_rank_score, reverse=True)
            available_slots = max(1, self.systemic_max_positions - len(self.active_positions))
            selection_limit = min(self.systemic_selection_limit, available_slots)
            preopen_limit = min(len(eligible), selection_limit * self.systemic_preopen_selection_multiplier)
            eligible = eligible[:preopen_limit]
        elif mode == "idiosyncratic" and eligible:
            # Idiosyncratic: prioritize high Harpoon bias
            eligible.sort(key=lambda x: self.harpoon_signal(x["product_id"]), reverse=True)
            
        return eligible

    def open_position(self, row: dict[str, Any], tick: dict[str, Any], *, event_path: Path, bid_depth_usd: float = 0.0) -> None:
        pid = str(row["product_id"])
        
        # GOD-TIER BYPASS (Horizon 6.0)
        god_tier = self.is_god_tier(pid)
        if god_tier:
             print(f"  [GOD-TIER PROMOTION] {pid}: Bypassing filters for confirmed alpha streak.")
        
        mode = self.execution_mode()
        max_pos = self.idiosyncratic_max_positions if mode == "idiosyncratic" else self.systemic_max_positions
        deploy_pct = self.idiosyncratic_deploy_pct if mode == "idiosyncratic" else self.systemic_deploy_pct
        
        if len(self.active_positions) >= max_pos:
            return

        # MAKER ENTRY: at Bid
        bid = float(tick["bid"])
        ask = float(tick.get("ask") or 0.0)
        mid = (ask + bid) / 2.0
        live_spread_bps = ((ask - bid) / mid) * 10000.0 if ask > 0.0 and bid > 0.0 and mid > 0.0 else 0.0
        board_spread_bps = to_float(row.get("spread_bps"), default=0.0)
        
        if not god_tier and mode == "systemic" and live_spread_bps < self.systemic_min_live_spread_bps:
            self.last_action = f"entry_veto_live_spread_{pid}"
            append_jsonl(
                event_path,
                {
                    "ts_utc": utc_now_iso(),
                    "action": "maker_entry_veto",
                    "product_id": pid,
                    "reason": "systemic_live_spread_below_gate",
                    "bid": bid,
                    "ask": ask,
                    "live_spread_bps": round(live_spread_bps, 6),
                    "board_spread_bps": round(board_spread_bps, 6),
                    "min_live_spread_bps": round(self.systemic_min_live_spread_bps, 6),
                    "mode": mode,
                },
            )
            return
        
        live_to_board_spread_ratio = live_spread_bps / board_spread_bps if board_spread_bps > 0.0 else 0.0
        if not god_tier and mode == "systemic" and live_to_board_spread_ratio < self.systemic_min_live_to_board_spread_ratio:
            self.last_action = f"entry_veto_live_board_spread_ratio_{pid}"
            append_jsonl(
                event_path,
                {
                    "ts_utc": utc_now_iso(),
                    "action": "maker_entry_veto",
                    "product_id": pid,
                    "reason": "systemic_live_to_board_spread_ratio_below_gate",
                    "bid": bid,
                    "ask": ask,
                    "live_spread_bps": round(live_spread_bps, 6),
                    "board_spread_bps": round(board_spread_bps, 6),
                    "live_to_board_spread_ratio": round(live_to_board_spread_ratio, 6),
                    "min_live_to_board_spread_ratio": round(self.systemic_min_live_to_board_spread_ratio, 6),
                    "mode": mode,
                },
            )
            return
        
        # QUEUE PRIORITY MODELING
        mer = to_float(self.maker_opportunities.get(pid, {}).get("mer"))
        
        # GENERATIVE STRATEGY MUTATION: MANIFEST LOOKUP
        manifest = self.alpha_manifest.get(pid, {})
        heat_score = to_float(manifest.get("heat_score"), default=0.0)
        size_mult = to_float(manifest.get("suggested_size_mult"), default=1.0)
        suggested_trail = to_float(manifest.get("suggested_trail_pct"), default=0.0)
        
        # MER-PROPORTIONAL SIZING + MANIFEST BOOST
        adjusted_deploy_pct = deploy_pct * size_mult
        quote_usd = min(self.cash_usd * adjusted_deploy_pct, self.cash_usd)
        if self.max_quote_usd > 0.0:
            quote_usd = min(quote_usd, self.max_quote_usd)

        # DYNAMIC DEPTH SIZING (DDS): Cap at dds_depth_pct of top-of-book depth
        if self.enable_dds and bid_depth_usd > 0:
             dds_ceiling = bid_depth_usd * self.dds_depth_pct
             if quote_usd > dds_ceiling:
                  quote_usd = max(self.min_quote_usd, dds_ceiling)

        if self.min_entry_microfill_rate > 0.0:
            entry_ok, entry_reason, entry_microfill_rate, entry_microfill_samples = self.entry_microfill_admission(pid)
            if not entry_ok:
                self.last_action = f"entry_veto_entry_microfill_{pid}"
                append_jsonl(
                    event_path,
                    {
                        "ts_utc": utc_now_iso(),
                        "action": "maker_entry_veto",
                        "product_id": pid,
                        "reason": entry_reason,
                        "entry_microfill_rate": round(entry_microfill_rate, 6) if entry_microfill_rate is not None else None,
                        "entry_microfill_samples": entry_microfill_samples,
                        "min_entry_microfill_rate": round(self.min_entry_microfill_rate, 6),
                        "quote_usd": round(quote_usd, 6),
                        "mode": mode,
                    },
                )
                return

        min_notional = self.min_notional_by_product.get(pid.upper(), 0.0)
        estimated_order_notional_usd = quote_usd * (1.0 - self.fee_rate())
        if self.enforce_min_notional and min_notional <= 0.0:
            self.last_action = f"entry_veto_min_notional_unknown_{pid}"
            self.reentry_blocks[pid] = max(self.reentry_blocks.get(pid, 0), self.reentry_cooldown_for(pid))
            append_jsonl(
                event_path,
                {
                    "ts_utc": utc_now_iso(),
                    "action": "maker_entry_veto",
                    "product_id": pid,
                    "reason": "min_notional_unknown",
                    "quote_usd": round(quote_usd, 6),
                    "estimated_order_notional_usd": round(estimated_order_notional_usd, 6),
                    "min_notional_usd": 0.0,
                    "max_quote_usd": round(self.max_quote_usd, 6),
                    "cooldown_polls": self.reentry_blocks[pid],
                    "mode": mode,
                },
            )
            return
        if self.enforce_min_notional and estimated_order_notional_usd < min_notional:
            self.last_action = f"entry_veto_below_min_notional_{pid}"
            self.reentry_blocks[pid] = max(self.reentry_blocks.get(pid, 0), self.reentry_cooldown_for(pid))
            append_jsonl(
                event_path,
                {
                    "ts_utc": utc_now_iso(),
                    "action": "maker_entry_veto",
                    "product_id": pid,
                    "reason": "quote_below_min_notional",
                    "quote_usd": round(quote_usd, 6),
                    "estimated_order_notional_usd": round(estimated_order_notional_usd, 6),
                    "min_notional_usd": round(min_notional, 6),
                    "max_quote_usd": round(self.max_quote_usd, 6),
                    "cooldown_polls": self.reentry_blocks[pid],
                    "mode": mode,
                },
            )
            return

        if self.min_exit_microfill_rate > 0.0:
            exit_ok, exit_reason, exit_microfill_rate, exit_microfill_samples = self.exit_microfill_admission(pid)
            if not exit_ok:
                self.last_action = f"entry_veto_exit_microfill_{pid}"
                append_jsonl(
                    event_path,
                    {
                        "ts_utc": utc_now_iso(),
                        "action": "maker_entry_veto",
                        "product_id": pid,
                        "reason": exit_reason,
                        "exit_microfill_rate": round(exit_microfill_rate, 6) if exit_microfill_rate is not None else None,
                        "exit_microfill_samples": exit_microfill_samples,
                        "min_exit_microfill_rate": round(self.min_exit_microfill_rate, 6),
                        "quote_usd": round(quote_usd, 6),
                        "mode": mode,
                    },
                )
                return
        
        if quote_usd < self.min_quote_usd:
            return

        # ICEBERG MACHINEGUN EXECUTION (Obfuscation Layer)
        # Slices large Titan orders into smaller randomized Hidden bursts.
        # MIDPOINT COMPROMISE: 6x bursts for Titan Stage 3/4.
        num_bursts = 1
        if self.enable_micro_cloud:
            num_bursts = 5
        elif quote_usd >= 100.0:
            num_bursts = 6
        elif quote_usd >= 50.0:
            num_bursts = 4
        elif quote_usd >= 25.0:
            num_bursts = 2

        burst_quote_usd = quote_usd / num_bursts
        filled_quote_usd = 0.0
        filled_entry_fee = 0.0
        filled_quantity = 0.0
        filled_bursts = 0
        last_fill_roll = 0.0
        last_fill_prob = 0.0
        burst_telemetry: list[dict[str, Any]] = []
        
        micro_cloud_offsets = [0, -1, -2, -1, 0] if self.enable_micro_cloud else [0] * num_bursts

        for i in range(num_bursts):
            tick_offset = micro_cloud_offsets[i]
            tick_size = self.tick_size_by_product.get(pid.upper(), 0.0)
            burst_bid = bid + (tick_offset * tick_size)
            burst_legal_price = self.legal_price(pid, burst_bid, side="buy")
            burst_fill_price = burst_legal_price if self.enable_micro_cloud else bid
            
            # Recalculate offset_frac for microfill calibration
            spread = max(0.0, ask - bid)
            offset_frac = (burst_legal_price - bid) / spread if self.enable_micro_cloud and spread > 0.0 else 0.0

            burst_meta = self.entry_burst_telemetry(
                pid,
                bid=bid,
                ask=ask,
                burst_idx=i,
                burst_quote_usd=burst_quote_usd,
                min_notional=min_notional,
                offset_frac=offset_frac,
                raw_price_override=burst_bid if self.enable_micro_cloud else None,
            )
            # POST-ONLY & HIDDEN SIMULATION
            if self.enable_post_only_simulation:
                reject_roll = random.random()
                if reject_roll < self.post_only_reject_prob:
                    burst_telemetry.append({**burst_meta, "status": "post_only_reject"})
                    append_jsonl(event_path, {
                        "ts_utc": utc_now_iso(),
                        "action": "post_only_reject_shadow",
                        "product_id": pid,
                        "burst_idx": i,
                        "reason": "order_would_cross_spread",
                        "bid": bid,
                        "ask": ask,
                        **burst_meta,
                    })
                    continue
            
            # ADVERSARIAL FILL MODEL (Titan 10.2 Reality Patch)
            # 1. Volume Weighted Probability (No trades, no fills)
            vol_24h_base = to_float(row.get("volume_24h_base"), 0.0)
            vol_1m_usd = (vol_24h_base / 1440.0) * bid
            volume_factor = min(1.0, vol_1m_usd / (quote_usd + 1e-9))
            
            # 2. Offset Decay (The 'Invisibility Gap')
            # Fills at 0.10 offset are 90% less likely than at midpoint (0.50).
            # This simulates queue position and HFT jumping.
            offset_penalty = max(0.05, offset_frac) 
            
            # 3. Signal Heat
            heat_boost = min(heat_score / 100.0, 0.45)
            
            # Combined Adversarial Probability
            fill_prob = (volume_factor * offset_penalty) + heat_boost
            fill_prob = min(0.95, fill_prob)
            raw_fill_prob = fill_prob
            
            # USE OFFSET-SPECIFIC CALIBRATION
            fill_prob, microfill_rate, microfill_samples = self.calibrate_fill_prob(pid, "buy", fill_prob)
            if offset_frac < 0:
                 # If we are deeper in the book, use the specific offset rate if available
                 spec_rate, spec_samples = self.microfill_offset_rate(pid, "buy", offset_frac)
                 if spec_rate is not None:
                      fill_prob = min(fill_prob, spec_rate)
                      microfill_rate = spec_rate
                      microfill_samples = spec_samples

            fill_roll = random.random()
            if fill_roll > fill_prob:
                burst_telemetry.append(
                    {
                        **burst_meta,
                        "status": "maker_burst_miss",
                        "fill_prob": round(fill_prob, 6),
                        "raw_fill_prob": round(raw_fill_prob, 6),
                        "microfill_rate": round(microfill_rate, 6) if microfill_rate is not None else None,
                        "microfill_samples": microfill_samples,
                    }
                )
                append_jsonl(event_path, {
                    "ts_utc": utc_now_iso(),
                    "action": "maker_burst_miss",
                        "product_id": pid,
                        "burst_idx": i,
                        "fill_prob": round(fill_prob, 6),
                        "raw_fill_prob": round(raw_fill_prob, 6),
                        "microfill_rate": round(microfill_rate, 6) if microfill_rate is not None else None,
                        "microfill_samples": microfill_samples,
                        **burst_meta,
                    })
                continue

            # Successful Fill (Simulation)
            # In a real Steel run, we would call self.client.add_order with hidden=True
            entry_fee = burst_quote_usd * self.fee_rate()
            quantity = (burst_quote_usd - entry_fee) / burst_fill_price
            filled_quote_usd += burst_quote_usd
            filled_entry_fee += entry_fee
            filled_quantity += quantity
            filled_bursts += 1
            last_fill_roll = fill_roll
            last_fill_prob = fill_prob
            burst_telemetry.append(
                {
                    **burst_meta,
                    "status": "maker_burst_fill",
                    "fill_prob": round(fill_prob, 6),
                    "raw_fill_prob": round(raw_fill_prob, 6),
                    "fill_roll": round(fill_roll, 6),
                    "microfill_rate": round(microfill_rate, 6) if microfill_rate is not None else None,
                    "microfill_samples": microfill_samples,
                }
            )

        if filled_bursts <= 0 or filled_quantity <= 0.0 or filled_quote_usd <= 0.0:
            self.last_action = f"entry_skipped_no_maker_burst_fill_{pid}"
            return

        self.cash_usd -= filled_quote_usd
        self.total_fees += filled_entry_fee
        
        # MAD SCIENTIST ADAPTIVE TRAILING (ATR vs MANIFEST)
        if suggested_trail > 0:
            trail_giveback = suggested_trail
        else:
            feat = self.foundry_features.get(pid, {})
            atr_pct = to_float(feat.get("atr_12_pct"))
            if atr_pct > 0:
                trail_giveback = max(1.5, atr_pct * 1.5) # Minimum 1.5% trail
            else:
                # Fallback to price-aware
                trail_giveback = 3.0 if bid < 0.01 else 1.5
        
        pos = MachinegunPosition(
            product_id=pid,
            playbook=str(row.get("playbook") or "maker_frontier"),
            entry_price=bid,
            quantity=filled_quantity,
            cost_usd=filled_quote_usd,
            entry_fee=filled_entry_fee,
            opened_at=utc_now_iso(),
            highest_bid=bid,
            trail_giveback_pct=trail_giveback,
            entry_edge_over_hurdle_pct=float(row.get("edge_over_hurdle_pct") or 0.0),
            max_net_pnl=-filled_entry_fee,
            max_net_pct_on_cost=(-filled_entry_fee / filled_quote_usd) * 100.0 if filled_quote_usd else 0.0,
            min_net_pct_on_cost=(-filled_entry_fee / filled_quote_usd) * 100.0 if filled_quote_usd else 0.0,
            peak_net_seen_at=utc_now_iso(),
            entry_ml_survival_prob=to_float(row.get("ml_survival_prob")),
            entry_fast_green_prob=to_float(row.get("fast_green_prob")),
            entry_tail_prob=to_float(row.get("tail_prob")),
            entry_mer=mer,
        )
        self.active_positions[pid] = pos
        self.mfe_tracker.on_entry(
            trade_id=f"{pid}-{pos.opened_at}",
            product_id=pid,
            entry_price=bid,
            predicted_mfe_pct=pos.entry_tail_prob or 0.01,
            fee_bps=self.maker_fee_bps*2
        )
        
        append_jsonl(
            event_path,
            {
                "ts_utc": utc_now_iso(),
                "action": "open_maker_shadow",
                "product_id": pid,
                "entry_type": "maker_fill",
                "entry_price": bid,
                "ask_at_entry": ask,
                "trail_pct": trail_giveback,
                "mer": mer,
                "live_spread_bps": round(live_spread_bps, 6),
                "board_spread_bps": round(board_spread_bps, 6),
                "heat_score": round(heat_score, 4),
                "fill_prob": round(last_fill_prob, 6),
                "microfill_calibrated": self.enable_microfill_calibration,
                "min_entry_microfill_rate": round(self.min_entry_microfill_rate, 6),
                "min_exit_microfill_rate": round(self.min_exit_microfill_rate, 6),
                "fill_roll": round(last_fill_roll, 6),
                "planned_quote_usd": round(quote_usd, 6),
                "planned_bursts": num_bursts,
                "filled_bursts": filled_bursts,
                "entry_burst_telemetry": burst_telemetry,
                "proposed_micro_cloud_telemetry": self.micro_cloud_telemetry(
                    pid,
                    bid=bid,
                    ask=ask,
                    side="buy",
                    total_quote_usd=quote_usd,
                    min_notional=min_notional,
                ),
                "mode": mode,
                "playbook": pos.playbook,
                "quote_usd": round(filled_quote_usd, 6),
                "entry_fee": round(filled_entry_fee, 6),
                "maker_fee_bps": round(self.maker_fee_bps, 4),
                "pulse_score": round(to_float(row.get("pulse_score")), 4),
                "harpoon_bias": round(self.harpoon_signal(pid), 4),
            },
        )
        print(f"[{utc_now_iso()}] ENTERED ({mode} MAKER): {pid} at {bid:.8f} | Quote: ${filled_quote_usd:.2f} | Heat: {heat_score:.1f}")

    def mark_position(self, pos: MachinegunPosition, tick: dict[str, Any]) -> dict[str, Any]:
        # MAKER EXIT: at Ask
        ask = float(tick["ask"])
        bid = float(tick["bid"])
        pos.highest_bid = max(pos.highest_bid, bid)
        
        proceeds = pos.quantity * ask # Exit at Ask!
        exit_fee = proceeds * self.fee_rate()
        net = proceeds - exit_fee - pos.cost_usd
        net_pct = (net / pos.cost_usd) * 100.0
        spread_bps = ((ask - bid) / ((ask + bid) / 2.0)) * 10000.0 if ask > 0 and bid > 0 else 0.0
        
        return {
            "product_id": pos.product_id,
            "bid": bid,
            "ask": ask,
            "spread_bps": spread_bps,
            "highest_bid": pos.highest_bid,
            "net_pnl": net,
            "net_pct_on_cost": net_pct,
            "trail_stop": pos.highest_bid * (1.0 - (pos.trail_giveback_pct / 100.0))
        }

    def position_age_seconds(self, pos: MachinegunPosition) -> float:
        try:
            opened_at = datetime.fromisoformat(pos.opened_at)
            if opened_at.tzinfo is None:
                opened_at = opened_at.replace(tzinfo=timezone.utc)
            return max(0.0, (datetime.now(timezone.utc) - opened_at).total_seconds())
        except Exception:
            return 0.0

    def taker_exit_accounting(self, pos: MachinegunPosition, bid: float) -> dict[str, float]:
        gross_proceeds = pos.quantity * bid
        exit_fee_bps = 40.0
        exit_fee = gross_proceeds * (exit_fee_bps / 10000.0)
        net_proceeds = gross_proceeds - exit_fee
        net = net_proceeds - pos.cost_usd
        net_pct = (net / pos.cost_usd) * 100.0 if pos.cost_usd else 0.0
        return {
            "exit_price": bid,
            "exit_fee_bps": exit_fee_bps,
            "gross_proceeds": gross_proceeds,
            "exit_fee": exit_fee,
            "net_proceeds": net_proceeds,
            "net": net,
            "net_pct": net_pct,
        }

    def maker_exit_accounting(self, pos: MachinegunPosition, price: float) -> dict[str, float]:
        gross_proceeds = pos.quantity * price
        exit_fee_bps = self.maker_fee_bps
        exit_fee = gross_proceeds * (exit_fee_bps / 10000.0)
        net_proceeds = gross_proceeds - exit_fee
        net = net_proceeds - pos.cost_usd
        net_pct = (net / pos.cost_usd) * 100.0 if pos.cost_usd else 0.0
        return {
            "exit_price": price,
            "exit_fee_bps": exit_fee_bps,
            "gross_proceeds": gross_proceeds,
            "exit_fee": exit_fee,
            "net_proceeds": net_proceeds,
            "net": net,
            "net_pct": net_pct,
        }

    def schedule_post_close_ghosts(
        self,
        pos: MachinegunPosition,
        *,
        closed_at: str,
        exit_reason: str,
        exit_price: float,
        exit_fee_bps: float,
        actual_net: float,
        actual_net_pct: float,
    ) -> None:
        if not self.post_close_ghost_horizons:
            return
        closed_epoch = time.time()
        for horizon in self.post_close_ghost_horizons:
            self.pending_close_ghosts.append(
                {
                    "product_id": pos.product_id,
                    "opened_at": pos.opened_at,
                    "closed_at": closed_at,
                    "closed_epoch": closed_epoch,
                    "due_epoch": closed_epoch + float(horizon),
                    "horizon_seconds": int(horizon),
                    "entry_price": pos.entry_price,
                    "quantity": pos.quantity,
                    "cost_usd": pos.cost_usd,
                    "entry_fee": pos.entry_fee,
                    "actual_net": actual_net,
                    "actual_net_pct": actual_net_pct,
                    "close_exit_price": exit_price,
                    "close_exit_fee_bps": exit_fee_bps,
                    "close_reason": exit_reason,
                    "peak_net_pct_at_close": pos.max_net_pct_on_cost,
                    "peak_net_seen_at": pos.peak_net_seen_at,
                }
            )

    def mark_due_close_ghosts(self, ticks: dict[str, dict[str, Any]], *, event_path: Path) -> None:
        if not self.pending_close_ghosts:
            return
        now_epoch = time.time()
        kept: list[dict[str, Any]] = []
        for ghost in self.pending_close_ghosts:
            product_id = str(ghost.get("product_id") or "")
            due_epoch = to_float(ghost.get("due_epoch"))
            closed_epoch = to_float(ghost.get("closed_epoch"))
            if now_epoch < due_epoch:
                kept.append(ghost)
                continue
            tick = ticks.get(product_id)
            if not tick:
                if now_epoch - closed_epoch <= self.post_close_ghost_max_age_seconds:
                    kept.append(ghost)
                else:
                    append_jsonl(
                        event_path,
                        {
                            "ts_utc": utc_now_iso(),
                            "action": "post_close_ghost_miss",
                            "product_id": product_id,
                            "horizon_seconds": int(to_float(ghost.get("horizon_seconds"))),
                            "reason": "tick_unavailable_before_expiry",
                            "closed_at": ghost.get("closed_at"),
                        },
                    )
                continue

            bid = to_float(tick.get("bid"))
            ask = to_float(tick.get("ask"))
            quantity = to_float(ghost.get("quantity"))
            cost_usd = to_float(ghost.get("cost_usd"))
            taker_fee_bps = 40.0
            gross_proceeds = quantity * bid
            exit_fee = gross_proceeds * (taker_fee_bps / 10000.0)
            ghost_net = gross_proceeds - exit_fee - cost_usd
            ghost_net_pct = (ghost_net / cost_usd) * 100.0 if cost_usd else 0.0
            actual_net = to_float(ghost.get("actual_net"))
            actual_net_pct = to_float(ghost.get("actual_net_pct"))
            spread_bps = ((ask - bid) / ((ask + bid) / 2.0)) * 10000.0 if ask > 0 and bid > 0 else 0.0
            append_jsonl(
                event_path,
                {
                    "ts_utc": utc_now_iso(),
                    "action": "post_close_ghost_mark",
                    "product_id": product_id,
                    "horizon_seconds": int(to_float(ghost.get("horizon_seconds"))),
                    "closed_at": ghost.get("closed_at"),
                    "opened_at": ghost.get("opened_at"),
                    "close_reason": ghost.get("close_reason"),
                    "bid": round(bid, 12),
                    "ask": round(ask, 12),
                    "spread_bps": round(spread_bps, 4),
                    "entry_price": round(to_float(ghost.get("entry_price")), 12),
                    "close_exit_price": round(to_float(ghost.get("close_exit_price")), 12),
                    "ghost_exit_price": round(bid, 12),
                    "ghost_exit_fee_bps": taker_fee_bps,
                    "ghost_net": round(ghost_net, 6),
                    "ghost_net_pct": round(ghost_net_pct, 4),
                    "actual_net": round(actual_net, 6),
                    "actual_net_pct": round(actual_net_pct, 4),
                    "delta_net_vs_actual": round(ghost_net - actual_net, 6),
                    "delta_net_pct_vs_actual": round(ghost_net_pct - actual_net_pct, 4),
                    "peak_net_pct_at_close": round(to_float(ghost.get("peak_net_pct_at_close")), 4),
                    "peak_net_seen_at": ghost.get("peak_net_seen_at"),
                },
            )
        self.pending_close_ghosts = kept

    def reentry_cooldown_for(self, product_id: str) -> int:
        return self.reentry_cooldown_overrides.get(product_id, self.reentry_cooldown_polls)

    def block_reentry(self, product_id: str, *, reason: str, event_path: Path) -> None:
        cooldown_polls = self.reentry_cooldown_for(product_id)
        self.reentry_blocks[product_id] = max(self.reentry_blocks.get(product_id, 0), cooldown_polls)
        append_jsonl(
            event_path,
            {
                "ts_utc": utc_now_iso(),
                "action": "block_maker_reentry",
                "product_id": product_id,
                "reason": reason,
                "cooldown_polls": self.reentry_blocks[product_id],
                "configured_cooldown_polls": cooldown_polls,
                "base_cooldown_polls": self.reentry_cooldown_polls,
            },
        )

    def tick_reentry_blocks(self) -> None:
        self.reentry_blocks = {
            product: polls - 1 for product, polls in self.reentry_blocks.items() if polls > 1
        }
        self.tracker.tick()

    def maybe_close_positions(self, ticks: dict[str, dict[str, Any]], *, event_path: Path) -> None:
        for pid, pos in list(self.active_positions.items()):
            tick = ticks.get(pid)
            if not tick: continue
            mark = self.mark_position(pos, tick)
            if mark["net_pnl"] > pos.max_net_pnl:
                pos.max_net_pnl = mark["net_pnl"]
            if mark["net_pct_on_cost"] > pos.max_net_pct_on_cost:
                pos.max_net_pct_on_cost = mark["net_pct_on_cost"]
                pos.peak_net_seen_at = utc_now_iso()
            pos.min_net_pct_on_cost = min(pos.min_net_pct_on_cost, mark["net_pct_on_cost"])
            age_seconds = self.position_age_seconds(pos)
            no_fee_paid_mfe = pos.max_net_pct_on_cost < self.min_rent_harvest_net_pct
            green_insurance_stop_pct = pos.max_net_pct_on_cost - self.green_insurance_giveback_pct
            green_insurance_active = (
                self.green_insurance_giveback_pct > 0.0
                and pos.max_net_pct_on_cost >= self.green_insurance_activation_pct
            )
            
            # EXIT CHECKS
            exit_reason = None
            
            # Fee-Aware Insurance Gate (Prevents friction-bleed on flat tapes)
            # We only allow technical stops (Insurance) if we've seen enough profit to cover the Taker exit churn.
            # Entry: MakerFee, Exit: 40bps Taker.
            fee_hurdle_pct = (self.maker_fee_bps + 40.0) / 100.0
            insurance_unlocked = pos.max_net_pct_on_cost > fee_hurdle_pct

            # Dynamic Emergency Stop: Cap loss at 3x the average win
            avg_win = (self.realized_wins_sum / self.realized_wins_count) if self.realized_wins_count > 0 else 0.0
            avg_win_pct = (avg_win / pos.cost_usd * 100.0) if pos.cost_usd > 0 else 0.02 # assume 2bps if no history
            
            dynamic_stop_pct = min(self.max_loss_pct, 3.0 * avg_win_pct)
            # Ensure stop is at least enough to cover fees + small buffer
            dynamic_stop_pct = max(dynamic_stop_pct, (self.maker_fee_bps * 2 / 100.0) + 0.05)

            if insurance_unlocked and mark["bid"] < pos.entry_price * (1.0 - (dynamic_stop_pct / 100.0)):
                if self.maker_first_adverse_bid_stop and mark["net_pct_on_cost"] > 0.0:
                    exit_reason = "maker_adverse_bid_escape"
                else:
                    exit_reason = "emergency_stop"
            elif mark["bid"] < mark["trail_stop"] and mark["net_pnl"] > 0:
                exit_reason = "maker_profit_trail"
            elif (
                insurance_unlocked
                and green_insurance_active
                and mark["net_pct_on_cost"] > 0.0
                and mark["net_pct_on_cost"] < green_insurance_stop_pct
            ):
                exit_reason = "maker_green_then_red_insurance"
            elif self.min_in_position_spread_bps > 0.0 and mark["spread_bps"] < self.min_in_position_spread_bps:
                exit_reason = "maker_spread_collapse_exit"
            elif (
                insurance_unlocked
                and self.no_mfe_stop_pct > 0.0
                and no_fee_paid_mfe
                and age_seconds >= self.no_mfe_stop_min_age_seconds
                and mark["net_pct_on_cost"] <= -abs(self.no_mfe_stop_pct)
            ):
                exit_reason = "maker_no_mfe_adverse_stop"
            elif mark["net_pct_on_cost"] <= -abs(self.max_loss_pct):
                exit_reason = "maker_emergency_stop"
            elif (
                age_seconds >= self.min_harvest_age_seconds
                and mark["net_pct_on_cost"] >= (mark["spread_bps"] / 100.0 * 0.5)
            ):
                # RENT HARVEST: We captured 50% of the spread. Book it.
                exit_reason = "maker_rent_harvest"
            elif (
                age_seconds >= self.min_harvest_age_seconds
                and mark["net_pct_on_cost"] >= self.min_rent_harvest_net_pct
            ):
                exit_reason = "maker_min_profit_harvest"
            
            # GHOSTING: Active bid cancellation if toxicity is detected
            if not exit_reason:
                bias = self.harpoon_signal(pid)
                if bias < -0.4:
                     exit_reason = "maker_ghost_veto"
                elif self.toxicity.is_toxic(pid):
                     exit_reason = "maker_harpoon_toxicity_ghost"
                
            if exit_reason:
                is_taker_exit = exit_reason in {
                    "emergency_stop",
                    "maker_emergency_stop",
                    "maker_no_mfe_adverse_stop",
                    "maker_ghost_veto",
                    "maker_harpoon_toxicity_ghost",
                    "trail_stop",
                }
                taker_lock_wait_seconds: float | None = None
                taker_lock_net_pct: float | None = None
                taker_lock_min_net_pct: float | None = None
                maker_exit_wait_seconds: float | None = None
                maker_exit_price = mark["ask"]
                maker_exit_price_improved = False
                maker_exit_price_improvement_bps = 0.0
                maker_exit_projected_net_pct: float | None = None
                maker_exit_improve_offset_frac: float | None = None
                maker_exit_improve_offset_microfill_rate: float | None = None
                maker_exit_improve_offset_microfill_samples = 0
                maker_exit_refresh_boost_applied = 0.0
                
                # Start the clock when a maker profit exit first becomes available.
                if not is_taker_exit and pos.exit_attempted_at is None:
                    pos.exit_attempted_at = utc_now_iso()

                if not is_taker_exit:
                    try:
                        attempted_raw = str(pos.exit_attempted_at or "")
                        attempted_at = datetime.fromisoformat(attempted_raw.replace("Z", "+00:00"))
                        if attempted_at.tzinfo is None:
                            attempted_at = attempted_at.replace(tzinfo=timezone.utc)
                        maker_exit_wait_seconds = max(
                            0.0, (datetime.now(timezone.utc) - attempted_at).total_seconds()
                        )
                    except Exception:
                        maker_exit_wait_seconds = 0.0

                    spread = max(0.0, mark["ask"] - mark["bid"])
                    offset_fracs = self.maker_exit_improve_offset_fracs
                    if not offset_fracs and self.maker_exit_improve_spread_frac > 0.0:
                        offset_fracs = [self.maker_exit_improve_spread_frac]
                    if spread > 0.0 and maker_exit_wait_seconds >= self.maker_exit_improve_after_seconds:
                        inside_floor = mark["bid"] + (spread * 0.01)
                        for offset_frac in sorted(offset_fracs):
                            improved_price = max(
                                inside_floor,
                                mark["ask"] - (spread * offset_frac),
                            )
                            improved_price = min(mark["ask"], improved_price)
                            improved_accounting = self.maker_exit_accounting(pos, improved_price)
                            projected_net_pct = improved_accounting["net_pct"]
                            offset_rate: float | None = None
                            offset_samples = 0
                            if self.maker_exit_improve_min_offset_microfill_rate > 0.0:
                                offset_rate, offset_samples = self.microfill_offset_rate(pid, "sell", offset_frac)
                                if offset_rate is None or offset_rate < self.maker_exit_improve_min_offset_microfill_rate:
                                    maker_exit_projected_net_pct = projected_net_pct
                                    maker_exit_improve_offset_frac = offset_frac
                                    maker_exit_improve_offset_microfill_rate = offset_rate
                                    maker_exit_improve_offset_microfill_samples = offset_samples
                                    continue
                            maker_exit_projected_net_pct = projected_net_pct
                            maker_exit_improve_offset_frac = offset_frac
                            maker_exit_improve_offset_microfill_rate = offset_rate
                            maker_exit_improve_offset_microfill_samples = offset_samples
                            if maker_exit_projected_net_pct >= self.maker_exit_improve_min_net_pct:
                                maker_exit_price = improved_price
                                maker_exit_price_improved = maker_exit_price < mark["ask"]
                                if maker_exit_price_improved:
                                    maker_exit_price_improvement_bps = (
                                        (mark["ask"] - maker_exit_price) / mark["ask"] * 10000.0
                                        if mark["ask"] > 0
                                        else 0.0
                                    )

                # Fill probability for Maker exit (more aggressive than entry)
                current_mer = to_float(self.maker_opportunities.get(pid, {}).get("mer"))
                entry_mer = to_float(pos.entry_mer)
                fill_mer = current_mer if current_mer > 0 else entry_mer

                if is_taker_exit:
                    fill_prob = 1.0
                    raw_fill_prob = fill_prob
                    microfill_rate = None
                    microfill_samples = 0
                    fill_roll = 0.0
                else:
                    # ADVERSARIAL EXIT MODEL (Titan 10.2 Reality Patch)
                    # 1. Volume Factor (Exiting at ASK requires buyer flow)
                    opp = self.maker_opportunities.get(pid, {})
                    vol_24h_base = to_float(opp.get("vol_24h_base"), 0.0)
                    vol_1m_usd = (vol_24h_base / 1440.0) * mark["ask"]
                    volume_factor = min(1.0, vol_1m_usd / (pos.cost_usd + 1e-9))
                    
                    # 2. Base Probability (Exit has slightly higher priority than entry)
                    fill_prob = (0.6 * volume_factor) + 0.1 
                    fill_prob = min(0.95, fill_prob)
                    raw_fill_prob = fill_prob
                    
                    fill_prob, microfill_rate, microfill_samples = self.calibrate_fill_prob(pid, "sell", fill_prob)
                    fill_roll = random.random()
                
                # TITAN 6.3: GENERATIVE STEALTH EXIT
                if self.enable_micro_cloud and not is_taker_exit:
                    # Attempt a staggered exit by checking L1-1t (inside spread) first.
                    tick_size = self.tick_size_by_product.get(pid.upper(), 0.0)
                    improved_price = self.legal_price(pid, mark["ask"] - tick_size, side="sell")
                    spread = max(0.0, mark["ask"] - mark["bid"])
                    # Ensure we don't cross the spread (stay maker)
                    if improved_price > mark["bid"] and improved_price < mark["ask"]:
                        # Inside-spread exit has significantly higher fill probability (+30%)
                        improved_fill_prob = min(0.99, fill_prob + 0.30)
                        improved_fill_roll = random.random()
                        if improved_fill_roll < improved_fill_prob:
                            maker_exit_price = improved_price
                            maker_exit_price_improved = True
                            fill_prob = improved_fill_prob
                            fill_roll = improved_fill_roll
                            maker_exit_price_improvement_bps = (
                                (mark["ask"] - maker_exit_price) / mark["ask"] * 10000.0
                                if mark["ask"] > 0
                                else 0.0
                            )

                # If a calibrated maker exit misses, only book a fallback when
                # the bid-side taker close itself is still green after fees.
                if not is_taker_exit and fill_roll > fill_prob:
                    try:
                        wait_seconds = maker_exit_wait_seconds or 0.0

                        if wait_seconds > 10.0 and self.maker_exit_refresh_fill_boost > 0.0:
                            maker_exit_refresh_boost_applied = self.maker_exit_refresh_fill_boost
                            fill_prob = min(0.99, fill_prob + self.maker_exit_refresh_fill_boost)
                            print(
                                f"  [PRIORITY HUNT] Refreshing Maker Exit for {pid} "
                                f"(+{self.maker_exit_refresh_fill_boost:.2f} fill boost)."
                            )
                        
                        taker_accounting = self.taker_exit_accounting(pos, mark["bid"])
                        taker_lock_wait_seconds = wait_seconds
                        taker_lock_net_pct = taker_accounting["net_pct"]
                        taker_lock_min_net_pct = self.taker_profit_lock_min_net_pct
                        if (
                            wait_seconds >= self.maker_exit_taker_fallback_seconds
                            and taker_accounting["net_pct"] >= self.taker_profit_lock_min_net_pct
                        ):
                            is_taker_exit = True
                            exit_reason = "maker_exit_miss_taker_profit_lock"
                            fill_prob = 1.0
                            fill_roll = 0.0
                            print(
                                f"  [PROFIT LOCK] Force taker exit on {pid}: "
                                f"{taker_accounting['net_pct']:.2f}% bid-side net after {wait_seconds:.1f}s maker-miss."
                            )
                    except Exception:
                        pass

                if not is_taker_exit and fill_roll > fill_prob:
                    append_jsonl(
                        event_path,
                        {
                            "ts_utc": utc_now_iso(),
                            "action": "maker_exit_miss",
                            "product_id": pid,
                            "reason": exit_reason,
                            "mer": round(fill_mer, 6),
                            "fill_prob": round(fill_prob, 6),
                            "raw_fill_prob": round(raw_fill_prob, 6),
                            "microfill_rate": round(microfill_rate, 6) if microfill_rate is not None else None,
                            "microfill_samples": microfill_samples,
                            "fill_roll": round(fill_roll, 6),
                            "net_pct": round(mark["net_pct_on_cost"], 4),
                            "bid_taker_net_pct": round(taker_lock_net_pct, 4) if taker_lock_net_pct is not None else None,
                            "taker_profit_lock_min_net_pct": round(taker_lock_min_net_pct, 4) if taker_lock_min_net_pct is not None else None,
                            "maker_exit_taker_fallback_seconds": round(self.maker_exit_taker_fallback_seconds, 3),
                            "maker_exit_wait_seconds": round(maker_exit_wait_seconds, 3) if maker_exit_wait_seconds is not None else None,
                            "maker_exit_price": round(maker_exit_price, 12),
                            "maker_exit_price_improved": maker_exit_price_improved,
                            "maker_exit_price_improvement_bps": round(maker_exit_price_improvement_bps, 6),
                            "maker_exit_projected_net_pct": round(maker_exit_projected_net_pct, 4) if maker_exit_projected_net_pct is not None else None,
                            "maker_exit_improve_min_net_pct": round(self.maker_exit_improve_min_net_pct, 4),
                            "maker_exit_improve_offset_frac": round(maker_exit_improve_offset_frac, 6) if maker_exit_improve_offset_frac is not None else None,
                            "maker_exit_improve_offset_microfill_rate": round(maker_exit_improve_offset_microfill_rate, 6) if maker_exit_improve_offset_microfill_rate is not None else None,
                            "maker_exit_improve_offset_microfill_samples": maker_exit_improve_offset_microfill_samples,
                            "maker_exit_improve_min_offset_microfill_rate": round(self.maker_exit_improve_min_offset_microfill_rate, 6),
                            "maker_exit_refresh_fill_boost_applied": round(maker_exit_refresh_boost_applied, 6),
                            "max_net_pct_on_cost": round(pos.max_net_pct_on_cost, 4),
                            "age_seconds": round(age_seconds, 3),
                            "proposed_micro_cloud_telemetry": self.micro_cloud_telemetry(
                                pid,
                                bid=mark["bid"],
                                ask=mark["ask"],
                                side="sell",
                                total_quote_usd=pos.cost_usd,
                                min_notional=self.min_notional_by_product.get(pid.upper(), 0.0),
                            ),
                        },
                    )
                    continue
                exit_price = mark["bid"] if is_taker_exit else maker_exit_price
                exit_fee_bps = 40.0 if is_taker_exit else self.maker_fee_bps
                
                gross_proceeds = pos.quantity * exit_price
                exit_fee = gross_proceeds * (exit_fee_bps / 10000.0)
                net_proceeds = gross_proceeds - exit_fee
                actual_net = net_proceeds - pos.cost_usd
                actual_net_pct = (actual_net / pos.cost_usd) * 100.0 if pos.cost_usd else 0.0
                close_bid_taker_net_pct = taker_lock_net_pct
                try:
                    close_bid_taker_net_pct = self.taker_exit_accounting(pos, mark["bid"])["net_pct"]
                except Exception:
                    pass
                if (
                    self.require_bid_taker_green_for_maker_close
                    and not is_taker_exit
                    and (
                        close_bid_taker_net_pct is None
                        or close_bid_taker_net_pct < self.bid_taker_green_min_net_pct
                    )
                ):
                    append_jsonl(
                        event_path,
                        {
                            "ts_utc": utc_now_iso(),
                            "action": "maker_exit_live_equivalence_hold",
                            "product_id": pid,
                            "reason": exit_reason,
                            "net_pct": round(mark["net_pct_on_cost"], 4),
                            "bid_taker_net_pct": round(close_bid_taker_net_pct, 4)
                            if close_bid_taker_net_pct is not None
                            else None,
                            "bid_taker_green_min_net_pct": round(self.bid_taker_green_min_net_pct, 4),
                            "maker_exit_price": round(maker_exit_price, 12),
                            "maker_exit_price_improved": maker_exit_price_improved,
                            "maker_exit_price_improvement_bps": round(maker_exit_price_improvement_bps, 6),
                            "maker_exit_wait_seconds": round(maker_exit_wait_seconds, 3)
                            if maker_exit_wait_seconds is not None
                            else None,
                            "fill_prob": round(fill_prob, 6),
                            "fill_roll": round(fill_roll, 6),
                            "max_net_pct_on_cost": round(pos.max_net_pct_on_cost, 4),
                            "age_seconds": round(age_seconds, 3),
                        },
                    )
                    continue
                closed_at = utc_now_iso()
                self.cash_usd += net_proceeds
                self.realized_net_usd += actual_net
                self.realized_closes += 1
                
                # RECURSIVE ALPHA FEEDBACK (Horizon 6.0): Update Win Streaks
                if actual_net > 0:
                    self.realized_wins_sum += actual_net
                    self.realized_wins_count += 1
                    self.product_win_streaks[pid] = self.product_win_streaks.get(pid, 0) + 1
                    self.schedule_post_close_ghosts(
                        pos,
                        closed_at=closed_at,
                        exit_reason=exit_reason,
                        exit_price=exit_price,
                        exit_fee_bps=exit_fee_bps,
                        actual_net=actual_net,
                        actual_net_pct=actual_net_pct,
                    )
                else:
                    self.product_win_streaks[pid] = 0 # Reset streak on loss
                
                self.active_positions.pop(pid)
                self.mfe_tracker.on_exit(f"{pid}-{pos.opened_at}", exit_price)
                self.block_reentry(pid, reason=exit_reason, event_path=event_path)
                
                # Update Fleet-Wide Loss Tracker
                res = self.tracker.record_close(pid, won=(actual_net > 0))
                if res["action"] == "blocked":
                    print(f"  🚨 FLEET-WIDE DEATH SPIRAL BLOCK: {pid} blocked for {res['cooldown_seconds']}s after {res['consecutive_losses']} losses.")
                self.tracker.save()

                
                exit_type = "maker_fill"
                if exit_reason == "maker_spread_collapse_exit":
                    exit_type = "spread_collapse_exit"
                elif is_taker_exit:
                    exit_type = "taker_insurance"

                append_jsonl(event_path, {
                    "ts_utc": closed_at, 
                    "action": "close_maker_shadow", 
                    "product_id": pid, 
                    "exit_type": exit_type,
                    "reason": exit_reason, 
                    "net": round(actual_net, 6),
                    "net_pct": round(actual_net_pct, 4),
                    "maker_mark_net": round(mark["net_pnl"], 6),
                    "maker_mark_net_pct": round(mark["net_pct_on_cost"], 4),
                    "mer": round(fill_mer, 6),
                    "entry_price": round(pos.entry_price, 12),
                    "exit_price": round(exit_price, 12),
                    "cost_usd": round(pos.cost_usd, 6),
                    "gross_proceeds": round(gross_proceeds, 6),
                    "net_proceeds": round(net_proceeds, 6),
                    "entry_fee": round(pos.entry_fee, 6),
                    "exit_fee": round(exit_fee, 6),
                    "exit_fee_bps": round(exit_fee_bps, 4),
                    "maker_fee_bps": round(self.maker_fee_bps, 4),
                    "spread_bps": round(mark["spread_bps"], 4),
                    "entry_mer": round(pos.entry_mer, 6),
                    "max_net_pnl": round(pos.max_net_pnl, 6),
                    "max_net_pct_on_cost": round(pos.max_net_pct_on_cost, 4),
                    "min_net_pct_on_cost": round(pos.min_net_pct_on_cost, 4),
                    "peak_net_seen_at": pos.peak_net_seen_at,
                    "green_insurance_activation_pct": round(self.green_insurance_activation_pct, 4),
                    "green_insurance_giveback_pct": round(self.green_insurance_giveback_pct, 4),
                    "green_insurance_stop_pct": round(green_insurance_stop_pct, 4),
                    "post_close_ghost_horizons": self.post_close_ghost_horizons if actual_net > 0 else [],
                    "age_seconds": round(age_seconds, 3),
                    "fill_prob": round(fill_prob, 6),
                    "microfill_calibrated": self.enable_microfill_calibration,
                    "fill_roll": round(fill_roll, 6),
                    "bid_taker_net_pct": round(close_bid_taker_net_pct, 4) if close_bid_taker_net_pct is not None else None,
                    "maker_exit_dependent": (close_bid_taker_net_pct < 0) if close_bid_taker_net_pct is not None else None,
                    "taker_profit_lock_min_net_pct": round(taker_lock_min_net_pct, 4) if taker_lock_min_net_pct is not None else None,
                    "proposed_micro_cloud_telemetry": self.micro_cloud_telemetry(
                        pid,
                        bid=mark["bid"],
                        ask=mark["ask"],
                        side="sell",
                        total_quote_usd=pos.cost_usd,
                        min_notional=self.min_notional_by_product.get(pid.upper(), 0.0),
                    ),
                    "maker_exit_taker_fallback_seconds": round(self.maker_exit_taker_fallback_seconds, 3),
                    "maker_exit_wait_seconds": round(maker_exit_wait_seconds, 3) if maker_exit_wait_seconds is not None else None,
                    "maker_exit_price_improved": maker_exit_price_improved,
                    "maker_exit_price_improvement_bps": round(maker_exit_price_improvement_bps, 6),
                    "maker_exit_projected_net_pct": round(maker_exit_projected_net_pct, 4) if maker_exit_projected_net_pct is not None else None,
                    "maker_exit_improve_min_net_pct": round(self.maker_exit_improve_min_net_pct, 4),
                    "maker_exit_improve_offset_frac": round(maker_exit_improve_offset_frac, 6) if maker_exit_improve_offset_frac is not None else None,
                    "maker_exit_improve_offset_microfill_rate": round(maker_exit_improve_offset_microfill_rate, 6) if maker_exit_improve_offset_microfill_rate is not None else None,
                    "maker_exit_improve_offset_microfill_samples": maker_exit_improve_offset_microfill_samples,
                    "maker_exit_improve_min_offset_microfill_rate": round(self.maker_exit_improve_min_offset_microfill_rate, 6),
                    "maker_exit_refresh_fill_boost_applied": round(maker_exit_refresh_boost_applied, 6),
                    "opened_at": pos.opened_at,
                })
                exit_liquidity = "TAKER" if is_taker_exit else "MAKER"
                print(
                    f"[{utc_now_iso()}] EXITED ({exit_liquidity}): {pid} at {exit_price:.8f} "
                    f"| Net: {actual_net_pct:.2f}% | Reason: {exit_reason}"
                )

    def load_swarm_brain(self) -> dict[str, Any]:
        if not self.swarm_brain_path or not self.swarm_brain_path.exists():
            return {}
        try:
            with open(self.swarm_brain_path, "r") as f:
                return json.load(f)
        except:
            return {}

    def load_dynamic_recommendations(self) -> dict[str, Any]:
        if not self.dynamic_target_recommendations_path or not self.dynamic_target_recommendations_path.exists():
            return {}
        try:
            with open(self.dynamic_target_recommendations_path, "r") as f:
                return json.load(f)
        except:
            return {}

    def is_global_veto_active(self) -> bool:
        if not self.enable_multipolar_alpha:
            return False
        brain = self.load_swarm_brain()
        return brain.get("global_veto_active", False)

    def get_adaptive_exit_target(self, product_id: str) -> float | None:
        if not self.enable_multipolar_alpha:
            return None
        recs = self.load_dynamic_recommendations()
        # recs format expected: { "ETH-BTC": { "target_net_pct": 0.19 }, ... }
        product_rec = recs.get(product_id)
        if isinstance(product_rec, dict):
            return to_float(product_rec.get("target_net_pct"), None)
        return None

def fetch_kraken_ticks(client: KrakenSpotClient, product_ids: list[str], pair_map: dict[str, str]) -> dict[str, dict[str, Any]]:
    if not product_ids: return {}
    
    # Map pids to rest_pairs
    request_pairs = []
    inv_map = {}
    for pid in product_ids:
        rp = pair_map.get(pid)
        if rp:
            request_pairs.append(rp)
            inv_map[rp] = pid
        else:
            # Fallback for newly listed assets not in map
            norm = pid.replace("-", "")
            request_pairs.append(norm)
            inv_map[norm] = pid
            
    chunk_size = 45
    all_payload = {}
    
    for i in range(0, len(request_pairs), chunk_size):
        chunk = request_pairs[i:i + chunk_size]
        try:
            p = client.ticker(chunk)
            all_payload.update(p)
        except Exception as e:
            print(f"Error fetching ticker chunk: {e}")
            continue
            
    ticks = {}
    now = int(time.time())
    for k_key, data in all_payload.items():
        pid = inv_map.get(k_key)
        if not pid:
            # Fuzzy match for legacy pairs (XXBTZUSD vs XBTUSD)
            for rp, p_id in inv_map.items():
                if rp in k_key or k_key in rp:
                    pid = p_id
                    break
        
        if pid and 'b' in data and 'a' in data:
            ticks[pid] = {"bid": float(data['b'][0]), "ask": float(data['a'][0]), "ts": now}
            
    return ticks


def fetch_kraken_l2_data(client: KrakenSpotClient, pid: str, pair_map: dict[str, str]) -> dict:
    """Fetch L2 depth and return imbalance + best bid depth."""
    try:
        rest_pair = pair_map.get(pid, pid.replace("-", ""))
        resp = client.depth(rest_pair, count=20)
        # Kraken returns { "pair_name": { "bids": [...], "asks": [...] } }
        pair_key = list(resp.keys())[0]
        book = resp[pair_key]
        bids = book.get("bids", [])
        asks = book.get("asks", [])
        if not bids or not asks:
            return {"imb": 0.0, "bid_depth_usd": 0.0}
        
        best_bid_price = float(bids[0][0])
        best_bid_size = float(bids[0][1])
        bid_depth_usd = best_bid_size * best_bid_price
        
        cum_bid = sum(float(b[1]) for b in bids)
        cum_ask = sum(float(a[1]) for a in asks)
        if (cum_bid + cum_ask) == 0:
            return {"imb": 0.0, "bid_depth_usd": bid_depth_usd}
        
        imb = (cum_bid - cum_ask) / (cum_bid + cum_ask)
        return {"imb": imb, "bid_depth_usd": bid_depth_usd}
    except Exception:
        return {"imb": 0.0, "bid_depth_usd": 0.0}

def run_once(client: KrakenSpotClient, engine: MakerMachinegunEngine, state_path: Path, event_path: Path):
    engine.poll_count += 1
    engine.repair_flat_cash_invariant(event_path=event_path)
    
    # GLOBAL VETO (Swarm Brain Integration)
    if engine.is_global_veto_active():
        print(f"[{utc_now_iso()}] GLOBAL VETO ACTIVE: Pausing entry attempts.")
        engine.maybe_close_positions(fetch_kraken_ticks(client, list(engine.active_positions.keys()), engine.pair_map), event_path=event_path)
        save_state(state_path, engine)
        return

    bear_payload = load_json(BEAR_VELOCITY_PATH)
    if bear_payload:
        engine.bear_veto_products = {str(r.get("product_id") or (r.get("base_currency", "") + "-" + r.get("quote_currency", ""))) for r in bear_payload.get("direct_dump_rows", [])}
        engine.bear_veto_products.discard("-")
    
    engine.toxicity.refresh()
    engine.refresh_microfill_summary()
    engine.refresh_harpoon_triggers(SHADOW_LOG_PATH)
    engine.foundry_features = load_json(LIVE_FOUNDRY_PATH)
    opps = load_json(MAKER_OPPORTUNITY_PATH).get("rows", [])
    engine.maker_opportunities = {r["product_id"]: r for r in opps}
    
    # Use the Maker Opportunity Board as the primary candidate source
    rows = opps
    engine.current_cluster_size = len(rows)
    
    manifest_payload = load_json(MANIFEST_PATH)
    engine.alpha_manifest = {r["product_id"]: r for r in manifest_payload.get("manifest", [])}

    active_pids = {r["product_id"] for r in rows}
    for pid in active_pids: engine.candidate_streaks[pid] = engine.candidate_streaks.get(pid, 0) + 1
    
    ghost_pids = {
        str(ghost.get("product_id") or "")
        for ghost in engine.pending_close_ghosts
        if str(ghost.get("product_id") or "")
    }
    ticks = fetch_kraken_ticks(
        client,
        sorted(active_pids | set(engine.active_positions.keys()) | ghost_pids),
        engine.pair_map,
    )
    engine.mark_due_close_ghosts(ticks, event_path=event_path)
    engine.maybe_close_positions(ticks, event_path=event_path)
    
    eligible = engine.eligible_rows(rows, client=client)
    for row in eligible:
        pid = row["product_id"]
        tick = ticks.get(pid)
        if tick:
            # L2 DATA already fetched in eligible_rows for DDS gate
            l2_data = fetch_kraken_l2_data(client, pid, engine.pair_map)
            engine.open_position(row, tick, event_path=event_path, bid_depth_usd=l2_data["bid_depth_usd"])
        
    engine.tick_reentry_blocks()
    save_state(state_path, engine)

def load_json(path: Path) -> dict:
    if not path.exists(): return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}

def load_min_notional_by_product(path: Path) -> dict[str, float]:
    payload = load_json(path)
    rows = payload.get("rows") if isinstance(payload.get("rows"), list) else []
    out: dict[str, float] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        product_id = str(row.get("product_id") or "").upper()
        if not product_id:
            continue
        min_notional = to_float(row.get("min_notional_usd"))
        if min_notional <= 0.0:
            min_notional = to_float(row.get("cost_min"))
        if min_notional > 0.0:
            out[product_id] = min_notional
    return out

def save_state(path: Path, engine: MakerMachinegunEngine):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"state": engine.snapshot(), "updated_at": utc_now_iso()}, indent=2))
    engine.tracker.save()

def parse_horizons(value: str) -> list[int]:
    horizons: list[int] = []
    for part in str(value or "").split(","):
        part = part.strip()
        if not part:
            continue
        try:
            horizon = int(part)
        except ValueError:
            continue
        if horizon > 0:
            horizons.append(horizon)
    return horizons

def parse_float_list(value: str) -> list[float]:
    floats: list[float] = []
    seen: set[float] = set()
    for part in str(value or "").split(","):
        item = part.strip()
        if not item:
            continue
        try:
            parsed = float(item)
        except ValueError:
            continue
        key = round(parsed, 6)
        if key not in seen:
            floats.append(parsed)
            seen.add(key)
    return floats

def parse_reentry_cooldown_overrides(value: str) -> dict[str, int]:
    overrides: dict[str, int] = {}
    for part in str(value or "").split(","):
        item = part.strip()
        if not item:
            continue
        if "=" not in item:
            raise ValueError(f"Invalid cooldown override {item!r}; expected PRODUCT-USD=POLLS")
        product_id, polls_raw = item.split("=", 1)
        product_id = product_id.strip()
        if not product_id:
            raise ValueError(f"Invalid cooldown override {item!r}; missing product id")
        try:
            polls = int(polls_raw.strip())
        except ValueError as exc:
            raise ValueError(f"Invalid cooldown override {item!r}; polls must be an integer") from exc
        if polls <= 0:
            raise ValueError(f"Invalid cooldown override {item!r}; polls must be positive")
        overrides[product_id] = polls
    return overrides

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--starting-cash", type=float, default=100.0)
    parser.add_argument("--maker-fee-bps", type=float, default=25.0)
    parser.add_argument("--reentry-cooldown-polls", type=int, default=60)
    parser.add_argument(
        "--reentry-cooldown-overrides",
        default="",
        help="Comma-separated product cooldown overrides, e.g. HOUSE-USD=20,FOLKS-USD=30.",
    )
    parser.add_argument("--max-loss-pct", type=float, default=3.0)
    parser.add_argument("--no-mfe-stop-pct", type=float, default=0.35)
    parser.add_argument("--no-mfe-stop-min-age-seconds", type=float, default=90.0)
    parser.add_argument("--min-rent-harvest-net-pct", type=float, default=0.10)
    parser.add_argument("--systemic-max-positions", type=int, default=1)
    parser.add_argument("--idiosyncratic-max-positions", type=int, default=10)
    parser.add_argument("--systemic-deploy-pct", type=float, default=0.10)
    parser.add_argument("--idiosyncratic-deploy-pct", type=float, default=0.08)
    parser.add_argument("--systemic-selection-limit", type=int, default=1)
    parser.add_argument(
        "--allowed-quote-currencies",
        default="",
        help="Comma-separated quote currencies to admit from opportunity rows, e.g. USD. Empty means no quote filter.",
    )
    parser.add_argument(
        "--systemic-exclude-products",
        default="",
        help="Comma-separated product IDs to exclude from systemic selection (e.g., HOUSE-USD,FOLKS-USD).",
    )
    parser.add_argument("--max-quote-usd", type=float, default=8.0)
    parser.add_argument("--systemic-min-entry-spread-bps", type=float, default=100.0)
    parser.add_argument("--systemic-min-entry-mer", type=float, default=3.5)
    parser.add_argument("--systemic-min-live-spread-bps", type=float, default=10.0)
    parser.add_argument("--systemic-min-live-to-board-spread-ratio", type=float, default=0.0)
    parser.add_argument("--min-notional-path", default=str(DEFAULT_MIN_NOTIONAL_PATH))
    parser.add_argument("--enforce-min-notional", action="store_true")
    parser.add_argument("--min-in-position-spread-bps", type=float, default=0.0)
    parser.add_argument(
        "--min-harvest-age-seconds",
        type=float,
        default=0.0,
        help="Delay profit-harvest exits until this age; risk, trail, and green-insurance exits remain active.",
    )
    parser.add_argument("--green-insurance-activation-pct", type=float, default=0.0)
    parser.add_argument("--green-insurance-giveback-pct", type=float, default=0.05)
    parser.add_argument("--post-close-ghost-horizons", default="30,60,180,300")
    parser.add_argument("--post-close-ghost-max-age-seconds", type=float, default=900.0)
    parser.add_argument("--state-path", default=str(DEFAULT_STATE_PATH))
    parser.add_argument("--event-path", default=str(DEFAULT_EVENT_PATH))
    parser.add_argument("--lock-path", default=str(DEFAULT_LOCK_PATH))
    parser.add_argument("--loss-tracker-state-path", default=str(LOSS_TRACKER_STATE_PATH))
    parser.add_argument("--enable-dds", action="store_true", help="Enable Dynamic Depth Sizing (L2-aware).")
    parser.add_argument("--dds-depth-pct", type=float, default=0.15, help="Percentage of top-of-book depth for DDS.")
    parser.add_argument("--enable-post-only-simulation", action="store_true", help="Enable Post-Only reject simulation.")
    parser.add_argument("--post-only-reject-prob", type=float, default=0.10, help="Probability of Post-Only rejection.")
    parser.add_argument("--enable-hidden", action="store_true", help="Enable Kraken 'Hidden' (vi) order flag.")
    parser.add_argument("--enable-microfill-calibration", action="store_true", help="Use public-book microfill calibration to cap maker fill probabilities.")
    parser.add_argument("--microfill-calibration-summary-path", default=str(MICROFILL_CALIBRATION_SUMMARY_PATH))
    parser.add_argument("--microfill-min-trials", type=int, default=6)
    parser.add_argument(
        "--maker-exit-taker-fallback-seconds",
        type=float,
        default=40.0,
        help="After a maker profit-exit miss, cross as taker once this many seconds have elapsed if bid-side net still clears the profit-lock threshold.",
    )
    parser.add_argument(
        "--taker-profit-lock-min-net-pct",
        type=float,
        default=0.50,
        help="Minimum bid-side net pct after taker fee required before a maker-exit miss can be locked as a taker profit close.",
    )
    parser.add_argument(
        "--min-exit-microfill-rate",
        type=float,
        default=0.0,
        help="Optional admission gate: require calibrated sell-side maker fill-like rate before opening a maker-harvest position.",
    )
    parser.add_argument(
        "--min-entry-microfill-rate",
        type=float,
        default=0.0,
        help="Optional admission gate: require calibrated buy-side maker fill-like rate before opening a maker-harvest position.",
    )
    parser.add_argument(
        "--systemic-rank-mode",
        choices=["heat", "microfill_adjusted"],
        default="heat",
        help="Systemic candidate ranking mode. microfill_adjusted ranks heat by calibrated buy*sell maker fill-like rates.",
    )
    parser.add_argument(
        "--systemic-preopen-selection-multiplier",
        type=int,
        default=1,
        help="Return extra ranked systemic candidates before open attempts so post-selection vetoes/misses can fall through to alternates.",
    )
    parser.add_argument(
        "--enable-dynamic-hurdle",
        action="store_true",
        help="Opt in to dynamic spread hurdle scaling from observed maker/taker fill mix.",
    )
    parser.add_argument(
        "--maker-exit-improve-after-seconds",
        type=float,
        default=0.0,
        help="After this many seconds of maker-exit attempts, optionally post the sell limit inside the spread.",
    )
    parser.add_argument(
        "--maker-exit-improve-spread-frac",
        type=float,
        default=0.0,
        help="Fraction of current spread to give up on stale maker exits while preserving post-only sell semantics.",
    )
    parser.add_argument(
        "--maker-exit-improve-min-net-pct",
        type=float,
        default=0.0,
        help="Minimum projected maker net pct required before using an improved inside-spread maker exit price.",
    )
    parser.add_argument(
        "--maker-exit-improve-offset-fracs",
        default="",
        help="Comma-separated inside-spread sell offset fractions to evaluate for stale maker exits, e.g. 0.25,0.5,0.75.",
    )
    parser.add_argument(
        "--maker-exit-improve-min-offset-microfill-rate",
        type=float,
        default=0.0,
        help="If >0, require calibrated sell-side fill-like rate for the chosen inside-spread offset before improving exit price.",
    )
    parser.add_argument(
        "--maker-exit-refresh-fill-boost",
        type=float,
        default=0.0,
        help="Optional explicit fill-probability boost for cancel/replace simulation. Keep 0.0 for live-equivalent calibrated tests.",
    )
    parser.add_argument(
        "--maker-first-adverse-bid-stop",
        "--maker-first-adverse-bid_stop",
        dest="maker_first_adverse_bid_stop",
        action="store_true",
        help="When bid-side dynamic stop fires but maker ask-side mark is still green, attempt maker escape before crossing as taker.",
    )
    parser.add_argument(
        "--require-bid-taker-green-for-maker-close",
        action="store_true",
        help="For live-equivalence shadows, do not book maker-profit closes unless immediate bid-side taker liquidation also clears the configured net pct.",
    )
    parser.add_argument(
        "--bid-taker-green-min-net-pct",
        type=float,
        default=0.0,
        help="Minimum immediate bid-side taker net pct required when --require-bid-taker-green-for-maker-close is set.",
    )
    parser.add_argument("--enable-micro-cloud", action="store_true", help="Enable actual Micro-Cloud 2.0 execution logic (staggered ticks).")
    parser.add_argument("--enable-multipolar-alpha", action="store_true", help="Enable crypto-quoted pairs and swarm-brain wiring (isolated probes).")
    parser.add_argument("--swarm-brain-path", default=str(ROOT / "reports" / "swarm_brain_features.json"))
    parser.add_argument("--dynamic-target-recommendations-path", default=str(ROOT / "reports" / "dynamic_target_recommendations.json"))
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()
    reentry_cooldown_overrides = parse_reentry_cooldown_overrides(args.reentry_cooldown_overrides)
    state_path = Path(args.state_path)
    event_path = Path(args.event_path)
    lock_path = Path(args.lock_path)
    loss_tracker_state_path = Path(args.loss_tracker_state_path)
    min_notional_by_product = (
        load_min_notional_by_product(Path(args.min_notional_path))
        if args.enforce_min_notional
        else {}
    )

    lease = acquire_singleton(
        lock_path,
        scope="kraken_spot_frontier_maker_machinegun_shadow",
        metadata={"state_path": str(state_path), "event_path": str(event_path)},
    )
    if not lease.acquired:
        print(f"Another Kraken maker machinegun runner is already active at pid {lease.owner_pid}; exiting.")
        return

    with lease:
        client = KrakenSpotClient()
        engine = MakerMachinegunEngine(
            starting_cash_usd=args.starting_cash, 
            maker_fee_bps=args.maker_fee_bps,
            reentry_cooldown_polls=args.reentry_cooldown_polls, 
            reentry_cooldown_overrides=reentry_cooldown_overrides,
            max_loss_pct=args.max_loss_pct,
            no_mfe_stop_pct=args.no_mfe_stop_pct,
            no_mfe_stop_min_age_seconds=args.no_mfe_stop_min_age_seconds,
            target_net_pct_per_hour=5.0, 
            entry_confirmation_polls=1,
            min_quote_usd=5.0,
            rotation_buffer_pct=0.5,
            min_profit_to_trail_usd=0.01,
            min_rent_harvest_net_pct=args.min_rent_harvest_net_pct,
            systemic_max_positions=args.systemic_max_positions,
            idiosyncratic_max_positions=args.idiosyncratic_max_positions,
            systemic_deploy_pct=args.systemic_deploy_pct,
            idiosyncratic_deploy_pct=args.idiosyncratic_deploy_pct,
            systemic_selection_limit=args.systemic_selection_limit,
            allowed_quote_currencies=[q.strip() for q in args.allowed_quote_currencies.split(",") if q.strip()] if args.allowed_quote_currencies else [],
            systemic_exclude_products=[p.strip() for p in args.systemic_exclude_products.split(",") if p.strip()] if args.systemic_exclude_products else [],
            max_quote_usd=args.max_quote_usd,
            systemic_min_entry_spread_bps=args.systemic_min_entry_spread_bps,
            systemic_min_entry_mer=args.systemic_min_entry_mer,
            systemic_min_live_spread_bps=args.systemic_min_live_spread_bps,
            systemic_min_live_to_board_spread_ratio=args.systemic_min_live_to_board_spread_ratio,
            min_notional_by_product=min_notional_by_product,
            enforce_min_notional=args.enforce_min_notional,
            min_in_position_spread_bps=args.min_in_position_spread_bps,
            min_harvest_age_seconds=args.min_harvest_age_seconds,
            green_insurance_activation_pct=args.green_insurance_activation_pct,
            green_insurance_giveback_pct=args.green_insurance_giveback_pct,
            post_close_ghost_horizons=parse_horizons(args.post_close_ghost_horizons),
            post_close_ghost_max_age_seconds=args.post_close_ghost_max_age_seconds,
            loss_tracker_state_path=loss_tracker_state_path,
            enable_dds=args.enable_dds,
            dds_depth_pct=args.dds_depth_pct,
            enable_post_only_simulation=args.enable_post_only_simulation,
            post_only_reject_prob=args.post_only_reject_prob,
            enable_hidden=args.enable_hidden,
            enable_microfill_calibration=args.enable_microfill_calibration,
            microfill_calibration_summary_path=Path(args.microfill_calibration_summary_path),
            microfill_min_trials=args.microfill_min_trials,
            maker_exit_taker_fallback_seconds=args.maker_exit_taker_fallback_seconds,
            taker_profit_lock_min_net_pct=args.taker_profit_lock_min_net_pct,
            min_entry_microfill_rate=args.min_entry_microfill_rate,
            min_exit_microfill_rate=args.min_exit_microfill_rate,
            systemic_rank_mode=args.systemic_rank_mode,
            systemic_preopen_selection_multiplier=args.systemic_preopen_selection_multiplier,
            enable_dynamic_hurdle=args.enable_dynamic_hurdle,
            maker_exit_improve_after_seconds=args.maker_exit_improve_after_seconds,
            maker_exit_improve_spread_frac=args.maker_exit_improve_spread_frac,
            maker_exit_improve_min_net_pct=args.maker_exit_improve_min_net_pct,
            maker_exit_improve_offset_fracs=parse_float_list(args.maker_exit_improve_offset_fracs),
            maker_exit_improve_min_offset_microfill_rate=args.maker_exit_improve_min_offset_microfill_rate,
            maker_exit_refresh_fill_boost=args.maker_exit_refresh_fill_boost,
            maker_first_adverse_bid_stop=args.maker_first_adverse_bid_stop,
            require_bid_taker_green_for_maker_close=args.require_bid_taker_green_for_maker_close,
            bid_taker_green_min_net_pct=args.bid_taker_green_min_net_pct,
            enable_micro_cloud=args.enable_micro_cloud,
            enable_multipolar_alpha=args.enable_multipolar_alpha,
            swarm_brain_path=Path(args.swarm_brain_path),
            dynamic_target_recommendations_path=Path(args.dynamic_target_recommendations_path),
        )
        
        if state_path.exists():
            engine.load_snapshot(load_json(state_path))
            
        engine.refresh_pair_map(client)
        run_once(client, engine, state_path, event_path)
        if not args.once:
            while True:
                time.sleep(20)
                try:
                    run_once(client, engine, state_path, event_path)
                except Exception as e:
                    print(f"Error: {e}")

if __name__ == "__main__":
    main()
