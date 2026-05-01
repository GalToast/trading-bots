#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
import warnings
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import joblib
import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parent.parent
REPORTS = ROOT / "reports"
TABLE_PATH = REPORTS / "coinbase_spot_fee_survival_training_table.csv"
TAIL_MODEL_PATH = REPORTS / "models" / "coinbase_spot_high_gross_tail_predictor_v2_fixed.joblib"
FAST_GREEN_MODEL_PATH = REPORTS / "models" / "coinbase_spot_fast_green_model.joblib"
DEFAULT_JSON_PATH = REPORTS / "coinbase_spot_tail_fastgreen_compression_audit.json"
DEFAULT_CSV_PATH = REPORTS / "coinbase_spot_tail_fastgreen_compression_audit.csv"
DEFAULT_MD_PATH = REPORTS / "coinbase_spot_tail_fastgreen_compression_audit.md"


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def to_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Audit Tail V2 + FastGreen signal after simultaneous-row compression.")
    parser.add_argument("--table-path", default=str(TABLE_PATH))
    parser.add_argument("--tail-model-path", default=str(TAIL_MODEL_PATH))
    parser.add_argument("--fast-green-model-path", default=str(FAST_GREEN_MODEL_PATH))
    parser.add_argument("--json-path", default=str(DEFAULT_JSON_PATH))
    parser.add_argument("--csv-path", default=str(DEFAULT_CSV_PATH))
    parser.add_argument("--md-path", default=str(DEFAULT_MD_PATH))
    parser.add_argument("--min-tail-prob", type=float, default=0.90)
    parser.add_argument("--min-fast-green-prob", type=float, default=0.90)
    parser.add_argument("--kraken-fee-bps-round-trip", type=float, default=80.0)
    return parser.parse_args()


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    tmp.replace(path)


def add_temporal_features_fixed(df: pd.DataFrame) -> pd.DataFrame:
    df = df.sort_values(["product_id", "time"]).reset_index(drop=True).copy()
    df["high_gross"] = df["gross_pct"] > 2.5

    def add_group(group: pd.DataFrame) -> pd.DataFrame:
        group = group.copy()
        group["tail_hit_rate_5"] = group["high_gross"].shift(1).rolling(window=5, min_periods=1).mean()
        group["time_since_tail"] = (group["high_gross"].shift(1) == 0).cumsum()
        group["prev_ret_1_bps"] = group["ret_1_bps"].shift(1).fillna(0)
        group["trend_3"] = group["ret_1_bps"].shift(1).rolling(window=3, min_periods=1).mean()
        group["trend_6"] = group["ret_1_bps"].shift(1).rolling(window=6, min_periods=1).mean()
        streaks: list[int] = []
        streak = 0
        values = group["high_gross"].tolist()
        for idx in range(len(group)):
            if idx == 0:
                streak = 0
            elif values[idx - 1] == 0:
                streak += 1
            else:
                streak = 0
            streaks.append(streak)
        group["non_tail_streak"] = streaks
        return group

    return df.groupby("product_id", group_keys=False).apply(add_group).reset_index(drop=True)


def score_tail(df: pd.DataFrame, payload: dict[str, Any]) -> np.ndarray:
    feature_cols = list(payload.get("feature_cols") or [])
    categorical_cols = list(payload.get("categorical_cols") or [])
    frame = df.copy()
    for col in categorical_cols:
        if col in frame:
            frame[col] = frame[col].astype("category").cat.codes
    for col in feature_cols:
        if col not in frame:
            frame[col] = 0.0
        frame[col] = pd.to_numeric(frame[col], errors="coerce").replace([np.inf, -np.inf], np.nan).fillna(0.0)
    with warnings.catch_warnings():
        warnings.filterwarnings("ignore", message="X does not have valid feature names")
        return payload["model"].predict_proba(frame[feature_cols])[:, 1]


def score_fast_green(df: pd.DataFrame, payload: dict[str, Any]) -> np.ndarray:
    frame = df.copy()
    categorical = list(payload.get("categorical") or [])
    numeric = list(payload.get("numeric") or [])
    for col in categorical:
        frame[col] = frame.get(col, "").astype(str).fillna("")
    for col in numeric:
        frame[col] = pd.to_numeric(frame.get(col, 0.0), errors="coerce").replace([np.inf, -np.inf], np.nan).fillna(0.0)
    return payload["model"].predict_proba(frame[categorical + numeric])[:, 1]


