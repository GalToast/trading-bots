from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent.parent
BASELINES_JSON = ROOT / "reports" / "clean_forward_baselines.json"


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def parse_iso(value: str | None) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None


def load_json(path: Path) -> Any:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def save_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")


def engine_payload(state_payload: dict[str, Any]) -> dict[str, Any]:
    if isinstance(state_payload.get("engine"), dict):
        return state_payload["engine"]
    if isinstance(state_payload.get("state"), dict):
        return state_payload["state"]
    return {}


def open_count_from_engine(engine: dict[str, Any]) -> int:
    if isinstance(engine.get("open_count"), int):
        return int(engine.get("open_count") or 0)
    details = engine.get("per_coin_details")
    if isinstance(details, dict):
        return sum(1 for row in details.values() if isinstance(row, dict) and bool(row.get("in_position")))
    if engine.get("position") or engine.get("current_position"):
        return 1
    if str(engine.get("pos") or "").lower() == "active":
        return 1
    return 0


def snapshot_from_state_payload(state_payload: dict[str, Any]) -> dict[str, Any]:
    symbols = state_payload.get("symbols")
    if isinstance(symbols, dict) and symbols:
        realized = 0.0
        closes = 0
        wins = 0
        losses = 0
        open_count = 0
        tracked_symbols = 0
        for snap in symbols.values():
            if not isinstance(snap, dict):
                continue
            tracked_symbols += 1
            realized += float(snap.get("realized_net_usd") or 0.0)
            closes += int(snap.get("realized_closes") or 0)
            wins += int(snap.get("wins") or 0)
            losses += int(snap.get("losses") or 0)
            open_count += len(list(snap.get("open_tickets", []) or []))
        return {
            "realized_net_usd": round(realized, 4),
            "closes": int(closes),
            "wins": int(wins),
            "losses": int(losses),
            "open_count": int(open_count),
            "tracked_symbols": int(tracked_symbols),
        }

    engine = engine_payload(state_payload)
    closes = int(engine.get("closes") or engine.get("realized_closes") or engine.get("total_closes") or 0)
    wins = int(engine.get("wins") or engine.get("total_wins") or 0)
    losses = int(engine.get("losses") or engine.get("total_losses") or max(0, closes - wins))
    realized = float(
        engine.get("realized_net_usd")
        or engine.get("realized_net")
        or engine.get("total_realized")
        or 0.0
    )
    return {
        "realized_net_usd": round(realized, 4),
        "closes": int(closes),
        "wins": int(wins),
        "losses": int(losses),
        "open_count": int(open_count_from_engine(engine)),
        "tracked_symbols": int(len(list(engine.get("products") or [])) or (1 if engine.get("product_id") else 0)),
    }


def load_reset_baselines(path: Path = BASELINES_JSON) -> dict[str, dict[str, Any]]:
    payload = load_json(path)
    rows = payload.get("lanes") if isinstance(payload, dict) else {}
    return rows if isinstance(rows, dict) else {}


def save_reset_baselines(resets: dict[str, dict[str, Any]], path: Path = BASELINES_JSON) -> None:
    save_json(path, {"updated_at": utc_now_iso(), "lanes": resets})


def reset_baseline_for_lane(
    lane_name: str,
    baseline_row: dict[str, Any] | None,
    resets: dict[str, dict[str, Any]],
) -> tuple[dict[str, Any] | None, str]:
    reset_row = resets.get(str(lane_name or ""))
    if not isinstance(reset_row, dict):
        return baseline_row, "seeded"
    reset_at = parse_iso(reset_row.get("reset_at"))
    baseline_at = parse_iso((baseline_row or {}).get("seeded_at"))
    if baseline_at is not None and reset_at is not None and reset_at < baseline_at:
        return baseline_row, "seeded"
    return reset_row, "stale_tick_repair"


def record_reset_baseline(
    *,
    lane_name: str,
    kind: str,
    state_path: Path,
    reason: str,
    reset_at: str | None = None,
    path: Path = BASELINES_JSON,
) -> dict[str, Any] | None:
    payload = load_json(state_path)
    if not isinstance(payload, dict) or not payload:
        return None
    snapshot = snapshot_from_state_payload(payload)
    resets = load_reset_baselines(path)
    record = {
        "reset_at": str(reset_at or utc_now_iso()),
        "reset_type": "stale_tick_repair",
        "reason": str(reason or ""),
        "kind": str(kind or ""),
        "state_path": str(state_path),
        "state_updated_at": str(payload.get("updated_at") or ""),
        **snapshot,
    }
    existing = resets.get(lane_name)
    if isinstance(existing, dict):
        existing_at = parse_iso(existing.get("reset_at"))
        record_at = parse_iso(record.get("reset_at"))
        if existing_at is not None and record_at is not None and existing_at >= record_at:
            return existing
    resets[lane_name] = record
    save_reset_baselines(resets, path)
    return record
