#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent.parent
REPORTS = ROOT / "reports"

DEFAULT_COINBASE_MAKER_REALITY_PATH = REPORTS / "coinbase_spot_maker_execution_reality_board.json"
DEFAULT_KRAKEN_ROUTE_PATH = REPORTS / "kraken_spot_tick_jump_route_board.json"
DEFAULT_OVERLAP_PATH = REPORTS / "coinbase_kraken_spot_overlap_board.json"
DEFAULT_KRAKEN_MAKER_OPPORTUNITY_PATH = REPORTS / "kraken_maker_opportunity_board.json"
DEFAULT_KRAKEN_RADAR_PATH = REPORTS / "kraken_spot_live_radar.json"
DEFAULT_JSON_PATH = REPORTS / "spot_venue_handoff_policy.json"
DEFAULT_MD_PATH = REPORTS / "spot_venue_handoff_policy.md"
DEFAULT_LOSS_TRACKER_PATHS = [
    REPORTS / "kraken_frontier_loss_tracker_state.json",
    REPORTS / "kraken_maker_loss_tracker_state.json",
    *REPORTS.glob("coinbase_rsi_loss_tracker_*.json"),
    *REPORTS.glob("strict_maker_loss_tracker_*.json"),
]


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def load_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    tmp.replace(path)


def to_float(value: Any) -> float:
    try:
        return float(value or 0.0)
    except (TypeError, ValueError):
        return 0.0


def clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def rows_by_product(payload: dict[str, Any]) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for row in payload.get("rows") or []:
        if not isinstance(row, dict):
            continue
        product_id = str(row.get("product_id") or "").upper()
        if product_id:
            out[product_id] = row
    return out


def normalize_product_id(value: Any) -> str:
    return str(value or "").strip().upper().replace("/", "-")


def parse_loss_tracker_blocks(paths: list[Path]) -> dict[str, dict[str, Any]]:
    blocked: dict[str, dict[str, Any]] = {}
    now_ts = datetime.now(timezone.utc).timestamp()
    for path in paths:
        payload = load_json(path)
        if not payload:
            continue
        blocked_until = payload.get("blocked_until") if isinstance(payload.get("blocked_until"), dict) else {}
        consecutive_losses = payload.get("consecutive_losses") if isinstance(payload.get("consecutive_losses"), dict) else {}
        total_losses = payload.get("total_losses") if isinstance(payload.get("total_losses"), dict) else {}
        total_wins = payload.get("total_wins") if isinstance(payload.get("total_wins"), dict) else {}
        for raw_pid, raw_unblock_at in blocked_until.items():
            product_id = normalize_product_id(raw_pid)
            unblock_at = to_float(raw_unblock_at)
            if not product_id or unblock_at <= now_ts:
                continue
            existing = blocked.get(product_id)
            source_paths = list(existing.get("source_paths", [])) if existing else []
            source_paths.append(str(path))
            blocked[product_id] = {
                "product_id": product_id,
                "unblock_at": unblock_at,
                "unblock_at_utc": datetime.fromtimestamp(unblock_at, tz=timezone.utc).isoformat(),
                "cooldown_remaining_seconds": round(unblock_at - now_ts, 0),
                "consecutive_losses": int(to_float(consecutive_losses.get(raw_pid))),
                "total_losses": int(to_float(total_losses.get(raw_pid))),
                "total_wins": int(to_float(total_wins.get(raw_pid))),
                "source_paths": sorted(set(source_paths)),
            }
    return blocked


