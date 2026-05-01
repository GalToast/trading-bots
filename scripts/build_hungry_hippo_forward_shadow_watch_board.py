#!/usr/bin/env python3
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent.parent
REPORTS = ROOT / "reports"

PORTABILITY_BOARD_PATH = REPORTS / "hungry_hippo_symbol_portability_board.json"
LAUNCH_SAFETY_PATH = REPORTS / "hungry_hippo_launch_safety_validation.json"
OUTPUT_JSON_PATH = REPORTS / "hungry_hippo_forward_shadow_watch_board.json"
OUTPUT_MD_PATH = REPORTS / "hungry_hippo_forward_shadow_watch_board.md"

VERDICT_PRIORITY = {"pass": 0, "research_only": 1, "fail": 2}
CLOSE_LIKE_ACTIONS = {"close_ticket", "forced_unwind"}


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def utc_now_iso() -> str:
    return utc_now().isoformat()


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def load_optional_json(path: Path) -> Any | None:
    if not path.exists():
        return None
    return load_json(path)


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if not path.exists():
        return rows
    for line in path.read_text(encoding="utf-8").splitlines():
        text = line.strip()
        if not text:
            continue
        rows.append(json.loads(text))
    return rows


def parse_iso8601(text: Any) -> datetime | None:
    raw = str(text or "").strip()
    if not raw:
        return None
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None


def relative_path_text(path: Path) -> str:
    try:
        return str(path.relative_to(ROOT))
    except ValueError:
        return str(path)


def choose_contract_row(rows: list[dict[str, Any]], symbol: str) -> dict[str, Any] | None:
    candidates = [
        row
        for row in rows
        if str(row.get("symbol") or "").upper() == symbol.upper()
        and str(row.get("scope") or "") != "live_surface"
        and str(row.get("runner_family") or "") not in {"config_surface", "unknown"}
        and str(row.get("verdict") or "") != "fail"
    ]
    if not candidates:
        return None
    candidates.sort(
        key=lambda row: (
            VERDICT_PRIORITY.get(str(row.get("verdict") or ""), 9),
            0 if bool(row.get("enabled")) else 1,
            str(row.get("config_path") or ""),
        )
    )
    return dict(candidates[0])


def event_summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    open_count = 0
    close_like_count = 0
    first_open_at = ""
    first_close_like_at = ""
    last_event_at = ""
    for row in rows:
        action = str(row.get("action") or "")
        ts_utc = str(row.get("ts_utc") or "")
        if ts_utc:
            last_event_at = ts_utc
        if action == "open_ticket":
            open_count += 1
            if not first_open_at and ts_utc:
                first_open_at = ts_utc
        if action in CLOSE_LIKE_ACTIONS or action.startswith("escape_"):
            close_like_count += 1
            if not first_close_like_at and ts_utc:
                first_close_like_at = ts_utc
    return {
        "event_open_count": open_count,
        "event_close_like_count": close_like_count,
        "first_open_at": first_open_at,
        "first_close_like_at": first_close_like_at,
        "last_event_at": last_event_at,
    }


def summarize_runtime_state(
    *,
    symbol: str,
    config_payload: dict[str, Any],
    state_payload: dict[str, Any] | None,
    event_rows: list[dict[str, Any]],
    now: datetime | None = None,
) -> dict[str, Any]:
    now = now or utc_now()
    runner = dict((state_payload or {}).get("runner") or {})
    symbol_state = dict(((state_payload or {}).get("symbols") or {}).get(symbol) or {})
    state_exists = state_payload is not None
    event_exists = bool(event_rows)
    event_stats = event_summary(event_rows)
    open_tickets = list(symbol_state.get("open_tickets") or [])
    current_open_count = len(open_tickets)
    realized_closes = int(symbol_state.get("realized_closes") or 0)
    if realized_closes <= 0:
        realized_closes = int(event_stats["event_close_like_count"])
    realized_net_usd = float(symbol_state.get("realized_net_usd") or 0.0)
    heartbeat_at = str(runner.get("heartbeat_at") or "")
    heartbeat_dt = parse_iso8601(heartbeat_at)
    stale_after_seconds = float(config_payload.get("stale_after_seconds") or 0.0)
    heartbeat_age_seconds = None
    runtime_stale = False
    if heartbeat_dt is not None:
        heartbeat_age_seconds = max((now - heartbeat_dt).total_seconds(), 0.0)
        runtime_stale = stale_after_seconds > 0 and heartbeat_age_seconds > stale_after_seconds
    proof_started = realized_closes > 0 or int(event_stats["event_close_like_count"]) > 0
    if not state_exists and not event_exists:
        runtime_state = "not_launched_yet"
    elif proof_started:
        runtime_state = "forward_proof_started"
    elif current_open_count > 0 or int(event_stats["event_open_count"]) > 0:
        runtime_state = "launched_waiting_first_close"
    else:
        runtime_state = "launched_waiting_first_open"
    return {
        "runtime_state": runtime_state,
        "proof_started": proof_started,
        "state_exists": state_exists,
        "event_exists": event_exists,
        "current_open_count": current_open_count,
        "realized_closes": realized_closes,
        "realized_net_usd": round(realized_net_usd, 2),
        "heartbeat_at": heartbeat_at,
        "heartbeat_age_seconds": None if heartbeat_age_seconds is None else round(heartbeat_age_seconds, 1),
        "runtime_stale": runtime_stale,
        **event_stats,
    }


