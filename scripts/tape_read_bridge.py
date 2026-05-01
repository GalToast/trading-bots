#!/usr/bin/env python3
"""
Tape-read bridge for the adaptive lattice profit-mode classifier.

Reads runner state files and event logs to compute the tape signals
that the profit-mode classifier needs:
  - same_bar_round_trip_rate
  - spread_to_step_ratio
  - spread_to_range_ratio
  - same_bar_open_burst_count
  - same_tick_open_burst_count
  - directional_bias
  - current_atr
  - atr_percentile

Usage:
  python scripts/tape_read_bridge.py --symbol BTCUSD --state-path reports/penetration_lattice_shadow_btcusd_m15_warp_state.json --event-path reports/penetration_lattice_shadow_btcusd_m15_warp_events.jsonl

Output: JSON with all classifier inputs ready for piping to the adaptive controller.
"""
from __future__ import annotations

import argparse
import json
import math
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

try:
    from profit_mode_classifier import classify_profit_mode
except ImportError:
    from scripts.profit_mode_classifier import classify_profit_mode


def load_state(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def load_events(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    events = []
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    events.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    return events


def event_kind(event: dict[str, Any]) -> str:
    return str(event.get("event", event.get("action", "")) or "").strip().lower()


def event_side(event: dict[str, Any]) -> str:
    return str(event.get("side", event.get("direction", "")) or "").strip().lower()


def event_id(event: dict[str, Any]) -> str:
    for key in ("ticket_id", "position_id", "live_ticket"):
        value = event.get(key)
        if value not in (None, "", 0, "0"):
            return str(value)
    return ""


def event_epoch_seconds(event: dict[str, Any]) -> float | None:
    raw_time_msc = event.get("time_msc")
    if raw_time_msc not in (None, ""):
        try:
            return float(raw_time_msc) / 1000.0
        except (TypeError, ValueError):
            pass

    raw_time = event.get("time")
    if raw_time not in (None, ""):
        try:
            return float(raw_time)
        except (TypeError, ValueError):
            pass

    raw_ts = str(event.get("timestamp", event.get("ts_utc", "")) or "").strip()
    if raw_ts:
        try:
            return datetime.fromisoformat(raw_ts).timestamp()
        except (TypeError, ValueError):
            pass
    return None


def quote_points(events: list[dict[str, Any]]) -> list[dict[str, float]]:
    points: list[dict[str, float]] = []
    for event in events:
        bid = event.get("bid")
        ask = event.get("ask")
        try:
            bid_f = float(bid)
            ask_f = float(ask)
        except (TypeError, ValueError):
            continue
        if bid_f <= 0.0 or ask_f <= 0.0 or ask_f < bid_f:
            continue
        ts = event_epoch_seconds(event)
        if ts is None:
            continue
        points.append({"bid": bid_f, "ask": ask_f, "ts": ts, "mid": (bid_f + ask_f) / 2.0})
    return points


def compute_base_step_px(state: dict[str, Any]) -> float | None:
    for key in ("base_step_px", "step", "step_buy", "step_sell"):
        value = state.get(key)
        if value is not None:
            try:
                return float(value)
            except (TypeError, ValueError):
                pass

    symbols = state.get("symbols", {})
    symbol_rows: list[dict[str, Any]] = []
    if isinstance(symbols, dict):
        symbol_rows = [dict(row) for row in symbols.values() if isinstance(row, dict)]
    elif isinstance(symbols, list):
        symbol_rows = [dict(row) for row in symbols if isinstance(row, dict)]

    for symbol_row in symbol_rows:
        for key in ("base_step_px", "base_step_buy_px", "base_step_sell_px"):
            value = symbol_row.get(key)
            if value is not None:
                try:
                    return float(value)
                except (TypeError, ValueError):
                    pass
    metadata = dict(state.get("metadata") or {})
    for key in ("step", "step_buy", "step_sell"):
        value = metadata.get(key)
        if value is not None:
            try:
                return float(value)
            except (TypeError, ValueError):
                pass
    return None


def compute_directional_bias(events: list[dict[str, Any]]) -> float | None:
    """Estimate directional bias from open/close side distribution in recent events."""
    opens = [e for e in events if event_kind(e) == "open_ticket"]
    if len(opens) < 3:
        return None
    # Use last 20 opens for recency
    recent = opens[-20:]
    buy_count = sum(1 for o in recent if event_side(o) == "buy")
    sell_count = len(recent) - buy_count
    if len(recent) == 0:
        return None
    # Bias: 0.0 = balanced, 1.0 = all one side
    return abs(buy_count - sell_count) / len(recent)


def compute_same_bar_round_trip_rate(events: list[dict[str, Any]]) -> float | None:
    """Compute fraction of opens that close in the same bar."""
    opens = [e for e in events if event_kind(e) == "open_ticket"]
    closes = [e for e in events if event_kind(e) in ("close_ticket", "close_partial", "forced_unwind")]
    if not opens:
        return None

    # Build a map of ticket_id -> open timestamp
    open_times: dict[str, float] = {}
    for o in opens:
        tid = event_id(o)
        ts = event_epoch_seconds(o)
        if tid and ts is not None:
            open_times[tid] = ts

    # Count same-bar closes
    same_bar_count = 0
    matched_by_id = 0
    for c in closes:
        tid = event_id(c)
        close_ts = event_epoch_seconds(c)
        open_ts = open_times.get(tid)
        if tid and open_ts is not None and close_ts is not None:
            matched_by_id += 1
            if (close_ts - open_ts) <= 60:
                same_bar_count += 1

    # Fallback for live artifacts that do not persist stable ticket ids.
    if matched_by_id == 0:
        open_rows = sorted(
            [
                {"ts": event_epoch_seconds(e), "side": event_side(e), "matched": False}
                for e in opens
                if event_epoch_seconds(e) is not None
            ],
            key=lambda row: row["ts"],
        )
        close_rows = sorted(
            [
                {"ts": event_epoch_seconds(e), "side": event_side(e)}
                for e in closes
                if event_epoch_seconds(e) is not None
            ],
            key=lambda row: row["ts"],
        )
        for close in close_rows:
            for open_row in open_rows:
                if open_row["matched"]:
                    continue
                if close["ts"] < open_row["ts"]:
                    continue
                if close["ts"] - open_row["ts"] > 60:
                    continue
                if close["side"] and open_row["side"] and close["side"] != open_row["side"]:
                    continue
                open_row["matched"] = True
                same_bar_count += 1
                break

    if not closes:
        return None
    return same_bar_count / len(closes)


def compute_burst_counts(events: list[dict[str, Any]]) -> tuple[int, int]:
    """Compute max same-bar and same-tick burst counts."""
    opens = [e for e in events if event_kind(e) == "open_ticket"]
    if not opens:
        return 0, 0

    # Group opens by bar (timestamp truncated to minute)
    bar_opens: dict[str, int] = defaultdict(int)
    tick_opens: dict[str, int] = defaultdict(int)

    for o in opens:
        ts_epoch = event_epoch_seconds(o)
        if ts_epoch is None:
            continue
        # Same bar: truncate to minute
        dt = datetime.fromtimestamp(ts_epoch, tz=timezone.utc)
        bar_key = dt.strftime("%Y-%m-%dT%H:%M")
        bar_opens[bar_key] += 1
        # Same tick: exact ms/second bucket
        tick_key = str(o.get("time_msc", o.get("timestamp", o.get("ts_utc", ts_epoch))))
        tick_opens[tick_key] += 1

    max_bar_burst = max(bar_opens.values(), default=0)
    max_tick_burst = max(tick_opens.values(), default=0)
    return max_bar_burst, max_tick_burst


def compute_spread_to_step_ratio(state: dict[str, Any]) -> float | None:
    """Estimate spread/step ratio from state if available."""
    # Try direct field
    ratio = state.get("spread_to_step_ratio")
    if ratio is not None:
        try:
            return float(ratio)
        except (TypeError, ValueError):
            pass

    # Derive from symbols dict if present
    symbols = state.get("symbols", {})
    for sym_data in symbols.values():
        ratio = sym_data.get("spread_to_step_ratio")
        if ratio is not None:
            try:
                return float(ratio)
            except (TypeError, ValueError):
                pass

    return None


def compute_spread_to_step_ratio_from_artifacts(state: dict[str, Any], events: list[dict[str, Any]]) -> float | None:
    base_step_px = compute_base_step_px(state)
    if base_step_px is None or base_step_px <= 0.0:
        return None
    points = quote_points(events)
    if not points:
        return None
    latest = points[-1]
    spread_px = latest["ask"] - latest["bid"]
    if spread_px <= 0.0:
        return None
    return spread_px / base_step_px


def compute_spread_to_range_ratio(state: dict[str, Any]) -> float | None:
    """Estimate spread/range ratio from state if available."""
    ratio = state.get("spread_to_range_ratio")
    if ratio is not None:
        try:
            return float(ratio)
        except (TypeError, ValueError):
            pass

    symbols = state.get("symbols", {})
    for sym_data in symbols.values():
        ratio = sym_data.get("spread_to_range_ratio")
        if ratio is not None:
            try:
                return float(ratio)
            except (TypeError, ValueError):
                pass

    return None


def compute_spread_to_range_ratio_from_artifacts(events: list[dict[str, Any]]) -> float | None:
    points = quote_points(events)
    if len(points) < 2:
        return None
    recent = points[-50:]
    latest_spread_px = recent[-1]["ask"] - recent[-1]["bid"]
    mids = [row["mid"] for row in recent]
    price_range = max(mids) - min(mids)
    if latest_spread_px <= 0.0 or price_range <= 0.0:
        return None
    return latest_spread_px / price_range


def compute_current_atr(state: dict[str, Any]) -> float | None:
    """Extract current ATR from state."""
    atr = state.get("current_atr")
    if atr is not None:
        try:
            return float(atr)
        except (TypeError, ValueError):
            pass

    symbols = state.get("symbols", {})
    for sym_data in symbols.values():
        atr = sym_data.get("current_atr", sym_data.get("atr"))
        if atr is not None:
            try:
                return float(atr)
            except (TypeError, ValueError):
                pass

    return None


def compute_realized_evidence(events: list[dict[str, Any]], state: dict[str, Any]) -> dict[str, Any]:
    """Compute realized close evidence from events and state."""
    closes = [e for e in events if event_kind(e) in ("close_ticket", "close_partial", "forced_unwind")]

    realized_net = 0.0
    realized_count = len(closes)

    for c in closes:
        pnl = c.get("realized_pnl", c.get("pnl", 0))
        try:
            realized_net += float(pnl)
        except (TypeError, ValueError):
            pass

    # Also check state for realized totals
    state_realized = state.get("runner_session_trade_realized_usd")
    state_closes = state.get("runner_session_trade_closes")

    if state_closes is None or state_realized is None:
        symbols = state.get("symbols", {})
        symbol_rows: list[dict[str, Any]] = []
        if isinstance(symbols, dict):
            symbol_rows = [dict(row) for row in symbols.values() if isinstance(row, dict)]
        elif isinstance(symbols, list):
            symbol_rows = [dict(row) for row in symbols if isinstance(row, dict)]
        for symbol_row in symbol_rows:
            if state_closes is None and symbol_row.get("realized_closes") is not None:
                state_closes = symbol_row.get("realized_closes")
            if state_realized is None and symbol_row.get("realized_net_usd") is not None:
                state_realized = symbol_row.get("realized_net_usd")
            if state_closes is not None and state_realized is not None:
                break

    # Use state if more complete
    if state_closes is not None:
        try:
            state_closes = int(state_closes)
            if state_closes > realized_count:
                realized_count = state_closes
        except (TypeError, ValueError):
            pass

    if state_realized is not None:
        try:
            state_realized = float(state_realized)
            # Use state net if it's larger (state may include broker-synced closes)
            if abs(state_realized) > abs(realized_net):
                realized_net = state_realized
        except (TypeError, ValueError):
            pass

    return {
        "realized_close_count": realized_count,
        "realized_net_usd": round(realized_net, 2),
        "realized_avg_per_close": round(realized_net / max(realized_count, 1), 2),
    }


def build_tape_read(
    state: dict[str, Any],
    events: list[dict[str, Any]],
    symbol: str,
) -> dict[str, Any]:
    """Build the complete tape read for the profit-mode classifier."""
    directional_bias = compute_directional_bias(events)
    round_trip_rate = compute_same_bar_round_trip_rate(events)
    max_bar_burst, max_tick_burst = compute_burst_counts(events)
    spread_to_step = compute_spread_to_step_ratio(state)
    if spread_to_step is None:
        spread_to_step = compute_spread_to_step_ratio_from_artifacts(state, events)
    spread_to_range = compute_spread_to_range_ratio(state)
    if spread_to_range is None:
        spread_to_range = compute_spread_to_range_ratio_from_artifacts(events)
    current_atr = compute_current_atr(state)
    realized = compute_realized_evidence(events, state)

    # Determine regime from state
    regime = str(state.get("regime", "mixed"))
    first_path_verdict = str(state.get("first_path_verdict", ""))

    # If first_path_verdict is never_green, that overrides everything
    if first_path_verdict == "never_green_toxic_continuation":
        directional_bias = 0.0  # Not relevant for toxic flow

    # Build classifier input
    classifier_input = {
        "symbol": symbol,
        "regime": regime,
        "directional_bias": directional_bias,
        "same_bar_round_trip_rate": round_trip_rate,
        "same_bar_open_burst_count": max_bar_burst,
        "same_tick_open_burst_count": max_tick_burst,
        "spread_to_step_ratio": spread_to_step,
        "spread_to_range_ratio": spread_to_range,
        "current_atr": current_atr,
        "first_path_verdict": first_path_verdict,
        "close_conversion_pressure": realized["realized_close_count"] <= 0 and realized["realized_net_usd"] <= 0,
        "negative_carry_pressure": realized["realized_net_usd"] < 0,
    }

    # Classify profit mode
    mode_result = classify_profit_mode(
        same_bar_round_trip_rate=classifier_input["same_bar_round_trip_rate"],
        spread_to_step_ratio=classifier_input["spread_to_step_ratio"],
        spread_to_range_ratio=classifier_input["spread_to_range_ratio"],
        same_bar_open_burst_count=classifier_input["same_bar_open_burst_count"],
        same_tick_open_burst_count=classifier_input["same_tick_open_burst_count"],
        first_path_verdict=classifier_input["first_path_verdict"],
        directional_bias=classifier_input["directional_bias"],
        regime=classifier_input["regime"],
        close_conversion_pressure=classifier_input["close_conversion_pressure"],
        negative_carry_pressure=classifier_input["negative_carry_pressure"],
        current_atr=classifier_input["current_atr"],
    )

    return {
        "symbol": symbol,
        "regime": regime,
        "tape_signals": {
            "directional_bias": directional_bias,
            "same_bar_round_trip_rate": round_trip_rate,
            "same_bar_open_burst_count": max_bar_burst,
            "same_tick_open_burst_count": max_tick_burst,
            "spread_to_step_ratio": spread_to_step,
            "spread_to_range_ratio": spread_to_range,
            "current_atr": current_atr,
            "first_path_verdict": first_path_verdict,
            "close_conversion_pressure": classifier_input["close_conversion_pressure"],
            "negative_carry_pressure": classifier_input["negative_carry_pressure"],
        },
        "realized_evidence": realized,
        "profit_mode": mode_result.profit_mode,
        "profit_mode_confidence": mode_result.confidence,
        "profit_mode_scores": mode_result.mode_scores,
        "profit_mode_reason": mode_result.reason,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Read tape signals from runner state/events for profit-mode classification.")
    parser.add_argument("--symbol", required=True)
    parser.add_argument("--state-path", required=True)
    parser.add_argument("--event-path", required=False, default="")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    state = load_state(Path(args.state_path))
    events = load_events(Path(args.event_path)) if args.event_path else []

    tape_read = build_tape_read(state, events, args.symbol)
    print(json.dumps(tape_read, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
