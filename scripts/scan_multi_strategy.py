#!/usr/bin/env python3
"""
Multi-Strategy Edge Scanner for Coinbase Spot.

Tests 6 fundamentally different strategy families across 12+ coins
to find edges that work broadly, not just on RAVE.

Strategy families:
1. Bollinger Band Mean Reversion (BB squeeze → buy at lower band)
2. Momentum Breakout (buy breakouts of N-bar highs)
3. Volume Spike Fade (fade abnormal volume + large moves)
4. Candle Pattern Reversal (N consecutive reds → buy reversal)
5. VWAP Reversion (buy below rolling VWAP, sell above)
6. Dip-and-Rip (buy >2% intrabar drop that recovers >50%)
"""
import json, os, sys, time, math, statistics
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from coinbase_advanced_client import CoinbaseAdvancedClient

ROOT = Path(__file__).resolve().parent.parent
RESULTS_PATH = ROOT / "reports" / "_multi_strategy_scan_results.json"

# Broad coin universe — major, mid, micro
COINS = [
    "RAVE-USD", "MOG-USD", "FARTCOIN-USD", "PEPE-USD",   # microcap memes
    "SOL-USD", "DOGE-USD", "SUI-USD", "AVAX-USD",        # mid alts
    "ETH-USD", "LINK-USD", "ARB-USD", "COMP-USD",        # established
    "BTC-USD",                                             # benchmark
]

FEE_RATE = 0.004  # 40 bps per side (Coinbase spot taker)
STARTING_CASH = 48.0


def fetch_candles(client, product_id, hours=72, granularity="FIVE_MINUTE"):
    gsec = {"FIVE_MINUTE": 300, "ONE_MINUTE": 60, "FIFTEEN_MINUTE": 900}.get(granularity, 300)
    end = int(time.time())
    start = end - hours * 3600
    all_c, seen = [], set()
    chunk_end = end
    while chunk_end > start:
        chunk_start = max(start, chunk_end - 300 * gsec)
        try:
            resp = client.market_candles(product_id, start=chunk_start, end=chunk_end, granularity=granularity)
        except Exception:
            break
        raw = resp.get("candles") or []
        if not raw:
            break
        for c in raw:
            t = int(c["start"])
            if t not in seen:
                seen.add(t)
                all_c.append({"time": t, "open": float(c["open"]), "high": float(c["high"]),
                              "low": float(c["low"]), "close": float(c["close"]),
                              "volume": float(c.get("volume", 0))})
        chunk_end = chunk_start - 1
        time.sleep(0.12)
    return sorted(all_c, key=lambda x: x["time"])


# ──────────── STRATEGY 1: Bollinger Band Mean Reversion ────────────
def strat_bb_reversion(candles, bb_period=20, bb_std=2.0, tp_pct=0.02, max_hold=24):
    """Buy when close < lower BB, sell at middle band or TP or timeout."""
    closes = [c["close"] for c in candles]
    cash, pos, wins, losses, realized = STARTING_CASH, None, 0, 0, 0.0

    for i in range(bb_period, len(candles)):
        c = candles[i]
        window = closes[i - bb_period:i]
        mean = sum(window) / bb_period
        std = (sum((x - mean) ** 2 for x in window) / bb_period) ** 0.5
        lower = mean - bb_std * std
        upper = mean + bb_std * std

        if pos:
            pos["hold"] += 1
            hit_mid = c["high"] >= mean
            hit_tp = c["high"] >= pos["tp"]
            timeout = pos["hold"] >= max_hold

            if hit_tp or hit_mid or timeout:
                exit_p = min(pos["tp"], mean) if (hit_tp or hit_mid) else c["close"]
                gross = (exit_p - pos["ep"]) * pos["units"]
                fee = exit_p * pos["units"] * FEE_RATE
                net = gross - pos["efee"] - fee
                cash += pos["q"] + net
                realized += net
                wins += 1 if net > 0 else 0
                losses += 1 if net <= 0 else 0
                pos = None

        if pos is None and cash >= 5.0 and c["close"] < lower and std > 0:
            deploy = cash
            efee = deploy * FEE_RATE
            units = (deploy - efee) / c["close"]
            cash -= deploy
            pos = {"ep": c["close"], "q": deploy, "hold": 0,
                   "tp": c["close"] * (1 + tp_pct), "units": units, "efee": efee}

    total = wins + losses
    return {"strategy": "bb_reversion", "closes": total, "wins": wins, "losses": losses,
            "win_rate": round(wins / max(1, total) * 100, 1), "realized_pnl": round(realized, 4)}


