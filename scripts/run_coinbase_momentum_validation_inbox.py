#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any


SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from coinbase_advanced_client import CoinbaseAdvancedClient
import build_coinbase_momentum_validation_inbox as inbox_builder
import run_coinbase_momentum_reconciliation_queue as recon_runner
import strategy_library as strategy_lib


ROOT = Path(__file__).resolve().parent.parent
REPORTS = ROOT / "reports"

INBOX_PATH = REPORTS / "coinbase_momentum_validation_inbox.json"
REGISTRY_PATH = REPORTS / "master_deployment_registry.md"
JSON_PATH = REPORTS / "coinbase_momentum_validation_results.json"
MD_PATH = REPORTS / "coinbase_momentum_validation_results.md"

FEE_RATE = 0.004
STARTING_CASH = 48.0
DEFAULT_MAX_HOLD = 48


def load_json(path: Path) -> Any:
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def load_text(path: Path) -> str:
    if not path.exists():
        return ""
    return path.read_text(encoding="utf-8")


def save_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


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


def registry_map() -> dict[tuple[str, str], dict[str, Any]]:
    sections = inbox_builder.parse_registry_sections(load_text(REGISTRY_PATH))
    return {
        (str(section.get("coin") or ""), str(section.get("strategy") or "")): section
        for section in sections
    }


def registry_rows_by_coin() -> dict[str, dict[str, Any]]:
    rows: dict[str, dict[str, Any]] = {}
    for section in inbox_builder.parse_registry_sections(load_text(REGISTRY_PATH)):
        coin = str(section.get("coin") or "")
        strategy = str(section.get("strategy") or "")
        tier = str(section.get("tier") or "")
        if not coin or "Momentum" not in strategy:
            continue
        if coin in rows:
            continue
        if tier.startswith("A-TIER"):
            action = "validate_30d_next"
            reason = "strong 7d claim, but not router-ready until 30d reconciliation exists"
        elif tier.startswith("B-TIER"):
            action = "optimize_then_validate"
            reason = "positive 7d claim exists, but parameter sweep and 30d confirmation are still missing"
        else:
            action = "archive_or_ignore"
            reason = "registry entry is not a current launch candidate"
        rows[coin] = {
            "coin": coin,
            "strategy": strategy,
            "tier": tier,
            "registry_net_pnl": round(to_float(section.get("net_pnl")), 4),
            "param_hit_rate": section.get("param_hit_rate"),
            "action": action,
            "reason": reason,
        }
    return rows


def parse_table_param(body: str, label: str) -> float | None:
    match = re.search(rf"\|\s*{re.escape(label)}\s*\|\s*(?P<value>[\d.]+)\s*\|", body)
    if not match:
        return None
    return float(match.group("value"))


def parse_registry_params(section: dict[str, Any]) -> dict[str, Any]:
    body = str(section.get("body") or "")
    strategy = str(section.get("strategy") or "")

    lookback = parse_table_param(body, "Lookback")
    tp_pct = parse_table_param(body, "TP%")
    sl_pct = parse_table_param(body, "SL%")
    max_hold = parse_table_param(body, "Max Hold")

    if lookback is None or tp_pct is None or sl_pct is None:
        inline = re.search(
            r"Momentum \(\s*lb=(?P<lb>[\d.]+),\s*tp=(?P<tp>[\d.]+),\s*sl=(?P<sl>[\d.]+)\s*\)",
            strategy,
        )
        if inline:
            lookback = float(inline.group("lb"))
            tp_pct = float(inline.group("tp"))
            sl_pct = float(inline.group("sl"))

    if lookback is None or tp_pct is None or sl_pct is None:
        raise ValueError(f"unable to parse momentum params for {section.get('coin')} / {strategy}")

    return {
        "lookback": int(lookback),
        "tp_pct": float(tp_pct),
        "sl_pct": float(sl_pct),
        "max_hold": int(max_hold) if max_hold is not None else DEFAULT_MAX_HOLD,
    }


def load_inbox_rows() -> list[dict[str, Any]]:
    payload = load_json(INBOX_PATH)
    return list(payload.get("validation_inbox") or [])


