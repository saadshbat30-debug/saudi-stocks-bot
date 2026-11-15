"""معادلة ضغط الشراء/البيع التقديري واحدة في الخادم (market._flow_parts) والمتصفح (static/pressure.js).

python -m unittest tests.test_pressure
"""
import json
import os
import random
import shutil
import subprocess
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import market  # noqa: E402

T0 = 1794400000 - 1794400000 % 86400 + 7 * 3600 + 17 * 3600  # بداية يوم تداول بالوقت المزاح + منتصفه


def bar(i, o, h, l, c, v, t0=T0):
    return {"time": t0 + 60 * i, "open": o, "high": h, "low": l, "close": c, "volume": v}


class TestEquation(unittest.TestCase):
    def test_close_position_splits_the_bar_value(self):
        # إغلاق عند ثلاثة أرباع المدى: 75% شراء. القيمة = (11 + 9 + 10.5) ÷ 3 × 600
        (buy, sell), = market._flow_parts([bar(0, 10, 11, 9, 10.5, 600)])
        value = (11 + 9 + 10.5) / 3 * 600
        self.assertAlmostEqual(buy, value * 0.75)
        self.assertAlmostEqual(sell, value * 0.25)

    def test_flat_bar_follows_direction_from_previous_close(self):
        b = [bar(0, 10, 10.2, 9.8, 10, 100), bar(1, 10.1, 10.1, 10.1, 10.1, 100),   # أعلى من السابق ← شراء
             bar(2, 10.0, 10.0, 10.0, 10.0, 100), bar(3, 10.0, 10.0, 10.0, 10.0, 100)]  # أدنى ← بيع، مساوٍ ← مناصفة
        p = market._flow_parts(b)
        self.assertEqual((p[1][1], p[2][0]), (0, 0))
        self.assertAlmostEqual(p[3][0], p[3][1])

    def test_first_flat_bar_uses_its_own_open(self):
        (buy, sell), = market._flow_parts([bar(0, 9.9, 10, 10, 10, 100)])
        self.assertEqual(sell, 0)

    def test_unknown_volume_bar_adds_nothing(self):
        self.assertEqual(market._flow_parts([bar(0, 10, 11, 9, 10.5, 0)]), [(0.0, 0.0)])


@unittest.skipUnless(shutil.which("node"), "node غير مثبت")
class TestServerBrowserParity(unittest.TestCase):
    """اللوحة (الخادم) والسلّم والمؤشر السفلي (pressure.js) تعطي الرقم نفسه على الشموع نفسها."""

    def test_same_numbers_on_two_days_with_flat_bars(self):
        rnd = random.Random(7)
        bars, price = [], 10.0
        for day in (0, 1):
            for i in range(300):
                o = price
                flat = rnd.random() < 0.3
                h = o if flat else o + rnd.random() * 0.2
                l = o if flat else o - rnd.random() * 0.2
                c = o if flat and rnd.random() < 0.5 else round(rnd.uniform(l, h), 4)
                if flat and c != o:
                    h = l = c = round(o + rnd.choice((-0.01, 0.01)), 4)
                bars.append(bar(i, o, h, l, c, rnd.choice((0, 0, 100, 2500, 40000)), T0 + day * 86400))
                price = c
        js = ("const p=require(process.argv[1]);const b=JSON.parse(require('fs').readFileSync(0,'utf8'));"
              "console.log(JSON.stringify(p.series(b).map(x=>[x.buy,x.sell,x.cum,x.day])))")
        out = subprocess.run(["node", "-e", js, os.path.join(ROOT, "static", "pressure.js")], input=json.dumps(bars),
                             capture_output=True, text=True, check=True).stdout
        got = json.loads(out)
        self.assertEqual(len({g[3] for g in got}), 2)  # يومان
        for d in sorted({g[3] for g in got}):
            idx = [i for i, g in enumerate(got) if g[3] == d]
            parts = market._flow_parts([bars[i] for i in idx])  # الخادم يحسب آخر يوم وحده
            for i, (buy, sell) in zip(idx, parts):
                self.assertAlmostEqual(got[i][0], buy, places=6)
                self.assertAlmostEqual(got[i][1], sell, places=6)
            self.assertAlmostEqual(got[idx[-1]][2], sum(p[0] - p[1] for p in parts), places=4)



class TestRecentWindowByTimestamps(unittest.TestCase):
    """«آخر 5 دقائق» بالوقت الفعلي لا بعدد الشموع."""
    NOW = 1794420000 + 37  # 37 ثانية داخل دقيقة

    def bars_at(self, minutes_ago):
        """شموع تبدأ قبل بداية الدقيقة الحالية بهذه الدقائق (0 = الدقيقة الجارية)."""
        base = self.NOW // 60 * 60 + market.RIYADH_OFFSET
        return [{"time": base - 60 * m, "open": 10, "high": 11, "low": 9, "close": 11, "volume": 100}
                for m in sorted(minutes_ago, reverse=True)]

    def window(self, minutes_ago, n=5):
        b = self.bars_at(minutes_ago)
        return market.flow_window(b, market._flow_parts(b), n, self.NOW)

    def test_only_complete_minutes_inside_the_window_count(self):
        w = self.window([0, 1, 2, 3, 4, 5, 6, 7])  # الجارية + 7 قبلها
        self.assertEqual(w["bars"], 5)             # الدقائق 1..5 فقط
        self.assertAlmostEqual(w["inflow"], 5 * 100 * (11 + 9 + 11) / 3, delta=1)

    def test_gaps_are_not_filled_with_older_bars(self):
        w = self.window([2, 4, 30, 31, 32])        # سهم متقطع التداول: شمعتان فقط في آخر 5 دقائق
        self.assertEqual(w["bars"], 2)

    def test_closed_market_gives_no_window(self):
        self.assertIsNone(self.window([600, 601, 602, 603, 604]))  # آخر الشموع قبل 10 ساعات
        self.assertIsNone(market.flow_window([], [], 5, self.NOW))

    def test_fifteen_minutes(self):
        self.assertEqual(self.window(range(0, 40), 15)["bars"], 15)


if __name__ == "__main__":
    unittest.main()
