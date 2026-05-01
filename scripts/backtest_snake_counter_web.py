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

from backtest_adaptive_deployment_study import TIMEFRAME_MAP, compute_ema_ladders, load_bars
from penetration_lattice_lab_v2 import pip_size_for, spread_price, unit_pnl_usd


ROOT = Path(__file__).resolve().parent.parent
OUTPUT_CSV = ROOT / "reports" / "snake_counter_web_study.csv"
OUTPUT_MD = ROOT / "reports" / "snake_counter_web_study.md"
OUTPUT_JSON = ROOT / "reports" / "snake_counter_web_study.json"

TIMEFRAME_SECONDS = {
    "M1": 60,
    "M5": 300,
    "M15": 900,
    "H1": 3600,
}
RESEARCH_VOLUME_LOTS = 0.01
DEFAULT_ACCOUNT_LEVERAGE = 500.0
DEFAULT_MARGIN_STOPOUT_LEVEL_PCT = 50.0


@dataclass(frozen=True)
class SnakeContract:
    symbol: str
    timeframe: str
    step_px: float
    retrace_steps: int
    hold_frontier: int
    rebase_on_flat: bool
    max_open_per_side: int
    controller_mode: str
    portfolio_close_mode: str
    hedge_mode: str
    hedge_trigger_depth: int
    hedge_profit_threshold_steps: int
    variant_label: str
    min_harvest_profit_usd: float = 0.0
    positive_only_closes: bool = False


@dataclass
class SnakeTicket:
    direction: str
    entry_price: float
    opened_time: int
    ticket_kind: str = "core"  # core, hedge, locked_core, locked_hedge
    live_ticket: int = 0
    position_comment: str = ""
    pair_id: int = 0  # Links locked pairs together


def research_unit_pnl_usd(symbol: str, direction: str, entry_price: float, exit_price: float, spread_px: float, symbol_info) -> float:
    currency_profit = str(getattr(symbol_info, "currency_profit", "") or "").upper()
    contract_size = float(getattr(symbol_info, "trade_contract_size", 0.0) or 0.0)
    if currency_profit == "USD" and contract_size > 0:
        volume = RESEARCH_VOLUME_LOTS
        gross = (exit_price - entry_price) * contract_size * volume
        if str(direction).upper() == "SELL":
            gross = -gross
        spread_cost = abs(float(spread_px or 0.0)) * contract_size * volume
        return float(gross) - float(spread_cost)
    return unit_pnl_usd(symbol, direction, entry_price, exit_price, spread_px)


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def safe_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _estimate_ticket_margin_usd(
    *,
    symbol: str,
    symbol_info,
    price: float,
    leverage: float,
) -> float:
    if leverage <= 0.0:
        return float("inf")
    contract_size = float(getattr(symbol_info, "trade_contract_size", 0.0) or 0.0)
    if contract_size <= 0.0:
        contract_size = 100000.0 if len(str(symbol)) >= 6 else 1.0
    notional = contract_size * RESEARCH_VOLUME_LOTS
    price = abs(float(price or 0.0))
    symbol_name = str(symbol).upper()
    base_ccy = symbol_name[:3] if len(symbol_name) >= 6 else ""
    quote_ccy = symbol_name[3:6] if len(symbol_name) >= 6 else ""
    if base_ccy == "USD":
        return float(notional / leverage)
    if price > 0.0 and (quote_ccy == "USD" or base_ccy and quote_ccy):
        return float((notional * price) / leverage)
    return float(notional / leverage)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Prototype M1 snake counter-order web study for micro-flutter harvest."
    )
    parser.add_argument("--symbols", nargs="*", default=["GBPUSD", "EURUSD"])
    parser.add_argument("--days", type=int, default=5)
    parser.add_argument("--timeframe", choices=sorted(TIMEFRAME_MAP.keys()), default="M1")
    parser.add_argument("--step-pips", nargs="*", type=float, default=[1.0, 2.0, 3.0])
    parser.add_argument("--retrace-steps", nargs="*", type=int, default=[1, 2, 3])
    parser.add_argument("--hold-frontier", nargs="*", type=int, default=[0, 1, 2])
    parser.add_argument(
        "--controller-modes",
        nargs="*",
        default=["static", "ema_ribbon", "ema_ribbon_aggressive", "gemini_elastic", "gemini_margin_aware"],
    )
    parser.add_argument("--max-open-per-side-values", nargs="*", type=int, default=[32])
    parser.add_argument(
        "--rank-mode",
        choices=["booked_usd_per_hour", "balanced"],
        default="booked_usd_per_hour",
        help="Ranking objective for the study output.",
    )
    parser.add_argument(
        "--portfolio-close-modes",
        nargs="*",
        default=["none"],
        choices=["none", "float_zero", "funded_rescue", "convergent_unwind", "forced_reanchor"],
        help="Optional portfolio-state close controller layered on top of ticket retrace closes.",
    )
    parser.add_argument(
        "--hedge-modes",
        nargs="*",
        default=["none"],
        choices=["none", "same_level", "depth_threshold", "profit_lock", "lock_and_release"],
        help="Optional admission-time hedge overlay layered on top of the snake opens.",
    )
    parser.add_argument(
        "--hedge-trigger-depths",
        nargs="*",
        type=int,
        default=[4],
        help="When `hedge_mode=depth_threshold`, start opening opposite hedge tickets once same-direction depth reaches this value.",
    )
    parser.add_argument(
        "--hedge-profit-threshold-steps",
        nargs="*",
        type=int,
        default=[2],
        help="When `hedge_mode=profit_lock`, lock and flatten a core ticket once it moves this many steps in profit.",
    )
    parser.add_argument("--max-mae-abs-usd", type=float, default=None)
    parser.add_argument("--max-final-open", type=int, default=None)
    parser.add_argument("--max-max-open", type=int, default=None)
    parser.add_argument("--require-realized-cover", action="store_true")
    parser.add_argument("--starting-balance-usd", type=float, default=None)
    parser.add_argument("--hard-floor-usd", type=float, default=None)
    parser.add_argument("--account-leverage", type=float, default=DEFAULT_ACCOUNT_LEVERAGE)
    parser.add_argument("--margin-stopout-level-pct", type=float, default=DEFAULT_MARGIN_STOPOUT_LEVEL_PCT)
    parser.add_argument("--require-margin-survival", action="store_true")
    parser.add_argument("--output-csv", default=str(OUTPUT_CSV))
    parser.add_argument("--output-md", default=str(OUTPUT_MD))
    parser.add_argument("--output-json", default=str(OUTPUT_JSON))
    return parser.parse_args()


