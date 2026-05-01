#!/usr/bin/env python3
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent.parent
REPORTS = ROOT / "reports"
PROXIMITY_JSON = REPORTS / "live_crypto_trigger_proximity_board.json"
STEP_ATR_JSON = REPORTS / "live_crypto_step_atr_quality_board.json"
OUTPUT_JSON = REPORTS / "live_crypto_first_fill_pressure_board.json"
OUTPUT_MD = REPORTS / "live_crypto_first_fill_pressure_board.md"


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


def safe_float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def classify_pressure_band(gap_reference_atr_ratio: float | None) -> str:
    if gap_reference_atr_ratio is None:
        return "missing_reference_atr"
    if gap_reference_atr_ratio <= 0.75:
        return "sub_atr_move_needed"
    if gap_reference_atr_ratio <= 1.25:
        return "about_one_atr_move_needed"
    if gap_reference_atr_ratio <= 2.0:
        return "one_to_two_atr_move_needed"
    return "multi_atr_move_needed"


def classify_priority(*, quality_band: str, pressure_band: str, authority_status: str) -> str:
    if authority_status:
        return "authority_hygiene_before_retune"
    if quality_band == "preferred_atr_band" and pressure_band in {
        "sub_atr_move_needed",
        "about_one_atr_move_needed",
    }:
        return "highest_near_term_watch"
    if quality_band == "borderline_between_floor_and_preferred" and pressure_band in {
        "sub_atr_move_needed",
        "about_one_atr_move_needed",
    }:
        return "secondary_near_term_watch"
    if quality_band == "supra_atr_watch_for_overwide_contract":
        return "nearest_in_steps_but_not_lightest_move"
    if quality_band == "preferred_atr_band":
        return "hold_contract_wait_for_larger_move"
    return "monitor_without_retune_claim"


def build_payload() -> dict[str, Any]:
    proximity_payload = load_json(PROXIMITY_JSON)
    step_atr_payload = load_json(STEP_ATR_JSON)
    proximity_rows = proximity_payload.get("rows") if isinstance(proximity_payload.get("rows"), list) else []
    step_rows = step_atr_payload.get("rows") if isinstance(step_atr_payload.get("rows"), list) else []
    step_by_symbol = {
        str(row.get("symbol") or ""): row
        for row in step_rows
        if isinstance(row, dict) and str(row.get("symbol") or "").strip()
    }

    rows: list[dict[str, Any]] = []
    for proximity_row in proximity_rows:
        if not isinstance(proximity_row, dict):
            continue
        symbol = str(proximity_row.get("symbol") or "").strip()
        if not symbol:
            continue
        step_row = step_by_symbol.get(symbol, {})
        bid = safe_float(proximity_row.get("bid")) or 0.0
        ask = safe_float(proximity_row.get("ask")) or 0.0
        mid = (bid + ask) / 2.0 if bid > 0.0 and ask > 0.0 else 0.0
        nearest_gap_px = safe_float(proximity_row.get("nearest_gap_px"))
        reference_atr = safe_float(step_row.get("reference_atr"))
        gap_reference_atr_ratio = (
            nearest_gap_px / reference_atr
            if nearest_gap_px is not None and reference_atr not in (None, 0.0)
            else None
        )
        move_pct_of_mid = (
            (nearest_gap_px / mid) * 100.0
            if nearest_gap_px is not None and mid > 0.0
            else None
        )
        pressure_band = classify_pressure_band(gap_reference_atr_ratio)
        quality_band = str(step_row.get("quality_band") or "")
        authority_status = str(step_row.get("authority_status") or "")
        priority = classify_priority(
            quality_band=quality_band,
            pressure_band=pressure_band,
            authority_status=authority_status,
        )
        rows.append(
            {
                "symbol": symbol,
                "lane": str(proximity_row.get("lane") or ""),
                "nearest_side": str(proximity_row.get("nearest_side") or ""),
                "nearest_gap_steps": safe_float(proximity_row.get("nearest_gap_steps")),
                "nearest_gap_px": nearest_gap_px,
                "reference_atr": reference_atr,
                "gap_reference_atr_ratio": gap_reference_atr_ratio,
                "move_pct_of_mid": move_pct_of_mid,
                "pressure_band": pressure_band,
                "quality_band": quality_band,
                "authority_status": authority_status,
                "priority": priority,
                "execution_read": str(proximity_row.get("execution_read") or ""),
                "spread_gate_status": str(proximity_row.get("spread_gate_status") or ""),
            }
        )

    rows.sort(
        key=lambda row: (
            safe_float(row.get("gap_reference_atr_ratio"))
            if safe_float(row.get("gap_reference_atr_ratio")) is not None
            else 9999.0,
            str(row.get("symbol") or ""),
        )
    )

    atr_watch_order = [str(row.get("symbol") or "") for row in rows]
    current_read: list[str] = []
    if rows:
        leader = rows[0]
        current_read.append(
            "ATR-normalized first-fill pressure says the nearest likely monetization watch is "
            f"{leader['symbol']} on the {leader['nearest_side']} side at "
            f"{leader['gap_reference_atr_ratio']:.2f} ATR of required move."
        )
    if len(rows) >= 2:
        first = rows[0]
        second = rows[1]
        current_read.append(
            f"Top ATR-pressure watch pair is {first['symbol']} then {second['symbol']}; both need materially less move than the rest of the crypto probe pack."
        )
    if rows:
        step_leader = str(proximity_payload.get("summary", {}).get("nearest_symbol") or "")
        atr_leader = str(rows[0].get("symbol") or "")
        if step_leader and atr_leader and step_leader != atr_leader:
            current_read.append(
                f"Step-space and ATR-space do not currently agree: raw trigger proximity says {step_leader} is closest, but ATR-normalized pressure says {atr_leader} is closer to likely first monetization."
            )
    ada_row = next((row for row in rows if row.get("symbol") == "ADAUSD"), None)
    if ada_row is not None:
        current_read.append(
            f"ADA remains the nearest in step space, but it still needs about {ada_row['gap_reference_atr_ratio']:.2f} ATR of move, so its {ada_row['priority']} label should not be confused with easiest next fill."
        )
    eth_row = next((row for row in rows if row.get("symbol") == "ETHUSD"), None)
    if eth_row is not None and eth_row.get("authority_status"):
        current_read.append(
            "ETH may be the lightest current move in ATR terms, but it stays non-actionable for retune until the ETH control-vs-variant authority conflict is resolved."
        )

    return {
        "generated_at": utc_now_iso(),
        "source_generated_at": {
            "proximity": str(proximity_payload.get("generated_at") or ""),
            "step_atr": str(step_atr_payload.get("generated_at") or ""),
        },
        "summary": {
            "row_count": len(rows),
            "atr_watch_order": atr_watch_order,
            "step_space_leader": str(proximity_payload.get("summary", {}).get("nearest_symbol") or ""),
            "atr_space_leader": atr_watch_order[0] if atr_watch_order else "",
        },
        "current_read": current_read,
        "rows": rows,
    }


