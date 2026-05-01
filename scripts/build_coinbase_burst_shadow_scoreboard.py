#!/usr/bin/env python3
from __future__ import annotations

import csv
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent.parent
REGISTRY_PATH = ROOT / "configs" / "penetration_lattice_runner_registry.json"
REPORTS = ROOT / "reports"
CSV_PATH = REPORTS / "coinbase_burst_shadow_scoreboard.csv"
MD_PATH = REPORTS / "coinbase_burst_shadow_scoreboard.md"


def parse_iso_utc(value: str | None) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None


def load_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def load_registry_lanes(path: Path) -> list[dict[str, Any]]:
    payload = load_json(path)
    lanes = payload if isinstance(payload, list) else payload.get("lanes", [])
    results: list[dict[str, Any]] = []
    for lane in lanes:
        if str(lane.get("kind") or "") != "shadow_coinbase_spot":
            continue
        name = str(lane.get("name") or "")
        if not name.startswith("shadow_coinbase_burst_"):
            continue
        restart_args = [str(item) for item in (lane.get("restart_args") or [])]
        if not any("burst_fade_" in item for item in restart_args):
            continue
        results.append(lane)
    return results


def heartbeat_age_seconds(state_payload: dict[str, Any], now: datetime) -> float | None:
    runner = state_payload.get("runner") or {}
    candidates = [
        parse_iso_utc(runner.get("heartbeat_at")),
        parse_iso_utc(runner.get("last_successful_run_at")),
        parse_iso_utc(state_payload.get("updated_at")),
    ]
    for candidate in candidates:
        if candidate is not None:
            return max(0.0, (now - candidate).total_seconds())
    return None


def open_count_from_engine(engine: dict[str, Any]) -> int:
    if isinstance(engine.get("open_positions"), int):
        return int(engine.get("open_positions") or 0)
    if isinstance(engine.get("open_positions"), dict):
        return len(engine.get("open_positions") or {})
    if isinstance(engine.get("positions"), list):
        return len(engine.get("positions") or [])
    if engine.get("position"):
        return 1
    return 0


def closes_from_engine(engine: dict[str, Any]) -> int:
    return int(engine.get("closes") or engine.get("realized_closes") or 0)


def products_tracked_from_engine(engine: dict[str, Any]) -> int:
    products = list(engine.get("products") or [])
    if products:
        return len(products)
    positions = engine.get("positions") or []
    if isinstance(positions, list) and positions:
        return len({str(pos.get("pid") or "") for pos in positions if pos.get("pid")})
    if engine.get("product_id"):
        return 1
    return 0


def lane_style(lane_name: str) -> str:
    if lane_name.endswith("_god_mode_live"):
        return "god_mode_live"
    if lane_name.endswith("_god_killer"):
        return "god_killer"
    if lane_name.endswith("_god_reclaimer_live"):
        return "god_reclaimer_live"
    if lane_name.endswith("_compound"):
        return "compound"
    if lane_name.endswith("_best"):
        return "roundrobin_best"
    if lane_name.endswith("_rotation"):
        return "multicoin_rotation"
    return "burst_research"


