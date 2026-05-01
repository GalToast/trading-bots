#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from coinbase_advanced_client import CoinbaseAdvancedClient
from coinbase_fee_model import CoinbaseSpotFeeTier, resolve_spot_fee_tier
from live_coinbase_spot_piranha_shadow import fetch_coinbase_tick
from live_penetration_lattice_shadow import append_jsonl, log_runner_exception, utc_now_iso

# Add scripts directory to path for local imports
sys.path.append(str(Path(__file__).parent))
from mfe_capture_tracker import MFETracker


ROOT = Path(__file__).resolve().parent.parent
DEFAULT_STATE_PATH = ROOT / "reports" / "coinbase_spot_machinegun_shadow_state.json"
DEFAULT_EVENT_PATH = ROOT / "reports" / "coinbase_spot_machinegun_shadow_events.jsonl"
DEFAULT_OPPORTUNITY_TAPE_PATH = ROOT / "reports" / "coinbase_spot_machinegun_opportunity_tape.jsonl"
STRATEGY_BOARD_PATH = ROOT / "reports" / "coinbase_spot_machinegun_strategy_board.json"
BEAR_VELOCITY_PATH = ROOT / "reports" / "coinbase_spot_bear_velocity_board.json"
MFE_TRACKER_PATH = ROOT / "reports" / "coinbase_spot_machinegun_mfe_tracker.json"


def to_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def round_optional(value: Any, digits: int = 6) -> float | None:
    if value is None:
        return None
    return round(to_float(value), digits)


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
    entry_ml_survival_prob: float = 0.0
    entry_fast_green_prob: float = 0.0
    entry_tail_prob: float = 0.0
    entry_bubble_capture_net_pct_per_hour: float = 0.0


@dataclass
class GhostPosition:
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
    entry_ml_survival_prob: float = 0.0
    entry_fast_green_prob: float = 0.0
    entry_tail_prob: float = 0.0
    entry_bubble_capture_net_pct_per_hour: float = 0.0


