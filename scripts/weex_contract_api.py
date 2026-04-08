#!/usr/bin/env python3
"""Compatibility entrypoint for the WEEX contract CLI."""

from __future__ import annotations

import sys
from pathlib import Path


SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from weex_contract.account_ops import (  # noqa: E402,F401
    build_adjust_position_margin_request,
    build_leverage_request,
    build_leverage_transition_plan,
    cmd_adjust_position_margin,
    cmd_set_auto_append_margin,
    cmd_set_leverage,
    cmd_set_margin_mode,
    infer_target_margin_type_for_leverage,
    infer_target_position_mode_for_leverage,
    leverage_already_matches,
    margin_mode_already_matches,
    resolve_position_id_for_symbol,
)
from weex_contract.cli import build_parser, main  # noqa: E402,F401
from weex_contract.core import *  # noqa: E402,F401,F403
from weex_contract.order_ops import (  # noqa: E402,F401
    build_cancel_orders_batch_body,
    build_conditional_order_body,
    build_place_order_body,
    build_place_orders_batch_request,
    build_tpsl_body,
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
    get_order_spec_value,
    namespace_from_batch_order_spec,
)
from weex_contract.read_ops import (  # noqa: E402,F401
    build_contract_bills_body,
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


if __name__ == "__main__":
    raise SystemExit(main())
