#!/usr/bin/env python3
"""
V6.1 Regime-Gated Backtest — Historical simulation of the live V6.1 runner.

Runs the EXACT same logic as live_rave_anchor_v61_regime_gated.py on
historical data to project what V6.1 would have achieved over 30d.

This answers: Does regime-gating actually improve the long-run outcome?

Usage:
    python scripts/backtest_v61_regime_gated.py --coin RAVE-USD --window 30d
    python scripts/backtest_v61_regime_gated.py --coin RAVE-USD --window 7d
"""
from __future__ import annotations

import argparse
import json
import math
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from candle_cache_service import load_candles

ROOT = Path(__file__).resolve().parent.parent
REPORT_DIR = ROOT / "reports"

# ── Regime Detection (same as V6.1 inline) ─────────────────────────────

def _compute_atr_pct(highs, lows, closes, period=14):
    if len(closes) < period + 1:
        return 0.0
    trs = []
    for i in range(1, len(closes)):
        tr = max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]), abs(lows[i] - closes[i - 1]))
        trs.append(tr)
    return (sum(trs[-period:]) / period) / max(0.001, closes[-1]) * 100 if len(trs) >= period else 0


def _pearson(x, y):
    n = min(len(x), len(y))
    if n < 5:
        return 0.0
    x = x[-n:]
    y = y[-n:]
    mx = sum(x) / n
    my = sum(y) / n
    num = sum((x[i] - mx) * (y[i] - my) for i in range(n))
    dx = math.sqrt(sum((xi - mx) ** 2 for xi in x))
    dy = math.sqrt(sum((yi - my) ** 2 for yi in y))
    if dx == 0 or dy == 0:
        return 0.0
    return num / (dx * dy)


def _compute_adx(highs, lows, closes, period=14):
    if len(closes) < period + 2:
        return 0.0
    plus_dm = []
    minus_dm = []
    for i in range(1, len(highs)):
        up = highs[i] - highs[i - 1]
        down = lows[i - 1] - lows[i]
        plus_dm.append(up if up > down and up > 0 else 0)
        minus_dm.append(down if down > up and down > 0 else 0)
    if len(plus_dm) < period:
        return 0.0
    avg_plus = sum(plus_dm[-period:]) / period
    avg_minus = sum(minus_dm[-period:]) / period
    tr_sum = avg_plus + avg_minus
    if tr_sum == 0:
        return 0.0
    di_plus = (avg_plus / tr_sum) * 100
    di_minus = (avg_minus / tr_sum) * 100
    return abs(di_plus - di_minus) / max(0.001, di_plus + di_minus) * 100


