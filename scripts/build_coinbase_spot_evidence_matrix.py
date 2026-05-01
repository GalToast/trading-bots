#!/usr/bin/env python3
from __future__ import annotations

import json
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent.parent
REPORTS = ROOT / "reports"

MD_PATH = REPORTS / "coinbase_spot_evidence_matrix.md"
JSON_PATH = REPORTS / "coinbase_spot_evidence_matrix.json"

RECON_PATH = REPORTS / "backtest_reconciliation.json"
SWEEP_PARTIAL_PATH = REPORTS / "coinbase_opportunity_sweep_partial.json"
RUNTIME_PATH = REPORTS / "multi_coin_portfolio_state.json"
DEPLOYABILITY_PATH = REPORTS / "coinbase_spot_deployability_board.json"
RAVE_LIVE_STATE_PATH = REPORTS / "rave_rsi_mr_live_v2_state.json"
MOMENTUM_RECON_RESULTS_PATH = REPORTS / "coinbase_momentum_reconciliation_results.json"

BASE_COMBOS = [
    {
        "combo_id": "rave_mom_10",
        "coin": "RAVE-USD",
        "strategy": "mom_10",
        "family": "momentum",
        "recon_key": "Momentum (RAVE)",
        "runtime_coin": "RAVE-USD",
        "runtime_strategy": "momentum",
    },
    {
        "combo_id": "rave_rsi_mr",
        "coin": "RAVE-USD",
        "strategy": "rsi_mr",
        "family": "rsi_mean_reversion",
        "recon_key": "RSI MR (RAVE)",
        "runtime_coin": "RAVE-USD",
        "runtime_strategy": "rsi_mean_reversion",
        "runtime_source": "rave_live",
    },
    {
        "combo_id": "iotx_bb_rev",
        "coin": "IOTX-USD",
        "strategy": "bb_rev",
        "family": "bb_reversion",
        "recon_key": "BB Reversion (IOTX)",
        "runtime_coin": "IOTX-USD",
        "runtime_strategy": "bb_reversion",
        "runtime_source": "portfolio",
    },
    {
        "combo_id": "bal_mom_50",
        "coin": "BAL-USD",
        "strategy": "mom_50",
        "family": "momentum",
        "recon_key": "Momentum (BAL)",
        "runtime_coin": "BAL-USD",
        "runtime_strategy": "momentum",
        "runtime_source": "portfolio",
    },
    {
        "combo_id": "blur_mom_25",
        "coin": "BLUR-USD",
        "strategy": "mom_25",
        "family": "momentum",
        "recon_key": "Momentum (BLUR)",
        "runtime_coin": "BLUR-USD",
        "runtime_strategy": "momentum",
        "runtime_source": "portfolio",
    },
    {
        "combo_id": "iotx_mom_25",
        "coin": "IOTX-USD",
        "strategy": "mom_25",
        "family": "momentum",
        "recon_key": None,
        "runtime_coin": None,
        "runtime_strategy": None,
        "runtime_source": None,
    },
]


def load_json(path: Path) -> Any:
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def to_float(value: Any) -> float:
    try:
        return float(value or 0.0)
    except (TypeError, ValueError):
        return 0.0


def to_int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def load_sweep_map() -> dict[tuple[str, str], dict[str, Any]]:
    payload = load_json(SWEEP_PARTIAL_PATH)
    return {
        (str(row.get("coin") or ""), str(row.get("strategy") or "")): row
        for row in payload.get("profitable_combos") or []
    }


def load_runtime_map() -> dict[str, dict[str, Any]]:
    payload = load_json(RUNTIME_PATH)
    return {
        str(coin): data
        for coin, data in (payload.get("coins") or {}).items()
    }


def load_deployability_map() -> dict[tuple[str, str], dict[str, Any]]:
    payload = load_json(DEPLOYABILITY_PATH)
    return {
        (str(row.get("product_id") or ""), str(row.get("family") or "")): row
        for row in payload.get("candidates") or []
    }


def load_momentum_reconciliation_map() -> dict[tuple[str, str], dict[str, Any]]:
    payload = load_json(MOMENTUM_RECON_RESULTS_PATH)
    return {
        (str(row.get("coin") or ""), str(row.get("strategy") or "")): row
        for row in payload.get("results") or []
    }


def combo_id_for(coin: str, strategy: str) -> str:
    return f"{coin.split('-', 1)[0].lower()}_{strategy}"


