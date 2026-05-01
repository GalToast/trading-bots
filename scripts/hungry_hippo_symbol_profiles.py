from __future__ import annotations

from typing import Any


CRYPTO_SYMBOLS = {
    "ADAUSD",
    "AVAXUSD",
    "BCHUSD",
    "BNBUSD",
    "BTCUSD",
    "CFGUSD",
    "DOGEUSD",
    "DOTUSD",
    "ETHUSD",
    "GHSTUSD",
    "IOTXUSD",
    "LINKUSD",
    "LTCUSD",
    "NOMUSD",
    "RAVEUSD",
    "SOLUSD",
    "SUPUSD",
    "XRPUSD",
}
INDEX_SYMBOLS = {"NAS100", "US30"}
COMMODITY_SYMBOLS = {"XAGUSD", "XAUUSD"}

ASSET_CLASS_ESCAPE_DEFAULTS: dict[str, dict[str, float | int]] = {
    "fx": {"max_bars": 20, "max_escape_loss": 1.0, "cut_count": 1, "max_cut_loss": 5.0},
    "crypto": {"max_bars": 12, "max_escape_loss": 3.0, "cut_count": 1, "max_cut_loss": 5.0},
    "index": {"max_bars": 10, "max_escape_loss": 5.0, "cut_count": 1, "max_cut_loss": 10.0},
    "commodity": {"max_bars": 10, "max_escape_loss": 5.0, "cut_count": 1, "max_cut_loss": 10.0},
    "unknown": {"max_bars": 12, "max_escape_loss": 3.0, "cut_count": 1, "max_cut_loss": 5.0},
}

ASSET_CLASS_SESSION_DEFAULTS: dict[str, dict[str, Any]] = {
    "fx": {"window": "06:00-10:00+13:00-17:00", "off_hour_weight": 0.5},
    "crypto": {"window": "14:00-20:00", "off_hour_weight": 0.4},
    "index": {"window": "14:00-19:00", "off_hour_weight": 0.2},
    "commodity": {"window": "06:00-10:00+13:00-17:00", "off_hour_weight": 0.5},
    "unknown": {"window": "None", "off_hour_weight": 1.0},
}

SYMBOL_ESCAPE_OVERRIDES: dict[str, dict[str, float | int]] = {
    "AUDJPY": {"max_bars": 15, "max_escape_loss": 2.0, "cut_count": 1, "max_cut_loss": 5.0},
    "AUDUSD": {"max_bars": 20, "max_escape_loss": 1.0, "cut_count": 1, "max_cut_loss": 5.0},
    "BTCUSD": {"max_bars": 12, "max_escape_loss": 5.0, "cut_count": 2, "max_cut_loss": 10.0},
    "ETHUSD": {"max_bars": 15, "max_escape_loss": 3.0, "cut_count": 1, "max_cut_loss": 5.0},
    "EURJPY": {"max_bars": 15, "max_escape_loss": 2.0, "cut_count": 1, "max_cut_loss": 5.0},
    "EURUSD": {"max_bars": 20, "max_escape_loss": 1.0, "cut_count": 1, "max_cut_loss": 5.0},
    "GBPJPY": {"max_bars": 15, "max_escape_loss": 2.0, "cut_count": 1, "max_cut_loss": 5.0},
    "GBPUSD": {"max_bars": 20, "max_escape_loss": 1.0, "cut_count": 1, "max_cut_loss": 5.0},
    "NAS100": {"max_bars": 10, "max_escape_loss": 5.0, "cut_count": 1, "max_cut_loss": 10.0},
    "NZDUSD": {"max_bars": 20, "max_escape_loss": 1.0, "cut_count": 1, "max_cut_loss": 5.0},
    "SOLUSD": {"max_bars": 12, "max_escape_loss": 3.0, "cut_count": 1, "max_cut_loss": 5.0},
    "US30": {"max_bars": 10, "max_escape_loss": 5.0, "cut_count": 1, "max_cut_loss": 10.0},
    "USDCAD": {"max_bars": 20, "max_escape_loss": 1.0, "cut_count": 1, "max_cut_loss": 5.0},
    "USDCHF": {"max_bars": 20, "max_escape_loss": 1.0, "cut_count": 1, "max_cut_loss": 5.0},
    "USDJPY": {"max_bars": 20, "max_escape_loss": 1.0, "cut_count": 1, "max_cut_loss": 5.0},
    "XAGUSD": {"max_bars": 12, "max_escape_loss": 3.0, "cut_count": 1, "max_cut_loss": 5.0},
    "XAUUSD": {"max_bars": 10, "max_escape_loss": 5.0, "cut_count": 1, "max_cut_loss": 10.0},
    "XRPUSD": {"max_bars": 12, "max_escape_loss": 2.0, "cut_count": 1, "max_cut_loss": 5.0},
}

