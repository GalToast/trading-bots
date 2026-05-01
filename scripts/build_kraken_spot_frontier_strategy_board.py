#!/usr/bin/env python3
from __future__ import annotations

import csv
import json
import warnings
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
if str(Path(__file__).resolve().parent) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parent))

REPORTS = ROOT / "reports"
VELOCITY_BOARD_PATH = REPORTS / "kraken_spot_money_velocity_board.json"
PULSE_PATH = REPORTS / "kraken_spot_pulse_board.json"
LIVE_RADAR_PATH = REPORTS / "kraken_spot_live_radar.json"
OPPORTUNITY_PATH = REPORTS / "kraken_maker_opportunity_board.json"
JSON_PATH = REPORTS / "kraken_spot_frontier_strategy_board.json"
CSV_PATH = REPORTS / "kraken_spot_frontier_strategy_board.csv"
MD_PATH = REPORTS / "kraken_spot_frontier_strategy_board.md"

# Using Coinbase models on Kraken features (Mad Scientist path)
TAIL_MODEL_PATH = REPORTS / "models" / "coinbase_spot_high_gross_tail_predictor_v2_fixed.joblib"
FAST_GREEN_MODEL_PATH = REPORTS / "models" / "coinbase_spot_fast_green_model.joblib"

# Kraken Live Foundry Features (12-bar features)
LIVE_FOUNDRY_FEATURES_PATH = REPORTS / "kraken_spot_live_foundry_features.json"

OVERLAP_BOARD_PATH = REPORTS / "coinbase_kraken_spot_overlap_board.json"
HANDOFF_POLICY_PATH = REPORTS / "spot_venue_handoff_policy.json"

# Geometric Siblings (High Discretization Alpha)
GEOMETRIC_SIBLINGS = ["DYM-USD", "HONEY-USD", "SWEAT-USD", "MYX-USD", "XCN-USD", "DUCK-USD", "STEP-USD", "CQT-USD", "BMB-USD"]

KRAKEN_FEE_BPS_PER_SIDE = 40.0
KRAKEN_PROFIT_BUFFER_PCT = 0.50
RADAR_MAX_AGE_SECONDS = 300.0
ML_WATCH_THRESHOLD = 0.95
FAST_GREEN_WATCH_THRESHOLD = 0.90
TAIL_THRESHOLD = 0.90
NUT_CRACKER_THRESHOLD = 0.70
MAX_MAKER_HARVEST_SPREAD_BPS = 750.0


def load_json(path: Path) -> Any:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


LIVE_FOUNDRY_LOOKUP = load_json(LIVE_FOUNDRY_FEATURES_PATH)
OPPORTUNITY_LOOKUP = {r["product_id"]: r for r in load_json(OPPORTUNITY_PATH).get("rows", [])}


def to_float(value: Any) -> float:
    try:
        return float(value or 0.0)
    except (TypeError, ValueError):
        return 0.0


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def build_strategy_row(row: dict[str, Any], rank: int) -> dict[str, Any]:
    edge = to_float(row.get("kraken_edge_bps")) / 100.0
    speed = to_float(row.get("best_move_bps")) / 100.0
    tail_p = to_float(row.get("tail_prob"))
    fg_p = to_float(row.get("fast_green_prob"))
    
    # MER (Maker Efficiency Ratio) boost: 
    # For High-MER, spread is our PROFIT, not our COST.
    pid = str(row.get("product_id") or "")
    mer = to_float(OPPORTUNITY_LOOKUP.get(pid, {}).get("mer"))
    mer_boost = min(mer * 5.0, 10.0) # Up to 10 points boost for high MER
    spread = to_float(row.get("spread_bps"))
    
    playbook = "maker_harvest" if mer > 0.5 else "frontier_machinegun"
    if mer > 0.5 and spread > MAX_MAKER_HARVEST_SPREAD_BPS:
        quality = -max(spread / 100.0, 1.0)
        playbook = "maker_harvest_extreme_spread_veto"
    elif mer > 0.5:
        # Prioritize by MER directly for the harvest playbook
        quality = 50.0 + (mer * 10.0) + (speed / 10.0)
    else:
        # Standard taker-hurdle scoring
        quality = max(edge, 0.0) * 3.0 + speed - (to_float(row.get("spread_bps")) / 10.0) + mer_boost
    
    # NUT CRACKER MULTIPLIER: Boost candidates with high-gross big move potential
    # If tail_p clears the frontier threshold, add a 100% boost to the base quality.
    if tail_p >= NUT_CRACKER_THRESHOLD:
        quality *= 2.0
    
    # Status
    nut_cracker_verdict = "not_detected"
    if tail_p >= NUT_CRACKER_THRESHOLD and fg_p >= NUT_CRACKER_THRESHOLD:
        nut_cracker_verdict = "NUT_CRACKER_PRIME"
    elif tail_p >= NUT_CRACKER_THRESHOLD:
        nut_cracker_verdict = "tail_only"
    
    return {
        "rank": rank,
        "product_id": pid,
        "verdict": str(row.get("verdict") or ""),
        "playbook": playbook,
        "frontier_score": round(quality, 4),
        "machinegun_score": round(quality, 4),
        "mer": round(mer, 4),
        "kraken_edge_bps": round(to_float(row.get("kraken_edge_bps")), 2),
        "edge_over_hurdle_pct": round(to_float(row.get("kraken_edge_bps")) / 100.0, 4),
        "best_move_bps": round(to_float(row.get("best_move_bps")), 2),
        "spread_bps": round(to_float(row.get("spread_bps")), 2),
        "source": str(row.get("source") or "money_velocity_board"),
        "ml_survival_prob": tail_p,
        "ml_gate_verdict": row.get("ml_gate_verdict", "not_scored"),
        "tail_prob": tail_p,
        "tail_verdict": row.get("tail_verdict", "not_scored"),
        "fast_green_prob": fg_p,
        "fast_green_verdict": row.get("fast_green_verdict", "not_scored"),
        "nut_cracker_verdict": nut_cracker_verdict
    }


