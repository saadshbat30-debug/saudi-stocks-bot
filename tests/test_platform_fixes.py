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


if __name__ == "__main__":
    unittest.main()
