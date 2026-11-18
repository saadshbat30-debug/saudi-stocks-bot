import time
import unittest

import liquidity_tape


class LiquidityClassificationTests(unittest.TestCase):
    def test_trade_at_ask_is_nbbo_buy(self):
        self.assertEqual(liquidity_tape.classify_trade(10.01, 10.00, 10.01, 0.2, 10.00), ("buy", "nbbo"))

    def test_trade_at_bid_is_nbbo_sell(self):
        self.assertEqual(liquidity_tape.classify_trade(10.00, 10.00, 10.01, 0.2, 10.01), ("sell", "nbbo"))

    def test_inside_spread_uses_tick_estimate(self):
        self.assertEqual(liquidity_tape.classify_trade(10.005, 10.00, 10.01, 0.2, 10.00), ("buy", "tick"))

    def test_stale_quote_uses_tick_estimate(self):
        self.assertEqual(liquidity_tape.classify_trade(9.99, 10.00, 10.01, 3.0, 10.00), ("sell", "tick"))

    def test_unchanged_price_is_unknown_not_persistent(self):
        self.assertEqual(liquidity_tape.classify_trade(10.00, None, None, None, 10.00), ("unknown", "unknown"))


if __name__ == "__main__":
    unittest.main()


class TestLiquidityRoute(unittest.TestCase):
    """المسار يسجل الرمز في التجميع: صفقة تصل بعد أول طلب تظهر في الطلب التالي."""

    def test_trades_after_first_request_are_counted(self):
        from unittest import mock
        import alpaca_stream
        import app as platform

        class Client:
            state = "streaming"
            on_data = None

            def watch(self, symbols, trades_only=False):
                pass

        c = Client()
        liquidity_tape._active.clear()
        liquidity_tape._states.clear()
        web = platform.app.test_client()
        with mock.patch.object(alpaca_stream, "client", return_value=c), \
                mock.patch.object(alpaca_stream, "status", return_value={}):
            self.assertEqual(web.get("/api/liquidity/ZZZT").status_code, 200)
            now = time.time()
            c.on_data({"T": "q", "S": "ZZZT", "bp": 9.99, "ap": 10.01, "t": now - 0.2})
            c.on_data({"T": "t", "S": "ZZZT", "p": 10.01, "s": 500, "t": now})
            w = web.get("/api/liquidity/ZZZT").get_json()["windows"]["60"]
        self.assertEqual(w["trade_count"], 1)
        self.assertAlmostEqual(w["buy_value"], 5005.0)
