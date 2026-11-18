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
