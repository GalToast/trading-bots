#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
import statistics
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import MetaTrader5 as mt5

from backtest_adaptive_deployment_study import TIMEFRAME_MAP, load_bars
from live_penetration_lattice_shadow import REARM_VARIANTS, RearmToken, _check_momentum_gate
from penetration_lattice_lab_v2 import dynamic_step, spread_price, unit_pnl_usd
from unified_objective import ObjectiveInput, UnifiedObjective


ROOT = Path(__file__).resolve().parent.parent
DEPLOYMENT_STUDY_PATH = ROOT / "reports" / "adaptive_deployment_backtest_study.json"
OUTPUT_CSV = ROOT / "reports" / "joint_control_law_study.csv"
OUTPUT_MD = ROOT / "reports" / "joint_control_law_study.md"
OUTPUT_JSON = ROOT / "reports" / "joint_control_law_study.json"


@dataclass(frozen=True)
class BestContract:
    symbol: str
    timeframe: str
    shape_id: str
    step_buy_px: float
    step_sell_px: float
    max_open_per_side: int
    close_style: str
    close_alpha: float
    sell_gap: int
    buy_gap: int
    rearm_variant: str
    rearm_cooldown_bars: int
    momentum_gate: bool
    variant_label: str


@dataclass(frozen=True)
class ControlLaw:
    name: str
    mode: str  # penetration | adaptive_depth
    description: str
    close_style: str
    close_alpha: float
    sell_gap: int | None = None
    buy_gap: int | None = None
    open_gate_low_confidence: bool = False
    min_gate_distance_steps: float = 0.0
    skip_rearm_below_confidence: float = 0.0
    hybrid_profile: str | None = None
    time_soft_limit_bars: int = 0


@dataclass(frozen=True)
class JointContract:
    symbol: str
    timeframe: str
    shape_id: str
    step_buy_px: float
    step_sell_px: float
    max_open_per_side: int
    rearm_variant: str
    rearm_cooldown_bars: int
    momentum_gate: bool
    base_variant_label: str
    geometry_label: str
    control_name: str
    control_mode: str
    control_description: str
    close_style: str
    close_alpha: float
    sell_gap: int
    buy_gap: int
    open_gate_low_confidence: bool
    min_gate_distance_steps: float
    skip_rearm_below_confidence: float
    hybrid_profile: str | None
    time_soft_limit_bars: int


@dataclass
class TicketState:
    direction: str
    entry_price: float
    opened_time: int
    opened_idx: int
    level_idx: int
    from_rearm: bool = False
    confidence_at_open: float = 1.0


@dataclass(frozen=True)
class AnchorState:
    confidence: float
    distance_steps: float
    trend_persistence: float
    range_expansion: float
    moved_toward_anchor: bool


STEP_CFG = type(
    "Cfg",
    (),
    {
        "adaptive_step_threshold_1": 10,
        "adaptive_step_threshold_2": 20,
        "adaptive_step_multiplier_1": 1.5,
        "adaptive_step_multiplier_2": 2.0,
    },
)()