def classify_regime(candles: list[dict], btc_candles: list[dict]) -> dict:
    if len(candles) < 20:
        return {"score": 50, "regime": "cold", "atr_pct": 0, "btc_corr": 0, "adx": 0, "volume_ratio": 1.0}

    closes = [float(c["close"]) for c in candles]
    highs = [float(c["high"]) for c in candles]
    lows = [float(c["low"]) for c in candles]
    volumes = [float(c.get("volume", 0)) for c in candles]

    atr_pct = _compute_atr_pct(highs, lows, closes, period=14)

    if len(btc_candles) >= len(candles):
        btc_closes = [float(c["close"]) for c in btc_candles[-len(closes):]]
        alt_returns = [(closes[i] - closes[i - 1]) / closes[i - 1] for i in range(1, len(closes))]
        btc_returns = [(btc_closes[i] - btc_closes[i - 1]) / btc_closes[i - 1] for i in range(1, len(btc_closes))]
        btc_corr = abs(_pearson(alt_returns, btc_returns))
    else:
        btc_corr = 0.5

    if len(volumes) >= 10 and volumes[-1] > 0:
        avg_vol = sum(volumes[-10:]) / 10
        volume_ratio = volumes[-1] / max(0.001, avg_vol)
    else:
        volume_ratio = 1.0

    adx = _compute_adx(highs, lows, closes, period=14)

    # Score
    if atr_pct >= 3.0:
        atr_pts = 30
    elif atr_pct >= 2.0:
        atr_pts = 25
    elif atr_pct >= 1.5:
        atr_pts = 20
    elif atr_pct >= 1.0:
        atr_pts = 15
    elif atr_pct >= 0.5:
        atr_pts = 10
    else:
        atr_pts = 0

    if btc_corr < 0.1:
        corr_pts = 30
    elif btc_corr < 0.2:
        corr_pts = 25
    elif btc_corr < 0.3:
        corr_pts = 20
    elif btc_corr < 0.5:
        corr_pts = 10
    else:
        corr_pts = 0

    if volume_ratio >= 2.0:
        vol_pts = 20
    elif volume_ratio >= 1.5:
        vol_pts = 15
    elif volume_ratio >= 1.0:
        vol_pts = 10
    elif volume_ratio >= 0.5:
        vol_pts = 5
    else:
        vol_pts = 0

    if adx < 15:
        adx_pts = 20
    elif adx < 25:
        adx_pts = 15
    elif adx < 35:
        adx_pts = 10
    elif adx < 50:
        adx_pts = 5
    else:
        adx_pts = 0

    score = atr_pts + corr_pts + vol_pts + adx_pts
    regime = "hot" if score >= 70 else ("cold" if score >= 40 else "choppy")

    return {"score": score, "regime": regime, "atr_pct": round(atr_pct, 2),
            "btc_corr": round(btc_corr, 3), "adx": round(adx, 1),
            "volume_ratio": round(volume_ratio, 2)}


def compute_rsi(closes, period=3):
    if len(closes) < period + 1:
        return 50.0
    deltas = [closes[i] - closes[i - 1] for i in range(1, len(closes))]
    gains = [d if d > 0 else 0 for d in deltas[-period:]]
    losses = [-d if d < 0 else 0 for d in deltas[-period:]]
    avg_gain = sum(gains) / period
    avg_loss = sum(losses) / period
    if avg_loss > 0:
        return 100 - 100 / (1 + avg_gain / avg_loss)
    return 100.0