def build_contracts(args: argparse.Namespace) -> list[SnakeContract]:
    contracts: list[SnakeContract] = []
    for symbol in [str(s).upper() for s in args.symbols]:
        info = mt5.symbol_info(symbol)
        if info is None:
            continue
        pip = pip_size_for(info)
        for step_pips in args.step_pips:
            step_px = float(step_pips) * float(pip)
            for retrace_steps in args.retrace_steps:
                for hold_frontier in args.hold_frontier:
                    for controller_mode in [str(mode) for mode in args.controller_modes]:
                        for portfolio_close_mode in [str(mode) for mode in args.portfolio_close_modes]:
                            for hedge_mode in [str(mode) for mode in args.hedge_modes]:
                                if hedge_mode == "depth_threshold":
                                    hedge_variants = [
                                        (int(v), 0, f"_hedge{hedge_mode}{int(v)}")
                                        for v in args.hedge_trigger_depths
                                    ]
                                elif hedge_mode == "profit_lock":
                                    hedge_variants = [
                                        (0, int(v), f"_hedge{hedge_mode}{int(v)}")
                                        for v in args.hedge_profit_threshold_steps
                                    ]
                                elif hedge_mode == "lock_and_release":
                                    hedge_variants = [
                                        (0, int(v), f"_hedge{hedge_mode}{int(v)}")
                                        for v in args.hedge_profit_threshold_steps
                                    ]
                                else:
                                    hedge_variants = [(0, 0, f"_hedge{hedge_mode}")]
                                for hedge_trigger_depth, hedge_profit_threshold_steps, hedge_suffix in hedge_variants:
                                    for max_open_per_side in args.max_open_per_side_values:
                                        for rebase_on_flat in (False, True):
                                            label = (
                                                f"snake_step{step_pips:g}pip_retrace{retrace_steps}"
                                                f"_hold{hold_frontier}_{controller_mode}"
                                                f"_{portfolio_close_mode}"
                                                f"{hedge_suffix}"
                                                f"_cap{int(max_open_per_side)}_{'rebase' if rebase_on_flat else 'fixed'}"
                                            )
                                            contracts.append(
                                                SnakeContract(
                                                    symbol=symbol,
                                                    timeframe=args.timeframe,
                                                    step_px=step_px,
                                                    retrace_steps=int(retrace_steps),
                                                    hold_frontier=int(hold_frontier),
                                                    rebase_on_flat=bool(rebase_on_flat),
                                                    max_open_per_side=int(max_open_per_side),
                                                    controller_mode=controller_mode,
                                                    portfolio_close_mode=portfolio_close_mode,
                                                    hedge_mode=hedge_mode,
                                                    hedge_trigger_depth=int(hedge_trigger_depth),
                                                    hedge_profit_threshold_steps=int(hedge_profit_threshold_steps),
                                                    variant_label=label,
                                                )
                                            )
    return contracts


def _resolve_controller_state(contract: SnakeContract, dynamic_context: dict[str, Any] | None, *, pip_px: float, open_count: int = 0) -> tuple[float, int, int, bool]:
    base_step = float(contract.step_px)
    
    if contract.controller_mode == "gemini_margin_aware":
        # Dynamic step sizing based on account health (Free Margin %)
        starting_balance = float(dynamic_context.get("starting_balance", 100.0) or 100.0)
        free_margin = float(dynamic_context.get("free_margin", starting_balance) or starting_balance)
        margin_level = free_margin / starting_balance if starting_balance > 0 else 1.0
        
        # High margin (>90%) -> Aggressive tight steps (0.5x base)
        # Low margin (<50%) -> Defensive wide steps (2.0x base)
        if margin_level >= 0.9:
            margin_mult = 0.5
        elif margin_level <= 0.5:
            margin_mult = 2.0
        else:
            # Linear interpolation between 0.5 and 2.0
            # (0.9, 0.5) -> (0.5, 2.0)
            margin_mult = 0.5 + (0.9 - margin_level) * (1.5 / 0.4)
            margin_mult = max(0.5, min(2.0, margin_mult))
            
        step = base_step * margin_mult
        
        # Overlay EMA ribbon for trend protection
        ema_fast = float(dynamic_context.get("ema_fast_3", 0.0) or 0.0)
        ema_heavy = float(dynamic_context.get("ema_mid_128", 0.0) or 0.0)
        span = abs(ema_fast - ema_heavy)
        compressed = span <= (base_step * 3.0)
        
        if compressed: step = max(step * 0.75, pip_px)
        
        return step, 1, 1, bool(contract.rebase_on_flat) and compressed

    if contract.controller_mode == "gemini_elastic":
        # Dynamic step thinning based on inventory count to protect MAE
        elastic_mult = 1.0
        if open_count > 10:
            elastic_mult = 1.0 + (open_count - 10) * 0.2 # 20% growth per position over 10
        
        ema_fast = float(dynamic_context.get("ema_fast_3", 0.0) or 0.0)
        ema_heavy = float(dynamic_context.get("ema_mid_128", 0.0) or 0.0)
        span = abs(ema_fast - ema_heavy)
        compressed = span <= (base_step * 3.0)
        
        step = base_step * elastic_mult
        if compressed: step = max(step * 0.75, pip_px)
        
        return step, 1, 1, bool(contract.rebase_on_flat) and compressed

    if contract.controller_mode == "static" or dynamic_context is None:
        return base_step, 1, 1, bool(contract.rebase_on_flat)
    ema_fast = float(dynamic_context.get("ema_fast_3", 0.0) or 0.0)
    ema_light = float(dynamic_context.get("ema_light_12", ema_fast) or ema_fast)
    ema_mid = float(dynamic_context.get("ema_mid_64", ema_light) or ema_light)
    ema_heavy = float(dynamic_context.get("ema_mid_128", ema_mid) or ema_mid)
    span = abs(ema_fast - ema_heavy)
    compressed = span <= (base_step * 3.0)
    trend_up = ema_fast > ema_light > ema_mid > ema_heavy and span >= (base_step * 4.0)
    trend_down = ema_fast < ema_light < ema_mid < ema_heavy and span >= (base_step * 4.0)

    if contract.controller_mode == "ema_ribbon":
        if compressed:
            step = max(base_step * 0.75, pip_px)
        elif trend_up or trend_down:
            step = base_step * 1.5
        else:
            step = base_step
        sell_divisor = 2 if trend_up else 1
        buy_divisor = 2 if trend_down else 1
        rebase_allowed = bool(contract.rebase_on_flat) and not (trend_up or trend_down)
        return step, sell_divisor, buy_divisor, rebase_allowed

    if contract.controller_mode == "ema_ribbon_aggressive":
        if compressed:
            step = max(base_step * 0.5, pip_px)
        elif trend_up or trend_down:
            step = base_step * 2.0
        else:
            step = base_step * 1.1
        sell_divisor = 3 if trend_up else 1
        buy_divisor = 3 if trend_down else 1
        rebase_allowed = bool(contract.rebase_on_flat) and compressed
        return step, sell_divisor, buy_divisor, rebase_allowed

    if contract.controller_mode == "ema_ribbon_hyper":
        if compressed:
            step = max(base_step * 0.35, pip_px)
        elif trend_up or trend_down:
            step = base_step * 2.5
        else:
            step = base_step * 0.9
        sell_divisor = 4 if trend_up else 1
        buy_divisor = 4 if trend_down else 1
        rebase_allowed = bool(contract.rebase_on_flat) and compressed
        return step, sell_divisor, buy_divisor, rebase_allowed

    return base_step, 1, 1, bool(contract.rebase_on_flat)