def apply_loss_tracker_blocks(actions: list[dict[str, Any]], blocked: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    if not blocked:
        return actions
    out: list[dict[str, Any]] = []
    for row in actions:
        product_id = normalize_product_id(row.get("product_id"))
        block = blocked.get(product_id)
        if not block:
            out.append(row)
            continue
        blocked_row = dict(row)
        blocked_row["action"] = f"{row.get('venue')}_death_spiral_blocked"
        blocked_row["proof_status"] = "death_spiral_blocked"
        blocked_row["score"] = -abs(to_float(row.get("score")))
        blocked_row["death_spiral_block"] = block
        blocked_row["notes"] = (
            f"Blocked by shared loss tracker until {block['unblock_at_utc']}; "
            f"original action was {row.get('action')}."
        )
        out.append(blocked_row)
    return out


def action_row(
    *,
    venue: str,
    execution_style: str,
    action: str,
    product_id: str,
    source_product_id: str = "",
    route_state: str,
    runner: str,
    playbook: str,
    score: float,
    expected_edge_pct: float = 0.0,
    expected_edge_bps: float = 0.0,
    spread_bps: float = 0.0,
    mer: float = 0.0,
    fill_score: float = 0.0,
    can_trade: bool = True,
    proof_status: str = "shadow_ready",
    source: str = "",
    notes: str = "",
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    row = {
        "venue": venue,
        "execution_style": execution_style,
        "action": action,
        "product_id": product_id,
        "source_product_id": source_product_id or product_id,
        "route_state": route_state,
        "runner": runner,
        "playbook": playbook,
        "score": round(score, 6),
        "expected_edge_pct": round(expected_edge_pct, 6),
        "expected_edge_bps": round(expected_edge_bps, 6),
        "spread_bps": round(spread_bps, 6),
        "mer": round(mer, 6),
        "fill_score": round(fill_score, 6),
        "can_trade": bool(can_trade),
        "proof_status": proof_status,
        "source": source,
        "notes": notes,
    }
    if extra:
        row.update(extra)
    return row


def coinbase_actions(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    actions: list[dict[str, Any]] = []
    for row in rows:
        verdict = str(row.get("current_verdict") or "")
        product_id = str(row.get("product_id") or "").upper()
        if not product_id:
            continue
        fill_score = to_float(row.get("current_maker_entry_fill_score"))
        edge = max(
            to_float(row.get("current_maker_taker_realistic_edge_pct")),
            to_float(row.get("current_maker_maker_realistic_edge_pct")),
        )
        score = edge * 20.0 + fill_score * 0.15 + to_float(row.get("pulse_score")) * 0.05
        if verdict == "maker_taker_shadow_probe":
            actions.append(
                action_row(
                    venue="coinbase",
                    execution_style="maker_entry_taker_exit",
                    action="coinbase_maker_taker_shadow",
                    product_id=product_id,
                    route_state=verdict,
                    runner="maker_fee_rsi_shadow",
                    playbook="rsi4_post_only_entry_fee_realism",
                    score=score,
                    expected_edge_pct=to_float(row.get("current_maker_taker_realistic_edge_pct")),
                    spread_bps=to_float(row.get("spread_bps")),
                    fill_score=fill_score,
                    source="coinbase_maker_execution_reality",
                    notes="Current-fee executable shadow candidate; post-only entry fill still needs live proof.",
                )
            )
        elif verdict == "maker_maker_only_needs_exit_fill_proof":
            actions.append(
                action_row(
                    venue="coinbase",
                    execution_style="maker_entry_maker_exit",
                    action="coinbase_maker_maker_proof_only",
                    product_id=product_id,
                    route_state=verdict,
                    runner="maker_fee_rsi_shadow",
                    playbook="rsi4_double_post_only_fill_proof",
                    score=score * 0.6,
                    expected_edge_pct=to_float(row.get("current_maker_maker_realistic_edge_pct")),
                    spread_bps=to_float(row.get("spread_bps")),
                    fill_score=fill_score,
                    proof_status="proof_only_needs_exit_fill_telemetry",
                    source="coinbase_maker_execution_reality",
                    notes="Do not treat maker exit as banked until order-book fill/miss telemetry proves it.",
                )
            )
    return actions


def kraken_route_actions(rows: list[dict[str, Any]], *, max_taker_spread_bps: float) -> list[dict[str, Any]]:
    actions: list[dict[str, Any]] = []
    accepted = {"kraken_fee_flip_candidate", "clears_kraken_hurdle", "near_kraken_hurdle"}
    for row in rows:
        verdict = str(row.get("route_verdict") or "")
        product_id = str(row.get("kraken_product_id") or "").upper()
        if not product_id or verdict not in accepted:
            continue
        spread = to_float(row.get("kraken_spread_bps"))
        can_trade = bool(row.get("can_trade_starting_cash"))
        if not can_trade:
            proof = "blocked_min_size"
        elif spread > max_taker_spread_bps:
            proof = "proof_only_wide_spread"
        else:
            proof = "shadow_ready" if to_float(row.get("kraken_edge_bps")) >= 0 else "watch_only_near_hurdle"
        score = to_float(row.get("route_score")) + max(0.0, to_float(row.get("kraken_edge_bps"))) * 0.03
        actions.append(
            action_row(
                venue="kraken",
                execution_style="taker_taker",
                action="kraken_taker_shadow" if proof == "shadow_ready" else "kraken_taker_watch",
                product_id=product_id,
                source_product_id=str(row.get("coinbase_product_id") or product_id),
                route_state=verdict,
                runner="live_kraken_spot_frontier_machinegun_shadow",
                playbook="lower_fee_tick_jump",
                score=score,
                expected_edge_bps=to_float(row.get("kraken_edge_bps")),
                spread_bps=spread,
                can_trade=can_trade,
                proof_status=proof,
                source="kraken_tick_jump_route",
                notes="Coinbase-style movement routed to lower-fee Kraken only when current radar route is tradable.",
            )
        )
    return actions


def kraken_overlap_actions(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    actions: list[dict[str, Any]] = []
    accepted_states = {"live_hot", "building"}
    accepted_routes = {"kraken_velocity_board", "kraken_live_radar"}
    for row in rows:
        signal_state = str(row.get("coinbase_signal_state") or "")
        route_state = str(row.get("kraken_route_state") or "")
        product_id = str(row.get("kraken_product_id") or "").upper()
        if signal_state not in accepted_states or route_state not in accepted_routes or not product_id:
            continue
        can_trade = bool(row.get("can_trade_100"))
        edge_bps = to_float(row.get("kraken_edge_bps"))
        score = min(to_float(row.get("candidate_score")), 100.0) * 0.15 + max(0.0, edge_bps) * 0.03
        actions.append(
            action_row(
                venue="kraken",
                execution_style="taker_taker",
                action="kraken_taker_shadow" if can_trade and edge_bps >= 0 else "kraken_signal_watch",
                product_id=product_id,
                source_product_id=str(row.get("product_id") or product_id),
                route_state=route_state,
                runner="live_kraken_spot_frontier_machinegun_shadow",
                playbook="coinbase_signal_kraken_route",
                score=score,
                expected_edge_bps=edge_bps,
                spread_bps=to_float(row.get("kraken_spread_bps")),
                can_trade=can_trade,
                proof_status="shadow_ready" if can_trade and edge_bps >= 0 else "watch_only_needs_kraken_edge",
                source="coinbase_kraken_overlap",
                notes="Coinbase live signal mapped to the actual Kraken product id; no Coinbase id is sent to Kraken runners.",
            )
        )
    return actions


def kraken_maker_actions(
    rows: list[dict[str, Any]],
    radar_by_product: dict[str, dict[str, Any]],
    *,
    min_mer: float,
    max_maker_spread_bps: float,
) -> list[dict[str, Any]]:
    actions: list[dict[str, Any]] = []
    for row in rows:
        product_id = str(row.get("product_id") or "").upper()
        radar = radar_by_product.get(product_id) or {}
        if not product_id or not radar:
            continue
        spread = to_float(row.get("spread_bps") or radar.get("spread_bps"))
        mer = to_float(row.get("mer"))
        can_trade = bool(radar.get("can_trade_starting_cash"))
        if mer < min_mer:
            continue
        if not can_trade:
            proof = "blocked_min_size"
            action = "kraken_maker_watch"
        elif spread > max_maker_spread_bps:
            proof = "proof_only_extreme_spread"
            action = "kraken_maker_proof_only"
        else:
            proof = "shadow_ready"
            action = "kraken_maker_shadow"
        pulse_score = to_float(row.get("pulse_score"))
        ret_15m_bps = to_float(row.get("ret_15m_bps"))
        score = mer * 35.0 + clamp(pulse_score, -10.0, 50.0) * 0.2 + max(0.0, ret_15m_bps) * 0.02
        if proof != "shadow_ready":
            score *= 0.25
        actions.append(
            action_row(
                venue="kraken",
                execution_style="maker_maker",
                action=action,
                product_id=product_id,
                route_state="kraken_maker_opportunity",
                runner="live_kraken_spot_frontier_maker_machinegun_shadow",
                playbook="bounded_mer_spread_harvest",
                score=score,
                spread_bps=spread,
                mer=mer,
                can_trade=can_trade,
                proof_status=proof,
                source="kraken_maker_opportunity",
                notes="MER is only accepted as executable when spread stays under the bounded maker-spread ceiling.",
                extra={
                    "atr_12_bps": round(to_float(row.get("atr_12_bps")), 6),
                    "ret_15m_bps": round(ret_15m_bps, 6),
                    "vol_24h_usd": round(to_float(row.get("vol_24h_usd")), 6),
                },
            )
        )
    return actions


def dedupe_actions(actions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    best: dict[tuple[str, str, str], dict[str, Any]] = {}
    for row in actions:
        key = (str(row.get("venue")), str(row.get("execution_style")), str(row.get("product_id")))
        current = best.get(key)
        if current is None or to_float(row.get("score")) > to_float(current.get("score")):
            best[key] = row
    rows = list(best.values())
    proof_rank = {
        "shadow_ready": 0,
        "watch_only_near_hurdle": 1,
        "watch_only_needs_kraken_edge": 2,
        "proof_only_needs_exit_fill_telemetry": 3,
        "proof_only_extreme_spread": 4,
        "proof_only_wide_spread": 5,
        "blocked_min_size": 6,
        "death_spiral_blocked": 7,
    }
    venue_rank = {"kraken": 0, "coinbase": 1}
    rows.sort(
        key=lambda row: (
            proof_rank.get(str(row.get("proof_status")), 99),
            venue_rank.get(str(row.get("venue")), 9),
            -to_float(row.get("score")),
            str(row.get("product_id")),
        )
    )
    for idx, row in enumerate(rows, start=1):
        row["rank"] = idx
    return rows


def build_payload(
    *,
    coinbase_maker_reality_path: Path = DEFAULT_COINBASE_MAKER_REALITY_PATH,
    kraken_route_path: Path = DEFAULT_KRAKEN_ROUTE_PATH,
    overlap_path: Path = DEFAULT_OVERLAP_PATH,
    kraken_maker_opportunity_path: Path = DEFAULT_KRAKEN_MAKER_OPPORTUNITY_PATH,
    kraken_radar_path: Path = DEFAULT_KRAKEN_RADAR_PATH,
    loss_tracker_paths: list[Path] | None = None,
    min_mer: float = 0.50,
    max_maker_spread_bps: float = 750.0,
    max_taker_spread_bps: float = 125.0,
) -> dict[str, Any]:
    coinbase_maker = load_json(coinbase_maker_reality_path)
    kraken_route = load_json(kraken_route_path)
    overlap = load_json(overlap_path)
    kraken_maker = load_json(kraken_maker_opportunity_path)
    kraken_radar = load_json(kraken_radar_path)
    loss_blocks = parse_loss_tracker_blocks(loss_tracker_paths if loss_tracker_paths is not None else DEFAULT_LOSS_TRACKER_PATHS)
    radar_by_product = rows_by_product(kraken_radar)

    actions = []
    actions.extend(coinbase_actions([r for r in coinbase_maker.get("rows") or [] if isinstance(r, dict)]))
    actions.extend(
        kraken_route_actions(
            [r for r in kraken_route.get("rows") or [] if isinstance(r, dict)],
            max_taker_spread_bps=max_taker_spread_bps,
        )
    )
    actions.extend(kraken_overlap_actions([r for r in overlap.get("rows") or [] if isinstance(r, dict)]))
    actions.extend(
        kraken_maker_actions(
            [r for r in kraken_maker.get("rows") or [] if isinstance(r, dict)],
            radar_by_product,
            min_mer=min_mer,
            max_maker_spread_bps=max_maker_spread_bps,
        )
    )
    actions = apply_loss_tracker_blocks(actions, loss_blocks)
    actions = dedupe_actions(actions)
    counts: dict[str, int] = {}
    proof_counts: dict[str, int] = {}
    for row in actions:
        counts[str(row.get("action"))] = counts.get(str(row.get("action")), 0) + 1
        proof_counts[str(row.get("proof_status"))] = proof_counts.get(str(row.get("proof_status")), 0) + 1

    return {
        "generated_at": utc_now_iso(),
        "mode": "spot_venue_handoff_policy_v1",
        "parameters": {
            "min_mer": min_mer,
            "max_maker_spread_bps": max_maker_spread_bps,
            "max_taker_spread_bps": max_taker_spread_bps,
            "loss_tracker_paths": [str(path) for path in (loss_tracker_paths if loss_tracker_paths is not None else DEFAULT_LOSS_TRACKER_PATHS)],
        },
        "counts": counts,
        "proof_counts": proof_counts,
        "loss_tracker_blocks": loss_blocks,
        "actions": actions,
        "leadership_read": [
            "This is an operational manifest, not a promotion claim.",
            "Kraken routes always use kraken_product_id; Coinbase product ids are kept as source ids only.",
            "Extreme-spread MER candidates are proof-only until post-patch fill/miss evidence proves they are not adverse-selection traps.",
            "Shared loss-tracker blocks override venue attractiveness; death-spiral products do not route until cooldown expires.",
            "Coinbase zero-fee hypotheticals are excluded; Coinbase actions use current-fee maker-reality rows.",
        ],
    }


def write_md(path: Path, payload: dict[str, Any], *, limit: int = 40) -> None:
    lines = [
        "# Spot Venue Handoff Policy",
        "",
        f"- Generated: `{payload['generated_at']}`",
        f"- Mode: `{payload['mode']}`",
        f"- Counts: `{payload['counts']}`",
        f"- Proof counts: `{payload['proof_counts']}`",
        "",
        "| Rank | Action | Venue | Product | Source Product | Proof | Score | Spread bps | MER | Edge bps | Runner |",
        "| ---: | --- | --- | --- | --- | --- | ---: | ---: | ---: | ---: | --- |",
    ]
    for row in payload["actions"][:limit]:
        lines.append(
            "| {rank} | {action} | {venue} | {product_id} | {source_product_id} | {proof_status} | {score:.4f} | "
            "{spread_bps:.2f} | {mer:.4f} | {expected_edge_bps:.2f} | {runner} |".format(**row)
        )
    lines.extend(["", "## Contract", ""])
    for item in payload["leadership_read"]:
        lines.append(f"- {item}")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build the executable Coinbase/Kraken spot venue handoff policy.")
    parser.add_argument("--coinbase-maker-reality-path", default=str(DEFAULT_COINBASE_MAKER_REALITY_PATH))
    parser.add_argument("--kraken-route-path", default=str(DEFAULT_KRAKEN_ROUTE_PATH))
    parser.add_argument("--overlap-path", default=str(DEFAULT_OVERLAP_PATH))
    parser.add_argument("--kraken-maker-opportunity-path", default=str(DEFAULT_KRAKEN_MAKER_OPPORTUNITY_PATH))
    parser.add_argument("--kraken-radar-path", default=str(DEFAULT_KRAKEN_RADAR_PATH))
    parser.add_argument("--min-mer", type=float, default=0.50)
    parser.add_argument("--max-maker-spread-bps", type=float, default=750.0)
    parser.add_argument("--max-taker-spread-bps", type=float, default=125.0)
    parser.add_argument(
        "--loss-tracker-path",
        action="append",
        default=None,
        help="Optional loss tracker JSON path. Repeat to override defaults with explicit block sources.",
    )
    parser.add_argument("--json-path", default=str(DEFAULT_JSON_PATH))
    parser.add_argument("--md-path", default=str(DEFAULT_MD_PATH))
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    payload = build_payload(
        coinbase_maker_reality_path=Path(args.coinbase_maker_reality_path),
        kraken_route_path=Path(args.kraken_route_path),
        overlap_path=Path(args.overlap_path),
        kraken_maker_opportunity_path=Path(args.kraken_maker_opportunity_path),
        kraken_radar_path=Path(args.kraken_radar_path),
        loss_tracker_paths=[Path(item) for item in args.loss_tracker_path] if args.loss_tracker_path else None,
        min_mer=args.min_mer,
        max_maker_spread_bps=args.max_maker_spread_bps,
        max_taker_spread_bps=args.max_taker_spread_bps,
    )
    write_json(Path(args.json_path), payload)
    write_md(Path(args.md_path), payload)
    print(f"DONE! Saved {len(payload['actions'])} actions to {args.json_path}")


if __name__ == "__main__":
    main()
