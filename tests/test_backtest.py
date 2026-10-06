import os
import unittest
from unittest import mock

import numpy as np
import pandas as pd

import app
import backtest
import market_data
from strategy import Params
from tests.test_app import FakeClient

P = Params(ma_type="sma")


def series(values):
    return pd.Series(values, index=pd.date_range("2025-01-01", periods=len(values)), dtype=float)


def breakout_then(after):
    flat = list(100 + 0.2 * np.sin(np.arange(40)))
    return series(flat + [103] + after)


class BacktestTest(unittest.TestCase):
    def test_take_profit(self):
        result = backtest.run(breakout_then([105, 110]), P, backtest.Rules(take_profit_pct=6))
        t = result["trades"][0]
        self.assertEqual((t["entry_price"], t["exit_price"], t["reason"]), (103, 110, "هدف الربح"))
        self.assertEqual((result["count"], result["wins"]), (1, 1))

    def test_stop_loss(self):
        t = backtest.run(breakout_then([101, 99]), P, backtest.Rules(stop_loss_pct=3))["trades"][0]
        self.assertEqual((t["exit_price"], t["reason"]), (99, "وقف الخسارة"))
        self.assertLess(t["return_pct"], 0)

    def test_max_hold(self):
        t = backtest.run(breakout_then([103.5] * 5), P, backtest.Rules(max_hold_days=3))["trades"][0]
        self.assertEqual(t["reason"], "انتهاء المدة")

    def test_open_trade_not_counted(self):
        result = backtest.run(breakout_then([104]), P)
        self.assertEqual(result["trades"][-1]["reason"], "مفتوحة")
        self.assertEqual(result["count"], 0)

    def test_page(self):
        market_data._cache.clear()
        with mock.patch.object(market_data, "client", lambda: FakeClient()), \
             mock.patch.dict(os.environ, {"SAHMK_API_KEY": "x"}):
            html = app.app.test_client().get("/backtest?symbols=2222&tp=5").get_data(as_text=True)
        self.assertIn("الاختبار التاريخي", html)
        self.assertIn("2222", html)
        self.assertIn('value="5.0"', html)


if __name__ == "__main__":
    unittest.main()