CONTROL_LAWS = [
    ControlLaw(
        name="current_contract",
        mode="penetration",
        description="Best current offline deployment contract.",
        close_style="current",
        close_alpha=-1.0,
    ),
    ControlLaw(
        name="inner_fast",
        mode="penetration",
        description="Fast inner reclaim on deeper profitable penetration.",
        close_style="inner",
        close_alpha=1.0,
        sell_gap=2,
        buy_gap=2,
    ),
    ControlLaw(
        name="depth_split_reclaim",
        mode="adaptive_depth",
        description="Depth-aware close law: shallow inventory closes fast, mid-depth inventory reclaims anchor, deep inventory only holds through zero when anchor confidence is high.",
        close_style="adaptive_depth",
        close_alpha=0.0,
        open_gate_low_confidence=True,
        min_gate_distance_steps=4.0,
        skip_rearm_below_confidence=0.35,
        hybrid_profile="depth_split_reclaim",
        time_soft_limit_bars=24,
    ),
    ControlLaw(
        name="depth_split_cash_guard",
        mode="adaptive_depth",
        description="More defensive depth split: shallow inventory closes fast, deeper inventory abandons heroic targets earlier when anchor confidence degrades.",
        close_style="adaptive_depth",
        close_alpha=0.0,
        open_gate_low_confidence=True,
        min_gate_distance_steps=3.0,
        skip_rearm_below_confidence=0.45,
        hybrid_profile="depth_split_cash_guard",
        time_soft_limit_bars=18,
    ),
    ControlLaw(
        name="time_disciplined_inner",
        mode="adaptive_depth",
        description="Inner-fast posture with time discipline: once a ticket stays open too long, the target downgrades toward immediate reclaim.",
        close_style="adaptive_depth",
        close_alpha=0.0,
        open_gate_low_confidence=True,
        min_gate_distance_steps=4.0,
        skip_rearm_below_confidence=0.4,
        hybrid_profile="time_disciplined_inner",
        time_soft_limit_bars=16,
    ),
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Joint open/close control-law tournament: geometry, depth-aware close law, anchor-confidence gating, and inventory-time penalties."
    )
    parser.add_argument("--symbols", nargs="*", default=["BTCUSD", "GBPUSD", "EURUSD", "NZDUSD"])
    parser.add_argument("--days", type=int, default=30)
    parser.add_argument("--timeframe", choices=sorted(TIMEFRAME_MAP.keys()), default="M15")
    parser.add_argument("--study-json", default=str(DEPLOYMENT_STUDY_PATH))
    parser.add_argument("--output-csv", default=str(OUTPUT_CSV))
    parser.add_argument("--output-md", default=str(OUTPUT_MD))
    parser.add_argument("--output-json", default=str(OUTPUT_JSON))
    return parser.parse_args()


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def load_best_contracts(path: Path, symbols: list[str], timeframe: str) -> list[BestContract]:
    payload = load_json(path)
    symbol_set = {str(symbol).upper() for symbol in symbols}
    contracts: list[BestContract] = []
    for row in list(((payload.get("summary") or {}).get("best_by_symbol")) or []):
        symbol = str(row.get("symbol") or "").upper()
        if symbol not in symbol_set:
            continue
        if str(row.get("timeframe") or "").upper() != timeframe.upper():
            continue
        contracts.append(
            BestContract(
                symbol=symbol,
                timeframe=str(row.get("timeframe") or timeframe),
                shape_id=str(row.get("shape_id") or ""),
                step_buy_px=float(row.get("step_buy_px") or 0.0),
                step_sell_px=float(row.get("step_sell_px") or 0.0),
                max_open_per_side=int(row.get("max_open_per_side") or 0),
                close_style=str(row.get("close_style") or "all_profitable"),
                close_alpha=float(row.get("close_alpha") or 0.0),
                sell_gap=int(row.get("sell_gap") or 1),
                buy_gap=int(row.get("buy_gap") or 1),
                rearm_variant=str(row.get("rearm_variant") or "rearm_lvl2_exc1"),
                rearm_cooldown_bars=int(row.get("rearm_cooldown_bars") or 0),
                momentum_gate=bool(row.get("momentum_gate", False)),
                variant_label=str(row.get("variant_label") or ""),
            )
        )
    return contracts


def build_joint_contracts(base: BestContract) -> list[JointContract]:
    step_scales = [0.9, 1.0]
    cap_shifts = [0, 3]
    variants: list[JointContract] = []
    for step_scale in step_scales:
        for cap_shift in cap_shifts:
            geometry_label = f"step{step_scale:.2f}_cap{base.max_open_per_side + cap_shift}"
            for control in CONTROL_LAWS:
                if control.name == "current_contract":
                    close_style = base.close_style
                    close_alpha = base.close_alpha
                    sell_gap = base.sell_gap
                    buy_gap = base.buy_gap
                else:
                    close_style = control.close_style
                    close_alpha = control.close_alpha
                    sell_gap = max(1, int(control.sell_gap or base.sell_gap))
                    buy_gap = max(1, int(control.buy_gap or base.buy_gap))
                variants.append(
                    JointContract(
                        symbol=base.symbol,
                        timeframe=base.timeframe,
                        shape_id=base.shape_id,
                        step_buy_px=round(base.step_buy_px * step_scale, 6),
                        step_sell_px=round(base.step_sell_px * step_scale, 6),
                        max_open_per_side=max(4, int(base.max_open_per_side + cap_shift)),
                        rearm_variant=base.rearm_variant,
                        rearm_cooldown_bars=base.rearm_cooldown_bars,
                        momentum_gate=base.momentum_gate,
                        base_variant_label=base.variant_label,
                        geometry_label=geometry_label,
                        control_name=control.name,
                        control_mode=control.mode,
                        control_description=control.description,
                        close_style=close_style,
                        close_alpha=close_alpha,
                        sell_gap=sell_gap,
                        buy_gap=buy_gap,
                        open_gate_low_confidence=control.open_gate_low_confidence,
                        min_gate_distance_steps=control.min_gate_distance_steps,
                        skip_rearm_below_confidence=control.skip_rearm_below_confidence,
                        hybrid_profile=control.hybrid_profile,
                        time_soft_limit_bars=control.time_soft_limit_bars,
                    )
                )
    return variants


def _window_ranges(bars: list[dict[str, Any]], start: int, end: int) -> list[float]:
    return [max(0.0, float(bar["high"]) - float(bar["low"])) for bar in bars[max(0, start):max(0, end)]]


