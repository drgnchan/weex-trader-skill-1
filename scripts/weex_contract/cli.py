#!/usr/bin/env python3
"""CLI parser and dispatch for the WEEX contract skill."""

from __future__ import annotations

import argparse
import os

from .account_ops import (
    cmd_adjust_position_margin,
    cmd_set_auto_append_margin,
    cmd_set_leverage,
    cmd_set_margin_mode,
)
from .core import (
    DEFAULT_BASE_URL,
    DEFAULT_LOCALE,
    DEFAULT_TIMEOUT,
    ENDPOINTS,
    ORDER_INTENTS,
    CommandError,
    WeexContractClient,
    make_action_payload,
    output_json,
)
from .order_ops import (
    cmd_cancel_conditional_order,
    cmd_cancel_open_orders,
    cmd_cancel_order,
    cmd_cancel_orders_batch,
    cmd_cancel_pending_orders,
    cmd_close_positions,
    cmd_modify_tpsl_order,
    cmd_place_conditional_order,
    cmd_place_order,
    cmd_place_orders_batch,
    cmd_place_tpsl_order,
)
from .read_ops import (
    cmd_account_snapshot,
    cmd_call,
    cmd_contract_bills,
    cmd_list_endpoints,
    cmd_open_orders,
    cmd_order_info,
    cmd_pending_orders,
    cmd_poll_ticker,
    cmd_positions,
    cmd_ticker,
)


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

    p_bills = sub.add_parser("contract-bills", help="Query contract income / bills with optional filters")
    p_bills.add_argument("--asset", default=None)
    p_bills.add_argument("--symbol", default=None)
    p_bills.add_argument("--income-type", default=None)
    p_bills.add_argument("--start-time", type=int, default=None)
    p_bills.add_argument("--end-time", type=int, default=None)
    p_bills.add_argument("--limit", type=int, default=None)
    p_bills.add_argument("--dry-run", action="store_true")
    p_bills.add_argument("--pretty", action="store_true")

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

    p_place_batch = sub.add_parser("place-orders-batch", help="Place up to 10 structured contract orders in one request")
    p_place_batch.add_argument("--symbol", default=None, help="Default symbol for batch items that omit symbol")
    p_place_batch.add_argument("--batch-orders", required=True, help="JSON array of order objects")
    p_place_batch.add_argument("--dry-run", action="store_true")
    p_place_batch.add_argument("--confirm-live", action="store_true")
    p_place_batch.add_argument("--pretty", action="store_true")

    p_cancel = sub.add_parser("cancel-order", help="Cancel one active order")
    p_cancel.add_argument("--order-id", default=None)
    p_cancel.add_argument("--client-oid", default=None)
    p_cancel.add_argument("--dry-run", action="store_true")
    p_cancel.add_argument("--confirm-live", action="store_true")
    p_cancel.add_argument("--pretty", action="store_true")

    p_cancel_batch = sub.add_parser("cancel-orders-batch", help="Cancel up to 10 active orders by order ID and/or client ID")
    p_cancel_batch.add_argument("--order-ids", default=None, help="Comma-separated list or JSON array of order IDs")
    p_cancel_batch.add_argument("--client-oids", default=None, help="Comma-separated list or JSON array of client order IDs")
    p_cancel_batch.add_argument("--dry-run", action="store_true")
    p_cancel_batch.add_argument("--confirm-live", action="store_true")
    p_cancel_batch.add_argument("--pretty", action="store_true")

    p_cancel_open = sub.add_parser("cancel-open-orders", help="Cancel all open orders for one symbol or the whole account")
    p_cancel_open.add_argument("--symbol", default=None)
    p_cancel_open.add_argument("--all", action="store_true")
    p_cancel_open.add_argument("--dry-run", action="store_true")
    p_cancel_open.add_argument("--confirm-live", action="store_true")
    p_cancel_open.add_argument("--pretty", action="store_true")

    p_cancel_pending = sub.add_parser("cancel-pending-orders", help="Cancel all conditional orders for one symbol or the whole account")
    p_cancel_pending.add_argument("--symbol", default=None)
    p_cancel_pending.add_argument("--all", action="store_true")
    p_cancel_pending.add_argument("--dry-run", action="store_true")
    p_cancel_pending.add_argument("--confirm-live", action="store_true")
    p_cancel_pending.add_argument("--pretty", action="store_true")

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
    p_leverage.add_argument("--position-mode", default=None, choices=["COMBINED", "SEPARATED", "combined", "separated"])
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

    p_adjust_margin = sub.add_parser("adjust-position-margin", help="Increase or decrease isolated margin for one position")
    p_adjust_margin.add_argument("--symbol", default=None)
    p_adjust_margin.add_argument("--position-side", default=None, choices=["LONG", "SHORT", "long", "short"])
    p_adjust_margin.add_argument("--position-id", default=None)
    p_adjust_margin.add_argument("--amount", required=True)
    p_adjust_margin.add_argument("--direction", required=True, choices=["INCREASE", "DECREASE", "increase", "decrease"])
    p_adjust_margin.add_argument("--dry-run", action="store_true")
    p_adjust_margin.add_argument("--confirm-live", action="store_true")
    p_adjust_margin.add_argument("--pretty", action="store_true")

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
        if args.command == "contract-bills":
            return cmd_contract_bills(args, client)
        if args.command == "place-order":
            return cmd_place_order(args, client)
        if args.command == "place-orders-batch":
            return cmd_place_orders_batch(args, client)
        if args.command == "cancel-order":
            return cmd_cancel_order(args, client)
        if args.command == "cancel-orders-batch":
            return cmd_cancel_orders_batch(args, client)
        if args.command == "cancel-open-orders":
            return cmd_cancel_open_orders(args, client)
        if args.command == "cancel-pending-orders":
            return cmd_cancel_pending_orders(args, client)
        if args.command == "close-positions":
            return cmd_close_positions(args, client)
        if args.command == "set-leverage":
            return cmd_set_leverage(args, client)
        if args.command == "set-margin-mode":
            return cmd_set_margin_mode(args, client)
        if args.command == "adjust-position-margin":
            return cmd_adjust_position_margin(args, client)
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
