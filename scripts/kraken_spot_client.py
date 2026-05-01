from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any

import kraken_config as cfg


class KrakenSpotClientError(RuntimeError):
    pass


class KrakenSpotAuthError(KrakenSpotClientError):
    pass


def to_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def normalize_asset(asset: str) -> str:
    value = str(asset or "").upper()
    aliases = {
        "XXBT": "BTC",
        "XBT": "BTC",
        "XDG": "DOGE",
        "XXDG": "DOGE",
        "ZUSD": "USD",
        "ZEUR": "EUR",
        "ZGBP": "GBP",
        "ZCAD": "CAD",
        "ZAUD": "AUD",
        "ZJPY": "JPY",
    }
    return aliases.get(value, value)


def normalize_pair_name(name: str) -> str:
    value = str(name or "").upper().replace("_", "/").replace("-", "/")
    if "/" in value:
        base, quote = value.split("/", 1)
        return f"{normalize_asset(base)}/{normalize_asset(quote)}"
    known_quotes = ("USDT", "USDC", "USD", "EUR", "BTC", "ETH")
    for quote in known_quotes:
        if value.endswith(quote) and len(value) > len(quote):
            return f"{normalize_asset(value[:-len(quote)])}/{normalize_asset(quote)}"
    return normalize_asset(value)


@dataclass(frozen=True)
class KrakenPair:
    rest_pair: str
    altname: str
    wsname: str
    base: str
    quote: str
    order_min: float
    cost_min: float
    tick_size: float
    lot_decimals: int
    pair_decimals: int
    status: str


@dataclass(frozen=True)
class KrakenBookTop:
    rest_pair: str
    wsname: str
    bid: float
    ask: float
    bid_size: float
    ask_size: float
    last: float
    volume_24h: float
    source: str


class KrakenSpotClient:
    def __init__(
        self,
        *,
        api_key: str | None = None,
        api_secret: str | None = None,
        base_url: str | None = None,
        timeout_seconds: float | None = None,
    ) -> None:
        self.api_key = (api_key if api_key is not None else cfg.API_KEY).strip()
        self.api_secret = (api_secret if api_secret is not None else cfg.API_SECRET).strip()
        self.base_url = (base_url if base_url is not None else cfg.API_BASE_URL).rstrip("/")
        self.timeout_seconds = float(timeout_seconds if timeout_seconds is not None else cfg.TIMEOUT_SECONDS)
        self.http_user_agent = cfg.HTTP_USER_AGENT

    def has_auth(self) -> bool:
        return bool(self.api_key and self.api_secret)

    def _request(self, method: str, path: str, *, params: dict[str, Any] | None = None, private: bool = False) -> Any:
        method = str(method or "GET").upper()
        payload = {k: v for k, v in (params or {}).items() if v is not None}
        encoded = urllib.parse.urlencode(payload)
        url = f"{self.base_url}{path}"
        data = None
        if method == "GET" and encoded:
            url = f"{url}?{encoded}"
        elif encoded:
            data = encoded.encode("utf-8")
        req = urllib.request.Request(url=url, data=data, method=method)
        req.add_header("Accept", "application/json")
        req.add_header("User-Agent", self.http_user_agent)
        if private:
            if not self.has_auth():
                raise KrakenSpotAuthError("Missing KRAKEN_API_KEY or KRAKEN_API_SECRET")
            nonce = str(int(time.time() * 1000000))
            signed_payload = dict(payload)
            signed_payload["nonce"] = nonce
            encoded_signed = urllib.parse.urlencode(signed_payload).encode("utf-8")
            req.data = encoded_signed
            req.add_header("API-Key", self.api_key)
            req.add_header("API-Sign", self._sign(path, nonce, encoded_signed))
            req.add_header("Content-Type", "application/x-www-form-urlencoded")
        try:
            with urllib.request.urlopen(req, timeout=self.timeout_seconds) as resp:
                raw = resp.read().decode("utf-8")
        except urllib.error.HTTPError as exc:
            payload_text = exc.read().decode("utf-8", errors="ignore")
            raise KrakenSpotClientError(f"HTTP {exc.code} {path}: {payload_text}") from exc
        except urllib.error.URLError as exc:
            raise KrakenSpotClientError(f"Network error calling {path}: {exc}") from exc
        parsed = json.loads(raw) if raw else {}
        errors = parsed.get("error") if isinstance(parsed, dict) else None
        if errors:
            raise KrakenSpotClientError(f"Kraken error {path}: {errors}")
        return parsed.get("result", parsed) if isinstance(parsed, dict) else parsed

    def _sign(self, path: str, nonce: str, encoded_payload: bytes) -> str:
        secret = base64.b64decode(self.api_secret)
        sha = hashlib.sha256(nonce.encode("utf-8") + encoded_payload).digest()
        mac = hmac.new(secret, path.encode("utf-8") + sha, hashlib.sha512)
        return base64.b64encode(mac.digest()).decode("ascii")

    def asset_pairs(self) -> dict[str, Any]:
        return self._request("GET", "/0/public/AssetPairs")

    def depth(self, rest_pair: str, count: int = 20) -> dict[str, Any]:
        """Fetch order book depth for a pair."""
        return self._request("GET", "/0/public/Depth", params={"pair": rest_pair, "count": count})

    def trades(self, rest_pair: str, since: str | int | None = None, count: int | None = None) -> dict[str, Any]:
        """Fetch recent public trades for a pair."""
        params: dict[str, Any] = {"pair": rest_pair}
        if since is not None:
            params["since"] = since
        if count is not None:
            params["count"] = count
        return self._request("GET", "/0/public/Trades", params=params)

    def ticker(self, rest_pairs: list[str]) -> dict[str, Any]:
        if not rest_pairs:
            return {}
        return self._request("GET", "/0/public/Ticker", params={"pair": ",".join(rest_pairs)})

    def ohlc(self, rest_pair: str, interval_minutes: int = 1, since_epoch: int | None = None) -> dict[str, Any]:
        params = {"pair": rest_pair, "interval": interval_minutes}
        if since_epoch is not None:
            params["since"] = since_epoch
        return self._request("GET", "/0/public/OHLC", params=params)

    def balance(self) -> dict[str, Any]:
        return self._request("POST", "/0/private/Balance", private=True)

    def add_order(
        self,
        *,
        rest_pair: str,
        side: str,
        order_type: str = "limit",
        volume: float,
        price: float | None = None,
        post_only: bool = False,
        hidden: bool = False,
        validate: bool = False,
    ) -> dict[str, Any]:
        params: dict[str, Any] = {
            "pair": rest_pair,
            "type": side.lower(),
            "ordertype": order_type.lower(),
            "volume": f"{volume:.8f}",
        }
        if price is not None:
            params["price"] = f"{price:.8f}"
        
        oflags = []
        if post_only:
            oflags.append("post")
        if hidden:
            oflags.append("vi") # 'vi' is the Kraken flag for hidden
        
        if oflags:
            params["oflags"] = ",".join(oflags)
            
        if validate:
            params["validate"] = "true"
        return self._request("POST", "/0/private/AddOrder", params=params, private=True)


