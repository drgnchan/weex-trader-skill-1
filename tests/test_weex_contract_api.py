import importlib.util
import sys
import unittest
from argparse import Namespace
from pathlib import Path


MODULE_PATH = Path(__file__).resolve().parents[1] / "scripts" / "weex_contract_api.py"
SPEC = importlib.util.spec_from_file_location("weex_contract_api", MODULE_PATH)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


class FakeState:
    def __init__(self, *, current_config=None, positions=None, ticker=None, contract_info=None, open_orders=None):
        self._current_config = current_config or {
            "symbol": "ETHUSDT",
            "marginType": "ISOLATED",
            "separatedType": "SEPARATED",
            "crossLeverage": "20",
            "isolatedLongLeverage": "10",
            "isolatedShortLeverage": "5",
        }
        self._positions = positions or []
        self._ticker = ticker or {"symbol": "ETHUSDT", "price": "1800"}
        self._contract_info = contract_info or {
            "symbol": "ETHUSDT",
            "tickSize": "0.1",
            "stepSize": "0.001",
            "minQty": "0.001",
        }
        self._open_orders = open_orders or []

    def account_config(self):
        return {"canTrade": True}

    def symbol_config(self, symbol):
        return dict(self._current_config)

    def positions(self, symbol=None):
        return list(self._positions)

    def ticker(self, symbol):
        return dict(self._ticker)

    def contract_info(self, symbol):
        return dict(self._contract_info)

    def pending_orders(self, symbol=None):
        return []

    def open_orders(self, symbol=None):
        return list(self._open_orders)


class AnalyzePayloadTests(unittest.TestCase):
    def test_wrapper_code_failure_is_not_treated_as_success(self):
        result = MODULE.analyze_business_payload({"code": "30001", "msg": "bad request", "data": {"value": 1}})
        self.assertFalse(result["ok"])
        self.assertEqual(result["analysis"]["signal"], "wrapper_code")

    def test_success_false_is_failure(self):
        result = MODULE.analyze_business_payload({"success": False, "errorCode": "1001", "errorMessage": "rejected"})
        self.assertFalse(result["ok"])
        self.assertIn("1001", result["analysis"]["errors"][0])

    def test_list_partial_failure_is_detected(self):
        result = MODULE.analyze_business_payload(
            [
                {"success": True, "orderId": "1"},
                {"success": False, "errorCode": "2002", "errorMessage": "position missing"},
            ]
        )
        self.assertFalse(result["ok"])
        self.assertEqual(result["analysis"]["signal"], "list_success")


