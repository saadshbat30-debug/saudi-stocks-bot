"""DATA_SOURCE=alpaca: أسعار الجدول وأحجامه من بث Alpaca، والشارت من Alpaca REST، مع الرجوع إلى ياهو عند الفشل.

python -m unittest tests.test_alpaca_source
"""
import os
import sys
import time
import unittest
from datetime import datetime
from unittest import mock
from zoneinfo import ZoneInfo

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import alpaca_data  # noqa: E402
import alpaca_stream as al  # noqa: E402
import market  # noqa: E402

NY = ZoneInfo("America/New_York")
CFG = {"key": "PKTESTKEY1234567890AB", "secret": "s" * 40, "feed": "sip", "url": "x", "trading_url": al.PAPER_TRADING_URL,
       "trades": [], "quotes": [], "bars": [], "daily": ["*"], "watch_max": 60, "stale_seconds": 30, "bars_keep": 30, "format": "json"}


class FakeResp:
    def __init__(self, data, code=200):
        self.status_code, self._d, self.text = code, data, str(data)

    def json(self):
        return self._d


class TestConfig(unittest.TestCase):
    def test_platform_source_defaults(self):
        env = {"APCA_API_KEY_ID": "k" * 20, "APCA_API_SECRET_KEY": "s" * 40, "DATA_SOURCE": "alpaca"}
        with mock.patch.dict(os.environ, env, clear=True), mock.patch.object(al, "load_env"):
            c = al.config()
            self.assertTrue(al.enabled() and al.source_is_alpaca())
        # كل السوق: شموع اليوم فقط؛ الصفقات والأسعار وشموع الدقيقة للأسهم المتابَعة
        self.assertEqual((c["daily"], c["trades"], c["quotes"], c["bars"]), (["*"], [], [], []))

    def test_stream_only_mode_keeps_old_defaults(self):
        env = {"APCA_API_KEY_ID": "k" * 20, "APCA_API_SECRET_KEY": "s" * 40, "ALPACA_STREAM": "1"}
        with mock.patch.dict(os.environ, env, clear=True), mock.patch.object(al, "load_env"):
            c = al.config()
            self.assertFalse(al.source_is_alpaca())
        self.assertEqual((c["trades"], c["daily"]), (["*"], []))


class TestBars(unittest.TestCase):
    def bar(self, t, c=10.0):
        return {"t": t, "o": c, "h": c + 1, "l": c - 1, "c": c, "v": 100, "n": 3, "vw": c}

    def test_pages_sip_feed_and_session_filter(self):
        alpaca_data._blocked.update(until=0, why=None)
        pages = [FakeResp({"bars": [self.bar("2026-11-18T13:00:00Z"), self.bar("2026-11-18T14:30:00Z", 11)], "next_page_token": "p2"}),
                 FakeResp({"bars": [self.bar("2026-11-18T20:59:00Z", 12), self.bar("2026-11-18T21:00:00Z", 13)], "next_page_token": None})]
        http = mock.Mock()
        http.get.side_effect = pages
        with mock.patch.object(alpaca_data, "_http", return_value=http):
            regular = alpaca_data.bars("BRK-B", "1m", prepost=False, cfg=CFG)
        url, kw = http.get.call_args_list[0].args[0], http.get.call_args_list[0].kwargs
        self.assertIn("/v2/stocks/BRK.B/bars", url)
        self.assertEqual((kw["params"]["feed"], kw["params"]["timeframe"], kw["params"]["adjustment"]), ("sip", "1Min", "split"))
        self.assertEqual(kw["headers"]["APCA-API-KEY-ID"], CFG["key"])
        self.assertEqual(http.get.call_args_list[1].kwargs["params"]["page_token"], "p2")
        # 8:00 و16:00 نيويورك خارج الجلسة الرسمية؛ 9:30 و15:59 داخلها
        self.assertEqual([b["close"] for b in regular], [11, 12])
        self.assertEqual(regular[0]["time"], int(datetime(2026, 11, 18, 9, 30, tzinfo=NY).timestamp()) + market.RIYADH_OFFSET)

    def test_http_error_raises(self):
        alpaca_data._blocked.update(until=0, why=None)
        http = mock.Mock()
        http.get.return_value = FakeResp({"message": "forbidden"}, 403)
        with mock.patch.object(alpaca_data, "_http", return_value=http), self.assertRaises(RuntimeError):
            alpaca_data.bars("AAPL", "5m", cfg=CFG)
        alpaca_data._blocked.update(until=0, why=None)

    def test_rejected_key_pauses_requests(self):
        http = mock.Mock()
        http.get.return_value = FakeResp({"message": "unauthorized"}, 401)
        alpaca_data._blocked.update(until=0, why=None)
        with mock.patch.object(alpaca_data, "_http", return_value=http):
            for _ in range(3):
                with self.assertRaises(RuntimeError):
                    alpaca_data.bars("AAPL", "5m", cfg=CFG)
        self.assertEqual(http.get.call_count, 1)
        alpaca_data._blocked.update(until=0, why=None)

    def test_unsupported_symbols(self):
        self.assertFalse(alpaca_data.supported("2222.SR", "5m"))
        self.assertFalse(alpaca_data.supported("^GSPC", "1d"))
        self.assertTrue(alpaca_data.supported("AAPL", "1mo"))