def ml_feature_row(row: dict[str, Any], geometry: dict[str, Any]) -> dict[str, Any]:
    now = datetime.now(timezone.utc)
    product_id = str(row.get("product_id") or "")
    live = LIVE_FOUNDRY_LOOKUP.get(product_id, {})
    
    move_bps = to_float(row.get("best_move_bps"))
    spread = to_float(row.get("spread_bps"))
    
    return {
        "product_id": product_id,
        "archetype": "bubble_ignition_reclaim",
        "trigger": "live_bid_burst",
        "confirmation": "one_poll_hot",
        "exit": "wide_bubble_trail",
        "sizing": "standard_50",
        "trigger_mode": "impulse",
        "hour_utc": now.hour,
        "lookback": 1,
        "trigger_bps": 25.0,
        "target_pct": 7.0,
        "stop_pct": 2.5,
        "hold_bars": 12,
        "spread_bps_proxy": spread,
        "fee_bps_round_trip": (2.0 * KRAKEN_FEE_BPS_PER_SIDE) + spread,
        "ret_1_bps": live.get("ret_1_bps", move_bps),
        "ret_3_bps": live.get("ret_3_bps", move_bps),
        "ret_6_bps": live.get("ret_6_bps", move_bps),
        "ret_12_bps": live.get("ret_12_bps", move_bps),
        "range_bps": live.get("range_bps", abs(move_bps)),
        "body_bps": live.get("body_bps", move_bps),
        "close_location": live.get("close_location", 0.85 if move_bps > 0 else 0.5),
        "volume_mult_12": live.get("volume_mult_12", 1.0),
        "volatility_12_bps": live.get("volatility_12_bps", max(abs(move_bps) * 0.33, 1.0)),
        "accel_vs_median_abs_12": live.get("accel_vs_median_abs_12", 1.0),
        "dist_from_12_high_bps": live.get("dist_from_12_high_bps", 0.0),
        "dist_from_12_low_bps": live.get("dist_from_12_low_bps", move_bps),
        "position_in_12_range": live.get("position_in_12_range", 0.85 if move_bps > 0 else 0.5),
        "tail_hit_rate_5": 0.0,
        "time_since_tail": 1000.0,
        "prev_ret_1_bps": 0.0,
        "trend_3": 0.0,
        "trend_6": 0.0,
        "non_tail_streak": 10.0,
    }


