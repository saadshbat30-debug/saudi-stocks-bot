"""اختبارات مزود TradingView دون اتصال: رسائل مُحاكاة بنفس شكل رسائل المصدر الحقيقية.

python -m unittest tests.test_tradingview_provider
"""
import os
import sys
import threading
import time
import unittest
from datetime import datetime
from zoneinfo import ZoneInfo

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from services import tradingview_provider as tvp  # noqa: E402

NY = ZoneInfo("America/New_York")


def ny(*a):
    return datetime(*a, tzinfo=NY).timestamp()


class FakeClient:
    """بديل اتصال المكتبة: يسجل الرسائل المرسلة ولا يتصل بالشبكة."""

    def __init__(self):
        self.sent = []
        self._connected = threading.Event()
        self._connected.set()
        self.connects = 1
        self.last_rx = time.time()
        self.connected_at = time.time()
        self.last_error = None
        self.disconnected_at = None

    def send(self, func, params):
        self.sent.append((func, params))


def provider():
    p = tvp.Provider()
    p.client = FakeClient()
    p.qs = "qs_test"
    return p


def bar_msg(cs, points, method="du"):
    return [cs, {"sds_1": {"s": [{"i": i, "v": list(v)} for i, v in enumerate(points)]}}]


class TestSymbols(unittest.TestCase):
    def test_candidates(self):
        self.assertEqual(tvp.tv_candidates("F", ("NYQ", False))[0], "NYSE:F")
        self.assertEqual(tvp.tv_candidates("BRK-B", ("NYQ", False))[0], "NYSE:BRK.B")
        self.assertEqual(tvp.tv_candidates("SPY", ("PCX", False))[0], "AMEX:SPY")
        self.assertEqual(tvp.tv_candidates("TCEHY", ("PNK", True))[0], "OTC:TCEHY")
        self.assertEqual(tvp.tv_candidates("XYZ"), ["NASDAQ:XYZ", "NYSE:XYZ", "AMEX:XYZ", "CBOE:XYZ", "OTC:XYZ"])
        self.assertEqual(tvp.tv_candidates("^DJI"), ["TVC:DJI"])
        self.assertEqual(tvp.tv_candidates("2222.SR"), [])  # السوق السعودي خارج هذا المزود

    def test_fallback_then_unavailable(self):
        p = provider()
        p.watch(["HODO"])
        self.assertIn(("quote_add_symbols", ["qs_test", "NASDAQ:HODO"]), p.client.sent)
        for name in ("NASDAQ:HODO", "NYSE:HODO", "AMEX:HODO", "CBOE:HODO"):
            p._on_qsd(["qs_test", {"n": name, "s": "error"}])
        self.assertEqual(p.client.sent[-1], ("quote_add_symbols", ["qs_test", "OTC:HODO"]))
        p._on_qsd(["qs_test", {"n": "OTC:HODO", "s": "ok", "v": {"lp": 0.01, "pro_name": "OTC:HODO", "update_mode": "delayed_streaming_900"}}])
        self.assertEqual(p.symbol_status("HODO"), "ok")
        self.assertEqual(p.resolved("HODO"), "OTC:HODO")
        self.assertEqual(p.meta("HODO")["state"], "delayed")
        self.assertEqual(p.meta("HODO")["delay_s"], 900)
        p.watch(["KVUE"])
        for e in ("NASDAQ", "NYSE", "AMEX", "CBOE", "OTC"):
            p._on_qsd(["qs_test", {"n": f"{e}:KVUE", "s": "error"}])
        self.assertEqual(p.symbol_status("KVUE"), "unavailable")
        self.assertIsNone(p.resolved("KVUE"))

    def test_partial_updates_merge_and_volume_sentinel(self):
        p = provider()
        seen = []
        p.on_quote = lambda s, v, ch, rx, first: seen.append((s, dict(v), set(ch), first))
        p.watch(["^DJI"])
        p._on_qsd(["qs_test", {"n": "TVC:DJI", "s": "ok", "v": {"lp": 100.0, "volume": 1e100, "update_mode": "streaming"}}])
        p._on_qsd(["qs_test", {"n": "TVC:DJI", "s": "ok", "v": {"lp": 101.0}}])
        self.assertIsNone(p.quote("^DJI")["v"]["volume"])  # 1e100 = لا حجم للمؤشر
        self.assertEqual(seen[-1][1]["lp"], 101.0)
        self.assertEqual(seen[-1][2], {"lp"})
        self.assertEqual([x[3] for x in seen], [True, False])

    def test_delay_seconds(self):
        self.assertEqual(tvp.delay_seconds("streaming"), 0)
        self.assertEqual(tvp.delay_seconds("delayed_streaming_900"), 900)
        self.assertIsNone(tvp.delay_seconds(None))


