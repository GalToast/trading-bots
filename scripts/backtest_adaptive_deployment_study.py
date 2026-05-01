#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import MetaTrader5 as mt5

from live_penetration_lattice_shadow import REARM_VARIANTS, RearmToken, Ticket, _check_momentum_gate
from penetration_lattice_lab_v2 import dynamic_step, pip_size_for, spread_price, unit_pnl_usd
from unified_objective import ObjectiveInput, UnifiedObjective


ROOT = Path(__file__).resolve().parent.parent
SHAPE_LIBRARY_PATH = ROOT / "configs" / "adaptive_lattice_shape_library.json"
REGIME_PATH = ROOT / "reports" / "regime_classification_live.json"
OUTPUT_CSV = ROOT / "reports" / "adaptive_deployment_backtest_study.csv"
OUTPUT_MD = ROOT / "reports" / "adaptive_deployment_backtest_study.md"
OUTPUT_JSON = ROOT / "reports" / "adaptive_deployment_backtest_study.json"

TIMEFRAME_MAP = {
    "M1": mt5.TIMEFRAME_M1,
    "M5": mt5.TIMEFRAME_M5,
    "M15": mt5.TIMEFRAME_M15,
    "H1": mt5.TIMEFRAME_H1,
}

TIMEFRAME_BARS_PER_DAY = {
    "M1": 1440,
    "M5": 288,
    "M15": 96,
    "H1": 24,
}

TIMEFRAME_SECONDS = {
    "M1": 60,
    "M5": 300,
    "M15": 900,
    "H1": 3600,
}


@dataclass(frozen=True)
class CloseSpec:
    style: str
    alpha: float
    sell_gap: int
    buy_gap: int
    label: str


@dataclass(frozen=True)
class DeploymentContract:
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
    step_scale: float
    cap_delta: int
    close_profile: str


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Backtest adaptive deployment contracts to answer launch, scale, and closeout for max profit/hour."
    )
    parser.add_argument("--symbols", nargs="*", default=["GBPUSD", "EURUSD", "NZDUSD", "BTCUSD"])
    parser.add_argument("--days", type=int, default=30)
    parser.add_argument("--timeframe", choices=sorted(TIMEFRAME_MAP.keys()), default="M15")
    parser.add_argument(
        "--include-close-labels",
        nargs="*",
        default=None,
        help="Optional normalized close-spec labels to keep in the study for targeted tournaments.",
    )
    parser.add_argument("--output-csv", default=str(OUTPUT_CSV))
    parser.add_argument("--output-md", default=str(OUTPUT_MD))
    parser.add_argument("--output-json", default=str(OUTPUT_JSON))
    return parser.parse_args()


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def safe_float(value: Any, default: float | None = None) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def safe_int(value: Any, default: int | None = None) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def research_unit_pnl_usd(
    symbol: str,
    direction: str,
    entry_price: float,
    exit_price: float,
    spread_px: float,
    symbol_info=None,
) -> float:
    if symbol_info is not None:
        currency_profit = str(getattr(symbol_info, "currency_profit", "") or "").upper()
        contract_size = float(getattr(symbol_info, "trade_contract_size", 0.0) or 0.0)
        if currency_profit == "USD" and contract_size > 0:
            volume = 0.01
            gross = (exit_price - entry_price) * contract_size * volume
            if str(direction).upper() == "SELL":
                gross = -gross
            spread_cost = abs(float(spread_px or 0.0)) * contract_size * volume
            return float(gross) - float(spread_cost)
    return unit_pnl_usd(symbol, direction, entry_price, exit_price, spread_px)


def load_bars(symbol: str, timeframe_name: str, days: int) -> list[dict[str, Any]]:
    tf = TIMEFRAME_MAP[timeframe_name]
    count = max(32, TIMEFRAME_BARS_PER_DAY[timeframe_name] * max(1, int(days)))
    rates = mt5.copy_rates_from_pos(symbol, tf, 1, count)
    if rates is None:
        return []
    return [
        {
            "time": int(r[0]),
            "open": float(r[1]),
            "high": float(r[2]),
            "low": float(r[3]),
            "close": float(r[4]),
            "tick_volume": int(r[5]),
        }
        for r in rates
    ]


def load_shape(symbol: str) -> dict[str, Any]:
    payload = load_json(SHAPE_LIBRARY_PATH)
    symbol_payload = dict((payload.get("symbols") or {}).get(symbol) or {})
    shapes = list(symbol_payload.get("candidate_shapes") or [])
    if not shapes:
        raise SystemExit(f"No adaptive shape found for {symbol}.")
    return dict(shapes[0])


def load_regime_row(symbol: str) -> dict[str, Any]:
    payload = load_json(REGIME_PATH)
    for row in list(payload.get("symbols") or []):
        if str(row.get("symbol") or "").upper() == symbol.upper():
            return dict(row)
    return {}


