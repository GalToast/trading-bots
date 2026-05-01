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

import optimal_portfolio_optimizer as optimizer


ROOT = Path(__file__).resolve().parent.parent
SOURCE_PATH = ROOT / "reports" / "optimal_portfolio_optimizer.json"
OUT_JSON_PATH = ROOT / "reports" / "optimal_portfolio_optimizer_reconciliation.json"
OUT_MD_PATH = ROOT / "reports" / "optimal_portfolio_optimizer_reconciliation.md"

CANONICAL_DEPLOY_FRACTION = 0.95
CANONICAL_MIN_ENTRY_CASH = 10.0


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def load_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def canonical_simulate(candles: list[dict[str, Any]], strategy_name: str, starting_cash: float) -> dict[str, Any]:
    if starting_cash < CANONICAL_MIN_ENTRY_CASH:
        return {
            "feasible": False,
            "reason": f"allocation ${starting_cash:.2f} below canonical min_entry_cash ${CANONICAL_MIN_ENTRY_CASH:.2f}",
            "canonical_net_pnl": 0.0,
            "canonical_trades": 0,
            "canonical_win_rate": None,
            "canonical_signals": 0,
        }

    entry_fn = optimizer.ENTRY_FUNCS[strategy_name]
    params = dict(optimizer.STRATEGIES[strategy_name]["params"])

    cash = starting_cash
    position: dict[str, Any] | None = None
    history: list[float] = []
    candle_history: list[dict[str, Any]] = []
    signals = 0
    trades = 0
    wins = 0
    losses = 0
    total_fees = 0.0

    tp_pct = params.get("tp_pct", 10.0) / 100.0
    sl_pct = params.get("sl_pct", 0.0) / 100.0
    max_hold = params.get("max_hold", 48)

    for candle in candles:
        close = float(candle["close"])
        high = float(candle["high"])
        low = float(candle["low"])
        open_price = float(candle["open"])

        if open_price <= 0 or close <= 0:
            continue

        history.append(close)
        candle_history.append(candle)
        if len(history) > 500:
            history = history[-500:]
            candle_history = candle_history[-500:]

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
                net = gross - position["entry_fee"] - (exit_price * units * optimizer.FEE_RATE)
                cash += position["deploy"] + net
                trades += 1
                total_fees += position["entry_fee"] + exit_price * units * optimizer.FEE_RATE
                if net > 0:
                    wins += 1
                else:
                    losses += 1
                position = None

        if position is None and cash >= CANONICAL_MIN_ENTRY_CASH:
            if entry_fn(candle_history, history, candle, params):
                signals += 1
                deploy = cash * CANONICAL_DEPLOY_FRACTION
                entry_fee = deploy * optimizer.FEE_RATE
                units = (deploy - entry_fee) / open_price
                tp = open_price * (1 + tp_pct)
                sl = open_price * (1 - sl_pct) if sl_pct > 0 else 0

                cash -= deploy
                position = {
                    "ep": open_price,
                    "deploy": deploy,
                    "units": units,
                    "tp": tp,
                    "sl": sl,
                    "hold": 0,
                    "entry_fee": entry_fee,
                }

    if position:
        last_close = float(candles[-1]["close"])
        gross = (last_close - position["ep"]) * position["units"]
        net = gross - position["entry_fee"] - (last_close * position["units"] * optimizer.FEE_RATE)
        cash += position["deploy"] + net
        trades += 1
        total_fees += position["entry_fee"] + last_close * position["units"] * optimizer.FEE_RATE
        if net > 0:
            wins += 1
        else:
            losses += 1

    return {
        "feasible": True,
        "reason": "",
        "canonical_net_pnl": round(cash - starting_cash, 4),
        "canonical_trades": trades,
        "canonical_win_rate": round(wins / max(1, trades) * 100, 1) if trades > 0 else 0.0,
        "canonical_signals": signals,
        "total_fees": round(total_fees, 4),
    }