def _ordered_tickets(tickets: list[SnakeTicket], direction: str) -> list[SnakeTicket]:
    side = [ticket for ticket in tickets if ticket.direction == direction]
    if direction == "SELL":
        return sorted(side, key=lambda ticket: ticket.entry_price, reverse=True)
    return sorted(side, key=lambda ticket: ticket.entry_price)


def _floating_pnl_usd(
    *,
    symbol: str,
    symbol_info,
    tickets: list[SnakeTicket],
    price: float,
    spread_px: float,
) -> float:
    total = 0.0
    for ticket in tickets:
        total += research_unit_pnl_usd(
            symbol,
            ticket.direction,
            float(ticket.entry_price),
            float(price),
            spread_px,
            symbol_info,
        )
    return float(total)


def _update_floating_stats(
    *,
    symbol: str,
    symbol_info,
    tickets: list[SnakeTicket],
    price: float,
    spread_px: float,
    stats: dict[str, Any],
    starting_balance_usd: float,
    account_leverage: float,
    margin_stopout_level_pct: float,
) -> None:
    floating_pnl = _floating_pnl_usd(
        symbol=symbol,
        symbol_info=symbol_info,
        tickets=tickets,
        price=price,
        spread_px=spread_px,
    )
    stats["min_floating_pnl_usd"] = min(float(stats["min_floating_pnl_usd"]), floating_pnl)
    stats["max_floating_pnl_usd"] = max(float(stats["max_floating_pnl_usd"]), floating_pnl)
    combined_equity = float(stats["realized_net_usd"]) + floating_pnl
    stats["min_combined_equity_usd"] = min(float(stats["min_combined_equity_usd"]), combined_equity)
    stats["max_combined_equity_usd"] = max(float(stats["max_combined_equity_usd"]), combined_equity)
    realized_cover_gap = float(stats["realized_net_usd"]) - abs(min(0.0, floating_pnl))
    stats["min_realized_cover_gap_usd"] = min(float(stats["min_realized_cover_gap_usd"]), realized_cover_gap)
    stats["min_combined_equity_delta_usd"] = min(
        float(stats["min_combined_equity_delta_usd"]),
        combined_equity,
    )
    if realized_cover_gap < 0.0:
        stats["realized_cover_violation_bars"] += 1
    equity_usd = float(starting_balance_usd) + combined_equity
    used_margin_usd = 0.0
    for ticket in tickets:
        ref_price = float(price or ticket.entry_price or 0.0)
        used_margin_usd += _estimate_ticket_margin_usd(
            symbol=symbol,
            symbol_info=symbol_info,
            price=ref_price,
            leverage=account_leverage,
        )
    stats["max_used_margin_usd"] = max(float(stats["max_used_margin_usd"]), used_margin_usd)
    free_margin_usd = equity_usd - used_margin_usd
    stats["min_free_margin_usd"] = min(float(stats["min_free_margin_usd"]), free_margin_usd)
    if used_margin_usd > 0.0:
        margin_level_pct = (equity_usd / used_margin_usd) * 100.0
        stats["min_margin_level_pct"] = min(float(stats["min_margin_level_pct"]), margin_level_pct)
        if free_margin_usd < 0.0 or margin_level_pct < float(margin_stopout_level_pct):
            stats["margin_stopout_bars"] += 1
    elif equity_usd < 0.0:
        stats["margin_stopout_bars"] += 1


