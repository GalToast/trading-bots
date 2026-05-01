#!/usr/bin/env python3
"""
Benchmark Framework - The Board's Single Source of Truth
========================================================
Lane 5: Head-to-head strategy testing with identical parameters.

Usage:
    python benchmark_framework.py                    # Run all benchmarks
    python benchmark_framework.py --test B001        # Run specific test
    python benchmark_framework.py --list             # List all tests

Any strategy claim gets tested here before deployment.
"""
from __future__ import annotations

import argparse
import json
import math
import time
import random
from pathlib import Path
from dataclasses import dataclass, field, asdict
from typing import Any, Callable

import sys
sys.path.insert(0, str(Path(__file__).resolve().parent))
from benchmark_harness import run_benchmark as run_harness_core
from benchmark_shared import BUILTIN_FILL_MODELS, RAVE_RSI_MR_BASELINE_PARAMS, framework_execution_kwargs
from candle_cache_service import load_candles
from coinbase_advanced_client import CoinbaseAdvancedClient

ROOT = Path(__file__).resolve().parent.parent
RESULTS_PATH = ROOT / "reports" / "benchmark_results.json"


@dataclass
class BenchmarkResult:
    test_id: str
    strategy: str
    coin: str
    execution_model: str  # "shadow", "realistic", "worst_case", "live"
    net_pnl: float = 0.0
    return_pct: float = 0.0
    trades: int = 0
    wins: int = 0
    losses: int = 0
    win_rate: float = 0.0
    avg_trade: float = 0.0
    total_volume: float = 0.0
    total_fees: float = 0.0
    max_drawdown: float = 0.0
    sharpe_ratio: float = 0.0
    profit_factor: float = 0.0
    avg_hold_bars: float = 0.0
    monthly_projection: float = 0.0
    status: str = "pending"  # "pass", "fail", "pending"
    notes: str = ""


@dataclass
class ExecutionConfig:
    fill_probability: float = 1.0
    latency_bars: int = 0
    entry_slippage_pct: float = 0.0
    exit_slippage_pct: float = 0.0
    name: str = "shadow"


# Predefined execution models
EXECUTION_MODELS = {
    "shadow": ExecutionConfig(name="shadow", **framework_execution_kwargs(BUILTIN_FILL_MODELS["perfect"])),
    "realistic": ExecutionConfig(name="realistic", **framework_execution_kwargs(BUILTIN_FILL_MODELS["realistic"])),
    "worst_case": ExecutionConfig(name="worst_case", **framework_execution_kwargs(BUILTIN_FILL_MODELS["harsh"])),
    "live": ExecutionConfig(1.0, 0, 0.0, 0.0, "live"),  # Actual live fills
}


def resolve_execution(execution: ExecutionConfig | None) -> tuple[float, float, float, int]:
    if execution is None:
        return 1.0, 0.0, 0.0, 0
    return (
        max(0.0, min(1.0, float(execution.fill_probability))),
        max(0.0, float(execution.entry_slippage_pct) / 100.0),
        max(0.0, float(execution.exit_slippage_pct) / 100.0),
        max(0, int(execution.latency_bars)),
    )


