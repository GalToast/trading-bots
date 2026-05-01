#!/usr/bin/env python3
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path


SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import build_kraken_maker_live_readiness_board as board


def write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")


def next_proof_payload(
    *,
    closes: int,
    losses: int,
    ghosts: int,
    open_positions: int,
    net: float,
    lane: str = "parallel_ratio50_taker_guard",
) -> dict:
    return {
        "summary": {
            "primary_lane": lane,
            "primary_status": "collect_more" if closes < 20 else "eligible_for_next_shadow_stage",
            "next_action": f"monitor_{lane}_until_20_clean_closes_20_ghost_marks_and_top3_exercised",
        },
        lane: {
            "status": "collect_more" if closes < 20 else "eligible_for_next_shadow_stage",
            "closes": closes,
            "losses": losses,
            "ghost_marks": ghosts,
            "open_positions": open_positions,
            "max_concurrent_positions": 3,
            "realized_net_usd": net,
            "closes_remaining": max(0, 20 - closes),
            "ghost_marks_remaining": max(0, 20 - ghosts),
        },
    }


def radar_payload() -> dict:
    return {
        "rows": [
            {
                "product_id": "HOUSE-USD",
                "rest_pair": "HOUSEUSD",
                "min_notional_usd": 1.0,
                "can_trade_starting_cash": True,
                "order_min_base": 1.0,
                "cost_min": 1.0,
                "spread_bps": 120.0,
                "samples": 20,
            }
        ]
    }


def closed_trade(product: str = "HOUSE-USD", net: float = 0.21, exit_fee_bps: float = 40.0) -> dict:
    return {
        "action": "close_maker_shadow",
        "product_id": product,
        "net": net,
        "spread_bps": 110.0,
        "exit_fee_bps": exit_fee_bps,
    }


def open_trade(product: str = "HOUSE-USD", quote_usd: float = 10.0) -> dict:
    return {
        "action": "open_maker_shadow",
        "product_id": product,
        "quote_usd": quote_usd,
    }


def empty_live_fill_path(root: Path) -> Path:
    path = root / "live-fill.json"
    write_json(path, {})
    return path


