"""اختبارات تصحيح أحجام الشموع (دون اتصال). الأرقام مأخوذة من حالات حقيقية في docs/liquidity_audit.

python -m unittest tests.test_volfix
"""
import os
import sys
import unittest
from datetime import date, datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import volfix  # noqa: E402

DAY = date(2026, 11, 10)


def bars(start, vols, step=60, day=DAY, price=10.0):
    """شموع متتالية من وقت نيويورك start=(ساعة، دقيقة) بأحجام vols، بأوقات مزاحة للرياض كما في المنصة."""
    t0 = int(datetime(day.year, day.month, day.day, *start, tzinfo=volfix.NY).timestamp()) + volfix.RIYADH_OFFSET
    return [{"time": t0 + step * i, "open": price, "high": price + 0.1, "low": price - 0.1, "close": price, "volume": v}
            for i, v in enumerate(vols)]


class TestPostCloseTotal(unittest.TestCase):
    """F 2026-11-10: الجلسة الرسمية 24,550,083 سهمًا، اليومي 33,125,600، وشمعة 16:03 حجمها 29,169,424."""

    def day(self, post):
        reg = bars((9, 30), [62_949] * 390)  # ≈ 24.55M
        return reg + bars((16, 0), post)

    def test_bar_carrying_the_whole_day_is_unknown(self):
        b = self.day([1_200_000, 300, 0, 29_169_424, 5_000])
        r = volfix.fix_post_close_total(b, {DAY: 33_125_600})
        self.assertEqual(r, {"fixed": 1, "unverifiable": 0})
        hit = b[390 + 3]
        self.assertTrue(hit["vol_unknown"])
        self.assertEqual((hit["volume"], hit["vol_why"]), (0, "post_total"))

    def test_real_extended_volume_is_kept(self):
        # مزاد الإغلاق (16:00) وتداول ما بعد الإغلاق الحقيقي يبقيان كما هما
        b = self.day([1_200_000, 300, 0, 29_169_424, 5_000])
        volfix.fix_post_close_total(b, {DAY: 33_125_600})
        self.assertEqual([x["volume"] for x in b[390:]], [1_200_000, 300, 0, 0, 5_000])
        self.assertEqual(sum(x["volume"] for x in b[:390]), 62_949 * 390)

    def test_heavy_after_hours_day_is_kept(self):
        # إعلان أرباح: 30% من حجم اليوم بعد الإغلاق موزعة على دقائق كثيرة — لا شمعة واحدة تبلغ نصف اليوم
        b = bars((9, 30), [62_949] * 390) + bars((16, 0), [2_000_000] + [150_000] * 60)
        self.assertEqual(volfix.fix_post_close_total(b, {DAY: 33_125_600})["fixed"], 0)

    def test_huge_closing_cross_that_fits_the_day_is_kept(self):
        # يوم إعادة موازنة: مزاد إغلاق = 55% من حجم اليوم، والجلسة الرسمية 45% — المجموع لا يتجاوز اليوم
        b = bars((9, 30), [10_000] * 390) + bars((16, 0), [4_800_000])
        self.assertEqual(volfix.fix_post_close_total(b, {DAY: 8_700_000})["fixed"], 0)
        self.assertEqual(b[-1]["volume"], 4_800_000)

    def test_without_daily_reference_nothing_changes(self):
        b = self.day([29_169_424])
        r = volfix.fix_post_close_total(b, {})
        self.assertEqual(r, {"fixed": 0, "unverifiable": 1})
        self.assertEqual(b[-1]["volume"], 29_169_424)


if __name__ == "__main__":
    unittest.main()
