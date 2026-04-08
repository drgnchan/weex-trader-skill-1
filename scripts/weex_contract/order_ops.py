#!/usr/bin/env python3
"""Order and plan-order operations for the WEEX contract CLI."""

from __future__ import annotations

import argparse
from typing import Any, Dict, List, Optional, Tuple

from .core import (
    RISK_ADVANCED_SIDE_PAIRS,
    RISK_SCOPES,
    CommandError,
    ContractState,
    WeexContractClient,
    collect_symbol_rules,
    current_position_summary,
    ensure_trade_enabled,
    execute_request,
    find_endpoint_key_by_doc_suffix,
    find_matching_orders,
    generate_client_id,
    make_action_payload,
    maybe_no_action_if_already,
    normalize_bool_flag,
    normalize_contract_symbol,
    normalize_enum,
    normalize_positive_decimal,
    output_json,
    parse_identifier_list,
    parse_json_list_arg,
    require_success,
    resolve_order_side_position,
    validate_against_symbol_rules,
)


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


def get_order_spec_value(spec: Dict[str, Any], *keys: str) -> Any:
    for key in keys:
        if key in spec and spec[key] not in (None, ""):
            return spec[key]
    return None


def namespace_from_batch_order_spec(spec: Dict[str, Any], default_symbol: Optional[str]) -> argparse.Namespace:
    symbol = get_order_spec_value(spec, "symbol") or default_symbol
    if symbol in (None, ""):
        raise CommandError("Each batch order must include symbol, or pass --symbol as a default for the batch.")
    return argparse.Namespace(
        symbol=symbol,
        intent=get_order_spec_value(spec, "intent"),
        side=get_order_spec_value(spec, "side"),
        position_side=get_order_spec_value(spec, "position_side", "positionSide"),
        order_type=get_order_spec_value(spec, "order_type", "type"),
        quantity=get_order_spec_value(spec, "quantity"),
        price=get_order_spec_value(spec, "price"),
        time_in_force=get_order_spec_value(spec, "time_in_force", "timeInForce"),
        take_profit=get_order_spec_value(spec, "take_profit", "takeProfit", "tpTriggerPrice"),
        stop_loss=get_order_spec_value(spec, "stop_loss", "stopLoss", "slTriggerPrice"),
        tp_working_type=get_order_spec_value(spec, "tp_working_type", "tpWorkingType", "TpWorkingType"),
        sl_working_type=get_order_spec_value(spec, "sl_working_type", "slWorkingType", "SlWorkingType"),
        new_client_order_id=get_order_spec_value(spec, "new_client_order_id", "newClientOrderId"),
        allow_position_reduction=bool(
            get_order_spec_value(spec, "allow_position_reduction", "allowPositionReduction")
        ),
    )


def build_place_orders_batch_request(
    args: argparse.Namespace,
    state: ContractState,
) -> Tuple[Dict[str, Any], List[Dict[str, Any]], List[Dict[str, Any]]]:
    raw_orders = parse_json_list_arg(args.batch_orders, "--batch-orders")
    if not raw_orders:
        raise CommandError("--batch-orders must contain at least one order.")
    if len(raw_orders) > 10:
        raise CommandError("--batch-orders supports at most 10 orders.")

    batch_orders: List[Dict[str, Any]] = []
    preflight: List[Dict[str, Any]] = []
    warnings: List[Dict[str, Any]] = []
    default_symbol = normalize_contract_symbol(args.symbol) if args.symbol else None

    for index, item in enumerate(raw_orders, start=1):
        if not isinstance(item, dict):
            raise CommandError(f"--batch-orders item #{index} must be a JSON object.")
        order_args = namespace_from_batch_order_spec(item, default_symbol)
        symbol = normalize_contract_symbol(order_args.symbol)
        body, order_preflight, order_warnings = build_place_order_body(order_args, state, symbol)
        batch_orders.append(body)
        preflight.append(
            {
                "index": index,
                "symbol": symbol,
                "client_order_id": body.get("newClientOrderId"),
                "preflight": order_preflight,
            }
        )
        warnings.append(
            {
                "index": index,
                "symbol": symbol,
                "warnings": order_warnings,
            }
        )
    return {"batchOrders": batch_orders}, preflight, warnings


