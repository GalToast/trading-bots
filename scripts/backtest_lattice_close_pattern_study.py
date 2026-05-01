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

from backtest_adaptive_deployment_study import TIMEFRAME_MAP, load_bars
from live_penetration_lattice_shadow import REARM_VARIANTS, RearmToken, _check_momentum_gate
from penetration_lattice_lab_v2 import dynamic_step, spread_price, unit_pnl_usd
from unified_objective import ObjectiveInput, UnifiedObjective


ROOT = Path(__file__).resolve().parent.parent
DEPLOYMENT_STUDY_PATH = ROOT / "reports" / "adaptive_deployment_backtest_study.json"
OUTPUT_CSV = ROOT / "reports" / "lattice_close_pattern_study.csv"
OUTPUT_MD = ROOT / "reports" / "lattice_close_pattern_study.md"
OUTPUT_JSON = ROOT / "reports" / "lattice_close_pattern_study.json"


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
class ClosePatternPolicy:
    name: str
    mode: str
    description: str
    close_style: str = "outer"
    close_alpha: float = 0.0
    sell_gap: int = 1
    buy_gap: int = 1
    retrace_steps: int | None = None
    cross_anchor_steps: int | None = None
    mirror_depth: bool = False
    hybrid_profile: str | None = None


@dataclass(frozen=True)
class CloseStudyContract:
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
    policy_name: str
    policy_mode: str
    policy_description: str
    close_style: str
    close_alpha: float
    sell_gap: int
    buy_gap: int
    retrace_steps: int | None
    cross_anchor_steps: int | None
    mirror_depth: bool
    hybrid_profile: str | None


@dataclass
class StudyTicket:
    direction: str
    entry_price: float
    opened_time: int
    level_idx: int
    from_rearm: bool = False


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


