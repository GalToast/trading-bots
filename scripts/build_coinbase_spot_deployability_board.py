#!/usr/bin/env python3
from __future__ import annotations

import csv
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent.parent
REPORTS = ROOT / "reports"
SCRIPTS = ROOT / "scripts"

MD_PATH = REPORTS / "coinbase_spot_deployability_board.md"
JSON_PATH = REPORTS / "coinbase_spot_deployability_board.json"

RSI_SCOREBOARD_PATH = REPORTS / "coinbase_spot_rsi_scoreboard.csv"
RAVE_LIVE_STATE_PATH = REPORTS / "rave_rsi_mr_live_v2_state.json"
RAVE_RECON_PATH = REPORTS / "benchmark_engine_reconciliation.json"
TACTICS_PATH = REPORTS / "coinbase_spot_tactics_72h.csv"
PIRANHA_CANDIDATE_PATHS = [
    REPORTS / "coinbase_spot_piranha_candidates_72h.csv",
    REPORTS / "coinbase_spot_piranha_candidates.csv",
]
PIRANHA_STATE_PATHS = [
    REPORTS / "coinbase_spot_shadow_dogeusd_piranha_state.json",
    REPORTS / "coinbase_spot_shadow_xrpusd_piranha_state.json",
    REPORTS / "coinbase_spot_shadow_suiusd_piranha_state.json",
    REPORTS / "coinbase_spot_shadow_adausd_piranha_state.json",
    REPORTS / "coinbase_spot_shadow_solusd_piranha_state.json",
]
RECLAIM_PATH = REPORTS / "coinbase_spot_flush_reclaim_72h.csv"
PULLBACK_PATH = REPORTS / "coinbase_spot_pullback_resume_72h.csv"
PORTFOLIO_PATH = REPORTS / "multi_strategy_portfolio_results.json"
IOTX_SCRIPT_PATH = SCRIPTS / "live_iotx_bb_reversion.py"

ACTION_PRIORITY = {
    "restore_live": 0,
    "promote_small_live": 1,
    "keep_shadow": 2,
    "launch_shadow": 3,
    "reconcile_first": 4,
    "reject": 5,
}


