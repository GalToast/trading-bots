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
from sklearn.model_selection import GroupShuffleSplit, train_test_split
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder, StandardScaler


ROOT = Path(__file__).resolve().parent.parent
REPORTS = ROOT / "reports"
MODEL_DIR = REPORTS / "models"
MATRIX_PATH = REPORTS / "coinbase_spot_gpu_foundry_product_matrix.csv"
JSON_PATH = REPORTS / "coinbase_spot_fee_survival_model_report.json"
MD_PATH = REPORTS / "coinbase_spot_fee_survival_model_report.md"
MODEL_PATH = MODEL_DIR / "coinbase_spot_fee_survival_model.joblib"


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def to_bool_series(series: pd.Series) -> pd.Series:
    return series.astype(str).str.lower().isin({"true", "1", "yes"})


def build_model(scale_pos_weight: float) -> Any:
    try:
        from lightgbm import LGBMClassifier

        return LGBMClassifier(
            n_estimators=450,
            learning_rate=0.035,
            num_leaves=31,
            min_child_samples=20,
            subsample=0.85,
            colsample_bytree=0.85,
            reg_alpha=0.2,
            reg_lambda=0.8,
            scale_pos_weight=scale_pos_weight,
            random_state=42,
            verbosity=-1,
        )
    except Exception:
        from sklearn.ensemble import HistGradientBoostingClassifier

        return HistGradientBoostingClassifier(
            learning_rate=0.04,
            max_iter=350,
            l2_regularization=0.05,
            random_state=42,
        )


def pick_product_holdout(df: pd.DataFrame, *, test_size: float, seed: int) -> tuple[np.ndarray, np.ndarray, str]:
    products = df["product_id"].astype(str)
    y = df["label"].astype(int).to_numpy()
    for offset in range(50):
        splitter = GroupShuffleSplit(n_splits=1, test_size=test_size, random_state=seed + offset)
        train_idx, test_idx = next(splitter.split(df, y, groups=products))
        if y[train_idx].sum() > 0 and y[test_idx].sum() > 0:
            return train_idx, test_idx, "product_holdout"
    train_idx, test_idx = train_test_split(
        np.arange(len(df)),
        test_size=test_size,
        random_state=seed,
        stratify=y if y.sum() and y.sum() < len(y) else None,
    )
    return np.asarray(train_idx), np.asarray(test_idx), "row_stratified_fallback"


def threshold_stats(df: pd.DataFrame, probabilities: np.ndarray, thresholds: list[float]) -> list[dict[str, Any]]:
    out = []
    y = df["label"].astype(int).to_numpy()
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
                    "cumulative_net_pct": 0.0,
                    "avg_net_pct": 0.0,
                    "worst_net_pct": 0.0,
                }
            )
            continue
        precision, recall, _, _ = precision_recall_fscore_support(y, allowed.astype(int), average="binary", zero_division=0)
        allowed_df = df.loc[allowed]
        out.append(
            {
                "threshold": threshold,
                "allowed": count,
                "precision": round(float(precision), 6),
                "recall": round(float(recall), 6),
                "cumulative_net_pct": round(float(allowed_df["cumulative_net_pct"].sum()), 6),
                "avg_net_pct": round(float(allowed_df["avg_net_pct"].mean()), 6),
                "worst_net_pct": round(float(allowed_df["worst_net_pct"].min()), 6),
            }
        )
    return out