def compute_anchor_state(
    bars: list[dict[str, Any]],
    idx: int,
    anchor: float,
    avg_step_px: float,
) -> AnchorState:
    bar = bars[idx]
    close_price = float(bar["close"])
    distance_steps = abs(close_price - float(anchor)) / max(float(avg_step_px), 1e-9)

    recent = bars[max(1, idx - 6):idx + 1]
    direction = 0
    if close_price > float(anchor):
        direction = 1
    elif close_price < float(anchor):
        direction = -1

    away_moves = 0
    toward_moves = 0
    total_moves = 0
    if direction != 0:
        for pos in range(1, len(recent)):
            delta = float(recent[pos]["close"]) - float(recent[pos - 1]["close"])
            if delta == 0:
                continue
            total_moves += 1
            if delta * direction > 0:
                away_moves += 1
            else:
                toward_moves += 1
    trend_persistence = (away_moves / total_moves) if total_moves else 0.0

    recent_ranges = _window_ranges(bars, idx - 6, idx + 1)
    baseline_ranges = _window_ranges(bars, idx - 18, idx - 6)
    recent_mean = statistics.mean(recent_ranges) if recent_ranges else 0.0
    baseline_mean = statistics.mean(baseline_ranges) if baseline_ranges else max(recent_mean, 1e-9)
    range_expansion = recent_mean / max(baseline_mean, 1e-9)

    moved_toward_anchor = toward_moves > away_moves
    confidence = 1.0
    confidence -= max(0.0, distance_steps - 1.5) * 0.08
    confidence -= max(0.0, trend_persistence - 0.5) * 0.45
    confidence -= max(0.0, range_expansion - 1.2) * 0.25
    if moved_toward_anchor:
        confidence += 0.1
    confidence = max(0.0, min(1.0, confidence))
    return AnchorState(
        confidence=confidence,
        distance_steps=distance_steps,
        trend_persistence=trend_persistence,
        range_expansion=range_expansion,
        moved_toward_anchor=moved_toward_anchor,
    )


def _update_token_arming(tokens: list[RearmToken], bar: dict[str, Any], step_px: float, excursion_levels: int) -> None:
    for token in tokens:
        if token.armed or step_px <= 0:
            continue
        if int(bar["time"]) < int(token.cooldown_until_time or 0):
            continue
        if token.direction == "SELL":
            away_trigger = float(token.level) - (excursion_levels * step_px)
            if float(bar["low"]) <= away_trigger:
                token.armed = True
        else:
            away_trigger = float(token.level) + (excursion_levels * step_px)
            if float(bar["high"]) >= away_trigger:
                token.armed = True


def _allow_new_opens(contract: JointContract, anchor_state: AnchorState) -> bool:
    if not contract.open_gate_low_confidence:
        return True
    if float(anchor_state.distance_steps) < float(contract.min_gate_distance_steps):
        return True
    return float(anchor_state.confidence) >= 0.35


def _open_token_if_hit(
    *,
    contract: JointContract,
    token: RearmToken,
    bar: dict[str, Any],
    tickets: list[TicketState],
    idx: int,
    confidence: float,
) -> TicketState | None:
    if not token.armed:
        return None
    if contract.momentum_gate and not _check_momentum_gate(bar, token.direction, float(token.level)):
        return None
    if token.direction == "SELL" and float(bar["high"]) >= float(token.level):
        ticket = TicketState(
            direction="SELL",
            entry_price=float(token.level),
            opened_time=int(bar["time"]),
            opened_idx=idx,
            level_idx=int(token.level_idx),
            from_rearm=True,
            confidence_at_open=confidence,
        )
        tickets.append(ticket)
        return ticket
    if token.direction == "BUY" and float(bar["low"]) <= float(token.level):
        ticket = TicketState(
            direction="BUY",
            entry_price=float(token.level),
            opened_time=int(bar["time"]),
            opened_idx=idx,
            level_idx=int(token.level_idx),
            from_rearm=True,
            confidence_at_open=confidence,
        )
        tickets.append(ticket)
        return ticket
    return None


