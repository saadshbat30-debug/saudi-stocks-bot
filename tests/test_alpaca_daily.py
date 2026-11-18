"""السيولة والزخم من Alpaca: تاريخ يومي (متوسط حجم 3 أشهر، RSI، متوسط 20) وعلامة مصدر كل رقم في الجدول.

python -m unittest tests.test_alpaca_daily
"""
import os
import sys
import tempfile
import time
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest import mock
from zoneinfo import ZoneInfo

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import alpaca_daily as ad  # noqa: E402
import alpaca_stream as al  # noqa: E402
import market  # noqa: E402

NY = ZoneInfo("America/New_York")
CFG = {"key": "PKTESTKEY1234567890AB", "secret": "s" * 40, "feed": "sip"}


class Resp:
    def __init__(self, d, code=200):
        self.status_code, self._d, self.text = code, d, str(d)

    def json(self):
        return self._d


def day(offset):
    """شمعة يومية بتاريخ نيويورك قبل اليوم بـ offset يوم (منتصف ليل نيويورك كما يرسله Alpaca)."""
    d = datetime.now(NY).replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=offset)
    return d.astimezone(ZoneInfo("UTC")).strftime("%Y-%m-%dT%H:%M:%SZ")


class TestFetch(unittest.TestCase):
    def test_complete_days_only_symbol_mapping_and_pages(self):
        p1 = {"bars": {"BRK.B": [{"t": day(2), "c": 400, "v": 10}], "AAPL": [{"t": day(2), "c": 190, "v": 100}]}, "next_page_token": "x"}
        p2 = {"bars": {"AAPL": [{"t": day(1), "c": 191, "v": 200}, {"t": day(0), "c": 192, "v": 5}]}, "next_page_token": None}
        http = mock.Mock()
        http.get.side_effect = [Resp(p1), Resp(p2)]
        out = ad._fetch(["AAPL", "BRK-B", "ZZZZ"], CFG, http)
        kw = http.get.call_args_list[0].kwargs
        self.assertEqual((kw["params"]["symbols"], kw["params"]["feed"], kw["params"]["timeframe"]), ("AAPL,BRK.B,ZZZZ", "sip", "1Day"))
        self.assertEqual(out["AAPL"], {"closes": [190.0, 191.0], "vols": [100, 200]})  # شمعة اليوم مستبعدة
        self.assertEqual(out["BRK-B"]["closes"], [400.0])
        self.assertEqual(out["ZZZZ"], {"closes": [], "vols": []})

    def test_avg_volume_needs_20_days_and_uses_last_63(self):
        ad._hist.clear()
        ad._hist["X"] = {"closes": [1] * 19, "vols": [10] * 19}
        self.assertIsNone(ad.avg_volume("X"))
        ad._hist["X"] = {"closes": [1] * 70, "vols": [1000] * 7 + [100] * 63}
        self.assertEqual(ad.avg_volume("X"), 100)
        ad._hist.clear()


