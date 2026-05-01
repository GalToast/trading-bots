#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import traceback
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# Ensure scripts/ directory is on PYTHONPATH so bare imports work
# regardless of cwd (repo root vs scripts/ directory)
_SCRIPTS_DIR = Path(__file__).resolve().parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

import MetaTrader5 as mt5

import live_penetration_lattice_mirror as live_mirror
import mt5_terminal_guard
from penetration_lattice_hybrid_apex import RawConfig
from penetration_lattice_lab_v2 import dynamic_step, load_bars, pip_size_for, spread_price, unit_pnl_usd, vwap_anchor
from penetration_lattice_lab_v3_bounded import Config as BoundedConfig
from penetration_lattice_lab_v3_bounded import recent_range


ROOT = Path(__file__).resolve().parent.parent
DEFAULT_STATE_PATH = ROOT / "reports" / "penetration_lattice_shadow_state.json"
DEFAULT_EVENT_PATH = ROOT / "reports" / "penetration_lattice_shadow_events.jsonl"
DEFAULT_DIRECT_EXEC_STATE_PATH = ROOT / "reports" / "penetration_lattice_live_mirror_state.json"
DEFAULT_DIRECT_EXEC_LOG_PATH = ROOT / "reports" / "penetration_lattice_live_mirror_events.jsonl"


@dataclass
class Ticket:
    direction: str
    entry_price: float
    opened_time: int
    level_idx: int = 0


@dataclass(frozen=True)
class RearmVariant:
    name: str
    min_level_idx: int
    excursion_levels: int
    anticipatory_tokens: int = 10
    anticipatory_steps_above: int = 1
    anticipatory_step_size: float = 50.0


@dataclass
class RearmToken:
    direction: str
    level: float
    level_idx: int
    armed: bool = False
    cooldown_until_time: int = 0


@dataclass
class SymbolEngineState:
    symbol: str
    mode: str
    anchor: float = 0.0
    next_sell_level: float = 0.0
    next_buy_level: float = 0.0
    open_tickets: list[dict[str, Any]] = field(default_factory=list)
    last_bar_time: int = 0
    realized_net_usd: float = 0.0
    realized_closes: int = 0
    breakout_net_usd: float = 0.0
    breakout_flushes: int = 0
    forced_net_usd: float = 0.0
    forced_unwinds: int = 0
    regime_high: float = 0.0
    regime_low: float = 0.0
    cooldown_until_time: int = 0
    lattice_started_time: int = 0
    anchor_resets: int = 0
    max_open_total: int = 0
    rearm_tokens: list[dict[str, Any]] = field(default_factory=list)
    rearm_opens: int = 0
    last_near_miss_reason: str = ""
    last_near_miss_time: int = 0


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def append_jsonl(path: Path, record: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, sort_keys=True) + "\n")


def log_runner_exception(event_path: Path, exc: Exception, *, phase: str) -> None:
    append_jsonl(
        event_path,
        {
            "ts_utc": utc_now_iso(),
            "action": "runner_exception",
            "phase": phase,
            "error_type": type(exc).__name__,
            "error": str(exc),
            "traceback": traceback.format_exc(limit=20),
        },
    )


def normalized_gap(value: int | None, default: int) -> int:
    try:
        out = default if value is None else int(value)
    except Exception:
        out = default
    return max(0, int(out))


def _normalize_close_realism_mode(value: str | None) -> str:
    mode = str(value or "intrabar").strip().lower()
    if mode == "bar_close":
        return "bar_close"
    return "intrabar"


def _normalize_open_realism_mode(value: str | None) -> str:
    mode = str(value or "intrabar").strip().lower()
    if mode in {"broker_touch", "bid_ask"}:
        return "broker_touch"
    return "intrabar"


def _apply_close_realism(direction: str, close_ref: float, bar: dict[str, Any], mode: str) -> float:
    if _normalize_close_realism_mode(mode) != "bar_close":
        return float(close_ref)
    close_px = float(bar["close"])
    if str(direction or "").upper() == "SELL":
        return max(float(close_ref), close_px)
    return min(float(close_ref), close_px)


def _bar_reaches_price_level(
    direction: str,
    level_price: float,
    bar: dict[str, Any],
    *,
    spread_px: float,
    mode: str,
    purpose: str,
) -> bool:
    direction_norm = str(direction or "").upper()
    realism_mode = _normalize_open_realism_mode(mode)
    if direction_norm == "SELL":
        if purpose == "open":
            return float(bar["high"]) >= float(level_price)
        if realism_mode == "broker_touch":
            return (float(bar["low"]) + max(float(spread_px or 0.0), 0.0)) <= float(level_price)
        return float(bar["low"]) <= float(level_price)
    if purpose == "close":
        return float(bar["high"]) >= float(level_price)
    if realism_mode == "broker_touch":
        return (float(bar["low"]) + max(float(spread_px or 0.0), 0.0)) <= float(level_price)
    return float(bar["low"]) <= float(level_price)


REARM_VARIANTS = {
    "rearm_lvl1_exc1": RearmVariant(name="rearm_lvl1_exc1", min_level_idx=1, excursion_levels=1),
    "rearm_lvl2_exc1": RearmVariant(name="rearm_lvl2_exc1", min_level_idx=2, excursion_levels=1),
    "rearm_lvl2_exc2": RearmVariant(name="rearm_lvl2_exc2", min_level_idx=2, excursion_levels=2),
    "rearm_lvl3_exc1": RearmVariant(name="rearm_lvl3_exc1", min_level_idx=3, excursion_levels=1),
    "rearm_lvl3_exc2": RearmVariant(name="rearm_lvl3_exc2", min_level_idx=3, excursion_levels=2),
}


def _make_adapt_cfg():
    return type(
        "Cfg",
        (),
        {
            "adaptive_step_threshold_1": 10,
            "adaptive_step_threshold_2": 20,
            "adaptive_step_multiplier_1": 1.5,
            "adaptive_step_multiplier_2": 2.0,
        },
    )()


def _side_count(tickets: list[Ticket], direction: str) -> int:
    return sum(1 for t in tickets if t.direction == direction)


def _ticket_level_idx(ticket: Ticket, anchor: float, base_step_px: float) -> int:
    if int(ticket.level_idx or 0) > 0:
        return int(ticket.level_idx)
    if base_step_px <= 0:
        return 0
    if ticket.direction == "SELL":
        return max(1, int(round((ticket.entry_price - anchor) / base_step_px)))
    return max(1, int(round((anchor - ticket.entry_price) / base_step_px)))


def _entry_level_idx(direction: str, entry_price: float, anchor: float, base_step_px: float) -> int:
    return _ticket_level_idx(Ticket(direction=direction, entry_price=entry_price, opened_time=0), anchor, base_step_px)


def _same_bar_hurdle_applies(
    *,
    ticket: Ticket,
    bar_time: int,
    pnl: float,
    min_pnl: float,
    shallow_level_cap: int,
    anchor: float,
    base_step_px: float,
) -> bool:
    if min_pnl <= 0.0 or shallow_level_cap <= 0:
        return False
    if int(ticket.opened_time or 0) != int(bar_time):
        return False
    if _ticket_level_idx(ticket, anchor, base_step_px) > int(shallow_level_cap):
        return False
    return pnl < min_pnl


def _update_token_arming(tokens: list[RearmToken], bar: dict[str, Any], base_step_px: float, variant: RearmVariant) -> None:
    for token in tokens:
        if token.armed:
            continue
        if int(bar["time"]) < int(token.cooldown_until_time or 0):
            continue
        if token.direction == "SELL":
            away_trigger = token.level - (variant.excursion_levels * base_step_px)
            if bar["low"] <= away_trigger:
                token.armed = True
        else:
            away_trigger = token.level + (variant.excursion_levels * base_step_px)
            if bar["high"] >= away_trigger:
                token.armed = True


def _check_momentum_gate(bar: dict[str, Any], direction: str, entry_price: float) -> bool:
    if direction == "SELL":
        return float(bar["close"]) < float(entry_price)
    return float(bar["close"]) > float(entry_price)


def price_bar_to_dict(bar: dict[str, Any]) -> dict[str, Any]:
    return {
        "time": int(bar["time"]),
        "open": float(bar["open"]),
        "high": float(bar["high"]),
        "low": float(bar["low"]),
        "close": float(bar["close"]),
        "tick_volume": int(bar["tick_volume"]),
    }


def default_apex_mix() -> dict[str, tuple[str, Any]]:
    return {
        "GBPUSD": ("raw_close2", RawConfig(step_pips=2.0, max_open_per_side=20, close_mode="two_level")),
        "EURUSD": ("raw_close2", RawConfig(step_pips=3.0, max_open_per_side=20, close_mode="two_level")),
        "NZDUSD": ("raw_close2", RawConfig(step_pips=1.5, max_open_per_side=12, close_mode="two_level")),
        "USDJPY": (
            "v3_bounded",
            BoundedConfig(
                step_pips=0.5,
                max_open_per_side=20,
                max_floating_loss_usd=-10.0,
                vwap_lookback=20,
                regime_lookback_bars=60,
                max_range_pips=24.0,
                breakout_buffer_pips=5.0,
                max_lattice_window_bars=240,
                cooldown_bars=60,
            ),
        ),
        "USDCHF": (
            "v3_bounded",
            BoundedConfig(
                step_pips=0.5,
                max_open_per_side=20,
                max_floating_loss_usd=-10.0,
                vwap_lookback=20,
                regime_lookback_bars=60,
                max_range_pips=24.0,
                breakout_buffer_pips=5.0,
                max_lattice_window_bars=240,
                cooldown_bars=60,
            ),
        ),
    }