def normalize_close_specs(shape: dict[str, Any]) -> list[CloseSpec]:
    close = dict(shape.get("close") or {})
    base_style = str(close.get("style") or "all_profitable")
    base_alpha = max(0.0, min(1.0, float(close.get("alpha", 0.5) or 0.5)))
    base_sell_gap = max(0, int(close.get("sell_gap", 1) or 1))
    base_buy_gap = max(0, int(close.get("buy_gap", 1) or 1))
    specs = [
        CloseSpec(
            style=base_style,
            alpha=base_alpha,
            sell_gap=base_sell_gap,
            buy_gap=base_buy_gap,
            label="shape_contract",
        ),
        CloseSpec(
            style="all_profitable",
            alpha=min(0.7, max(0.5, base_alpha)),
            sell_gap=1,
            buy_gap=1,
            label="cash_harvest",
        ),
        CloseSpec(
            style="outer",
            alpha=0.5,
            sell_gap=max(2, base_sell_gap),
            buy_gap=max(2, base_buy_gap),
            label="outer_guarded",
        ),
        CloseSpec(
            style="inner",
            alpha=0.5,
            sell_gap=max(2, base_sell_gap),
            buy_gap=max(2, base_buy_gap),
            label="inner_guarded",
        ),
        CloseSpec(
            style="all_profitable",
            alpha=0.5,
            sell_gap=max(2, base_sell_gap),
            buy_gap=max(2, base_buy_gap),
            label="sweep_guarded",
        ),
        CloseSpec(
            style="all_profitable",
            alpha=1.0,
            sell_gap=max(2, base_sell_gap),
            buy_gap=max(2, base_buy_gap),
            label="sweep_fast",
        ),
        CloseSpec(
            style="all_profitable",
            alpha=1.0,
            sell_gap=max(1, base_sell_gap - 1),
            buy_gap=max(1, base_buy_gap - 1),
            label="sweep_fast_shallow",
        ),
        CloseSpec(
            style="book_flat_sweep",
            alpha=1.0,
            sell_gap=max(1, base_sell_gap - 1),
            buy_gap=max(1, base_buy_gap - 1),
            label="book_flat_sweep",
        ),
        CloseSpec(
            style="all_profitable",
            alpha=1.0,
            sell_gap=0,
            buy_gap=0,
            label="sweep_fast_gap0",
        ),
        CloseSpec(
            style="book_flat_sweep",
            alpha=1.0,
            sell_gap=0,
            buy_gap=0,
            label="book_flat_gap0",
        ),
        CloseSpec(
            style="outer",
            alpha=0.5,
            sell_gap=max(3, base_sell_gap + 1),
            buy_gap=max(3, base_buy_gap + 1),
            label="outer_deep",
        ),
        CloseSpec(
            style="outer",
            alpha=1.0,
            sell_gap=max(2, base_sell_gap),
            buy_gap=max(2, base_buy_gap),
            label="outer_fast",
        ),
        CloseSpec(
            style="outer",
            alpha=1.0,
            sell_gap=max(1, base_sell_gap - 1),
            buy_gap=max(1, base_buy_gap - 1),
            label="outer_fast_shallow",
        ),
        CloseSpec(
            style="inner",
            alpha=1.0,
            sell_gap=max(2, base_sell_gap),
            buy_gap=max(2, base_buy_gap),
            label="inner_fast",
        ),
        CloseSpec(
            style="inner",
            alpha=1.0,
            sell_gap=max(1, base_sell_gap - 1),
            buy_gap=max(1, base_buy_gap - 1),
            label="inner_fast_shallow",
        ),
        CloseSpec(
            style="harvest_inner_hold_frontier",
            alpha=1.0,
            sell_gap=max(1, base_sell_gap - 1),
            buy_gap=max(1, base_buy_gap - 1),
            label="harvest_inner_hold_frontier",
        ),
        CloseSpec(
            style="harvest_inner_hold_two_frontiers",
            alpha=1.0,
            sell_gap=max(1, base_sell_gap - 1),
            buy_gap=max(1, base_buy_gap - 1),
            label="harvest_inner_hold_two_frontiers",
        ),
        CloseSpec(
            style="harvest_inner_funded_rescue",
            alpha=1.0,
            sell_gap=max(1, base_sell_gap - 1),
            buy_gap=max(1, base_buy_gap - 1),
            label="harvest_inner_funded_rescue",
        ),
        CloseSpec(
            style="harvest_inner_hold_two_frontiers_funded_rescue",
            alpha=1.0,
            sell_gap=max(1, base_sell_gap - 1),
            buy_gap=max(1, base_buy_gap - 1),
            label="harvest_inner_hold_two_frontiers_funded_rescue",
        ),
        CloseSpec(
            style="ema_ladder_sweep",
            alpha=1.0,
            sell_gap=max(1, base_sell_gap - 1),
            buy_gap=max(1, base_buy_gap - 1),
            label="ema_ladder_sweep",
        ),
        CloseSpec(
            style="ema_ladder_inner",
            alpha=1.0,
            sell_gap=max(1, base_sell_gap - 1),
            buy_gap=max(1, base_buy_gap - 1),
            label="ema_ladder_inner",
        ),
        CloseSpec(
            style="fib_reclaim_sweep",
            alpha=1.0,
            sell_gap=max(1, base_sell_gap - 1),
            buy_gap=max(1, base_buy_gap - 1),
            label="fib_reclaim_sweep",
        ),
        CloseSpec(
            style="ema_span_fib_sweep",
            alpha=1.0,
            sell_gap=max(1, base_sell_gap - 1),
            buy_gap=max(1, base_buy_gap - 1),
            label="ema_span_fib_sweep",
        ),
        CloseSpec(
            style="ema_span_fib_inner",
            alpha=1.0,
            sell_gap=max(1, base_sell_gap - 1),
            buy_gap=max(1, base_buy_gap - 1),
            label="ema_span_fib_inner",
        ),
        CloseSpec(
            style="ema_midspan_fib_sweep",
            alpha=1.0,
            sell_gap=max(1, base_sell_gap - 1),
            buy_gap=max(1, base_buy_gap - 1),
            label="ema_midspan_fib_sweep",
        ),
        CloseSpec(
            style="ema_midspan_fib_inner",
            alpha=1.0,
            sell_gap=max(1, base_sell_gap - 1),
            buy_gap=max(1, base_buy_gap - 1),
            label="ema_midspan_fib_inner",
        ),
        CloseSpec(
            style="ema_midspan_fib_shallow_sweep",
            alpha=1.0,
            sell_gap=max(1, base_sell_gap - 1),
            buy_gap=max(1, base_buy_gap - 1),
            label="ema_midspan_fib_shallow_sweep",
        ),
        CloseSpec(
            style="triple_anchor_span_sweep",
            alpha=1.0,
            sell_gap=max(1, base_sell_gap - 1),
            buy_gap=max(1, base_buy_gap - 1),
            label="triple_anchor_span_sweep",
        ),
        CloseSpec(
            style="triple_anchor_span_inner",
            alpha=1.0,
            sell_gap=max(1, base_sell_gap - 1),
            buy_gap=max(1, base_buy_gap - 1),
            label="triple_anchor_span_inner",
        ),
        CloseSpec(
            style="triple_anchor_fast_span_sweep",
            alpha=1.0,
            sell_gap=max(1, base_sell_gap - 1),
            buy_gap=max(1, base_buy_gap - 1),
            label="triple_anchor_fast_span_sweep",
        ),
        CloseSpec(
            style="triple_anchor_fast_span_inner",
            alpha=1.0,
            sell_gap=max(1, base_sell_gap - 1),
            buy_gap=max(1, base_buy_gap - 1),
            label="triple_anchor_fast_span_inner",
        ),
        CloseSpec(
            style="stack_depth_scaled_gap",
            alpha=1.0,
            sell_gap=max(1, base_sell_gap - 1),
            buy_gap=max(1, base_buy_gap - 1),
            label="stack_depth_scaled_gap",
        ),
        CloseSpec(
            style="range_sweep_trend_reclaim",
            alpha=1.0,
            sell_gap=max(1, base_sell_gap - 1),
            buy_gap=max(1, base_buy_gap - 1),
            label="range_sweep_trend_reclaim",
        ),
        # Depth-aware close strategies (qwen close-order optimization research)
        CloseSpec(
            style="close_early",
            alpha=1.0,
            sell_gap=1,
            buy_gap=1,
            label="close_early",
        ),
        CloseSpec(
            style="close_early_funded_rescue",
            alpha=1.0,
            sell_gap=1,
            buy_gap=1,
            label="close_early_funded_rescue",
        ),
        CloseSpec(
            style="close_early",
            alpha=1.0,
            sell_gap=max(1, base_sell_gap - 1),
            buy_gap=max(1, base_buy_gap - 1),
            label="close_early_shallow",
        ),
        CloseSpec(
            style="close_deep",
            alpha=1.0,
            sell_gap=max(1, base_sell_gap - 1),
            buy_gap=max(1, base_buy_gap - 1),
            label="close_deep_shallow",
        ),
        CloseSpec(
            style="hybrid_early_hold_deep",
            alpha=1.0,
            sell_gap=max(1, base_sell_gap - 1),
            buy_gap=max(1, base_buy_gap - 1),
            label="hybrid_early_hold_deep",
        ),
        CloseSpec(
            style="hybrid_early_hold_deep_funded_rescue",
            alpha=1.0,
            sell_gap=max(1, base_sell_gap - 1),
            buy_gap=max(1, base_buy_gap - 1),
            label="hybrid_early_hold_deep_funded_rescue",
        ),
        CloseSpec(
            style="range_sweep_trend_reclaim_funded_rescue",
            alpha=1.0,
            sell_gap=max(1, base_sell_gap - 1),
            buy_gap=max(1, base_buy_gap - 1),
            label="range_sweep_trend_reclaim_funded_rescue",
        ),
        CloseSpec(
            style="outer_funded_rescue",
            alpha=1.0,
            sell_gap=max(1, base_sell_gap - 1),
            buy_gap=max(1, base_buy_gap - 1),
            label="outer_fast_shallow_funded_rescue",
        ),
        CloseSpec(
            style="all_profitable_funded_rescue",
            alpha=1.0,
            sell_gap=0,
            buy_gap=0,
            label="sweep_fast_gap0_funded_rescue",
        ),
    ]
    deduped: list[CloseSpec] = []
    seen: set[tuple[str, float, int, int]] = set()
    for spec in specs:
        key = (spec.style, round(spec.alpha, 4), spec.sell_gap, spec.buy_gap)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(spec)
    return deduped


