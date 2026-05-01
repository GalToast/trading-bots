#!/usr/bin/env python3
"""
Titan 9.0 — Unified Pulse→Verify→Fire Scanner for Kraken Spot.

Chains 3 gears:
  Gear 1 (Pulse):    Crossing-pressure scanner every N seconds
  Gear 2 (Verify):   When pressure > threshold, run microfill proxy check
  Gear 3 (Fire):     If proxy passes, run validate=true probe

This is the 5%/hour machine. It catches transient crossing-pressure peaks
and validates them before firing.

Usage:
    python scripts/titan9_scanner.py --products BMB-USD,TRAC-USD,BILLY-USD,CHEX-USD,WARD-USD --cycles 10 --interval 30 --pressure-threshold 0.35
"""

from __future__ import annotations

import argparse
import json
import math
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "scripts"
REPORTS = ROOT / "reports"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from kraken_spot_client import KrakenSpotClient, normalize_pair_name, parse_pair, to_float
from run_kraken_tiny_live_maker_roundtrip_probe import (
    exit_floor_above_ask_bps,
    legal_price,
    legal_volume,
    maker_exit_floor_price,
)
# Import offset-based pricing from fire queue
import importlib
_fire_queue = importlib.import_module("build_kraken_tiny_live_fire_queue")
legal_maker_buy_price_at_offset = _fire_queue.legal_maker_buy_price_at_offset
from crossing_pressure_scanner import (
    compute_spread_bps,
    compute_book_imbalance,
    compute_crossing_pressure,
    kraken_name,
)


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class Gear1Pulse:
    """Crossing-pressure scanner — monitors 60s heartbeat of taker flow."""

    def __init__(self, client: KrakenSpotClient, products: list[str], samples: int = 3, interval: float = 2.0):
        self.client = client
        self.products = products
        self.samples = samples
        self.interval = interval
        self.baseline_spreads: dict[str, float] = {}

    def scan(self, product: str) -> dict:
        """Single product crossing-pressure scan."""
        kc = kraken_name(product)
        snapshots = []
        spread_history = []

        for i in range(self.samples):
            try:
                depth_data = self.client.depth(kc, count=20)
                if kc not in depth_data:
                    for key in depth_data:
                        if key.upper() == kc:
                            depth_data = {kc: depth_data[key]}
                            break
                if kc not in depth_data:
                    time.sleep(self.interval)
                    continue

                book = depth_data[kc]
                bids = book.get("bids", [])
                asks = book.get("asks", [])

                ticker_data = self.client.ticker([kc])
                if kc not in ticker_data:
                    for key in ticker_data:
                        if key.upper() == kc:
                            ticker_data = {kc: ticker_data[key]}
                            break
                t = ticker_data.get(kc, {})
                bid = to_float((t.get("b") or [None])[0])
                ask = to_float((t.get("a") or [None])[0])
                last = to_float((t.get("c") or [None])[0])

                if bid <= 0 and bids:
                    bid = to_float(bids[0][0])
                if ask <= 0 and asks:
                    ask = to_float(asks[0][0])

                if bid <= 0 or ask <= 0:
                    time.sleep(self.interval)
                    continue

                spread = compute_spread_bps(bid, ask)
                imbalance = compute_book_imbalance(bids, asks, levels=3)

                snapshots.append({
                    "bid": bid, "ask": ask, "last": last,
                    "spread_bps": round(spread, 2),
                    "imbalance": round(imbalance, 4),
                })
                spread_history.append(spread)

                if i < self.samples - 1:
                    time.sleep(self.interval)

            except Exception:
                time.sleep(self.interval)

        if not snapshots:
            return {"product": product, "error": "no_snapshots", "pressure_score": 0.0}

        baseline = sum(spread_history) / len(spread_history)
        current = spread_history[-1]
        cur = snapshots[-1]

        # Update baseline cache
        if product not in self.baseline_spreads:
            self.baseline_spreads[product] = baseline
        else:
            # Exponential moving average
            self.baseline_spreads[product] = 0.7 * self.baseline_spreads[product] + 0.3 * baseline

        mid = (cur["bid"] + cur["ask"]) / 2
        if mid > 0 and cur["last"] > 0:
            taker_direction = (cur["last"] - mid) / mid * 10000.0
            taker_direction_norm = min(1.0, abs(taker_direction) / 50.0)
            taker_sign = 1.0 if taker_direction > 0 else -1.0
        else:
            taker_direction_norm = 0.0
            taker_sign = 0.0

        if len(spread_history) >= 3:
            recent_change = abs(spread_history[-1] - spread_history[-3])
            velocity_ratio = 1.0 + (recent_change / max(baseline, 10.0))
        else:
            velocity_ratio = 1.0

        pressure = compute_crossing_pressure(
            spread_bps=current,
            baseline_spread_bps=self.baseline_spreads[product],
            imbalance=cur["imbalance"],
            velocity_ratio=velocity_ratio,
            taker_direction=taker_sign * taker_direction_norm,
        )

        return {
            "product": product,
            "pressure_score": pressure["score"],
            "pressure_breakdown": pressure["breakdown"],
            "current_spread_bps": round(current, 2),
            "baseline_spread_bps": round(self.baseline_spreads[product], 2),
            "book_imbalance": cur["imbalance"],
            "bid": cur["bid"],
            "ask": cur["ask"],
            "last": cur["last"],
            "velocity_ratio": round(velocity_ratio, 3),
        }

    def scan_all(self) -> dict[str, dict]:
        results = {}
        for product in self.products:
            results[product] = self.scan(product)
        return results