def build_combo_list(momentum_recon_map: dict[tuple[str, str], dict[str, Any]]) -> list[dict[str, Any]]:
    combos = [dict(combo) for combo in BASE_COMBOS]
    seen = {str(combo["combo_id"]) for combo in combos}
    for (coin, strategy), row in momentum_recon_map.items():
        if str(row.get("verdict") or "") != "confirmed_positive":
            continue
        combo_id = combo_id_for(coin, strategy)
        if combo_id in seen:
            continue
        combos.append(
            {
                "combo_id": combo_id,
                "coin": coin,
                "strategy": strategy,
                "family": "momentum",
                "recon_key": None,
                "runtime_coin": None,
                "runtime_strategy": None,
                "runtime_source": None,
            }
        )
        seen.add(combo_id)
    return combos


def load_rave_live_runtime() -> dict[str, Any]:
    payload = load_json(RAVE_LIVE_STATE_PATH)
    return payload.get("state") or {}


def governance_verdict(*, recon_net: float | None, runtime_net: float | None, runtime_closes: int, sweep_net: float | None) -> str:
    if recon_net is not None:
        if recon_net > 0.0 and runtime_net is not None and runtime_net > 0.0:
            return "deployable_priority"
        if recon_net > 0.0:
            return "bench_positive_wait_runtime"
        return "reject_or_debug"
    if runtime_net is not None and runtime_closes > 0 and runtime_net > 0.0:
        return "runtime_positive_but_unreconciled"
    if sweep_net is not None and sweep_net > 0.0:
        return "explore_only"
    return "insufficient_evidence"


def verdict_note(verdict: str) -> str:
    notes = {
        "deployable_priority": "positive in reconciliation and runtime",
        "bench_positive_wait_runtime": "positive in reconciliation but runtime proof is absent or weak",
        "reject_or_debug": "negative in reconciliation or contradicted by runtime",
        "runtime_positive_but_unreconciled": "runtime positive, but no reconciliation baseline for this exact combo yet",
        "explore_only": "only partial sweep evidence exists so far",
        "insufficient_evidence": "not enough aligned evidence yet",
    }
    return notes.get(verdict, "")


def build_payload() -> dict[str, Any]:
    recon = load_json(RECON_PATH)
    recon_rows = recon.get("individual_full_cash") or {}
    sweep_map = load_sweep_map()
    runtime_map = load_runtime_map()
    deploy_map = load_deployability_map()
    momentum_recon_map = load_momentum_reconciliation_map()
    rave_live_runtime = load_rave_live_runtime()
    combos = build_combo_list(momentum_recon_map)

    rows: list[dict[str, Any]] = []
    for combo in combos:
        recon_row = recon_rows.get(combo["recon_key"]) if combo["recon_key"] else None
        momentum_recon_row = momentum_recon_map.get((combo["coin"], combo["strategy"]))
        sweep_row = sweep_map.get((combo["coin"], combo["strategy"]))
        runtime_row = None
        if combo.get("runtime_source") == "rave_live":
            runtime_row = rave_live_runtime
        elif combo["runtime_coin"]:
            runtime_row = runtime_map.get(combo["runtime_coin"])
        deploy_row = deploy_map.get((combo["coin"], combo["family"]))

        runtime_matches = (
            runtime_row is not None
            and combo["runtime_strategy"] is not None
            and (
                combo.get("runtime_source") == "rave_live"
                or str(runtime_row.get("strategy") or "") == combo["runtime_strategy"]
            )
        )

        recon_net = None
        recon_closes = None
        if recon_row:
            recon_net = to_float(recon_row.get("net_pnl"))
            recon_closes = to_int(recon_row.get("closes"))
        elif momentum_recon_row and momentum_recon_row.get("reconciliation_30d_net_usd") is not None:
            recon_net = to_float(momentum_recon_row.get("reconciliation_30d_net_usd"))
            recon_closes = to_int(momentum_recon_row.get("reconciliation_30d_closes"))

        sweep_net = to_float(sweep_row.get("net_pnl")) if sweep_row else None
        if sweep_row is None and momentum_recon_row and momentum_recon_row.get("library_sweep_partial_14d_net_usd") is not None:
            sweep_net = to_float(momentum_recon_row.get("library_sweep_partial_14d_net_usd"))
        runtime_net = to_float(runtime_row.get("realized_net")) if runtime_matches else None
        runtime_closes = to_int(runtime_row.get("closes")) if runtime_matches else 0

        verdict = governance_verdict(
            recon_net=recon_net,
            runtime_net=runtime_net,
            runtime_closes=runtime_closes,
            sweep_net=sweep_net,
        )
        rows.append(
            {
                "combo_id": combo["combo_id"],
                "coin": combo["coin"],
                "strategy": combo["strategy"],
                "family": combo["family"],
                "reconciliation_net_30d_usd": round(recon_net, 4) if recon_net is not None else None,
                "reconciliation_closes_30d": recon_closes,
                "library_sweep_partial_14d_net_usd": round(sweep_net, 4) if sweep_net is not None else None,
                "library_sweep_partial_14d_closes": (
                    to_int(sweep_row.get("closes"))
                    if sweep_row
                    else to_int(momentum_recon_row.get("library_sweep_partial_14d_closes"))
                    if momentum_recon_row
                    else None
                ),
                "runtime_realized_usd": round(runtime_net, 4) if runtime_net is not None else None,
                "runtime_closes": runtime_closes if runtime_matches else None,
                "deployability_action": str(deploy_row.get("action") or "") if deploy_row else "",
                "verdict": verdict,
                "note": verdict_note(verdict),
            }
        )

    rows.sort(
        key=lambda row: (
            [
                "deployable_priority",
                "bench_positive_wait_runtime",
                "runtime_positive_but_unreconciled",
                "explore_only",
                "insufficient_evidence",
                "reject_or_debug",
            ].index(row["verdict"]),
            -(row["reconciliation_net_30d_usd"] or -9999.0),
            -(row["library_sweep_partial_14d_net_usd"] or -9999.0),
            row["coin"],
        )
    )

    by_id = {str(row["combo_id"]): row for row in rows}
    leadership_read = [
        (
            "RAVE momentum remains the strongest benchmarked combo, and RAVE RSI MR is now back in live-positive territory."
            if by_id.get("rave_rsi_mr", {}).get("verdict") == "deployable_priority"
            else "RAVE momentum is still the cleanest key combo, while RAVE RSI MR remains bench-positive but weaker operationally."
        ),
        "IOTX BB reversion remains the canonical reject/debug example: negative in reconciliation and contradicted by runtime.",
    ]
    confirmed_momentum = [
        row for row in rows
        if row["family"] == "momentum"
        and row["combo_id"] not in {"rave_mom_10", "bal_mom_50", "blur_mom_25", "iotx_mom_25"}
        and (row["reconciliation_net_30d_usd"] or 0.0) > 0.0
    ]
    if confirmed_momentum:
        top_confirmed = ", ".join(
            f"{row['coin']} {row['strategy']} +${row['reconciliation_net_30d_usd']:.2f}"
            for row in confirmed_momentum[:3]
        )
        leadership_read.append(f"Fresh momentum confirmations widened the queue: {top_confirmed}.")
    thin_confirmed = [
        row for row in confirmed_momentum
        if 0.0 < float(row["reconciliation_net_30d_usd"] or 0.0) < 2.0
    ]
    if thin_confirmed:
        leadership_read.append(
            "Thin positives still need restraint: "
            + ", ".join(
                f"{row['coin']} {row['strategy']} +${row['reconciliation_net_30d_usd']:.2f}"
                for row in thin_confirmed
            )
            + "."
        )
    if by_id.get("iotx_mom_25", {}).get("verdict") == "explore_only":
        leadership_read.append(
            "IOTX momentum is still exploration-only until its own 30d reconciliation or runtime proof lands."
        )

    return {
        "generated_at": load_json(MOMENTUM_RECON_RESULTS_PATH).get("generated_at")
        or load_json(SWEEP_PARTIAL_PATH).get("run_at")
        or "",
        "leadership_read": leadership_read,
        "rows": rows,
    }


