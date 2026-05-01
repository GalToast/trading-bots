#!/usr/bin/env python3
import time
from datetime import datetime, timezone

from coinbase_advanced_client import CoinbaseAdvancedClientError


def utc_now_iso():
    return datetime.now(timezone.utc).isoformat()


def is_rate_limited_error(exc: Exception) -> bool:
    return "HTTP 429" in str(exc or "")


def _emit(event_logger, record: dict) -> None:
    if event_logger:
        event_logger(record)


def _chunk_seconds(granularity: str) -> int:
    if granularity == "ONE_MINUTE":
        return 300 * 60
    if granularity == "FIFTEEN_MINUTE":
        return 300 * 15 * 60
    return 300 * 5 * 60


def safe_market_candles(client, pid, *, start, end, granularity, retries=4, base_delay=1.0):
    delay = max(0.2, float(base_delay))
    max_retries = max(1, int(retries))
    for attempt in range(max_retries):
        try:
            return client.market_candles(pid, start=start, end=end, granularity=granularity)
        except CoinbaseAdvancedClientError as exc:
            if not is_rate_limited_error(exc):
                raise
            if attempt == max_retries - 1:
                return None
            time.sleep(delay)
            delay = min(delay * 2.0, 15.0)
    return None


def safe_market_candles_limit(client, pid, *, granularity, limit, retries=4, base_delay=1.0):
    delay = max(0.2, float(base_delay))
    max_retries = max(1, int(retries))
    for attempt in range(max_retries):
        try:
            return client.market_candles(pid, granularity=granularity, limit=int(limit))
        except CoinbaseAdvancedClientError as exc:
            if not is_rate_limited_error(exc):
                raise
            if attempt == max_retries - 1:
                return None
            time.sleep(delay)
            delay = min(delay * 2.0, 15.0)
    return None


def fetch_candles_chunked(
    client,
    pid,
    start,
    end,
    granularity="FIVE_MINUTE",
    *,
    event_logger=None,
    retries=4,
    base_delay=1.0,
):
    all_candles = []
    chunk_seconds = _chunk_seconds(granularity)
    cursor = int(start)
    end_ts = int(end)
    while cursor < end_ts:
        chunk_end = min(cursor + chunk_seconds, end_ts)
        try:
            response = safe_market_candles(
                client,
                pid,
                start=cursor,
                end=chunk_end,
                granularity=granularity,
                retries=retries,
                base_delay=base_delay,
            )
            if response is None:
                _emit(
                    event_logger,
                    {
                        "ts_utc": utc_now_iso(),
                        "action": "rate_limit_skip_chunk",
                        "product": pid,
                        "granularity": granularity,
                        "start": int(cursor),
                        "end": int(chunk_end),
                    },
                )
                cursor = chunk_end
                continue
            candles = response.get("candles", [])
            all_candles.extend(candles)
            cursor = chunk_end
            if not candles:
                break
            time.sleep(0.2)
        except Exception:
            cursor = chunk_end
            time.sleep(0.5)
    all_candles.sort(key=lambda candle: int(candle["start"]))
    return all_candles


def fetch_live_candles(
    client,
    pid,
    *,
    start,
    end,
    granularity,
    filter_after,
    event_logger=None,
    retries=4,
    base_delay=1.0,
):
    response = safe_market_candles(
        client,
        pid,
        start=int(start),
        end=int(end),
        granularity=granularity,
        retries=retries,
        base_delay=base_delay,
    )
    if response is None:
        _emit(
            event_logger,
            {
                "ts_utc": utc_now_iso(),
                "action": "rate_limit_skip_live_fetch",
                "product": pid,
                "granularity": granularity,
                "start": int(start),
                "end": int(end),
            },
        )
        return []
    return [candle for candle in response.get("candles", []) if int(candle["start"]) > int(filter_after)]
