import os
import unittest
from datetime import date, timedelta
from unittest import mock

import numpy as np
from sahmk import SahmkError
from sahmk.models import HistoricalResponse, MarketMoversResponse, MarketSummary, Quote

import app
import market_data


def history(symbol):
    n = 120
    closes = 50 + 0.1 * np.sin(np.arange(n))
    closes[-1] = 52  # breakout out of a tight range on the latest bar
    start = date.today() - timedelta(days=n - 1)
    return HistoricalResponse.from_dict({
        "symbol": symbol,
        "data": [{"date": (start + timedelta(days=i)).isoformat(), "close": float(c)}
                 for i, c in enumerate(closes)],
    })


class FakeClient:
    def __init__(self, plan_has_history=True):
        self.plan_has_history = plan_has_history
        self.calls = 0

    def market_summary(self):
        self.calls += 1
        return MarketSummary.from_dict({"index": "TASI", "index_value": 11500.5, "index_change_percent": 0.42,
                                        "advancing": 140, "declining": 90, "market_mood": "bullish", "is_delayed": True})

    def gainers(self, limit):
        return MarketMoversResponse.from_dict({"gainers": [{"symbol": "4001", "name": "أسواق العثيم", "change_percent": 6.1}]}, "gainers")

    def losers(self, limit):
        return MarketMoversResponse.from_dict({"losers": [{"symbol": "2380", "name": "بترو رابغ", "change_percent": -3.2}]}, "losers")

    def quote(self, symbol):
        self.calls += 1
        return Quote.from_dict({"symbol": symbol, "name": "أرامكو السعودية", "price": 52.0, "change_percent": 1.25})

    def historical(self, symbol, **kwargs):
        self.calls += 1
        if not self.plan_has_history:
            raise SahmkError("PLAN_LIMIT", status_code=403)
        return history(symbol)


class AppTest(unittest.TestCase):
    def render(self, fake, url="/?symbols=2222"):
        market_data._cache.clear()
        with mock.patch.object(market_data, "client", lambda: fake), \
             mock.patch.dict(os.environ, {"SAHMK_API_KEY": "shmk_test_x"}):
            return app.app.test_client().get(url).get_data(as_text=True)

    def test_page_shows_quotes_and_breakout(self):
        html = self.render(FakeClient())
        self.assertIn("أرامكو السعودية", html)
        self.assertIn("11,500.50", html)
        self.assertIn("اختراق صاعد", html)
        self.assertIn("أسواق العثيم", html)

    def test_free_plan_shows_notice_and_caches_plan_error(self):
        fake = FakeClient(plan_has_history=False)
        html = self.render(fake)
        self.assertIn("باقة Starter", html)
        calls = fake.calls
        with mock.patch.object(market_data, "client", lambda: fake), \
             mock.patch.dict(os.environ, {"SAHMK_API_KEY": "shmk_test_x"}):
            app.app.test_client().get("/?symbols=2222")
        self.assertEqual(fake.calls, calls)  # everything served from cache

    def test_parse_symbols(self):
        self.assertEqual(app.parse_symbols("2222.sr, 1120,2222,"), ["2222", "1120"])


if __name__ == "__main__":
    unittest.main()