def load_recent_closed_bars(symbol: str, count: int) -> list[dict[str, Any]]:
    rates = mt5.copy_rates_from_pos(symbol, mt5.TIMEFRAME_M1, 1, count)
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


class BaseEngine:
    def __init__(
        self,
        symbol: str,
        cfg: Any,
        mode: str,
        symbol_info,
        *,
        close_realism_mode: str = "intrabar",
        open_realism_mode: str = "intrabar",
    ) -> None:
        self.symbol = symbol
        self.cfg = cfg
        self.mode = mode
        self.symbol_info = symbol_info
        self.pip_size = pip_size_for(symbol_info)
        self.spread_px = spread_price(symbol_info)
        self.close_realism_mode = _normalize_close_realism_mode(close_realism_mode)
        self.open_realism_mode = _normalize_open_realism_mode(open_realism_mode)
        self.history: list[dict[str, Any]] = []
        self.state = SymbolEngineState(symbol=symbol, mode=mode)

    def snapshot(self) -> dict[str, Any]:
        snap = asdict(self.state)
        snap["open_tickets"] = list(self.state.open_tickets)
        if hasattr(self, "base_step_px"):
            base_step_px = float(getattr(self, "base_step_px", 0.0) or 0.0)
            snap["base_step_px"] = base_step_px
            snap["reconcile_open_max_drift_px"] = max(float(self.spread_px or 0.0) * 2.0, base_step_px * 0.25)
        snap["close_realism_mode"] = self.close_realism_mode
        snap["open_realism_mode"] = self.open_realism_mode
        return snap

    def _record_ticket_event(self, path: Path, bar: dict[str, Any], action: str, **extra: Any) -> None:
        append_jsonl(
            path,
            {
                "ts_utc": utc_now_iso(),
                "bar_time": int(bar["time"]),
                "symbol": self.symbol,
                "mode": self.mode,
                "action": action,
                **extra,
            },
        )

    def _record_near_miss(
        self,
        path: Path | None,
        bar: dict[str, Any],
        reason: str,
        *,
        emit: bool,
        throttle_bars: int = 5,
        **extra: Any,
    ) -> None:
        if not emit or path is None:
            return
        bar_time = int(bar["time"])
        if (
            self.state.last_near_miss_reason == reason
            and self.state.last_near_miss_time > 0
            and bar_time < (self.state.last_near_miss_time + (throttle_bars * 60))
        ):
            return
        self.state.last_near_miss_reason = reason
        self.state.last_near_miss_time = bar_time
        self._record_ticket_event(path, bar, "near_miss", reason=reason, **extra)

    def replay(self, bars: list[dict[str, Any]], event_path: Path | None = None) -> None:
        for bar in bars:
            self.process_bar(bar, event_path=event_path, emit=False)

    def process_bar(self, bar: dict[str, Any], event_path: Path | None = None, emit: bool = True) -> None:
        raise NotImplementedError


class RawClose2Engine(BaseEngine):
    def __init__(
        self,
        symbol: str,
        cfg: RawConfig,
        symbol_info,
        close_alpha: float = 0.0,
        close_realism_mode: str = "intrabar",
        open_realism_mode: str = "intrabar",
    ) -> None:
        super().__init__(
            symbol,
            cfg,
            "raw_close2",
            symbol_info,
            close_realism_mode=close_realism_mode,
            open_realism_mode=open_realism_mode,
        )
        if getattr(cfg, 'step_is_price_units', False):
            self.base_step_px = cfg.step_pips
        else:
            self.base_step_px = cfg.step_pips * self.pip_size
        self.close_alpha = max(0.0, min(1.0, float(close_alpha)))

    def _interpolate_close_ref(self, level_price: float, bar_extreme: float) -> float:
        return level_price + ((bar_extreme - level_price) * self.close_alpha)

    def process_bar(self, bar: dict[str, Any], event_path: Path | None = None, emit: bool = True) -> None:
        self.history.append(price_bar_to_dict(bar))
        if len(self.history) == 1:
            close = float(bar["close"])
            self.state.anchor = close
            self.state.next_sell_level = close + self.base_step_px
            self.state.next_buy_level = close - self.base_step_px
            self.state.last_bar_time = int(bar["time"])
            return

        tickets = [Ticket(**t) for t in self.state.open_tickets]
        open_buy = sum(1 for t in tickets if t.direction == "BUY")
        open_sell = sum(1 for t in tickets if t.direction == "SELL")

        current_sell_step = dynamic_step(self.base_step_px, open_sell, type("Cfg", (), {
            "adaptive_step_threshold_1": 10,
            "adaptive_step_threshold_2": 20,
            "adaptive_step_multiplier_1": 1.5,
            "adaptive_step_multiplier_2": 2.0,
        })())
        current_buy_step = dynamic_step(self.base_step_px, open_buy, type("Cfg", (), {
            "adaptive_step_threshold_1": 10,
            "adaptive_step_threshold_2": 20,
            "adaptive_step_multiplier_1": 1.5,
            "adaptive_step_multiplier_2": 2.0,
        })())

        while _bar_reaches_price_level(
            "SELL",
            self.state.next_sell_level,
            bar,
            spread_px=self.spread_px,
            mode=self.open_realism_mode,
            purpose="open",
        ) and open_sell < self.cfg.max_open_per_side:
            ticket = Ticket(direction="SELL", entry_price=self.state.next_sell_level, opened_time=int(bar["time"]))
            tickets.append(ticket)
            open_sell += 1
            if emit and event_path:
                self._record_ticket_event(event_path, bar, "open_ticket", direction="SELL", entry_price=round(ticket.entry_price, 6))
            current_sell_step = dynamic_step(self.base_step_px, open_sell, type("Cfg", (), {
                "adaptive_step_threshold_1": 10,
                "adaptive_step_threshold_2": 20,
                "adaptive_step_multiplier_1": 1.5,
                "adaptive_step_multiplier_2": 2.0,
            })())
            self.state.next_sell_level += current_sell_step

        while _bar_reaches_price_level(
            "BUY",
            self.state.next_buy_level,
            bar,
            spread_px=self.spread_px,
            mode=self.open_realism_mode,
            purpose="open",
        ) and open_buy < self.cfg.max_open_per_side:
            ticket = Ticket(direction="BUY", entry_price=self.state.next_buy_level, opened_time=int(bar["time"]))
            tickets.append(ticket)
            open_buy += 1
            if emit and event_path:
                self._record_ticket_event(event_path, bar, "open_ticket", direction="BUY", entry_price=round(ticket.entry_price, 6))
            current_buy_step = dynamic_step(self.base_step_px, open_buy, type("Cfg", (), {
                "adaptive_step_threshold_1": 10,
                "adaptive_step_threshold_2": 20,
                "adaptive_step_multiplier_1": 1.5,
                "adaptive_step_multiplier_2": 2.0,
            })())
            self.state.next_buy_level -= current_buy_step

        gap = 1 if self.cfg.close_mode == "one_level" else 2

        sells = sorted((t for t in tickets if t.direction == "SELL"), key=lambda t: t.entry_price, reverse=True)
        while len(sells) > gap and _bar_reaches_price_level(
            "SELL",
            sells[gap].entry_price,
            bar,
            spread_px=self.spread_px,
            mode=self.open_realism_mode,
            purpose="close",
        ):
            outer = sells[0]
            close_ref = self._interpolate_close_ref(sells[gap].entry_price, float(bar["low"]))
            close_ref = _apply_close_realism("SELL", close_ref, bar, self.close_realism_mode)
            pnl = unit_pnl_usd(self.symbol, "SELL", outer.entry_price, close_ref, self.spread_px)
            self.state.realized_net_usd += pnl
            self.state.realized_closes += 1
            tickets.remove(outer)
            if emit and event_path:
                self._record_ticket_event(
                    event_path,
                    bar,
                    "close_ticket",
                    direction="SELL",
                    entry_price=round(outer.entry_price, 6),
                    exit_price=round(close_ref, 6),
                    realized_pnl=round(pnl, 3),
                    close_alpha=self.close_alpha,
                )
            sells = sorted((t for t in tickets if t.direction == "SELL"), key=lambda t: t.entry_price, reverse=True)

        buys = sorted((t for t in tickets if t.direction == "BUY"), key=lambda t: t.entry_price)
        while len(buys) > gap and _bar_reaches_price_level(
            "BUY",
            buys[gap].entry_price,
            bar,
            spread_px=self.spread_px,
            mode=self.open_realism_mode,
            purpose="close",
        ):
            outer = buys[0]
            close_ref = self._interpolate_close_ref(buys[gap].entry_price, float(bar["high"]))
            close_ref = _apply_close_realism("BUY", close_ref, bar, self.close_realism_mode)
            pnl = unit_pnl_usd(self.symbol, "BUY", outer.entry_price, close_ref, self.spread_px)
            self.state.realized_net_usd += pnl
            self.state.realized_closes += 1
            tickets.remove(outer)
            if emit and event_path:
                self._record_ticket_event(
                    event_path,
                    bar,
                    "close_ticket",
                    direction="BUY",
                    entry_price=round(outer.entry_price, 6),
                    exit_price=round(close_ref, 6),
                    realized_pnl=round(pnl, 3),
                    close_alpha=self.close_alpha,
                )
            buys = sorted((t for t in tickets if t.direction == "BUY"), key=lambda t: t.entry_price)

        if not tickets and abs(bar["close"] - self.state.anchor) >= self.base_step_px:
            self.state.anchor = float(bar["close"])
            self.state.next_sell_level = self.state.anchor + self.base_step_px
            self.state.next_buy_level = self.state.anchor - self.base_step_px
            self.state.anchor_resets += 1

        self.state.open_tickets = [asdict(t) for t in tickets]
        self.state.last_bar_time = int(bar["time"])
        self.state.max_open_total = max(self.state.max_open_total, len(tickets))