def _close_positions_penetration(
    *,
    symbol: str,
    direction: str,
    tickets: list[TicketState],
    trigger_price: float,
    bar_extreme: float,
    contract: JointContract,
    spread_px: float,
    variant,
    tokens: list[RearmToken],
    stats: dict[str, Any],
    idx: int,
    anchor_state: AnchorState,
) -> None:
    if direction == "SELL":
        ordered = sorted((ticket for ticket in tickets if ticket.direction == "SELL"), key=lambda item: item.entry_price, reverse=True)
        gap = contract.sell_gap
    else:
        ordered = sorted((ticket for ticket in tickets if ticket.direction == "BUY"), key=lambda item: item.entry_price)
        gap = contract.buy_gap
    while len(ordered) > gap:
        level_price = float(ordered[gap].entry_price)
        if direction == "SELL" and trigger_price > level_price:
            break
        if direction == "BUY" and trigger_price < level_price:
            break
        close_ref = level_price + ((bar_extreme - level_price) * float(contract.close_alpha))
        profitable_positions = [
            pos
            for pos, ticket in enumerate(ordered)
            if unit_pnl_usd(symbol, direction, float(ticket.entry_price), close_ref, spread_px) > 0
        ]
        if contract.close_style == "outer":
            close_positions = [0]
        elif contract.close_style == "inner":
            close_positions = [max(0, gap - 1)]
        elif contract.close_style == "all_profitable":
            close_positions = profitable_positions
        else:
            raise ValueError(f"Unsupported close style: {contract.close_style}")
        close_positions = sorted(set(close_positions), reverse=True)
        if not close_positions:
            break
        closed_any = False
        for pos in close_positions:
            ticket = ordered[pos]
            pnl = unit_pnl_usd(symbol, direction, float(ticket.entry_price), close_ref, spread_px)
            if pnl <= 0:
                continue
            tickets.remove(ticket)
            hold_bars = max(1, int(idx - ticket.opened_idx))
            stats["realized_net_usd"] += pnl
            stats["realized_closes"] += 1
            stats["wins"] += 1
            stats["close_pnls"].append(pnl)
            stats["closed_hold_bars"].append(hold_bars)
            if float(anchor_state.confidence) < float(contract.skip_rearm_below_confidence):
                closed_any = True
                continue
            if int(ticket.level_idx) >= int(variant.min_level_idx):
                token = RearmToken(direction=ticket.direction, level=float(ticket.entry_price), level_idx=int(ticket.level_idx))
                token.cooldown_until_time = int(stats["bar_time"]) + (contract.rearm_cooldown_bars * 60)
                tokens.append(token)
            closed_any = True
        if not closed_any:
            break
        if direction == "SELL":
            ordered = sorted((ticket for ticket in tickets if ticket.direction == "SELL"), key=lambda item: item.entry_price, reverse=True)
        else:
            ordered = sorted((ticket for ticket in tickets if ticket.direction == "BUY"), key=lambda item: item.entry_price)


def _signed_level_price(anchor: float, sell_step_px: float, buy_step_px: float, signed_level: int) -> float:
    if signed_level > 0:
        return float(anchor) + (float(sell_step_px) * float(signed_level))
    if signed_level < 0:
        return float(anchor) - (float(buy_step_px) * float(abs(int(signed_level))))
    return float(anchor)


def _adaptive_target_signed_level(
    ticket: TicketState,
    contract: JointContract,
    anchor_state: AnchorState,
    hold_bars: int,
) -> int:
    direction_sign = 1 if ticket.direction == "SELL" else -1
    depth = max(1, int(ticket.level_idx))
    confidence = float(anchor_state.confidence)
    profile = str(contract.hybrid_profile or "")

    if profile == "depth_split_reclaim":
        if depth <= 2:
            target = max(0, depth - 1)
        elif depth <= 4:
            target = 0 if confidence >= 0.55 else max(0, depth - 1)
        else:
            target = -1 if confidence >= 0.7 else 0
    elif profile == "depth_split_cash_guard":
        if depth <= 2:
            target = max(0, depth - 1)
        elif depth <= 5:
            target = 0 if confidence >= 0.65 else max(0, depth - 1)
        else:
            target = -1 if confidence >= 0.8 else max(0, depth - 1)
    elif profile == "time_disciplined_inner":
        if hold_bars >= int(contract.time_soft_limit_bars):
            target = max(0, depth - 1)
        elif confidence < 0.45:
            target = max(0, depth - 1)
        elif depth <= 3:
            target = 0
        else:
            target = -1
    else:
        target = max(0, depth - 1)

    if int(contract.time_soft_limit_bars) > 0 and hold_bars >= int(contract.time_soft_limit_bars):
        target = max(target, max(0, depth - 1))
        if target < 0:
            target = 0
    if float(anchor_state.confidence) < 0.35 and target < 0:
        target = 0
    return direction_sign * target


