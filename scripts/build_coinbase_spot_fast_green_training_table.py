#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np

from build_coinbase_spot_fee_survival_training_table import (
    build_signal_mask,
    parse_candles_with_time,
    pct_change,
    rolling_max,
    rolling_mean,
    rolling_min,
    rolling_std,
    select_pairs,
)
from run_coinbase_spot_gpu_foundry import candle_files, build_variant_rows, load_spreads, load_theories


ROOT = Path(__file__).resolve().parent.parent
REPORTS = ROOT / "reports"
MATRIX_PATH = REPORTS / "coinbase_spot_gpu_foundry_product_matrix.csv"
CSV_PATH = REPORTS / "coinbase_spot_fast_green_training_table.csv"
JSON_PATH = REPORTS / "coinbase_spot_fast_green_training_table.json"
MD_PATH = REPORTS / "coinbase_spot_fast_green_training_table.md"


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def to_bool_count(rows: list[dict[str, Any]], key: str) -> int:
    return sum(1 for row in rows if bool(row.get(key)))


def build_table(args: argparse.Namespace) -> dict[str, Any]:
    theories = load_theories(int(args.max_variants))
    variants = {int(row.get("id") or 0): row for row in build_variant_rows(theories)}
    pairs = select_pairs(args)
    spreads = load_spreads()
    needed: dict[str, set[int]] = {}
    for row in pairs.to_dict("records"):
        variant_id = int(row["variant_id"])
        if variant_id in variants:
            needed.setdefault(str(row["product_id"]), set()).add(variant_id)

    files = candle_files(granularity=str(args.granularity), days=int(args.days), max_products=int(args.max_products))
    file_by_product = {path.name.split("_USD_", 1)[0].replace("_", "-") + "-USD": path for path in files}
    rows: list[dict[str, Any]] = []
    skipped_products: list[str] = []
    max_horizon = 2
    for product_id, variant_ids in sorted(needed.items()):
        path = file_by_product.get(product_id)
        if not path:
            skipped_products.append(product_id)
            continue
        parsed = parse_candles_with_time(path)
        if not parsed:
            skipped_products.append(product_id)
            continue
        candles = np.asarray(parsed["candles"], dtype=float)
        timestamps = candles[:, 0].astype(int)
        open_ = candles[:, 1]
        high = candles[:, 2]
        low = candles[:, 3]
        close = candles[:, 4]
        volume = candles[:, 5]
        if len(close) <= 90:
            skipped_products.append(product_id)
            continue
        spread_bps = min(max(0.0, spreads.get(product_id, float(args.default_spread_bps))), float(args.max_spread_bps))
        fee_bps_round_trip = (2.0 * float(args.fee_bps_per_side)) + spread_bps
        fee_pct = fee_bps_round_trip / 10_000.0
        abs_ret_1 = np.abs(np.r_[0.0, np.diff(close) / np.maximum(close[:-1], 1e-12)])

        for variant_id in sorted(variant_ids):
            variant = variants.get(variant_id)
            if not variant:
                continue
            valid_t = len(close) - max_horizon
            signal = build_signal_mask(variant, open_, high, low, close, volume, valid_t)
            signal_indices = np.flatnonzero(signal)
            for idx in signal_indices:
                if int(args.max_rows) > 0 and len(rows) >= int(args.max_rows):
                    break
                entry = float(close[idx])
                high_5m = float(high[idx + 1])
                low_5m = float(low[idx + 1])
                close_5m = float(close[idx + 1])
                high_10m = float(np.max(high[idx + 1 : idx + 3]))
                low_10m = float(np.min(low[idx + 1 : idx + 3]))
                close_10m = float(close[idx + 2])
                mfe_5m_net_pct = (((high_5m / entry) - 1.0) - fee_pct) * 100.0
                mfe_10m_net_pct = (((high_10m / entry) - 1.0) - fee_pct) * 100.0
                close_5m_net_pct = (((close_5m / entry) - 1.0) - fee_pct) * 100.0
                close_10m_net_pct = (((close_10m / entry) - 1.0) - fee_pct) * 100.0
                mae_5m_pct = ((low_5m / entry) - 1.0) * 100.0
                mae_10m_pct = ((low_10m / entry) - 1.0) * 100.0
                high_12 = rolling_max(high, idx, 12)
                low_12 = rolling_min(low, idx, 12)
                range_12 = max(high_12 - low_12, 1e-12)
                median_abs_12 = float(np.median(abs_ret_1[max(0, idx - 11) : idx + 1])) if idx > 0 else 0.0
                row = {
                    "product_id": product_id,
                    "variant_id": variant_id,
                    "time": int(timestamps[idx]),
                    "hour_utc": int(datetime.fromtimestamp(int(timestamps[idx]), tz=timezone.utc).hour),
                    "archetype": str(variant.get("archetype") or ""),
                    "trigger": str(variant.get("trigger") or ""),
                    "confirmation": str(variant.get("confirmation") or ""),
                    "exit": str(variant.get("exit") or ""),
                    "sizing": str(variant.get("sizing") or ""),
                    "trigger_mode": str(variant.get("trigger_mode") or ""),
                    "lookback": int(variant["lookback"]),
                    "trigger_bps": float(variant["trigger_bps"]),
                    "target_pct": round(float(variant["target_pct"]) * 100.0, 4),
                    "stop_pct": round(float(variant["stop_pct"]) * 100.0, 4),
                    "hold_bars": int(variant["hold_bars"]),
                    "spread_bps_proxy": round(spread_bps, 4),
                    "fee_bps_round_trip": round(fee_bps_round_trip, 4),
                    "ret_1_bps": round(pct_change(close, idx, 1) * 10_000.0, 6),
                    "ret_3_bps": round(pct_change(close, idx, 3) * 10_000.0, 6),
                    "ret_6_bps": round(pct_change(close, idx, 6) * 10_000.0, 6),
                    "ret_12_bps": round(pct_change(close, idx, 12) * 10_000.0, 6),
                    "range_bps": round(((high[idx] / max(low[idx], 1e-12)) - 1.0) * 10_000.0, 6),
                    "body_bps": round(((close[idx] / max(open_[idx], 1e-12)) - 1.0) * 10_000.0, 6),
                    "close_location": round(float(np.clip((close[idx] - low[idx]) / max(high[idx] - low[idx], 1e-12), 0.0, 1.0)), 6),
                    "volume_mult_12": round(volume[idx] / max(rolling_mean(volume, idx, 12), 1e-12), 6),
                    "volatility_12_bps": round(rolling_std(abs_ret_1, idx, 12) * 10_000.0, 6),
                    "accel_vs_median_abs_12": round(abs(pct_change(close, idx, 1)) / max(median_abs_12, 1e-12), 6),
                    "dist_from_12_high_bps": round(((close[idx] / max(high_12, 1e-12)) - 1.0) * 10_000.0, 6),
                    "dist_from_12_low_bps": round(((close[idx] / max(low_12, 1e-12)) - 1.0) * 10_000.0, 6),
                    "position_in_12_range": round(float(np.clip((close[idx] - low_12) / range_12, 0.0, 1.0)), 6),
                    "net_mfe_5m_pct": round(mfe_5m_net_pct, 6),
                    "net_mfe_10m_pct": round(mfe_10m_net_pct, 6),
                    "net_close_5m_pct": round(close_5m_net_pct, 6),
                    "net_close_10m_pct": round(close_10m_net_pct, 6),
                    "mae_5m_pct": round(mae_5m_pct, 6),
                    "mae_10m_pct": round(mae_10m_pct, 6),
                    "fast_green_5m": mfe_5m_net_pct >= 0.0,
                    "fast_green_10m": mfe_10m_net_pct >= 0.0,
                    "fast_pay_1pct_5m": mfe_5m_net_pct >= 1.0,
                    "fast_pay_1pct_10m": mfe_10m_net_pct >= 1.0,
                    "fast_pay_2pct_5m": mfe_5m_net_pct >= 2.0,
                    "fast_pay_2pct_10m": mfe_10m_net_pct >= 2.0,
                }
                rows.append(row)
            if int(args.max_rows) > 0 and len(rows) >= int(args.max_rows):
                break
    rows.sort(key=lambda row: (row["product_id"], row["time"], row["variant_id"]))
    return {
        "summary": {
            "generated_at": utc_now_iso(),
            "mode": "coinbase_spot_fast_green_training_table",
            "source_matrix": str(args.matrix_path),
            "rows": len(rows),
            "selected_pairs": len(pairs),
            "selected_products": len(needed),
            "skipped_products": skipped_products[:50],
            "fee_bps_per_side": float(args.fee_bps_per_side),
            "horizon": "5m_and_10m_from_entry",
            "leadership_read": [
                "This table labels whether a candidate manifests fee-paid profit in the next one or two five-minute candles.",
                "It targets the money-velocity question directly: enter, go green fast, lock/bank, or kill.",
                "Labels use future high as reachability, not guaranteed fills; use as a model target and then forward-shadow it.",
            ],
        },
        "rows": rows,
    }


