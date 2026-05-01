#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
import statistics
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent.parent
CACHE_DIR = ROOT / "reports" / "candle_cache"
REPORTS = ROOT / "reports"
JSON_PATH = REPORTS / "coinbase_spot_bubble_capture_simulator.json"
CSV_PATH = REPORTS / "coinbase_spot_bubble_capture_simulator.csv"
MD_PATH = REPORTS / "coinbase_spot_bubble_capture_simulator.md"

DEFAULT_SPREAD_BPS = 25.0
BLUE_CHIP_SPREAD_BPS = {
    "BTC-USD": 1.0,
    "ETH-USD": 1.0,
    "SOL-USD": 2.0,
    "DOGE-USD": 5.0,
    "ADA-USD": 6.0,
    "XRP-USD": 5.0,
}


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def to_float(value: Any) -> float:
    try:
        return float(value or 0.0)
    except (TypeError, ValueError):
        return 0.0


def parse_candle(raw: Any) -> tuple[int, float, float, float, float, float] | None:
    if isinstance(raw, dict):
        ts = int(to_float(raw.get("start", raw.get("time", raw.get("ts", 0)))))
        open_ = to_float(raw.get("open"))
        high = to_float(raw.get("high"))
        low = to_float(raw.get("low"))
        close = to_float(raw.get("close"))
        volume = to_float(raw.get("volume"))
    elif isinstance(raw, list) and len(raw) >= 5:
        ts = int(to_float(raw[0]))
        open_ = to_float(raw[1])
        high = to_float(raw[2])
        low = to_float(raw[3])
        close = to_float(raw[4])
        volume = to_float(raw[5]) if len(raw) > 5 else 0.0
    else:
        return None
    if min(open_, high, low, close) <= 0.0:
        return None
    return ts, open_, high, low, close, volume


def product_from_file(path: Path, granularity: str) -> str:
    return path.name.split(f"_{granularity}_", 1)[0].replace("_", "-")


def candle_files(granularity: str, days: int, max_products: int) -> list[Path]:
    files = sorted(CACHE_DIR.glob(f"*_USD_{granularity}_{days}d.json"))
    if max_products > 0:
        return files[:max_products]
    return files


def load_candles(path: Path) -> list[tuple[int, float, float, float, float, float]]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return []
    raw_rows = payload.get("candles") if isinstance(payload, dict) else payload
    candles = [candle for candle in (parse_candle(raw) for raw in (raw_rows or [])) if candle is not None]
    candles.sort(key=lambda item: item[0])
    return candles


def rolling_volume_mult(candles: list[tuple[int, float, float, float, float, float]], idx: int, lookback: int) -> float:
    current = candles[idx][5]
    prior = [row[5] for row in candles[max(0, idx - lookback) : idx] if row[5] > 0.0]
    if not prior:
        return 1.0
    return current / max(statistics.mean(prior), 1e-12)


def close_location(candle: tuple[int, float, float, float, float, float]) -> float:
    _, _, high, low, close, _ = candle
    return max(0.0, min(1.0, (close - low) / max(high - low, 1e-12)))