def _close_positions_adaptive_depth(
    *,
    symbol: str,
    direction: str,
    tickets: list[TicketState],
    trigger_price: float,
    contract: JointContract,
    spread_px: float,
    anchor: float,
    variant,
    tokens: list[RearmToken],
    stats: dict[str, Any],
    idx: int,
    anchor_state: AnchorState,
) -> None:
    side_tickets = [ticket for ticket in tickets if ticket.direction == direction]
    side_tickets.sort(key=lambda item: (item.level_idx, item.entry_price), reverse=True)
    for ticket in side_tickets:
        hold_bars = max(1, int(idx - ticket.opened_idx))
        target_signed_level = _adaptive_target_signed_level(ticket, contract, anchor_state, hold_bars)
        target_price = _signed_level_price(anchor, contract.step_sell_px, contract.step_buy_px, target_signed_level)
        if direction == "SELL" and trigger_price > target_price:
            continue
        if direction == "BUY" and trigger_price < target_price:
            continue
        pnl = unit_pnl_usd(symbol, direction, float(ticket.entry_price), float(target_price), spread_px)
        if pnl <= 0:
            continue
        tickets.remove(ticket)
        stats["realized_net_usd"] += pnl
        stats["realized_closes"] += 1
        stats["wins"] += 1
        stats["close_pnls"].append(pnl)
        stats["closed_hold_bars"].append(hold_bars)
        if float(anchor_state.confidence) < float(contract.skip_rearm_below_confidence):
            continue
        if int(ticket.level_idx) >= int(variant.min_level_idx):
            token = RearmToken(direction=ticket.direction, level=float(ticket.entry_price), level_idx=int(ticket.level_idx))
            token.cooldown_until_time = int(stats["bar_time"]) + (contract.rearm_cooldown_bars * 60)
            tokens.append(token)


