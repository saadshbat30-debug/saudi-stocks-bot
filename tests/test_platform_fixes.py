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


class TestCumulativeVolumeGlitch(unittest.TestCase):
    """ياهو يرسل أحيانًا شمعة دقيقة حجمها = حجم اليوم التراكمي (قِيس على TSLA وNVDA في 2026-11-10/11)."""
    from datetime import date, datetime
    from zoneinfo import ZoneInfo
    DAY = date(2026, 11, 11)
    NY = ZoneInfo("America/New_York")

    def bars(self, vols, start=(9, 31)):
        t0 = int(self.datetime(2026, 11, 11, *start, tzinfo=self.NY).timestamp()) + market.RIYADH_OFFSET
        return [{"time": t0 + 60 * i, "open": 10, "high": 10.1, "low": 9.9, "close": 10, "volume": v}
                for i, v in enumerate(vols)]

    def test_glitch_bars_are_unknown_not_counted(self):
        vols = [1000] * 20
        bars = self.bars(vols[:10] + [10_400] + vols[10:] + [21_500])  # حجمان تراكميان ≈ مجموع ما قبلهما
        n = market.drop_cumulative_volume(bars, {self.DAY: 20_000})
        self.assertEqual(n, 2)
        self.assertEqual(sum(b["volume"] for b in bars), 20_000)
        self.assertTrue(bars[10]["vol_unknown"] and bars[-1]["vol_unknown"])

    def test_real_spike_on_clean_day_is_kept(self):
        bars = self.bars([1000] * 20 + [30_000] + [1000] * 5)  # قفزة حجم حقيقية يؤكدها حجم اليوم
        n = market.drop_cumulative_volume(bars, {self.DAY: 55_000})
        self.assertEqual(n, 0)
        self.assertEqual(bars[20]["volume"], 30_000)

    def test_no_reference_volume_means_untouched(self):
        bars = self.bars([1000] * 10 + [10_400])
        self.assertEqual(market.drop_cumulative_volume(bars, {}), 0)
        self.assertEqual(bars[-1]["volume"], 10_400)

    def test_active_minutes_right_after_open_are_kept(self):
        # شمعة افتتاح كبيرة ثم دقائق نشطة (ربع حجمها) ثم يوم هادئ؛ المرجع متأخر قليلًا فاليوم يبدو أكبر منه
        bars = self.bars([1_400_000, 400_000, 350_000] + [40_000] * 30 + [3_500_000], start=(9, 30))
        n = market.drop_cumulative_volume(bars, {self.DAY: 2_900_000})
        self.assertEqual(n, 1)
        self.assertEqual([b["volume"] for b in bars[:3]], [1_400_000, 400_000, 350_000])
        self.assertTrue(bars[-1]["vol_unknown"])
