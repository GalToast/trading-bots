#!/usr/bin/env python3
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent.parent
REPORTS = ROOT / "reports"
ORGANISM_PATH = REPORTS / "organism_state.json"
EXECUTION_PATH = REPORTS / "execution_monitor_report.json"
OUTPUT_JSON = REPORTS / "booked_pnl_breakdown_board.json"
OUTPUT_MD = REPORTS / "booked_pnl_breakdown_board.md"

SHADOW_LATTICE_KINDS = {
    "shadow_fx",
    "shadow_crypto",
    "shadow_crypto_candidate",
    "shadow_fx_m15_bar",
}


def load_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


def safe_float(value: Any) -> float:
    if value in ("", None):
        return 0.0
    try:
        return float(value)
    except Exception:
        return 0.0


def safe_int(value: Any) -> int:
    if value in ("", None):
        return 0
    try:
        return int(float(value))
    except Exception:
        return 0


def sort_rows(rows: list[dict[str, Any]], key: str, reverse: bool = True) -> list[dict[str, Any]]:
    return sorted(rows, key=lambda row: float(row.get(key, 0.0) or 0.0), reverse=reverse)


def top_rows(rows: list[dict[str, Any]], *, count: int = 5, descending: bool = True) -> list[dict[str, Any]]:
    return sort_rows(rows, "booked_usd", reverse=descending)[:count]


def build_live_rows(organism_payload: dict[str, Any]) -> list[dict[str, Any]]:
    live_rows = organism_payload.get("live_lanes")
    if not isinstance(live_rows, list):
        return []
    built: list[dict[str, Any]] = []
    for row in live_rows:
        if not isinstance(row, dict):
            continue
        built.append(
            {
                "lane": str(row.get("lane") or ""),
                "kind": str(row.get("kind") or ""),
                "booked_usd": safe_float(row.get("realized_usd")),
                "open_count": safe_int(row.get("open_count")),
                "close_count": safe_int(row.get("closes")),
                "watchdog_status": str(row.get("watchdog_status") or ""),
                "notes": str(row.get("notes") or ""),
            }
        )
    return built


def build_shadow_lattice_rows(execution_payload: dict[str, Any]) -> list[dict[str, Any]]:
    rows = execution_payload.get("rows")
    if not isinstance(rows, list):
        return []
    built: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        kind = str(row.get("kind") or "")
        if kind not in SHADOW_LATTICE_KINDS:
            continue
        built.append(
            {
                "lane": str(row.get("lane") or ""),
                "kind": kind,
                "booked_usd": safe_float(row.get("pre_start_state_carry_realized_usd")),
                "runner_session_booked_usd": safe_float(row.get("runner_session_trade_realized_usd")),
                "clean_forward_delta_usd": safe_float(row.get("clean_forward_realized_delta_usd")),
                "open_count": safe_int(row.get("open_count")),
                "close_count": safe_int(row.get("close_count")),
                "watchdog_status": str(row.get("watchdog_status") or ""),
                "notes": str(row.get("notes") or ""),
            }
        )
    return built


def build_coinbase_rows(organism_payload: dict[str, Any]) -> list[dict[str, Any]]:
    rows = organism_payload.get("forward_triage")
    if not isinstance(rows, list):
        return []
    built: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        kind = str(row.get("kind") or "")
        if not kind.startswith("shadow_coinbase"):
            continue
        built.append(
            {
                "lane": str(row.get("lane") or ""),
                "kind": kind,
                "booked_usd": safe_float(row.get("realized_net_usd")),
                "open_count": safe_int(row.get("open_count")),
                "close_count": safe_int(row.get("closes")),
                "forward_status": str(row.get("forward_status") or ""),
                "action": str(row.get("action") or ""),
                "notes": str(row.get("notes") or ""),
            }
        )
    return built


def sum_booked(rows: list[dict[str, Any]]) -> float:
    return round(sum(float(row.get("booked_usd", 0.0) or 0.0) for row in rows), 2)