def simulate_contract(contract: JointContract, bars: list[dict[str, Any]], symbol_info) -> dict[str, Any]:
    if not bars:
        return {}
    variant = REARM_VARIANTS.get(contract.rearm_variant)
    if variant is None:
        raise ValueError(f"Unknown rearm variant {contract.rearm_variant!r}")

    spread_px = spread_price(symbol_info)
    tickets: list[TicketState] = []
    tokens: list[RearmToken] = []
    anchor = float(bars[0]["close"])
    next_sell_level = anchor + contract.step_sell_px
    next_buy_level = anchor - contract.step_buy_px
    next_sell_level_idx = 1
    next_buy_level_idx = 1
    stats: dict[str, Any] = {
        "realized_net_usd": 0.0,
        "realized_closes": 0,
        "wins": 0,
        "max_open_total": 0,
        "anchor_resets": 0,
        "rearm_opens": 0,
        "close_pnls": [],
        "closed_hold_bars": [],
        "open_blocked_low_confidence": 0,
        "rearm_blocked_low_confidence": 0,
        "mean_anchor_confidence": [],
        "max_adverse_excursion_usd": 0.0,
        "bar_time": int(bars[0]["time"]),
    }

    for idx in range(1, len(bars)):
        bar = bars[idx]
        stats["bar_time"] = int(bar["time"])
        anchor_state = compute_anchor_state(
            bars=bars,
            idx=idx,
            anchor=anchor,
            avg_step_px=(float(contract.step_buy_px) + float(contract.step_sell_px)) / 2.0,
        )
        stats["mean_anchor_confidence"].append(float(anchor_state.confidence))

        _update_token_arming(tokens, bar, contract.step_sell_px, variant.excursion_levels)
        _update_token_arming(tokens, bar, contract.step_buy_px, variant.excursion_levels)
        allow_new_opens = _allow_new_opens(contract, anchor_state)

        open_sell_main = sum(1 for ticket in tickets if ticket.direction == "SELL" and not ticket.from_rearm)
        open_buy_main = sum(1 for ticket in tickets if ticket.direction == "BUY" and not ticket.from_rearm)
        open_sell_rearm = sum(1 for ticket in tickets if ticket.direction == "SELL" and ticket.from_rearm)
        open_buy_rearm = sum(1 for ticket in tickets if ticket.direction == "BUY" and ticket.from_rearm)
        current_sell_step = dynamic_step(contract.step_sell_px, open_sell_main, STEP_CFG)
        current_buy_step = dynamic_step(contract.step_buy_px, open_buy_main, STEP_CFG)

        while float(bar["high"]) >= next_sell_level and open_sell_main < contract.max_open_per_side:
            if not allow_new_opens:
                stats["open_blocked_low_confidence"] += 1
                break
            tickets.append(
                TicketState(
                    direction="SELL",
                    entry_price=float(next_sell_level),
                    opened_time=int(bar["time"]),
                    opened_idx=idx,
                    level_idx=next_sell_level_idx,
                    from_rearm=False,
                    confidence_at_open=float(anchor_state.confidence),
                )
            )
            open_sell_main += 1
            next_sell_level_idx += 1
            current_sell_step = dynamic_step(contract.step_sell_px, open_sell_main, STEP_CFG)
            next_sell_level += current_sell_step

        while float(bar["low"]) <= next_buy_level and open_buy_main < contract.max_open_per_side:
            if not allow_new_opens:
                stats["open_blocked_low_confidence"] += 1
                break
            tickets.append(
                TicketState(
                    direction="BUY",
                    entry_price=float(next_buy_level),
                    opened_time=int(bar["time"]),
                    opened_idx=idx,
                    level_idx=next_buy_level_idx,
                    from_rearm=False,
                    confidence_at_open=float(anchor_state.confidence),
                )
            )
            open_buy_main += 1
            next_buy_level_idx += 1
            current_buy_step = dynamic_step(contract.step_buy_px, open_buy_main, STEP_CFG)
            next_buy_level -= current_buy_step

        for token in list(tokens):
            if float(anchor_state.confidence) < float(contract.skip_rearm_below_confidence):
                stats["rearm_blocked_low_confidence"] += 1
                continue
            if token.direction == "SELL" and open_sell_rearm < contract.max_open_per_side:
                ticket = _open_token_if_hit(
                    contract=contract,
                    token=token,
                    bar=bar,
                    tickets=tickets,
                    idx=idx,
                    confidence=float(anchor_state.confidence),
                )
                if ticket is not None:
                    tokens.remove(token)
                    open_sell_rearm += 1
                    stats["rearm_opens"] += 1
            elif token.direction == "BUY" and open_buy_rearm < contract.max_open_per_side:
                ticket = _open_token_if_hit(
                    contract=contract,
                    token=token,
                    bar=bar,
                    tickets=tickets,
                    idx=idx,
                    confidence=float(anchor_state.confidence),
                )
                if ticket is not None:
                    tokens.remove(token)
                    open_buy_rearm += 1
                    stats["rearm_opens"] += 1

        if contract.control_mode == "penetration":
            _close_positions_penetration(
                symbol=contract.symbol,
                direction="SELL",
                tickets=tickets,
                trigger_price=float(bar["low"]),
                bar_extreme=float(bar["low"]),
                contract=contract,
                spread_px=spread_px,
                variant=variant,
                tokens=tokens,
                stats=stats,
                idx=idx,
                anchor_state=anchor_state,
            )
            _close_positions_penetration(
                symbol=contract.symbol,
                direction="BUY",
                tickets=tickets,
                trigger_price=float(bar["high"]),
                bar_extreme=float(bar["high"]),
                contract=contract,
                spread_px=spread_px,
                variant=variant,
                tokens=tokens,
                stats=stats,
                idx=idx,
                anchor_state=anchor_state,
            )
        elif contract.control_mode == "adaptive_depth":
            _close_positions_adaptive_depth(
                symbol=contract.symbol,
                direction="SELL",
                tickets=tickets,
                trigger_price=float(bar["low"]),
                contract=contract,
                spread_px=spread_px,
                anchor=anchor,
                variant=variant,
                tokens=tokens,
                stats=stats,
                idx=idx,
                anchor_state=anchor_state,
            )
            _close_positions_adaptive_depth(
                symbol=contract.symbol,
                direction="BUY",
                tickets=tickets,
                trigger_price=float(bar["high"]),
                contract=contract,
                spread_px=spread_px,
                anchor=anchor,
                variant=variant,
                tokens=tokens,
                stats=stats,
                idx=idx,
                anchor_state=anchor_state,
            )
        else:
            raise ValueError(f"Unsupported control mode: {contract.control_mode}")

        floating_now = sum(
            unit_pnl_usd(contract.symbol, ticket.direction, float(ticket.entry_price), float(bar["close"]), spread_px)
            for ticket in tickets
        )
        stats["max_adverse_excursion_usd"] = min(float(stats["max_adverse_excursion_usd"]), float(floating_now))
        stats["max_open_total"] = max(int(stats["max_open_total"]), len(tickets))

        if not tickets and (
            float(bar["close"]) >= anchor + contract.step_sell_px
            or float(bar["close"]) <= anchor - contract.step_buy_px
        ):
            anchor = float(bar["close"])
            next_sell_level = anchor + contract.step_sell_px
            next_buy_level = anchor - contract.step_buy_px
            next_sell_level_idx = 1
            next_buy_level_idx = 1
            stats["anchor_resets"] += 1
            tokens = []

    floating_net_usd = sum(
        unit_pnl_usd(contract.symbol, ticket.direction, float(ticket.entry_price), float(bars[-1]["close"]), spread_px)
        for ticket in tickets
    )
    combined_net_usd = float(stats["realized_net_usd"]) + float(floating_net_usd)
    hours = max((int(bars[-1]["time"]) - int(bars[0]["time"])) / 3600.0, 0.01)
    realized_closes = int(stats["realized_closes"])
    avg_close_usd = (float(stats["realized_net_usd"]) / float(realized_closes)) if realized_closes else 0.0
    closes_per_hour = float(realized_closes) / hours
    avg_hold_bars = float(statistics.mean(stats["closed_hold_bars"])) if stats["closed_hold_bars"] else 0.0
    hold_penalty_per_hour = max(0.0, avg_hold_bars - 8.0) * closes_per_hour * max(0.02, abs(avg_close_usd) * 0.01)
    carry_penalty_per_hour = (abs(float(floating_net_usd)) / hours) * 0.15
    survival_adjusted_usd_per_hour = (float(stats["realized_net_usd"]) / hours) - hold_penalty_per_hour - carry_penalty_per_hour
    win_rate = (float(stats["wins"]) / float(realized_closes)) if realized_closes else 0.0
    objective = UnifiedObjective.evaluate(
        ObjectiveInput(
            realized_net_usd=float(stats["realized_net_usd"]),
            close_count=realized_closes,
            floating_usd=float(floating_net_usd),
            open_count=len(tickets),
            anchor_reset_count=int(stats["anchor_resets"]),
            max_adverse_excursion_usd=float(stats["max_adverse_excursion_usd"]),
            realized_win_rate=win_rate,
        )
    )
    mean_anchor_confidence = float(statistics.mean(stats["mean_anchor_confidence"])) if stats["mean_anchor_confidence"] else 1.0
    avg_open_hold_bars_current = (
        float(statistics.mean([max(1, len(bars) - ticket.opened_idx) for ticket in tickets]))
        if tickets
        else 0.0
    )
    return {
        **asdict(contract),
        "realized_net_usd": round(float(stats["realized_net_usd"]), 3),
        "floating_net_usd": round(float(floating_net_usd), 3),
        "combined_net_usd": round(float(combined_net_usd), 3),
        "realized_closes": realized_closes,
        "avg_close_usd": round(avg_close_usd, 3),
        "realized_usd_per_hour": round(float(stats["realized_net_usd"]) / hours, 3),
        "combined_usd_per_hour": round(float(combined_net_usd) / hours, 3),
        "closes_per_hour": round(closes_per_hour, 3),
        "avg_hold_bars_closed": round(avg_hold_bars, 3),
        "avg_open_hold_bars_current": round(avg_open_hold_bars_current, 3),
        "hold_penalty_per_hour": round(hold_penalty_per_hour, 3),
        "carry_penalty_per_hour": round(carry_penalty_per_hour, 3),
        "survival_adjusted_usd_per_hour": round(survival_adjusted_usd_per_hour, 3),
        "max_open_total": int(stats["max_open_total"]),
        "final_open_count": len(tickets),
        "anchor_resets": int(stats["anchor_resets"]),
        "rearm_opens": int(stats["rearm_opens"]),
        "open_blocked_low_confidence": int(stats["open_blocked_low_confidence"]),
        "rearm_blocked_low_confidence": int(stats["rearm_blocked_low_confidence"]),
        "mean_anchor_confidence": round(mean_anchor_confidence, 4),
        "max_adverse_excursion_usd": round(float(stats["max_adverse_excursion_usd"]), 3),
        "realized_win_rate": round(win_rate, 4),
        "unified_objective_score": round(float(objective.total), 3),
        "objective_verdict": objective.verdict,
    }


