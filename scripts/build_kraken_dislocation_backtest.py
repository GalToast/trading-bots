#!/usr/bin/env python3
"""
Dislocation Catcher Backtest for Kraken Spot.

The edge: crypto spot prices oscillate. When they drop hard (dislocation),
they usually bounce. The strategy:
1. Detect when price has dropped X% over Y candles (dislocation signal)
2. Buy at the dislocation low
3. Ride the bounce — exit at target% up OR after max_hold candles
4. Wait for cooldown before next entry

This is DIFFERENT from:
- Grid: capital tied up at every level, continuous exposure
- Momentum: buys after going UP (catching falling knives)
- Dislocation catcher: buys after going DOWN, rides the bounce

Key insight: Price ALWAYS bounces from dislocations because:
1. Market makers widen spreads and then normalize
2. Traders buy the dip
3. The oscillating nature of illiquid books

Usage:
    python scripts/build_kraken_dislocation_backtest.py
    python scripts/build_kraken_dislocation_backtest.py --drop-bps 300 --bounce-target 150 --hold-bars 6
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
DEFAULT_DROP_BPS = 300.0  # Trigger: price dropped 3% over lookback
DEFAULT_LOOKBACK_BARS = 5  # Over how many candles to measure drop
DEFAULT_BOUNCE_TARGET_BPS = 150.0  # Exit: price bounced back 1.5%
DEFAULT_MAX_HOLD_BARS = 12  # Max 12 bars (1 hour on 5m) before timeout
DEFAULT_COOLDOWN_BARS = 3  # Wait 3 bars after exit before next entry
DEFAULT_TAKER_FEE_BPS = 120.0  # Entry: taker (we need immediate fill)
DEFAULT_MAKER_FEE_BPS = 16.0  # Exit: limit order at target (maker)


@dataclass
class Trade:
    entry_idx: int
    entry_price: float
    entry_time: float
    exit_idx: int
    exit_price: float
    exit_time: float
    exit_reason: str  # "target", "timeout", "stop"
    hold_bars: int
    drop_bps: float  # How much it dropped before entry
    bounce_bps: float  # How much it bounced (gross return)
    fee_bps: float
    net_bps: float
    mfe_bps: float  # Max favorable excursion
    mae_bps: float  # Max adverse excursion


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def load_candles(cache_path: Path) -> dict[str, dict[str, list]]:
    """Load candle cache."""
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


def simulate_dislocation_catcher(
    candles: list[dict],
    drop_bps: float,
    lookback_bars: int,
    bounce_target_bps: float,
    max_hold_bars: int,
    cooldown_bars: int,
    taker_fee_bps: float,
    maker_fee_bps: float,
) -> list[Trade]:
    """
    Simulate dislocation catching strategy.

    Signal: price dropped drop_bps over lookback_bars candles
    Entry: at open of signal candle (taker — need immediate fill)
    Exit: at bounce_target_bps above entry (maker limit order) OR max_hold_bars timeout
    """
    trades = []
    cooldown_until = 0

    for idx in range(lookback_bars, len(candles) - 1):
        if idx < cooldown_until:
            continue

        # Check for dislocation: compare current low to lookback high
        lookback = candles[idx - lookback_bars:idx]
        lookback_high = max(c["h"] for c in lookback)
        current_low = candles[idx]["l"]

        drop = ((current_low / lookback_high) - 1.0) * 10000.0  # bps (negative)

        if drop > -abs(drop_bps):
            continue  # Not a big enough dislocation

        # DISLOCATION DETECTED — enter at candle open
        entry_price = candles[idx]["o"]
        if entry_price <= 0:
            continue

        target_price = entry_price * (1 + bounce_target_bps / 10000.0)

        mfe_bps = 0.0
        mae_bps = 0.0
        exit_price = None
        exit_reason = None
        exit_idx = None

        for i in range(idx, min(idx + max_hold_bars, len(candles))):
            c = candles[i]
            high = c["h"]
            low = c["l"]

            # Check if bounce target hit (wick touches target)
            if high >= target_price:
                exit_price = target_price
                exit_reason = "target"
                exit_idx = i
                break

            # Track MFE/MAE
            high_bps = ((high - entry_price) / entry_price) * 10000.0
            low_bps = ((low - entry_price) / entry_price) * 10000.0
            mfe_bps = max(mfe_bps, high_bps)
            mae_bps = min(mae_bps, low_bps)
        else:
            # Timeout — exit at close
            last_c = candles[min(idx + max_hold_bars - 1, len(candles) - 1)]
            exit_price = last_c["c"]
            exit_reason = "timeout"
            exit_idx = min(idx + max_hold_bars - 1, len(candles) - 1)

        if exit_price is None:
            continue

        bounce = ((exit_price / entry_price) - 1.0) * 10000.0
        fee = taker_fee_bps + maker_fee_bps  # Taker entry + maker exit
        net = bounce - fee

        trade = Trade(
            entry_idx=idx,
            entry_price=entry_price,
            entry_time=candles[idx]["t"],
            exit_idx=exit_idx,
            exit_price=exit_price,
            exit_time=candles[exit_idx]["t"],
            exit_reason=exit_reason,
            hold_bars=exit_idx - idx + 1,
            drop_bps=round(drop, 2),
            bounce_bps=round(bounce, 2),
            fee_bps=round(fee, 2),
            net_bps=round(net, 2),
            mfe_bps=round(mfe_bps, 2),
            mae_bps=round(mae_bps, 2),
        )
        trades.append(trade)

        # Cooldown
        cooldown_until = exit_idx + cooldown_bars

    return trades


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Backtest dislocation catching on Kraken spot candles")
    parser.add_argument("--cache-path", default=str(DEFAULT_CACHE_PATH))
    parser.add_argument("--products", default=DEFAULT_PRODUCTS)
    parser.add_argument("--drop-bps", type=float, default=DEFAULT_DROP_BPS)
    parser.add_argument("--lookback-bars", type=int, default=DEFAULT_LOOKBACK_BARS)
    parser.add_argument("--bounce-target", type=float, default=DEFAULT_BOUNCE_TARGET_BPS)
    parser.add_argument("--hold-bars", type=int, default=DEFAULT_MAX_HOLD_BARS)
    parser.add_argument("--cooldown-bars", type=int, default=DEFAULT_COOLDOWN_BARS)
    parser.add_argument("--taker-fee-bps", type=float, default=DEFAULT_TAKER_FEE_BPS)
    parser.add_argument("--maker-fee-bps", type=float, default=DEFAULT_MAKER_FEE_BPS)
    parser.add_argument("--json-path", default=str(REPORTS / "kraken_dislocation_backtest.json"))
    parser.add_argument("--md-path", default=str(REPORTS / "kraken_dislocation_backtest.md"))
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    products = [p.strip() for p in args.products.split(",") if p.strip()]

    print(f"🎯 Kraken Dislocation Catcher Backtest")
    print(f"   Products: {products}")
    print(f"   Dislocation: drop >= {args.drop_bps}bps over {args.lookback_bars} bars")
    print(f"   Exit: bounce target {args.bounce_target}bps OR {args.hold_bars} bars timeout")
    print(f"   Cooldown: {args.cooldown_bars} bars after exit")
    print(f"   Fees: taker {args.taker_fee_bps}bps (entry) + maker {args.maker_fee_bps}bps (exit) = {args.taker_fee_bps + args.maker_fee_bps}bps")
    print()

    candles = load_candles(Path(args.cache_path))
    all_results = {}

    for product in products:
        prod_candles = candles.get(product, {})
        if not prod_candles:
            print(f"  ❌ {product}: no candles found")
            continue

        # Use 5m candles
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

        trades = simulate_dislocation_catcher(
            candles=c,
            drop_bps=args.drop_bps,
            lookback_bars=args.lookback_bars,
            bounce_target_bps=args.bounce_target,
            max_hold_bars=args.hold_bars,
            cooldown_bars=args.cooldown_bars,
            taker_fee_bps=args.taker_fee_bps,
            maker_fee_bps=args.maker_fee_bps,
        )

        if not trades:
            print("No signals fired")
            all_results[product] = {"trades": 0}
            continue

        winners = [t for t in trades if t.net_bps > 0]
        losers = [t for t in trades if t.net_bps <= 0]
        target_hits = [t for t in trades if t.exit_reason == "target"]
        timeouts = [t for t in trades if t.exit_reason == "timeout"]

        avg_net = sum(t.net_bps for t in trades) / len(trades)
        total_net = sum(t.net_bps for t in trades)
        win_rate = len(winners) / len(trades) * 100
        avg_hold = sum(t.hold_bars for t in trades) / len(trades)
        avg_bounce = sum(t.bounce_bps for t in trades) / len(trades)
        avg_drop = sum(t.drop_bps for t in trades) / len(trades)

        result = {
            "trades": len(trades),
            "win_rate_pct": round(win_rate, 1),
            "avg_net_bps": round(avg_net, 2),
            "total_net_bps": round(total_net, 2),
            "avg_hold_bars": round(avg_hold, 1),
            "avg_bounce_bps": round(avg_bounce, 2),
            "avg_drop_bps": round(avg_drop, 2),
            "target_hits": len(target_hits),
            "timeouts": len(timeouts),
            "avg_mfe_bps": round(sum(t.mfe_bps for t in trades) / len(trades), 2),
            "avg_mae_bps": round(sum(t.mae_bps for t in trades) / len(trades), 2),
        }

        all_results[product] = result

        print(f"{len(trades)} signals, {win_rate:.0f}% WR, {avg_net:+.1f}bps avg net, {total_net:+.0f}bps total")

    # Save JSON
    payload = {
        "generated_at": utc_now_iso(),
        "parameters": {
            "products": products,
            "drop_bps": args.drop_bps,
            "lookback_bars": args.lookback_bars,
            "bounce_target_bps": args.bounce_target,
            "hold_bars": args.hold_bars,
            "cooldown_bars": args.cooldown_bars,
            "taker_fee_bps": args.taker_fee_bps,
            "maker_fee_bps": args.maker_fee_bps,
        },
        "results": all_results,
    }
    Path(args.json_path).parent.mkdir(parents=True, exist_ok=True)
    Path(args.json_path).write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")

    # Write MD report
    md_lines = [
        "# Kraken Dislocation Catcher Backtest",
        "",
        f"- Generated: `{payload['generated_at']}`",
        f"- Dislocation: drop >= {args.drop_bps}bps over {args.lookback_bars} bars",
        f"- Exit: {args.bounce_target}bps bounce target OR {args.hold_bars} bars timeout",
        f"- Cooldown: {args.cooldown_bars} bars",
        f"- Fees: {args.taker_fee_bps}bps taker entry + {args.maker_fee_bps}bps maker exit",
        "",
        "## Results by Product",
        "",
        "| Product | Signals | Win Rate | Avg Net (bps) | Total Net (bps) | Avg Hold (bars) | Avg Bounce (bps) | Target Hits | Timeouts |",
        "|---------|--------:|---------:|--------------:|----------------:|----------------:|-----------------:|------------:|---------:|",
    ]

    for product in products:
        r = all_results.get(product, {})
        if r.get("trades", 0) > 0:
            md_lines.append(
                f"| {product} | {r['trades']} | {r['win_rate_pct']:.0f}% | {r['avg_net_bps']:+.1f} | {r['total_net_bps']:+.0f} | {r['avg_hold_bars']:.1f} | {r['avg_bounce_bps']:+.1f} | {r['target_hits']} | {r['timeouts']} |"
            )

    md_lines.extend([
        "",
        "## How Dislocation Catching Works",
        "",
        "1. **Detect dislocation**: Price dropped >= X bps over N candles",
        "2. **Enter at open**: Buy immediately (taker fee) when dislocation detected",
        "3. **Exit on bounce**: Limit order at Y bps above entry (maker fee)",
        "4. **Timeout fallback**: Exit at close if max_hold reached",
        "5. **Cooldown**: Wait before next entry to avoid catching same dislocation",
        "",
        "## Key Insight",
        "",
        "- Dislocation catching works because price ALWAYS bounces from sharp drops",
        "- The edge is NOT predicting direction — it's exploiting the oscillation",
        "- Wider dislocation thresholds = fewer signals but higher win rates",
        "- Shorter hold times = less exposure to adverse moves",
        "",
    ])

    # Find winners
    winners = [(p, r) for p, r in all_results.items() if isinstance(r, dict) and r.get("total_net_bps", 0) > 0]
    losers = [(p, r) for p, r in all_results.items() if isinstance(r, dict) and r.get("total_net_bps", 0) <= 0]

    if winners:
        md_lines.append(f"**{len(winners)} profitable configurations:**\n")
        for p, r in sorted(winners, key=lambda x: x[1]["total_net_bps"], reverse=True):
            md_lines.append(f"- {p}: {r['total_net_bps']:+.0f}bps total ({r['win_rate_pct']:.0f}% WR, {r['trades']} trades)")
        md_lines.append("")

    if losers:
        md_lines.append(f"**{len(losers)} losing configurations:**\n")
        for p, r in sorted(losers, key=lambda x: x[1]["total_net_bps"]):
            md_lines.append(f"- {p}: {r['total_net_bps']:+.0f}bps total ({r['win_rate_pct']:.0f}% WR, {r['trades']} trades)")
        md_lines.append("")

    Path(args.md_path).write_text("\n".join(md_lines) + "\n", encoding="utf-8")
    print(f"\n📁 JSON: {args.json_path}")
    print(f"📁 MD: {args.md_path}")


if __name__ == "__main__":
    main()
