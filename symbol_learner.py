import json
import os
import time


class SymbolLearner:
    def __init__(self, path=None):
        self.path = path or os.path.join(os.path.dirname(__file__), "symbol_learner.json")
        self.state = self._load()

    def _load(self):
        try:
            with open(self.path, "r", encoding="utf-8") as handle:
                data = json.load(handle)
                if isinstance(data, dict):
                    return data
        except Exception:
            pass
        return {}

    def _save(self):
        try:
            with open(self.path, "w", encoding="utf-8") as handle:
                json.dump(self.state, handle, indent=2, sort_keys=True)
        except Exception:
            pass

    def _entry(self, symbol):
        normalized = str(symbol or "").upper()
        if not normalized:
            normalized = "UNKNOWN"
        entry = self.state.setdefault(
            normalized,
            {
                "atr_multiplier": 1.2,
                "confidence_bump": 0.0,
                "cooldown_until_ts": 0.0,
                "wins": 0,
                "losses": 0,
                "last_pnl": 0.0,
            },
        )
        return normalized, entry

    def get_cooldown(self, symbol):
        _normalized, entry = self._entry(symbol)
        cooldown_until = float(entry.get("cooldown_until_ts", 0.0) or 0.0)
        remaining_seconds = cooldown_until - time.time()
        if remaining_seconds <= 0:
            return None
        return remaining_seconds / 60.0

    def get_params(self, symbol):
        _normalized, entry = self._entry(symbol)
        return {
            "atr_multiplier": float(entry.get("atr_multiplier", 1.2) or 1.2),
            "confidence_bump": float(entry.get("confidence_bump", 0.0) or 0.0),
        }

    def record_outcome(self, symbol, pnl, mode, metadata=None):
        _normalized, entry = self._entry(symbol)
        pnl = float(pnl or 0.0)
        metadata = metadata or {}
        failure_reason = str(metadata.get("failure_reason") or "").upper()

        entry["last_pnl"] = pnl
        if pnl > 0:
            entry["wins"] = int(entry.get("wins", 0) or 0) + 1
            entry["cooldown_until_ts"] = 0.0
            entry["confidence_bump"] = 0.0
            entry["atr_multiplier"] = 1.2
        elif pnl < 0:
            entry["losses"] = int(entry.get("losses", 0) or 0) + 1
            cooldown_minutes = 0.0
            if "EARLY_FAIL" in failure_reason:
                cooldown_minutes = 30.0
            elif "WRONG_DIRECTION" in failure_reason:
                cooldown_minutes = 20.0
            elif "REVERSAL" in failure_reason:
                cooldown_minutes = 15.0
            elif abs(pnl) >= 100.0:
                cooldown_minutes = 45.0
            elif abs(pnl) >= 25.0:
                cooldown_minutes = 20.0
            elif abs(pnl) >= 5.0:
                cooldown_minutes = 10.0
            if cooldown_minutes > 0:
                entry["cooldown_until_ts"] = max(
                    float(entry.get("cooldown_until_ts", 0.0) or 0.0),
                    time.time() + cooldown_minutes * 60.0,
                )
            entry["confidence_bump"] = 0.0
            entry["atr_multiplier"] = 1.2

        self._save()
