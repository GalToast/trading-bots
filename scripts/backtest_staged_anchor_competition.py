#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import MetaTrader5 as mt5

from penetration_lattice_lab_v2 import VOLUME, spread_price


ROOT = Path(__file__).resolve().parent.parent
REGISTRY_PATH = ROOT / "configs" / "penetration_lattice_runner_registry.json"
DEFAULT_OUTPUT_CSV = ROOT / "reports" / "staged_anchor_competition.csv"
DEFAULT_OUTPUT_MD = ROOT / "reports" / "staged_anchor_competition.md"

TIMEFRAME_MAP = {
    "M1": mt5.TIMEFRAME_M1,
    "M5": mt5.TIMEFRAME_M5,
    "M15": mt5.TIMEFRAME_M15,
    "H1": mt5.TIMEFRAME_H1,
}


@dataclass(frozen=True)
class LiveLaneConfig:
    lane_name: str
    kind: str
    symbol: str
    timeframe: str
    step_px: float
    max_open_per_side: int


@dataclass
class Ticket:
    direction: str
    entry_price: float
    opened_idx: int
    level_idx: int
    best_price: float
    stop_price: float | None = None


def estimate_usd_per_price_unit(
    *,
    symbol: str,
    symbol_info: Any,
    reference_price: float,
) -> float:
    point = float(getattr(symbol_info, "point", 0.0) or 0.0)
    if point > 0.0:
        probe_profit = mt5.order_calc_profit(
            mt5.ORDER_TYPE_BUY,
            symbol,
            VOLUME,
            float(reference_price),
            float(reference_price) + point,
        )
        if probe_profit not in (None, 0.0):
            return abs(float(probe_profit)) / point
    tick_size = float(getattr(symbol_info, "trade_tick_size", 0.0) or 0.0)
    tick_value = float(getattr(symbol_info, "trade_tick_value", 0.0) or 0.0)
    if tick_size > 0.0 and tick_value > 0.0:
        return abs(tick_value) * float(VOLUME) / tick_size
    return 0.0


def approx_unit_pnl_usd(
    *,
    direction: str,
    entry_price: float,
    exit_price: float,
    spread_px: float,
    usd_per_price_unit: float,
) -> float:
    if direction == "BUY":
        gross = (float(exit_price) - float(entry_price)) * float(usd_per_price_unit)
    else:
        gross = (float(entry_price) - float(exit_price)) * float(usd_per_price_unit)
    spread_cost = abs(float(spread_px)) * float(usd_per_price_unit)
    return float(gross) - float(spread_cost)


def lane_case_label(cfg: LiveLaneConfig) -> str:
    return f"{cfg.symbol}:{cfg.timeframe}:{cfg.lane_name}"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Focused staged-ladder competition for anchor and close logic."
    )
    parser.add_argument("--symbols", nargs="*", default=None)
    parser.add_argument("--lane-names", nargs="*", default=None)
    parser.add_argument("--kinds", nargs="*", default=["live_crypto"])
    parser.add_argument("--include-disabled", action="store_true")
    parser.add_argument("--days", type=int, default=14)
    parser.add_argument(
        "--step-scale-values",
        nargs="*",
        type=float,
        default=None,
        help="Optional sweep values for scaling the configured step geometry.",
    )
    parser.add_argument(
        "--entry-start-values",
        nargs="*",
        type=float,
        default=None,
        help="Optional sweep values for the first ladder entry distance from anchor, in step units.",
    )
    parser.add_argument(
        "--entry-shapes",
        nargs="*",
        default=["uniform"],
        help="Entry ladder shape family: uniform, scout_double, or scout_skip.",
    )
    parser.add_argument(
        "--anchor-modes",
        nargs="*",
        default=["stable_price", "vwap20", "self_last_fill"],
    )
    parser.add_argument(
        "--handoff-steps",
        nargs="*",
        type=float,
        default=[0.0, 0.5, 1.0],
        help="Close profitable tickets when price reclaims anchor plus/minus handoff_steps * step.",
    )
    parser.add_argument(
        "--split-depth-values",
        nargs="*",
        type=int,
        default=None,
        help="Optional sweep values for inner-depth split closes before trailing the remaining book.",
    )
    parser.add_argument(
        "--close-modes",
        nargs="*",
        default=[
            "handoff",
            "trail_50",
            "trail_75",
            "handoff_then_trail_50",
            "handoff_then_trail_75",
            "handoff_inner_then_trail_75",
        ],
        help="Profitable-only close families to compare on the same staged ladder.",
    )
    parser.add_argument("--trail-activation-steps", type=float, default=1.0)
    parser.add_argument("--trail-floor-steps", type=float, default=0.25)
    parser.add_argument("--flat-reset-steps", type=float, default=2.0)
    parser.add_argument(
        "--trail-activation-values",
        nargs="*",
        type=float,
        default=None,
        help="Optional sweep values for trailing activation depth in step units.",
    )
    parser.add_argument(
        "--trail-floor-values",
        nargs="*",
        type=float,
        default=None,
        help="Optional sweep values for trailing retained floor in step units.",
    )
    parser.add_argument(
        "--flat-reset-values",
        nargs="*",
        type=float,
        default=None,
        help="Optional sweep values for stable-anchor flat reset distance in step units.",
    )
    parser.add_argument("--workers", type=int, default=1)
    parser.add_argument(
        "--aggregate-objective",
        choices=["realized", "cover_priority"],
        default="realized",
        help="How to rank aggregate doctrine rows: raw realized flow first or cover survivability first.",
    )
    parser.add_argument("--output-csv", default=str(DEFAULT_OUTPUT_CSV))
    parser.add_argument("--output-md", default=str(DEFAULT_OUTPUT_MD))
    return parser.parse_args()


def _arg_value(args: list[str], key: str, default: str = "") -> str:
    try:
        idx = args.index(key)
    except ValueError:
        return default
    if idx + 1 >= len(args):
        return default
    return str(args[idx + 1])