class TestBars(unittest.TestCase):
    def setUp(self):
        self.p = provider()
        self.s = tvp._Series(("NASDAQ:TSLA", "1m", False), 10)
        self.p.series[self.s.key] = self.s
        self.p._open_series(self.s)
        self.cs = self.s.cs
        self.t0 = int(ny(2026, 11, 5, 10, 0))

    def bars(self, now):
        out, meta = self.p._export(self.s, "1m", False, now=now)
        return out, meta

    def test_history_then_forming_update_in_place(self):
        hist = [(self.t0 + 60 * i, 10 + i, 11 + i, 9 + i, 10.5 + i, 100) for i in range(5)]
        self.p._on_bars(bar_msg(self.cs, hist, "timescale_update"))
        self.p._on_completed([self.cs])
        last = self.t0 + 240
        self.p._on_bars(bar_msg(self.cs, [(last, 14, 15.5, 13, 15.2, 180)]))
        self.p._on_bars(bar_msg(self.cs, [(last, 14, 15.9, 13, 15.8, 260)]))
        out, meta = self.bars(now=last + 30)
        self.assertEqual(len(out), 5)  # تحديث الشمعة الجارية لا يضيف شمعة
        self.assertEqual((out[-1]["close"], out[-1]["high"], out[-1]["volume"]), (15.8, 15.9, 260))
        self.assertFalse(out[-1]["closed"])
        self.assertTrue(all(b["closed"] for b in out[:-1]))
        self.assertTrue(meta["forming"])

    def test_new_bar_closes_previous(self):
        self.p._on_bars(bar_msg(self.cs, [(self.t0, 10, 11, 9, 10.5, 100)]))
        self.p._on_bars(bar_msg(self.cs, [(self.t0 + 60, 10.5, 10.6, 10.4, 10.55, 5)]))
        out, _ = self.bars(now=self.t0 + 61)
        self.assertEqual([b["closed"] for b in out], [True, False])
        out, _ = self.bars(now=self.t0 + 125)  # انتهت دقيقتها
        self.assertTrue(out[-1]["closed"])

    def test_reconnect_replay_no_duplicates_and_gap_recovery(self):
        live = [(self.t0 + 60 * i, 10, 11, 9, 10, 100) for i in range(3)]
        self.p._on_bars(bar_msg(self.cs, live, "timescale_update"))
        old_cs = self.cs
        # انقطاع: فاتت الدقيقتان 3 و4، ثم إعادة اتصال تعيد بناء الجلسة وتعيد التاريخ كاملًا
        self.p.client.connects = 2
        self.p._setup()
        end = time.time() + 5
        while self.s.cs == old_cs and time.time() < end:  # إعادة بناء الجلسات تجري في خيط منفصل
            time.sleep(0.02)
        self.assertNotEqual(self.s.cs, old_cs)
        self.assertEqual(self.s.resyncs, 1)
        replay = live + [(self.t0 + 180, 10, 12, 9, 11, 140), (self.t0 + 240, 11, 12, 10, 11.5, 90)]
        self.p._on_bars(bar_msg(old_cs, [(self.t0 + 300, 1, 1, 1, 1, 1)]))  # رسالة من الجلسة القديمة تُتجاهل
        self.p._on_bars(bar_msg(self.s.cs, replay, "timescale_update"))
        out, meta = self.bars(now=self.t0 + 250)
        times = [b["time"] for b in out]
        self.assertEqual(times, sorted(set(times)))  # لا تكرار
        self.assertEqual(len(out), 5)  # الشموع المفقودة استُرجعت من التاريخ
        self.assertEqual(self.s.duplicates, 3)
        self.assertEqual(meta["corrections"], 0)

    def test_closed_bar_changed_by_source_is_recorded(self):
        self.p._on_bars(bar_msg(self.cs, [(self.t0, 10, 11, 9, 10.5, 100), (self.t0 + 60, 10.5, 11, 10, 10.7, 50)]))
        self.p._on_bars(bar_msg(self.cs, [(self.t0, 10, 11, 9, 10.6, 120)]))  # تصحيح من المصدر لشمعة مغلقة
        out, meta = self.bars(now=self.t0 + 70)
        self.assertEqual(out[0]["close"], 10.6)
        self.assertEqual(meta["corrections"], 1)
        self.assertEqual(self.s.corrections[0]["old"][3], 10.5)

    def test_gaps_reported_not_filled(self):
        pts = [(self.t0, 1, 1, 1, 1, 1), (self.t0 + 60, 1, 1, 1, 1, 1), (self.t0 + 300, 1, 1, 1, 1, 1)]
        self.p._on_bars(bar_msg(self.cs, pts))
        out, meta = self.bars(now=self.t0 + 1000)
        self.assertEqual(len(out), 3)  # لا شموع مصطنعة
        self.assertEqual(meta["gaps"], {"missing": 3, "largest_min": 3})

    def test_no_delete_for_sessions_of_previous_connection(self):
        old = self.s.cs
        self.p._setup()  # اتصال جديد: جلسة السلسلة القديمة لم تعد موجودة عند الخادم
        stale = tvp._Series(("NASDAQ:AAPL", "1m", False), 10)
        stale.cs, stale.gen = "cs_old", self.p.gen - 1
        self.p.series[stale.key] = stale
        self.p.client.sent.clear()
        self.p._close_series(stale)
        self.assertNotIn(("chart_delete_session", ["cs_old"]), self.p.client.sent)
        end = time.time() + 5
        while self.s.cs == old and time.time() < end:
            time.sleep(0.02)
        self.p.client.sent.clear()
        self.p._close_series(self.s)
        self.assertIn(("chart_delete_session", [self.s.cs]), self.p.client.sent)

    def test_series_error_then_retry_waits(self):
        self.p._on_series_error([self.cs, "invalid symbol"])
        self.assertTrue(self.s.error)
        self.assertTrue(self.s.done.is_set())


