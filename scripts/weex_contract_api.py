#!/usr/bin/env python3
"""WEEX contract trading helper for agent-facing workflows.

- Contract only. Spot is intentionally unsupported in this skill version.
- Read-only raw endpoint access is available for inspection.
- Mutating actions are exposed through structured commands with preflight checks.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import hmac
import json
import os
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
    "cancel_order": "normal",
    "cancel_open_orders": "elevated",
    "close_positions": "elevated",
    "set_leverage": "normal",
    "set_margin_mode": "elevated",
    "set_auto_append_margin": "normal",
    "place_conditional_order": "normal",
    "cancel_conditional_order": "normal",
    "place_tpsl_order": "normal",
    "modify_tpsl_order": "normal",
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
    refs = Path(__file__).resolve().parent.parent / "references" / "contract-api-definitions.json"
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
        raise CommandError(f"{context} failed: {describe_failure(result['http_ok'], result['status'], result['raw_result'], {'analysis': result.get('analysis') or {}})}")
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


def cmd_list_endpoints(args: argparse.Namespace) -> int:
    rows = []
    for endpoint in sorted(ENDPOINTS.values(), key=lambda item: (item.group, item.key)):
        if args.group and endpoint.group != args.group:
            continue
        if args.read_only and endpoint.mutating:
            continue
        rows.append(
            {
                "key": endpoint.key,
                "group": endpoint.group,
                "method": endpoint.method,
                "path": endpoint.path,
                "auth": endpoint.auth,
                "mutating": endpoint.mutating,
                "raw_access": "read_only" if not endpoint.mutating else "disabled",
                "doc_url": endpoint.doc_url,
            }
        )
    output_json({"ok": True, "count": len(rows), "endpoints": rows}, args.pretty)
    return 0


def cmd_call(args: argparse.Namespace, client: WeexContractClient) -> int:
    query = parse_json_arg(args.query, "--query")
    body = parse_json_arg(args.body, "--body")
    result = execute_request(
        client,
        endpoint_key=args.endpoint,
        query=query,
        body=body,
        dry_run=args.dry_run,
        confirm_live=False,
        allow_mutating=False,
    )
    output_json(result, args.pretty)
    return 0 if result.get("ok") else 1


def cmd_ticker(args: argparse.Namespace, client: WeexContractClient) -> int:
    symbol = normalize_contract_symbol(args.symbol)
    state = ContractState(client)
    payload = make_action_payload(
        "ticker",
        ok=True,
        symbol=symbol,
        ticker=state.ticker(symbol),
        book_ticker=state.book_ticker(symbol),
        funding_rate=state.funding_rate(symbol),
    )
    output_json(payload, args.pretty)
    return 0


def cmd_poll_ticker(args: argparse.Namespace, client: WeexContractClient) -> int:
    symbol = normalize_contract_symbol(args.symbol)
    run_count = 0
    while True:
        run_count += 1
        state = ContractState(client)
        payload = make_action_payload(
            "poll_ticker",
            ok=True,
            symbol=symbol,
            iteration=run_count,
            ticker=state.ticker(symbol),
            book_ticker=state.book_ticker(symbol),
        )
        output_json(payload, args.pretty)
        if args.count > 0 and run_count >= args.count:
            return 0
        time.sleep(args.interval)


def cmd_account_snapshot(args: argparse.Namespace, client: WeexContractClient) -> int:
    symbol = normalize_contract_symbol(args.symbol) if args.symbol else None
    state = ContractState(client)
    payload = make_action_payload(
        "account_snapshot",
        ok=True,
        symbol=symbol,
        account_config=state.account_config(),
        balances=state.balances(),
        positions=state.positions(symbol),
        open_orders=state.open_orders(symbol),
        pending_orders=state.pending_orders(symbol),
        symbol_config=state.symbol_config(symbol) if symbol else None,
        ticker=state.ticker(symbol) if symbol else None,
    )
    output_json(payload, args.pretty)
    return 0


def cmd_positions(args: argparse.Namespace, client: WeexContractClient) -> int:
    symbol = normalize_contract_symbol(args.symbol) if args.symbol else None
    state = ContractState(client)
    positions = state.positions(symbol)
    payload = make_action_payload(
        "positions",
        ok=True,
        symbol=symbol,
        positions=positions,
        open_positions=non_flat_positions(positions),
    )
    output_json(payload, args.pretty)
    return 0


def cmd_open_orders(args: argparse.Namespace, client: WeexContractClient) -> int:
    symbol = normalize_contract_symbol(args.symbol) if args.symbol else None
    state = ContractState(client)
    payload = make_action_payload(
        "open_orders",
        ok=True,
        symbol=symbol,
        orders=state.open_orders(symbol),
    )
    output_json(payload, args.pretty)
    return 0


def cmd_pending_orders(args: argparse.Namespace, client: WeexContractClient) -> int:
    symbol = normalize_contract_symbol(args.symbol) if args.symbol else None
    state = ContractState(client)
    payload = make_action_payload(
        "pending_orders",
        ok=True,
        symbol=symbol,
        orders=state.pending_orders(symbol),
    )
    output_json(payload, args.pretty)
    return 0


def cmd_order_info(args: argparse.Namespace, client: WeexContractClient) -> int:
    state = ContractState(client)
    order = state.order_info(str(args.order_id))
    trades = state.trade_details(symbol=args.symbol, order_id=str(args.order_id)) if args.include_trades else None
    payload = make_action_payload(
        "order_info",
        ok=True,
        order=order,
        trades=trades,
    )
    output_json(payload, args.pretty)
    return 0


def build_place_order_body(args: argparse.Namespace, state: ContractState, symbol: str) -> Tuple[Dict[str, Any], Dict[str, Any], List[str]]:
    ensure_trade_enabled(state)
    symbol_config = state.symbol_config(symbol)
    symbol_rules = collect_symbol_rules(state.contract_info(symbol))

    side, position_side = resolve_order_side_position(args)
    if (side, position_side) in RISK_ADVANCED_SIDE_PAIRS and not normalize_bool_flag(args.allow_position_reduction):
        raise CommandError(
            "This side/position-side pair can reduce or reverse an existing position. "
            "Use close-positions for full exits, or pass --allow-position-reduction for an explicit advanced order."
        )

    order_type = normalize_enum(args.order_type, {"LIMIT", "MARKET"}, "type")
    quantity = normalize_positive_decimal(args.quantity, "quantity")
    price = normalize_positive_decimal(args.price, "price") if args.price is not None else None
    time_in_force = normalize_enum(args.time_in_force or ("GTC" if order_type == "LIMIT" else None), {"GTC", "IOC", "FOK"}, "time-in-force")

    body: Dict[str, Any] = {
        "symbol": symbol,
        "side": side,
        "positionSide": position_side,
        "type": order_type,
        "quantity": quantity,
        "newClientOrderId": args.new_client_order_id or generate_client_id("order"),
    }
    if order_type == "LIMIT":
        if price is None:
            raise CommandError("price is required when type=LIMIT.")
        body["price"] = price
        body["timeInForce"] = time_in_force
    elif price is not None:
        raise CommandError("price must be omitted when type=MARKET.")

    take_profit = normalize_positive_decimal(args.take_profit, "take-profit") if args.take_profit is not None else None
    stop_loss = normalize_positive_decimal(args.stop_loss, "stop-loss") if args.stop_loss is not None else None
    if take_profit is not None:
        body["tpTriggerPrice"] = take_profit
        body["TpWorkingType"] = normalize_enum(args.tp_working_type or "CONTRACT_PRICE", {"CONTRACT_PRICE", "MARK_PRICE"}, "tp-working-type")
    if stop_loss is not None:
        body["slTriggerPrice"] = stop_loss
        body["SlWorkingType"] = normalize_enum(args.sl_working_type or "CONTRACT_PRICE", {"CONTRACT_PRICE", "MARK_PRICE"}, "sl-working-type")

    warnings = validate_against_symbol_rules(quantity=quantity, price=price, symbol_rules=symbol_rules)
    preflight = {
        "risk": RISK_SCOPES["place_order"],
        "symbol_config": symbol_config,
        "symbol_rules": symbol_rules,
        "positions": current_position_summary(state.positions(symbol)),
        "ticker": state.ticker(symbol),
    }
    return body, preflight, warnings


def cmd_place_order(args: argparse.Namespace, client: WeexContractClient) -> int:
    symbol = normalize_contract_symbol(args.symbol)
    state = ContractState(client)
    body, preflight, warnings = build_place_order_body(args, state, symbol)

    result = execute_request(
        client,
        endpoint_key=find_endpoint_key_by_doc_suffix("PlaceOrder"),
        body=body,
        dry_run=args.dry_run,
        confirm_live=args.confirm_live,
        allow_mutating=True,
    )

    if result.get("dry_run"):
        output_json(
            make_action_payload(
                "place_order",
                ok=True,
                dry_run=True,
                symbol=symbol,
                preflight=preflight,
                warnings=warnings,
                request=result,
            ),
            args.pretty,
        )
        return 0

    response_data = require_success(result, "place_order")
    order_id = None
    if isinstance(response_data, dict):
        order_id = response_data.get("orderId")

    verification = None
    if order_id:
        verification_state = ContractState(client)
        verification = {
            "order": verification_state.order_info(str(order_id)),
            "trades": verification_state.trade_details(symbol=symbol, order_id=str(order_id)),
        }

    output_json(
        make_action_payload(
            "place_order",
            ok=True,
            symbol=symbol,
            risk=RISK_SCOPES["place_order"],
            preflight=preflight,
            warnings=warnings,
            request_body=body,
            result=response_data,
            verification=verification,
        ),
        args.pretty,
    )
    return 0


def cmd_cancel_order(args: argparse.Namespace, client: WeexContractClient) -> int:
    query: Dict[str, Any] = {}
    if args.order_id:
        query["orderId"] = str(args.order_id)
    if args.client_oid:
        query["origClientOrderId"] = args.client_oid
    if not query:
        raise CommandError("Provide at least one of --order-id or --client-oid.")

    result = execute_request(
        client,
        endpoint_key=find_endpoint_key_by_doc_suffix("CancelOrder"),
        query=query,
        dry_run=args.dry_run,
        confirm_live=args.confirm_live,
        allow_mutating=True,
    )

    if result.get("dry_run"):
        output_json(
            make_action_payload(
                "cancel_order",
                ok=True,
                dry_run=True,
                risk=RISK_SCOPES["cancel_order"],
                request=result,
            ),
            args.pretty,
        )
        return 0

    response_data = require_success(result, "cancel_order")
    verification = None
    order_id = None
    if isinstance(response_data, dict):
        order_id = response_data.get("orderId")
    if order_id:
        try:
            verification = ContractState(client).order_info(str(order_id))
        except CommandError:
            verification = None

    output_json(
        make_action_payload(
            "cancel_order",
            ok=True,
            risk=RISK_SCOPES["cancel_order"],
            query=query,
            result=response_data,
            verification=verification,
        ),
        args.pretty,
    )
    return 0


def cmd_cancel_open_orders(args: argparse.Namespace, client: WeexContractClient) -> int:
    if not args.symbol and not args.all:
        raise CommandError("Provide --symbol for symbol-scoped cancellation, or --all for account-wide cancellation.")
    symbol = normalize_contract_symbol(args.symbol) if args.symbol else None
    state = ContractState(client)
    before_orders = state.open_orders(symbol)
    if not before_orders:
        return maybe_no_action_if_already(
            action="cancel_open_orders",
            pretty=args.pretty,
            summary="No open orders to cancel.",
            state_before={"symbol": symbol, "orders": before_orders},
        )

    endpoint_key = "transaction.cancel_all_orders"
    query = {"symbol": symbol} if symbol else {}
    result = execute_request(
        client,
        endpoint_key=endpoint_key,
        query=query,
        dry_run=args.dry_run,
        confirm_live=args.confirm_live,
        allow_mutating=True,
    )

    if result.get("dry_run"):
        output_json(
            make_action_payload(
                "cancel_open_orders",
                ok=True,
                dry_run=True,
                risk=RISK_SCOPES["cancel_open_orders"],
                preflight={"orders_before": before_orders},
                request=result,
            ),
            args.pretty,
        )
        return 0

    response_data = require_success(result, "cancel_open_orders")
    after_orders = ContractState(client).open_orders(symbol)
    output_json(
        make_action_payload(
            "cancel_open_orders",
            ok=True,
            symbol=symbol,
            all=bool(args.all),
            risk=RISK_SCOPES["cancel_open_orders"],
            orders_before=before_orders,
            result=response_data,
            verification={"orders_after": after_orders},
        ),
        args.pretty,
    )
    return 0


def cmd_close_positions(args: argparse.Namespace, client: WeexContractClient) -> int:
    if not args.symbol and not args.all:
        raise CommandError("Provide --symbol for symbol-scoped close, or --all for account-wide close.")
    symbol = normalize_contract_symbol(args.symbol) if args.symbol else None
    state = ContractState(client)
    before_positions = non_flat_positions(state.positions(symbol))
    if not before_positions:
        return maybe_no_action_if_already(
            action="close_positions",
            pretty=args.pretty,
            summary="No open positions to close.",
            state_before={"symbol": symbol, "positions": before_positions},
        )

    body = {"symbol": symbol} if symbol else {}
    result = execute_request(
        client,
        endpoint_key="transaction.close_positions",
        body=body,
        dry_run=args.dry_run,
        confirm_live=args.confirm_live,
        allow_mutating=True,
    )

    if result.get("dry_run"):
        output_json(
            make_action_payload(
                "close_positions",
                ok=True,
                dry_run=True,
                risk=RISK_SCOPES["close_positions"],
                preflight={"positions_before": before_positions},
                request=result,
            ),
            args.pretty,
        )
        return 0

    response_data = require_success(result, "close_positions")
    after_positions = non_flat_positions(ContractState(client).positions(symbol))
    output_json(
        make_action_payload(
            "close_positions",
            ok=True,
            symbol=symbol,
            all=bool(args.all),
            risk=RISK_SCOPES["close_positions"],
            positions_before=before_positions,
            result=response_data,
            verification={"positions_after": after_positions},
        ),
        args.pretty,
    )
    return 0


def build_leverage_request(args: argparse.Namespace, state: ContractState, symbol: str) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    ensure_trade_enabled(state)
    current_config = state.symbol_config(symbol)
    current_margin_type = normalize_enum(current_config.get("marginType"), {"CROSSED", "ISOLATED"}, "current marginType")
    target_margin_type = normalize_enum(args.margin_type or current_margin_type, {"CROSSED", "ISOLATED"}, "margin-type")
    if target_margin_type is None:
        raise CommandError(f"Unable to determine current margin type for {symbol}.")

    request_body: Dict[str, Any] = {"symbol": symbol, "marginType": target_margin_type}
    if target_margin_type == "CROSSED":
        if args.long or args.short:
            raise CommandError("Cross leverage accepts only --value/--cross, not --long or --short.")
        cross = normalize_positive_decimal(args.cross or args.value, "cross leverage")
        if cross is None:
            raise CommandError("Provide --value or --cross when setting cross leverage.")
        request_body["crossLeverage"] = cross
    else:
        if args.cross:
            raise CommandError("Isolated leverage does not accept --cross.")
        long_value = normalize_positive_decimal(args.long, "isolated long leverage") if args.long else None
        short_value = normalize_positive_decimal(args.short, "isolated short leverage") if args.short else None
        if args.value:
            shared = normalize_positive_decimal(args.value, "isolated leverage")
            long_value = shared
            short_value = shared
        if long_value is None and short_value is None:
            raise CommandError(
                "Provide --value to set both isolated sides, or use --long/--short for side-specific leverage."
            )
        if long_value is not None:
            request_body["isolatedLongLeverage"] = long_value
        if short_value is not None:
            request_body["isolatedShortLeverage"] = short_value

    verification_target = {
        "symbol": symbol,
        "current_config": current_config,
        "positions": current_position_summary(state.positions(symbol)),
    }
    return request_body, verification_target


def leverage_already_matches(current_config: Dict[str, Any], request_body: Dict[str, Any]) -> bool:
    if current_config.get("marginType") != request_body.get("marginType"):
        return False
    for key in ["crossLeverage", "isolatedLongLeverage", "isolatedShortLeverage"]:
        if key in request_body and str(current_config.get(key)) != str(request_body.get(key)):
            return False
    return True


def cmd_set_leverage(args: argparse.Namespace, client: WeexContractClient) -> int:
    symbol = normalize_contract_symbol(args.symbol)
    state = ContractState(client)
    body, preflight = build_leverage_request(args, state, symbol)
    if leverage_already_matches(preflight["current_config"], body):
        return maybe_no_action_if_already(
            action="set_leverage",
            pretty=args.pretty,
            summary="Leverage already matches the requested settings.",
            state_before=preflight,
        )

    result = execute_request(
        client,
        endpoint_key="account.update_leverage_trade",
        body=body,
        dry_run=args.dry_run,
        confirm_live=args.confirm_live,
        allow_mutating=True,
    )
    if result.get("dry_run"):
        output_json(
            make_action_payload(
                "set_leverage",
                ok=True,
                dry_run=True,
                risk=RISK_SCOPES["set_leverage"],
                preflight=preflight,
                request=result,
            ),
            args.pretty,
        )
        return 0

    response_data = require_success(result, "set_leverage")
    verification = ContractState(client).symbol_config(symbol)
    output_json(
        make_action_payload(
            "set_leverage",
            ok=True,
            symbol=symbol,
            risk=RISK_SCOPES["set_leverage"],
            preflight=preflight,
            request_body=body,
            result=response_data,
            verification=verification,
        ),
        args.pretty,
    )
    return 0


def margin_mode_already_matches(current_config: Dict[str, Any], target_margin_type: str, target_position_mode: Optional[str]) -> bool:
    if str(current_config.get("marginType")) != target_margin_type:
        return False
    if target_position_mode is not None and str(current_config.get("separatedType")) != target_position_mode:
        return False
    return True


def cmd_set_margin_mode(args: argparse.Namespace, client: WeexContractClient) -> int:
    symbol = normalize_contract_symbol(args.symbol)
    state = ContractState(client)
    ensure_trade_enabled(state)
    current_config = state.symbol_config(symbol)
    target_margin_type = normalize_enum(args.margin_type, {"CROSSED", "ISOLATED"}, "margin-type")
    target_position_mode = normalize_enum(args.position_mode, {"COMBINED", "SEPARATED"}, "position-mode") if args.position_mode else current_config.get("separatedType")
    positions = non_flat_positions(state.positions(symbol))
    open_orders = state.open_orders(symbol)
    pending_orders = state.pending_orders(symbol)

    if not args.allow_when_active and (positions or open_orders or pending_orders):
        raise CommandError(
            "Refusing to switch margin mode while active positions or orders exist for this symbol. "
            "Close/cancel them first, or pass --allow-when-active to attempt the change explicitly."
        )

    if margin_mode_already_matches(current_config, target_margin_type, target_position_mode):
        return maybe_no_action_if_already(
            action="set_margin_mode",
            pretty=args.pretty,
            summary="Margin mode already matches the requested settings.",
            state_before={
                "symbol": symbol,
                "current_config": current_config,
                "positions": positions,
                "open_orders": open_orders,
                "pending_orders": pending_orders,
            },
        )

    body = {
        "symbol": symbol,
        "marginType": target_margin_type,
        "separatedType": target_position_mode,
    }
    result = execute_request(
        client,
        endpoint_key="account.change_margin_mode_trade",
        body=body,
        dry_run=args.dry_run,
        confirm_live=args.confirm_live,
        allow_mutating=True,
    )
    if result.get("dry_run"):
        output_json(
            make_action_payload(
                "set_margin_mode",
                ok=True,
                dry_run=True,
                risk=RISK_SCOPES["set_margin_mode"],
                preflight={
                    "current_config": current_config,
                    "positions": positions,
                    "open_orders": open_orders,
                    "pending_orders": pending_orders,
                },
                request=result,
            ),
            args.pretty,
        )
        return 0

    response_data = require_success(result, "set_margin_mode")
    verification = ContractState(client).symbol_config(symbol)
    output_json(
        make_action_payload(
            "set_margin_mode",
            ok=True,
            symbol=symbol,
            risk=RISK_SCOPES["set_margin_mode"],
            request_body=body,
            result=response_data,
            verification=verification,
        ),
        args.pretty,
    )
    return 0


def resolve_position_id_for_symbol(
    *,
    state: ContractState,
    symbol: Optional[str],
    position_side: Optional[str],
    explicit_position_id: Optional[str],
) -> Tuple[str, Dict[str, Any]]:
    if explicit_position_id:
        return str(explicit_position_id), {}
    if not symbol:
        raise CommandError("Provide --position-id or --symbol.")
    positions = non_flat_positions(state.positions(symbol))
    isolated_positions = [item for item in positions if str(item.get("marginType")) == "ISOLATED"]
    if not isolated_positions:
        raise CommandError(f"No open isolated positions found for {symbol}.")
    if position_side:
        target_side = normalize_enum(position_side, {"LONG", "SHORT"}, "position-side")
        isolated_positions = [item for item in isolated_positions if str(item.get("side")).upper() == target_side]
        if not isolated_positions:
            raise CommandError(f"No isolated {target_side} position found for {symbol}.")
    if len(isolated_positions) != 1:
        raise CommandError("Multiple isolated positions match. Provide --position-side or --position-id to disambiguate.")
    return str(isolated_positions[0]["id"]), isolated_positions[0]


def cmd_set_auto_append_margin(args: argparse.Namespace, client: WeexContractClient) -> int:
    state = ContractState(client)
    symbol = normalize_contract_symbol(args.symbol) if args.symbol else None
    position_id, position = resolve_position_id_for_symbol(
        state=state,
        symbol=symbol,
        position_side=args.position_side,
        explicit_position_id=args.position_id,
    )
    body = {
        "positionId": position_id,
        "autoAppendMargin": bool(args.enabled),
    }
    result = execute_request(
        client,
        endpoint_key="account.modify_auto_append_margin_trade",
        body=body,
        dry_run=args.dry_run,
        confirm_live=args.confirm_live,
        allow_mutating=True,
    )
    if result.get("dry_run"):
        output_json(
            make_action_payload(
                "set_auto_append_margin",
                ok=True,
                dry_run=True,
                risk=RISK_SCOPES["set_auto_append_margin"],
                preflight={"position": position},
                request=result,
            ),
            args.pretty,
        )
        return 0

    response_data = require_success(result, "set_auto_append_margin")
    verification = None
    if symbol:
        verification = non_flat_positions(ContractState(client).positions(symbol))
    output_json(
        make_action_payload(
            "set_auto_append_margin",
            ok=True,
            symbol=symbol,
            risk=RISK_SCOPES["set_auto_append_margin"],
            position=position,
            result=response_data,
            verification=verification,
        ),
        args.pretty,
    )
    return 0


def build_conditional_order_body(args: argparse.Namespace, state: ContractState, symbol: str) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    ensure_trade_enabled(state)
    side, position_side = resolve_order_side_position(args)
    if (side, position_side) in RISK_ADVANCED_SIDE_PAIRS and not normalize_bool_flag(args.allow_position_reduction):
        raise CommandError(
            "This conditional order may reduce or reverse a position. "
            "Use place-tpsl-order for dedicated exits, or pass --allow-position-reduction for an explicit advanced order."
        )

    conditional_type = normalize_enum(args.conditional_type, {"STOP", "TAKE_PROFIT", "STOP_MARKET", "TAKE_PROFIT_MARKET"}, "conditional-type")
    quantity = normalize_positive_decimal(args.quantity, "quantity")
    trigger_price = normalize_positive_decimal(args.trigger_price, "trigger-price")
    execute_price = normalize_positive_decimal(args.execute_price, "execute-price", allow_zero=True) if args.execute_price is not None else None

    if conditional_type in {"STOP", "TAKE_PROFIT"} and execute_price is None:
        raise CommandError("execute-price is required for STOP and TAKE_PROFIT conditional orders.")
    if conditional_type in {"STOP_MARKET", "TAKE_PROFIT_MARKET"} and execute_price not in (None, "0"):
        raise CommandError("execute-price must be omitted or 0 for *_MARKET conditional orders.")

    body: Dict[str, Any] = {
        "symbol": symbol,
        "side": side,
        "positionSide": position_side,
        "type": conditional_type,
        "quantity": quantity,
        "triggerPrice": trigger_price,
        "clientAlgoId": args.client_algo_id or generate_client_id("algo"),
    }
    if execute_price is not None:
        body["price"] = execute_price

    preset_take_profit = normalize_positive_decimal(args.preset_take_profit, "preset-take-profit") if args.preset_take_profit else None
    preset_stop_loss = normalize_positive_decimal(args.preset_stop_loss, "preset-stop-loss") if args.preset_stop_loss else None
    if preset_take_profit is not None:
        body["presetTakeProfitPrice"] = preset_take_profit
        body["TpWorkingType"] = normalize_enum(args.tp_working_type or "CONTRACT_PRICE", {"CONTRACT_PRICE", "MARK_PRICE"}, "tp-working-type")
    if preset_stop_loss is not None:
        body["presetStopLossPrice"] = preset_stop_loss
        body["SlWorkingType"] = normalize_enum(args.sl_working_type or "CONTRACT_PRICE", {"CONTRACT_PRICE", "MARK_PRICE"}, "sl-working-type")

    preflight = {
        "risk": RISK_SCOPES["place_conditional_order"],
        "positions": current_position_summary(state.positions(symbol)),
        "pending_orders": state.pending_orders(symbol),
        "ticker": state.ticker(symbol),
    }
    return body, preflight


def cmd_place_conditional_order(args: argparse.Namespace, client: WeexContractClient) -> int:
    symbol = normalize_contract_symbol(args.symbol)
    state = ContractState(client)
    body, preflight = build_conditional_order_body(args, state, symbol)
    result = execute_request(
        client,
        endpoint_key="transaction.place_pending_order",
        body=body,
        dry_run=args.dry_run,
        confirm_live=args.confirm_live,
        allow_mutating=True,
    )
    if result.get("dry_run"):
        output_json(
            make_action_payload(
                "place_conditional_order",
                ok=True,
                dry_run=True,
                symbol=symbol,
                preflight=preflight,
                request=result,
            ),
            args.pretty,
        )
        return 0

    response_data = require_success(result, "place_conditional_order")
    verification = ContractState(client).pending_orders(symbol)
    output_json(
        make_action_payload(
            "place_conditional_order",
            ok=True,
            symbol=symbol,
            risk=RISK_SCOPES["place_conditional_order"],
            preflight=preflight,
            request_body=body,
            result=response_data,
            verification=verification,
        ),
        args.pretty,
    )
    return 0


def cmd_cancel_conditional_order(args: argparse.Namespace, client: WeexContractClient) -> int:
    query = {"orderId": str(args.order_id)}
    result = execute_request(
        client,
        endpoint_key="transaction.cancel_pending_order",
        query=query,
        dry_run=args.dry_run,
        confirm_live=args.confirm_live,
        allow_mutating=True,
    )
    if result.get("dry_run"):
        output_json(
            make_action_payload(
                "cancel_conditional_order",
                ok=True,
                dry_run=True,
                risk=RISK_SCOPES["cancel_conditional_order"],
                request=result,
            ),
            args.pretty,
        )
        return 0

    response_data = require_success(result, "cancel_conditional_order")
    output_json(
        make_action_payload(
            "cancel_conditional_order",
            ok=True,
            risk=RISK_SCOPES["cancel_conditional_order"],
            query=query,
            result=response_data,
        ),
        args.pretty,
    )
    return 0


def build_tpsl_body(args: argparse.Namespace, state: ContractState, symbol: str) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    ensure_trade_enabled(state)
    position_side = normalize_enum(args.position_side, {"LONG", "SHORT"}, "position-side")
    plan_type = normalize_enum(args.plan_type, {"TAKE_PROFIT", "STOP_LOSS"}, "plan-type")
    trigger_price = normalize_positive_decimal(args.trigger_price, "trigger-price")
    execute_price = normalize_positive_decimal(args.execute_price, "execute-price", allow_zero=True) if args.execute_price is not None else None
    quantity = normalize_positive_decimal(args.quantity, "quantity")

    matching_positions = [
        item for item in non_flat_positions(state.positions(symbol))
        if str(item.get("side", "")).upper() == position_side
    ]
    if not matching_positions:
        raise CommandError(f"No open {position_side} position found for {symbol}.")

    body: Dict[str, Any] = {
        "symbol": symbol,
        "clientAlgoId": args.client_algo_id or generate_client_id("tpsl"),
        "planType": plan_type,
        "triggerPrice": trigger_price,
        "quantity": quantity,
        "positionSide": position_side,
        "triggerPriceType": normalize_enum(args.trigger_price_type or "CONTRACT_PRICE", {"CONTRACT_PRICE", "MARK_PRICE"}, "trigger-price-type"),
    }
    if execute_price is not None:
        body["executePrice"] = execute_price

    preflight = {
        "positions": matching_positions,
        "pending_orders": state.pending_orders(symbol),
        "ticker": state.ticker(symbol),
    }
    return body, preflight


def cmd_place_tpsl_order(args: argparse.Namespace, client: WeexContractClient) -> int:
    symbol = normalize_contract_symbol(args.symbol)
    state = ContractState(client)
    body, preflight = build_tpsl_body(args, state, symbol)
    result = execute_request(
        client,
        endpoint_key="transaction.place_tp_sl_order",
        body=body,
        dry_run=args.dry_run,
        confirm_live=args.confirm_live,
        allow_mutating=True,
    )
    if result.get("dry_run"):
        output_json(
            make_action_payload(
                "place_tpsl_order",
                ok=True,
                dry_run=True,
                symbol=symbol,
                risk=RISK_SCOPES["place_tpsl_order"],
                preflight=preflight,
                request=result,
            ),
            args.pretty,
        )
        return 0

    response_data = require_success(result, "place_tpsl_order")
    verification = ContractState(client).pending_orders(symbol)
    output_json(
        make_action_payload(
            "place_tpsl_order",
            ok=True,
            symbol=symbol,
            risk=RISK_SCOPES["place_tpsl_order"],
            request_body=body,
            result=response_data,
            verification=verification,
        ),
        args.pretty,
    )
    return 0


def cmd_modify_tpsl_order(args: argparse.Namespace, client: WeexContractClient) -> int:
    body: Dict[str, Any] = {
        "orderId": str(args.order_id),
        "triggerPrice": normalize_positive_decimal(args.trigger_price, "trigger-price"),
        "triggerPriceType": normalize_enum(args.trigger_price_type or "CONTRACT_PRICE", {"CONTRACT_PRICE", "MARK_PRICE"}, "trigger-price-type"),
    }
    if args.execute_price is not None:
        body["executePrice"] = normalize_positive_decimal(args.execute_price, "execute-price", allow_zero=True)

    result = execute_request(
        client,
        endpoint_key="transaction.modify_tp_sl_order",
        body=body,
        dry_run=args.dry_run,
        confirm_live=args.confirm_live,
        allow_mutating=True,
    )
    if result.get("dry_run"):
        output_json(
            make_action_payload(
                "modify_tpsl_order",
                ok=True,
                dry_run=True,
                risk=RISK_SCOPES["modify_tpsl_order"],
                request=result,
            ),
            args.pretty,
        )
        return 0

    response_data = require_success(result, "modify_tpsl_order")
    output_json(
        make_action_payload(
            "modify_tpsl_order",
            ok=True,
            risk=RISK_SCOPES["modify_tpsl_order"],
            request_body=body,
            result=response_data,
        ),
        args.pretty,
    )
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="WEEX Contract REST helper for agent-facing trading workflows")
    parser.add_argument("--base-url", default=os.getenv("WEEX_API_BASE", DEFAULT_BASE_URL))
    parser.add_argument("--locale", default=os.getenv("WEEX_LOCALE", DEFAULT_LOCALE))
    parser.add_argument("--timeout", type=float, default=float(os.getenv("WEEX_API_TIMEOUT", DEFAULT_TIMEOUT)))
    groups = sorted({endpoint.group for endpoint in ENDPOINTS.values() if endpoint.group})

    sub = parser.add_subparsers(dest="command", required=True)

    p_list = sub.add_parser("list-endpoints", help="List supported contract endpoints")
    p_list.add_argument("--group", choices=groups, default=None)
    p_list.add_argument("--read-only", action="store_true", help="Show only read-only endpoints")
    p_list.add_argument("--pretty", action="store_true")

    p_call = sub.add_parser("call", help="Call a read-only endpoint by key with JSON query/body")
    p_call.add_argument("--endpoint", required=True, choices=sorted(ENDPOINTS.keys()))
    p_call.add_argument("--query", default="{}", help="JSON object string")
    p_call.add_argument("--body", default="{}", help="JSON object string")
    p_call.add_argument("--dry-run", action="store_true")
    p_call.add_argument("--pretty", action="store_true")

    p_ticker = sub.add_parser("ticker", help="Get ticker, best bid/ask, and funding rate for a symbol")
    p_ticker.add_argument("--symbol", required=True)
    p_ticker.add_argument("--pretty", action="store_true")

    p_poll = sub.add_parser("poll-ticker", help="Continuously poll ticker")
    p_poll.add_argument("--symbol", required=True)
    p_poll.add_argument("--interval", type=float, default=2.0)
    p_poll.add_argument("--count", type=int, default=0, help="0 means infinite")
    p_poll.add_argument("--pretty", action="store_true")

    p_snapshot = sub.add_parser("account-snapshot", help="Fetch account, positions, orders, and optional symbol state")
    p_snapshot.add_argument("--symbol", default=None)
    p_snapshot.add_argument("--pretty", action="store_true")

    p_positions = sub.add_parser("positions", help="List contract positions")
    p_positions.add_argument("--symbol", default=None)
    p_positions.add_argument("--pretty", action="store_true")

    p_open_orders = sub.add_parser("open-orders", help="List current open orders")
    p_open_orders.add_argument("--symbol", default=None)
    p_open_orders.add_argument("--pretty", action="store_true")

    p_pending_orders = sub.add_parser("pending-orders", help="List current conditional orders")
    p_pending_orders.add_argument("--symbol", default=None)
    p_pending_orders.add_argument("--pretty", action="store_true")

    p_order_info = sub.add_parser("order-info", help="Get one order and optional fills")
    p_order_info.add_argument("--order-id", required=True)
    p_order_info.add_argument("--symbol", default=None)
    p_order_info.add_argument("--include-trades", action="store_true")
    p_order_info.add_argument("--pretty", action="store_true")

    p_place = sub.add_parser("place-order", help="Place a structured contract order")
    p_place.add_argument("--symbol", required=True)
    p_place.add_argument("--intent", choices=sorted(ORDER_INTENTS.keys()), default=None)
    p_place.add_argument("--side", choices=["BUY", "SELL", "buy", "sell"], default=None)
    p_place.add_argument("--position-side", choices=["LONG", "SHORT", "long", "short"], default=None)
    p_place.add_argument("--type", dest="order_type", required=True, choices=["LIMIT", "MARKET", "limit", "market"])
    p_place.add_argument("--quantity", required=True)
    p_place.add_argument("--price", default=None)
    p_place.add_argument("--time-in-force", default=None, choices=["GTC", "IOC", "FOK", "gtc", "ioc", "fok"])
    p_place.add_argument("--take-profit", default=None)
    p_place.add_argument("--stop-loss", default=None)
    p_place.add_argument("--tp-working-type", default=None, choices=["CONTRACT_PRICE", "MARK_PRICE", "contract_price", "mark_price"])
    p_place.add_argument("--sl-working-type", default=None, choices=["CONTRACT_PRICE", "MARK_PRICE", "contract_price", "mark_price"])
    p_place.add_argument("--new-client-order-id", default=None)
    p_place.add_argument("--allow-position-reduction", action="store_true")
    p_place.add_argument("--dry-run", action="store_true")
    p_place.add_argument("--confirm-live", action="store_true")
    p_place.add_argument("--pretty", action="store_true")

    p_cancel = sub.add_parser("cancel-order", help="Cancel one active order")
    p_cancel.add_argument("--order-id", default=None)
    p_cancel.add_argument("--client-oid", default=None)
    p_cancel.add_argument("--dry-run", action="store_true")
    p_cancel.add_argument("--confirm-live", action="store_true")
    p_cancel.add_argument("--pretty", action="store_true")

    p_cancel_open = sub.add_parser("cancel-open-orders", help="Cancel all open orders for one symbol or the whole account")
    p_cancel_open.add_argument("--symbol", default=None)
    p_cancel_open.add_argument("--all", action="store_true")
    p_cancel_open.add_argument("--dry-run", action="store_true")
    p_cancel_open.add_argument("--confirm-live", action="store_true")
    p_cancel_open.add_argument("--pretty", action="store_true")

    p_close = sub.add_parser("close-positions", help="Close open positions for one symbol or the whole account")
    p_close.add_argument("--symbol", default=None)
    p_close.add_argument("--all", action="store_true")
    p_close.add_argument("--dry-run", action="store_true")
    p_close.add_argument("--confirm-live", action="store_true")
    p_close.add_argument("--pretty", action="store_true")

    p_leverage = sub.add_parser("set-leverage", help="Update contract leverage settings")
    p_leverage.add_argument("--symbol", required=True)
    p_leverage.add_argument("--margin-type", default=None, choices=["CROSSED", "ISOLATED", "crossed", "isolated"])
    p_leverage.add_argument("--value", default=None, help="Cross leverage, or both isolated sides")
    p_leverage.add_argument("--cross", default=None)
    p_leverage.add_argument("--long", default=None)
    p_leverage.add_argument("--short", default=None)
    p_leverage.add_argument("--dry-run", action="store_true")
    p_leverage.add_argument("--confirm-live", action="store_true")
    p_leverage.add_argument("--pretty", action="store_true")

    p_margin = sub.add_parser("set-margin-mode", help="Switch symbol margin mode and optional position mode")
    p_margin.add_argument("--symbol", required=True)
    p_margin.add_argument("--margin-type", required=True, choices=["CROSSED", "ISOLATED", "crossed", "isolated"])
    p_margin.add_argument("--position-mode", default=None, choices=["COMBINED", "SEPARATED", "combined", "separated"])
    p_margin.add_argument("--allow-when-active", action="store_true")
    p_margin.add_argument("--dry-run", action="store_true")
    p_margin.add_argument("--confirm-live", action="store_true")
    p_margin.add_argument("--pretty", action="store_true")

    p_auto_append = sub.add_parser("set-auto-append-margin", help="Enable or disable isolated auto-append margin")
    p_auto_append.add_argument("--symbol", default=None)
    p_auto_append.add_argument("--position-side", default=None, choices=["LONG", "SHORT", "long", "short"])
    p_auto_append.add_argument("--position-id", default=None)
    p_auto_append.add_argument("--enabled", action="store_true")
    p_auto_append.add_argument("--dry-run", action="store_true")
    p_auto_append.add_argument("--confirm-live", action="store_true")
    p_auto_append.add_argument("--pretty", action="store_true")

    p_cond = sub.add_parser("place-conditional-order", help="Place a structured conditional order")
    p_cond.add_argument("--symbol", required=True)
    p_cond.add_argument("--intent", choices=sorted(ORDER_INTENTS.keys()), default=None)
    p_cond.add_argument("--side", choices=["BUY", "SELL", "buy", "sell"], default=None)
    p_cond.add_argument("--position-side", choices=["LONG", "SHORT", "long", "short"], default=None)
    p_cond.add_argument("--conditional-type", required=True, choices=["STOP", "TAKE_PROFIT", "STOP_MARKET", "TAKE_PROFIT_MARKET"])
    p_cond.add_argument("--quantity", required=True)
    p_cond.add_argument("--trigger-price", required=True)
    p_cond.add_argument("--execute-price", default=None)
    p_cond.add_argument("--preset-take-profit", default=None)
    p_cond.add_argument("--preset-stop-loss", default=None)
    p_cond.add_argument("--tp-working-type", default=None, choices=["CONTRACT_PRICE", "MARK_PRICE", "contract_price", "mark_price"])
    p_cond.add_argument("--sl-working-type", default=None, choices=["CONTRACT_PRICE", "MARK_PRICE", "contract_price", "mark_price"])
    p_cond.add_argument("--client-algo-id", default=None)
    p_cond.add_argument("--allow-position-reduction", action="store_true")
    p_cond.add_argument("--dry-run", action="store_true")
    p_cond.add_argument("--confirm-live", action="store_true")
    p_cond.add_argument("--pretty", action="store_true")

    p_cancel_cond = sub.add_parser("cancel-conditional-order", help="Cancel one conditional order")
    p_cancel_cond.add_argument("--order-id", required=True)
    p_cancel_cond.add_argument("--dry-run", action="store_true")
    p_cancel_cond.add_argument("--confirm-live", action="store_true")
    p_cancel_cond.add_argument("--pretty", action="store_true")

    p_place_tpsl = sub.add_parser("place-tpsl-order", help="Place a dedicated TP/SL plan order against an existing position")
    p_place_tpsl.add_argument("--symbol", required=True)
    p_place_tpsl.add_argument("--plan-type", required=True, choices=["TAKE_PROFIT", "STOP_LOSS"])
    p_place_tpsl.add_argument("--trigger-price", required=True)
    p_place_tpsl.add_argument("--execute-price", default=None)
    p_place_tpsl.add_argument("--quantity", required=True)
    p_place_tpsl.add_argument("--position-side", required=True, choices=["LONG", "SHORT", "long", "short"])
    p_place_tpsl.add_argument("--trigger-price-type", default=None, choices=["CONTRACT_PRICE", "MARK_PRICE", "contract_price", "mark_price"])
    p_place_tpsl.add_argument("--client-algo-id", default=None)
    p_place_tpsl.add_argument("--dry-run", action="store_true")
    p_place_tpsl.add_argument("--confirm-live", action="store_true")
    p_place_tpsl.add_argument("--pretty", action="store_true")

    p_modify_tpsl = sub.add_parser("modify-tpsl-order", help="Modify an existing TP/SL plan order")
    p_modify_tpsl.add_argument("--order-id", required=True)
    p_modify_tpsl.add_argument("--trigger-price", required=True)
    p_modify_tpsl.add_argument("--execute-price", default=None)
    p_modify_tpsl.add_argument("--trigger-price-type", default=None, choices=["CONTRACT_PRICE", "MARK_PRICE", "contract_price", "mark_price"])
    p_modify_tpsl.add_argument("--dry-run", action="store_true")
    p_modify_tpsl.add_argument("--confirm-live", action="store_true")
    p_modify_tpsl.add_argument("--pretty", action="store_true")

    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    client = WeexContractClient(
        base_url=args.base_url,
        timeout=args.timeout,
        locale=args.locale,
        api_key=os.getenv("WEEX_API_KEY"),
        api_secret=os.getenv("WEEX_API_SECRET"),
        api_passphrase=os.getenv("WEEX_API_PASSPHRASE"),
    )

    try:
        if args.command == "list-endpoints":
            return cmd_list_endpoints(args)
        if args.command == "call":
            return cmd_call(args, client)
        if args.command == "ticker":
            return cmd_ticker(args, client)
        if args.command == "poll-ticker":
            return cmd_poll_ticker(args, client)
        if args.command == "account-snapshot":
            return cmd_account_snapshot(args, client)
        if args.command == "positions":
            return cmd_positions(args, client)
        if args.command == "open-orders":
            return cmd_open_orders(args, client)
        if args.command == "pending-orders":
            return cmd_pending_orders(args, client)
        if args.command == "order-info":
            return cmd_order_info(args, client)
        if args.command == "place-order":
            return cmd_place_order(args, client)
        if args.command == "cancel-order":
            return cmd_cancel_order(args, client)
        if args.command == "cancel-open-orders":
            return cmd_cancel_open_orders(args, client)
        if args.command == "close-positions":
            return cmd_close_positions(args, client)
        if args.command == "set-leverage":
            return cmd_set_leverage(args, client)
        if args.command == "set-margin-mode":
            return cmd_set_margin_mode(args, client)
        if args.command == "set-auto-append-margin":
            return cmd_set_auto_append_margin(args, client)
        if args.command == "place-conditional-order":
            return cmd_place_conditional_order(args, client)
        if args.command == "cancel-conditional-order":
            return cmd_cancel_conditional_order(args, client)
        if args.command == "place-tpsl-order":
            return cmd_place_tpsl_order(args, client)
        if args.command == "modify-tpsl-order":
            return cmd_modify_tpsl_order(args, client)
    except CommandError as exc:
        output_json(make_action_payload(args.command, ok=False, error=str(exc)), getattr(args, "pretty", False))
        return 1

    parser.print_help()
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
