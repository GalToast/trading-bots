#!/usr/bin/env python3
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent.parent
REPORTS = ROOT / "reports"
PROXIMITY_JSON = REPORTS / "live_crypto_trigger_proximity_board.json"
ATR_OPTIMIZATION_JSON = REPORTS / "atr_step_optimization.json"
REGIME_LIVE_JSON = REPORTS / "regime_classification_live.json"
ETH_M5_COMPARISON_JSON = REPORTS / "eth_m5_first_pilot_comparison_board.json"
OUTPUT_JSON = REPORTS / "live_crypto_step_atr_quality_board.json"
OUTPUT_MD = REPORTS / "live_crypto_step_atr_quality_board.md"

TIMEFRAME_BY_SYMBOL = {
    "ETHUSD": "M5",
    "SOLUSD": "M15",
    "XRPUSD": "M15",
    "ADAUSD": "M15",
    "LTCUSD": "M15",
}


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


def atr_reference_rows() -> dict[tuple[str, str], dict[str, Any]]:
    payload = load_json(ATR_OPTIMIZATION_JSON)
    rows = payload.get("atr_data") if isinstance(payload.get("atr_data"), list) else []
    mapped: dict[tuple[str, str], dict[str, Any]] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        symbol = str(row.get("symbol") or "").strip()
        timeframe = str(row.get("tf") or "").strip()
        if symbol and timeframe:
            mapped[(symbol, timeframe)] = row
    return mapped


def regime_rows() -> dict[str, dict[str, Any]]:
    payload = load_json(REGIME_LIVE_JSON)
    rows = payload.get("symbols") if isinstance(payload.get("symbols"), list) else []
    mapped: dict[str, dict[str, Any]] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        symbol = str(row.get("symbol") or "").strip()
        if symbol:
            mapped[symbol] = row
    return mapped


def classify_ratio(ratio: float | None) -> tuple[str, str]:
    if ratio is None or ratio <= 0.0:
        return "missing_atr_reference", "needs_atr_reference"
    if ratio < 1.0:
        return "sub_atr_danger", "retune_step_up_before_live_confidence"
    if ratio < 1.5:
        return "borderline_between_floor_and_preferred", "monitor_or_widen_if_fill_quality_stays_thin"
    if ratio <= 3.0:
        return "preferred_atr_band", "hold_contract_and_wait_for_forward_proof"
    return "supra_atr_watch_for_overwide_contract", "monitor_fill_rate_before_calling_it_optimal"


def eth_authority_context() -> dict[str, Any]:
    payload = load_json(ETH_M5_COMPARISON_JSON)
    historical = payload.get("historical_baseline") if isinstance(payload.get("historical_baseline"), dict) else {}
    comparison_status = str(payload.get("comparison_status") or "")
    archival_conflict = bool(historical.get("archival_vs_current_conflict"))
    archival_read = str(historical.get("archival_probe_read") or "")
    leadership = payload.get("leadership_read") if isinstance(payload.get("leadership_read"), list) else []
    return {
        "comparison_status": comparison_status,
        "archival_conflict": archival_conflict,
        "archival_read": archival_read,
        "leadership_read": leadership,
    }


