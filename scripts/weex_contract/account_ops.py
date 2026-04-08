#!/usr/bin/env python3
"""Account and position-management operations for the WEEX contract CLI."""

from __future__ import annotations

import argparse
from typing import Any, Dict, Optional, Tuple

from .core import (
    RISK_SCOPES,
    CommandError,
    ContractState,
    WeexContractClient,
    current_position_summary,
    ensure_trade_enabled,
    execute_request,
    extract_position_identifier,
    extract_position_side,
    make_action_payload,
    maybe_no_action_if_already,
    non_flat_positions,
    normalize_contract_symbol,
    normalize_enum,
    normalize_positive_decimal,
    output_json,
    require_success,
)


def infer_target_margin_type_for_leverage(args: argparse.Namespace, current_margin_type: str) -> str:
    explicit_margin_type = normalize_enum(args.margin_type, {"CROSSED", "ISOLATED"}, "margin-type") if getattr(args, "margin_type", None) else None
    if explicit_margin_type is not None:
        return explicit_margin_type
    if args.long or args.short:
        return "ISOLATED"
    return current_margin_type


def infer_target_position_mode_for_leverage(args: argparse.Namespace, current_config: Dict[str, Any], request_body: Dict[str, Any]) -> Optional[str]:
    explicit_position_mode = normalize_enum(args.position_mode, {"COMBINED", "SEPARATED"}, "position-mode") if getattr(args, "position_mode", None) else None
    if explicit_position_mode is not None:
        return explicit_position_mode
    if args.long or args.short:
        return "SEPARATED"
    current_position_mode = current_config.get("separatedType")
    if current_position_mode in (None, ""):
        return None
    return normalize_enum(current_position_mode, {"COMBINED", "SEPARATED"}, "current position-mode")


def build_leverage_request(args: argparse.Namespace, state: ContractState, symbol: str) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    ensure_trade_enabled(state)
    current_config = state.symbol_config(symbol)
    current_margin_type = normalize_enum(current_config.get("marginType"), {"CROSSED", "ISOLATED"}, "current marginType")
    target_margin_type = infer_target_margin_type_for_leverage(args, current_margin_type)
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
        "open_orders": state.open_orders(symbol),
        "pending_orders": state.pending_orders(symbol),
        "target_position_mode": infer_target_position_mode_for_leverage(args, current_config, request_body),
    }
    return request_body, verification_target


def leverage_already_matches(current_config: Dict[str, Any], request_body: Dict[str, Any]) -> bool:
    if current_config.get("marginType") != request_body.get("marginType"):
        return False
    for key in ["crossLeverage", "isolatedLongLeverage", "isolatedShortLeverage"]:
        if key in request_body and str(current_config.get(key)) != str(request_body.get(key)):
            return False
    return True


def build_leverage_transition_plan(
    args: argparse.Namespace,
    preflight: Dict[str, Any],
    request_body: Dict[str, Any],
) -> Dict[str, Any]:
    current_config = preflight["current_config"]
    target_margin_type = request_body["marginType"]
    target_position_mode = preflight.get("target_position_mode")
    current_position_mode = current_config.get("separatedType")
    needs_margin_mode_change = (
        str(current_config.get("marginType")) != str(target_margin_type)
        or (
            target_position_mode is not None
            and str(current_position_mode) != str(target_position_mode)
        )
    )
    positions = non_flat_positions(preflight["positions"]["positions"])
    open_orders = preflight.get("open_orders") or []
    pending_orders = preflight.get("pending_orders") or []

    steps = []
    if needs_margin_mode_change and open_orders:
        steps.append("cancel_open_orders")
    if needs_margin_mode_change and pending_orders:
        steps.append("cancel_pending_orders")
    if needs_margin_mode_change and positions:
        steps.append("close_positions")
    if needs_margin_mode_change:
        steps.append("set_margin_mode")
    steps.append("set_leverage")

    return {
        "current_margin_type": current_config.get("marginType"),
        "current_position_mode": current_position_mode,
        "target_margin_type": target_margin_type,
        "target_position_mode": target_position_mode,
        "requires_mode_change": needs_margin_mode_change,
        "requires_clearing_active_state": needs_margin_mode_change and bool(positions or open_orders or pending_orders),
        "positions_before": positions,
        "open_orders_before": open_orders,
        "pending_orders_before": pending_orders,
        "steps": steps,
    }