ASSET_CLASS_RUNTIME_DEFAULTS: dict[str, dict[str, Any]] = {
    "fx": {
        "timeframe": "M15",
        "base_step": 0.0004,
        "base_step_jpy": 0.04,
        "max_open_per_side": 12,
        "max_floating_loss_usd": -15.0,
        "breakout_buffer_pips": 5.0,
    },
    "crypto": {
        "timeframe": "M15",
        "base_step": 5.0,
        "max_open_per_side": 12,
        "max_floating_loss_usd": -15.0,
        "breakout_buffer_pips": 5.0,
    },
    "index": {
        "timeframe": "M15",
        "base_step": 10.0,
        "max_open_per_side": 12,
        "max_floating_loss_usd": -15.0,
        "breakout_buffer_pips": 5.0,
    },
    "commodity": {
        "timeframe": "M15",
        "base_step": 2.5,
        "max_open_per_side": 12,
        "max_floating_loss_usd": -15.0,
        "breakout_buffer_pips": 5.0,
    },
    "unknown": {
        "timeframe": "M15",
        "base_step": 1.0,
        "max_open_per_side": 12,
        "max_floating_loss_usd": -15.0,
        "breakout_buffer_pips": 5.0,
    },
}

SYMBOL_RUNTIME_OVERRIDES: dict[str, dict[str, Any]] = {
    # Low-priced crypto needs a symbol-aware base step instead of the generic BTC/ETH-sized fallback.
    "XRPUSD": {
        "base_step": 0.01,
    },
}


def infer_asset_class(symbol: str, kind: str = "") -> str:
    symbol = str(symbol or "").upper()
    kind = str(kind or "").lower()
    if symbol in CRYPTO_SYMBOLS or "crypto" in kind:
        return "crypto"
    if symbol in INDEX_SYMBOLS or "index" in kind:
        return "index"
    if symbol in COMMODITY_SYMBOLS or "commodity" in kind:
        return "commodity"
    if symbol.endswith("JPY") or (len(symbol) == 6 and symbol.isalpha()):
        return "fx"
    return "unknown"


def discover_symbols(*payloads: Any) -> list[str]:
    discovered: set[str] = set()

    def add(symbol: Any) -> None:
        normalized = str(symbol or "").strip().upper()
        if normalized:
            discovered.add(normalized)

    for payload in payloads:
        if not isinstance(payload, dict):
            continue
        symbols = payload.get("symbols")
        if isinstance(symbols, dict):
            for symbol in symbols.keys():
                add(symbol)
        elif isinstance(symbols, list):
            for row in symbols:
                if isinstance(row, dict):
                    add(row.get("symbol"))
        rows = payload.get("rows")
        if isinstance(rows, list):
            for row in rows:
                if isinstance(row, dict):
                    add(row.get("symbol"))
        session_windows = payload.get("session_windows")
        if isinstance(session_windows, dict):
            for symbol in session_windows.keys():
                add(symbol)
    return sorted(discovered)


def runtime_defaults_for_symbol(symbol: str, kind: str = "") -> dict[str, Any]:
    symbol = str(symbol or "").upper()
    asset_class = infer_asset_class(symbol, kind)
    defaults = dict(ASSET_CLASS_RUNTIME_DEFAULTS.get(asset_class, ASSET_CLASS_RUNTIME_DEFAULTS["unknown"]))
    defaults.update(SYMBOL_RUNTIME_OVERRIDES.get(symbol, {}))
    base_step = float(defaults.get("base_step", 1.0))
    if asset_class == "fx" and symbol.endswith("JPY"):
        base_step = float(defaults.get("base_step_jpy", base_step))
    defaults["asset_class"] = asset_class
    defaults["base_step"] = base_step
    return defaults


def default_session_profile_for_symbol(symbol: str, kind: str = "") -> dict[str, Any]:
    symbol = str(symbol or "").upper()
    asset_class = infer_asset_class(symbol, kind)
    defaults = dict(ASSET_CLASS_SESSION_DEFAULTS.get(asset_class, ASSET_CLASS_SESSION_DEFAULTS["unknown"]))
    defaults["source"] = "derived_family_defaults"
    return defaults


def escape_defaults_for_symbol(
    symbol: str,
    kind: str = "",
    *,
    atr_current: float | None = None,
    reference_step: float | None = None,
) -> dict[str, float | int]:
    symbol = str(symbol or "").upper()
    asset_class = infer_asset_class(symbol, kind)
    if symbol in SYMBOL_ESCAPE_OVERRIDES:
        defaults = dict(SYMBOL_ESCAPE_OVERRIDES[symbol])
    else:
        defaults = dict(ASSET_CLASS_ESCAPE_DEFAULTS.get(asset_class, ASSET_CLASS_ESCAPE_DEFAULTS["unknown"]))

    scale = max(abs(float(atr_current or 0.0)), abs(float(reference_step or 0.0)))
    if asset_class == "crypto" and symbol not in SYMBOL_ESCAPE_OVERRIDES:
        if scale >= 100.0:
            defaults.update({"max_bars": 12, "max_escape_loss": 5.0, "cut_count": 2, "max_cut_loss": 10.0})
        elif scale >= 1.0:
            defaults.update({"max_bars": 15, "max_escape_loss": 3.0, "cut_count": 1, "max_cut_loss": 5.0})
        else:
            defaults.update({"max_bars": 12, "max_escape_loss": 2.0, "cut_count": 1, "max_cut_loss": 5.0})
    return defaults
