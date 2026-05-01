#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from statistics import mean
from typing import Any


ROOT = Path(__file__).resolve().parent.parent
DEFAULT_EVENT_PATH = ROOT / "reports" / "cache" / "kraken_l2_wall_tape_honey_billy_ldo_xion_rave_iotx_quick.jsonl"
DEFAULT_SUMMARY_PATH = ROOT / "reports" / "kraken_l2_imbalance_forward_replay_summary.json"
DEFAULT_ROWS_PATH = ROOT / "reports" / "kraken_l2_imbalance_forward_replay_rows.jsonl"


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def to_float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None:
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def load_events(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if not path.exists():
        return rows
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if "error" in row:
            continue
        if to_float(row.get("bid")) <= 0.0 or to_float(row.get("ask")) <= 0.0:
            continue
        rows.append(row)
    rows.sort(key=lambda row: (str(row.get("product_id")), to_float(row.get("ts_epoch"))))
    return rows


def bps_change(new: float, old: float) -> float:
    return ((new / old) - 1.0) * 10000.0 if new > 0.0 and old > 0.0 else 0.0


def first_future_at_or_after(rows: list[dict[str, Any]], start_index: int, target_ts: float) -> dict[str, Any] | None:
    for row in rows[start_index + 1 :]:
        if to_float(row.get("ts_epoch")) >= target_ts:
            return row
    return None


def future_window(rows: list[dict[str, Any]], start_index: int, end_ts: float) -> list[dict[str, Any]]:
    window: list[dict[str, Any]] = []
    for row in rows[start_index + 1 :]:
        ts = to_float(row.get("ts_epoch"))
        if ts > end_ts:
            break
        window.append(row)
    return window


def label_rows(
    events: list[dict[str, Any]],
    *,
    horizon_seconds: float,
    fee_bps: float,
    min_net_bps: float,
) -> list[dict[str, Any]]:
    by_product: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in events:
        by_product[str(row.get("product_id"))].append(row)

    labels: list[dict[str, Any]] = []
    for product_id, rows in by_product.items():
        rows.sort(key=lambda row: to_float(row.get("ts_epoch")))
        for index, row in enumerate(rows):
            start_ts = to_float(row.get("ts_epoch"))
            entry_ask = to_float(row.get("ask"))
            entry_bid = to_float(row.get("bid"))
            horizon_row = first_future_at_or_after(rows, index, start_ts + horizon_seconds)
            window = future_window(rows, index, start_ts + horizon_seconds)
            if horizon_row is None or not window:
                continue
            horizon_bid = to_float(horizon_row.get("bid"))
            horizon_ask = to_float(horizon_row.get("ask"))
            future_bids = [to_float(item.get("bid")) for item in window if to_float(item.get("bid")) > 0.0]
            future_asks = [to_float(item.get("ask")) for item in window if to_float(item.get("ask")) > 0.0]
            max_future_bid = max(future_bids, default=horizon_bid)
            min_future_bid = min(future_bids, default=horizon_bid)
            max_future_ask = max(future_asks, default=horizon_ask)
            min_future_ask = min(future_asks, default=horizon_ask)
            horizon_taker_net_bps = bps_change(horizon_bid, entry_ask) - fee_bps
            mfe_taker_net_bps = bps_change(max_future_bid, entry_ask) - fee_bps
            labels.append(
                {
                    "ts_utc": row.get("ts_utc"),
                    "ts_epoch": start_ts,
                    "product_id": product_id,
                    "horizon_seconds": horizon_seconds,
                    "spread_bps": to_float(row.get("spread_bps")),
                    "l10_imbalance_ratio": to_float(row.get("l10_imbalance_ratio")),
                    "l10_obi": to_float(row.get("l10_obi")),
                    "l10_bid_usd": to_float(row.get("l10_bid_usd")),
                    "l10_ask_usd": to_float(row.get("l10_ask_usd")),
                    "entry_bid": entry_bid,
                    "entry_ask": entry_ask,
                    "horizon_bid": horizon_bid,
                    "horizon_ask": horizon_ask,
                    "horizon_bid_move_bps": round(bps_change(horizon_bid, entry_bid), 6),
                    "horizon_ask_move_bps": round(bps_change(horizon_ask, entry_ask), 6),
                    "horizon_taker_net_bps": round(horizon_taker_net_bps, 6),
                    "mfe_taker_net_bps": round(mfe_taker_net_bps, 6),
                    "mae_bid_bps": round(bps_change(min_future_bid, entry_bid), 6),
                    "max_future_bid_bps": round(bps_change(max_future_bid, entry_bid), 6),
                    "max_future_ask_bps": round(bps_change(max_future_ask, entry_ask), 6),
                    "min_future_ask_bps": round(bps_change(min_future_ask, entry_ask), 6),
                    "fee_clear_horizon": horizon_taker_net_bps >= min_net_bps,
                    "fee_clear_mfe": mfe_taker_net_bps >= min_net_bps,
                }
            )
    return labels


def bucket_name(row: dict[str, Any]) -> str:
    ratio = to_float(row.get("l10_imbalance_ratio"))
    spread = to_float(row.get("spread_bps"))
    if spread > 100.0:
        spread_bucket = "spread_gt100"
    elif spread > 50.0:
        spread_bucket = "spread_50_100"
    else:
        spread_bucket = "spread_lte50"
    if ratio >= 2.0:
        ratio_bucket = "ratio_gte2"
    elif ratio >= 1.25:
        ratio_bucket = "ratio_1p25_2"
    elif ratio >= 0.8:
        ratio_bucket = "ratio_balanced"
    else:
        ratio_bucket = "ratio_ask_wall"
    return f"{spread_bucket}|{ratio_bucket}"


def summarize(labels: list[dict[str, Any]]) -> dict[str, Any]:
    by_product: dict[str, list[dict[str, Any]]] = defaultdict(list)
    by_bucket: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in labels:
        by_product[str(row.get("product_id"))].append(row)
        by_bucket[bucket_name(row)].append(row)

    def aggregate(rows: list[dict[str, Any]]) -> dict[str, Any]:
        if not rows:
            return {}
        return {
            "samples": len(rows),
            "fee_clear_horizon_rate": round(sum(1 for row in rows if row.get("fee_clear_horizon")) / len(rows), 6),
            "fee_clear_mfe_rate": round(sum(1 for row in rows if row.get("fee_clear_mfe")) / len(rows), 6),
            "avg_horizon_taker_net_bps": round(mean(to_float(row.get("horizon_taker_net_bps")) for row in rows), 6),
            "best_horizon_taker_net_bps": round(max(to_float(row.get("horizon_taker_net_bps")) for row in rows), 6),
            "avg_mfe_taker_net_bps": round(mean(to_float(row.get("mfe_taker_net_bps")) for row in rows), 6),
            "best_mfe_taker_net_bps": round(max(to_float(row.get("mfe_taker_net_bps")) for row in rows), 6),
            "avg_spread_bps": round(mean(to_float(row.get("spread_bps")) for row in rows), 6),
            "avg_l10_ratio": round(mean(to_float(row.get("l10_imbalance_ratio")) for row in rows), 6),
        }

    products = [{"product_id": product_id, **aggregate(rows)} for product_id, rows in sorted(by_product.items())]
    products.sort(key=lambda row: (row["fee_clear_horizon_rate"], row["best_horizon_taker_net_bps"], row["avg_mfe_taker_net_bps"]), reverse=True)
    buckets = [{"bucket": name, **aggregate(rows)} for name, rows in sorted(by_bucket.items())]
    buckets.sort(key=lambda row: (row["fee_clear_horizon_rate"], row["best_horizon_taker_net_bps"]), reverse=True)
    return {
        "generated_at": utc_now_iso(),
        "mode": "kraken_l2_imbalance_forward_replay",
        "rows": len(labels),
        "products": products,
        "buckets": buckets,
        "read": "Forward labels on public L2 snapshots. Positive MFE is not fill proof; require live-depth fillability/roundtrip validation before funding.",
    }


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, sort_keys=True) + "\n")