# ──────────── STRATEGY 2: Momentum Breakout ────────────
def strat_momentum_breakout(candles, lookback=20, tp_pct=0.03, sl_pct=0.015, max_hold=30):
    """Buy breakout above N-bar high. Trend-following, not MR."""
    cash, pos, wins, losses, realized = STARTING_CASH, None, 0, 0, 0.0

    for i in range(lookback, len(candles)):
        c = candles[i]
        recent_high = max(candles[j]["high"] for j in range(i - lookback, i))

        if pos:
            pos["hold"] += 1
            hit_tp = c["high"] >= pos["tp"]
            hit_sl = c["low"] <= pos["sl"]
            timeout = pos["hold"] >= max_hold

            if hit_sl or hit_tp or timeout:
                if hit_sl:
                    exit_p = pos["sl"]
                elif hit_tp:
                    exit_p = pos["tp"]
                else:
                    exit_p = c["close"]
                gross = (exit_p - pos["ep"]) * pos["units"]
                fee = exit_p * pos["units"] * FEE_RATE
                net = gross - pos["efee"] - fee
                cash += pos["q"] + net
                realized += net
                wins += 1 if net > 0 else 0
                losses += 1 if net <= 0 else 0
                pos = None

        if pos is None and cash >= 5.0 and c["close"] > recent_high:
            deploy = cash
            efee = deploy * FEE_RATE
            units = (deploy - efee) / c["close"]
            cash -= deploy
            pos = {"ep": c["close"], "q": deploy, "hold": 0,
                   "tp": c["close"] * (1 + tp_pct), "sl": c["close"] * (1 - sl_pct),
                   "units": units, "efee": efee}

    total = wins + losses
    return {"strategy": "momentum_breakout", "closes": total, "wins": wins, "losses": losses,
            "win_rate": round(wins / max(1, total) * 100, 1), "realized_pnl": round(realized, 4)}


# ──────────── STRATEGY 3: Volume Spike Fade ────────────
def strat_volume_spike_fade(candles, vol_lookback=20, vol_mult=2.5, tp_pct=0.015, max_hold=12):
    """Buy when volume > 2.5× average AND price dropped >1%. Fade the panic."""
    cash, pos, wins, losses, realized = STARTING_CASH, None, 0, 0, 0.0

    for i in range(vol_lookback, len(candles)):
        c = candles[i]
        avg_vol = sum(candles[j]["volume"] for j in range(i - vol_lookback, i)) / vol_lookback
        price_change = (c["close"] - c["open"]) / c["open"]

        if pos:
            pos["hold"] += 1
            hit_tp = c["high"] >= pos["tp"]
            timeout = pos["hold"] >= max_hold

            if hit_tp or timeout:
                exit_p = pos["tp"] if hit_tp else c["close"]
                gross = (exit_p - pos["ep"]) * pos["units"]
                fee = exit_p * pos["units"] * FEE_RATE
                net = gross - pos["efee"] - fee
                cash += pos["q"] + net
                realized += net
                wins += 1 if net > 0 else 0
                losses += 1 if net <= 0 else 0
                pos = None

        if pos is None and cash >= 5.0:
            is_spike = avg_vol > 0 and c["volume"] > avg_vol * vol_mult
            is_drop = price_change < -0.01  # dropped >1%
            if is_spike and is_drop:
                deploy = cash
                efee = deploy * FEE_RATE
                units = (deploy - efee) / c["close"]
                cash -= deploy
                pos = {"ep": c["close"], "q": deploy, "hold": 0,
                       "tp": c["close"] * (1 + tp_pct), "units": units, "efee": efee}

    total = wins + losses
    return {"strategy": "volume_spike_fade", "closes": total, "wins": wins, "losses": losses,
            "win_rate": round(wins / max(1, total) * 100, 1), "realized_pnl": round(realized, 4)}


