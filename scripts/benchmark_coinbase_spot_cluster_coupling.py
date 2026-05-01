#!/usr/bin/env python3
"""
Audit capital coupling within the strongest discovered Coinbase spot lattice cluster.

This replays the tuned winners from `coinbase_spot_lattice_discovery.json` and
measures:
- how often each discovered sleeve is active
- pairwise overlap in time
- shared-denominator contention (same parked asset needed)
- shared-numerator concentration (multiple sleeves long the same asset)

Outputs:
- reports/coinbase_spot_cluster_coupling.csv
- reports/coinbase_spot_cluster_coupling.md
- reports/coinbase_spot_cluster_coupling.json
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
import time
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from coinbase_advanced_client import CoinbaseAdvancedClient
from ratio_lattice_60d_validation import (  # type: ignore
    SYMBOL_TO_PRODUCT,
    build_price_map,
    build_ratio_series,
    fetch_candles,
    find_attractors_kde,
)


DEFAULT_DISCOVERY_JSON = ROOT / "reports" / "coinbase_spot_lattice_discovery.json"
DEFAULT_CSV = ROOT / "reports" / "coinbase_spot_cluster_coupling.csv"
DEFAULT_MD = ROOT / "reports" / "coinbase_spot_cluster_coupling.md"
DEFAULT_JSON = ROOT / "reports" / "coinbase_spot_cluster_coupling.json"
DEFAULT_FOCUS_PAIRS: tuple[str, ...] = (
    "RAVE/BAL",
    "RAVE/BTC",
    "IOTX/ETH",
    "IOTX/BTC",
    "RAVE/ETH",
    "RAVE/IOTX",
    "BAL/IOTX",
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Audit overlap and capital coupling inside the discovered Coinbase spot cluster.")
    parser.add_argument("--discovery-json", default=str(DEFAULT_DISCOVERY_JSON))
    parser.add_argument("--days", type=int, default=60)
    parser.add_argument("--position-size", type=float, default=0.01)
    parser.add_argument("--max-concurrent", type=int, default=5)
    parser.add_argument("--pairs", nargs="*", default=list(DEFAULT_FOCUS_PAIRS))
    parser.add_argument("--csv-path", default=str(DEFAULT_CSV))
    parser.add_argument("--md-path", default=str(DEFAULT_MD))
    parser.add_argument("--json-path", default=str(DEFAULT_JSON))
    return parser.parse_args()


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def select_discovered_rows(rows: list[dict[str, Any]], focus_pairs: set[str]) -> list[dict[str, Any]]:
    return [row for row in rows if row["pair"].upper() in focus_pairs]


def replay_with_events(
    ratio_series: list[dict[str, Any]],
    attractors: list[dict[str, float]],
    *,
    profit_threshold: float,
    max_levels: int,
    position_size: float,
    max_concurrent: int,
) -> dict[str, Any]:
    levels = attractors[:max_levels]
    positions: list[dict[str, Any]] = []
    events: list[dict[str, Any]] = []
    active_points: list[dict[str, Any]] = []

    for point in ratio_series:
        ratio = float(point["ratio"])
        ts = int(point["t"])

        for idx, attr in enumerate(levels):
            level_val = float(attr["ratio"])
            occupied = any(pos["level_idx"] == idx for pos in positions)
            if not occupied and ratio <= level_val and len(positions) < max_concurrent:
                pos = {
                    "level_idx": idx,
                    "entry_ratio": ratio,
                    "level_value": level_val,
                    "size": position_size,
                    "opened_at": ts,
                }
                positions.append(pos)
                events.append({"ts": ts, "type": "open", "level_idx": idx, "ratio": ratio})

        closes: list[dict[str, Any]] = []
        for pos in positions:
            if ratio >= pos["level_value"] * profit_threshold:
                pnl = pos["size"] * (ratio - pos["entry_ratio"]) / pos["entry_ratio"]
                closes.append(pos)
                events.append(
                    {
                        "ts": ts,
                        "type": "close",
                        "level_idx": pos["level_idx"],
                        "ratio": ratio,
                        "pnl_den": pnl,
                        "opened_at": pos["opened_at"],
                    }
                )
        for pos in closes:
            positions.remove(pos)

        active_points.append({"ts": ts, "open_count": len(positions), "active": len(positions) > 0})

    active_bars = sum(1 for point in active_points if point["active"])
    max_open_seen = max((point["open_count"] for point in active_points), default=0)
    avg_open_when_active = (
        sum(point["open_count"] for point in active_points if point["active"]) / active_bars if active_bars else 0.0
    )
    return {
        "events": events,
        "active_points": active_points,
        "active_bars": active_bars,
        "total_bars": len(active_points),
        "active_ratio": active_bars / len(active_points) if active_points else 0.0,
        "max_open_seen": max_open_seen,
        "avg_open_when_active": avg_open_when_active,
    }


def pairwise_overlap(left: dict[str, Any], right: dict[str, Any]) -> dict[str, Any]:
    left_map = {int(point["ts"]): point for point in left["active_points"]}
    right_map = {int(point["ts"]): point for point in right["active_points"]}
    common_ts = sorted(set(left_map.keys()) & set(right_map.keys()))
    overlap_bars = 0
    overlap_open_sum = 0
    for ts in common_ts:
        if left_map[ts]["active"] and right_map[ts]["active"]:
            overlap_bars += 1
            overlap_open_sum += int(left_map[ts]["open_count"]) + int(right_map[ts]["open_count"])
    return {
        "common_bars": len(common_ts),
        "overlap_bars": overlap_bars,
        "overlap_ratio_common": overlap_bars / len(common_ts) if common_ts else 0.0,
        "overlap_ratio_left_active": overlap_bars / left["active_bars"] if left["active_bars"] else 0.0,
        "overlap_ratio_right_active": overlap_bars / right["active_bars"] if right["active_bars"] else 0.0,
        "avg_total_open_when_overlap": overlap_open_sum / overlap_bars if overlap_bars else 0.0,
    }


def aggregate_asset_overlap(sleeves: list[dict[str, Any]]) -> list[dict[str, Any]]:
    asset_maps: dict[str, dict[int, int]] = {}
    for sleeve in sleeves:
        numerator = sleeve["symbol_a"]
        denominator = sleeve["symbol_b"]
        for point in sleeve["replay"]["active_points"]:
            ts = int(point["ts"])
            if not point["active"]:
                continue
            asset_maps.setdefault(f"num:{numerator}", {}).setdefault(ts, 0)
            asset_maps[f"num:{numerator}"][ts] += 1
            asset_maps.setdefault(f"den:{denominator}", {}).setdefault(ts, 0)
            asset_maps[f"den:{denominator}"][ts] += 1

    rows: list[dict[str, Any]] = []
    for asset_key, timeline in sorted(asset_maps.items()):
        conflict_bars = sum(1 for count in timeline.values() if count >= 2)
        max_parallel = max(timeline.values(), default=0)
        total_bars = len(timeline)
        rows.append(
            {
                "asset_key": asset_key,
                "active_bars": total_bars,
                "conflict_bars": conflict_bars,
                "conflict_ratio": conflict_bars / total_bars if total_bars else 0.0,
                "max_parallel_sleeves": max_parallel,
            }
        )
    return rows


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    columns = [
        "left_pair",
        "right_pair",
        "same_numerator",
        "same_denominator",
        "common_bars",
        "overlap_bars",
        "overlap_ratio_common",
        "overlap_ratio_left_active",
        "overlap_ratio_right_active",
        "avg_total_open_when_overlap",
    ]
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns)
        writer.writeheader()
        for row in rows:
            writer.writerow({column: row.get(column) for column in columns})


def write_markdown(
    path: Path,
    *,
    sleeve_rows: list[dict[str, Any]],
    overlap_rows: list[dict[str, Any]],
    asset_rows: list[dict[str, Any]],
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    sorted_overlap = sorted(overlap_rows, key=lambda row: row["overlap_ratio_common"], reverse=True)
    sorted_assets = sorted(asset_rows, key=lambda row: row["conflict_ratio"], reverse=True)

    lines = [
        "# Coinbase Spot Cluster Coupling",
        "",
        "- This audit asks the deployment question the broad discovery screen did not answer: how badly do the strongest `RAVE` / `IOTX` / `BAL` spot sleeves collide when replayed together?",
        "- `same_denominator` means the sleeves compete for the same parked asset in spot rotation.",
        "- `same_numerator` means the sleeves concentrate into the same long asset simultaneously.",
        "",
        "## Sleeve Activity",
        "",
        "| Pair | Group | Tuned Shape | Active Bars | Active Share | Max Open | Avg Open When Active |",
        "| --- | --- | --- | ---: | ---: | ---: | ---: |",
    ]
    for row in sleeve_rows:
        lines.append(
            f"| `{row['pair']}` | `{row['group']}` | `thr={row['profit_threshold']:.3f} levels={row['max_levels']}` | "
            f"`{row['replay']['active_bars']}` | `{row['replay']['active_ratio']:.1%}` | "
            f"`{row['replay']['max_open_seen']}` | `{row['replay']['avg_open_when_active']:.2f}` |"
        )

    lines.extend(
        [
            "",
            "## Pairwise Overlap",
            "",
            "| Left | Right | Same Num | Same Den | Overlap / Common | Overlap / Left Active | Overlap / Right Active | Avg Total Open |",
            "| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |",
        ]
    )
    for row in sorted_overlap:
        lines.append(
            f"| `{row['left_pair']}` | `{row['right_pair']}` | `{int(row['same_numerator'])}` | `{int(row['same_denominator'])}` | "
            f"`{row['overlap_ratio_common']:.1%}` | `{row['overlap_ratio_left_active']:.1%}` | `{row['overlap_ratio_right_active']:.1%}` | `{row['avg_total_open_when_overlap']:.2f}` |"
        )

    lines.extend(["", "## Asset Contention", "", "| Asset Key | Conflict Bars | Conflict Share | Max Parallel Sleeves |", "| --- | ---: | ---: | ---: |"])
    for row in sorted_assets:
        lines.append(
            f"| `{row['asset_key']}` | `{row['conflict_bars']}` | `{row['conflict_ratio']:.1%}` | `{row['max_parallel_sleeves']}` |"
        )

    lines.extend(["", "## Read", ""])
    for row in sorted_overlap[:5]:
        coupling = "denominator contention" if row["same_denominator"] else "numerator concentration" if row["same_numerator"] else "timing overlap"
        lines.append(
            f"- `{row['left_pair']}` vs `{row['right_pair']}`: `{row['overlap_ratio_common']:.1%}` common-bar overlap, main issue is {coupling}."
        )
    lines.extend(
        [
            "- High same-denominator overlap means isolated discovery winners will overstate what one shared parked-asset bucket can do in a single spot account.",
            "- High same-numerator overlap means the cluster is partly a concentrated long on one coin disguised as multiple pair sleeves.",
            "- Forward shadow should start with the strongest cluster sleeves that also avoid the worst numerator and denominator collisions.",
        ]
    )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    args = parse_args()
    discovery = load_json(Path(args.discovery_json))
    tuned_rows = select_discovered_rows(discovery["pair_best_rows"], {pair.upper() for pair in args.pairs})
    if not tuned_rows:
        raise SystemExit("No matching discovered pairs found in discovery JSON.")

    symbols: set[str] = set()
    for row in tuned_rows:
        symbols.add(row["symbol_a"])
        symbols.add(row["symbol_b"])

    client = CoinbaseAdvancedClient()
    now_ts = int(time.time())
    start_ts = now_ts - int(args.days) * 86400

    price_maps: dict[str, dict[int, float]] = {}
    candle_counts: dict[str, int] = {}
    for symbol in sorted(symbols):
        candles = fetch_candles(client, SYMBOL_TO_PRODUCT[symbol], start_ts, now_ts)
        price_maps[symbol] = build_price_map(candles)
        candle_counts[symbol] = len(candles)

    sleeves: list[dict[str, Any]] = []
    for row in tuned_rows:
        symbol_a = row["symbol_a"]
        symbol_b = row["symbol_b"]
        series = build_ratio_series(price_maps[symbol_a], price_maps[symbol_b])
        attractors = find_attractors_kde(series)
        replay = replay_with_events(
            series,
            attractors,
            profit_threshold=float(row["profit_threshold"]),
            max_levels=int(row["max_levels"]),
            position_size=float(args.position_size),
            max_concurrent=int(args.max_concurrent),
        )
        sleeves.append({**row, "replay": replay})

    overlap_rows: list[dict[str, Any]] = []
    for index, left in enumerate(sleeves):
        for right in sleeves[index + 1:]:
            overlap = pairwise_overlap(left["replay"], right["replay"])
            overlap_rows.append(
                {
                    "left_pair": left["pair"],
                    "right_pair": right["pair"],
                    "same_numerator": left["symbol_a"] == right["symbol_a"],
                    "same_denominator": left["symbol_b"] == right["symbol_b"],
                    **overlap,
                }
            )

    asset_rows = aggregate_asset_overlap(sleeves)

    csv_path = Path(args.csv_path)
    md_path = Path(args.md_path)
    json_path = Path(args.json_path)
    write_csv(csv_path, overlap_rows)
    write_markdown(md_path, sleeve_rows=sleeves, overlap_rows=overlap_rows, asset_rows=asset_rows)
    json_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.write_text(
        json.dumps(
            {
                "run_params": {
                    "discovery_json": str(args.discovery_json),
                    "days": args.days,
                    "position_size": args.position_size,
                    "max_concurrent": args.max_concurrent,
                    "pairs": args.pairs,
                },
                "candle_counts": candle_counts,
                "sleeves": [
                    {
                        "pair": sleeve["pair"],
                        "group": sleeve["group"],
                        "symbol_a": sleeve["symbol_a"],
                        "symbol_b": sleeve["symbol_b"],
                        "profit_threshold": sleeve["profit_threshold"],
                        "max_levels": sleeve["max_levels"],
                        "replay_summary": {
                            "active_bars": sleeve["replay"]["active_bars"],
                            "total_bars": sleeve["replay"]["total_bars"],
                            "active_ratio": sleeve["replay"]["active_ratio"],
                            "max_open_seen": sleeve["replay"]["max_open_seen"],
                            "avg_open_when_active": sleeve["replay"]["avg_open_when_active"],
                        },
                    }
                    for sleeve in sleeves
                ],
                "pairwise_overlap": overlap_rows,
                "asset_contention": asset_rows,
            },
            indent=2,
        ),
        encoding="utf-8",
    )

    print(f"CSV:  {csv_path}")
    print(f"MD:   {md_path}")
    print(f"JSON: {json_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
