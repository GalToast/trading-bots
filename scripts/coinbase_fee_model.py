from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from coinbase_advanced_client import CoinbaseAdvancedClient, CoinbaseAdvancedClientError
from live_penetration_lattice_shadow import utc_now_iso


@dataclass
class CoinbaseSpotFeeTier:
    taker_bps: float
    maker_bps: float
    source: str
    pricing_tier: str = ""
    fetched_at: str = ""
    error: str = ""


def rate_to_bps(value: Any) -> float:
    if value in (None, ""):
        return 0.0
    rate = float(value)
    return rate * 10000.0


def resolve_spot_fee_tier(
    client: CoinbaseAdvancedClient,
    *,
    fallback_taker_bps: float,
    fallback_maker_bps: float | None = None,
) -> CoinbaseSpotFeeTier:
    fallback_maker = float(fallback_taker_bps if fallback_maker_bps is None else fallback_maker_bps)
    if not client.has_auth():
        return CoinbaseSpotFeeTier(
            taker_bps=float(fallback_taker_bps),
            maker_bps=fallback_maker,
            source="fallback_no_auth",
            fetched_at=utc_now_iso(),
        )
    try:
        payload = client.transaction_summary(product_type="SPOT")
        tier = payload.get("fee_tier") or {}
        taker_bps = rate_to_bps(tier.get("taker_fee_rate"))
        maker_bps = rate_to_bps(tier.get("maker_fee_rate"))
        if taker_bps <= 0.0:
            raise ValueError("missing taker_fee_rate")
        if maker_bps <= 0.0:
            maker_bps = fallback_maker
        return CoinbaseSpotFeeTier(
            taker_bps=taker_bps,
            maker_bps=maker_bps,
            source="coinbase_transaction_summary_spot",
            pricing_tier=str(tier.get("pricing_tier") or ""),
            fetched_at=utc_now_iso(),
        )
    except (CoinbaseAdvancedClientError, ValueError, TypeError) as exc:
        return CoinbaseSpotFeeTier(
            taker_bps=float(fallback_taker_bps),
            maker_bps=fallback_maker,
            source="fallback_transaction_summary_error",
            fetched_at=utc_now_iso(),
            error=str(exc),
        )