def simulate_product(
    product_id: str,
    candles: list[tuple[int, float, float, float, float, float]],
    *,
    bars_per_hour: int,
    trigger_bars: int,
    trigger_pct: float,
    min_volume_mult: float,
    min_close_location: float,
    activation_pct: float,
    trail_pct: float,
    stop_pct: float,
    max_hold_bars: int,
    fee_bps_per_side: float,
    spread_bps: float,
) -> dict[str, Any]:
    trades: list[dict[str, float]] = []
    if len(candles) <= trigger_bars + max_hold_bars + 2:
        return {"trades": 0}
    fee_drag_pct = ((2.0 * fee_bps_per_side) + spread_bps) / 100.0
    idx = trigger_bars
    while idx < len(candles) - max_hold_bars - 1:
        prev_close = candles[idx - trigger_bars][4]
        signal_close = candles[idx][4]
        signal_ret_pct = ((signal_close / prev_close) - 1.0) * 100.0 if prev_close else 0.0
        if signal_ret_pct < trigger_pct:
            idx += 1
            continue
        if rolling_volume_mult(candles, idx, bars_per_hour) < min_volume_mult:
            idx += 1
            continue
        if close_location(candles[idx]) < min_close_location:
            idx += 1
            continue
        entry_idx = idx + 1
        entry_price = candles[entry_idx][1]
        if entry_price <= 0.0:
            idx += 1
            continue
        high_water = entry_price
        exit_price = candles[entry_idx][4]
        exit_idx = entry_idx
        exit_reason = "max_hold"
        max_gross_pct = 0.0
        min_gross_pct = 0.0
        for held in range(1, max_hold_bars + 1):
            j = entry_idx + held
            if j >= len(candles):
                break
            _, _, high, low, close, _ = candles[j]
            high_water = max(high_water, high)
            max_gross_pct = max(max_gross_pct, ((high_water / entry_price) - 1.0) * 100.0)
            min_gross_pct = min(min_gross_pct, ((low / entry_price) - 1.0) * 100.0)
            exit_idx = j
            if ((low / entry_price) - 1.0) * 100.0 <= -abs(stop_pct):
                exit_price = entry_price * (1.0 - (abs(stop_pct) / 100.0))
                exit_reason = "stop"
                break
            if max_gross_pct >= activation_pct:
                trail_stop = high_water * (1.0 - (trail_pct / 100.0))
                if low <= trail_stop:
                    exit_price = trail_stop
                    exit_reason = "trail"
                    break
            exit_price = close
        gross_pct = ((exit_price / entry_price) - 1.0) * 100.0
        net_pct = gross_pct - fee_drag_pct
        trades.append(
            {
                "signal_ret_pct": signal_ret_pct,
                "gross_pct": gross_pct,
                "net_pct": net_pct,
                "max_gross_pct": max_gross_pct,
                "max_net_reachable_pct": max_gross_pct - fee_drag_pct,
                "min_gross_pct": min_gross_pct,
                "hold_bars": float(exit_idx - entry_idx),
                "trail_exit": 1.0 if exit_reason == "trail" else 0.0,
                "stop_exit": 1.0 if exit_reason == "stop" else 0.0,
            }
        )
        idx = max(exit_idx + 1, idx + 1)
    nets = [row["net_pct"] for row in trades]
    winners = [value for value in nets if value > 0.0]
    hours = len(candles) / max(1, bars_per_hour)
    return {
        "product_id": product_id,
        "trades": len(trades),
        "trades_per_hour": len(trades) / hours if hours else 0.0,
        "wins": len(winners),
        "win_rate_pct": (len(winners) / len(trades) * 100.0) if trades else 0.0,
        "net_pct_sum": sum(nets),
        "net_pct_per_hour": (sum(nets) / hours) if hours else 0.0,
        "avg_net_pct": statistics.mean(nets) if nets else 0.0,
        "median_net_pct": statistics.median(nets) if nets else 0.0,
        "best_net_pct": max(nets, default=0.0),
        "worst_net_pct": min(nets, default=0.0),
        "avg_max_net_reachable_pct": statistics.mean([row["max_net_reachable_pct"] for row in trades]) if trades else 0.0,
        "best_max_net_reachable_pct": max([row["max_net_reachable_pct"] for row in trades], default=0.0),
        "avg_hold_bars": statistics.mean([row["hold_bars"] for row in trades]) if trades else 0.0,
        "trail_exit_pct": (sum(row["trail_exit"] for row in trades) / len(trades) * 100.0) if trades else 0.0,
        "stop_exit_pct": (sum(row["stop_exit"] for row in trades) / len(trades) * 100.0) if trades else 0.0,
    }


def build(args: argparse.Namespace) -> dict[str, Any]:
    bars_per_hour = 60 if args.granularity == "ONE_MINUTE" else 12
    rows: list[dict[str, Any]] = []
    files = candle_files(args.granularity, int(args.days), int(args.max_products))
    for path in files:
        product_id = product_from_file(path, args.granularity)
        candles = load_candles(path)
        if len(candles) < bars_per_hour * 12:
            continue
        spread_bps = BLUE_CHIP_SPREAD_BPS.get(product_id, float(args.default_spread_bps))
        for trigger_minutes in args.trigger_minutes:
            trigger_bars = max(1, int(round(trigger_minutes / (60 / bars_per_hour))))
            for trigger_pct in args.trigger_pct:
                for min_volume_mult in args.min_volume_mult:
                    for activation_pct in args.activation_pct:
                        for trail_pct in args.trail_pct:
                            for stop_pct in args.stop_pct:
                                for max_hold_minutes in args.max_hold_minutes:
                                    max_hold_bars = max(1, int(round(max_hold_minutes / (60 / bars_per_hour))))
                                    stats = simulate_product(
                                        product_id,
                                        candles,
                                        bars_per_hour=bars_per_hour,
                                        trigger_bars=trigger_bars,
                                        trigger_pct=trigger_pct,
                                        min_volume_mult=min_volume_mult,
                                        min_close_location=float(args.min_close_location),
                                        activation_pct=activation_pct,
                                        trail_pct=trail_pct,
                                        stop_pct=stop_pct,
                                        max_hold_bars=max_hold_bars,
                                        fee_bps_per_side=float(args.fee_bps_per_side),
                                        spread_bps=spread_bps,
                                    )
                                    if int(stats.get("trades", 0) or 0) < int(args.min_trades):
                                        continue
                                    rows.append(
                                        {
                                            **stats,
                                            "granularity": args.granularity,
                                            "days": int(args.days),
                                            "trigger_minutes": trigger_minutes,
                                            "trigger_pct": trigger_pct,
                                            "min_volume_mult": min_volume_mult,
                                            "min_close_location": float(args.min_close_location),
                                            "activation_pct": activation_pct,
                                            "trail_pct": trail_pct,
                                            "stop_pct": stop_pct,
                                            "max_hold_minutes": max_hold_minutes,
                                            "fee_bps_per_side": float(args.fee_bps_per_side),
                                            "spread_bps": spread_bps,
                                        }
                                    )
    rows.sort(
        key=lambda row: (
            float(row.get("net_pct_per_hour") or 0.0),
            float(row.get("avg_net_pct") or 0.0),
            int(row.get("trades") or 0),
        ),
        reverse=True,
    )
    return {
        "generated_at": utc_now_iso(),
        "mode": "coinbase_spot_bubble_capture_simulator",
        "parameters": vars(args),
        "leadership_read": [
            "This tests longer bubble capture geometry: momentum ignition, optional volume confirmation, profit activation, then trailing exit.",
            "The target is not scalp frequency; it is whether any Coinbase spot product can produce fee-paid multi-percent captures big enough to matter.",
            "Positive backtest rows still need live bid/ask shadow proof because candle high/low path and queue priority are approximations.",
        ],
        "rows": rows,
    }


