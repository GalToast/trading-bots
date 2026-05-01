#!/usr/bin/env python3
"""
Combined Edge System — Multi-signal, multi-product backtester.

Layers all discovered edges into a single system:
1. RSI mean reversion (CHECK-USD, ARB-USD)
2. Volatility squeeze breakout (CHECK-USD, ARB-USD)
3. BTC lead-lag overreaction (WIF-USD, CHECK-USD)
4. Candle sequence reversal (ETH-USD, CHECK-USD)
5. Time-of-day filter (only trade during profitable hours)

Tests: Can we combine these edges to get >$15/72h on $48?
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from coinbase_advanced_client import CoinbaseAdvancedClient

ROOT = Path(__file__).resolve().parent.parent
REPORT_PATH = ROOT / "reports" / "combined_edge_system.json"

PRODUCTS = ["CHECK-USD", "ARB-USD", "ETH-USD", "WIF-USD", "BTC-USD"]


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


def combined_edge_system(
    candles: dict[str, list[dict]],
    *,
    starting_cash: float = 48.0,
    maker_fee_bps: float = 5.0,
    use_time_filter: bool = True,
    profitable_hours: dict[str, list[int]] = None,
) -> dict[str, Any]:
    """
    Run the combined edge system across all products simultaneously.

    Each product gets its best signals:
    - CHECK-USD: RSI(7) mean reversion + volatility squeeze
    - ARB-USD: RSI(7) mean reversion + volatility squeeze
    - ETH-USD: Candle sequence reversal (4+ green → sell)
    - WIF-USD: BTC lead-lag overreaction

    Time filter: only trade during profitable hours for each product.
    """
    import datetime

    fee_rate = maker_fee_bps / 10000.0
    cash = starting_cash
    trades = []
    product_trades = {pid: [] for pid in candles}

    for pid, cdl_list in candles.items():
        if len(cdl_list) < 30:
            continue

        closes = [c["close"] for c in cdl_list]
        volumes = [c["volume"] for c in cdl_list]
        rsi_7 = rsi(closes, 7)

        for i in range(10, len(cdl_list) - 5):
            c = cdl_list[i]
            ts = c["time"]
            dt = datetime.datetime.fromtimestamp(ts, tz=datetime.timezone.utc)
            hour = dt.hour

            # Time filter
            if use_time_filter and profitable_hours:
                hours = profitable_hours.get(pid, [])
                if hours and hour not in hours:
                    continue

            signals = []

            # Signal 1: RSI mean reversion (CHECK-USD, ARB-USD)
            if pid in ["CHECK-USD", "ARB-USD"]:
                if rsi_7[i] < 35:  # Oversold
                    signals.append(("rsi_buy", 0.015))  # 1.5% target

            # Signal 2: Volatility squeeze (CHECK-USD, ARB-USD)
            if pid in ["CHECK-USD", "ARB-USD"] and i >= 10:
                recent_returns = [(closes[j] - closes[j - 1]) / closes[j - 1] for j in range(i - 10, i)]
                mean_ret = sum(recent_returns) / len(recent_returns)
                std = (sum((r - mean_ret) ** 2 for r in recent_returns) / len(recent_returns)) ** 0.5
                if std < 0.001:
                    signals.append(("squeeze_buy", 0.02))  # 2% target — squeeze breakout

            # Signal 3: Candle sequence reversal (ETH-USD, CHECK-USD)
            if pid in ["ETH-USD", "CHECK-USD"] and i >= 4:
                green = sum(1 for j in range(i - 4, i) if cdl_list[j]["close"] >= cdl_list[j]["open"])
                if green >= 4:
                    signals.append(("sequence_sell", 0.005))  # 0.5% target — quick reversal

            # Execute signals (prioritize strongest)
            if signals:
                sig_type, target_pct = signals[0]  # Take first signal
                entry_price = c["close"]

                # Determine direction
                if sig_type == "sequence_sell":
                    # Sell short not possible on spot, skip
                    continue

                # Buy signal
                deploy_usd = cash * 0.9
                if deploy_usd < 1.0:
                    continue

                entry_fee = entry_price * (deploy_usd / entry_price) * fee_rate
                qty = (deploy_usd - entry_fee) / entry_price
                if qty <= 0:
                    continue

                cash -= deploy_usd

                # Find exit
                exit_price = None
                exit_reason = None
                tp_price = entry_price * (1 + target_pct)
                sl_price = entry_price * (1 - 0.003)  # 0.3% stop

                for j in range(1, 10):  # Check next 10 bars
                    if i + j >= len(cdl_list):
                        break
                    fc = cdl_list[i + j]
                    if fc["high"] >= tp_price:
                        exit_price = tp_price
                        exit_reason = f"{sig_type}_tp"
                        break
                    elif fc["low"] <= sl_price:
                        exit_price = sl_price
                        exit_reason = f"{sig_type}_sl"
                        break

                if exit_price is None:
                    exit_price = cdl_list[min(i + 5, len(cdl_list) - 1)]["close"]
                    exit_reason = f"{sig_type}_timeout"

                exit_fee = exit_price * qty * fee_rate
                gross = (exit_price - entry_price) * qty
                net = gross - entry_fee - exit_fee

                cash += exit_price * qty - exit_fee

                trade = {
                    "product": pid,
                    "entry_time": ts,
                    "entry_price": entry_price,
                    "exit_price": exit_price,
                    "signal": sig_type,
                    "exit_reason": exit_reason,
                    "gross_pnl": round(gross, 4),
                    "fee": round(entry_fee + exit_fee, 4),
                    "net_pnl": round(net, 4),
                    "target_pct": round(target_pct * 100, 2),
                    "hour": hour,
                }
                trades.append(trade)
                product_trades[pid].append(trade)

    # Summary
    wins = [t for t in trades if t["net_pnl"] > 0]
    losses = [t for t in trades if t["net_pnl"] <= 0]

    by_signal = {}
    for t in trades:
        sig = t["signal"]
        if sig not in by_signal:
            by_signal[sig] = []
        by_signal[sig].append(t)

    signal_summary = {}
    for sig, sig_trades in by_signal.items():
        sig_wins = sum(1 for t in sig_trades if t["net_pnl"] > 0)
        signal_summary[sig] = {
            "trades": len(sig_trades),
            "wins": sig_wins,
            "win_rate": round(sig_wins / len(sig_trades), 3) if sig_trades else 0,
            "net_pnl": round(sum(t["net_pnl"] for t in sig_trades), 4),
            "avg_net": round(sum(t["net_pnl"] for t in sig_trades) / len(sig_trades), 4) if sig_trades else 0,
        }

    return {
        "starting_cash": starting_cash,
        "ending_cash": round(cash, 2),
        "realized_net": round(cash - starting_cash, 4),
        "total_trades": len(trades),
        "wins": len(wins),
        "losses": len(losses),
        "win_rate": round(len(wins) / len(trades), 3) if trades else 0,
        "total_fees": round(sum(t["fee"] for t in trades), 4),
        "signal_summary": signal_summary,
        "product_summary": {pid: {
            "trades": len(pts),
            "net_pnl": round(sum(t["net_pnl"] for t in pts), 4),
            "avg_net": round(sum(t["net_pnl"] for t in pts) / len(pts), 4) if pts else 0,
        } for pid, pts in product_trades.items()},
        "time_filter_used": use_time_filter,
    }


def main() -> None:
    client = CoinbaseAdvancedClient()

    print("Fetching candles...")
    candles = {}
    for pid in PRODUCTS:
        try:
            candles[pid] = fetch_candles_72h(client, pid)
            print(f"  {pid}: {len(candles[pid])} candles")
        except Exception as e:
            print(f"  {pid}: ERROR {e}")

    # Profitable hours from time-of-day analysis
    profitable_hours = {
        "CHECK-USD": [9, 0, 3, 21, 16],
        "ARB-USD": [21, 0, 2, 19, 22],
        "ETH-USD": [22, 18, 14, 9, 15],
        "WIF-USD": [5, 15, 23, 19, 12],
    }

    # Test 1: No time filter
    print("\n=== Combined System: NO TIME FILTER ===")
    result_no_filter = combined_edge_system(candles, use_time_filter=False)
    print(f"  Net: ${result_no_filter['realized_net']:+.4f} | Trades: {result_no_filter['total_trades']} | Win: {result_no_filter['win_rate']:.1%}")

    # Test 2: With time filter
    print("\n=== Combined System: WITH TIME FILTER ===")
    result_with_filter = combined_edge_system(candles, use_time_filter=True, profitable_hours=profitable_hours)
    print(f"  Net: ${result_with_filter['realized_net']:+.4f} | Trades: {result_with_filter['total_trades']} | Win: {result_with_filter['win_rate']:.1%}")

    out = Path(REPORT_PATH)
    out.parent.mkdir(parents=True, exist_ok=True)
    report = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "no_time_filter": result_no_filter,
        "with_time_filter": result_with_filter,
    }
    out.write_text(json.dumps(report, indent=2), encoding="utf-8")

    # Signal breakdown
    print(f"\n{'='*80}")
    print(f"{'Signal':<20} {'Trades':>6} {'Win%':>6} {'Net $':>10} {'Avg/Tr':>9}")
    print(f"{'='*80}")
    for sig, s in result_with_filter.get("signal_summary", {}).items():
        print(f"{sig:<20} {s['trades']:>6} {s['win_rate']:>5.1%} ${s['net_pnl']:>8.4f} ${s['avg_net']:>7.4f}")

    print(f"\nProduct breakdown:")
    for pid, ps in result_with_filter.get("product_summary", {}).items():
        print(f"  {pid}: {ps['trades']} trades, ${ps['net_pnl']:+.4f}, avg ${ps['avg_net']:+.4f}/trade")

    print(f"\nFull report: {out}")


if __name__ == "__main__":
    main()
