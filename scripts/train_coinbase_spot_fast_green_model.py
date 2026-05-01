#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import joblib
import numpy as np
import pandas as pd
from sklearn.compose import ColumnTransformer
from sklearn.metrics import average_precision_score, precision_recall_fscore_support, roc_auc_score
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder, StandardScaler


ROOT = Path(__file__).resolve().parent.parent
REPORTS = ROOT / "reports"
TABLE_PATH = REPORTS / "coinbase_spot_fast_green_training_table.csv"
JSON_PATH = REPORTS / "coinbase_spot_fast_green_model_report.json"
MD_PATH = REPORTS / "coinbase_spot_fast_green_model_report.md"
MODEL_PATH = REPORTS / "models" / "coinbase_spot_fast_green_model.joblib"
DEFAULT_LABEL = "fast_pay_1pct_10m"


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def build_model(scale_pos_weight: float) -> Any:
    try:
        from lightgbm import LGBMClassifier

        return LGBMClassifier(
            n_estimators=800,
            learning_rate=0.025,
            num_leaves=63,
            min_child_samples=70,
            subsample=0.85,
            colsample_bytree=0.85,
            reg_alpha=0.45,
            reg_lambda=1.35,
            scale_pos_weight=scale_pos_weight,
            random_state=42,
            verbosity=-1,
        )
    except Exception:
        from sklearn.ensemble import HistGradientBoostingClassifier

        return HistGradientBoostingClassifier(
            learning_rate=0.03,
            max_iter=500,
            l2_regularization=0.12,
            random_state=42,
        )


def threshold_stats(df: pd.DataFrame, probabilities: np.ndarray, thresholds: list[float]) -> list[dict[str, Any]]:
    y = df["label"].astype(int).to_numpy()
    out = []
    for threshold in thresholds:
        allowed = probabilities >= threshold
        count = int(allowed.sum())
        if count == 0:
            out.append(
                {
                    "threshold": threshold,
                    "allowed": 0,
                    "precision": 0.0,
                    "recall": 0.0,
                    "label_hits": 0,
                    "cumulative_mfe_5m_pct": 0.0,
                    "avg_mfe_5m_pct": 0.0,
                    "cumulative_mfe_10m_pct": 0.0,
                    "avg_mfe_10m_pct": 0.0,
                    "avg_close_10m_pct": 0.0,
                    "worst_mae_10m_pct": 0.0,
                    "avg_mae_10m_pct": 0.0,
                    "best_mfe_10m_pct": 0.0,
                }
            )
            continue
        precision, recall, _, _ = precision_recall_fscore_support(y, allowed.astype(int), average="binary", zero_division=0)
        selected = df.loc[allowed]
        out.append(
            {
                "threshold": threshold,
                "allowed": count,
                "precision": round(float(precision), 6),
                "recall": round(float(recall), 6),
                "label_hits": int(selected["label"].sum()),
                "cumulative_mfe_5m_pct": round(float(selected["net_mfe_5m_pct"].sum()), 6),
                "avg_mfe_5m_pct": round(float(selected["net_mfe_5m_pct"].mean()), 6),
                "cumulative_mfe_10m_pct": round(float(selected["net_mfe_10m_pct"].sum()), 6),
                "avg_mfe_10m_pct": round(float(selected["net_mfe_10m_pct"].mean()), 6),
                "avg_close_10m_pct": round(float(selected["net_close_10m_pct"].mean()), 6),
                "worst_mae_10m_pct": round(float(selected["mae_10m_pct"].min()), 6),
                "avg_mae_10m_pct": round(float(selected["mae_10m_pct"].mean()), 6),
                "best_mfe_10m_pct": round(float(selected["net_mfe_10m_pct"].max()), 6),
            }
        )
    return out


