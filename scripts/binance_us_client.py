from __future__ import annotations

import hashlib
import hmac
import json
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any

import binance_us_config as cfg


class BinanceUSClientError(RuntimeError):
    pass


class BinanceUSAuthError(BinanceUSClientError):
    pass


def normalize_symbol(symbol: str) -> str:
    return str(symbol or "").replace("/", "").replace("-", "").upper()


def encode_params(params: dict[str, Any] | None) -> str:
    if not params:
        return ""
    filtered = {k: v for k, v in params.items() if v is not None}
    return urllib.parse.urlencode(filtered, doseq=True)


@dataclass
class BinanceUSBookTicker:
    symbol: str
    bid_price: float
    bid_qty: float
    ask_price: float
    ask_qty: float


class BinanceUSClient:
    def __init__(
        self,
        *,
        api_key: str | None = None,
        api_secret: str | None = None,
        base_url: str | None = None,
        recv_window_ms: int | None = None,
        timeout_seconds: float = 10.0,
    ) -> None:
        self.api_key = (api_key if api_key is not None else cfg.API_KEY).strip()
        self.api_secret = (api_secret if api_secret is not None else cfg.API_SECRET).strip()
        self.base_url = (base_url if base_url is not None else cfg.API_BASE_URL).rstrip("/")
        self.recv_window_ms = int(recv_window_ms if recv_window_ms is not None else cfg.RECV_WINDOW_MS)
        self.timeout_seconds = float(timeout_seconds)

    def has_auth(self) -> bool:
        return bool(self.api_key and self.api_secret)

    def _signed_params(self, params: dict[str, Any] | None) -> dict[str, Any]:
        if not self.has_auth():
            raise BinanceUSAuthError("Missing BINANCE_US_API_KEY or BINANCE_US_API_SECRET")
        payload = dict(params or {})
        payload["timestamp"] = int(time.time() * 1000)
        payload["recvWindow"] = int(self.recv_window_ms)
        query = encode_params(payload)
        payload["signature"] = hmac.new(
            self.api_secret.encode("utf-8"),
            query.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()
        return payload

    def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        signed: bool = False,
    ) -> Any:
        method = str(method or "GET").upper()
        request_params = self._signed_params(params) if signed else dict(params or {})
        query = encode_params(request_params)
        url = f"{self.base_url}{path}"
        body: bytes | None = None
        if method in {"GET", "DELETE"}:
            if query:
                url = f"{url}?{query}"
        else:
            body = query.encode("utf-8")
        req = urllib.request.Request(url=url, data=body, method=method)
        req.add_header("Accept", "application/json")
        if body is not None:
            req.add_header("Content-Type", "application/x-www-form-urlencoded")
        if signed or self.api_key:
            req.add_header("X-MBX-APIKEY", self.api_key)
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
            raise BinanceUSClientError(f"HTTP {exc.code} {path}: {detail}") from exc
        except urllib.error.URLError as exc:
            raise BinanceUSClientError(f"Network error calling {path}: {exc}") from exc

    def ping(self) -> dict[str, Any]:
        return self._request("GET", "/api/v3/ping")

    def server_time(self) -> dict[str, Any]:
        return self._request("GET", "/api/v3/time")

    def exchange_info(self, symbol: str | None = None) -> dict[str, Any]:
        params = {"symbol": normalize_symbol(symbol)} if symbol else None
        return self._request("GET", "/api/v3/exchangeInfo", params=params)

    def book_ticker(self, symbol: str) -> BinanceUSBookTicker:
        payload = self._request("GET", "/api/v3/ticker/bookTicker", params={"symbol": normalize_symbol(symbol)})
        return BinanceUSBookTicker(
            symbol=str(payload["symbol"]),
            bid_price=float(payload["bidPrice"]),
            bid_qty=float(payload["bidQty"]),
            ask_price=float(payload["askPrice"]),
            ask_qty=float(payload["askQty"]),
        )

    def account(self) -> dict[str, Any]:
        return self._request("GET", "/api/v3/account", signed=True)

    def open_orders(self, symbol: str | None = None) -> list[dict[str, Any]]:
        params = {"symbol": normalize_symbol(symbol)} if symbol else None
        payload = self._request("GET", "/api/v3/openOrders", params=params, signed=True)
        return list(payload or [])

    def get_order(self, *, symbol: str, order_id: int | None = None, orig_client_order_id: str | None = None) -> dict[str, Any]:
        params: dict[str, Any] = {"symbol": normalize_symbol(symbol)}
        if order_id is not None:
            params["orderId"] = int(order_id)
        if orig_client_order_id:
            params["origClientOrderId"] = str(orig_client_order_id)
        return self._request("GET", "/api/v3/order", params=params, signed=True)

    def new_order(
        self,
        *,
        symbol: str,
        side: str,
        order_type: str = "MARKET",
        quantity: float | None = None,
        quote_order_qty: float | None = None,
        client_order_id: str | None = None,
        time_in_force: str | None = None,
        price: float | None = None,
        test: bool = False,
    ) -> dict[str, Any]:
        params: dict[str, Any] = {
            "symbol": normalize_symbol(symbol),
            "side": str(side or "").upper(),
            "type": str(order_type or "MARKET").upper(),
            "quantity": quantity,
            "quoteOrderQty": quote_order_qty,
            "newClientOrderId": client_order_id,
            "timeInForce": time_in_force,
            "price": price,
        }
        path = "/api/v3/order/test" if test else "/api/v3/order"
        return self._request("POST", path, params=params, signed=True)

    def cancel_order(
        self,
        *,
        symbol: str,
        order_id: int | None = None,
        orig_client_order_id: str | None = None,
    ) -> dict[str, Any]:
        params: dict[str, Any] = {"symbol": normalize_symbol(symbol)}
        if order_id is not None:
            params["orderId"] = int(order_id)
        if orig_client_order_id:
            params["origClientOrderId"] = str(orig_client_order_id)
        return self._request("DELETE", "/api/v3/order", params=params, signed=True)