class Gear2Verify:
    """Microfill proxy checker — verifies fill reachability at target offset."""

    def __init__(self, client: KrakenSpotClient):
        self.client = client

    def verify(self, product: str, offset_frac: float, side: str = "buy",
               max_exit_floor_bps: float = 50.0, quote_usd: float = 9.0,
               maker_fee_bps: float = 25.0, target_net_pct: float = 0.001) -> dict:
        """
        Run public-book proxy check for a product at target offset.
        Returns whether the entry+exit math clears.
        """
        kc = kraken_name(product)
        try:
            # Load pair metadata for tick_size
            assets = self.client.asset_pairs()
            pair = None
            for rest_pair, payload in assets.items():
                p = parse_pair(rest_pair, payload)
                if p and p.status == "online":
                    wanted = product.replace("-", "/")
                    if p.wsname.upper() == wanted or f"{p.base}/{p.quote}" == wanted:
                        pair = p
                        break

            if pair is None:
                return {"product": product, "error": "pair_not_found", "passed": False}

            ticker_data = self.client.ticker([kc])
            if kc not in ticker_data:
                for key in ticker_data:
                    if key.upper() == kc:
                        ticker_data = {kc: ticker_data[key]}
                        break
            t = ticker_data.get(kc, {})
            bid = to_float((t.get("b") or [None])[0])
            ask = to_float((t.get("a") or [None])[0])

            if bid <= 0 or ask <= 0:
                return {"product": product, "error": "no_ticker", "passed": False}

            spread_bps = compute_spread_bps(bid, ask)

            # Compute entry price at offset using correct function
            entry_price = legal_maker_buy_price_at_offset(bid, ask, pair.tick_size, offset_frac)

            if entry_price is None or entry_price <= 0:
                return {"product": product, "error": "invalid_entry_price", "passed": False}

            # Compute volume for the target quote_usd
            vol = quote_usd / entry_price if entry_price > 0 else 0
            vol = legal_volume(vol, pair.lot_decimals)

            if vol <= 0:
                return {"product": product, "error": "invalid_volume", "passed": False}

            # Compute exit floor (returns tuple: (legal_price, raw_price))
            entry_cost = entry_price * vol
            entry_fee = entry_cost * (maker_fee_bps / 10000.0)

            exit_legal, exit_raw = maker_exit_floor_price(
                entry_cost=entry_cost,
                entry_fee=entry_fee,
                volume=vol,
                maker_fee_bps=maker_fee_bps,
                target_net_pct=target_net_pct,
                tick_size=pair.tick_size,
            )

            # Compute exit floor distance above ask
            floor_above_ask = exit_floor_above_ask_bps(exit_legal, ask)

            passed = floor_above_ask <= max_exit_floor_bps

            # Compute net margin
            entry_concession = max(0.0, (entry_price - bid) / bid * 10000.0)
            gross_capture = (exit_legal - entry_price) / entry_price * 10000.0
            net_margin_bps = gross_capture - maker_fee_bps - maker_fee_bps  # entry + exit fees

            return {
                "product": product,
                "offset": offset_frac,
                "side": side,
                "passed": passed,
                "spread_bps": round(spread_bps, 1),
                "entry_price": round(entry_price, 8),
                "exit_price": round(exit_legal, 8),
                "entry_concession_bps": round(entry_concession, 1),
                "exit_floor_above_ask_bps": round(floor_above_ask, 1),
                "gross_capture_bps": round(gross_capture, 1),
                "net_margin_bps": round(net_margin_bps, 1),
                "max_exit_floor_bps": max_exit_floor_bps,
                "volume": round(vol, 8),
            }

        except Exception as e:
            return {"product": product, "error": str(e), "passed": False}


