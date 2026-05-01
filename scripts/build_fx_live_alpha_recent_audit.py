#!/usr/bin/env python3
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent.parent
REPORTS = ROOT / "reports"
CONFIGS = ROOT / "configs"

LIVE_STATE_PATH = REPORTS / "penetration_lattice_live_source_state.json"
LIVE_EVENT_PATH = REPORTS / "penetration_lattice_live_source_events.jsonl"
MOMENTUM_STATE_PATH = REPORTS / "penetration_lattice_live_momentum_alpha50_source_state.json"
REGISTRY_PATH = CONFIGS / "penetration_lattice_runner_registry.json"

JSON_PATH = REPORTS / "fx_live_alpha_recent_audit.json"
MD_PATH = REPORTS / "fx_live_alpha_recent_audit.md"

THIN_SAMPLE_CLOSES = 10


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


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        try:
            payload = json.loads(line)
        except Exception:
            continue
        if isinstance(payload, dict):
            rows.append(payload)
    return rows


def to_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except Exception:
        return default


def parse_iso(value: str | None) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None


def age_minutes_text(start_at: str | None, end_at: str | None) -> str:
    start = parse_iso(start_at)
    end = parse_iso(end_at)
    if start is None or end is None:
        return "-"
    minutes = max(0.0, (end - start).total_seconds() / 60.0)
    return f"{minutes:.1f}m"


def summarize_symbol_breakdown(symbols: dict[str, dict[str, Any]]) -> str:
    parts: list[str] = []
    for symbol in sorted(symbols):
        row = symbols[symbol]
        parts.append(
            f"{symbol} {int(to_float(row.get('close_count')))}c / ${to_float(row.get('net_usd')):+.2f}"
        )
    return "; ".join(parts) if parts else "-"


def open_total_from_state(payload: dict[str, Any]) -> int:
    symbols = payload.get("symbols") if isinstance(payload.get("symbols"), dict) else {}
    total = 0
    for symbol_state in symbols.values():
        if not isinstance(symbol_state, dict):
            continue
        total += len(symbol_state.get("open_tickets") or [])
    return total


def realized_close_total(payload: dict[str, Any]) -> int:
    symbols = payload.get("symbols") if isinstance(payload.get("symbols"), dict) else {}
    total = 0
    for symbol_state in symbols.values():
        if not isinstance(symbol_state, dict):
            continue
        total += int(to_float(symbol_state.get("realized_closes")))
    return total


def realized_net_total(payload: dict[str, Any]) -> float:
    symbols = payload.get("symbols") if isinstance(payload.get("symbols"), dict) else {}
    total = 0.0
    for symbol_state in symbols.values():
        if not isinstance(symbol_state, dict):
            continue
        total += to_float(symbol_state.get("realized_net_usd"))
    return total


def lane_registry_settings(payload: dict[str, Any], lane_name: str) -> dict[str, Any]:
    lanes = payload.get("lanes") if isinstance(payload.get("lanes"), list) else []
    lane = next((row for row in lanes if isinstance(row, dict) and row.get("name") == lane_name), {})
    args = lane.get("restart_args") if isinstance(lane.get("restart_args"), list) else []

    def arg_value(flag: str, default: str = "") -> str:
        for idx, value in enumerate(args):
            if value == flag and idx + 1 < len(args):
                return str(args[idx + 1])
        return default

    return {
        "name": lane_name,
        "raw_close_alpha": to_float(arg_value("--raw-close-alpha")),
        "raw_rearm_cooldown_bars": int(to_float(arg_value("--raw-rearm-cooldown-bars"))),
        "symbols": [
            str(value)
            for idx, value in enumerate(args)
            if idx > 0 and args[idx - 1] == "--symbols"
        ],
    }


