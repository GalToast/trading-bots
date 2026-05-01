import unittest
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
import track_universal_m5_portfolio as tracker


class RenderUniversalM5PortfolioTest(unittest.TestCase):
    def test_extract_aggregates_multi_symbol_state(self) -> None:
        payload = {
            "metadata": {"step": 5.0},
            "updated_at": "2026-04-16T03:00:00+00:00",
            "runner": {"heartbeat_at": "2026-04-16T03:00:05+00:00"},
            "symbols": {
                "BTCUSD": {
                    "realized_closes": 3,
                    "realized_net_usd": 120.5,
                    "open_tickets": [{}, {}],
                    "anchor_resets": 1,
                    "anchor_resets_flat": 1,
                    "anchor_resets_risk": 0,
                },
                "ETHUSD": {
                    "realized_closes": 2,
                    "realized_net_usd": -20.5,
                    "open_tickets": [{}],
                    "anchor_resets": 2,
                    "anchor_resets_flat": 0,
                    "anchor_resets_risk": 1,
                },
            },
        }

        extracted = tracker.extract(payload)

        self.assertEqual(extracted["closes"], 5)
        self.assertEqual(extracted["net"], 100.0)
        self.assertEqual(extracted["open"], 3)
        self.assertEqual(extracted["resets"], 3)
        self.assertEqual(extracted["resets_flat"], 1)
        self.assertEqual(extracted["resets_risk"], 1)
        self.assertEqual(extracted["symbol_count"], 2)

    def test_render_includes_fx_coefficient_and_expansion_sections(self) -> None:
        now = "2026-04-14T19:15:00+00:00"
        rows = {
            "GBPUSD M5 1.5x": {
                "asset_class": "shadow_fx",
                "closes": 0,
                "net": 0.0,
                "cpr": 0.0,
                "open": 0,
                "resets": 0,
                "resets_flat": 0,
                "resets_risk": 0,
                "step": 0.000337,
                "updated": now,
            },
            "GBPUSD M5 1.0x": {
                "asset_class": "shadow_fx_coeff",
                "closes": 0,
                "net": 0.0,
                "cpr": 0.0,
                "open": 1,
                "resets": 0,
                "resets_flat": 0,
                "resets_risk": 0,
                "step": 0.00028,
                "updated": now,
            },
            "EURUSD M5 1.5x": {
                "asset_class": "shadow_fx_new",
                "closes": 0,
                "net": 0.0,
                "cpr": 0.0,
                "open": 0,
                "resets": 0,
                "resets_flat": 0,
                "resets_risk": 0,
                "step": 0.00036,
                "updated": now,
            },
        }

        rendered = tracker.render(now, rows)

        self.assertIn("### FX Core 1.5x", rendered)
        self.assertIn("### FX Coefficient Sweep", rendered)
        self.assertIn("### FX Expansion Pack", rendered)
        self.assertIn("| GBPUSD M5 1.0x |", rendered)
        self.assertIn("| EURUSD M5 1.5x |", rendered)
        self.assertIn("## Read", rendered)


if __name__ == "__main__":
    unittest.main()
