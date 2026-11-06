"""اختبارات الماسح الشامل: صحة المؤشرات والشروط والتوقيت، وسلوك المحرك ببيانات اختبار مُعلنة المصدر
(شموع مُولَّدة رياضيًا، ليست أسعار سوق). python -m unittest tests.test_fullscan
"""
import json
import os
import shutil
import subprocess
import sys
import unittest
from datetime import datetime
from zoneinfo import ZoneInfo

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from fullscan import bars as B  # noqa: E402
from fullscan import indicators as I  # noqa: E402
from fullscan.engine import Engine  # noqa: E402
from fullscan.providers import RateLimited  # noqa: E402
from fullscan.store import Store  # noqa: E402

NY = ZoneInfo("America/New_York")


def ny(*a):
    return datetime(*a, tzinfo=NY).timestamp()


def ema_ref(values, n):
    """مرجع مستقل: EMA تبدأ بمتوسط بسيط لأول n قيمة ثم k = 2/(n+1)."""
    k, out = 2 / (n + 1), [None] * len(values)
    seed = sum(values[:n]) / n
    out[n - 1] = seed
    for i in range(n, len(values)):
        out[i] = values[i] * k + out[i - 1] * (1 - k)
    return out


def bars_from(closes, start, step, vols=None):
    return [{"t": int(start + i * step), "o": c, "h": c, "l": c, "c": c, "v": (vols[i] if vols else 1000.0)}
            for i, c in enumerate(closes)]


def series(n, seed=1, drift=0.0):
    x, out = 100.0 + seed, []
    for i in range(n):
        x = x * (1 + drift + 0.004 * ((i * 7919 + seed * 104729) % 11 - 5) / 5)
        out.append(round(x, 4))
    return out


class TestIndicators(unittest.TestCase):
    def test_1_macd_matches_independent_reference(self):
        c = series(300, 3)
        from scanner import macd
        line, sig, hist = macd(c)
        e12, e26 = ema_ref(c, 12), ema_ref(c, 26)
        ref_line = [a - b for a, b in zip(e12[25:], e26[25:])]
        ref_sig = ema_ref(ref_line, 9)
        self.assertAlmostEqual(line[-1], ref_line[-1], places=10)
        self.assertAlmostEqual(sig[-1], ref_sig[-1], places=10)
        self.assertIsNone(sig[32])        # أول خط إشارة عند الشمعة 34 (26 + 9 − 1)
        self.assertIsNotNone(sig[33])

    @unittest.skipUnless(shutil.which("node"), "node غير متوفر")
    def test_1b_macd_equals_chart_javascript(self):
        with open(os.path.join(os.path.dirname(__file__), "..", "static", "indicators.js"), encoding="utf-8") as f:
            src = f.read()
        js = src[src.index("  function ema("):src.index("  function rsi(")]
        c = series(200, 5)
        code = js + f"const m = macd({json.dumps(c)}); console.log(JSON.stringify([m.line.at(-1), m.sig.at(-1)]));"
        out = json.loads(subprocess.run(["node", "-e", code], capture_output=True, text=True, timeout=30).stdout)
        from scanner import macd
        line, sig, _ = macd(c)
        self.assertEqual([line[-1], sig[-1]], out)  # نفس أرقام نافذة MACD في الشارت

    def test_2_rsi_wilder(self):
        # مثال StockCharts المرجعي: الجدول يقرّب متوسطات الربح والخسارة لخانتين، فالفرق ≤ 0.1 متوقع
        c = [44.34, 44.09, 44.15, 43.61, 44.33, 44.83, 45.10, 45.42, 45.84, 46.08, 45.89, 46.03, 45.61, 46.28, 46.28,
             46.00, 46.03, 46.41, 46.22, 45.64]
        from scanner import rsi_series_last
        for k, ref in zip(range(15, 21), [70.53, 66.32, 66.55, 69.41, 66.36, 57.97]):
            self.assertAlmostEqual(rsi_series_last(c[:k]), ref, delta=0.1)

    def test_3_volume_average_excludes_evaluated_bar(self):
        vols = [100.0] * 40 + [100.0] * 20 + [500.0]
        b = bars_from(series(61), 0, 60, vols)
        ind, _ = I.compute(b, I.DEFAULTS)
        self.assertEqual(ind["vol_avg"], 100.0)
        self.assertEqual(ind["vol"], 500.0)
        vols2 = [100.0] * 40 + [300.0] * 20 + [100.0]  # المتوسط من العشرين السابقة فقط
        ind2, _ = I.compute(bars_from(series(61), 0, 60, vols2), I.DEFAULTS)
        self.assertEqual(ind2["vol_avg"], 300.0)

    def test_4_rsi_bounds_inclusive(self):
        cfg = {**I.DEFAULTS, "macd_on": False, "vol_on": False}
        base = {"macd": 1, "signal": 0, "vol": 1, "vol_avg": 1}
        self.assertTrue(I.evaluate({**base, "rsi": 50.0}, cfg))
        self.assertTrue(I.evaluate({**base, "rsi": 70.0}, cfg))
        self.assertFalse(I.evaluate({**base, "rsi": 49.99}, cfg))
        self.assertFalse(I.evaluate({**base, "rsi": 70.01}, cfg))

    def test_5_volume_strictly_greater(self):
        cfg = {**I.DEFAULTS, "macd_on": False, "rsi_on": False}
        self.assertFalse(I.evaluate({"macd": 0, "signal": 0, "rsi": 0, "vol": 150.0, "vol_avg": 100.0}, cfg))
        self.assertTrue(I.evaluate({"macd": 0, "signal": 0, "rsi": 0, "vol": 150.01, "vol_avg": 100.0}, cfg))

    def test_macd_strictly_above_signal(self):
        cfg = {**I.DEFAULTS, "rsi_on": False, "vol_on": False}
        self.assertFalse(I.evaluate({"macd": 1.0, "signal": 1.0, "rsi": 0, "vol": 0, "vol_avg": 1}, cfg))
        self.assertTrue(I.evaluate({"macd": 1.0001, "signal": 1.0, "rsi": 0, "vol": 0, "vol_avg": 1}, cfg))

    def test_10_missing_data(self):
        ind, why = I.compute(bars_from(series(20), 0, 60), I.DEFAULTS)
        self.assertIsNone(ind)
        self.assertIn("أقل من اللازم", why)
        b = bars_from(series(60), 0, 60)
        b[-3]["v"] = None
        ind, _ = I.compute(b, I.DEFAULTS)
        self.assertIsNone(I.evaluate(ind, I.DEFAULTS))  # حجم ناقص = غير كافٍ، وليس «لا يطابق»