def stats_for(df: pd.DataFrame, *, kraken_fee_bps_round_trip: float) -> dict[str, Any]:
    if df.empty:
        return {
            "rows": 0,
            "avg_gross_pct": 0.0,
            "avg_coinbase_net_pct": 0.0,
            "avg_kraken_net_pct": 0.0,
            "coinbase_cum_net_pct": 0.0,
            "kraken_cum_net_pct": 0.0,
            "coinbase_win_rate_pct": 0.0,
            "kraken_win_rate_pct": 0.0,
            "unique_timestamps": 0,
            "unique_products": 0,
        }
    kraken_fee_pct = (kraken_fee_bps_round_trip + pd.to_numeric(df["spread_bps_proxy"], errors="coerce").fillna(0.0)) / 100.0
    kraken_net = pd.to_numeric(df["gross_pct"], errors="coerce").fillna(0.0) - kraken_fee_pct
    coinbase_net = pd.to_numeric(df["net_pct"], errors="coerce").fillna(0.0)
    return {
        "rows": int(len(df)),
        "avg_gross_pct": round(float(df["gross_pct"].mean()), 6),
        "avg_coinbase_net_pct": round(float(coinbase_net.mean()), 6),
        "avg_kraken_net_pct": round(float(kraken_net.mean()), 6),
        "coinbase_cum_net_pct": round(float(coinbase_net.sum()), 6),
        "kraken_cum_net_pct": round(float(kraken_net.sum()), 6),
        "coinbase_win_rate_pct": round(float((coinbase_net > 0).mean() * 100.0), 6),
        "kraken_win_rate_pct": round(float((kraken_net > 0).mean() * 100.0), 6),
        "unique_timestamps": int(df["time"].nunique()) if "time" in df else 0,
        "unique_products": int(df["product_id"].nunique()) if "product_id" in df else 0,
    }


