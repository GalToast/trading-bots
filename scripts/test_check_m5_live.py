#!/usr/bin/env python3
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from unittest.mock import patch


SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import check_m5_live


class CheckM5LiveTests(unittest.TestCase):
    def test_main_prints_trigger_watch_summary(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            reports = Path(tmp)
            state_payload = {
                "runner": {"heartbeat_at": "2026-04-17T20:00:00+00:00"},
                "symbols": {
                    "ETHUSD": {
                        "timeframe": "M5",
                        "realized_closes": 0,
                        "realized_net_usd": 0.0,
                        "anchor_resets": 3,
                        "open_tickets": [],
                    }
                },
            }
            (reports / "penetration_lattice_live_ethusd_m5_warp_5_state.json").write_text(
                json.dumps(state_payload), encoding="utf-8"
            )
            trigger_payload = {
                "summary": {"spread_admissible_count": 5, "probe_count": 5, "crossed_count": 0, "waiting_for_first_fill_count": 5},
                "watch_order_by_steps": ["XRPUSD", "ADAUSD", "SOLUSD"],
                "rows": [
                    {"symbol": "XRPUSD", "nearest_side": "SELL", "nearest_gap_steps": 0.577, "spread_gate_status": "admissible_now", "execution_read": "waiting_for_first_fill"},
                    {"symbol": "ADAUSD", "nearest_side": "BUY", "nearest_gap_steps": 1.100, "spread_gate_status": "admissible_now", "execution_read": "waiting_for_first_fill"},
                ],
            }
            (reports / "live_crypto_trigger_proximity_board.json").write_text(
                json.dumps(trigger_payload), encoding="utf-8"
            )

            buf = StringIO()
            with patch.object(check_m5_live, "REPORTS", reports), patch.object(
                check_m5_live, "TRIGGER_BOARD_JSON", reports / "live_crypto_trigger_proximity_board.json"
            ):
                with redirect_stdout(buf):
                    check_m5_live.main()

        output = buf.getvalue()
        self.assertIn("ETH M5 LIVE STEP5: tf=M5, closes=0, net=$0.00, resets=3, open=0, hb=2026-04-17T20:00:00+00:00", output)
        self.assertIn("Trigger watch:", output)
        self.assertIn("order=['XRPUSD', 'ADAUSD', 'SOLUSD']", output)
        self.assertIn("waiting_first_fill=5", output)
        self.assertIn("XRPUSD SELL 0.577 step gate=admissible_now read=waiting_for_first_fill", output)


if __name__ == "__main__":
    unittest.main()
