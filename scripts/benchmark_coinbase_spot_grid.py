#!/usr/bin/env python3
"""
Benchmark v2: Grid scalping on Coinbase spot.

Grid scalping places buy/sell limits at fixed intervals around current price.
In a ranging/volatile market, price bounces between levels, each bounce
capturing the grid spacing minus fees.

Key differences from momentum approach:
- No momentum filter — trades both directions (buy low, sell high)
- Fixed grid levels above/below current price
- Quick exits: sell when price reaches next grid level above
- Works in ranging markets (which is most of the time)
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
DEFAULT_REPORT_PATH = ROOT / "reports" / "coinbase_spot_grid_scalp_benchmark.json"


@dataclass
class GridTrade:
    entry_time: int
    entry_price: float
    direction: str
    quantity: float
    exit_time: int = 0
    exit_price: float = 0.0
    exit_reason: str = ""
    gross_pnl: float = 0.0
    fee: float = 0.0
    net_pnl: float = 0.0
    hold_seconds: int = 0


def simulate_grid_scalper(
    candles: list[dict[str, Any]],
    *,
    starting_cash: float,
    maker_fee_bps: float,
    grid_spacing_pct: float,
    grid_levels: int,
    max_hold_seconds: int,
    deploy_pct: float,
    product_id: str,
    use_trailing_stop: bool = False,
    trailing_stop_pct: float = 0.0,
) -> dict[str, Any]:
    """
    Grid scalping simulation.

    Strategy:
    1. Place a grid of buy orders below current price (grid_levels levels)
    2. When a buy fills, immediately place a sell order at grid_spacing above entry
    3. If price drops further, buy more at next grid level (averaging down)
    4. Sell all inventory when price bounces back to the average entry + grid_spacing
    5. Timeout exit if position held too long

    Fill simulation:
    - BUY filled when candle low <= buy_level price
    - SELL filled when candle high >= sell_level price
    - Conservative: require full penetration of level
    """
    if len(candles) < 20:
        return {"error": "not enough candles", "trades": 0}

    fee_rate = maker_fee_bps / 10000.0
    trades: list[GridTrade] = []
    cash = starting_cash
    inventory: list[dict] = []  # [{price, qty, fee}]
    total_fees = 0.0
    grid_buys: list[float] = []  # active buy order levels

    anchor_price = float(candles[0]["close"])
    last_anchor_time = int(candles[0]["time"])

    # Initialize grid buy levels below anchor
    def reset_grid_levels(price: float) -> list[float]:
        levels = []
        for i in range(1, grid_levels + 1):
            levels.append(price * (1 - grid_spacing_pct * i))
        return levels

    grid_buys = reset_grid_levels(anchor_price)
    pending_sells: list[dict] = []  # [{target_price, total_qty, avg_cost}]

    for i in range(1, len(candles)):
        c = candles[i]
        h = float(c["high"])
        l = float(c["low"])
        cl = float(c["close"])
        ts = int(c["time"])

        # Check if grid needs re-anchoring (all positions closed + significant move)
        if not inventory and not pending_sells:
            if abs(cl - anchor_price) / anchor_price > grid_spacing_pct * grid_levels * 0.5:
                anchor_price = cl
                grid_buys = reset_grid_levels(cl)

        # Step 1: Check buy order fills
        new_inventory = []
        filled_buy_indices = []
        for gi, buy_level in enumerate(grid_buys):
            if l <= buy_level:  # price dipped to our buy level
                # Fill the buy order
                deploy_usd = cash * deploy_pct / (grid_levels - gi)  # scale deployment
                deploy_usd = min(deploy_usd, cash)
                if deploy_usd < 0.50:  # Coinbase minimum ~$1
                    continue

                entry_fee = buy_level * (deploy_usd / buy_level) * fee_rate
                qty = (deploy_usd - entry_fee) / buy_level

                if qty > 0 and cash >= deploy_usd:
                    cash -= deploy_usd
                    inventory.append({"price": buy_level, "qty": qty, "entry_fee": entry_fee})
                    total_fees += entry_fee
                    filled_buy_indices.append(gi)

        # Remove filled buy levels (one-time use)
        for idx in sorted(filled_buy_indices, reverse=True):
            grid_buys.pop(idx)

        # Step 2: If we have inventory, check for sell opportunity
        if inventory:
            # Calculate average entry and target sell price
            total_qty = sum(inv["qty"] for inv in inventory)
            total_cost = sum(inv["price"] * inv["qty"] for inv in inventory)
            avg_entry = total_cost / total_qty if total_qty > 0 else 0

            # Sell target: average entry + grid spacing
            sell_target = avg_entry * (1 + grid_spacing_pct)

            # Check if we should sell
            should_sell = False
            exit_price = sell_target
            exit_reason = "grid_tp"

            if h >= sell_target:
                should_sell = True
                exit_price = sell_target
            elif use_trailing_stop and trailing_stop_pct > 0:
                # Check if we hit trailing stop
                peak = max(avg_entry, h)
                if l <= peak * (1 - trailing_stop_pct):
                    should_sell = True
                    exit_price = l
                    exit_reason = "trailing_sl"
            elif (ts - last_anchor_time) >= max_hold_seconds:
                should_sell = True
                exit_price = cl
                exit_reason = "timeout"

            if should_sell and total_qty > 0:
                # Execute sell
                gross = (exit_price - avg_entry) * total_qty
                exit_fee = exit_price * total_qty * fee_rate
                net = gross - exit_fee
                total_fees += exit_fee

                hold = ts - int(candles[max(0, i - 30)]["time"])  # approximate

                trade = GridTrade(
                    entry_time=last_anchor_time,
                    entry_price=avg_entry,
                    direction="BUY",
                    quantity=total_qty,
                    exit_time=ts,
                    exit_price=exit_price,
                    exit_reason=exit_reason,
                    gross_pnl=round(gross, 4),
                    fee=round(exit_fee + sum(inv["entry_fee"] for inv in inventory), 4),
                    net_pnl=round(net, 4),
                    hold_seconds=ts - last_anchor_time,
                )
                trades.append(trade)

                cash += exit_price * total_qty - exit_fee
                inventory = []
                last_anchor_time = ts

                # Reset grid levels around new price
                anchor_price = cl
                grid_buys = reset_grid_levels(cl)

    # Summary
    wins = [t for t in trades if t.net_pnl > 0]
    losses = [t for t in trades if t.net_pnl <= 0]
    tp_exits = [t for t in trades if t.exit_reason == "grid_tp"]
    sl_exits = [t for t in trades if "sl" in t.exit_reason]
    to_exits = [t for t in trades if t.exit_reason == "timeout"]
    holds = [t.hold_seconds for t in trades if t.hold_seconds > 0]

    ending_value = cash
    if inventory:
        # Mark inventory to market at last close
        ending_value += sum(inv["qty"] for inv in inventory) * cl

    return {
        "product_id": product_id,
        "candles_used": len(candles),
        "starting_cash": starting_cash,
        "ending_value": round(ending_value, 2),
        "ending_cash": round(cash, 2),
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
        "timeout_exits": len(to_exits),
        "median_hold_seconds": sorted(holds)[len(holds) // 2] if holds else 0,
        "avg_hold_seconds": round(sum(holds) / len(holds), 1) if holds else 0,
        "total_fees": round(total_fees, 4),
        "total_gross_pnl": round(sum(t.gross_pnl for t in trades), 4),
    }


def fetch_candles(client: CoinbaseAdvancedClient, product_id: str, granularity: str = "ONE_MINUTE", hours: int = 72) -> list[dict[str, Any]]:
    """Fetch historical candles from Coinbase. Paginates in 300-candle chunks."""
    granularity_seconds = {"ONE_MINUTE": 60, "FIVE_MINUTE": 300, "FIFTEEN_MINUTE": 900, "ONE_HOUR": 3600}
    gsec = granularity_seconds.get(granularity, 60)
    max_per_req = 300
    end = int(time.time())
    start = end - (hours * 3600)
    all_candles = []
    seen_times = set()

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
                    "time": t, "open": float(c["open"]), "high": float(c["high"]),
                    "low": float(c["low"]), "close": float(c["close"]), "volume": float(c.get("volume", 0)),
                })
        chunk_end = chunk_start - 1
        time.sleep(0.2)

    return sorted(all_candles, key=lambda x: x["time"])


def main() -> None:
    parser = argparse.ArgumentParser(description="Coinbase grid scalping benchmark v2")
    parser.add_argument("--products", nargs="+", default=["DOGE-USD", "SOL-USD", "XRP-USD", "SUI-USD", "AVAX-USD", "PEPE-USD"])
    parser.add_argument("--hours", type=int, default=72)
    parser.add_argument("--granularity", default="ONE_MINUTE")
    parser.add_argument("--starting-cash", type=float, default=48.0)
    parser.add_argument("--maker-fee-bps", type=float, default=5.0)
    parser.add_argument("--grid-spacing-pct", type=float, default=0.003)  # 0.3% grid
    parser.add_argument("--grid-levels", type=int, default=3)
    parser.add_argument("--max-hold-seconds", type=int, default=1800)
    parser.add_argument("--deploy-pct", type=float, default=0.9)
    parser.add_argument("--trailing-stop", action="store_true")
    parser.add_argument("--trailing-stop-pct", type=float, default=0.002)
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
                results.append({"product_id": pid, "error": "insufficient data"})
                continue

            result = simulate_grid_scalper(
                candles,
                starting_cash=args.starting_cash,
                maker_fee_bps=args.maker_fee_bps,
                grid_spacing_pct=args.grid_spacing_pct,
                grid_levels=args.grid_levels,
                max_hold_seconds=args.max_hold_seconds,
                deploy_pct=args.deploy_pct,
                product_id=pid,
                use_trailing_stop=args.trailing_stop,
                trailing_stop_pct=args.trailing_stop_pct,
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
            "starting_cash": args.starting_cash, "maker_fee_bps": args.maker_fee_bps,
            "grid_spacing_pct": args.grid_spacing_pct, "grid_levels": args.grid_levels,
            "max_hold_seconds": args.max_hold_seconds, "deploy_pct": args.deploy_pct,
            "trailing_stop": args.trailing_stop, "trailing_stop_pct": args.trailing_stop_pct,
            "hours": args.hours, "granularity": args.granularity,
        },
        "results": results,
    }
    out.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(f"\nReport: {out}")

    # Summary
    print(f"\n{'='*90}")
    print(f"{'Product':<14} {'Trades':>6} {'Win%':>6} {'Net $':>10} {'Avg/Tr':>9} {'Med Hold':>10} {'TP/SL/TO':>12}")
    print(f"{'='*90}")
    for r in results:
        if "error" in r:
            print(f"{r['product_id']:<14} {'ERR':>6} {'—':>6} {'—':>10} {'—':>9} {'—':>10} {'—':>12}")
        else:
            print(f"{r['product_id']:<14} {r['total_trades']:>6} {r['win_rate']:>5.1%} ${r['realized_net_usd']:>8.4f} ${r['avg_net_per_trade']:>7.4f} {r['median_hold_seconds']:>8}s {r['tp_exits']}/{r['sl_exits']}/{r['timeout_exits']}")


if __name__ == "__main__":
    main()
