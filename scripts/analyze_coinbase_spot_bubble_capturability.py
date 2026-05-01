#!/usr/bin/env python3
from __future__ import annotations

import csv
import json
import statistics
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent.parent
CACHE_DIR = ROOT / "reports" / "candle_cache"
REPORTS = ROOT / "reports"
JSON_PATH = REPORTS / "coinbase_spot_bubble_capturability.json"
CSV_PATH = REPORTS / "coinbase_spot_bubble_capturability.csv"
MD_PATH = REPORTS / "coinbase_spot_bubble_capturability.md"

PRODUCT_FILES = [
    "RAVE_USD_ONE_MINUTE_30d.json",
    "SOL_USD_ONE_MINUTE_30d.json",
    "BTC_USD_ONE_MINUTE_7d.json",
    "ETH_USD_ONE_MINUTE_7d.json",
    "IOTX_USD_ONE_MINUTE_7d.json",
    "ALEPH_USD_ONE_MINUTE_7d.json",
    "BAL_USD_ONE_MINUTE_7d.json",
    "BLUR_USD_ONE_MINUTE_7d.json",
]
TRIGGER_GRID = [1.0, 1.5, 2.4, 3.0]
TRAIL_GRID = [1.0, 2.0, 3.0, 5.0]
MAX_HOLD_GRID = [15, 30, 60]
DEFAULT_FEE_BPS_PER_SIDE = 120.0
DEFAULT_SPREAD_BPS = {
    "RAVE-USD": 13.5,
    "IOTX-USD": 25.0,
    "BAL-USD": 70.0,
    "BLUR-USD": 31.8,
    "ALEPH-USD": 50.0,
    "SOL-USD": 2.0,
    "BTC-USD": 1.0,
    "ETH-USD": 1.0,
}


@dataclass
class Candle:
    ts: int
    open: float
    high: float
    low: float
    close: float


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def to_float(value: Any) -> float:
    try:
        return float(value or 0.0)
    except (TypeError, ValueError):
        return 0.0


def parse_candle(raw: Any) -> Candle | None:
    if isinstance(raw, dict):
        ts = int(to_float(raw.get("start", raw.get("time", raw.get("ts", 0)))))
        open_ = to_float(raw.get("open"))
        high = to_float(raw.get("high"))
        low = to_float(raw.get("low"))
        close = to_float(raw.get("close"))
    elif isinstance(raw, list) and len(raw) >= 5:
        ts = int(to_float(raw[0]))
        open_ = to_float(raw[1])
        high = to_float(raw[2])
        low = to_float(raw[3])
        close = to_float(raw[4])
    else:
        return None
    if min(open_, high, low, close) <= 0.0:
        return None
    return Candle(ts=ts, open=open_, high=high, low=low, close=close)


def product_from_filename(filename: str) -> str:
    return filename.replace("_ONE_MINUTE_30d.json", "").replace("_ONE_MINUTE_7d.json", "").replace("_", "-")


def load_candles(filename: str) -> list[Candle]:
    path = CACHE_DIR / filename
    if not path.exists():
        return []
    payload = json.loads(path.read_text(encoding="utf-8"))
    raw_rows = payload.get("candles") if isinstance(payload, dict) else payload
    candles = [candle for candle in (parse_candle(raw) for raw in (raw_rows or [])) if candle is not None]
    candles.sort(key=lambda candle: candle.ts)
    return candles


def simulate(
    candles: list[Candle],
    *,
    trigger_pct: float,
    trail_pct: float,
    max_hold_bars: int,
    fee_bps_per_side: float,
    spread_bps: float,
) -> dict[str, Any]:
    trades: list[dict[str, float]] = []
    idx = 5
    total_cost_pct = ((2.0 * fee_bps_per_side) + spread_bps) / 100.0
    while idx < len(candles) - 1:
        window = candles[idx - 4 : idx + 1]
        base = candles[idx - 5].close
        ignition_high = max(candle.high for candle in window)
        ignition_pct = ((ignition_high - base) / base) * 100.0 if base > 0 else 0.0
        if ignition_pct < trigger_pct:
            idx += 1
            continue
        entry_idx = idx + 1
        entry = candles[entry_idx].open
        high = entry
        exit_price = candles[entry_idx].close
        exit_idx = entry_idx
        exit_reason = "max_hold"
        for hold in range(1, max_hold_bars + 1):
            j = entry_idx + hold
            if j >= len(candles):
                break
            candle = candles[j]
            high = max(high, candle.high)
            trail_stop = high * (1.0 - (trail_pct / 100.0))
            exit_idx = j
            if candle.low <= trail_stop:
                exit_price = trail_stop
                exit_reason = "trail"
                break
            exit_price = candle.close
        gross_pct = ((exit_price - entry) / entry) * 100.0 if entry > 0 else 0.0
        net_pct = gross_pct - total_cost_pct
        trades.append(
            {
                "entry_idx": float(entry_idx),
                "exit_idx": float(exit_idx),
                "ignition_pct": ignition_pct,
                "gross_pct": gross_pct,
                "net_pct": net_pct,
                "hold_bars": float(exit_idx - entry_idx),
                "exit_reason": 1.0 if exit_reason == "trail" else 0.0,
            }
        )
        idx = max(exit_idx + 1, idx + 1)
    wins = [trade["net_pct"] for trade in trades if trade["net_pct"] > 0.0]
    losses = [trade["net_pct"] for trade in trades if trade["net_pct"] <= 0.0]
    return {
        "trades": len(trades),
        "wins": len(wins),
        "losses": len(losses),
        "win_rate_pct": (len(wins) / len(trades) * 100.0) if trades else 0.0,
        "net_pct_sum": sum(trade["net_pct"] for trade in trades),
        "avg_net_pct": statistics.mean([trade["net_pct"] for trade in trades]) if trades else 0.0,
        "median_net_pct": statistics.median([trade["net_pct"] for trade in trades]) if trades else 0.0,
        "best_net_pct": max([trade["net_pct"] for trade in trades], default=0.0),
        "worst_net_pct": min([trade["net_pct"] for trade in trades], default=0.0),
        "avg_hold_bars": statistics.mean([trade["hold_bars"] for trade in trades]) if trades else 0.0,
        "trail_exit_pct": (sum(trade["exit_reason"] for trade in trades) / len(trades) * 100.0) if trades else 0.0,
    }


