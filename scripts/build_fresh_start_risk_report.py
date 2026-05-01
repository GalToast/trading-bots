#!/usr/bin/env python3
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent.parent
REGISTRY_PATH = ROOT / "configs" / "penetration_lattice_runner_registry.json"
WATCHDOG_GROUPS_PATH = ROOT / "configs" / "watchdog_groups.json"
REPORT_JSON = ROOT / "reports" / "fresh_start_risk_report.json"
REPORT_MD = ROOT / "reports" / "fresh_start_risk_report.md"


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def parse_iso(value: str | None) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None


def iso_or_dash(value: str | None) -> str:
    text = str(value or "").strip()
    return text or "-"


def load_json(path: Path) -> Any:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        try:
            payload, _ = json.JSONDecoder().raw_decode(path.read_text(encoding="utf-8", errors="ignore"))
            return payload
        except Exception:
            return {}
    except Exception:
        return {}


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")


def read_registry(path: Path) -> list[dict[str, Any]]:
    payload = load_json(path)
    lanes = payload.get("lanes") if isinstance(payload, dict) else []
    return [lane for lane in lanes if isinstance(lane, dict) and lane.get("name")]


def watchdog_group_membership(path: Path) -> dict[str, list[str]]:
    payload = load_json(path)
    groups = payload.get("groups") if isinstance(payload, dict) else {}
    out: dict[str, list[str]] = {}
    if not isinstance(groups, dict):
        return out
    for group_name, group_payload in groups.items():
        lanes = group_payload.get("lanes") if isinstance(group_payload, dict) else []
        if not isinstance(lanes, list):
            continue
        for lane_name in lanes:
            name = str(lane_name or "").strip()
            if not name:
                continue
            out.setdefault(name, []).append(str(group_name))
    for owners in out.values():
        owners.sort()
    return out


def extract_state_metrics(payload: dict[str, Any]) -> tuple[int, int, float]:
    symbols = payload.get("symbols")
    if isinstance(symbols, dict) and symbols:
        close_count = 0
        open_count = 0
        realized_net = 0.0
        for symbol_state in symbols.values():
            if not isinstance(symbol_state, dict):
                continue
            close_count += int(symbol_state.get("realized_closes") or 0)
            open_count += len(symbol_state.get("open_tickets") or [])
            realized_net += float(symbol_state.get("realized_net_usd") or 0.0)
        return close_count, open_count, realized_net
    if isinstance(payload.get("positions"), list):
        stats = payload.get("stats") if isinstance(payload.get("stats"), dict) else {}
        return (
            int(stats.get("total_closes") or 0),
            len(payload.get("positions") or []),
            float(stats.get("realized_net_usd") or stats.get("realized_usd") or 0.0),
        )
    return 0, 0, 0.0


def read_fresh_start_stats(event_path: Path, now: datetime) -> dict[str, Any]:
    stats = {
        "event_exists": event_path.exists(),
        "fresh_start_total": 0,
        "fresh_start_last_60m": 0,
        "fresh_start_last_24h": 0,
        "last_fresh_start_at": "",
    }
    if not event_path.exists():
        return stats
    hour_cutoff = now - timedelta(hours=1)
    day_cutoff = now - timedelta(hours=24)
    try:
        with event_path.open("r", encoding="utf-8") as handle:
            for line in handle:
                text = line.strip()
                if not text:
                    continue
                try:
                    row = json.loads(text)
                except json.JSONDecodeError:
                    continue
                if str(row.get("action") or "") != "fresh_start_prime":
                    continue
                stats["fresh_start_total"] += 1
                ts = parse_iso(str(row.get("ts_utc") or ""))
                if ts is not None:
                    if not stats["last_fresh_start_at"] or ts.isoformat() > stats["last_fresh_start_at"]:
                        stats["last_fresh_start_at"] = ts.isoformat()
                    if ts >= hour_cutoff:
                        stats["fresh_start_last_60m"] += 1
                    if ts >= day_cutoff:
                        stats["fresh_start_last_24h"] += 1
    except Exception:
        return stats
    return stats


def classify_risk(*, close_count: int, open_count: int, fresh_start_last_60m: int, fresh_start_total: int) -> str:
    if open_count > 0 or close_count > 0 or fresh_start_last_60m >= 2:
        return "high"
    if fresh_start_total > 0:
        return "medium"
    return "low"