class MachinegunShadowEngine:
    def __init__(
        self,
        *,
        starting_cash_usd: float,
        deploy_pct: float,
        taker_fee_bps: float,
        min_quote_usd: float,
        max_loss_pct: float,
        min_profit_to_trail_usd: float,
        rotation_buffer_pct: float,
        reentry_cooldown_polls: int,
        ghost_top_n: int,
        ghost_min_closes_for_bias: int,
        ghost_edge_bias_cap_pct: float,
        profit_lock_retention_pct: float,
        target_net_pct_per_hour: float,
        ghost_timing_cooloff_min_closes: int,
        ghost_timing_cooloff_max_avg_loss_pct: float,
        target_pressure_exit_net_loss_pct: float,
        entry_confirmation_polls: int,
        target_pressure_min_entry_edge_pct: float = 0.0,
        target_pressure_min_live_move_bps: float = 0.0,
        target_pressure_live_override_bps: float = 0.0,
        target_pressure_live_override_min_edge_pct: float = 0.0,
        require_ml_survival_prob: float = 0.0,
        require_fast_green_prob: float = 0.0,
        require_tail_prob: float = 0.0,
        require_bubble_capture_net_pct_per_hour: float = 0.0,
        manifest_positive_within_seconds: float = 0.0,
        manifest_positive_min_net_pct: float = 0.0,
        min_in_position_spread_bps: float = 50.0,
        require_cluster_size_threshold: int = 20,
        systemic_max_positions: int = 1,
        idiosyncratic_max_positions: int = 3,
        systemic_deploy_pct: float = 0.2,
        idiosyncratic_deploy_pct: float = 0.8,
    ) -> None:
        self.starting_cash_usd = float(starting_cash_usd)
        self.deploy_pct = float(deploy_pct)
        self.taker_fee_bps = float(taker_fee_bps)
        self.min_quote_usd = float(min_quote_usd)
        self.max_loss_pct = float(max_loss_pct)
        self.min_profit_to_trail_usd = float(min_profit_to_trail_usd)
        self.rotation_buffer_pct = float(rotation_buffer_pct)
        self.reentry_cooldown_polls = max(0, int(reentry_cooldown_polls))
        self.ghost_top_n = max(0, int(ghost_top_n))
        self.ghost_min_closes_for_bias = max(1, int(ghost_min_closes_for_bias))
        self.ghost_edge_bias_cap_pct = max(0.0, float(ghost_edge_bias_cap_pct))
        self.profit_lock_retention_pct = max(0.0, min(100.0, float(profit_lock_retention_pct)))
        self.target_net_pct_per_hour = max(0.0, float(target_net_pct_per_hour))
        self.target_started_at = utc_now_iso()
        self.ghost_timing_cooloff_min_closes = max(1, int(ghost_timing_cooloff_min_closes))
        self.ghost_timing_cooloff_max_avg_loss_pct = max(0.0, float(ghost_timing_cooloff_max_avg_loss_pct))
        self.target_pressure_exit_net_loss_pct = max(0.0, float(target_pressure_exit_net_loss_pct))
        self.entry_confirmation_polls = max(1, int(entry_confirmation_polls))
        self.target_pressure_min_entry_edge_pct = max(0.0, float(target_pressure_min_entry_edge_pct))
        self.target_pressure_min_live_move_bps = max(0.0, float(target_pressure_min_live_move_bps))
        self.target_pressure_live_override_bps = max(0.0, float(target_pressure_live_override_bps))
        self.target_pressure_live_override_min_edge_pct = max(0.0, float(target_pressure_live_override_min_edge_pct))
        self.require_ml_survival_prob = max(0.0, min(1.0, float(require_ml_survival_prob)))
        self.require_fast_green_prob = max(0.0, min(1.0, float(require_fast_green_prob)))
        self.require_tail_prob = max(0.0, min(1.0, float(require_tail_prob)))
        self.require_bubble_capture_net_pct_per_hour = max(0.0, float(require_bubble_capture_net_pct_per_hour))
        self.manifest_positive_within_seconds = max(0.0, float(manifest_positive_within_seconds))
        self.manifest_positive_min_net_pct = max(0.0, float(manifest_positive_min_net_pct))
        self.min_in_position_spread_bps = max(0.0, float(min_in_position_spread_bps))
        self.require_cluster_size_threshold = int(require_cluster_size_threshold)
        self.systemic_max_positions = max(1, int(systemic_max_positions))
        self.idiosyncratic_max_positions = max(1, int(idiosyncratic_max_positions))
        self.systemic_deploy_pct = max(0.0, min(1.0, float(systemic_deploy_pct)))
        self.idiosyncratic_deploy_pct = max(0.0, min(1.0, float(idiosyncratic_deploy_pct)))
        self.cash_usd = float(starting_cash_usd)
        self.active_positions: dict[str, MachinegunPosition] = {}
        self.position: MachinegunPosition | None = None
        self.ghost_positions: dict[str, GhostPosition] = {}
        self.ghost_stats: dict[str, dict[str, Any]] = {}
        self.reentry_blocks: dict[str, int] = {}
        self.candidate_streaks: dict[str, int] = {}
        self.live_momentum: dict[str, dict[str, Any]] = {}
        self.bear_veto_products: set[str] = set()
        self.mfe_tracker = MFETracker(default_fee_bps=taker_fee_bps * 2.0)
        self.current_cluster_size = 0
        self.realized_net_usd = 0.0
        self.realized_closes = 0
        self.total_fees = 0.0
        self.last_action = ""
        self.last_decision: dict[str, Any] = {}
        self.fee_source = "configured"
        self.fee_tier = ""

    def fee_rate(self) -> float:
        return self.taker_fee_bps / 10000.0

    def apply_fee_tier(self, fee_tier: CoinbaseSpotFeeTier) -> None:
        self.taker_fee_bps = float(fee_tier.taker_bps)
        self.fee_source = fee_tier.source
        self.fee_tier = fee_tier.pricing_tier

    def sync_primary_position(self) -> None:
        self.position = next(iter(self.active_positions.values()), None)

    def snapshot(self) -> dict[str, Any]:
        return {
            "mode": "coinbase_spot_machinegun_shadow",
            "starting_cash_usd": self.starting_cash_usd,
            "cash_usd": round(self.cash_usd, 6),
            "realized_net_usd": round(self.realized_net_usd, 6),
            "realized_closes": self.realized_closes,
            "total_fees": round(self.total_fees, 6),
            "taker_fee_bps": round(self.taker_fee_bps, 4),
            "fee_source": self.fee_source,
            "fee_tier": self.fee_tier,
            "deploy_pct": self.deploy_pct,
            "min_quote_usd": self.min_quote_usd,
            "max_loss_pct": self.max_loss_pct,
            "min_profit_to_trail_usd": self.min_profit_to_trail_usd,
            "rotation_buffer_pct": self.rotation_buffer_pct,
            "reentry_cooldown_polls": self.reentry_cooldown_polls,
            "reentry_blocks": self.reentry_blocks,
            "ghost_top_n": self.ghost_top_n,
            "ghost_min_closes_for_bias": self.ghost_min_closes_for_bias,
            "ghost_edge_bias_cap_pct": self.ghost_edge_bias_cap_pct,
            "profit_lock_retention_pct": self.profit_lock_retention_pct,
            "target_net_pct_per_hour": self.target_net_pct_per_hour,
            "target_started_at": self.target_started_at,
            "ghost_timing_cooloff_min_closes": self.ghost_timing_cooloff_min_closes,
            "ghost_timing_cooloff_max_avg_loss_pct": self.ghost_timing_cooloff_max_avg_loss_pct,
            "target_pressure_exit_net_loss_pct": self.target_pressure_exit_net_loss_pct,
            "entry_confirmation_polls": self.entry_confirmation_polls,
            "target_pressure_min_entry_edge_pct": self.target_pressure_min_entry_edge_pct,
            "target_pressure_min_live_move_bps": self.target_pressure_min_live_move_bps,
            "target_pressure_live_override_bps": self.target_pressure_live_override_bps,
            "target_pressure_live_override_min_edge_pct": self.target_pressure_live_override_min_edge_pct,
            "require_ml_survival_prob": self.require_ml_survival_prob,
            "require_fast_green_prob": self.require_fast_green_prob,
            "require_tail_prob": self.require_tail_prob,
            "require_bubble_capture_net_pct_per_hour": self.require_bubble_capture_net_pct_per_hour,
            "manifest_positive_within_seconds": self.manifest_positive_within_seconds,
            "manifest_positive_min_net_pct": self.manifest_positive_min_net_pct,
            "require_cluster_size_threshold": self.require_cluster_size_threshold,
            "systemic_max_positions": self.systemic_max_positions,
            "idiosyncratic_max_positions": self.idiosyncratic_max_positions,
            "systemic_deploy_pct": self.systemic_deploy_pct,
            "idiosyncratic_deploy_pct": self.idiosyncratic_deploy_pct,
            "candidate_streaks": self.candidate_streaks,
            "live_momentum": self.live_momentum,
            "current_cluster_size": self.current_cluster_size,
            "ghost_positions": {product: asdict(position) for product, position in self.ghost_positions.items()},
            "ghost_stats": self.ghost_stats,
            "active_positions": {product: asdict(position) for product, position in self.active_positions.items()},
            "mfe_stats": self.mfe_tracker.get_stats(),
            "last_action": self.last_action,
            "last_decision": self.last_decision,
        }

    def load_snapshot(self, payload: dict[str, Any]) -> None:
        state = payload.get("state") if isinstance(payload, dict) else None
        if not isinstance(state, dict):
            return
        self.cash_usd = float(state.get("cash_usd", self.starting_cash_usd) or self.starting_cash_usd)
        self.realized_net_usd = float(state.get("realized_net_usd", 0.0) or 0.0)
        self.realized_closes = int(state.get("realized_closes", 0) or 0)
        self.total_fees = float(state.get("total_fees", 0.0) or 0.0)
        self.target_net_pct_per_hour = float(state.get("target_net_pct_per_hour", self.target_net_pct_per_hour) or self.target_net_pct_per_hour)
        self.target_started_at = str(
            state.get("target_started_at")
            or (payload.get("runner") if isinstance(payload.get("runner"), dict) else {}).get("started_at")
            or self.target_started_at
        )
        self.ghost_timing_cooloff_min_closes = int(
            state.get("ghost_timing_cooloff_min_closes", state.get("ghost_veto_min_closes", self.ghost_timing_cooloff_min_closes))
            or self.ghost_timing_cooloff_min_closes
        )
        self.ghost_timing_cooloff_max_avg_loss_pct = float(
            state.get(
                "ghost_timing_cooloff_max_avg_loss_pct",
                state.get("ghost_veto_max_avg_loss_pct", self.ghost_timing_cooloff_max_avg_loss_pct),
            )
            or self.ghost_timing_cooloff_max_avg_loss_pct
        )
        self.target_pressure_exit_net_loss_pct = float(
            state.get("target_pressure_exit_net_loss_pct", self.target_pressure_exit_net_loss_pct)
            or self.target_pressure_exit_net_loss_pct
        )
        self.entry_confirmation_polls = int(state.get("entry_confirmation_polls", self.entry_confirmation_polls) or self.entry_confirmation_polls)
        self.target_pressure_min_entry_edge_pct = float(
            state.get("target_pressure_min_entry_edge_pct", self.target_pressure_min_entry_edge_pct)
            or self.target_pressure_min_entry_edge_pct
        )
        self.target_pressure_min_live_move_bps = float(
            state.get("target_pressure_min_live_move_bps", self.target_pressure_min_live_move_bps)
            or self.target_pressure_min_live_move_bps
        )
        self.target_pressure_live_override_bps = float(
            state.get("target_pressure_live_override_bps", self.target_pressure_live_override_bps)
            or self.target_pressure_live_override_bps
        )
        self.target_pressure_live_override_min_edge_pct = float(
            state.get("target_pressure_live_override_min_edge_pct", self.target_pressure_live_override_min_edge_pct)
            or self.target_pressure_live_override_min_edge_pct
        )
        self.require_ml_survival_prob = max(
            0.0,
            min(1.0, float(state.get("require_ml_survival_prob", self.require_ml_survival_prob) or self.require_ml_survival_prob)),
        )
        self.require_fast_green_prob = max(
            0.0,
            min(1.0, float(state.get("require_fast_green_prob", self.require_fast_green_prob) or self.require_fast_green_prob)),
        )
        self.require_tail_prob = max(
            0.0,
            min(1.0, float(state.get("require_tail_prob", self.require_tail_prob) or self.require_tail_prob)),
        )
        self.require_bubble_capture_net_pct_per_hour = max(
            0.0,
            float(
                state.get(
                    "require_bubble_capture_net_pct_per_hour",
                    self.require_bubble_capture_net_pct_per_hour,
                )
                or self.require_bubble_capture_net_pct_per_hour
            ),
        )
        self.manifest_positive_within_seconds = max(
            0.0,
            float(state.get("manifest_positive_within_seconds", self.manifest_positive_within_seconds) or self.manifest_positive_within_seconds),
        )
        self.manifest_positive_min_net_pct = max(
            0.0,
            float(state.get("manifest_positive_min_net_pct", self.manifest_positive_min_net_pct) or self.manifest_positive_min_net_pct),
        )
        self.require_cluster_size_threshold = int(
            state.get("require_cluster_size_threshold", self.require_cluster_size_threshold)
            or self.require_cluster_size_threshold
        )
        self.systemic_max_positions = int(
            state.get("systemic_max_positions", self.systemic_max_positions)
            or self.systemic_max_positions
        )
        self.idiosyncratic_max_positions = int(
            state.get("idiosyncratic_max_positions", self.idiosyncratic_max_positions)
            or self.idiosyncratic_max_positions
        )
        self.systemic_deploy_pct = float(
            state.get("systemic_deploy_pct", self.systemic_deploy_pct)
            or self.systemic_deploy_pct
        )
        self.idiosyncratic_deploy_pct = float(
            state.get("idiosyncratic_deploy_pct", self.idiosyncratic_deploy_pct)
            or self.idiosyncratic_deploy_pct
        )
        self.current_cluster_size = int(state.get("current_cluster_size", 0))
        streaks = state.get("candidate_streaks")
        if isinstance(streaks, dict):
            self.candidate_streaks = {str(product): int(count) for product, count in streaks.items() if int(count or 0) > 0}
        momentum = state.get("live_momentum")
        if isinstance(momentum, dict):
            self.live_momentum = {str(product): dict(payload) for product, payload in momentum.items() if isinstance(payload, dict)}
        self.last_action = str(state.get("last_action") or "")
        decision = state.get("last_decision")
        if isinstance(decision, dict):
            self.last_decision = decision
        blocks = state.get("reentry_blocks")
        if isinstance(blocks, dict):
            self.reentry_blocks = {str(product): int(polls) for product, polls in blocks.items() if int(polls or 0) > 0}
        ghosts = state.get("ghost_positions")
        if isinstance(ghosts, dict):
            self.ghost_positions = {}
            for product, payload in ghosts.items():
                if not isinstance(payload, dict):
                    continue
                self.ghost_positions[str(product)] = GhostPosition(
                    product_id=str(payload.get("product_id") or product),
                    playbook=str(payload.get("playbook") or ""),
                    entry_price=float(payload.get("entry_price") or 0.0),
                    quantity=float(payload.get("quantity") or 0.0),
                    cost_usd=float(payload.get("cost_usd") or 1.0),
                    entry_fee=float(payload.get("entry_fee") or 0.0),
                    opened_at=str(payload.get("opened_at") or ""),
                    highest_bid=float(payload.get("highest_bid") or 0.0),
                    trail_giveback_pct=float(payload.get("trail_giveback_pct") or 0.25),
                    entry_edge_over_hurdle_pct=float(payload.get("entry_edge_over_hurdle_pct") or 0.0),
                    max_net_pnl=float(payload.get("max_net_pnl") or 0.0),
                    max_net_pct_on_cost=float(payload.get("max_net_pct_on_cost") or 0.0),
                    entry_ml_survival_prob=to_float(payload.get("entry_ml_survival_prob")),
                    entry_fast_green_prob=to_float(payload.get("entry_fast_green_prob")),
                    entry_tail_prob=to_float(payload.get("entry_tail_prob")),
                    entry_bubble_capture_net_pct_per_hour=to_float(payload.get("entry_bubble_capture_net_pct_per_hour")),
                )
        stats = state.get("ghost_stats")
        if isinstance(stats, dict):
            self.ghost_stats = {str(product): dict(payload) for product, payload in stats.items() if isinstance(payload, dict)}
        
        active = state.get("active_positions")
        if isinstance(active, dict):
            self.active_positions = {}
            for product, pos in active.items():
                if not isinstance(pos, dict):
                    continue
                self.active_positions[str(product)] = MachinegunPosition(
                    product_id=str(pos.get("product_id") or product),
                    playbook=str(pos.get("playbook") or ""),
                    entry_price=float(pos.get("entry_price") or 0.0),
                    quantity=float(pos.get("quantity") or 0.0),
                    cost_usd=float(pos.get("cost_usd") or 0.0),
                    entry_fee=float(pos.get("entry_fee") or 0.0),
                    opened_at=str(pos.get("opened_at") or ""),
                    highest_bid=float(pos.get("highest_bid") or 0.0),
                    trail_giveback_pct=float(pos.get("trail_giveback_pct") or 0.25),
                    entry_edge_over_hurdle_pct=float(pos.get("entry_edge_over_hurdle_pct") or 0.0),
                    max_net_pnl=float(pos.get("max_net_pnl") or 0.0),
                    max_net_pct_on_cost=float(pos.get("max_net_pct_on_cost") or 0.0),
                    entry_ml_survival_prob=to_float(pos.get("entry_ml_survival_prob")),
                    entry_fast_green_prob=to_float(pos.get("entry_fast_green_prob")),
                    entry_tail_prob=to_float(pos.get("entry_tail_prob")),
                    entry_bubble_capture_net_pct_per_hour=to_float(pos.get("entry_bubble_capture_net_pct_per_hour")),
                )
        
        # Legacy support
        pos = state.get("position")
        if isinstance(pos, dict) and not self.active_positions:
            product_id = str(pos.get("product_id") or "legacy")
            self.active_positions[product_id] = MachinegunPosition(
                product_id=product_id,
                playbook=str(pos.get("playbook") or ""),
                entry_price=float(pos.get("entry_price") or 0.0),
                quantity=float(pos.get("quantity") or 0.0),
                cost_usd=float(pos.get("cost_usd") or 0.0),
                entry_fee=float(pos.get("entry_fee") or 0.0),
                opened_at=str(pos.get("opened_at") or ""),
                highest_bid=float(pos.get("highest_bid") or 0.0),
                trail_giveback_pct=float(pos.get("trail_giveback_pct") or 0.25),
                entry_edge_over_hurdle_pct=float(pos.get("entry_edge_over_hurdle_pct") or 0.0),
                max_net_pnl=float(pos.get("max_net_pnl") or 0.0),
                max_net_pct_on_cost=float(pos.get("max_net_pct_on_cost") or 0.0),
                entry_ml_survival_prob=to_float(pos.get("entry_ml_survival_prob")),
                entry_fast_green_prob=to_float(pos.get("entry_fast_green_prob")),
                entry_tail_prob=to_float(pos.get("entry_tail_prob")),
                entry_bubble_capture_net_pct_per_hour=to_float(pos.get("entry_bubble_capture_net_pct_per_hour")),
            )
        self.sync_primary_position()

    def open_position(self, row: dict[str, Any], tick: dict[str, Any], *, event_path: Path) -> None:
        product_id = str(row["product_id"])
        if self.reentry_blocks.get(product_id, 0) > 0:
            self.last_action = "entry_skipped_reentry_cooldown"
            append_jsonl(
                event_path,
                {
                    "ts_utc": utc_now_iso(),
                    "action": self.last_action,
                    "product_id": product_id,
                    "remaining_polls": self.reentry_blocks[product_id],
                },
            )
            return
            
        mode = self.execution_mode()
        max_pos = self.idiosyncratic_max_positions if mode == "idiosyncratic" else self.systemic_max_positions
        deploy_pct = self.idiosyncratic_deploy_pct if mode == "idiosyncratic" else self.systemic_deploy_pct
        
        if len(self.active_positions) >= max_pos:
            self.last_action = "entry_skipped_max_positions_reached"
            return

        ask = float(tick["ask"])
        quote_usd = min(self.cash_usd * deploy_pct, self.cash_usd)
        if quote_usd < self.min_quote_usd:
            self.last_action = "entry_skipped_cash_below_min"
            append_jsonl(event_path, {"ts_utc": utc_now_iso(), "action": self.last_action, "cash_usd": self.cash_usd})
            return
        entry_fee = quote_usd * self.fee_rate()
        quantity = (quote_usd - entry_fee) / ask
        if quantity <= 0.0:
            return
        self.cash_usd -= quote_usd
        self.total_fees += entry_fee
        opened_position = MachinegunPosition(
            product_id=product_id,
            playbook=str(row["playbook"]),
            entry_price=ask,
            quantity=quantity,
            cost_usd=quote_usd,
            entry_fee=entry_fee,
            opened_at=utc_now_iso(),
            highest_bid=float(tick["bid"]),
            trail_giveback_pct=max(0.25, float(row.get("trail_giveback_pct") or 0.25)),
            entry_edge_over_hurdle_pct=float(row.get("edge_over_hurdle_pct") or 0.0),
            max_net_pnl=-entry_fee,
            max_net_pct_on_cost=(-entry_fee / quote_usd) * 100.0 if quote_usd else 0.0,
            entry_ml_survival_prob=to_float(row.get("ml_survival_prob")),
            entry_fast_green_prob=to_float(row.get("fast_green_prob")),
            entry_tail_prob=to_float(row.get("tail_prob")),
            entry_bubble_capture_net_pct_per_hour=to_float(row.get("bubble_capture_net_pct_per_hour")),
        )
        self.active_positions[product_id] = opened_position
        self.position = opened_position
        
        # Track MFE for telemetry
        self.mfe_tracker.on_entry(
            trade_id=f"{product_id}-{opened_position.opened_at}",
            product_id=product_id,
            entry_price=ask,
            predicted_mfe_pct=opened_position.entry_tail_prob or 0.01, # Use tail prob as proxy for predicted MFE
            fee_bps=self.taker_fee_bps * 2.0
        )
        
        self.last_action = "open_machinegun_shadow"
        append_jsonl(
            event_path,
            {
                "ts_utc": utc_now_iso(),
                "action": self.last_action,
                "product_id": product_id,
                "playbook": str(row["playbook"]),
                "entry_price": ask,
                "quantity": round(quantity, 12),
                "quote_usd": round(quote_usd, 6),
                "entry_fee": round(entry_fee, 6),
                "fee_bps_per_side": round(self.taker_fee_bps, 4),
                "trail_giveback_pct": round(max(0.25, float(row.get("trail_giveback_pct") or 0.25)), 4),
                "edge_over_hurdle_pct": round(float(row.get("edge_over_hurdle_pct") or 0.0), 4),
                "entry_ml_survival_prob": round(to_float(row.get("ml_survival_prob")), 6),
                "entry_fast_green_prob": round(to_float(row.get("fast_green_prob")), 6),
                "entry_tail_prob": round(to_float(row.get("tail_prob")), 6),
                "entry_bubble_capture_net_pct_per_hour": round(to_float(row.get("bubble_capture_net_pct_per_hour")), 6),
                "cash_after": round(self.cash_usd, 6),
                "execution_mode": mode,
            },
        )

    def mark_position(self, pos: MachinegunPosition | dict[str, Any], tick: dict[str, Any] | None = None) -> dict[str, Any]:
        if tick is None:
            tick = pos  # type: ignore[assignment]
            if self.position is None:
                self.sync_primary_position()
            if self.position is None:
                return {}
            pos = self.position
        if not isinstance(pos, MachinegunPosition):
            return {}
        bid = float(tick["bid"])
        ask = float(tick["ask"]) if "ask" in tick else None
        spread_bps = ((ask - bid) / bid) * 10000.0 if ask is not None and bid > 0.0 else None
        pos.highest_bid = max(pos.highest_bid, bid)
        
        # Update MFE heartbeat
        self.mfe_tracker.on_heartbeat(
            trade_id=f"{pos.product_id}-{pos.opened_at}",
            current_high=pos.highest_bid
        )
        
        proceeds = pos.quantity * bid
        exit_fee = proceeds * self.fee_rate()
        net = proceeds - exit_fee - pos.cost_usd
        gross = (bid - pos.entry_price) * pos.quantity
        mfe_gross_pnl = (pos.highest_bid - pos.entry_price) * pos.quantity
        mfe_gross_pct = ((pos.highest_bid - pos.entry_price) / pos.entry_price) * 100.0 if pos.entry_price else 0.0
        trail_stop = pos.highest_bid * (1.0 - (pos.trail_giveback_pct / 100.0))
        loss_pct = ((bid - pos.entry_price) / pos.entry_price) * 100.0 if pos.entry_price else 0.0
        net_pct_on_cost = (net / pos.cost_usd) * 100.0 if pos.cost_usd else 0.0
        pos.max_net_pnl = max(pos.max_net_pnl, net)
        pos.max_net_pct_on_cost = max(pos.max_net_pct_on_cost, net_pct_on_cost)
        distance_to_trail_pct = ((bid - trail_stop) / bid) * 100.0 if bid else 0.0
        gross_mfe_capture_pct = (gross / mfe_gross_pnl) * 100.0 if mfe_gross_pnl > 0.0 else None
        net_mfe_capture_pct = (net / pos.max_net_pnl) * 100.0 if pos.max_net_pnl > 0.0 else None
        return {
            "product_id": pos.product_id,
            "playbook": pos.playbook,
            "entry_price": pos.entry_price,
            "bid": bid,
            "ask": ask,
            "spread_bps": spread_bps,
            "highest_bid": pos.highest_bid,
            "trail_stop": trail_stop,
            "distance_to_trail_pct": distance_to_trail_pct,
            "quantity": pos.quantity,
            "proceeds": proceeds,
            "gross_pnl": gross,
            "mfe_gross_pnl": mfe_gross_pnl,
            "mfe_gross_pct": mfe_gross_pct,
            "gross_mfe_capture_pct": gross_mfe_capture_pct,
            "entry_fee": pos.entry_fee,
            "exit_fee": exit_fee,
            "roundtrip_fee": pos.entry_fee + exit_fee,
            "net_pnl": net,
            "net_pct_on_cost": net_pct_on_cost,
            "max_net_pnl": pos.max_net_pnl,
            "max_net_pct_on_cost": pos.max_net_pct_on_cost,
            "net_mfe_capture_pct": net_mfe_capture_pct,
            "loss_pct": loss_pct,
            "entry_ml_survival_prob": pos.entry_ml_survival_prob,
            "entry_fast_green_prob": pos.entry_fast_green_prob,
            "entry_tail_prob": pos.entry_tail_prob,
            "entry_bubble_capture_net_pct_per_hour": pos.entry_bubble_capture_net_pct_per_hour,
        }

    def close_position(
        self,
        pos: MachinegunPosition | dict[str, Any],
        mark: dict[str, Any] | None = None,
        *,
        event_path: Path,
        exit_reason: str,
    ) -> None:
        if mark is None:
            mark = pos  # type: ignore[assignment]
            if self.position is None:
                self.sync_primary_position()
            if self.position is None:
                return
            pos = self.position
        if not isinstance(pos, MachinegunPosition):
            return
        proceeds = float(mark["proceeds"])
        exit_fee = float(mark["exit_fee"])
        net = float(mark["net_pnl"])
        
        # Track MFE exit
        mfe_result = self.mfe_tracker.on_exit(
            trade_id=f"{pos.product_id}-{pos.opened_at}",
            exit_price=float(mark["bid"])
        )
        
        self.cash_usd += proceeds - exit_fee
        self.total_fees += exit_fee
        self.realized_net_usd += net
        self.realized_closes += 1
        self.last_action = f"close_{exit_reason}"
        
        close_event = {
            "ts_utc": utc_now_iso(),
            "action": "close_machinegun_shadow",
            "exit_reason": exit_reason,
            "product_id": pos.product_id,
            "playbook": pos.playbook,
            "entry_price": pos.entry_price,
            "exit_price": round(float(mark["bid"]), 12),
            "highest_bid": round(float(mark["highest_bid"]), 12),
            "trail_stop": round(float(mark["trail_stop"]), 12),
            "quantity": round(pos.quantity, 12),
            "gross_pnl": round(float(mark["gross_pnl"]), 6),
            "mfe_gross_pnl": round(float(mark.get("mfe_gross_pnl") or 0.0), 6),
            "mfe_gross_pct": round(float(mark.get("mfe_gross_pct") or 0.0), 6),
            "gross_mfe_capture_pct": round_optional(mark.get("gross_mfe_capture_pct")),
            "entry_fee": round(pos.entry_fee, 6),
            "exit_fee": round(exit_fee, 6),
            "fee": round(pos.entry_fee + exit_fee, 6),
            "net_pnl": round(net, 6),
            "net_pct_on_cost": round(float(mark["net_pct_on_cost"]), 6),
            "max_net_pnl": round(float(mark.get("max_net_pnl") or 0.0), 6),
            "max_net_pct_on_cost": round(float(mark.get("max_net_pct_on_cost") or 0.0), 6),
            "net_mfe_capture_pct": round_optional(mark.get("net_mfe_capture_pct")),
            "entry_ml_survival_prob": round(pos.entry_ml_survival_prob, 6),
            "entry_fast_green_prob": round(pos.entry_fast_green_prob, 6),
            "entry_tail_prob": round(pos.entry_tail_prob, 6),
            "entry_bubble_capture_net_pct_per_hour": round(pos.entry_bubble_capture_net_pct_per_hour, 6),
            "fee_bps_per_side": round(self.taker_fee_bps, 4),
            "realized_net_usd": round(self.realized_net_usd, 6),
            "cash_after": round(self.cash_usd, 6),
        }
        
        if mfe_result:
            close_event["mfe_capture_rate"] = round_optional(mfe_result.capture_rate)
            close_event["predicted_mfe_capture_rate"] = round_optional(mfe_result.predicted_capture_rate)
            
        append_jsonl(event_path, close_event)
        self.active_positions.pop(pos.product_id, None)
        self.sync_primary_position()

    def block_reentry(self, product_id: str, *, reason: str, event_path: Path) -> None:
        if self.reentry_cooldown_polls <= 0:
            return
        self.reentry_blocks[product_id] = max(self.reentry_blocks.get(product_id, 0), self.reentry_cooldown_polls)
        append_jsonl(
            event_path,
            {
                "ts_utc": utc_now_iso(),
                "action": "block_machinegun_reentry",
                "product_id": product_id,
                "reason": reason,
                "cooldown_polls": self.reentry_blocks[product_id],
            },
        )

    def tick_reentry_blocks(self) -> None:
        self.reentry_blocks = {
            product: polls - 1 for product, polls in self.reentry_blocks.items() if polls > 1
        }

    def refresh_candidate_streaks(self, rows: list[dict[str, Any]]) -> None:
        ranked_products = [str(row.get("product_id") or "") for row in rows if str(row.get("product_id") or "")]
        active = set(ranked_products)
        self.candidate_streaks = {product: count for product, count in self.candidate_streaks.items() if product in active}
        for product in ranked_products:
            self.candidate_streaks[product] = self.candidate_streaks.get(product, 0) + 1

    def update_live_momentum(self, ticks: dict[str, dict[str, Any]]) -> None:
        now = utc_now_iso()
        for product_id, tick in ticks.items():
            bid = float(tick.get("bid") or 0.0)
            ask = float(tick.get("ask") or 0.0)
            if bid <= 0.0:
                continue
            previous = self.live_momentum.get(product_id) if isinstance(self.live_momentum.get(product_id), dict) else {}
            previous_bid = float(previous.get("bid") or 0.0) if previous else 0.0
            move_bps = ((bid - previous_bid) / previous_bid) * 10000.0 if previous_bid > 0.0 else 0.0
            spread_bps = ((ask - bid) / bid) * 10000.0 if ask > 0.0 else 0.0
            live_move_streak = (
                int(previous.get("live_move_streak", 0) or 0) + 1
                if self.target_pressure_min_live_move_bps > 0.0 and move_bps >= self.target_pressure_min_live_move_bps
                else 0
            )
            live_override_streak = (
                int(previous.get("live_override_streak", 0) or 0) + 1
                if self.target_pressure_live_override_bps > 0.0 and move_bps >= self.target_pressure_live_override_bps
                else 0
            )
            self.live_momentum[product_id] = {
                "bid": bid,
                "ask": ask,
                "previous_bid": previous_bid,
                "move_bps": round(move_bps, 6),
                "spread_bps": round(spread_bps, 6),
                "live_move_streak": live_move_streak,
                "live_override_streak": live_override_streak,
                "updated_at": now,
                "samples": int(previous.get("samples", 0) or 0) + 1 if previous else 1,
            }

    def entry_confirmed(self, product_id: str) -> bool:
        if self.entry_confirmation_polls <= 1:
            return True
        return self.candidate_streaks.get(product_id, 0) >= self.entry_confirmation_polls

    def age_seconds(self, opened_at: str) -> float:
        try:
            opened = datetime.fromisoformat(str(opened_at).replace("Z", "+00:00"))
        except ValueError:
            return 0.0
        if opened.tzinfo is None:
            opened = opened.replace(tzinfo=timezone.utc)
        return max(0.0, (datetime.now(timezone.utc) - opened.astimezone(timezone.utc)).total_seconds())

    def live_velocity_override(self, row: dict[str, Any], product_id: str) -> bool:
        if self.target_pressure_live_override_bps <= 0.0:
            return False
        momentum = self.live_momentum.get(product_id)
        if not isinstance(momentum, dict) or int(momentum.get("samples", 0) or 0) < 2:
            return False
        if int(momentum.get("live_override_streak", 0) or 0) < max(1, self.entry_confirmation_polls):
            return False
        edge = float(row.get("ghost_adjusted_edge_over_hurdle_pct", row.get("edge_over_hurdle_pct")) or 0.0)
        if edge < self.target_pressure_live_override_min_edge_pct:
            return False
        if float(row.get("ret_15m_pct") or 0.0) <= 0.0 or float(row.get("ret_60m_pct") or 0.0) <= 0.0:
            return False
        return float(momentum.get("move_bps") or 0.0) >= self.target_pressure_live_override_bps

    def ghost_timing_cooloff_reason(self, product_id: str, *, bid: float | None = None) -> str:
        stats = self.ghost_stats.get(product_id)
        if not isinstance(stats, dict):
            return ""
        closes = int(stats.get("closes", 0) or 0)
        wins = int(stats.get("wins", 0) or 0)
        if closes < self.ghost_timing_cooloff_min_closes or wins > 0:
            return ""
        avg_net_pct = float(stats.get("net_pct", 0.0) or 0.0) / max(1, closes)
        if avg_net_pct > -abs(self.ghost_timing_cooloff_max_avg_loss_pct):
            return ""
        ghost = self.ghost_positions.get(product_id)
        reclaim_price = float(ghost.highest_bid) if ghost else 0.0
        if bid is not None and reclaim_price > 0.0 and float(bid) > reclaim_price:
            return ""
        return f"ghost_timing_cooloff_{closes}_closes_avg_{avg_net_pct:.4f}pct_reclaim_above_{reclaim_price:.12g}"

    def execution_mode(self) -> str:
        if self.current_cluster_size < self.require_cluster_size_threshold:
            return "idiosyncratic"
        return "systemic"

    def eligible_rows(self, rows: list[dict[str, Any]], ticks: dict[str, dict[str, Any]] | None = None) -> list[dict[str, Any]]:
        eligible: list[dict[str, Any]] = []
        ticks = ticks or {}
        
        mode = self.execution_mode()
        active_products = set(self.active_positions.keys())
        
        for row in rows:
            product_id = str(row.get("product_id") or "")
            if product_id in active_products:
                continue
            # Apply Bear Velocity Veto
            if product_id in self.bear_veto_products:
                continue
            if self.require_ml_survival_prob > 0.0:
                ml_prob = row.get("ml_survival_prob")
                if ml_prob is None or float(ml_prob or 0.0) < self.require_ml_survival_prob:
                    continue
            if self.require_fast_green_prob > 0.0:
                fast_green_prob = row.get("fast_green_prob")
                if fast_green_prob is None or float(fast_green_prob or 0.0) < self.require_fast_green_prob:
                    continue
            if self.require_tail_prob > 0.0:
                tail_prob = row.get("tail_prob")
                if tail_prob is None or float(tail_prob or 0.0) < self.require_tail_prob:
                    continue
            if self.require_bubble_capture_net_pct_per_hour > 0.0:
                bubble_capture = row.get("bubble_capture_net_pct_per_hour")
                if bubble_capture is None or float(bubble_capture or 0.0) < self.require_bubble_capture_net_pct_per_hour:
                    continue
            if self.reentry_blocks.get(product_id, 0) > 0:
                continue
            if not self.entry_confirmed(product_id):
                continue
            tick = ticks.get(product_id) or {}
            bid = float(tick["bid"]) if "bid" in tick else None
            if self.ghost_timing_cooloff_reason(product_id, bid=bid):
                continue
            if self.behind_target() and self.target_pressure_min_entry_edge_pct > 0.0:
                edge = float(row.get("ghost_adjusted_edge_over_hurdle_pct", row.get("edge_over_hurdle_pct")) or 0.0)
                if edge < self.target_pressure_min_entry_edge_pct and not self.live_velocity_override(row, product_id):
                    continue
            if self.behind_target() and self.target_pressure_min_live_move_bps > 0.0:
                momentum = self.live_momentum.get(product_id)
                if not isinstance(momentum, dict) or int(momentum.get("samples", 0) or 0) < 2:
                    continue
                if float(momentum.get("move_bps") or 0.0) < self.target_pressure_min_live_move_bps:
                    continue
                if int(momentum.get("live_move_streak", 0) or 0) < max(1, self.entry_confirmation_polls):
                    continue
            eligible.append(row)
            
        if mode == "systemic" and eligible:
            # ONLY take the #1 Ranked signal by volume_mult_12
            eligible.sort(key=lambda x: float(x.get("volume_mult_12") or 0.0), reverse=True)
            eligible = eligible[:1]
            
        return eligible

    def target_gap_usd(self) -> float:
        if self.target_net_pct_per_hour <= 0.0:
            return 0.0
        try:
            started = datetime.fromisoformat(str(self.target_started_at).replace("Z", "+00:00"))
        except ValueError:
            return 0.0
        if started.tzinfo is None:
            started = started.replace(tzinfo=timezone.utc)
        elapsed_hours = max(0.0, (datetime.now(timezone.utc) - started.astimezone(timezone.utc)).total_seconds() / 3600.0)
        target_usd = self.starting_cash_usd * (self.target_net_pct_per_hour / 100.0) * elapsed_hours
        return self.realized_net_usd - target_usd

    def behind_target(self) -> bool:
        return self.target_net_pct_per_hour > 0.0 and self.target_gap_usd() < 0.0

    def ghost_bias_pct(self, product_id: str) -> float:
        stats = self.ghost_stats.get(product_id)
        if not isinstance(stats, dict):
            return 0.0
        closes = int(stats.get("closes", 0) or 0)
        if closes < self.ghost_min_closes_for_bias:
            return 0.0
        avg_net_pct = float(stats.get("net_pct", 0.0) or 0.0) / max(1, closes)
        cap = self.ghost_edge_bias_cap_pct
        return max(-cap, min(cap, avg_net_pct))

    def ghost_adjusted_rows(self, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        adjusted: list[dict[str, Any]] = []
        for row in rows:
            product_id = str(row.get("product_id") or "")
            copy = dict(row)
            stats = self.ghost_stats.get(product_id) if isinstance(self.ghost_stats.get(product_id), dict) else {}
            bias = self.ghost_bias_pct(product_id)
            raw_edge = float(copy.get("edge_over_hurdle_pct") or 0.0)
            raw_score = float(copy.get("machinegun_score") or 0.0)
            copy["raw_rank"] = int(copy.get("rank") or 0)
            copy["ghost_closes"] = int(stats.get("closes", 0) or 0) if isinstance(stats, dict) else 0
            copy["ghost_net_pct"] = round(float(stats.get("net_pct", 0.0) or 0.0), 6) if isinstance(stats, dict) else 0.0
            copy["ghost_edge_bias_pct"] = round(bias, 6)
            copy["ghost_timing_cooloff_reason"] = self.ghost_timing_cooloff_reason(product_id)
            copy["ghost_adjusted_edge_over_hurdle_pct"] = round(raw_edge + bias, 6)
            copy["ghost_adjusted_machinegun_score"] = round(raw_score + (bias * 3.0), 6)
            adjusted.append(copy)
        adjusted.sort(
            key=lambda row: (
                float(row.get("ghost_adjusted_machinegun_score") or 0.0),
                float(row.get("ghost_adjusted_edge_over_hurdle_pct") or 0.0),
            ),
            reverse=True,
        )
        for idx, row in enumerate(adjusted, start=1):
            row["rank"] = idx
        return adjusted

    def ghost_mark(self, ghost: GhostPosition, tick: dict[str, Any]) -> dict[str, Any]:
        bid = float(tick["bid"])
        ghost.highest_bid = max(ghost.highest_bid, bid)
        proceeds = ghost.quantity * bid
        exit_fee = proceeds * self.fee_rate()
        net = proceeds - exit_fee - ghost.cost_usd
        gross = (bid - ghost.entry_price) * ghost.quantity
        mfe_gross_pnl = (ghost.highest_bid - ghost.entry_price) * ghost.quantity
        mfe_gross_pct = ((ghost.highest_bid - ghost.entry_price) / ghost.entry_price) * 100.0 if ghost.entry_price else 0.0
        trail_stop = ghost.highest_bid * (1.0 - (ghost.trail_giveback_pct / 100.0))
        loss_pct = ((bid - ghost.entry_price) / ghost.entry_price) * 100.0 if ghost.entry_price else 0.0
        net_pct_on_cost = (net / ghost.cost_usd) * 100.0 if ghost.cost_usd else 0.0
        ghost.max_net_pnl = max(ghost.max_net_pnl, net)
        ghost.max_net_pct_on_cost = max(ghost.max_net_pct_on_cost, net_pct_on_cost)
        gross_mfe_capture_pct = (gross / mfe_gross_pnl) * 100.0 if mfe_gross_pnl > 0.0 else None
        net_mfe_capture_pct = (net / ghost.max_net_pnl) * 100.0 if ghost.max_net_pnl > 0.0 else None
        return {
            "product_id": ghost.product_id,
            "playbook": ghost.playbook,
            "bid": bid,
            "entry_price": ghost.entry_price,
            "highest_bid": ghost.highest_bid,
            "trail_stop": trail_stop,
            "gross_pnl": gross,
            "mfe_gross_pnl": mfe_gross_pnl,
            "mfe_gross_pct": mfe_gross_pct,
            "gross_mfe_capture_pct": gross_mfe_capture_pct,
            "entry_fee": ghost.entry_fee,
            "exit_fee": exit_fee,
            "roundtrip_fee": ghost.entry_fee + exit_fee,
            "net_pnl": net,
            "net_pct_on_cost": net_pct_on_cost,
            "max_net_pnl": ghost.max_net_pnl,
            "max_net_pct_on_cost": ghost.max_net_pct_on_cost,
            "net_mfe_capture_pct": net_mfe_capture_pct,
            "loss_pct": loss_pct,
            "entry_ml_survival_prob": ghost.entry_ml_survival_prob,
            "entry_fast_green_prob": ghost.entry_fast_green_prob,
            "entry_tail_prob": ghost.entry_tail_prob,
            "entry_bubble_capture_net_pct_per_hour": ghost.entry_bubble_capture_net_pct_per_hour,
        }

    def update_ghost_stats(self, product_id: str, mark: dict[str, Any]) -> None:
        net_pct = float(mark["net_pct_on_cost"])
        stats = self.ghost_stats.setdefault(
            product_id,
            {
                "closes": 0,
                "wins": 0,
                "losses": 0,
                "net_pct": 0.0,
                "best_pct": None,
                "worst_pct": None,
                "mfe_positive_closes": 0,
                "gross_mfe_capture_pct_sum": 0.0,
                "net_mfe_capture_pct_sum": 0.0,
                "gross_mfe_capture_ge_20_count": 0,
                "net_mfe_capture_ge_20_count": 0,
            },
        )
        stats["closes"] = int(stats.get("closes", 0) or 0) + 1
        stats["wins"] = int(stats.get("wins", 0) or 0) + (1 if net_pct > 0.0 else 0)
        stats["losses"] = int(stats.get("losses", 0) or 0) + (0 if net_pct > 0.0 else 1)
        stats["net_pct"] = round(float(stats.get("net_pct", 0.0) or 0.0) + net_pct, 6)
        best = stats.get("best_pct")
        worst = stats.get("worst_pct")
        stats["best_pct"] = round(net_pct if best is None else max(float(best), net_pct), 6)
        stats["worst_pct"] = round(net_pct if worst is None else min(float(worst), net_pct), 6)
        gross_capture = mark.get("gross_mfe_capture_pct")
        net_capture = mark.get("net_mfe_capture_pct")
        if gross_capture is not None:
            stats["mfe_positive_closes"] = int(stats.get("mfe_positive_closes", 0) or 0) + 1
            stats["gross_mfe_capture_pct_sum"] = round(
                float(stats.get("gross_mfe_capture_pct_sum", 0.0) or 0.0) + float(gross_capture),
                6,
            )
            stats["gross_mfe_capture_ge_20_count"] = int(stats.get("gross_mfe_capture_ge_20_count", 0) or 0) + (
                1 if float(gross_capture) >= 20.0 else 0
            )
        if net_capture is not None:
            stats["net_mfe_capture_pct_sum"] = round(
                float(stats.get("net_mfe_capture_pct_sum", 0.0) or 0.0) + float(net_capture),
                6,
            )
            stats["net_mfe_capture_ge_20_count"] = int(stats.get("net_mfe_capture_ge_20_count", 0) or 0) + (
                1 if float(net_capture) >= 20.0 else 0
            )

    def update_ghost_tournament(
        self,
        rows: list[dict[str, Any]],
        ticks: dict[str, dict[str, Any]],
        *,
        event_path: Path,
    ) -> None:
        if self.ghost_top_n <= 0:
            return
        top_rows = rows[: self.ghost_top_n]
        active_products = {str(row.get("product_id") or "") for row in top_rows}
        for product_id in list(self.ghost_positions):
            if product_id not in active_products:
                self.ghost_positions.pop(product_id, None)
        for row in top_rows:
            product_id = str(row.get("product_id") or "")
            tick = ticks.get(product_id)
            if not product_id or not tick:
                continue
            ghost = self.ghost_positions.get(product_id)
            if ghost is None:
                ask = float(tick["ask"])
                cost_usd = 1.0
                entry_fee = cost_usd * self.fee_rate()
                quantity = (cost_usd - entry_fee) / ask if ask else 0.0
                if quantity <= 0.0:
                    continue
                ghost = GhostPosition(
                    product_id=product_id,
                    playbook=str(row.get("playbook") or ""),
                    entry_price=ask,
                    quantity=quantity,
                    cost_usd=cost_usd,
                    entry_fee=entry_fee,
                    opened_at=utc_now_iso(),
                    highest_bid=float(tick["bid"]),
                    trail_giveback_pct=max(0.25, float(row.get("trail_giveback_pct") or 0.25)),
                    entry_edge_over_hurdle_pct=float(row.get("edge_over_hurdle_pct") or 0.0),
                    max_net_pnl=-entry_fee,
                    max_net_pct_on_cost=(-entry_fee / cost_usd) * 100.0 if cost_usd else 0.0,
                    entry_ml_survival_prob=to_float(row.get("ml_survival_prob")),
                    entry_fast_green_prob=to_float(row.get("fast_green_prob")),
                    entry_tail_prob=to_float(row.get("tail_prob")),
                    entry_bubble_capture_net_pct_per_hour=to_float(row.get("bubble_capture_net_pct_per_hour")),
                )
                self.ghost_positions[product_id] = ghost
                append_jsonl(
                    event_path,
                    {
                        "ts_utc": utc_now_iso(),
                        "action": "open_machinegun_ghost",
                        "product_id": product_id,
                        "playbook": ghost.playbook,
                        "entry_price": round(ask, 12),
                        "quantity": round(quantity, 12),
                        "entry_fee": round(entry_fee, 6),
                        "fee_bps_per_side": round(self.taker_fee_bps, 4),
                        "entry_ml_survival_prob": round(ghost.entry_ml_survival_prob, 6),
                        "entry_fast_green_prob": round(ghost.entry_fast_green_prob, 6),
                        "entry_tail_prob": round(ghost.entry_tail_prob, 6),
                        "entry_bubble_capture_net_pct_per_hour": round(ghost.entry_bubble_capture_net_pct_per_hour, 6),
                    },
                )
                continue
            mark = self.ghost_mark(ghost, tick)
            exit_reason = ""
            max_net_pnl = float(mark.get("max_net_pnl") or 0.0)
            current_net_pnl = float(mark.get("net_pnl") or 0.0)
            profit_lock_floor = max(
                self.min_profit_to_trail_usd,
                max_net_pnl * (self.profit_lock_retention_pct / 100.0),
            )
            
            if not exit_reason:
                if float(mark["bid"]) <= float(mark["trail_stop"]) and float(mark["net_pnl"]) > 0.0:
                    exit_reason = "ghost_profit_trail"
                elif max_net_pnl >= self.min_profit_to_trail_usd and current_net_pnl <= profit_lock_floor:
                    exit_reason = "ghost_fee_paid_profit_lock"
                elif (
                    self.manifest_positive_within_seconds > 0.0
                    and self.age_seconds(ghost.opened_at) >= self.manifest_positive_within_seconds
                    and float(mark.get("max_net_pct_on_cost") or 0.0) < self.manifest_positive_min_net_pct
                ):
                    exit_reason = "ghost_manifest_positive_timeout"
                elif float(mark["net_pct_on_cost"]) <= -abs(self.max_loss_pct):
                    exit_reason = "ghost_emergency_net_loss"
            if not exit_reason:
                continue
            self.update_ghost_stats(product_id, mark)
            append_jsonl(
                event_path,
                {
                    "ts_utc": utc_now_iso(),
                    "action": "close_machinegun_ghost",
                    "exit_reason": exit_reason,
                    "product_id": product_id,
                    "playbook": ghost.playbook,
                    "entry_price": round(ghost.entry_price, 12),
                    "exit_price": round(float(mark["bid"]), 12),
                    "highest_bid": round(float(mark["highest_bid"]), 12),
                    "gross_pnl": round(float(mark["gross_pnl"]), 6),
                    "mfe_gross_pnl": round(float(mark.get("mfe_gross_pnl") or 0.0), 6),
                    "mfe_gross_pct": round(float(mark.get("mfe_gross_pct") or 0.0), 6),
                    "gross_mfe_capture_pct": round_optional(mark.get("gross_mfe_capture_pct")),
                    "fee": round(float(mark["roundtrip_fee"]), 6),
                    "net_pnl": round(float(mark["net_pnl"]), 6),
                    "net_pct_on_cost": round(float(mark["net_pct_on_cost"]), 6),
                    "max_net_pnl": round(float(mark.get("max_net_pnl") or 0.0), 6),
                    "max_net_pct_on_cost": round(float(mark.get("max_net_pct_on_cost") or 0.0), 6),
                    "net_mfe_capture_pct": round_optional(mark.get("net_mfe_capture_pct")),
                    "entry_ml_survival_prob": round(ghost.entry_ml_survival_prob, 6),
                    "entry_fast_green_prob": round(ghost.entry_fast_green_prob, 6),
                    "entry_tail_prob": round(ghost.entry_tail_prob, 6),
                    "entry_bubble_capture_net_pct_per_hour": round(ghost.entry_bubble_capture_net_pct_per_hour, 6),
                    "fee_bps_per_side": round(self.taker_fee_bps, 4),
                },
            )
            self.ghost_positions.pop(product_id, None)

    def maybe_close_positions(self, ticks: dict[str, dict[str, Any]], *, event_path: Path) -> None:
        for product_id in list(self.active_positions.keys()):
            pos = self.active_positions.get(product_id)
            if not pos:
                continue
            tick = ticks.get(product_id)
            if not tick:
                continue
                
            mark = self.mark_position(pos, tick)
            if not mark:
                continue
            
            # IN-POSITION SPREAD MONITOR: Exit immediately if spread collapses
            spread_bps = mark.get("spread_bps")
            exit_reason = ""
            if spread_bps is not None and float(spread_bps) < self.min_in_position_spread_bps:
                exit_reason = "coinbase_spread_collapse_exit"
            
            max_net_pnl = float(mark.get("max_net_pnl") or 0.0)
            current_net_pnl = float(mark.get("net_pnl") or 0.0)
            profit_lock_floor = max(
                self.min_profit_to_trail_usd,
                max_net_pnl * (self.profit_lock_retention_pct / 100.0),
            )
            if not exit_reason and float(mark["bid"]) <= float(mark["trail_stop"]) and float(mark["net_pnl"]) >= self.min_profit_to_trail_usd:
                exit_reason = "profit_trail"
            elif (
                self.behind_target()
                and self.target_pressure_min_entry_edge_pct > 0.0
                and pos.entry_edge_over_hurdle_pct < self.target_pressure_min_entry_edge_pct
                and current_net_pnl >= self.min_profit_to_trail_usd
            ):
                exit_reason = "target_pressure_profit_bank"
            elif (
                self.behind_target()
                and self.target_pressure_min_entry_edge_pct > 0.0
                and pos.entry_edge_over_hurdle_pct < self.target_pressure_min_entry_edge_pct
                and float(mark["bid"]) <= float(mark["trail_stop"])
            ):
                exit_reason = "target_pressure_weak_edge_trail_exit"
            elif max_net_pnl >= self.min_profit_to_trail_usd and current_net_pnl <= profit_lock_floor:
                exit_reason = "fee_paid_profit_lock"
            elif (
                self.manifest_positive_within_seconds > 0.0
                and self.age_seconds(pos.opened_at) >= self.manifest_positive_within_seconds
                and float(mark.get("max_net_pct_on_cost") or 0.0) < self.manifest_positive_min_net_pct
            ):
                exit_reason = "manifest_positive_timeout"
            elif (
                self.behind_target()
                and self.target_pressure_min_live_move_bps > 0.0
                and float(mark["net_pct_on_cost"]) <= -abs(self.target_pressure_exit_net_loss_pct)
                and float(self.live_momentum.get(product_id, {}).get("move_bps") or 0.0) < 0.0
            ):
                exit_reason = "target_pressure_live_momentum_failed_exit"
            elif (
                self.behind_target()
                and self.target_pressure_min_entry_edge_pct > 0.0
                and pos.entry_edge_over_hurdle_pct < self.target_pressure_min_entry_edge_pct
                and float(mark["net_pct_on_cost"]) <= -abs(self.target_pressure_exit_net_loss_pct)
            ):
                exit_reason = "target_pressure_weak_edge_loss_exit"
            elif (
                self.behind_target()
                and self.ghost_timing_cooloff_reason(product_id, bid=float(mark["bid"]))
                and float(mark["net_pct_on_cost"]) <= -abs(self.target_pressure_exit_net_loss_pct)
            ):
                exit_reason = "target_pressure_timing_cooloff_exit"
            elif float(mark["loss_pct"]) <= -abs(self.max_loss_pct):
                exit_reason = "emergency_failed_breakout"
            elif float(mark["net_pct_on_cost"]) <= -abs(self.max_loss_pct):
                exit_reason = "emergency_net_loss"
                
            if exit_reason:
                self.close_position(pos, mark, event_path=event_path, exit_reason=exit_reason)
                if exit_reason.startswith("emergency") or exit_reason.startswith("target_pressure"):
                    self.block_reentry(product_id, reason=exit_reason, event_path=event_path)
            else:
                self.last_action = "hold_machinegun_shadow"

    def maybe_close_position(self, tick: dict[str, Any], *, event_path: Path) -> None:
        if self.position is None:
            self.sync_primary_position()
        if self.position is None:
            return
        self.maybe_close_positions({self.position.product_id: tick}, event_path=event_path)

    def rotation_required_pct(self) -> float:
        fee_pct = self.taker_fee_bps / 100.0
        return (fee_pct * 2.0) + max(0.0, self.rotation_buffer_pct)

    def evaluate_rotation(
        self,
        product_id: str | list[dict[str, Any]],
        rows: list[dict[str, Any]] | dict[str, Any],
        current_mark: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if current_mark is None:
            current_mark = rows if isinstance(rows, dict) else {}
            rows = product_id if isinstance(product_id, list) else []
            if self.position is None:
                self.sync_primary_position()
            product_id = self.position.product_id if self.position is not None else ""
        product_id = str(product_id)
        rows = rows if isinstance(rows, list) else []
        pos = self.active_positions.get(product_id)
        if not pos:
            return {"decision": "no_position"}
        
        active_products = set(self.active_positions.keys())
        current_row = next((row for row in rows if str(row.get("product_id") or "") == product_id), None)
        # Challenger must NOT be any of the currently active positions
        challenger = next((row for row in rows if str(row.get("product_id") or "") not in active_products), None)
        
        current_edge = (
            float(current_row.get("ghost_adjusted_edge_over_hurdle_pct", current_row.get("edge_over_hurdle_pct")) or 0.0)
            if current_row
            else pos.entry_edge_over_hurdle_pct
        )
        challenger_edge = (
            float(challenger.get("ghost_adjusted_edge_over_hurdle_pct", challenger.get("edge_over_hurdle_pct")) or 0.0)
            if challenger
            else 0.0
        )
        current_raw_edge = float(current_row.get("edge_over_hurdle_pct") or 0.0) if current_row else pos.entry_edge_over_hurdle_pct
        challenger_raw_edge = float(challenger.get("edge_over_hurdle_pct") or 0.0) if challenger else 0.0
        edge_advantage = challenger_edge - current_edge
        required = self.rotation_required_pct()
        current_net_pnl = float(current_mark.get("net_pnl") or 0.0)
        decision = {
            "decision": "hold_no_challenger",
            "current_product_id": product_id,
            "current_rank": int(current_row.get("rank") or 0) if current_row else None,
            "current_edge_over_hurdle_pct": round(current_edge, 6),
            "current_raw_edge_over_hurdle_pct": round(current_raw_edge, 6),
            "current_ghost_edge_bias_pct": round(float(current_row.get("ghost_edge_bias_pct") or 0.0), 6) if current_row else 0.0,
            "current_net_pnl": round(current_net_pnl, 6),
            "current_net_pct_on_cost": round(float(current_mark.get("net_pct_on_cost") or 0.0), 6),
            "current_distance_to_trail_pct": round(float(current_mark.get("distance_to_trail_pct") or 0.0), 6),
            "challenger_product_id": str(challenger.get("product_id") or "") if challenger else "",
            "challenger_rank": int(challenger.get("rank") or 0) if challenger else None,
            "challenger_playbook": str(challenger.get("playbook") or "") if challenger else "",
            "challenger_edge_over_hurdle_pct": round(challenger_edge, 6),
            "challenger_raw_edge_over_hurdle_pct": round(challenger_raw_edge, 6),
            "challenger_ghost_edge_bias_pct": round(float(challenger.get("ghost_edge_bias_pct") or 0.0), 6) if challenger else 0.0,
            "edge_advantage_pct": round(edge_advantage, 6),
            "rotation_required_pct": round(required, 6),
            "rotation_buffer_pct": round(self.rotation_buffer_pct, 6),
            "fee_bps_per_side": round(self.taker_fee_bps, 4),
        }
        if self.behind_target() and current_net_pnl < self.min_profit_to_trail_usd:
            decision["decision"] = "hold_current_red_no_rotation"
        elif challenger and edge_advantage >= required:
            decision["decision"] = "rotate_to_challenger"
        elif challenger:
            decision["decision"] = "hold_challenger_not_fee_clear"
        self.last_decision = decision
        return decision


def load_json(path: Path) -> Any:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}


def save_state(path: Path, engine: MachinegunShadowEngine, runner: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "updated_at": utc_now_iso(),
        "state": engine.snapshot(),
        "runner": runner,
    }
    path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")


def parse_iso(raw: Any) -> datetime | None:
    if not raw:
        return None
    try:
        return datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    except ValueError:
        return None


def refresh_boards(*, refresh_pulse: bool, runner: dict[str, Any], pulse_refresh_seconds: float, taker_fee_bps: float) -> None:
    should_refresh_pulse = False
    if refresh_pulse:
        last = parse_iso(runner.get("last_pulse_refresh_at"))
        if last is None:
            should_refresh_pulse = True
        else:
            should_refresh_pulse = (datetime.now(timezone.utc) - last).total_seconds() >= max(60.0, float(pulse_refresh_seconds))
    if should_refresh_pulse:
        try:
            subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "scripts" / "build_coinbase_spot_pulse_board.py"),
                    "--quote-currencies",
                    "USD,USDC",
                    "--top-products",
                    "180",
                    "--top-per-quote",
                    "90",
                    "--min-quote-volume-usd",
                    "0",
                    "--hours",
                    "3",
                    "--max-candle-fetches",
                    "140",
                    "--cache-ttl-seconds",
                    "60",
                    "--request-sleep-seconds",
                    "0.02",
                ],
                cwd=ROOT,
                check=False,
                timeout=60,
            )
        except subprocess.TimeoutExpired:
            runner["last_pulse_refresh_timeout_at"] = utc_now_iso()
        runner["last_pulse_refresh_at"] = utc_now_iso()
    try:
        subprocess.run(
            [
                sys.executable,
                str(ROOT / "scripts" / "build_coinbase_spot_live_radar.py"),
                "--all-spot-quotes",
                "--direct-usd-stable-only",
                "--max-products",
                "500",
                "--chunk-size",
                "75",
                "--keep-seconds",
                "3900",
                "--max-spread-bps",
                "100",
            ],
            cwd=ROOT,
            check=False,
            timeout=20,
        )
        subprocess.run(
            [sys.executable, str(ROOT / "scripts" / "build_coinbase_spot_dissonance_board.py")],
            cwd=ROOT,
            check=False,
            timeout=20,
        )
        subprocess.run(
            [sys.executable, str(ROOT / "scripts" / "build_coinbase_live_foundry_bridge.py")],
            cwd=ROOT,
            check=False,
            timeout=20,
        )
        subprocess.run(
            [
                sys.executable,
                str(ROOT / "scripts" / "build_coinbase_spot_fee_hurdle_board.py"),
                "--taker-fee-bps",
                str(float(taker_fee_bps)),
            ],
            cwd=ROOT,
            check=False,
            timeout=20,
        )
        subprocess.run(
            [sys.executable, str(ROOT / "scripts" / "build_coinbase_spot_machinegun_strategy_board.py")],
            cwd=ROOT,
            check=False,
            timeout=20,
        )
    except subprocess.TimeoutExpired:
        runner["last_strategy_refresh_timeout_at"] = utc_now_iso()


