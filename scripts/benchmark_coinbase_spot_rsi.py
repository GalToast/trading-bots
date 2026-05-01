#!/usr/bin/env python3
"""
Spot trading system: RSI mean reversion + volume filter.

Tests whether RSI-based oversold/overbought entries on 5-min candles,
combined with volume confirmation, can survive fee drag on Coinbase spot.

Key differences from prior approaches:
- RSI entries instead of grid levels or momentum thresholds
- Volume filter: only trade when volume > 1.5x average (confirms the move)
- 5-min candles (less noise than 1-min)
- Asymmetric exits: take profit at 1.5x the stop loss distance
"""
from __future__ import annotations

import argparse
import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from coinbase_advanced_client import CoinbaseAdvancedClient

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_REPORT_PATH = ROOT / "reports" / "coinbase_spot_rsi_scalp_benchmark.json"


@dataclass
class RSITrade:
    entry_time: int
    entry_price: float
    direction: str  # always BUY for spot
    quantity: float
    entry_rsi: float = 0.0
    entry_bar: int = 0
    entry_fee: float = 0.0
    exit_time: int = 0
    exit_price: float = 0.0
    exit_reason: str = ""
    exit_rsi: float = 0.0
    gross_pnl: float = 0.0
    fee: float = 0.0
    net_pnl: float = 0.0
    hold_bars: int = 0