def next_action_for_runtime_state(runtime_state: str, runtime_stale: bool) -> str:
    if runtime_state == "not_launched_yet":
        return "No state or event file exists yet. Keep the contract parked until an owner explicitly starts the lane, then switch this board to proof monitoring."
    if runtime_state == "forward_proof_started":
        return "A real close-like path exists now. Read the first forward sample before changing policy or contract geometry."
    if runtime_stale:
        return "Runtime wrote state before but the heartbeat is now stale. Verify whether the lane is intentionally parked or needs a controlled relaunch before judging proof."
    if runtime_state == "launched_waiting_first_close":
        return "The lane has opened a path but not closed one yet. Wait for the first close-like event before judging quality."
    return "The contract has written runtime state but has not opened yet. Keep monitoring for the first open before reading proof quality."


def build_row(
    portability_row: dict[str, Any],
    launch_contract_row: dict[str, Any],
    now: datetime | None = None,
) -> dict[str, Any]:
    now = now or utc_now()
    symbol = str(portability_row.get("symbol") or "")
    config_path = ROOT / Path(str(launch_contract_row.get("config_path") or "").replace("\\", "/"))
    config_payload = load_json(config_path)
    state_path = ROOT / Path(str(config_payload.get("state_path") or "").replace("\\", "/"))
    event_path = ROOT / Path(str(config_payload.get("event_path") or "").replace("\\", "/"))
    state_payload = load_optional_json(state_path)
    event_rows = load_jsonl(event_path)
    runtime = summarize_runtime_state(
        symbol=symbol,
        config_payload=config_payload,
        state_payload=state_payload,
        event_rows=event_rows,
        now=now,
    )
    return {
        "symbol": symbol,
        "asset_class": str(portability_row.get("asset_class") or ""),
        "generalization_status": str(portability_row.get("generalization_status") or ""),
        "highest_leverage_gap": str(portability_row.get("highest_leverage_gap") or ""),
        "deployment_verdict": str(portability_row.get("deployment_verdict") or ""),
        "guardrail_status": str(portability_row.get("guardrail_status") or ""),
        "config_path": relative_path_text(config_path),
        "validation_verdict": str(launch_contract_row.get("verdict") or ""),
        "enabled": bool(config_payload.get("enabled")),
        "pause_note": str(config_payload.get("pause_note") or ""),
        "watchdog_group": str(config_payload.get("watchdog_group") or ""),
        "runner_family": str(launch_contract_row.get("runner_family") or ""),
        "state_path": relative_path_text(state_path),
        "event_path": relative_path_text(event_path),
        "next_action": next_action_for_runtime_state(str(runtime["runtime_state"]), bool(runtime["runtime_stale"])),
        **runtime,
    }