def score_frame(frame: pd.DataFrame, y: pd.Series, probabilities: np.ndarray) -> dict[str, Any]:
    roc = roc_auc_score(y, probabilities) if len(set(y.astype(int))) > 1 else None
    ap = average_precision_score(y, probabilities) if len(set(y.astype(int))) > 1 else None
    return {
        "rows": int(len(frame)),
        "positives": int(y.sum()),
        "positive_rate_pct": round((float(y.sum()) / max(1, len(y))) * 100.0, 6),
        "all_rows_cumulative_mfe_10m_pct": round(float(frame["net_mfe_10m_pct"].sum()), 6),
        "all_rows_avg_mfe_10m_pct": round(float(frame["net_mfe_10m_pct"].mean()), 6),
        "all_rows_avg_close_10m_pct": round(float(frame["net_close_10m_pct"].mean()), 6),
        "all_rows_avg_mae_10m_pct": round(float(frame["mae_10m_pct"].mean()), 6),
        "roc_auc": round(float(roc), 6) if roc is not None else None,
        "average_precision": round(float(ap), 6) if ap is not None else None,
        "thresholds": threshold_stats(
            frame.reset_index(drop=True),
            probabilities,
            [0.05, 0.10, 0.15, 0.20, 0.30, 0.40, 0.50, 0.60, 0.70, 0.80, 0.90, 0.95],
        ),
    }


def train(args: argparse.Namespace) -> dict[str, Any]:
    df = pd.read_csv(args.table_path)
    if df.empty:
        raise SystemExit(f"No rows found at {args.table_path}")
    if args.label not in df.columns:
        raise SystemExit(f"Label column not found: {args.label}")
    df["label"] = df[args.label].astype(str).str.lower().isin({"true", "1", "yes"}).astype(int)
    df["time"] = pd.to_numeric(df["time"], errors="coerce").fillna(0).astype(int)
    df = df.sort_values(["time", "product_id", "variant_id"]).reset_index(drop=True)

    categorical = ["product_id", "archetype", "trigger", "confirmation", "exit", "sizing", "trigger_mode"]
    numeric = [
        "hour_utc",
        "lookback",
        "trigger_bps",
        "target_pct",
        "stop_pct",
        "hold_bars",
        "spread_bps_proxy",
        "fee_bps_round_trip",
        "ret_1_bps",
        "ret_3_bps",
        "ret_6_bps",
        "ret_12_bps",
        "range_bps",
        "body_bps",
        "close_location",
        "volume_mult_12",
        "volatility_12_bps",
        "accel_vs_median_abs_12",
        "dist_from_12_high_bps",
        "dist_from_12_low_bps",
        "position_in_12_range",
    ]
    outcome_columns = [
        "net_mfe_5m_pct",
        "net_mfe_10m_pct",
        "net_close_5m_pct",
        "net_close_10m_pct",
        "mae_5m_pct",
        "mae_10m_pct",
    ]
    for column in categorical:
        df[column] = df[column].astype(str).fillna("")
    for column in numeric + outcome_columns:
        df[column] = pd.to_numeric(df[column], errors="coerce").replace([np.inf, -np.inf], np.nan).fillna(0.0)

    split_at = int(len(df) * (1.0 - float(args.test_size)))
    split_at = max(1, min(split_at, len(df) - 1))
    train_df = df.iloc[:split_at].copy()
    test_df = df.iloc[split_at:].copy()
    y_train = train_df["label"].astype(int)
    y_test = test_df["label"].astype(int)
    if int(y_train.sum()) == 0 or int(y_test.sum()) == 0:
        raise SystemExit("Chronological split has no positives on one side; rebuild table or adjust split.")

    scale_pos_weight = max(1.0, float((len(y_train) - int(y_train.sum())) / max(1, int(y_train.sum()))))
    preprocessor = ColumnTransformer(
        transformers=[
            ("cat", OneHotEncoder(handle_unknown="ignore", min_frequency=20), categorical),
            ("num", StandardScaler(), numeric),
        ]
    )
    model = Pipeline([("features", preprocessor), ("model", build_model(scale_pos_weight))])
    model.fit(train_df[categorical + numeric], y_train)
    train_prob = model.predict_proba(train_df[categorical + numeric])[:, 1]
    test_prob = model.predict_proba(test_df[categorical + numeric])[:, 1]

    report = {
        "generated_at": utc_now_iso(),
        "mode": "coinbase_spot_fast_green_model",
        "source": str(args.table_path),
        "model_path": str(args.model_path),
        "label": args.label,
        "split_mode": "chronological",
        "parameters": {
            "test_size": float(args.test_size),
            "scale_pos_weight": round(scale_pos_weight, 6),
            "train_time_min": int(train_df["time"].min()),
            "train_time_max": int(train_df["time"].max()),
            "test_time_min": int(test_df["time"].min()),
            "test_time_max": int(test_df["time"].max()),
        },
        "leadership_read": [
            "This model targets fast fee-paid manifestation: net-positive or +1/+2% reachability inside five to ten minutes.",
            "MFE labels use the future candle high, so they identify reachable moves rather than guaranteed fills; forward shadow execution must prove capture quality.",
            "The report is most useful for choosing candidate gates and restricted universes, not for declaring live profitability by itself.",
        ],
        "features": {"categorical": categorical, "numeric": numeric},
        "train": score_frame(train_df, y_train, train_prob),
        "test": score_frame(test_df, y_test, test_prob),
    }
    payload = {"model": model, "categorical": categorical, "numeric": numeric, "report": report, "label": args.label}
    Path(args.model_path).parent.mkdir(parents=True, exist_ok=True)
    joblib.dump(payload, args.model_path)
    return report


