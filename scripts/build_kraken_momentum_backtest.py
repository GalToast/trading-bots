#!/usr/bin/env python3
"""
Longer-Hold Momentum Backtest on Kraken OHLC Candles.

Uses the candle collector's OHLC data (with wicks) to test directional momentum
strategies at 5m, 15m, and 30m hold windows. This is the answer to the fee problem:
at longer horizons, fees become negligible compared to the move.

Key: wick-aware execution — stops and targets can trigger mid-candle.

Usage:
    python scripts/build_kraken_momentum_backtest.py
    python scripts/build_kraken_momentum_backtest.py --products SHAPE-USD,SWEAT-USD --hold-minutes 15,30,60
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "scripts"
REPORTS = ROOT / "reports"
CACHE = ROOT / "reports" / "cache"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

DEFAULT_CACHE_PATH = CACHE / "kraken_ohlc_collector.json"
DEFAULT_PRODUCTS = "SHAPE-USD,SWEAT-USD,HONEY-USD,CQT-USD,BILLY-USD,DUCK-USD,CLOUD-USD"
DEFAULT_HOLD_MINUTES = "15,30,60"
DEFAULT_TAKER_FEE_BPS = 120.0
DEFAULT_STOP_BPS = 500.0  # 5% stop
DEFAULT_TARGET_BPS = 1000.0  # 10% target
DEFAULT_MOMENTUM_LOOKBACK = 3  # Number of prior candles to measure momentum


@dataclass
class Trade:
    product: str
    entry_idx: int
    entry_price: float
    entry_time: float
    exit_idx: int
    exit_price: float
    exit_time: float
    exit_reason: str  # "target", "stop", "timeout"
    hold_minutes: float
    gross_bps: float
    fee_bps: float
    net_bps: float
    mfe_bps: float  # Maximum favorable excursion
    mae_bps: float  # Maximum adverse excursion


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def load_candles(cache_path: Path) -> dict[str, dict[str, list]]:
    """Load candle cache. Returns {product: {grain: [candle_dicts]}}."""
    cache = json.loads(cache_path.read_text(encoding="utf-8"))
    products = cache.get("products", {})
    result = {}
    for product, prod_data in products.items():
        grains = {}
        for grain_str, grain_data in prod_data.get("granularities", {}).items():
            grains[int(grain_str)] = grain_data.get("candles", [])
        if grains:
            result[product] = grains
    return result


def compute_momentum(candles: list[dict], idx: int, lookback: int) -> float:
    """Compute momentum as % change over lookback candles."""
    if idx < lookback:
        return 0.0
    prior_close = candles[idx - lookback]["c"]
    current_close = candles[idx - 1]["c"]
    if prior_close <= 0:
        return 0.0
    return ((current_close / prior_close) - 1.0) * 10000.0  # bps


def simulate_trade(
    candles: list[dict],
    idx: int,
    hold_bars: int,
    stop_bps: float,
    target_bps: float,
    taker_fee_bps: float,
) -> Trade | None:
    """
    Simulate a long trade with wick-aware execution.

    Entry: at open of signal candle (idx)
    Exit: first of target hit, stop hit, or timeout (hold_bars elapsed)

    Uses wicks (high/low) to detect mid-candle stop/target triggers.
    """
    entry_candle = candles[idx]
    entry_price = entry_candle["o"]
    if entry_price <= 0:
        return None

    # Compute targets
    target_price = entry_price * (1 + target_bps / 10000.0)
    stop_price = entry_price * (1 - stop_bps / 10000.0)

    mfe_bps = 0.0
    mae_bps = 0.0
    exit_price = None
    exit_reason = None
    exit_idx = None

    for i in range(idx, min(idx + hold_bars, len(candles))):
        c = candles[i]
        high = c["h"]
        low = c["l"]
        close = c["c"]

        # Check if target hit (wick touches target)
        if high >= target_price:
            exit_price = target_price
            exit_reason = "target"
            exit_idx = i
            break

        # Check if stop hit (wick touches stop)
        if low <= stop_price:
            exit_price = stop_price
            exit_reason = "stop"
            exit_idx = i
            break

        # Track MFE/MAE
        high_bps = ((high - entry_price) / entry_price) * 10000.0
        low_bps = ((low - entry_price) / entry_price) * 10000.0
        mfe_bps = max(mfe_bps, high_bps)
        mae_bps = min(mae_bps, low_bps)
    else:
        # Timeout — exit at close of last candle
        last_c = candles[min(idx + hold_bars - 1, len(candles) - 1)]
        exit_price = last_c["c"]
        exit_reason = "timeout"
        exit_idx = min(idx + hold_bars - 1, len(candles) - 1)

    if exit_price is None:
        return None

    gross_bps = ((exit_price / entry_price) - 1.0) * 10000.0
    fee_bps = 2.0 * taker_fee_bps  # Entry + exit
    net_bps = gross_bps - fee_bps

    hold_minutes = (candles[exit_idx]["t"] - candles[idx]["t"]) / 60.0

    return Trade(
        product="",  # Filled by caller
        entry_idx=idx,
        entry_price=entry_price,
        entry_time=candles[idx]["t"],
        exit_idx=exit_idx,
        exit_price=exit_price,
        exit_time=candles[exit_idx]["t"],
        exit_reason=exit_reason,
        hold_minutes=round(hold_minutes, 1),
        gross_bps=round(gross_bps, 2),
        fee_bps=round(fee_bps, 2),
        net_bps=round(net_bps, 2),
        mfe_bps=round(mfe_bps, 2),
        mae_bps=round(mae_bps, 2),
    )


def backtest_product(
    product: str,
    candles: list[dict],
    hold_minutes: list[int],
    stop_bps: float,
    target_bps: float,
    taker_fee_bps: float,
    momentum_lookback: int,
    min_momentum_bps: float,
) -> dict[str, Any]:
    """Backtest momentum strategy on one product's candles."""
    results = {}

    for hold_min in hold_minutes:
        # Convert minutes to bars (assuming 5m candles)
        candle_interval = 5  # minutes
        hold_bars = max(1, int(round(hold_min / candle_interval)))

        trades = []
        for idx in range(momentum_lookback, len(candles) - 1):
            # Signal: positive momentum over lookback
            momentum = compute_momentum(candles, idx, momentum_lookback)
            if momentum < min_momentum_bps:
                continue  # No signal

            trade = simulate_trade(candles, idx, hold_bars, stop_bps, target_bps, taker_fee_bps)
            if trade:
                trade.product = product
                trades.append(trade)

        if not trades:
            results[str(hold_min)] = {
                "hold_minutes": hold_min,
                "hold_bars": hold_bars,
                "trades": 0,
                "win_rate": 0,
                "avg_net_bps": 0,
                "total_net_bps": 0,
                "target_hits": 0,
                "stops_hit": 0,
                "timeouts": 0,
            }
            continue

        winners = [t for t in trades if t.net_bps > 0]
        target_hits = [t for t in trades if t.exit_reason == "target"]
        stops_hit = [t for t in trades if t.exit_reason == "stop"]
        timeouts = [t for t in trades if t.exit_reason == "timeout"]

        avg_net = sum(t.net_bps for t in trades) / len(trades)
        total_net = sum(t.net_bps for t in trades)
        win_rate = len(winners) / len(trades) * 100

        results[str(hold_min)] = {
            "hold_minutes": hold_min,
            "hold_bars": hold_bars,
            "signals_fired": len(trades),
            "win_rate_pct": round(win_rate, 1),
            "avg_net_bps": round(avg_net, 2),
            "total_net_bps": round(total_net, 2),
            "target_hits": len(target_hits),
            "stops_hit": len(stops_hit),
            "timeouts": len(timeouts),
            "avg_mfe_bps": round(sum(t.mfe_bps for t in trades) / len(trades), 2),
            "avg_mae_bps": round(sum(t.mae_bps for t in trades) / len(trades), 2),
            "avg_hold_min": round(sum(t.hold_minutes for t in trades) / len(trades), 1),
        }

    return results


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Backtest momentum strategies on Kraken OHLC candles")
    parser.add_argument("--cache-path", default=str(DEFAULT_CACHE_PATH))
    parser.add_argument("--products", default=DEFAULT_PRODUCTS)
    parser.add_argument("--hold-minutes", default=DEFAULT_HOLD_MINUTES)
    parser.add_argument("--taker-fee-bps", type=float, default=DEFAULT_TAKER_FEE_BPS)
    parser.add_argument("--stop-bps", type=float, default=DEFAULT_STOP_BPS)
    parser.add_argument("--target-bps", type=float, default=DEFAULT_TARGET_BPS)
    parser.add_argument("--momentum-lookback", type=int, default=DEFAULT_MOMENTUM_LOOKBACK)
    parser.add_argument("--min-momentum-bps", type=float, default=0.0, help="Minimum momentum signal threshold (bps)")
    parser.add_argument("--json-path", default=str(REPORTS / "kraken_momentum_backtest.json"))
    parser.add_argument("--md-path", default=str(REPORTS / "kraken_momentum_backtest.md"))
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    hold_minutes = [int(x) for x in args.hold_minutes.split(",")]
    products = [p.strip() for p in args.products.split(",") if p.strip()]

    print(f"📊 Kraken Momentum Backtest")
    print(f"   Products: {products}")
    print(f"   Hold windows: {hold_minutes} minutes")
    print(f"   Taker fee: {args.taker_fee_bps}bps (round-trip: {2*args.taker_fee_bps}bps)")
    print(f"   Stop: {args.stop_bps}bps, Target: {args.target_bps}bps")
    print(f"   Momentum lookback: {args.momentum_lookback} candles, min signal: {args.min_momentum_bps}bps")
    print()

    candles = load_candles(Path(args.cache_path))
    all_results = {}

    for product in products:
        prod_candles = candles.get(product, {})
        if not prod_candles:
            print(f"  ❌ {product}: no candles found")
            continue

        # Use 5m candles (or whatever is available)
        if 5 in prod_candles:
            grain = 5
            grain_name = "5m"
        elif 1 in prod_candles:
            grain = 1
            grain_name = "1m"
        else:
            grain = list(prod_candles.keys())[0]
            grain_name = f"{grain}m"

        c = prod_candles[grain]
        print(f"  Testing {product} ({grain_name}, {len(c)} candles)...", end=" ", flush=True)

        result = backtest_product(
            product=product,
            candles=c,
            hold_minutes=hold_minutes,
            stop_bps=args.stop_bps,
            target_bps=args.target_bps,
            taker_fee_bps=args.taker_fee_bps,
            momentum_lookback=args.momentum_lookback,
            min_momentum_bps=args.min_momentum_bps,
        )

        all_results[product] = result

        # Print summary
        best_hold = max(result.values(), key=lambda r: r.get("total_net_bps", 0))
        print(f"{best_hold.get('signals_fired', 0)} signals, {best_hold.get('win_rate_pct', 0):.0f}% WR, {best_hold.get('total_net_bps', 0):+.0f}bps total net")

    # Save JSON
    payload = {
        "generated_at": utc_now_iso(),
        "parameters": {
            "products": products,
            "hold_minutes": hold_minutes,
            "taker_fee_bps": args.taker_fee_bps,
            "stop_bps": args.stop_bps,
            "target_bps": args.target_bps,
            "momentum_lookback": args.momentum_lookback,
            "min_momentum_bps": args.min_momentum_bps,
        },
        "results": all_results,
    }
    Path(args.json_path).parent.mkdir(parents=True, exist_ok=True)
    Path(args.json_path).write_text(json.dumps(payload, indent=2, sort_keys=True))

    # Write MD report
    md_lines = [
        "# Kraken Momentum Backtest (Wick-Aware)",
        "",
        f"- Generated: `{payload['generated_at']}`",
        f"- Candle grain: 5m",
        f"- Taker fee: {args.taker_fee_bps}bps/side ({2*args.taker_fee_bps}bps round-trip)",
        f"- Stop: {args.stop_bps}bps, Target: {args.target_bps}bps",
        f"- Momentum lookback: {args.momentum_lookback} candles",
        "",
        "## Results by Product",
        "",
        "| Product | Hold (min) | Signals | Win Rate | Avg Net (bps) | Total Net (bps) | Target Hits | Stops | Timeouts |",
        "|---------|-----------:|--------:|---------:|--------------:|----------------:|------------:|------:|---------:|",
    ]

    for product in products:
        result = all_results.get(product, {})
        for hold_str in [str(h) for h in hold_minutes]:
            r = result.get(hold_str, {})
            if r:
                md_lines.append(
                    f"| {product} | {r.get('hold_minutes', hold_str)}m | {r.get('signals_fired', 0)} | {r.get('win_rate_pct', 0):.1f}% | {r.get('avg_net_bps', 0):+.1f} | {r.get('total_net_bps', 0):+.1f} | {r.get('target_hits', 0)} | {r.get('stops_hit', 0)} | {r.get('timeouts', 0)} |"
                )

    md_lines.extend([
        "",
        "## Interpretation",
        "",
        "- **Wick-aware**: Stops and targets trigger mid-candle if the wick touches them",
        "- **240bps round-trip fees** (taker entry + taker exit) are charged on EVERY trade",
        "- Positive total net bps = the strategy clears fees and generates profit",
        "",
    ])

    # Find winners
    winners = []
    for product, result in all_results.items():
        for hold_str, r in result.items():
            if r.get("total_net_bps", 0) > 0:
                winners.append((product, r["hold_minutes"], r["total_net_bps"], r["win_rate_pct"], r["signals_fired"]))

    if winners:
        md_lines.append(f"✅ **{len(winners)} profitable configurations found**\n")
        md_lines.append("| Product | Hold (min) | Total Net (bps) | Win Rate | Signals |")
        md_lines.append("|---------|-----------:|----------------:|---------:|--------:|")
        for product, hold, total, wr, sig in sorted(winners, key=lambda x: x[2], reverse=True):
            md_lines.append(f"| {product} | {hold}m | {total:+.0f}bps | {wr:.0f}% | {sig} |")
    else:
        md_lines.append("❌ **No profitable configurations found** — all net-negative after fees\n")

    Path(args.md_path).write_text("\n".join(md_lines) + "\n", encoding="utf-8")
    print(f"\n📁 JSON: {args.json_path}")
    print(f"📁 MD: {args.md_path}")


if __name__ == "__main__":
    main()