def load_step_ladder_configs(
    *,
    symbol_filter: set[str] | None = None,
    lane_name_filter: set[str] | None = None,
    kind_filter: set[str] | None = None,
    include_disabled: bool = False,
) -> list[LiveLaneConfig]:
    payload = json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))
    lane_rows = payload.get("lanes") if isinstance(payload, dict) else payload
    if not isinstance(lane_rows, list):
        return []
    rows: list[LiveLaneConfig] = []
    for row in lane_rows:
        lane_kind = str(row.get("kind") or "")
        if kind_filter and lane_kind not in kind_filter:
            continue
        lane_name = str(row.get("name") or "")
        if lane_name_filter and lane_name not in lane_name_filter:
            continue
        enabled_value = row.get("enabled")
        if not include_disabled and enabled_value is False:
            continue
        args = [str(v) for v in (row.get("restart_args") or [])]
        symbol = _arg_value(args, "--symbol")
        if not symbol:
            continue
        if symbol_filter and symbol not in symbol_filter:
            continue
        timeframe = _arg_value(args, "--timeframe", "M15")
        step_px = float(_arg_value(args, "--step", "0") or 0.0)
        max_open = int(
            float(
                _arg_value(
                    args,
                    "--max-open-per-side",
                    _arg_value(args, "--max-open", "0"),
                )
                or 0.0
            )
        )
        if timeframe not in TIMEFRAME_MAP or step_px <= 0.0 or max_open <= 0:
            continue
        rows.append(
            LiveLaneConfig(
                lane_name=lane_name or symbol,
                kind=lane_kind,
                symbol=symbol,
                timeframe=timeframe,
                step_px=step_px,
                max_open_per_side=max_open,
            )
        )
    return rows


def load_bars(symbol: str, timeframe_name: str, days: int) -> list[dict[str, Any]]:
    timeframe = TIMEFRAME_MAP[timeframe_name]
    bars_per_day = {
        "M1": 1440,
        "M5": 288,
        "M15": 96,
        "H1": 24,
    }[timeframe_name]
    count = bars_per_day * days
    rates = mt5.copy_rates_from_pos(symbol, timeframe, 0, count)
    if rates is None or len(rates) == 0:
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


def load_bars_cached(
    cache: dict[tuple[str, str, int], list[dict[str, Any]]],
    *,
    symbol: str,
    timeframe_name: str,
    days: int,
) -> list[dict[str, Any]]:
    key = (str(symbol), str(timeframe_name), int(days))
    if key not in cache:
        cache[key] = load_bars(symbol, timeframe_name, days)
    return cache[key]


def compute_vwap_anchor(bars: list[dict[str, Any]], idx: int, lookback: int = 20) -> float:
    start = max(0, idx - lookback + 1)
    window = bars[start : idx + 1]
    if not window:
        return float(bars[idx]["close"])
    weighted = sum(float(bar["close"]) * int(bar.get("tick_volume") or 0) for bar in window)
    volume = sum(int(bar.get("tick_volume") or 0) for bar in window)
    if volume <= 0:
        return float(window[-1]["close"])
    return float(weighted / volume)


def _segment_path(bar: dict[str, Any]) -> list[float]:
    open_px = float(bar["open"])
    high_px = float(bar["high"])
    low_px = float(bar["low"])
    close_px = float(bar["close"])
    if close_px >= open_px:
        return [open_px, high_px, low_px, close_px]
    return [open_px, low_px, high_px, close_px]


def _cross_up_levels(
    anchor: float,
    start: float,
    end: float,
    step_px: float,
    last_level: int,
    entry_start_steps: float,
    entry_shape: str,
) -> list[int]:
    if end <= start:
        return []
    levels: list[int] = []
    idx = max(1, int(last_level) + 1)
    while True:
        level_px = anchor + (_entry_offset_steps(float(entry_start_steps), str(entry_shape), idx) * step_px)
        if level_px <= start:
            idx += 1
            continue
        if level_px > end:
            break
        levels.append(idx)
        idx += 1
    return levels


def _cross_down_levels(
    anchor: float,
    start: float,
    end: float,
    step_px: float,
    last_level: int,
    entry_start_steps: float,
    entry_shape: str,
) -> list[int]:
    if end >= start:
        return []
    levels: list[int] = []
    idx = max(1, int(last_level) + 1)
    while True:
        level_px = anchor - (_entry_offset_steps(float(entry_start_steps), str(entry_shape), idx) * step_px)
        if level_px >= start:
            idx += 1
            continue
        if level_px < end:
            break
        levels.append(idx)
        idx += 1
    return levels


def _entry_offset_steps(entry_start_steps: float, entry_shape: str, level_idx: int) -> float:
    start = float(entry_start_steps)
    idx = int(level_idx)
    shape = str(entry_shape)
    if idx <= 1:
        return start
    if shape == "uniform":
        return start + float(idx - 1)
    if shape == "scout_double":
        if idx == 2:
            return start + 0.5
        return start + float(idx) - 1.0
    if shape == "scout_skip":
        if idx == 2:
            return start + 1.5
        return start + float(idx) - 0.5
    raise ValueError(f"Unsupported entry_shape={shape}")


def _profit_close_threshold(anchor: float, step_px: float, direction: str, handoff_steps: float) -> float:
    if direction == "SELL":
        return float(anchor + (step_px * handoff_steps))
    return float(anchor - (step_px * handoff_steps))


def _maybe_close_profitable(
    *,
    tickets: list[Ticket],
    symbol: str,
    price: float,
    spread_px: float,
    usd_per_price_unit: float,
    anchor: float,
    step_px: float,
    handoff_steps: float,
    stats: dict[str, Any],
    max_level_idx: int | None = None,
) -> list[Ticket]:
    remaining: list[Ticket] = []
    for ticket in tickets:
        if max_level_idx is not None and int(ticket.level_idx) > int(max_level_idx):
            remaining.append(ticket)
            continue
        threshold = _profit_close_threshold(anchor, step_px, ticket.direction, handoff_steps)
        if ticket.direction == "SELL":
            touched = price <= threshold
        else:
            touched = price >= threshold
        if not touched:
            remaining.append(ticket)
            continue
        pnl = approx_unit_pnl_usd(
            direction=ticket.direction,
            entry_price=float(ticket.entry_price),
            exit_price=float(threshold),
            spread_px=float(spread_px),
            usd_per_price_unit=float(usd_per_price_unit),
        )
        if pnl > 0.0:
            stats["realized_net_usd"] += float(pnl)
            stats["realized_closes"] += 1
            stats["wins"] += 1
            stats["close_pnls"].append(float(pnl))
        else:
            remaining.append(ticket)
    return remaining