def candidate_rows() -> list[dict[str, Any]]:
    payload = load_json(STRATEGY_BOARD_PATH)
    rows = [row for row in (payload.get("rows") or []) if str(row.get("playbook") or "") != "watch_only"]
    rows.sort(key=lambda row: float(row.get("machinegun_score") or 0.0), reverse=True)
    return rows


def fetch_coinbase_ticks(client: CoinbaseAdvancedClient, product_ids: list[str]) -> dict[str, dict[str, Any]]:
    product_ids = [pid for pid in dict.fromkeys(product_ids) if pid]
    if not product_ids:
        return {}
    payload = client.best_bid_ask(product_ids)
    ticks: dict[str, dict[str, Any]] = {}
    now_msc = int(time.time() * 1000)
    now_sec = int(now_msc // 1000)
    for book in payload.get("pricebooks") or []:
        product_id = str(book.get("product_id") or book.get("productId") or "")
        bids = book.get("bids") or []
        asks = book.get("asks") or []
        if not product_id or not bids or not asks:
            continue
        ticks[product_id] = {
            "time": now_sec,
            "time_msc": now_msc,
            "bid": float(bids[0]["price"]),
            "ask": float(asks[0]["price"]),
        }
    return ticks


def compact_candidate(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "rank": int(row.get("rank") or 0),
        "raw_rank": int(row.get("raw_rank") or row.get("rank") or 0),
        "product_id": str(row.get("product_id") or ""),
        "playbook": str(row.get("playbook") or ""),
        "hurdle_state": str(row.get("hurdle_state") or ""),
        "machinegun_score": round(float(row.get("machinegun_score") or 0.0), 6),
        "ghost_adjusted_machinegun_score": round(float(row.get("ghost_adjusted_machinegun_score", row.get("machinegun_score")) or 0.0), 6),
        "edge_over_hurdle_pct": round(float(row.get("edge_over_hurdle_pct") or 0.0), 6),
        "ghost_adjusted_edge_over_hurdle_pct": round(
            float(row.get("ghost_adjusted_edge_over_hurdle_pct", row.get("edge_over_hurdle_pct")) or 0.0), 6
        ),
        "ghost_edge_bias_pct": round(float(row.get("ghost_edge_bias_pct") or 0.0), 6),
        "ghost_closes": int(row.get("ghost_closes") or 0),
        "ghost_net_pct": round(float(row.get("ghost_net_pct") or 0.0), 6),
        "ret_15m_pct": round(float(row.get("ret_15m_pct") or 0.0), 6),
        "ret_60m_pct": round(float(row.get("ret_60m_pct") or 0.0), 6),
        "spread_bps": round(float(row.get("spread_bps") or 0.0), 6),
        "trail_giveback_pct": round(float(row.get("trail_giveback_pct") or 0.0), 6),
        "live_move_bps": row.get("live_move_bps"),
        "live_move_samples": row.get("live_move_samples"),
        "live_move_streak": row.get("live_move_streak"),
        "live_override_streak": row.get("live_override_streak"),
        "ml_survival_prob": row.get("ml_survival_prob"),
        "ml_gate_verdict": row.get("ml_gate_verdict"),
        "ml_score_basis": row.get("ml_score_basis"),
        "fast_green_prob": row.get("fast_green_prob"),
        "fast_green_verdict": row.get("fast_green_verdict"),
        "fast_green_label": row.get("fast_green_label"),
        "fast_green_score_basis": row.get("fast_green_score_basis"),
        "bubble_capture_net_pct_per_hour": row.get("bubble_capture_net_pct_per_hour"),
        "bubble_capture_avg_net_pct": row.get("bubble_capture_avg_net_pct"),
        "bubble_capture_trades": row.get("bubble_capture_trades"),
        "bubble_capture_win_rate_pct": row.get("bubble_capture_win_rate_pct"),
        "bubble_capture_verdict": row.get("bubble_capture_verdict"),
        "bubble_capture_basis": row.get("bubble_capture_basis"),
    }


def append_opportunity_tape(
    path: Path,
    *,
    engine: MachinegunShadowEngine,
    rows: list[dict[str, Any]],
    current_marks: dict[str, Any],
    decisions: list[dict[str, Any]],
) -> None:
    record: dict[str, Any] = {
        "ts_utc": utc_now_iso(),
        "action": "machinegun_opportunity_scan",
        "fee_bps_per_side": round(engine.taker_fee_bps, 4),
        "fee_source": engine.fee_source,
        "fee_tier": engine.fee_tier,
        "cash_usd": round(engine.cash_usd, 6),
        "realized_net_usd": round(engine.realized_net_usd, 6),
        "realized_closes": engine.realized_closes,
        "current_cluster_size": engine.current_cluster_size,
        "execution_mode": engine.execution_mode(),
        "decisions": decisions,
        "top_candidates": [compact_candidate(row) for row in rows[:12]],
    }
    if current_marks:
        record["current_positions"] = []
        for product_id, current_mark in current_marks.items():
            record["current_positions"].append({
                "product_id": str(current_mark.get("product_id") or ""),
                "playbook": str(current_mark.get("playbook") or ""),
                "bid": round(float(current_mark.get("bid") or 0.0), 12),
                "entry_price": round(float(current_mark.get("entry_price") or 0.0), 12),
                "highest_bid": round(float(current_mark.get("highest_bid") or 0.0), 12),
                "trail_stop": round(float(current_mark.get("trail_stop") or 0.0), 12),
                "distance_to_trail_pct": round(float(current_mark.get("distance_to_trail_pct") or 0.0), 6),
                "gross_pnl": round(float(current_mark.get("gross_pnl") or 0.0), 6),
                "entry_fee": round(float(current_mark.get("entry_fee") or 0.0), 6),
                "exit_fee": round(float(current_mark.get("exit_fee") or 0.0), 6),
                "roundtrip_fee": round(float(current_mark.get("roundtrip_fee") or 0.0), 6),
                "net_pnl": round(float(current_mark.get("net_pnl") or 0.0), 6),
                "net_pct_on_cost": round(float(current_mark.get("net_pct_on_cost") or 0.0), 6),
                "loss_pct": round(float(current_mark.get("loss_pct") or 0.0), 6),
            })
    if engine.ghost_stats:
        record["ghost_leaders"] = sorted(
            [
                {
                    "product_id": product,
                    "closes": int(stats.get("closes", 0) or 0),
                    "wins": int(stats.get("wins", 0) or 0),
                    "losses": int(stats.get("losses", 0) or 0),
                    "net_pct": round(float(stats.get("net_pct", 0.0) or 0.0), 6),
                    "best_pct": stats.get("best_pct"),
                    "worst_pct": stats.get("worst_pct"),
                }
                for product, stats in engine.ghost_stats.items()
            ],
            key=lambda row: float(row["net_pct"]),
            reverse=True,
        )[:8]
    append_jsonl(path, record)


def run_once(
    client: CoinbaseAdvancedClient,
    engine: MachinegunShadowEngine,
    *,
    state_path: Path,
    event_path: Path,
    runner: dict[str, Any],
    refresh_pulse: bool,
    pulse_refresh_seconds: float,
    opportunity_tape_path: Path,
) -> None:
    refresh_boards(
        refresh_pulse=refresh_pulse,
        runner=runner,
        pulse_refresh_seconds=pulse_refresh_seconds,
        taker_fee_bps=engine.taker_fee_bps,
    )
    rows = candidate_rows()
    rows = engine.ghost_adjusted_rows(rows)
    engine.current_cluster_size = len(rows)  # CRITICAL: compute cluster size for dual-mode execution
    engine.refresh_candidate_streaks(rows)
    tracked_products = [str(row.get("product_id") or "") for row in rows]
    ghost_products = [str(row.get("product_id") or "") for row in rows[: engine.ghost_top_n]]
    candidate_ticks = fetch_coinbase_ticks(client, tracked_products) if tracked_products else {}
    ghost_ticks = {product: candidate_ticks[product] for product in ghost_products if product in candidate_ticks}
    engine.update_live_momentum(candidate_ticks)
    for row in rows:
        product_id = str(row.get("product_id") or "")
        momentum = engine.live_momentum.get(product_id)
        if isinstance(momentum, dict):
            row["live_move_bps"] = round(float(momentum.get("move_bps") or 0.0), 6)
            row["live_move_samples"] = int(momentum.get("samples", 0) or 0)
            row["live_move_streak"] = int(momentum.get("live_move_streak", 0) or 0)
            row["live_override_streak"] = int(momentum.get("live_override_streak", 0) or 0)
            
    engine.update_ghost_tournament(rows, ghost_ticks, event_path=event_path)
    
    current_marks: dict[str, Any] = {}
    decisions: list[dict[str, Any]] = []
    
    # 0. Load Bear Velocity Vetoes
    bear_payload = load_json(BEAR_VELOCITY_PATH)
    if bear_payload:
        engine.bear_veto_products = {str(r.get("base_currency", "") + "-" + r.get("quote_currency", "")) for r in bear_payload.get("direct_dump_rows", [])}
        engine.bear_veto_products.discard("-")
        
    # 1. Check for exits on all active positions
    engine.maybe_close_positions(candidate_ticks, event_path=event_path)
    
    # 2. Evaluate rotation for remaining positions
    for product_id in list(engine.active_positions.keys()):
        pos = engine.active_positions[product_id]
        tick = candidate_ticks.get(product_id) or fetch_coinbase_tick(client, product_id)
        mark = engine.mark_position(pos, tick)
        current_marks[product_id] = mark
        
        eligible = engine.eligible_rows(rows, candidate_ticks)
        decision = engine.evaluate_rotation(product_id, eligible, mark)
        decisions.append(decision)
        
        if decision.get("decision") == "rotate_to_challenger":
            challenger_id = str(decision.get("challenger_product_id") or "")
            challenger = next((row for row in rows if str(row.get("product_id") or "") == challenger_id), None)
            if challenger:
                challenger_tick = candidate_ticks.get(challenger_id) or fetch_coinbase_tick(client, challenger_id)
                engine.close_position(pos, mark, event_path=event_path, exit_reason="fee_cleared_rotation")
                engine.open_position(challenger, challenger_tick, event_path=event_path)
                
    # 3. Open new positions if we have capacity
    mode = engine.execution_mode()
    max_pos = engine.idiosyncratic_max_positions if mode == "idiosyncratic" else engine.systemic_max_positions
    
    while len(engine.active_positions) < max_pos:
        eligible = engine.eligible_rows(rows, candidate_ticks)
        if eligible:
            top = eligible[0]
            top_id = str(top.get("product_id") or "")
            decision = {
                "decision": "open_top_candidate",
                "challenger_product_id": top_id,
                "challenger_rank": int(top.get("rank") or 0),
                "challenger_playbook": str(top.get("playbook") or ""),
                "challenger_edge_over_hurdle_pct": round(float(top.get("edge_over_hurdle_pct") or 0.0), 6),
                "fee_bps_per_side": round(engine.taker_fee_bps, 4),
            }
            decisions.append(decision)
            tick = candidate_ticks.get(top_id) or fetch_coinbase_tick(client, top_id)
            engine.open_position(top, tick, event_path=event_path)
        else:
            if not engine.active_positions:
                engine.last_action = "idle_no_eligible_fee_hurdle_candidate"
                decisions.append({"decision": engine.last_action})
                append_jsonl(event_path, {"ts_utc": utc_now_iso(), "action": engine.last_action})
            break

    append_opportunity_tape(
        opportunity_tape_path,
        engine=engine,
        rows=rows,
        current_marks=current_marks,
        decisions=decisions,
    )
    runner["heartbeat_at"] = utc_now_iso()
    runner["last_successful_run_at"] = runner["heartbeat_at"]
    runner["current_cluster_size"] = engine.current_cluster_size  # Track cluster size in runner state
    runner["consecutive_exceptions"] = 0
    runner["last_exception_at"] = None
    runner["last_exception_type"] = ""
    runner["last_exception_message"] = ""
    save_state(state_path, engine, runner)
    engine.mfe_tracker.save(MFE_TRACKER_PATH)
    engine.tick_reentry_blocks()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Coinbase spot machinegun shadow runner.")
    parser.add_argument("--starting-cash", type=float, default=48.0)
    parser.add_argument("--deploy-pct", type=float, default=0.8)
    parser.add_argument("--taker-fee-bps", type=float, default=120.0)
    parser.add_argument("--min-quote-usd", type=float, default=5.0)
    parser.add_argument("--max-loss-pct", type=float, default=4.0)
    parser.add_argument("--min-profit-to-trail-usd", type=float, default=0.01)
    parser.add_argument("--rotation-buffer-pct", type=float, default=0.5)
    parser.add_argument("--reentry-cooldown-polls", type=int, default=3)
    parser.add_argument("--ghost-top-n", type=int, default=8)
    parser.add_argument("--ghost-min-closes-for-bias", type=int, default=3)
    parser.add_argument("--ghost-edge-bias_cap_pct", type=float, default=2.0)
    parser.add_argument("--profit-lock-retention-pct", type=float, default=85.0)
    parser.add_argument("--target-net-pct-per-hour", type=float, default=5.0)
    parser.add_argument("--ghost-timing-cooloff-min-closes", type=int, default=3)
    parser.add_argument("--ghost-timing-cooloff-max-avg-loss-pct", type=float, default=3.0)
    parser.add_argument("--target-pressure-exit-net-loss-pct", type=float, default=2.0)
    parser.add_argument("--entry-confirmation-polls", type=int, default=2)
    parser.add_argument("--target-pressure-min-entry-edge-pct", type=float, default=3.0)
    parser.add_argument("--target-pressure-min-live-move-bps", type=float, default=25.0)
    parser.add_argument("--target-pressure-live-override-bps", type=float, default=50.0)
    parser.add_argument("--target-pressure-live-override-min-edge-pct", type=float, default=2.5)
    parser.add_argument("--require-ml-survival-prob", type=float, default=0.80)
    parser.add_argument("--require-fast-green-prob", type=float, default=0.90)
    parser.add_argument("--require-tail-prob", type=float, default=0.80)
    parser.add_argument("--require-bubble-capture-net-pct-per-hour", type=float, default=0.0)
    parser.add_argument("--manifest-positive-within-seconds", type=float, default=0.0)
    parser.add_argument("--manifest-positive-min-net-pct", type=float, default=0.0)
    # Cluster size filter + dual-mode execution (Gemini's "Solitary Mycelium" filter)
    parser.add_argument("--require-cluster-size-threshold", type=int, default=20,
                        help="Cluster size threshold for dual-mode execution (default: 20)")
    parser.add_argument("--systemic-max-positions", type=int, default=1,
                        help="Max positions in systemic mode (cluster >= threshold, default: 1)")
    parser.add_argument("--idiosyncratic-max-positions", type=int, default=3,
                        help="Max positions in idiosyncratic mode (cluster < threshold, default: 3)")
    parser.add_argument("--systemic-deploy-pct", type=float, default=0.2,
                        help="Deploy pct in systemic mode (default: 0.2)")
    parser.add_argument("--idiosyncratic-deploy-pct", type=float, default=0.8,
                        help="Deploy pct in idiosyncratic mode (default: 0.8)")
    parser.add_argument("--poll-seconds", type=float, default=20.0)
    parser.add_argument("--refresh-pulse", action="store_true")
    parser.add_argument("--pulse-refresh-seconds", type=float, default=300.0)
    parser.add_argument("--fresh-start", action="store_true")
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--state-path", default=str(DEFAULT_STATE_PATH))
    parser.add_argument("--event-path", default=str(DEFAULT_EVENT_PATH))
    parser.add_argument("--opportunity-tape-path", default=str(DEFAULT_OPPORTUNITY_TAPE_PATH))
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    client = CoinbaseAdvancedClient()
    engine = MachinegunShadowEngine(
        starting_cash_usd=float(args.starting_cash),
        deploy_pct=float(args.deploy_pct),
        taker_fee_bps=float(args.taker_fee_bps),
        min_quote_usd=float(args.min_quote_usd),
        max_loss_pct=float(args.max_loss_pct),
        min_profit_to_trail_usd=float(args.min_profit_to_trail_usd),
        rotation_buffer_pct=float(args.rotation_buffer_pct),
        reentry_cooldown_polls=int(args.reentry_cooldown_polls),
        ghost_top_n=int(args.ghost_top_n),
        ghost_min_closes_for_bias=int(args.ghost_min_closes_for_bias),
        ghost_edge_bias_cap_pct=float(args.ghost_edge_bias_cap_pct),
        profit_lock_retention_pct=float(args.profit_lock_retention_pct),
        target_net_pct_per_hour=float(args.target_net_pct_per_hour),
        ghost_timing_cooloff_min_closes=int(args.ghost_timing_cooloff_min_closes),
        ghost_timing_cooloff_max_avg_loss_pct=float(args.ghost_timing_cooloff_max_avg_loss_pct),
        target_pressure_exit_net_loss_pct=float(args.target_pressure_exit_net_loss_pct),
        entry_confirmation_polls=int(args.entry_confirmation_polls),
        target_pressure_min_entry_edge_pct=float(args.target_pressure_min_entry_edge_pct),
        target_pressure_min_live_move_bps=float(args.target_pressure_min_live_move_bps),
        target_pressure_live_override_bps=float(args.target_pressure_live_override_bps),
        target_pressure_live_override_min_edge_pct=float(args.target_pressure_live_override_min_edge_pct),
        require_ml_survival_prob=float(args.require_ml_survival_prob),
        require_fast_green_prob=float(args.require_fast_green_prob),
        require_tail_prob=float(args.require_tail_prob),
        require_bubble_capture_net_pct_per_hour=float(args.require_bubble_capture_net_pct_per_hour),
        manifest_positive_within_seconds=float(args.manifest_positive_within_seconds),
        manifest_positive_min_net_pct=float(args.manifest_positive_min_net_pct),
        require_cluster_size_threshold=int(args.require_cluster_size_threshold),
        systemic_max_positions=int(args.systemic_max_positions),
        idiosyncratic_max_positions=int(args.idiosyncratic_max_positions),
        systemic_deploy_pct=float(args.systemic_deploy_pct),
        idiosyncratic_deploy_pct=float(args.idiosyncratic_deploy_pct),
    )
    fee_tier = resolve_spot_fee_tier(client, fallback_taker_bps=float(args.taker_fee_bps))
    engine.apply_fee_tier(fee_tier)
    state_path = Path(args.state_path)
    event_path = Path(args.event_path)
    opportunity_tape_path = Path(args.opportunity_tape_path)
    if state_path.exists() and not args.fresh_start:
        engine.load_snapshot(load_json(state_path))
        engine.mfe_tracker.load(MFE_TRACKER_PATH)
        engine.profit_lock_retention_pct = max(0.0, min(100.0, float(args.profit_lock_retention_pct)))
        engine.ghost_timing_cooloff_min_closes = max(1, int(args.ghost_timing_cooloff_min_closes))
        engine.ghost_timing_cooloff_max_avg_loss_pct = max(0.0, float(args.ghost_timing_cooloff_max_avg_loss_pct))
        engine.target_pressure_exit_net_loss_pct = max(0.0, float(args.target_pressure_exit_net_loss_pct))
        engine.entry_confirmation_polls = max(1, int(args.entry_confirmation_polls))
        engine.target_pressure_min_entry_edge_pct = max(0.0, float(args.target_pressure_min_entry_edge_pct))
        engine.target_pressure_min_live_move_bps = max(0.0, float(args.target_pressure_min_live_move_bps))
        engine.target_pressure_live_override_bps = max(0.0, float(args.target_pressure_live_override_bps))
        engine.target_pressure_live_override_min_edge_pct = max(0.0, float(args.target_pressure_live_override_min_edge_pct))
        engine.require_ml_survival_prob = max(0.0, min(1.0, float(args.require_ml_survival_prob)))
        engine.require_fast_green_prob = max(0.0, min(1.0, float(args.require_fast_green_prob)))
        engine.require_tail_prob = max(0.0, min(1.0, float(args.require_tail_prob)))
        engine.require_bubble_capture_net_pct_per_hour = max(0.0, float(args.require_bubble_capture_net_pct_per_hour))
        engine.manifest_positive_within_seconds = max(0.0, float(args.manifest_positive_within_seconds))
        engine.manifest_positive_min_net_pct = max(0.0, float(args.manifest_positive_min_net_pct))
        engine.require_cluster_size_threshold = max(1, int(args.require_cluster_size_threshold))
        engine.systemic_max_positions = max(1, int(args.systemic_max_positions))
        engine.idiosyncratic_max_positions = max(1, int(args.idiosyncratic_max_positions))
        engine.systemic_deploy_pct = max(0.0, min(1.0, float(args.systemic_deploy_pct)))
        engine.idiosyncratic_deploy_pct = max(0.0, min(1.0, float(args.idiosyncratic_deploy_pct)))
    runner = {
        "pid": os.getpid(),
        "script": Path(__file__).name,
        "started_at": utc_now_iso(),
        "poll_seconds": max(1.0, float(args.poll_seconds)),
        "heartbeat_at": None,
        "last_successful_run_at": None,
        "consecutive_exceptions": 0,
        "last_exception_at": None,
        "last_exception_type": "",
        "last_exception_message": "",
        "fee_bps_per_side": round(engine.taker_fee_bps, 4),
        "fee_source": engine.fee_source,
        "fee_tier": engine.fee_tier,
        "shadow_only": True,
        "last_pulse_refresh_at": None,
        "opportunity_tape_path": str(opportunity_tape_path),
        "require_ml_survival_prob": round(engine.require_ml_survival_prob, 6),
        "require_fast_green_prob": round(engine.require_fast_green_prob, 6),
        "require_bubble_capture_net_pct_per_hour": round(engine.require_bubble_capture_net_pct_per_hour, 6),
        "manifest_positive_within_seconds": round(engine.manifest_positive_within_seconds, 3),
        "manifest_positive_min_net_pct": round(engine.manifest_positive_min_net_pct, 6),
        "require_cluster_size_threshold": engine.require_cluster_size_threshold,
        "systemic_max_positions": engine.systemic_max_positions,
        "idiosyncratic_max_positions": engine.idiosyncratic_max_positions,
        "systemic_deploy_pct": round(engine.systemic_deploy_pct, 4),
        "idiosyncratic_deploy_pct": round(engine.idiosyncratic_deploy_pct, 4),
        "current_cluster_size": 0,  # Updated each cycle in run_once
    }

    try:
        run_once(
            client,
            engine,
            state_path=state_path,
            event_path=event_path,
            runner=runner,
            refresh_pulse=bool(args.refresh_pulse),
            pulse_refresh_seconds=float(args.pulse_refresh_seconds),
            opportunity_tape_path=opportunity_tape_path,
        )
        if args.once:
            return 0
        while True:
            time.sleep(max(1.0, float(args.poll_seconds)))
            try:
                run_once(
                    client,
                    engine,
                    state_path=state_path,
                    event_path=event_path,
                    runner=runner,
                    refresh_pulse=bool(args.refresh_pulse),
                    pulse_refresh_seconds=float(args.pulse_refresh_seconds),
                    opportunity_tape_path=opportunity_tape_path,
                )
            except Exception as exc:
                runner["consecutive_exceptions"] = int(runner.get("consecutive_exceptions", 0) or 0) + 1
                runner["last_exception_at"] = utc_now_iso()
                runner["last_exception_type"] = type(exc).__name__
                runner["last_exception_message"] = str(exc)
                save_state(state_path, engine, runner)
                log_runner_exception(event_path, exc, phase="loop_run_once")
    except Exception as exc:
        runner["consecutive_exceptions"] = int(runner.get("consecutive_exceptions", 0) or 0) + 1
        runner["last_exception_at"] = utc_now_iso()
        runner["last_exception_type"] = type(exc).__name__
        runner["last_exception_message"] = str(exc)
        save_state(state_path, engine, runner)
        log_runner_exception(event_path, exc, phase="initial_run_once")
        raise


if __name__ == "__main__":
    raise SystemExit(main())
