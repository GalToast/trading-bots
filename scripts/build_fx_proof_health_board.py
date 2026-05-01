#!/usr/bin/env python3
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent.parent
REPORTS = ROOT / "reports"

LIVE_STATE_PATH = REPORTS / "penetration_lattice_live_source_state.json"
GBP_STATE_PATH = REPORTS / "shadow_gbpusd_tick_forward_state.json"
GBP_REPORT_PATH = REPORTS / "gbpusd_tick_forward_shadow.md"

JSON_PATH = REPORTS / "fx_proof_health_board.json"
MD_PATH = REPORTS / "fx_proof_health_board.md"


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def utc_now_iso() -> str:
    return utc_now().isoformat()


def parse_iso(value: str | None) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None


def age_hours_text(value: str | None) -> str:
    dt = parse_iso(value)
    if dt is None:
        return "-"
    age_hours = max(0.0, (utc_now() - dt).total_seconds() / 3600.0)
    return f"{age_hours:.2f}h"


def load_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


def load_text(path: Path) -> str:
    if not path.exists():
        return ""
    try:
        return path.read_text(encoding="utf-8")
    except Exception:
        return ""


def to_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except Exception:
        return default


def extract_marked_net(report_text: str) -> float | None:
    marker = "| Marked Net (USD) | $"
    for line in report_text.splitlines():
        if marker not in line:
            continue
        try:
            value = line.split(marker, 1)[1].split("|", 1)[0].strip().replace(",", "")
            return float(value)
        except Exception:
            return None
    return None


def build_live_reference_row() -> dict[str, Any]:
    payload = load_json(LIVE_STATE_PATH)
    runner = payload.get("runner") if isinstance(payload.get("runner"), dict) else {}
    metadata = payload.get("metadata") if isinstance(payload.get("metadata"), dict) else {}
    symbols = payload.get("symbols") if isinstance(payload.get("symbols"), dict) else {}
    eur = symbols.get("EURUSD") if isinstance(symbols.get("EURUSD"), dict) else {}
    gbp = symbols.get("GBPUSD") if isinstance(symbols.get("GBPUSD"), dict) else {}
    realized_closes = int(to_float(eur.get("realized_closes"))) + int(to_float(gbp.get("realized_closes")))
    realized_net = to_float(eur.get("realized_net_usd")) + to_float(gbp.get("realized_net_usd"))
    open_count = len(eur.get("open_tickets") or []) + len(gbp.get("open_tickets") or [])
    return {
        "lane_name": "live_rearm_941777",
        "role": "live_reference",
        "runner_status": "running" if str(runner.get("heartbeat_at") or "").strip() else "unknown",
        "heartbeat_at": str(runner.get("heartbeat_at") or ""),
        "proof_status": "graduated_live",
        "snapshot_closes": realized_closes,
        "durable_closes": realized_closes,
        "close_gap": 0,
        "snapshot_open": open_count,
        "durable_open": open_count,
        "open_gap": 0,
        "snapshot_net_usd": round(realized_net, 2),
        "durable_net_usd": round(realized_net, 2),
        "counter_regressed": False,
        "last_durable_seen_at": str(runner.get("heartbeat_at") or ""),
        "last_durable_seen_age": age_hours_text(str(runner.get("heartbeat_at") or "")),
        "note": (
            f"live conservative reference; alpha={metadata.get('raw_close_alpha')} "
            f"cooldown={metadata.get('raw_rearm_cooldown_bars')}"
        ),
    }