class TestChartSource(unittest.TestCase):
    def setUp(self):
        market._charts.clear()
        market._raw_charts.clear()

    def chart(self, alpaca_bars=None, error=None):
        def fake_bars(symbol, interval, prepost=False):
            if error:
                raise RuntimeError(error)
            return alpaca_bars
        yahoo = mock.Mock(return_value=([{"time": 1, "open": 1, "high": 1, "low": 1, "close": 1, "volume": 7}], time.time()))
        with mock.patch.object(market, "_tv_us", return_value=[]), mock.patch.object(al, "source_is_alpaca", return_value=True), \
                mock.patch.object(alpaca_data, "bars", side_effect=fake_bars), mock.patch.object(market, "_yahoo_raw", yahoo), \
                mock.patch.object(market, "_true_day_volumes", return_value={}):
            return market.get_chart("AAPL", "5m", prepost=True), yahoo

    def test_chart_comes_from_alpaca(self):
        bars, yahoo = self.chart([{"time": 5, "open": 2, "high": 2, "low": 2, "close": 2, "volume": 9}])
        self.assertEqual(bars[0]["volume"], 9)
        self.assertEqual(yahoo.call_count, 0)
        self.assertEqual(market.chart_meta("AAPL", "5m", True)["src"], "Alpaca (SIP)")

    def test_alpaca_failure_falls_back_to_yahoo_and_says_why(self):
        bars, yahoo = self.chart(error="Alpaca HTTP 403: forbidden")
        self.assertEqual(bars[0]["volume"], 7)
        meta = market.chart_meta("AAPL", "5m", True)
        self.assertEqual(meta["src"], "ياهو (yfinance)")
        self.assertIn("403", meta["alpaca_error"])


class TestTableValues(unittest.TestCase):
    def row(self, **kw):
        r = {"symbol": "AAPL", "price": 100.0, "change": 0, "change_pct": 0, "prev_close": 95.0, "volume": 1000,
             "day_high": 101.0, "day_low": 99.0}
        r.update(kw)
        return r

    def apply(self, r, live, yahoo_time):
        c = mock.Mock()
        c.live.return_value = live
        with mock.patch.object(al, "source_is_alpaca", return_value=True), mock.patch.object(al, "client", return_value=c):
            market._apply_alpaca(r, time.time(), yahoo_time)
        return r

    def test_price_volume_and_change_from_alpaca(self):
        now = time.time()
        today = datetime.fromtimestamp(now, NY).replace(hour=0, minute=0, second=0, microsecond=0).timestamp()
        r = self.apply(self.row(), {"price": 104.5, "at": now - 2, "kind": "trade", "day_date": today, "volume": 55_000,
                                    "day_high": 105.0, "day_low": 96.0}, now - 30)
        self.assertEqual((r["price"], r["volume"], r["day_high"], r["day_low"], r["src"], r["live"]), (104.5, 55_000, 105.0, 96.0, "alpaca", True))
        self.assertAlmostEqual(r["change_pct"], (104.5 / 95 - 1) * 100)

    def test_yesterdays_daily_bar_volume_is_not_used(self):
        now = time.time()
        r = self.apply(self.row(), {"price": 101.0, "at": now - 10, "kind": "trade", "day_date": now - 3 * 86400, "volume": 9,
                                    "day_high": 1, "day_low": 1}, now - 30)
        self.assertEqual((r["price"], r["volume"]), (101.0, 1000))

    def test_much_newer_yahoo_price_is_kept(self):
        now = time.time()
        r = self.apply(self.row(), {"price": 50.0, "at": now - 600, "kind": "daily"}, now - 5)  # قبل الافتتاح مثلًا
        self.assertEqual((r["price"], r.get("src")), (100.0, None))

    def test_no_alpaca_data_leaves_row(self):
        r = self.apply(self.row(), None, time.time())
        self.assertEqual(r, self.row())


if __name__ == "__main__":
    unittest.main()