class TestTiming(unittest.TestCase):
    def test_14_slot_ends_and_dst(self):
        summer = ny(2026, 10, 30, 15, 30)  # قبل انتهاء التوقيت الصيفي (1 نوفمبر)
        winter = ny(2026, 11, 2, 15, 30)
        self.assertEqual(B.slot_end(summer, "1h"), ny(2026, 10, 30, 16, 0))  # آخر شمعة ساعة نصف ساعة
        self.assertEqual(B.slot_end(winter, "1h"), ny(2026, 11, 2, 16, 0))
        self.assertEqual(B.slot_end(ny(2026, 11, 2), "1D"), ny(2026, 11, 2, 16, 0))
        self.assertEqual(datetime.fromtimestamp(winter, ZoneInfo("UTC")).hour, 20)
        self.assertEqual(datetime.fromtimestamp(summer, ZoneInfo("UTC")).hour, 19)

    def test_14b_three_minute_aggregation_aligned_to_open(self):
        mins = [{"t": int(ny(2026, 11, 6, 9, 29 + i)), "o": i, "h": i + 1, "l": i - 1, "c": i, "v": 10.0} for i in range(8)]
        agg = B.aggregate(mins, 3)
        starts = [datetime.fromtimestamp(b["t"], NY).strftime("%H:%M") for b in agg]
        self.assertEqual(starts, ["09:30", "09:33", "09:36"])  # 9:29 قبل الافتتاح يُستبعد
        self.assertEqual(agg[0]["v"], 30.0)
        self.assertEqual(agg[0]["o"], 1)
        self.assertEqual(agg[0]["c"], 3)

    def test_6_7_no_future_and_no_forming_bar(self):
        start = ny(2026, 11, 6, 9, 30)
        b = bars_from(series(10), start, 300)
        asof = start + 3 * 300 + 10  # بعد 3 شموع وعشر ثوانٍ: الرابعة جارية
        closed, forming = B.split(b, "5m", asof, grace=5)
        self.assertEqual(len(closed), 3)
        self.assertEqual(forming["t"], start + 3 * 300)  # الجارية هي الرابعة؛ الشموع بعد لحظة الجلب مرفوضة
        self.assertTrue(all(B.slot_end(x["t"], "5m") <= asof - 5 for x in closed))
        closed2, _ = B.split(b, "5m", asof - 8, grace=5)  # داخل مهلة الإغلاق: الشمعة الثالثة لا تُعد مغلقة بعد
        self.assertEqual(len(closed2), 2)

    def test_latest_slot(self):
        t = ny(2026, 11, 6, 10, 44)
        f = lambda x: datetime.fromtimestamp(x, NY).strftime("%a %H:%M")
        self.assertEqual(f(B.latest_slot_start("3m", t)), "Fri 10:39")
        self.assertEqual(f(B.latest_slot_start("1h", t)), "Fri 09:30")
        self.assertEqual(f(B.latest_slot_start("5m", ny(2026, 11, 9, 9, 31))), "Fri 15:55")  # الاثنين قبل أول إغلاق