def _maybe_close_trailing(
    *,
    tickets: list[Ticket],
    symbol: str,
    start: float,
    end: float,
    spread_px: float,
    usd_per_price_unit: float,
    step_px: float,
    retain_ratio: float,
    activation_steps: float,
    floor_steps: float,
    stats: dict[str, Any],
) -> list[Ticket]:
    remaining: list[Ticket] = []
    activation_px = float(activation_steps) * float(step_px)
    floor_px = float(floor_steps) * float(step_px)
    for ticket in tickets:
        if ticket.direction == "BUY":
            if end > float(ticket.best_price):
                ticket.best_price = float(end)
            mfe_px = float(ticket.best_price) - float(ticket.entry_price)
            if mfe_px >= activation_px:
                retained_px = max(floor_px, mfe_px * float(retain_ratio))
                next_stop = float(ticket.entry_price) + retained_px
                ticket.stop_price = max(float(ticket.stop_price) if ticket.stop_price is not None else next_stop, next_stop)
            crossed = (
                ticket.stop_price is not None
                and end < start
                and float(end) <= float(ticket.stop_price) <= float(start)
            )
            if not crossed:
                remaining.append(ticket)
                continue
            exit_price = float(ticket.stop_price)
        else:
            if end < float(ticket.best_price):
                ticket.best_price = float(end)
            mfe_px = float(ticket.entry_price) - float(ticket.best_price)
            if mfe_px >= activation_px:
                retained_px = max(floor_px, mfe_px * float(retain_ratio))
                next_stop = float(ticket.entry_price) - retained_px
                ticket.stop_price = min(float(ticket.stop_price) if ticket.stop_price is not None else next_stop, next_stop)
            crossed = (
                ticket.stop_price is not None
                and end > start
                and float(start) <= float(ticket.stop_price) <= float(end)
            )
            if not crossed:
                remaining.append(ticket)
                continue
            exit_price = float(ticket.stop_price)
        pnl = approx_unit_pnl_usd(
            direction=ticket.direction,
            entry_price=float(ticket.entry_price),
            exit_price=float(exit_price),
            spread_px=float(spread_px),
            usd_per_price_unit=float(usd_per_price_unit),
        )
        if pnl > 0.0:
            stats["realized_net_usd"] += float(pnl)
            stats["realized_closes"] += 1
            stats["wins"] += 1
            stats["close_pnls"].append(float(pnl))
            stats["trail_closes"] += 1
        else:
            remaining.append(ticket)
    return remaining