def fmt(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, float):
        return f"{value:.4f}"
    return str(value)


def write_reports(payload: dict[str, Any], *, md_path: Path = MD_PATH, json_path: Path = JSON_PATH) -> None:
    json_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    lines = [
        "# Coinbase Spot Evidence Matrix",
        "",
        "## Leadership Read",
        "",
    ]
    for line in payload["leadership_read"]:
        lines.append(f"- {line}")
    lines.extend(
        [
            "",
            "## Key Combos",
            "",
            "| Coin | Strategy | Recon 30d $ | Recon Closes | Sweep Partial 14d $ | Sweep Closes | Runtime $ | Runtime Closes | Deployability | Verdict | Note |",
            "| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | --- | --- | --- |",
        ]
    )
    for row in payload["rows"]:
        lines.append(
            "| {coin} | {strategy} | {recon} | {recon_closes} | {sweep} | {sweep_closes} | {runtime} | {runtime_closes} | {deploy} | {verdict} | {note} |".format(
                coin=row["coin"],
                strategy=row["strategy"],
                recon=fmt(row["reconciliation_net_30d_usd"]),
                recon_closes=fmt(row["reconciliation_closes_30d"]),
                sweep=fmt(row["library_sweep_partial_14d_net_usd"]),
                sweep_closes=fmt(row["library_sweep_partial_14d_closes"]),
                runtime=fmt(row["runtime_realized_usd"]),
                runtime_closes=fmt(row["runtime_closes"]),
                deploy=row["deployability_action"],
                verdict=row["verdict"],
                note=row["note"],
            )
        )
    md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    payload = build_payload()
    write_reports(payload)
    print(f"wrote {MD_PATH}")
    print(f"wrote {JSON_PATH}")


if __name__ == "__main__":
    main()