class StatefulRearmRawEngine(BaseEngine):
    def __init__(
        self,
        symbol: str,
        cfg: RawConfig,
        symbol_info,
        variant: RearmVariant,
        close_alpha: float = 0.0,
        cooldown_bars: int = 0,
        momentum_gate: bool = False,
        sell_gap: int | None = None,
        buy_gap: int | None = None,
        close_realism_mode: str = "intrabar",
        open_realism_mode: str = "intrabar",
    ) -> None:
        super().__init__(
            symbol,
            cfg,
            "raw_stateful_rearm",
            symbol_info,
            close_realism_mode=close_realism_mode,
            open_realism_mode=open_realism_mode,
        )
        if getattr(cfg, 'step_is_price_units', False):
            self.base_step_px = cfg.step_pips
        else:
            self.base_step_px = cfg.step_pips * self.pip_size
        self.variant = variant
        self.adapt_cfg = _make_adapt_cfg()
        self.close_alpha = max(0.0, min(1.0, float(close_alpha)))
        self.cooldown_bars = max(0, int(cooldown_bars))
        self.momentum_gate = bool(momentum_gate)
        self.sell_gap = sell_gap
        self.buy_gap = buy_gap

    def _interpolate_close_ref(self, level_price: float, bar_extreme: float) -> float:
        return level_price + ((bar_extreme - level_price) * self.close_alpha)

    def _consume_rearm_tokens(self, *, tokens: list[RearmToken], bar: dict[str, Any], tickets: list[Ticket], direction: str) -> list[Ticket]:
        # Count rearm positions separately from main lattice positions
        rearm_count = sum(1 for t in tickets if t.direction == direction and getattr(t, 'from_rearm', False))
        opened: list[Ticket] = []
        for token in list(tokens):
            if token.direction != direction or not token.armed:
                continue
            if rearm_count >= self.cfg.max_open_per_side:
                break
            if self.momentum_gate and not _check_momentum_gate(bar, direction, token.level):
                continue
            if _bar_reaches_price_level(
                direction,
                token.level,
                bar,
                spread_px=self.spread_px,
                mode=self.open_realism_mode,
                purpose="open",
            ):
                if direction == "SELL":
                    ticket = Ticket(direction="SELL", entry_price=token.level, opened_time=int(bar["time"]))
                    setattr(ticket, 'from_rearm', True)
                    tickets.append(ticket)
                    tokens.remove(token)
                    rearm_count += 1
                    opened.append(ticket)
                else:
                    ticket = Ticket(direction="BUY", entry_price=token.level, opened_time=int(bar["time"]))
                    setattr(ticket, 'from_rearm', True)
                    tickets.append(ticket)
                    tokens.remove(token)
                    rearm_count += 1
                    opened.append(ticket)
        return opened

    def process_bar(self, bar: dict[str, Any], event_path: Path | None = None, emit: bool = True) -> None:
        self.history.append(price_bar_to_dict(bar))
        if len(self.history) == 1:
            close = float(bar["close"])
            self.state.anchor = close
            self.state.next_sell_level = close + self.base_step_px
            self.state.next_buy_level = close - self.base_step_px
            self.state.last_bar_time = int(bar["time"])
            return

        tickets = [Ticket(**t) for t in self.state.open_tickets]
        rearm_tokens = [RearmToken(**t) for t in self.state.rearm_tokens]

        _update_token_arming(rearm_tokens, bar, self.base_step_px, self.variant)

        # Track main lattice and rearm positions SEPARATELY (each capped at max_open_per_side)
        open_sell_main = sum(1 for t in tickets if t.direction == "SELL" and not getattr(t, 'from_rearm', False))
        open_buy_main = sum(1 for t in tickets if t.direction == "BUY" and not getattr(t, 'from_rearm', False))
        open_sell_rearm = sum(1 for t in tickets if t.direction == "SELL" and getattr(t, 'from_rearm', False))
        open_buy_rearm = sum(1 for t in tickets if t.direction == "BUY" and getattr(t, 'from_rearm', False))

        current_sell_step = dynamic_step(self.base_step_px, open_sell_main, self.adapt_cfg)
        current_buy_step = dynamic_step(self.base_step_px, open_buy_main, self.adapt_cfg)

        while _bar_reaches_price_level(
            "SELL",
            self.state.next_sell_level,
            bar,
            spread_px=self.spread_px,
            mode=self.open_realism_mode,
            purpose="open",
        ) and open_sell_main < self.cfg.max_open_per_side:
            ticket = Ticket(direction="SELL", entry_price=self.state.next_sell_level, opened_time=int(bar["time"]))
            setattr(ticket, 'from_rearm', False)
            tickets.append(ticket)
            open_sell_main += 1
            if emit and event_path:
                self._record_ticket_event(event_path, bar, "open_ticket", direction="SELL", entry_price=round(ticket.entry_price, 6))
            current_sell_step = dynamic_step(self.base_step_px, open_sell_main, self.adapt_cfg)
            self.state.next_sell_level += current_sell_step

        while _bar_reaches_price_level(
            "BUY",
            self.state.next_buy_level,
            bar,
            spread_px=self.spread_px,
            mode=self.open_realism_mode,
            purpose="open",
        ) and open_buy_main < self.cfg.max_open_per_side:
            ticket = Ticket(direction="BUY", entry_price=self.state.next_buy_level, opened_time=int(bar["time"]))
            setattr(ticket, 'from_rearm', False)
            tickets.append(ticket)
            open_buy_main += 1
            if emit and event_path:
                self._record_ticket_event(event_path, bar, "open_ticket", direction="BUY", entry_price=round(ticket.entry_price, 6))
            current_buy_step = dynamic_step(self.base_step_px, open_buy_main, self.adapt_cfg)
            self.state.next_buy_level -= current_buy_step

        rearm_sell_opens = self._consume_rearm_tokens(tokens=rearm_tokens, bar=bar, tickets=tickets, direction="SELL")
        rearm_buy_opens = self._consume_rearm_tokens(tokens=rearm_tokens, bar=bar, tickets=tickets, direction="BUY")
        if emit and event_path:
            for ticket in rearm_sell_opens:
                self._record_ticket_event(
                    event_path,
                    bar,
                    "open_ticket",
                    direction="SELL",
                    entry_price=round(ticket.entry_price, 6),
                    rearm_open=True,
                    rearm_variant=self.variant.name,
                    rearm_cooldown_bars=self.cooldown_bars,
                    rearm_momentum_gate=self.momentum_gate,
                )
            for ticket in rearm_buy_opens:
                self._record_ticket_event(
                    event_path,
                    bar,
                    "open_ticket",
                    direction="BUY",
                    entry_price=round(ticket.entry_price, 6),
                    rearm_open=True,
                    rearm_variant=self.variant.name,
                    rearm_cooldown_bars=self.cooldown_bars,
                    rearm_momentum_gate=self.momentum_gate,
                )
        self.state.rearm_opens += len(rearm_sell_opens) + len(rearm_buy_opens)

        gap = 1 if self.cfg.close_mode == "one_level" else 2
        sell_gap = normalized_gap(self.sell_gap, gap)
        buy_gap = normalized_gap(self.buy_gap, gap)

        sells = sorted((t for t in tickets if t.direction == "SELL"), key=lambda t: t.entry_price, reverse=True)
        while len(sells) > sell_gap and _bar_reaches_price_level(
            "SELL",
            sells[sell_gap].entry_price,
            bar,
            spread_px=self.spread_px,
            mode=self.open_realism_mode,
            purpose="close",
        ):
            outer = sells[0]
            close_ref = self._interpolate_close_ref(sells[sell_gap].entry_price, float(bar["low"]))
            close_ref = _apply_close_realism("SELL", close_ref, bar, self.close_realism_mode)
            pnl = unit_pnl_usd(self.symbol, "SELL", outer.entry_price, close_ref, self.spread_px)
            self.state.realized_net_usd += pnl
            self.state.realized_closes += 1
            tickets.remove(outer)
            # Create rearm tokens from ALL closes (main + rearm-origin)
            level_idx = int(round((outer.entry_price - self.state.anchor) / self.base_step_px))
            if level_idx >= self.variant.min_level_idx:
                rearm_tokens.append(
                    RearmToken(
                        direction="SELL",
                        level=outer.entry_price,
                        level_idx=level_idx,
                        cooldown_until_time=int(bar["time"]) + (self.cooldown_bars * 60),
                    )
                )
            if emit and event_path:
                self._record_ticket_event(
                    event_path,
                    bar,
                    "close_ticket",
                    direction="SELL",
                    entry_price=round(outer.entry_price, 6),
                    exit_price=round(close_ref, 6),
                    realized_pnl=round(pnl, 3),
                    rearm_variant=self.variant.name,
                    close_alpha=self.close_alpha,
                    rearm_cooldown_bars=self.cooldown_bars,
                    rearm_momentum_gate=self.momentum_gate,
                )
            sells = sorted((t for t in tickets if t.direction == "SELL"), key=lambda t: t.entry_price, reverse=True)

        buys = sorted((t for t in tickets if t.direction == "BUY"), key=lambda t: t.entry_price)
        while len(buys) > buy_gap and _bar_reaches_price_level(
            "BUY",
            buys[buy_gap].entry_price,
            bar,
            spread_px=self.spread_px,
            mode=self.open_realism_mode,
            purpose="close",
        ):
            outer = buys[0]
            close_ref = self._interpolate_close_ref(buys[buy_gap].entry_price, float(bar["high"]))
            close_ref = _apply_close_realism("BUY", close_ref, bar, self.close_realism_mode)
            pnl = unit_pnl_usd(self.symbol, "BUY", outer.entry_price, close_ref, self.spread_px)
            self.state.realized_net_usd += pnl
            self.state.realized_closes += 1
            tickets.remove(outer)
            # Create rearm tokens from ALL closes (main + rearm-origin)
            level_idx = int(round((self.state.anchor - outer.entry_price) / self.base_step_px))
            if level_idx >= self.variant.min_level_idx:
                rearm_tokens.append(
                    RearmToken(
                        direction="BUY",
                        level=outer.entry_price,
                        level_idx=level_idx,
                        cooldown_until_time=int(bar["time"]) + (self.cooldown_bars * 60),
                    )
                )
            if emit and event_path:
                self._record_ticket_event(
                    event_path,
                    bar,
                    "close_ticket",
                    direction="BUY",
                    entry_price=round(outer.entry_price, 6),
                    exit_price=round(close_ref, 6),
                    realized_pnl=round(pnl, 3),
                    rearm_variant=self.variant.name,
                    close_alpha=self.close_alpha,
                    rearm_cooldown_bars=self.cooldown_bars,
                    rearm_momentum_gate=self.momentum_gate,
                )
            buys = sorted((t for t in tickets if t.direction == "BUY"), key=lambda t: t.entry_price)

        if not tickets and abs(bar["close"] - self.state.anchor) >= self.base_step_px:
            self.state.anchor = float(bar["close"])
            self.state.next_sell_level = self.state.anchor + self.base_step_px
            self.state.next_buy_level = self.state.anchor - self.base_step_px
            self.state.anchor_resets += 1
            rearm_tokens = []
        elif (
            not tickets
            and len(rearm_sell_opens) == 0
            and len(rearm_buy_opens) == 0
            and open_sell_main == 0
            and open_buy_main == 0
        ):
            armed_tokens = sum(1 for token in rearm_tokens if token.armed)
            self._record_near_miss(
                event_path,
                bar,
                "inside_band_flat",
                emit=emit,
                anchor=round(self.state.anchor, 6),
                next_buy_level=round(self.state.next_buy_level, 6),
                next_sell_level=round(self.state.next_sell_level, 6),
                bar_low=round(float(bar["low"]), 6),
                bar_high=round(float(bar["high"]), 6),
                rearm_tokens=len(rearm_tokens),
                armed_rearm_tokens=armed_tokens,
                rearm_variant=self.variant.name,
                close_alpha=self.close_alpha,
                rearm_cooldown_bars=self.cooldown_bars,
                rearm_momentum_gate=self.momentum_gate,
            )

        self.state.open_tickets = [asdict(t) for t in tickets]
        self.state.rearm_tokens = [asdict(t) for t in rearm_tokens]
        self.state.last_bar_time = int(bar["time"])
        self.state.max_open_total = max(self.state.max_open_total, len(tickets))