class TestTime(unittest.TestCase):
    def test_daily_normalized_to_ny_midnight_across_dst(self):
        # TradingView: افتتاح الجلسة 13:30 UTC في التوقيت الصيفي و14:30 UTC في الشتوي
        summer = datetime(2026, 10, 30, 13, 30, tzinfo=ZoneInfo("UTC")).timestamp()
        winter = datetime(2026, 11, 2, 14, 30, tzinfo=ZoneInfo("UTC")).timestamp()
        self.assertEqual(tvp.normalize_time(summer, "1d"), int(ny(2026, 10, 30)))
        self.assertEqual(tvp.normalize_time(winter, "1d"), int(ny(2026, 11, 2)))
        self.assertEqual(tvp.normalize_time(winter, "1mo"), int(ny(2026, 11, 1)))
        self.assertEqual(tvp.normalize_time(winter, "1w"), int(ny(2026, 11, 2)))
        self.assertEqual(tvp.normalize_time(winter, "5m"), int(winter))

    def test_forming_daily_uses_ny_close(self):
        bar = datetime(2026, 11, 5, 14, 30, tzinfo=ZoneInfo("UTC")).timestamp()
        self.assertTrue(tvp.is_forming(bar, "1d", ny(2026, 11, 5, 15, 59)))
        self.assertFalse(tvp.is_forming(bar, "1d", ny(2026, 11, 5, 16, 0)))
        self.assertFalse(tvp.is_forming(bar, "1d", ny(2026, 11, 6, 10, 0)))

    def test_forming_weekly(self):
        bar = datetime(2026, 11, 2, 14, 30, tzinfo=ZoneInfo("UTC")).timestamp()
        self.assertTrue(tvp.is_forming(bar, "1w", ny(2026, 11, 6, 12, 0)))
        self.assertFalse(tvp.is_forming(bar, "1w", ny(2026, 11, 6, 16, 1)))
        self.assertFalse(tvp.is_forming(bar, "1w", ny(2026, 11, 7, 12, 0)))