def _apply_closes(
    *,
    symbol: str,
    symbol_info,
    tickets: list[SnakeTicket],
    price: float,
    spread_px: float,
    contract: SnakeContract,
    stats: dict[str, Any],
) -> None:
    for direction in ("SELL", "BUY"):
        ordered = _ordered_tickets(tickets, direction)
        profitable: list[tuple[int, SnakeTicket, float]] = []
        for idx, ticket in enumerate(ordered):
            pnl = research_unit_pnl_usd(symbol, direction, float(ticket.entry_price), float(price), spread_px, symbol_info)
            if pnl > 0:
                if direction == "SELL":
                    close_threshold = float(ticket.entry_price) - (float(contract.step_px) * float(contract.retrace_steps))
                    if float(price) > close_threshold:
                        continue
                else:
                    close_threshold = float(ticket.entry_price) + (float(contract.step_px) * float(contract.retrace_steps))
                    if float(price) < close_threshold:
                        continue
                profitable.append((idx, ticket, pnl))
        if not profitable:
            continue
        to_close = profitable[contract.hold_frontier :] if contract.hold_frontier > 0 else profitable
        for _, ticket, pnl in to_close:
            if ticket not in tickets:
                continue
            tickets.remove(ticket)
            stats["realized_net_usd"] += pnl
            stats["gross_positive_booked_usd"] += pnl
            stats["realized_closes"] += 1
            stats["wins"] += 1
            stats["close_pnls"].append(pnl)

    if contract.portfolio_close_mode == "float_zero" and tickets:
        total_floating = 0.0
        for ticket in tickets:
            pnl = research_unit_pnl_usd(
                symbol,
                ticket.direction,
                float(ticket.entry_price),
                float(price),
                spread_px,
                symbol_info,
            )
            total_floating += pnl
        
        # Track best floating seen for debug
        if "best_total_floating" not in stats: stats["best_total_floating"] = -999999.0
        stats["best_total_floating"] = max(stats["best_total_floating"], total_floating)

        if total_floating >= -0.1: # Lenient breakthrough attempt
            for ticket in list(tickets):
                pnl = research_unit_pnl_usd(
                    symbol,
                    ticket.direction,
                    float(ticket.entry_price),
                    float(price),
                    spread_px,
                    symbol_info,
                )
                tickets.remove(ticket)
                stats["realized_net_usd"] += pnl
                if pnl > 0:
                    stats["gross_positive_booked_usd"] += pnl
                    stats["wins"] += 1
                else:
                    stats["losses"] += 1
                stats["realized_closes"] += 1
                stats["float_zero_closes"] += 1
                stats["close_pnls"].append(pnl)

    if contract.portfolio_close_mode == "funded_rescue" and tickets:
        rows = []
        for t in tickets:
            pnl = research_unit_pnl_usd(
                symbol,
                t.direction,
                float(t.entry_price),
                float(price),
                spread_px,
                symbol_info,
            )
            rows.append((t, pnl))
        rows.sort(key=lambda x: x[1], reverse=True)
        while rows and len(rows) >= 2:
            best_t, best_pnl = rows[0]
            worst_t, worst_pnl = rows[-1]
            if best_pnl > 0 and worst_pnl < 0 and (best_pnl + worst_pnl) >= 0:
                if best_t in tickets: tickets.remove(best_t)
                if worst_t in tickets: tickets.remove(worst_t)
                stats["realized_net_usd"] += (best_pnl + worst_pnl)
                stats["gross_positive_booked_usd"] += best_pnl
                stats["realized_closes"] += 2
                stats["wins"] += 1
                stats["close_pnls"].extend([best_pnl, worst_pnl])
                rows.pop(0)
                rows.pop(-1)
            else:
                break

    if contract.portfolio_close_mode == "convergent_unwind" and tickets:
        # Pair winning BUYs with losing SELLs (and vice-versa) to evaporate inventory
        buys = []
        sells = []
        for t in tickets:
            pnl = research_unit_pnl_usd(symbol, t.direction, float(t.entry_price), float(price), spread_px, symbol_info)
            if t.direction == "BUY":
                buys.append((t, pnl))
            else:
                sells.append((t, pnl))

        buys.sort(key=lambda x: x[1], reverse=True)  # Best BUYs first
        sells.sort(key=lambda x: x[1], reverse=True)  # Best SELLs first

        # Cross-side pairing
        while buys and sells:
            b_t, b_pnl = buys[0]
            s_t, s_pnl = sells[-1]  # Pair best BUY with WORST SELL
            if (b_pnl + s_pnl) >= (spread_px * 2.0):  # Covered spread
                if b_t in tickets: tickets.remove(b_t)
                if s_t in tickets: tickets.remove(s_t)
                stats["realized_net_usd"] += (b_pnl + s_pnl)
                stats["gross_positive_booked_usd"] += max(0.0, b_pnl) + max(0.0, s_pnl)
                stats["realized_closes"] += 2
                stats["wins"] += 1
                stats["close_pnls"].extend([b_pnl, s_pnl])
                buys.pop(0)
                sells.pop(-1)
            else:
                # Try the other way: worst BUY with best SELL
                b_t, b_pnl = buys[-1]
                s_t, s_pnl = sells[0]
                if (b_pnl + s_pnl) >= (spread_px * 2.0):
                    if b_t in tickets: tickets.remove(b_t)
                    if s_t in tickets: tickets.remove(s_t)
                    stats["realized_net_usd"] += (b_pnl + s_pnl)
                    stats["gross_positive_booked_usd"] += max(0.0, b_pnl) + max(0.0, s_pnl)
                    stats["realized_closes"] += 2
                    stats["wins"] += 1
                    stats["close_pnls"].extend([b_pnl, s_pnl])
                    buys.pop(-1)
                    sells.pop(0)
                else:
                    break

    if contract.portfolio_close_mode == "forced_reanchor" and tickets:
        # If net floating < threshold, wipe the slate to prevent total account death
        # threshold is arbitrary: -20 USD for 0.01 study unit
        total_floating = _floating_pnl_usd(symbol=symbol, symbol_info=symbol_info, tickets=tickets, price=price, spread_px=spread_px)
        if total_floating <= -20.0:
            for t in list(tickets):
                pnl = research_unit_pnl_usd(symbol, t.direction, float(t.entry_price), float(price), spread_px, symbol_info)
                stats["realized_net_usd"] += pnl
                stats["realized_closes"] += 1
                stats["close_pnls"].append(pnl)
                tickets.remove(t)
            stats["reanchor_requested"] = True


def _same_direction_depth(tickets: list[SnakeTicket], direction: str) -> int:
    return sum(1 for ticket in tickets if ticket.direction == direction and ticket.ticket_kind == "core")


def _maybe_add_hedge_ticket(
    *,
    tickets: list[SnakeTicket],
    contract: SnakeContract,
    level_direction: str,
    entry_price: float,
    bar_time: int,
    stats: dict[str, Any],
) -> None:
    if contract.hedge_mode not in {"same_level", "depth_threshold"}:
        return
    opposite_direction = "BUY" if level_direction == "SELL" else "SELL"
    if sum(1 for ticket in tickets if ticket.direction == opposite_direction) >= contract.max_open_per_side:
        return
    if contract.hedge_mode == "depth_threshold":
        if _same_direction_depth(tickets, level_direction) < int(contract.hedge_trigger_depth):
            return
    tickets.append(
        SnakeTicket(
            direction=opposite_direction,
            entry_price=entry_price,
            opened_time=bar_time,
            ticket_kind="hedge",
        )
    )
    stats["opens"] += 1
    stats["hedge_opens"] += 1


def _maybe_apply_profit_lock(
    *,
    symbol: str,
    symbol_info,
    tickets: list[SnakeTicket],
    price: float,
    spread_px: float,
    contract: SnakeContract,
    stats: dict[str, Any],
) -> None:
    if contract.hedge_mode != "profit_lock":
        return
    threshold_steps = max(1, int(contract.hedge_profit_threshold_steps))
    lock_distance = float(contract.step_px) * float(threshold_steps)
    locked: list[tuple[SnakeTicket, float]] = []
    for ticket in list(tickets):
        if ticket.ticket_kind != "core":
            continue
        entry_price = float(ticket.entry_price)
        if ticket.direction == "SELL":
            lock_ready = float(price) <= (entry_price - lock_distance)
            opposite_direction = "BUY"
        else:
            lock_ready = float(price) >= (entry_price + lock_distance)
            opposite_direction = "SELL"
        if not lock_ready:
            continue
        core_pnl = research_unit_pnl_usd(
            symbol,
            ticket.direction,
            entry_price,
            float(price),
            spread_px,
            symbol_info,
        )
        hedge_tax = research_unit_pnl_usd(
            symbol,
            opposite_direction,
            float(price),
            float(price),
            spread_px,
            symbol_info,
        )
        locked.append((ticket, core_pnl + hedge_tax))
    for ticket, locked_pnl in locked:
        if ticket not in tickets:
            continue
        tickets.remove(ticket)
        stats["realized_net_usd"] += locked_pnl
        if locked_pnl > 0.0:
            stats["gross_positive_booked_usd"] += locked_pnl
            stats["wins"] += 1
        stats["realized_closes"] += 1
        stats["profit_lock_closes"] += 1
        stats["close_pnls"].append(locked_pnl)