def parse_recent_windows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    windows: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    last_event_at = ""

    for row in rows:
        action = str(row.get("action") or "")
        ts_utc = str(row.get("ts_utc") or "")
        if ts_utc:
            last_event_at = ts_utc
        if action == "fresh_start_prime":
            if current is not None:
                windows.append(current)
            current = {
                "start_at": ts_utc,
                "end_at": "",
                "raw_close_alpha": to_float(row.get("raw_close_alpha")),
                "raw_rearm_cooldown_bars": int(to_float(row.get("raw_rearm_cooldown_bars"))),
                "symbols": [str(symbol) for symbol in (row.get("symbols") or []) if str(symbol).strip()],
                "close_count": 0,
                "close_net_usd": 0.0,
                "open_count": 0,
                "last_close_at": "",
                "symbols_breakdown": {},
            }
            continue

        if current is None:
            continue

        if action == "open_ticket":
            current["open_count"] += 1
            continue

        if action != "close_ticket":
            continue

        symbol = str(row.get("symbol") or "")
        current["close_count"] += 1
        current["close_net_usd"] += to_float(row.get("realized_pnl"))
        current["last_close_at"] = ts_utc or current["last_close_at"]
        breakdown = current["symbols_breakdown"].setdefault(
            symbol,
            {"close_count": 0, "net_usd": 0.0},
        )
        breakdown["close_count"] += 1
        breakdown["net_usd"] += to_float(row.get("realized_pnl"))

    if current is not None:
        windows.append(current)

    for idx, window in enumerate(windows):
        next_start = windows[idx + 1]["start_at"] if idx + 1 < len(windows) else last_event_at
        window["end_at"] = next_start or window["start_at"]
        window["duration_text"] = age_minutes_text(window["start_at"], window["end_at"])
        window["symbol_breakdown_text"] = summarize_symbol_breakdown(window["symbols_breakdown"])
        window["sample_status"] = "thin_sample" if int(window["close_count"]) < THIN_SAMPLE_CLOSES else "sized_sample"

    return windows


def summarize_interval(
    rows: list[dict[str, Any]],
    start_at: str,
    end_at: str | None,
    alpha: float,
    cooldown_bars: int,
    symbols: list[str],
) -> dict[str, Any]:
    start_dt = parse_iso(start_at)
    end_dt = parse_iso(end_at) if end_at else None
    window = {
        "start_at": start_at,
        "end_at": end_at or "",
        "raw_close_alpha": alpha,
        "raw_rearm_cooldown_bars": cooldown_bars,
        "symbols": list(symbols),
        "close_count": 0,
        "close_net_usd": 0.0,
        "open_count": 0,
        "last_close_at": "",
        "symbols_breakdown": {},
    }
    for row in rows:
        ts_utc = str(row.get("ts_utc") or "")
        row_dt = parse_iso(ts_utc)
        if row_dt is None:
            continue
        if start_dt is not None and row_dt < start_dt:
            continue
        if end_dt is not None and row_dt >= end_dt:
            continue
        action = str(row.get("action") or "")
        if action == "open_ticket":
            window["open_count"] += 1
            continue
        if action != "close_ticket":
            continue
        symbol = str(row.get("symbol") or "")
        window["close_count"] += 1
        window["close_net_usd"] += to_float(row.get("realized_pnl"))
        window["last_close_at"] = ts_utc
        breakdown = window["symbols_breakdown"].setdefault(symbol, {"close_count": 0, "net_usd": 0.0})
        breakdown["close_count"] += 1
        breakdown["net_usd"] += to_float(row.get("realized_pnl"))
    window["end_at"] = end_at or window["last_close_at"] or start_at
    window["duration_text"] = age_minutes_text(window["start_at"], window["end_at"])
    window["symbol_breakdown_text"] = summarize_symbol_breakdown(window["symbols_breakdown"])
    window["sample_status"] = "thin_sample" if int(window["close_count"]) < THIN_SAMPLE_CLOSES else "sized_sample"
    return window