class Gear3Fire:
    """Validate=true probe — tests order acceptance by Kraken matching engine."""

    def __init__(self, client: KrakenSpotClient):
        self.client = client

    def fire(self, product: str, side: str, price: float, volume: float,
             order_type: str = "limit", post_only: bool = True) -> dict:
        """
        Send validate=true probe. Returns Kraken's response.
        """
        kc = kraken_name(product)
        try:
            result = self.client.add_order(
                rest_pair=kc,
                side=side,
                order_type=order_type,
                volume=volume,
                price=price,
                post_only=post_only,
                validate=True,
            )
            return {
                "product": product,
                "side": side,
                "price": price,
                "volume": volume,
                "accepted": True,
                "kraken_response": result,
            }
        except Exception as e:
            return {
                "product": product,
                "side": side,
                "price": price,
                "volume": volume,
                "accepted": False,
                "error": str(e),
            }


class Titan9Scanner:
    """Unified Pulse→Verify→Fire scanner."""

    def __init__(self, products: list[str], pressure_threshold: float = 0.35,
                 cycle_interval: float = 30.0, quote_usd: float = 9.0,
                 max_exit_floor_bps: float = 50.0, offsets: list[float] | None = None):
        self.client = KrakenSpotClient()
        self.products = products
        self.pressure_threshold = pressure_threshold
        self.cycle_interval = cycle_interval
        self.quote_usd = quote_usd
        self.max_exit_floor_bps = max_exit_floor_bps
        self.offsets = offsets or [0.50, 0.75]

        self.pulse = Gear1Pulse(self.client, products, samples=3, interval=2.0)
        self.verify = Gear2Verify(self.client)
        self.fire = Gear3Fire(self.client)

        self.history: list[dict] = []

    def run_cycle(self, cycle_num: int) -> dict:
        """Single cycle: scan all → verify fires → fire validates."""
        ts = utc_now_iso()
        print(f"\n{'='*60}")
        print(f"  TITAN 9.0 — Cycle {cycle_num} at {ts}")
        print(f"{'='*60}")

        # Gear 1: Pulse scan all products
        print("\n  Gear 1: Scanning crossing-pressure...")
        pressure_results = self.pulse.scan_all()

        fire_candidates = []
        for product, data in pressure_results.items():
            score = data.get("pressure_score", 0)
            status = "FIRE" if score >= self.pressure_threshold else "WAIT"
            emoji = "🔥" if status == "FIRE" else "⏳"
            print(f"    {emoji} {product}: {score:.4f} [{status}]")
            if score >= self.pressure_threshold:
                fire_candidates.append((product, data))

        # Gear 2: Verify fill proxy for fire candidates
        verify_results = []
        for product, pressure_data in fire_candidates:
            print(f"\n  Gear 2: Verifying {product} at offsets {self.offsets}...")
            for offset in self.offsets:
                v = self.verify.verify(product, offset, side="buy",
                                       max_exit_floor_bps=self.max_exit_floor_bps,
                                       quote_usd=self.quote_usd)
                status = "PASS" if v.get("passed") else "FAIL"
                emoji = "✅" if status == "PASS" else "❌"
                print(f"    {emoji} {product}@{offset}: spread={v.get('spread_bps', '?')}bps "
                      f"exit_floor={v.get('exit_floor_above_ask_bps', '?')}bps "
                      f"net_margin={v.get('net_margin_bps', '?')}bps")
                if v.get("passed"):
                    verify_results.append((product, offset, pressure_data, v))

        # Gear 3: Fire validate=true probes for verified candidates
        fire_results = []
        for product, offset, pressure_data, verify_data in verify_results:
            print(f"\n  Gear 3: Firing validate=true on {product}@{offset}...")
            entry_price = verify_data.get("entry_price", 0)
            # Compute volume from quote_usd
            vol = self.quote_usd / entry_price if entry_price > 0 else 0.001

            f = self.fire.fire(product, "buy", entry_price, vol)
            status = "ACCEPTED" if f.get("accepted") else "REJECTED"
            emoji = "🚀" if status == "ACCEPTED" else "🛑"
            print(f"    {emoji} {product}@{offset}: {status}")
            if f.get("accepted"):
                print(f"    🚀🚀🚀 FULLY VALIDATED — {product} clears Pressure + Verify + Fire!")
            fire_results.append(f)

        cycle_result = {
            "cycle": cycle_num,
            "ts": ts,
            "pressure_results": {k: {kk: vv for kk, vv in v.items() if kk != "pressure_breakdown"}
                                 for k, v in pressure_results.items()},
            "fire_candidates": [{"product": p, "pressure": d.get("pressure_score")} for p, d in fire_candidates],
            "verify_results": verify_results,
            "fire_results": fire_results,
        }
        self.history.append(cycle_result)

        # Summary
        print(f"\n  Cycle {cycle_num} Summary:")
        print(f"    Pressure fires: {len(fire_candidates)}")
        print(f"    Verify passes: {len(verify_results)}")
        print(f"    Fire accepts: {len([f for f in fire_results if f.get('accepted')])}")

        return cycle_result

    def run(self, cycles: int = 10) -> dict:
        """Run multiple cycles with interval between each."""
        print(f"\n🔬 TITAN 9.0 Scanner starting:")
        print(f"   Products: {', '.join(self.products)}")
        print(f"   Pressure threshold: {self.pressure_threshold}")
        print(f"   Cycles: {cycles}, interval: {self.cycle_interval}s")
        print(f"   Offsets: {self.offsets}")

        all_results = []
        for i in range(1, cycles + 1):
            result = self.run_cycle(i)
            all_results.append(result)

            if i < cycles:
                print(f"\n  Waiting {self.cycle_interval}s for next cycle...")
                time.sleep(self.cycle_interval)

        return {
            "generated": utc_now_iso(),
            "products": self.products,
            "pressure_threshold": self.pressure_threshold,
            "offsets": self.offsets,
            "cycles": cycles,
            "history": all_results,
        }