_next_pair_id = 1


def _maybe_apply_lock_and_release(
    *,
    symbol: str,
    symbol_info,
    tickets: list[SnakeTicket],
    price: float,
    spread_px: float,
    contract: SnakeContract,
    stats: dict[str, Any],
    pair_counter: dict,
) -> None:
    """User's profit-lock concept WITH release mechanism.
    
    When core ticket reaches profit threshold:
    1. Mark it as locked_core, open opposite as locked_hedge (with pair_id)
    2. When both in pair converge (combined PnL >= small threshold), close BOTH
    """
    global _next_pair_id
    if contract.hedge_mode != "lock_and_release":
        return
    threshold_steps = max(1, int(contract.hedge_profit_threshold_steps))
    lock_distance = float(contract.step_px) * float(threshold_steps)
    release_threshold = float(contract.step_px) * 0.5  # Release when pair converges within 0.5 step
    
    # Step 1: Lock profitable core positions by opening opposite hedge
    newly_locked: list[tuple[SnakeTicket, int]] = []  # (ticket, pair_id)
    for ticket in list(tickets):
        if ticket.ticket_kind != "core":
            continue
        entry_price = float(ticket.entry_price)
        if ticket.direction == "SELL":
            lock_ready = float(price) <= (entry_price - lock_distance)
            opposite_direction = "BUY"
        else:
            lock_ready = float(price) >= (entry_price + lock_distance)
            opposite_direction = "SELL"
        if not lock_ready:
            continue
        
        # Create locked pair
        pid = pair_counter.get("next", 1)
        pair_counter["next"] = pid + 1
        
        ticket.ticket_kind = "locked_core"
        ticket.pair_id = pid
        
        tickets.append(
            SnakeTicket(
                direction=opposite_direction,
                entry_price=float(price),
                opened_time=int(ticket.opened_time),
                ticket_kind="locked_hedge",
                pair_id=pid,
            )
        )
        stats["opens"] += 1
        stats["hedge_opens"] += 1
        stats["lock_and_release_locks"] += 1
        newly_locked.append((ticket, pid))
    
    # Step 2: Release locked pairs when they converge
    pairs_to_check: dict[int, list[SnakeTicket]] = {}
    for t in tickets:
        if t.pair_id > 0 and t.ticket_kind in ("locked_core", "locked_hedge"):
            pairs_to_check.setdefault(t.pair_id, []).append(t)
    
    for pid, pair in pairs_to_check.items():
        if len(pair) != 2:
            continue
        core_t = pair[0] if pair[0].ticket_kind == "locked_core" else pair[1]
        hedge_t = pair[1] if pair[0].ticket_kind == "locked_core" else pair[0]
        
        core_pnl = research_unit_pnl_usd(
            symbol, core_t.direction, float(core_t.entry_price), float(price), spread_px, symbol_info
        )
        hedge_pnl = research_unit_pnl_usd(
            symbol, hedge_t.direction, float(hedge_t.entry_price), float(price), spread_px, symbol_info
        )
        combined_pnl = core_pnl + hedge_pnl
        
        # Release when combined PnL is positive or near zero
        if combined_pnl >= -release_threshold:
            # Close both
            if core_t in tickets:
                tickets.remove(core_t)
            if hedge_t in tickets:
                tickets.remove(hedge_t)
            
            stats["realized_net_usd"] += combined_pnl
            if combined_pnl > 0:
                stats["gross_positive_booked_usd"] += combined_pnl
                stats["wins"] += 1
            stats["realized_closes"] += 2
            stats["lock_and_release_releases"] += 1
            stats["close_pnls"].extend([core_pnl, hedge_pnl])


def _cross_up_levels(anchor: float, start: float, end: float, step_px: float, last_level: int) -> list[int]:
    if end <= start or step_px <= 0:
        return []
    levels: list[int] = []
    idx = last_level + 1
    while anchor + (idx * step_px) <= end:
        if anchor + (idx * step_px) > start:
            levels.append(idx)
        idx += 1
    return levels


def _cross_down_levels(anchor: float, start: float, end: float, step_px: float, last_level: int) -> list[int]:
    if end >= start or step_px <= 0:
        return []
    levels: list[int] = []
    idx = last_level + 1
    while anchor - (idx * step_px) >= end:
        if anchor - (idx * step_px) < start:
            levels.append(idx)
        idx += 1
    return levels


def _segment_path(bar: dict[str, Any]) -> list[float]:
    open_px = float(bar["open"])
    high_px = float(bar["high"])
    low_px = float(bar["low"])
    close_px = float(bar["close"])
    if close_px >= open_px:
        return [open_px, high_px, low_px, close_px]
    return [open_px, low_px, high_px, close_px]


