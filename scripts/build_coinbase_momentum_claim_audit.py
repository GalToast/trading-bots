#!/usr/bin/env python3
from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent.parent
REPORTS = ROOT / "reports"

MD_PATH = REPORTS / "coinbase_momentum_claim_audit.md"
JSON_PATH = REPORTS / "coinbase_momentum_claim_audit.json"

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


def load_text(path: Path) -> str:
    if not path.exists():
        return ""
    return path.read_text(encoding="utf-8")


def to_float(value: Any) -> float:
    try:
        return float(value or 0.0)
    except (TypeError, ValueError):
        return 0.0


def parse_optimized_sweep(text: str) -> dict[str, dict[str, Any]]:
    rows: dict[str, dict[str, Any]] = {}
    current_coin: str | None = None
    for raw_line in text.splitlines():
        line = raw_line.strip()
        coin_match = re.match(r"^(?P<coin>[A-Z0-9-]+): \d+ candles$", line)
        if coin_match:
            current_coin = coin_match.group("coin")
            rows[current_coin] = {"coin": current_coin}
            continue
        if current_coin is None:
            continue
        best_match = re.match(
            r"^BEST: lb=(?P<lb>\d+) TP=(?P<tp>\d+) SL=(?P<sl>\d+): Net=\$(?P<sign>[+-])(?P<net>[\d.]+) WR=(?P<wr>[\d.]+)% T=(?P<trades>\d+) DD=(?P<dd>[\d.]+)%$",
            line,
        )
        if best_match:
            net = float(best_match.group("net"))
            if best_match.group("sign") == "-":
                net *= -1.0
            rows[current_coin].update(
                {
                    "best_lookback": int(best_match.group("lb")),
                    "best_tp_pct": float(best_match.group("tp")),
                    "best_sl_pct": float(best_match.group("sl")),
                    "best_net_pnl": net,
                    "best_win_rate": float(best_match.group("wr")),
                    "best_trades": int(best_match.group("trades")),
                    "best_max_dd": float(best_match.group("dd")),
                }
            )
            continue
        hit_match = re.match(r"^Hit rate: (?P<hit>[\d.]+)% \((?P<wins>\d+)/(?P<total>\d+) combos profitable\)$", line)
        if hit_match:
            rows[current_coin].update(
                {
                    "hit_rate": float(hit_match.group("hit")),
                    "profitable_combos": int(hit_match.group("wins")),
                    "tested_combos": int(hit_match.group("total")),
                }
            )
    return rows


def load_optimized_map() -> dict[str, dict[str, Any]]:
    rows: dict[str, dict[str, Any]] = {}
    for path in SWEEP_PATHS:
        if not path.exists():
            continue
        rows.update(parse_optimized_sweep(load_text(path)))
    return rows


def audit_class(*, registry_net: float, optimized_net: float, hit_rate: float) -> tuple[str, str]:
    if registry_net > 0.0 and optimized_net > 0.0:
        return (
            "robust_confirmed",
            "positive both at the originally claimed params and after optimization",
        )
    if registry_net <= 0.0 and optimized_net > 0.0:
        return (
            "optimize_only",
            "negative at the claimed params; only becomes positive after re-optimization",
        )
    if registry_net > 0.0 and optimized_net <= 0.0:
        return (
            "claimed_only",
            "claimed params validate, but the local optimization surface does not reinforce the edge",
        )
    return (
        "rejected",
        "negative or flat at both the claimed params and the optimized surface",
    )


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
    robust = [row["coin"] for row in rows if row["audit_verdict"] == "robust_confirmed"]
    claimed_only = [row["coin"] for row in rows if row["audit_verdict"] == "claimed_only"]
    optimize_only = [row["coin"] for row in rows if row["audit_verdict"] == "optimize_only"]

    lines: list[str] = []
    if robust:
        lines.append(
            f"{format_coin_list(robust)} {verb(robust, 'validates', 'validate')} at the claimed params and {verb(robust, 'stays', 'stay')} positive on the local optimized sweep."
        )
    if optimize_only:
        lines.append(
            f"{format_coin_list(optimize_only)} {verb(optimize_only, 'is', 'are')} still the warning case: the claimed params fail 30d, so the edge is optimization-only."
        )
    if claimed_only:
        lines.append(
            f"{format_coin_list(claimed_only)} {verb(claimed_only, 'validates', 'validate')} at the claimed params, but this audit still lacks reinforcing optimized-surface evidence for {verb(claimed_only, 'it', 'them')}."
        )
    lines.append(
        "The board should stop using optimized claims and claimed-parameter confirmations as if they were the same evidence class."
    )
    return lines


