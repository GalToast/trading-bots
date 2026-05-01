#!/usr/bin/env python3
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent.parent
REPORTS = ROOT / "reports"

MD_PATH = REPORTS / "coinbase_momentum_registry_alignment_audit.md"
JSON_PATH = REPORTS / "coinbase_momentum_registry_alignment_audit.json"

REGISTRY_PATH = REPORTS / "master_deployment_registry.md"
VALIDATION_RESULTS_PATH = REPORTS / "coinbase_momentum_validation_results.json"
SWEEP_PATHS = [
    REPORTS / "reconciliation_troll_sup_mdt.txt",
    REPORTS / "reconciliation_tru_ghst_red_nom.txt",
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


def parse_registry_params_for_coin(coin: str) -> dict[str, Any]:
    import build_coinbase_momentum_validation_inbox as inbox_builder
    import run_coinbase_momentum_validation_inbox as validation_runner

    registry_text = inbox_builder.load_text(REGISTRY_PATH)
    for section in inbox_builder.parse_registry_sections(registry_text):
        if str(section.get("coin") or "") != coin:
            continue
        strategy = str(section.get("strategy") or "")
        if "Momentum" not in strategy:
            continue
        try:
            params = validation_runner.parse_registry_params(section)
        except ValueError:
            continue
        return {
            "coin": coin,
            "registry_strategy": strategy,
            "registry_lookback": params["lookback"],
            "registry_tp_pct": float(params["tp_pct"]),
            "registry_sl_pct": float(params["sl_pct"]),
            "registry_max_hold": int(params["max_hold"]),
            "registry_7d_net_pnl": round(to_float(section.get("net_pnl")), 4),
        }
    return {"coin": coin}


def load_optimized_map() -> dict[str, dict[str, Any]]:
    import build_coinbase_momentum_claim_audit as claim_audit

    rows: dict[str, dict[str, Any]] = {}
    for path in SWEEP_PATHS:
        rows.update(claim_audit.parse_optimized_sweep(path.read_text(encoding="utf-8")) if path.exists() else {})
    return rows


def param_alignment(row: dict[str, Any]) -> str:
    if row.get("registry_lookback") is None or row.get("optimized_best_lookback") is None:
        return "unknown"
    if (
        int(row["registry_lookback"]) == int(row["optimized_best_lookback"])
        and float(row["registry_tp_pct"]) == float(row["optimized_best_tp_pct"])
        and float(row["registry_sl_pct"]) == float(row["optimized_best_sl_pct"])
    ):
        return "exact_match"
    return "shifted"


def audit_verdict(*, claimed_verdict: str, optimized_net: float) -> str:
    if claimed_verdict == "confirmed_positive" and optimized_net > 0.0:
        return "claimed_and_optimized_positive"
    if claimed_verdict == "rejected" and optimized_net > 0.0:
        return "optimized_only"
    if not claimed_verdict and optimized_net > 0.0:
        return "optimized_positive_claim_unverified"
    if claimed_verdict == "confirmed_positive":
        return "claimed_only"
    return "insufficient_alignment"


def format_coin_list(coins: list[str]) -> str:
    labels = [coin.replace("-USD", "") for coin in coins]
    if not labels:
        return ""
    if len(labels) == 1:
        return labels[0]
    if len(labels) == 2:
        return f"{labels[0]} and {labels[1]}"
    return f"{', '.join(labels[:-1])}, and {labels[-1]}"


def verb(coins: list[str], singular: str, plural: str) -> str:
    return singular if len(coins) == 1 else plural


def build_leadership_read(rows: list[dict[str, Any]]) -> list[str]:
    claimed_and_optimized = [row["coin"] for row in rows if row["audit_verdict"] == "claimed_and_optimized_positive"]
    optimized_unverified = [row["coin"] for row in rows if row["audit_verdict"] == "optimized_positive_claim_unverified"]
    optimized_only = [row["coin"] for row in rows if row["audit_verdict"] == "optimized_only"]
    claimed_only_rows = [row["coin"] for row in rows if row["audit_verdict"] == "claimed_only"]

    lines: list[str] = []
    if claimed_and_optimized:
        lines.append(
            f"{format_coin_list(claimed_and_optimized)} {verb(claimed_and_optimized, 'is', 'are')} now positive both at the claimed params and on the local optimized sweep."
        )
    if optimized_unverified:
        lines.append(
            f"{format_coin_list(optimized_unverified)} {verb(optimized_unverified, 'is', 'are')} still optimized-surface winners without a claimed-parameter confirmation in the structured runner."
        )
    if optimized_only:
        lines.append(
            f"{format_coin_list(optimized_only)} {verb(optimized_only, 'remains', 'remain')} optimization-only: the claimed params fail 30d even though a local sweep finds a positive variant."
        )
    if claimed_only_rows:
        lines.append(
            f"{format_coin_list(claimed_only_rows)} {verb(claimed_only_rows, 'validates', 'validate')} at the claimed params, but {verb(claimed_only_rows, 'its', 'their')} optimized surfaces still do not reinforce the same edge."
        )
    lines.append(
        "Keep the board vocabulary strict: claimed-param confirmation and optimized-surface positivity are separate evidence classes until both are present."
    )
    return lines


def build_payload(*, now: datetime | None = None) -> dict[str, Any]:
    now = now or datetime.now(timezone.utc)
    validation_rows = load_json(VALIDATION_RESULTS_PATH).get("results") or []
    validation_map = {str(row.get("coin") or ""): row for row in validation_rows}
    optimized_map = load_optimized_map()

    coins = ["GHST-USD", "TRU-USD", "RED-USD", "NOM-USD", "SUP-USD", "MDT-USD", "TROLL-USD"]
    rows: list[dict[str, Any]] = []
    for coin in coins:
        registry = parse_registry_params_for_coin(coin)
        validation = validation_map.get(coin) or {}
        optimized = optimized_map.get(coin) or {}

        row = {
            "coin": coin,
            "registry_strategy": str(registry.get("registry_strategy") or ""),
            "registry_lookback": registry.get("registry_lookback"),
            "registry_tp_pct": registry.get("registry_tp_pct"),
            "registry_sl_pct": registry.get("registry_sl_pct"),
            "registry_7d_net_pnl": round(to_float(registry.get("registry_7d_net_pnl")), 4),
            "claimed_params_30d_net_usd": round(to_float(validation.get("reconciliation_30d_net_usd")), 4),
            "claimed_params_verdict": str(validation.get("verdict") or ""),
            "optimized_best_net_30d_usd": round(to_float(optimized.get("best_net_pnl")), 4),
            "optimized_best_lookback": optimized.get("best_lookback"),
            "optimized_best_tp_pct": optimized.get("best_tp_pct"),
            "optimized_best_sl_pct": optimized.get("best_sl_pct"),
            "optimized_hit_rate": optimized.get("hit_rate"),
        }
        row["param_alignment"] = param_alignment(row)
        row["audit_verdict"] = audit_verdict(
            claimed_verdict=row["claimed_params_verdict"],
            optimized_net=to_float(row["optimized_best_net_30d_usd"]),
        )
        rows.append(row)

    verdict_priority = {
        "claimed_and_optimized_positive": 0,
        "optimized_positive_claim_unverified": 1,
        "optimized_only": 2,
        "claimed_only": 3,
        "insufficient_alignment": 4,
    }
    rows.sort(key=lambda row: (verdict_priority[row["audit_verdict"]], -to_float(row["optimized_best_net_30d_usd"]), row["coin"]))

    return {
        "generated_at": now.isoformat(),
        "leadership_read": build_leadership_read(rows),
        "rows": rows,
    }


def write_reports(payload: dict[str, Any], *, md_path: Path = MD_PATH, json_path: Path = JSON_PATH) -> None:
    json_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    lines = [
        "# Coinbase Momentum Registry Alignment Audit",
        "",
        "## Leadership Read",
        "",
    ]
    for line in payload["leadership_read"]:
        lines.append(f"- {line}")
    lines.extend(
        [
            "",
            "## Audit Rows",
            "",
            "| Coin | Registry Params | 7d Registry $ | Claimed Params 30d $ | Claimed Verdict | Optimized Best 30d $ | Optimized Best | Hit Rate | Alignment | Audit Verdict |",
            "| --- | --- | ---: | ---: | --- | ---: | --- | ---: | --- | --- |",
        ]
    )
    for row in payload["rows"]:
        registry_params = ""
        if row["registry_lookback"] is not None:
            registry_params = "lb={registry_lookback},tp={registry_tp_pct},sl={registry_sl_pct}".format(**row)
        best_params = ""
        if row["optimized_best_lookback"] is not None:
            best_params = "lb={optimized_best_lookback},tp={optimized_best_tp_pct},sl={optimized_best_sl_pct}".format(**row)
        hit_rate = "" if row["optimized_hit_rate"] is None else f"{float(row['optimized_hit_rate']):.1f}%"
        lines.append(
            "| {coin} | {registry_params} | {registry_7d_net_pnl:.4f} | {claimed_params_30d_net_usd:.4f} | {claimed_params_verdict} | {optimized_best_net_30d_usd:.4f} | {best_params} | {hit_rate} | {param_alignment} | {audit_verdict} |".format(
                coin=row["coin"],
                registry_params=registry_params,
                registry_7d_net_pnl=float(row["registry_7d_net_pnl"]),
                claimed_params_30d_net_usd=float(row["claimed_params_30d_net_usd"]),
                claimed_params_verdict=row["claimed_params_verdict"],
                optimized_best_net_30d_usd=float(row["optimized_best_net_30d_usd"]),
                best_params=best_params,
                hit_rate=hit_rate,
                param_alignment=row["param_alignment"],
                audit_verdict=row["audit_verdict"],
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
