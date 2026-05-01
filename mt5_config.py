import os
from pathlib import Path


def _load_env_file():
    env_path = Path(__file__).with_name(".env")
    if not env_path.exists():
        return

    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ[key.strip()] = value.strip()


_load_env_file()

_missing = [key for key in ("MT5_LOGIN", "MT5_PASSWORD") if not os.environ.get(key)]
if _missing:
    missing_text = ", ".join(_missing)
    raise RuntimeError(
        f"Missing MT5 runtime configuration: {missing_text}. "
        "Set these environment variables only in a local .env file; do not commit credentials."
    )

LOGIN = int(os.environ["MT5_LOGIN"])
PASSWORD = os.environ["MT5_PASSWORD"]
SERVER = os.environ.get("MT5_SERVER", "Hugosway-Demo")
BOT_MAGIC = int(os.environ.get("MT5_BOT_MAGIC", "100078"))
BOT_COMMENT_PREFIX = os.environ.get("MT5_BOT_COMMENT_PREFIX", "V10")