def rsi(closes: list[float], period: int = 14) -> list[float]:
    """Compute RSI for a series of closing prices."""
    if len(closes) < period + 1:
        return [50.0] * len(closes)

    deltas = [closes[i] - closes[i - 1] for i in range(1, len(closes))]

    gains = [d if d > 0 else 0 for d in deltas]
    losses = [-d if d < 0 else 0 for d in deltas]

    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period

    result = [50.0] * period
    if avg_loss > 0:
        rs = avg_gain / avg_loss
        result.append(100 - 100 / (1 + rs))
    else:
        result.append(100.0)

    for i in range(period, len(deltas)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period
        if avg_loss > 0:
            rs = avg_gain / avg_loss
            result.append(100 - 100 / (1 + rs))
        else:
            result.append(100.0)

    return result


def run_rsi_system(
    candles: list[dict[str, Any]],
    *,
    starting_cash: float,
    maker_fee_bps: float,
    rsi_period: int,
    oversold_threshold: float,
    overbought_threshold: float,
    profit_target_pct: float,
    stop_loss_pct: float,
    max_hold_bars: int,
    volume_filter_mult: float,
    deploy_pct: float,
    product_id: str,
) -> dict[str, Any]:
    """
    RSI mean reversion system simulation.

    Rules:
    1. BUY when RSI < oversold_threshold AND volume > volume_filter_mult * avg_volume
    2. SELL when:
       a. Price reaches entry * (1 + profit_target_pct) → "tp"
       b. Price drops to entry * (1 - stop_loss_pct) → "sl"
       c. RSI crosses above overbought_threshold → "rsi_exit"
       d. Held for max_hold_bars → "timeout"
    """
    if len(candles) < rsi_period + 20:
        return {"error": "not enough candles", "trades": 0}

    closes = [c["close"] for c in candles]
    volumes = [c["volume"] for c in candles]
    rsi_values = rsi(closes, rsi_period)

    fee_rate = maker_fee_bps / 10000.0
    trades: list[RSITrade] = []
    cash = starting_cash
    in_position = False
    current_trade: RSITrade | None = None
    avg_volume_20 = 0.0

    for i in range(rsi_period + 1, len(candles) - 1):
        c = candles[i]
        h = float(c["high"])
        l = float(c["low"])
        cl = float(c["close"])
        vol = float(c["volume"])
        current_rsi = rsi_values[i]
        ts = int(c["time"])

        # Calculate rolling average volume
        vol_window = volumes[max(0, i - 20):i]
        avg_vol = sum(vol_window) / len(vol_window) if vol_window else 1

        # Check exit conditions for open position
        if in_position and current_trade is not None:
            # Take profit
            tp_price = current_trade.entry_price * (1 + profit_target_pct)
            sl_price = current_trade.entry_price * (1 - stop_loss_pct)

            if h >= tp_price:
                current_trade.exit_price = tp_price
                current_trade.exit_reason = "tp"
            elif l <= sl_price:
                current_trade.exit_price = sl_price
                current_trade.exit_reason = "sl"
            elif current_rsi >= overbought_threshold:
                current_trade.exit_price = cl
                current_trade.exit_reason = "rsi_exit"
            elif (i - current_trade.entry_bar) >= max_hold_bars:
                current_trade.exit_price = cl
                current_trade.exit_reason = "timeout"

            if current_trade.exit_reason:
                qty = current_trade.quantity
                gross = (current_trade.exit_price - current_trade.entry_price) * qty
                exit_fee = current_trade.exit_price * qty * fee_rate
                net = gross - exit_fee

                current_trade.gross_pnl = round(gross, 4)
                current_trade.fee = round(exit_fee + current_trade.entry_fee, 4)
                current_trade.net_pnl = round(net, 4)
                current_trade.hold_bars = i - current_trade.entry_bar
                current_trade.exit_rsi = round(current_rsi, 2)

                cash += current_trade.exit_price * qty - exit_fee
                in_position = False
                trades.append(current_trade)
                current_trade = None

        # Check entry conditions (only if not in position)
        if not in_position:
            # RSI oversold + volume spike
            if current_rsi <= oversold_threshold and avg_vol > 0 and vol > avg_vol * volume_filter_mult:
                deploy_usd = cash * deploy_pct
                if deploy_usd >= 1.0:  # Coinbase minimum
                    entry_price = cl
                    entry_fee = entry_price * (deploy_usd / entry_price) * fee_rate
                    qty = (deploy_usd - entry_fee) / entry_price

                    if qty > 0:
                        cash -= deploy_usd
                        current_trade = RSITrade(
                            entry_time=ts,
                            entry_price=entry_price,
                            direction="BUY",
                            quantity=qty,
                            entry_rsi=round(current_rsi, 2),
                            entry_bar=i,
                            entry_fee=round(entry_fee, 4),
                        )
                        in_position = True

    # Summary
    wins = [t for t in trades if t.net_pnl > 0]
    losses = [t for t in trades if t.net_pnl <= 0]
    tp_exits = [t for t in trades if t.exit_reason == "tp"]
    sl_exits = [t for t in trades if t.exit_reason == "sl"]
    rsi_exits = [t for t in trades if t.exit_reason == "rsi_exit"]
    to_exits = [t for t in trades if t.exit_reason == "timeout"]
    holds = [t.hold_bars for t in trades if t.hold_bars > 0]

    return {
        "product_id": product_id,
        "candles_used": len(candles),
        "starting_cash": starting_cash,
        "realized_net_usd": round(sum(t.net_pnl for t in trades), 4),
        "total_trades": len(trades),
        "wins": len(wins),
        "losses": len(losses),
        "win_rate": round(len(wins) / len(trades), 3) if trades else 0,
        "avg_net_per_trade": round(sum(t.net_pnl for t in trades) / len(trades), 4) if trades else 0,
        "avg_win": round(sum(t.net_pnl for t in wins) / len(wins), 4) if wins else 0,
        "avg_loss": round(sum(t.net_pnl for t in losses) / len(losses), 4) if losses else 0,
        "tp_exits": len(tp_exits),
        "sl_exits": len(sl_exits),
        "rsi_exits": len(rsi_exits),
        "timeout_exits": len(to_exits),
        "median_hold_bars": sorted(holds)[len(holds) // 2] if holds else 0,
        "avg_hold_bars": round(sum(holds) / len(holds), 1) if holds else 0,
        "total_fees": round(sum(t.fee for t in trades), 4),
        "total_gross_pnl": round(sum(t.gross_pnl for t in trades), 4),
        "avg_entry_rsi": round(sum(t.entry_rsi for t in trades) / len(trades), 1) if trades else 0,
    }


def fetch_candles_72h(client: CoinbaseAdvancedClient, product_id: str, granularity: str = "FIVE_MINUTE") -> list[dict]:
    gsec_map = {"FIVE_MINUTE": 300, "ONE_MINUTE": 60, "FIFTEEN_MINUTE": 900}
    gsec = gsec_map.get(granularity, 300)
    max_per_req = 300
    end = int(time.time())
    start = end - (72 * 3600)
    all_candles = []
    seen = set()
    chunk_end = end
    while chunk_end > start:
        chunk_start = max(start, chunk_end - max_per_req * gsec)
        resp = client.market_candles(product_id, start=chunk_start, end=chunk_end, granularity=granularity)
        raw = resp.get("candles") or []
        if not raw:
            break
        for c in raw:
            t = int(c["start"])
            if t not in seen:
                seen.add(t)
                all_candles.append({
                    "time": t, "open": float(c["open"]), "high": float(c["high"]),
                    "low": float(c["low"]), "close": float(c["close"]), "volume": float(c.get("volume", 0)),
                })
        chunk_end = chunk_start - 1
        time.sleep(0.15)
    return sorted(all_candles, key=lambda x: x["time"])


def main() -> None:
    parser = argparse.ArgumentParser(description="Coinbase RSI mean reversion benchmark")
    parser.add_argument("--products", nargs="+", default=["COMP-USD", "FLOKI-USD", "WIF-USD", "SNX-USD", "ARB-USD", "SOL-USD"])
    parser.add_argument("--granularity", default="FIVE_MINUTE")
    parser.add_argument("--hours", type=int, default=72)
    parser.add_argument("--starting-cash", type=float, default=48.0)
    parser.add_argument("--maker-fee-bps", type=float, default=5.0)
    parser.add_argument("--rsi-period", type=int, default=14)
    parser.add_argument("--oversold", type=float, default=30.0)
    parser.add_argument("--overbought", type=float, default=70.0)
    parser.add_argument("--profit-target-pct", type=float, default=0.01)
    parser.add_argument("--stop-loss-pct", type=float, default=0.005)
    parser.add_argument("--max-hold-bars", type=int, default=24)
    parser.add_argument("--volume-filter-mult", type=float, default=1.5)
    parser.add_argument("--deploy-pct", type=float, default=0.9)
    parser.add_argument("--output", default=str(DEFAULT_REPORT_PATH))
    args = parser.parse_args()

    client = CoinbaseAdvancedClient()
    results = []

    for pid in args.products:
        print(f"\n{'='*60}")
        print(f"  {pid}")
        print(f"{'='*60}")

        try:
            candles = fetch_candles_72h(client, pid, args.granularity)
            print(f"  Fetched {len(candles)} candles")

            if len(candles) < args.rsi_period + 20:
                print(f"  Skipping — not enough data")
                results.append({"product_id": pid, "error": "insufficient data"})
                continue

            result = run_rsi_system(
                candles,
                starting_cash=args.starting_cash,
                maker_fee_bps=args.maker_fee_bps,
                rsi_period=args.rsi_period,
                oversold_threshold=args.oversold,
                overbought_threshold=args.overbought,
                profit_target_pct=args.profit_target_pct,
                stop_loss_pct=args.stop_loss_pct,
                max_hold_bars=args.max_hold_bars,
                volume_filter_mult=args.volume_filter_mult,
                deploy_pct=args.deploy_pct,
                product_id=pid,
            )

            print(f"  Trades: {result.get('total_trades', 0)}")
            print(f"  Win rate: {result.get('win_rate', 0):.1%}")
            print(f"  Realized net: ${result.get('realized_net_usd', 0):+.4f}")
            print(f"  Avg/trade: ${result.get('avg_net_per_trade', 0):+.4f}")
            print(f"  Median hold: {result.get('median_hold_bars', 0)} bars")
            print(f"  TP/SL/RSI/TO: {result.get('tp_exits', 0)}/{result.get('sl_exits', 0)}/{result.get('rsi_exits', 0)}/{result.get('timeout_exits', 0)}")
            print(f"  Avg entry RSI: {result.get('avg_entry_rsi', 0):.1f}")
            results.append(result)

        except Exception as e:
            print(f"  ERROR: {e}")
            results.append({"product_id": pid, "error": str(e)})

    # Write report
    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    report = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "params": {
            "starting_cash": args.starting_cash,
            "maker_fee_bps": args.maker_fee_bps,
            "rsi_period": args.rsi_period,
            "oversold_threshold": args.oversold,
            "overbought_threshold": args.overbought,
            "profit_target_pct": args.profit_target_pct,
            "stop_loss_pct": args.stop_loss_pct,
            "max_hold_bars": args.max_hold_bars,
            "volume_filter_mult": args.volume_filter_mult,
            "deploy_pct": args.deploy_pct,
            "hours": args.hours,
            "granularity": args.granularity,
        },
        "results": results,
    }
    out.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(f"\nReport: {out}")

    # Summary table
    print(f"\n{'='*95}")
    print(f"{'Product':<14} {'Trades':>6} {'Win%':>6} {'Net $':>10} {'Avg/Tr':>9} {'Med Hold':>10} {'TP/SL/RSI/TO':>14} {'AvgRSI':>7}")
    print(f"{'='*95}")
    for r in results:
        if "error" in r:
            print(f"{r['product_id']:<14} {'ERR':>6} {'—':>6} {'—':>10} {'—':>9} {'—':>10} {'—':>14} {'—':>7}")
        else:
            print(f"{r['product_id']:<14} {r['total_trades']:>6} {r['win_rate']:>5.1%} ${r['realized_net_usd']:>8.4f} ${r['avg_net_per_trade']:>7.4f} {r['median_hold_bars']:>8}b {r['tp_exits']}/{r['sl_exits']}/{r['rsi_exits']}/{r['timeout_exits']} {r['avg_entry_rsi']:>6.1f}")


if __name__ == "__main__":
    main()