def select_rows(
    *,
    action: str,
    limit: int,
    include_coins: list[str],
    exclude_coins: list[str],
) -> list[dict[str, Any]]:
    include_set = set(include_coins)
    exclude_set = set(exclude_coins)
    rows = []
    seen: set[str] = set()
    for row in load_inbox_rows():
        coin = str(row.get("coin") or "")
        if action != "all" and str(row.get("action") or "") != action:
            continue
        if include_set and coin not in include_set:
            continue
        if coin in exclude_set:
            continue
        rows.append(row)
        seen.add(coin)

    if include_set:
        for coin, row in registry_rows_by_coin().items():
            if coin not in include_set or coin in exclude_set or coin in seen:
                continue
            if action != "all" and str(row.get("action") or "") != action:
                continue
            rows.append(row)
            seen.add(coin)

    return rows[:limit]


def run_momentum_validation(candles: list[dict[str, str]], params: dict[str, Any]) -> dict[str, Any]:
    result = strategy_lib.momentum(
        candles,
        fee_rate=FEE_RATE,
        starting_cash=STARTING_CASH,
        entry_slip=0.0,
        exit_slip=0.0,
        **params,
    )
    return {
        "net_pnl": round(result["net_pnl"], 4),
        "return_pct": round(result["return_pct"], 4),
        "trades": int(result["trades"]),
        "win_rate": round(result["win_rate"], 1),
        "max_drawdown": round(result["max_drawdown"], 1),
        "signals": int(result["signals"]),
        "total_fees": round(result["total_fees"], 4),
        "engine": "strategy_library_registry_validation",
    }


def classify_verdict(net_pnl: float) -> str:
    if net_pnl > 0.0:
        return "confirmed_positive"
    if net_pnl < 0.0:
        return "rejected"
    return "flat"


def build_results(selected_rows: list[dict[str, Any]], *, fetch_missing: bool = False) -> dict[str, Any]:
    reg_map = registry_map()
    snapshot_map = recon_runner.load_snapshot_map()
    client: CoinbaseAdvancedClient | None = CoinbaseAdvancedClient() if fetch_missing else None
    results: list[dict[str, Any]] = []

    for row in selected_rows:
        coin = str(row.get("coin") or "")
        registry_strategy = str(row.get("strategy") or "")
        section = reg_map.get((coin, registry_strategy))
        if section is None:
            results.append(
                {
                    "coin": coin,
                    "registry_strategy": registry_strategy,
                    "tier": str(row.get("tier") or ""),
                    "action": str(row.get("action") or ""),
                    "verdict": "missing_registry_entry",
                    "reason": "registry section missing for inbox row",
                }
            )
            continue

        params = parse_registry_params(section)
        candles = snapshot_map.get(coin) or recon_runner.load_cache_candles(coin)
        source = "snapshot" if snapshot_map.get(coin) else "cache"
        if not candles and fetch_missing and client is not None:
            candles = recon_runner.fetch_candles(client, coin)
            source = "fetched"
        if not candles:
            results.append(
                {
                    "coin": coin,
                    "registry_strategy": registry_strategy,
                    "tier": str(row.get("tier") or ""),
                    "action": str(row.get("action") or ""),
                    "verdict": "missing_candles",
                    "reason": "no snapshot/cache candles available",
                }
            )
            continue

        recon = run_momentum_validation(candles, params)
        results.append(
            {
                "coin": coin,
                "registry_strategy": registry_strategy,
                "tier": str(row.get("tier") or ""),
                "action": str(row.get("action") or ""),
                "source": source,
                "lookback": params["lookback"],
                "tp_pct": params["tp_pct"],
                "sl_pct": params["sl_pct"],
                "max_hold": params["max_hold"],
                "registry_net_pnl_7d_usd": round(to_float(row.get("registry_net_pnl")), 4),
                "param_hit_rate": row.get("param_hit_rate"),
                "reconciliation_30d_net_usd": recon["net_pnl"],
                "reconciliation_30d_closes": recon["trades"],
                "reconciliation_30d_win_rate": recon["win_rate"],
                "reconciliation_30d_max_dd": recon["max_drawdown"],
                "verdict": classify_verdict(recon["net_pnl"]),
                "reason": str(row.get("reason") or ""),
            }
        )

    return {
        "generated_at": recon_runner.utc_now_iso(),
        "results": results,
    }


