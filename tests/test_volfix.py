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



def minute_day(vols_by_window):
    """يوم من نوافذ 5 دقائق تبدأ 9:30: يرجع (شموع الدقيقة، شموع 5 دقائق المطابقة لها تمامًا)."""
    ones = bars((9, 30), [v for w in vols_by_window for v in w])
    fives = bars((9, 30), [sum(w) for w in vols_by_window], step=300)
    return ones, fives


class TestReconcileChildren(unittest.TestCase):
    """شمعة دقيقة تحمل حجم اليوم التراكمي (TSLA 2026-11-11 12:53: 16.29M بين دقائق ~20K)."""

    def setUp(self):
        self.windows = [[1000] * 5 for _ in range(20)]  # 100 دقيقة × 1000 سهم
        self.ones, self.fives = minute_day(self.windows)

    def glitch(self, i, extra=0):
        """الدقيقة i تأخذ مجموع ما قبلها في اليوم (+extra) بدل حجمها."""
        self.ones[i]["volume"] = sum(b["volume"] for b in self.ones[:i]) + 1000 + extra

    def test_cumulative_minute_gets_its_volume_back_from_the_5m_bar(self):
        self.glitch(47)
        r = volfix.reconcile_children(self.ones, self.fives, label="5m")
        self.assertEqual((r["derived"], r["unknown"], r["conflict"]), (1, 0, 0))
        self.assertEqual((self.ones[47]["volume"], self.ones[47]["vol_derived"]), (1000, "5m"))
        self.assertEqual(sum(b["volume"] for b in self.ones), 100_000)  # لا حجم حقيقي مفقود

    def test_two_glitches_in_one_window_are_unknown_not_guessed(self):
        self.glitch(46)
        self.glitch(47)
        self.ones[47]["volume"] = 47_000  # مجموع اليوم الحقيقي قبلها، لا المتضخم
        r = volfix.reconcile_children(self.ones, self.fives)
        self.assertEqual(r["unknown"], 2)
        self.assertTrue(self.ones[46]["vol_unknown"] and self.ones[47]["vol_unknown"])

    def test_real_spike_confirmed_by_the_5m_bar_is_kept(self):
        self.windows[9][2] = 60_000  # قفزة حجم حقيقية: شمعة 5 دقائق تحملها أيضًا
        ones, fives = minute_day(self.windows)
        r = volfix.reconcile_children(ones, fives)
        self.assertEqual((r["derived"], r["unknown"]), (0, 0))
        self.assertEqual(ones[47]["volume"], 60_000)

    def test_parent_missing_volume_is_a_conflict_not_a_fix(self):
        # شمعة 5 دقائق ناقصة (مصدر متأخر): الدقيقة الكبيرة ليست بحجم تراكمي فلا تُمس
        self.windows[9][2] = 4000
        ones, fives = minute_day(self.windows)
        fives[9]["volume"] = 5000  # الحقيقي 8000
        r = volfix.reconcile_children(ones, fives)
        self.assertEqual((r["derived"], r["unknown"], r["conflict"]), (0, 0, 1))
        self.assertEqual(ones[47]["volume"], 4000)

    def test_missing_parent_bar_leaves_children_alone(self):
        self.glitch(47)
        del self.fives[9]
        r = volfix.reconcile_children(self.ones, self.fives)
        self.assertEqual((r["derived"], r["unknown"]), (0, 0))  # لا مصدر يثبت: يبقى كما هو

    def test_shifted_parents_make_the_day_inconsistent_and_untouched(self):
        import random
        rnd = random.Random(1)
        windows = [[rnd.randint(200, 3000) for _ in range(5)] for _ in range(20)]
        ones, fives = minute_day(windows)
        ones[47]["volume"] = sum(b["volume"] for b in ones[:48])
        for f in fives:
            f["time"] += 60  # طابع زمني مزاح دقيقة
        before = [b["volume"] for b in ones]
        r = volfix.reconcile_children(ones, fives)
        self.assertEqual(r["inconsistent_days"], 1)
        self.assertEqual([b["volume"] for b in ones], before)

    def test_unsettled_window_is_not_judged(self):
        # الشمعة الجارية: شمعة 5 دقائق لم يكتمل حجمها بعد والدقائق سبقتها
        self.fives[-1]["volume"] = 1000
        r = volfix.reconcile_children(self.ones, self.fives, settled_before=self.fives[-1]["time"] + 120)
        self.assertEqual((r["derived"], r["unknown"], r["conflict"]), (0, 0, 0))


class TestReconcileParent(unittest.TestCase):
    """شمعة ساعة فيها دقيقة تراكمية (TSLA 2026-11-11 شمعة 10:30 = 47.7M، وحجم الساعة الحقيقي ~3.7M)."""

    def day(self):
        fives = bars((9, 30), [5000] * 78, step=300)          # 390,000 سهم
        hours = bars((9, 30), [60_000] * 6 + [30_000], step=3600)
        return hours, fives

    def test_glitched_hour_is_rebuilt_from_its_5m_bars(self):
        hours, fives = self.day()
        hours[2]["volume"] = 60_000 + 150_000
        r = volfix.reconcile_parent(hours, fives, {DAY: 400_000}, label="5m")
        self.assertEqual(r["derived"], 1)
        self.assertEqual((hours[2]["volume"], hours[2]["vol_derived"]), (60_000, "5m"))

    def test_smaller_children_sum_is_not_trusted_without_day_excess(self):
        # مجموع شموع 5 دقائق أصغر لأن بعضها ناقص، واليوم لا يتجاوز حجمه: الساعة هي الصحيحة
        hours, fives = self.day()
        for f in fives[24:36]:
            f["volume"] = 2000
        r = volfix.reconcile_parent(hours, fives, {DAY: 400_000})
        self.assertEqual(r["derived"], 0)
        self.assertEqual(hours[2]["volume"], 60_000)
        self.assertTrue(hours[2]["vol_suspect"])  # لا تُستعمل مرجعًا لغيرها

    def test_gap_in_children_blocks_the_rebuild(self):
        hours, fives = self.day()
        hours[2]["volume"] = 210_000
        del fives[24:30]  # نصف الساعة بلا شموع 5 دقائق
        r = volfix.reconcile_parent(hours, fives, {DAY: 400_000})
        self.assertEqual(r["derived"], 0)
        self.assertEqual(hours[2]["volume"], 210_000)

    def test_no_daily_reference_no_rebuild(self):
        hours, fives = self.day()
        hours[2]["volume"] = 210_000
        self.assertEqual(volfix.reconcile_parent(hours, fives, {})["derived"], 0)


class TestCleanFamily(unittest.TestCase):
    def test_raw_input_is_not_modified_and_all_intervals_come_back(self):
        ones, fives = minute_day([[1000] * 5 for _ in range(78)])
        hours = bars((9, 30), [60_000] * 6 + [30_000], step=3600)
        ones[200]["volume"] = 201_000
        raw = {"1m": ones, "5m": fives, "1h": hours}
        clean, stats = volfix.clean_family(raw, {DAY: 400_000})
        self.assertEqual(ones[200]["volume"], 201_000)
        self.assertEqual(clean["1m"][200]["volume"], 1000)
        self.assertEqual(stats["1m"]["derived"], 1)
        self.assertEqual(stats["5m"]["derived"] + stats["1h"]["derived"], 0)
        self.assertEqual(stats["1m"]["days_unverified"], [])


if __name__ == "__main__":
    unittest.main()