POLICIES = [
    ClosePatternPolicy(
        name="current_contract",
        mode="current_contract",
        description="Current best adaptive contract as selected by the deployment study.",
    ),
    ClosePatternPolicy(
        name="penetration_outer_gap1_exact",
        mode="penetration",
        description="Classic outer-first penetration. Example: level 5 closes when level 4 is revisited.",
        close_style="outer",
        close_alpha=0.0,
        sell_gap=1,
        buy_gap=1,
    ),
    ClosePatternPolicy(
        name="penetration_outer_gap2_exact",
        mode="penetration",
        description="Deeper penetration before monetizing. Example: level 5 waits for level 3.",
        close_style="outer",
        close_alpha=0.0,
        sell_gap=2,
        buy_gap=2,
    ),
    ClosePatternPolicy(
        name="penetration_allprof_gap1_exact",
        mode="penetration",
        description="Close every profitable ticket once price penetrates back one level.",
        close_style="all_profitable",
        close_alpha=0.0,
        sell_gap=1,
        buy_gap=1,
    ),
    ClosePatternPolicy(
        name="independent_stepback1_exact",
        mode="independent",
        description="Each ticket is its own mini-lattice. Example: 5 closes at 4, 4 at 3, 1 at 0.",
        retrace_steps=1,
    ),
    ClosePatternPolicy(
        name="independent_anchor_zero_exact",
        mode="independent",
        description="Every ticket holds until the original anchor is revisited. Example: 5 closes at 0.",
        cross_anchor_steps=0,
    ),
    ClosePatternPolicy(
        name="independent_through_zero_1_exact",
        mode="independent",
        description="Every ticket waits one step through the anchor. Example: 5 closes at -1.",
        cross_anchor_steps=1,
    ),
    ClosePatternPolicy(
        name="independent_far_side_mirror_exact",
        mode="independent",
        description="Every ticket waits for a full mirrored traverse. Example: 5 closes at -5.",
        mirror_depth=True,
    ),
    ClosePatternPolicy(
        name="inner_guarded",
        mode="penetration",
        description="Team candidate: reclaim deeper inventory first with inner selection and guarded alpha.",
        close_style="inner",
        close_alpha=0.5,
    ),
    ClosePatternPolicy(
        name="sweep_guarded",
        mode="penetration",
        description="Team candidate: close every profitable ticket once price reclaims a guarded deeper level.",
        close_style="all_profitable",
        close_alpha=0.5,
    ),
    ClosePatternPolicy(
        name="outer_deep",
        mode="penetration",
        description="Team candidate: outer-first, but only after a deeper reclaim than the current control.",
        close_style="outer",
        close_alpha=1.0,
    ),
    ClosePatternPolicy(
        name="inner_fast",
        mode="penetration",
        description="Team candidate: fast inner reclaim. Inner selection with full alpha after gap>=2 recovery.",
        close_style="inner",
        close_alpha=1.0,
    ),
    ClosePatternPolicy(
        name="outer_fast",
        mode="penetration",
        description="Team candidate: fast outer reclaim on a deeper gap than the gap1 cash-harvest family.",
        close_style="outer",
        close_alpha=1.0,
    ),
    ClosePatternPolicy(
        name="hybrid_stepback_zero_cross",
        mode="hybrid",
        description="Codex hybrid: depths 1-2 close one step back, depths 3-5 close at the anchor, depths 6+ wait one step through zero.",
        hybrid_profile="stepback_zero_cross",
    ),
    ClosePatternPolicy(
        name="hybrid_fast_reclaim_anchor",
        mode="hybrid",
        description="Codex hybrid: depths 1-3 close one step back, deeper tickets close at the anchor.",
        hybrid_profile="fast_reclaim_anchor",
    ),
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Backtest alternative lattice close patterns on the current best adaptive deployment contracts."
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


def build_contract_variants(base: BestContract) -> list[CloseStudyContract]:
    variants: list[CloseStudyContract] = []
    for policy in POLICIES:
        close_style = base.close_style if policy.mode == "current_contract" else policy.close_style
        close_alpha = base.close_alpha if policy.mode == "current_contract" else policy.close_alpha
        if policy.mode == "current_contract":
            sell_gap = base.sell_gap
            buy_gap = base.buy_gap
        elif policy.name in {"inner_guarded", "sweep_guarded", "inner_fast", "outer_fast"}:
            sell_gap = max(2, int(base.sell_gap))
            buy_gap = max(2, int(base.buy_gap))
        elif policy.name == "outer_deep":
            sell_gap = max(3, int(base.sell_gap))
            buy_gap = max(3, int(base.buy_gap))
        else:
            sell_gap = policy.sell_gap
            buy_gap = policy.buy_gap
        variants.append(
            CloseStudyContract(
                symbol=base.symbol,
                timeframe=base.timeframe,
                shape_id=base.shape_id,
                step_buy_px=base.step_buy_px,
                step_sell_px=base.step_sell_px,
                max_open_per_side=base.max_open_per_side,
                rearm_variant=base.rearm_variant,
                rearm_cooldown_bars=base.rearm_cooldown_bars,
                momentum_gate=base.momentum_gate,
                base_variant_label=base.variant_label,
                policy_name=policy.name,
                policy_mode=policy.mode,
                policy_description=policy.description,
                close_style=close_style,
                close_alpha=close_alpha,
                sell_gap=sell_gap,
                buy_gap=buy_gap,
                retrace_steps=policy.retrace_steps,
                cross_anchor_steps=policy.cross_anchor_steps,
                mirror_depth=policy.mirror_depth,
                hybrid_profile=policy.hybrid_profile,
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
    contract: CloseStudyContract,
    token: RearmToken,
    bar: dict[str, Any],
    tickets: list[StudyTicket],
) -> StudyTicket | None:
    if not token.armed:
        return None
    if contract.momentum_gate and not _check_momentum_gate(bar, token.direction, float(token.level)):
        return None
    if token.direction == "SELL" and float(bar["high"]) >= float(token.level):
        ticket = StudyTicket(
            direction="SELL",
            entry_price=float(token.level),
            opened_time=int(bar["time"]),
            level_idx=int(token.level_idx),
            from_rearm=True,
        )
        tickets.append(ticket)
        return ticket
    if token.direction == "BUY" and float(bar["low"]) <= float(token.level):
        ticket = StudyTicket(
            direction="BUY",
            entry_price=float(token.level),
            opened_time=int(bar["time"]),
            level_idx=int(token.level_idx),
            from_rearm=True,
        )
        tickets.append(ticket)
        return ticket
    return None


def _maybe_add_rearm_token(
    *,
    contract: CloseStudyContract,
    variant,
    ticket: StudyTicket,
    bar_time: int,
    tokens: list[RearmToken],
) -> None:
    if int(ticket.level_idx) < int(variant.min_level_idx):
        return
    token = RearmToken(direction=ticket.direction, level=float(ticket.entry_price), level_idx=int(ticket.level_idx))
    token.cooldown_until_time = int(bar_time) + (contract.rearm_cooldown_bars * 60)
    tokens.append(token)


def _penetration_close_positions(
    *,
    symbol: str,
    direction: str,
    tickets: list[StudyTicket],
    trigger_price: float,
    bar_extreme: float,
    contract: CloseStudyContract,
    spread_px: float,
    variant,
    tokens: list[RearmToken],
    stats: dict[str, Any],
) -> None:
    if direction == "SELL":
        ordered = sorted((ticket for ticket in tickets if ticket.direction == "SELL"), key=lambda item: item.entry_price, reverse=True)
        gap = int(contract.sell_gap)
    else:
        ordered = sorted((ticket for ticket in tickets if ticket.direction == "BUY"), key=lambda item: item.entry_price)
        gap = int(contract.buy_gap)
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
            stats["realized_net_usd"] += pnl
            stats["realized_closes"] += 1
            stats["wins"] += 1
            stats["close_pnls"].append(pnl)
            _maybe_add_rearm_token(
                contract=contract,
                variant=variant,
                ticket=ticket,
                bar_time=int(stats["bar_time"]),
                tokens=tokens,
            )
            closed_any = True
        if not closed_any:
            break
        if direction == "SELL":
            ordered = sorted((ticket for ticket in tickets if ticket.direction == "SELL"), key=lambda item: item.entry_price, reverse=True)
        else:
            ordered = sorted((ticket for ticket in tickets if ticket.direction == "BUY"), key=lambda item: item.entry_price)


def _target_signed_level(ticket: StudyTicket, contract: CloseStudyContract) -> int:
    direction_sign = 1 if ticket.direction == "SELL" else -1
    depth = max(1, int(ticket.level_idx))
    if contract.hybrid_profile == "stepback_zero_cross":
        if depth <= 2:
            return direction_sign * max(0, depth - 1)
        if depth <= 5:
            return 0
        return (-direction_sign) * 1
    if contract.hybrid_profile == "fast_reclaim_anchor":
        if depth <= 3:
            return direction_sign * max(0, depth - 1)
        return 0
    if contract.retrace_steps is not None:
        remaining_depth = max(0, depth - int(contract.retrace_steps))
        return direction_sign * remaining_depth
    if contract.cross_anchor_steps is not None:
        return (-direction_sign) * int(contract.cross_anchor_steps)
    if contract.mirror_depth:
        return (-direction_sign) * depth
    return 0


def _signed_level_price(anchor: float, sell_step_px: float, buy_step_px: float, signed_level: int) -> float:
    if signed_level > 0:
        return float(anchor) + (float(sell_step_px) * float(signed_level))
    if signed_level < 0:
        return float(anchor) - (float(buy_step_px) * float(abs(int(signed_level))))
    return float(anchor)


def _independent_close_positions(
    *,
    symbol: str,
    direction: str,
    tickets: list[StudyTicket],
    trigger_price: float,
    anchor: float,
    contract: CloseStudyContract,
    spread_px: float,
    variant,
    tokens: list[RearmToken],
    stats: dict[str, Any],
) -> None:
    side_tickets = [ticket for ticket in tickets if ticket.direction == direction]
    side_tickets.sort(key=lambda item: (item.level_idx, item.entry_price), reverse=True)
    for ticket in side_tickets:
        target_signed_level = _target_signed_level(ticket, contract)
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
        _maybe_add_rearm_token(
            contract=contract,
            variant=variant,
            ticket=ticket,
            bar_time=int(stats["bar_time"]),
            tokens=tokens,
        )


def simulate_contract(contract: CloseStudyContract, bars: list[dict[str, Any]], symbol_info) -> dict[str, Any]:
    if not bars:
        return {}
    variant = REARM_VARIANTS.get(contract.rearm_variant)
    if variant is None:
        raise ValueError(f"Unknown rearm variant {contract.rearm_variant!r}")

    spread_px = spread_price(symbol_info)
    tickets: list[StudyTicket] = []
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
        "max_adverse_excursion_usd": 0.0,
        "bar_time": int(bars[0]["time"]),
    }

    for idx in range(1, len(bars)):
        bar = bars[idx]
        stats["bar_time"] = int(bar["time"])
        _update_token_arming(tokens, bar, contract.step_sell_px, variant.excursion_levels)
        _update_token_arming(tokens, bar, contract.step_buy_px, variant.excursion_levels)

        open_sell_main = sum(1 for ticket in tickets if ticket.direction == "SELL" and not ticket.from_rearm)
        open_buy_main = sum(1 for ticket in tickets if ticket.direction == "BUY" and not ticket.from_rearm)
        open_sell_rearm = sum(1 for ticket in tickets if ticket.direction == "SELL" and ticket.from_rearm)
        open_buy_rearm = sum(1 for ticket in tickets if ticket.direction == "BUY" and ticket.from_rearm)
        current_sell_step = dynamic_step(contract.step_sell_px, open_sell_main, STEP_CFG)
        current_buy_step = dynamic_step(contract.step_buy_px, open_buy_main, STEP_CFG)

        while float(bar["high"]) >= next_sell_level and open_sell_main < contract.max_open_per_side:
            tickets.append(
                StudyTicket(
                    direction="SELL",
                    entry_price=float(next_sell_level),
                    opened_time=int(bar["time"]),
                    level_idx=next_sell_level_idx,
                    from_rearm=False,
                )
            )
            open_sell_main += 1
            next_sell_level_idx += 1
            current_sell_step = dynamic_step(contract.step_sell_px, open_sell_main, STEP_CFG)
            next_sell_level += current_sell_step

        while float(bar["low"]) <= next_buy_level and open_buy_main < contract.max_open_per_side:
            tickets.append(
                StudyTicket(
                    direction="BUY",
                    entry_price=float(next_buy_level),
                    opened_time=int(bar["time"]),
                    level_idx=next_buy_level_idx,
                    from_rearm=False,
                )
            )
            open_buy_main += 1
            next_buy_level_idx += 1
            current_buy_step = dynamic_step(contract.step_buy_px, open_buy_main, STEP_CFG)
            next_buy_level -= current_buy_step

        for token in list(tokens):
            if token.direction == "SELL" and open_sell_rearm < contract.max_open_per_side:
                ticket = _open_token_if_hit(contract=contract, token=token, bar=bar, tickets=tickets)
                if ticket is not None:
                    tokens.remove(token)
                    open_sell_rearm += 1
                    stats["rearm_opens"] += 1
            elif token.direction == "BUY" and open_buy_rearm < contract.max_open_per_side:
                ticket = _open_token_if_hit(contract=contract, token=token, bar=bar, tickets=tickets)
                if ticket is not None:
                    tokens.remove(token)
                    open_buy_rearm += 1
                    stats["rearm_opens"] += 1

        if contract.policy_mode in {"current_contract", "penetration"}:
            _penetration_close_positions(
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
            )
            _penetration_close_positions(
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
            )
        elif contract.policy_mode in {"independent", "hybrid"}:
            _independent_close_positions(
                symbol=contract.symbol,
                direction="SELL",
                tickets=tickets,
                trigger_price=float(bar["low"]),
                anchor=anchor,
                contract=contract,
                spread_px=spread_px,
                variant=variant,
                tokens=tokens,
                stats=stats,
            )
            _independent_close_positions(
                symbol=contract.symbol,
                direction="BUY",
                tickets=tickets,
                trigger_price=float(bar["high"]),
                anchor=anchor,
                contract=contract,
                spread_px=spread_px,
                variant=variant,
                tokens=tokens,
                stats=stats,
            )
        else:
            raise ValueError(f"Unsupported policy mode: {contract.policy_mode}")

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
    avg_close_usd = (float(stats["realized_net_usd"]) / float(realized_closes)) if realized_closes else 0.0
    realized_usd_per_hour = float(stats["realized_net_usd"]) / hours
    closes_per_hour = float(realized_closes) / hours
    conversion_ratio = (
        float(stats["realized_net_usd"]) / (abs(float(stats["realized_net_usd"])) + abs(float(floating_net_usd)))
        if (abs(float(stats["realized_net_usd"])) + abs(float(floating_net_usd))) > 0
        else 0.0
    )
    return {
        **asdict(contract),
        "realized_net_usd": round(float(stats["realized_net_usd"]), 3),
        "floating_net_usd": round(float(floating_net_usd), 3),
        "combined_net_usd": round(float(combined_net_usd), 3),
        "realized_closes": realized_closes,
        "avg_close_usd": round(avg_close_usd, 3),
        "realized_usd_per_hour": round(realized_usd_per_hour, 3),
        "combined_usd_per_hour": round(combined_net_usd / hours, 3),
        "closes_per_hour": round(closes_per_hour, 3),
        "max_open_total": int(stats["max_open_total"]),
        "final_open_count": len(tickets),
        "anchor_resets": int(stats["anchor_resets"]),
        "rearm_opens": int(stats["rearm_opens"]),
        "max_adverse_excursion_usd": round(float(stats["max_adverse_excursion_usd"]), 3),
        "realized_win_rate": round(win_rate, 4),
        "conversion_ratio": round(conversion_ratio, 4),
        "unified_objective_score": round(float(objective.total), 3),
        "objective_verdict": objective.verdict,
    }


def score_sort_key(row: dict[str, Any]) -> tuple[float, float, float, float]:
    return (
        float(row["realized_usd_per_hour"]),
        float(row["unified_objective_score"]),
        float(row["combined_net_usd"]),
        float(row["avg_close_usd"]),
    )


def build_summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    by_symbol: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        by_symbol.setdefault(str(row["symbol"]), []).append(row)

    best_by_symbol: list[dict[str, Any]] = []
    leadership: list[str] = []
    policy_wins: dict[str, int] = {}
    for symbol in sorted(by_symbol):
        symbol_rows = sorted(by_symbol[symbol], key=score_sort_key, reverse=True)
        best = symbol_rows[0]
        current = next((row for row in symbol_rows if str(row["policy_name"]) == "current_contract"), best)
        delta_per_hour = round(float(best["realized_usd_per_hour"]) - float(current["realized_usd_per_hour"]), 3)
        leadership.append(
            f"{symbol} best close law is `{best['policy_name']}` at `${best['realized_usd_per_hour']}/h` "
            f"vs current-contract `${current['realized_usd_per_hour']}/h` (delta `${delta_per_hour}/h`)."
        )
        policy_wins[str(best["policy_name"])] = policy_wins.get(str(best["policy_name"]), 0) + 1
        best_by_symbol.append(best)

    dominant_policy = ""
    if policy_wins:
        dominant_policy = max(policy_wins.items(), key=lambda item: (item[1], item[0]))[0]
        leadership.append(
            f"Cross-symbol winner count: `{dominant_policy}` leads with `{policy_wins[dominant_policy]}` symbol wins."
        )

    overall_best = max(rows, key=score_sort_key) if rows else {}
    if overall_best:
        leadership.append(
            f"Highest raw money-velocity row in the study is `{overall_best['symbol']}:{overall_best['policy_name']}` "
            f"at `${overall_best['realized_usd_per_hour']}/h`."
        )

    return {
        "leadership": leadership,
        "best_by_symbol": best_by_symbol,
        "overall_best": overall_best,
        "policy_wins": policy_wins,
        "dominant_policy": dominant_policy,
    }


def build_markdown(rows: list[dict[str, Any]], summary: dict[str, Any], *, timeframe: str, days: int) -> str:
    lines: list[str] = []
    lines.append("# Lattice Close Pattern Study")
    lines.append("")
    lines.append(
        f"This study holds the current best adaptive deployment geometry fixed and changes only the close law on `{timeframe}` "
        f"bars over `{days}` days. It directly tests the user question: should a deep ticket close one step back, at the anchor, "
        f"through the anchor, or only after a full mirrored traverse?"
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
            f"- `{row['symbol']}`: `{row['policy_name']}` -> `${row['realized_usd_per_hour']}/h`, "
            f"`{row['closes_per_hour']}` closes/h, `$ {row['avg_close_usd']}` per close, floating `${row['floating_net_usd']}`, "
            f"carry `{row['max_open_total']}` max open, objective `{row['objective_verdict']}`."
        )
        lines.append(f"  Logic: {row['policy_description']}")
    lines.append("")
    lines.append("## Interpretation")
    lines.append("")
    lines.append("- `penetration_outer_gap1_exact` corresponds to `5 -> 4` outer-first behavior.")
    lines.append("- `independent_anchor_zero_exact` corresponds to `5 -> 0`.")
    lines.append("- `independent_through_zero_1_exact` corresponds to `5 -> -1`.")
    lines.append("- `independent_far_side_mirror_exact` corresponds to `5 -> -5`.")
    lines.append("- The study uses exact target fills for the synthetic alternative close laws, so it compares structural target depth rather than optimistic bar-extreme alpha interpolation.")
    lines.append("- Treat the ranking as offline contract evidence. Same-bar ordering on OHLC bars is still an approximation, especially for aggressive close laws that can both open and close in one bar.")
    lines.append("")
    lines.append("## Full Ranking")
    lines.append("")
    lines.append("| Symbol | Policy | $/h | Closes/h | Avg/Close | Floating | Max Open | Objective |")
    lines.append("| --- | --- | ---: | ---: | ---: | ---: | ---: | --- |")
    for row in rows:
        lines.append(
            f"| {row['symbol']} | {row['policy_name']} | {row['realized_usd_per_hour']} | {row['closes_per_hour']} | "
            f"{row['avg_close_usd']} | {row['floating_net_usd']} | {row['max_open_total']} | {row['objective_verdict']} |"
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
        contracts = load_best_contracts(Path(args.study_json), [str(symbol).upper() for symbol in args.symbols], args.timeframe)
        if not contracts:
            print("No deployment-study contracts matched the requested symbols/timeframe.")
            return 1

        rows: list[dict[str, Any]] = []
        for base in contracts:
            info = mt5.symbol_info(base.symbol)
            if info is None:
                continue
            bars = load_bars(base.symbol, args.timeframe, args.days)
            if not bars:
                continue
            for contract in build_contract_variants(base):
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