def build_markdown(payload: dict[str, Any]) -> str:
    summary = payload.get("summary") if isinstance(payload.get("summary"), dict) else {}
    rows = payload.get("rows") if isinstance(payload.get("rows"), list) else []
    lines = [
        "# Live Crypto First Fill Pressure Board",
        "",
        "> Current runtime generated board.",
        "",
        f"Generated: `{payload.get('generated_at', '-')}`",
        f"Proximity source: `{payload.get('source_generated_at', {}).get('proximity', '-')}`",
        f"Step-ATR source: `{payload.get('source_generated_at', {}).get('step_atr', '-')}`",
        "",
        "## Current Read",
        "",
    ]
    for line in payload.get("current_read") or []:
        lines.append(f"- {line}")

    lines.extend(
        [
            "",
            "## Summary",
            "",
            f"- Rows: `{summary.get('row_count', 0)}`",
            f"- ATR watch order: `{' -> '.join(summary.get('atr_watch_order') or [])}`",
            f"- Step-space leader: `{summary.get('step_space_leader') or '-'}`",
            f"- ATR-space leader: `{summary.get('atr_space_leader') or '-'}`",
            "",
            "## Rows",
            "",
            "| Symbol | Side | Gap steps | Gap px | Gap / ATR | Move % mid | Pressure band | Step quality | Priority |",
            "|---|---|---:|---:|---:|---:|---|---|---|",
        ]
    )

    for row in rows:
        gap_steps = safe_float(row.get("nearest_gap_steps"))
        gap_px = safe_float(row.get("nearest_gap_px"))
        gap_atr = safe_float(row.get("gap_reference_atr_ratio"))
        move_pct = safe_float(row.get("move_pct_of_mid"))
        lines.append(
            "| `{symbol}` | `{side}` | {gap_steps} | {gap_px} | {gap_atr} | {move_pct} | `{pressure}` | `{quality}` | `{priority}` |".format(
                symbol=row.get("symbol") or "-",
                side=row.get("nearest_side") or "-",
                gap_steps=f"{gap_steps:.3f}" if gap_steps is not None else "-",
                gap_px=f"{gap_px:.6f}" if gap_px is not None else "-",
                gap_atr=f"{gap_atr:.3f}x" if gap_atr is not None else "-",
                move_pct=f"{move_pct:.3f}%" if move_pct is not None else "-",
                pressure=row.get("pressure_band") or "-",
                quality=row.get("quality_band") or "-",
                priority=row.get("priority") or "-",
            )
        )

    lines.extend(
        [
            "",
            "## Notes",
            "",
            "- This board is a crypto-only fill-pressure surface layered on top of `reports/live_crypto_trigger_proximity_board.json` and `reports/live_crypto_step_atr_quality_board.json`.",
            "- Trigger proximity answers who is nearest in step space; this board answers who needs the smallest ATR-normalized move to fire next.",
            "- Use this board for crypto `$ / hr` prioritization during quiet windows before calling the nearest-step lane the best next monetization candidate.",
        ]
    )
    return "\n".join(lines) + "\n"


def main() -> None:
    payload = build_payload()
    OUTPUT_JSON.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    OUTPUT_MD.write_text(build_markdown(payload), encoding="utf-8")
    print(json.dumps(payload, indent=2))


if __name__ == "__main__":
    main()
