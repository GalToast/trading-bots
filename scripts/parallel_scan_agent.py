#!/usr/bin/env python3
"""
Parallel RSI Mean-Reversion Scan Agent
=======================================
Scans assigned coin range for profitable RSI MR strategies.
Grid search: RSI periods x TP levels x Max hold bars = 500 combos/coin.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

# CRITICAL: add scripts/ to path so we can import candle_cache_service
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
from candle_cache_service import load_candles

# ── Configuration ──────────────────────────────────────────────────────────
START_IDX = 0
END_IDX = 49

RSI_PERIODS = [2, 3, 4, 5, 6, 7, 8, 10, 12, 14]
TP_LEVELS = [0.05, 0.08, 0.10, 0.15, 0.20, 0.25, 0.30, 0.35, 0.40, 0.50]
MAX_HOLD_BARS = [12, 24, 36, 48, 72]

FEE_PER_SIDE = 0.0040          # 40 bps
STARTING_CASH = 48.0
POSITION_PCT = 0.95            # 95% of cash per trade
RSI_ENTRY_THRESH = 30           # enter when RSI < 30

REPORTS_DIR = Path(__file__).resolve().parent.parent / "reports"
REPORTS_DIR.mkdir(parents=True, exist_ok=True)


# ── Helpers ────────────────────────────────────────────────────────────────
def _ema(values: list[float], period: int) -> list[float]:
    """Exponential moving average."""
    if not values:
        return []
    result = [values[0]]
    multiplier = 2.0 / (period + 1)
    for v in values[1:]:
        result.append((v - result[-1]) * multiplier + result[-1])
    return result


def _rsi(closes: list[float], period: int) -> list[float | None]:
    """Wilder RSI. Returns list aligned with closes; None where not enough data."""
    n = len(closes)
    rsi = [None] * n
    if n < period + 1:
        return rsi

    # First avg gain/loss (SMA over first `period` changes)
    gains = 0.0
    losses = 0.0
    for i in range(1, period + 1):
        delta = closes[i] - closes[i - 1]
        if delta > 0:
            gains += delta
        else:
            losses -= delta
    avg_gain = gains / period
    avg_loss = losses / period

    if avg_loss == 0:
        rsi[period] = 100.0
    else:
        rs = avg_gain / avg_loss
        rsi[period] = 100.0 - 100.0 / (1.0 + rs)

    for i in range(period + 1, n):
        delta = closes[i] - closes[i - 1]
        gain = delta if delta > 0 else 0.0
        loss = -delta if delta < 0 else 0.0
        avg_gain = (avg_gain * (period - 1) + gain) / period
        avg_loss = (avg_loss * (period - 1) + loss) / period
        if avg_loss == 0:
            rsi[i] = 100.0
        else:
            rs = avg_gain / avg_loss
            rsi[i] = 100.0 - 100.0 / (1.0 + rs)

    return rsi


def backtest(candles: list[dict], rsi_period: int, tp: float, max_hold: int) -> dict:
    """
    Fast backtest:
      - Entry: RSI < 30
      - Exit: price >= entry * (1+tp)  OR  hold reaches max_hold bars
      - Position: 95% cash
      - Fee: 0.40% per side
    """
    closes = [c["close"] for c in candles]
    rsi_vals = _rsi(closes, rsi_period)

    cash = STARTING_CASH
    position = 0.0          # units of coin held
    entry_price = 0.0
    bars_held = 0
    in_position = False

    trades = 0
    wins = 0
    total_pnl = 0.0
    peak_equity = cash
    max_dd = 0.0

    for i in range(len(candles)):
        price = closes[i]
        equity = cash + position * price
        if equity > peak_equity:
            peak_equity = equity
        dd = (peak_equity - equity) / peak_equity if peak_equity > 0 else 0
        if dd > max_dd:
            max_dd = dd

        if in_position:
            bars_held += 1
            # Check exit: TP hit or timeout
            tp_price = entry_price * (1.0 + tp)
            if price >= tp_price or bars_held >= max_hold:
                # Sell
                proceeds = position * price * (1.0 - FEE_PER_SIDE)
                pnl = proceeds - entry_price * position * (1.0 + FEE_PER_SIDE)
                total_pnl += pnl
                cash += proceeds
                if pnl > 0:
                    wins += 1
                trades += 1
                position = 0.0
                in_position = False
                bars_held = 0
        else:
            # Check entry: RSI < 30
            if rsi_vals[i] is not None and rsi_vals[i] < RSI_ENTRY_THRESH:
                # Buy with 95% of cash
                buy_amount = cash * POSITION_PCT
                units = buy_amount / (price * (1.0 + FEE_PER_SIDE))
                if units > 0:
                    cash -= buy_amount
                    position = units
                    entry_price = price
                    in_position = True
                    bars_held = 0

    # If still in position at end, close at last price
    if in_position and position > 0:
        price = closes[-1]
        proceeds = position * price * (1.0 - FEE_PER_SIDE)
        pnl = proceeds - entry_price * position * (1.0 + FEE_PER_SIDE)
        total_pnl += pnl
        cash += proceeds
        if pnl > 0:
            wins += 1
        trades += 1

    final_equity = cash
    net_pnl = final_equity - STARTING_CASH
    wr = wins / trades if trades > 0 else 0.0

    return {
        "net_pnl": round(net_pnl, 4),
        "final_equity": round(final_equity, 4),
        "trades": trades,
        "wins": wins,
        "wr_pct": round(wr * 100, 2),
        "max_dd_pct": round(max_dd * 100, 2),
        "total_pnl": round(total_pnl, 4),
    }


# ── Main ───────────────────────────────────────────────────────────────────
def main():
    # Read coin list
    coin_file = Path(__file__).resolve().parent.parent / "coinbase_usd_pairs.txt"
    lines = [l.strip() for l in coin_file.read_text(encoding="utf-8").splitlines() if l.strip() and not l.strip().startswith("Total")]

    coins = lines[START_IDX:END_IDX + 1]
    print(f"Scanning coins {START_IDX}–{END_IDX}: {len(coins)} coins")
    print(f"Grid: {len(RSI_PERIODS)} rsi × {len(TP_LEVELS)} tp × {len(MAX_HOLD_BARS)} hold = {len(RSI_PERIODS)*len(TP_LEVELS)*len(MAX_HOLD_BARS)} combos/coin")
    print()

    all_results = []

    for idx, coin in enumerate(coins, start=START_IDX):
        print(f"  [{idx+1 - START_IDX}/{len(coins)}] {coin} ...", end="", flush=True)
        try:
            candles = load_candles(coin, "FIVE_MINUTE", 7)
        except Exception as e:
            print(f" ERROR loading candles: {e}")
            continue

        if len(candles) < 20:
            print(f" SKIP (only {len(candles)} candles)")
            continue

        coin_results = []
        for rsi_p in RSI_PERIODS:
            for tp in TP_LEVELS:
                for mh in MAX_HOLD_BARS:
                    res = backtest(candles, rsi_p, tp, mh)
                    if res["net_pnl"] > 0 and res["trades"] >= 5:
                        coin_results.append({
                            "coin": coin,
                            "rsi_period": rsi_p,
                            "tp": tp,
                            "max_hold": mh,
                            **res,
                        })
        print(f" {len(coin_results)} profitable combos")
        all_results.extend(coin_results)

    # Sort by net PnL descending
    all_results.sort(key=lambda x: x["net_pnl"], reverse=True)

    # Write report
    report_name = f"parallel_scan_chunk_{START_IDX}_{END_IDX}.json"
    report_path = REPORTS_DIR / report_name
    report_data = {
        "start_idx": START_IDX,
        "end_idx": END_IDX,
        "coins_scanned": len(coins),
        "total_combos_tested": len(coins) * len(RSI_PERIODS) * len(TP_LEVELS) * len(MAX_HOLD_BARS),
        "profitable_combos": len(all_results),
        "top_results": all_results[:100],   # keep top 100
    }
    report_path.write_text(json.dumps(report_data, indent=2), encoding="utf-8")
    print(f"\nReport written to: {report_path}")
    print(f"Total profitable combos (net>0, trades>=5): {len(all_results)}")

    # Print TOP 10
    print(f"\n{'='*90}")
    print(f"  TOP 10 MOST PROFITABLE COMBOS")
    print(f"{'='*90}")
    for i, r in enumerate(all_results[:10], 1):
        print(f"  #{i:2d}  {r['coin']:16s}  RSI={r['rsi_period']:2d}  TP={r['tp']:.2f}  "
              f"Hold={r['max_hold']:3d}  Net=${r['net_pnl']:+.2f}  "
              f"Trades={r['trades']:3d}  WR={r['wr_pct']:.1f}%  DD={r['max_dd_pct']:.1f}%")
    print(f"{'='*90}")


if __name__ == "__main__":
    main()