class LeverageBuilderTests(unittest.TestCase):
    def test_isolated_value_sets_both_sides(self):
        state = FakeState()
        body, preflight = MODULE.build_leverage_request(
            Namespace(symbol="ETHUSDT", margin_type="ISOLATED", value="15", cross=None, long=None, short=None),
            state,
            "ETHUSDT",
        )
        self.assertEqual(body["isolatedLongLeverage"], "15")
        self.assertEqual(body["isolatedShortLeverage"], "15")
        self.assertEqual(preflight["current_config"]["marginType"], "ISOLATED")

    def test_isolated_side_specific_preserves_partial_request(self):
        state = FakeState()
        body, _ = MODULE.build_leverage_request(
            Namespace(symbol="ETHUSDT", margin_type="ISOLATED", value=None, cross=None, long="12", short=None),
            state,
            "ETHUSDT",
        )
        self.assertEqual(body["isolatedLongLeverage"], "12")
        self.assertNotIn("isolatedShortLeverage", body)

    def test_cross_requires_single_value(self):
        state = FakeState(current_config={"symbol": "ETHUSDT", "marginType": "CROSSED", "crossLeverage": "10"})
        body, _ = MODULE.build_leverage_request(
            Namespace(symbol="ETHUSDT", margin_type="CROSSED", value="20", cross=None, long=None, short=None),
            state,
            "ETHUSDT",
        )
        self.assertEqual(body["crossLeverage"], "20")

    def test_side_specific_leverage_implies_isolated_mode(self):
        state = FakeState(
            current_config={
                "symbol": "ETHUSDT",
                "marginType": "CROSSED",
                "separatedType": "COMBINED",
                "crossLeverage": "10",
            }
        )
        body, preflight = MODULE.build_leverage_request(
            Namespace(
                symbol="ETHUSDT",
                margin_type=None,
                position_mode=None,
                value=None,
                cross=None,
                long="20",
                short="10",
            ),
            state,
            "ETHUSDT",
        )
        self.assertEqual(body["marginType"], "ISOLATED")
        self.assertEqual(body["isolatedLongLeverage"], "20")
        self.assertEqual(body["isolatedShortLeverage"], "10")
        self.assertEqual(preflight["target_position_mode"], "SEPARATED")

    def test_transition_plan_auto_clears_cross_state_before_side_specific_leverage(self):
        state = FakeState(
            current_config={
                "symbol": "ETHUSDT",
                "marginType": "CROSSED",
                "separatedType": "COMBINED",
                "crossLeverage": "10",
            },
            positions=[
                {"symbol": "ETHUSDT", "side": "LONG", "marginType": "CROSSED", "size": "0.01"},
            ],
            open_orders=[
                {"symbol": "ETHUSDT", "orderId": "1001"},
            ],
        )
        body, preflight = MODULE.build_leverage_request(
            Namespace(
                symbol="ETHUSDT",
                margin_type=None,
                position_mode=None,
                value=None,
                cross=None,
                long="20",
                short="10",
            ),
            state,
            "ETHUSDT",
        )
        plan = MODULE.build_leverage_transition_plan(
            Namespace(
                symbol="ETHUSDT",
                margin_type=None,
                position_mode=None,
                value=None,
                cross=None,
                long="20",
                short="10",
            ),
            preflight,
            body,
        )
        self.assertTrue(plan["requires_mode_change"])
        self.assertTrue(plan["requires_clearing_active_state"])
        self.assertEqual(
            plan["steps"],
            ["cancel_open_orders", "close_positions", "set_margin_mode", "set_leverage"],
        )


class PlaceOrderBuilderTests(unittest.TestCase):
    def test_limit_order_defaults_tif_to_gtc(self):
        state = FakeState()
        body, _, warnings = MODULE.build_place_order_body(
            Namespace(
                symbol="ETHUSDT",
                intent="OPEN_LONG",
                side=None,
                position_side=None,
                order_type="LIMIT",
                quantity="0.001",
                price="1000",
                time_in_force=None,
                take_profit=None,
                stop_loss=None,
                tp_working_type=None,
                sl_working_type=None,
                new_client_order_id=None,
                allow_position_reduction=False,
            ),
            state,
            "ETHUSDT",
        )
        self.assertEqual(body["timeInForce"], "GTC")
        self.assertEqual(warnings, [])

    def test_risky_side_pair_is_blocked_without_explicit_flag(self):
        state = FakeState()
        with self.assertRaises(MODULE.CommandError):
            MODULE.build_place_order_body(
                Namespace(
                    symbol="ETHUSDT",
                    intent=None,
                    side="SELL",
                    position_side="LONG",
                    order_type="MARKET",
                    quantity="0.001",
                    price=None,
                    time_in_force=None,
                    take_profit=None,
                    stop_loss=None,
                    tp_working_type=None,
                    sl_working_type=None,
                    new_client_order_id=None,
                    allow_position_reduction=False,
                ),
                state,
                "ETHUSDT",
            )