def apply_tail_scores(rows: list[dict[str, Any]]) -> dict[str, Any]:
    meta = {"model_path": str(TAIL_MODEL_PATH), "available": False, "scored_rows": 0, "watch_rows": 0}
    if not TAIL_MODEL_PATH.exists():
        return meta
    try:
        import joblib
        import pandas as pd
        tail_payload = joblib.load(TAIL_MODEL_PATH)
        model = tail_payload["model"]
        categorical = tail_payload.get("categorical_cols", [])
        numeric = tail_payload.get("numeric_cols", [])
        encoders = tail_payload.get("encoders", {})
        meta["available"] = True
    except Exception:
        return meta

    for row in rows:
        product_id = row["product_id"]
        feature_row = ml_feature_row(row, {})
        frame = pd.DataFrame([feature_row])
        for col in categorical:
            if col in encoders:
                val = str(feature_row[col])
                if val not in encoders[col].classes_:
                    print(f"  WARNING: Unseen label '{val}' for column '{col}' on {product_id}. Fallback to most frequent.")
                    frame[col] = encoders[col].transform([encoders[col].classes_[0]])
                else:
                    frame[col] = encoders[col].transform([val])
            else:
                frame[col] = 0
        try:
            with warnings.catch_warnings():
                warnings.filterwarnings("ignore")
                prob = model.predict_proba(frame[categorical + numeric])[0, 1]
            row["tail_prob"] = round(float(prob), 6)
            row["tail_verdict"] = "tail_watch" if prob >= TAIL_THRESHOLD else "below_tail_threshold"
            row["ml_survival_prob"] = row["tail_prob"]
            row["ml_gate_verdict"] = "ultra_strict_watch" if prob >= ML_WATCH_THRESHOLD else "watch_only_below_threshold"
            meta["scored_rows"] += 1
            if prob >= TAIL_THRESHOLD:
                meta["watch_rows"] += 1
        except Exception:
            continue
    return meta


def apply_fast_green_scores(rows: list[dict[str, Any]]) -> dict[str, Any]:
    meta = {"model_path": str(FAST_GREEN_MODEL_PATH), "available": False, "scored_rows": 0, "watch_rows": 0}
    if not FAST_GREEN_MODEL_PATH.exists():
        return meta
    try:
        import joblib
        import pandas as pd
        fg_payload = joblib.load(FAST_GREEN_MODEL_PATH)
        model = fg_payload["model"]
        categorical = fg_payload.get("categorical", [])
        numeric = fg_payload.get("numeric", [])
        meta["available"] = True
    except Exception:
        return meta

    for row in rows:
        product_id = row["product_id"]
        feature_row = ml_feature_row(row, {})
        frame = pd.DataFrame([feature_row])
        for col in categorical:
            frame[col] = frame[col].astype(str)
        try:
            with warnings.catch_warnings():
                warnings.filterwarnings("ignore")
                prob = model.predict_proba(frame[categorical + numeric])[0, 1]
            row["fast_green_prob"] = round(float(prob), 6)
            row["fast_green_verdict"] = "fast_green_watch" if prob >= FAST_GREEN_WATCH_THRESHOLD else "below_fast_green_threshold"
            meta["scored_rows"] += 1
            if prob >= FAST_GREEN_WATCH_THRESHOLD:
                meta["watch_rows"] += 1
        except Exception:
            continue
    return meta