def build_payload(
    portability_payload: dict[str, Any],
    launch_safety_payload: dict[str, Any],
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    now = now or utc_now()
    portability_rows = [
        dict(row)
        for row in list(portability_payload.get("rows") or [])
        if str(row.get("generalization_status") or "") == "ready_for_shadow_discussion"
    ]
    launch_rows = [dict(row) for row in list(launch_safety_payload.get("rows") or [])]
    rows: list[dict[str, Any]] = []
    for portability_row in portability_rows:
        symbol = str(portability_row.get("symbol") or "")
        launch_row = choose_contract_row(launch_rows, symbol)
        if launch_row is None:
            continue
        rows.append(build_row(portability_row, launch_row, now=now))

    rows.sort(key=lambda row: str(row.get("symbol") or ""))

    not_launched = [row["symbol"] for row in rows if row["runtime_state"] == "not_launched_yet"]
    waiting_first_open = [row["symbol"] for row in rows if row["runtime_state"] == "launched_waiting_first_open"]
    waiting_first_close = [row["symbol"] for row in rows if row["runtime_state"] == "launched_waiting_first_close"]
    proof_started = [row["symbol"] for row in rows if row["runtime_state"] == "forward_proof_started"]
    stale_runtime = [row["symbol"] for row in rows if row["runtime_stale"]]

    leadership_read = [
        f"Current Hungry Hippo portable-forward watch set is `{[row['symbol'] for row in rows] or ['none']}`.",
        (
            f"Fresh forward proof has started on `{proof_started}`."
            if proof_started
            else f"No watched Hungry Hippo portable-forward lane has produced a close-like event yet; current zero-proof state is `not_launched={not_launched or ['none']}`, `waiting_first_open={waiting_first_open or ['none']}`, `waiting_first_close={waiting_first_close or ['none']}`."
        ),
        (
            f"Runtime freshness needs review for `{stale_runtime}`."
            if stale_runtime
            else "Use this board to distinguish a parked contract from an actually running proof lane before drawing any breadth conclusions."
        ),
    ]

    return {
        "generated_at": now.isoformat(),
        "sources": [
            str(PORTABILITY_BOARD_PATH.relative_to(ROOT)),
            str(LAUNCH_SAFETY_PATH.relative_to(ROOT)),
        ],
        "summary": {
            "watch_symbol_count": len(rows),
            "not_launched_symbols": not_launched,
            "waiting_first_open_symbols": waiting_first_open,
            "waiting_first_close_symbols": waiting_first_close,
            "proof_started_symbols": proof_started,
            "stale_runtime_symbols": stale_runtime,
        },
        "leadership_read": leadership_read,
        "rows": rows,
        "notes": [
            "This is a forward-proof watch surface for current Hungry Hippo `ready_for_shadow_discussion` symbols only.",
            "It does not decide promotion; it tells the room whether a checked-in contract is still parked, has started opening, or has already produced first close-like proof.",
        ],
    }


def render_markdown(payload: dict[str, Any]) -> str:
    summary = dict(payload.get("summary") or {})
    lines = [
        "# Hungry Hippo Forward Shadow Watch Board",
        "",
        f"- Generated at: `{payload.get('generated_at', '')}`",
        "- Purpose: show whether the current Hungry Hippo `ready_for_shadow_discussion` contracts are still parked, have started opening, or have already produced first close-like forward proof.",
        "",
        "## Leadership Read",
        "",
    ]
    for line in list(payload.get("leadership_read") or []):
        lines.append(f"- {line}")

    lines.extend(
        [
            "",
            "## Summary",
            "",
            f"- Watched symbols: `{summary.get('watch_symbol_count', 0)}`",
            f"- Not launched: `{summary.get('not_launched_symbols', [])}`",
            f"- Waiting first open: `{summary.get('waiting_first_open_symbols', [])}`",
            f"- Waiting first close: `{summary.get('waiting_first_close_symbols', [])}`",
            f"- Proof started: `{summary.get('proof_started_symbols', [])}`",
            "",
            "## Rows",
            "",
            "| Symbol | Runtime State | Contract | Open Count | Close-Like Count | Realized Closes | Heartbeat |",
            "| --- | --- | --- | ---: | ---: | ---: | --- |",
        ]
    )
    for row in list(payload.get("rows") or []):
        heartbeat = str(row.get("heartbeat_at") or "") or "-"
        lines.append(
            "| "
            + " | ".join(
                [
                    str(row.get("symbol") or ""),
                    str(row.get("runtime_state") or ""),
                    str(row.get("config_path") or ""),
                    str(row.get("current_open_count") or 0),
                    str(row.get("event_close_like_count") or 0),
                    str(row.get("realized_closes") or 0),
                    heartbeat,
                ]
            )
            + " |"
        )

    lines.extend(["", "## Details", ""])
    for row in list(payload.get("rows") or []):
        lines.append(f"### {row['symbol']}")
        lines.append(f"- Runtime state: `{row['runtime_state']}`")
        lines.append(f"- Contract: `{row['config_path']}`")
        lines.append(f"- Gate / guardrail: `deployment_verdict={row['deployment_verdict']}`, `guardrail_status={row['guardrail_status']}`")
        lines.append(f"- State / event files: `state_exists={row['state_exists']}`, `event_exists={row['event_exists']}`")
        lines.append(
            f"- Current path facts: `current_open_count={row['current_open_count']}`, `event_open_count={row['event_open_count']}`, `event_close_like_count={row['event_close_like_count']}`, `realized_closes={row['realized_closes']}`, `realized_net_usd={row['realized_net_usd']}`"
        )
        lines.append(f"- Next action: {row['next_action']}")
        lines.append("")

    lines.extend(["## Notes", ""])
    for note in list(payload.get("notes") or []):
        lines.append(f"- {note}")

    return "\n".join(lines).rstrip() + "\n"


def write_outputs(payload: dict[str, Any]) -> None:
    OUTPUT_JSON_PATH.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    OUTPUT_MD_PATH.write_text(render_markdown(payload), encoding="utf-8")


def main() -> int:
    payload = build_payload(
        load_json(PORTABILITY_BOARD_PATH),
        load_json(LAUNCH_SAFETY_PATH),
    )
    write_outputs(payload)
    print(f"Wrote {OUTPUT_JSON_PATH}")
    print(f"Wrote {OUTPUT_MD_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
