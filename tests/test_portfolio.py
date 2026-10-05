import os
import tempfile
import unittest
from datetime import date
from unittest import mock

from sahmk.models import BatchQuotesResponse, DividendsResponse

import app
import market_data
import portfolio


class PortfolioMathTest(unittest.TestCase):
    def test_build_suggested_equal_split(self):
        prices = {s: 10.0 for s in portfolio.SUGGESTED}
        h = portfolio.build_suggested(24000, prices)
        self.assertEqual(len(h), 24)
        self.assertTrue(all(x["shares"] == 100 for x in h))

    def test_evaluate_pnl_weights_and_trades(self):
        holdings = [{"symbol": "A", "shares": 100, "cost": 10}, {"symbol": "B", "shares": 100, "cost": 10}]
        r = portfolio.evaluate(holdings, {"A": 20.0, "B": 10.0}, {"A": 1.0}, today=date(2026, 10, 5))
        self.assertEqual(r["total_value"], 3000)
        self.assertEqual(r["pnl"], 1000)
        a, b = r["rows"]
        self.assertAlmostEqual(a["weight"], 2 / 3)
        self.assertEqual(a["trade"], -25)  # sell 25 A at 20 to reach 1500
        self.assertEqual(b["trade"], 50)   # buy 50 B at 10
        self.assertEqual(r["dividends"], 100)
        self.assertTrue(r["needs_rebalance"])
        self.assertEqual(r["next_rebalance"], date(2027, 4, 1))

    def test_small_gaps_not_traded(self):
        holdings = [{"symbol": "A", "shares": 101, "cost": 10}, {"symbol": "B", "shares": 100, "cost": 10}]
        r = portfolio.evaluate(holdings, {"A": 10.0, "B": 10.0}, {})
        self.assertEqual([x["trade"] for x in r["rows"]], [0, 0])
        self.assertFalse(r["needs_rebalance"])

    def test_parse_form_drops_empty_and_zero(self):
        h = portfolio.parse_form(["1120", "", "2222.sr", "7010"], ["10", "5", "3", "0"], ["60", "1", "", "40"])
        self.assertEqual(h, [{"symbol": "1120", "shares": 10, "cost": 60.0}, {"symbol": "2222", "shares": 3, "cost": 0.0}])


class FakeClient:
    def quotes(self, symbols):
        return BatchQuotesResponse.from_dict({"quotes": [{"symbol": s, "name": "شركة " + s, "price": 10.0} for s in symbols]})

    def dividends(self, symbol):
        return DividendsResponse.from_dict({"symbol": symbol, "trailing_12m_dividends": 0.5})


class PortfolioPageTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(suffix=".json", delete=False)
        self.tmp.close()
        os.unlink(self.tmp.name)
        self.patches = [
            mock.patch.object(portfolio, "PORTFOLIO_FILE", self.tmp.name),
            mock.patch.object(market_data, "client", lambda: FakeClient()),
            mock.patch.dict(os.environ, {"SAHMK_API_KEY": "x"}),
        ]
        for p in self.patches:
            p.start()
        market_data._cache.clear()
        self.c = app.app.test_client()

    def tearDown(self):
        for p in self.patches:
            p.stop()
        if os.path.exists(self.tmp.name):
            os.unlink(self.tmp.name)

    def test_empty_then_suggested_then_edit(self):
        self.assertIn("أنشئ المحفظة المقترحة", self.c.get("/portfolio").get_data(as_text=True))
        self.c.post("/portfolio/suggested", data={"amount": "24000"})
        self.assertEqual(len(portfolio.load()), 24)
        html = self.c.get("/portfolio?saved=1").get_data(as_text=True)
        self.assertIn("تم حفظ المحفظة", html)
        self.assertIn("24,000", html)
        self.assertIn("1,200", html)  # 2400 shares x 0.5 dividend
        self.c.post("/portfolio/save", data={"symbol": ["1120"], "shares": ["50"], "cost": ["8"]})
        self.assertEqual(portfolio.load(), [{"symbol": "1120", "shares": 50, "cost": 8.0}])


if __name__ == "__main__":
    unittest.main()
