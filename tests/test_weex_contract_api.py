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
    def __init__(self, *, current_config=None, positions=None, ticker=None, contract_info=None):
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


if __name__ == "__main__":
    unittest.main()