class BoundedV3Engine(BaseEngine):
    def __init__(
        self,
        symbol: str,
        cfg: BoundedConfig,
        symbol_info,
        close_gap: int = 1,
        same_bar_min_pnl: float = 0.0,
        same_bar_shallow_level_cap: int = 0,
        close_realism_mode: str = "intrabar",
        open_realism_mode: str = "intrabar",
    ) -> None:
        super().__init__(
            symbol,
            cfg,
            "v3_bounded",
            symbol_info,
            close_realism_mode=close_realism_mode,
            open_realism_mode=open_realism_mode,
        )
        if getattr(cfg, 'step_is_price_units', False):
            self.base_step_px = cfg.step_pips
        else:
            self.base_step_px = cfg.step_pips * self.pip_size
        self.breakout_buffer_px = cfg.breakout_buffer_pips * self.pip_size
        self.close_gap = max(1, int(close_gap))
        self.same_bar_min_pnl = max(0.0, float(same_bar_min_pnl))
        self.same_bar_shallow_level_cap = max(0, int(same_bar_shallow_level_cap))

    def process_bar(self, bar: dict[str, Any], event_path: Path | None = None, emit: bool = True) -> None:
        bar = price_bar_to_dict(bar)
        self.history.append(bar)
        if len(self.history) > 600:
            self.history = self.history[-600:]
        if len(self.history) <= self.cfg.regime_lookback_bars:
            self.state.last_bar_time = int(bar["time"])
            return

        tickets = [Ticket(**t) for t in self.state.open_tickets]

        if not tickets:
            regime_high, regime_low = recent_range(self.history, len(self.history) - 1, self.cfg.regime_lookback_bars)
            regime_width_pips = (regime_high - regime_low) / self.pip_size
            if regime_width_pips > self.cfg.max_range_pips:
                self._record_near_miss(
                    event_path,
                    bar,
                    "blocked_regime_width",
                    emit=emit,
                    regime_width_pips=round(regime_width_pips, 3),
                    max_range_pips=round(self.cfg.max_range_pips, 3),
                    regime_high=round(regime_high, 6),
                    regime_low=round(regime_low, 6),
                )
                self.state.last_bar_time = int(bar["time"])
                return
            if int(bar["time"]) < self.state.cooldown_until_time:
                self._record_near_miss(
                    event_path,
                    bar,
                    "blocked_cooldown",
                    emit=emit,
                    cooldown_until_time=int(self.state.cooldown_until_time),
                    cooldown_remaining_sec=int(self.state.cooldown_until_time) - int(bar["time"]),
                )
                self.state.last_bar_time = int(bar["time"])
                return
            self.state.regime_high = regime_high
            self.state.regime_low = regime_low
            self.state.anchor = vwap_anchor(self.history, len(self.history) - 1, self.cfg.vwap_lookback)
            self.state.next_sell_level = self.state.anchor + self.base_step_px
            self.state.next_buy_level = self.state.anchor - self.base_step_px

        open_buy = sum(1 for t in tickets if t.direction == "BUY")
        open_sell = sum(1 for t in tickets if t.direction == "SELL")
        current_sell_step = dynamic_step(self.base_step_px, open_sell, self.cfg)
        current_buy_step = dynamic_step(self.base_step_px, open_buy, self.cfg)

        while _bar_reaches_price_level(
            "SELL",
            self.state.next_sell_level,
            bar,
            spread_px=self.spread_px,
            mode=self.open_realism_mode,
            purpose="open",
        ) and open_sell < self.cfg.max_open_per_side:
            ticket = Ticket(
                direction="SELL",
                entry_price=self.state.next_sell_level,
                opened_time=int(bar["time"]),
                level_idx=_entry_level_idx("SELL", self.state.next_sell_level, self.state.anchor, self.base_step_px),
            )
            tickets.append(ticket)
            if self.state.lattice_started_time <= 0:
                self.state.lattice_started_time = int(bar["time"])
            open_sell += 1
            if emit and event_path:
                self._record_ticket_event(event_path, bar, "open_ticket", direction="SELL", entry_price=round(ticket.entry_price, 6))
            current_sell_step = dynamic_step(self.base_step_px, open_sell, self.cfg)
            self.state.next_sell_level += current_sell_step

        while _bar_reaches_price_level(
            "BUY",
            self.state.next_buy_level,
            bar,
            spread_px=self.spread_px,
            mode=self.open_realism_mode,
            purpose="open",
        ) and open_buy < self.cfg.max_open_per_side:
            ticket = Ticket(
                direction="BUY",
                entry_price=self.state.next_buy_level,
                opened_time=int(bar["time"]),
                level_idx=_entry_level_idx("BUY", self.state.next_buy_level, self.state.anchor, self.base_step_px),
            )
            tickets.append(ticket)
            if self.state.lattice_started_time <= 0:
                self.state.lattice_started_time = int(bar["time"])
            open_buy += 1
            if emit and event_path:
                self._record_ticket_event(event_path, bar, "open_ticket", direction="BUY", entry_price=round(ticket.entry_price, 6))
            current_buy_step = dynamic_step(self.base_step_px, open_buy, self.cfg)
            self.state.next_buy_level -= current_buy_step

        sells = sorted((t for t in tickets if t.direction == "SELL"), key=lambda t: t.entry_price, reverse=True)
        while len(sells) > self.close_gap and _bar_reaches_price_level(
            "SELL",
            sells[self.close_gap].entry_price,
            bar,
            spread_px=self.spread_px,
            mode=self.open_realism_mode,
            purpose="close",
        ):
            close_ref = _apply_close_realism("SELL", float(bar["low"]), bar, self.close_realism_mode)
            profitable = []
            for ticket in sells:
                pnl = unit_pnl_usd(self.symbol, "SELL", ticket.entry_price, close_ref, self.spread_px)
                if pnl <= 0:
                    continue
                if _same_bar_hurdle_applies(
                    ticket=ticket,
                    bar_time=int(bar["time"]),
                    pnl=pnl,
                    min_pnl=self.same_bar_min_pnl,
                    shallow_level_cap=self.same_bar_shallow_level_cap,
                    anchor=self.state.anchor,
                    base_step_px=self.base_step_px,
                ):
                    continue
                profitable.append(ticket)
            if not profitable:
                break
            for ticket in profitable:
                pnl = unit_pnl_usd(self.symbol, "SELL", ticket.entry_price, close_ref, self.spread_px)
                self.state.realized_net_usd += pnl
                self.state.realized_closes += 1
                tickets.remove(ticket)
                if emit and event_path:
                    self._record_ticket_event(event_path, bar, "close_ticket", direction="SELL", entry_price=round(ticket.entry_price, 6), exit_price=round(close_ref, 6), realized_pnl=round(pnl, 3))
            sells = sorted((t for t in tickets if t.direction == "SELL"), key=lambda t: t.entry_price, reverse=True)

        buys = sorted((t for t in tickets if t.direction == "BUY"), key=lambda t: t.entry_price)
        while len(buys) > self.close_gap and _bar_reaches_price_level(
            "BUY",
            buys[self.close_gap].entry_price,
            bar,
            spread_px=self.spread_px,
            mode=self.open_realism_mode,
            purpose="close",
        ):
            close_ref = _apply_close_realism("BUY", float(bar["high"]), bar, self.close_realism_mode)
            profitable = []
            for ticket in buys:
                pnl = unit_pnl_usd(self.symbol, "BUY", ticket.entry_price, close_ref, self.spread_px)
                if pnl <= 0:
                    continue
                if _same_bar_hurdle_applies(
                    ticket=ticket,
                    bar_time=int(bar["time"]),
                    pnl=pnl,
                    min_pnl=self.same_bar_min_pnl,
                    shallow_level_cap=self.same_bar_shallow_level_cap,
                    anchor=self.state.anchor,
                    base_step_px=self.base_step_px,
                ):
                    continue
                profitable.append(ticket)
            if not profitable:
                break
            for ticket in profitable:
                pnl = unit_pnl_usd(self.symbol, "BUY", ticket.entry_price, close_ref, self.spread_px)
                self.state.realized_net_usd += pnl
                self.state.realized_closes += 1
                tickets.remove(ticket)
                if emit and event_path:
                    self._record_ticket_event(event_path, bar, "close_ticket", direction="BUY", entry_price=round(ticket.entry_price, 6), exit_price=round(close_ref, 6), realized_pnl=round(pnl, 3))
            buys = sorted((t for t in tickets if t.direction == "BUY"), key=lambda t: t.entry_price)

        if tickets:
            floating = [(t, unit_pnl_usd(self.symbol, t.direction, t.entry_price, bar["close"], self.spread_px)) for t in tickets]
            worst_pnl = min(pnl for _, pnl in floating)
            breakout_up = bar["close"] >= self.state.regime_high + self.breakout_buffer_px
            breakout_down = bar["close"] <= self.state.regime_low - self.breakout_buffer_px
            timed_out = (
                self.state.lattice_started_time > 0
                and (int(bar["time"]) - self.state.lattice_started_time) >= (self.cfg.max_lattice_window_bars * 60)
            )

            if worst_pnl <= self.cfg.max_floating_loss_usd:
                total = 0.0
                for ticket, pnl in floating:
                    total += pnl
                    if emit and event_path:
                        self._record_ticket_event(event_path, bar, "forced_unwind", direction=ticket.direction, entry_price=round(ticket.entry_price, 6), realized_pnl=round(pnl, 3))
                self.state.forced_net_usd += total
                self.state.forced_unwinds += len(floating)
                tickets = []
                self.state.cooldown_until_time = int(bar["time"]) + self.cfg.cooldown_bars * 60
                self.state.lattice_started_time = 0

            elif breakout_up or breakout_down or timed_out:
                total = 0.0
                reason = "breakout_kill" if (breakout_up or breakout_down) else "timed_kill"
                for ticket, pnl in floating:
                    total += pnl
                    if emit and event_path:
                        self._record_ticket_event(event_path, bar, reason, direction=ticket.direction, entry_price=round(ticket.entry_price, 6), realized_pnl=round(pnl, 3))
                self.state.breakout_net_usd += total
                self.state.breakout_flushes += len(floating)
                tickets = []
                self.state.cooldown_until_time = int(bar["time"]) + self.cfg.cooldown_bars * 60
                self.state.lattice_started_time = 0

        if not tickets:
            self.state.lattice_started_time = 0
            candidate_anchor = vwap_anchor(self.history, len(self.history) - 1, self.cfg.vwap_lookback)
            if abs(candidate_anchor - self.state.anchor) >= self.base_step_px:
                self.state.anchor = candidate_anchor
                self.state.next_sell_level = self.state.anchor + self.base_step_px
                self.state.next_buy_level = self.state.anchor - self.base_step_px
                self.state.anchor_resets += 1

        self.state.open_tickets = [asdict(t) for t in tickets]
        self.state.last_bar_time = int(bar["time"])
        self.state.max_open_total = max(self.state.max_open_total, len(tickets))