def summarize_close_block(close_rows: list[dict[str, Any]], alpha: float) -> dict[str, Any]:
    if not close_rows:
        return {}
    start_at = str(close_rows[0].get("ts_utc") or "")
    end_at = str(close_rows[-1].get("ts_utc") or "") or start_at
    window = {
        "start_at": start_at,
        "end_at": end_at,
        "raw_close_alpha": alpha,
        "raw_rearm_cooldown_bars": "",
        "symbols": sorted({str(row.get("symbol") or "") for row in close_rows if str(row.get("symbol") or "").strip()}),
        "close_count": 0,
        "close_net_usd": 0.0,
        "open_count": 0,
        "last_close_at": end_at,
        "symbols_breakdown": {},
    }
    for row in close_rows:
        symbol = str(row.get("symbol") or "")
        window["close_count"] += 1
        window["close_net_usd"] += to_float(row.get("realized_pnl"))
        breakdown = window["symbols_breakdown"].setdefault(symbol, {"close_count": 0, "net_usd": 0.0})
        breakdown["close_count"] += 1
        breakdown["net_usd"] += to_float(row.get("realized_pnl"))
    window["duration_text"] = age_minutes_text(start_at, end_at)
    window["symbol_breakdown_text"] = summarize_symbol_breakdown(window["symbols_breakdown"])
    window["sample_status"] = "thin_sample" if int(window["close_count"]) < THIN_SAMPLE_CLOSES else "sized_sample"
    return window


def recent_close_block_before(rows: list[dict[str, Any]], cutoff_at: str) -> dict[str, Any]:
    cutoff_dt = parse_iso(cutoff_at)
    close_rows: list[dict[str, Any]] = []
    for row in rows:
        if str(row.get("action") or "") != "close_ticket":
            continue
        row_dt = parse_iso(str(row.get("ts_utc") or ""))
        if row_dt is None:
            continue
        if cutoff_dt is not None and row_dt >= cutoff_dt:
            continue
        close_rows.append(row)
    if not close_rows:
        return {}
    last_alpha = to_float(close_rows[-1].get("close_alpha"))
    block: list[dict[str, Any]] = []
    for row in reversed(close_rows):
        if abs(to_float(row.get("close_alpha")) - last_alpha) > 1e-9:
            break
        block.append(row)
    block.reverse()
    return summarize_close_block(block, last_alpha)


def recent_windows_for_live_state(rows: list[dict[str, Any]], live_state: dict[str, Any]) -> list[dict[str, Any]]:
    runner = live_state.get("runner") if isinstance(live_state.get("runner"), dict) else {}
    metadata = live_state.get("metadata") if isinstance(live_state.get("metadata"), dict) else {}
    started_at = str(runner.get("started_at") or "")
    if not started_at:
        return parse_recent_windows(rows)[-4:]

    current_alpha = to_float(metadata.get("raw_close_alpha"))
    current_cooldown = int(to_float(metadata.get("raw_rearm_cooldown_bars")))
    current_symbols = [str(symbol) for symbol in (metadata.get("symbols") or []) if str(symbol).strip()]

    current_window = summarize_interval(
        rows,
        start_at=started_at,
        end_at=None,
        alpha=current_alpha,
        cooldown_bars=current_cooldown,
        symbols=current_symbols,
    )
    windows: list[dict[str, Any]] = []
    prior_block = recent_close_block_before(rows, started_at)
    if prior_block:
        windows.append(prior_block)
    windows.append(current_window)
    return windows


