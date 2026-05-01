#!/usr/bin/env python3
"""
Causal State-Space Classifier for the Adaptive Lattice Controller.

Implements Gap 1 (Validated State-Space Model of Market Motion):
Separates causes of motion, not just symptoms, so the controller
conditions on WHY price moved, not just that it moved.

FIVE CAUSAL STATES:
1. temporary_inventory_displacement — price moves away but reclaims quickly; recoverable
2. toxic_continuation — one-way movement without recovery; dangerous, opens should stay guarded
3. trapped_position_unwind — multiple positions at same fill price, collective drawdown; systemic risk
4. liquidity_thinning — spread deterioration, low tick rate, off-session; friction trap
5. failed_reclaim — price briefly recovered then reversed; false recovery signal

USAGE:
    # Post-hoc analysis from event log
    from causal_state_classifier import CausalStateClassifier
    classifier = CausalStateClassifier.from_event_log("reports/penetration_lattice_shadow_gbpusd_m15_trend_harvest_v1_events.jsonl")
    result = classifier.classify()
    print(result.state, result.confidence, result.reason)

    # Runtime classification from state file
    from causal_state_classifier import CausalStateClassifier
    classifier = CausalStateClassifier.from_state_file("reports/penetration_lattice_shadow_btcusd_m15_warp_state.json")
    result = classifier.classify()
"""
from __future__ import annotations

import json
import statistics
from dataclasses import dataclass
from pathlib import Path
from typing import Any


# ── State vocabulary (pinned, not free-text) ──────────────────────────────────

STATE_TEMPORARY_INVENTORY = "temporary_inventory_displacement"
STATE_TOXIC_CONTINUATION = "toxic_continuation"
STATE_TRAPPED_UNWIND = "trapped_position_unwind"
STATE_LIQUIDITY_THINNING = "liquidity_thinning"
STATE_FAILED_RECLAIM = "failed_reclaim"
STATE_HEALTHY_HARVEST = "healthy_harvest"  # baseline: clean closes, no distress signals
STATE_INSUFFICIENT_DATA = "insufficient_data"  # not enough evidence to classify

ALL_STATES = [
    STATE_TEMPORARY_INVENTORY,
    STATE_TOXIC_CONTINUATION,
    STATE_TRAPPED_UNWIND,
    STATE_LIQUIDITY_THINNING,
    STATE_FAILED_RECLAIM,
    STATE_HEALTHY_HARVEST,
    STATE_INSUFFICIENT_DATA,
]


# ── Input / output contracts ──────────────────────────────────────────────────

@dataclass(frozen=True)
class StateFeatures:
    """Features extracted from telemetry for state classification."""
    # Close-level aggregates
    close_count: int = 0
    win_count: int = 0
    win_rate: float = 0.0
    avg_hold_seconds: float = 0.0
    avg_realized_pnl: float = 0.0

    # Excursion profile
    avg_mfe_pnl: float = 0.0
    avg_mae_pnl: float = 0.0
    avg_mfe_mae_ratio: float = 0.0  # MFE / |MAE| — >1 means favorable dominates

    # First-green timing
    first_green_hit_rate: float = 0.0  # fraction of closes that saw first green
    avg_time_to_first_green: float = 0.0

    # Reclaim/retrace behavior
    reclaim_rate: float = 0.0  # fraction of closes that reclaimed trigger level
    retrace_half_step_rate: float = 0.0  # fraction that retraced 0.5x step

    # Spread / execution context
    avg_spread_at_entry: float = 0.0
    wide_spread_open_fraction: float = 0.0  # fraction of opens with wide_spread_stress regime
    off_session_open_fraction: float = 0.0  # fraction of opens in off_session

    # Burst / concentration
    burst_open_fraction: float = 0.0  # fraction of opens with same_bar_burst >= 2
    avg_burst_count: float = 0.0

    # Inventory state
    current_open_count: int = 0
    current_anchor_resets: int = 0
    # Clustered fills: groups of open tickets at same fill price (±threshold)
    clustered_open_groups: int = 0
    max_cluster_size: int = 0

    # Regime-at-entry distribution
    regime_distribution: dict[str, int] | None = None

    # Toxicity flags
    anchor_reset_rate: float = 0.0  # resets per close
    toxic_close_fraction: float = 0.0  # closes that are net loss with MAE > MFE

    # Runtime freshness
    realized_net_usd: float = 0.0
    runner_exception_count: int = 0


