from __future__ import annotations

import sys


def main() -> int:
    print(
        "Deprecated: patch_gemini_everywhere.py no longer edits mt5_bot_v10.py directly.\n"
        "Use apply_patch for worker changes and run:\n"
        "  python scripts/validate_canonical_runtime.py"
    )
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