def write_markdown(report: dict[str, Any], path: Path) -> None:
    lines = [
        "# Coinbase Spot Fast-Green Model",
        "",
        f"- Generated: `{report['generated_at']}`",
        f"- Source: `{report['source']}`",
        f"- Model: `{report['model_path']}`",
        f"- Label: `{report['label']}`",
        f"- Split: `{report['split_mode']}`",
        "",
        "## Read",
        "",
    ]
    lines.extend([f"- {item}" for item in report["leadership_read"]])
    for section in ("train", "test"):
        data = report[section]
        lines.extend(
            [
                "",
                f"## {section.title()}",
                "",
                f"- Rows: `{data['rows']}`",
                f"- Positives: `{data['positives']}`",
                f"- Positive rate pct: `{data['positive_rate_pct']}`",
                f"- All rows cumulative MFE 10m pct: `{data['all_rows_cumulative_mfe_10m_pct']}`",
                f"- All rows avg MFE 10m pct: `{data['all_rows_avg_mfe_10m_pct']}`",
                f"- All rows avg close 10m pct: `{data['all_rows_avg_close_10m_pct']}`",
                f"- All rows avg MAE 10m pct: `{data['all_rows_avg_mae_10m_pct']}`",
                f"- ROC AUC: `{data['roc_auc']}`",
                f"- Average precision: `{data['average_precision']}`",
                "",
                "| Threshold | Allowed | Hits | Precision | Recall | Cum MFE 10m % | Avg MFE 10m % | Avg Close 10m % | Worst MAE 10m % | Best MFE 10m % |",
                "| ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
            ]
        )
        for row in data["thresholds"]:
            lines.append(
                "| {threshold:.2f} | {allowed} | {label_hits} | {precision:.4f} | {recall:.4f} | {cumulative_mfe_10m_pct:.4f} | {avg_mfe_10m_pct:.4f} | {avg_close_10m_pct:.4f} | {worst_mae_10m_pct:.4f} | {best_mfe_10m_pct:.4f} |".format(
                    **row
                )
            )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Train a Coinbase spot fast-green model for 5m/10m fee-paid momentum.")
    parser.add_argument("--table-path", default=str(TABLE_PATH))
    parser.add_argument("--json-path", default=str(JSON_PATH))
    parser.add_argument("--md-path", default=str(MD_PATH))
    parser.add_argument("--model-path", default=str(MODEL_PATH))
    parser.add_argument("--label", default=DEFAULT_LABEL)
    parser.add_argument("--test-size", type=float, default=0.25)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    report = train(args)
    Path(args.json_path).write_text(json.dumps(report, indent=2), encoding="utf-8")
    write_markdown(report, Path(args.md_path))
    print(
        json.dumps(
            {
                "json_path": args.json_path,
                "md_path": args.md_path,
                "model_path": args.model_path,
                "label": report["label"],
                "test": report["test"],
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
