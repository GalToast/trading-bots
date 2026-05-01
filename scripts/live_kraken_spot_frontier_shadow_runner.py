#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from dataclasses import asdict, dataclass
from datetime import timezone, datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
if str(Path(__file__).resolve().parent) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parent))

import kraken_config as cfg
from kraken_spot_client import KrakenSpotClient, to_float
from mfe_capture_tracker import MFETracker
from toxicity_filter import ToxicityFilter
from death_spiral_prevention import LossTracker
from volatility_targets import AdaptiveTargetCalculator


REPORTS = ROOT / "reports"
DEFAULT_STATE_PATH = REPORTS / "kraken_spot_frontier_shadow_state.json"
DEFAULT_EVENT_PATH = REPORTS / "kraken_spot_frontier_shadow_events.jsonl"
FRONTIER_BOARD_PATH = REPORTS / "kraken_spot_frontier_strategy_board.json"
BEAR_VELOCITY_PATH = REPORTS / "kraken_spot_money_velocity_board.json"
MFE_TRACKER_PATH = REPORTS / "kraken_spot_frontier_mfe_tracker.json"
TOXIC_VETO_PATH = REPORTS / "kraken_toxic_veto.json"
MANIFEST_PATH = REPORTS / "structural_alpha_manifest.json"
LOSS_TRACKER_PATH = REPORTS / "kraken_frontier_loss_tracker.json"
LIVE_FOUNDRY_FEATURES_PATH = REPORTS / "kraken_spot_live_foundry_features.json"
FORCE_BLOCK = {"AKE-USD"}


@dataclass
class KrakenPosition:
    product_id: str
    verdict: str
    entry_price: float
    quantity: float
    cost_usd: float
    opened_at: str
    highest_price: float
    trail_pct: float
    target_pct: float
    stop_pct: float
    tail_prob: float
    fg_prob: float
    entry_type: str = "taker"
    entry_fee_bps: float = 40.0
    exit_fee_bps: float = 40.0
    status: str = "open"


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def load_json(path: Path) -> Any:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def append_jsonl(path: Path, row: dict[str, Any]) -> None:
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(row) + "\n")


