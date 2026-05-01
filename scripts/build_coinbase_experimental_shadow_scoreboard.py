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
CSV_PATH = REPORTS / "coinbase_experimental_shadow_scoreboard.csv"
MD_PATH = REPORTS / "coinbase_experimental_shadow_scoreboard.md"


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
    rows: list[dict[str, Any]] = []
    for lane in lanes:
        if str(lane.get("kind") or "") != "shadow_coinbase_spot":
            continue
        name = str(lane.get("name") or "")
        if not name.startswith("shadow_coinbase_experimental_"):
            continue
        rows.append(lane)
    return rows


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
    if engine.get("current_position") or engine.get("position"):
        return 1
    if str(engine.get("pos") or "").lower() == "active":
        return 1
    return 0


def style_for_lane(lane_name: str) -> str:
    suffix = lane_name.removeprefix("shadow_coinbase_experimental_")
    return suffix or "experimental"


def lane_row(lane: dict[str, Any], *, state_payload: dict[str, Any], now: datetime) -> dict[str, Any]:
    engine = engine_payload(state_payload)
    runner = state_payload.get("runner") if isinstance(state_payload.get("runner"), dict) else {}
    heartbeat_age = heartbeat_age_seconds(state_payload, now)
    closes = int(engine.get("closes") or engine.get("realized_closes") or engine.get("total_closes") or 0)
    wins = int(engine.get("wins") or engine.get("total_wins") or 0)
    losses = int(engine.get("losses") or engine.get("total_losses") or max(0, closes - wins))
    realized = float(
        engine.get("realized_net_usd")
        or engine.get("realized_net")
        or engine.get("total_realized")
        or 0.0
    )
    cash = float(engine.get("cash") or engine.get("total_cash") or 0.0)
    fees = float(engine.get("total_fees") or engine.get("total_fees_paid") or 0.0)
    product_id = str(engine.get("product_id") or "")
    products = list(engine.get("products") or [])
    if not product_id and len(products) == 1:
        product_id = str(products[0] or "")
    products_tracked = len(products) if products else (1 if product_id else 0)
    avg_pnl = float(engine.get("avg_pnl_per_close") or (realized / closes if closes else 0.0))
    return {
        "lane_name": str(lane.get("name") or ""),
        "style": style_for_lane(str(lane.get("name") or "")),
        "product_id": product_id,
        "realized_net_usd": round(realized, 4),
        "closes": closes,
        "wins": wins,
        "losses": losses,
        "win_rate": round(float(engine.get("win_rate") or 0.0), 2),
        "avg_pnl_per_close": round(avg_pnl, 4),
        "cash_usd": round(cash, 4),
        "fees_usd": round(fees, 4),
        "open_count": open_count_from_engine(engine),
        "products_tracked": products_tracked,
        "heartbeat_age_seconds": round(float(heartbeat_age), 1) if heartbeat_age is not None else "",
        "pid": int(runner.get("pid") or 0),
        "script": str(runner.get("script") or ""),
        "state_path": str(Path(str(lane.get("state_path") or ""))),
        "note": "supervised_experimental",
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
        rows.append(lane_row(lane, state_payload=load_json(state_path), now=now_utc))

    rows.sort(key=lambda row: -float(row["realized_net_usd"]))
    total = {
        "lane_name": "TOTAL",
        "style": "experimental_pack",
        "product_id": "",
        "realized_net_usd": round(sum(float(row["realized_net_usd"]) for row in rows), 4),
        "closes": sum(int(row["closes"]) for row in rows),
        "wins": sum(int(row["wins"]) for row in rows),
        "losses": sum(int(row["losses"]) for row in rows),
        "win_rate": round((sum(int(row["wins"]) for row in rows) / max(1, sum(int(row["closes"]) for row in rows))) * 100, 2),
        "avg_pnl_per_close": round(sum(float(row["realized_net_usd"]) for row in rows) / max(1, sum(int(row["closes"]) for row in rows)), 4),
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
        "# Coinbase Experimental Shadow Scoreboard",
        "",
        "| Lane | Style | Product | Realized $ | Closes | Wins | Losses | Win Rate % | Avg/Close $ | Cash $ | Fees $ | Open | Heartbeat Age (s) | Note |",
        "| --- | --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- |",
    ]
    for row in rows:
        lines.append(
            "| {lane_name} | {style} | {product_id} | {realized_net_usd:.4f} | {closes} | {wins} | {losses} | "
            "{win_rate:.2f} | {avg_pnl_per_close:.4f} | {cash_usd:.4f} | {fees_usd:.4f} | {open_count} | "
            "{heartbeat_age_seconds} | {note} |".format(**row)
        )
    md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    rows = build_rows()
    if not rows:
        raise SystemExit("no supervised experimental Coinbase rows found")
    write_reports(rows)
    print(json.dumps({"csv_path": str(CSV_PATH), "md_path": str(MD_PATH), "rows": rows}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