def train(args: argparse.Namespace) -> dict[str, Any]:
    df = pd.read_csv(args.matrix_path)
    if df.empty:
        raise SystemExit(f"No matrix rows found at {args.matrix_path}")
    df["survived_fees"] = to_bool_series(df["survived_fees"])
    df = df[df["signals"].astype(float) >= float(args.min_signals)].copy()
    df["label"] = (
        df["survived_fees"]
        & (df["avg_net_pct"].astype(float) >= float(args.min_avg_net_pct))
        & (df["cumulative_net_pct"].astype(float) >= float(args.min_cumulative_net_pct))
    ).astype(int)
    if int(df["label"].sum()) <= 1:
        raise SystemExit("Not enough positive labels for training after filters.")

    categorical = [
        "product_id",
        "archetype",
        "trigger",
        "confirmation",
        "exit",
        "sizing",
        "trigger_mode",
    ]
    numeric = [
        "duplicate_theory_count",
        "lookback",
        "trigger_bps",
        "target_pct",
        "stop_pct",
        "hold_bars",
        "signals",
        "wins",
        "win_rate_pct",
        "avg_gross_pct",
        "worst_net_pct",
        "spread_bps_proxy",
    ]
    for column in categorical:
        df[column] = df[column].astype(str).fillna("")
    for column in numeric:
        df[column] = pd.to_numeric(df[column], errors="coerce").fillna(0.0)

    train_idx, test_idx, split_mode = pick_product_holdout(df, test_size=float(args.test_size), seed=int(args.seed))
    train_df = df.iloc[train_idx].copy()
    test_df = df.iloc[test_idx].copy()
    y_train = train_df["label"].astype(int)
    y_test = test_df["label"].astype(int)
    scale_pos_weight = max(1.0, float((len(y_train) - int(y_train.sum())) / max(1, int(y_train.sum()))))
    preprocessor = ColumnTransformer(
        transformers=[
            ("cat", OneHotEncoder(handle_unknown="ignore", min_frequency=3), categorical),
            ("num", StandardScaler(), numeric),
        ]
    )
    model = Pipeline(
        steps=[
            ("features", preprocessor),
            ("model", build_model(scale_pos_weight)),
        ]
    )
    model.fit(train_df[categorical + numeric], y_train)
    train_prob = model.predict_proba(train_df[categorical + numeric])[:, 1]
    test_prob = model.predict_proba(test_df[categorical + numeric])[:, 1]

    def score_frame(frame: pd.DataFrame, y: pd.Series, prob: np.ndarray) -> dict[str, Any]:
        roc = roc_auc_score(y, prob) if len(set(y.astype(int))) > 1 else None
        ap = average_precision_score(y, prob) if len(set(y.astype(int))) > 1 else None
        stats = threshold_stats(frame.reset_index(drop=True), prob, thresholds=[0.50, 0.60, 0.70, 0.80, 0.90])
        return {
            "rows": len(frame),
            "positives": int(y.sum()),
            "positive_rate_pct": round((float(y.sum()) / max(1, len(y))) * 100.0, 6),
            "roc_auc": round(float(roc), 6) if roc is not None else None,
            "average_precision": round(float(ap), 6) if ap is not None else None,
            "thresholds": stats,
        }

    report = {
        "generated_at": utc_now_iso(),
        "mode": "coinbase_spot_fee_survival_model",
        "source": str(args.matrix_path),
        "model_path": str(args.model_path),
        "split_mode": split_mode,
        "parameters": {
            "min_signals": float(args.min_signals),
            "min_avg_net_pct": float(args.min_avg_net_pct),
            "min_cumulative_net_pct": float(args.min_cumulative_net_pct),
            "test_size": float(args.test_size),
            "seed": int(args.seed),
            "scale_pos_weight": round(scale_pos_weight, 6),
        },
        "leadership_read": [
            "This is a first-pass fee-survival classifier trained on product/setup aggregate rows, not a live trading oracle.",
            "Product-holdout validation is intentionally harsh; weak holdout lift means pockets are likely product-specific or tiny-sample artifacts.",
            "Use the model only as research until a timestamp-level candidate table and forward shadow proof show lift.",
        ],
        "features": {"categorical": categorical, "numeric": numeric},
        "train": score_frame(train_df, y_train, train_prob),
        "test": score_frame(test_df, y_test, test_prob),
    }
    model_payload = {
        "model": model,
        "categorical": categorical,
        "numeric": numeric,
        "report": report,
    }
    Path(args.model_path).parent.mkdir(parents=True, exist_ok=True)
    joblib.dump(model_payload, args.model_path)
    return report


def write_markdown(report: dict[str, Any], path: Path) -> None:
    lines = [
        "# Coinbase Spot Fee Survival Model",
        "",
        f"- Generated: `{report['generated_at']}`",
        f"- Source: `{report['source']}`",
        f"- Model: `{report['model_path']}`",
        f"- Split mode: `{report['split_mode']}`",
        "",
        "## Read",
        "",
    ]
    lines.extend([f"- {line}" for line in report["leadership_read"]])
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
                f"- ROC AUC: `{data['roc_auc']}`",
                f"- Average precision: `{data['average_precision']}`",
                "",
                "| Threshold | Allowed | Precision | Recall | Cum Net % | Avg Net % | Worst Net % |",
                "| ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
            ]
        )
        for row in data["thresholds"]:
            lines.append(
                "| {threshold:.2f} | {allowed} | {precision:.4f} | {recall:.4f} | {cumulative_net_pct:.4f} | {avg_net_pct:.4f} | {worst_net_pct:.4f} |".format(
                    **row
                )
            )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Train a first-pass Coinbase spot fee-survival classifier from foundry matrix rows.")
    parser.add_argument("--matrix-path", default=str(MATRIX_PATH))
    parser.add_argument("--json-path", default=str(JSON_PATH))
    parser.add_argument("--md-path", default=str(MD_PATH))
    parser.add_argument("--model-path", default=str(MODEL_PATH))
    parser.add_argument("--min-signals", type=float, default=2.0)
    parser.add_argument("--min-avg-net-pct", type=float, default=0.0)
    parser.add_argument("--min-cumulative-net-pct", type=float, default=0.0)
    parser.add_argument("--test-size", type=float, default=0.25)
    parser.add_argument("--seed", type=int, default=42)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    report = train(args)
    Path(args.json_path).write_text(json.dumps(report, indent=2), encoding="utf-8")
    write_markdown(report, Path(args.md_path))
    print(json.dumps({"json_path": args.json_path, "md_path": args.md_path, "model_path": args.model_path, "test": report["test"]}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
