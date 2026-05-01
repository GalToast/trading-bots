#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import MetaTrader5 as mt5

import build_execution_monitor_report as execution_monitor
import mt5_terminal_guard


ROOT = Path(__file__).resolve().parent.parent
REPORTS = ROOT / "reports"
CONFIGS = ROOT / "configs"
ORGANISM_STATE_JSON = REPORTS / "organism_state.json"
EXECUTION_MONITOR_JSON = REPORTS / "execution_monitor_report.json"
M5_SURVIVABILITY_JSON = REPORTS / "live_btcusd_m5_survivability_board.json"
M5_SURVIVABILITY_BUILDER = ROOT / "scripts" / "build_live_btcusd_m5_survivability_board.py"
RUNNER_REGISTRY_JSON = CONFIGS / "penetration_lattice_runner_registry.json"
OUTPUT_JSON = REPORTS / "live_btcusd_concentration_board.json"
OUTPUT_MD = REPORTS / "live_btcusd_concentration_board.md"
M5_SURVIVABILITY_MAX_AGE_SECONDS = 180.0

BTC_LANES = (
    "live_btcusd_exc2_tight_941779",
    "live_btcusd_m15_warp_941781",
    "live_btcusd_m5_warp_probation_941780",
)
BTC_LANE_MAGICS = {
    "live_btcusd_exc2_tight_941779": 941779,
    "live_btcusd_m15_warp_941781": 941781,
    "live_btcusd_m5_warp_probation_941780": 941780,
}
M5_LANE = "live_btcusd_m5_warp_probation_941780"
BTC_INTERVENTION_PRICE = 76500.0
COMBINED_FLOATING_ALERT_USD = -10000.0
EQUITY_FLOOR_USD = 55000.0
M5_NO_COMPRESSION_HOURS = 6.0
M5_NO_COMPRESSION_BID_MIN = 75000.0
M5_NO_COMPRESSION_BID_MAX = 76000.0


def load_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def parse_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except Exception:
        return default


def parse_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except Exception:
        return default


