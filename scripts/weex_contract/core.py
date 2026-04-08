#!/usr/bin/env python3
"""Shared primitives for the WEEX contract trading CLI."""

from __future__ import annotations

import argparse
import base64
import hashlib
import hmac
import json
import secrets
import time
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple
from urllib import error, parse, request


DEFAULT_BASE_URL = "https://api-contract.weex.com"
DEFAULT_LOCALE = "en-US"
DEFAULT_TIMEOUT = 15.0

OK_CODES = {"0", "200", "00000"}
ORDER_INTENTS = {
    "OPEN_LONG": ("BUY", "LONG"),
    "OPEN_SHORT": ("SELL", "SHORT"),
}
RISK_ADVANCED_SIDE_PAIRS = {
    ("BUY", "SHORT"),
    ("SELL", "LONG"),
}
RISK_SCOPES = {
    "place_order": "normal",
    "place_orders_batch": "normal",
    "cancel_order": "normal",
    "cancel_orders_batch": "normal",
    "cancel_open_orders": "elevated",
    "cancel_pending_orders": "elevated",
    "close_positions": "elevated",
    "set_leverage": "normal",
    "set_margin_mode": "elevated",
    "adjust_position_margin": "elevated",
    "set_auto_append_margin": "normal",
    "place_conditional_order": "normal",
    "cancel_conditional_order": "normal",
    "place_tpsl_order": "normal",
    "modify_tpsl_order": "normal",
    "contract_bills": "read_only",
}


class CommandError(RuntimeError):
    """User-facing command failure."""


@dataclass(frozen=True)
class Endpoint:
    key: str
    group: str
    title: str
    method: str
    path: str
    auth: bool
    mutating: bool
    doc_url: str


def load_endpoint_map() -> Dict[str, Endpoint]:
    refs = Path(__file__).resolve().parents[2] / "references" / "contract-api-definitions.json"
    obj = json.loads(refs.read_text(encoding="utf-8"))
    endpoint_map: Dict[str, Endpoint] = {}
    for definition in obj.get("definitions", []):
        method = str(definition.get("method", "GET")).upper()
        auth = bool(definition.get("requires_auth", False))
        endpoint = Endpoint(
            key=definition["key"],
            group=str(definition.get("category", "")),
            title=str(definition.get("title", "")),
            method=method,
            path=str(definition.get("path", "")),
            auth=auth,
            mutating=auth and method in {"POST", "PUT", "DELETE"},
            doc_url=str(definition.get("doc_url", "")),
        )
        endpoint_map[endpoint.key] = endpoint
    return endpoint_map


ENDPOINTS = load_endpoint_map()


def output_json(payload: Dict[str, Any], pretty: bool) -> None:
    if pretty:
        print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=False))
    else:
        print(json.dumps(payload, ensure_ascii=False))


def sanitize_headers(headers: Dict[str, str]) -> Dict[str, str]:
    result = dict(headers)
    for key in ["ACCESS-KEY", "ACCESS-PASSPHRASE", "ACCESS-SIGN"]:
        if key in result:
            result[key] = "***"
    return result


def normalize_contract_symbol(symbol: str) -> str:
    text = symbol.strip().upper().replace("-", "").replace("/", "").replace(" ", "").replace("_", "")
    if text.startswith("CMT") and text.endswith("USDT"):
        text = text[3:]
    if text.endswith("USDT") and len(text) > 4:
        return text
    raise CommandError(f"Unsupported symbol format: {symbol}. Expected like ETHUSDT.")


def normalize_enum(raw: Optional[str], valid_values: Iterable[str], field_name: str) -> Optional[str]:
    if raw is None:
        return None
    value = str(raw).strip().upper()
    if value not in set(valid_values):
        joined = ", ".join(sorted(set(valid_values)))
        raise CommandError(f"Invalid {field_name}: {raw}. Expected one of: {joined}.")
    return value