def lane_row(lane: dict[str, Any], *, state_payload: dict[str, Any], now: datetime) -> dict[str, Any]:
    engine = state_payload.get("engine") or {}
    runner = state_payload.get("runner") or {}
    heartbeat_age = heartbeat_age_seconds(state_payload, now)
    closes = closes_from_engine(engine)
    realized = float(engine.get("realized_net_usd") or engine.get("realized_net") or 0.0)
    wins = int(engine.get("wins") or engine.get("realized_wins") or 0)
    losses = int(engine.get("losses") or max(0, closes - wins))
    avg_pnl_per_close = float(engine.get("avg_pnl_per_close") or (realized / closes if closes else 0.0))
    return {
        "lane_name": str(lane.get("name") or ""),
        "style": lane_style(str(lane.get("name") or "")),
        "realized_net_usd": round(realized, 4),
        "closes": closes,
        "wins": wins,
        "losses": losses,
        "win_rate": round(float(engine.get("win_rate") or 0.0), 2),
        "avg_pnl_per_close": round(avg_pnl_per_close, 4),
        "cash_usd": round(float(engine.get("cash") or 0.0), 4),
        "fees_usd": round(float(engine.get("total_fees") or 0.0), 4),
        "open_count": open_count_from_engine(engine),
        "products_tracked": products_tracked_from_engine(engine),
        "heartbeat_age_seconds": round(float(heartbeat_age), 1) if heartbeat_age is not None else "",
        "pid": int(runner.get("pid") or 0),
        "script": str(runner.get("script") or ""),
        "state_path": str(Path(str(lane.get("state_path") or ""))),
        "note": "supervised_research",
    }


def build_rows(*, registry_path: Path = REGISTRY_PATH, now: datetime | None = None) -> list[dict[str, Any]]:
    repo_root = registry_path.resolve().parent.parent
    lanes = load_registry_lanes(registry_path)
    now_utc = now or datetime.now(timezone.utc)
    rows: list[dict[str, Any]] = []
    for lane in lanes:
        state_path = repo_root / str(lane.get("state_path") or "")
        if not state_path.exists():
            continue
        state_payload = load_json(state_path)
        rows.append(lane_row(lane, state_payload=state_payload, now=now_utc))

    rows.sort(key=lambda row: -float(row["realized_net_usd"]))
    total = {
        "lane_name": "TOTAL",
        "style": "supervised_burst_pack",
        "realized_net_usd": round(sum(float(row["realized_net_usd"]) for row in rows), 4),
        "closes": sum(int(row["closes"]) for row in rows),
        "wins": sum(int(row["wins"]) for row in rows),
        "losses": sum(int(row["losses"]) for row in rows),
        "win_rate": round(
            (sum(int(row["wins"]) for row in rows) / max(1, sum(int(row["closes"]) for row in rows))) * 100,
            2,
        ),
        "avg_pnl_per_close": round(
            sum(float(row["realized_net_usd"]) for row in rows) / max(1, sum(int(row["closes"]) for row in rows)),
            4,
        ),
        "cash_usd": round(sum(float(row["cash_usd"]) for row in rows), 4),
        "fees_usd": round(sum(float(row["fees_usd"]) for row in rows), 4),
        "open_count": sum(int(row["open_count"]) for row in rows),
        "products_tracked": sum(int(row["products_tracked"]) for row in rows),
        "heartbeat_age_seconds": "",
        "pid": 0,
        "script": "",
        "state_path": "",
        "note": f"lanes={len(rows)}",
    }
    rows.append(total)
    return rows


def write_reports(rows: list[dict[str, Any]], *, csv_path: Path = CSV_PATH, md_path: Path = MD_PATH) -> None:
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    with csv_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)

    lines = [
        "# Coinbase Burst Shadow Scoreboard",
        "",
        "| Lane | Style | Realized $ | Closes | Wins | Losses | Win Rate % | Avg/Close $ | Cash $ | Fees $ | Open | Products | Heartbeat Age (s) | Note |",
        "| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- |",
    ]
    for row in rows:
        lines.append(
            "| {lane_name} | {style} | {realized_net_usd:.4f} | {closes} | {wins} | {losses} | "
            "{win_rate:.2f} | {avg_pnl_per_close:.4f} | {cash_usd:.4f} | {fees_usd:.4f} | "
            "{open_count} | {products_tracked} | {heartbeat_age_seconds} | {note} |".format(**row)
        )
    md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    rows = build_rows()
    if not rows:
        raise SystemExit("no supervised Coinbase burst rows found")
    write_reports(rows)
    print(json.dumps({"csv_path": str(CSV_PATH), "md_path": str(MD_PATH), "rows": rows}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
