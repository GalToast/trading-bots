#!/usr/bin/env python3
from __future__ import annotations

import csv
import json
import warnings
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from live_penetration_lattice_shadow import utc_now_iso


ROOT = Path(__file__).resolve().parent.parent
REPORTS = ROOT / "reports"
HURDLE_PATH = REPORTS / "coinbase_spot_fee_hurdle_board.json"
PULSE_PATH = REPORTS / "coinbase_spot_pulse_board.json"
LIVE_RADAR_PATH = REPORTS / "coinbase_spot_live_radar.json"
DISSONANCE_PATH = REPORTS / "coinbase_spot_dissonance_board.json"
JSON_PATH = REPORTS / "coinbase_spot_machinegun_strategy_board.json"
CSV_PATH = REPORTS / "coinbase_spot_machinegun_strategy_board.csv"
MD_PATH = REPORTS / "coinbase_spot_machinegun_strategy_board.md"
ML_MODEL_PATH = REPORTS / "models" / "coinbase_spot_fee_survival_trade_model.joblib"
FAST_GREEN_MODEL_PATH = REPORTS / "models" / "coinbase_spot_fast_green_model.joblib"
# Tail model: V2 FIXED with temporal features (AUC=0.9944)
TAIL_MODEL_PATH = REPORTS / "models" / "coinbase_spot_high_gross_tail_predictor_v2_fixed.joblib"
# Temporal feature lookup table for V2 model
TEMPORAL_FEATURES_PATH = REPORTS / "coinbase_spot_temporal_features.json"
POCKET_BOARD_PATH = REPORTS / "coinbase_spot_foundry_pocket_board.json"
BUBBLE_CAPTURE_PATH = REPORTS / "coinbase_spot_bubble_capture_simulator.csv"
RADAR_MAX_AGE_SECONDS = 90.0
PULSE_STALE_SECONDS = 900.0
RADAR_FEE_BPS_PER_SIDE = 120.0
RADAR_PROFIT_BUFFER_PCT = 0.75
DISSONANCE_MAX_AGE_SECONDS = 300.0
ML_WATCH_THRESHOLD = 0.98
FAST_GREEN_WATCH_THRESHOLD = 0.95
TAIL_THRESHOLD = 0.90
BUBBLE_CAPTURE_WATCH_MIN_NET_PER_HOUR = 0.25


def load_json(path: Path) -> Any:
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


# Temporal feature lookup table for V2 model
TEMPORAL_FEATURES_PATH = REPORTS / "coinbase_spot_temporal_features.json"
TEMPORAL_LOOKUP = load_json(TEMPORAL_FEATURES_PATH)
LIVE_FOUNDRY_FEATURES_PATH = REPORTS / "coinbase_spot_live_foundry_features.json"
LIVE_FOUNDRY_LOOKUP = load_json(LIVE_FOUNDRY_FEATURES_PATH)


def to_float(value: Any) -> float:
    try:
        return float(value or 0.0)
    except (TypeError, ValueError):
        return 0.0


def load_ml_payload() -> dict[str, Any] | None:
    if not ML_MODEL_PATH.exists():
        return None
    try:
        import joblib

        payload = joblib.load(ML_MODEL_PATH)
    except Exception:
        return None
    return payload if isinstance(payload, dict) and "model" in payload else None


def load_fast_green_payload() -> dict[str, Any] | None:
    if not FAST_GREEN_MODEL_PATH.exists():
        return None
    try:
        import joblib

        payload = joblib.load(FAST_GREEN_MODEL_PATH)
    except Exception:
        return None
    return payload if isinstance(payload, dict) and "model" in payload else None


def load_tail_payload() -> dict[str, Any] | None:
    if not TAIL_MODEL_PATH.exists():
        return None
    try:
        import joblib
        payload = joblib.load(TAIL_MODEL_PATH)
    except Exception:
        return None
    return payload if isinstance(payload, dict) and 'model' in payload else None

def load_pocket_rows() -> dict[str, list[dict[str, Any]]]:
    payload = load_json(POCKET_BOARD_PATH)
    by_product: dict[str, list[dict[str, Any]]] = {}
    for row in payload.get("rows") or []:
        product_id = str(row.get("product_id") or "")
        if product_id:
            by_product.setdefault(product_id, []).append(row)
    for rows in by_product.values():
        rows.sort(key=lambda item: to_float(item.get("pocket_score")), reverse=True)
    return by_product


