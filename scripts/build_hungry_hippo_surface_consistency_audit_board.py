#!/usr/bin/env python3
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent.parent
REPORTS = ROOT / "reports"

PORTABILITY_PATH = REPORTS / "hungry_hippo_symbol_portability_board.json"
WATCH_PATH = REPORTS / "hungry_hippo_forward_shadow_watch_board.json"
ROLLOUT_GATE_PATH = REPORTS / "hungry_hippo_parallel_rollout_gate_board.json"
LAUNCH_PACKET_PATH = REPORTS / "hungry_hippo_first_proof_launch_packet_board.json"

OUTPUT_JSON_PATH = REPORTS / "hungry_hippo_surface_consistency_audit_board.json"
OUTPUT_MD_PATH = REPORTS / "hungry_hippo_surface_consistency_audit_board.md"


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def find_symbol(rows: list[dict[str, Any]], symbol: str) -> dict[str, Any] | None:
    clean_symbol = str(symbol or "").upper()
    for row in rows:
        if str(row.get("symbol") or "").upper() == clean_symbol:
            return dict(row)
    return None


def slot_info(rollout_payload: dict[str, Any]) -> dict[str, dict[str, Any]]:
    info: dict[str, dict[str, Any]] = {}
    for row in list(rollout_payload.get("rows") or []):
        machine = dict(row.get("machine_truth") or {})
        for key in ("starter_candidate_symbol", "slot1_symbol", "slot2_symbol", "slot3_symbol"):
            symbol = str(machine.get(key) or "")
            if not symbol:
                continue
            info[symbol.upper()] = {
                "slot": int(row.get("max_active_lanes") or 0),
                "current_status": str(row.get("current_status") or ""),
                "blocker_reason": str(row.get("blocker_reason") or ""),
                "machine_truth": machine,
            }
    return info


def verdict_for_symbol(
    *,
    symbol: str,
    portability_row: dict[str, Any] | None,
    watch_row: dict[str, Any] | None,
    rollout_slot: dict[str, Any] | None,
    launch_packet_row: dict[str, Any] | None,
) -> tuple[str, str]:
    port_status = str((portability_row or {}).get("generalization_status") or "")
    rollout_machine = dict((rollout_slot or {}).get("machine_truth") or {})
    rollout_slot_status = str((rollout_slot or {}).get("current_status") or "")
    packet_readiness = str((launch_packet_row or {}).get("launch_readiness") or "")

    if watch_row and rollout_slot and symbol.upper() == "XRPUSD":
        if port_status == "ready_for_shadow_discussion" and "missing_launch_contract" in rollout_slot_status:
            return (
                "stale_rollout_gate_and_packet",
                "Portability and watch boards say XRPUSD is ready_for_shadow_discussion, but rollout gate still treats slot #2 as missing launch-contract follow-through. Launch packet inherits that stale blocker.",
            )
    if watch_row and rollout_slot and symbol.upper() == "AUDUSD":
        slot3_port_status = str(rollout_machine.get("slot3_portability_status") or "")
        if port_status == "ready_for_shadow_discussion" and slot3_port_status == "portable_missing_gate_surface":
            return (
                "stale_rollout_gate",
                "Forward-watch and portability boards include AUDUSD as ready_for_shadow_discussion, but rollout gate still says slot #3 is missing gate surface.",
            )
    if watch_row and launch_packet_row and packet_readiness == "launch_now":
        return (
            "aligned_launch_now",
            "Watch board and launch packet agree this parked lane is the first legal proof launch.",
        )
    if watch_row and launch_packet_row and packet_readiness == "hold_until_prior_gate_clears":
        return (
            "aligned_hold_behind_rollout_gate",
            "The lane is proof-eligible in portability/watch truth but still intentionally held behind the rollout gate.",
        )
    if watch_row and launch_packet_row and packet_readiness == "watch_only_outside_current_unlock_ladder":
        return (
            "aligned_watch_only_outside_unlock_ladder",
            "The lane is in the current forward-watch set, but the packet now correctly shows it as outside the current tiny-account unlock ladder.",
        )
    if not watch_row and launch_packet_row and packet_readiness == "blocked_validation_fail":
        return (
            "stale_rollout_gate_vs_validation",
            "Rollout doctrine still carries this symbol, but launch-safety truth currently blocks it and the forward-watch surface excludes it.",
        )
    if watch_row and not launch_packet_row:
        return (
            "missing_from_launch_packet",
            "This symbol is in the current forward-watch set but not represented in the first-proof launch packet.",
        )
    return (
        "aligned",
        "The inspected surfaces do not currently show a direct contradiction for this symbol.",
    )


