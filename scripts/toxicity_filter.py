#!/usr/bin/env python3
import json
from datetime import datetime, timezone
from pathlib import Path

class ToxicityFilter:
    def __init__(self, log_path: Path):
        self.log_path = log_path
        self.toxic_pids = {} # pid -> last_seen_ts

    def refresh(self):
        if not self.log_path.exists():
            return
        try:
            with open(self.log_path, "r") as f:
                # Read last 100 lines for efficiency
                lines = f.readlines()
                for line in lines[-100:]:
                    data = json.loads(line)
                    pid = data.get("product_id")
                    # If Coinbase detects toxic SHORT pressure, veto Kraken bids
                    if data.get("harpoon_action") == "SHADOW_SHORT":
                        self.toxic_pids[pid] = data.get("ts_utc")
        except:
            pass

    def is_toxic(self, pid: str, cooldown_seconds: int = 1800) -> bool:
        last_seen = self.toxic_pids.get(pid)
        if not last_seen: return False
        try:
            ts = datetime.fromisoformat(last_seen.replace("Z", "+00:00"))
            age = (datetime.now(timezone.utc) - ts).total_seconds()
            return age < cooldown_seconds
        except: return False