def build_best_assignment(report: dict[str, Any], scenario_name: str) -> dict[str, Any]:
    scenario_results = dict((report.get("results") or {}).get(scenario_name) or {})
    assignment: dict[str, Any] = {}
    total_projected = 0.0

    for coin in list(report.get("coins") or []):
        best_strategy = None
        best_projected = float("-inf")
        best_row: dict[str, Any] = {}

        for strategy_name in list(report.get("strategies") or []):
            row = dict(scenario_results.get(strategy_name, {}).get(coin) or {})
            projected = float(row.get("net_pnl", 0.0) or 0.0)
            if projected > best_projected:
                best_projected = projected
                best_strategy = strategy_name
                best_row = row

        if best_strategy is None:
            continue

        total_projected += best_projected
        assignment[coin] = {
            "strategy": best_strategy,
            "projected_net_pnl": round(best_projected, 4),
            "projected_trades": int(best_row.get("trades", 0) or 0),
            "projected_win_rate": float(best_row.get("win_rate", 0.0) or 0.0),
        }

    return {
        "scenario_name": scenario_name,
        "starting_cash_per_coin": float(scenario_name.removeprefix("per_coin_").replace("_", ".")),
        "projected_total_pnl": round(total_projected, 4),
        "assignment": assignment,
    }


def reconcile_assignment(report: dict[str, Any], scenario_name: str) -> dict[str, Any]:
    plan = build_best_assignment(report, scenario_name)
    per_coin: dict[str, Any] = {}
    canonical_total = 0.0
    feasible_count = 0

    for coin, row in plan["assignment"].items():
        coin_file = ROOT / "reports" / "candle_cache" / f"{coin.replace('-', '_')}_FIVE_MINUTE_30d.json"
        candle_payload = load_json(coin_file)
        replay = canonical_simulate(list(candle_payload["candles"]), str(row["strategy"]), float(plan["starting_cash_per_coin"]))
        canonical_total += float(replay["canonical_net_pnl"])
        if replay["feasible"]:
            feasible_count += 1
        per_coin[coin] = {
            **row,
            "feasible": replay["feasible"],
            "reason": replay["reason"],
            "canonical_net_pnl": replay["canonical_net_pnl"],
            "delta_vs_projected": round(replay["canonical_net_pnl"] - float(row["projected_net_pnl"]), 4),
            "canonical_trades": replay["canonical_trades"],
            "canonical_win_rate": replay["canonical_win_rate"],
        }

    strategy_buckets: dict[str, list[str]] = {}
    for coin, row in per_coin.items():
        strategy_buckets.setdefault(str(row["strategy"]), []).append(coin)

    return {
        "scenario_name": scenario_name,
        "starting_cash_per_coin": plan["starting_cash_per_coin"],
        "coin_count": len(per_coin),
        "feasible_count": feasible_count,
        "projected_total_pnl": plan["projected_total_pnl"],
        "canonical_total_pnl": round(canonical_total, 4),
        "delta_vs_projected": round(canonical_total - float(plan["projected_total_pnl"]), 4),
        "assignment": per_coin,
        "strategy_buckets": strategy_buckets,
    }


def reconcile() -> dict[str, Any]:
    report = load_json(SOURCE_PATH)
    scenarios = [
        reconcile_assignment(report, "per_coin_5_33"),
        reconcile_assignment(report, "per_coin_100"),
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
        "scenarios": scenarios,
    }


def render_md(payload: dict[str, Any]) -> str:
    lines = [
        "# Optimal Portfolio Optimizer Reconciliation",
        "",
        "This report replays the saved best-strategy-per-coin assignment through a single-reference canonical engine on the same 30d candle cache.",
        "",
        "## Canonical Assumptions",
        "",
    ]
    for key, value in payload["canonical_assumptions"].items():
        lines.append(f"- `{key}`: `{value}`")

    lines.extend(["", "## Scenarios", ""])
    for scenario in payload["scenarios"]:
        lines.append(f"### {scenario['scenario_name']}")
        lines.append(f"- feasible coins: `{scenario['feasible_count']}/{scenario['coin_count']}`")
        lines.append(f"- projected total: `{scenario['projected_total_pnl']}`")
        lines.append(f"- canonical total: `{scenario['canonical_total_pnl']}`")
        lines.append(f"- delta vs projected: `{scenario['delta_vs_projected']}`")
        lines.append("")
        lines.append("| Coin | Strategy | Feasible | Projected | Canonical | Delta | Reason |")
        lines.append("|---|---|---|---:|---:|---:|---|")
        for coin, row in scenario["assignment"].items():
            lines.append(
                f"| {coin} | {row['strategy']} | {row['feasible']} | {row['projected_net_pnl']} | "
                f"{row['canonical_net_pnl']} | {row['delta_vs_projected']} | {row['reason'] or '-'} |"
            )
        lines.append("")
        lines.append("Strategy buckets:")
        for strategy_name, coins in sorted(scenario["strategy_buckets"].items()):
            lines.append(f"- `{strategy_name}`: {', '.join(coins)}")
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
