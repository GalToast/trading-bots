from __future__ import annotations

import json
import secrets
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any

import jwt
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec, ed25519

import coinbase_config as cfg


class CoinbaseAdvancedClientError(RuntimeError):
    pass


class CoinbaseAdvancedAuthError(CoinbaseAdvancedClientError):
    pass


def normalize_product_id(product_id: str) -> str:
    value = str(product_id or "").strip().upper().replace("/", "-").replace("_", "-")
    if "-" in value:
        return value
    known_quotes = ("USDC", "USDT", "USD", "EUR")
    for quote in known_quotes:
        if value.endswith(quote) and len(value) > len(quote):
            return f"{value[:-len(quote)]}-{quote}"
    return value


@dataclass
class CoinbasePublicTicker:
    product_id: str
    price: float
    bid_price: float
    ask_price: float
    size: float
    volume: float
    time: str


class CoinbaseAdvancedClient:
    def __init__(
        self,
        *,
        api_key_name: str | None = None,
        api_key_secret: str | None = None,
        base_url: str | None = None,
        exchange_public_url: str | None = None,
        sandbox_base_url: str | None = None,
        timeout_seconds: float | None = None,
        jwt_expires_seconds: int | None = None,
    ) -> None:
        self.api_key_name = (api_key_name if api_key_name is not None else cfg.API_KEY_NAME).strip()
        self.api_key_secret = (api_key_secret if api_key_secret is not None else cfg.API_KEY_SECRET).strip()
        self.base_url = (base_url if base_url is not None else cfg.API_BASE_URL).rstrip("/")
        self.exchange_public_url = (exchange_public_url if exchange_public_url is not None else cfg.EXCHANGE_PUBLIC_URL).rstrip("/")
        self.sandbox_base_url = (sandbox_base_url if sandbox_base_url is not None else cfg.SANDBOX_BASE_URL).rstrip("/")
        self.timeout_seconds = float(timeout_seconds if timeout_seconds is not None else cfg.TIMEOUT_SECONDS)
        self.jwt_expires_seconds = int(jwt_expires_seconds if jwt_expires_seconds is not None else cfg.JWT_EXPIRES_SECONDS)
        self.http_user_agent = cfg.HTTP_USER_AGENT
        self._private_key = None
        self._jwt_algorithm: str | None = None

    def has_auth(self) -> bool:
        return bool(self.api_key_name and self.api_key_secret)

    def _ensure_private_key(self) -> Any:
        if not self.has_auth():
            raise CoinbaseAdvancedAuthError("Missing COINBASE_API_KEY_NAME or COINBASE_API_KEY_SECRET")
        if self._private_key is None:
            self._private_key = serialization.load_pem_private_key(self.api_key_secret.encode("utf-8"), password=None)
            if isinstance(self._private_key, ed25519.Ed25519PrivateKey):
                self._jwt_algorithm = "EdDSA"
            elif isinstance(self._private_key, ec.EllipticCurvePrivateKey):
                self._jwt_algorithm = "ES256"
            else:
                raise CoinbaseAdvancedAuthError("Unsupported Coinbase private key type; expected Ed25519 or EC/ECDSA PEM")
        return self._private_key

    def _build_jwt(self, *, method: str, host: str, request_path: str) -> str:
        key = self._ensure_private_key()
        now = int(time.time())
        uri = f"{str(method or 'GET').upper()} {host}{request_path}"
        headers = {
            "typ": "JWT",
            "kid": self.api_key_name,
            "nonce": secrets.token_hex(16),
        }
        claims = {
            "sub": self.api_key_name,
            "iss": "cdp",
            "aud": ["cdp_service"],
            "nbf": now,
            "exp": now + int(self.jwt_expires_seconds),
            "uri": uri,
        }
        token = jwt.encode(claims, key, algorithm=self._jwt_algorithm, headers=headers)
        return str(token)

    def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        body: dict[str, Any] | None = None,
        signed: bool = False,
        sandbox: bool = False,
    ) -> Any:
        method = str(method or "GET").upper()
        clean_params = {k: v for k, v in (params or {}).items() if v is not None}
        query = urllib.parse.urlencode(clean_params, doseq=True)
        request_path = path if not query else f"{path}?{query}"
        signed_request_path = path
        root = self.sandbox_base_url if sandbox else self.base_url
        url = f"{root}{request_path}"
        encoded_body = json.dumps(body).encode("utf-8") if body is not None else None
        req = urllib.request.Request(url=url, data=encoded_body, method=method)
        req.add_header("Accept", "application/json")
        req.add_header("User-Agent", self.http_user_agent)
        if encoded_body is not None:
            req.add_header("Content-Type", "application/json")
        if signed:
            host = urllib.parse.urlparse(root).netloc
            req.add_header("Authorization", f"Bearer {self._build_jwt(method=method, host=host, request_path=signed_request_path)}")
        try:
            with urllib.request.urlopen(req, timeout=self.timeout_seconds) as resp:
                raw = resp.read().decode("utf-8")
                return json.loads(raw) if raw else {}
        except urllib.error.HTTPError as exc:
            payload = exc.read().decode("utf-8", errors="ignore")
            try:
                detail = json.loads(payload) if payload else {}
            except Exception:
                detail = {"raw": payload}
            raise CoinbaseAdvancedClientError(f"HTTP {exc.code} {path}: {detail}") from exc
        except urllib.error.URLError as exc:
            raise CoinbaseAdvancedClientError(f"Network error calling {path}: {exc}") from exc

    def public_exchange_ticker(self, product_id: str) -> CoinbasePublicTicker:
        product_id = normalize_product_id(product_id)
        path = f"/products/{product_id}/ticker"
        url = f"{self.exchange_public_url}{path}"
        req = urllib.request.Request(url=url, method="GET")
        req.add_header("Accept", "application/json")
        req.add_header("User-Agent", self.http_user_agent)
        try:
            with urllib.request.urlopen(req, timeout=self.timeout_seconds) as resp:
                payload = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            payload = exc.read().decode("utf-8", errors="ignore")
            raise CoinbaseAdvancedClientError(f"HTTP {exc.code} {path}: {payload}") from exc
        except urllib.error.URLError as exc:
            raise CoinbaseAdvancedClientError(f"Network error calling {path}: {exc}") from exc
        return CoinbasePublicTicker(
            product_id=product_id,
            price=float(payload["price"]),
            bid_price=float(payload["bid"]),
            ask_price=float(payload["ask"]),
            size=float(payload["size"]),
            volume=float(payload["volume"]),
            time=str(payload["time"]),
        )

    def get_product(self, product_id: str) -> dict[str, Any]:
        """Fetch product details, including min_market_funds (min notional)."""
        return self._request("GET", f"/api/v3/brokerage/products/{normalize_product_id(product_id)}", signed=True)

    def list_products(self, *, get_all_products: bool = False, product_type: str | None = None, limit: int | None = None) -> dict[str, Any]:
        params: dict[str, Any] = {"get_all_products": str(bool(get_all_products)).lower()}
        if product_type:
            params["product_type"] = str(product_type)
        if limit is not None:
            params["limit"] = int(limit)
        return self._request("GET", "/api/v3/brokerage/products", params=params, signed=True)

    def best_bid_ask(self, product_ids: list[str] | None = None) -> dict[str, Any]:
        params = None
        if product_ids:
            params = {"product_ids": [normalize_product_id(pid) for pid in product_ids]}
        return self._request("GET", "/api/v3/brokerage/best_bid_ask", params=params, signed=True)

    def market_candles(
        self,
        product_id: str,
        *,
        start: int | None = None,
        end: int | None = None,
        granularity: str = "ONE_MINUTE",
        limit: int | None = None,
    ) -> dict[str, Any]:
        params: dict[str, Any] = {
            "granularity": str(granularity or "ONE_MINUTE").upper(),
        }
        if start is not None:
            params["start"] = int(start)
        if end is not None:
            params["end"] = int(end)
        if limit is not None:
            params["limit"] = int(limit)
        return self._request(
            "GET",
            f"/api/v3/brokerage/market/products/{normalize_product_id(product_id)}/candles",
            params=params,
            signed=False,
        )

    def accounts(self, *, sandbox: bool = False) -> dict[str, Any]:
        return self._request("GET", "/api/v3/brokerage/accounts", signed=not sandbox, sandbox=sandbox)

    def api_key_permissions(self) -> dict[str, Any]:
        return self._request("GET", "/api/v3/brokerage/key_permissions", signed=True)

    def transaction_summary(self, *, product_type: str | None = None) -> dict[str, Any]:
        params: dict[str, Any] = {}
        if product_type:
            params["product_type"] = str(product_type).upper()
        return self._request("GET", "/api/v3/brokerage/transaction_summary", params=params, signed=True)

    def list_orders(self, *, product_id: str | None = None, order_status: str | None = None, limit: int | None = None) -> dict[str, Any]:
        params: dict[str, Any] = {}
        if product_id:
            params["product_id"] = normalize_product_id(product_id)
        if order_status:
            params["order_status"] = str(order_status)
        if limit is not None:
            params["limit"] = int(limit)
        return self._request("GET", "/api/v3/brokerage/orders/historical/batch", params=params, signed=True)

    def get_order(self, order_id: str) -> dict[str, Any]:
        return self._request("GET", f"/api/v3/brokerage/orders/historical/{order_id}", signed=True)

    def create_market_order(
        self,
        *,
        product_id: str,
        side: str,
        base_size: float | None = None,
        quote_size: float | None = None,
        client_order_id: str,
        futures_mode: bool = False,
        sandbox: bool = False,
    ) -> dict[str, Any]:
        config_key = "market_market_fok" if futures_mode else "market_market_ioc"
        order_config: dict[str, str] = {}
        if base_size is not None:
            order_config["base_size"] = str(base_size)
        if quote_size is not None:
            order_config["quote_size"] = str(quote_size)
        body = {
            "client_order_id": str(client_order_id),
            "product_id": normalize_product_id(product_id),
            "side": str(side or "").upper(),
            "order_configuration": {
                config_key: order_config,
            },
        }
        return self._request("POST", "/api/v3/brokerage/orders", body=body, signed=not sandbox, sandbox=sandbox)