def parse_pair(rest_pair: str, payload: dict[str, Any]) -> KrakenPair | None:
    wsname = str(payload.get("wsname") or "")
    altname = str(payload.get("altname") or rest_pair)
    status = str(payload.get("status") or "online")
    if ".d" in altname.lower() or ".d" in wsname.lower():
        return None
    if wsname and "/" in wsname:
        base_raw, quote_raw = wsname.split("/", 1)
    else:
        normalized = normalize_pair_name(altname)
        if "/" not in normalized:
            return None
        base_raw, quote_raw = normalized.split("/", 1)
    tick_size = 10 ** (-int(to_float(payload.get("pair_decimals"), 8)))
    return KrakenPair(
        rest_pair=str(rest_pair),
        altname=altname,
        wsname=wsname or normalize_pair_name(altname),
        base=normalize_asset(base_raw),
        quote=normalize_asset(quote_raw),
        order_min=to_float(payload.get("ordermin")),
        cost_min=to_float(payload.get("costmin"), 0.0),
        tick_size=tick_size,
        lot_decimals=int(to_float(payload.get("lot_decimals"), 8)),
        pair_decimals=int(to_float(payload.get("pair_decimals"), 8)),
        status=status,
    )


def parse_ticker(rest_pair: str, wsname: str, payload: dict[str, Any], *, source: str = "rest_ticker") -> KrakenBookTop | None:
    ask_arr = payload.get("a") or [None, None, None]
    bid_arr = payload.get("b") or [None, None, None]
    
    ask = to_float(ask_arr[0])
    ask_size = to_float(ask_arr[2])
    
    bid = to_float(bid_arr[0])
    bid_size = to_float(bid_arr[2])
    
    last = to_float((payload.get("c") or [None])[0])
    volume = to_float((payload.get("v") or [None, None])[1])
    
    if bid <= 0.0 or ask <= 0.0:
        return None
        
    return KrakenBookTop(
        rest_pair=rest_pair, 
        wsname=wsname, 
        bid=bid, 
        ask=ask, 
        bid_size=bid_size, 
        ask_size=ask_size, 
        last=last, 
        volume_24h=volume, 
        source=source
    )