# ===== محرك بمزود مُحاكى =====
class FakeProvider:
    name, label, licensed, live_market_wide, license_note = "fake", "مزود اختبار", False, False, "اختبار"
    grace, batch_size, min_gap = 5, 500, 0

    def __init__(self, n, matching=(), start=None, step=300):
        self.n, self.matching, self.start, self.step = n, set(matching), start or ny(2026, 11, 6, 9, 30), step
        self.calls, self.fail_next = 0, 0
        self.bars_upto = 60  # عدد الشموع المتاحة عند المزود

    def supported(self, iv):
        return True, None

    def symbol_bars(self, s):
        i = int(s[1:])
        if s in self.matching:
            # إغلاقات صاعدة بهدوء ثم ارتفاع حجم آخر شمعة: MACD فوق الإشارة وRSI بين 50 و70 وحجم > 1.5×
            closes = [100 + 0.05 * k + (0.3 if k % 2 else -0.25) for k in range(self.bars_upto)]
            vols = [1000.0] * (self.bars_upto - 1) + [5000.0]
        else:
            closes = [100 - 0.1 * k for k in range(self.bars_upto)]
            vols = [1000.0] * self.bars_upto
        return bars_from(closes, self.start + (i % 1) * 0, self.step, vols)

    def history(self, symbols, iv):
        self.calls += 1
        if self.fail_next:
            self.fail_next -= 1
            raise RateLimited("انقطاع محاكى", retry_after=0)
        asof = self.start + self.bars_upto * self.step + 10
        return {s: self.symbol_bars(s) for s in symbols if s != "S0013"}, {"S0013": "رمز غير مدعوم"}, asof


def universe(n):
    return lambda force=False: ([{"symbol": f"S{i:04d}", "name": f"Co {i}", "exchange": "NYSE"} for i in range(n)] +
                                [{"symbol": "S0001", "name": "dup", "exchange": "NYSE"}], {"eligible": n})