class KrakenFrontierShadowRunner:
    def __init__(
        self,
        starting_cash: float = 100.0,
        max_positions: int = 15,
        deploy_pct: float = 0.15,
        default_trail_pct: float = 0.015,
        default_target_pct: float = 0.05,
        default_stop_pct: float = 0.03,
        reentry_cooldown_polls: int = 120, # 1 hour cooldown
        require_cluster_size_threshold: int = 20,
    ):
        self.cash = starting_cash
        self.max_positions = max_positions
        self.deploy_pct = deploy_pct
        self.default_trail_pct = default_trail_pct
        self.default_target_pct = default_target_pct
        self.default_stop_pct = default_stop_pct
        self.reentry_cooldown_polls = reentry_cooldown_polls
        self.require_cluster_size_threshold = require_cluster_size_threshold
        self.positions: dict[str, KrakenPosition] = {}
        self.exit_history: dict[str, int] = {} # product_id -> last_exit_poll_index
        self.poll_count = 0
        self.client = KrakenSpotClient()
        self.mfe = MFETracker(default_fee_bps=80.0, output_path=MFE_TRACKER_PATH)
        self.mfe.load()
        self.toxicity = ToxicityFilter(ROOT / "reports" / "neural_harpoon_shadow_log.jsonl")
        self.alpha_manifest: dict[str, dict[str, Any]] = {}
        self.tracker = LossTracker(
            max_consecutive_losses=3,
            cooldown_seconds=3600,
            state_path=LOSS_TRACKER_PATH
        )
        self.foundry_lookup = {}
        self.current_cluster_size = 0

    def load_state(self, path: Path):
        if path.exists():
            state = load_json(path)
            self.cash = state.get("cash", self.cash)
            self.poll_count = state.get("poll_count", 0)
            self.exit_history = state.get("exit_history", {})
            for p in state.get("positions", []):
                self.positions[p["product_id"]] = KrakenPosition(**p)

    def save_state(self, path: Path):
        state = {
            "cash": self.cash,
            "poll_count": self.poll_count,
            "exit_history": self.exit_history,
            "positions": [asdict(p) for p in self.positions.values()],
            "updated_at": utc_now_iso()
        }
        path.write_text(json.dumps(state, indent=2), encoding="utf-8")
        self.mfe.save()
        self.tracker.save()

    def get_fresh_tickers(self, product_ids: list[str]) -> dict[str, dict[str, Any]]:
        if not product_ids:
            return {}
        # Kraken REST ticker uses rest_pairs (e.g. XBTUSD)
        # product_id is wsname-style (e.g. XBT-USD)
        rest_pairs = [pid.replace("-", "") for pid in product_ids]
        try:
            payload = self.client.ticker(rest_pairs)
            # Map back to product_id
            results = {}
            for rest_pair, data in payload.items():
                results[rest_pair] = data
            return results
        except Exception as e:
            print(f"Ticker fetch error: {e}")
            return {}

    def execution_mode(self) -> str:
        if self.current_cluster_size < self.require_cluster_size_threshold:
            return "idiosyncratic"
        return "systemic"

    def exit_profile(self, *, product_id: str, row: dict[str, Any] | None, maker: bool, suggested_trail: float, price: float) -> dict[str, float]:
        entry_fee_bps = 25.0 if maker else 40.0
        exit_fee_bps = 40.0
        fee_drag_pct = (entry_fee_bps + exit_fee_bps) / 10000.0
        live = self.foundry_lookup.get(product_id, {})
        atr_pct = to_float(live.get("atr_12_pct")) / 100.0
        spread_bps = to_float((row or {}).get("spread_bps"))
        spread_pct = max(0.0, spread_bps / 10000.0)

        # Minimums based on fees and spread
        min_target = max(0.003, fee_drag_pct + spread_pct + 0.001)

        calc = AdaptiveTargetCalculator(
            atr_multiplier=2.0,
            stop_multiplier=1.0,
            trail_giveback_pct=self.default_trail_pct * 100.0,
            min_target_pct=min_target * 100.0,
            max_target_pct=10.0,
        )

        atr_price = price * atr_pct if atr_pct > 0 else price * 0.005 # 0.5% default if unknown
        target_pct_100, stop_pct_100, trail_pct_100 = calc.get_targets(price, atr_price)

        target_pct = target_pct_100 / 100.0
        stop_pct = stop_pct_100 / 100.0
        
        # Manifest/suggested override
        if suggested_trail > 0:
            trail_pct = suggested_trail / 100.0
        else:
            trail_pct = trail_pct_100 / 100.0

        return {
            "target_pct": target_pct,
            "trail_pct": trail_pct,
            "stop_pct": stop_pct,
            "entry_fee_bps": entry_fee_bps,
            "exit_fee_bps": exit_fee_bps,
        }

    def run_poll(self):
        self.poll_count += 1
        print(f"--- POLL {self.poll_count} | {utc_now_iso()} | Cash: ${self.cash:.2f} | Open: {len(self.positions)} ---")
        
        # 1. Load Strategy, Veto, and Foundry Features
        frontier = load_json(FRONTIER_BOARD_PATH)
        bear_veto = load_json(BEAR_VELOCITY_PATH)
        foundry_features = load_json(LIVE_FOUNDRY_FEATURES_PATH)
        manifest_payload = load_json(MANIFEST_PATH)
        self.alpha_manifest = {r["product_id"]: r for r in manifest_payload.get("manifest", [])}
        vetoed = {str(row["product_id"]) for row in bear_veto.get("direct_dump_rows", [])}
        
        # Neural Toxicity Filter
        self.toxicity.refresh()
        
        # Self-correcting lookup for ATR
        self.foundry_lookup = foundry_features
        
        # Update Cluster Size
        candidates = frontier.get("rows", [])
        self.current_cluster_size = len(candidates)
        mode = self.execution_mode()

        # 2. Update existing positions
        if self.positions:
            pids = list(self.positions.keys())
            tickers = self.get_fresh_tickers(pids)
            for pid, pos in list(self.positions.items()):
                data = None
                clean_pid = pid.replace("-", "")
                for k, v in tickers.items():
                    if clean_pid in k:
                        data = v
                        break
                
                if not data:
                    continue
                
                current_bid = to_float(data.get("b", [0])[0])
                current_ask = to_float(data.get("a", [0])[0])
                
                if current_bid > pos.highest_price:
                    pos.highest_price = current_bid
                    self.mfe.on_heartbeat(pid, current_bid)
                
                # Check Exit: Trailing
                drawdown = (pos.highest_price - current_bid) / pos.highest_price if pos.highest_price > 0 else 0
                if drawdown >= pos.trail_pct:
                    self.exit_position(pos, current_bid, "trail_hit")
                    continue
                
                # Check Exit: Stop
                loss = (pos.entry_price - current_bid) / pos.entry_price
                if loss >= pos.stop_pct:
                    self.exit_position(pos, current_bid, "stop_hit")
                    continue

                # Check Exit: Target
                gain = (current_bid - pos.entry_price) / pos.entry_price
                if gain >= pos.target_pct:
                    self.exit_position(pos, current_bid, "target_hit")
                    continue

                # RENT HARVEST (for Maker entries in Taker lane)
                # We don't track spread at entry in Taker runner yet, 
                # but we can check if gain > 1% (typical spread).
                if "MAKER" in str(pos.verdict) and gain >= 0.015:
                    self.exit_position(pos, current_bid, "maker_rent_harvest")
                    continue

        # 3. Check for entries
        if len(self.positions) < self.max_positions:
            # Solitary Mycelium Filter: If systemic, only consider top-1
            if mode == "systemic":
                candidates.sort(key=lambda x: self.alpha_manifest.get(x["product_id"], {}).get("heat_score", 0.0), reverse=True)
                candidates = candidates[:1]

            for row in candidates:
                pid = str(row["product_id"])
                if pid in self.positions or pid in vetoed or pid in FORCE_BLOCK:
                    continue
                
                # Toxicity check
                if self.toxicity.is_toxic(pid):
                    print(f"  VETO (TOXICITY): {pid}")
                    continue

                # Death Spiral check
                if self.tracker.is_blocked(pid):
                    print(f"  VETO (DEATH SPIRAL): {pid}")
                    continue
                
                # Cooldown check
                last_exit = self.exit_history.get(pid, -999)
                if self.poll_count - last_exit < self.reentry_cooldown_polls:
                    continue

                # Entry Gate: MER + spread filter (ML scores are Coinbase-only, always zero on Kraken)
                # The Nut Cracker model was trained on Coinbase foundry geometry — it can't score
                # Kraken products because ml_feature_row() hardcodes fake feature values.
                # Instead, we use the proven signals we actually have:
                # - MER >= 0.60 (edge quality — the Maker runner uses this successfully)
                # - spread < 500 bps (cost control — spreads this wide eat any edge)
                mer = to_float(row.get("mer"))
                spread = to_float(row.get("spread_bps"))

                # Nut Cracker scores are available when ML model is loaded, but they default
                # to ~0 for Kraken. Use them ONLY if they're meaningfully non-zero.
                tail_prob = to_float(row.get("tail_prob"))
                fg_prob = to_float(row.get("fast_green_prob"))
                ml_active = tail_prob > 0.01 or fg_prob > 0.01  # Model actually scored something

                if ml_active:
                    # ML is meaningful — use Nut Cracker gate as originally designed
                    is_nut_cracker = tail_prob >= 0.70 and fg_prob >= 0.70
                    passes_ml_gate = is_nut_cracker or tail_prob >= 0.65 or fg_prob >= 0.60
                else:
                    # ML is decorative (Coinbase model on Kraken products) — fall back to MER gate
                    passes_ml_gate = mer >= 0.60

                # Spread gate: reject if spread would eat the entire edge
                passes_spread_gate = spread < 500  # 5% spread = fees destroy any edge

                if passes_ml_gate and passes_spread_gate:
                    # Determine entry type
                    spread = to_float(row.get("spread_bps"))
                    use_maker = spread > 50.0 # Force maker entry for wide spreads
                    self.enter_position(row, maker=use_maker)
                    if len(self.positions) >= self.max_positions:
                        break

    def enter_position(self, row: dict[str, Any], maker: bool = False):
        pid = str(row["product_id"])
        ticker = self.get_fresh_tickers([pid])
        data = None
        clean_pid = pid.replace("-", "")
        for k, v in ticker.items():
            if clean_pid in k:
                data = v
                break
        
        if not data:
            return
            
        ask = to_float(data.get("a", [0])[0])
        bid = to_float(data.get("b", [0])[0])
        
        # Modeling entry price
        # Maker: we join the bid. Shadow fill assume we get hit.
        # Taker: we hit the ask.
        entry_price = bid if maker else ask
        fee_bps = 25.0 if maker else 40.0 # Kraken maker fee is ~25bps at base tier

        if entry_price <= 0:
            return

        # GENERATIVE STRATEGY MUTATION: MANIFEST LOOKUP
        manifest = self.alpha_manifest.get(pid, {})
        size_mult = to_float(manifest.get("suggested_size_mult"), default=1.0)
        suggested_trail = to_float(manifest.get("suggested_trail_pct"), default=0.0)

        # Scale deploy_pct if systemic mode is active?
        # For now, just use manifest size_mult.
        cost = self.cash * (self.deploy_pct * size_mult)
        if cost > self.cash:
            cost = self.cash

        fee_usd = cost * (fee_bps / 10000.0)
        quantity = (cost - fee_usd) / entry_price

        exit_profile = self.exit_profile(product_id=pid, row=row, maker=maker, suggested_trail=suggested_trail, price=entry_price)

        pos = KrakenPosition(
            product_id=pid,
            verdict=row.get("verdict", "unknown"),
            entry_price=entry_price,
            quantity=quantity,
            cost_usd=cost,
            opened_at=utc_now_iso(),
            highest_price=entry_price,
            trail_pct=exit_profile["trail_pct"],
            target_pct=exit_profile["target_pct"],
            stop_pct=exit_profile["stop_pct"],
            tail_prob=to_float(row.get("tail_prob")),
            fg_prob=to_float(row.get("fast_green_prob")),
            entry_type="maker" if maker else "taker",
            entry_fee_bps=exit_profile["entry_fee_bps"],
            exit_fee_bps=exit_profile["exit_fee_bps"],
        )
        
        self.positions[pid] = pos
        self.cash -= cost
        self.mfe.on_entry(pid, pid, entry_price, predicted_mfe_pct=pos.target_pct, fee_bps=pos.entry_fee_bps + pos.exit_fee_bps)
        
        event = {
            "time": utc_now_iso(),
            "event": "entry",
            "mode": f"{self.execution_mode().upper()}_{'MAKER' if maker else 'TAKER'}",
            "product_id": pid,
            "price": entry_price,
            "cost": cost,
            "quantity": quantity,
            "target_pct": pos.target_pct,
            "trail_pct": pos.trail_pct,
            "stop_pct": pos.stop_pct,
            "entry_fee_bps": pos.entry_fee_bps,
            "exit_fee_bps": pos.exit_fee_bps,
            "tail_prob": pos.tail_prob,
            "fg_prob": pos.fg_prob
        }
        append_jsonl(DEFAULT_EVENT_PATH, event)
        print(f"ENTERED ({event['mode']}): {pid} at ${entry_price:.6f}")

    def exit_position(self, pos: KrakenPosition, price: float, reason: str):
        # Modeling exit: Shadow runner always hits bid for exit (taker)
        gross_proceeds = pos.quantity * price
        fee = gross_proceeds * (pos.exit_fee_bps / 10000.0)
        net_proceeds = gross_proceeds - fee
        
        pnl_usd = net_proceeds - pos.cost_usd
        pnl_pct = (pnl_usd / pos.cost_usd) * 100.0
        
        self.cash += net_proceeds
        
        # Cooldown only for negative/trail/stop exits
        if reason != "target_hit":
            self.exit_history[pos.product_id] = self.poll_count
        
        # Record outcome in LossTracker
        self.tracker.record_close(pos.product_id, won=(pnl_usd > 0))
            
        del self.positions[pos.product_id]
        
        self.mfe.on_exit(pos.product_id, price)
        
        event = {
            "time": utc_now_iso(),
            "event": "exit",
            "product_id": pos.product_id,
            "price": price,
            "proceeds": net_proceeds,
            "pnl_usd": pnl_usd,
            "pnl_pct": pnl_pct,
            "reason": reason,
            "target_pct": pos.target_pct,
            "trail_pct": pos.trail_pct,
            "stop_pct": pos.stop_pct,
            "entry_fee_bps": pos.entry_fee_bps,
            "exit_fee_bps": pos.exit_fee_bps,
        }
        append_jsonl(DEFAULT_EVENT_PATH, event)
        print(f"EXITED: {pos.product_id} at ${price:.6f} | PnL: {pnl_pct:.2f}% | Reason: {reason}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--loop", action="store_true")
    parser.add_argument("--interval", type=int, default=30)
    parser.add_argument("--cooldown", type=int, default=60) # 20 mins
    args = parser.parse_args()
    
    runner = KrakenFrontierShadowRunner(reentry_cooldown_polls=args.cooldown)
    runner.load_state(DEFAULT_STATE_PATH)
    
    try:
        while True:
            runner.run_poll()
            runner.save_state(DEFAULT_STATE_PATH)
            if not args.loop:
                break
            time.sleep(args.interval)
    except KeyboardInterrupt:
        print("Stopping runner...")


if __name__ == "__main__":
    main()