def write_outputs(payload: dict[str, Any]) -> None:
    rows = payload["rows"]
    summary = payload["summary"]
    JSON_PATH.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    if rows:
        with CSV_PATH.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=list(rows[0].keys()))
            writer.writeheader()
            writer.writerows(rows)
    else:
        CSV_PATH.write_text("", encoding="utf-8")
    lines = [
        "# Coinbase Spot Fast-Green Training Table",
        "",
        f"- Generated: `{summary['generated_at']}`",
        f"- Rows: `{summary['rows']}`",
        f"- Selected product/setup pairs: `{summary['selected_pairs']}`",
        f"- Selected products: `{summary['selected_products']}`",
        f"- Fee bps per side: `{summary['fee_bps_per_side']}`",
        f"- Fast green 5m: `{to_bool_count(rows, 'fast_green_5m')}`",
        f"- Fast green 10m: `{to_bool_count(rows, 'fast_green_10m')}`",
        f"- Fast +1% 10m: `{to_bool_count(rows, 'fast_pay_1pct_10m')}`",
        f"- Fast +2% 10m: `{to_bool_count(rows, 'fast_pay_2pct_10m')}`",
        "",
        "## Read",
        "",
    ]
    lines.extend([f"- {item}" for item in summary["leadership_read"]])
    lines.extend(
        [
            "",
            "## Top 10m Manifest Rows",
            "",
            "| Rank | Product | Variant | Time | Setup | Net MFE 5m % | Net MFE 10m % | Net Close 10m % | MAE 10m % |",
            "| ---: | --- | ---: | ---: | --- | ---: | ---: | ---: | ---: |",
        ]
    )
    for rank, row in enumerate(sorted(rows, key=lambda item: item["net_mfe_10m_pct"], reverse=True)[:30], start=1):
        setup = f"{row['trigger']} / {row['confirmation']} / {row['exit']}"
        lines.append(
            f"| {rank} | {row['product_id']} | {row['variant_id']} | {row['time']} | {setup} | {row['net_mfe_5m_pct']:.4f} | {row['net_mfe_10m_pct']:.4f} | {row['net_close_10m_pct']:.4f} | {row['mae_10m_pct']:.4f} |"
        )
    MD_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build Coinbase spot fast-green ML training rows.")
    parser.add_argument("--matrix-path", default=str(MATRIX_PATH))
    parser.add_argument("--granularity", default="FIVE_MINUTE")
    parser.add_argument("--days", type=int, default=7)
    parser.add_argument("--max-products", type=int, default=0)
    parser.add_argument("--max-variants", type=int, default=5000)
    parser.add_argument("--min-pair-signals", type=int, default=2)
    parser.add_argument("--min-pair-avg-net-pct", type=float, default=0.0)
    parser.add_argument("--negative-pair-multiplier", type=int, default=4)
    parser.add_argument("--max-pairs", type=int, default=0)
    parser.add_argument("--max-rows", type=int, default=250000)
    parser.add_argument("--fee-bps-per-side", type=float, default=120.0)
    parser.add_argument("--default-spread-bps", type=float, default=25.0)
    parser.add_argument("--max-spread-bps", type=float, default=150.0)
    return parser.parse_args()


def main() -> int:
    payload = build_table(parse_args())
    write_outputs(payload)
    print(json.dumps({"csv_path": str(CSV_PATH), "json_path": str(JSON_PATH), "md_path": str(MD_PATH), **payload["summary"]}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