def simulate_contract(
    contract: SnakeContract,
    bars: list[dict[str, Any]],
    info,
    *,
    starting_balance_usd: float = 0.0,
    account_leverage: float = DEFAULT_ACCOUNT_LEVERAGE,
    margin_stopout_level_pct: float = DEFAULT_MARGIN_STOPOUT_LEVEL_PCT,
) -> dict[str, Any]:
    if not bars:
        return {}
    spread_px = spread_price(info)
    pip_px = pip_size_for(info)
    ema_rows = compute_ema_ladders(bars, [3, 12, 24, 64, 128, 500])
    tickets: list[SnakeTicket] = []
    anchor = float(bars[0]["close"])
    high_level = 0
    low_level = 0
    stats: dict[str, Any] = {
        "realized_net_usd": 0.0,
        "gross_positive_booked_usd": 0.0,
        "realized_closes": 0,
        "wins": 0,
        "losses": 0,
        "close_pnls": [],
        "opens": 0,
        "hedge_opens": 0,
        "max_open_total": 0,
        "max_open_sell": 0,
        "max_open_buy": 0,
        "float_zero_closes": 0,
        "best_total_floating": -999999.0,
        "min_floating_pnl_usd": 0.0,
        "max_floating_pnl_usd": 0.0,
        "min_combined_equity_usd": 0.0,
        "max_combined_equity_usd": 0.0,
        "min_realized_cover_gap_usd": 0.0,
        "min_combined_equity_delta_usd": 0.0,
        "realized_cover_violation_bars": 0,
        "profit_lock_closes": 0,
        "lock_and_release_locks": 0,
        "lock_and_release_releases": 0,
        "max_used_margin_usd": 0.0,
        "min_free_margin_usd": float(starting_balance_usd),
        "min_margin_level_pct": float("inf"),
        "margin_stopout_bars": 0,
    }

    for bar_idx, bar in enumerate(bars):
        bar_time = int(bar["time"])
        path = _segment_path(bar)
        dynamic_context = {
            "ema_fast_3": float((ema_rows[bar_idx] or {}).get(3, bar["close"])),
            "ema_light_12": float((ema_rows[bar_idx] or {}).get(12, bar["close"])),
            "ema_mid_64": float((ema_rows[bar_idx] or {}).get(64, bar["close"])),
            "ema_mid_128": float((ema_rows[bar_idx] or {}).get(128, bar["close"])),
            "ema_slow_500": float((ema_rows[bar_idx] or {}).get(500, bar["close"])),
            "free_margin": float(stats["min_free_margin_usd"]), # Use previous bar's min free margin as proxy
            "starting_balance": float(starting_balance_usd),
        }
        active_step, sell_divisor, buy_divisor, rebase_allowed = _resolve_controller_state(
            contract,
            dynamic_context,
            pip_px=pip_px,
            open_count=len(tickets),
        )
        for start, end in zip(path, path[1:]):
            for level in _cross_up_levels(anchor, start, end, active_step, high_level):
                if (
                    sum(1 for ticket in tickets if ticket.direction == "SELL") < contract.max_open_per_side
                    and (sell_divisor <= 1 or level % sell_divisor == 0)
                ):
                    entry_price = anchor + (level * active_step)
                    tickets.append(
                        SnakeTicket(
                            direction="SELL",
                            entry_price=entry_price,
                            opened_time=bar_time,
                        )
                    )
                    stats["opens"] += 1
                    _maybe_add_hedge_ticket(
                        tickets=tickets,
                        contract=contract,
                        level_direction="SELL",
                        entry_price=entry_price,
                        bar_time=bar_time,
                        stats=stats,
                    )
                high_level = max(high_level, level)
            for level in _cross_down_levels(anchor, start, end, active_step, low_level):
                if (
                    sum(1 for ticket in tickets if ticket.direction == "BUY") < contract.max_open_per_side
                    and (buy_divisor <= 1 or level % buy_divisor == 0)
                ):
                    entry_price = anchor - (level * active_step)
                    tickets.append(
                        SnakeTicket(
                            direction="BUY",
                            entry_price=entry_price,
                            opened_time=bar_time,
                        )
                    )
                    stats["opens"] += 1
                    _maybe_add_hedge_ticket(
                        tickets=tickets,
                        contract=contract,
                        level_direction="BUY",
                        entry_price=entry_price,
                        bar_time=bar_time,
                        stats=stats,
                    )
                low_level = max(low_level, level)
            _update_floating_stats(
                symbol=contract.symbol,
                symbol_info=info,
                tickets=tickets,
                price=end,
                spread_px=spread_px,
                stats=stats,
                starting_balance_usd=float(starting_balance_usd),
                account_leverage=float(account_leverage),
                margin_stopout_level_pct=float(margin_stopout_level_pct),
            )
            _maybe_apply_profit_lock(
                symbol=contract.symbol,
                symbol_info=info,
                tickets=tickets,
                price=end,
                spread_px=spread_px,
                contract=contract,
                stats=stats,
            )
            if "pair_counter" not in locals():
                pair_counter = {"next": 1}
            _maybe_apply_lock_and_release(
                symbol=contract.symbol,
                symbol_info=info,
                tickets=tickets,
                price=end,
                spread_px=spread_px,
                contract=contract,
                stats=stats,
                pair_counter=pair_counter,
            )
            _apply_closes(
                symbol=contract.symbol,
                symbol_info=info,
                tickets=tickets,
                price=end,
                spread_px=spread_px,
                contract=contract,
                stats=stats,
            )
            if stats.get("reanchor_requested"):
                anchor = float(end)
                high_level = 0
                low_level = 0
                stats["reanchor_requested"] = False
                continue

            if rebase_allowed and not tickets:
                anchor = float(end)
                high_level = 0
                low_level = 0
        open_sell = sum(1 for ticket in tickets if ticket.direction == "SELL")
        open_buy = sum(1 for ticket in tickets if ticket.direction == "BUY")
        open_total = open_sell + open_buy
        stats["max_open_total"] = max(int(stats["max_open_total"]), open_total)
        stats["max_open_sell"] = max(int(stats["max_open_sell"]), open_sell)
        stats["max_open_buy"] = max(int(stats["max_open_buy"]), open_buy)

    duration_hours = max(1e-9, (len(bars) * TIMEFRAME_SECONDS[contract.timeframe]) / 3600.0)
    realized_net_usd = float(stats["realized_net_usd"])
    gross_positive_booked_usd = float(stats["gross_positive_booked_usd"])
    realized_closes = int(stats["realized_closes"])
    return {
        "symbol": contract.symbol,
        "timeframe": contract.timeframe,
        "variant_label": contract.variant_label,
        "step_px": round(contract.step_px, 8),
        "step_pips": round(contract.step_px / pip_size_for(info), 3),
        "retrace_steps": int(contract.retrace_steps),
        "hold_frontier": int(contract.hold_frontier),
        "rebase_on_flat": bool(contract.rebase_on_flat),
        "controller_mode": str(contract.controller_mode),
        "portfolio_close_mode": str(contract.portfolio_close_mode),
        "hedge_mode": str(contract.hedge_mode),
        "hedge_trigger_depth": int(contract.hedge_trigger_depth),
        "hedge_profit_threshold_steps": int(contract.hedge_profit_threshold_steps),
        "max_open_per_side": int(contract.max_open_per_side),
        "opens": int(stats["opens"]),
        "hedge_opens": int(stats["hedge_opens"]),
        "profit_lock_closes": int(stats["profit_lock_closes"]),
        "lock_and_release_locks": int(stats["lock_and_release_locks"]),
        "lock_and_release_releases": int(stats["lock_and_release_releases"]),
        "realized_closes": realized_closes,
        "wins": int(stats["wins"]),
        "float_zero_closes": int(stats["float_zero_closes"]),
        "realized_net_usd": round(realized_net_usd, 2),
        "gross_positive_booked_usd": round(gross_positive_booked_usd, 2),
        "gross_positive_booked_usd_per_hour": round(gross_positive_booked_usd / duration_hours, 3),
        "realized_usd_per_hour": round(realized_net_usd / duration_hours, 3),
        "avg_close_usd": round(realized_net_usd / realized_closes, 4) if realized_closes else 0.0,
        "max_open_total": int(stats["max_open_total"]),
        "max_open_sell": int(stats["max_open_sell"]),
        "max_open_buy": int(stats["max_open_buy"]),
        "min_floating_pnl_usd": round(float(stats["min_floating_pnl_usd"]), 2),
        "max_floating_pnl_usd": round(float(stats["max_floating_pnl_usd"]), 2),
        "min_combined_equity_usd": round(float(stats["min_combined_equity_usd"]), 2),
        "max_combined_equity_usd": round(float(stats["max_combined_equity_usd"]), 2),
        "max_adverse_excursion_usd": round(float(stats["min_floating_pnl_usd"]), 2),
        "final_open_count": len(tickets),
        "min_realized_cover_gap_usd": round(float(stats["min_realized_cover_gap_usd"]), 2),
        "min_combined_equity_delta_usd": round(float(stats["min_combined_equity_delta_usd"]), 2),
        "realized_cover_violation_bars": int(stats["realized_cover_violation_bars"]),
        "max_used_margin_usd": round(float(stats["max_used_margin_usd"]), 2),
        "min_free_margin_usd": round(float(stats["min_free_margin_usd"]), 2),
        "min_margin_level_pct": None
        if float(stats["min_margin_level_pct"]) == float("inf")
        else round(float(stats["min_margin_level_pct"]), 2),
        "margin_stopout_bars": int(stats["margin_stopout_bars"]),
    }


