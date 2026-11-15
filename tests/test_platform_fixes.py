"""اختبارات إصلاحات المراجعة (دون اتصال): العقود عند رد فارغ من ياهو، وعدم تكرار طلب رمز بلا بيانات.

python -m unittest tests.test_platform_fixes
"""
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import market  # noqa: E402
import options  # noqa: E402


class TestOptionsEmptyChain(unittest.TestCase):
    def test_none_tables_return_error_and_are_not_cached(self):
        class Empty:  # ما ترجعه yfinance عند فشل الطلب دون استثناء
            calls = puts = None
            underlying = {}
        options._chain.clear()
        with mock.patch.object(options, "expirations", return_value=["2030-01-18"]), \
                mock.patch.object(options.yf, "Ticker") as tk:
            tk.return_value.option_chain.return_value = Empty()
            out = options.chain("AAPL")
            self.assertIn("error", out)
            self.assertEqual(options._chain, {})  # الرد الفارغ لم يُحفظ
            options.chain("AAPL")
            self.assertEqual(tk.return_value.option_chain.call_count, 2)  # الطلب التالي يحاول من جديد


class TestMissingSymbolNotRefetched(unittest.TestCase):
    def test_refresh_skips_recent_misses(self):
        cache = {}
        market._missing.clear()
        with mock.patch.object(market, "_download", return_value={}) as dl:
            market._refresh(cache, ["ZZZZZZ"], "5d", 20, market._snapshot)
            market._refresh(cache, ["ZZZZZZ"], "5d", 20, market._snapshot)
            market._refresh(cache, ["ZZZZZZ"], "5d", 20, market._snapshot)
            self.assertEqual(dl.call_count, 1)  # طلب واحد ثم انتظار MISSING_SECONDS
        with mock.patch.object(market.time, "time", return_value=market.time.time() + market.MISSING_SECONDS + 1), \
                mock.patch.object(market, "_download", return_value={}) as dl:
            market._refresh(cache, ["ZZZZZZ"], "5d", 20, market._snapshot)
            self.assertEqual(dl.call_count, 1)  # بعد المهلة يُعاد الطلب



class TestChartVolumeVerification(unittest.TestCase):
    """get_chart يطابق أحجام الأسهم الأمريكية بفواصل ياهو الأخرى، ولا يلمس غيرها ولا الشموع اليومية."""

    def setUp(self):
        market._charts.clear()
        market._raw_charts.clear()
        self.calls = []

    def fake_raw(self, symbol, interval, prepost, keep=0):
        self.calls.append(interval)
        t0 = 1794407400 + market.RIYADH_OFFSET  # 2026-11-11 09:30 نيويورك
        step = {"1m": 60, "5m": 300, "1h": 3600, "1d": 86400}[interval]
        n = {"1m": 60, "5m": 12, "1h": 1, "1d": 1}[interval]
        vol = {"1m": 1000, "5m": 5000, "1h": 60000, "1d": 400000}[interval]
        bars = [{"time": t0 + step * i, "open": 10, "high": 10.1, "low": 9.9, "close": 10, "volume": vol} for i in range(n)]
        if interval == "1m":
            bars[47]["volume"] = 48000  # حجم اليوم التراكمي بدل حجم الدقيقة
        return bars, 1794420000

    def chart(self, symbol, interval):
        with mock.patch.object(market, "_tv_us", return_value=[]), mock.patch.object(market, "_yahoo_raw", self.fake_raw):
            return market.get_chart(symbol, interval, prepost=True)

    def test_us_minute_bars_are_checked_against_5m_and_hour(self):
        bars = self.chart("TSLA", "1m")
        self.assertEqual((bars[47]["volume"], bars[47]["vol_derived"]), (1000, "5m"))
        self.assertEqual(set(self.calls), {"1m", "5m", "1h", "1d"})
        check = market.chart_meta("TSLA", "1m", True)["volume_check"]
        self.assertEqual((check["derived"], check["unknown"], check["missing_sources"]), (1, 0, []))

    def test_non_us_symbol_is_left_as_yahoo_sent_it(self):
        bars = self.chart("2222.SR", "1m")
        self.assertEqual(bars[47]["volume"], 48000)
        self.assertEqual(self.calls, ["1m"])  # بلا طلبات إضافية
        self.assertNotIn("volume_check", market.chart_meta("2222.SR", "1m", True))

    def test_daily_bars_need_no_extra_requests(self):
        self.chart("TSLA", "1d")
        self.assertEqual(self.calls, ["1d"])


if __name__ == "__main__":
    unittest.main()