def build_rows(
    *,
    registry_path: Path = REGISTRY_PATH,
    watchdog_groups_path: Path = WATCHDOG_GROUPS_PATH,
    now: datetime | None = None,
) -> list[dict[str, Any]]:
    now = now or utc_now()
    owners = watchdog_group_membership(watchdog_groups_path)
    rows: list[dict[str, Any]] = []
    for lane in read_registry(registry_path):
        restart_args = [str(item or "") for item in lane.get("restart_args") or []]
        if "--fresh-start" not in restart_args:
            continue
        enabled = bool(lane.get("enabled", True))
        if not enabled:
            continue
        state_path = ROOT / str(lane.get("state_path") or "")
        event_path = ROOT / str(lane.get("event_path") or "")
        state_payload = load_json(state_path)
        close_count, open_count, realized_net = extract_state_metrics(state_payload if isinstance(state_payload, dict) else {})
        event_stats = read_fresh_start_stats(event_path, now)
        risk = classify_risk(
            close_count=close_count,
            open_count=open_count,
            fresh_start_last_60m=int(event_stats["fresh_start_last_60m"]),
            fresh_start_total=int(event_stats["fresh_start_total"]),
        )
        notes: list[str] = []
        if open_count > 0:
            notes.append(f"open_inventory={open_count}")
        if close_count > 0:
            notes.append(f"historical_closes={close_count}")
        if int(event_stats["fresh_start_last_60m"]) >= 2:
            notes.append(f"restart_churn_60m={event_stats['fresh_start_last_60m']}")
        elif int(event_stats["fresh_start_total"]) > 0:
            notes.append(f"fresh_start_total={event_stats['fresh_start_total']}")
        if not notes:
            notes.append("fresh_start_configured_no_history_yet")
        rows.append(
            {
                "lane": str(lane.get("name") or ""),
                "kind": str(lane.get("kind") or ""),
                "groups": owners.get(str(lane.get("name") or ""), []),
                "risk": risk,
                "close_count": close_count,
                "open_count": open_count,
                "realized_net_usd": round(realized_net, 2),
                "state_exists": state_path.exists(),
                "event_exists": bool(event_stats["event_exists"]),
                "fresh_start_total": int(event_stats["fresh_start_total"]),
                "fresh_start_last_60m": int(event_stats["fresh_start_last_60m"]),
                "fresh_start_last_24h": int(event_stats["fresh_start_last_24h"]),
                "last_fresh_start_at": str(event_stats["last_fresh_start_at"] or ""),
                "notes": notes,
            }
        )
    rows.sort(key=lambda row: ({"high": 0, "medium": 1, "low": 2}[row["risk"]], row["lane"]))
    return rows


def render_markdown(*, generated_at: str, rows: list[dict[str, Any]]) -> str:
    high = sum(1 for row in rows if row["risk"] == "high")
    medium = sum(1 for row in rows if row["risk"] == "medium")
    low = sum(1 for row in rows if row["risk"] == "low")
    lines = [
        "# Fresh-Start Risk Report",
        "",
        f"- Generated: `{generated_at}`",
        f"- Enabled fresh-start lanes: `{len(rows)}`",
        f"- Risk split: `high={high}` / `medium={medium}` / `low={low}`",
        "",
        "| Lane | Kind | Groups | Risk | Closes | Open | Net $ | fresh_start 60m | fresh_start total | Last fresh_start | Notes |",
        "| --- | --- | --- | --- | ---: | ---: | ---: | ---: | ---: | --- | --- |",
    ]
    for row in rows:
        lines.append(
            f"| {row['lane']} | {row['kind']} | {', '.join(row['groups']) or '-'} | {row['risk']} | "
            f"{row['close_count']} | {row['open_count']} | ${row['realized_net_usd']:+.2f} | "
            f"{row['fresh_start_last_60m']} | {row['fresh_start_total']} | {iso_or_dash(row['last_fresh_start_at'])} | "
            f"{'; '.join(row['notes'])} |"
        )
    lines.extend(
        [
            "",
            "## Read",
            "",
            "- `high`: restarting this lane with the current `--fresh-start` posture would wipe live open inventory, nonzero historical closes, or a lane already showing repeated fresh-start churn.",
            "- `medium`: the lane is still configured for `--fresh-start` and already has at least one fresh-start event on record, but it is not currently carrying closes or open inventory.",
            "- `low`: `--fresh-start` is configured but there is no evidence yet that a restart would wipe meaningful history.",
            "- Use this report with `docs/deployment-safe-restart-protocol.md` before code deploys, watchdog repairs, or manual relaunches.",
        ]
    )
    return "\n".join(lines)


def main() -> int:
    generated_at = utc_now().isoformat()
    rows = build_rows()
    payload = {
        "generated_at": generated_at,
        "row_count": len(rows),
        "rows": rows,
    }
    write_json(REPORT_JSON, payload)
    REPORT_MD.write_text(render_markdown(generated_at=generated_at, rows=rows) + "\n", encoding="utf-8")
    print(json.dumps(payload, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