def main() -> int:
    rows: list[dict[str, Any]] = []
    for filename in PRODUCT_FILES:
        product = product_from_filename(filename)
        candles = load_candles(filename)
        if not candles:
            continue
        hours = len(candles) / 60.0
        spread_bps = DEFAULT_SPREAD_BPS.get(product, 50.0)
        for trigger_pct in TRIGGER_GRID:
            for trail_pct in TRAIL_GRID:
                for max_hold_bars in MAX_HOLD_GRID:
                    result = simulate(
                        candles,
                        trigger_pct=trigger_pct,
                        trail_pct=trail_pct,
                        max_hold_bars=max_hold_bars,
                        fee_bps_per_side=DEFAULT_FEE_BPS_PER_SIDE,
                        spread_bps=spread_bps,
                    )
                    trades = int(result["trades"])
                    rows.append(
                        {
                            "product_id": product,
                            "candles": len(candles),
                            "hours": round(hours, 3),
                            "trigger_5m_pct": trigger_pct,
                            "trail_pct": trail_pct,
                            "max_hold_bars": max_hold_bars,
                            "fee_bps_per_side": DEFAULT_FEE_BPS_PER_SIDE,
                            "spread_bps": spread_bps,
                            "trades": trades,
                            "trades_per_hour": round((trades / hours) if hours else 0.0, 6),
                            "wins": int(result["wins"]),
                            "losses": int(result["losses"]),
                            "win_rate_pct": round(float(result["win_rate_pct"]), 4),
                            "net_pct_sum": round(float(result["net_pct_sum"]), 6),
                            "net_pct_per_hour": round((float(result["net_pct_sum"]) / hours) if hours else 0.0, 6),
                            "avg_net_pct": round(float(result["avg_net_pct"]), 6),
                            "median_net_pct": round(float(result["median_net_pct"]), 6),
                            "best_net_pct": round(float(result["best_net_pct"]), 6),
                            "worst_net_pct": round(float(result["worst_net_pct"]), 6),
                            "avg_hold_bars": round(float(result["avg_hold_bars"]), 4),
                            "trail_exit_pct": round(float(result["trail_exit_pct"]), 4),
                        }
                    )
    rows.sort(key=lambda row: (row["trades"] > 0, row["net_pct_per_hour"], row["net_pct_sum"], row["trades"]), reverse=True)
    payload = {
        "generated_at": utc_now_iso(),
        "mode": "coinbase_spot_bubble_capturability",
        "parameters": {
            "trigger_grid": TRIGGER_GRID,
            "trail_grid": TRAIL_GRID,
            "max_hold_grid": MAX_HOLD_GRID,
            "fee_bps_per_side": DEFAULT_FEE_BPS_PER_SIDE,
            "spread_model": "static_current_proxy_bps",
        },
        "leadership_read": [
            "This tests whether hourly volatility is actually capturable by an ignition-entry and trailing-exit rule.",
            "Positive rows are research leads only; they still need live bid/ask shadow proof before any live order permission.",
            "A row can have huge hourly excursions and still fail here if the trigger enters too late or the trail gives fees back.",
        ],
        "rows": rows,
    }
    JSON_PATH.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    columns = list(rows[0].keys()) if rows else []
    with CSV_PATH.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns)
        writer.writeheader()
        for row in rows:
            writer.writerow(row)
    lines = [
        "# Coinbase Spot Bubble Capturability",
        "",
        f"- Generated: `{payload['generated_at']}`",
        f"- Fee: `{DEFAULT_FEE_BPS_PER_SIDE}` bps/side",
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
            "| Rank | Product | Trigger 5m % | Trail % | Max Hold | Trades | Win % | Net %/h | Sum Net % | Avg Net % | Best % | Worst % |",
            "| ---: | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
        ]
    )
    for idx, row in enumerate(rows[:30], start=1):
        lines.append(
            "| {idx} | {product_id} | {trigger_5m_pct:.2f} | {trail_pct:.2f} | {max_hold_bars} | {trades} | {win_rate_pct:.2f} | {net_pct_per_hour:.4f} | {net_pct_sum:.4f} | {avg_net_pct:.4f} | {best_net_pct:.4f} | {worst_net_pct:.4f} |".format(
                idx=idx,
                **row,
            )
        )
    MD_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(json.dumps({"rows": len(rows), "top": rows[:5], "md_path": str(MD_PATH)}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