def merge_results(payload: dict[str, Any], *, existing_payload: dict[str, Any] | None = None) -> dict[str, Any]:
    existing_payload = existing_payload or {}
    merged: dict[str, dict[str, Any]] = {
        str(row.get("coin") or ""): row
        for row in existing_payload.get("results") or []
        if str(row.get("coin") or "")
    }
    for row in payload.get("results") or []:
        coin = str(row.get("coin") or "")
        if not coin:
            continue
        merged[coin] = row
    merged_rows = list(merged.values())
    merged_rows.sort(
        key=lambda row: (
            str(row.get("action") or ""),
            -(to_float(row.get("reconciliation_30d_net_usd"))),
            str(row.get("coin") or ""),
        )
    )
    return {
        "generated_at": payload.get("generated_at") or recon_runner.utc_now_iso(),
        "results": merged_rows,
    }


def write_reports(payload: dict[str, Any]) -> None:
    merged_payload = merge_results(payload, existing_payload=load_json(JSON_PATH))
    save_json(JSON_PATH, merged_payload)

    lines = [
        "# Coinbase Momentum Validation Results",
        "",
        "| Coin | Registry Strategy | Tier | Action | Source | Params | 7d Registry $ | Hit Rate | Recon 30d $ | Closes | WR | DD | Verdict | Reason |",
        "| --- | --- | --- | --- | --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | --- | --- |",
    ]
    for row in merged_payload.get("results") or []:
        params = ""
        if row.get("lookback") is not None:
            params = "lb={lookback},tp={tp_pct},sl={sl_pct},hold={max_hold}".format(**row)
        hit_rate = "" if row.get("param_hit_rate") is None else f"{float(row['param_hit_rate']):.1f}%"
        recon = "" if row.get("reconciliation_30d_net_usd") is None else f"{float(row['reconciliation_30d_net_usd']):.4f}"
        closes = "" if row.get("reconciliation_30d_closes") is None else row["reconciliation_30d_closes"]
        wr = "" if row.get("reconciliation_30d_win_rate") is None else f"{float(row['reconciliation_30d_win_rate']):.1f}"
        dd = "" if row.get("reconciliation_30d_max_dd") is None else f"{float(row['reconciliation_30d_max_dd']):.1f}"
        registry_pnl = "" if row.get("registry_net_pnl_7d_usd") is None else f"{float(row['registry_net_pnl_7d_usd']):.4f}"
        lines.append(
            "| {coin} | {registry_strategy} | {tier} | {action} | {source} | {params} | {registry_pnl} | {hit_rate} | {recon} | {closes} | {wr} | {dd} | {verdict} | {reason} |".format(
                coin=row.get("coin", ""),
                registry_strategy=row.get("registry_strategy", ""),
                tier=row.get("tier", ""),
                action=row.get("action", ""),
                source=row.get("source", ""),
                params=params,
                registry_pnl=registry_pnl,
                hit_rate=hit_rate,
                recon=recon,
                closes=closes,
                wr=wr,
                dd=dd,
                verdict=row.get("verdict", ""),
                reason=row.get("reason", ""),
            )
        )
    MD_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--action",
        choices=["validate_30d_next", "optimize_then_validate", "archive_or_ignore", "all"],
        default="validate_30d_next",
    )
    parser.add_argument("--limit", type=int, default=3)
    parser.add_argument("--include-coins", nargs="*", default=[])
    parser.add_argument("--exclude-coins", nargs="*", default=[])
    parser.add_argument("--fetch-missing", action="store_true")
    args = parser.parse_args()

    selected = select_rows(
        action=args.action,
        limit=args.limit,
        include_coins=args.include_coins,
        exclude_coins=args.exclude_coins,
    )
    payload = build_results(selected, fetch_missing=args.fetch_missing)
    write_reports(payload)
    print(f"wrote {MD_PATH}")
    print(f"wrote {JSON_PATH}")


if __name__ == "__main__":
    main()