def load_bubble_capture_rows() -> dict[str, dict[str, Any]]:
    if not BUBBLE_CAPTURE_PATH.exists():
        return {}
    rows: dict[str, dict[str, Any]] = {}
    with BUBBLE_CAPTURE_PATH.open(newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            product_id = str(row.get("product_id") or "")
            if not product_id:
                continue
            if to_float(row.get("net_pct_sum")) <= 0.0:
                continue
            current = rows.get(product_id)
            if current is None or to_float(row.get("net_pct_per_hour")) > to_float(current.get("net_pct_per_hour")):
                rows[product_id] = dict(row)
    return rows


def age_seconds(value: Any) -> float | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return (datetime.now(timezone.utc) - parsed.astimezone(timezone.utc)).total_seconds()


def choose_playbook(row: dict[str, Any]) -> dict[str, Any]:
    state = str(row.get("hurdle_state") or "")
    edge = to_float(row.get("edge_over_hurdle_pct"))
    ret_15 = to_float(row.get("ret_15m_pct"))
    ret_60 = to_float(row.get("ret_60m_pct"))
    ret_4h = to_float(row.get("ret_4h_pct"))
    trail = max(0.25, to_float(row.get("suggested_trail_giveback_pct")))
    if state == "clears_fast_hurdle":
        return {
            "playbook": "fee_hurdle_breakout_trailer",
            "entry_rule": "shadow-buy only after current ask stays above fee hurdle and 15m/60m momentum both stay positive on the next poll",
            "exit_rule": f"trail from local high by {trail:.2f}% and hard-exit if net edge falls back under fee hurdle",
            "reentry_rule": "re-enter only after a pullback and reclaim above the prior local high; no immediate churn rebuy",
            "risk_rule": "one open symbol max; size from reserved test budget, never average down",
        }
    if state == "radar_clears_live_hurdle":
        return {
            "playbook": "radar_live_breakout_trailer",
            "entry_rule": "shadow-buy only while rolling best-bid radar still clears the live fee hurdle across the confirmation window",
            "exit_rule": f"trail from live radar high by {trail:.2f}% and hard-exit if net edge falls back under fee hurdle",
            "reentry_rule": "re-enter only if live radar momentum rebuilds after a pullback; stale radar samples do not count",
            "risk_rule": "experimental radar-origin candidate; no live order permission without shadow proof",
        }
    if state == "clears_hour_hurdle" and ret_15 > 0.0:
        return {
            "playbook": "hot_potato_hour_rotation",
            "entry_rule": "shadow-buy only if this symbol is top-ranked by edge_over_hurdle and improves versus the previous scan",
            "exit_rule": f"rotate out if another symbol's edge beats this one by at least {max(1.0, trail):.2f}% after paying exit+entry fees",
            "reentry_rule": "if rotated out, require this symbol to regain top rank for two scans before re-entry",
            "risk_rule": "capital follows one leader, not a basket; churn tax is part of the trigger",
        }
    if state == "clears_hour_hurdle":
        return {
            "playbook": "slow_burn_profit_bond",
            "entry_rule": "wait for a positive 15m reload while 60m/4h remain above the all-in fee hurdle",
            "exit_rule": f"after profit exceeds fee hurdle, lock a fee-bond stop near entry+roundtrip fees and trail by {trail:.2f}%",
            "reentry_rule": "re-open only from protected profit after a reload candle clears the previous stop zone",
            "risk_rule": "principal stays capped; only realized fee-cleared profit can finance add-ons",
        }
    if state == "pullback_reentry_watch":
        return {
            "playbook": "rubber_band_reload",
            "entry_rule": "do not buy the red candle; wait for 15m return to flip positive while 4h edge still clears hurdle",
            "exit_rule": f"if entry triggers, trail by {trail:.2f}% or exit on failed reclaim within two polls",
            "reentry_rule": "failed reclaim means wait for a lower low plus reclaim, not a market chase",
            "risk_rule": "designed for buying strength after pullback, not catching falling knives",
        }
    return {
        "playbook": "watch_only",
        "entry_rule": "no entry until move clears fee hurdle",
        "exit_rule": "none",
        "reentry_rule": "none",
        "risk_rule": "blocked by fee/spread/route state",
    }


def build_strategy_row(row: dict[str, Any], rank: int) -> dict[str, Any]:
    playbook = choose_playbook(row)
    edge = to_float(row.get("edge_over_hurdle_pct"))
    speed = max(to_float(row.get("ret_15m_pct")), 0.0) * 1.5 + max(to_float(row.get("ret_60m_pct")), 0.0)
    quality = max(edge, 0.0) * 3.0 + speed - (to_float(row.get("spread_bps")) / 25.0)
    return {
        "rank": rank,
        "product_id": str(row.get("product_id") or ""),
        "hurdle_state": str(row.get("hurdle_state") or ""),
        "playbook": playbook["playbook"],
        "machinegun_score": round(quality, 4),
        "edge_over_hurdle_pct": round(edge, 4),
        "ret_15m_pct": round(to_float(row.get("ret_15m_pct")), 4),
        "ret_60m_pct": round(to_float(row.get("ret_60m_pct")), 4),
        "ret_4h_pct": round(to_float(row.get("ret_4h_pct")), 4),
        "spread_bps": round(to_float(row.get("spread_bps")), 4),
        "trail_giveback_pct": round(to_float(row.get("suggested_trail_giveback_pct")), 4),
        "entry_rule": playbook["entry_rule"],
        "exit_rule": playbook["exit_rule"],
        "reentry_rule": playbook["reentry_rule"],
        "risk_rule": playbook["risk_rule"],
        "source": str(row.get("source") or "fee_hurdle_board"),
        "live_radar_signal_state": str(row.get("live_radar_signal_state") or ""),
        "live_radar_best_window_bps": round(to_float(row.get("live_radar_best_window_bps")), 6),
        "live_radar_move_last_bps": round(to_float(row.get("live_radar_move_last_bps")), 6),
        "live_radar_ret_30s_bps": round(to_float(row.get("live_radar_ret_30s_bps")), 6),
        "live_radar_ret_60s_bps": round(to_float(row.get("live_radar_ret_60s_bps")), 6),
        "live_radar_ret_5m_bps": round(to_float(row.get("live_radar_ret_5m_bps")), 6),
        "ml_survival_prob": None,
        "ml_gate_verdict": "not_scored",
        "ml_score_basis": "",
        "ml_feature_completeness": 0.0,
        "fast_green_prob": None,
        "fast_green_verdict": "not_scored",
        "fast_green_label": "",
        "fast_green_score_basis": "",
        "bubble_capture_net_pct_per_hour": None,
        "bubble_capture_avg_net_pct": None,
        "bubble_capture_trades": 0,
        "bubble_capture_win_rate_pct": None,
        "bubble_capture_verdict": "not_scored",
        "bubble_capture_basis": "",
    }


def radar_hurdle_row(row: dict[str, Any]) -> dict[str, Any] | None:
    if str(row.get("live_route_state") or "") != "ready_direct_usd_or_stable":
        return None
    if str(row.get("signal_state") or "") not in {"live_hot", "building"}:
        return None
    spread_bps = to_float(row.get("spread_bps"))
    if spread_bps <= 0.0 or spread_bps > 100.0:
        return None
    fee_round_trip_pct = (2.0 * RADAR_FEE_BPS_PER_SIDE) / 100.0
    spread_pct = spread_bps / 100.0
    hurdle_pct = fee_round_trip_pct + spread_pct + RADAR_PROFIT_BUFFER_PCT
    best_window_bps = max(
        to_float(row.get("move_last_bps")),
        to_float(row.get("ret_30s_bps")),
        to_float(row.get("ret_60s_bps")),
        to_float(row.get("ret_5m_bps")),
        to_float(row.get("ret_15m_bps")),
        to_float(row.get("ret_60m_bps")),
    )
    best_window_pct = best_window_bps / 100.0
    if best_window_pct < hurdle_pct:
        return None
    trail = max(0.25, min(max(best_window_pct * 0.35, 0.35), max(best_window_pct - hurdle_pct, 0.25)))
    short_pct = max(to_float(row.get("move_last_bps")), to_float(row.get("ret_30s_bps")), to_float(row.get("ret_60s_bps"))) / 100.0
    return {
        "source": "live_radar",
        "product_id": str(row.get("product_id") or ""),
        "quote_currency": str(row.get("quote_currency") or ""),
        "live_route_state": str(row.get("live_route_state") or ""),
        "pulse_state": str(row.get("signal_state") or ""),
        "hurdle_state": "radar_clears_live_hurdle",
        "pulse_score": round(to_float(row.get("velocity_score")), 4),
        "ret_15m_pct": round(short_pct, 4),
        "ret_60m_pct": round(best_window_pct, 4),
        "ret_4h_pct": round(best_window_pct, 4),
        "best_move_pct": round(best_window_pct, 4),
        "fee_round_trip_pct": round(fee_round_trip_pct, 4),
        "spread_bps": round(spread_bps, 4),
        "spread_pct": round(spread_pct, 4),
        "profit_buffer_pct": round(RADAR_PROFIT_BUFFER_PCT, 4),
        "all_in_hurdle_pct": round(hurdle_pct, 4),
        "edge_over_hurdle_pct": round(best_window_pct - hurdle_pct, 4),
        "median_range_60m_pct": round(max(best_window_pct * 0.20, 0.25), 4),
        "p90_range_60m_pct": round(max(best_window_pct * 0.45, 0.35), 4),
        "suggested_trail_giveback_pct": round(trail, 4),
        "quote_volume_native": round(to_float(row.get("quote_volume_native")), 4),
        "candles": int(to_float(row.get("samples"))),
        "live_radar_signal_state": str(row.get("signal_state") or ""),
        "live_radar_best_window_bps": round(best_window_bps, 6),
        "live_radar_move_last_bps": round(to_float(row.get("move_last_bps")), 6),
        "live_radar_ret_30s_bps": round(to_float(row.get("ret_30s_bps")), 6),
        "live_radar_ret_60s_bps": round(to_float(row.get("ret_60s_bps")), 6),
        "live_radar_ret_5m_bps": round(to_float(row.get("ret_5m_bps")), 6),
    }


def default_ml_geometries(row: dict[str, Any]) -> list[dict[str, Any]]:
    if str(row.get("hurdle_state") or "") == "radar_clears_live_hurdle":
        return [
            {
                "variant_id": 1,
                "archetype": "bubble_ignition_reclaim",
                "trigger": "live_bid_burst",
                "confirmation": "one_poll_hot",
                "exit": "wide_bubble_trail",
                "sizing": "standard_50",
                "trigger_mode": "impulse",
                "lookback": 1,
                "trigger_bps": 25.0,
                "target_pct": 7.0,
                "stop_pct": 2.5,
                "hold_bars": 12,
            },
            {
                "variant_id": 61,
                "archetype": "bubble_ignition_reclaim",
                "trigger": "live_bid_burst",
                "confirmation": "spread_not_widening",
                "exit": "wide_bubble_trail",
                "sizing": "standard_50",
                "trigger_mode": "impulse",
                "lookback": 1,
                "trigger_bps": 25.0,
                "target_pct": 7.0,
                "stop_pct": 2.5,
                "hold_bars": 12,
            },
        ]
    return [
        {
            "variant_id": 0,
            "archetype": "fee_hurdle_breakout",
            "trigger": "five_min_ignition",
            "confirmation": "two_poll_hold",
            "exit": "tight_fee_paid_trail",
            "sizing": "standard_50",
            "trigger_mode": "impulse",
            "lookback": 1,
            "trigger_bps": 50.0,
            "target_pct": 3.5,
            "stop_pct": 1.2,
            "hold_bars": 6,
        }
    ]


def ml_feature_row(row: dict[str, Any], geometry: dict[str, Any]) -> dict[str, Any]:
    now = datetime.now(timezone.utc)
    product_id = str(row.get("product_id") or "")
    live = LIVE_FOUNDRY_LOOKUP.get(product_id, {})
    
    best_bps = to_float(row.get("live_radar_best_window_bps")) or to_float(row.get("ret_60m_pct")) * 100.0
    move_bps = to_float(row.get("live_radar_move_last_bps")) or to_float(row.get("ret_15m_pct")) * 100.0
    ret_30s = to_float(row.get("live_radar_ret_30s_bps")) or move_bps
    ret_60s = to_float(row.get("live_radar_ret_60s_bps")) or move_bps
    ret_5m = to_float(row.get("live_radar_ret_5m_bps")) or best_bps
    spread = to_float(row.get("spread_bps"))
    short_abs = max(abs(move_bps), abs(ret_30s), abs(ret_60s), 1.0)
    
    return {
        "product_id": product_id,
        "archetype": str(geometry.get("archetype") or ""),
        "trigger": str(geometry.get("trigger") or ""),
        "confirmation": str(geometry.get("confirmation") or ""),
        "exit": str(geometry.get("exit") or ""),
        "sizing": str(geometry.get("sizing") or ""),
        "trigger_mode": str(geometry.get("trigger_mode") or "impulse"),
        "hour_utc": now.hour,
        "lookback": int(to_float(geometry.get("lookback"))),
        "trigger_bps": to_float(geometry.get("trigger_bps")),
        "target_pct": to_float(geometry.get("target_pct")),
        "stop_pct": to_float(geometry.get("stop_pct")),
        "hold_bars": int(to_float(geometry.get("hold_bars"))),
        "spread_bps_proxy": spread,
        "fee_bps_round_trip": (2.0 * RADAR_FEE_BPS_PER_SIDE) + spread,
        "ret_1_bps": live.get("ret_1_bps") if "ret_1_bps" in live else move_bps,
        "ret_3_bps": live.get("ret_3_bps") if "ret_3_bps" in live else ret_30s,
        "ret_6_bps": live.get("ret_6_bps") if "ret_6_bps" in live else ret_60s,
        "ret_12_bps": live.get("ret_12_bps") if "ret_12_bps" in live else ret_5m,
        "range_bps": live.get("range_bps") if "range_bps" in live else max(abs(best_bps), abs(ret_5m), short_abs),
        "body_bps": live.get("body_bps") if "body_bps" in live else move_bps,
        "close_location": live.get("close_location") if "close_location" in live else (0.85 if best_bps > 0.0 else 0.5),
        "volume_mult_12": live.get("volume_mult_12") if "volume_mult_12" in live else 1.0,
        "volatility_12_bps": live.get("volatility_12_bps") if "volatility_12_bps" in live else max(short_abs * 0.33, 1.0),
        "accel_vs_median_abs_12": live.get("accel_vs_median_abs_12") if "accel_vs_median_abs_12" in live else min(max(short_abs / 5.0, 0.0), 50.0),
        "dist_from_12_high_bps": live.get("dist_from_12_high_bps") if "dist_from_12_high_bps" in live else min(0.0, move_bps - best_bps),
        "dist_from_12_low_bps": live.get("dist_from_12_low_bps") if "dist_from_12_low_bps" in live else max(best_bps, move_bps, 0.0),
        "position_in_12_range": live.get("position_in_12_range") if "position_in_12_range" in live else (0.85 if best_bps > 0.0 else 0.5),
        # Temporal features for V2 model — loaded from lookup table
        **_get_temporal_features(product_id),
    }


def _get_temporal_features(product_id: str) -> dict[str, float]:
    """Get temporal features for a product from the lookup table."""
    temporal = TEMPORAL_LOOKUP.get(product_id, {})
    return {
        "tail_hit_rate_5": float(temporal.get("tail_hit_rate_5", 0.0)),
        "time_since_tail": float(temporal.get("time_since_tail", 0.0)),
        "prev_ret_1_bps": float(temporal.get("prev_ret_1_bps", 0.0)),
        "trend_3": float(temporal.get("trend_3", 0.0)),
        "trend_6": float(temporal.get("trend_6", 0.0)),
        "non_tail_streak": float(temporal.get("non_tail_streak", 0.0)),
    }


def apply_ml_scores(rows: list[dict[str, Any]], *, ml_payload: dict[str, Any] | None, pockets: dict[str, list[dict[str, Any]]]) -> dict[str, Any]:
    meta = {
        "model_path": str(ML_MODEL_PATH),
        "available": ml_payload is not None,
        "threshold": ML_WATCH_THRESHOLD,
        "scored_rows": 0,
        "ultra_strict_rows": 0,
        "mode": "watch_only_metadata",
    }
    if not rows or ml_payload is None:
        return meta
    try:
        import pandas as pd
    except Exception:
        meta["available"] = False
        return meta
    model = ml_payload.get("model")
    categorical = list(ml_payload.get("categorical") or [])
    numeric = list(ml_payload.get("numeric") or [])
    for row in rows:
        product_id = str(row.get("product_id") or "")
        geometries = pockets.get(product_id, [])[:8] or default_ml_geometries(row)
        feature_rows = [ml_feature_row(row, geometry) for geometry in geometries]
        frame = pd.DataFrame(feature_rows)
        for column in categorical:
            frame[column] = frame.get(column, "").astype(str)
        for column in numeric:
            if column in frame.columns:
                frame[column] = pd.to_numeric(frame[column], errors="coerce").fillna(0.0)
            else:
                frame[column] = 0.0
        try:
            with warnings.catch_warnings():
                warnings.filterwarnings("ignore", message="X does not have valid feature names")
                probabilities = model.predict_proba(frame[categorical + numeric])[:, 1]
        except Exception:
            continue
        best_idx = int(probabilities.argmax())
        best_prob = float(probabilities[best_idx])
        best_geometry = geometries[best_idx]
        row["ml_survival_prob"] = round(best_prob, 6)
        row["ml_gate_verdict"] = "ultra_strict_watch" if best_prob >= ML_WATCH_THRESHOLD else "watch_only_below_threshold"
        row["ml_score_basis"] = "{trigger}/{confirmation}/{exit}".format(**best_geometry)
        row["ml_feature_completeness"] = 0.72 if str(row.get("source")) == "live_radar" else 0.55
        meta["scored_rows"] += 1
        if best_prob >= ML_WATCH_THRESHOLD:
            meta["ultra_strict_rows"] += 1
    return meta


def apply_fast_green_scores(
    rows: list[dict[str, Any]], *, fast_green_payload: dict[str, Any] | None, pockets: dict[str, list[dict[str, Any]]]
) -> dict[str, Any]:
    meta = {
        "model_path": str(FAST_GREEN_MODEL_PATH),
        "available": fast_green_payload is not None,
        "threshold": FAST_GREEN_WATCH_THRESHOLD,
        "label": "",
        "scored_rows": 0,
        "watch_rows": 0,
        "mode": "watch_only_metadata",
    }
    if not rows or fast_green_payload is None:
        return meta
    try:
        import pandas as pd
    except Exception:
        meta["available"] = False
        return meta
    model = fast_green_payload.get("model")
    categorical = list(fast_green_payload.get("categorical") or [])
    numeric = list(fast_green_payload.get("numeric") or [])
    report = fast_green_payload.get("report") if isinstance(fast_green_payload.get("report"), dict) else {}
    label = str(fast_green_payload.get("label") or report.get("label") or "fast_green")
    meta["label"] = label
    for row in rows:
        product_id = str(row.get("product_id") or "")
        geometries = pockets.get(product_id, [])[:8] or default_ml_geometries(row)
        feature_rows = [ml_feature_row(row, geometry) for geometry in geometries]
        frame = pd.DataFrame(feature_rows)
        for column in categorical:
            frame[column] = frame.get(column, "").astype(str)
        for column in numeric:
            if column in frame.columns:
                frame[column] = pd.to_numeric(frame[column], errors="coerce").fillna(0.0)
            else:
                frame[column] = 0.0
        try:
            with warnings.catch_warnings():
                warnings.filterwarnings("ignore", message="X does not have valid feature names")
                probabilities = model.predict_proba(frame[categorical + numeric])[:, 1]
        except Exception:
            continue
        best_idx = int(probabilities.argmax())
        best_prob = float(probabilities[best_idx])
        best_geometry = geometries[best_idx]
        row["fast_green_prob"] = round(best_prob, 6)
        row["fast_green_verdict"] = "fast_green_watch" if best_prob >= FAST_GREEN_WATCH_THRESHOLD else "below_fast_green_threshold"
        row["fast_green_label"] = label
        row["fast_green_score_basis"] = "{trigger}/{confirmation}/{exit}".format(**best_geometry)
        meta["scored_rows"] += 1
        if best_prob >= FAST_GREEN_WATCH_THRESHOLD:
            meta["watch_rows"] += 1
    return meta


def apply_tail_scores(
    rows: list[dict[str, Any]],
    *,
    tail_payload: dict[str, Any] | None,
    pockets: dict[str, list[dict[str, Any]]],
) -> dict[str, Any]:
    meta = {
        "model_path": str(TAIL_MODEL_PATH),
        "available": tail_payload is not None,
        "threshold": TAIL_THRESHOLD,
        "scored_rows": 0,
        "watch_rows": 0,
        "mode": "watch_only_metadata",
    }
    if not rows or tail_payload is None:
        return meta
    try:
        import pandas as pd
    except Exception:
        meta["available"] = False
        return meta
    model = tail_payload.get("model")
    encoders = tail_payload.get("encoders", {})
    
    # Use truth from the model object itself if possible
    if hasattr(model, "feature_names_in_"):
        feature_cols = list(model.feature_names_in_)
    else:
        # Fallback to metadata
        feature_cols = list(tail_payload.get("feature_cols") or tail_payload.get("categorical_cols") or [])
        if not feature_cols:
             feature_cols = list(tail_payload.get("categorical") or []) + list(tail_payload.get("numeric") or [])

    for row in rows:
        product_id = str(row.get("product_id") or "")
        geometries = pockets.get(product_id, [])[:8] or default_ml_geometries(row)
        feature_rows = [ml_feature_row(row, geometry) for geometry in geometries]
        frame = pd.DataFrame(feature_rows)
        
        # Preprocess features according to the specific model's requirements
        for col in feature_cols:
            if col in encoders:
                # Use the saved LabelEncoder
                le = encoders[col]
                try:
                    frame[col] = le.transform(frame[col].astype(str).fillna("unknown"))
                except Exception:
                    frame[col] = 0
            elif col in tail_payload.get("categorical", []) or col in tail_payload.get("categorical_cols", []):
                frame[col] = frame[col].astype(str).fillna("")
            else:
                if col in frame.columns:
                    frame[col] = pd.to_numeric(frame[col], errors="coerce").fillna(0.0)
                else:
                    frame[col] = 0.0
                    
        try:
            with warnings.catch_warnings():
                warnings.filterwarnings("ignore", message="X does not have valid feature names")
                probabilities = model.predict_proba(frame[feature_cols])[:, 1]
        except Exception:
            continue
            
        best_idx = int(probabilities.argmax())
        best_prob = float(probabilities[best_idx])
        best_geometry = geometries[best_idx]
        row["tail_prob"] = round(best_prob, 6)
        row["tail_verdict"] = "tail_watch" if best_prob >= TAIL_THRESHOLD else "below_tail_threshold"
        row["tail_score_basis"] = "{trigger}/{confirmation}/{exit}".format(**best_geometry)
        
        # Intersection Scorer (Alpha Moon Horizon)
        if row.get("fast_green_prob") is not None:
             row["combined_ml_score"] = round(row["tail_prob"] * row["fast_green_prob"], 6)
        
        meta["scored_rows"] += 1
        if best_prob >= TAIL_THRESHOLD:
            meta["watch_rows"] += 1
    return meta

def apply_bubble_capture_scores(rows: list[dict[str, Any]], bubble_rows: dict[str, dict[str, Any]]) -> dict[str, Any]:
    meta = {
        "path": str(BUBBLE_CAPTURE_PATH),
        "available": bool(bubble_rows),
        "threshold_net_pct_per_hour": BUBBLE_CAPTURE_WATCH_MIN_NET_PER_HOUR,
        "scored_rows": 0,
        "watch_rows": 0,
        "mode": "watch_only_metadata",
    }
    if not rows or not bubble_rows:
        return meta
    for row in rows:
        product_id = str(row.get("product_id") or "")
        bubble = bubble_rows.get(product_id)
        if not bubble:
            continue
        net_per_hour = to_float(bubble.get("net_pct_per_hour"))
        row["bubble_capture_net_pct_per_hour"] = round(net_per_hour, 6)
        row["bubble_capture_avg_net_pct"] = round(to_float(bubble.get("avg_net_pct")), 6)
        row["bubble_capture_trades"] = int(to_float(bubble.get("trades")))
        row["bubble_capture_win_rate_pct"] = round(to_float(bubble.get("win_rate_pct")), 6)
        row["bubble_capture_verdict"] = (
            "bubble_capture_watch" if net_per_hour >= BUBBLE_CAPTURE_WATCH_MIN_NET_PER_HOUR else "below_bubble_capture_threshold"
        )
        row["bubble_capture_basis"] = "trigger={trigger_pct}%/{trigger_minutes}m activation={activation_pct}% trail={trail_pct}% hold={max_hold_minutes}m".format(
            **bubble
        )
        if row["bubble_capture_verdict"] == "bubble_capture_watch":
            row["trail_giveback_pct"] = max(0.25, round(to_float(bubble.get("trail_pct")), 4))
            row["playbook"] = "bubble_capture_trailer"
            row["exit_rule"] = (
                f"after historical bubble activation, trail from local high by {row['trail_giveback_pct']:.2f}% and bank; "
                "kill if the move fails to manifest under the shadow lane timeout"
            )
            row["risk_rule"] = "bubble-capture metadata is watch-only until the dedicated shadow lane proves live bid/ask capture"
        meta["scored_rows"] += 1
        if row["bubble_capture_verdict"] == "bubble_capture_watch":
            meta["watch_rows"] += 1
    return meta


def radar_candidates() -> tuple[list[dict[str, Any]], dict[str, Any]]:
    radar = load_json(LIVE_RADAR_PATH)
    radar_age = age_seconds(radar.get("generated_at"))
    meta = {
        "path": str(LIVE_RADAR_PATH),
        "generated_at": radar.get("generated_at"),
        "age_seconds": round(radar_age, 3) if radar_age is not None else None,
        "fresh": radar_age is not None and 0.0 <= radar_age <= RADAR_MAX_AGE_SECONDS,
    }
    if not meta["fresh"]:
        return [], meta
    rows = []
    for row in radar.get("rows") or []:
        candidate = radar_hurdle_row(row)
        if candidate:
            rows.append(candidate)
    return rows, meta


def dissonance_gate() -> tuple[set[str], dict[str, Any]]:
    payload = load_json(DISSONANCE_PATH)
    dissonance_age = age_seconds(payload.get("generated_at"))
    summary = payload.get("summary") if isinstance(payload, dict) else {}
    if not isinstance(summary, dict):
        summary = {}
    fresh = dissonance_age is not None and 0.0 <= dissonance_age <= DISSONANCE_MAX_AGE_SECONDS
    blocked_actions = {"avoid_toxic_spread", "avoid_broad_dump_wave", "wait_for_alignment", "rebound_watch_only"}
    blocked_products = {
        str(row.get("product_id") or "")
        for row in payload.get("rows") or []
        if str(row.get("action") or "") in blocked_actions
    }
    blocked_products.discard("")
    meta = {
        "path": str(DISSONANCE_PATH),
        "generated_at": payload.get("generated_at"),
        "age_seconds": round(dissonance_age, 3) if dissonance_age is not None else None,
        "fresh": fresh,
        "broad_toxic": bool(summary.get("broad_toxic")) if fresh else False,
        "broad_toxicity_score": summary.get("broad_toxicity_score") if fresh else None,
        "blocked_products": len(blocked_products) if fresh else 0,
    }
    if not fresh:
        return set(), meta
    return blocked_products, meta


def is_broad_toxic_exception(row: dict[str, Any]) -> bool:
    state = str(row.get("hurdle_state") or "")
    if state not in {"clears_fast_hurdle", "clears_hour_hurdle"}:
        return False
    if str(row.get("live_route_state") or "") != "ready_direct_usd_or_stable":
        return False
    if to_float(row.get("ret_15m_pct")) <= 0.0:
        return False
    if to_float(row.get("edge_over_hurdle_pct")) < 1.0:
        return False
    if to_float(row.get("spread_bps")) > 35.0:
        return False
    return True


def build_payload() -> dict[str, Any]:
    hurdle = load_json(HURDLE_PATH)
    pulse = load_json(PULSE_PATH)
    pulse_age = age_seconds(pulse.get("generated_at"))
    pulse_stale = pulse_age is None or pulse_age > PULSE_STALE_SECONDS
    radar_rows, radar_meta = radar_candidates()
    dissonance_blocks, dissonance_meta = dissonance_gate()
    candidates = []
    if not pulse_stale and not dissonance_meta["broad_toxic"]:
        candidates.extend(
            row
            for row in (hurdle.get("rows") or [])
            if str(row.get("hurdle_state") or "") in {"clears_fast_hurdle", "clears_hour_hurdle", "pullback_reentry_watch"}
        )
    elif not pulse_stale and dissonance_meta["broad_toxic"]:
        candidates.extend(row for row in (hurdle.get("rows") or []) if is_broad_toxic_exception(row))
    if not dissonance_meta["broad_toxic"]:
        candidates.extend(radar_rows)
    else:
        candidates.extend(row for row in radar_rows if is_broad_toxic_exception(row))
    deduped: dict[str, dict[str, Any]] = {}
    for row in candidates:
        product_id = str(row.get("product_id") or "")
        if not product_id or product_id in dissonance_blocks:
            continue
        current = deduped.get(product_id)
        if current is None or to_float(row.get("edge_over_hurdle_pct")) > to_float(current.get("edge_over_hurdle_pct")):
            deduped[product_id] = row
    candidates = list(deduped.values())
    strategy_rows = [build_strategy_row(row, idx + 1) for idx, row in enumerate(candidates)]
    concurrent_cluster_size = len(strategy_rows)
    strategy_rows.sort(key=lambda row: row["machinegun_score"], reverse=True)
    for idx, row in enumerate(strategy_rows, start=1):
        row["rank"] = idx
        row["concurrent_cluster_size"] = concurrent_cluster_size
    pockets = load_pocket_rows()
    ml_meta = apply_ml_scores(strategy_rows, ml_payload=load_ml_payload(), pockets=pockets)
    tail_payload = load_tail_payload()
    tail_meta = apply_tail_scores(
        strategy_rows, tail_payload=tail_payload, pockets=pockets
    )

    fast_green_meta = apply_fast_green_scores(strategy_rows, fast_green_payload=load_fast_green_payload(), pockets=pockets)
    bubble_capture_meta = apply_bubble_capture_scores(strategy_rows, load_bubble_capture_rows())
    return {
        "generated_at": utc_now_iso(),
        "mode": "coinbase_spot_machinegun_strategy_board",
        "source": str(HURDLE_PATH),
        "source_freshness": {
            "pulse_generated_at": pulse.get("generated_at"),
            "pulse_age_seconds": round(pulse_age, 3) if pulse_age is not None else None,
            "pulse_stale": pulse_stale,
            "pulse_candidates_suppressed": pulse_stale,
            "broad_toxic_exception_candidates": sum(1 for row in candidates if is_broad_toxic_exception(row)),
            "live_radar": radar_meta,
            "live_radar_seeded_candidates": len(radar_rows),
            "dissonance": dissonance_meta,
            "ml_fee_survival": ml_meta,
            "ml_fast_green": fast_green_meta,
            "bubble_capture": bubble_capture_meta,
        },
        "leadership_read": [
            "High-fee Coinbase spot cannot machinegun sub-percent scalps; it needs fee-cleared state transitions.",
            "The off-wall idea is a rotating state machine: chase only when the move has paid the fee tax, trail profit aggressively, and reload only after the setup re-earns entry.",
            "Fresh live-radar candidates can supplement stale candle-pulse candidates, but only when rolling best-bid movement already clears fee + spread + profit buffer.",
            "Dissonance blocks broad toxic regimes and product-level dump/spread/misalignment rows before they can become long-only spot entries.",
            "These are shadow playbooks, not live permission. The next proof step is to run them on the hurdle-clearing universe with recorded bid/ask fills and actual account fees.",
            "Fee-survival ML scores are watch-only metadata. They do not change rank, entry, or rotation until a forward shadow gate proves lift.",
            "Fast-green ML scores are also watch-only; they mark candidates resembling historical setups that reached fee-paid +1% within ten minutes.",
            "Bubble-capture metadata marks products whose longer ignition/trailing geometry survived real-fee historical replay; it is not live permission without forward shadow capture.",
        ],
        "playbook_stack": [
            {
                "name": "fee_hurdle_breakout_trailer",
                "concept": "Buy only after momentum has already paid for the round trip, then trail from the high instead of waiting for a fixed target.",
            },
            {
                "name": "hot_potato_hour_rotation",
                "concept": "Capital is a single baton that rotates only when the next symbol's fee-cleared edge is strong enough to pay the churn tax.",
            },
            {
                "name": "slow_burn_profit_bond",
                "concept": "Once a trade earns enough to cover fees, convert part of that profit into a protected stop; add-ons can only be financed by realized fee-cleared profit.",
            },
            {
                "name": "rubber_band_reload",
                "concept": "Do not catch the falling pullback; wait for the rebound to re-clear the fee hurdle, then trail tightly and allow lower re-entry later.",
            },
        ],
        "rows": strategy_rows,
    }


def write_reports(payload: dict[str, Any]) -> None:
    JSON_PATH.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    columns = [
        "rank",
        "product_id",
        "concurrent_cluster_size",
        "hurdle_state",
        "playbook",
        "machinegun_score",
        "edge_over_hurdle_pct",
        "ret_15m_pct",
        "ret_60m_pct",
        "ret_4h_pct",
        "spread_bps",
        "trail_giveback_pct",
        "source",
        "live_radar_signal_state",
        "live_radar_best_window_bps",
        "ml_survival_prob",
        "ml_gate_verdict",
        "ml_score_basis",
        "ml_feature_completeness",
        "fast_green_prob",
        "fast_green_verdict",
        "fast_green_label",
        "fast_green_score_basis",
        "bubble_capture_net_pct_per_hour",
        "bubble_capture_avg_net_pct",
        "bubble_capture_trades",
        "bubble_capture_win_rate_pct",
        "bubble_capture_verdict",
        "bubble_capture_basis",
        "entry_rule",
        "exit_rule",
        "reentry_rule",
        "risk_rule",
    ]
    with CSV_PATH.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns)
        writer.writeheader()
        for row in payload["rows"]:
            writer.writerow({key: row.get(key, "") for key in columns})
    lines = ["# Coinbase Spot Machinegun Strategy Board", "", "## Leadership Read", ""]
    for line in payload["leadership_read"]:
        lines.append(f"- {line}")
    freshness = payload.get("source_freshness", {})
    lines.extend(
        [
            "",
            "## Source Freshness",
            "",
            f"- Pulse generated: `{freshness.get('pulse_generated_at')}`",
            f"- Pulse age seconds: `{freshness.get('pulse_age_seconds')}`",
            f"- Pulse stale: `{freshness.get('pulse_stale')}`",
            f"- Pulse candidates suppressed: `{freshness.get('pulse_candidates_suppressed')}`",
            f"- Live radar seeded candidates: `{freshness.get('live_radar_seeded_candidates')}`",
            f"- Dissonance broad toxic: `{(freshness.get('dissonance') or {}).get('broad_toxic')}`",
            f"- Dissonance blocked products: `{(freshness.get('dissonance') or {}).get('blocked_products')}`",
            f"- ML fee-survival scorer: `{(freshness.get('ml_fee_survival') or {}).get('mode')}`",
            f"- ML scored rows: `{(freshness.get('ml_fee_survival') or {}).get('scored_rows')}`",
            f"- ML ultra-strict rows: `{(freshness.get('ml_fee_survival') or {}).get('ultra_strict_rows')}`",
            f"- ML fast-green scorer: `{(freshness.get('ml_fast_green') or {}).get('mode')}`",
            f"- ML fast-green label: `{(freshness.get('ml_fast_green') or {}).get('label')}`",
            f"- ML fast-green scored rows: `{(freshness.get('ml_fast_green') or {}).get('scored_rows')}`",
            f"- ML fast-green watch rows: `{(freshness.get('ml_fast_green') or {}).get('watch_rows')}`",
            f"- Bubble-capture scorer: `{(freshness.get('bubble_capture') or {}).get('mode')}`",
            f"- Bubble-capture scored rows: `{(freshness.get('bubble_capture') or {}).get('scored_rows')}`",
            f"- Bubble-capture watch rows: `{(freshness.get('bubble_capture') or {}).get('watch_rows')}`",
        ]
    )
    lines.extend(["", "## Playbook Stack", ""])
    for playbook in payload["playbook_stack"]:
        lines.append(f"- `{playbook['name']}`: {playbook['concept']}")
    lines.extend(
        [
            "",
            "## Candidate Rows",
            "",
            "| Rank | Product | Hurdle State | Playbook | Score | Edge % | 15m % | 60m % | Spread bps | Trail % | Fee ML p | Fast ML p | Bubble %/h | Fast Verdict | Bubble Verdict |",
            "| ---: | --- | --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- | --- |",
        ]
    )
    for row in payload["rows"]:
        ml_probability = row.get("ml_survival_prob")
        ml_probability_text = "" if ml_probability is None else f"{float(ml_probability):.4f}"
        fast_green_probability = row.get("fast_green_prob")
        fast_green_probability_text = "" if fast_green_probability is None else f"{float(fast_green_probability):.4f}"
        bubble_capture = row.get("bubble_capture_net_pct_per_hour")
        bubble_capture_text = "" if bubble_capture is None else f"{float(bubble_capture):.4f}"
        lines.append(
            "| {rank} | {product_id} | {hurdle_state} | {playbook} | {machinegun_score:.4f} | {edge_over_hurdle_pct:.4f} | {ret_15m_pct:.4f} | {ret_60m_pct:.4f} | {spread_bps:.2f} | {trail_giveback_pct:.4f} | {ml_probability} | {fast_green_probability} | {bubble_capture} | {fast_green_verdict} | {bubble_capture_verdict} |".format(
                ml_probability=ml_probability_text,
                fast_green_probability=fast_green_probability_text,
                bubble_capture=bubble_capture_text,
                **row
            )
        )
    lines.extend(["", "## Rules", ""])
    for row in payload["rows"]:
        lines.extend(
            [
                f"### {row['rank']}. {row['product_id']} - {row['playbook']}",
                f"- Source: `{row.get('source', '')}`",
                f"- ML: `{row.get('ml_gate_verdict', '')}` p=`{row.get('ml_survival_prob')}` basis=`{row.get('ml_score_basis', '')}` completeness=`{row.get('ml_feature_completeness', '')}`",
                f"- Fast-green ML: `{row.get('fast_green_verdict', '')}` p=`{row.get('fast_green_prob')}` label=`{row.get('fast_green_label', '')}` basis=`{row.get('fast_green_score_basis', '')}`",
                f"- Bubble capture: `{row.get('bubble_capture_verdict', '')}` net_pct_per_hour=`{row.get('bubble_capture_net_pct_per_hour')}` avg_net=`{row.get('bubble_capture_avg_net_pct')}` trades=`{row.get('bubble_capture_trades')}` basis=`{row.get('bubble_capture_basis', '')}`",
                f"- Entry: {row['entry_rule']}",
                f"- Exit: {row['exit_rule']}",
                f"- Re-entry: {row['reentry_rule']}",
                f"- Risk: {row['risk_rule']}",
                "",
            ]
        )
    MD_PATH.write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    payload = build_payload()
    write_reports(payload)
    print(json.dumps({"json_path": str(JSON_PATH), "csv_path": str(CSV_PATH), "md_path": str(MD_PATH), "rows": len(payload["rows"])}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