class ContractBillsBuilderTests(unittest.TestCase):
    def test_symbol_and_limit_are_normalized(self):
        body = MODULE.build_contract_bills_body(
            Namespace(
                asset="usdt",
                symbol="eth/usdt",
                income_type="position_funding",
                start_time=None,
                end_time=None,
                limit=50,
            )
        )
        self.assertEqual(body["asset"], "USDT")
        self.assertEqual(body["symbol"], "ETHUSDT")
        self.assertEqual(body["limit"], 50)

    def test_time_range_above_100_days_is_rejected(self):
        with self.assertRaises(MODULE.CommandError):
            MODULE.build_contract_bills_body(
                Namespace(
                    asset=None,
                    symbol=None,
                    income_type=None,
                    start_time=0,
                    end_time=101 * 24 * 60 * 60 * 1000,
                    limit=None,
                )
            )


class PositionMarginBuilderTests(unittest.TestCase):
    def test_adjust_position_margin_uses_isolated_position_field_aliases(self):
        state = FakeState(
            positions=[
                {"positionId": "12345", "side": "LONG", "marginType": "ISOLATED", "size": "0.01", "symbol": "ETHUSDT"},
            ]
        )
        body, preflight = MODULE.build_adjust_position_margin_request(
            Namespace(
                symbol="ETHUSDT",
                position_side="LONG",
                position_id=None,
                amount="12.5",
                direction="increase",
            ),
            state,
        )
        self.assertEqual(body["isolatedPositionId"], "12345")
        self.assertEqual(body["type"], 1)
        self.assertEqual(preflight["symbol"], "ETHUSDT")

    def test_adjust_position_margin_requires_disambiguation(self):
        state = FakeState(
            positions=[
                {"id": "1", "side": "LONG", "marginType": "ISOLATED", "size": "0.01", "symbol": "ETHUSDT"},
                {"id": "2", "side": "SHORT", "marginType": "ISOLATED", "size": "0.02", "symbol": "ETHUSDT"},
            ]
        )
        with self.assertRaises(MODULE.CommandError):
            MODULE.build_adjust_position_margin_request(
                Namespace(
                    symbol="ETHUSDT",
                    position_side=None,
                    position_id=None,
                    amount="5",
                    direction="DECREASE",
                ),
                state,
            )


class CancelOrdersBatchBuilderTests(unittest.TestCase):
    def test_cancel_orders_batch_accepts_csv_and_json_array(self):
        body = MODULE.build_cancel_orders_batch_body(
            Namespace(order_ids="1001,1002", client_oids='["cli-1","cli-2"]')
        )
        self.assertEqual(body["orderIdList"], ["1001", "1002"])
        self.assertEqual(body["origClientOrderIdList"], ["cli-1", "cli-2"])

    def test_cancel_orders_batch_requires_some_identifier(self):
        with self.assertRaises(MODULE.CommandError):
            MODULE.build_cancel_orders_batch_body(Namespace(order_ids=None, client_oids=None))


class BatchPlaceOrderBuilderTests(unittest.TestCase):
    def test_batch_place_applies_default_symbol(self):
        state = FakeState()
        body, preflight, warnings = MODULE.build_place_orders_batch_request(
            Namespace(
                symbol="ETHUSDT",
                batch_orders='[{"intent":"OPEN_LONG","type":"MARKET","quantity":"0.01"},{"intent":"OPEN_SHORT","type":"LIMIT","quantity":"0.02","price":"1700"}]',
            ),
            state,
        )
        self.assertEqual(len(body["batchOrders"]), 2)
        self.assertEqual(body["batchOrders"][0]["symbol"], "ETHUSDT")
        self.assertEqual(body["batchOrders"][1]["type"], "LIMIT")
        self.assertEqual(preflight[0]["symbol"], "ETHUSDT")
        self.assertEqual(warnings[0]["warnings"], [])

    def test_batch_place_requires_symbol_per_order_or_default(self):
        state = FakeState()
        with self.assertRaises(MODULE.CommandError):
            MODULE.build_place_orders_batch_request(
                Namespace(
                    symbol=None,
                    batch_orders='[{"intent":"OPEN_LONG","type":"MARKET","quantity":"0.01"}]',
                ),
                state,
            )


if __name__ == "__main__":
    unittest.main()