def simulate_contract(
    *,
    cfg: LiveLaneConfig,
    bars: list[dict[str, Any]],
    spread_px: float,
    usd_per_price_unit: float,
    step_scale: float,
    entry_start_steps: float,
    entry_shape: str,
    anchor_mode: str,
    close_mode: str,
    handoff_steps: float,
    split_depth: int,
    flat_reset_steps: float,
    trail_activation_steps: float,
    trail_floor_steps: float,
) -> dict[str, Any]:
    if not bars:
        return {}
    step_px = float(cfg.step_px) * float(step_scale)
    tickets: list[Ticket] = []
    anchor = float(bars[0]["close"])
    sell_anchor = float(anchor)
    buy_anchor = float(anchor)
    high_level = 0
    low_level = 0
    stats: dict[str, Any] = {
        "realized_net_usd": 0.0,
        "realized_closes": 0,
        "wins": 0,
        "close_pnls": [],
        "opens": 0,
        "anchor_resets": 0,
        "max_open_total": 0,
        "min_floating_usd": 0.0,
        "max_floating_usd": 0.0,
        "min_realized_cover_gap_usd": 0.0,
        "min_combined_equity_delta_usd": 0.0,
        "realized_cover_violation_bars": 0,
        "final_open_count": 0,
        "trail_closes": 0,
        "handoff_closes": 0,
    }

    for idx, bar in enumerate(bars):
        if anchor_mode == "vwap20":
            anchor = compute_vwap_anchor(bars, idx, 20)
            sell_anchor = float(anchor)
            buy_anchor = float(anchor)
        path = _segment_path(bar)
        for start, end in zip(path, path[1:]):
            if anchor_mode in {"stable_price", "vwap20"}:
                for level in _cross_up_levels(sell_anchor, start, end, step_px, high_level, float(entry_start_steps), str(entry_shape)):
                    if sum(1 for ticket in tickets if ticket.direction == "SELL") >= cfg.max_open_per_side:
                        break
                    entry_price = float(sell_anchor + (_entry_offset_steps(float(entry_start_steps), str(entry_shape), level) * step_px))
                    tickets.append(Ticket(direction="SELL", entry_price=entry_price, opened_idx=idx, level_idx=int(level), best_price=entry_price))
                    stats["opens"] += 1
                high_level = max(high_level, max(_cross_up_levels(sell_anchor, start, end, step_px, high_level, float(entry_start_steps), str(entry_shape)) or [high_level]))
                for level in _cross_down_levels(buy_anchor, start, end, step_px, low_level, float(entry_start_steps), str(entry_shape)):
                    if sum(1 for ticket in tickets if ticket.direction == "BUY") >= cfg.max_open_per_side:
                        break
                    entry_price = float(buy_anchor - (_entry_offset_steps(float(entry_start_steps), str(entry_shape), level) * step_px))
                    tickets.append(Ticket(direction="BUY", entry_price=entry_price, opened_idx=idx, level_idx=int(level), best_price=entry_price))
                    stats["opens"] += 1
                low_level = max(low_level, max(_cross_down_levels(buy_anchor, start, end, step_px, low_level, float(entry_start_steps), str(entry_shape)) or [low_level]))
            elif anchor_mode == "self_last_fill":
                while end > start and end >= sell_anchor + step_px:
                    if sum(1 for ticket in tickets if ticket.direction == "SELL") >= cfg.max_open_per_side:
                        break
                    high_level += 1
                    sell_anchor = float(sell_anchor + step_px)
                    tickets.append(Ticket(direction="SELL", entry_price=float(sell_anchor), opened_idx=idx, level_idx=int(high_level), best_price=float(sell_anchor)))
                    stats["opens"] += 1
                while end < start and end <= buy_anchor - step_px:
                    if sum(1 for ticket in tickets if ticket.direction == "BUY") >= cfg.max_open_per_side:
                        break
                    low_level += 1
                    buy_anchor = float(buy_anchor - step_px)
                    tickets.append(Ticket(direction="BUY", entry_price=float(buy_anchor), opened_idx=idx, level_idx=int(low_level), best_price=float(buy_anchor)))
                    stats["opens"] += 1
            else:
                raise ValueError(f"Unsupported anchor_mode={anchor_mode}")

            if close_mode == "handoff":
                tickets = _maybe_close_profitable(
                    tickets=tickets,
                    symbol=cfg.symbol,
                    price=float(end),
                    spread_px=spread_px,
                    usd_per_price_unit=float(usd_per_price_unit),
                    anchor=anchor,
                    step_px=step_px,
                    handoff_steps=float(handoff_steps),
                    stats=stats,
                )
                stats["handoff_closes"] = int(stats["realized_closes"]) - int(stats["trail_closes"])
            elif close_mode == "trail_50":
                tickets = _maybe_close_trailing(
                    tickets=tickets,
                    symbol=cfg.symbol,
                    start=float(start),
                    end=float(end),
                    spread_px=spread_px,
                    usd_per_price_unit=float(usd_per_price_unit),
                    step_px=step_px,
                    retain_ratio=0.50,
                    activation_steps=float(trail_activation_steps),
                    floor_steps=float(trail_floor_steps),
                    stats=stats,
                )
            elif close_mode == "trail_75":
                tickets = _maybe_close_trailing(
                    tickets=tickets,
                    symbol=cfg.symbol,
                    start=float(start),
                    end=float(end),
                    spread_px=spread_px,
                    usd_per_price_unit=float(usd_per_price_unit),
                    step_px=step_px,
                    retain_ratio=0.75,
                    activation_steps=float(trail_activation_steps),
                    floor_steps=float(trail_floor_steps),
                    stats=stats,
                )
            elif close_mode == "handoff_then_trail_50":
                before_handoff = int(stats["realized_closes"])
                tickets = _maybe_close_profitable(
                    tickets=tickets,
                    symbol=cfg.symbol,
                    price=float(end),
                    spread_px=spread_px,
                    usd_per_price_unit=float(usd_per_price_unit),
                    anchor=anchor,
                    step_px=step_px,
                    handoff_steps=float(handoff_steps),
                    stats=stats,
                )
                stats["handoff_closes"] += int(stats["realized_closes"]) - before_handoff
                tickets = _maybe_close_trailing(
                    tickets=tickets,
                    symbol=cfg.symbol,
                    start=float(start),
                    end=float(end),
                    spread_px=spread_px,
                    usd_per_price_unit=float(usd_per_price_unit),
                    step_px=step_px,
                    retain_ratio=0.50,
                    activation_steps=float(trail_activation_steps),
                    floor_steps=float(trail_floor_steps),
                    stats=stats,
                )
            elif close_mode == "handoff_then_trail_75":
                before_handoff = int(stats["realized_closes"])
                tickets = _maybe_close_profitable(
                    tickets=tickets,
                    symbol=cfg.symbol,
                    price=float(end),
                    spread_px=spread_px,
                    usd_per_price_unit=float(usd_per_price_unit),
                    anchor=anchor,
                    step_px=step_px,
                    handoff_steps=float(handoff_steps),
                    stats=stats,
                )
                stats["handoff_closes"] += int(stats["realized_closes"]) - before_handoff
                tickets = _maybe_close_trailing(
                    tickets=tickets,
                    symbol=cfg.symbol,
                    start=float(start),
                    end=float(end),
                    spread_px=spread_px,
                    usd_per_price_unit=float(usd_per_price_unit),
                    step_px=step_px,
                    retain_ratio=0.75,
                    activation_steps=float(trail_activation_steps),
                    floor_steps=float(trail_floor_steps),
                    stats=stats,
                )
            elif close_mode == "handoff_inner_then_trail_75":
                before_handoff = int(stats["realized_closes"])
                tickets = _maybe_close_profitable(
                    tickets=tickets,
                    symbol=cfg.symbol,
                    price=float(end),
                    spread_px=spread_px,
                    usd_per_price_unit=float(usd_per_price_unit),
                    anchor=anchor,
                    step_px=step_px,
                    handoff_steps=float(handoff_steps),
                    stats=stats,
                    max_level_idx=int(split_depth),
                )
                stats["handoff_closes"] += int(stats["realized_closes"]) - before_handoff
                tickets = _maybe_close_trailing(
                    tickets=tickets,
                    symbol=cfg.symbol,
                    start=float(start),
                    end=float(end),
                    spread_px=spread_px,
                    usd_per_price_unit=float(usd_per_price_unit),
                    step_px=step_px,
                    retain_ratio=0.75,
                    activation_steps=float(trail_activation_steps),
                    floor_steps=float(trail_floor_steps),
                    stats=stats,
                )
            else:
                raise ValueError(f"Unsupported close_mode={close_mode}")

            floating = sum(
                approx_unit_pnl_usd(
                    direction=ticket.direction,
                    entry_price=float(ticket.entry_price),
                    exit_price=float(end),
                    spread_px=float(spread_px),
                    usd_per_price_unit=float(usd_per_price_unit),
                )
                for ticket in tickets
            )
            realized_cover_gap = float(stats["realized_net_usd"]) - abs(min(0.0, float(floating)))
            combined_equity_delta = float(stats["realized_net_usd"]) + float(floating)
            stats["min_floating_usd"] = min(float(stats["min_floating_usd"]), float(floating))
            stats["max_floating_usd"] = max(float(stats["max_floating_usd"]), float(floating))
            stats["min_realized_cover_gap_usd"] = min(
                float(stats["min_realized_cover_gap_usd"]), float(realized_cover_gap)
            )
            stats["min_combined_equity_delta_usd"] = min(
                float(stats["min_combined_equity_delta_usd"]), float(combined_equity_delta)
            )
            if realized_cover_gap < 0.0:
                stats["realized_cover_violation_bars"] += 1

        if anchor_mode == "stable_price" and not tickets:
            if abs(float(bar["close"]) - float(anchor)) >= (step_px * float(flat_reset_steps)):
                anchor = float(bar["close"])
                sell_anchor = float(anchor)
                buy_anchor = float(anchor)
                high_level = 0
                low_level = 0
                stats["anchor_resets"] += 1
        stats["max_open_total"] = max(int(stats["max_open_total"]), len(tickets))

    stats["final_open_count"] = len(tickets)
    hours = max((int(bars[-1]["time"]) - int(bars[0]["time"])) / 3600.0, 0.01)
    realized_usd_per_hour = float(stats["realized_net_usd"]) / float(hours)
    avg_close_usd = (
        float(stats["realized_net_usd"]) / float(stats["realized_closes"])
        if int(stats["realized_closes"]) > 0
        else 0.0
    )
    return {
        "symbol": cfg.symbol,
        "timeframe": cfg.timeframe,
        "lane_name": cfg.lane_name,
        "lane_case": lane_case_label(cfg),
        "step_scale": float(step_scale),
        "entry_start_steps": float(entry_start_steps),
        "entry_shape": str(entry_shape),
        "step_px": round(step_px, 8),
        "anchor_mode": str(anchor_mode),
        "close_mode": str(close_mode),
        "handoff_steps": float(handoff_steps),
        "split_depth": int(split_depth),
        "flat_reset_steps": float(flat_reset_steps),
        "trail_activation_steps": float(trail_activation_steps),
        "trail_floor_steps": float(trail_floor_steps),
        "realized_net_usd": round(float(stats["realized_net_usd"]), 3),
        "realized_usd_per_hour": round(float(realized_usd_per_hour), 4),
        "realized_closes": int(stats["realized_closes"]),
        "handoff_closes": int(stats["handoff_closes"]),
        "trail_closes": int(stats["trail_closes"]),
        "avg_close_usd": round(float(avg_close_usd), 4),
        "opens": int(stats["opens"]),
        "max_open_total": int(stats["max_open_total"]),
        "final_open_count": int(stats["final_open_count"]),
        "min_floating_usd": round(float(stats["min_floating_usd"]), 3),
        "max_floating_usd": round(float(stats["max_floating_usd"]), 3),
        "min_realized_cover_gap_usd": round(float(stats["min_realized_cover_gap_usd"]), 3),
        "min_combined_equity_delta_usd": round(float(stats["min_combined_equity_delta_usd"]), 3),
        "realized_cover_violation_bars": int(stats["realized_cover_violation_bars"]),
        "anchor_resets": int(stats["anchor_resets"]),
    }


