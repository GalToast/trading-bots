#!/usr/bin/env python3
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import optimize_allocation as alloc


ROOT = Path(__file__).resolve().parent.parent
SOURCE_PATH = ROOT / "reports" / "allocation_optimizer.json"
OUT_JSON_PATH = ROOT / "reports" / "allocation_optimizer_reconciliation.json"
OUT_MD_PATH = ROOT / "reports" / "allocation_optimizer_reconciliation.md"

CANONICAL_DEPLOY_FRACTION = 0.95
CANONICAL_MIN_ENTRY_CASH = 10.0


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def load_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def build_allocation_map(report: dict[str, Any], plan_name: str) -> dict[str, float]:
    if plan_name == "equal_split":
        per_coin = float((report.get("equal_split") or {}).get("per_coin", 0.0) or 0.0)
        return {coin: per_coin for coin in list(report.get("active_coins") or [])}
    payload = dict(report.get(plan_name) or {})
    return {str(coin): float(amount or 0.0) for coin, amount in dict(payload.get("allocation") or {}).items()}


def canonical_simulate_coin(coin: str, allocation: float) -> dict[str, Any]:
    if allocation < CANONICAL_MIN_ENTRY_CASH:
        return {
            "feasible": False,
            "reason": f"allocation ${allocation:.2f} below canonical min_entry_cash ${CANONICAL_MIN_ENTRY_CASH:.2f}",
            "canonical_net_pnl": 0.0,
            "canonical_trades": 0,
            "canonical_win_rate": None,
        }

    candles = alloc.load_candles(coin)
    if not candles:
        return {
            "feasible": False,
            "reason": "missing cached candles",
            "canonical_net_pnl": 0.0,
            "canonical_trades": 0,
            "canonical_win_rate": None,
        }

    strategy = dict(alloc.COIN_STRATEGIES[coin])
    strategy_name = str(strategy["strategy"])
    strategy_params = dict(strategy.get("params") or {})
    trade_params = dict(alloc.STRATEGY_TRADE_PARAMS[strategy_name])
    tp_pct = float(trade_params["tp_pct"])
    sl_pct = float(trade_params["sl_pct"])
    max_hold = int(trade_params["max_hold"])

    cash = allocation
    position: dict[str, Any] | None = None
    trades = 0
    wins = 0
    losses = 0
    total_fees = 0.0
    signals = 0
    candle_history: list[dict[str, Any]] = []
    closes: list[float] = []

    for candle in candles:
        ts = int(candle["time"])
        open_price = float(candle["open"])
        high = float(candle["high"])
        low = float(candle["low"])
        close = float(candle["close"])

        if open_price <= 0 or close <= 0:
            continue

        closes.append(close)
        candle_history.append(candle)
        if len(candle_history) > 500:
            candle_history = candle_history[-500:]
            closes = closes[-500:]

        if position:
            position["hold"] += 1
            exit_price = None
            if high >= position["tp"]:
                exit_price = position["tp"]
            elif position["sl"] > 0 and low <= position["sl"]:
                exit_price = position["sl"]
            elif position["hold"] >= max_hold:
                exit_price = close

            if exit_price is not None:
                units = position["units"]
                gross = (exit_price - position["ep"]) * units
                net = gross - position["entry_fee"] - (exit_price * units * alloc.FEE_RATE)
                cash += position["deploy"] + net
                trades += 1
                total_fees += position["entry_fee"] + exit_price * units * alloc.FEE_RATE
                if net > 0:
                    wins += 1
                else:
                    losses += 1
                position = None

        if position is None and cash >= CANONICAL_MIN_ENTRY_CASH:
            signal = False
            if strategy_name == "fibonacci_breakout":
                signal = alloc._fibonacci_entry(candle_history, closes, strategy_params)
            elif strategy_name == "supertrend":
                signal = alloc._supertrend_entry(candle_history, strategy_params)
            elif strategy_name == "momentum":
                signal = alloc._momentum_entry(candle_history, strategy_params)

            if signal:
                signals += 1
                deploy = cash * CANONICAL_DEPLOY_FRACTION
                entry_fee = deploy * alloc.FEE_RATE
                units = (deploy - entry_fee) / open_price
                tp = open_price * (1 + tp_pct)
                sl = open_price * (1 - sl_pct) if sl_pct > 0 else 0.0
                cash -= deploy
                position = {
                    "ep": open_price,
                    "deploy": deploy,
                    "units": units,
                    "tp": tp,
                    "sl": sl,
                    "hold": 0,
                    "entry_fee": entry_fee,
                    "ts": ts,
                }

    if position:
        last_close = float(candles[-1]["close"])
        gross = (last_close - position["ep"]) * position["units"]
        net = gross - position["entry_fee"] - (last_close * position["units"] * alloc.FEE_RATE)
        cash += position["deploy"] + net
        trades += 1
        total_fees += position["entry_fee"] + last_close * position["units"] * alloc.FEE_RATE
        if net > 0:
            wins += 1
        else:
            losses += 1

    pnl = round(cash - allocation, 4)
    return {
        "feasible": True,
        "reason": "",
        "canonical_net_pnl": pnl,
        "canonical_trades": trades,
        "canonical_win_rate": round(wins / max(1, trades) * 100, 1) if trades > 0 else 0.0,
        "signals": signals,
        "total_fees": round(total_fees, 4),
    }