def score_sort_key(row: dict[str, Any]) -> tuple[float, float, float, float]:
    return (
        float(row["survival_adjusted_usd_per_hour"]),
        float(row["unified_objective_score"]),
        float(row["realized_usd_per_hour"]),
        float(row["combined_net_usd"]),
    )


def build_summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    by_symbol: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        by_symbol.setdefault(str(row["symbol"]), []).append(row)

    best_by_symbol: list[dict[str, Any]] = []
    leadership: list[str] = []
    for symbol in sorted(by_symbol):
        symbol_rows = sorted(by_symbol[symbol], key=score_sort_key, reverse=True)
        best = symbol_rows[0]
        incumbent = next((row for row in symbol_rows if str(row["control_name"]) == "current_contract"), best)
        delta = round(float(best["survival_adjusted_usd_per_hour"]) - float(incumbent["survival_adjusted_usd_per_hour"]), 3)
        leadership.append(
            f"{symbol} best joint control is `{best['control_name']}:{best['geometry_label']}` at survival-adjusted `${best['survival_adjusted_usd_per_hour']}/h` "
            f"vs incumbent `${incumbent['survival_adjusted_usd_per_hour']}/h` (delta `${delta}/h`)."
        )
        best_by_symbol.append(best)
    overall_best = max(rows, key=score_sort_key) if rows else {}
    if overall_best:
        leadership.append(
            f"Highest survival-adjusted row in the study is `{overall_best['symbol']}:{overall_best['control_name']}:{overall_best['geometry_label']}` "
            f"at `${overall_best['survival_adjusted_usd_per_hour']}/h`."
        )
    return {
        "leadership": leadership,
        "best_by_symbol": best_by_symbol,
        "overall_best": overall_best,
    }


