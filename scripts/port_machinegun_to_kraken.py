#!/usr/bin/env python3
import sys
import re

code = open('scripts/live_coinbase_spot_machinegun_shadow.py', 'r', encoding='utf-8').read()

# Client replacement
code = code.replace('from coinbase_advanced_client import CoinbaseAdvancedClient', 'from kraken_spot_client import KrakenSpotClient')
code = code.replace('CoinbaseAdvancedClient', 'KrakenSpotClient')
code = code.replace('client = CoinbaseAdvancedClient()', 'client = KrakenSpotClient()')

# Fee model replacement
code = code.replace('from coinbase_fee_model import CoinbaseSpotFeeTier, resolve_spot_fee_tier', '')

code = re.sub(
    r'    def apply_fee_tier(self, fee_tier: CoinbaseSpotFeeTier) -> None:\n        self.taker_fee_bps = float(fee_tier.taker_bps)\n        self.fee_source = fee_tier.source\n        self.fee_tier = fee_tier.pricing_tier',
    '',
    code,
    flags=re.MULTILINE
)

# Remove tick import
code = code.replace('from live_coinbase_spot_piranha_shadow import fetch_coinbase_tick', '')

# File paths
code = code.replace('coinbase_spot_machinegun_shadow', 'kraken_spot_machinegun_shadow')
code = code.replace('coinbase_spot_machinegun_opportunity_tape.jsonl', 'kraken_spot_machinegun_opportunity_tape.jsonl')
code = code.replace('coinbase_spot_machinegun_strategy_board.json', 'kraken_spot_frontier_strategy_board.json')
code = code.replace('coinbase_spot_bear_velocity_board.json', 'kraken_spot_money_velocity_board.json') # Use standard board for vetos
code = code.replace('coinbase_spot_machinegun_mfe_tracker.json', 'kraken_spot_machinegun_mfe_tracker.json')

# Function replacements
code = code.replace('fetch_coinbase_ticks', 'fetch_kraken_ticks')
code = code.replace('fetch_coinbase_tick', 'fetch_kraken_tick')

# Default args
code = code.replace('type=float, default=120.0', 'type=float, default=40.0')
code = code.replace('type=float, default=48.0', 'type=float, default=100.0')

# Write custom fetch_kraken_ticks
tick_func = """

def fetch_kraken_ticks(client: KrakenSpotClient, product_ids: list[str]) -> dict[str, dict[str, Any]]:
    product_ids = [pid for pid in dict.fromkeys(product_ids) if pid]
    if not product_ids:
        return {}
    try:
        payload = client.ticker(product_ids)
    except Exception:
        return {}
    ticks = {}
    import time
    now_msc = int(time.time() * 1000)
    now_sec = int(now_msc // 1000)
    for product_id, data in payload.items():
        bids = data.get("b", [])
        asks = data.get("a", [])
        if not bids or not asks:
            continue
        # Ensure we return using the standard product ID, not Kraken's weird alias if it returns one
        ticks[product_id] = {
            "time": now_sec,
            "time_msc": now_msc,
            "bid": float(bids[0]),
            "ask": float(asks[0]),
        }
    return ticks

def fetch_kraken_tick(client: KrakenSpotClient, product_id: str) -> dict[str, Any]:
    ticks = fetch_kraken_ticks(client, [product_id])
    return ticks.get(product_id) or {}
"""

code = re.sub(r'def fetch_coinbase_ticks.*?return ticks', tick_func, code, flags=re.DOTALL)

# Remove the pulse/hurdle board refresh since Kraken has its own
code = re.sub(r'def refresh_boards.*?\n\n\ndef candidate_rows', '\n\ndef candidate_rows', code, flags=re.DOTALL)

# Update run_once to NOT call refresh_boards (we rely on separate orchestrator or cron)
code = re.sub(r'    refresh_boards\([^)]+\)', '', code, flags=re.DOTALL)

# Fix fee tier usage in main
code = re.sub(r'fee_tier = resolve_spot_fee_tier.*?engine.apply_fee_tier(fee_tier)', 'engine.fee_source = "static_kraken"\n    engine.fee_tier = "pro"', code, flags=re.DOTALL)

with open('scripts/live_kraken_spot_frontier_machinegun_shadow.py', 'w', encoding='utf-8') as f:
    f.write(code)
print('Ported to Kraken!')