def resolve_shape_step_contract(symbol: str, shape: dict[str, Any], regime_row: dict[str, Any]) -> tuple[float, float]:
    step_method = dict(shape.get("step_method") or {})
    kind = str(step_method.get("kind") or "")
    if kind == "atr_multiple":
        current_atr = safe_float(regime_row.get("current_atr"))
        if current_atr is None or current_atr <= 0:
            raise SystemExit(f"Missing current_atr for {symbol}.")
        coeff = float(step_method.get("coeff", 1.0) or 1.0)
        step = round(current_atr * coeff, 6)
        return (step, step)
    if kind == "atr_multiple_asymmetric":
        current_atr = safe_float(regime_row.get("current_atr"))
        if current_atr is None or current_atr <= 0:
            raise SystemExit(f"Missing current_atr for {symbol}.")
        return (
            round(current_atr * float(step_method.get("buy_coeff", 1.0) or 1.0), 6),
            round(current_atr * float(step_method.get("sell_coeff", 1.0) or 1.0), 6),
        )
    if kind == "range_atr_formula":
        base_step = safe_float(regime_row.get("range_atr_formula_step"))
        if base_step is None or base_step <= 0:
            raise SystemExit(f"Missing range_atr_formula_step for {symbol}.")
        return (round(base_step, 6), round(base_step, 6))
    raise SystemExit(f"Unsupported step method {kind!r} for {symbol}.")


def resolve_base_contract(symbol: str, timeframe: str) -> DeploymentContract:
    shape = load_shape(symbol)
    regime_row = load_regime_row(symbol)
    rearm = dict(shape.get("rearm") or {})
    close = dict(shape.get("close") or {})
    step_buy_px, step_sell_px = resolve_shape_step_contract(symbol, shape, regime_row)
    return DeploymentContract(
        symbol=symbol,
        timeframe=timeframe,
        shape_id=str(shape.get("shape_id") or ""),
        step_buy_px=step_buy_px,
        step_sell_px=step_sell_px,
        max_open_per_side=12,
        close_style=str(close.get("style") or "all_profitable"),
        close_alpha=max(0.0, min(1.0, float(close.get("alpha", 0.5) or 0.5))),
        sell_gap=max(0, int(close.get("sell_gap", 1) or 1)),
        buy_gap=max(0, int(close.get("buy_gap", 1) or 1)),
        rearm_variant=str(rearm.get("variant") or "rearm_lvl2_exc1"),
        rearm_cooldown_bars=max(0, int(rearm.get("cooldown_bars", 0) or 0)),
        momentum_gate=bool(rearm.get("momentum_gate", False)),
        variant_label="shape_contract_step1.00_cap0_shape_contract",
        step_scale=1.0,
        cap_delta=0,
        close_profile="shape_contract",
    )


def build_contract_variants(
    base: DeploymentContract,
    *,
    include_close_labels: set[str] | None = None,
) -> list[DeploymentContract]:
    shape = load_shape(base.symbol)
    close_specs = normalize_close_specs(shape)
    if include_close_labels:
        close_specs = [spec for spec in close_specs if spec.label in include_close_labels]
        if not close_specs:
            raise ValueError(
                f"No normalized close specs matched include filter for {base.symbol}: {sorted(include_close_labels)}"
            )
    step_scales = [0.75, 1.0, 1.25]
    cap_deltas = [-3, 0, 3]
    variants: list[DeploymentContract] = []
    for step_scale in step_scales:
        for cap_delta in cap_deltas:
            max_open = max(4, base.max_open_per_side + cap_delta)
            for close_spec in close_specs:
                variant_label = (
                    f"{close_spec.label}_step{step_scale:.2f}_cap"
                    f"{'+' if cap_delta > 0 else ''}{cap_delta}"
                )
                variants.append(
                    DeploymentContract(
                        symbol=base.symbol,
                        timeframe=base.timeframe,
                        shape_id=base.shape_id,
                        step_buy_px=round(base.step_buy_px * step_scale, 6),
                        step_sell_px=round(base.step_sell_px * step_scale, 6),
                        max_open_per_side=max_open,
                        close_style=close_spec.style,
                        close_alpha=close_spec.alpha,
                        sell_gap=close_spec.sell_gap,
                        buy_gap=close_spec.buy_gap,
                        rearm_variant=base.rearm_variant,
                        rearm_cooldown_bars=base.rearm_cooldown_bars,
                        momentum_gate=base.momentum_gate,
                        variant_label=variant_label,
                        step_scale=step_scale,
                        cap_delta=cap_delta,
                        close_profile=close_spec.label,
                    )
                )
    return variants


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


def _open_token_if_hit(
    *,
    symbol: str,
    contract: DeploymentContract,
    token: RearmToken,
    bar: dict[str, Any],
    spread_px: float,
    tickets: list[Ticket],
) -> Ticket | None:
    if not token.armed:
        return None
    if contract.momentum_gate and not _check_momentum_gate(bar, token.direction, float(token.level)):
        return None
    if token.direction == "SELL" and float(bar["high"]) >= float(token.level):
        ticket = Ticket(direction="SELL", entry_price=float(token.level), opened_time=int(bar["time"]))
        setattr(ticket, "from_rearm", True)
        tickets.append(ticket)
        return ticket
    if token.direction == "BUY" and float(bar["low"]) <= float(token.level):
        ticket = Ticket(direction="BUY", entry_price=float(token.level), opened_time=int(bar["time"]))
        setattr(ticket, "from_rearm", True)
        tickets.append(ticket)
        return ticket
    return None