def normalize_positive_decimal(raw: Optional[str], field_name: str, allow_zero: bool = False) -> Optional[str]:
    if raw is None:
        return None
    text = str(raw).strip()
    if not text:
        raise CommandError(f"{field_name} cannot be empty.")
    try:
        value = Decimal(text)
    except InvalidOperation as exc:
        raise CommandError(f"Invalid decimal for {field_name}: {raw}.") from exc
    if not value.is_finite():
        raise CommandError(f"Invalid decimal for {field_name}: {raw}.")
    if allow_zero:
        if value < 0:
            raise CommandError(f"{field_name} must be >= 0.")
    elif value <= 0:
        raise CommandError(f"{field_name} must be > 0.")
    rendered = format(value.normalize(), "f")
    if "." in rendered:
        rendered = rendered.rstrip("0").rstrip(".")
    return rendered or "0"


def is_zeroish(value: Any) -> bool:
    if value in (None, "", False):
        return True
    try:
        return Decimal(str(value)) == 0
    except InvalidOperation:
        return False


def parse_json_arg(raw: str, arg_name: str) -> Dict[str, Any]:
    text = raw.strip()
    if not text:
        return {}
    if text.startswith("@"):
        raise CommandError(f"{arg_name} no longer accepts @file input. Pass a JSON object string directly.")
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError as exc:
        raise CommandError(f"Invalid JSON for {arg_name}: {exc}") from exc
    if parsed is None:
        return {}
    if not isinstance(parsed, dict):
        raise CommandError(f"{arg_name} must be a JSON object.")
    return parsed


def parse_json_list_arg(raw: str, arg_name: str) -> List[Any]:
    text = raw.strip()
    if not text:
        return []
    if text.startswith("@"):
        raise CommandError(f"{arg_name} no longer accepts @file input. Pass a JSON array string directly.")
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError as exc:
        raise CommandError(f"Invalid JSON for {arg_name}: {exc}") from exc
    if parsed is None:
        return []
    if not isinstance(parsed, list):
        raise CommandError(f"{arg_name} must be a JSON array.")
    return parsed


def compact_json(value: Optional[Dict[str, Any]]) -> str:
    if not value:
        return ""
    return json.dumps(value, separators=(",", ":"), ensure_ascii=False)


def generate_client_id(prefix: str = "codex") -> str:
    return f"{prefix}-{int(time.time() * 1000)}-{secrets.token_hex(3)}"


def find_endpoint_key_by_doc_suffix(doc_suffix: str) -> str:
    target = f"/{doc_suffix}"
    for endpoint in ENDPOINTS.values():
        if endpoint.doc_url.endswith(target):
            return endpoint.key
    raise CommandError(f"Unable to find endpoint with doc suffix {doc_suffix}.")