class TestEngine(unittest.TestCase):
    def make(self, n=200, matching=("S0150",), clock_at=None):
        p = FakeProvider(n, matching)
        now = [clock_at or p.start + p.bars_upto * p.step + 20]
        e = Engine(p, store=Store(":memory:"), universe_fn=universe(n), clock=lambda: now[0], sleep=lambda s: None)
        e.load_universe()
        e.configure({"interval": "5m"})
        return e, p, now

    def run_cycle(self, e):
        for _ in range(100):  # حتى لا يبقى رمز مستحق (الدفعة الفاشلة تُعاد في الخطوة التالية)
            e.step()
            if not e.due()[0]:
                break

    def test_9_13_match_outside_first_30_and_no_duplicates(self):
        e, p, _ = self.make(6000, matching=("S5000", "S0007"))
        self.assertEqual(len(e.symbols), 6000)  # الرمز المكرر في قاعدة الرموز لم يُضف مرتين
        self.run_cycle(e)
        c = e.counts()
        self.assertEqual(c["fetched_this_cycle"], 6000)
        self.assertTrue(c["full_cycle_complete"])
        res = e.changes(0)
        syms = [r["symbol"] for r in res["upsert"]]
        self.assertEqual(sorted(syms), ["S0007", "S5000"])
        self.assertEqual(len(syms), len(set(syms)))
        self.assertEqual(c["by_status"].get("error"), 1)  # S0013 بسببه
        self.assertIn("رمز غير مدعوم", e.failures()["rows"][0]["why"])

    def test_counts_add_up_to_eligible(self):
        e, p, _ = self.make(300, matching=("S0100",))
        p.bars_upto = 60
        bars, err, asof = p.history(e.symbols[:120], "5m")
        e.ingest(bars, err, asof, e.symbols[:120])  # دورة غير مكتملة: 120 من 300
        c = e.counts()
        self.assertEqual(sum(c["by_status"].values()) + sum(1 for s in e.symbols if s not in e.rows), c["eligible"])
        self.assertEqual(c["pending"], 300 - 120)
        self.assertFalse(c["full_cycle_complete"])

    def test_12_appear_and_disappear(self):
        e, p, now = self.make(50, matching=("S0010",))
        self.run_cycle(e)
        seq = e.changes(0)["seq"]
        self.assertEqual([r["symbol"] for r in e.changes(0)["upsert"]], ["S0010"])
        # الشمعة التالية: السهم يفقد شرط الحجم
        p.matching = set()
        p.bars_upto += 1
        now[0] += p.step
        self.run_cycle(e)
        d = e.changes(seq)
        self.assertIn("S0010", d["removed"])
        self.assertEqual(d["upsert"], [])

    def test_stale_symbol_leaves_matches_when_reference_advances(self):
        e, p, now = self.make(20, matching=("S0005",))
        self.run_cycle(e)
        self.assertTrue(e.rows["S0005"]["matched"])
        # شمعة جديدة أُغلقت؛ جُلب سهم آخر فقط: S0005 لم تتأكد مطابقته بعد فتخرج من القائمة
        p.bars_upto += 1
        now[0] += p.step
        bars, err, asof = p.history(["S0001"], "5m")
        e.ingest(bars, err, asof, ["S0001"])
        self.assertFalse(e.rows["S0005"]["matched"])
        self.assertEqual(e.rows["S0005"]["status"], "stale")

    def test_8_interval_switch_reevaluates(self):
        e, p, now = self.make(30, matching=("S0003",))
        self.run_cycle(e)
        e.configure({"interval": "15m"})
        self.assertEqual(e.counts()["matched"], 0)  # الفاصل الجديد لا يرث مطابقات الفاصل السابق
        p.step = 900
        now[0] = p.start + p.bars_upto * p.step + 20
        self.run_cycle(e)
        self.assertEqual([r["symbol"] for r in e.changes(0)["upsert"]], ["S0003"])
        self.assertEqual(e.cfg["interval"], "15m")

    def test_11_outage_then_recover_without_duplicates(self):
        e, p, _ = self.make(40, matching=("S0020",))
        p.fail_next = 2
        e.step()
        self.assertIn("انقطاع", e.provider_state["last_error"])
        self.run_cycle(e)
        self.assertIsNone(e.provider_state["last_error"])
        self.run_cycle(e)  # إعادة الجلب لا تكرر الشموع
        bars = e.store.load("5m")["S0020"]
        self.assertEqual(len(bars), len({b["t"] for b in bars}))

    def test_source_correction_is_saved_and_counted(self):
        e, p, _ = self.make(5)
        self.run_cycle(e)
        bars, err, asof = p.history(["S0002"], "5m")
        bars["S0002"][-5] = {**bars["S0002"][-5], "c": 999.0}  # المصدر صحح شمعة مغلقة
        e.ingest(bars, err, asof + 1, ["S0002"])
        self.assertEqual(e.store.revisions, 1)
        self.assertEqual(e.store.load("5m")["S0002"][-5]["c"], 999.0)

    def test_conditions_change_without_refetch(self):
        e, p, _ = self.make(30, matching=("S0003",))
        self.run_cycle(e)
        calls = p.calls
        e.configure({"vol_mult": 10})  # 5000 ليست > 10×1000
        self.assertEqual(e.counts()["matched"], 0)
        self.assertEqual(p.calls, calls)

    def test_forming_mode_is_separate(self):
        e, p, now = self.make(30, matching=("S0003",))
        e.configure({"mode": "forming"})
        self.run_cycle(e)
        d = e.changes(0, kind="provisional")
        self.assertIsInstance(d["upsert"], list)
        for r in e.rows.values():  # النتيجة المؤقتة لا تُخلط بالمطابقة المؤكدة
            if r["provisional"] and not r["matched"]:
                self.assertNotIn(r["symbol"], [x["symbol"] for x in e.changes(0)["upsert"]])


if __name__ == "__main__":
    unittest.main()