class KrakenMakerLiveReadinessBoardTests(unittest.TestCase):
    def test_immature_shadow_blocks_live_probe(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            next_proof = root / "next.json"
            radar = root / "radar.json"
            events = root / "events.jsonl"
            live_fill = empty_live_fill_path(root)
            write_json(next_proof, next_proof_payload(closes=7, losses=0, ghosts=28, open_positions=0, net=3.79))
            write_json(radar, radar_payload())
            write_jsonl(events, [closed_trade()])

            payload = board.build_payload(
                next_proof_path=next_proof,
                radar_path=radar,
                events_path=events,
                live_fill_telemetry_path=live_fill,
            )

            self.assertEqual(payload["summary"]["verdict"], "shadow_collect_more")
            self.assertIn("shadow_maturity_not_met", payload["summary"]["blockers"])
            self.assertFalse(payload["live_evidence"]["shadow_mature"])
            self.assertTrue(payload["live_evidence"]["product_minimums_clear"])

    def test_mature_shadow_requests_validate_only_before_live_probe(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            next_proof = root / "next.json"
            radar = root / "radar.json"
            events = root / "events.jsonl"
            live_fill = empty_live_fill_path(root)
            write_json(next_proof, next_proof_payload(closes=20, losses=0, ghosts=25, open_positions=0, net=8.0))
            write_json(radar, radar_payload())
            write_jsonl(events, [closed_trade() for _ in range(20)])

            payload = board.build_payload(
                next_proof_path=next_proof,
                radar_path=radar,
                events_path=events,
                live_fill_telemetry_path=live_fill,
            )

            self.assertEqual(payload["summary"]["verdict"], "needs_validate_only_probe")
            self.assertNotIn("shadow_maturity_not_met", payload["summary"]["blockers"])
            self.assertIn("post_only_validate_order_not_recorded", payload["summary"]["blockers"])
            self.assertIn("no_live_fill_telemetry", payload["summary"]["blockers"])
            self.assertEqual(payload["summary"]["product_minimum_blockers"], [])
            self.assertEqual(payload["summary"]["recommended_probe_quote_usd"], 1.02)

    def test_product_minimum_blocker_recommends_probe_quote_cushion(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            next_proof = root / "next.json"
            radar = root / "radar.json"
            events = root / "events.jsonl"
            live_fill = empty_live_fill_path(root)
            payload = radar_payload()
            payload["rows"][0]["min_notional_usd"] = 8.97
            write_json(next_proof, next_proof_payload(closes=20, losses=0, ghosts=25, open_positions=0, net=8.0))
            write_json(radar, payload)
            write_jsonl(events, [closed_trade() for _ in range(20)])

            readiness = board.build_payload(
                next_proof_path=next_proof,
                radar_path=radar,
                events_path=events,
                live_fill_telemetry_path=live_fill,
                max_quote_usd=8.0,
            )

            self.assertEqual(readiness["summary"]["verdict"], "blocked_by_product_minimums")
            self.assertEqual(readiness["summary"]["product_minimum_blockers"], ["HOUSE-USD"])
            self.assertEqual(readiness["summary"]["min_required_quote_usd"], 8.97)
            self.assertEqual(readiness["summary"]["recommended_probe_quote_usd"], 9.15)

    def test_default_event_path_follows_primary_lane_and_flags_mixed_quote_tape(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            next_proof = root / "next.json"
            radar = root / "radar.json"
            default_events = root / "old-live-exec-events.jsonl"
            fast_events = root / "fast-cooldown-events.jsonl"
            live_fill = empty_live_fill_path(root)
            lane = "parallel_ratio50_taker_guard_live_exec_fast_cooldown"
            write_json(
                next_proof,
                next_proof_payload(closes=20, losses=0, ghosts=25, open_positions=0, net=8.0, lane=lane),
            )
            write_json(radar, radar_payload())
            write_jsonl(default_events, [open_trade(quote_usd=25.0), closed_trade()])
            write_jsonl(fast_events, [open_trade(quote_usd=10.0), open_trade(quote_usd=12.0), closed_trade()])

            original_default = board.DEFAULT_EVENTS_PATH
            original_lane_paths = dict(board.LANE_EVENTS_PATHS)
            try:
                board.DEFAULT_EVENTS_PATH = default_events
                board.LANE_EVENTS_PATHS[lane] = fast_events
                payload = board.build_payload(
                    next_proof_path=next_proof,
                    radar_path=radar,
                    events_path=default_events,
                    live_fill_telemetry_path=live_fill,
                )
            finally:
                board.DEFAULT_EVENTS_PATH = original_default
                board.LANE_EVENTS_PATHS.clear()
                board.LANE_EVENTS_PATHS.update(original_lane_paths)

            self.assertEqual(Path(payload["parameters"]["events_path"]), fast_events)
            self.assertEqual(Path(payload["parameters"]["requested_events_path"]), default_events)
            self.assertEqual(payload["parameters"]["max_quote_usd"], 12.0)
            self.assertEqual(payload["parameters"]["requested_max_quote_usd"], 10.0)
            self.assertEqual(payload["event_summary"]["quote_usd_values"], [10.0, 12.0])
            self.assertTrue(payload["event_summary"]["mixed_quote_sizes"])

    def test_full_evidence_switches_to_live_probe_review(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            next_proof = root / "next.json"
            radar = root / "radar.json"
            events = root / "events.jsonl"
            live_fill = empty_live_fill_path(root)
            rows = [closed_trade() for _ in range(20)]
            rows.append({"action": "kraken_validate_order", "product_id": "HOUSE-USD"})
            rows.append({"action": "live_close_fill", "product_id": "HOUSE-USD", "net": 0.08})
            write_json(next_proof, next_proof_payload(closes=20, losses=0, ghosts=25, open_positions=0, net=8.0))
            write_json(radar, radar_payload())
            write_jsonl(events, rows)

            payload = board.build_payload(
                next_proof_path=next_proof,
                radar_path=radar,
                events_path=events,
                live_fill_telemetry_path=live_fill,
            )

            self.assertEqual(payload["summary"]["verdict"], "live_probe_evidence_present")
            self.assertEqual(payload["summary"]["blockers"], [])
            self.assertTrue(payload["live_evidence"]["post_only_validate_order_recorded"])
            self.assertTrue(payload["live_evidence"]["live_order_telemetry_present"])

    def test_failed_validate_only_event_does_not_clear_blocker(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            next_proof = root / "next.json"
            radar = root / "radar.json"
            events = root / "events.jsonl"
            live_fill = empty_live_fill_path(root)
            rows = [closed_trade() for _ in range(20)]
            rows.append(
                {
                    "action": "kraken_validate_order",
                    "product_id": "HOUSE-USD",
                    "ok": False,
                    "status": "validate_failed",
                }
            )
            write_json(next_proof, next_proof_payload(closes=20, losses=0, ghosts=25, open_positions=0, net=8.0))
            write_json(radar, radar_payload())
            write_jsonl(events, rows)

            payload = board.build_payload(
                next_proof_path=next_proof,
                radar_path=radar,
                events_path=events,
                live_fill_telemetry_path=live_fill,
            )

            self.assertEqual(payload["summary"]["verdict"], "needs_validate_only_probe")
            self.assertFalse(payload["live_evidence"]["post_only_validate_order_recorded"])
            self.assertEqual(payload["live_evidence"]["post_only_validate_order_success_count"], 0)
            self.assertEqual(payload["live_evidence"]["post_only_validate_order_failure_count"], 1)

    def test_write_reports(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            payload = board.build_payload(
                next_proof_path=root / "missing-next.json",
                radar_path=root / "missing-radar.json",
                events_path=root / "missing-events.jsonl",
                live_fill_telemetry_path=empty_live_fill_path(root),
            )
            json_path = root / "readiness.json"
            md_path = root / "readiness.md"

            board.write_reports(payload, json_path=json_path, md_path=md_path)

            self.assertTrue(json_path.exists())
            self.assertTrue(md_path.exists())
            self.assertIn("Kraken Maker Live Readiness Board", md_path.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