def run(args: argparse.Namespace) -> dict[str, Any]:
    events = load_events(Path(args.event_path))
    labels = label_rows(
        events,
        horizon_seconds=float(args.horizon_seconds),
        fee_bps=float(args.fee_bps),
        min_net_bps=float(args.min_net_bps),
    )
    write_jsonl(Path(args.rows_path), labels)
    summary = summarize(labels)
    summary["parameters"] = {
        "event_path": str(args.event_path),
        "rows_path": str(args.rows_path),
        "summary_path": str(args.summary_path),
        "horizon_seconds": float(args.horizon_seconds),
        "fee_bps": float(args.fee_bps),
        "min_net_bps": float(args.min_net_bps),
    }
    summary_path = Path(args.summary_path)
    summary_path.parent.mkdir(parents=True, exist_ok=True)
    summary_path.write_text(json.dumps(summary, indent=2, sort_keys=True), encoding="utf-8")
    return summary


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Label Kraken L2 imbalance snapshots with forward bid/ask outcomes.")
    parser.add_argument("--event-path", type=Path, default=DEFAULT_EVENT_PATH)
    parser.add_argument("--rows-path", type=Path, default=DEFAULT_ROWS_PATH)
    parser.add_argument("--summary-path", type=Path, default=DEFAULT_SUMMARY_PATH)
    parser.add_argument("--horizon-seconds", type=float, default=30.0)
    parser.add_argument("--fee-bps", type=float, default=120.0, help="Round-trip taker fee bps used for ask-entry/bid-exit labels.")
    parser.add_argument("--min-net-bps", type=float, default=0.0)
    return parser.parse_args()


def main() -> int:
    summary = run(parse_args())
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
