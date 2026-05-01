import importlib.util
import sys
import unittest
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

MODULE_PATH = Path(__file__).with_name("benchmark_live_btcusd_h1_cap_ladder.py")
SPEC = importlib.util.spec_from_file_location("benchmark_live_btcusd_h1_cap_ladder", MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
benchmark = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = benchmark
SPEC.loader.exec_module(benchmark)


class LiveBTCUSDH1CapLadderTests(unittest.TestCase):
    def test_parse_caps_dedupes_and_preserves_order(self) -> None:
        self.assertEqual(benchmark.parse_caps("12, 20,20, 50"), [12, 20, 50])

    def test_rank_rows_prefers_higher_marked_net_then_realized(self) -> None:
        rows = [
            {"max_open_per_side": 50, "marked_net_usd": 10.0, "realized_net_usd": 9.0, "open_count": 5},
            {"max_open_per_side": 40, "marked_net_usd": 15.0, "realized_net_usd": 4.0, "open_count": 7},
            {"max_open_per_side": 30, "marked_net_usd": 10.0, "realized_net_usd": 11.0, "open_count": 6},
        ]
        ranked = benchmark.rank_rows(rows)
        self.assertEqual([row["max_open_per_side"] for row in ranked], [40, 30, 50])


if __name__ == "__main__":
    unittest.main()