def execute_margin_mode_change(
    client: WeexContractClient,
    *,
    symbol: str,
    target_margin_type: str,
    target_position_mode: Optional[str],
) -> Dict[str, Any]:
    body = {
        "symbol": symbol,
        "marginType": target_margin_type,
    }
    if target_position_mode is not None:
        body["separatedType"] = target_position_mode
    result = execute_request(
        client,
        endpoint_key="account.change_margin_mode_trade",
        body=body,
        dry_run=False,
        confirm_live=True,
        allow_mutating=True,
    )
    response_data = require_success(result, "set_margin_mode")
    verification = ContractState(client).symbol_config(symbol)
    return {
        "step": "set_margin_mode",
        "request_body": body,
        "result": response_data,
        "verification": verification,
    }


def execute_symbol_open_order_cancel(client: WeexContractClient, *, symbol: str) -> Dict[str, Any]:
    result = execute_request(
        client,
        endpoint_key="transaction.cancel_all_orders",
        query={"symbol": symbol},
        dry_run=False,
        confirm_live=True,
        allow_mutating=True,
    )
    response_data = require_success(result, "cancel_open_orders")
    verification = ContractState(client).open_orders(symbol)
    return {
        "step": "cancel_open_orders",
        "result": response_data,
        "verification": verification,
    }


def execute_symbol_pending_order_cancel(client: WeexContractClient, *, symbol: str) -> Dict[str, Any]:
    result = execute_request(
        client,
        endpoint_key="transaction.cancel_all_pending_orders",
        query={"symbol": symbol},
        dry_run=False,
        confirm_live=True,
        allow_mutating=True,
    )
    response_data = require_success(result, "cancel_pending_orders")
    verification = ContractState(client).pending_orders(symbol)
    return {
        "step": "cancel_pending_orders",
        "result": response_data,
        "verification": verification,
    }


def execute_symbol_close_positions(client: WeexContractClient, *, symbol: str) -> Dict[str, Any]:
    result = execute_request(
        client,
        endpoint_key="transaction.close_positions",
        body={"symbol": symbol},
        dry_run=False,
        confirm_live=True,
        allow_mutating=True,
    )
    response_data = require_success(result, "close_positions")
    verification = non_flat_positions(ContractState(client).positions(symbol))
    return {
        "step": "close_positions",
        "result": response_data,
        "verification": verification,
    }


def execute_leverage_update(client: WeexContractClient, *, symbol: str, request_body: Dict[str, Any]) -> Dict[str, Any]:
    result = execute_request(
        client,
        endpoint_key="account.update_leverage_trade",
        body=request_body,
        dry_run=False,
        confirm_live=True,
        allow_mutating=True,
    )
    response_data = require_success(result, "set_leverage")
    verification = ContractState(client).symbol_config(symbol)
    return {
        "step": "set_leverage",
        "request_body": request_body,
        "result": response_data,
        "verification": verification,
    }