# ──────────── STRATEGY 4: Consecutive Red Reversal ────────────
def strat_red_reversal(candles, min_reds=4, tp_pct=0.02, max_hold=16):
    """Buy after N consecutive red candles. Classic oversold bounce."""
    cash, pos, wins, losses, realized = STARTING_CASH, None, 0, 0, 0.0

    for i in range(min_reds, len(candles)):
        c = candles[i]

        if pos:
            pos["hold"] += 1
            hit_tp = c["high"] >= pos["tp"]
            timeout = pos["hold"] >= max_hold

            if hit_tp or timeout:
                exit_p = pos["tp"] if hit_tp else c["close"]
                gross = (exit_p - pos["ep"]) * pos["units"]
                fee = exit_p * pos["units"] * FEE_RATE
                net = gross - pos["efee"] - fee
                cash += pos["q"] + net
                realized += net
                wins += 1 if net > 0 else 0
                losses += 1 if net <= 0 else 0
                pos = None

        if pos is None and cash >= 5.0:
            all_red = all(candles[i - j]["close"] < candles[i - j]["open"] for j in range(min_reds))
            if all_red:
                deploy = cash
                efee = deploy * FEE_RATE
                units = (deploy - efee) / c["close"]
                cash -= deploy
                pos = {"ep": c["close"], "q": deploy, "hold": 0,
                       "tp": c["close"] * (1 + tp_pct), "units": units, "efee": efee}

    total = wins + losses
    return {"strategy": "red_reversal_4", "closes": total, "wins": wins, "losses": losses,
            "win_rate": round(wins / max(1, total) * 100, 1), "realized_pnl": round(realized, 4)}


# ──────────── STRATEGY 5: VWAP Reversion ────────────
def strat_vwap_reversion(candles, vwap_period=30, entry_pct=-0.005, tp_pct=0.01, max_hold=20):
    """Buy when price < VWAP by entry_pct. Sell at VWAP or TP."""
    cash, pos, wins, losses, realized = STARTING_CASH, None, 0, 0, 0.0

    for i in range(vwap_period, len(candles)):
        c = candles[i]
        # Rolling VWAP: sum(close * volume) / sum(volume)
        window = candles[i - vwap_period:i]
        total_vol = sum(w["volume"] for w in window)
        if total_vol <= 0:
            continue
        vwap = sum(w["close"] * w["volume"] for w in window) / total_vol
        distance = (c["close"] - vwap) / vwap

        if pos:
            pos["hold"] += 1
            hit_vwap = c["high"] >= vwap
            hit_tp = c["high"] >= pos["tp"]
            timeout = pos["hold"] >= max_hold

            if hit_tp or hit_vwap or timeout:
                exit_p = min(pos["tp"], vwap) if (hit_tp or hit_vwap) else c["close"]
                gross = (exit_p - pos["ep"]) * pos["units"]
                fee = exit_p * pos["units"] * FEE_RATE
                net = gross - pos["efee"] - fee
                cash += pos["q"] + net
                realized += net
                wins += 1 if net > 0 else 0
                losses += 1 if net <= 0 else 0
                pos = None

        if pos is None and cash >= 5.0 and distance < entry_pct:
            deploy = cash
            efee = deploy * FEE_RATE
            units = (deploy - efee) / c["close"]
            cash -= deploy
            pos = {"ep": c["close"], "q": deploy, "hold": 0,
                   "tp": c["close"] * (1 + tp_pct), "units": units, "efee": efee}

    total = wins + losses
    return {"strategy": "vwap_reversion", "closes": total, "wins": wins, "losses": losses,
            "win_rate": round(wins / max(1, total) * 100, 1), "realized_pnl": round(realized, 4)}


# ──────────── STRATEGY 6: Dip-and-Rip (wick reversal) ────────────
def strat_dip_and_rip(candles, min_wick_pct=0.02, min_recovery=0.5, tp_pct=0.015, max_hold=12):
    """Buy when candle has deep wick (>2%) but closes in upper half. Reversal signal."""
    cash, pos, wins, losses, realized = STARTING_CASH, None, 0, 0, 0.0

    for i in range(1, len(candles)):
        c = candles[i]
        bar_range = c["high"] - c["low"]
        if bar_range <= 0 or c["open"] == 0:
            continue
        wick_depth = (c["open"] - c["low"]) / c["open"]
        recovery = (c["close"] - c["low"]) / bar_range if bar_range > 0 else 0

        if pos:
            pos["hold"] += 1
            hit_tp = c["high"] >= pos["tp"]
            timeout = pos["hold"] >= max_hold

            if hit_tp or timeout:
                exit_p = pos["tp"] if hit_tp else c["close"]
                gross = (exit_p - pos["ep"]) * pos["units"]
                fee = exit_p * pos["units"] * FEE_RATE
                net = gross - pos["efee"] - fee
                cash += pos["q"] + net
                realized += net
                wins += 1 if net > 0 else 0
                losses += 1 if net <= 0 else 0
                pos = None

        if pos is None and cash >= 5.0:
            if wick_depth >= min_wick_pct and recovery >= min_recovery:
                deploy = cash
                efee = deploy * FEE_RATE
                units = (deploy - efee) / c["close"]
                cash -= deploy
                pos = {"ep": c["close"], "q": deploy, "hold": 0,
                       "tp": c["close"] * (1 + tp_pct), "units": units, "efee": efee}

    total = wins + losses
    return {"strategy": "dip_and_rip", "closes": total, "wins": wins, "losses": losses,
            "win_rate": round(wins / max(1, total) * 100, 1), "realized_pnl": round(realized, 4)}