def run_strategy(candles, strategy_fn, execution: ExecutionConfig,
                 starting_cash=48.0, fee_bps=40) -> dict[str, Any]:
    """
    Run a strategy with the given execution model.
    
    strategy_fn should yield (entry_signal, exit_signal) for each bar.
    """
    if len(candles) < 50:
        return {"error": "not enough candles"}
    
    fee_rate = fee_bps / 10000.0
    closes = [float(c["close"]) for c in candles]
    highs = [float(c["high"]) for c in candles]
    lows = [float(c["low"]) for c in candles]
    
    cash = starting_cash
    in_position = False
    position = None
    trades = []
    total_volume = 0.0
    total_fees = 0.0
    peak_equity = starting_cash
    max_dd = 0.0
    
    for i in range(50, len(candles) - 1):
        c = candles[i]
        h = highs[i]
        l = lows[i]
        cl = closes[i]
        
        # Get signals from strategy
        entry_signal, exit_signal = strategy_fn(i, candles, closes, highs, lows)
        
        # Apply execution model
        if exit_signal and in_position and position:
            # Try to exit (with fill probability and slippage)
            if random.random() < execution.fill_probability:
                base_exit = exit_signal
                exit_price = base_exit * (1 + execution.exit_slippage_pct / 100)
                
                qty = position["qty"]
                gross = (exit_price - position["entry"]) * qty
                entry_fee = position["entry"] * qty * fee_rate
                exit_fee = exit_price * qty * fee_rate
                net = gross - entry_fee - exit_fee
                
                cash += position["quote"] + net
                total_volume += position["quote"] + (exit_price * qty)
                total_fees += entry_fee + exit_fee
                
                equity = cash
                peak_equity = max(peak_equity, equity)
                if peak_equity > 0:
                    dd = (peak_equity - equity) / peak_equity * 100
                    max_dd = max(max_dd, dd)
                
                trades.append({
                    "net": net,
                    "win": net > 0,
                    "hold_bars": i - position["bar"],
                })
                
                in_position = False
                position = None
        
        # Apply entry signal
        if entry_signal and not in_position:
            # Try to enter (with fill probability and slippage)
            if random.random() < execution.fill_probability:
                base_entry = entry_signal
                entry_price = base_entry * (1 - execution.entry_slippage_pct / 100)
                
                deploy = cash * 0.95
                entry_fee = deploy * fee_rate
                qty = (deploy - entry_fee) / entry_price
                
                if qty > 0:
                    cash -= deploy
                    position = {
                        "entry": entry_price,
                        "qty": qty,
                        "bar": i,
                        "quote": deploy,
                    }
                    in_position = True
    
    # Close remaining position
    if position:
        exit_price = closes[-1] * (1 + execution.exit_slippage_pct / 100)
        qty = position["qty"]
        gross = (exit_price - position["entry"]) * qty
        entry_fee = position["entry"] * qty * fee_rate
        exit_fee = exit_price * qty * fee_rate
        net = gross - entry_fee - exit_fee
        
        cash += position["quote"] + net
        total_volume += position["quote"] + (exit_price * qty)
        total_fees += entry_fee + exit_fee
        trades.append({
            "net": net,
            "win": net > 0,
            "hold_bars": len(candles) - position["bar"],
        })
    
    net = cash - starting_cash
    wins = [t for t in trades if t["win"]]
    losses = [t for t in trades if not t["win"]]
    
    # Compute metrics
    avg_trade = net / max(1, len(trades))
    wr = len(wins) / max(1, len(trades)) * 100
    
    # Profit factor
    gross_wins = sum(t["net"] for t in wins)
    gross_losses = abs(sum(t["net"] for t in losses))
    pf = gross_wins / gross_losses if gross_losses > 0 else float("inf")
    
    # Sharpe ratio (simplified)
    if len(trades) > 1:
        returns = [t["net"] / starting_cash for t in trades]
        avg_ret = sum(returns) / len(returns)
        std_ret = (sum((r - avg_ret) ** 2 for r in returns) / len(returns)) ** 0.5
        sharpe = avg_ret / std_ret if std_ret > 0 else 0
    else:
        sharpe = 0
    
    avg_hold = sum(t["hold_bars"] for t in trades) / max(1, len(trades))
    
    # Monthly projection (assuming 5-min candles = 288 bars/day)
    bars = len(candles)
    days = bars / 288
    monthly = net / max(0.001, days) * 30
    
    return {
        "net_pnl": round(net, 2),
        "return_pct": round(net / starting_cash * 100, 1),
        "trades": len(trades),
        "wins": len(wins),
        "losses": len(losses),
        "win_rate": round(wr, 1),
        "avg_trade": round(avg_trade, 4),
        "total_volume": round(total_volume, 2),
        "total_fees": round(total_fees, 2),
        "max_drawdown": round(max_dd, 1),
        "sharpe_ratio": round(sharpe, 3),
        "profit_factor": round(pf, 3) if pf != float("inf") else "inf",
        "avg_hold_bars": round(avg_hold, 1),
        "monthly_projection": round(monthly, 2),
    }