def build_payload(*, now: datetime | None = None) -> dict[str, Any]:
    now = now or datetime.now(timezone.utc)
    validation_rows = load_json(VALIDATION_RESULTS_PATH).get("results") or []
    validation_map = {str(row.get("coin") or ""): row for row in validation_rows}
    optimized_map = load_optimized_map()

    rows: list[dict[str, Any]] = []
    for coin in sorted(set(validation_map) | set(optimized_map)):
        validation_row = validation_map.get(coin) or {}
        optimized_row = optimized_map.get(coin) or {}

        registry_net = to_float(validation_row.get("reconciliation_30d_net_usd"))
        optimized_net = to_float(optimized_row.get("best_net_pnl"))
        hit_rate = to_float(optimized_row.get("hit_rate"))
        audit_verdict, audit_note = audit_class(
            registry_net=registry_net,
            optimized_net=optimized_net,
            hit_rate=hit_rate,
        )

        rows.append(
            {
                "coin": coin,
                "registry_strategy": str(validation_row.get("registry_strategy") or ""),
                "registry_params_net_30d_usd": round(registry_net, 4),
                "registry_params_verdict": str(validation_row.get("verdict") or ""),
                "optimized_best_net_30d_usd": round(optimized_net, 4),
                "optimized_best_lookback": optimized_row.get("best_lookback"),
                "optimized_best_tp_pct": optimized_row.get("best_tp_pct"),
                "optimized_best_sl_pct": optimized_row.get("best_sl_pct"),
                "optimized_hit_rate": optimized_row.get("hit_rate"),
                "audit_verdict": audit_verdict,
                "audit_note": audit_note,
            }
        )

    verdict_priority = {
        "robust_confirmed": 0,
        "optimize_only": 1,
        "claimed_only": 2,
        "rejected": 3,
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
        "# Coinbase Momentum Claim Audit",
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
            "| Coin | Claimed Params 30d $ | Claimed Verdict | Optimized Best 30d $ | Best Params | Hit Rate | Audit Verdict | Note |",
            "| --- | ---: | --- | ---: | --- | ---: | --- | --- |",
        ]
    )
    for row in payload["rows"]:
        best_params = ""
        if row["optimized_best_lookback"] is not None:
            best_params = "lb={optimized_best_lookback},tp={optimized_best_tp_pct},sl={optimized_best_sl_pct}".format(**row)
        hit_rate = "" if row["optimized_hit_rate"] is None else f"{float(row['optimized_hit_rate']):.1f}%"
        lines.append(
            "| {coin} | {registry_params_net_30d_usd:.4f} | {registry_params_verdict} | {optimized_best_net_30d_usd:.4f} | {best_params} | {hit_rate} | {audit_verdict} | {audit_note} |".format(
                coin=row["coin"],
                registry_params_net_30d_usd=float(row["registry_params_net_30d_usd"]),
                registry_params_verdict=row["registry_params_verdict"],
                optimized_best_net_30d_usd=float(row["optimized_best_net_30d_usd"]),
                best_params=best_params,
                hit_rate=hit_rate,
                audit_verdict=row["audit_verdict"],
                audit_note=row["audit_note"],
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