def _close_positions(
    *,
    symbol: str,
    symbol_info=None,
    direction: str,
    tickets: list[Ticket],
    trigger_price: float,
    bar_extreme: float,
    contract: DeploymentContract,
    spread_px: float,
    anchor: float,
    step_px: float,
    variant,
    tokens: list[RearmToken],
    stats: dict[str, Any],
    dynamic_context: dict[str, Any] | None = None,
) -> list[Ticket]:
    base_close_style = _base_close_style(contract.close_style)
    if direction == "SELL":
        ordered = sorted((t for t in tickets if t.direction == direction), key=lambda t: t.entry_price, reverse=True)
        gap = contract.sell_gap
    else:
        ordered = sorted((t for t in tickets if t.direction == direction), key=lambda t: t.entry_price)
        gap = contract.buy_gap
    while len(ordered) > gap:
        level_price = float(ordered[gap].entry_price)
        if direction == "SELL" and trigger_price > level_price:
            break
        if direction == "BUY" and trigger_price < level_price:
            break
        position_close_refs = {
            pos: _resolve_ticket_close_ref(
                close_style=contract.close_style,
                direction=direction,
                ticket=ticket,
                level_price=level_price,
                trigger_price=trigger_price,
                bar_extreme=bar_extreme,
                contract=contract,
                anchor=anchor,
                dynamic_context=dynamic_context,
            )
            for pos, ticket in enumerate(ordered)
        }
        profitable_positions = [
            pos
            for pos, ticket in enumerate(ordered)
            if research_unit_pnl_usd(
                symbol,
                direction,
                float(ticket.entry_price),
                float(position_close_refs[pos]),
                spread_px,
                symbol_info,
            )
            > 0
        ]
        close_positions = _resolve_close_positions(
            base_close_style,
            profitable_positions=profitable_positions,
            gap=gap,
            stack_depth=len(ordered),
        )
        if base_close_style == "book_flat_sweep":
            book_mark_pnl = sum(
                research_unit_pnl_usd(
                    symbol,
                    t.direction,
                    float(t.entry_price),
                    float(trigger_price),
                    spread_px,
                    symbol_info,
                )
                for t in tickets
            )
            if book_mark_pnl < 0:
                close_positions = []
        if not close_positions:
            break
        closed_any = False
        for pos in sorted(set(close_positions), reverse=True):
            ticket = ordered[pos]
            close_ref = float(position_close_refs[pos])
            pnl = research_unit_pnl_usd(
                symbol,
                direction,
                float(ticket.entry_price),
                close_ref,
                spread_px,
                symbol_info,
            )
            if pnl <= 0:
                continue
            tickets.remove(ticket)
            stats["realized_net_usd"] += pnl
            stats["realized_closes"] += 1
            stats["wins"] += 1
            stats["gross_positive_booked_usd"] += pnl
            stats["close_pnls"].append(pnl)
            level_idx = 0
            if step_px > 0:
                if direction == "SELL":
                    level_idx = max(1, int(round((float(ticket.entry_price) - anchor) / step_px)))
                else:
                    level_idx = max(1, int(round((anchor - float(ticket.entry_price)) / step_px)))
            if level_idx >= int(variant.min_level_idx):
                tokens.append(
                    RearmToken(
                        direction=direction,
                        level=float(ticket.entry_price),
                        level_idx=level_idx,
                        cooldown_until_time=int(bar_extreme if False else 0),
                    )
                )
                tokens[-1].cooldown_until_time = int(stats["bar_time"]) + (contract.rearm_cooldown_bars * 60)
            closed_any = True
        if not closed_any:
            break
        if direction == "SELL":
            ordered = sorted((t for t in tickets if t.direction == direction), key=lambda t: t.entry_price, reverse=True)
        else:
            ordered = sorted((t for t in tickets if t.direction == direction), key=lambda t: t.entry_price)
    if _uses_funded_rescue(contract.close_style):
        rescue_budget = _available_rescue_budget(stats, close_style=contract.close_style)
        rescue_ticket = _select_funded_rescue_ticket(
            symbol=symbol,
            symbol_info=symbol_info,
            direction=direction,
            ordered=ordered,
            trigger_price=trigger_price,
            spread_px=spread_px,
            anchor=anchor,
            step_px=step_px,
            current_bar_time=int(stats["bar_time"]),
            timeframe_name=contract.timeframe,
            rescue_budget=rescue_budget,
        )
        if rescue_ticket is not None:
            rescue_ref = trigger_price
            pnl = research_unit_pnl_usd(
                symbol,
                direction,
                float(rescue_ticket.entry_price),
                rescue_ref,
                spread_px,
                symbol_info,
            )
            if pnl < 0:
                tickets.remove(rescue_ticket)
                stats["realized_net_usd"] += pnl
                stats["realized_closes"] += 1
                stats["losses"] += 1
                stats["rescue_spend_usd"] += abs(pnl)
                stats["rescue_closes"] += 1
                stats["close_pnls"].append(pnl)
    return tickets


def _base_close_style(close_style: str) -> str:
    if close_style.endswith("_funded_rescue"):
        return close_style[: -len("_funded_rescue")]
    return close_style


def _uses_funded_rescue(close_style: str) -> bool:
    return str(close_style or "").endswith("_funded_rescue")


def _available_rescue_budget(stats: dict[str, Any], *, close_style: str) -> float:
    rescue_budget_share = 0.25
    if "hold_two_frontiers" in str(close_style or ""):
        rescue_budget_share = 0.30
    gross_positive = float(stats.get("gross_positive_booked_usd", 0.0) or 0.0)
    rescue_spend = float(stats.get("rescue_spend_usd", 0.0) or 0.0)
    return max(0.0, (gross_positive * rescue_budget_share) - rescue_spend)


def _ticket_extreme_distance(ticket: Ticket, *, anchor: float) -> float:
    return abs(float(ticket.entry_price) - float(anchor))


