#!/usr/bin/env python3
"""
Benchmark: Maker-order spot scalping on Coinbase.

Tests whether a momentum-filtered maker-limit strategy can survive
fee drag on a $48 Coinbase spot account across volatile alt pairs.

Uses 1-minute candles to simulate:
1. Momentum detection (rate-of-change over lookback window)
2. Simulated maker limit entry at inside market
3. Take-profit / stop-loss exits
4. Maker fee modeling (configurable bps)

This is a SIMULATION — actual limit order fills depend on queue position,
which candles can't perfectly model. We approximate fills using
high/low penetration logic.
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
DEFAULT_REPORT_PATH = ROOT / "reports" / "coinbase_spot_scalp_benchmark.json"


@dataclass
class SimTrade:
    entry_time: int
    entry_price: float
    direction: str  # BUY or SELL
    quantity: float
    exit_time: int = 0
    exit_price: float = 0.0
    exit_reason: str = ""  # "tp", "sl", "timeout", "momentum_reversal"
    gross_pnl: float = 0.0
    fee: float = 0.0
    net_pnl: float = 0.0
    hold_seconds: int = 0


@dataclass
class SimState:
    cash_usd: float
    inventory: float = 0.0  # units held
    realized_net: float = 0.0
    trades: list[SimTrade] = field(default_factory=list)


def roc(prices: list[float], lookback: int) -> float:
    """Rate of change over lookback periods."""
    if len(prices) < lookback + 1:
        return 0.0
    old = prices[-(lookback + 1)]
    new = prices[-1]
    if old == 0:
        return 0.0
    return (new - old) / old


def ema(prices: list[float], period: int) -> float:
    """Simple EMA value (last value only, for signal)."""
    if not prices:
        return 0.0
    k = 2.0 / (period + 1)
    val = prices[0]
    for p in prices[1:]:
        val = p * k + val * (1 - k)
    return val


def simulate_scalper(
    candles: list[dict[str, Any]],
    *,
    starting_cash: float,
    maker_fee_bps: float,
    profit_target_pct: float,
    stop_loss_pct: float,
    momentum_lookback: int,
    momentum_threshold: float,
    max_hold_seconds: int,
    deploy_pct: float,
    product_id: str,
) -> dict[str, Any]:
    """
    Simulate maker-order scalping on historical candles.

    Fill approximation:
    - BUY: if we place a limit at close[i], we model it as filled
      if low[i+1] <= close[i] (price dipped to our level next bar).
      This is conservative — in reality we'd place at bid, not close.
    - EXIT: if high reaches entry * (1 + tp_pct) → take profit
            if low drops to entry * (1 - sl_pct) → stop loss

    Momentum filter:
    - Only BUY if ROC(momentum_lookback) > momentum_threshold
    - Only SELL (short not possible on spot, so skip) 
    - For spot-long only: buy dips in uptrend, sell on bounce
    """
    if len(candles) < momentum_lookback + 10:
        return {"error": "not enough candles", "trades": 0}

    state = SimState(cash_usd=starting_cash)
    price_history: list[float] = []
    in_position = False
    current_trade: SimTrade | None = None

    fee_rate = maker_fee_bps / 10000.0

    for i in range(len(candles)):
        c = candles[i]
        o = float(c["open"])
        h = float(c["high"])
        l = float(c["low"])
        cl = float(c["close"])
        ts = int(c["time"])

        price_history.append(cl)

        # Check exit conditions for open position
        if in_position and current_trade is not None:
            hold = ts - current_trade.entry_time

            # Take profit: did high reach our target?
            tp_price = current_trade.entry_price * (1 + profit_target_pct)
            sl_price = current_trade.entry_price * (1 - stop_loss_pct)

            if h >= tp_price:
                # Filled at take profit
                current_trade.exit_price = tp_price
                current_trade.exit_time = ts
                current_trade.exit_reason = "tp"
                current_trade.hold_seconds = hold
            elif l <= sl_price:
                # Stop loss hit
                current_trade.exit_price = sl_price
                current_trade.exit_time = ts
                current_trade.exit_reason = "sl"
                current_trade.hold_seconds = hold
            elif hold >= max_hold_seconds:
                # Timeout exit — exit at close
                current_trade.exit_price = cl
                current_trade.exit_time = ts
                current_trade.exit_reason = "timeout"
                current_trade.hold_seconds = hold

            if current_trade.exit_time > 0:
                # Calculate PnL
                qty = current_trade.quantity
                gross = (current_trade.exit_price - current_trade.entry_price) * qty
                # Fee on exit (maker)
                exit_fee = current_trade.exit_price * qty * fee_rate
                net = gross - exit_fee

                current_trade.gross_pnl = round(gross, 4)
                current_trade.fee = round(exit_fee, 4)
                current_trade.net_pnl = round(net, 4)

                state.realized_net += net
                state.cash_usd += current_trade.exit_price * qty - exit_fee
                state.inventory -= qty
                state.trades.append(current_trade)

                in_position = False
                current_trade = None

        # Check entry conditions (only if not in position)
        if not in_position and len(price_history) > momentum_lookback + 1:
            momentum = roc(price_history, momentum_lookback)

            # BUY signal: momentum positive and price dipped
            if momentum > momentum_threshold:
                # Simulate placing a maker buy limit at current close (bid proxy)
                # Filled next bar if low dips to our price
                if i + 1 < len(candles):
                    next_l = float(candles[i + 1]["low"])
                    next_ts = int(candles[i + 1]["time"])
                    next_cl = float(candles[i + 1]["close"])

                    if next_l <= cl:
                        # Fill confirmed
                        deploy_usd = state.cash_usd * deploy_pct
                        entry_fee = cl * (deploy_usd / cl) * fee_rate
                        qty = (deploy_usd - entry_fee) / cl

                        if qty > 0 and state.cash_usd >= deploy_usd:
                            state.cash_usd -= deploy_usd
                            state.inventory += qty

                            current_trade = SimTrade(
                                entry_time=next_ts,
                                entry_price=cl,
                                direction="BUY",
                                quantity=qty,
                            )
                            # Entry fee already deducted from cash
                            current_trade.fee = round(entry_fee, 4)

                            in_position = True

    # Summary
    trades = state.trades
    wins = [t for t in trades if t.net_pnl > 0]
    losses = [t for t in trades if t.net_pnl <= 0]
    tp_exits = [t for t in trades if t.exit_reason == "tp"]
    sl_exits = [t for t in trades if t.exit_reason == "sl"]
    to_exits = [t for t in trades if t.exit_reason == "timeout"]

    holds = [t.hold_seconds for t in trades if t.hold_seconds > 0]

    return {
        "product_id": product_id,
        "candles_used": len(candles),
        "starting_cash": starting_cash,
        "ending_cash": round(state.cash_usd + state.inventory * cl if trades else starting_cash, 2),
        "realized_net_usd": round(state.realized_net, 4),
        "total_trades": len(trades),
        "wins": len(wins),
        "losses": len(losses),
        "win_rate": round(len(wins) / len(trades), 3) if trades else 0,
        "avg_net_per_trade": round(state.realized_net / len(trades), 4) if trades else 0,
        "avg_win": round(sum(t.net_pnl for t in wins) / len(wins), 4) if wins else 0,
        "avg_loss": round(sum(t.net_pnl for t in losses) / len(losses), 4) if losses else 0,
        "tp_exits": len(tp_exits),
        "sl_exits": len(sl_exits),
        "timeout_exits": len(to_exits),
        "median_hold_seconds": round(sorted(holds)[len(holds) // 2]) if holds else 0,
        "avg_hold_seconds": round(sum(holds) / len(holds), 1) if holds else 0,
        "total_fees": round(sum(t.fee for t in trades), 4),
        "total_gross_pnl": round(sum(t.gross_pnl for t in trades), 4),
    }


def fetch_candles(client: CoinbaseAdvancedClient, product_id: str, granularity: str = "ONE_MINUTE", hours: int = 72) -> list[dict[str, Any]]:
    """Fetch historical candles from Coinbase. Paginates in 300-candle chunks."""
    granularity_seconds = {
        "ONE_MINUTE": 60,
        "FIVE_MINUTE": 300,
        "FIFTEEN_MINUTE": 900,
        "ONE_HOUR": 3600,
    }
    gsec = granularity_seconds.get(granularity, 60)
    max_per_req = 300  # Coinbase API limit
    end = int(time.time())
    start = end - (hours * 3600)
    all_candles = []
    seen_times = set()

    # Fetch in chunks of max_per_req * gsec seconds
    chunk_end = end
    while chunk_end > start:
        chunk_start = max(start, chunk_end - max_per_req * gsec)
        resp = client.market_candles(product_id, start=chunk_start, end=chunk_end, granularity=granularity)
        raw = resp.get("candles") or []
        if not raw:
            break
        for c in raw:
            t = int(c["start"])
            if t not in seen_times:
                seen_times.add(t)
                all_candles.append({
                    "time": t,
                    "open": float(c["open"]),
                    "high": float(c["high"]),
                    "low": float(c["low"]),
                    "close": float(c["close"]),
                    "volume": float(c.get("volume", 0)),
                })
        chunk_end = chunk_start - 1
        time.sleep(0.2)  # rate limit courtesy

    return sorted(all_candles, key=lambda x: x["time"])


def main() -> None:
    parser = argparse.ArgumentParser(description="Coinbase spot scalping benchmark")
    parser.add_argument("--products", nargs="+", default=["DOGE-USD", "SOL-USD", "XRP-USD", "SUI-USD", "AVAX-USD"])
    parser.add_argument("--hours", type=int, default=72)
    parser.add_argument("--granularity", default="ONE_MINUTE")
    parser.add_argument("--starting-cash", type=float, default=48.0)
    parser.add_argument("--maker-fee-bps", type=float, default=5.0)
    parser.add_argument("--profit-target-pct", type=float, default=0.005)  # 0.5%
    parser.add_argument("--stop-loss-pct", type=float, default=0.003)  # 0.3%
    parser.add_argument("--momentum-lookback", type=int, default=5)
    parser.add_argument("--momentum-threshold", type=float, default=0.0005)  # 0.05% ROC
    parser.add_argument("--max-hold-seconds", type=int, default=1800)  # 30 min
    parser.add_argument("--deploy-pct", type=float, default=1.0)  # deploy all cash
    parser.add_argument("--output", default=str(DEFAULT_REPORT_PATH))
    args = parser.parse_args()

    client = CoinbaseAdvancedClient()
    results = []

    for pid in args.products:
        print(f"\n{'='*60}")
        print(f"  {pid}")
        print(f"{'='*60}")

        try:
            candles = fetch_candles(client, pid, args.granularity, args.hours)
            print(f"  Fetched {len(candles)} candles")

            if len(candles) < 20:
                print(f"  Skipping — not enough data")
                results.append({"product_id": pid, "error": "insufficient data", "candles": len(candles)})
                continue

            result = simulate_scalper(
                candles,
                starting_cash=args.starting_cash,
                maker_fee_bps=args.maker_fee_bps,
                profit_target_pct=args.profit_target_pct,
                stop_loss_pct=args.stop_loss_pct,
                momentum_lookback=args.momentum_lookback,
                momentum_threshold=args.momentum_threshold,
                max_hold_seconds=args.max_hold_seconds,
                deploy_pct=args.deploy_pct,
                product_id=pid,
            )

            print(f"  Trades: {result.get('total_trades', 0)}")
            print(f"  Win rate: {result.get('win_rate', 0):.1%}")
            print(f"  Realized net: ${result.get('realized_net_usd', 0):+.4f}")
            print(f"  Avg/trade: ${result.get('avg_net_per_trade', 0):+.4f}")
            print(f"  Median hold: {result.get('median_hold_seconds', 0)}s")
            print(f"  TP/SL/TO: {result.get('tp_exits', 0)}/{result.get('sl_exits', 0)}/{result.get('timeout_exits', 0)}")

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
            "profit_target_pct": args.profit_target_pct,
            "stop_loss_pct": args.stop_loss_pct,
            "momentum_lookback": args.momentum_lookback,
            "momentum_threshold": args.momentum_threshold,
            "max_hold_seconds": args.max_hold_seconds,
            "deploy_pct": args.deploy_pct,
            "hours": args.hours,
            "granularity": args.granularity,
        },
        "results": results,
    }
    out.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(f"\nReport written to: {out}")

    # Summary table
    print(f"\n{'='*80}")
    print(f"{'Product':<15} {'Trades':>6} {'Win%':>6} {'Net $':>10} {'Avg/Tr':>9} {'Med Hold':>10} {'TP/SL/TO':>10}")
    print(f"{'='*80}")
    for r in results:
        if "error" in r:
            print(f"{r['product_id']:<15} {'ERR':>6} {'—':>6} {'—':>10} {'—':>9} {'—':>10} {'—':>10}")
        else:
            print(f"{r['product_id']:<15} {r['total_trades']:>6} {r['win_rate']:>5.1%} ${r['realized_net_usd']:>8.4f} ${r['avg_net_per_trade']:>7.4f} {r['median_hold_seconds']:>8}s {r['tp_exits']}/{r['sl_exits']}/{r['timeout_exits']}")


if __name__ == "__main__":
    main()