def build_payload() -> dict[str, Any]:
    velocity = load_json(VELOCITY_BOARD_PATH)
    pulse = load_json(PULSE_PATH)
    radar = load_json(LIVE_RADAR_PATH)
    overlap = load_json(OVERLAP_BOARD_PATH)
    handoff = load_json(HANDOFF_POLICY_PATH)
    
    candidates = []
    # 0. Consume the operational handoff policy first when present. This is a
    # runner-facing contract, not another review board.
    for action in handoff.get("actions") or []:
        if action.get("venue") != "kraken":
            continue
        if action.get("proof_status") != "shadow_ready":
            continue
        if action.get("action") not in {"kraken_maker_shadow", "kraken_taker_shadow"}:
            continue
        pid = str(action.get("product_id") or "")
        if not pid:
            continue
        candidates.append({
            "product_id": pid,
            "kraken_edge_bps": to_float(action.get("expected_edge_bps")),
            "best_move_bps": max(to_float(action.get("expected_edge_bps")), to_float(action.get("ret_15m_bps"))),
            "spread_bps": to_float(action.get("spread_bps")),
            "source": "spot_venue_handoff_policy",
            "verdict": str(action.get("action") or ""),
            "policy_score": to_float(action.get("score")),
        })

    # 1. Try velocity board (radar-backed)
    seen_from_policy = {str(c["product_id"]) for c in candidates}
    for row in velocity.get("rows") or []:
        if str(row.get("product_id") or "") in seen_from_policy:
            continue
        if row.get("verdict") in {"kraken_fee_flip_candidate", "clears_both_fee_models", "near_kraken_hurdle"}:
            candidates.append(row)
            
    # 2. Integrate Coinbase Lead Radar (The Bridge)
    seen_pids = {str(c["product_id"]) for c in candidates}
    for row in overlap.get("rows") or []:
        pid = str(row.get("product_id") or "")
        if pid in seen_pids:
            continue
        
        # Only take high-fidelity Coinbase signals
        if row.get("coinbase_signal_state") in {"live_hot", "building"}:
            kraken_pid = str(row.get("kraken_product_id") or pid)
            candidates.append({
                "product_id": kraken_pid,
                "kraken_edge_bps": to_float(row.get("kraken_edge_bps")),
                "best_move_bps": to_float(row.get("best_move_bps")) or to_float(row.get("coinbase_net_pct")) * 100.0,
                "spread_bps": to_float(row.get("kraken_spread_bps") or row.get("spread_bps")),
                "source": "coinbase_lead_radar",
                "verdict": row.get("coinbase_signal_state")
            })
            seen_pids.add(pid)
            
    if not candidates:
        for row in pulse.get("rows") or []:
            if to_float(row.get("pulse_score")) > 5.0:
                candidates.append({
                    "product_id": row.get("product_id"),
                    "kraken_edge_bps": to_float(row.get("ret_60m_pct")) * 100.0 - 130.0,
                    "best_move_bps": to_float(row.get("ret_15m_pct")) * 100.0,
                    "spread_bps": to_float(row.get("spread_bps")),
                    "source": "pulse_fallback",
                    "verdict": "pulse_hot"
                })
                
    seen_pids = {str(c["product_id"]) for c in candidates}
    radar_rows = {str(r["product_id"]): r for r in radar.get("rows") or []}
    pulse_rows = {str(r["product_id"]): r for r in pulse.get("rows") or []}
    
    for pid in GEOMETRIC_SIBLINGS:
        if pid in seen_pids:
            continue
        if pid in radar_rows:
            r = radar_rows[pid]
            candidates.append({
                "product_id": pid,
                "kraken_edge_bps": to_float(r.get("ret_60m_bps")),
                "best_move_bps": to_float(r.get("ret_15m_bps")),
                "spread_bps": to_float(r.get("spread_bps")),
                "source": "geometric_priority",
                "verdict": "geometric_alpha"
            })
        elif pid in pulse_rows:
            p = pulse_rows[pid]
            candidates.append({
                "product_id": pid,
                "kraken_edge_bps": to_float(p.get("ret_60m_pct")) * 100.0 - 130.0,
                "best_move_bps": to_float(p.get("ret_15m_pct")) * 100.0,
                "spread_bps": to_float(p.get("spread_bps")),
                "source": "geometric_priority",
                "verdict": "geometric_alpha"
            })
            
    apply_tail_scores(candidates)
    apply_fast_green_scores(candidates)
    
    strategy_rows = [build_strategy_row(row, idx + 1) for idx, row in enumerate(candidates)]
    concurrent_cluster_size = len(strategy_rows)
    strategy_rows.sort(key=lambda x: x["frontier_score"], reverse=True)
    
    for idx, row in enumerate(strategy_rows, start=1):
        row["rank"] = idx
        row["concurrent_cluster_size"] = concurrent_cluster_size
    
    return {
        "generated_at": utc_now_iso(),
        "mode": "kraken_spot_frontier_strategy_board",
        "rows": strategy_rows,
        "leadership_read": [
            "Kraken Spot Frontier: Optimized for lower fees and MER harvest.",
            "MER (Maker Efficiency Ratio) boosts products where spread > ATR.",
            "High MER products are assigned to the 'maker_harvest' playbook.",
            f"NUT CRACKER: Multiplier (2x) applied to products with Tail Prob >= {NUT_CRACKER_THRESHOLD:.2f}; prime requires Tail and FastGreen both above that threshold."
        ]
    }


def write_reports(payload: dict[str, Any]) -> None:
    JSON_PATH.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    columns = ["rank", "product_id", "playbook", "frontier_score", "mer", "spread_bps", "tail_prob", "fast_green_prob", "nut_cracker_verdict"]
    with CSV_PATH.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns)
        writer.writeheader()
        for row in payload["rows"]:
            writer.writerow({key: row.get(key, "") for key in columns})
            
    lines = ["# Kraken Spot Frontier Strategy Board", "", "## Candidate Rows", ""]
    lines.append("| Rank | Product | Playbook | Score | MER | Spread | Tail P | FG P | Verdict |")
    lines.append("| ---: | --- | --- | ---: | ---: | ---: | ---: | ---: | --- |")
    for row in payload["rows"]:
        lines.append("| {rank} | {product_id} | {playbook} | {frontier_score:.4f} | {mer:.4f} | {spread_bps:.1f} | {tail_prob} | {fast_green_prob} | {nut_cracker_verdict} |".format(**row))
    MD_PATH.write_text("\n".join(lines), encoding="utf-8")


def main():
    payload = build_payload()
    write_reports(payload)
    print(f"DONE! Saved {len(payload['rows'])} rows to {JSON_PATH}")


if __name__ == "__main__":
    main()
