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
import reconcile_optimal_portfolio_optimizer as reconcile_mod


ROOT = Path(__file__).resolve().parent.parent
SOURCE_PATH = ROOT / "reports" / "optimal_portfolio_optimizer.json"
OUTPUT_JSON = ROOT / "reports" / "optimal_portfolio_drift_attribution.json"
OUTPUT_MD = ROOT / "reports" / "optimal_portfolio_drift_attribution.md"


VARIANTS = [
    {
        "variant_id": "optimizer_native",
        "deploy_fraction": optimizer.DEPLOY_FRACTION,
        "min_entry_cash": optimizer.MIN_CASH,
        "session_gate": "on",
    },
    {
        "variant_id": "session_gate_off",
        "deploy_fraction": optimizer.DEPLOY_FRACTION,
        "min_entry_cash": optimizer.MIN_CASH,
        "session_gate": "off",
    },
    {
        "variant_id": "deploy_95",
        "deploy_fraction": 0.95,
        "min_entry_cash": optimizer.MIN_CASH,
        "session_gate": "on",
    },
    {
        "variant_id": "min_cash_10",
        "deploy_fraction": optimizer.DEPLOY_FRACTION,
        "min_entry_cash": 10.0,
        "session_gate": "on",
    },
    {
        "variant_id": "canonical",
        "deploy_fraction": reconcile_mod.CANONICAL_DEPLOY_FRACTION,
        "min_entry_cash": reconcile_mod.CANONICAL_MIN_ENTRY_CASH,
        "session_gate": "off",
    },
]


def load_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def simulate_variant(
    candles: list[dict[str, Any]],
    strategy_name: str,
    starting_cash: float,
    *,
    deploy_fraction: float,
    min_entry_cash: float,
    session_gate: str,
) -> dict[str, Any]:
    if starting_cash < min_entry_cash:
        return {
            "feasible": False,
            "net_pnl": 0.0,
            "trades": 0,
            "win_rate": None,
            "reason": f"allocation ${starting_cash:.2f} below min_entry_cash ${min_entry_cash:.2f}",
        }

    entry_fn = optimizer.ENTRY_FUNCS[strategy_name]
    params = dict(optimizer.STRATEGIES[strategy_name]["params"])
    tp_pct = params.get("tp_pct", 10.0) / 100.0
    sl_pct = params.get("sl_pct", 0.0) / 100.0
    max_hold = params.get("max_hold", 48)

    cash = starting_cash
    position: dict[str, Any] | None = None
    history: list[float] = []
    candle_history: list[dict[str, Any]] = []
    trades = 0
    wins = 0
    losses = 0

    for candle in candles:
        ts = int(candle.get("time", candle.get("start", 0)))
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
                if net > 0:
                    wins += 1
                else:
                    losses += 1
                position = None

        hour = datetime.fromtimestamp(ts, tz=timezone.utc).hour
        session_open = hour not in optimizer.SESSION_DEAD if session_gate == "on" else True
        if position is None and cash >= min_entry_cash and session_open:
            if entry_fn(candle_history, history, candle, params):
                deploy = cash * deploy_fraction
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
        if net > 0:
            wins += 1
        else:
            losses += 1

    return {
        "feasible": True,
        "net_pnl": round(cash - starting_cash, 4),
        "trades": trades,
        "win_rate": round(wins / max(1, trades) * 100, 1) if trades > 0 else 0.0,
        "reason": "",
    }