def build_gbp_shadow_row() -> dict[str, Any]:
    state = load_json(GBP_STATE_PATH)
    report_text = load_text(GBP_REPORT_PATH)
    runner = state.get("runner") if isinstance(state.get("runner"), dict) else {}
    symbols = state.get("symbols") if isinstance(state.get("symbols"), dict) else {}
    gbp = symbols.get("GBPUSD") if isinstance(symbols.get("GBPUSD"), dict) else {}
    durable = state.get("durable_proof") if isinstance(state.get("durable_proof"), dict) else {}

    snapshot_closes = int(to_float(gbp.get("realized_closes")))
    durable_closes = int(to_float(durable.get("durable_realized_closes")))
    snapshot_open = len(gbp.get("open_tickets") or [])
    durable_open = int(to_float(durable.get("durable_open_count")))
    snapshot_net = to_float(gbp.get("realized_net_usd"))
    durable_net = to_float(durable.get("durable_realized_net_usd"))
    marked_net = extract_marked_net(report_text)
    proof_status = "collecting"
    if durable_closes > 0:
        proof_status = "proof_positive" if durable_net > 0 else "proof_negative"

    note = "proof and snapshot aligned"
    if durable_closes > snapshot_closes or durable_open != snapshot_open or bool(durable.get("counter_regressed")):
        note = "snapshot behind durable proof; use durable ledger for graduation"
    elif proof_status == "proof_negative":
        note = "proof and snapshot aligned, but forward net is negative"
    if marked_net is not None:
        note += f"; marked={marked_net:+.2f}"

    return {
        "lane_name": "shadow_gbpusd_tick_forward",
        "role": "macro_candidate",
        "runner_status": "running" if str(runner.get("heartbeat_at") or "").strip() else "unknown",
        "heartbeat_at": str(runner.get("heartbeat_at") or ""),
        "proof_status": proof_status,
        "snapshot_closes": snapshot_closes,
        "durable_closes": durable_closes,
        "close_gap": durable_closes - snapshot_closes,
        "snapshot_open": snapshot_open,
        "durable_open": durable_open,
        "open_gap": durable_open - snapshot_open,
        "snapshot_net_usd": round(snapshot_net, 2),
        "durable_net_usd": round(durable_net, 2),
        "counter_regressed": bool(durable.get("counter_regressed")),
        "last_durable_seen_at": str(durable.get("last_seen_at") or ""),
        "last_durable_seen_age": age_hours_text(str(durable.get("last_seen_at") or "")),
        "note": note,
    }


def build_payload() -> dict[str, Any]:
    rows = [
        build_live_reference_row(),
        build_gbp_shadow_row(),
    ]
    return {
        "generated_at": utc_now_iso(),
        "summary": {
            "lanes": len(rows),
            "proof_positive_lanes": sum(1 for row in rows if row["proof_status"] == "proof_positive"),
            "divergent_lanes": sum(1 for row in rows if row["close_gap"] != 0 or row["open_gap"] != 0 or row["counter_regressed"]),
        },
        "rows": rows,
    }


def write_outputs(payload: dict[str, Any]) -> None:
    JSON_PATH.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    lines = [
        "# FX Proof Health Board",
        "",
        f"- Generated at: `{payload['generated_at']}`",
        (
            "- Summary: "
            f"`lanes={payload['summary']['lanes']}` "
            f"`proof_positive={payload['summary']['proof_positive_lanes']}` "
            f"`divergent={payload['summary']['divergent_lanes']}`"
        ),
        "",
        "| Lane | Role | Runner | Proof Status | Snapshot Closes | Durable Closes | Close Gap | Snapshot Open | Durable Open | Open Gap | Snapshot Net USD | Durable Net USD | Counter Regressed | Last Durable Seen | Age | Note |",
        "| --- | --- | --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- | --- | --- | --- |",
    ]
    for row in payload["rows"]:
        lines.append(
            f"| {row['lane_name']} | {row['role']} | {row['runner_status']} | {row['proof_status']} | "
            f"{row['snapshot_closes']} | {row['durable_closes']} | {row['close_gap']} | "
            f"{row['snapshot_open']} | {row['durable_open']} | {row['open_gap']} | "
            f"{row['snapshot_net_usd']:+.2f} | {row['durable_net_usd']:+.2f} | "
            f"{'yes' if row['counter_regressed'] else 'no'} | {row['last_durable_seen_at'] or '-'} | "
            f"{row['last_durable_seen_age']} | {row['note']} |"
        )
    MD_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    payload = build_payload()
    write_outputs(payload)
    print(
        json.dumps(
            {
                "json_path": str(JSON_PATH),
                "md_path": str(MD_PATH),
                "summary": payload["summary"],
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