def build_payload(
    portability_payload: dict[str, Any],
    watch_payload: dict[str, Any],
    rollout_payload: dict[str, Any],
    launch_packet_payload: dict[str, Any],
) -> dict[str, Any]:
    portability_rows = list(portability_payload.get("rows") or [])
    watch_rows = list(watch_payload.get("rows") or [])
    packet_rows = list(launch_packet_payload.get("rows") or [])
    rollout_slots = slot_info(rollout_payload)

    symbols = sorted(
        {
            str(row.get("symbol") or "").upper()
            for row in portability_rows + watch_rows + packet_rows
            if str(row.get("symbol") or "")
        }
    )

    rows: list[dict[str, Any]] = []
    verdict_counts: dict[str, int] = {}
    for symbol in symbols:
        portability_row = find_symbol(portability_rows, symbol)
        watch_row = find_symbol(watch_rows, symbol)
        packet_row = find_symbol(packet_rows, symbol)
        rollout_slot = rollout_slots.get(symbol.upper())
        verdict, rationale = verdict_for_symbol(
            symbol=symbol,
            portability_row=portability_row,
            watch_row=watch_row,
            rollout_slot=rollout_slot,
            launch_packet_row=packet_row,
        )
        verdict_counts[verdict] = verdict_counts.get(verdict, 0) + 1
        rows.append(
            {
                "symbol": symbol,
                "portability_status": str((portability_row or {}).get("generalization_status") or ""),
                "watch_present": watch_row is not None,
                "watch_runtime_state": str((watch_row or {}).get("runtime_state") or ""),
                "rollout_slot": int((rollout_slot or {}).get("slot") or 0),
                "rollout_status": str((rollout_slot or {}).get("current_status") or ""),
                "launch_packet_present": packet_row is not None,
                "launch_packet_readiness": str((packet_row or {}).get("launch_readiness") or ""),
                "verdict": verdict,
                "rationale": rationale,
            }
        )

    stale_symbols = [
        row["symbol"]
        for row in rows
        if row["verdict"] in {"stale_rollout_gate", "stale_rollout_gate_and_packet", "stale_rollout_gate_vs_validation", "missing_from_launch_packet"}
    ]

    return {
        "generated_at": utc_now_iso(),
        "sources": [
            str(PORTABILITY_PATH.relative_to(ROOT)),
            str(WATCH_PATH.relative_to(ROOT)),
            str(ROLLOUT_GATE_PATH.relative_to(ROOT)),
            str(LAUNCH_PACKET_PATH.relative_to(ROOT)),
        ],
        "summary": {
            "symbol_count": len(rows),
            "verdict_counts": verdict_counts,
            "stale_symbols": stale_symbols,
            "watch_set_symbols": [str(row.get("symbol") or "") for row in watch_rows],
        },
        "leadership_read": [
            (
                f"Current HH surface drift is not hypothetical: stale or contradictory symbols are `{stale_symbols}`."
                if stale_symbols
                else "Current HH rollout/watch/packet surfaces are aligned."
            ),
            "When surfaces disagree, trust order is: portability + forward-watch over rollout-gate over launch-packet, because the packet intentionally inherits gate decisions.",
            "This board is passive audit only. It should shrink back to near-empty as the active truth-sync lane lands.",
        ],
        "rows": rows,
        "notes": [
            "A symbol can be both ready_for_shadow_discussion and held behind the rollout gate without contradiction; that is sequential doctrine, not drift.",
            "This board flags only direct surface disagreement or stale inherited packet decisions.",
        ],
    }


def render_markdown(payload: dict[str, Any]) -> str:
    summary = dict(payload.get("summary") or {})
    lines = [
        "# Hungry Hippo Surface Consistency Audit Board",
        "",
        f"Generated at: `{payload.get('generated_at')}`",
        "",
        "## Leadership Read",
        "",
    ]
    for item in list(payload.get("leadership_read") or []):
        lines.append(f"- {item}")
    lines.extend(
        [
            "",
            "## Summary",
            "",
            f"- Symbol count: `{summary.get('symbol_count')}`",
            f"- Verdict counts: `{summary.get('verdict_counts')}`",
            f"- Stale symbols: `{summary.get('stale_symbols')}`",
            f"- Watch set: `{summary.get('watch_set_symbols')}`",
            "",
            "## Rows",
            "",
            "| Symbol | Portability | Watch | Rollout slot | Launch packet | Verdict |",
            "| --- | --- | --- | ---: | --- | --- |",
        ]
    )
    for row in list(payload.get("rows") or []):
        lines.append(
            f"| `{row.get('symbol')}` | `{row.get('portability_status') or '-'}` | "
            f"`{row.get('watch_runtime_state') if row.get('watch_present') else 'absent'}` | "
            f"`{row.get('rollout_slot') or '-'}` | `{row.get('launch_packet_readiness') if row.get('launch_packet_present') else 'absent'}` | "
            f"`{row.get('verdict')}` |"
        )
    lines.extend(["", "## Detail", ""])
    for row in list(payload.get("rows") or []):
        lines.extend(
            [
                f"### {row.get('symbol')}",
                "",
                f"- Verdict: `{row.get('verdict')}`",
                f"- Rationale: {row.get('rationale')}",
                "",
            ]
        )
    return "\n".join(lines).rstrip() + "\n"


def main() -> None:
    payload = build_payload(
        load_json(PORTABILITY_PATH),
        load_json(WATCH_PATH),
        load_json(ROLLOUT_GATE_PATH),
        load_json(LAUNCH_PACKET_PATH),
    )
    OUTPUT_JSON_PATH.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    OUTPUT_MD_PATH.write_text(render_markdown(payload), encoding="utf-8")


if __name__ == "__main__":
    main()