def score_key(row: dict[str, Any], *, rank_mode: str = "booked_usd_per_hour") -> tuple[float, float, float, float]:
    if str(rank_mode) == "balanced":
        return (
            float(row.get("gross_positive_booked_usd_per_hour") or 0.0),
            float(row.get("realized_usd_per_hour") or 0.0),
            -float(row.get("max_open_total") or 0.0),
            float(row.get("avg_close_usd") or 0.0),
        )
    return (
        float(row.get("gross_positive_booked_usd_per_hour") or 0.0),
        float(row.get("avg_close_usd") or 0.0),
        float(row.get("realized_usd_per_hour") or 0.0),
        -float(row.get("max_open_total") or 0.0),
    )


def constraint_filter_payload(args: argparse.Namespace) -> dict[str, Any]:
    return {
        "max_mae_abs_usd": safe_float(getattr(args, "max_mae_abs_usd", None)),
        "max_final_open": None if getattr(args, "max_final_open", None) is None else int(args.max_final_open),
        "max_max_open": None if getattr(args, "max_max_open", None) is None else int(args.max_max_open),
        "require_realized_cover": bool(getattr(args, "require_realized_cover", False)),
        "starting_balance_usd": safe_float(getattr(args, "starting_balance_usd", None)),
        "hard_floor_usd": safe_float(getattr(args, "hard_floor_usd", None)),
        "account_leverage": safe_float(getattr(args, "account_leverage", None)),
        "margin_stopout_level_pct": safe_float(getattr(args, "margin_stopout_level_pct", None)),
        "require_margin_survival": bool(getattr(args, "require_margin_survival", False)),
    }


def row_meets_constraints(row: dict[str, Any], args: argparse.Namespace) -> bool:
    max_mae_abs_usd = safe_float(getattr(args, "max_mae_abs_usd", None))
    if max_mae_abs_usd is not None and abs(float(row.get("max_adverse_excursion_usd", 0.0) or 0.0)) > max_mae_abs_usd:
        return False
    max_final_open = getattr(args, "max_final_open", None)
    if max_final_open is not None and int(row.get("final_open_count", 0) or 0) > int(max_final_open):
        return False
    max_max_open = getattr(args, "max_max_open", None)
    if max_max_open is not None and int(row.get("max_open_total", 0) or 0) > int(max_max_open):
        return False
    if bool(getattr(args, "require_realized_cover", False)):
        if float(row.get("min_realized_cover_gap_usd", 0.0) or 0.0) < 0.0:
            return False
    starting_balance_usd = safe_float(getattr(args, "starting_balance_usd", None))
    hard_floor_usd = safe_float(getattr(args, "hard_floor_usd", None))
    if starting_balance_usd is not None and hard_floor_usd is not None:
        min_equity = float(starting_balance_usd) + float(row.get("min_combined_equity_delta_usd", 0.0) or 0.0)
        if min_equity < float(hard_floor_usd):
            return False
    if bool(getattr(args, "require_margin_survival", False)):
        if int(row.get("margin_stopout_bars", 0) or 0) > 0:
            return False
        min_free_margin_usd = safe_float(row.get("min_free_margin_usd", None))
        if min_free_margin_usd is not None and min_free_margin_usd < 0.0:
            return False
        min_margin_level_pct = safe_float(row.get("min_margin_level_pct", None))
        margin_stopout_level_pct = safe_float(getattr(args, "margin_stopout_level_pct", None))
        if (
            min_margin_level_pct is not None
            and margin_stopout_level_pct is not None
            and min_margin_level_pct < float(margin_stopout_level_pct)
        ):
            return False
    return True


def filter_rows(rows: list[dict[str, Any]], args: argparse.Namespace) -> list[dict[str, Any]]:
    return [row for row in rows if row_meets_constraints(row, args)]