class StatefulRearmBoundedEngine(BaseEngine):
    def __init__(
        self,
        symbol: str,
        cfg: BoundedConfig,
        symbol_info,
        variant: RearmVariant,
        close_gap: int = 1,
        same_bar_min_pnl: float = 0.0,
        same_bar_shallow_level_cap: int = 0,
        close_realism_mode: str = "intrabar",
        open_realism_mode: str = "intrabar",
    ) -> None:
        super().__init__(
            symbol,
            cfg,
            "v3_bounded_rearm",
            symbol_info,
            close_realism_mode=close_realism_mode,
            open_realism_mode=open_realism_mode,
        )
        if getattr(cfg, 'step_is_price_units', False):
            self.base_step_px = cfg.step_pips
        else:
            self.base_step_px = cfg.step_pips * self.pip_size
        self.breakout_buffer_px = cfg.breakout_buffer_pips * self.pip_size
        self.variant = variant
        self.close_gap = max(1, int(close_gap))
        self.same_bar_min_pnl = max(0.0, float(same_bar_min_pnl))
        self.same_bar_shallow_level_cap = max(0, int(same_bar_shallow_level_cap))

    def _consume_rearm_tokens(self, *, tokens: list[RearmToken], bar: dict[str, Any], tickets: list[Ticket], direction: str) -> list[Ticket]:
        open_count = _side_count(tickets, direction)
        opened: list[Ticket] = []
        for token in list(tokens):
            if token.direction != direction or not token.armed:
                continue
            if open_count >= self.cfg.max_open_per_side:
                break
            if _bar_reaches_price_level(
                direction,
                token.level,
                bar,
                spread_px=self.spread_px,
                mode=self.open_realism_mode,
                purpose="open",
            ):
                if direction == "SELL":
                    ticket = Ticket(direction="SELL", entry_price=token.level, opened_time=int(bar["time"]), level_idx=int(token.level_idx or 0))
                    tickets.append(ticket)
                    tokens.remove(token)
                    open_count += 1
                    opened.append(ticket)
                else:
                    ticket = Ticket(direction="BUY", entry_price=token.level, opened_time=int(bar["time"]), level_idx=int(token.level_idx or 0))
                    tickets.append(ticket)
                    tokens.remove(token)
                    open_count += 1
                    opened.append(ticket)
        return opened

    def process_bar(self, bar: dict[str, Any], event_path: Path | None = None, emit: bool = True) -> None:
        bar = price_bar_to_dict(bar)
        self.history.append(bar)
        if len(self.history) > 600:
            self.history = self.history[-600:]
        if len(self.history) <= self.cfg.regime_lookback_bars:
            self.state.last_bar_time = int(bar["time"])
            return

        tickets = [Ticket(**t) for t in self.state.open_tickets]
        rearm_tokens = [RearmToken(**t) for t in self.state.rearm_tokens]
        _update_token_arming(rearm_tokens, bar, self.base_step_px, self.variant)

        if not tickets:
            regime_high, regime_low = recent_range(self.history, len(self.history) - 1, self.cfg.regime_lookback_bars)
            regime_width_pips = (regime_high - regime_low) / self.pip_size
            if regime_width_pips > self.cfg.max_range_pips or int(bar["time"]) < self.state.cooldown_until_time:
                self.state.last_bar_time = int(bar["time"])
                return
            self.state.regime_high = regime_high
            self.state.regime_low = regime_low
            self.state.anchor = vwap_anchor(self.history, len(self.history) - 1, self.cfg.vwap_lookback)
            self.state.next_sell_level = self.state.anchor + self.base_step_px
            self.state.next_buy_level = self.state.anchor - self.base_step_px

        open_buy = _side_count(tickets, "BUY")
        open_sell = _side_count(tickets, "SELL")
        current_sell_step = dynamic_step(self.base_step_px, open_sell, self.cfg)
        current_buy_step = dynamic_step(self.base_step_px, open_buy, self.cfg)

        while bar["high"] >= self.state.next_sell_level and open_sell < self.cfg.max_open_per_side:
            ticket = Ticket(
                direction="SELL",
                entry_price=self.state.next_sell_level,
                opened_time=int(bar["time"]),
                level_idx=_entry_level_idx("SELL", self.state.next_sell_level, self.state.anchor, self.base_step_px),
            )
            tickets.append(ticket)
            if self.state.lattice_started_time <= 0:
                self.state.lattice_started_time = int(bar["time"])
            open_sell += 1
            if emit and event_path:
                self._record_ticket_event(event_path, bar, "open_ticket", direction="SELL", entry_price=round(ticket.entry_price, 6))
            current_sell_step = dynamic_step(self.base_step_px, open_sell, self.cfg)
            self.state.next_sell_level += current_sell_step

        while bar["low"] <= self.state.next_buy_level and open_buy < self.cfg.max_open_per_side:
            ticket = Ticket(
                direction="BUY",
                entry_price=self.state.next_buy_level,
                opened_time=int(bar["time"]),
                level_idx=_entry_level_idx("BUY", self.state.next_buy_level, self.state.anchor, self.base_step_px),
            )
            tickets.append(ticket)
            if self.state.lattice_started_time <= 0:
                self.state.lattice_started_time = int(bar["time"])
            open_buy += 1
            if emit and event_path:
                self._record_ticket_event(event_path, bar, "open_ticket", direction="BUY", entry_price=round(ticket.entry_price, 6))
            current_buy_step = dynamic_step(self.base_step_px, open_buy, self.cfg)
            self.state.next_buy_level -= current_buy_step

        rearm_sell_opens = self._consume_rearm_tokens(tokens=rearm_tokens, bar=bar, tickets=tickets, direction="SELL")
        rearm_buy_opens = self._consume_rearm_tokens(tokens=rearm_tokens, bar=bar, tickets=tickets, direction="BUY")
        if emit and event_path:
            for ticket in rearm_sell_opens:
                self._record_ticket_event(
                    event_path,
                    bar,
                    "open_ticket",
                    direction="SELL",
                    entry_price=round(ticket.entry_price, 6),
                    rearm_open=True,
                    rearm_variant=self.variant.name,
                )
            for ticket in rearm_buy_opens:
                self._record_ticket_event(
                    event_path,
                    bar,
                    "open_ticket",
                    direction="BUY",
                    entry_price=round(ticket.entry_price, 6),
                    rearm_open=True,
                    rearm_variant=self.variant.name,
                )
        self.state.rearm_opens += len(rearm_sell_opens) + len(rearm_buy_opens)

        sells = sorted((t for t in tickets if t.direction == "SELL"), key=lambda t: t.entry_price, reverse=True)
        while len(sells) > self.close_gap and _bar_reaches_price_level(
            "SELL",
            sells[self.close_gap].entry_price,
            bar,
            spread_px=self.spread_px,
            mode=self.open_realism_mode,
            purpose="close",
        ):
            close_ref = _apply_close_realism("SELL", float(bar["low"]), bar, self.close_realism_mode)
            profitable = []
            for ticket in sells:
                pnl = unit_pnl_usd(self.symbol, "SELL", ticket.entry_price, close_ref, self.spread_px)
                if pnl <= 0:
                    continue
                if _same_bar_hurdle_applies(
                    ticket=ticket,
                    bar_time=int(bar["time"]),
                    pnl=pnl,
                    min_pnl=self.same_bar_min_pnl,
                    shallow_level_cap=self.same_bar_shallow_level_cap,
                    anchor=self.state.anchor,
                    base_step_px=self.base_step_px,
                ):
                    continue
                profitable.append(ticket)
            if not profitable:
                break
            for ticket in profitable:
                pnl = unit_pnl_usd(self.symbol, "SELL", ticket.entry_price, close_ref, self.spread_px)
                self.state.realized_net_usd += pnl
                self.state.realized_closes += 1
                tickets.remove(ticket)
                level_idx = _ticket_level_idx(ticket, self.state.anchor, self.base_step_px)
                if level_idx >= self.variant.min_level_idx:
                    rearm_tokens.append(RearmToken(direction="SELL", level=ticket.entry_price, level_idx=level_idx))
                if emit and event_path:
                    self._record_ticket_event(
                        event_path,
                        bar,
                        "close_ticket",
                        direction="SELL",
                        entry_price=round(ticket.entry_price, 6),
                        exit_price=round(close_ref, 6),
                        realized_pnl=round(pnl, 3),
                        rearm_variant=self.variant.name,
                    )
            sells = sorted((t for t in tickets if t.direction == "SELL"), key=lambda t: t.entry_price, reverse=True)

        buys = sorted((t for t in tickets if t.direction == "BUY"), key=lambda t: t.entry_price)
        while len(buys) > self.close_gap and _bar_reaches_price_level(
            "BUY",
            buys[self.close_gap].entry_price,
            bar,
            spread_px=self.spread_px,
            mode=self.open_realism_mode,
            purpose="close",
        ):
            close_ref = _apply_close_realism("BUY", float(bar["high"]), bar, self.close_realism_mode)
            profitable = []
            for ticket in buys:
                pnl = unit_pnl_usd(self.symbol, "BUY", ticket.entry_price, close_ref, self.spread_px)
                if pnl <= 0:
                    continue
                if _same_bar_hurdle_applies(
                    ticket=ticket,
                    bar_time=int(bar["time"]),
                    pnl=pnl,
                    min_pnl=self.same_bar_min_pnl,
                    shallow_level_cap=self.same_bar_shallow_level_cap,
                    anchor=self.state.anchor,
                    base_step_px=self.base_step_px,
                ):
                    continue
                profitable.append(ticket)
            if not profitable:
                break
            for ticket in profitable:
                pnl = unit_pnl_usd(self.symbol, "BUY", ticket.entry_price, close_ref, self.spread_px)
                self.state.realized_net_usd += pnl
                self.state.realized_closes += 1
                tickets.remove(ticket)
                level_idx = _ticket_level_idx(ticket, self.state.anchor, self.base_step_px)
                if level_idx >= self.variant.min_level_idx:
                    rearm_tokens.append(RearmToken(direction="BUY", level=ticket.entry_price, level_idx=level_idx))
                if emit and event_path:
                    self._record_ticket_event(
                        event_path,
                        bar,
                        "close_ticket",
                        direction="BUY",
                        entry_price=round(ticket.entry_price, 6),
                        exit_price=round(close_ref, 6),
                        realized_pnl=round(pnl, 3),
                        rearm_variant=self.variant.name,
                    )
            buys = sorted((t for t in tickets if t.direction == "BUY"), key=lambda t: t.entry_price)

        if tickets:
            floating = [(t, unit_pnl_usd(self.symbol, t.direction, t.entry_price, bar["close"], self.spread_px)) for t in tickets]
            worst_pnl = min(pnl for _, pnl in floating)
            breakout_up = bar["close"] >= self.state.regime_high + self.breakout_buffer_px
            breakout_down = bar["close"] <= self.state.regime_low - self.breakout_buffer_px
            timed_out = (
                self.state.lattice_started_time > 0
                and (int(bar["time"]) - self.state.lattice_started_time) >= (self.cfg.max_lattice_window_bars * 60)
            )

            if worst_pnl <= self.cfg.max_floating_loss_usd:
                total = 0.0
                for ticket, pnl in floating:
                    total += pnl
                    if emit and event_path:
                        self._record_ticket_event(event_path, bar, "forced_unwind", direction=ticket.direction, entry_price=round(ticket.entry_price, 6), realized_pnl=round(pnl, 3))
                self.state.forced_net_usd += total
                self.state.forced_unwinds += len(floating)
                tickets = []
                rearm_tokens = []
                self.state.cooldown_until_time = int(bar["time"]) + self.cfg.cooldown_bars * 60
                self.state.lattice_started_time = 0

            elif breakout_up or breakout_down or timed_out:
                total = 0.0
                reason = "breakout_kill" if (breakout_up or breakout_down) else "timed_kill"
                for ticket, pnl in floating:
                    total += pnl
                    if emit and event_path:
                        self._record_ticket_event(event_path, bar, reason, direction=ticket.direction, entry_price=round(ticket.entry_price, 6), realized_pnl=round(pnl, 3))
                self.state.breakout_net_usd += total
                self.state.breakout_flushes += len(floating)
                tickets = []
                rearm_tokens = []
                self.state.cooldown_until_time = int(bar["time"]) + self.cfg.cooldown_bars * 60
                self.state.lattice_started_time = 0

        if not tickets:
            self.state.lattice_started_time = 0
            candidate_anchor = vwap_anchor(self.history, len(self.history) - 1, self.cfg.vwap_lookback)
            if abs(candidate_anchor - self.state.anchor) >= self.base_step_px:
                self.state.anchor = candidate_anchor
                self.state.next_sell_level = self.state.anchor + self.base_step_px
                self.state.next_buy_level = self.state.anchor - self.base_step_px
                self.state.anchor_resets += 1
                rearm_tokens = []
            elif len(rearm_sell_opens) == 0 and len(rearm_buy_opens) == 0 and open_sell == 0 and open_buy == 0:
                armed_tokens = sum(1 for token in rearm_tokens if token.armed)
                regime_width_pips = (self.state.regime_high - self.state.regime_low) / self.pip_size
                self._record_near_miss(
                    event_path,
                    bar,
                    "inside_band_flat",
                    emit=emit,
                    anchor=round(self.state.anchor, 6),
                    next_buy_level=round(self.state.next_buy_level, 6),
                    next_sell_level=round(self.state.next_sell_level, 6),
                    bar_low=round(float(bar["low"]), 6),
                    bar_high=round(float(bar["high"]), 6),
                    regime_high=round(self.state.regime_high, 6),
                    regime_low=round(self.state.regime_low, 6),
                    regime_width_pips=round(regime_width_pips, 3),
                    rearm_tokens=len(rearm_tokens),
                    armed_rearm_tokens=armed_tokens,
                    rearm_variant=self.variant.name,
                )

        self.state.open_tickets = [asdict(t) for t in tickets]
        self.state.rearm_tokens = [asdict(t) for t in rearm_tokens]
        self.state.last_bar_time = int(bar["time"])
        self.state.max_open_total = max(self.state.max_open_total, len(tickets))


