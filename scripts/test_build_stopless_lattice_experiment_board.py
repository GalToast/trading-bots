from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from build_stopless_lattice_experiment_board import build_markdown, load_json, read_csv_rows


class StoplessLatticeExperimentBoardTests(unittest.TestCase):
    def test_build_markdown_surfaces_ranked_experiments(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)

            (root / "alpha_quick.csv").write_text(
                "\n".join(
                    [
                        "symbol,variant,days,baseline_combined_usd,baseline_closes,variant_combined_usd,variant_closes,variant_alpha_closes",
                        "GBPUSD,cool12_alpha50,7,10,100,25,200,200",
                        "GBPUSD,cool12_alpha100,7,10,100,35,200,200",
                        "EURUSD,cool12_alpha50,7,5,50,15,90,90",
                        "EURUSD,cool12_alpha100,7,5,50,20,90,90",
                        "NZDUSD,cool12_alpha50,7,-3,60,1,100,100",
                        "NZDUSD,cool12_alpha100,7,-3,60,4,100,100",
                    ]
                ),
                encoding="utf-8",
            )
            (root / "alpha_summary.csv").write_text(
                "\n".join(
                    [
                        "variant,baseline_total_usd,variant_total_usd,delta_total_usd",
                        "cool12_alpha50,12,41,29",
                        "cool12_alpha100,12,59,47",
                    ]
                ),
                encoding="utf-8",
            )
            (root / "inside.csv").write_text(
                "\n".join(
                    [
                        "symbol,baseline_combined_usd,repeat_combined_usd,delta_combined_usd,repeat_interior_reopens",
                        "GBPUSD,100,60,-40,20",
                        "EURUSD,80,30,-50,15",
                    ]
                ),
                encoding="utf-8",
            )
            (root / "canonical.csv").write_text(
                "\n".join(
                    [
                        "alpha,bars,cap,closes,daily,days,fires,net,step_b,step_s,step_usd,stop_usd,symbol,tf,type,worst,wr",
                        "0.5,1,20,100,1,60,1,123.45,2.0,1.0,,,EURUSD,M1,FX,-1,100",
                    ]
                ),
                encoding="utf-8",
            )
            (root / "asym.csv").write_text(
                "\n".join(
                    [
                        "name,sell_gap,buy_gap,alpha,mom,total,delta,mult",
                        "sell3_buy1_a100,3,1,1.0,False,99,40,2.0",
                        "sell2_buy1_a100,2,1,1.0,False,88,30,1.8",
                    ]
                ),
                encoding="utf-8",
            )
            (root / "ratio.json").write_text(
                '{"realized_usd": 31.31, "total_closes": 5431, "max_open_seen": 4, "n_attractors_used": 4}',
                encoding="utf-8",
            )

            markdown = build_markdown(
                alpha_rows=read_csv_rows(root / "alpha_quick.csv"),
                alpha_summary_rows=read_csv_rows(root / "alpha_summary.csv"),
                inside_rows=read_csv_rows(root / "inside.csv"),
                canonical_rows=read_csv_rows(root / "canonical.csv"),
                asym_rows=read_csv_rows(root / "asym.csv"),
                ratio_payload=load_json(root / "ratio.json"),
            )

        self.assertIn("# Stopless Lattice Experiment Board", markdown)
        self.assertIn("FX Close-Policy Ladder At Fixed Step", markdown)
        self.assertIn("Relationship-Lattice Expansion (Shadow Only)", markdown)
        self.assertIn("cool12_alpha100", markdown)
        self.assertIn("sell3_buy1_a100", markdown)
        self.assertIn("5431", markdown)


if __name__ == "__main__":
    unittest.main()