def build_payload() -> dict[str, Any]:
    report = load_json(SOURCE_PATH)
    scenario = reconcile_mod.build_best_assignment(report, "per_coin_100")
    assignment = scenario["assignment"]
    coins = list(assignment.keys())

    per_variant: list[dict[str, Any]] = []
    for variant in VARIANTS:
        total = 0.0
        feasible_count = 0
        per_coin: dict[str, Any] = {}
        for coin in coins:
            coin_path = ROOT / "reports" / "candle_cache" / f"{coin.replace('-', '_')}_FIVE_MINUTE_30d.json"
            candles = load_json(coin_path)["candles"]
            strategy_name = str(assignment[coin]["strategy"])
            replay = simulate_variant(
                candles,
                strategy_name,
                float(scenario["starting_cash_per_coin"]),
                deploy_fraction=float(variant["deploy_fraction"]),
                min_entry_cash=float(variant["min_entry_cash"]),
                session_gate=str(variant["session_gate"]),
            )
            if replay["feasible"]:
                feasible_count += 1
            total += float(replay["net_pnl"])
            per_coin[coin] = {
                "strategy": strategy_name,
                "net_pnl": replay["net_pnl"],
                "trades": replay["trades"],
                "win_rate": replay["win_rate"],
                "feasible": replay["feasible"],
                "reason": replay["reason"],
            }
        per_variant.append(
            {
                **variant,
                "feasible_count": feasible_count,
                "coin_count": len(coins),
                "total_net_pnl": round(total, 4),
                "delta_vs_optimizer_native": 0.0,  # patched after native is known
                "per_coin": per_coin,
            }
        )

    native_total = next(row["total_net_pnl"] for row in per_variant if row["variant_id"] == "optimizer_native")
    for row in per_variant:
        row["delta_vs_optimizer_native"] = round(float(row["total_net_pnl"]) - float(native_total), 4)

    by_id = {row["variant_id"]: row for row in per_variant}
    session_effect = float(by_id["session_gate_off"]["delta_vs_optimizer_native"])
    deploy_effect = float(by_id["deploy_95"]["delta_vs_optimizer_native"])
    min_cash_effect = float(by_id["min_cash_10"]["delta_vs_optimizer_native"])
    canonical_effect = float(by_id["canonical"]["delta_vs_optimizer_native"])
    interaction_effect = round(canonical_effect - session_effect - deploy_effect - min_cash_effect, 4)

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source_report": str(SOURCE_PATH),
        "scenario_name": "per_coin_100",
        "projected_total_pnl": scenario["projected_total_pnl"],
        "assignment": assignment,
        "variants": per_variant,
        "summary": {
            "component_effects": {
                "session_gate_off": round(session_effect, 4),
                "deploy_95": round(deploy_effect, 4),
                "min_cash_10": round(min_cash_effect, 4),
                "canonical_total_shift": round(canonical_effect, 4),
                "interaction_effect": interaction_effect,
            },
        },
    }


def render_markdown(payload: dict[str, Any]) -> str:
    lines = [
        "# Optimal Portfolio Drift Attribution",
        "",
        "This report keeps the saved best-strategy-per-coin assignment fixed and attributes the remaining simulator drift across a small set of execution-semantic variants.",
        "",
        f"- scenario: `{payload['scenario_name']}`",
        f"- projected total from optimizer report: `{payload['projected_total_pnl']}`",
        "",
        "## Variant Totals",
        "",
        "| Variant | Deploy | Min Cash | Session Gate | Feasible | Total | Delta vs Native |",
        "|---|---:|---:|---|---:|---:|---:|",
    ]

    for row in payload["variants"]:
        lines.append(
            f"| `{row['variant_id']}` | {row['deploy_fraction']} | {row['min_entry_cash']} | "
            f"{row['session_gate']} | {row['feasible_count']}/{row['coin_count']} | "
            f"{row['total_net_pnl']} | {row['delta_vs_optimizer_native']} |"
        )

    lines.extend(
        [
            "",
            "## Attribution Read",
            "",
            f"- session gate effect: `{payload['summary']['component_effects']['session_gate_off']}`",
            f"- deploy fraction effect: `{payload['summary']['component_effects']['deploy_95']}`",
            f"- min cash effect at $100 sleeves: `{payload['summary']['component_effects']['min_cash_10']}`",
            f"- interaction effect: `{payload['summary']['component_effects']['interaction_effect']}`",
            f"- canonical total shift: `{payload['summary']['component_effects']['canonical_total_shift']}`",
            "",
        ]
    )
    return "\n".join(lines)


def main() -> int:
    payload = build_payload()
    OUTPUT_JSON.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    OUTPUT_MD.write_text(render_markdown(payload), encoding="utf-8")
    print(f"Wrote {OUTPUT_JSON}")
    print(f"Wrote {OUTPUT_MD}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