def build_markdown(
    rows: list[dict[str, Any]],
    *,
    timeframe: str,
    days: int,
    rank_mode: str,
    constraint_filter: dict[str, Any] | None = None,
    raw_row_count: int | None = None,
) -> str:
    lines = [
        "# Snake Counter-Web Study",
        "",
        f"- Generated: `{utc_now_iso()}`",
        f"- Timeframe: `{timeframe}`",
        f"- Days: `{days}`",
        f"- Rank mode: `{rank_mode}`",
        "- Objective: maximize booked positive USD/hour for the separate snake counter-order web prototype.",
        "",
    ]
    if constraint_filter and any(value is not None and value is not False for value in constraint_filter.values()):
        lines.extend(
            [
                "- Constraint filter:",
                f"  - `max_mae_abs_usd={constraint_filter.get('max_mae_abs_usd')}`",
                f"  - `max_final_open={constraint_filter.get('max_final_open')}`",
                f"  - `max_max_open={constraint_filter.get('max_max_open')}`",
                f"  - `require_realized_cover={constraint_filter.get('require_realized_cover')}`",
                f"  - `starting_balance_usd={constraint_filter.get('starting_balance_usd')}`",
                f"  - `hard_floor_usd={constraint_filter.get('hard_floor_usd')}`",
                f"  - `account_leverage={constraint_filter.get('account_leverage')}`",
                f"  - `margin_stopout_level_pct={constraint_filter.get('margin_stopout_level_pct')}`",
                f"  - `require_margin_survival={constraint_filter.get('require_margin_survival')}`",
                "",
            ]
        )
    if raw_row_count is not None and raw_row_count != len(rows):
        lines.append(f"- Survivors after filtering: `{len(rows)}` / `{raw_row_count}`")
        lines.append("")
    for symbol in sorted({str(row["symbol"]) for row in rows}):
        symbol_rows = [row for row in rows if row["symbol"] == symbol]
        symbol_rows.sort(key=lambda row: score_key(row, rank_mode=rank_mode), reverse=True)
        best = symbol_rows[0]
        lines.extend(
            [
                f"## {symbol}",
                "",
                f"- Best row: `{best['variant_label']}`",
                f"- Gross positive booked USD/hour: `${best['gross_positive_booked_usd_per_hour']}`",
                f"- Net USD/hour: `${best['realized_usd_per_hour']}`",
                f"- Avg close USD: `${best['avg_close_usd']}`",
                f"- Portfolio close mode: `{best['portfolio_close_mode']}`",
                f"- Opens / closes: `{best['opens']}` / `{best['realized_closes']}`",
                f"- Max / final open: `{best['max_open_total']}` / `{best['final_open_count']}`",
                f"- Min / max floating PnL: `${best['min_floating_pnl_usd']}` / `${best['max_floating_pnl_usd']}`",
                f"- Min realized cover gap / min combined equity delta: `${best['min_realized_cover_gap_usd']}` / `${best['min_combined_equity_delta_usd']}`",
                f"- Max used margin / min free margin / min margin level: `${best['max_used_margin_usd']}` / `${best['min_free_margin_usd']}` / `{best['min_margin_level_pct']}`",
                "",
                "| Variant | Portfolio close | Hedge | Gross $+/h | Net $/h | Avg close | Opens | Closes | Float-zero / lock | Best float | Max/final open | Min float | Cover gap | Equity delta | Max margin | Min free | Stopout bars |",
                "| --- | --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
            ]
        )
        for row in symbol_rows[:8]:
            lines.append(
                f"| `{row['variant_label']}` | `{row['portfolio_close_mode']}` | `{row['hedge_mode']}` | `${row['gross_positive_booked_usd_per_hour']}` | "
                f"${row['realized_usd_per_hour']}` | `${row['avg_close_usd']}` | `{row['opens']}` | "
                f"`{row['realized_closes']}` | `{row['float_zero_closes']}/{row.get('profit_lock_closes', 0)}/{row.get('lock_and_release_releases', 0)}` | "
                f"`${row.get('best_total_floating', -999999.0)}` | `{row['max_open_total']}/{row['final_open_count']}` | "
                f"`${row['min_floating_pnl_usd']}` | `${row['min_realized_cover_gap_usd']}` | `${row['min_combined_equity_delta_usd']}` | "
                f"`${row['max_used_margin_usd']}` | `${row['min_free_margin_usd']}` | `{row['margin_stopout_bars']}` |"
            )
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def main() -> int:
    args = parse_args()
    if not mt5.initialize():
        print("MetaTrader5 initialize() failed")
        return 1
    try:
        rows: list[dict[str, Any]] = []
        contracts = build_contracts(args)
        unique_symbols = sorted({contract.symbol for contract in contracts})
        infos = {symbol: mt5.symbol_info(symbol) for symbol in unique_symbols}
        bars_by_symbol = {
            symbol: load_bars(symbol, args.timeframe, args.days)
            for symbol in unique_symbols
            if infos.get(symbol) is not None
        }
        progress_file = Path(args.output_json).with_suffix(".progress.json")
        completed_labels = set()
        if progress_file.exists():
            try:
                prog_data = json.loads(progress_file.read_text(encoding="utf-8"))
                rows = prog_data.get("rows", [])
                completed_labels = {r.get("variant_label") for r in rows if r}
                print(f"Resuming from {len(completed_labels)} completed contracts...")
            except Exception:
                pass

        for contract in contracts:
            if contract.variant_label in completed_labels:
                continue
            info = infos.get(contract.symbol)
            if info is None:
                continue
            bars = bars_by_symbol.get(contract.symbol) or []
            if not bars:
                continue
            res = simulate_contract(
                contract,
                bars,
                info,
                starting_balance_usd=float(safe_float(getattr(args, "starting_balance_usd", None)) or 0.0),
                account_leverage=float(safe_float(getattr(args, "account_leverage", None)) or DEFAULT_ACCOUNT_LEVERAGE),
                margin_stopout_level_pct=float(
                    safe_float(getattr(args, "margin_stopout_level_pct", None)) or DEFAULT_MARGIN_STOPOUT_LEVEL_PCT
                ),
            )
            if res:
                rows.append(res)
                progress_file.write_text(json.dumps({"rows": [r for r in rows if r]}, indent=2), encoding="utf-8")
        rows = [row for row in rows if row]
        if not rows:
            print("No snake study rows generated.")
            return 1
        raw_row_count = len(rows)
        rows = filter_rows(rows, args)
        if not rows:
            print("No snake study rows satisfied the requested survivability constraints.")
            return 1
        rows.sort(key=lambda row: score_key(row, rank_mode=args.rank_mode), reverse=True)
        fieldnames = list(rows[0].keys())
        out_csv = Path(args.output_csv)
        out_csv.parent.mkdir(parents=True, exist_ok=True)
        with out_csv.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(rows)
        payload = {
            "generated_at": utc_now_iso(),
            "timeframe": args.timeframe,
            "days": args.days,
            "constraint_filter": constraint_filter_payload(args),
            "raw_row_count": raw_row_count,
            "rows": rows,
        }
        Path(args.output_json).write_text(json.dumps(payload, indent=2), encoding="utf-8")
        Path(args.output_md).write_text(
            build_markdown(
                rows,
                timeframe=args.timeframe,
                days=args.days,
                rank_mode=args.rank_mode,
                constraint_filter=constraint_filter_payload(args),
                raw_row_count=raw_row_count,
            ),
            encoding="utf-8",
        )
        print(json.dumps(rows[:6], indent=2))
        return 0
    finally:
        mt5.shutdown()


if __name__ == "__main__":
    raise SystemExit(main())