def build_engines(
    raw_close_alpha: float = 0.0,
    symbols: set[str] | None = None,
    raw_rearm_variant: str | None = None,
    bounded_rearm_variant: str | None = None,
    raw_rearm_cooldown_bars: int = 0,
    raw_rearm_momentum_gate: bool = False,
    bounded_close_gap: int = 1,
    bounded_same_bar_min_pnl: float = 0.0,
    bounded_same_bar_shallow_level_cap: int = 0,
    raw_sell_gap: int | None = None,
    raw_buy_gap: int | None = None,
    close_realism_mode: str = "intrabar",
    open_realism_mode: str = "intrabar",
) -> dict[str, BaseEngine]:
    engines: dict[str, BaseEngine] = {}
    rearm_variant = REARM_VARIANTS.get(raw_rearm_variant or "")
    bounded_variant = REARM_VARIANTS.get(bounded_rearm_variant or "")
    for symbol, (mode, cfg) in default_apex_mix().items():
        if symbols and symbol not in symbols:
            continue
        info = mt5.symbol_info(symbol)
        if info is None:
            continue
        if mode == "raw_close2":
            if rearm_variant:
                engines[symbol] = StatefulRearmRawEngine(
                    symbol,
                    cfg,
                    info,
                    variant=rearm_variant,
                    close_alpha=raw_close_alpha,
                    cooldown_bars=raw_rearm_cooldown_bars,
                    momentum_gate=raw_rearm_momentum_gate,
                    sell_gap=raw_sell_gap,
                    buy_gap=raw_buy_gap,
                    close_realism_mode=close_realism_mode,
                    open_realism_mode=open_realism_mode,
                )
            else:
                engines[symbol] = RawClose2Engine(
                    symbol,
                    cfg,
                    info,
                    close_alpha=raw_close_alpha,
                    close_realism_mode=close_realism_mode,
                    open_realism_mode=open_realism_mode,
                )
        else:
            if bounded_variant:
                engines[symbol] = StatefulRearmBoundedEngine(
                    symbol,
                    cfg,
                    info,
                    variant=bounded_variant,
                    close_gap=bounded_close_gap,
                    same_bar_min_pnl=bounded_same_bar_min_pnl,
                    same_bar_shallow_level_cap=bounded_same_bar_shallow_level_cap,
                    close_realism_mode=close_realism_mode,
                    open_realism_mode=open_realism_mode,
                )
            else:
                engines[symbol] = BoundedV3Engine(
                    symbol,
                    cfg,
                    info,
                    close_gap=bounded_close_gap,
                    same_bar_min_pnl=bounded_same_bar_min_pnl,
                    same_bar_shallow_level_cap=bounded_same_bar_shallow_level_cap,
                    close_realism_mode=close_realism_mode,
                    open_realism_mode=open_realism_mode,
                )
    return engines