def run_cfg_grid(
    *,
    cfg: LiveLaneConfig,
    bars: list[dict[str, Any]],
    spread_px: float,
    usd_per_price_unit: float,
    step_scale_values: list[float],
    entry_start_values: list[float],
    entry_shapes: list[str],
    anchor_modes: list[str],
    close_modes: list[str],
    handoff_steps_values: list[float],
    split_depth_values: list[int],
    flat_reset_values: list[float],
    trail_activation_values: list[float],
    trail_floor_values: list[float],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for step_scale in step_scale_values:
        for entry_start_steps in entry_start_values:
            for entry_shape in entry_shapes:
                for anchor_mode in anchor_modes:
                    for close_mode in close_modes:
                        close_mode_name = str(close_mode)
                        if close_mode_name == "handoff" or close_mode_name.startswith("handoff_then_") or close_mode_name == "handoff_inner_then_trail_75":
                            handoff_values = handoff_steps_values
                        else:
                            handoff_values = [0.0]
                        for handoff_steps in handoff_values:
                            split_values = split_depth_values if close_mode_name == "handoff_inner_then_trail_75" else [0]
                            for split_depth in split_values:
                                for flat_reset_steps in flat_reset_values:
                                    for trail_activation_steps in trail_activation_values:
                                        for trail_floor_steps in trail_floor_values:
                                            row = simulate_contract(
                                                cfg=cfg,
                                                bars=bars,
                                                spread_px=float(spread_px),
                                                usd_per_price_unit=float(usd_per_price_unit),
                                                step_scale=float(step_scale),
                                                entry_start_steps=float(entry_start_steps),
                                                entry_shape=str(entry_shape),
                                                anchor_mode=str(anchor_mode),
                                                close_mode=close_mode_name,
                                                handoff_steps=float(handoff_steps),
                                                split_depth=int(split_depth),
                                                flat_reset_steps=float(flat_reset_steps),
                                                trail_activation_steps=float(trail_activation_steps),
                                                trail_floor_steps=float(trail_floor_steps),
                                            )
                                            if row:
                                                rows.append(row)
    return rows


def run_cfg_grid_task(task: dict[str, Any]) -> list[dict[str, Any]]:
    return run_cfg_grid(**task)


def sort_key(row: dict[str, Any]) -> tuple[float, float, float, int]:
    return (
        float(row.get("realized_usd_per_hour") or 0.0),
        float(row.get("realized_net_usd") or 0.0),
        -abs(float(row.get("min_floating_usd") or 0.0)),
        -int(row.get("max_open_total") or 0),
    )


def aggregate_sort_key(row: dict[str, Any], *, objective: str) -> tuple[float, ...]:
    if objective == "cover_priority":
        return (
            float(int(bool(row.get("cover_safe_pass")))),
            float(int(bool(row.get("universal_pass")))),
            float(int(row.get("cover_safe_cases") or 0)),
            float(row.get("min_realized_cover_gap_usd_sum") or 0.0),
            float(row.get("min_combined_equity_delta_usd_sum") or 0.0),
            float(row.get("realized_usd_per_hour") or 0.0),
            float(row.get("realized_net_usd") or 0.0),
            -abs(float(row.get("min_floating_usd_sum") or 0.0)),
            -float(int(row.get("max_open_total_sum") or 0)),
        )
    return (
        float(int(bool(row.get("universal_pass")))),
        float(int(row.get("positive_cases") or 0)),
        float(row.get("realized_usd_per_hour") or 0.0),
        float(row.get("realized_net_usd") or 0.0),
        -abs(float(row.get("min_floating_usd_sum") or 0.0)),
        -float(int(row.get("max_open_total_sum") or 0)),
    )


def aggregate_rows(
    rows: list[dict[str, Any]], *, expected_cases: int, objective: str = "realized"
) -> list[dict[str, Any]]:
    grouped: dict[tuple[str, str, float, int, float, float, float, float, str], dict[str, Any]] = {}
    for row in rows:
        key = (
            str(row.get("anchor_mode") or ""),
            str(row.get("close_mode") or ""),
            float(row.get("handoff_steps") or 0.0),
            int(row.get("split_depth") or 0),
            float(row.get("flat_reset_steps") or 2.0),
            float(row.get("trail_activation_steps") or 0.0),
            float(row.get("trail_floor_steps") or 0.0),
            float(row.get("step_scale") or 1.0),
            float(row.get("entry_start_steps") or 1.0),
            str(row.get("entry_shape") or "uniform"),
        )
        bucket = grouped.setdefault(
            key,
            {
                "anchor_mode": key[0],
                "close_mode": key[1],
                "handoff_steps": key[2],
                "split_depth": key[3],
                "flat_reset_steps": key[4],
                "trail_activation_steps": key[5],
                "trail_floor_steps": key[6],
                "step_scale": key[7],
                "entry_start_steps": key[8],
                "entry_shape": key[9],
                "cases": 0,
                "positive_cases": 0,
                "realized_net_usd": 0.0,
                "realized_usd_per_hour": 0.0,
                "realized_closes": 0,
                "handoff_closes": 0,
                "trail_closes": 0,
                "min_floating_usd_sum": 0.0,
                "min_realized_cover_gap_usd_sum": 0.0,
                "min_combined_equity_delta_usd_sum": 0.0,
                "realized_cover_violation_bars_sum": 0,
                "max_open_total_sum": 0,
                "final_open_count_sum": 0,
                "anchor_resets_sum": 0,
                "cover_safe_cases": 0,
            },
        )
        bucket["cases"] += 1
        if float(row.get("realized_usd_per_hour") or 0.0) > 0.0:
            bucket["positive_cases"] += 1
        bucket["realized_net_usd"] += float(row.get("realized_net_usd") or 0.0)
        bucket["realized_usd_per_hour"] += float(row.get("realized_usd_per_hour") or 0.0)
        bucket["realized_closes"] += int(row.get("realized_closes") or 0)
        bucket["handoff_closes"] += int(row.get("handoff_closes") or 0)
        bucket["trail_closes"] += int(row.get("trail_closes") or 0)
        bucket["min_floating_usd_sum"] += float(row.get("min_floating_usd") or 0.0)
        bucket["min_realized_cover_gap_usd_sum"] += float(row.get("min_realized_cover_gap_usd") or 0.0)
        bucket["min_combined_equity_delta_usd_sum"] += float(row.get("min_combined_equity_delta_usd") or 0.0)
        bucket["realized_cover_violation_bars_sum"] += int(row.get("realized_cover_violation_bars") or 0)
        bucket["max_open_total_sum"] += int(row.get("max_open_total") or 0)
        bucket["final_open_count_sum"] += int(row.get("final_open_count") or 0)
        bucket["anchor_resets_sum"] += int(row.get("anchor_resets") or 0)
        if float(row.get("min_realized_cover_gap_usd") or 0.0) >= 0.0:
            bucket["cover_safe_cases"] += 1
    aggregates = list(grouped.values())
    for row in aggregates:
        row["universal_pass"] = bool(
            int(row.get("cases") or 0) == int(expected_cases)
            and int(row.get("positive_cases") or 0) == int(expected_cases)
        )
        row["cover_safe_pass"] = bool(
            int(row.get("cases") or 0) == int(expected_cases)
            and int(row.get("cover_safe_cases") or 0) == int(expected_cases)
        )
    aggregates.sort(key=lambda row: aggregate_sort_key(row, objective=objective), reverse=True)
    return aggregates


def build_markdown(
    rows: list[dict[str, Any]],
    *,
    days: int,
    tested_cases: list[str],
    aggregate_objective: str = "realized",
) -> str:
    lines = [
        "# Staged Anchor Competition",
        "",
        f"- Days: `{days}`",
        "- Objective: compare staged executable-side ladders under profitable-only close families, sweeping anchor law, handoff depth, trailing retention, and hybrid handoff-plus-trailing variants.",
        f"- Aggregate ranking objective: `{aggregate_objective}`",
        f"- Tested lane/timeframe universe: `{', '.join(tested_cases)}`",
        "- Universal pass rule: a row must finish positive on every tested lane/timeframe case before it can be treated as a universal doctrine candidate.",
        "",
    ]
    if not rows:
        lines.append("- No rows generated.")
        return "\n".join(lines).strip() + "\n"
    aggregate_rows_list = aggregate_rows(
        rows, expected_cases=len(tested_cases), objective=aggregate_objective
    )
    if aggregate_rows_list:
        best = aggregate_rows_list[0]
        universal_rows = [row for row in aggregate_rows_list if row.get("universal_pass")]
        best_universal = universal_rows[0] if universal_rows else None
        cover_sorted_rows = aggregate_rows(
            rows, expected_cases=len(tested_cases), objective="cover_priority"
        )
        cover_universal_rows = [
            row
            for row in cover_sorted_rows
            if row.get("universal_pass")
        ]
        best_cover_universal = cover_universal_rows[0] if cover_universal_rows else None
        lines.extend(
            [
                "## Aggregate Ranking",
                "",
                f"- Highest aggregate row: `{best['anchor_mode']}` / `{best['close_mode']}` / handoff `{best['handoff_steps']}` "
                f"/ step scale `{best['step_scale']}` / entry start `{best['entry_start_steps']}` / entry shape `{best['entry_shape']}` / split depth `{best['split_depth']}` / flat reset `{best['flat_reset_steps']}` / trail act `{best['trail_activation_steps']}` / floor `{best['trail_floor_steps']}` "
                f"-> realized `${round(float(best['realized_usd_per_hour']), 4)}/h`, combined min floating sum `${round(float(best['min_floating_usd_sum']), 2)}`, cover gap sum `${round(float(best['min_realized_cover_gap_usd_sum']), 2)}`, "
                f"final open sum `{best['final_open_count_sum']}` across `{best['cases']}` tested cases, universal pass `{best['universal_pass']}`.",
                "",
            ]
        )
        if best_universal is not None:
            lines.extend(
                [
                    f"- Best universal-pass row: `{best_universal['anchor_mode']}` / `{best_universal['close_mode']}` / handoff `{best_universal['handoff_steps']}` "
                    f"/ step scale `{best_universal['step_scale']}` / entry start `{best_universal['entry_start_steps']}` / entry shape `{best_universal['entry_shape']}` / split depth `{best_universal['split_depth']}` / flat reset `{best_universal['flat_reset_steps']}` / trail act `{best_universal['trail_activation_steps']}` / floor `{best_universal['trail_floor_steps']}` "
                    f"-> realized `${round(float(best_universal['realized_usd_per_hour']), 4)}/h`, min floating sum `${round(float(best_universal['min_floating_usd_sum']), 2)}`, cover gap sum `${round(float(best_universal['min_realized_cover_gap_usd_sum']), 2)}`, "
                    f"final open sum `{best_universal['final_open_count_sum']}`.",
                    "",
                ]
            )
        if best_cover_universal is not None:
            lines.extend(
                [
                    f"- Best cover-priority universal-pass row: `{best_cover_universal['anchor_mode']}` / `{best_cover_universal['close_mode']}` / handoff `{best_cover_universal['handoff_steps']}` "
                    f"/ step scale `{best_cover_universal['step_scale']}` / entry start `{best_cover_universal['entry_start_steps']}` / entry shape `{best_cover_universal['entry_shape']}` / split depth `{best_cover_universal['split_depth']}` / flat reset `{best_cover_universal['flat_reset_steps']}` / trail act `{best_cover_universal['trail_activation_steps']}` / floor `{best_cover_universal['trail_floor_steps']}` "
                    f"-> realized `${round(float(best_cover_universal['realized_usd_per_hour']), 4)}/h`, min floating sum `${round(float(best_cover_universal['min_floating_usd_sum']), 2)}`, cover gap sum `${round(float(best_cover_universal['min_realized_cover_gap_usd_sum']), 2)}`, "
                    f"final open sum `{best_cover_universal['final_open_count_sum']}`, cover safe pass `{best_cover_universal['cover_safe_pass']}`.",
                    "",
                ]
            )
        lines.extend(
            [
                "| Anchor | Close | Handoff | Split Depth | Flat Reset | Step Scale | Entry Start | Entry Shape | Trail Act | Trail Floor | Cases | Positive Cases | Universal Pass | Cover Safe Cases | Cover Safe Pass | Realized $/h | Realized | Min Float Sum | Cover Gap Sum | Max Open Sum | Final Open Sum | Closes | Handoff Closes | Trail Closes |",
                "| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- | ---: | ---: | --- | ---: | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
            ]
        )
        for row in aggregate_rows_list[:10]:
            lines.append(
                f"| `{row['anchor_mode']}` | `{row['close_mode']}` | `{row['handoff_steps']}` | `{row['split_depth']}` | `{row['flat_reset_steps']}` | `{row['step_scale']}` | `{row['entry_start_steps']}` | `{row['entry_shape']}` | `{row['trail_activation_steps']}` | `{row['trail_floor_steps']}` | `{row['cases']}` | "
                f"`{row['positive_cases']}` | `{row['universal_pass']}` | `{row['cover_safe_cases']}` | `{row['cover_safe_pass']}` | `${round(float(row['realized_usd_per_hour']), 4)}` | `${round(float(row['realized_net_usd']), 2)}` | "
                f"`${round(float(row['min_floating_usd_sum']), 2)}` | `${round(float(row['min_realized_cover_gap_usd_sum']), 2)}` | `{row['max_open_total_sum']}` | `{row['final_open_count_sum']}` | "
                f"`{row['realized_closes']}` | `{row['handoff_closes']}` | `{row['trail_closes']}` |"
            )
        lines.append("")
    for symbol in sorted({str(row["symbol"]) for row in rows}):
        symbol_rows = [row for row in rows if str(row["symbol"]) == symbol]
        symbol_rows.sort(key=sort_key, reverse=True)
        best = symbol_rows[0]
        lines.extend(
            [
                f"## {symbol}",
                "",
                f"- Best row: `{best['anchor_mode']}` / `{best['close_mode']}` / handoff `{best['handoff_steps']}` -> `${best['realized_usd_per_hour']}/h`, "
                f"step scale `{best['step_scale']}`, entry start `{best['entry_start_steps']}`, entry shape `{best['entry_shape']}`, split depth `{best['split_depth']}`, flat reset `{best['flat_reset_steps']}`, trail act `{best['trail_activation_steps']}` / floor `{best['trail_floor_steps']}`, "
                f"realized `${best['realized_net_usd']}`, closes `{best['realized_closes']}`, "
                f"min floating `${best['min_floating_usd']}`, cover gap `${best['min_realized_cover_gap_usd']}`, max open `{best['max_open_total']}`.",
                "",
                "| Anchor | Close | Handoff | Split Depth | Flat Reset | Step Scale | Entry Start | Entry Shape | Trail Act | Trail Floor | $/h | Realized | Closes | Handoff Closes | Trail Closes | Avg Close | Min Float | Cover Gap | Cover Viol Bars | Max Open | Final Open | Resets |",
                "| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
            ]
        )
        for row in symbol_rows[:6]:
            lines.append(
                f"| `{row['anchor_mode']}` | `{row['close_mode']}` | `{row['handoff_steps']}` | `{row['split_depth']}` | `{row['flat_reset_steps']}` | `{row['step_scale']}` | `{row['entry_start_steps']}` | `{row['entry_shape']}` | `{row['trail_activation_steps']}` | `{row['trail_floor_steps']}` | `${row['realized_usd_per_hour']}` | "
                f"`${row['realized_net_usd']}` | `{row['realized_closes']}` | `{row['handoff_closes']}` | `{row['trail_closes']}` | `${row['avg_close_usd']}` | "
                f"`${row['min_floating_usd']}` | `${row['min_realized_cover_gap_usd']}` | `{row['realized_cover_violation_bars']}` | `{row['max_open_total']}` | `{row['final_open_count']}` | `{row['anchor_resets']}` |"
            )
        lines.append("")
    return "\n".join(lines).strip() + "\n"


def main() -> None:
    args = parse_args()
    if not mt5.initialize():
        raise RuntimeError(f"MT5 initialize failed: {mt5.last_error()}")
    symbol_filter = {str(symbol).upper() for symbol in (args.symbols or [])} if args.symbols else None
    lane_name_filter = {str(name) for name in (args.lane_names or [])} if args.lane_names else None
    kind_filter = {str(kind) for kind in (args.kinds or [])} if args.kinds else None
    configs = load_step_ladder_configs(
        symbol_filter=symbol_filter,
        lane_name_filter=lane_name_filter,
        kind_filter=kind_filter,
        include_disabled=bool(args.include_disabled),
    )
    rows: list[dict[str, Any]] = []
    symbol_info_cache: dict[str, Any] = {}
    bars_cache: dict[tuple[str, str, int], list[dict[str, Any]]] = {}
    try:
        trail_activation_values = (
            [float(v) for v in args.trail_activation_values]
            if args.trail_activation_values
            else [float(args.trail_activation_steps)]
        )
        trail_floor_values = (
            [float(v) for v in args.trail_floor_values]
            if args.trail_floor_values
            else [float(args.trail_floor_steps)]
        )
        flat_reset_values = (
            [float(v) for v in args.flat_reset_values]
            if args.flat_reset_values
            else [float(args.flat_reset_steps)]
        )
        step_scale_values = (
            [float(v) for v in args.step_scale_values]
            if args.step_scale_values
            else [1.0]
        )
        entry_start_values = (
            [float(v) for v in args.entry_start_values]
            if args.entry_start_values
            else [1.0]
        )
        entry_shapes = [str(v) for v in (args.entry_shapes or ["uniform"])]
        split_depth_values = (
            [int(v) for v in args.split_depth_values]
            if args.split_depth_values
            else [2]
        )
        case_tasks: list[dict[str, Any]] = []
        for cfg in configs:
            info = symbol_info_cache.get(cfg.symbol)
            if info is None:
                info = mt5.symbol_info(cfg.symbol)
                symbol_info_cache[cfg.symbol] = info
            if info is None:
                continue
            bars = load_bars_cached(
                bars_cache,
                symbol=cfg.symbol,
                timeframe_name=cfg.timeframe,
                days=int(args.days),
            )
            if not bars:
                continue
            case_tasks.append(
                {
                    "cfg": cfg,
                    "bars": bars,
                    "spread_px": float(spread_price(info)),
                    "usd_per_price_unit": float(
                        estimate_usd_per_price_unit(
                            symbol=cfg.symbol,
                            symbol_info=info,
                            reference_price=float(bars[-1]["close"]),
                        )
                    ),
                    "step_scale_values": [float(v) for v in step_scale_values],
                    "entry_start_values": [float(v) for v in entry_start_values],
                    "entry_shapes": [str(v) for v in entry_shapes],
                    "anchor_modes": [str(v) for v in args.anchor_modes],
                    "close_modes": [str(v) for v in args.close_modes],
                    "handoff_steps_values": [float(v) for v in args.handoff_steps],
                    "split_depth_values": [int(v) for v in split_depth_values],
                    "flat_reset_values": [float(v) for v in flat_reset_values],
                    "trail_activation_values": [float(v) for v in trail_activation_values],
                    "trail_floor_values": [float(v) for v in trail_floor_values],
                }
            )
        if int(args.workers) > 1 and case_tasks:
            with ProcessPoolExecutor(max_workers=int(args.workers)) as executor:
                for case_rows in executor.map(run_cfg_grid_task, case_tasks):
                    rows.extend(case_rows)
        else:
            for task in case_tasks:
                rows.extend(run_cfg_grid_task(task))
        rows.sort(key=sort_key, reverse=True)
        tested_cases = sorted({lane_case_label(cfg) for cfg in configs})
        output_csv = Path(args.output_csv)
        output_csv.parent.mkdir(parents=True, exist_ok=True)
        with output_csv.open("w", newline="", encoding="utf-8") as handle:
            fieldnames = list(rows[0].keys()) if rows else ["symbol"]
            writer = csv.DictWriter(handle, fieldnames=fieldnames)
            writer.writeheader()
            if rows:
                writer.writerows(rows)
        Path(args.output_md).write_text(
            build_markdown(
                rows,
                days=int(args.days),
                tested_cases=tested_cases,
                aggregate_objective=str(args.aggregate_objective),
            ),
            encoding="utf-8",
        )
    finally:
        mt5.shutdown()


if __name__ == "__main__":
    main()