def cmd_place_orders_batch(args: argparse.Namespace, client: WeexContractClient) -> int:
    state = ContractState(client)
    body, preflight, warnings = build_place_orders_batch_request(args, state)
    result = execute_request(
        client,
        endpoint_key="transaction.place_orders_batch",
        body=body,
        dry_run=args.dry_run,
        confirm_live=args.confirm_live,
        allow_mutating=True,
    )

    if result.get("dry_run"):
        output_json(
            make_action_payload(
                "place_orders_batch",
                ok=True,
                dry_run=True,
                risk=RISK_SCOPES["place_orders_batch"],
                preflight=preflight,
                warnings=warnings,
                request=result,
            ),
            args.pretty,
        )
        return 0

    response_data = require_success(result, "place_orders_batch")
    verification_state = ContractState(client)
    verification: Dict[str, Any] = {}
    for symbol in sorted({item["symbol"] for item in preflight}):
        client_ids = [
            item["client_order_id"]
            for item in preflight
            if item["symbol"] == symbol and item.get("client_order_id")
        ]
        verification[symbol] = find_matching_orders(
            verification_state.open_orders(symbol),
            client_ids=client_ids,
        )

    output_json(
        make_action_payload(
            "place_orders_batch",
            ok=True,
            risk=RISK_SCOPES["place_orders_batch"],
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


def build_cancel_orders_batch_body(args: argparse.Namespace) -> Dict[str, Any]:
    order_ids = parse_identifier_list(args.order_ids, "--order-ids", numeric=True)
    client_ids = parse_identifier_list(args.client_oids, "--client-oids", numeric=False)
    if not order_ids and not client_ids:
        raise CommandError("Provide at least one of --order-ids or --client-oids.")
    if len(order_ids) > 10:
        raise CommandError("--order-ids supports at most 10 items.")
    if len(client_ids) > 10:
        raise CommandError("--client-oids supports at most 10 items.")

    body: Dict[str, Any] = {}
    if order_ids:
        body["orderIdList"] = order_ids
    if client_ids:
        body["origClientOrderIdList"] = client_ids
    return body


def cmd_cancel_orders_batch(args: argparse.Namespace, client: WeexContractClient) -> int:
    body = build_cancel_orders_batch_body(args)
    state = ContractState(client)
    before_orders = state.open_orders()
    target_before = find_matching_orders(
        before_orders,
        order_ids=body.get("orderIdList"),
        client_ids=body.get("origClientOrderIdList"),
    )
    result = execute_request(
        client,
        endpoint_key="transaction.cancel_orders_batch",
        body=body,
        dry_run=args.dry_run,
        confirm_live=args.confirm_live,
        allow_mutating=True,
    )
    if result.get("dry_run"):
        output_json(
            make_action_payload(
                "cancel_orders_batch",
                ok=True,
                dry_run=True,
                risk=RISK_SCOPES["cancel_orders_batch"],
                preflight={"matching_open_orders": target_before},
                request=result,
            ),
            args.pretty,
        )
        return 0

    response_data = require_success(result, "cancel_orders_batch")
    remaining = find_matching_orders(
        ContractState(client).open_orders(),
        order_ids=body.get("orderIdList"),
        client_ids=body.get("origClientOrderIdList"),
    )
    output_json(
        make_action_payload(
            "cancel_orders_batch",
            ok=True,
            risk=RISK_SCOPES["cancel_orders_batch"],
            request_body=body,
            preflight={"matching_open_orders": target_before},
            result=response_data,
            verification={"remaining_open_orders": remaining},
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

    query = {"symbol": symbol} if symbol else {}
    result = execute_request(
        client,
        endpoint_key="transaction.cancel_all_orders",
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


def cmd_cancel_pending_orders(args: argparse.Namespace, client: WeexContractClient) -> int:
    if not args.symbol and not args.all:
        raise CommandError("Provide --symbol for symbol-scoped cancellation, or --all for account-wide cancellation.")
    symbol = normalize_contract_symbol(args.symbol) if args.symbol else None
    state = ContractState(client)
    before_orders = state.pending_orders(symbol)
    if not before_orders:
        return maybe_no_action_if_already(
            action="cancel_pending_orders",
            pretty=args.pretty,
            summary="No pending conditional orders to cancel.",
            state_before={"symbol": symbol, "orders": before_orders},
        )

    query = {"symbol": symbol} if symbol else {}
    result = execute_request(
        client,
        endpoint_key="transaction.cancel_all_pending_orders",
        query=query,
        dry_run=args.dry_run,
        confirm_live=args.confirm_live,
        allow_mutating=True,
    )
    if result.get("dry_run"):
        output_json(
            make_action_payload(
                "cancel_pending_orders",
                ok=True,
                dry_run=True,
                risk=RISK_SCOPES["cancel_pending_orders"],
                preflight={"orders_before": before_orders},
                request=result,
            ),
            args.pretty,
        )
        return 0

    response_data = require_success(result, "cancel_pending_orders")
    after_orders = ContractState(client).pending_orders(symbol)
    output_json(
        make_action_payload(
            "cancel_pending_orders",
            ok=True,
            symbol=symbol,
            all=bool(args.all),
            risk=RISK_SCOPES["cancel_pending_orders"],
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
    before_positions = state.positions(symbol)
    before_positions = [item for item in before_positions if item.get("size") not in (None, "", "0", 0)]
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
    after_positions = ContractState(client).positions(symbol)
    after_positions = [item for item in after_positions if item.get("size") not in (None, "", "0", 0)]
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
        item for item in state.positions(symbol)
        if item.get("size") not in (None, "", "0", 0) and str(item.get("side", "")).upper() == position_side
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