def main():
    parser = argparse.ArgumentParser(description="Titan 9.0 Unified Pulse→Verify→Fire Scanner")
    parser.add_argument("--products", default="BMB-USD,TRAC-USD,BILLY-USD,CHEX-USD,WARD-USD")
    parser.add_argument("--cycles", type=int, default=5)
    parser.add_argument("--interval", type=float, default=30.0, help="Seconds between cycles")
    parser.add_argument("--pressure-threshold", type=float, default=0.35)
    parser.add_argument("--offsets", default="0.5,0.75", help="Comma-separated entry offsets")
    parser.add_argument("--quote-usd", type=float, default=9.0)
    parser.add_argument("--max-exit-floor-bps", type=float, default=50.0)
    parser.add_argument("--json-path", type=Path, default=REPORTS / "titan9_scanner.json")
    args = parser.parse_args()

    products = [p.strip() for p in args.products.split(",") if p.strip()]
    offsets = [float(x) for x in args.offsets.split(",")]

    scanner = Titan9Scanner(
        products=products,
        pressure_threshold=args.pressure_threshold,
        cycle_interval=args.interval,
        quote_usd=args.quote_usd,
        max_exit_floor_bps=args.max_exit_floor_bps,
        offsets=offsets,
    )

    output = scanner.run(cycles=args.cycles)

    args.json_path.parent.mkdir(parents=True, exist_ok=True)
    with open(args.json_path, "w", encoding="utf-8") as f:
        json.dump(output, f, indent=2)

    print(f"\n✅ Results saved to {args.json_path}")

    # Final summary
    total_fires = sum(len(c["fire_candidates"]) for c in output["history"])
    total_verifies = sum(len(c["verify_results"]) for c in output["history"])
    total_accepts = sum(len([f for f in c["fire_results"] if f.get("accepted")]) for c in output["history"])
    print(f"\n📊 FINAL: {total_fires} pressure fires → {total_verifies} verify passes → {total_accepts} fire accepts")

    if total_accepts > 0:
        print(f"\n🚀🚀🚀 TITAN 9.0 CONFIRMED: {total_accepts} full-chain validations!")
    else:
        print(f"\n⏳ No full-chain validations yet. Pressure may need to build or thresholds may need tuning.")


if __name__ == "__main__":
    main()