def parse_iso_datetime(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00"))
    except Exception:
        return None


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def file_age_seconds(path: Path, now: datetime) -> float | None:
    if not path.exists():
        return None
    modified_at = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
    return max(0.0, round((now - modified_at).total_seconds(), 3))


def ensure_fresh_survivability_payload(
    now: datetime,
    *,
    runner: Any = subprocess.run,
) -> tuple[dict[str, Any], float | None, bool]:
    age_seconds = file_age_seconds(M5_SURVIVABILITY_JSON, now)
    refreshed = False
    if age_seconds is None or age_seconds > M5_SURVIVABILITY_MAX_AGE_SECONDS:
        result = runner(
            [sys.executable, str(M5_SURVIVABILITY_BUILDER)],
            cwd=str(ROOT),
            capture_output=True,
            text=True,
        )
        if getattr(result, "returncode", 1) != 0:
            if M5_SURVIVABILITY_JSON.exists():
                payload = load_json(M5_SURVIVABILITY_JSON)
                return payload if isinstance(payload, dict) else {}, age_seconds, False
            stderr = str(getattr(result, "stderr", "") or "").strip()
            stdout = str(getattr(result, "stdout", "") or "").strip()
            detail = stderr or stdout or "unknown error"
            raise RuntimeError(f"Failed to refresh survivability board: {detail}")
        refreshed = True
        age_seconds = file_age_seconds(M5_SURVIVABILITY_JSON, now)
    payload = load_json(M5_SURVIVABILITY_JSON) if M5_SURVIVABILITY_JSON.exists() else {}
    return payload if isinstance(payload, dict) else {}, age_seconds, refreshed


def collect_btc_market_snapshot(mt5_module: Any = mt5) -> dict[str, float]:
    initialized, _mt5_connection = mt5_terminal_guard.initialize_mt5(mt5_module=mt5_module)
    if not initialized:
        return {}
    try:
        tick = mt5_module.symbol_info_tick("BTCUSD")
        account = mt5_module.account_info()
        return {
            "current_bid": parse_float(getattr(tick, "bid", 0.0)),
            "current_ask": parse_float(getattr(tick, "ask", 0.0)),
            "balance": parse_float(getattr(account, "balance", 0.0)),
            "equity": parse_float(getattr(account, "equity", 0.0)),
            "margin_level": parse_float(getattr(account, "margin_level", 0.0)),
        }
    finally:
        mt5_module.shutdown()


def organism_live_lanes(payload: dict[str, Any]) -> dict[str, dict[str, Any]]:
    rows = payload.get("live_lanes") or []
    mapped: dict[str, dict[str, Any]] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        lane = str(row.get("lane") or "").strip()
        if lane:
            mapped[lane] = row
    return mapped


def execution_rows(payload: dict[str, Any]) -> dict[str, dict[str, Any]]:
    rows = payload.get("rows") or []
    mapped: dict[str, dict[str, Any]] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        lane = str(row.get("lane") or "").strip()
        if lane:
            mapped[lane] = row
    return mapped


def registry_rows(payload: dict[str, Any]) -> dict[str, dict[str, Any]]:
    rows = payload.get("lanes") or []
    mapped: dict[str, dict[str, Any]] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        lane = str(row.get("name") or "").strip()
        if lane:
            mapped[lane] = row
    return mapped


def age_hours(now: datetime, timestamp: Any) -> float | None:
    parsed = parse_iso_datetime(timestamp)
    if parsed is None:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return round((now - parsed).total_seconds() / 3600.0, 2)


def evaluate_thresholds(
    *,
    current_bid: float,
    equity_usd: float,
    combined_floating_usd: float,
    m5_last_trade_age_hours: float | None,
) -> dict[str, dict[str, Any]]:
    in_no_compression_band = M5_NO_COMPRESSION_BID_MIN <= current_bid <= M5_NO_COMPRESSION_BID_MAX
    no_compression_trigger = bool(
        in_no_compression_band
        and m5_last_trade_age_hours is not None
        and m5_last_trade_age_hours >= M5_NO_COMPRESSION_HOURS
    )
    return {
        "price_intervention": {
            "triggered": current_bid >= BTC_INTERVENTION_PRICE,
            "threshold": BTC_INTERVENTION_PRICE,
            "read": f"bid>={BTC_INTERVENTION_PRICE:.0f}",
        },
        "combined_floating": {
            "triggered": combined_floating_usd <= COMBINED_FLOATING_ALERT_USD,
            "threshold": COMBINED_FLOATING_ALERT_USD,
            "read": f"combined_floating<={COMBINED_FLOATING_ALERT_USD:.0f}",
        },
        "equity_floor": {
            "triggered": equity_usd > 0 and equity_usd <= EQUITY_FLOOR_USD,
            "threshold": EQUITY_FLOOR_USD,
            "read": f"equity<={EQUITY_FLOOR_USD:.0f}",
        },
        "m5_no_compression": {
            "triggered": no_compression_trigger,
            "threshold_hours": M5_NO_COMPRESSION_HOURS,
            "band_min": M5_NO_COMPRESSION_BID_MIN,
            "band_max": M5_NO_COMPRESSION_BID_MAX,
            "read": f"bid in [{M5_NO_COMPRESSION_BID_MIN:.0f},{M5_NO_COMPRESSION_BID_MAX:.0f}] and last_trade_age>={M5_NO_COMPRESSION_HOURS:.1f}h",
        },
    }


def collect_unassigned_btc_positions(active_magics: set[int], mt5_module: Any = mt5) -> list[dict[str, Any]]:
    initialized, _mt5_connection = mt5_terminal_guard.initialize_mt5(mt5_module=mt5_module)
    if not initialized:
        return []
    try:
        rows: list[dict[str, Any]] = []
        for pos in mt5_module.positions_get(symbol="BTCUSD") or []:
            magic = parse_int(getattr(pos, "magic", 0))
            if magic in active_magics:
                continue
            opened_at = parse_int(getattr(pos, "time", 0))
            rows.append(
                {
                    "ticket": parse_int(getattr(pos, "ticket", 0)),
                    "magic": magic,
                    "side": "SELL" if parse_int(getattr(pos, "type", 0)) == 1 else "BUY",
                    "volume": parse_float(getattr(pos, "volume", 0.0)),
                    "price_open": parse_float(getattr(pos, "price_open", 0.0)),
                    "profit_usd": parse_float(getattr(pos, "profit", 0.0)),
                    "comment": str(getattr(pos, "comment", "") or ""),
                    "opened_at": datetime.fromtimestamp(opened_at, tz=timezone.utc).isoformat() if opened_at > 0 else "",
                }
            )
        rows.sort(key=lambda row: parse_int(row.get("ticket")))
        return rows
    finally:
        mt5_module.shutdown()


def build_payload() -> dict[str, Any]:
    generated_at = utc_now_iso()
    now = parse_iso_datetime(generated_at) or datetime.now(timezone.utc)
    organism = load_json(ORGANISM_STATE_JSON)
    execution = load_json(EXECUTION_MONITOR_JSON)
    registry_payload = load_json(RUNNER_REGISTRY_JSON)

    organism_rows = organism_live_lanes(organism if isinstance(organism, dict) else {})
    execution_rows_map = execution_rows(execution if isinstance(execution, dict) else {})
    registry_rows_map = registry_rows(registry_payload if isinstance(registry_payload, dict) else {})
    active_btc_lane_names = [lane for lane in BTC_LANES if bool((registry_rows_map.get(lane) or {}).get("enabled", True))]
    paused_btc_lane_names = [lane for lane in BTC_LANES if lane not in active_btc_lane_names]
    registry_lane_rows = list((registry_payload if isinstance(registry_payload, dict) else {}).get("lanes") or [])
    active_btc_magics: set[int] = set()
    for lane in registry_lane_rows:
        name = str(lane.get("name") or "")
        if name not in active_btc_lane_names:
            continue
        state_path_text = str(lane.get("state_path") or "")
        state_payload = load_json(ROOT / state_path_text) if state_path_text else {}
        active_btc_magics.update(execution_monitor.lane_live_magics(lane, state_payload if isinstance(state_payload, dict) else {}))
    if not active_btc_magics:
        active_btc_magics = {BTC_LANE_MAGICS[lane] for lane in active_btc_lane_names if lane in BTC_LANE_MAGICS}
    m5_lane_active = M5_LANE in active_btc_lane_names
    if m5_lane_active:
        survivability, survivability_age_seconds, survivability_refreshed = ensure_fresh_survivability_payload(now)
    else:
        survivability, survivability_age_seconds, survivability_refreshed = {}, None, False
    market_snapshot = collect_btc_market_snapshot()
    unassigned_btc_positions = collect_unassigned_btc_positions(active_btc_magics)

    current_bid = parse_float(market_snapshot.get("current_bid"), parse_float(survivability.get("current_bid")))
    current_ask = parse_float(market_snapshot.get("current_ask"), parse_float(survivability.get("current_ask")))
    account = survivability.get("account") if isinstance(survivability.get("account"), dict) else {}
    equity_usd = parse_float(market_snapshot.get("equity"), parse_float(account.get("equity")))
    balance_usd = parse_float(market_snapshot.get("balance"), parse_float(account.get("balance")))
    margin_level = parse_float(market_snapshot.get("margin_level"), parse_float(account.get("margin_level")))

    lane_rows: list[dict[str, Any]] = []
    paused_rows: list[dict[str, Any]] = []
    combined_realized = 0.0
    combined_floating = 0.0
    combined_net = 0.0
    combined_open = 0

    for lane in BTC_LANES:
        registry_row = registry_rows_map.get(lane) or {}
        enabled = bool(registry_row.get("enabled", True))
        live_row = organism_rows.get(lane) or {}
        exec_row = execution_rows_map.get(lane) or {}
        realized_usd = parse_float(live_row.get("realized_usd"))
        floating_usd = parse_float(live_row.get("floating_usd"))
        net_usd = parse_float(live_row.get("net_usd"))
        open_count = max(
            parse_int(live_row.get("open_count")),
            parse_int(exec_row.get("broker_scoped_open_count")),
            parse_int(exec_row.get("open_count")),
        )
        closes = parse_int(live_row.get("closes"))
        last_trade_event_at = str(exec_row.get("last_trade_event_at") or "")
        row = {
            "lane": lane,
            "realized_usd": round(realized_usd, 2),
            "floating_usd": round(floating_usd, 2),
            "net_usd": round(net_usd, 2),
            "open_count": open_count,
            "closes": closes,
            "watchdog_status": str(exec_row.get("watchdog_status") or live_row.get("watchdog_status") or ""),
            "notes": str(exec_row.get("notes") or live_row.get("notes") or ""),
            "clean_forward_realized_delta_usd": round(parse_float(exec_row.get("clean_forward_realized_delta_usd")), 2),
            "clean_forward_new_closes": parse_int(exec_row.get("clean_forward_new_closes")),
            "last_trade_event_at": last_trade_event_at,
            "last_trade_event_age_hours": age_hours(now, last_trade_event_at),
            "quote_bid": round(parse_float(exec_row.get("quote_bid"), current_bid), 2),
            "quote_ask": round(parse_float(exec_row.get("quote_ask"), current_ask), 2),
            "next_buy_level": round(parse_float(exec_row.get("next_buy_level")), 2),
            "next_sell_level": round(parse_float(exec_row.get("next_sell_level")), 2),
            "has_room": bool(exec_row.get("has_room")) if exec_row.get("has_room") not in ("", None) else False,
            "enabled": enabled,
            "pause_note": str(registry_row.get("pause_note") or ""),
        }
        if equity_usd > 0:
            row["net_pct_equity"] = round((net_usd / equity_usd) * 100.0, 3)
            row["floating_pct_equity"] = round((floating_usd / equity_usd) * 100.0, 3)
        else:
            row["net_pct_equity"] = 0.0
            row["floating_pct_equity"] = 0.0
        if lane == M5_LANE and isinstance(survivability.get("current_lane"), dict):
            current_lane = survivability["current_lane"]
            row["buy_count"] = parse_int(current_lane.get("buy_count"))
            row["sell_count"] = parse_int(current_lane.get("sell_count"))
        if enabled:
            lane_rows.append(row)
            combined_realized += realized_usd
            combined_floating += floating_usd
            combined_net += net_usd
            combined_open += open_count
        else:
            paused_rows.append(row)

    m5_row = next((row for row in lane_rows if row["lane"] == M5_LANE), None)
    thresholds = evaluate_thresholds(
        current_bid=current_bid,
        equity_usd=equity_usd,
        combined_floating_usd=combined_floating,
        m5_last_trade_age_hours=(m5_row or {}).get("last_trade_event_age_hours"),
    )
    triggered = [name for name, row in thresholds.items() if row.get("triggered")]

    summary = {
        "current_bid": round(current_bid, 2),
        "current_ask": round(current_ask, 2),
        "balance_usd": round(balance_usd, 2),
        "equity_usd": round(equity_usd, 2),
        "margin_level_pct": round(margin_level, 2),
        "combined_realized_usd": round(combined_realized, 2),
        "combined_floating_usd": round(combined_floating, 2),
        "combined_net_usd": round(combined_net, 2),
        "combined_open_count": combined_open,
        "active_btc_lane_count": len(lane_rows),
        "paused_btc_lane_count": len(paused_rows),
        "triggered_thresholds": triggered,
        "operator_posture": "carry_until_threshold_break" if not triggered else "operator_review_required",
        "largest_floating_drag_lane": min(lane_rows, key=lambda row: row["floating_usd"])["lane"] if lane_rows else "",
        "unassigned_btc_open_count": len(unassigned_btc_positions),
        "unassigned_btc_floating_usd": round(sum(parse_float(row.get("profit_usd")) for row in unassigned_btc_positions), 2),
    }
    if equity_usd > 0:
        summary["combined_floating_pct_equity"] = round((combined_floating / equity_usd) * 100.0, 3)
        summary["combined_net_pct_equity"] = round((combined_net / equity_usd) * 100.0, 3)
    else:
        summary["combined_floating_pct_equity"] = 0.0
        summary["combined_net_pct_equity"] = 0.0

    return {
        "generated_at": generated_at,
        "sources": [
            str(ORGANISM_STATE_JSON.relative_to(ROOT)),
            str(EXECUTION_MONITOR_JSON.relative_to(ROOT)),
            str(M5_SURVIVABILITY_JSON.relative_to(ROOT)) if M5_SURVIVABILITY_JSON.exists() else "",
        ],
        "survivability_source_age_seconds": survivability_age_seconds,
        "survivability_source_refreshed": survivability_refreshed,
        "summary": summary,
        "thresholds": thresholds,
        "rows": lane_rows,
        "paused_rows": paused_rows,
        "unassigned_btc_positions": unassigned_btc_positions,
    }


def render_markdown(payload: dict[str, Any]) -> str:
    summary = payload.get("summary") or {}
    thresholds = payload.get("thresholds") or {}
    rows = payload.get("rows") or []
    paused_rows = payload.get("paused_rows") or []
    lines = [
        "# Live BTCUSD Concentration Board",
        "",
        "> Current runtime generated board.",
        "",
        f"- Generated at: `{payload.get('generated_at', '')}`",
        (
            f"- M5 survivability source age: `{parse_float(payload.get('survivability_source_age_seconds')):.1f}s` | "
            f"refreshed_now=`{str(bool(payload.get('survivability_source_refreshed'))).lower()}`"
            if payload.get("survivability_source_age_seconds") is not None
            else "- M5 survivability source: `inactive` (BTC M5 lane paused in registry)"
        ),
        f"- Current bid/ask: `{parse_float(summary.get('current_bid')):.2f}` / `{parse_float(summary.get('current_ask')):.2f}`",
        f"- Account equity: `${parse_float(summary.get('equity_usd')):.2f}` | balance `${parse_float(summary.get('balance_usd')):.2f}` | margin level `{parse_float(summary.get('margin_level_pct')):.2f}%`",
        f"- Active BTC live lanes: `{parse_int(summary.get('active_btc_lane_count'))}` | paused BTC live lanes: `{parse_int(summary.get('paused_btc_lane_count'))}`",
        f"- Combined active BTC live floating: `${parse_float(summary.get('combined_floating_usd')):+.2f}` ({parse_float(summary.get('combined_floating_pct_equity')):+.3f}% of equity)",
        f"- Combined active BTC live net: `${parse_float(summary.get('combined_net_usd')):+.2f}` ({parse_float(summary.get('combined_net_pct_equity')):+.3f}% of equity)",
        f"- Combined active BTC live opens: `{parse_int(summary.get('combined_open_count'))}`",
        f"- Unassigned BTC broker positions: `{parse_int(summary.get('unassigned_btc_open_count'))}` with floating `${parse_float(summary.get('unassigned_btc_floating_usd')):+.2f}`",
        f"- Operator posture: `{summary.get('operator_posture', '')}`",
        "",
        "## Active Lane Breakdown",
        "",
        "| Lane | Realized | Floating | Net | Net % Equity | Open | Closes | Clean Forward | Last Trade Age | Watchdog | Notes |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: | --- | ---: | --- | --- |",
    ]
    for row in rows:
        clean_forward = f"{parse_float(row.get('clean_forward_realized_delta_usd')):+.2f}/{parse_int(row.get('clean_forward_new_closes'))}c"
        age_hours_value = row.get("last_trade_event_age_hours")
        age_text = "-" if age_hours_value is None else f"{float(age_hours_value):.2f}h"
        lines.append(
            f"| `{row.get('lane', '')}` | {parse_float(row.get('realized_usd')):+.2f} | "
            f"{parse_float(row.get('floating_usd')):+.2f} | {parse_float(row.get('net_usd')):+.2f} | "
            f"{parse_float(row.get('net_pct_equity')):+.3f}% | {parse_int(row.get('open_count'))} | "
            f"{parse_int(row.get('closes'))} | `{clean_forward}` | {age_text} | "
            f"{row.get('watchdog_status', '') or '-'} | {row.get('notes', '') or '-'} |"
        )
    if paused_rows:
        lines.extend(
            [
                "",
                "## Paused / Disabled BTC Live IDs",
                "",
                "| Lane | Realized | Floating | Net | Open | Closes | Registry | Pause Note |",
                "| --- | ---: | ---: | ---: | ---: | ---: | --- | --- |",
            ]
        )
        for row in paused_rows:
            lines.append(
                f"| `{row.get('lane', '')}` | {parse_float(row.get('realized_usd')):+.2f} | "
                f"{parse_float(row.get('floating_usd')):+.2f} | {parse_float(row.get('net_usd')):+.2f} | "
                f"{parse_int(row.get('open_count'))} | {parse_int(row.get('closes'))} | paused | {row.get('pause_note') or '-'} |"
            )
    lines.extend(
        [
            "",
            "## Thresholds",
            "",
            "| Threshold | Rule | Triggered |",
            "| --- | --- | --- |",
        ]
    )
    for name, row in thresholds.items():
        lines.append(
            f"| `{name}` | `{row.get('read', '')}` | `{'yes' if row.get('triggered') else 'no'}` |"
        )
    lines.extend(["", "## Unassigned BTC Broker Inventory", ""])
    unassigned_positions = list(payload.get("unassigned_btc_positions") or [])
    if unassigned_positions:
        lines.append("- These positions affect BTC account equity but are not attributed to the current managed live BTC lane magics.")
        lines.append("")
        lines.append("| Ticket | Magic | Side | Volume | Open Price | Floating USD | Comment | Opened At |")
        lines.append("| ---: | ---: | --- | ---: | ---: | ---: | --- | --- |")
        for row in unassigned_positions[:12]:
            lines.append(
                f"| {parse_int(row.get('ticket'))} | {parse_int(row.get('magic'))} | {row.get('side') or '-'} | "
                f"{parse_float(row.get('volume')):.2f} | {parse_float(row.get('price_open')):.2f} | "
                f"{parse_float(row.get('profit_usd')):+.2f} | {row.get('comment') or '-'} | {row.get('opened_at') or '-'} |"
            )
        lines.append("")
    else:
        lines.append("- none")
        lines.append("")
    lines.extend(
        [
            "",
            "## Read",
            "",
            "- Use this board for the combined active BTC live answer across the registry-enabled BTC live lanes before arguing from one lane in isolation.",
            "- Parked BTC live ids stay visible in the paused section for context, but they do not contribute to the combined active BTC exposure totals or threshold logic.",
            "- When the BTC M5 lane is active, the concentration builder refreshes the BTC M5 survivability source when that input is missing or older than three minutes, so a stale survivability snapshot cannot silently poison the combined BTC read.",
            "- Default posture is carry unless one of the explicit thresholds trips; this board is an operator concentration surface, not an automatic kill switch.",
            "- `m5_no_compression` only trips when the BTC M5 lane is active, BTC stays inside the defined carry band, and that lane shows no new trade event for `6h+`.",
            "- `Unassigned BTC Broker Inventory` is separate from the active managed BTC live-lane exposure, but it still changes user-visible MT5 equity and should be reviewed before treating lane-only BTC totals as the whole account truth.",
        ]
    )
    return "\n".join(lines).rstrip() + "\n"


def write_outputs(payload: dict[str, Any]) -> None:
    OUTPUT_JSON.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    OUTPUT_MD.write_text(render_markdown(payload), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Build compact combined BTC live concentration board")
    parser.add_argument("--json", action="store_true", help="Print JSON payload instead of markdown path summary")
    args = parser.parse_args()

    payload = build_payload()
    write_outputs(payload)

    if args.json:
        print(json.dumps(payload, indent=2))
    else:
        print(f"Wrote {OUTPUT_JSON}")
        print(f"Wrote {OUTPUT_MD}")
        summary = payload["summary"]
        print(
            f"Combined BTC floating {parse_float(summary.get('combined_floating_usd')):+.2f} | "
            f"net {parse_float(summary.get('combined_net_usd')):+.2f} | "
            f"triggered={','.join(summary.get('triggered_thresholds') or []) or 'none'}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
