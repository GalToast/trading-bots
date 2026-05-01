#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path
from statistics import mean
from typing import Any


ROOT = Path(__file__).resolve().parent.parent
DEFAULT_INPUT_PATH = ROOT / "reports" / "spot_microstructure_sync.jsonl"
DEFAULT_JSON_PATH = ROOT / "reports" / "spot_microstructure_sync_analysis.json"
DEFAULT_MD_PATH = ROOT / "reports" / "spot_microstructure_sync_analysis.md"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Analyze Kraken/Coinbase sync capture")
    parser.add_argument("--input-path", default=str(DEFAULT_INPUT_PATH))
    parser.add_argument("--json-path", default=str(DEFAULT_JSON_PATH))
    parser.add_argument("--md-path", default=str(DEFAULT_MD_PATH))
    parser.add_argument("--kraken-symbol", default="BTC-USD")
    parser.add_argument("--coinbase-symbol", default="BTC-USD")
    parser.add_argument("--move-threshold-usd", type=float, default=1.0)
    parser.add_argument("--max-follow-samples", type=int, default=3)
    return parser.parse_args()


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if not path.exists():
        return rows
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                payload = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(payload, dict):
                rows.append(payload)
    return rows


def extract_series(rows: list[dict[str, Any]], kraken_symbol: str, coinbase_symbol: str) -> list[dict[str, float]]:
    out: list[dict[str, float]] = []
    for row in rows:
        try:
            ts_epoch = float(row["ts_epoch"])
            kraken_mid = float(row["kraken"][kraken_symbol]["mid"])
            coinbase_mid = float(row["coinbase"][coinbase_symbol]["mid"])
        except Exception:
            continue
        out.append({"ts_epoch": ts_epoch, "kraken_mid": kraken_mid, "coinbase_mid": coinbase_mid})
    out.sort(key=lambda item: float(item["ts_epoch"]))
    return out


def analyze_follow_behavior(
    series: list[dict[str, float]],
    *,
    move_threshold_usd: float,
    max_follow_samples: int,
) -> dict[str, Any]:
    if len(series) < 3:
        return {
            "sample_count": len(series),
            "avg_interval_seconds": 0.0,
            "avg_diff_usd": 0.0,
            "significant_kraken_moves": 0,
            "follow_hits": {},
            "best_follow_window_samples": 0,
            "best_follow_hit_rate_pct": 0.0,
        }

    intervals = [
        float(series[i]["ts_epoch"]) - float(series[i - 1]["ts_epoch"])
        for i in range(1, len(series))
    ]
    diffs = [
        float(row["kraken_mid"]) - float(row["coinbase_mid"])
        for row in series
    ]
    follow_hits = {window: 0 for window in range(1, max_follow_samples + 1)}
    significant_moves = 0

    for idx in range(1, len(series) - 1):
        kraken_move = float(series[idx]["kraken_mid"]) - float(series[idx - 1]["kraken_mid"])
        if abs(kraken_move) < move_threshold_usd:
            continue
        significant_moves += 1
        direction = 1.0 if kraken_move > 0 else -1.0
        for window in range(1, max_follow_samples + 1):
            end_idx = min(len(series) - 1, idx + window)
            coinbase_move = float(series[end_idx]["coinbase_mid"]) - float(series[idx]["coinbase_mid"])
            if coinbase_move == 0:
                continue
            if (coinbase_move > 0 and direction > 0) or (coinbase_move < 0 and direction < 0):
                follow_hits[window] += 1

    hit_rates = {
        window: round((count / significant_moves * 100.0), 2) if significant_moves else 0.0
        for window, count in follow_hits.items()
    }
    best_window = max(hit_rates, key=hit_rates.get) if hit_rates else 0
    return {
        "sample_count": len(series),
        "avg_interval_seconds": round(mean(intervals), 4) if intervals else 0.0,
        "avg_diff_usd": round(mean(diffs), 4) if diffs else 0.0,
        "min_diff_usd": round(min(diffs), 4) if diffs else 0.0,
        "max_diff_usd": round(max(diffs), 4) if diffs else 0.0,
        "significant_kraken_moves": significant_moves,
        "follow_hits": follow_hits,
        "follow_hit_rates_pct": hit_rates,
        "best_follow_window_samples": int(best_window),
        "best_follow_hit_rate_pct": float(hit_rates.get(best_window, 0.0)),
    }


def render_md(payload: dict[str, Any]) -> str:
    lines = [
        "# Spot Microstructure Sync Analysis",
        "",
        f"- Input: `{payload['input_path']}`",
        f"- Kraken symbol: `{payload['kraken_symbol']}`",
        f"- Coinbase symbol: `{payload['coinbase_symbol']}`",
        f"- Move threshold USD: `{payload['move_threshold_usd']}`",
        "",
        "## Summary",
        "",
        f"- Samples: `{payload['analysis']['sample_count']}`",
        f"- Avg interval seconds: `{payload['analysis']['avg_interval_seconds']}`",
        f"- Avg Kraken-Coinbase diff USD: `{payload['analysis']['avg_diff_usd']}`",
        f"- Diff range USD: `{payload['analysis']['min_diff_usd']}` -> `{payload['analysis']['max_diff_usd']}`",
        f"- Significant Kraken moves: `{payload['analysis']['significant_kraken_moves']}`",
        f"- Best follow window samples: `{payload['analysis']['best_follow_window_samples']}`",
        f"- Best follow hit rate %: `{payload['analysis']['best_follow_hit_rate_pct']}`",
        "",
        "## Follow Rates",
        "",
        "| Window Samples | Follow Hits | Follow Hit Rate % |",
        "|---:|---:|---:|",
    ]
    for window in sorted(payload["analysis"]["follow_hits"]):
        lines.append(
            f"| {window} | {payload['analysis']['follow_hits'][window]} | {payload['analysis']['follow_hit_rates_pct'][window]} |"
        )
    return "\n".join(lines) + "\n"


def main() -> int:
    args = parse_args()
    input_path = Path(args.input_path)
    rows = load_jsonl(input_path)
    series = extract_series(rows, str(args.kraken_symbol).upper(), str(args.coinbase_symbol).upper())
    analysis = analyze_follow_behavior(
        series,
        move_threshold_usd=float(args.move_threshold_usd),
        max_follow_samples=max(1, int(args.max_follow_samples)),
    )
    payload = {
        "input_path": str(input_path),
        "kraken_symbol": str(args.kraken_symbol).upper(),
        "coinbase_symbol": str(args.coinbase_symbol).upper(),
        "move_threshold_usd": float(args.move_threshold_usd),
        "analysis": analysis,
    }
    Path(args.json_path).write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    Path(args.md_path).write_text(render_md(payload), encoding="utf-8")
    print(json.dumps(analysis, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