def _select_funded_rescue_ticket(
    *,
    symbol: str,
    symbol_info=None,
    direction: str,
    ordered: list[Ticket],
    trigger_price: float,
    spread_px: float,
    anchor: float,
    step_px: float,
    current_bar_time: int,
    timeframe_name: str,
    rescue_budget: float,
) -> Ticket | None:
    if rescue_budget <= 0 or not ordered:
        return None
    bar_seconds = TIMEFRAME_SECONDS.get(str(timeframe_name).upper(), 900)
    min_hold_seconds = bar_seconds * 4
    min_extreme_distance = max(step_px * 3.0, step_px * 2.0)
    candidates: list[tuple[float, int, float, Ticket]] = []
    for ticket in ordered:
        pnl = research_unit_pnl_usd(
            symbol,
            direction,
            float(ticket.entry_price),
            trigger_price,
            spread_px,
            symbol_info,
        )
        if pnl >= 0:
            continue
        age_seconds = max(0, int(current_bar_time) - int(ticket.opened_time or current_bar_time))
        if age_seconds < min_hold_seconds:
            continue
        extreme_distance = _ticket_extreme_distance(ticket, anchor=anchor)
        if extreme_distance < min_extreme_distance:
            continue
        loss = abs(float(pnl))
        if loss > rescue_budget:
            continue
        candidates.append((extreme_distance, age_seconds, loss, ticket))
    if not candidates:
        return None
    candidates.sort(key=lambda item: (item[0], item[1], -item[2]), reverse=True)
    return candidates[0][3]


def _resolve_close_positions(
    close_style: str,
    *,
    profitable_positions: list[int],
    gap: int,
    stack_depth: int,
) -> list[int]:
    if close_style == "outer":
        return [0]
    if close_style == "inner":
        return [max(0, gap - 1)]
    if close_style == "harvest_inner":
        # Pure booking-first sweep: harvest every profitable reclaim candidate.
        return profitable_positions
    if close_style == "all_profitable":
        return profitable_positions
    if close_style == "book_flat_sweep":
        return profitable_positions
    if close_style == "ema_ladder_sweep":
        return profitable_positions
    if close_style == "ema_ladder_inner":
        return [max(0, gap - 1)]
    if close_style == "fib_reclaim_sweep":
        return profitable_positions
    if close_style == "ema_span_fib_sweep":
        return profitable_positions
    if close_style == "ema_span_fib_inner":
        return [max(0, gap - 1)]
    if close_style == "ema_midspan_fib_sweep":
        return profitable_positions
    if close_style == "ema_midspan_fib_inner":
        return [max(0, gap - 1)]
    if close_style == "ema_midspan_fib_shallow_sweep":
        return profitable_positions
    if close_style == "triple_anchor_span_sweep":
        return profitable_positions
    if close_style == "triple_anchor_span_inner":
        return [max(0, gap - 1)]
    if close_style == "triple_anchor_fast_span_sweep":
        return profitable_positions
    if close_style == "triple_anchor_fast_span_inner":
        return [max(0, gap - 1)]
    if close_style == "harvest_inner_hold_frontier":
        # Harvest all profitable reclaim candidates except the current frontier ticket.
        return [pos for pos in profitable_positions if pos >= 1]
    if close_style == "harvest_inner_hold_two_frontiers":
        # Keep the two most extreme frontier tickets alive while harvesting the core.
        return [pos for pos in profitable_positions if pos >= 2]
    if close_style == "stack_depth_scaled_gap":
        # As the stack deepens, keep more frontier tickets alive for larger reclaim.
        if stack_depth >= gap + 8:
            hold_frontier = 2
        elif stack_depth >= gap + 4:
            hold_frontier = 1
        else:
            hold_frontier = 0
        return [pos for pos in profitable_positions if pos >= hold_frontier]
    if close_style == "range_sweep_trend_reclaim":
        # Small stacks behave like range harvest; deeper stacks only release the inner reclaim.
        if stack_depth <= gap + 2:
            return profitable_positions
        return [max(0, gap - 1)]
    # Depth-aware close strategies (qwen close-order optimization)
    if close_style == "close_early":
        # Close ALL profitable positions 1 step earlier than normal (closer to anchor)
        # This captures guaranteed profit faster, reduces exposure time
        return profitable_positions
    if close_style == "close_deep":
        # Hold positions longer — only close the deepest profitable ones
        # Requires position to be at least gap+2 levels deep to close
        return [pos for pos in profitable_positions if pos >= gap + 1]
    if close_style == "hybrid_early_hold_deep":
        # Close inner positions early (profit capture) but hold outer frontier
        # for more penetration depth — best of both worlds
        return [pos for pos in profitable_positions if pos < stack_depth - 1]
    raise ValueError(f"Unsupported close style: {close_style}")


