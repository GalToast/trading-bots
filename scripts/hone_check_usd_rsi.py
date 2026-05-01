#!/usr/bin/env python3
"""
Edge hone: Deep optimization of CHECK-USD RSI mean reversion.

Tests every parameter variation to find the TRUE optimal configuration:
- RSI period: 5-14
- Oversold: 20-40
- Overbought: 60-80
- Profit target: 1-3%
- Stop loss: 0.2-0.5%
- Deploy %: 50-100%
- Volume filter: 0.5-3.0x
- Max hold bars: 12-96
- Trailing stop: on/off, 0.5-2.0%
- Scaling: partial exits at 50% TP
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from coinbase_advanced_client import CoinbaseAdvancedClient

ROOT = Path(__file__).resolve().parent.parent
REPORT_PATH = ROOT / "reports" / "check_usd_rsi_hone.json"


@dataclass
class Trade:
    entry_bar: int
    entry_time: int
    entry_price: float
    quantity: float
    entry_fee: float
    rsi_at_entry: float
    exit_bar: int = 0
    exit_time: int = 0
    exit_price: float = 0.0
    exit_reason: str = ""
    rsi_at_exit: float = 0.0
    gross_pnl: float = 0.0
    total_fee: float = 0.0
    net_pnl: float = 0.0
    hold_bars: int = 0
    peak_unrealized: float = 0.0


def rsi(closes: list[float], period: int) -> list[float]:
    if len(closes) < period + 1:
        return [50.0] * len(closes)
    deltas = [closes[i] - closes[i - 1] for i in range(1, len(closes))]
    gains = [d if d > 0 else 0 for d in deltas]
    losses = [-d if d < 0 else 0 for d in deltas]
    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period
    result = [50.0] * period
    if avg_loss > 0:
        result.append(100 - 100 / (1 + avg_gain / avg_loss))
    else:
        result.append(100.0)
    for i in range(period, len(deltas)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period
        if avg_loss > 0:
            result.append(100 - 100 / (1 + avg_gain / avg_loss))
        else:
            result.append(100.0)
    return result


def run_single_config(
    candles: list[dict],
    *,
    rsi_period: int,
    oversold: float,
    overbought: float,
    profit_target_pct: float,
    stop_loss_pct: float,
    deploy_pct: float,
    volume_filter_mult: float,
    max_hold_bars: int,
    maker_fee_bps: float,
    starting_cash: float,
    use_trailing_stop: bool = False,
    trailing_stop_pct: float = 0.0,
    partial_exit_pct: float = 0.0,  # exit 50% at 50% of TP, rest at full TP
) -> dict[str, Any]:
    """Run one configuration and return results."""
    if len(candles) < rsi_period + 20:
        return {"error": "insufficient candles"}

    closes = [c["close"] for c in candles]
    volumes = [c["volume"] for c in candles]
    rsi_values = rsi(closes, rsi_period)
    fee_rate = maker_fee_bps / 10000.0

    cash = starting_cash
    trades: list[Trade] = []
    in_position = False
    current_trade: Trade | None = None

    for i in range(rsi_period + 1, len(candles) - 1):
        c = candles[i]
        h = float(c["high"])
        l = float(c["low"])
        cl = float(c["close"])
        vol = float(c["volume"])
        current_rsi = rsi_values[i]
        ts = int(c["time"])

        # Volume filter
        vol_window = volumes[max(0, i - 20):i]
        avg_vol = sum(vol_window) / len(vol_window) if vol_window else 1
        if volume_filter_mult > 0 and vol < avg_vol * volume_filter_mult:
            # Still process exits, but don't enter
            pass

        # Exit logic
        if in_position and current_trade is not None:
            tp_price = current_trade.entry_price * (1 + profit_target_pct)
            sl_price = current_trade.entry_price * (1 - stop_loss_pct)

            # Track peak unrealized
            peak_pnl = (h - current_trade.entry_price) / current_trade.entry_price
            if peak_pnl > current_trade.peak_unrealized:
                current_trade.peak_unrealized = peak_pnl

            # Trailing stop
            effective_sl = sl_price
            if use_trailing_stop and trailing_stop_pct > 0 and current_trade.peak_unrealized > 0:
                trail_price = current_trade.entry_price * (1 + current_trade.peak_unrealized)
                effective_sl = trail_price * (1 - trailing_stop_pct)
                if effective_sl < sl_price:
                    effective_sl = sl_price  # Don't trail below original stop

            # Partial exit
            if partial_exit_pct > 0:
                half_tp = current_trade.entry_price * (1 + profit_target_pct * partial_exit_pct)
                if h >= half_tp and current_trade.exit_reason == "":
                    # Exit half the position
                    half_qty = current_trade.quantity * 0.5
                    half_gross = (half_tp - current_trade.entry_price) * half_qty
                    half_fee = half_tp * half_qty * fee_rate
                    half_net = half_gross - half_fee

                    current_trade.quantity = half_qty
                    current_trade.gross_pnl += half_gross
                    current_trade.total_fee += half_fee
                    cash += half_tp * half_qty - half_fee

                    # Update remaining position targets
                    tp_price = current_trade.entry_price * (1 + profit_target_pct)

            # Check exit conditions
            exit_reason = ""
            exit_price = cl

            if h >= tp_price:
                exit_reason = "tp"
                exit_price = tp_price
            elif l <= effective_sl:
                exit_reason = "sl"
                exit_price = effective_sl
            elif current_rsi >= overbought:
                exit_reason = "rsi_exit"
                exit_price = cl
            elif (i - current_trade.entry_bar) >= max_hold_bars:
                exit_reason = "timeout"
                exit_price = cl

            if exit_reason and current_trade.exit_reason == "":
                qty = current_trade.quantity
                gross = (exit_price - current_trade.entry_price) * qty
                exit_fee = exit_price * qty * fee_rate
                net = gross - exit_fee

                current_trade.exit_price = exit_price
                current_trade.exit_reason = exit_reason
                current_trade.rsi_at_exit = round(current_rsi, 2)
                current_trade.gross_pnl += round(gross, 4)
                current_trade.total_fee += round(exit_fee, 4)
                current_trade.net_pnl = round(current_trade.gross_pnl + gross - exit_fee, 4)
                current_trade.hold_bars = i - current_trade.entry_bar
                current_trade.exit_time = ts

                cash += exit_price * qty - exit_fee
                in_position = False
                trades.append(current_trade)
                current_trade = None

        # Entry logic (only if volume filter passes)
        if not in_position:
            passes_vol = volume_filter_mult <= 0 or (avg_vol > 0 and vol >= avg_vol * volume_filter_mult)

            if passes_vol and current_rsi <= oversold:
                deploy_usd = cash * deploy_pct
                if deploy_usd >= 1.0:
                    entry_price = cl
                    entry_fee = entry_price * (deploy_usd / entry_price) * fee_rate
                    qty = (deploy_usd - entry_fee) / entry_price

                    if qty > 0:
                        cash -= deploy_usd
                        current_trade = Trade(
                            entry_bar=i,
                            entry_time=ts,
                            entry_price=entry_price,
                            quantity=qty,
                            entry_fee=round(entry_fee, 6),
                            rsi_at_entry=round(current_rsi, 2),
                        )
                        in_position = True

    # Close any open position at end
    if in_position and current_trade is not None and len(candles) > 0:
        last_c = candles[-1]
        qty = current_trade.quantity
        exit_price = float(last_c["close"])
        gross = (exit_price - current_trade.entry_price) * qty
        exit_fee = exit_price * qty * fee_rate
        net = gross - exit_fee

        current_trade.exit_price = exit_price
        current_trade.exit_reason = "end_of_data"
        current_trade.gross_pnl = round(gross, 4)
        current_trade.total_fee = round(current_trade.entry_fee + exit_fee, 4)
        current_trade.net_pnl = round(net, 4)
        current_trade.hold_bars = len(candles) - 1 - current_trade.entry_bar
        trades.append(current_trade)

    # Compute results
    wins = [t for t in trades if t.net_pnl > 0]
    losses = [t for t in trades if t.net_pnl <= 0]
    tp_exits = [t for t in trades if t.exit_reason == "tp"]
    sl_exits = [t for t in trades if t.exit_reason == "sl"]
    rsi_exits = [t for t in trades if t.exit_reason == "rsi_exit"]
    to_exits = [t for t in trades if t.exit_reason == "timeout"]
    holds = [t.hold_bars for t in trades if t.hold_bars > 0]
    max_dd = 0
    peak_equity = starting_cash
    equity = starting_cash
    for t in trades:
        equity += t.net_pnl
        if equity > peak_equity:
            peak_equity = equity
        dd = (peak_equity - equity) / peak_equity
        if dd > max_dd:
            max_dd = dd

    return {
        "trades": len(trades),
        "wins": len(wins),
        "losses": len(losses),
        "win_rate": round(len(wins) / len(trades), 4) if trades else 0,
        "realized_net": round(sum(t.net_pnl for t in trades), 4),
        "return_pct": round(sum(t.net_pnl for t in trades) / starting_cash * 100, 4) if starting_cash > 0 else 0,
        "avg_net_per_trade": round(sum(t.net_pnl for t in trades) / len(trades), 4) if trades else 0,
        "avg_win": round(sum(t.net_pnl for t in wins) / len(wins), 4) if wins else 0,
        "avg_loss": round(sum(t.net_pnl for t in losses) / len(losses), 4) if losses else 0,
        "best_trade": round(max((t.net_pnl for t in trades), default=0), 4),
        "worst_trade": round(min((t.net_pnl for t in trades), default=0), 4),
        "profit_factor": round(sum(t.net_pnl for t in wins) / abs(sum(t.net_pnl for t in losses)), 3) if losses and sum(t.net_pnl for t in losses) != 0 else float("inf") if wins else 0,
        "max_drawdown_pct": round(max_dd * 100, 2),
        "tp_exits": len(tp_exits),
        "sl_exits": len(sl_exits),
        "rsi_exits": len(rsi_exits),
        "timeout_exits": len(to_exits),
        "median_hold_bars": sorted(holds)[len(holds) // 2] if holds else 0,
        "avg_hold_bars": round(sum(holds) / len(holds), 1) if holds else 0,
        "total_fees": round(sum(t.total_fee for t in trades), 4),
        "total_gross_pnl": round(sum(t.gross_pnl for t in trades), 4),
        "avg_entry_rsi": round(sum(t.rsi_at_entry for t in trades) / len(trades), 1) if trades else 0,
        "peak_unrealized_avg": round(sum(t.peak_unrealized for t in trades) / len(trades), 4) if trades else 0,
        "ending_cash": round(cash, 2),
    }


def fetch_candles_7d(client: CoinbaseAdvancedClient, product_id: str, granularity: str = "FIVE_MINUTE") -> list[dict]:
    """Fetch 7 days of candles for more robust testing."""
    gsec_map = {"FIVE_MINUTE": 300, "FIFTEEN_MINUTE": 900}
    gsec = gsec_map.get(granularity, 300)
    max_per_req = 300
    end = int(time.time())
    start = end - (7 * 24 * 3600)
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
    client = CoinbaseAdvancedClient()

    print("Fetching 7-day CHECK-USD candles...")
    candles = fetch_candles_7d(client, "CHECK-USD", "FIVE_MINUTE")
    print(f"  Fetched {len(candles)} candles ({len(candles)/288:.1f} days)")

    if len(candles) < 100:
        print("  Not enough candles")
        return

    # Parameter grid — focused but comprehensive
    rsi_periods = [5, 7, 10, 14]
    oversold_levels = [20, 25, 30, 35, 40]
    overbought_levels = [60, 65, 70, 75, 80]
    profit_targets = [0.008, 0.01, 0.012, 0.015, 0.02, 0.025, 0.03]
    stop_losses = [0.002, 0.003, 0.004, 0.005]
    deploy_pcts = [0.5, 0.7, 0.9, 1.0]
    volume_filters = [0.0, 1.0, 1.5, 2.0]
    max_hold_bars = [12, 24, 48, 72, 96]

    total_configs = len(rsi_periods) * len(oversold_levels) * len(overbought_levels) * len(profit_targets) * len(stop_losses) * len(deploy_pcts) * len(volume_filters) * len(max_hold_bars)
    print(f"\nTesting {total_configs:,} configurations...")

    results = []
    count = 0
    start_time = time.time()

    for rsi_p in rsi_periods:
        for os_level in oversold_levels:
            for ob_level in overbought_levels:
                for pt in profit_targets:
                    for sl in stop_losses:
                        for dep in deploy_pcts:
                            for vf in volume_filters:
                                for mhb in max_hold_bars:
                                    count += 1
                                    result = run_single_config(
                                        candles,
                                        rsi_period=rsi_p,
                                        oversold=float(os_level),
                                        overbought=float(ob_level),
                                        profit_target_pct=pt,
                                        stop_loss_pct=sl,
                                        deploy_pct=dep,
                                        volume_filter_mult=vf,
                                        max_hold_bars=mhb,
                                        maker_fee_bps=5.0,
                                        starting_cash=48.0,
                                    )
                                    result["config"] = f"rsi{rsi_p}_os{os_level}_ob{ob_level}_pt{pt*100:.1f}pct_sl{sl*100:.1f}pct_dep{dep*100:.0f}pct_vf{vf}x_mhb{mhb}"
                                    results.append(result)

                                    if count % 50000 == 0:
                                        elapsed = time.time() - start_time
                                        rate = count / elapsed
                                        print(f"  Progress: {count:,}/{total_configs:,} ({count/total_configs*100:.1f}%) — {rate:.0f} configs/sec")

    # Sort by Sharpe-like metric: avg_net * win_rate * sqrt(trades)
    for r in results:
        if r.get("trades", 0) >= 3:
            r["quality_score"] = r["avg_net_per_trade"] * r["win_rate"] * (r["trades"] ** 0.5)
        else:
            r["quality_score"] = -999

    results.sort(key=lambda x: x.get("quality_score", -999), reverse=True)

    elapsed = time.time() - start_time
    print(f"\nCompleted {count:,} configs in {elapsed:.0f}s ({count/elapsed:.0f}/sec)")

    # Top 30 results
    print(f"\n{'='*140}")
    print(f"{'Rank':>4} {'Config':<65} {'Trades':>6} {'Win%':>6} {'Net $':>8} {'Avg/Tr':>8} {'PF':>5} {'DD%':>6} {'Hold':>5}")
    print(f"{'='*140}")
    for i, r in enumerate(results[:30]):
        if r.get("trades", 0) < 3:
            continue
        print(f"{i+1:>4} {r['config']:<65} {r['trades']:>6} {r['win_rate']:>5.1%} ${r['realized_net']:>6.2f} ${r['avg_net_per_trade']:>6.4f} {r.get('profit_factor', 0):>4.1f} {r['max_drawdown_pct']:>5.1f}% {r['median_hold_bars']:>4}b")

    # How many configs are net positive with >= 10 trades?
    positive_10 = [r for r in results if r.get("realized_net", 0) > 0 and r.get("trades", 0) >= 10]
    print(f"\nConfigs with net positive AND >= 10 trades: {len(positive_10)}")

    # Parameter importance analysis
    print(f"\n=== PARAMETER IMPORTANCE (avg realized_net by parameter value) ===")

    # RSI period
    by_rsi = {}
    for r in results:
        if r.get("trades", 0) >= 3:
            p = r["config"].split("_")[0]  # rsiN
            if p not in by_rsi:
                by_rsi[p] = []
            by_rsi[p].append(r["realized_net"])
    print("RSI Period:")
    for k, v in sorted(by_rsi.items(), key=lambda x: sum(x[1])/len(x[1]), reverse=True)[:5]:
        print(f"  {k:15s}: avg ${sum(v)/len(v):+.4f} ({len(v)} configs)")

    # Oversold
    by_os = {}
    for r in results:
        if r.get("trades", 0) >= 3:
            p = r["config"].split("_")[1]
            if p not in by_os:
                by_os[p] = []
            by_os[p].append(r["realized_net"])
    print("Oversold:")
    for k, v in sorted(by_os.items(), key=lambda x: sum(x[1])/len(x[1]), reverse=True)[:5]:
        print(f"  {k:15s}: avg ${sum(v)/len(v):+.4f} ({len(v)} configs)")

    # Profit target
    by_pt = {}
    for r in results:
        if r.get("trades", 0) >= 3:
            p = r["config"].split("_")[3]
            if p not in by_pt:
                by_pt[p] = []
            by_pt[p].append(r["realized_net"])
    print("Profit Target:")
    for k, v in sorted(by_pt.items(), key=lambda x: sum(x[1])/len(x[1]), reverse=True)[:5]:
        print(f"  {k:15s}: avg ${sum(v)/len(v):+.4f} ({len(v)} configs)")

    # Deploy %
    by_dep = {}
    for r in results:
        if r.get("trades", 0) >= 3:
            p = r["config"].split("_")[5]
            if p not in by_dep:
                by_dep[p] = []
            by_dep[p].append(r["realized_net"])
    print("Deploy %:")
    for k, v in sorted(by_dep.items(), key=lambda x: sum(x[1])/len(x[1]), reverse=True)[:5]:
        print(f"  {k:15s}: avg ${sum(v)/len(v):+.4f} ({len(v)} configs)")

    # Write full report
    out = Path(REPORT_PATH)
    out.parent.mkdir(parents=True, exist_ok=True)
    report = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "total_configs": count,
        "elapsed_seconds": round(elapsed, 1),
        "top_30": results[:30],
        "positive_10plus_count": len(positive_10),
    }
    out.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(f"\nFull report: {out}")


if __name__ == "__main__":
    main()