def prime_engines_fresh(engines: dict[str, BaseEngine], lookback_bars: int = 120) -> None:
    for symbol, engine in engines.items():
        bars = load_recent_closed_bars(symbol, lookback_bars)
        if not bars:
            continue
        engine.history = [price_bar_to_dict(bar) for bar in bars]
        last_bar = engine.history[-1]
        engine.state.last_bar_time = int(last_bar["time"])
        engine.state.open_tickets = []
        engine.state.lattice_started_time = 0
        engine.state.cooldown_until_time = 0
        if engine.mode in ("raw_close2", "raw_stateful_rearm"):
            base_step_px = engine.base_step_px
            engine.state.anchor = float(last_bar["close"])
            engine.state.next_sell_level = engine.state.anchor + base_step_px
            engine.state.next_buy_level = engine.state.anchor - base_step_px
        elif engine.mode in ("v3_bounded", "v3_bounded_rearm"):
            idx = len(engine.history) - 1
            regime_high, regime_low = recent_range(engine.history, idx, engine.cfg.regime_lookback_bars)
            engine.state.regime_high = regime_high
            engine.state.regime_low = regime_low
            engine.state.anchor = vwap_anchor(engine.history, idx, engine.cfg.vwap_lookback)
            engine.state.next_sell_level = engine.state.anchor + engine.base_step_px
            engine.state.next_buy_level = engine.state.anchor - engine.base_step_px


def hydrate_engine_histories(engines: dict[str, BaseEngine], lookback_bars: int = 120) -> None:
    for symbol, engine in engines.items():
        bars = load_recent_closed_bars(symbol, lookback_bars)
        if not bars:
            continue
        engine.history = [price_bar_to_dict(bar) for bar in bars]


def save_state(
    path: Path,
    engines: dict[str, BaseEngine],
    metadata: dict[str, Any] | None = None,
    runner: dict[str, Any] | None = None,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "updated_at": utc_now_iso(),
        "metadata": metadata or {},
        "runner": runner or {},
        "symbols": {symbol: engine.snapshot() for symbol, engine in engines.items()},
    }
    path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")


def load_state(path: Path, engines: dict[str, BaseEngine]) -> None:
    if not path.exists():
        return
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return
    for symbol, snap in (payload.get("symbols") or {}).items():
        if symbol not in engines:
            continue
        state = engines[symbol].state
        for key, value in snap.items():
            if hasattr(state, key):
                setattr(state, key, value)


def bootstrap(
    engines: dict[str, BaseEngine],
    replay_days: int,
    state_path: Path,
    event_path: Path,
    fresh_start: bool,
    metadata: dict[str, Any],
) -> None:
    if state_path.exists():
        load_state(state_path, engines)
        hydrate_engine_histories(engines)
        return
    if fresh_start:
        prime_engines_fresh(engines)
        save_state(state_path, engines, metadata=metadata)
        append_jsonl(event_path, {"ts_utc": utc_now_iso(), "action": "fresh_start_prime", "symbols": sorted(engines.keys()), **metadata})
        return
    for symbol, engine in engines.items():
        bars = load_recent_closed_bars(symbol, 1440 * replay_days)
        engine.replay(bars, event_path=None)
    save_state(state_path, engines, metadata=metadata)
    append_jsonl(event_path, {"ts_utc": utc_now_iso(), "action": "bootstrap_complete", "symbols": sorted(engines.keys()), "replay_days": replay_days, **metadata})


def run_direct_live_exec(
    exec_state: dict[str, Any],
    *,
    source_state_path: Path,
    source_event_path: Path,
    exec_state_path: Path,
    exec_log_path: Path,
    allowed_symbols: set[str],
    live_magic: int,
    attached_live_magics: list[int] | tuple[int, ...] | set[int] | None,
    live_comment_prefix: str,
    live_volume: float,
) -> None:
    if source_event_path.exists():
        # Direct-live execution must mirror the final source snapshot, not the
        # bar-simulated event stream. Advancing the offset keeps old event logs
        # from being replayed if the process restarts.
        exec_state["offset"] = source_event_path.stat().st_size
    live_mirror.reconcile_from_source_state(
        exec_state,
        source_state_path,
        allowed_symbols,
        exec_log_path,
        flatten_tracked_extras=False,
        live_magic=live_magic,
        attached_live_magics=attached_live_magics,
        comment_prefix=live_comment_prefix,
        live_volume=live_volume,
    )
    live_mirror.save_state(exec_state_path, exec_state)