# ──────────── MAIN SCANNER ────────────
STRATEGIES = [
    ("bb_reversion", strat_bb_reversion),
    ("momentum_breakout", strat_momentum_breakout),
    ("volume_spike_fade", strat_volume_spike_fade),
    ("red_reversal_4", strat_red_reversal),
    ("vwap_reversion", strat_vwap_reversion),
    ("dip_and_rip", strat_dip_and_rip),
]


def main():
    client = CoinbaseAdvancedClient()
    all_results = []

    print(f"Multi-Strategy Edge Scanner: {len(COINS)} coins × {len(STRATEGIES)} strategies")
    print(f"{'':>15}", end="")
    for name, _ in STRATEGIES:
        print(f"  {name[:12]:>12}", end="")
    print()
    print("-" * (15 + 14 * len(STRATEGIES)))

    for coin in COINS:
        try:
            candles = fetch_candles(client, coin)
        except Exception as e:
            print(f"{coin:>15}  ERROR: {e}")
            continue

        if len(candles) < 30:
            print(f"{coin:>15}  SKIP ({len(candles)} candles)")
            continue

        print(f"{coin:>15}", end="")
        for name, func in STRATEGIES:
            try:
                result = func(candles)
                result["coin"] = coin
                result["candles"] = len(candles)
                all_results.append(result)

                pnl = result["realized_pnl"]
                flag = "+" if pnl > 0 else " "
                trades = result["closes"]
                if trades > 0:
                    print(f"  {flag}${pnl:>7.2f}({trades:>2})", end="")
                else:
                    print(f"  {'---':>12}", end="")
            except Exception as e:
                print(f"  {'ERR':>12}", end="")
                all_results.append({"strategy": name, "coin": coin, "error": str(e)})
        print()
        time.sleep(0.3)

    # Summary: best strategy per coin
    print("\n" + "=" * 80)
    print("TOP EDGES (positive PnL only):")
    print(f"{'Strategy':>20}  {'Coin':>15}  {'Trades':>6}  {'WR':>5}  {'PnL':>10}")
    print("-" * 65)

    winners = [r for r in all_results if "error" not in r and r["realized_pnl"] > 0]
    winners.sort(key=lambda x: x["realized_pnl"], reverse=True)
    for r in winners[:20]:
        print(f"{r['strategy']:>20}  {r['coin']:>15}  {r['closes']:>6}  "
              f"{r['win_rate']:>4.0f}%  ${r['realized_pnl']:>8.2f}")

    # Strategy family summary
    print("\n" + "=" * 80)
    print("STRATEGY FAMILY SUMMARY (across all coins):")
    print(f"{'Strategy':>20}  {'Coins+':>6}  {'Coins-':>6}  {'Total PnL':>10}  {'Avg PnL':>10}")
    print("-" * 60)

    for name, _ in STRATEGIES:
        strat_results = [r for r in all_results if r.get("strategy") == name and "error" not in r]
        pos = sum(1 for r in strat_results if r["realized_pnl"] > 0)
        neg = sum(1 for r in strat_results if r["realized_pnl"] <= 0)
        total_pnl = sum(r["realized_pnl"] for r in strat_results)
        avg_pnl = total_pnl / max(1, len(strat_results))
        flag = "🟢" if pos > neg else "🔴"
        print(f"{flag} {name:>18}  {pos:>6}  {neg:>6}  ${total_pnl:>8.2f}  ${avg_pnl:>8.2f}")

    RESULTS_PATH.parent.mkdir(parents=True, exist_ok=True)
    RESULTS_PATH.write_text(json.dumps(all_results, indent=2), encoding="utf-8")
    print(f"\nFull results: {RESULTS_PATH}")


if __name__ == "__main__":
    main()