def reconcile_plan(report: dict[str, Any], plan_name: str) -> dict[str, Any]:
    allocation = build_allocation_map(report, plan_name)
    projected = dict((report.get(plan_name) or {}).get("per_coin_pnl") or {})
    per_coin: dict[str, Any] = {}
    canonical_total = 0.0
    projected_total = 0.0
    feasible_count = 0
    for coin, amount in allocation.items():
        projected_pnl = float(projected.get(coin, 0.0) or 0.0)
        replay = canonical_simulate_coin(coin, float(amount))
        canonical_total += float(replay["canonical_net_pnl"])
        projected_total += projected_pnl
        if replay["feasible"]:
            feasible_count += 1
        per_coin[coin] = {
            "allocation": round(float(amount), 2),
            "projected_pnl": round(projected_pnl, 4),
            "canonical_net_pnl": replay["canonical_net_pnl"],
            "delta_vs_projected": round(replay["canonical_net_pnl"] - projected_pnl, 4),
            "feasible": replay["feasible"],
            "reason": replay["reason"],
            "canonical_trades": replay["canonical_trades"],
            "canonical_win_rate": replay["canonical_win_rate"],
        }
    return {
        "plan_name": plan_name,
        "feasible_count": feasible_count,
        "coin_count": len(allocation),
        "projected_total_pnl": round(projected_total, 4),
        "canonical_total_pnl": round(canonical_total, 4),
        "delta_vs_projected": round(canonical_total - projected_total, 4),
        "per_coin": per_coin,
    }


def reconcile() -> dict[str, Any]:
    report = load_json(SOURCE_PATH)
    plans = [
        reconcile_plan(report, "equal_split"),
        reconcile_plan(report, "optimized"),
        reconcile_plan(report, "proportional"),
    ]
    return {
        "generated_at": utc_now_iso(),
        "source_report": str(SOURCE_PATH),
        "canonical_assumptions": {
            "deploy_fraction": CANONICAL_DEPLOY_FRACTION,
            "min_entry_cash": CANONICAL_MIN_ENTRY_CASH,
            "entry": "candle open",
            "fills": "100%",
            "slippage": "0bps",
            "session_gate": "off",
        },
        "plans": plans,
    }


def render_md(payload: dict[str, Any]) -> str:
    lines = [
        "# Allocation Optimizer Reconciliation",
        "",
        "This report replays the saved allocation plans through a single-reference canonical engine on the same 30d candle cache.",
        "",
        "## Canonical Assumptions",
        "",
    ]
    for key, value in payload["canonical_assumptions"].items():
        lines.append(f"- `{key}`: `{value}`")
    lines.extend(["", "## Plans", ""])
    for plan in payload["plans"]:
        lines.append(f"### {plan['plan_name']}")
        lines.append(f"- feasible coins: `{plan['feasible_count']}/{plan['coin_count']}`")
        lines.append(f"- projected total: `{plan['projected_total_pnl']}`")
        lines.append(f"- canonical total: `{plan['canonical_total_pnl']}`")
        lines.append(f"- delta vs projected: `{plan['delta_vs_projected']}`")
        lines.append("")
        lines.append("| Coin | Allocation | Feasible | Projected | Canonical | Delta | Reason |")
        lines.append("|---|---:|---|---:|---:|---:|---|")
        for coin, row in plan["per_coin"].items():
            lines.append(
                f"| {coin} | {row['allocation']} | {row['feasible']} | {row['projected_pnl']} | "
                f"{row['canonical_net_pnl']} | {row['delta_vs_projected']} | {row['reason'] or '-'} |"
            )
        lines.append("")
    return "\n".join(lines)


def main() -> int:
    payload = reconcile()
    OUT_JSON_PATH.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    OUT_MD_PATH.write_text(render_md(payload), encoding="utf-8")
    print(f"Wrote {OUT_JSON_PATH}")
    print(f"Wrote {OUT_MD_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