def build_markdown(rows: list[dict[str, Any]], summary: dict[str, Any], *, timeframe: str, days: int) -> str:
    lines: list[str] = []
    lines.append("# Joint Control-Law Study")
    lines.append("")
    lines.append(
        f"This study tests the deeper seams together on `{timeframe}` bars over `{days}` days: geometry and cap are varied jointly with "
        "depth-aware close laws, a bar-derived anchor-confidence proxy, low-confidence open/rearm gating, and an explicit time-in-inventory penalty."
    )
    lines.append("")
    lines.append("## Leadership Read")
    lines.append("")
    for line in summary["leadership"]:
        lines.append(f"- {line}")
    lines.append("")
    lines.append("## Best By Symbol")
    lines.append("")
    for row in summary["best_by_symbol"]:
        lines.append(
            f"- `{row['symbol']}`: `{row['control_name']}` on `{row['geometry_label']}` -> survival-adjusted `${row['survival_adjusted_usd_per_hour']}/h`, "
            f"raw `${row['realized_usd_per_hour']}/h`, `{row['closes_per_hour']}` closes/h, avg hold `{row['avg_hold_bars_closed']}` bars, "
            f"floating `${row['floating_net_usd']}`, blocked opens `{row['open_blocked_low_confidence']}`, objective `{row['objective_verdict']}`."
        )
        lines.append(f"  Logic: {row['control_description']}")
    lines.append("")
    lines.append("## What This Adds")
    lines.append("")
    lines.append("- `survival_adjusted_usd_per_hour` = realized `$ / h` minus a hold-duration penalty and a floating-carry penalty.")
    lines.append("- Anchor-confidence is a bar-derived proxy based on distance from anchor, trend persistence away from the anchor, and recent range expansion versus baseline.")
    lines.append("- Low-confidence gates can block new opens and skip rearm so the search can test when not to keep supplying liquidity.")
    lines.append("- Adaptive-depth laws can change target depth by ticket level, confidence, and hold age instead of forcing one close family onto every ticket.")
    lines.append("")
    lines.append("## Full Ranking")
    lines.append("")
    lines.append("| Symbol | Control | Geometry | Adj $/h | Raw $/h | Closes/h | Hold Bars | Floating | Open Blocks | Objective |")
    lines.append("| --- | --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | --- |")
    for row in rows:
        lines.append(
            f"| {row['symbol']} | {row['control_name']} | {row['geometry_label']} | {row['survival_adjusted_usd_per_hour']} | "
            f"{row['realized_usd_per_hour']} | {row['closes_per_hour']} | {row['avg_hold_bars_closed']} | {row['floating_net_usd']} | "
            f"{row['open_blocked_low_confidence']} | {row['objective_verdict']} |"
        )
    return "\n".join(lines) + "\n"


def write_outputs(rows: list[dict[str, Any]], summary: dict[str, Any], *, args: argparse.Namespace) -> None:
    out_csv = Path(args.output_csv)
    out_csv.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = list(rows[0].keys()) if rows else []
    with out_csv.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

    payload = {
        "generated_at": utc_now_iso(),
        "timeframe": args.timeframe,
        "days": args.days,
        "symbols": args.symbols,
        "summary": summary,
        "rows": rows,
    }
    Path(args.output_json).write_text(json.dumps(payload, indent=2), encoding="utf-8")
    Path(args.output_md).write_text(
        build_markdown(rows, summary, timeframe=args.timeframe, days=args.days),
        encoding="utf-8",
    )


def main() -> int:
    args = parse_args()
    if not mt5.initialize():
        print("MetaTrader5 initialize() failed")
        return 1
    try:
        base_contracts = load_best_contracts(Path(args.study_json), [str(symbol).upper() for symbol in args.symbols], args.timeframe)
        if not base_contracts:
            print("No base contracts matched the requested symbols/timeframe.")
            return 1
        rows: list[dict[str, Any]] = []
        for base in base_contracts:
            info = mt5.symbol_info(base.symbol)
            if info is None:
                continue
            bars = load_bars(base.symbol, args.timeframe, args.days)
            if not bars:
                continue
            for contract in build_joint_contracts(base):
                rows.append(simulate_contract(contract, bars, info))
        if not rows:
            print("No study rows generated.")
            return 1
        rows.sort(key=score_sort_key, reverse=True)
        summary = build_summary(rows)
        write_outputs(rows, summary, args=args)
        print(f"Wrote {args.output_csv}")
        print(f"Wrote {args.output_md}")
        print(f"Wrote {args.output_json}")
        return 0
    finally:
        mt5.shutdown()


if __name__ == "__main__":
    raise SystemExit(main())
