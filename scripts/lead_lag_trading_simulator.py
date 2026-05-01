#!/usr/bin/env python3
"""
Lane 1: Lead-Lag Event-Driven Trading Simulator

Take the spike events detected by lead_lag_event_logger.py and simulate
actual trades: enter altcoin when BTC/ETH spikes, exit after reaction or timeout.

Tests:
1. Entry timing: enter on NEXT bar after BTC spike (realistic) vs same bar (optimistic)
2. Exit strategies: TP/SL vs fixed hold vs reaction-based exit
3. Fee impact: 40bps → 15bps → 10bps tiers
4. Position sizing: fixed $50 vs compounding

Output: reports/lead_lag_trading_results.json
"""
from __future__ import annotations

import json
import math
import statistics
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from candle_cache_service import load_candles

ROOT = Path(__file__).resolve().parent.parent
REPORT_DIR = ROOT / "reports"
REPORT_DIR.mkdir(exist_ok=True)

LEADERS = ["BTC-USD", "ETH-USD"]
LAGGERS = ["RAVE-USD", "IOTX-USD", "BAL-USD", "BLUR-USD", "ALEPH-USD"]

SPIKE_THRESHOLD_PCT = 0.2  # Leader must move >0.2% in one bar


def simulate_event_driven_trading(candles_data, entry_mode="next_bar",
                                    exit_strategy="tp_sl", tp_pct=0.05, sl_pct=0.03,
                                    hold_bars=5, fee_rate=0.0040,
                                    position_size="fixed", fixed_amount=50):
    """
    Simulate trading BTC/ETH spike → altcoin reaction signals.

    entry_mode:
      - "next_bar": enter on the bar AFTER the spike bar (realistic)
      - "same_bar": enter on the same bar as the spike (optimistic/impossible)

    exit_strategy:
      - "tp_sl": exit at TP or SL or timeout
      - "fixed_hold": exit after exactly N bars
      - "reaction_exit": exit when lagger reverses direction

    position_size:
      - "fixed": always trade $X
      - "compound": reinvest all cash
    """
    all_results = []

    for leader in LEADERS:
        if leader not in candles_data:
            continue
        leader_candles = candles_data[leader]
        leader_closes = [float(c["close"]) for c in leader_candles]

        for lagger in LAGGERS:
            if lagger not in candles_data:
                continue
            lagger_candles = candles_data[lagger]
            lagger_opens = [float(c["open"]) for c in lagger_candles]
            lagger_highs = [float(c["high"]) for c in lagger_candles]
            lagger_lows = [float(c["low"]) for c in lagger_candles]
            lagger_closes = [float(c["close"]) for c in lagger_candles]

            # Align
            min_len = min(len(leader_closes), len(lagger_closes))

            if position_size == "compound":
                cash = 48.0
            else:
                cash = fixed_amount

            starting_cash = cash
            trades = []

            for i in range(2, min_len - hold_bars - 1):
                # Detect spike
                leader_ret = (leader_closes[i] - leader_closes[i - 1]) / leader_closes[i - 1]
                leader_ret_pct = abs(leader_ret) * 100

                if leader_ret_pct < SPIKE_THRESHOLD_PCT:
                    continue

                leader_dir = 1 if leader_ret > 0 else -1

                # Entry point
                if entry_mode == "next_bar":
                    entry_bar = i + 1
                else:
                    entry_bar = i

                if entry_bar >= min_len - hold_bars:
                    continue

                # Determine entry price
                if entry_mode == "next_bar":
                    entry_price = lagger_opens[entry_bar]  # Realistic: enter at next bar's open
                else:
                    entry_price = lagger_closes[entry_bar]  # Optimistic

                if entry_price == 0:
                    continue

                # Position sizing
                if position_size == "compound":
                    deploy = cash * 0.95
                else:
                    deploy = fixed_amount

                if deploy < 1.0 or deploy > cash:
                    continue

                entry_fee = deploy * fee_rate
                units = (deploy - entry_fee) / entry_price
                cash_before_trade = cash
                cash -= deploy

                # Exit logic
                exit_price = None
                exit_reason = None
                exit_bar = None

                if exit_strategy == "tp_sl":
                    tp_price = entry_price * (1 + tp_pct) if leader_dir > 0 else entry_price * (1 - tp_pct)
                    sl_price = entry_price * (1 - sl_pct) if leader_dir > 0 else entry_price * (1 + sl_pct)

                    for b in range(1, hold_bars + 1):
                        idx = entry_bar + b
                        if idx >= min_len:
                            break
                        bar_high = lagger_highs[idx]
                        bar_low = lagger_lows[idx]

                        if leader_dir > 0:
                            # Long: check SL first (intra-bar)
                            if bar_low <= sl_price:
                                exit_price = sl_price
                                exit_reason = "sl"
                                exit_bar = idx
                                break
                            if bar_high >= tp_price:
                                exit_price = tp_price
                                exit_reason = "tp"
                                exit_bar = idx
                                break
                        else:
                            # Short simulation (simplified — just skip for spot)
                            break

                        if b == hold_bars:
                            exit_price = lagger_closes[idx]
                            exit_reason = "timeout"
                            exit_bar = idx

                elif exit_strategy == "fixed_hold":
                    idx = entry_bar + hold_bars
                    if idx < min_len:
                        exit_price = lagger_closes[idx]
                        exit_reason = "timeout"
                        exit_bar = idx

                elif exit_strategy == "reaction_exit":
                    # Exit when lagger reverses (goes against leader direction)
                    for b in range(1, hold_bars + 1):
                        idx = entry_bar + b
                        if idx >= min_len:
                            break

                        # Current bar return from entry
                        bar_ret = (lagger_closes[idx] - entry_price) / entry_price

                        if leader_dir > 0:
                            if bar_ret < -sl_pct:
                                exit_price = entry_price * (1 - sl_pct)
                                exit_reason = "sl"
                                exit_bar = idx
                                break
                            elif bar_ret > tp_pct:
                                exit_price = entry_price * (1 + tp_pct)
                                exit_reason = "tp"
                                exit_bar = idx
                                break
                            # Reversal: lagger starts going down after going up
                            if b > 1:
                                prev_ret = (lagger_closes[idx - 1] - entry_price) / entry_price
                                if prev_ret > 0 and bar_ret < prev_ret * 0.5:
                                    # Momentum dying — exit
                                    exit_price = lagger_closes[idx]
                                    exit_reason = "reversal"
                                    exit_bar = idx
                                    break

                        if b == hold_bars:
                            exit_price = lagger_closes[idx]
                            exit_reason = "timeout"
                            exit_bar = idx

                if exit_price is None or exit_price == 0:
                    continue

                exit_proceeds = exit_price * units
                exit_fee = exit_proceeds * fee_rate
                net = exit_proceeds - deploy - entry_fee - exit_fee

                if position_size == "compound":
                    cash += exit_proceeds - exit_fee
                else:
                    cash = cash_before_trade + net

                trade = {
                    "leader": leader,
                    "lagger": lagger,
                    "leader_spike_pct": round(leader_ret_pct, 3),
                    "leader_dir": leader_dir,
                    "entry_bar": entry_bar,
                    "exit_bar": exit_bar,
                    "entry_price": round(entry_price, 6),
                    "exit_price": round(exit_price, 6),
                    "exit_reason": exit_reason,
                    "hold_bars": exit_bar - entry_bar if exit_bar else 0,
                    "net": round(net, 4),
                    "net_pct": round(net / deploy * 100, 3),
                    "win": net > 0,
                }
                trades.append(trade)

            if not trades:
                continue

            wins = [t for t in trades if t["win"]]
            losses = [t for t in trades if not t["win"]]
            total_net = cash - starting_cash if position_size == "compound" else sum(t["net"] for t in trades)

            result = {
                "leader": leader,
                "lagger": lagger,
                "entry_mode": entry_mode,
                "exit_strategy": exit_strategy,
                "fee_rate_bps": round(fee_rate * 10000, 0),
                "position_size": position_size,
                "trades": len(trades),
                "wins": len(wins),
                "losses": len(losses),
                "wr": round(len(wins) / len(trades) * 100, 1),
                "total_net": round(total_net, 2),
                "total_return_pct": round(total_net / starting_cash * 100, 1),
                "avg_win_pct": round(statistics.mean([t["net_pct"] for t in wins]), 3) if wins else 0,
                "avg_loss_pct": round(statistics.mean([t["net_pct"] for t in losses]), 3) if losses else 0,
                "exit_reasons": {
                    "tp": sum(1 for t in trades if t["exit_reason"] == "tp"),
                    "sl": sum(1 for t in trades if t["exit_reason"] == "sl"),
                    "timeout": sum(1 for t in trades if t["exit_reason"] == "timeout"),
                    "reversal": sum(1 for t in trades if t["exit_reason"] == "reversal"),
                },
                "avg_hold_bars": round(statistics.mean([t["hold_bars"] for t in trades]), 2),
            }
            all_results.append(result)

    return all_results