def build_summary(
    live_state: dict[str, Any],
    momentum_state: dict[str, Any],
    registry: dict[str, Any],
    windows: list[dict[str, Any]],
) -> dict[str, Any]:
    live_metadata = live_state.get("metadata") if isinstance(live_state.get("metadata"), dict) else {}
    live_runner = live_state.get("runner") if isinstance(live_state.get("runner"), dict) else {}
    momentum_metadata = momentum_state.get("metadata") if isinstance(momentum_state.get("metadata"), dict) else {}
    current_window = windows[-1] if windows else {}
    prior_window = windows[-2] if len(windows) >= 2 else {}

    current_alpha = to_float(live_metadata.get("raw_close_alpha"))
    current_cooldown = int(to_float(live_metadata.get("raw_rearm_cooldown_bars")))
    prior_alpha = to_float(prior_window.get("raw_close_alpha")) if prior_window else current_alpha
    prior_close_count = int(to_float(prior_window.get("close_count")))
    prior_close_net_usd = round(to_float(prior_window.get("close_net_usd")), 2)
    current_window_close_count = int(to_float(current_window.get("close_count")))

    live_registry = lane_registry_settings(registry, "live_rearm_941777")
    momentum_registry = lane_registry_settings(registry, "live_momentum_alpha50_941778")
    momentum_running_alpha = to_float(momentum_metadata.get("raw_close_alpha"))
    momentum_running_cooldown = int(to_float(momentum_metadata.get("raw_rearm_cooldown_bars")))
    momentum_restart_needed = (
        momentum_registry["raw_close_alpha"] != momentum_running_alpha
        or momentum_registry["raw_rearm_cooldown_bars"] != momentum_running_cooldown
    )

    provisional = (
        bool(prior_window)
        and abs(prior_alpha - current_alpha) > 1e-9
        and prior_close_count < THIN_SAMPLE_CLOSES
    )
    next_gate = "accumulate_post_revert_sample" if provisional else "monitor_live_only"
    if provisional:
        recommendation = (
            f"Keep `live_rearm_941777` on the current alpha `{current_alpha:.1f}` config, "
            f"but treat the revert as provisional until the post-revert window accumulates at least "
            f"`{THIN_SAMPLE_CLOSES}` closes."
        )
    else:
        recommendation = "Current live alpha window is adequately sampled for routine monitoring."

    return {
        "current_running_alpha": current_alpha,
        "current_running_cooldown_bars": current_cooldown,
        "current_running_symbols": [str(symbol) for symbol in (live_metadata.get("symbols") or []) if str(symbol).strip()],
        "current_running_pid": int(to_float(live_runner.get("pid"))),
        "current_running_started_at": str(live_runner.get("started_at") or ""),
        "current_running_open_total": open_total_from_state(live_state),
        "current_running_realized_closes": realized_close_total(live_state),
        "current_running_realized_net_usd": round(realized_net_total(live_state), 2),
        "current_window_close_count": current_window_close_count,
        "current_window_close_net_usd": round(to_float(current_window.get("close_net_usd")), 2),
        "current_window_sample_status": str(current_window.get("sample_status") or "no_sample"),
        "prior_window_alpha": prior_alpha,
        "prior_window_close_count": prior_close_count,
        "prior_window_close_net_usd": prior_close_net_usd,
        "prior_window_symbol_breakdown": str(prior_window.get("symbol_breakdown_text") or "-"),
        "revert_is_thin_sample": provisional,
        "next_gate": next_gate,
        "recommendation": recommendation,
        "live_registry_alpha": live_registry["raw_close_alpha"],
        "live_registry_cooldown_bars": live_registry["raw_rearm_cooldown_bars"],
        "momentum_registry_alpha": momentum_registry["raw_close_alpha"],
        "momentum_registry_cooldown_bars": momentum_registry["raw_rearm_cooldown_bars"],
        "momentum_running_alpha": momentum_running_alpha,
        "momentum_running_cooldown_bars": momentum_running_cooldown,
        "momentum_restart_needed": momentum_restart_needed,
    }


def build_payload() -> dict[str, Any]:
    live_state = load_json(LIVE_STATE_PATH)
    momentum_state = load_json(MOMENTUM_STATE_PATH)
    registry = load_json(REGISTRY_PATH)
    rows = load_jsonl(LIVE_EVENT_PATH)
    windows = recent_windows_for_live_state(rows, live_state)
    summary = build_summary(live_state, momentum_state, registry, windows)
    return {
        "generated_at": utc_now_iso(),
        "summary": summary,
        "recent_windows": windows,
    }


