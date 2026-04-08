#!/usr/bin/env python3
"""Read-oriented operations for the WEEX contract CLI."""

from __future__ import annotations

import argparse
import time

from .core import (
    ENDPOINTS,
    RISK_SCOPES,
    CommandError,
    ContractState,
    WeexContractClient,
    execute_request,
    make_action_payload,
    normalize_contract_symbol,
    non_flat_positions,
    output_json,
    parse_json_arg,
    require_success,
)


def build_contract_bills_body(args: argparse.Namespace) -> dict:
    body: dict = {}
    if args.asset:
        body["asset"] = str(args.asset).strip().upper()
    if args.symbol:
        body["symbol"] = normalize_contract_symbol(args.symbol)
    if args.income_type:
        body["incomeType"] = str(args.income_type).strip()
    if args.start_time is not None:
        body["startTime"] = int(args.start_time)
    if args.end_time is not None:
        body["endTime"] = int(args.end_time)
    if args.limit is not None:
        if args.limit < 1 or args.limit > 100:
            raise CommandError("limit must be between 1 and 100.")
        body["limit"] = int(args.limit)

    start_time = body.get("startTime")
    end_time = body.get("endTime")
    if start_time is not None and end_time is not None:
        if start_time > end_time:
            raise CommandError("start-time must be <= end-time.")
        max_window_ms = 100 * 24 * 60 * 60 * 1000
        if end_time - start_time > max_window_ms:
            raise CommandError("The bill query time range cannot exceed 100 days.")
    return body


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


def cmd_contract_bills(args: argparse.Namespace, client: WeexContractClient) -> int:
    body = build_contract_bills_body(args)
    result = execute_request(
        client,
        endpoint_key="account.get_contract_bills",
        body=body,
        dry_run=args.dry_run,
        confirm_live=True,
        allow_mutating=True,
    )
    if result.get("dry_run"):
        output_json(
            make_action_payload(
                "contract_bills",
                ok=True,
                dry_run=True,
                risk=RISK_SCOPES["contract_bills"],
                request=result,
            ),
            args.pretty,
        )
        return 0

    response_data = require_success(result, "contract_bills")
    output_json(
        make_action_payload(
            "contract_bills",
            ok=True,
            risk=RISK_SCOPES["contract_bills"],
            filters=body,
            result=response_data,
        ),
        args.pretty,
    )
    return 0