# ============================================================================
# BENCHMARK DEFINITIONS
# ============================================================================

def rsi_rave_strategy(
    candles,
    period=RAVE_RSI_MR_BASELINE_PARAMS["rsi_period"],
    os_thresh=RAVE_RSI_MR_BASELINE_PARAMS["os_thresh"],
    tp_pct=RAVE_RSI_MR_BASELINE_PARAMS["tp_pct"] / 100.0,
    sl_pct=RAVE_RSI_MR_BASELINE_PARAMS["sl_pct"] / 100.0,
    max_hold=RAVE_RSI_MR_BASELINE_PARAMS["max_hold"],
    fee_bps=40,
    starting_cash=48.0,
    execution=None,
):
    """B001/B002: RAVE RSI(3)<30, TP25%, No SL, 48-bar max hold"""
    if len(candles) < period + 50:
        return {"error": "not enough candles"}
    fill_prob, entry_slip, exit_slip, _latency = resolve_execution(execution)
    harness_model = {
        "fill_prob": fill_prob,
        "entry_slippage_bps": entry_slip * 10000.0,
        "exit_slippage_bps": exit_slip * 10000.0,
    }
    harness_result = run_harness_core(
        candles,
        [],
        {
            "rsi_period": int(period),
            "os_thresh": float(os_thresh),
            "tp_pct": float(tp_pct * 100.0 if tp_pct <= 1.0 else tp_pct),
            "max_hold": int(max_hold),
            "sl_pct": float(sl_pct * 100.0 if sl_pct <= 1.0 else sl_pct),
        },
        fee_bps / 10000.0,
        harness_model,
        starting_cash,
    )
    return {
        "net_pnl": round(float(harness_result["net"]), 2),
        "return_pct": round(float(harness_result["return_pct"]), 1),
        "trades": int(harness_result["closes"]),
        "wins": int(harness_result["wins"]),
        "losses": int(harness_result["losses"]),
        "win_rate": round(float(harness_result["win_rate"]), 1),
        "avg_trade": round(float(harness_result["net"]) / max(1, int(harness_result["closes"])), 4),
        "total_volume": round(float(harness_result["total_volume"]), 2),
        "total_fees": round(float(harness_result["total_fees"]), 2),
        "max_drawdown": round(float(harness_result["max_dd"]), 1),
        "sharpe_ratio": 0.0,
        "profit_factor": 0.0,
        "avg_hold_bars": 0.0,
        "monthly_projection": 0.0,
    }