def write_outputs(payload: dict[str, Any]) -> None:
    JSON_PATH.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    summary = payload["summary"]
    lines = [
        "# FX Live Alpha Recent Audit",
        "",
        f"- Generated at: `{payload['generated_at']}`",
        (
            f"- Current `live_rearm_941777` runtime: `alpha={summary['current_running_alpha']:.1f}` "
            f"`cooldown={summary['current_running_cooldown_bars']}` "
            f"`symbols={','.join(summary['current_running_symbols'])}` "
            f"`pid={summary['current_running_pid']}` "
            f"`started={summary['current_running_started_at'] or '-'}`"
        ),
        (
            f"- Prior restart window before the current config: `alpha={summary['prior_window_alpha']:.1f}` "
            f"with `{summary['prior_window_close_count']}` closes for `${summary['prior_window_close_net_usd']:+.2f}` "
            f"({summary['prior_window_symbol_breakdown']})."
        ),
        (
            f"- Current post-revert window: `{summary['current_window_close_count']}` closes for "
            f"`${summary['current_window_close_net_usd']:+.2f}`. "
            f"Sample status: `{summary['current_window_sample_status']}`."
        ),
        (
            f"- Current posture: {'provisional_under_audit' if summary['revert_is_thin_sample'] else 'routine_monitoring'}; "
            f"next gate = `{summary['next_gate']}`."
        ),
        (
            f"- Momentum split: registry targets `alpha={summary['momentum_registry_alpha']:.1f}` "
            f"`cooldown={summary['momentum_registry_cooldown_bars']}`, while the running lane is "
            f"`alpha={summary['momentum_running_alpha']:.1f}` "
            f"`cooldown={summary['momentum_running_cooldown_bars']}` "
            f"(restart needed: `{'yes' if summary['momentum_restart_needed'] else 'no'}`)."
        ),
        "",
        "## Live Rearm Current State",
        "",
        "| Metric | Value |",
        "| --- | --- |",
        f"| Running alpha | `{summary['current_running_alpha']:.1f}` |",
        f"| Running cooldown bars | `{summary['current_running_cooldown_bars']}` |",
        f"| Registry alpha | `{summary['live_registry_alpha']:.1f}` |",
        f"| Registry cooldown bars | `{summary['live_registry_cooldown_bars']}` |",
        f"| Realized closes | `{summary['current_running_realized_closes']}` |",
        f"| Realized net USD | `${summary['current_running_realized_net_usd']:+.2f}` |",
        f"| Open tickets | `{summary['current_running_open_total']}` |",
        "",
        "## Recent Restart Windows",
        "",
        "| Start (UTC) | End (UTC) | Alpha | Cooldown | Closes | Net USD | Opens | Duration | Sample | Close Breakdown |",
        "| --- | --- | ---: | ---: | ---: | ---: | ---: | --- | --- | --- |",
    ]
    for row in payload["recent_windows"]:
        lines.append(
            f"| {row.get('start_at') or '-'} | {row.get('end_at') or '-'} | "
            f"{to_float(row.get('raw_close_alpha')):.1f} | "
            f"{('-' if str(row.get('raw_rearm_cooldown_bars') or '') == '' else str(int(to_float(row.get('raw_rearm_cooldown_bars')))))} | "
            f"{int(to_float(row.get('close_count')))} | {to_float(row.get('close_net_usd')):+.2f} | "
            f"{int(to_float(row.get('open_count')))} | {row.get('duration_text') or '-'} | "
            f"{row.get('sample_status') or '-'} | {row.get('symbol_breakdown_text') or '-'} |"
        )
    lines.extend(
        [
            "",
            "## Recommendation",
            "",
            f"- {summary['recommendation']}",
            "",
        ]
    )
    MD_PATH.write_text("\n".join(lines), encoding="utf-8")


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