class TestEnsure(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.p = mock.patch.object(ad, "CACHE", Path(self.tmp.name) / "c.json.gz")
        self.p.start()
        ad._hist.clear()
        ad._state.update(day=None, running=False, done=set(), failed=0, error=None, loaded_from_disk=True, next_try=0.0)

    def tearDown(self):
        self.p.stop()
        self.tmp.cleanup()
        ad._hist.clear()

    def test_most_active_first_saved_and_reloaded_same_day(self):
        calls = []

        def fetch(batch, cfg, http):
            calls.append(list(batch))
            return {s: {"closes": [1.0] * 30, "vols": [5] * 30} for s in batch}
        with mock.patch.object(ad, "_fetch", fetch), mock.patch.object(al, "config", return_value=CFG), \
                mock.patch.object(ad, "BATCH", 2):
            ad.ensure(["NVDA", "AAPL", "2222.SR", "F"])
            for _ in range(100):
                if not ad._state["running"]:
                    break
                time.sleep(0.02)
        self.assertEqual(calls, [["NVDA", "AAPL"], ["F"]])
        self.assertEqual(ad.status()["with_history"], 3)
        ad._hist.clear()
        ad._state.update(day=None, done=set(), loaded_from_disk=False)
        ad._load_disk()
        self.assertEqual(ad.avg_volume("F"), 5)

    def test_rejected_key_stops_early(self):
        def fetch(batch, cfg, http):
            raise RuntimeError("Alpaca HTTP 403: forbidden")
        with mock.patch.object(ad, "_fetch", fetch) as f, mock.patch.object(al, "config", return_value=CFG), \
                mock.patch.object(ad, "BATCH", 1):
            ad._run(["A", "B", "C"])
        self.assertEqual(ad._state["failed"], 1)
        self.assertIn("403", ad.status()["error"])
        # المنصة تطلب ensure كل 5 ثوانٍ: لا طلب جديد قبل 10 دقائق
        with mock.patch.object(ad.threading, "Thread") as th:
            ad._state["day"] = ad._today()
            ad.ensure(["A"])
        th.assert_not_called()


class TestTableSources(unittest.TestCase):
    """_enrich كامل: السعر والحجم والسيولة والزخم من Alpaca، وعلامة المصدر لكل رقم."""

    def row(self):
        return {"symbol": "TEST", "name": "T", "price": 10.0, "change": 0.0, "change_pct": 0.0, "prev_close": 9.0, "open": 9.5,
                "day_high": 10.5, "day_low": 9.4, "volume": 1000, "avg_volume": 50_000, "shares": 1_000_000, "high52": None,
                "low52": None, "otc": False, "exchange": "NMS", "quote_time": time.time() - 30, "ext_price": None, "ext_change_pct": None,
                "night_price": None, "night_change_pct": None}

    def enrich(self, live, hist):
        c = mock.Mock()
        c.live.return_value = live
        with mock.patch.object(al, "source_is_alpaca", return_value=True), mock.patch.object(al, "client", return_value=c), \
                mock.patch.object(ad, "_hist", hist), mock.patch.object(market.stream, "latest", return_value=None), \
                mock.patch.object(market.stream, "tv", return_value=None), mock.patch.object(market.breakout, "metrics", return_value=None), \
                mock.patch.object(market.extras, "float_for", return_value=None), \
                mock.patch.object(market.extras, "sharia_for", return_value={"status": None, "why": None}):
            return market._enrich(self.row(), time.time())

    def test_all_from_alpaca(self):
        now = time.time()
        today = datetime.now(NY).replace(hour=0, minute=0, second=0, microsecond=0).timestamp()
        live = {"price": 12.0, "at": now - 1, "kind": "trade", "day_date": today, "volume": 300_000, "day_high": 12.5,
                "day_low": 9.8, "vwap": 11.0}
        hist = {"TEST": {"closes": [9.0 + i * 0.01 for i in range(30)], "vols": [100_000] * 30}}
        r = self.enrich(live, hist)
        self.assertEqual((r["price"], r["volume"], r["src"]), (12.0, 300_000, "alpaca"))
        self.assertEqual(r["vol_ratio"], 3.0)          # 300,000 ÷ متوسط Alpaca 100,000 (لا متوسط ياهو 50,000)
        self.assertEqual(r["value"], 3_300_000)         # VWAP Alpaca 11 × الحجم
        self.assertEqual((r["liq_src"], r["mom_src"]), ("alpaca", "alpaca"))
        self.assertIsNotNone(r["rsi"])

    def test_without_alpaca_history_falls_back_and_says_so(self):
        now = time.time()
        today = datetime.now(NY).replace(hour=0, minute=0, second=0, microsecond=0).timestamp()
        live = {"price": 12.0, "at": now - 1, "kind": "trade", "day_date": today, "volume": 300_000, "day_high": 12.5,
                "day_low": 9.8, "vwap": 11.0}
        r = self.enrich(live, {})
        self.assertEqual(r["vol_ratio"], 6.0)           # متوسط ياهو 50,000
        self.assertEqual((r["liq_src"], r["mom_src"]), ("yahoo", "yahoo"))


if __name__ == "__main__":
    unittest.main()