def main():
    print("=" * 80)
    print("  LANE 1: LEAD-LAG — Event-Driven Trading Simulator")
    print("=" * 80)

    # Load candles
    all_products = LEADERS + LAGGERS
    candles_data = {}
    for pid in all_products:
        candles = load_candles(pid, "ONE_MINUTE", 7, max_age_minutes=7 * 24 * 60)
        if candles:
            candles_data[pid] = candles

    if len(candles_data) < 3:
        print("ERROR: Not enough candle data.")
        return 1

    # Run multiple configurations
    configs = [
        # (entry_mode, exit_strategy, tp_pct, sl_pct, fee_rate, position_size, fixed_amount, hold_bars)
        # Realistic baseline with wider TP/SL for microcaps
        ("next_bar", "tp_sl", 0.10, 0.05, 0.0040, "fixed", 50, 5),
        ("next_bar", "tp_sl", 0.15, 0.05, 0.0040, "fixed", 50, 5),
        ("next_bar", "tp_sl", 0.20, 0.08, 0.0040, "fixed", 50, 5),
        # Tighter holds — get in, get out fast
        ("next_bar", "tp_sl", 0.05, 0.03, 0.0040, "fixed", 50, 3),
        ("next_bar", "tp_sl", 0.08, 0.03, 0.0040, "fixed", 50, 3),
        # Fee tier comparison
        ("next_bar", "tp_sl", 0.10, 0.05, 0.0015, "fixed", 50, 5),
        ("next_bar", "tp_sl", 0.10, 0.05, 0.0010, "fixed", 50, 5),
        # Reaction exit
        ("next_bar", "reaction_exit", 0.10, 0.05, 0.0040, "fixed", 50, 5),
        # Fixed hold
        ("next_bar", "fixed_hold", 0, 0, 0.0040, "fixed", 50, 2),
        ("next_bar", "fixed_hold", 0, 0, 0.0040, "fixed", 50, 3),
        # Optimistic upper bound (same bar entry)
        ("same_bar", "tp_sl", 0.10, 0.05, 0.0040, "fixed", 50, 5),
        # Compounding (best config only)
        ("next_bar", "tp_sl", 0.10, 0.05, 0.0040, "compound", None, 5),
    ]

    all_results = {}

    for entry_mode, exit_strat, tp, sl, fee, pos_size, fixed_amt, hold in configs:
        results = simulate_event_driven_trading(
            candles_data,
            entry_mode=entry_mode,
            exit_strategy=exit_strat,
            tp_pct=tp,
            sl_pct=sl,
            hold_bars=hold,
            fee_rate=fee,
            position_size=pos_size,
            fixed_amount=fixed_amt or 50,
        )

        config_key = f"{entry_mode}|{exit_strat}|TP{tp*100:.0f}SL{sl*100:.0f}|{fee*10000:.0f}bps|{pos_size}|{hold}b"
        all_results[config_key] = results

        # Print summary
        print(f"\n{'─' * 70}")
        print(f"  Config: {config_key}")
        print(f"{'─' * 70}")

        for r in results:
            if r["trades"] >= 1:
                emoji = "✅" if r["total_net"] > 0 else "❌"
                print(f"  {emoji} {r['leader']}→{r['lagger']:<12} {r['trades']:>4}t  "
                      f"{r['wr']:>5.1f}%WR  ${r['total_net']:>+8.2f}  "
                      f"avg_win={r['avg_win_pct']:+.3f}%  avg_loss={r['avg_loss_pct']:+.3f}%  "
                      f"hold={r['avg_hold_bars']:.1f}b")

    # Find best strategy
    best_config = None
    best_strategy = None
    best_net = -999999

    for config, results in all_results.items():
        for r in results:
            if r["trades"] >= 3 and r["total_net"] > best_net:
                best_net = r["total_net"]
                best_config = config
                best_strategy = r

    print(f"\n{'=' * 80}")
    print(f"  OVERALL BEST (min 10 trades):")
    print(f"{'=' * 80}")
    if best_strategy:
        print(f"  Config: {best_config}")
        print(f"  {best_strategy['leader']}→{best_strategy['lagger']}: "
              f"{best_strategy['trades']}t, {best_strategy['wr']}% WR, "
              f"${best_strategy['total_net']:+.2f} ({best_strategy['total_return_pct']:+.1f}%)")
    else:
        print(f"  No strategy produced 10+ profitable trades.")

    # Save
    output_path = REPORT_DIR / "lead_lag_trading_results.json"
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(all_results, f, indent=2, default=str)
    print(f"\n  Results saved: {output_path}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
