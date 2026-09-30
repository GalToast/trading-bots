"""Experimental strategy-lab lane logic."""
from __future__ import annotations
from .config import *  # noqa: F401,F403
from .state import alleyway_state  # noqa: F401
from datetime import datetime
from datetime import timezone
import MetaTrader5 as mt5
import json
import time
from .indicators import calc_atr
from .log import log
from .market_data import get_bars


def build_lane_key(symbol, signal_type, mode, regime):
    return (
        str(symbol or "").upper(),
        str(signal_type or ""),
        str(mode or "").upper(),
        str(regime or "").upper(),
    )

def is_strategy_lab_symbol(symbol):
    return str(symbol or "").upper() in STRATEGY_LAB_SYMBOLS

def is_strategy_lab_lane(symbol, signal_type, mode, regime):
    return build_lane_key(symbol, signal_type, mode, regime) in STRATEGY_LAB_TARGET_LANES

def strategy_lab_pip_size(symbol):
    symbol_text = str(symbol or "").upper()
    if symbol_text.endswith("JPY") or "JPY" in symbol_text:
        return 0.01
    return 0.0001

def _strategy_lab_mean(values):
    if not values:
        return 0.0
    return sum(values) / len(values)

def _strategy_lab_iter_closed_lane_records(path=TRADE_BEHAVIOR_LOG_FILE):
    try:
        with open(path, "r", encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                try:
                    record = json.loads(line)
                except Exception:
                    continue
                if build_lane_key(
                    record.get("symbol"),
                    record.get("entry_signal_type"),
                    record.get("entry_mode"),
                    record.get("regime_at_entry"),
                ) not in STRATEGY_LAB_TARGET_LANES:
                    continue
                lane_id = str(
                    record.get("strategy_lab_lane_id")
                    or record.get("lane_id")
                    or ""
                ).strip()
                if lane_id not in STRATEGY_LAB_OWNER_POOL:
                    continue
                yield lane_id, float(record.get("realized_pnl", 0.0) or 0.0)
    except FileNotFoundError:
        return
    except Exception as exc:
        log(f"  [STRATEGY_LAB_OWNER_LOG_FAIL] {exc}")

def choose_strategy_lab_owner_lane(completed_lane_id=None):
    lane_history = {lane_id: [] for lane_id in STRATEGY_LAB_OWNER_POOL}
    for lane_id, realized in _strategy_lab_iter_closed_lane_records():
        lane_history.setdefault(lane_id, []).append(realized)

    lane_counts = {lane_id: len(values) for lane_id, values in lane_history.items()}
    lane_id = str(completed_lane_id or "").strip()
    bootstrap_pending = [
        candidate for candidate in STRATEGY_LAB_OWNER_POOL
        if lane_counts.get(candidate, 0) < STRATEGY_LAB_OWNER_BOOTSTRAP_MIN_TRADES
    ]
    if bootstrap_pending:
        if lane_id in STRATEGY_LAB_OWNER_POOL:
            current_index = STRATEGY_LAB_OWNER_POOL.index(lane_id)
            for offset in range(1, len(STRATEGY_LAB_OWNER_POOL) + 1):
                candidate = STRATEGY_LAB_OWNER_POOL[
                    (current_index + offset) % len(STRATEGY_LAB_OWNER_POOL)
                ]
                if candidate in bootstrap_pending:
                    reason = (
                        f"bootstrap counts="
                        + ",".join(
                            f"{key}:{lane_counts.get(key, 0)}"
                            for key in STRATEGY_LAB_OWNER_POOL
                        )
                    )
                    return candidate, reason
        candidate = bootstrap_pending[0]
        reason = (
            f"bootstrap counts="
            + ",".join(f"{key}:{lane_counts.get(key, 0)}" for key in STRATEGY_LAB_OWNER_POOL)
        )
        return candidate, reason

    ranked = []
    for candidate in STRATEGY_LAB_OWNER_POOL:
        recent = lane_history.get(candidate, [])[-STRATEGY_LAB_OWNER_WINDOW_TRADES:]
        if len(recent) < STRATEGY_LAB_OWNER_MIN_SAMPLES:
            ranked.append((float("-inf"), len(recent), candidate, recent))
            continue
        ranked.append((_strategy_lab_mean(recent), len(recent), candidate, recent))
    ranked.sort(reverse=True, key=lambda item: (item[0], item[1], item[2]))
    if ranked and ranked[0][0] != float("-inf"):
        best_score, _, best_lane_id, _ = ranked[0]
        reason = (
            f"owner_recent{STRATEGY_LAB_OWNER_WINDOW_TRADES} "
            + ", ".join(
                f"{candidate}:{score:+.3f}"
                for score, _, candidate, _ in ranked
            )
        )
        return best_lane_id, reason

    return DEFAULT_STRATEGY_LAB_LANE_ID, "fallback_default"

def refresh_strategy_lab_owner_lane_on_startup(current_lane_id=None):
    lane_id = str(current_lane_id or "").strip()
    if lane_id not in STRATEGY_LAB_OWNER_POOL:
        lane_id = DEFAULT_STRATEGY_LAB_LANE_ID
    next_lane_id, selection_reason = choose_strategy_lab_owner_lane(lane_id)
    alleyway_state["strategy_lab_active_lane_id"] = next_lane_id
    alleyway_state["strategy_lab_owner_last_reason"] = selection_reason
    return next_lane_id, selection_reason

def is_experiment_allowed_lane(symbol, signal_type, mode, regime):
    lane = build_lane_key(symbol, signal_type, mode, regime)
    if not EXPERIMENT_ONLY_MODE:
        return True
    return lane in EXPERIMENT_ONLY_ALLOWED_LANES

def get_current_strategy_lab_lane_id():
    lane_id = str(
        alleyway_state.get("strategy_lab_active_lane_id", DEFAULT_STRATEGY_LAB_LANE_ID)
        or DEFAULT_STRATEGY_LAB_LANE_ID
    )
    if lane_id not in STRATEGY_LAB_LANES or lane_id not in STRATEGY_LAB_OWNER_POOL:
        lane_id = DEFAULT_STRATEGY_LAB_LANE_ID
    alleyway_state["strategy_lab_active_lane_id"] = lane_id
    return lane_id

def get_current_strategy_lab_lane_config():
    return STRATEGY_LAB_LANES[get_current_strategy_lab_lane_id()]

def get_resolved_strategy_lab_lane_id(symbol, signal_type, mode, regime):
    lane_id = STRATEGY_LAB_LANE_OVERRIDES.get(
        build_lane_key(symbol, signal_type, mode, regime),
    )
    if lane_id in STRATEGY_LAB_LANES:
        return lane_id
    return get_current_strategy_lab_lane_id()

def advance_strategy_lab_lane(completed_lane_id):
    lane_id = str(completed_lane_id or "").strip()
    if lane_id not in STRATEGY_LAB_OWNER_POOL:
        return
    next_lane_id, selection_reason = choose_strategy_lab_owner_lane(lane_id)
    alleyway_state["strategy_lab_active_lane_id"] = next_lane_id
    alleyway_state["strategy_lab_last_completed_lane_id"] = lane_id
    alleyway_state["strategy_lab_lane_rotated_at"] = datetime.now(timezone.utc).isoformat()
    alleyway_state["strategy_lab_owner_last_reason"] = selection_reason
    log(f"  [STRATEGY_LAB_ROTATE] {lane_id} -> {next_lane_id} ({selection_reason})")

def get_active_strategy_lab_lane_config(symbol, signal_type, mode, regime):
    if not is_strategy_lab_lane(symbol, signal_type, mode, regime):
        return None
    return STRATEGY_LAB_LANES[get_resolved_strategy_lab_lane_id(symbol, signal_type, mode, regime)]

def get_strategy_lab_symbol_variants():
    variants = {}
    seen_symbols = set()
    for symbol, signal_type, mode, regime in sorted(STRATEGY_LAB_TARGET_LANES):
        symbol_key = str(symbol or "").upper()
        if symbol_key in seen_symbols:
            continue
        lane_config = get_active_strategy_lab_lane_config(symbol, signal_type, mode, regime)
        if not lane_config:
            continue
        variants[symbol_key] = str(lane_config.get("variant_label", "") or "")
        seen_symbols.add(symbol_key)
    return variants

def get_strategy_lab_trail_floor(pdata, mode, scaled_peak, hold_sec):
    lane_config = get_active_strategy_lab_lane_config(
        pdata.get("symbol"),
        pdata.get("entry_signal_type"),
        mode,
        pdata.get("entry_regime"),
    )
    if not lane_config:
        return None
    scaled_peak = float(scaled_peak or 0.0)
    if scaled_peak <= 0:
        return None
    retain_ratio = None
    tiered_peak_capture = lane_config.get("tiered_peak_capture") or ()
    if tiered_peak_capture:
        for peak_cap, candidate_ratio in tiered_peak_capture:
            if scaled_peak <= float(peak_cap):
                retain_ratio = float(candidate_ratio)
                break
    time_decay_capture = lane_config.get("time_decay_capture") or ()
    if retain_ratio is None and time_decay_capture:
        time_since_first_green = None
        if pdata.get("time_to_first_green_seconds") is not None:
            time_since_first_green = max(
                0.0,
                float(hold_sec or 0.0) - float(pdata.get("time_to_first_green_seconds") or 0.0),
            )
        if time_since_first_green is not None:
            for elapsed_cap, candidate_ratio in time_decay_capture:
                if time_since_first_green <= float(elapsed_cap):
                    retain_ratio = float(candidate_ratio)
                    break
    large_peak_threshold = lane_config.get("large_peak_threshold_usd")
    large_peak_ratio = lane_config.get("large_peak_retain_ratio")
    if (
        retain_ratio is None
        and large_peak_threshold is not None
        and large_peak_ratio is not None
        and scaled_peak >= float(large_peak_threshold)
    ):
        retain_ratio = float(large_peak_ratio)
    if retain_ratio is None and lane_config.get("exit_retain_ratio") is not None:
        retain_ratio = float(lane_config.get("exit_retain_ratio"))
    if retain_ratio is None:
        return None
    min_profit_floor_usd = float(lane_config.get("exit_min_profit_floor_usd", 0.0) or 0.0)
    min_floor = (
        min_profit_floor_usd
        if scaled_peak >= min_profit_floor_usd
        else 0.0
    )
    return max(min_floor, scaled_peak * float(retain_ratio))

def get_strategy_lab_stall_exit_reason(pdata, mode, hold_sec):
    lane_config = get_active_strategy_lab_lane_config(
        pdata.get("symbol"),
        pdata.get("entry_signal_type"),
        mode,
        pdata.get("entry_regime"),
    ) or {}
    if not lane_config:
        return None

    max_hold_seconds = lane_config.get("max_hold_seconds")
    if max_hold_seconds is not None and hold_sec >= float(max_hold_seconds):
        return f"STALL_TIMEOUT ({int(hold_sec)}s, lane={lane_config.get('lane_id', '')})"

    if not lane_config.get("stall_exit_on_nonprogress_close"):
        return None

    first_check = float(lane_config.get("stall_exit_check_after_seconds", 60.0) or 60.0)
    if hold_sec < first_check:
        return None

    bars = get_bars(pdata.get("symbol"), mt5.TIMEFRAME_M1, 4)
    if len(bars) < 3:
        return None

    prev_close = float(bars[-3]["c"])
    last_close = float(bars[-2]["c"])
    direction = str(pdata.get("direction", "") or "").upper()
    progressed = last_close > prev_close if direction == "BUY" else last_close < prev_close
    if progressed:
        return None

    return (
        f"STALL_EXIT ({int(hold_sec)}s, lane={lane_config.get('lane_id', '')}, "
        f"prev_close={prev_close:.3f}, last_close={last_close:.3f})"
    )

def get_strategy_lab_variant_label(symbol, signal_type, mode, regime):
    lane_config = get_active_strategy_lab_lane_config(symbol, signal_type, mode, regime)
    if not lane_config:
        return ""
    return str(lane_config.get("variant_label", "") or "")

def get_strategy_lab_lane_meta(symbol, signal_type, mode, regime):
    lane_config = get_active_strategy_lab_lane_config(symbol, signal_type, mode, regime)
    if not lane_config:
        return {}
    return {
        "lane_id": str(lane_config.get("lane_id", "") or ""),
        "role": str(lane_config.get("role", "") or ""),
        "hypothesis": str(lane_config.get("hypothesis", "") or ""),
        "variant_label": str(lane_config.get("variant_label", "") or ""),
    }

def get_strategy_lab_entry_gate(symbol, signal_type, mode, regime, signal):
    lane_config = get_active_strategy_lab_lane_config(symbol, signal_type, mode, regime)
    if not lane_config:
        return True, "not_lab_lane"

    entry_style = str(lane_config.get("entry_style", "") or "").strip().lower()
    if not entry_style:
        return True, "no_entry_gate"

    direction = str(signal or "").upper()
    if direction not in {"BUY", "SELL"}:
        return False, "no_direction"

    lookback_bars = max(4, int(lane_config.get("lookback_bars", 8) or 8))
    bars = get_bars(symbol, mt5.TIMEFRAME_M1, max(lookback_bars + 20, 40))
    if len(bars) < lookback_bars + 3:
        return False, "insufficient_bars"

    signal_bar = bars[-2]
    prior = bars[-2 - lookback_bars : -2]
    pip_size = strategy_lab_pip_size(symbol)
    prior_high = max(float(bar["h"]) for bar in prior)
    prior_low = min(float(bar["l"]) for bar in prior)

    if entry_style == "confirmed_displacement_recipe":
        atr = calc_atr(prior + [signal_bar], period=14)
        atr_pips = float(atr or 0.0) / pip_size if pip_size > 0 else 0.0
        signal_body_pips = abs(float(signal_bar["c"]) - float(signal_bar["o"])) / pip_size
        required_expansion = float(lane_config.get("min_body_atr_expansion", 0.0) or 0.0)
        signal_break_margin_pips = float(lane_config.get("signal_break_margin_pips", 0.0) or 0.0)
        breakout_margin_ok = (
            float(signal_bar["c"]) >= prior_high + signal_break_margin_pips * pip_size
            if direction == "BUY"
            else float(signal_bar["c"]) <= prior_low - signal_break_margin_pips * pip_size
        )
        if not breakout_margin_ok:
            return False, f"signal_margin<{signal_break_margin_pips:.2f}p"
        if atr_pips <= 0:
            return False, "atr_unavailable"
        if signal_body_pips < required_expansion * atr_pips:
            return False, f"body_atr<{required_expansion:.2f}x"

        confirm_window_bars = max(1, int(lane_config.get("confirm_window_bars", 1) or 1))
        now_ts = time.time()
        confirm_windows = alleyway_state.setdefault("strategy_lab_confirm_windows", {})
        lane_id = get_resolved_strategy_lab_lane_id(symbol, signal_type, mode, regime)
        confirm_key = (
            f"{symbol}|{signal_type}|{mode}|{regime}|{direction}|{lane_id}"
        )
        state = confirm_windows.get(confirm_key)
        if (
            not isinstance(state, dict)
            or int(state.get("signal_bar_time", 0) or 0) != int(signal_bar["t"])
        ):
            state = {
                "signal_bar_time": int(signal_bar["t"]),
                "structure_level": float(prior_high if direction == "BUY" else prior_low),
                "started_at": now_ts,
                "expires_at": now_ts + confirm_window_bars * 60.0,
            }
            confirm_windows[confirm_key] = state
            return False, f"confirm_window_start<{signal_break_margin_pips:.2f}p"

        if now_ts > float(state.get("expires_at", 0.0) or 0.0):
            confirm_windows.pop(confirm_key, None)
            return False, f"confirm_window_expired<{signal_break_margin_pips:.2f}p"

        structure_level = float(state.get("structure_level", prior_high if direction == "BUY" else prior_low))
        for bar in bars:
            if int(bar["t"]) <= int(state.get("signal_bar_time", 0) or 0):
                continue
            if direction == "BUY" and float(bar["c"]) >= structure_level:
                confirm_windows.pop(confirm_key, None)
                return True, entry_style
            if direction == "SELL" and float(bar["c"]) <= structure_level:
                confirm_windows.pop(confirm_key, None)
                return True, entry_style
        return False, f"confirm_hold_wait<{signal_break_margin_pips:.2f}p"

    signal_range_pips = max((float(signal_bar["h"]) - float(signal_bar["l"])) / pip_size, 0.01)
    signal_body_pips = abs(float(signal_bar["c"]) - float(signal_bar["o"])) / pip_size
    signal_body_ratio = signal_body_pips / signal_range_pips
    avg_volume = _strategy_lab_mean([float(bar["v"]) for bar in prior])
    avg_range_pips = _strategy_lab_mean(
        [max((float(bar["h"]) - float(bar["l"])) / pip_size, 0.01) for bar in prior]
    )
    breakout_ok = (
        float(signal_bar["c"]) > prior_high if direction == "BUY"
        else float(signal_bar["c"]) < prior_low
    )
    candle_ok = (
        float(signal_bar["c"]) > float(signal_bar["o"]) if direction == "BUY"
        else float(signal_bar["c"]) < float(signal_bar["o"])
    )
    body_ok = signal_body_pips >= float(lane_config.get("min_body_pips", 0.0) or 0.0)
    ratio_ok = signal_body_ratio >= float(lane_config.get("min_body_ratio", 0.0) or 0.0)
    burst_ratio = float(lane_config.get("volume_burst_ratio", 0.0) or 0.0)
    burst_ok = True if burst_ratio <= 0 or avg_volume <= 0 else float(signal_bar["v"]) >= avg_volume * burst_ratio
    expansion_ratio = float(lane_config.get("min_range_expansion", 0.0) or 0.0)
    expansion_ok = True if expansion_ratio <= 0 or avg_range_pips <= 0 else signal_range_pips >= avg_range_pips * expansion_ratio

    if not breakout_ok:
        return False, "breakout_missing"
    if not candle_ok:
        return False, "candle_not_directional"
    if not body_ok:
        return False, f"body<{float(lane_config.get('min_body_pips', 0.0) or 0.0):.2f}"
    if not ratio_ok:
        return False, f"body_ratio<{float(lane_config.get('min_body_ratio', 0.0) or 0.0):.2f}"
    if not burst_ok:
        return False, f"volume_burst<{burst_ratio:.2f}"
    if not expansion_ok:
        return False, f"range_expand<{expansion_ratio:.2f}"

    if entry_style == "confirmed_displacement":
        confirm_pips = float(lane_config.get("confirm_pips", 0.0) or 0.0)
        confirm_window_bars = max(1, int(lane_config.get("confirm_window_bars", 2) or 2))
        now_ts = time.time()
        confirm_windows = alleyway_state.setdefault("strategy_lab_confirm_windows", {})
        lane_id = get_resolved_strategy_lab_lane_id(symbol, signal_type, mode, regime)
        confirm_key = (
            f"{symbol}|{signal_type}|{mode}|{regime}|{direction}|{lane_id}"
        )
        target_price = (
            float(signal_bar["c"]) + confirm_pips * pip_size
            if direction == "BUY"
            else float(signal_bar["c"]) - confirm_pips * pip_size
        )
        state = confirm_windows.get(confirm_key)
        if (
            not isinstance(state, dict)
            or int(state.get("signal_bar_time", 0) or 0) != int(signal_bar["t"])
        ):
            state = {
                "signal_bar_time": int(signal_bar["t"]),
                "target_price": float(target_price),
                "started_at": now_ts,
                "expires_at": now_ts + confirm_window_bars * 60.0,
            }
            confirm_windows[confirm_key] = state
            return False, f"confirm_window_start<{confirm_pips:.2f}p"

        if now_ts > float(state.get("expires_at", 0.0) or 0.0):
            confirm_windows.pop(confirm_key, None)
            return False, f"confirm_window_expired<{confirm_pips:.2f}p"

        window_target = float(state.get("target_price", target_price) or target_price)
        for bar in bars:
            if int(bar["t"]) <= int(state.get("signal_bar_time", 0) or 0):
                continue
            if direction == "BUY" and float(bar["h"]) >= window_target:
                confirm_windows.pop(confirm_key, None)
                return True, entry_style
            if direction == "SELL" and float(bar["l"]) <= window_target:
                confirm_windows.pop(confirm_key, None)
                return True, entry_style

        tick = mt5.symbol_info_tick(symbol)
        if not tick:
            return False, "no_tick"
        live_price = float(tick.ask if direction == "BUY" else tick.bid)
        confirmed = live_price >= window_target if direction == "BUY" else live_price <= window_target
        if not confirmed:
            return False, f"confirm_wait<{confirm_pips:.2f}p"
        confirm_windows.pop(confirm_key, None)

    return True, entry_style

def get_post_cleanup_raw_shotgun_min_confidence(symbol):
    normalized_symbol = str(symbol or "").upper()
    return float(
        POST_CLEANUP_QUALITY_RAW_SHOTGUN_SYMBOL_MIN_CONFIDENCE.get(
            normalized_symbol,
            POST_CLEANUP_QUALITY_RAW_SHOTGUN_MIN_CONFIDENCE,
        )
    )