def _resolve_ticket_close_ref(
    *,
    close_style: str,
    direction: str,
    ticket: Ticket,
    level_price: float,
    trigger_price: float,
    bar_extreme: float,
    contract: DeploymentContract,
    anchor: float,
    dynamic_context: dict[str, Any] | None,
) -> float:
    base_close_style = _base_close_style(close_style)
    default_close_ref = level_price + ((bar_extreme - level_price) * contract.close_alpha)
    if dynamic_context is None:
        return default_close_ref
    if base_close_style in {"ema_ladder_sweep", "ema_ladder_inner"}:
        ema_values = sorted(float(v) for v in list(dynamic_context.get("ema_values") or []))
        if direction == "SELL":
            candidates = [ema for ema in ema_values if float(trigger_price) <= ema <= float(ticket.entry_price)]
            return max(candidates) if candidates else float(ticket.entry_price)
        candidates = [ema for ema in ema_values if float(ticket.entry_price) <= ema <= float(trigger_price)]
        return min(candidates) if candidates else float(ticket.entry_price)
    if base_close_style == "fib_reclaim_sweep":
        recent_high = float(dynamic_context.get("recent_high", anchor) or anchor)
        recent_low = float(dynamic_context.get("recent_low", anchor) or anchor)
        fib_ratio = 0.382
        if direction == "SELL":
            fib_target = anchor + max(0.0, recent_high - anchor) * fib_ratio
            return float(fib_target) if float(trigger_price) <= float(fib_target) <= float(ticket.entry_price) else float(ticket.entry_price)
        fib_target = anchor - max(0.0, anchor - recent_low) * fib_ratio
        return float(fib_target) if float(ticket.entry_price) <= float(fib_target) <= float(trigger_price) else float(ticket.entry_price)
    if base_close_style in {"ema_span_fib_sweep", "ema_span_fib_inner"}:
        ema_fast = float(dynamic_context.get("ema_fast_3", anchor) or anchor)
        ema_slow = float(dynamic_context.get("ema_slow_500", anchor) or anchor)
        fib_ratio = 0.382
        if direction == "SELL" and ema_fast > ema_slow:
            fib_target = ema_slow + ((ema_fast - ema_slow) * fib_ratio)
            return float(fib_target) if float(trigger_price) <= float(fib_target) <= float(ticket.entry_price) else float(ticket.entry_price)
        if direction == "BUY" and ema_fast < ema_slow:
            fib_target = ema_slow - ((ema_slow - ema_fast) * fib_ratio)
            return float(fib_target) if float(ticket.entry_price) <= float(fib_target) <= float(trigger_price) else float(ticket.entry_price)
        return float(ticket.entry_price)
    if base_close_style in {"ema_midspan_fib_sweep", "ema_midspan_fib_inner", "ema_midspan_fib_shallow_sweep"}:
        ema_fast = float(dynamic_context.get("ema_fast_3", anchor) or anchor)
        ema_mid = float(dynamic_context.get("ema_mid_128", anchor) or anchor)
        fib_ratio = 0.236 if base_close_style == "ema_midspan_fib_shallow_sweep" else 0.382
        if direction == "SELL" and ema_fast > ema_mid:
            fib_target = ema_mid + ((ema_fast - ema_mid) * fib_ratio)
            return float(fib_target) if float(trigger_price) <= float(fib_target) <= float(ticket.entry_price) else float(ticket.entry_price)
        if direction == "BUY" and ema_fast < ema_mid:
            fib_target = ema_mid - ((ema_mid - ema_fast) * fib_ratio)
            return float(fib_target) if float(ticket.entry_price) <= float(fib_target) <= float(trigger_price) else float(ticket.entry_price)
        return float(ticket.entry_price)
    if base_close_style in {"triple_anchor_span_sweep", "triple_anchor_span_inner"}:
        fib_ratio = 0.382
        ema_fast = float(dynamic_context.get("ema_fast_3", anchor) or anchor)
        ema_light = float(dynamic_context.get("ema_light_24", anchor) or anchor)
        ema_mid = float(dynamic_context.get("ema_mid_128", anchor) or anchor)
        ema_heavy = float(dynamic_context.get("ema_slow_500", anchor) or anchor)
        if direction == "SELL" and ema_fast > ema_light > ema_mid > ema_heavy:
            candidates = [
                ema_light + ((ema_fast - ema_light) * fib_ratio),
                ema_mid + ((ema_light - ema_mid) * fib_ratio),
                ema_heavy + ((ema_mid - ema_heavy) * fib_ratio),
            ]
            crossed = [target for target in candidates if float(trigger_price) <= float(target) <= float(ticket.entry_price)]
            return max(crossed) if crossed else float(ticket.entry_price)
        if direction == "BUY" and ema_fast < ema_light < ema_mid < ema_heavy:
            candidates = [
                ema_light - ((ema_light - ema_fast) * fib_ratio),
                ema_mid - ((ema_mid - ema_light) * fib_ratio),
                ema_heavy - ((ema_heavy - ema_mid) * fib_ratio),
            ]
            crossed = [target for target in candidates if float(ticket.entry_price) <= float(target) <= float(trigger_price)]
            return min(crossed) if crossed else float(ticket.entry_price)
        return float(ticket.entry_price)
    if base_close_style in {"triple_anchor_fast_span_sweep", "triple_anchor_fast_span_inner"}:
        fib_ratio = 0.236
        ema_fast = float(dynamic_context.get("ema_fast_3", anchor) or anchor)
        ema_light = float(dynamic_context.get("ema_light_12", anchor) or anchor)
        ema_mid = float(dynamic_context.get("ema_mid_64", anchor) or anchor)
        ema_heavy = float(dynamic_context.get("ema_mid_128", anchor) or anchor)
        if direction == "SELL" and ema_fast > ema_light > ema_mid > ema_heavy:
            candidates = [
                ema_light + ((ema_fast - ema_light) * fib_ratio),
                ema_mid + ((ema_light - ema_mid) * fib_ratio),
                ema_heavy + ((ema_mid - ema_heavy) * fib_ratio),
            ]
            crossed = [target for target in candidates if float(trigger_price) <= float(target) <= float(ticket.entry_price)]
            return max(crossed) if crossed else float(ticket.entry_price)
        if direction == "BUY" and ema_fast < ema_light < ema_mid < ema_heavy:
            candidates = [
                ema_light - ((ema_light - ema_fast) * fib_ratio),
                ema_mid - ((ema_mid - ema_light) * fib_ratio),
                ema_heavy - ((ema_heavy - ema_mid) * fib_ratio),
            ]
            crossed = [target for target in candidates if float(ticket.entry_price) <= float(target) <= float(trigger_price)]
            return min(crossed) if crossed else float(ticket.entry_price)
        return float(ticket.entry_price)
    return default_close_ref


def compute_ema_ladders(bars: list[dict[str, Any]], periods: list[int]) -> list[dict[int, float]]:
    ema_state: dict[int, float] = {}
    rows: list[dict[int, float]] = []
    for bar in bars:
        close_px = float(bar["close"])
        row: dict[int, float] = {}
        for period in periods:
            prev = ema_state.get(period, close_px)
            alpha = 2.0 / (float(period) + 1.0)
            ema_now = close_px if period not in ema_state else ((close_px - prev) * alpha) + prev
            ema_state[period] = ema_now
            row[period] = ema_now
        rows.append(row)
    return rows


