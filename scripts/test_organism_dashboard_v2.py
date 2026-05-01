#!/usr/bin/env python3
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory


SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import organism_dashboard_v2 as dashboard


class OrganismDashboardV2Tests(unittest.TestCase):
    def test_time_ago_accepts_naive_iso_strings(self) -> None:
        value = dashboard.time_ago("2026-04-15T23:24:48")
        self.assertNotEqual(value, "parse error")

    def test_iter_enabled_live_lanes_excludes_registry_disabled_rows(self) -> None:
        original_configs = dashboard.CONFIGS
        try:
            with TemporaryDirectory() as tmpdir:
                tmp = Path(tmpdir)
                configs = tmp / "configs"
                configs.mkdir(parents=True, exist_ok=True)
                (configs / "penetration_lattice_runner_registry.json").write_text(
                    """
{
  "lanes": [
    {"name": "live_rearm_941777", "kind": "live_fx", "enabled": true},
    {"name": "live_btcusd_m5_warp_probation_941780", "kind": "live_crypto", "enabled": false, "pause_note": "paused_for_test"},
    {"name": "shadow_gbpusd_tick_forward", "kind": "shadow_fx", "enabled": true}
  ]
}
""".strip(),
                    encoding="utf-8",
                )
                dashboard.CONFIGS = configs

                rows = dashboard.iter_enabled_live_lanes()
        finally:
            dashboard.CONFIGS = original_configs

        self.assertEqual([row["name"] for row in rows], ["live_rearm_941777"])


if __name__ == "__main__":
    unittest.main()
