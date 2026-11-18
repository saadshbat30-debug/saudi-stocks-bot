"""صفقات Alpaca تُكتب في مخزن البث المشترك، فيحسب منها «قبل الانطلاق» وشريط الصفقات، وبث ياهو يتوقف للأسهم الأمريكية.

python -m unittest tests.test_alpaca_breakout
"""
import os
import sys
import time
import unittest
from datetime import datetime
from unittest import mock
from zoneinfo import ZoneInfo

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import alpaca_daily as ad  # noqa: E402
import alpaca_stream as al  # noqa: E402
import breakout  # noqa: E402
import market  # noqa: E402
import stream  # noqa: E402

NY = ZoneInfo("America/New_York")


class TestTradesToStore(unittest.TestCase):
    def setUp(self):
        for d in (stream._hist, stream._prices, stream._tape, stream._last_volume, market._a_cum):
            d.clear()

    def test_breakout_metrics_from_alpaca_trades(self):
        """3 دقائق و20 ثانية من الصفقات: الحجم يتسارع في الدقيقة الأخيرة والسعر في نطاق ضيق."""
        start = datetime(2026, 11, 18, 11, 0, tzinfo=NY).timestamp()
        c = mock.Mock(daily={})
        clock = {"t": start}
        with mock.patch.object(al, "client", return_value=c), mock.patch.object(ad, "_hist", {"TEST": {"closes": [9.8], "vols": [1]}}), \
                mock.patch.object(stream.time, "time", side_effect=lambda: clock["t"]):
            for sec in range(0, 200, 5):
                clock["t"] = start + sec
                size = 4000 if sec >= 140 else 1000  # آخر دقيقة: 4 أضعاف
                market._alpaca_trade("TEST", 10.0 + (sec % 3) * 0.005, size, start + sec)
            m = breakout.metrics("TEST", avg_volume=390 * 12_000, now=start + 199)
            latest = stream.latest("TEST", max_age=10 ** 9)
        self.assertEqual((latest["src"], round(latest["change_pct"], 2)), ("alpaca", 2.04))  # مقابل إغلاق Alpaca 9.8
        self.assertEqual(m["acc1m"], 4.0)             # حجم آخر دقيقة ÷ متوسط الدقيقتين قبلها
        self.assertLess(m["rng3m"], 0.2)
        self.assertEqual(len(stream.tape("TEST")), 39)  # الكمية = الزيادة في حجم اليوم = كمية الصفقة
        self.assertEqual(stream.tape("TEST")[0]["size"], 4000)

    def test_day_volume_starts_from_daily_bar_and_never_drops(self):
        now = datetime(2026, 11, 18, 11, 0, tzinfo=NY).timestamp()
        day = datetime(2026, 11, 18, tzinfo=NY).astimezone(ZoneInfo("UTC")).strftime("%Y-%m-%dT%H:%M:%SZ")
        c = mock.Mock(daily={"X": ({"t": day, "v": 50_000}, now)})
        with mock.patch.object(al, "client", return_value=c), mock.patch.object(ad, "_hist", {}):
            market._alpaca_trade("X", 5.0, 100, now)
            self.assertEqual(market._a_cum["X"][1], 50_000)
            market._alpaca_trade("X", 5.0, 100, now + 1)
            self.assertEqual(market._a_cum["X"][1], 50_100)
            c.daily["X"] = ({"t": day, "v": 49_000}, now + 60)  # شمعة يوم أقل (شروط صفقات مستبعدة): لا نقص
            market._alpaca_trade("X", 5.0, 100, now + 61)
            self.assertEqual(market._a_cum["X"][1], 50_200)


class TestYahooStreamOff(unittest.TestCase):
    def test_us_symbols_not_sent_to_yahoo(self):
        with mock.patch.object(al, "source_is_alpaca", return_value=True), mock.patch.object(stream, "tv", return_value=None), \
                mock.patch.object(stream, "_wanted", set()) as wanted, mock.patch.object(stream, "_shards", []), \
                mock.patch.object(stream.threading, "Thread"):
            stream.watch(["AAPL", "TSLA", "^GSPC", "2222.SR"])
        self.assertEqual(sorted(wanted), ["2222.SR", "^GSPC"])



class TestOvernight(unittest.TestCase):
    def test_overnight_prices_from_alpaca(self):
        now = time.time()
        iso = lambda t: datetime.fromtimestamp(t, ZoneInfo("UTC")).strftime("%Y-%m-%dT%H:%M:%SZ")
        snaps = {"TSLA": {"latestTrade": {"p": 381.5, "t": iso(now - 120)}},
                 "OLD": {"latestTrade": {"p": 5.0, "t": iso(now - 3 * 86400)}}}  # صفقة قديمة: لا تُعرض كسعر الليلة
        market._night_quotes.clear()
        with mock.patch.object(market.alpaca_data, "snapshots", return_value=snaps) as sn:
            self.assertTrue(market._night_alpaca(["TSLA", "OLD"]))
        self.assertEqual(sn.call_args.args[1], "overnight")
        self.assertEqual((market._night_quotes["TSLA"]["price"], market._night_quotes["TSLA"]["src"]), (381.5, "alpaca"))
        self.assertNotIn("OLD", market._night_quotes)
        with mock.patch.object(market.alpaca_data, "snapshots", side_effect=RuntimeError("Alpaca HTTP 403")):
            self.assertFalse(market._night_alpaca(["TSLA"]))  # ← ياهو بدلًا منه

if __name__ == "__main__":
    unittest.main()