@dataclass(frozen=True)
class StateResult:
    """Classification result with confidence and evidence."""
    state: str
    confidence: float  # 0.0–1.0
    reason: str
    state_scores: dict[str, float]  # raw score for each state before normalization
    falsification_read: str  # what evidence would make this classification wrong?
    recommended_control_action: str  # what the controller should do in this state


# ── Core classifier ───────────────────────────────────────────────────────────

class CausalStateClassifier:
    """
    Classifies the current market state into one of five causal categories
    plus baseline healthy harvest and insufficient data.

    The classifier uses a weighted scoring system where each state has
    specific evidence requirements. States are mutually exclusive —
    the highest-scoring state wins, but only if its confidence exceeds
    a minimum threshold.
    """

    MIN_CLOSES_FOR_CONFIDENCE = 3  # below this, all classifications are degraded

    @classmethod
    def from_event_log(cls, path: str | Path) -> "CausalStateClassifier":
        """Build classifier from a JSONL event log file."""
        events = []
        p = Path(path)
        if p.exists():
            with p.open("r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line:
                        try:
                            events.append(json.loads(line))
                        except json.JSONDecodeError:
                            pass
        return cls(events=events, state_snapshot=None)

    @classmethod
    def from_state_file(cls, path: str | Path) -> "CausalStateClassifier":
        """Build classifier from a runtime state JSON file."""
        p = Path(path)
        state_snapshot = {}
        if p.exists():
            with p.open("r", encoding="utf-8") as f:
                state_snapshot = json.load(f)
        return cls(events=[], state_snapshot=state_snapshot)

    def __init__(
        self,
        events: list[dict[str, Any]] | None = None,
        state_snapshot: dict[str, Any] | None = None,
    ):
        self.events = events or []
        self.state_snapshot = state_snapshot or {}

    def _extract_features(self) -> StateFeatures:
        """Extract features from events and/or state snapshot."""
        close_events = [e for e in self.events if e.get("action") == "close_ticket"]
        open_events = [e for e in self.events if e.get("action") == "open_ticket"]

        # Close-level aggregates
        close_count = len(close_events)
        if close_count > 0:
            pnls = [e.get("realized_pnl", 0.0) for e in close_events]
            win_count = sum(1 for p in pnls if p > 0)
            hold_times = [e.get("hold_seconds", 0.0) for e in close_events]
            first_green_times = [e.get("time_to_first_green_seconds") for e in close_events if e.get("time_to_first_green_seconds") is not None]

            features = StateFeatures(
                close_count=close_count,
                win_count=win_count,
                win_rate=win_count / max(close_count, 1),
                avg_hold_seconds=statistics.mean(hold_times) if hold_times else 0.0,
                avg_realized_pnl=statistics.mean(pnls),
                avg_mfe_pnl=statistics.mean([e.get("max_favorable_excursion_pnl", 0.0) for e in close_events]),
                avg_mae_pnl=statistics.mean([e.get("max_adverse_excursion_pnl", 0.0) for e in close_events]),
                first_green_hit_rate=sum(1 for e in close_events if e.get("first_green_seen")) / close_count,
                avg_time_to_first_green=statistics.mean(first_green_times) if first_green_times else 0.0,
                reclaim_rate=sum(1 for e in close_events if e.get("reclaimed_trigger_level_seen")) / close_count,
                retrace_half_step_rate=sum(1 for e in close_events if e.get("retraced_0_5x_step_seen")) / close_count,
                avg_spread_at_entry=statistics.mean([e.get("spread_at_entry", 0.0) for e in close_events if e.get("spread_at_entry") is not None]) if any(e.get("spread_at_entry") is not None for e in close_events) else 0.0,
                wide_spread_open_fraction=sum(1 for e in open_events if e.get("regime_at_entry") == "wide_spread_stress") / max(len(open_events), 1),
                off_session_open_fraction=sum(1 for e in open_events if e.get("session_bucket_at_open") == "off_session") / max(len(open_events), 1),
                burst_open_fraction=sum(1 for e in open_events if e.get("same_bar_open_burst_count_at_open", 0) >= 2) / max(len(open_events), 1),
                avg_burst_count=statistics.mean([e.get("same_bar_open_burst_count_at_open", 0) for e in open_events]) if open_events else 0.0,
                regime_distribution=self._regime_distribution(open_events),
            )

            # Computed features
            avg_mae_abs = abs(features.avg_mae_pnl) if features.avg_mae_pnl != 0 else 1.0
            features = features.__class__(
                **{**features.__dict__, "avg_mfe_mae_ratio": features.avg_mfe_pnl / avg_mae_abs}
            )

            # Toxicity: closes that are net loss with MAE > MFE (in magnitude)
            toxic_closes = 0
            for e in close_events:
                pnl = e.get("realized_pnl", 0.0)
                mfe = e.get("max_favorable_excursion_pnl", 0.0)
                mae = abs(e.get("max_adverse_excursion_pnl", 0.0))
                if pnl < 0 and mae > mfe:
                    toxic_closes += 1
            features = features.__class__(
                **{**features.__dict__, "toxic_close_fraction": toxic_closes / max(close_count, 1)}
            )
        else:
            features = StateFeatures()

        # Inventory state from state snapshot or events
        symbol_state = self._get_symbol_state()
        if symbol_state:
            features = features.__class__(
                **{**features.__dict__,
                   "current_open_count": symbol_state.get("realized_closes", 0),  # wrong field — need open count
                   "current_anchor_resets": symbol_state.get("anchor_resets", 0),
                   "realized_net_usd": symbol_state.get("realized_net_usd", 0.0),
                }
            )
            # Count actual open positions from snapshot
            # (state file may have open_tickets at top level)
            open_tickets = self.state_snapshot.get("open_tickets", [])
            if not open_tickets:
                # Try symbol-level
                for sym_key in ["BTCUSD", "ETHUSD", "GBPUSD", "SOLUSD", "XRPUSD", "EURUSD", "NZDUSD", "USDJPY", "AUDUSD", "USDCAD"]:
                    sym_data = self.state_snapshot.get("symbols", {}).get(sym_key, {})
                    if "open_tickets" in sym_data:
                        open_tickets = sym_data["open_tickets"]
                        break
            features = features.__class__(
                **{**features.__dict__, "current_open_count": len(open_tickets)}
            )

            # Cluster analysis: group open tickets by fill price
            if open_tickets:
                clusters = self._cluster_fills(open_tickets)
                features = features.__class__(
                    **{**features.__dict__,
                       "clustered_open_groups": len(clusters),
                       "max_cluster_size": max(len(c) for c in clusters) if clusters else 0,
                    }
                )

            # Runner exception count
            runner = self.state_snapshot.get("runner", {})
            features = features.__class__(
                **{**features.__dict__,
                   "runner_exception_count": runner.get("consecutive_exceptions", 0),
                }
            )

            # Anchor reset rate
            if close_count > 0:
                reset_rate = features.current_anchor_resets / max(close_count, 1)
                features = features.__class__(
                    **{**features.__dict__, "anchor_reset_rate": reset_rate}
                )

        return features

    def _get_symbol_state(self) -> dict[str, Any] | None:
        """Get the per-symbol state from the snapshot."""
        # Top-level symbol keys
        for key in ["BTCUSD", "ETHUSD", "GBPUSD", "SOLUSD", "XRPUSD", "EURUSD", "NZDUSD", "USDJPY", "AUDUSD", "USDCAD"]:
            if key in self.state_snapshot.get("symbols", {}):
                return self.state_snapshot["symbols"][key]
        # Try single symbol at top level
        if "anchor" in self.state_snapshot:
            return self.state_snapshot
        return None

    def _regime_distribution(self, open_events: list[dict[str, Any]]) -> dict[str, int]:
        """Count opens by regime_at_entry."""
        dist: dict[str, int] = {}
        for e in open_events:
            regime = e.get("regime_at_entry", "unknown")
            dist[regime] = dist.get(regime, 0) + 1
        return dist

    def _cluster_fills(self, open_tickets: list[dict[str, Any]], tolerance: float = 0.01) -> list[list[dict[str, Any]]]:
        """Group open tickets by fill price cluster (within tolerance)."""
        if not open_tickets:
            return []

        # Normalize fill price from various field names
        def get_fill(ticket: dict) -> float:
            for key in ["fill_price", "entry_fill_price", "entry_price"]:
                val = ticket.get(key)
                if val is not None:
                    return float(val)
            return 0.0

        fills = [(get_fill(t), t) for t in open_tickets]
        fills.sort(key=lambda x: x[0])

        clusters: list[list[dict[str, Any]]] = [[fills[0][1]]]
        for i in range(1, len(fills)):
            if abs(fills[i][0] - fills[i - 1][0]) <= tolerance:
                clusters[-1].append(fills[i][1])
            else:
                clusters.append([fills[i][1]])
        return clusters

    def classify(self) -> StateResult:
        """Classify the current market state."""
        features = self._extract_features()

        # If insufficient data, return early
        if features.close_count < 1 and features.current_open_count == 0:
            return StateResult(
                state=STATE_INSUFFICIENT_DATA,
                confidence=1.0,
                reason="No closes or open positions to analyze.",
                state_scores={s: 0.0 for s in ALL_STATES},
                falsification_read="N/A — no data yet. Re-classify after first close or open.",
                recommended_control_action="Collect evidence before making adaptive decisions.",
            )

        # Score each state
        scores = {s: 0.0 for s in ALL_STATES}

        # ── 1. Temporary Inventory Displacement ──
        # Evidence: high first-green rate, high reclaim, short hold, positive PnL
        inv_score = 0.0
        if features.first_green_hit_rate >= 0.7:
            inv_score += 3.0
        elif features.first_green_hit_rate >= 0.5:
            inv_score += 1.5
        if features.reclaim_rate >= 0.6:
            inv_score += 2.0
        elif features.reclaim_rate >= 0.3:
            inv_score += 1.0
        if features.avg_hold_seconds > 0 and features.avg_hold_seconds < 120:
            inv_score += 1.5  # Short holds suggest quick recovery
        if features.avg_mfe_mae_ratio > 1.5:
            inv_score += 2.0  # Favorable excursion dominates
        if features.win_rate >= 0.6:
            inv_score += 1.0
        if features.close_count >= self.MIN_CLOSES_FOR_CONFIDENCE:
            inv_score += 0.5  # Sample confidence
        scores[STATE_TEMPORARY_INVENTORY] = inv_score

        # ── 2. Toxic Continuation ──
        # Evidence: low first-green, high MAE, anchor resets, toxic closes
        tox_score = 0.0
        if features.first_green_hit_rate < 0.2:
            tox_score += 3.0
        elif features.first_green_hit_rate < 0.4:
            tox_score += 1.5
        if features.avg_mae_pnl < 0 and abs(features.avg_mae_pnl) > max(abs(features.avg_mfe_pnl), 1.0) * 2:
            tox_score += 3.0  # MAE dominates MFE by 2x
        elif features.avg_mae_pnl < 0 and abs(features.avg_mae_pnl) > max(abs(features.avg_mfe_pnl), 1.0):
            tox_score += 1.5
        if features.anchor_reset_rate > 0.5:
            tox_score += 2.0
        elif features.anchor_reset_rate > 0.1:
            tox_score += 1.0
        if features.toxic_close_fraction > 0.5:
            tox_score += 2.0
        elif features.toxic_close_fraction > 0.3:
            tox_score += 1.0
        if features.win_rate < 0.3:
            tox_score += 1.5
        elif features.win_rate < 0.4:
            tox_score += 0.5
        if features.close_count >= self.MIN_CLOSES_FOR_CONFIDENCE:
            tox_score += 0.5
        scores[STATE_TOXIC_CONTINUATION] = tox_score

        # ── 3. Trapped Position Unwind ──
        # Evidence: clustered fills, large open inventory, collective drawdown
        trap_score = 0.0
        if features.max_cluster_size >= 3:
            trap_score += 3.0
        elif features.max_cluster_size >= 2:
            trap_score += 1.5
        if features.clustered_open_groups >= 2:
            trap_score += 1.0
        if features.current_open_count >= 10:
            trap_score += 2.0
        elif features.current_open_count >= 5:
            trap_score += 1.0
        if features.burst_open_fraction > 0.3:
            trap_score += 1.5  # Burst opens create clusters
        if features.avg_burst_count > 2:
            trap_score += 1.0
        # If opens are all on same side, higher trap risk
        regime_dist = features.regime_distribution or {}
        total_regime = sum(regime_dist.values()) if regime_dist else 1
        buy_regimes = sum(v for k, v in regime_dist.items() if "orderly" in k or "normal" in k or "burst" in k or "clustered" in k)
        if total_regime > 0 and (buy_regimes / total_regime > 0.8 or buy_regimes / total_regime < 0.2):
            trap_score += 1.0  # One-sided regime = trapped risk
        scores[STATE_TRAPPED_UNWIND] = trap_score

        # ── 4. Liquidity Thinning ──
        # Evidence: wide spread entries, off-session opens, low tick rate
        liq_score = 0.0
        if features.wide_spread_open_fraction > 0.3:
            liq_score += 3.0
        elif features.wide_spread_open_fraction > 0.1:
            liq_score += 1.5
        if features.off_session_open_fraction > 0.5:
            liq_score += 2.0
        elif features.off_session_open_fraction > 0.2:
            liq_score += 1.0
        if features.avg_spread_at_entry > 0:
            # Spread cost as fraction of average close PnL
            if abs(features.avg_realized_pnl) > 0:
                spread_cost_ratio = features.avg_spread_at_entry / abs(features.avg_realized_pnl)
                if spread_cost_ratio > 0.5:
                    liq_score += 2.0
                elif spread_cost_ratio > 0.2:
                    liq_score += 1.0
        if features.runner_exception_count > 0:
            liq_score += 0.5  # Infrastructure stress correlates with thin markets
        scores[STATE_LIQUIDITY_THINNING] = liq_score

        # ── 5. Failed Reclaim ──
        # Evidence: some reclaim but close still loss, retrace without monetization
        fail_score = 0.0
        if features.reclaim_rate > 0.3 and features.win_rate < 0.4:
            fail_score += 3.0  # Reclaiming but still losing
        elif features.reclaim_rate > 0.2 and features.win_rate < 0.5:
            fail_score += 1.5
        if features.retrace_half_step_rate > 0.5 and features.win_rate < 0.5:
            fail_score += 2.0  # Price retraces but doesn't monetize
        # MFE present but realized PnL is negative = price went favorable then reversed
        if features.avg_mfe_pnl > 0 and features.avg_realized_pnl < 0:
            mfe_capture = features.avg_realized_pnl / max(features.avg_mfe_pnl, 0.01)
            if mfe_capture < -0.5:
                fail_score += 2.0  # Captured negative despite positive MFE
            elif mfe_capture < 0:
                fail_score += 1.5
        scores[STATE_FAILED_RECLAIM] = fail_score

        # ── 6. Healthy Harvest (baseline) ──
        # Evidence: high win rate, positive PnL, low resets, good first-green rate
        healthy_score = 0.0
        if features.win_rate >= 0.7:
            healthy_score += 3.0
        elif features.win_rate >= 0.5:
            healthy_score += 1.5
        if features.avg_realized_pnl > 0:
            healthy_score += 2.0
        if features.first_green_hit_rate >= 0.6:
            healthy_score += 1.5
        if features.anchor_reset_rate < 0.1:
            healthy_score += 1.5
        elif features.anchor_reset_rate == 0:
            healthy_score += 2.0
        if features.toxic_close_fraction < 0.1:
            healthy_score += 1.0
        if features.close_count >= 10:
            healthy_score += 1.0  # Sustained evidence
        if features.max_cluster_size <= 1:
            healthy_score += 0.5  # No clustering = clean fills
        scores[STATE_HEALTHY_HARVEST] = healthy_score

        # ── Select winning state ──
        best_state = max(scores, key=scores.get)
        best_score = scores[best_state]
        total_score = sum(max(s, 0) for s in scores.values())

        # Confidence: how dominant is the winning state?
        if total_score == 0:
            confidence = 0.0
        else:
            confidence = min(best_score / total_score, 1.0)

        # Degrade confidence if insufficient sample
        if features.close_count < self.MIN_CLOSES_FOR_CONFIDENCE:
            confidence *= max(0.3, features.close_count / self.MIN_CLOSES_FOR_CONFIDENCE)

        # Build reason and falsification read
        reason, falsification, control_action = self._build_interpretation(
            best_state, confidence, features, scores
        )

        return StateResult(
            state=best_state,
            confidence=round(confidence, 3),
            reason=reason,
            state_scores={k: round(v, 2) for k, v in scores.items()},
            falsification_read=falsification,
            recommended_control_action=control_action,
        )

    def _build_interpretation(
        self, state: str, confidence: float, features: StateFeatures, scores: dict[str, float]
    ) -> tuple[str, str, str]:
        """Build human-readable interpretation, falsification, and control action."""

        if state == STATE_TEMPORARY_INVENTORY:
            reason = (
                f"First-green hit rate {features.first_green_hit_rate:.0%}, "
                f"reclaim rate {features.reclaim_rate:.0%}, "
                f"MFE/|MAE| ratio {features.avg_mfe_mae_ratio:.2f}, "
                f"win rate {features.win_rate:.0%}. "
                f"Price movements are recoverable — inventory displacement, not trend."
            )
            falsification = (
                f"Would be wrong if: first-green rate drops below 40%, "
                f"anchor reset rate exceeds 0.5, or MAE starts exceeding MFE by >2x."
            )
            control_action = (
                "Normal adaptive geometry is safe. Close policy can be aggressive. "
                "Rearm tokens should fire on normal reclaim. No guard needed."
            )

        elif state == STATE_TOXIC_CONTINUATION:
            reason = (
                f"First-green hit rate {features.first_green_hit_rate:.0%}, "
                f"toxic close fraction {features.toxic_close_fraction:.0%}, "
                f"anchor reset rate {features.anchor_reset_rate:.2f}, "
                f"avg MAE ${abs(features.avg_mae_pnl):.2f} vs MFE ${features.avg_mfe_pnl:.2f}. "
                f"One-way flow without recovery — opens are dangerous."
            )
            falsification = (
                f"Would be wrong if: first-green rate recovers above 60%, "
                f"toxic close fraction drops below 20%, or reclaim rate exceeds 50%."
            )
            control_action = (
                "GUARD new opens. Suppress additional levels. Use cluster-aware escape. "
                "Do NOT widen geometry. Wait for flow normalization before resuming normal operation."
            )

        elif state == STATE_TRAPPED_UNWIND:
            reason = (
                f"{features.current_open_count} open positions, "
                f"max cluster size {features.max_cluster_size}, "
                f"{features.clustered_open_groups} fill-price groups. "
                f"Burst open fraction {features.burst_open_fraction:.0%}. "
                f"Positions form risk clusters — they recover or fail together."
            )
            falsification = (
                f"Would be wrong if: open count drops below 5, "
                f"max cluster size drops to 1, or cluster escape reduces drawdown."
            )
            control_action = (
                "Use cluster-aware escape for same-fill-price groups. "
                "Do NOT escape individually. Scale escape threshold by sqrt(cluster_size). "
                "Block new opens until cluster count decreases."
            )

        elif state == STATE_LIQUIDITY_THINNING:
            reason = (
                f"Wide-spread open fraction {features.wide_spread_open_fraction:.0%}, "
                f"off-session fraction {features.off_session_open_fraction:.0%}, "
                f"avg spread at entry ${features.avg_spread_at_entry:.4f}. "
                f"Spread cost is consuming edge."
            )
            falsification = (
                f"Would be wrong if: wide-spread fraction drops below 10%, "
                f"off-session fraction drops below 20%, or spread-to-realized-PnL ratio drops below 20%."
            )
            control_action = (
                "Widen steps to overcome spread cost, or stand down entirely. "
                "If off-session, session-gate. If in-session but wide spread, "
                "increase minimum step to >3x current spread."
            )

        elif state == STATE_FAILED_RECLAIM:
            reason = (
                f"Reclaim rate {features.reclaim_rate:.0%} but win rate {features.win_rate:.0%}, "
                f"retrace-half-step rate {features.retrace_half_step_rate:.0%}. "
                f"Price briefly recovers but cannot monetize — false recovery signals."
            )
            falsification = (
                f"Would be wrong if: win rate exceeds 55% (reclaims are genuine), "
                f"or reclaim rate drops below 20% (not attempting recovery)."
            )
            control_action = (
                "Tighten close alpha to capture partial profits on reclaim. "
                "Do not wait for full retrace. Use offensive closure if available. "
                "Reduce rearm cooldown to capture micro-recoveries."
            )

        elif state == STATE_HEALTHY_HARVEST:
            reason = (
                f"Win rate {features.win_rate:.0%}, "
                f"avg realized PnL ${features.avg_realized_pnl:+.2f}/close, "
                f"first-green rate {features.first_green_hit_rate:.0%}, "
                f"anchor reset rate {features.anchor_reset_rate:.2f}. "
                f"Clean monetization with minimal distress signals."
            )
            falsification = (
                f"Would be wrong if: win rate drops below 50%, "
                f"avg realized PnL turns negative, or anchor reset rate exceeds 0.1."
            )
            control_action = (
                "Maintain current geometry. Consider modest rearm optimization. "
                "No guard or suppression needed. Continue harvesting."
            )

        else:  # insufficient_data
            reason = f"Only {features.close_count} closes and {features.current_open_count} open positions. Need 3+ closes for reliable classification."
            falsification = "Will become actionable after 3+ closes or 5+ open positions."
            control_action = "Collect evidence. Use default geometry. Do not adapt until state is clear."

        return reason, falsification, control_action


# ── CLI / quick test ──────────────────────────────────────────────────────────

def main() -> int:
    import sys

    if len(sys.argv) > 1:
        path = sys.argv[1]
        classifier = CausalStateClassifier.from_event_log(path)
    else:
        # Default: GBPUSD adaptive event log
        default_path = Path(__file__).parent.parent / "reports" / "penetration_lattice_shadow_gbpusd_m15_trend_harvest_v1_events.jsonl"
        if default_path.exists():
            classifier = CausalStateClassifier.from_event_log(default_path)
        else:
            print("No event log found. Run with: python causal_state_classifier.py <path>")
            return 1

    result = classifier.classify()
    print(f"State: {result.state}")
    print(f"Confidence: {result.confidence:.1%}")
    print(f"Reason: {result.reason}")
    print(f"\nState Scores:")
    for state, score in sorted(result.state_scores.items(), key=lambda x: -x[1]):
        marker = " ←" if state == result.state else ""
        print(f"  {state}: {score:+.2f}{marker}")
    print(f"\nFalsification: {result.falsification_read}")
    print(f"Control Action: {result.recommended_control_action}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