def cmd_set_leverage(args: argparse.Namespace, client: WeexContractClient) -> int:
    symbol = normalize_contract_symbol(args.symbol)
    state = ContractState(client)
    body, preflight = build_leverage_request(args, state, symbol)
    transition_plan = build_leverage_transition_plan(args, preflight, body)
    if leverage_already_matches(preflight["current_config"], body) and not transition_plan["requires_mode_change"]:
        return maybe_no_action_if_already(
            action="set_leverage",
            pretty=args.pretty,
            summary="Leverage already matches the requested settings.",
            state_before={**preflight, "transition_plan": transition_plan},
        )

    if args.dry_run:
        output_json(
            make_action_payload(
                "set_leverage",
                ok=True,
                dry_run=True,
                risk=RISK_SCOPES["set_leverage"],
                preflight=preflight,
                transition_plan=transition_plan,
                request={
                    "endpoint": "account.update_leverage_trade",
                    "body": body,
                },
            ),
            args.pretty,
        )
        return 0

    if not args.confirm_live:
        raise CommandError("Refusing live mutating leverage workflow. Use --confirm-live to execute, or --dry-run to preview.")

    execution_steps = []
    if transition_plan["requires_mode_change"]:
        if transition_plan["open_orders_before"]:
            execution_steps.append(execute_symbol_open_order_cancel(client, symbol=symbol))
        if transition_plan["pending_orders_before"]:
            execution_steps.append(execute_symbol_pending_order_cancel(client, symbol=symbol))
        if transition_plan["positions_before"]:
            execution_steps.append(execute_symbol_close_positions(client, symbol=symbol))
        execution_steps.append(
            execute_margin_mode_change(
                client,
                symbol=symbol,
                target_margin_type=transition_plan["target_margin_type"],
                target_position_mode=transition_plan["target_position_mode"],
            )
        )

    leverage_step = execute_leverage_update(client, symbol=symbol, request_body=body)
    execution_steps.append(leverage_step)
    verification = leverage_step["verification"]

    output_json(
        make_action_payload(
            "set_leverage",
            ok=True,
            symbol=symbol,
            risk=RISK_SCOPES["set_leverage"],
            preflight=preflight,
            request_body=body,
            transition_plan=transition_plan,
            auto_prepared_symbol=transition_plan["requires_mode_change"],
            execution_steps=execution_steps,
            result=leverage_step["result"],
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
    }
    if target_position_mode is not None:
        body["separatedType"] = target_position_mode
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
    positions = non_flat_positions(state.positions(symbol))
    if explicit_position_id:
        explicit_id = str(explicit_position_id)
        matching_positions = [
            item for item in positions
            if extract_position_identifier(item) == explicit_id
        ]
        if position_side:
            target_side = normalize_enum(position_side, {"LONG", "SHORT"}, "position-side")
            matching_positions = [
                item for item in matching_positions
                if extract_position_side(item) == target_side
            ]
        if matching_positions and str(matching_positions[0].get("marginType", "")).upper() != "ISOLATED":
            raise CommandError(f"Position {explicit_id} is not isolated.")
        return explicit_id, matching_positions[0] if matching_positions else {}

    if not symbol:
        raise CommandError("Provide --position-id or --symbol.")
    isolated_positions = [item for item in positions if str(item.get("marginType", "")).upper() == "ISOLATED"]
    if not isolated_positions:
        raise CommandError(f"No open isolated positions found for {symbol}.")
    if position_side:
        target_side = normalize_enum(position_side, {"LONG", "SHORT"}, "position-side")
        isolated_positions = [item for item in isolated_positions if extract_position_side(item) == target_side]
        if not isolated_positions:
            raise CommandError(f"No isolated {target_side} position found for {symbol}.")
    if len(isolated_positions) != 1:
        raise CommandError("Multiple isolated positions match. Provide --position-side or --position-id to disambiguate.")
    position_id = extract_position_identifier(isolated_positions[0])
    if position_id is None:
        raise CommandError("Unable to determine isolated position ID from the current position snapshot.")
    return position_id, isolated_positions[0]


def build_adjust_position_margin_request(
    args: argparse.Namespace,
    state: ContractState,
) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    symbol = normalize_contract_symbol(args.symbol) if args.symbol else None
    position_id, position = resolve_position_id_for_symbol(
        state=state,
        symbol=symbol,
        position_side=args.position_side,
        explicit_position_id=args.position_id,
    )
    body = {
        "isolatedPositionId": position_id,
        "amount": normalize_positive_decimal(args.amount, "amount"),
        "type": 1 if normalize_enum(args.direction, {"INCREASE", "DECREASE"}, "direction") == "INCREASE" else 2,
    }
    preflight = {
        "symbol": symbol,
        "position": position,
        "positions": non_flat_positions(state.positions(symbol)),
    }
    return body, preflight


def cmd_adjust_position_margin(args: argparse.Namespace, client: WeexContractClient) -> int:
    state = ContractState(client)
    body, preflight = build_adjust_position_margin_request(args, state)
    result = execute_request(
        client,
        endpoint_key="account.adjust_position_margin_trade",
        body=body,
        dry_run=args.dry_run,
        confirm_live=args.confirm_live,
        allow_mutating=True,
    )
    if result.get("dry_run"):
        output_json(
            make_action_payload(
                "adjust_position_margin",
                ok=True,
                dry_run=True,
                risk=RISK_SCOPES["adjust_position_margin"],
                preflight=preflight,
                request=result,
            ),
            args.pretty,
        )
        return 0

    response_data = require_success(result, "adjust_position_margin")
    verification_positions = non_flat_positions(ContractState(client).positions(preflight.get("symbol")))
    verification_position = next(
        (
            item for item in verification_positions
            if extract_position_identifier(item) == str(body["isolatedPositionId"])
        ),
        None,
    )
    output_json(
        make_action_payload(
            "adjust_position_margin",
            ok=True,
            risk=RISK_SCOPES["adjust_position_margin"],
            request_body=body,
            preflight=preflight,
            result=response_data,
            verification={
                "position": verification_position,
                "positions": verification_positions,
            },
        ),
        args.pretty,
    )
    return 0


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
