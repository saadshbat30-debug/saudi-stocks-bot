"""DATA_SOURCE=alpaca: العقود من Alpaca OPRA والأخبار من Alpaca (Benzinga)، مع الرجوع إلى ياهو عند الفشل.

python -m unittest tests.test_alpaca_options_news
"""
import os
import sys
import time
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import alpaca_news  # noqa: E402
import alpaca_options as ao  # noqa: E402
import alpaca_stream as al  # noqa: E402
import extras  # noqa: E402
import options  # noqa: E402

CFG = {"key": "PKTESTKEY1234567890AB", "secret": "s" * 40, "trading_url": al.PAPER_TRADING_URL}


class Resp:
    def __init__(self, d, code=200):
        self.status_code, self._d, self.text = code, d, str(d)

    def json(self):
        return self._d


class TestOptions(unittest.TestCase):
    def setUp(self):
        options._exp.clear()
        options._chain.clear()

    def http(self):
        contracts = {"option_contracts": [
            {"symbol": "TSLA261218C00400000", "expiration_date": "2026-12-18", "type": "call", "strike_price": "400", "open_interest": "1500", "close_price": "20"},
            {"symbol": "TSLA261218P00350000", "expiration_date": "2026-12-18", "type": "put", "strike_price": "350", "open_interest": "800", "close_price": "5"},
            {"symbol": "TSLA270115C00400000", "expiration_date": "2027-01-15", "type": "call", "strike_price": "400", "open_interest": "10", "close_price": "30"},
        ], "next_page_token": None}
        snaps = {"snapshots": {
            "TSLA261218C00400000": {"latestQuote": {"bp": 21.0, "ap": 21.5}, "latestTrade": {"p": 22.0, "t": "2026-11-18T15:00:00Z"},
                                    "dailyBar": {"v": 3000}, "prevDailyBar": {"c": 20.0}, "impliedVolatility": 0.55},
            "TSLA261218P00350000": {"latestQuote": {"bp": 4.0, "ap": 4.2}, "latestTrade": {"p": 4.1, "t": "2026-11-18T15:00:00Z"},
                                    "dailyBar": {"v": 100}, "prevDailyBar": {"c": 5.0}, "impliedVolatility": 0.6}},
            "next_page_token": None}

        def get(url, params=None, headers=None, timeout=None):
            if "/v2/options/contracts" in url:
                d = dict(contracts)
                if params.get("expiration_date"):
                    d = {"option_contracts": [c for c in contracts["option_contracts"] if c["expiration_date"] == params["expiration_date"]]}
                return Resp(d)
            self.assertEqual(params["feed"], "opra")
            return Resp(snaps)
        h = mock.Mock()
        h.get.side_effect = get
        return h

    def test_parse_occ(self):
        self.assertEqual(ao.parse_occ("TSLA261218C00400000"), ("TSLA", "2026-12-18", "call", 400.0))
        self.assertEqual(ao.parse_occ("SPY270115P00587500"), ("SPY", "2027-01-15", "put", 587.5))
        self.assertIsNone(ao.parse_occ("BAD"))

    def test_chain_page_from_alpaca(self):
        with mock.patch.object(ao, "_session", return_value=self.http()), mock.patch.object(al, "config", return_value=CFG), \
                mock.patch.object(al, "source_is_alpaca", return_value=True), mock.patch.object(options.yf, "Ticker") as yf:
            d = options.chain("TSLA", "2026-12-18", spot=380.0)
        yf.assert_not_called()
        self.assertEqual((d["source"], d["expirations"], d["date"]), ("Alpaca (OPRA)", ["2026-12-18", "2027-01-15"], "2026-12-18"))
        c, p = d["calls"][0], d["puts"][0]
        self.assertEqual((c["strike"], c["bid"], c["ask"], c["last"], c["volume"], c["oi"], c["iv"], c["itm"]),
                         (400.0, 21.0, 21.5, 22.0, 3000, 1500, 55.0, False))
        self.assertAlmostEqual(c["change_pct"], 10.0)
        self.assertTrue(c["unusual"])               # حجم اليوم 3000 > العقود المفتوحة 1500
        self.assertEqual((p["itm"], p["oi"]), (False, 800))
        self.assertIsNotNone(c.get("delta"))         # تحسبها المنصة من التذبذب الضمني
        self.assertEqual(d["summary"]["call_oi"], 1500)

    def test_alpaca_failure_falls_back_to_yahoo(self):
        bad = mock.Mock()
        bad.get.return_value = Resp({"message": "forbidden"}, 403)
        with mock.patch.object(ao, "_session", return_value=bad), mock.patch.object(al, "config", return_value=CFG), \
                mock.patch.object(al, "source_is_alpaca", return_value=True), mock.patch.object(options.yf, "Ticker") as yf:
            yf.return_value.options = ["2026-12-18"]
            options.expirations("TSLA")
        yf.assert_called()


class TestNews(unittest.TestCase):
    def setUp(self):
        extras._news.clear()

    def test_news_from_alpaca(self):
        h = mock.Mock()
        h.get.return_value = Resp({"news": [{"headline": "Tesla &amp; partner <b>deal</b>", "url": "https://x/1", "source": "benzinga",
                                             "created_at": "2026-11-18T15:00:00Z", "summary": "<p>Short</p>"}]})
        with mock.patch.object(alpaca_news, "_session", return_value=h), mock.patch.object(al, "config", return_value=CFG), \
                mock.patch.object(al, "source_is_alpaca", return_value=True), mock.patch.object(extras.yf, "Ticker") as yf:
            items = extras.get_news("TSLA")
        yf.assert_not_called()
        self.assertEqual(items, [{"title": "Tesla & partner deal", "url": "https://x/1", "publisher": "benzinga",
                                  "time": 1795014000, "summary": "Short"}])
        self.assertEqual(h.get.call_args.kwargs["params"]["symbols"], "TSLA")

    def test_failure_falls_back_to_yahoo(self):
        h = mock.Mock()
        h.get.return_value = Resp({}, 500)
        with mock.patch.object(alpaca_news, "_session", return_value=h), mock.patch.object(al, "config", return_value=CFG), \
                mock.patch.object(al, "source_is_alpaca", return_value=True), mock.patch.object(extras, "_rss_news", return_value=[]), \
                mock.patch.object(extras.yf, "Ticker") as yf:
            yf.return_value.get_news.return_value = []
            extras.get_news("TSLA")
        yf.assert_called()


if __name__ == "__main__":
    unittest.main()