def write_outputs(payload: dict[str, Any]) -> None:
    rows = payload["rows"]
    JSON_PATH.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    if rows:
        with CSV_PATH.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=list(rows[0].keys()))
            writer.writeheader()
            writer.writerows(rows)
    else:
        CSV_PATH.write_text("", encoding="utf-8")
    lines = [
        "# Coinbase Spot Bubble Capture Simulator",
        "",
        f"- Generated: `{payload['generated_at']}`",
        f"- Rows: `{len(rows)}`",
        "",
        "## Read",
        "",
    ]
    lines.extend([f"- {item}" for item in payload["leadership_read"]])
    lines.extend(
        [
            "",
            "## Top Rows",
            "",
            "| Rank | Product | Gran | Trigger | Volume | Activation | Trail | Stop | Hold m | Trades | Win % | Net %/h | Avg Net % | Best Net % | Avg Reach % |",
            "| ---: | --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
        ]
    )
    for rank, row in enumerate(rows[:40], start=1):
        lines.append(
            "| {rank} | {product_id} | {granularity} | {trigger_pct:.2f} | {min_volume_mult:.2f} | {activation_pct:.2f} | {trail_pct:.2f} | {stop_pct:.2f} | {max_hold_minutes} | {trades} | {win_rate_pct:.2f} | {net_pct_per_hour:.4f} | {avg_net_pct:.4f} | {best_net_pct:.4f} | {avg_max_net_reachable_pct:.4f} |".format(
                rank=rank,
                **row,
            )
        )
    MD_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")


def float_list(value: str) -> list[float]:
    return [float(part.strip()) for part in value.split(",") if part.strip()]


def int_list(value: str) -> list[int]:
    return [int(part.strip()) for part in value.split(",") if part.strip()]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Search longer Coinbase spot bubble-capture geometry.")
    parser.add_argument("--granularity", default="FIVE_MINUTE", choices=["ONE_MINUTE", "FIVE_MINUTE"])
    parser.add_argument("--days", type=int, default=7)
    parser.add_argument("--max-products", type=int, default=0)
    parser.add_argument("--min-trades", type=int, default=3)
    parser.add_argument("--fee-bps-per-side", type=float, default=120.0)
    parser.add_argument("--default-spread-bps", type=float, default=25.0)
    parser.add_argument("--min-close-location", type=float, default=0.65)
    parser.add_argument("--trigger-minutes", type=int_list, default=[5, 15, 30])
    parser.add_argument("--trigger-pct", type=float_list, default=[1.0, 2.0, 3.5, 5.0])
    parser.add_argument("--min-volume-mult", type=float_list, default=[0.0, 1.5, 3.0])
    parser.add_argument("--activation-pct", type=float_list, default=[3.0, 5.0, 8.0, 12.0])
    parser.add_argument("--trail-pct", type=float_list, default=[1.5, 2.5, 4.0, 6.0])
    parser.add_argument("--stop-pct", type=float_list, default=[2.5, 5.0, 8.0])
    parser.add_argument("--max-hold-minutes", type=int_list, default=[30, 60, 120, 240])
    return parser.parse_args()


def main() -> int:
    payload = build(parse_args())
    write_outputs(payload)
    print(json.dumps({"json_path": str(JSON_PATH), "csv_path": str(CSV_PATH), "md_path": str(MD_PATH), "rows": len(payload["rows"])}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