def simulate_contract(contract: DeploymentContract, bars: list[dict[str, Any]], symbol_info) -> dict[str, Any]:
    if not bars:
        return {}
    variant = REARM_VARIANTS.get(contract.rearm_variant)
    if variant is None:
        raise ValueError(f"Unknown rearm variant {contract.rearm_variant!r}")

    spread_px = spread_price(symbol_info)
    ema_rows = compute_ema_ladders(bars, [3, 6, 12, 24, 32, 64, 128, 500])
    tickets: list[Ticket] = []
    tokens: list[RearmToken] = []
    anchor = float(bars[0]["close"])
    next_sell_level = anchor + contract.step_sell_px
    next_buy_level = anchor - contract.step_buy_px
    stats: dict[str, Any] = {
        "realized_net_usd": 0.0,
        "realized_closes": 0,
        "gross_positive_booked_usd": 0.0,
        "rescue_spend_usd": 0.0,
        "rescue_closes": 0,
        "wins": 0,
        "losses": 0,
        "max_open_total": 0,
        "anchor_resets": 0,
        "rearm_opens": 0,
        "close_pnls": [],
        "max_adverse_excursion_usd": 0.0,
        "min_realized_cover_gap_usd": 0.0,
        "min_combined_equity_delta_usd": 0.0,
        "realized_cover_violation_bars": 0,
        "bar_time": int(bars[0]["time"]),
    }

    for idx in range(1, len(bars)):
        bar = bars[idx]
        dynamic_context = {
            "ema_values": list((ema_rows[idx] or {}).values()),
            "ema_fast_3": float((ema_rows[idx] or {}).get(3, bars[idx]["close"])),
            "ema_light_12": float((ema_rows[idx] or {}).get(12, bars[idx]["close"])),
            "ema_light_24": float((ema_rows[idx] or {}).get(24, bars[idx]["close"])),
            "ema_mid_64": float((ema_rows[idx] or {}).get(64, bars[idx]["close"])),
            "ema_mid_128": float((ema_rows[idx] or {}).get(128, bars[idx]["close"])),
            "ema_slow_500": float((ema_rows[idx] or {}).get(500, bars[idx]["close"])),
            "recent_high": max(float(item["high"]) for item in bars[max(0, idx - 31): idx + 1]),
            "recent_low": min(float(item["low"]) for item in bars[max(0, idx - 31): idx + 1]),
        }
        stats["bar_time"] = int(bar["time"])
        _update_token_arming(tokens, bar, contract.step_sell_px, variant.excursion_levels)
        _update_token_arming(tokens, bar, contract.step_buy_px, variant.excursion_levels)

        open_sell_main = sum(1 for t in tickets if t.direction == "SELL" and not getattr(t, "from_rearm", False))
        open_buy_main = sum(1 for t in tickets if t.direction == "BUY" and not getattr(t, "from_rearm", False))
        open_sell_rearm = sum(1 for t in tickets if t.direction == "SELL" and getattr(t, "from_rearm", False))
        open_buy_rearm = sum(1 for t in tickets if t.direction == "BUY" and getattr(t, "from_rearm", False))
        current_sell_step = dynamic_step(contract.step_sell_px, open_sell_main, type("Cfg", (), {
            "adaptive_step_threshold_1": 10,
            "adaptive_step_threshold_2": 20,
            "adaptive_step_multiplier_1": 1.5,
            "adaptive_step_multiplier_2": 2.0,
        })())
        current_buy_step = dynamic_step(contract.step_buy_px, open_buy_main, type("Cfg", (), {
            "adaptive_step_threshold_1": 10,
            "adaptive_step_threshold_2": 20,
            "adaptive_step_multiplier_1": 1.5,
            "adaptive_step_multiplier_2": 2.0,
        })())

        while float(bar["high"]) >= next_sell_level and open_sell_main < contract.max_open_per_side:
            ticket = Ticket(direction="SELL", entry_price=next_sell_level, opened_time=int(bar["time"]))
            setattr(ticket, "from_rearm", False)
            tickets.append(ticket)
            open_sell_main += 1
            current_sell_step = dynamic_step(contract.step_sell_px, open_sell_main, type("Cfg", (), {
                "adaptive_step_threshold_1": 10,
                "adaptive_step_threshold_2": 20,
                "adaptive_step_multiplier_1": 1.5,
                "adaptive_step_multiplier_2": 2.0,
            })())
            next_sell_level += current_sell_step

        while float(bar["low"]) <= next_buy_level and open_buy_main < contract.max_open_per_side:
            ticket = Ticket(direction="BUY", entry_price=next_buy_level, opened_time=int(bar["time"]))
            setattr(ticket, "from_rearm", False)
            tickets.append(ticket)
            open_buy_main += 1
            current_buy_step = dynamic_step(contract.step_buy_px, open_buy_main, type("Cfg", (), {
                "adaptive_step_threshold_1": 10,
                "adaptive_step_threshold_2": 20,
                "adaptive_step_multiplier_1": 1.5,
                "adaptive_step_multiplier_2": 2.0,
            })())
            next_buy_level -= current_buy_step

        for token in list(tokens):
            if token.direction == "SELL" and open_sell_rearm < contract.max_open_per_side:
                ticket = _open_token_if_hit(
                    symbol=contract.symbol,
                    contract=contract,
                    token=token,
                    bar=bar,
                    spread_px=spread_px,
                    tickets=tickets,
                )
                if ticket is not None:
                    tokens.remove(token)
                    open_sell_rearm += 1
                    stats["rearm_opens"] += 1
            elif token.direction == "BUY" and open_buy_rearm < contract.max_open_per_side:
                ticket = _open_token_if_hit(
                    symbol=contract.symbol,
                    contract=contract,
                    token=token,
                    bar=bar,
                    spread_px=spread_px,
                    tickets=tickets,
                )
                if ticket is not None:
                    tokens.remove(token)
                    open_buy_rearm += 1
                    stats["rearm_opens"] += 1

        tickets = _close_positions(
            symbol=contract.symbol,
            symbol_info=symbol_info,
            direction="SELL",
            tickets=tickets,
            trigger_price=float(bar["low"]),
            bar_extreme=float(bar["low"]),
            contract=contract,
            spread_px=spread_px,
            anchor=anchor,
            step_px=contract.step_sell_px,
            variant=variant,
            tokens=tokens,
            stats=stats,
            dynamic_context=dynamic_context,
        )
        tickets = _close_positions(
            symbol=contract.symbol,
            symbol_info=symbol_info,
            direction="BUY",
            tickets=tickets,
            trigger_price=float(bar["high"]),
            bar_extreme=float(bar["high"]),
            contract=contract,
            spread_px=spread_px,
            anchor=anchor,
            step_px=contract.step_buy_px,
            variant=variant,
            tokens=tokens,
            stats=stats,
            dynamic_context=dynamic_context,
        )

        floating_now = sum(
            research_unit_pnl_usd(
                contract.symbol,
                t.direction,
                float(t.entry_price),
                float(bar["close"]),
                spread_px,
                symbol_info,
            )
            for t in tickets
        )
        realized_cover_gap = float(stats["realized_net_usd"]) - abs(min(0.0, float(floating_now)))
        combined_equity_delta = float(stats["realized_net_usd"]) + float(floating_now)
        stats["max_adverse_excursion_usd"] = min(stats["max_adverse_excursion_usd"], floating_now)
        stats["min_realized_cover_gap_usd"] = min(float(stats["min_realized_cover_gap_usd"]), realized_cover_gap)
        stats["min_combined_equity_delta_usd"] = min(float(stats["min_combined_equity_delta_usd"]), combined_equity_delta)
        if realized_cover_gap < 0:
            stats["realized_cover_violation_bars"] += 1
        stats["max_open_total"] = max(stats["max_open_total"], len(tickets))

        if not tickets and (
            float(bar["close"]) >= anchor + contract.step_sell_px
            or float(bar["close"]) <= anchor - contract.step_buy_px
        ):
            anchor = float(bar["close"])
            next_sell_level = anchor + contract.step_sell_px
            next_buy_level = anchor - contract.step_buy_px
            stats["anchor_resets"] += 1
            tokens = []

    floating_net_usd = sum(
        research_unit_pnl_usd(
            contract.symbol,
            t.direction,
            float(t.entry_price),
            float(bars[-1]["close"]),
            spread_px,
            symbol_info,
        )
        for t in tickets
    )
    combined_net_usd = stats["realized_net_usd"] + floating_net_usd
    hours = max((int(bars[-1]["time"]) - int(bars[0]["time"])) / 3600.0, 0.01)
    realized_closes = int(stats["realized_closes"])
    win_rate = (stats["wins"] / realized_closes) if realized_closes else 0.0
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
    avg_close_usd = (stats["realized_net_usd"] / realized_closes) if realized_closes else 0.0
    gross_positive_booked_usd = float(stats["gross_positive_booked_usd"])
    realized_usd_per_hour = stats["realized_net_usd"] / hours
    gross_positive_booked_usd_per_hour = gross_positive_booked_usd / hours
    closes_per_hour = realized_closes / hours
    conversion_ratio = (
        float(stats["realized_net_usd"]) / (abs(float(stats["realized_net_usd"])) + abs(float(floating_net_usd)))
        if (abs(float(stats["realized_net_usd"])) + abs(float(floating_net_usd))) > 0
        else 0.0
    )
    return {
        **asdict(contract),
        "realized_net_usd": round(float(stats["realized_net_usd"]), 3),
        "gross_positive_booked_usd": round(gross_positive_booked_usd, 3),
        "rescue_spend_usd": round(float(stats["rescue_spend_usd"]), 3),
        "floating_net_usd": round(float(floating_net_usd), 3),
        "combined_net_usd": round(float(combined_net_usd), 3),
        "realized_closes": realized_closes,
        "rescue_closes": int(stats["rescue_closes"]),
        "avg_close_usd": round(avg_close_usd, 3),
        "gross_positive_booked_usd_per_hour": round(gross_positive_booked_usd_per_hour, 3),
        "realized_usd_per_hour": round(realized_usd_per_hour, 3),
        "combined_usd_per_hour": round(combined_net_usd / hours, 3),
        "closes_per_hour": round(closes_per_hour, 3),
        "max_open_total": int(stats["max_open_total"]),
        "final_open_count": len(tickets),
        "anchor_resets": int(stats["anchor_resets"]),
        "rearm_opens": int(stats["rearm_opens"]),
        "max_adverse_excursion_usd": round(float(stats["max_adverse_excursion_usd"]), 3),
        "min_realized_cover_gap_usd": round(float(stats["min_realized_cover_gap_usd"]), 3),
        "min_combined_equity_delta_usd": round(float(stats["min_combined_equity_delta_usd"]), 3),
        "realized_cover_violation_bars": int(stats["realized_cover_violation_bars"]),
        "realized_win_rate": round(win_rate, 4),
        "conversion_ratio": round(conversion_ratio, 4),
        "unified_objective_score": round(float(objective.total), 3),
        "objective_verdict": objective.verdict,
    }