def build_payload(
    *,
    now: datetime | None = None,
    organism_payload: dict[str, Any] | None = None,
    execution_payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    now = now or datetime.now(timezone.utc)
    organism_payload = organism_payload if organism_payload is not None else load_json(ORGANISM_PATH)
    execution_payload = execution_payload if execution_payload is not None else load_json(EXECUTION_PATH)

    live_rows = build_live_rows(organism_payload)
    shadow_lattice_rows = build_shadow_lattice_rows(execution_payload)
    coinbase_rows = build_coinbase_rows(organism_payload)

    shadow_lattice_active_rows = [row for row in shadow_lattice_rows if row.get("watchdog_status") == "ok"]
    shadow_fx_rows = [row for row in shadow_lattice_rows if row.get("kind") == "shadow_fx"]
    shadow_fx_active_rows = [row for row in shadow_fx_rows if row.get("watchdog_status") == "ok"]
    shadow_crypto_rows = [row for row in shadow_lattice_rows if row.get("kind") in {"shadow_crypto", "shadow_crypto_candidate"}]
    shadow_crypto_active_rows = [row for row in shadow_crypto_rows if row.get("watchdog_status") == "ok"]

    live_total = sum_booked(live_rows)
    shadow_lattice_total = sum_booked(shadow_lattice_rows)
    shadow_lattice_active_total = sum_booked(shadow_lattice_active_rows)
    coinbase_total = sum_booked(coinbase_rows)
    combined_shadow_total = round(shadow_lattice_total + coinbase_total, 2)
    combined_shadow_active_proxy = round(shadow_lattice_active_total + coinbase_total, 2)

    if combined_shadow_total > 0:
        verdict = "shadow_book_positive_with_family_split"
    elif combined_shadow_active_proxy > 0:
        verdict = "historical_shadow_drag_dominates_active_book"
    else:
        verdict = "shadow_book_negative"

    summary = {
        "live_total_booked_usd": live_total,
        "shadow_lattice_total_booked_proxy_usd": shadow_lattice_total,
        "shadow_lattice_active_total_booked_proxy_usd": shadow_lattice_active_total,
        "shadow_fx_total_booked_proxy_usd": sum_booked(shadow_fx_rows),
        "shadow_fx_active_total_booked_proxy_usd": sum_booked(shadow_fx_active_rows),
        "shadow_crypto_total_booked_proxy_usd": sum_booked(shadow_crypto_rows),
        "shadow_crypto_active_total_booked_proxy_usd": sum_booked(shadow_crypto_active_rows),
        "shadow_coinbase_total_booked_usd": coinbase_total,
        "combined_shadow_total_mixed_basis_usd": combined_shadow_total,
        "combined_shadow_active_plus_coinbase_mixed_basis_usd": combined_shadow_active_proxy,
        "live_lane_count": len(live_rows),
        "shadow_lattice_lane_count": len(shadow_lattice_rows),
        "shadow_coinbase_lane_count": len(coinbase_rows),
    }

    return {
        "generated_at": now.isoformat(),
        "readiness": verdict,
        "summary": summary,
        "methodology": {
            "live": "Exact booked realized P/L from organism_state live lanes (`realized_usd`).",
            "shadow_lattice": "Booked proxy from execution_monitor `pre_start_state_carry_realized_usd`; this is the repo's closest uniform booked field across FX/crypto shadow lattice families.",
            "shadow_coinbase": "Booked realized P/L from organism_state forward_triage `realized_net_usd` for Coinbase shadow families.",
        },
        "read_rules": [
            "Live booked totals are exact current realized fields. Shadow totals are split because the repo does not emit one uniform booked field across all shadow families.",
            "Read `shadow_lattice_*_booked_proxy_usd` as the best current booked approximation for FX/crypto lattice shadows, not as a broker-style realized ledger.",
            "Use the active shadow subtotals to separate current live research posture from older quarantined, stale, or historical loser rows that still drag the broader shadow book.",
        ],
        "live": {
            "rows": sort_rows(live_rows, "booked_usd", reverse=True),
            "top_winners": top_rows(live_rows, count=5, descending=True),
        },
        "shadow_lattice": {
            "rows": sort_rows(shadow_lattice_rows, "booked_usd", reverse=True),
            "active_rows": sort_rows(shadow_lattice_active_rows, "booked_usd", reverse=True),
            "fx_top": top_rows(shadow_fx_rows, count=5, descending=True),
            "fx_bottom": top_rows(shadow_fx_rows, count=5, descending=False),
            "crypto_top": top_rows(shadow_crypto_rows, count=5, descending=True),
            "crypto_bottom": top_rows(shadow_crypto_rows, count=5, descending=False),
        },
        "shadow_coinbase": {
            "rows": sort_rows(coinbase_rows, "booked_usd", reverse=True),
            "top": top_rows(coinbase_rows, count=5, descending=True),
            "bottom": top_rows(coinbase_rows, count=5, descending=False),
        },
    }


def money(value: float) -> str:
    sign = "+" if value > 0 else ""
    return f"{sign}{value:,.2f}"


def render_row_table(rows: list[dict[str, Any]], *, include_status: bool = True) -> list[str]:
    header = "| Lane | Kind | Booked USD | Closes | Open |"
    divider = "| --- | --- | ---: | ---: | ---: |"
    if include_status:
        header = "| Lane | Kind | Booked USD | Closes | Open | Status |"
        divider = "| --- | --- | ---: | ---: | ---: | --- |"
    lines = [header, divider]
    for row in rows:
        base = (
            f"| `{row.get('lane', '')}` | `{row.get('kind', '')}` | `{money(float(row.get('booked_usd', 0.0) or 0.0))}` | "
            f"`{safe_int(row.get('close_count'))}` | `{safe_int(row.get('open_count'))}` |"
        )
        if include_status:
            status = row.get("watchdog_status") or row.get("forward_status") or "-"
            base = base + f" `{status}` |"
        lines.append(base)
    return lines


def render_markdown(payload: dict[str, Any]) -> str:
    summary = payload.get("summary") if isinstance(payload.get("summary"), dict) else {}
    live = payload.get("live") if isinstance(payload.get("live"), dict) else {}
    shadow_lattice = payload.get("shadow_lattice") if isinstance(payload.get("shadow_lattice"), dict) else {}
    shadow_coinbase = payload.get("shadow_coinbase") if isinstance(payload.get("shadow_coinbase"), dict) else {}

    lines = [
        "# Booked P/L Breakdown Board",
        "",
        "> Runtime/operator board for the current booked P/L answer across live lanes and the repo's mixed shadow families.",
        "> Purpose: stop answering booked P/L from memory or mixed board arithmetic when live fields are exact but shadow families are not uniform.",
        "",
        f"- generated_at: `{payload.get('generated_at', '')}`",
        f"- readiness: `{payload.get('readiness', '')}`",
        "",
        "## Leadership Read",
        "",
        f"- Live booked P/L is `{money(float(summary.get('live_total_booked_usd', 0.0) or 0.0))}` across `{int(summary.get('live_lane_count', 0) or 0)}` enabled live lanes.",
        f"- Shadow lattice booked proxy is `{money(float(summary.get('shadow_lattice_total_booked_proxy_usd', 0.0) or 0.0))}` overall, but `{money(float(summary.get('shadow_lattice_active_total_booked_proxy_usd', 0.0) or 0.0))}` for the current watchdog-`ok` subset.",
        f"- Shadow Coinbase booked P/L is `{money(float(summary.get('shadow_coinbase_total_booked_usd', 0.0) or 0.0))}`.",
        f"- Mixed-basis combined shadow total is `{money(float(summary.get('combined_shadow_total_mixed_basis_usd', 0.0) or 0.0))}`; active lattice plus Coinbase is `{money(float(summary.get('combined_shadow_active_plus_coinbase_mixed_basis_usd', 0.0) or 0.0))}`.",
        "",
        "## Summary",
        "",
        f"- live_total_booked_usd: `{money(float(summary.get('live_total_booked_usd', 0.0) or 0.0))}`",
        f"- shadow_lattice_total_booked_proxy_usd: `{money(float(summary.get('shadow_lattice_total_booked_proxy_usd', 0.0) or 0.0))}`",
        f"- shadow_lattice_active_total_booked_proxy_usd: `{money(float(summary.get('shadow_lattice_active_total_booked_proxy_usd', 0.0) or 0.0))}`",
        f"- shadow_fx_total_booked_proxy_usd: `{money(float(summary.get('shadow_fx_total_booked_proxy_usd', 0.0) or 0.0))}`",
        f"- shadow_fx_active_total_booked_proxy_usd: `{money(float(summary.get('shadow_fx_active_total_booked_proxy_usd', 0.0) or 0.0))}`",
        f"- shadow_crypto_total_booked_proxy_usd: `{money(float(summary.get('shadow_crypto_total_booked_proxy_usd', 0.0) or 0.0))}`",
        f"- shadow_crypto_active_total_booked_proxy_usd: `{money(float(summary.get('shadow_crypto_active_total_booked_proxy_usd', 0.0) or 0.0))}`",
        f"- shadow_coinbase_total_booked_usd: `{money(float(summary.get('shadow_coinbase_total_booked_usd', 0.0) or 0.0))}`",
        "",
        "## Methodology",
        "",
    ]
    methodology = payload.get("methodology") if isinstance(payload.get("methodology"), dict) else {}
    for key in ("live", "shadow_lattice", "shadow_coinbase"):
        lines.append(f"- `{key}`: {methodology.get(key, '')}")

    lines.extend(["", "## Live Lanes", ""])
    lines.extend(render_row_table(list(live.get("rows") or [])))

    lines.extend(["", "## Shadow FX Winners", ""])
    lines.extend(render_row_table(list(shadow_lattice.get("fx_top") or [])))

    lines.extend(["", "## Shadow FX Drag", ""])
    lines.extend(render_row_table(list(shadow_lattice.get("fx_bottom") or [])))

    lines.extend(["", "## Shadow Crypto Winners", ""])
    lines.extend(render_row_table(list(shadow_lattice.get("crypto_top") or [])))

    lines.extend(["", "## Shadow Crypto Drag", ""])
    lines.extend(render_row_table(list(shadow_lattice.get("crypto_bottom") or [])))

    lines.extend(["", "## Shadow Coinbase Winners", ""])
    lines.extend(render_row_table(list(shadow_coinbase.get("top") or []), include_status=True))

    lines.extend(["", "## Shadow Coinbase Drag", ""])
    lines.extend(render_row_table(list(shadow_coinbase.get("bottom") or []), include_status=True))

    lines.extend(["", "## Read Rules", ""])
    for rule in list(payload.get("read_rules") or []):
        lines.append(f"- {rule}")
    lines.append("")
    return "\n".join(lines)


def main() -> int:
    payload = build_payload()
    OUTPUT_JSON.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    OUTPUT_MD.write_text(render_markdown(payload), encoding="utf-8")
    print(f"wrote {OUTPUT_JSON}")
    print(f"wrote {OUTPUT_MD}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