def load_csv(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open("r", newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


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


def to_int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def parse_iso_timestamp(raw: str) -> datetime | None:
    if not raw:
        return None
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None


def pick_existing_path(paths: list[Path]) -> Path | None:
    for path in paths:
        if path.exists():
            return path
    return None


def runner_status_from_age(age_seconds: float | None) -> str:
    if age_seconds is None:
        return "not_running"
    if age_seconds <= 300:
        return "active"
    if age_seconds <= 3600:
        return "stale"
    return "offline"


def runner_status_from_timestamp(raw: str, *, now: datetime) -> str:
    parsed = parse_iso_timestamp(raw)
    if parsed is None:
        return "not_running"
    age = max(0.0, (now - parsed).total_seconds())
    return runner_status_from_age(age)


def format_usd(value: float | None) -> str:
    if value is None:
        return ""
    return f"{value:.4f}"


def build_rave_live_candidate(*, now: datetime) -> dict[str, Any] | None:
    state = load_json(RAVE_LIVE_STATE_PATH)
    if not state:
        return None
    recon = load_json(RAVE_RECON_PATH)
    live_state = state.get("state") or {}
    recon_models = recon.get("models") or {}
    realistic = ((recon_models.get("realistic") or {}).get("harness") or {})
    updated_at = str(state.get("updated_at") or "")
    runner_status = runner_status_from_timestamp(updated_at, now=now)
    action = "restore_live" if runner_status != "active" else "promote_small_live"
    live_net = to_float(live_state.get("realized_net"))
    benchmark_net = to_float(realistic.get("net_pnl"))
    note = (
        f"live closes={to_int(live_state.get('closes'))}, "
        f"win_rate={to_float(live_state.get('win_rate')):.2f}%; "
        f"7d realistic reconciliation net=${benchmark_net:.2f}"
    )
    return {
        "product_id": "RAVE-USD",
        "lane": "rave_rsi_mr_live_v2",
        "family": "rsi_mean_reversion",
        "evidence_tier": "live_reconciled",
        "runner_status": runner_status,
        "action": action,
        "observed_net_usd": round(live_net, 4),
        "supporting_net_usd": round(benchmark_net, 4),
        "note": note,
    }


def build_rsi_shadow_candidates() -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for row in load_csv(RSI_SCOREBOARD_PATH):
        product_id = str(row.get("product_id") or "")
        lane_name = str(row.get("lane_name") or "")
        if lane_name == "TOTAL" or not product_id or product_id == "RAVE-USD":
            continue
        realized = to_float(row.get("realized_net_usd"))
        baseline = to_float(row.get("baseline_72h_net_usd"))
        readiness = str(row.get("readiness_verdict") or "")
        runner_status = runner_status_from_age(to_float(row.get("heartbeat_age_seconds")))
        action = "promote_small_live" if realized > 0.0 and readiness == "probationary" else "reject"
        note = (
            f"shadow realized=${realized:.4f}, baseline72h=${baseline:.4f}, "
            f"walkforward={row.get('walkforward') or '-'}"
        )
        rows.append(
            {
                "product_id": product_id,
                "lane": lane_name,
                "family": "rsi_mean_reversion",
                "evidence_tier": "shadow_probationary" if readiness == "probationary" else "shadow_unrated",
                "runner_status": runner_status,
                "action": action,
                "observed_net_usd": round(realized, 4),
                "supporting_net_usd": round(baseline, 4),
                "note": note,
            }
        )
    rows.sort(
        key=lambda row: (
            ACTION_PRIORITY[row["action"]],
            -float(row["observed_net_usd"]),
            -float(row["supporting_net_usd"]),
            str(row["product_id"]),
        )
    )
    return rows


def load_piranha_state_map() -> dict[str, dict[str, Any]]:
    states: dict[str, dict[str, Any]] = {}
    for path in PIRANHA_STATE_PATHS:
        payload = load_json(path)
        metadata = payload.get("metadata") or {}
        product_id = str(metadata.get("product_id") or "")
        if product_id:
            states[product_id] = payload
    return states


def build_piranha_candidates(*, now: datetime) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    path = pick_existing_path(PIRANHA_CANDIDATE_PATHS)
    state_map = load_piranha_state_map()
    candidates: list[dict[str, Any]] = []
    rejects: list[dict[str, Any]] = []
    seen: set[str] = set()
    for row in load_csv(path) if path is not None else []:
        product_id = str(row.get("product_id") or row.get("Product") or "")
        if not product_id:
            continue
        seen.add(product_id)
        sim_pnl = to_float(row.get("sim_realized_usd") or row.get("Sim PnL") or row.get("realized_net_usd"))
        sim_closes = to_int(row.get("sim_closes") or row.get("Closes"))
        state = state_map.get(product_id) or {}
        runner = state.get("runner") or {}
        symbol = ((state.get("symbols") or {}).get(product_id) or {})
        runner_status = runner_status_from_timestamp(str(runner.get("heartbeat_at") or ""), now=now)
        realized = to_float(symbol.get("realized_net_usd"))
        open_lots = len(symbol.get("open_lots") or [])
        if state:
            action = "keep_shadow" if sim_pnl > 0.0 else "reject"
            evidence = "shadow_probe" if sim_pnl > 0.0 else "shadow_negative"
            note = (
                f"sim=${sim_pnl:.4f} over {sim_closes} closes; "
                f"shadow realized=${realized:.4f}; open_lots={open_lots}"
            )
        else:
            action = "launch_shadow" if sim_pnl > 0.0 else "reject"
            evidence = "benchmark_only" if sim_pnl > 0.0 else "benchmark_negative"
            note = f"sim=${sim_pnl:.4f} over {sim_closes} closes; no runner state yet"
        record = {
            "product_id": product_id,
            "lane": "coinbase_spot_piranha",
            "family": "spot_piranha",
            "evidence_tier": evidence,
            "runner_status": runner_status if state else "not_running",
            "action": action,
            "observed_net_usd": round(realized, 4),
            "supporting_net_usd": round(sim_pnl, 4),
            "note": note,
        }
        if action == "reject":
            rejects.append(record)
        else:
            candidates.append(record)

    for product_id, state in state_map.items():
        if product_id in seen:
            continue
        runner = state.get("runner") or {}
        symbol = ((state.get("symbols") or {}).get(product_id) or {})
        rejects.append(
            {
                "product_id": product_id,
                "lane": "coinbase_spot_piranha",
                "family": "spot_piranha",
                "evidence_tier": "shadow_unscored",
                "runner_status": runner_status_from_timestamp(str(runner.get("heartbeat_at") or ""), now=now),
                "action": "reject",
                "observed_net_usd": round(to_float(symbol.get("realized_net_usd")), 4),
                "supporting_net_usd": 0.0,
                "note": "runner exists without positive candidate benchmark",
            }
        )

    return candidates, rejects


def build_maker_candidate() -> dict[str, Any] | None:
    for row in load_csv(TACTICS_PATH):
        if str(row.get("tactic") or "") != "maker_scavenger":
            continue
        realized = to_float(row.get("realized_net_usd"))
        if realized <= 0.0:
            return None
        product_id = str(row.get("best_product_id") or "")
        trades = to_int(row.get("trades"))
        return {
            "product_id": product_id,
            "lane": "maker_scavenger",
            "family": "maker_scavenger",
            "evidence_tier": "benchmark_only",
            "runner_status": "not_running",
            "action": "launch_shadow",
            "observed_net_usd": 0.0,
            "supporting_net_usd": round(realized, 4),
            "note": f"72h benchmark net=${realized:.4f} over {trades} closes",
        }
    return None


def build_contested_candidates() -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    portfolio = load_json(PORTFOLIO_PATH)
    equal = portfolio.get("equal_allocation") or {}
    individuals = equal.get("individual") or []
    by_name = {str(row.get("name") or ""): row for row in individuals}

    iotx_script_text = load_text(IOTX_SCRIPT_PATH)
    iotx_claim = re.search(
        r"Backtest:\s*(?P<wr>[\d.]+)% WR,\s*\$(?P<monthly>[\d.]+)\/mo,\s*(?P<dd>[\d.]+)% DD,\s*RAR\s*(?P<rar>[\d.]+)",
        iotx_script_text,
    )
    iotx_portfolio = by_name.get("IOTX BB Rev") or {}
    if iotx_claim or iotx_portfolio:
        claim_monthly = to_float(iotx_claim.group("monthly")) if iotx_claim else 0.0
        portfolio_net = to_float(iotx_portfolio.get("net_pnl"))
        note_parts = []
        if iotx_claim:
            note_parts.append(
                f"script docstring claims ${claim_monthly:.2f}/mo at {to_float(iotx_claim.group('wr')):.1f}% WR"
            )
        if iotx_portfolio:
            note_parts.append(f"portfolio report shows net=${portfolio_net:.2f}")
        rows.append(
            {
                "product_id": "IOTX-USD",
                "lane": "iotx_bb_reversion",
                "family": "bb_reversion",
                "evidence_tier": "contested_claim",
                "runner_status": "not_running",
                "action": "reconcile_first",
                "observed_net_usd": round(portfolio_net, 4),
                "supporting_net_usd": round(claim_monthly, 4),
                "note": "; ".join(note_parts),
            }
        )

    for name, product_id in [("BAL Momentum", "BAL-USD"), ("BLUR Momentum", "BLUR-USD")]:
        row = by_name.get(name) or {}
        if not row:
            continue
        net = to_float(row.get("net_pnl"))
        action = "reconcile_first" if net > 0.0 else "reject"
        rows.append(
            {
                "product_id": product_id,
                "lane": name.lower().replace(" ", "_"),
                "family": "alt_momentum",
                "evidence_tier": "noncanonical_portfolio_only",
                "runner_status": "not_running",
                "action": action,
                "observed_net_usd": round(net, 4),
                "supporting_net_usd": round(to_float(row.get("return_pct")), 4),
                "note": (
                    f"noncanonical equal-allocation portfolio report; "
                    f"win_rate={to_float(row.get('win_rate')):.1f}%, closes={to_int(row.get('closes'))}"
                ),
            }
        )
    return rows


def negative_family_rows() -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    reclaim_rows = load_csv(RECLAIM_PATH)
    if reclaim_rows:
        positive_products = sum(1 for row in reclaim_rows if to_float(row.get("cumulative_net_pct")) > 0.0)
        signals = sum(to_int(row.get("signals")) for row in reclaim_rows)
        rows.append(
            {
                "family": "flush_reclaim",
                "status": "reject",
                "note": f"positive_products={positive_products}, signals={signals}",
            }
        )
    pullback_rows = load_csv(PULLBACK_PATH)
    if pullback_rows:
        positive_products = sum(1 for row in pullback_rows if to_float(row.get("cumulative_net_pct")) > 0.0)
        signals = sum(to_int(row.get("signals")) for row in pullback_rows)
        rows.append(
            {
                "family": "pullback_resume",
                "status": "reject",
                "note": f"positive_products={positive_products}, signals={signals}",
            }
        )
    for row in load_csv(TACTICS_PATH):
        tactic = str(row.get("tactic") or "")
        realized = to_float(row.get("realized_net_usd"))
        if tactic in {"relative_strength_rotator", "pump_rider_breakout"} and realized <= 0.0:
            rows.append(
                {
                    "family": tactic,
                    "status": "reject",
                    "note": f"72h realized_net_usd={realized:.4f}",
                }
            )
    return rows


def build_leadership_read(candidates: list[dict[str, Any]]) -> list[str]:
    rave = next((row for row in candidates if row.get("product_id") == "RAVE-USD" and row.get("family") == "rsi_mean_reversion"), None)
    doge_or_xrp = [
        str(row["product_id"])
        for row in candidates
        if row.get("family") == "spot_piranha" and row.get("action") == "keep_shadow"
    ]
    return [
        (
            "RAVE live is active again, so it stays first but no longer as a blind restore order."
            if rave and str(rave.get("action")) == "promote_small_live"
            else "Restore the RAVE live RSI lane before inventing more portfolio structure."
        ),
        "Treat positive non-RAVE RSI rows as the cleanest small-live promotion queue.",
        (
            "Keep " + "/".join(doge_or_xrp) + " piranha in shadow until they produce closes; AVAX remains benchmark-only if capacity opens."
            if doge_or_xrp
            else "Keep piranha in shadow until it produces closes; launch SUI/AVAX next if capacity opens."
        ),
        "Do not allocate to IOTX/BAL claims until the backtest engines are reconciled on shared candles.",
    ]


def build_payload(*, now: datetime | None = None) -> dict[str, Any]:
    now = now or datetime.now(timezone.utc)
    candidates: list[dict[str, Any]] = []

    rave = build_rave_live_candidate(now=now)
    if rave is not None:
        candidates.append(rave)
    candidates.extend(build_rsi_shadow_candidates())
    piranha_candidates, piranha_rejects = build_piranha_candidates(now=now)
    candidates.extend(piranha_candidates)
    maker = build_maker_candidate()
    if maker is not None:
        candidates.append(maker)
    candidates.extend(build_contested_candidates())

    candidates.sort(
        key=lambda row: (
            ACTION_PRIORITY[row["action"]],
            -float(row["observed_net_usd"]),
            -float(row["supporting_net_usd"]),
            str(row["product_id"]),
            str(row["lane"]),
        )
    )

    router: list[dict[str, Any]] = []
    seen_products: set[str] = set()
    for row in candidates:
        product_id = str(row["product_id"])
        if product_id in seen_products or row["action"] == "reject":
            continue
        seen_products.add(product_id)
        router.append(
            {
                "product_id": product_id,
                "recommended_lane": row["lane"],
                "family": row["family"],
                "action": row["action"],
                "reason": row["note"],
            }
        )

    rejects = [row for row in candidates if row["action"] == "reject"]
    rejects.extend(piranha_rejects)
    rejects.sort(key=lambda row: (str(row["product_id"]), str(row["lane"])))

    return {
        "generated_at": now.isoformat(),
        "candidates": candidates,
        "router": router,
        "rejects": rejects,
        "negative_families": negative_family_rows(),
        "leadership_read": build_leadership_read(candidates),
    }


def write_reports(payload: dict[str, Any], *, md_path: Path = MD_PATH, json_path: Path = JSON_PATH) -> None:
    json_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    lines = [
        "# Coinbase Spot Deployability Board",
        "",
        "## Leadership Read",
        "",
    ]
    for line in payload["leadership_read"]:
        lines.append(f"- {line}")

    lines.extend(
        [
            "",
            "## Promotion Order",
            "",
            "| Action | Product | Lane | Family | Evidence | Runner | Observed $ | Support $ | Why |",
            "| --- | --- | --- | --- | --- | --- | ---: | ---: | --- |",
        ]
    )
    for row in payload["candidates"]:
        lines.append(
            "| {action} | {product_id} | {lane} | {family} | {evidence_tier} | {runner_status} | {observed} | {supporting} | {note} |".format(
                action=row["action"],
                product_id=row["product_id"],
                lane=row["lane"],
                family=row["family"],
                evidence_tier=row["evidence_tier"],
                runner_status=row["runner_status"],
                observed=format_usd(to_float(row["observed_net_usd"])),
                supporting=format_usd(to_float(row["supporting_net_usd"])),
                note=row["note"],
            )
        )

    lines.extend(
        [
            "",
            "## Product Router",
            "",
            "| Product | Recommended Lane | Family | Action | Reason |",
            "| --- | --- | --- | --- | --- |",
        ]
    )
    for row in payload["router"]:
        lines.append(
            "| {product_id} | {recommended_lane} | {family} | {action} | {reason} |".format(**row)
        )

    lines.extend(
        [
            "",
            "## Do Not Allocate",
            "",
            "| Family | Status | Note |",
            "| --- | --- | --- |",
        ]
    )
    for row in payload["negative_families"]:
        lines.append("| {family} | {status} | {note} |".format(**row))
    for row in payload["rejects"]:
        lines.append(
            "| {family} / {product_id} | reject | {note} |".format(
                family=row["family"], product_id=row["product_id"], note=row["note"]
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