class TestLimitsAndCache(unittest.TestCase):
    def test_limiter_rate_and_background_reserve(self):
        lim = tvp._Limiter(rate=10, burst=2)
        t = time.time()
        lim.acquire()
        lim.acquire()   # الدفعة الأولى فورية
        lim.acquire()   # الثالثة تنتظر ~0.1 ثانية
        self.assertGreaterEqual(time.time() - t, 0.08)
        lim.tokens, lim.at = 1.0, time.time()
        lim.rate = 0.0001  # لا تجدد: الخلفية لا تأخذ آخر رمز، والطلب التفاعلي يأخذه
        done = threading.Event()
        threading.Thread(target=lambda: (lim.acquire(background=True), done.set()), daemon=True).start()
        self.assertFalse(done.wait(0.3))
        lim.acquire()
        self.assertEqual(int(lim.tokens), 0)

    def test_pause_after_rate_error_blocks_background_only(self):
        p = provider()
        p.client.critical = []
        p._on_critical('{"m":"critical_error","p":["max sessions rate reached"]}')
        self.assertEqual(p.rate_errors, 1)
        self.assertGreater(p.limiter.paused_until, time.time() + 60)
        t = time.time()
        p.limiter.acquire()  # التفاعلي لا يتوقف
        self.assertLess(time.time() - t, 0.5)

    def test_daily_cache_cutoff(self):
        # قبل 16:15 نيويورك: آخر إغلاق مكتمل هو أمس 16:15 (والجمعة لو كان اليوم سبتًا أو أحدًا أو الاثنين صباحًا)
        self.assertEqual(tvp.Provider.last_close_cutoff(ny(2026, 11, 5, 15, 0)), ny(2026, 11, 4, 16, 15))
        self.assertEqual(tvp.Provider.last_close_cutoff(ny(2026, 11, 5, 17, 0)), ny(2026, 11, 5, 16, 15))
        self.assertEqual(tvp.Provider.last_close_cutoff(ny(2026, 11, 9, 9, 0)), ny(2026, 11, 6, 16, 15))
        self.assertEqual(tvp.Provider.last_close_cutoff(ny(2026, 11, 8, 12, 0)), ny(2026, 11, 6, 16, 15))

    def test_daily_cached_uses_file_when_fresh(self):
        p = provider()
        p.daily_cache = {"AAA": {"at": time.time(), "count": 70, "bars": [[1, 1, 1, 1, 1, 1]] * 70}}
        calls = []
        p.daily = lambda syms, count, background=True: calls.append(syms) or {}
        out = p.daily_cached(["AAA", "BBB"], 70)
        self.assertEqual(len(out["AAA"]), 70)
        self.assertEqual(calls, [["BBB"]])  # المحفوظ لا يُطلب من جديد


class TestStreamRouting(unittest.TestCase):
    """رسائل TradingView تملأ نفس مخازن بث ياهو التي تقرأ منها المنصة (فلتر قبل الانطلاق، الرادار، الأخبار)."""

    def setUp(self):
        import stream
        self.st = stream
        for d in (stream._prices, stream._ext_prices, stream._hist, stream._tape, stream._last_volume, stream._delayed):
            d.clear()

    def test_market_trade_goes_to_regular_and_history(self):
        now = time.time()
        v = {"lp": 10.0, "lp_time": now - 1, "volume": 1000, "ch": 0.5, "chp": 5.0, "current_session": "market", "update_mode": "streaming"}
        self.st._on_tv_quote("ABC", v, set(v), now, True)
        self.st._on_tv_quote("ABC", {**v, "lp": 10.1, "volume": 1500}, {"lp", "volume"}, now + 1, False)
        self.assertEqual(self.st.latest("ABC")["price"], 10.1)
        self.assertEqual(self.st.latest("ABC")["src"], "tradingview")
        self.assertEqual(len(self.st.history("ABC")), 2)
        self.assertEqual(self.st.tape("ABC")[0]["size"], 500)

    def test_old_first_snapshot_is_not_a_trade(self):
        now = time.time()
        v = {"lp": 10.0, "lp_time": now - 3600, "volume": 1000, "current_session": "market", "update_mode": "streaming"}
        self.st._on_tv_quote("OLD", v, set(v), now, True)
        self.assertIsNone(self.st.latest("OLD"))
        self.assertEqual(self.st.history("OLD"), [])

    def test_post_market_goes_to_ext(self):
        now = time.time()
        v = {"lp": 10.0, "rtc": 10.4, "rtc_time": now - 2, "rch": 0.4, "rchp": 4.0, "volume": 1000, "current_session": "post_market", "update_mode": "streaming"}
        self.st._on_tv_quote("ABC", v, set(v), now, True)
        self.assertIsNone(self.st.latest("ABC"))
        self.assertEqual(self.st.latest_ext("ABC")["price"], 10.4)
        self.assertEqual(self.st.latest_ext("ABC")["change_pct"], 4.0)

    def test_delayed_never_counted_as_live(self):
        now = time.time()
        v = {"lp": 1.0, "lp_time": now - 900, "volume": 10, "current_session": "market", "update_mode": "delayed_streaming_900"}
        self.st._on_tv_quote("OTCX", v, set(v), now, True)
        self.assertIsNone(self.st.latest("OTCX"))
        self.assertEqual(self.st.history("OTCX"), [])
        self.assertEqual(self.st.latest_delayed("OTCX")["delay"], 900)


if __name__ == "__main__":
    unittest.main()