def strict_warp_strategy(candles, fee_bps=40, starting_cash=48.0, execution=None):
    """B003-B006: Simplified warp (large movement trigger)"""
    if len(candles) < 50:
        return {"error": "not enough candles"}

    fill_prob, entry_slip, exit_slip, latency = resolve_execution(execution)
    
    fee_rate = fee_bps / 10000.0
    closes = [float(c["close"]) for c in candles]
    highs = [float(c["high"]) for c in candles]
    lows = [float(c["low"]) for c in candles]
    
    cash = starting_cash
    in_position = False
    position = None
    trades = []
    total_volume = 0.0
    total_fees = 0.0
    
    for i in range(2, len(candles) - 1):
        h = highs[i]
        l = lows[i]
        cl = closes[i]
        prev_close = closes[i-1]
        ret_pct = abs(cl - prev_close) / prev_close * 100
        
        # EXIT
        if in_position and position:
            exit_price = None
            if h >= position["target"]:
                exit_price = position["target"]
            elif (i - position["bar"]) >= 48:
                exit_price = cl
            
            if exit_price is not None:
                if random.random() >= fill_prob:
                    continue
                if latency > 0:
                    delayed_index = min(i + latency, len(closes) - 1)
                    exit_price = closes[delayed_index]
                exit_price = exit_price * (1 - exit_slip)
                qty = position["qty"]
                gross = (exit_price - position["entry"]) * qty
                entry_fee = position["entry"] * qty * fee_rate
                exit_fee = exit_price * qty * fee_rate
                net = gross - entry_fee - exit_fee
                
                cash += position["quote"] + net
                total_volume += position["quote"] + (exit_price * qty)
                total_fees += entry_fee + exit_fee
                trades.append({"net": net, "win": net > 0, "hold_bars": i - position["bar"]})
                in_position = False
                position = None
        
        # ENTRY on large movement
        if not in_position and cash >= 10.0 and ret_pct > 2.0:
            if random.random() >= fill_prob:
                continue
            entry_index = min(i + latency, len(closes) - 1) if latency > 0 else i
            deploy = cash * 0.95
            entry_price = closes[entry_index] * (1 + entry_slip)
            entry_fee = entry_price * (deploy / entry_price) * fee_rate
            qty = (deploy - entry_fee) / entry_price
            
            if qty > 0:
                cash -= deploy
                position = {
                    "entry": entry_price, "qty": qty, "bar": entry_index, "quote": deploy,
                    "target": entry_price * 1.006,  # 0.6% target
                }
                in_position = True
    
    if position:
        cash += position["quote"]
    
    net = cash - starting_cash
    wins = [t for t in trades if t["win"]]
    
    return {
        "net_pnl": round(net, 2),
        "return_pct": round(net / starting_cash * 100, 1),
        "trades": len(trades),
        "wins": len(wins),
        "losses": len(trades) - len(wins),
        "win_rate": round(len(wins) / max(1, len(trades)) * 100, 1),
        "avg_trade": round(net / max(1, len(trades)), 4),
        "total_volume": round(total_volume, 2),
        "total_fees": round(total_fees, 2),
        "max_drawdown": 0.0,
        "sharpe_ratio": 0.0,
        "profit_factor": 0.0,
        "avg_hold_bars": 0.0,
        "monthly_projection": 0.0,
    }


# ============================================================================
# BENCHMARK SUITE
# ============================================================================

BENCHMARKS = [
    # Test ID | Name | Coin | Strategy | Dynamic sign expectation
    ("B001", "RAVE RSI MR (Shadow)", "RAVE-USD", rsi_rave_strategy, "shadow", None, "pass"),
    ("B002", "RAVE RSI MR (Realistic)", "RAVE-USD", rsi_rave_strategy, "realistic", None, "pass"),
    ("B003", "Strict Warp IOTX (Shadow)", "IOTX-USD", strict_warp_strategy, "shadow", None, "fail"),
    ("B004", "Strict Warp IOTX (Realistic)", "IOTX-USD", strict_warp_strategy, "realistic", None, "fail"),
    ("B005", "Strict Warp BAL (Shadow)", "BAL-USD", strict_warp_strategy, "shadow", None, "fail"),
    ("B006", "Strict Warp BAL (Realistic)", "BAL-USD", strict_warp_strategy, "realistic", None, "fail"),
]