def backtest_v61(rave_candles, btc_candles, starting_cash=48.0,
                  rsi_period=3, os_thresh=30, tp_pct=0.25, max_hold=48,
                  fee_rate=0.0025, regime_window=24):
    """
    Backtest V6.1 regime-gated strategy.

    Returns dict with results.
    """
    if len(rave_candles) < regime_window + 10:
        return {"error": "not enough candles"}

    # Align BTC candles
    btc_lookup = {int(c["time"]): c for c in btc_candles} if btc_candles else {}

    # Compute regime for each candle using rolling window
    regimes = []
    for i in range(len(rave_candles)):
        if i < regime_window:
            window = rave_candles[:i + 1]
        else:
            window = rave_candles[i - regime_window + 1:i + 1]

        window_btc = []
        for wc in window:
            ts = int(wc.get("time") or wc.get("start") or 0)
            if ts in btc_lookup:
                window_btc.append(btc_lookup[ts])
        if not window_btc:
            window_btc = btc_candles[-len(window):] if btc_candles and len(btc_candles) >= len(window) else btc_candles

        regimes.append(classify_regime(window, window_btc))

    # Backtest
    cash = starting_cash
    position = None
    realized_net = 0.0
    closes = 0
    wins = 0
    losses = 0
    total_volume = 0.0
    total_fees = 0.0
    peak_equity = starting_cash
    max_dd = 0.0

    # Telemetry
    signals_by_regime = {"hot": 0, "cold": 0, "choppy": 0}
    entries_by_regime = {"hot": 0, "cold": 0, "choppy": 0}
    skips_choppy = 0
    trades_by_regime = {"hot": [], "cold": [], "choppy": []}
    regime_time_pct = {"hot": 0, "cold": 0, "choppy": 0}

    # RSI computation
    all_closes = [float(c["close"]) for c in rave_candles]
    all_highs = [float(c["high"]) for c in rave_candles]
    all_lows = [float(c["low"]) for c in rave_candles]

    for i in range(max(regime_window + 10, 50), len(rave_candles) - 1):
        regime = regimes[i]["regime"]
        regime_score = regimes[i]["score"]
        regime_time_pct[regime] += 1

        cl = all_closes[i]
        h = all_highs[i]
        l = all_lows[i]

        # Exit logic
        if position:
            position["hold"] += 1
            exit_price = None
            exit_reason = None

            if h >= position["target"]:
                exit_price = position["target"]
                exit_reason = "tp"
            elif position["hold"] >= max_hold:
                exit_price = cl
                exit_reason = "timeout"

            if exit_price is not None:
                units = position["quote"] / position["entry"]
                gross = units * exit_price
                exit_fee = gross * fee_rate
                net = gross - exit_fee - position["entry_cost"]
                cash += gross - exit_fee
                realized_net += net
                closes += 1
                total_volume += position["quote"] + gross
                total_fees += position["entry_fee"] + exit_fee

                win = net > 0
                if win:
                    wins += 1
                else:
                    losses += 1

                entry_regime = position["regime_at_entry"]
                trades_by_regime[entry_regime].append({"net": net, "win": win, "reason": exit_reason})

                equity = cash
                peak_equity = max(peak_equity, equity)
                if peak_equity > 0:
                    dd = (peak_equity - equity) / peak_equity * 100
                    max_dd = max(max_dd, dd)

                position = None

        # Entry logic
        if position is None and cash >= 10.0:
            # Session gate
            ts = int(rave_candles[i].get("time") or rave_candles[i].get("start") or 0)
            hour = datetime.fromtimestamp(ts, tz=timezone.utc).hour
            if hour in [12, 19, 6, 0]:
                continue

            # RSI
            start = max(0, i - rsi_period)
            window_closes = all_closes[start:i + 1]
            rsi_now = compute_rsi(window_closes, period=rsi_period)

            if rsi_now <= os_thresh:
                signals_by_regime[regime] += 1

                # Regime gate
                if regime == "choppy":
                    skips_choppy += 1
                    continue

                # Position sizing
                if regime == "hot":
                    size_pct = 0.95
                else:
                    size_pct = 0.50

                entries_by_regime[regime] += 1
                deploy = cash * size_pct
                entry_fee = deploy * fee_rate
                entry_price = cl  # Use close price (same as live runner)
                units = (deploy - entry_fee) / entry_price
                entry_cost = deploy  # Total cost including fee
                cash -= deploy

                position = {
                    "entry": entry_price,
                    "quote": deploy,
                    "hold": 0,
                    "target": entry_price * (1 + tp_pct),
                    "entry_fee": entry_fee,
                    "entry_cost": entry_cost,
                    "regime_at_entry": regime,
                    "regime_score_at_entry": regime_score,
                }

    # Close remaining position
    if position:
        exit_price = all_closes[-1]
        units = position["quote"] / position["entry"]
        gross = units * exit_price
        exit_fee = gross * fee_rate
        net = gross - exit_fee - position["entry_cost"]
        realized_net += net
        total_fees += position["entry_fee"] + exit_fee
        closes += 1
        if net > 0:
            wins += 1
        else:
            losses += 1
        entry_regime = position["regime_at_entry"]
        trades_by_regime[entry_regime].append({"net": net, "win": net > 0, "reason": "close_remaining"})

    # Compute per-regime stats
    per_regime = {}
    for reg in ["hot", "cold", "choppy"]:
        reg_trades = trades_by_regime[reg]
        if reg_trades:
            reg_wins = sum(1 for t in reg_trades if t["win"])
            reg_net = sum(t["net"] for t in reg_trades)
            per_regime[reg] = {
                "trades": len(reg_trades),
                "wins": reg_wins,
                "wr": round(reg_wins / len(reg_trades) * 100, 1),
                "net": round(reg_net, 2),
                "signals": signals_by_regime[reg],
                "entries": entries_by_regime[reg],
                "time_pct": round(regime_time_pct[reg] / max(1, len(rave_candles)) * 100, 1),
            }

    total_trades = closes
    wr = round(wins / max(1, total_trades) * 100, 1)
    net = round(realized_net, 2)
    return_pct = round(net / starting_cash * 100, 1)

    bars = len(rave_candles)
    days = bars / 288  # 5-min candles
    monthly_proj = round(net / max(0.001, days) * 30, 2)

    return {
        "net": net,
        "return_pct": return_pct,
        "trades": total_trades,
        "wins": wins,
        "losses": losses,
        "win_rate": wr,
        "total_volume": round(total_volume, 2),
        "total_fees": round(total_fees, 2),
        "max_drawdown": round(max_dd, 1),
        "monthly_projection": monthly_proj,
        "skips_choppy": skips_choppy,
        "signals_by_regime": signals_by_regime,
        "entries_by_regime": entries_by_regime,
        "regime_time_pct": {k: round(v / max(1, len(rave_candles)) * 100, 1) for k, v in regime_time_pct.items()},
        "per_regime": per_regime,
        "window": f"{bars} candles ({days:.0f} days)",
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--coin", default="RAVE-USD")
    parser.add_argument("--window", default="30d")
    args = parser.parse_args()

    days = int(args.window.replace("d", ""))
    coin = args.coin

    print(f"V6.1 Regime-Gated Backtest — {coin} {days}d")
    print(f"{'=' * 60}")

    # Load data
    rave_candles = load_candles(coin, "FIVE_MINUTE", days, max_age_minutes=days * 24 * 60)
    btc_candles = load_candles("BTC-USD", "FIVE_MINUTE", days, max_age_minutes=days * 24 * 60)

    if not rave_candles:
        print(f"ERROR: No {coin} data for {days}d")
        return 1

    print(f"Loaded {len(rave_candles)} {coin} candles, {len(btc_candles) if btc_candles else 0} BTC candles")

    # Run V6.1 backtest
    result = backtest_v61(rave_candles, btc_candles)

    if "error" in result:
        print(f"ERROR: {result['error']}")
        return 1

    # Print results
    print(f"\n{'=' * 60}")
    print(f"  V6.1 REGIME-GATED RESULTS")
    print(f"{'=' * 60}")
    print(f"  Net: ${result['net']:+.2f} ({result['return_pct']:+.1f}%)")
    print(f"  Trades: {result['trades']} ({result['win_rate']}% WR)")
    print(f"  Max DD: {result['max_drawdown']}%")
    print(f"  Monthly proj: ${result['monthly_projection']:+.2f}")
    print(f"  Choppy skips: {result['skips_choppy']}")
    print(f"  Signals by regime: {result['signals_by_regime']}")
    print(f"  Entries by regime: {result['entries_by_regime']}")
    print(f"  Regime time%: {result['regime_time_pct']}")

    print(f"\n{'=' * 60}")
    print(f"  PER-REGIME BREAKDOWN")
    print(f"{'=' * 60}")
    for reg in ["hot", "cold", "choppy"]:
        if reg in result["per_regime"]:
            r = result["per_regime"][reg]
            print(f"  {reg.upper():>8}: {r['trades']:>3}t  {r['wr']:>5.1f}%WR  ${r['net']:+8.2f}  "
                  f"signals={r['signals']}  entries={r['entries']}  time={r['time_pct']}%")

    # Compare with V6 (no regime gate) — run simplified
    print(f"\n{'=' * 60}")
    print(f"  V6 vs V6.1 COMPARISON")
    print(f"{'=' * 60}")

    # Simple V6 backtest (no regime gate, full size)
    v6_result = backtest_v6_no_regime(rave_candles, btc_candles)
    if "error" not in v6_result:
        print(f"  V6 (no gate): ${v6_result['net']:+.2f}  {v6_result['trades']}t  {v6_result['win_rate']}%WR  "
              f"DD={v6_result['max_drawdown']}%  monthly=${v6_result['monthly_projection']:+.2f}")
        print(f"  V6.1 (gated): ${result['net']:+.2f}  {result['trades']}t  {result['win_rate']}%WR  "
              f"DD={result['max_drawdown']}%  monthly=${result['monthly_projection']:+.2f}")
        delta_net = result['net'] - v6_result['net']
        delta_dd = result['max_drawdown'] - v6_result['max_drawdown']
        print(f"  Delta:      ${delta_net:+.2f}  {result['trades'] - v6_result['trades']:+d}t  "
              f"DD={delta_dd:+.1f}%")

    # Save
    output_path = REPORT_DIR / f"v61_regime_gated_{coin}_{args.window}.json"
    with open(output_path, "w") as f:
        json.dump(result, f, indent=2, default=str)
    print(f"\n  Saved: {output_path}")

    return 0


def backtest_v6_no_regime(rave_candles, btc_candles, starting_cash=48.0):
    """Backtest V6 (no regime gate, full size always) for comparison."""
    if len(rave_candles) < 60:
        return {"error": "not enough candles"}

    all_closes = [float(c["close"]) for c in rave_candles]
    all_highs = [float(c["high"]) for c in rave_candles]
    all_lows = [float(c["low"]) for c in rave_candles]
    fee_rate = 0.0025

    cash = starting_cash
    position = None
    realized_net = 0.0
    closes = 0
    wins = 0
    losses = 0
    max_dd = 0.0
    peak_equity = starting_cash

    for i in range(50, len(rave_candles) - 1):
        cl = all_closes[i]
        h = all_highs[i]

        # Exit
        if position:
            position["hold"] += 1
            if h >= position["target"] or position["hold"] >= 48:
                exit_p = position["target"] if h >= position["target"] else cl
                units = position["quote"] / position["entry"]
                gross = units * exit_p
                exit_fee = gross * fee_rate
                net = gross - exit_fee - position["entry_cost"]
                cash += gross - exit_fee
                realized_net += net
                closes += 1
                if net > 0:
                    wins += 1
                else:
                    losses += 1
                equity = cash
                peak_equity = max(peak_equity, equity)
                if peak_equity > 0:
                    max_dd = max(max_dd, (peak_equity - equity) / peak_equity * 100)
                position = None

        # Entry (no regime gate, full size, session gate only)
        if position is None and cash >= 10.0:
            ts = int(rave_candles[i].get("time") or rave_candles[i].get("start") or 0)
            hour = datetime.fromtimestamp(ts, tz=timezone.utc).hour
            if hour in [12, 19, 6, 0]:
                continue

            start = max(0, i - 3)
            rsi_now = compute_rsi(all_closes[start:i + 1], period=3)
            if rsi_now <= 30:
                deploy = cash * 0.95  # Full size
                entry_fee = deploy * fee_rate
                units = (deploy - entry_fee) / cl
                cash -= deploy
                position = {
                    "entry": cl, "quote": deploy, "hold": 0,
                    "target": cl * 1.25, "entry_fee": entry_fee,
                    "entry_cost": deploy,
                }

    if position:
        exit_p = all_closes[-1]
        units = position["quote"] / position["entry"]
        gross = units * exit_p
        exit_fee = gross * fee_rate
        realized_net += gross - exit_fee - position["entry_cost"]
        closes += 1

    bars = len(rave_candles)
    days = bars / 288
    monthly = realized_net / max(0.001, days) * 30

    return {
        "net": round(realized_net, 2),
        "return_pct": round(realized_net / starting_cash * 100, 1),
        "trades": closes,
        "wins": wins,
        "losses": losses,
        "win_rate": round(wins / max(1, closes) * 100, 1),
        "max_drawdown": round(max_dd, 1),
        "monthly_projection": round(monthly, 2),
    }


if __name__ == "__main__":
    raise SystemExit(main())