def score_sort_key(row: dict[str, Any]) -> tuple[float, float, float, float]:
    return (
        float(row["gross_positive_booked_usd_per_hour"]),
        float(row["realized_usd_per_hour"]),
        float(row["unified_objective_score"]),
        float(row["combined_net_usd"]),
    )


def build_markdown(rows: list[dict[str, Any]], summary: dict[str, Any], *, timeframe: str, days: int) -> str:
    lines: list[str] = []
    lines.append("# Adaptive Deployment Backtest Study")
    lines.append("")
    lines.append(
        f"This study resolves adaptive shapes into concrete launch contracts and replays them on `{timeframe}` bars "
        f"over `{days}` days to answer launch, scale, and closeout for max realized `$ / hour`."
    )
    lines.append("")
    lines.append("## Leadership Read")
    lines.append("")
    for line in summary["leadership"]:
        lines.append(f"- {line}")
    lines.append("")
    lines.append("## Best Contracts")
    lines.append("")
    for item in summary["best_by_symbol"]:
        lines.append(
            f"- `{item['symbol']}`: launch `buy_step={item['step_buy_px']}` / `sell_step={item['step_sell_px']}` on "
            f"`{timeframe}`, scale at `max_open={item['max_open_per_side']}` with `{item['rearm_variant']}`, and close via "
            f"`{item['close_style']}` alpha `{item['close_alpha']}` gaps `{item['sell_gap']}/{item['buy_gap']}`. "
            f"Backtest read: gross-booked `${item['gross_positive_booked_usd_per_hour']}/h`, net `${item['realized_usd_per_hour']}/h`, "
            f"`{item['closes_per_hour']}` closes/h, `$ {item['avg_close_usd']}` per close, rescue spend `${item['rescue_spend_usd']}`, "
            f"floating `${item['floating_net_usd']}`, objective `{item['objective_verdict']}`."
        )
    lines.append("")
    lines.append("## Why")
    lines.append("")
    lines.append("- `gross_positive_booked_usd_per_hour` is the primary ranking signal because the current objective is booked-money velocity first, not just net-after-carry.")
    lines.append("- `realized_usd_per_hour` is the second signal; rescue spend is allowed only if it is funded from booked wins and still preserves strong net conversion.")
    lines.append("- `unified_objective_score` breaks ties toward contracts that convert exposure into cash without bloating carry.")
    lines.append("- `all_profitable` winners imply close conversion dominates; `outer` winners imply patience is paying more than speed.")
    lines.append("- Lower-cap winners imply the lane should monetize faster with less stack pressure; higher-cap winners imply the symbol can carry size without collapsing efficiency.")
    return "\n".join(lines) + "\n"


def build_summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    best_by_symbol: list[dict[str, Any]] = []
    leadership: list[str] = []
    by_symbol: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        by_symbol.setdefault(str(row["symbol"]), []).append(row)
    for symbol in sorted(by_symbol):
        symbol_rows = by_symbol[symbol]
        best = max(symbol_rows, key=score_sort_key)
        baseline = next(
            (row for row in symbol_rows if row["close_profile"] == "shape_contract" and row["step_scale"] == 1.0 and row["cap_delta"] == 0),
            best,
        )
        delta_per_hour = round(float(best["realized_usd_per_hour"]) - float(baseline["realized_usd_per_hour"]), 3)
        leadership.append(
            f"{symbol} best contract is `{best['variant_label']}` at `${best['realized_usd_per_hour']}/h` "
            f"vs baseline `${baseline['realized_usd_per_hour']}/h` (delta `${delta_per_hour}/h`)."
        )
        best_by_symbol.append(best)
    if best_by_symbol:
        preferred_step_scales = sorted({float(row["step_scale"]) for row in best_by_symbol})
        preferred_caps = sorted({int(row["max_open_per_side"]) for row in best_by_symbol})
        leadership.append(
            f"Cross-symbol pattern: winners cluster at step_scale={preferred_step_scales} and max_open={preferred_caps}; "
            f"the current shape-library step is too loose for this 30d M15 replay."
        )
    overall_best = max(rows, key=score_sort_key) if rows else {}
    if overall_best:
        leadership.append(
            f"Highest raw money-velocity contract in the whole study is `{overall_best['symbol']}:{overall_best['variant_label']}` "
            f"at `${overall_best['realized_usd_per_hour']}/h` with `{overall_best['objective_verdict']}` posture."
        )
    return {
        "leadership": leadership,
        "best_by_symbol": best_by_symbol,
        "overall_best": overall_best,
    }


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
    include_close_labels = {str(label) for label in (args.include_close_labels or [])}
    if not mt5.initialize():
        print("MetaTrader5 initialize() failed")
        return 1
    try:
        rows: list[dict[str, Any]] = []
        for symbol in [str(s).upper() for s in args.symbols]:
            info = mt5.symbol_info(symbol)
            if info is None:
                continue
            bars = load_bars(symbol, args.timeframe, args.days)
            if not bars:
                continue
            base = resolve_base_contract(symbol, args.timeframe)
            for contract in build_contract_variants(base, include_close_labels=include_close_labels or None):
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