def build_payload() -> dict[str, Any]:
    proximity = load_json(PROXIMITY_JSON)
    proximity_rows = proximity.get("rows") if isinstance(proximity.get("rows"), list) else []
    atr_rows = atr_reference_rows()
    regime = regime_rows()
    eth_context = eth_authority_context()

    rows: list[dict[str, Any]] = []
    for row in proximity_rows:
        if not isinstance(row, dict):
            continue
        if str(row.get("evidence_basis") or "") == "decommissioned_or_parked":
            continue
        symbol = str(row.get("symbol") or "").strip()
        if not symbol:
            continue
        timeframe = TIMEFRAME_BY_SYMBOL.get(symbol, "")
        step_px = safe_float(row.get("step_px"))
        reference = atr_rows.get((symbol, timeframe), {})
        reference_atr = safe_float(reference.get("ATR"))
        regime_row = regime.get(symbol, {})
        live_regime_atr = safe_float(regime_row.get("current_atr"))
        step_atr_ratio = (step_px / reference_atr) if step_px and reference_atr else None
        quality_band, next_action = classify_ratio(step_atr_ratio)
        authority_status = ""
        authority_read = ""
        if (
            symbol == "ETHUSD"
            and quality_band == "sub_atr_danger"
            and eth_context.get("archival_conflict")
        ):
            authority_status = "historical_proof_conflicts_with_current_control_truth"
            authority_read = (
                "ETH M5 $5 shelf proof exists, but the current comparison board says it is archival context only; "
                "comparison hygiene is the honest next job before using that shelf proof as live retune authority."
            )
            next_action = "comparison_hygiene_before_live_regrade"
        rows.append(
            {
                "lane": str(row.get("lane") or ""),
                "symbol": symbol,
                "timeframe": timeframe,
                "status": str(row.get("status") or ""),
                "operator_posture": str(row.get("operator_posture") or ""),
                "execution_read": str(row.get("execution_read") or ""),
                "step_px": step_px,
                "reference_atr": reference_atr,
                "step_atr_ratio": step_atr_ratio,
                "reference_atr_generated_at": str(load_json(ATR_OPTIMIZATION_JSON).get("generated_at") or ""),
                "reference_source": "reports/atr_step_optimization.json",
                "reference_current_x_atr": safe_float(reference.get("current_x_ATR")),
                "live_regime_atr": live_regime_atr,
                "live_regime_atr_available": live_regime_atr is not None,
                "quality_band": quality_band,
                "next_action": next_action,
                "authority_status": authority_status,
                "authority_read": authority_read,
                "nearest_gap_steps": safe_float(row.get("nearest_gap_steps")),
                "spread_ratio": safe_float(row.get("spread_ratio")),
                "max_entry_spread_ratio": safe_float(row.get("max_entry_spread_ratio")),
            }
        )

    def _sort_key(item: dict[str, Any]) -> tuple[float, str]:
        ratio = safe_float(item.get("step_atr_ratio"))
        return (ratio if ratio is not None else 9999.0, str(item.get("symbol") or ""))

    rows.sort(key=_sort_key)

    sub_atr = [row for row in rows if row["quality_band"] == "sub_atr_danger"]
    borderline = [row for row in rows if row["quality_band"] == "borderline_between_floor_and_preferred"]
    preferred = [row for row in rows if row["quality_band"] == "preferred_atr_band"]
    supra = [row for row in rows if row["quality_band"] == "supra_atr_watch_for_overwide_contract"]

    current_read = [
        "This board compares current live crypto step size against the checked-in ATR reference surface by symbol/timeframe.",
        f"{len(preferred)}/{len(rows)} live crypto probes are inside the preferred 1.5x-3.0x ATR band.",
        f"{len(borderline)}/{len(rows)} are above the 1.0x danger floor but still below the preferred band.",
        f"{len(sub_atr)}/{len(rows)} are below the 1.0x ATR danger floor.",
    ]
    if supra:
        names = ", ".join(f"{row['symbol']} {row['step_atr_ratio']:.2f}x" for row in supra)
        current_read.append(f"Supra-ATR watch rows: {names}.")
    if sub_atr:
        names = ", ".join(f"{row['symbol']} {row['step_atr_ratio']:.2f}x" for row in sub_atr if row["step_atr_ratio"] is not None)
        current_read.append(f"Immediate step-quality concern: {names}.")
    eth_rows = [row for row in rows if row.get("symbol") == "ETHUSD" and row.get("authority_status")]
    if eth_rows:
        current_read.append(
            "ETH has an authority conflict: the live ratio reads sub-ATR against the checked-in ATR reference, but the dedicated ETH comparison board says the old $5 shelf proof is archival-only, so the next honest action is comparison hygiene rather than blind live retune."
        )

    return {
        "generated_at": utc_now_iso(),
        "reference_generated_at": str(load_json(ATR_OPTIMIZATION_JSON).get("generated_at") or ""),
        "summary": {
            "row_count": len(rows),
            "preferred_band_count": len(preferred),
            "borderline_count": len(borderline),
            "sub_atr_danger_count": len(sub_atr),
            "supra_atr_watch_count": len(supra),
        },
        "current_read": current_read,
        "rows": rows,
    }


def build_markdown(payload: dict[str, Any]) -> str:
    summary = payload.get("summary") if isinstance(payload.get("summary"), dict) else {}
    rows = payload.get("rows") if isinstance(payload.get("rows"), list) else []
    lines = [
        "# Live Crypto Step ATR Quality Board",
        "",
        "> Current runtime generated board.",
        "",
        f"Generated: `{payload.get('generated_at', '-')}`",
        f"ATR reference generated: `{payload.get('reference_generated_at', '-')}`",
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
            f"- Preferred 1.5x-3.0x ATR band: `{summary.get('preferred_band_count', 0)}`",
            f"- Borderline 1.0x-1.5x ATR: `{summary.get('borderline_count', 0)}`",
            f"- Below 1.0x ATR danger floor: `{summary.get('sub_atr_danger_count', 0)}`",
            f"- Supra-ATR watch rows: `{summary.get('supra_atr_watch_count', 0)}`",
            "",
            "## Rows",
            "",
            "| Symbol | TF | Lane | Step | Ref ATR | Step/ATR | Band | Next action | Authority | Execution | Trigger gap |",
            "|---|---|---|---:|---:|---:|---|---|---|---|---:|",
        ]
    )

    for row in rows:
        ratio = row.get("step_atr_ratio")
        lines.append(
            "| `{symbol}` | `{tf}` | `{lane}` | {step:.6f} | {atr:.6f} | {ratio_text} | `{band}` | `{action}` | `{authority}` | `{execution}` | {gap:.3f} |".format(
                symbol=row.get("symbol") or "-",
                tf=row.get("timeframe") or "-",
                lane=row.get("lane") or "-",
                step=float(row.get("step_px") or 0.0),
                atr=float(row.get("reference_atr") or 0.0),
                ratio_text=f"`{ratio:.3f}x`" if isinstance(ratio, (int, float)) else "`-`",
                band=row.get("quality_band") or "-",
                action=row.get("next_action") or "-",
                authority=row.get("authority_status") or "-",
                execution=row.get("execution_read") or "-",
                gap=float(row.get("nearest_gap_steps") or 0.0),
            )
        )

    lines.extend(
        [
            "",
            "## Notes",
            "",
            "- Primary ATR source is `reports/atr_step_optimization.json`, because it is the checked-in symbol-specific and timeframe-specific crypto ATR surface.",
            "- `reports/regime_classification_live.json` is read only as a secondary runtime hint because it is not consistently populated for the whole crypto probe pack.",
            "- ETH has one extra authority check from `reports/eth_m5_first_pilot_comparison_board.json`, because the old ETH M5 $5 proof is explicitly marked archival-only there.",
            "- Use this board together with `reports/live_crypto_trigger_proximity_board.md`: proximity answers 'which lane is nearest to firing now', while this board answers 'is the current step quality sane relative to ATR'.",
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