def compress_one_per_time(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return df.copy()
    ranked = df.copy()
    ranked["combined_prob"] = ranked["tail_prob"] * ranked["fast_green_prob"]
    ranked = ranked.sort_values(["time", "combined_prob", "tail_prob", "fast_green_prob"], ascending=[True, False, False, False])
    return ranked.groupby("time", as_index=False, group_keys=False).head(1).reset_index(drop=True)


def build(args: argparse.Namespace) -> dict[str, Any]:
    raw = pd.read_csv(args.table_path)
    df = add_temporal_features_fixed(raw)
    tail_payload = joblib.load(args.tail_model_path)
    fast_payload = joblib.load(args.fast_green_model_path)
    df["tail_prob"] = score_tail(df, tail_payload)
    df["fast_green_prob"] = score_fast_green(df, fast_payload)
    df["combined_prob"] = df["tail_prob"] * df["fast_green_prob"]

    selected = df[(df["tail_prob"] >= float(args.min_tail_prob)) & (df["fast_green_prob"] >= float(args.min_fast_green_prob))].copy()
    compressed_product_time = (
        selected.sort_values(["product_id", "time", "combined_prob"], ascending=[True, True, False])
        .groupby(["product_id", "time"], as_index=False, group_keys=False)
        .head(1)
        .reset_index(drop=True)
    )
    compressed_time = compress_one_per_time(selected)

    unique_times = sorted(df["time"].dropna().unique().tolist()) if "time" in df else []
    cutoff = unique_times[int(len(unique_times) * 0.75)] if unique_times else ""
    true_time_test = df[df["time"] >= cutoff].copy() if cutoff != "" else df.iloc[0:0].copy()
    true_time_selected = true_time_test[
        (true_time_test["tail_prob"] >= float(args.min_tail_prob)) & (true_time_test["fast_green_prob"] >= float(args.min_fast_green_prob))
    ].copy()
    true_time_compressed = compress_one_per_time(true_time_selected)

    model_order_test = df.iloc[int(len(df) * 0.75) :].copy()
    model_order_selected = model_order_test[
        (model_order_test["tail_prob"] >= float(args.min_tail_prob)) & (model_order_test["fast_green_prob"] >= float(args.min_fast_green_prob))
    ].copy()
    model_order_compressed = compress_one_per_time(model_order_selected)

    payload = {
        "generated_at": utc_now_iso(),
        "mode": "coinbase_spot_tail_fastgreen_compression_audit",
        "parameters": {
            "table_path": str(args.table_path),
            "tail_model_path": str(args.tail_model_path),
            "fast_green_model_path": str(args.fast_green_model_path),
            "min_tail_prob": float(args.min_tail_prob),
            "min_fast_green_prob": float(args.min_fast_green_prob),
            "kraken_fee_bps_round_trip": float(args.kraken_fee_bps_round_trip),
            "true_time_split_cutoff": cutoff,
            "unique_time_count": len(unique_times),
        },
        "model_meta": {
            "tail_test_auc": to_float(tail_payload.get("test_auc")),
            "tail_test_ap": to_float(tail_payload.get("test_ap")),
            "tail_version": tail_payload.get("version"),
            "fast_green_label": fast_payload.get("label") or (fast_payload.get("report") or {}).get("label"),
        },
        "read": [
            "This audits Tail V2 + FastGreen before strategy-board promotion.",
            "Raw row counts are not executable trade counts because many product/setup variants fire at the same timestamp.",
            "The one_per_time rows are the stricter first approximation of a one-position allocator.",
        ],
        "stats": {
            "all_selected_raw": stats_for(selected, kraken_fee_bps_round_trip=float(args.kraken_fee_bps_round_trip)),
            "all_selected_one_per_product_time": stats_for(
                compressed_product_time, kraken_fee_bps_round_trip=float(args.kraken_fee_bps_round_trip)
            ),
            "all_selected_one_per_time": stats_for(compressed_time, kraken_fee_bps_round_trip=float(args.kraken_fee_bps_round_trip)),
            "true_time_test_raw": stats_for(true_time_selected, kraken_fee_bps_round_trip=float(args.kraken_fee_bps_round_trip)),
            "true_time_test_one_per_time": stats_for(true_time_compressed, kraken_fee_bps_round_trip=float(args.kraken_fee_bps_round_trip)),
            "model_order_test_raw": stats_for(model_order_selected, kraken_fee_bps_round_trip=float(args.kraken_fee_bps_round_trip)),
            "model_order_test_one_per_time": stats_for(model_order_compressed, kraken_fee_bps_round_trip=float(args.kraken_fee_bps_round_trip)),
        },
        "top_one_per_time": compressed_time.sort_values("combined_prob", ascending=False)
        .head(50)[
            [
                "time",
                "product_id",
                "variant_id",
                "tail_prob",
                "fast_green_prob",
                "combined_prob",
                "gross_pct",
                "net_pct",
                "spread_bps_proxy",
                "archetype",
                "trigger",
                "confirmation",
                "exit",
            ]
        ]
        .to_dict(orient="records"),
    }
    write_reports(payload, Path(str(args.json_path)), Path(str(args.csv_path)), Path(str(args.md_path)))
    return payload


def write_reports(payload: dict[str, Any], json_path: Path, csv_path: Path, md_path: Path) -> None:
    write_json(json_path, payload)
    rows = payload.get("top_one_per_time") or []
    columns = [
        "time",
        "product_id",
        "variant_id",
        "tail_prob",
        "fast_green_prob",
        "combined_prob",
        "gross_pct",
        "net_pct",
        "spread_bps_proxy",
        "archetype",
        "trigger",
        "confirmation",
        "exit",
    ]
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    with csv_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns)
        writer.writeheader()
        for row in rows:
            writer.writerow({column: row.get(column, "") for column in columns})
    stats = payload.get("stats") or {}
    lines = [
        "# Coinbase Spot Tail + FastGreen Compression Audit",
        "",
        f"- Generated: `{payload.get('generated_at')}`",
        f"- Tail threshold: `{payload.get('parameters', {}).get('min_tail_prob')}`",
        f"- FastGreen threshold: `{payload.get('parameters', {}).get('min_fast_green_prob')}`",
        f"- Tail model AUC/AP: `{payload.get('model_meta', {}).get('tail_test_auc')}` / `{payload.get('model_meta', {}).get('tail_test_ap')}`",
        "",
        "## Read",
        "",
    ]
    lines.extend([f"- {item}" for item in payload.get("read") or []])
    lines.extend(["", "## Stats", ""])
    lines.extend(
        [
            "| Slice | Rows | Timestamps | Products | Avg Gross % | Avg Coinbase Net % | Avg Kraken Net % | Coinbase Cum % | Kraken Cum % | Coinbase Win % | Kraken Win % |",
            "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
        ]
    )
    for name, stat in stats.items():
        lines.append(
            "| {name} | {rows} | {unique_timestamps} | {unique_products} | {avg_gross_pct:.4f} | {avg_coinbase_net_pct:.4f} | {avg_kraken_net_pct:.4f} | {coinbase_cum_net_pct:.2f} | {kraken_cum_net_pct:.2f} | {coinbase_win_rate_pct:.2f} | {kraken_win_rate_pct:.2f} |".format(
                name=name,
                **stat,
            )
        )
    lines.extend(["", "## Top One-Per-Time Rows", ""])
    lines.extend(
        [
            "| Rank | Time | Product | Tail | Fast | Combined | Gross % | Coinbase Net % | Setup |",
            "| ---: | --- | --- | ---: | ---: | ---: | ---: | ---: | --- |",
        ]
    )
    for idx, row in enumerate(rows[:30], start=1):
        setup = f"{row.get('archetype')}/{row.get('trigger')}/{row.get('confirmation')}/{row.get('exit')}"
        lines.append(
            "| {idx} | {time} | {product_id} | {tail_prob:.4f} | {fast_green_prob:.4f} | {combined_prob:.4f} | {gross_pct:.4f} | {net_pct:.4f} | {setup} |".format(
                idx=idx,
                setup=setup,
                **row,
            )
        )
    md_path.parent.mkdir(parents=True, exist_ok=True)
    md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    args = parse_args()
    payload = build(args)
    print(json.dumps({"json_path": str(Path(args.json_path).resolve()), "md_path": str(Path(args.md_path).resolve())}, indent=2))


if __name__ == "__main__":
    main()