def run_once(
    engines: dict[str, BaseEngine],
    state_path: Path,
    event_path: Path,
    metadata: dict[str, Any],
    direct_exec: dict[str, Any] | None = None,
    runner_status: dict[str, Any] | None = None,
) -> None:
    for symbol, engine in engines.items():
        bars = load_recent_closed_bars(symbol, 5)
        new_bars = [b for b in bars if int(b["time"]) > int(engine.state.last_bar_time or 0)]
        for bar in new_bars:
            engine.process_bar(bar, event_path=event_path, emit=True)
    if runner_status is not None:
        runner_status["heartbeat_at"] = utc_now_iso()
        runner_status["last_successful_run_at"] = runner_status["heartbeat_at"]
        runner_status["consecutive_exceptions"] = 0
    save_state(state_path, engines, metadata=metadata, runner=runner_status)
    if direct_exec:
        run_direct_live_exec(
            direct_exec["state"],
            source_state_path=state_path,
            source_event_path=event_path,
            exec_state_path=direct_exec["state_path"],
            exec_log_path=direct_exec["log_path"],
            allowed_symbols=direct_exec["allowed_symbols"],
            live_magic=direct_exec["live_magic"],
            attached_live_magics=direct_exec.get("attached_live_magics"),
            live_comment_prefix=direct_exec["live_comment_prefix"],
            live_volume=direct_exec["live_volume"],
        )


def main() -> int:
    parser = argparse.ArgumentParser(description="Live shadow runner for the current penetration-lattice apex mix.")
    parser.add_argument("--state-path", default=str(DEFAULT_STATE_PATH))
    parser.add_argument("--event-path", default=str(DEFAULT_EVENT_PATH))
    parser.add_argument("--replay-days", type=int, default=10)
    parser.add_argument("--poll-seconds", type=float, default=5.0)
    parser.add_argument("--fresh-start", action="store_true")
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--raw-close-alpha", type=float, default=0.0)
    parser.add_argument("--raw-rearm-variant", default=None)
    parser.add_argument("--raw-rearm-cooldown-bars", type=int, default=0)
    parser.add_argument("--raw-rearm-momentum-gate", action="store_true")
    parser.add_argument("--raw-sell-gap", type=int, default=None)
    parser.add_argument("--raw-buy-gap", type=int, default=None)
    parser.add_argument("--bounded-rearm-variant", default=None)
    parser.add_argument("--bounded-close-gap", type=int, default=1)
    parser.add_argument("--bounded-same-bar-min-pnl", type=float, default=0.0)
    parser.add_argument("--bounded-same-bar-shallow-level-cap", type=int, default=0)
    parser.add_argument("--symbols", nargs="*", default=None)
    parser.add_argument("--direct-live", action="store_true")
    parser.add_argument("--live-close-realism-mode", choices=["auto", "intrabar", "bar_close"], default="auto")
    parser.add_argument("--live-open-realism-mode", choices=["auto", "intrabar", "broker_touch"], default="auto")
    parser.add_argument("--direct-exec-state-path", default=str(DEFAULT_DIRECT_EXEC_STATE_PATH))
    parser.add_argument("--direct-exec-log-path", default=str(DEFAULT_DIRECT_EXEC_LOG_PATH))
    parser.add_argument("--live-magic", type=int, default=live_mirror.DEFAULT_LIVE_MAGIC)
    parser.add_argument("--attach-broker-magic", action="append", type=int, default=[],
                        help="Additional broker magics this live lane should adopt into its managed inventory.")
    parser.add_argument("--live-comment-prefix", default=live_mirror.DEFAULT_LIVE_COMMENT_PREFIX)
    parser.add_argument("--live-volume", type=float, default=live_mirror.DEFAULT_LIVE_VOLUME)
    args = parser.parse_args()

    mt5_ready, mt5_connection = mt5_terminal_guard.initialize_mt5(
        mt5_module=mt5,
        require_trade_allowed=bool(args.direct_live),
    )
    if not mt5_ready:
        print(mt5_terminal_guard.failure_summary(mt5_connection))
        return 1

    try:
        state_path = Path(args.state_path)
        event_path = Path(args.event_path)
        selected_symbols = {s.upper() for s in args.symbols} if args.symbols else None
        attached_broker_magics = sorted(
            {
                int(magic)
                for magic in list(args.attach_broker_magic or [])
                if int(magic or 0) > 0 and int(magic or 0) != int(args.live_magic)
            }
        )
        close_realism_mode = "bar_close" if args.direct_live and args.live_close_realism_mode == "auto" else _normalize_close_realism_mode(
            None if args.live_close_realism_mode == "auto" else args.live_close_realism_mode
        )
        open_realism_mode = "broker_touch" if args.direct_live and args.live_open_realism_mode == "auto" else _normalize_open_realism_mode(
            None if args.live_open_realism_mode == "auto" else args.live_open_realism_mode
        )
        metadata = {
            "raw_close_alpha": max(0.0, min(1.0, float(args.raw_close_alpha))),
            "raw_rearm_variant": args.raw_rearm_variant or "",
            "raw_rearm_cooldown_bars": max(0, int(args.raw_rearm_cooldown_bars)),
            "raw_rearm_momentum_gate": bool(args.raw_rearm_momentum_gate),
            "bounded_rearm_variant": args.bounded_rearm_variant or "",
            "bounded_close_gap": max(1, int(args.bounded_close_gap)),
            "bounded_same_bar_min_pnl": max(0.0, float(args.bounded_same_bar_min_pnl)),
            "bounded_same_bar_shallow_level_cap": max(0, int(args.bounded_same_bar_shallow_level_cap)),
            "live_close_realism_mode": close_realism_mode,
            "live_open_realism_mode": open_realism_mode,
            "symbols": sorted(selected_symbols) if selected_symbols else sorted(default_apex_mix().keys()),
            "direct_live": bool(args.direct_live),
            "live_magic": int(args.live_magic),
            "attached_broker_magics": attached_broker_magics,
            "live_comment_prefix": str(args.live_comment_prefix),
            "live_volume": float(args.live_volume),
            "mt5_connection": mt5_connection,
        }
        runner_status = {
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
            "mt5_identity_ok": bool(mt5_connection.get("identity_ok")),
            "mt5_terminal_path": str(mt5_connection.get("terminal_path") or ""),
            "mt5_login": int(mt5_connection.get("login") or 0),
            "mt5_server": str(mt5_connection.get("server") or ""),
        }
        engines = build_engines(
            raw_close_alpha=metadata["raw_close_alpha"],
            symbols=selected_symbols,
            raw_rearm_variant=args.raw_rearm_variant,
            bounded_rearm_variant=args.bounded_rearm_variant,
            raw_rearm_cooldown_bars=args.raw_rearm_cooldown_bars,
            raw_rearm_momentum_gate=args.raw_rearm_momentum_gate,
            bounded_close_gap=args.bounded_close_gap,
            bounded_same_bar_min_pnl=args.bounded_same_bar_min_pnl,
            bounded_same_bar_shallow_level_cap=args.bounded_same_bar_shallow_level_cap,
            raw_sell_gap=args.raw_sell_gap,
            raw_buy_gap=args.raw_buy_gap,
            close_realism_mode=close_realism_mode,
            open_realism_mode=open_realism_mode,
        )
        bootstrap(
            engines,
            replay_days=args.replay_days,
            state_path=state_path,
            event_path=event_path,
            fresh_start=args.fresh_start,
            metadata=metadata,
        )
        direct_exec = None
        if args.direct_live:
            exec_state_path = Path(args.direct_exec_state_path)
            exec_log_path = Path(args.direct_exec_log_path)
            direct_exec = {
                "state": live_mirror.load_state(exec_state_path),
                "state_path": exec_state_path,
                "log_path": exec_log_path,
                "allowed_symbols": set(metadata["symbols"]),
                "live_magic": metadata["live_magic"],
                "attached_live_magics": metadata["attached_broker_magics"],
                "live_comment_prefix": metadata["live_comment_prefix"],
                "live_volume": metadata["live_volume"],
            }
        try:
            run_once(
                engines,
                state_path=state_path,
                event_path=event_path,
                metadata=metadata,
                direct_exec=direct_exec,
                runner_status=runner_status,
            )
        except Exception as exc:
            runner_status["consecutive_exceptions"] = int(runner_status.get("consecutive_exceptions", 0) or 0) + 1
            runner_status["last_exception_at"] = utc_now_iso()
            runner_status["last_exception_type"] = type(exc).__name__
            runner_status["last_exception_message"] = str(exc)
            log_runner_exception(event_path, exc, phase="initial_run_once")
        if args.once:
            return 0
        while True:
            time.sleep(max(1.0, args.poll_seconds))
            try:
                run_once(
                    engines,
                    state_path=state_path,
                    event_path=event_path,
                    metadata=metadata,
                    direct_exec=direct_exec,
                    runner_status=runner_status,
                )
            except Exception as exc:
                runner_status["consecutive_exceptions"] = int(runner_status.get("consecutive_exceptions", 0) or 0) + 1
                runner_status["last_exception_at"] = utc_now_iso()
                runner_status["last_exception_type"] = type(exc).__name__
                runner_status["last_exception_message"] = str(exc)
                log_runner_exception(event_path, exc, phase="loop_run_once")
    finally:
        mt5.shutdown()


if __name__ == "__main__":
    raise SystemExit(main())