def run_benchmark(test_id, coin, strategy_fn, execution_name, expected_pnl=None, expected_status=None):
    """Run a single benchmark test."""
    print(f"  Running {test_id}: {coin} ({execution_name})...")
    
    # Load cached data
    candles = load_candles(coin, "FIVE_MINUTE", 7, max_age_minutes=10000)
    if not candles or len(candles) < 50:
        return BenchmarkResult(
            test_id=test_id, strategy=str(strategy_fn.__name__), coin=coin,
            execution_model=execution_name, status="error", notes="Insufficient data"
        )
    
    # Get execution model
    execution = EXECUTION_MODELS.get(execution_name, EXECUTION_MODELS["shadow"])
    random.seed(f"{test_id}|{coin}|{execution_name}")

    # Run strategy with execution model applied
    result = strategy_fn(candles, execution=execution)

    if "error" in result:
        return BenchmarkResult(
            test_id=test_id, strategy=str(strategy_fn.__name__), coin=coin,
            execution_model=execution_name, status="error", notes=result["error"]
        )

    # Determine pass/fail
    status = "pass"
    if expected_pnl is not None:
        # Allow 20% tolerance or $10, whichever is larger
        tolerance = max(abs(expected_pnl) * 0.2, 10)
        if abs(result["net_pnl"] - expected_pnl) > tolerance:
            status = "fail"
    elif expected_status == "pass":
        status = "pass" if result["net_pnl"] > 0 else "fail"
    elif expected_status == "fail":
        status = "pass" if result["net_pnl"] < 0 else "fail"

    return BenchmarkResult(
        test_id=test_id,
        strategy=str(strategy_fn.__name__),
        coin=coin,
        execution_model=execution_name,
        status=status,
        **{k: v for k, v in result.items() if k in BenchmarkResult.__dataclass_fields__}
    )


def main():
    parser = argparse.ArgumentParser(description="Benchmark Framework - Single Source of Truth")
    parser.add_argument("--test", type=str, help="Run specific test (e.g., B001)")
    parser.add_argument("--list", action="store_true", help="List all benchmarks")
    args = parser.parse_args()
    
    if args.list:
        print(f"\n{'Test ID':<8} {'Strategy':<30} {'Coin':<12} {'Execution':<12} {'Expected':>10} {'Status':>8}")
        print(f"{'-'*85}")
        for test_id, name, coin, strategy, execution, expected, status in BENCHMARKS:
            print(f"{test_id:<8} {name:<30} {coin:<12} {execution:<12} ${expected:>8.2f} {status:>8}")
        return 0
    
    print("=" * 80)
    print("  BENCHMARK FRAMEWORK - Single Source of Truth")
    print("=" * 80)
    
    # Filter tests
    tests_to_run = BENCHMARKS
    if args.test:
        tests_to_run = [t for t in BENCHMARKS if t[0] == args.test]
        if not tests_to_run:
            print(f"Test {args.test} not found.")
            return 1
    
    results = []
    for test_id, name, coin, strategy_fn, execution, expected, expected_status in tests_to_run:
        result = run_benchmark(test_id, coin, strategy_fn, execution, expected, expected_status)
        results.append(result)
        
        status_icon = {"pass": "PASS", "fail": "FAIL", "error": "ERR", "pending": "PEND"}.get(result.status, "?")
        print(f"  {status_icon} {test_id}: ${result.net_pnl:+.2f} ({result.trades}t, {result.win_rate}%WR)")
    
    # Summary
    print(f"\n{'='*80}")
    print(f"  BENCHMARK RESULTS")
    print(f"{'='*80}")
    
    print(f"\n{'Test':<8} {'Strategy':<25} {'Coin':<12} {'Net $':>8} {'Trades':>7} {'WR%':>6} {'DD%':>6} {'Status':>8}")
    print(f"{'-'*80}")
    for r in results:
        print(f"{r.test_id:<8} {r.strategy:<25} {r.coin:<12} ${r.net_pnl:>6.2f} {r.trades:>7} {r.win_rate:>5.1f}% {r.max_drawdown:>5.1f}% {r.status:>8}")
    
    # Save results
    results_data = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "results": [asdict(r) for r in results],
    }
    
    RESULTS_PATH.parent.mkdir(parents=True, exist_ok=True)
    RESULTS_PATH.write_text(json.dumps(results_data, indent=2), encoding="utf-8")
    print(f"\n  Results saved to: {RESULTS_PATH}")
    
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