def ensure_list(value: Any) -> List[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    return [value]


def parse_identifier_list(raw: Optional[str], arg_name: str, *, numeric: bool) -> List[str]:
    if raw is None:
        return []
    text = str(raw).strip()
    if not text:
        return []

    values: List[Any]
    if text.startswith("["):
        values = parse_json_list_arg(text, arg_name)
    else:
        values = [part.strip() for part in text.split(",")]

    parsed: List[str] = []
    seen = set()
    for item in values:
        value = str(item).strip()
        if not value:
            continue
        if numeric:
            if not value.isdigit():
                raise CommandError(f"{arg_name} must contain integer IDs only.")
            value = str(int(value))
            if int(value) <= 0:
                raise CommandError(f"{arg_name} must contain IDs > 0.")
        if value not in seen:
            seen.add(value)
            parsed.append(value)
    return parsed


def extract_position_identifier(position: Dict[str, Any]) -> Optional[str]:
    for key in ("id", "positionId", "isolatedPositionId"):
        value = position.get(key)
        if value not in (None, ""):
            return str(value)
    return None


def extract_position_side(position: Dict[str, Any]) -> Optional[str]:
    raw = position.get("side")
    if raw in (None, ""):
        raw = position.get("positionSide")
    if raw in (None, ""):
        return None
    return str(raw).strip().upper()


def extract_order_client_id(order: Dict[str, Any]) -> Optional[str]:
    for key in ("clientOrderId", "newClientOrderId", "origClientOrderId", "clientOid"):
        value = order.get(key)
        if value not in (None, ""):
            return str(value)
    return None


def order_matches_identifiers(
    order: Dict[str, Any],
    *,
    order_ids: Optional[Iterable[str]] = None,
    client_ids: Optional[Iterable[str]] = None,
) -> bool:
    order_id_values = {str(item) for item in order_ids or []}
    client_id_values = {str(item) for item in client_ids or []}
    if order_id_values:
        order_id = order.get("orderId")
        if order_id not in (None, "") and str(order_id) in order_id_values:
            return True
    if client_id_values:
        client_id = extract_order_client_id(order)
        if client_id is not None and client_id in client_id_values:
            return True
    return False


def find_matching_orders(
    orders: Iterable[Dict[str, Any]],
    *,
    order_ids: Optional[Iterable[str]] = None,
    client_ids: Optional[Iterable[str]] = None,
) -> List[Dict[str, Any]]:
    return [
        item for item in orders
        if isinstance(item, dict) and order_matches_identifiers(item, order_ids=order_ids, client_ids=client_ids)
    ]


def first_matching_symbol(items: Any, symbol: str) -> Optional[Dict[str, Any]]:
    normalized = normalize_contract_symbol(symbol)
    for item in ensure_list(items):
        if isinstance(item, dict) and str(item.get("symbol", "")).upper() == normalized:
            return item
    return None


def filter_positions_by_symbol(positions: Any, symbol: Optional[str]) -> List[Dict[str, Any]]:
    normalized = normalize_contract_symbol(symbol) if symbol else None
    results: List[Dict[str, Any]] = []
    for item in ensure_list(positions):
        if not isinstance(item, dict):
            continue
        if normalized and str(item.get("symbol", "")).upper() != normalized:
            continue
        results.append(item)
    return results


def non_flat_positions(positions: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
    return [item for item in positions if not is_zeroish(item.get("size"))]


def summarize_success_items(items: List[Dict[str, Any]]) -> Tuple[bool, List[str]]:
    failures: List[str] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        if item.get("success") is True:
            continue
        error_message = item.get("errorMessage") or item.get("msg") or item.get("message") or "unknown failure"
        error_code = item.get("errorCode") or item.get("code")
        if error_code not in (None, "", 0, "0"):
            failures.append(f"{error_code}: {error_message}")
        else:
            failures.append(str(error_message))
    return not failures, failures


def unwrap_api_payload(payload: Any) -> Tuple[Any, Optional[Dict[str, Any]]]:
    if not isinstance(payload, dict):
        return payload, None
    if "data" not in payload:
        return payload, None
    wrapper_keys = {"code", "msg", "message", "success", "requestTime", "ts", "traceId", "data"}
    if set(payload.keys()).issubset(wrapper_keys):
        wrapper = dict(payload)
        return wrapper.get("data"), wrapper
    return payload, None


def analyze_business_payload(payload: Any) -> Dict[str, Any]:
    unwrapped, wrapper = unwrap_api_payload(payload)
    errors: List[str] = []
    signal = "implicit_http_ok"
    ok = True

    if wrapper is not None:
        code = wrapper.get("code")
        msg = str(wrapper.get("msg") or wrapper.get("message") or "").strip().lower()
        if code is not None:
            signal = "wrapper_code"
            if str(code) not in OK_CODES and msg not in {"", "success", "ok"}:
                ok = False
                errors.append(f"code={code} msg={wrapper.get('msg') or wrapper.get('message') or ''}".strip())
        elif isinstance(wrapper.get("success"), bool):
            signal = "wrapper_success"
            ok = bool(wrapper.get("success"))
            if not ok:
                errors.append(str(wrapper.get("msg") or wrapper.get("message") or "wrapper reported failure"))

    if ok and isinstance(unwrapped, dict):
        if isinstance(unwrapped.get("success"), bool):
            signal = "success_boolean"
            ok = bool(unwrapped.get("success"))
            if not ok:
                error_code = unwrapped.get("errorCode")
                error_message = unwrapped.get("errorMessage") or "business failure"
                if error_code not in (None, "", 0, "0"):
                    errors.append(f"{error_code}: {error_message}")
                else:
                    errors.append(str(error_message))
        elif "errorCode" in unwrapped and str(unwrapped.get("errorCode") or "") not in {"", "0"}:
            signal = "error_code"
            ok = False
            errors.append(f"{unwrapped.get('errorCode')}: {unwrapped.get('errorMessage') or 'business failure'}")
        elif "code" in unwrapped and str(unwrapped.get("code")) not in OK_CODES:
            signal = "code_field"
            ok = False
            errors.append(f"code={unwrapped.get('code')} msg={unwrapped.get('msg') or unwrapped.get('message') or ''}".strip())
    elif ok and isinstance(unwrapped, list):
        success_items = [item for item in unwrapped if isinstance(item, dict) and "success" in item]
        if success_items:
            signal = "list_success"
            ok, errors = summarize_success_items(success_items)

    return {
        "ok": ok,
        "data": unwrapped,
        "analysis": {
            "signal": signal,
            "wrapper": wrapper,
            "errors": errors,
        },
    }


def describe_failure(http_ok: bool, status: Optional[int], payload: Any, business: Optional[Dict[str, Any]] = None) -> str:
    if not http_ok:
        return f"http_status={status} payload={json.dumps(payload, ensure_ascii=False)}"
    if business is None:
        return f"payload={json.dumps(payload, ensure_ascii=False)}"
    errors = business["analysis"].get("errors") or ["business failure"]
    return "; ".join(errors)


def normalize_bool_flag(raw: bool) -> bool:
    return bool(raw)


def decimal_places(value: str) -> int:
    text = str(value)
    if "." not in text:
        return 0
    return len(text.split(".", 1)[1].rstrip("0"))


def validate_step(value: str, step: str) -> bool:
    try:
        remainder = Decimal(value) % Decimal(step)
    except InvalidOperation:
        return False
    return remainder == 0


def collect_symbol_rules(symbol_info: Optional[Dict[str, Any]]) -> Dict[str, str]:
    if not symbol_info:
        return {}
    rules: Dict[str, str] = {}
    direct_map = {
        "tickSize": "price_step",
        "priceStep": "price_step",
        "stepSize": "quantity_step",
        "qtyStep": "quantity_step",
        "quantityStep": "quantity_step",
        "minQty": "min_quantity",
        "minOrderQty": "min_quantity",
        "minTradeNum": "min_quantity",
        "minNotional": "min_notional",
        "minTradeAmount": "min_notional",
    }
    for source_key, target_key in direct_map.items():
        value = symbol_info.get(source_key)
        if value not in (None, ""):
            rules[target_key] = str(value)
    if "pricePrecision" in symbol_info and symbol_info["pricePrecision"] not in (None, ""):
        rules["price_precision"] = str(symbol_info["pricePrecision"])
    if "priceScale" in symbol_info and symbol_info["priceScale"] not in (None, ""):
        rules["price_precision"] = str(symbol_info["priceScale"])
    if "quantityPrecision" in symbol_info and symbol_info["quantityPrecision"] not in (None, ""):
        rules["quantity_precision"] = str(symbol_info["quantityPrecision"])
    if "qtyPrecision" in symbol_info and symbol_info["qtyPrecision"] not in (None, ""):
        rules["quantity_precision"] = str(symbol_info["qtyPrecision"])
    return rules


def validate_against_symbol_rules(
    *,
    quantity: str,
    price: Optional[str],
    symbol_rules: Dict[str, str],
) -> List[str]:
    warnings: List[str] = []
    min_quantity = symbol_rules.get("min_quantity")
    quantity_step = symbol_rules.get("quantity_step")
    quantity_precision = symbol_rules.get("quantity_precision")
    price_step = symbol_rules.get("price_step")
    price_precision = symbol_rules.get("price_precision")
    min_notional = symbol_rules.get("min_notional")

    if min_quantity and Decimal(quantity) < Decimal(min_quantity):
        warnings.append(f"quantity is below min_quantity {min_quantity}")
    if quantity_step and not validate_step(quantity, quantity_step):
        warnings.append(f"quantity does not align with quantity_step {quantity_step}")
    if quantity_precision and decimal_places(quantity) > int(quantity_precision):
        warnings.append(f"quantity has more than {quantity_precision} decimal places")
    if price is not None:
        if price_step and not validate_step(price, price_step):
            warnings.append(f"price does not align with price_step {price_step}")
        if price_precision and decimal_places(price) > int(price_precision):
            warnings.append(f"price has more than {price_precision} decimal places")
        if min_notional and Decimal(quantity) * Decimal(price) < Decimal(min_notional):
            warnings.append(f"notional is below min_notional {min_notional}")
    return warnings


class WeexContractClient:
    def __init__(
        self,
        base_url: str,
        timeout: float,
        locale: str,
        api_key: Optional[str],
        api_secret: Optional[str],
        api_passphrase: Optional[str],
        user_agent: str = "weex-trader-skill-contract/2.0",
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.locale = locale
        self.api_key = api_key
        self.api_secret = api_secret
        self.api_passphrase = api_passphrase
        self.user_agent = user_agent

    def _require_auth(self) -> None:
        missing = []
        if not self.api_key:
            missing.append("WEEX_API_KEY")
        if not self.api_secret:
            missing.append("WEEX_API_SECRET")
        if not self.api_passphrase:
            missing.append("WEEX_API_PASSPHRASE")
        if missing:
            raise CommandError(
                "Missing private API credentials in environment. "
                "Set these vars and retry: " + ", ".join(missing)
            )

    def _sign(self, timestamp_ms: str, method: str, path: str, query_string: str, body_str: str) -> str:
        message = f"{timestamp_ms}{method}{path}"
        if query_string:
            message += f"?{query_string}"
        message += body_str
        digest = hmac.new(
            self.api_secret.encode("utf-8"),
            message.encode("utf-8"),
            hashlib.sha256,
        ).digest()
        return base64.b64encode(digest).decode("utf-8")

    def prepare_request(
        self,
        endpoint: Endpoint,
        query: Optional[Dict[str, Any]] = None,
        body: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        method = endpoint.method.upper()
        query_payload = query or {}
        body_payload = body or {}
        query_string = parse.urlencode(query_payload, doseq=True)
        body_str = compact_json(body_payload)

        headers = {
            "Accept": "application/json",
            "Content-Type": "application/json",
            "locale": self.locale,
            "User-Agent": self.user_agent,
        }

        if endpoint.auth:
            self._require_auth()
            timestamp_ms = str(int(time.time() * 1000))
            sign = self._sign(timestamp_ms, method, endpoint.path, query_string, body_str)
            headers.update(
                {
                    "ACCESS-KEY": self.api_key,
                    "ACCESS-PASSPHRASE": self.api_passphrase,
                    "ACCESS-TIMESTAMP": timestamp_ms,
                    "ACCESS-SIGN": sign,
                }
            )

        url = f"{self.base_url}{endpoint.path}"
        if query_string:
            url = f"{url}?{query_string}"

        data = body_str.encode("utf-8") if body_str and method != "GET" else None

        return {
            "method": method,
            "url": url,
            "headers": headers,
            "data": data,
            "query": query_payload,
            "body": body_payload,
        }

    def send(self, prepared: Dict[str, Any]) -> Dict[str, Any]:
        req = request.Request(
            url=prepared["url"],
            method=prepared["method"],
            data=prepared["data"],
            headers=prepared["headers"],
        )
        try:
            with request.urlopen(req, timeout=self.timeout) as resp:
                raw = resp.read().decode("utf-8", errors="replace")
                try:
                    payload = json.loads(raw)
                except json.JSONDecodeError:
                    payload = {"raw": raw}
                return {"ok": True, "status": resp.status, "data": payload}
        except error.HTTPError as exc:
            raw = exc.read().decode("utf-8", errors="replace")
            try:
                payload = json.loads(raw)
            except json.JSONDecodeError:
                payload = {"raw": raw}
            return {"ok": False, "status": exc.code, "error": payload}
        except error.URLError as exc:
            return {"ok": False, "status": None, "error": {"message": str(exc)}}


def execute_request(
    client: WeexContractClient,
    *,
    endpoint_key: str,
    query: Optional[Dict[str, Any]] = None,
    body: Optional[Dict[str, Any]] = None,
    dry_run: bool = False,
    confirm_live: bool = False,
    allow_mutating: bool = False,
) -> Dict[str, Any]:
    endpoint = ENDPOINTS[endpoint_key]
    if endpoint.mutating and not allow_mutating:
        raise CommandError(
            f"Raw mutating access to {endpoint_key} is disabled. Use a structured command for live trading actions."
        )
    if endpoint.mutating and not dry_run and not confirm_live:
        raise CommandError(
            f"Refusing live mutating request for {endpoint_key}. Use --confirm-live to send, or --dry-run to preview."
        )

    prepared = client.prepare_request(endpoint, query=query, body=body)
    if dry_run:
        return {
            "ok": True,
            "dry_run": True,
            "endpoint": endpoint.key,
            "method": endpoint.method,
            "path": endpoint.path,
            "url": prepared["url"],
            "headers": sanitize_headers(prepared["headers"]),
            "query": query or {},
            "body": body or {},
        }

    response = client.send(prepared)
    raw_payload = response.get("data") if response.get("ok") else response.get("error")
    business = analyze_business_payload(raw_payload) if response.get("ok") else None
    result = {
        "endpoint": endpoint.key,
        "method": endpoint.method,
        "path": endpoint.path,
        "status": response.get("status"),
        "http_ok": bool(response.get("ok")),
        "business_ok": business["ok"] if business is not None else False,
        "ok": bool(response.get("ok")) and business is not None and business["ok"],
        "result": business["data"] if business is not None else raw_payload,
        "raw_result": raw_payload,
        "analysis": business["analysis"] if business is not None else None,
    }
    return result


def require_success(result: Dict[str, Any], context: str) -> Any:
    if not result.get("ok"):
        raise CommandError(
            f"{context} failed: {describe_failure(result['http_ok'], result['status'], result['raw_result'], {'analysis': result.get('analysis') or {}})}"
        )
    return result.get("result")


class ContractState:
    def __init__(self, client: WeexContractClient) -> None:
        self.client = client
        self._cache: Dict[Tuple[str, str], Any] = {}

    def _fetch(self, endpoint_key: str, *, query: Optional[Dict[str, Any]] = None, body: Optional[Dict[str, Any]] = None) -> Any:
        query_key = json.dumps(query or {}, sort_keys=True, ensure_ascii=False)
        body_key = json.dumps(body or {}, sort_keys=True, ensure_ascii=False)
        cache_key = (endpoint_key, f"{query_key}|{body_key}")
        if cache_key not in self._cache:
            result = execute_request(self.client, endpoint_key=endpoint_key, query=query, body=body, allow_mutating=True, confirm_live=True)
            self._cache[cache_key] = require_success(result, endpoint_key)
        return self._cache[cache_key]

    def account_config(self) -> Dict[str, Any]:
        data = self._fetch("account.get_account_config")
        return data if isinstance(data, dict) else {}

    def balances(self) -> List[Dict[str, Any]]:
        data = self._fetch("account.get_account_balance")
        return [item for item in ensure_list(data) if isinstance(item, dict)]

    def positions(self, symbol: Optional[str] = None) -> List[Dict[str, Any]]:
        data = self._fetch("account.get_all_positions")
        return filter_positions_by_symbol(data, symbol)

    def symbol_config(self, symbol: str) -> Dict[str, Any]:
        normalized = normalize_contract_symbol(symbol)
        data = self._fetch("account.get_symbol_config", query={"symbol": normalized})
        if isinstance(data, dict) and str(data.get("symbol", "")).upper() == normalized:
            return data
        match = first_matching_symbol(data, normalized)
        if match is None:
            raise CommandError(f"No symbol configuration returned for {normalized}.")
        return match

    def open_orders(self, symbol: Optional[str] = None) -> List[Dict[str, Any]]:
        query = {"symbol": normalize_contract_symbol(symbol)} if symbol else {}
        data = self._fetch("transaction.get_current_order_status", query=query)
        return [item for item in ensure_list(data) if isinstance(item, dict)]

    def pending_orders(self, symbol: Optional[str] = None) -> List[Dict[str, Any]]:
        query = {"symbol": normalize_contract_symbol(symbol)} if symbol else {}
        data = self._fetch("transaction.get_current_pending_orders", query=query)
        if isinstance(data, dict) and "orders" in data:
            data = data["orders"]
        return [item for item in ensure_list(data) if isinstance(item, dict)]

    def order_info(self, order_id: str) -> Dict[str, Any]:
        data = self._fetch("transaction.get_single_order_info", query={"orderId": order_id})
        return data if isinstance(data, dict) else {}

    def trade_details(self, symbol: Optional[str] = None, order_id: Optional[str] = None) -> List[Dict[str, Any]]:
        query: Dict[str, Any] = {}
        if symbol:
            query["symbol"] = normalize_contract_symbol(symbol)
        if order_id:
            query["orderId"] = order_id
        data = self._fetch("transaction.get_trade_details", query=query)
        return [item for item in ensure_list(data) if isinstance(item, dict)]

    def ticker(self, symbol: str) -> Dict[str, Any]:
        normalized = normalize_contract_symbol(symbol)
        data = self._fetch("market.get_symbol_price", query={"symbol": normalized})
        if isinstance(data, dict) and "symbol" in data:
            return data
        match = first_matching_symbol(data, normalized)
        return match or {}

    def book_ticker(self, symbol: str) -> Dict[str, Any]:
        normalized = normalize_contract_symbol(symbol)
        data = self._fetch("market.get_book_ticker", query={"symbol": normalized})
        if isinstance(data, dict) and "symbol" in data:
            return data
        match = first_matching_symbol(data, normalized)
        return match or {}

    def funding_rate(self, symbol: str) -> Dict[str, Any]:
        normalized = normalize_contract_symbol(symbol)
        data = self._fetch("market.get_current_funding_rate", query={"symbol": normalized})
        if isinstance(data, dict) and "symbol" in data:
            return data
        match = first_matching_symbol(data, normalized)
        return match or {}

    def contract_info(self, symbol: str) -> Dict[str, Any]:
        normalized = normalize_contract_symbol(symbol)
        data = self._fetch("market.get_contract_info", query={"symbol": normalized})
        if isinstance(data, dict):
            match = first_matching_symbol(data.get("symbols"), normalized)
            if match:
                return match
            if str(data.get("symbol", "")).upper() == normalized:
                return data
        match = first_matching_symbol(data, normalized)
        return match or {}


def make_action_payload(action: str, *, ok: bool, **extra: Any) -> Dict[str, Any]:
    payload = {"ok": ok, "action": action}
    payload.update(extra)
    return payload


def resolve_order_side_position(args: argparse.Namespace) -> Tuple[str, str]:
    intent = normalize_enum(args.intent, ORDER_INTENTS.keys(), "intent") if getattr(args, "intent", None) else None
    side = normalize_enum(getattr(args, "side", None), {"BUY", "SELL"}, "side") if getattr(args, "side", None) else None
    position_side = normalize_enum(getattr(args, "position_side", None), {"LONG", "SHORT"}, "position-side") if getattr(args, "position_side", None) else None

    if intent is not None:
        mapped_side, mapped_position_side = ORDER_INTENTS[intent]
        if side is not None and side != mapped_side:
            raise CommandError(f"intent={intent} conflicts with side={side}.")
        if position_side is not None and position_side != mapped_position_side:
            raise CommandError(f"intent={intent} conflicts with position-side={position_side}.")
        side = mapped_side
        position_side = mapped_position_side

    if side is None or position_side is None:
        raise CommandError("Provide either --intent or both --side and --position-side.")
    return side, position_side


def ensure_trade_enabled(state: ContractState) -> Dict[str, Any]:
    account_config = state.account_config()
    if account_config.get("canTrade") is False:
        raise CommandError("Trading is disabled for this account according to account configuration.")
    return account_config


def current_position_summary(positions: List[Dict[str, Any]]) -> Dict[str, Any]:
    return {
        "count": len(positions),
        "open_count": len(non_flat_positions(positions)),
        "positions": positions,
    }


def maybe_no_action_if_already(
    *,
    action: str,
    pretty: bool,
    summary: str,
    state_before: Dict[str, Any],
) -> int:
    output_json(
        make_action_payload(
            action,
            ok=True,
            no_action=True,
            summary=summary,
            state_before=state_before,
        ),
        pretty,
    )
    return 0
