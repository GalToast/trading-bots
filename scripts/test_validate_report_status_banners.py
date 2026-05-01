#!/usr/bin/env python3
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory


SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import validate_report_status_banners as validator


class ValidateReportStatusBannersTests(unittest.TestCase):
    def test_classify_banner_detects_current_and_historical(self) -> None:
        self.assertEqual(
            validator.classify_banner("> Current runtime authority board.\n# Title"),
            validator.CURRENT_RUNTIME,
        )
        self.assertEqual(
            validator.classify_banner("> Historical snapshot only.\n# Title"),
            validator.HISTORICAL,
        )
        self.assertIsNone(validator.classify_banner("# Title\nNo banner here"))

    def test_validate_targets_flags_missing_banner(self) -> None:
        with TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            current_path = root / "reports" / "README.md"
            historical_path = root / "reports" / "old.md"
            current_path.parent.mkdir(parents=True, exist_ok=True)
            current_path.write_text(
                "# Reports Start Here\n\n> Current runtime landing page.\n",
                encoding="utf-8",
            )
            historical_path.write_text(
                "# Old Report\n\nNo banner\n",
                encoding="utf-8",
            )

            result = validator.validate_targets(
                root=root,
                expectations={
                    "reports/README.md": validator.CURRENT_RUNTIME,
                    "reports/old.md": validator.HISTORICAL,
                },
            )

        self.assertFalse(result["ok"])
        self.assertIn(
            "missing_or_wrong_banner: reports/old.md expected=historical found=missing",
            result["errors"],
        )


if __name__ == "__main__":
    unittest.main()
