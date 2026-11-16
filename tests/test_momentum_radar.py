"""اختبارات رادار الزخم (دون اتصال): المؤشر، التصنيف، الترتيب، ورد المسار عند النجاح والفشل.

python -m unittest tests.test_momentum_radar
"""
import os
import sys
import unittest
from datetime import datetime
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import momentum_radar as mr  # noqa: E402


def raw(ticker="NASDAQ:AAA", **kw):
    """صف كما يرجعه ماسح TradingView."""
    r = {"ticker": ticker, "name": ticker.split(":")[1], "exchange": ticker.split(":")[0], "close": 10.0, "change": 5.0,
         "volume": 2_000_000, "relative_volume_10d_calc": 3.0, "market_cap_basic": 1e9, "sector": "Tech",
         "update_mode": "delayed_streaming_900", "high|15": 10.05, "change|5": 0.9, "change|15": 1.5, "change|60": 2.5,
         "RSI": 65.0, "MACD.macd": 0.2, "MACD.signal": 0.1, "EMA20": 9.5, "EMA50": 9.0, "VWAP": 9.8}
    r.update(kw)
    return r


class TestMomentum(unittest.TestCase):
    def test_full_components_match_hand_calculation(self):
        row = {"price": 10, "chg_5m": 0.75, "chg_15m": 1.5, "chg_1h": 2.5, "rsi": 60, "macd": 0.2, "macd_signal": 0.1,
               "vwap": 9.8, "ema20": 9.5, "ema50": 9.9, "rvol": 3}
        m = mr.compute_momentum(row)
        # السرعة 0.5 · RSI 0.5 · MACD 1.0 · الاتجاه 2/3 · الحجم 0.5
        self.assertEqual(m["parts"], {"speed": 50, "rsi": 50, "macd": 100, "trend": 67, "volume": 50})
        self.assertAlmostEqual(m["value"], round(0.5 * 30 + 0.5 * 20 + 15 + (2 / 3) * 15 + 0.5 * 20, 1))
        self.assertEqual((m["label"], m["trend"]), ("زخم قوي", "يتسارع"))

    def test_missing_components_are_excluded_and_reweighted(self):
        m = mr.compute_momentum({"price": 10, "rsi": 70, "rvol": 5, "macd": 1, "macd_signal": 0})
        self.assertEqual(m["value"], 100.0)
        self.assertEqual(m["trend"], "غير معروف")
        self.assertEqual(mr.compute_momentum({"rsi": 70, "rvol": float("nan")})["label"], "بيانات غير كافية")

    def test_overbought_rsi_weakens_and_falling_price_is_flagged(self):
        self.assertEqual(mr.compute_momentum({"rsi": 90})["parts"]["rsi"], 50)
        self.assertEqual(mr.compute_momentum({"chg_5m": -0.2, "chg_15m": 1})["trend"], "يتراجع")


class TestClassify(unittest.TestCase):
    def row(self, **kw):
        return {"price": 10, "change_pct": 5, "volume": 2_000_000, "rvol": 3, "breakout_dist_pct": 0.5, **kw}

    def test_early_breakout_during_market_hours(self):
        self.assertEqual(mr.classify_row(self.row(), "Market Open")[:2], ("اختراق مبكر محتمل", 75))

    def test_late_moves_are_marked(self):
        for kw in ({"change_pct": 12}, {"breakout_dist_pct": 2.6}, {"rvol": 8}):
            self.assertEqual(mr.classify_row(self.row(**kw), "Market Open")[0], "متأخر / ممتد")

    def test_session_adjustments(self):
        scores = {s: mr.classify_row(self.row(), s)[1] for s in ("Pre-market", "Market Open", "After-hours", "Closed")}
        self.assertEqual(scores, {"Pre-market": 73, "Market Open": 75, "After-hours": 60, "Closed": 50})

    def test_sessions_by_new_york_clock(self):
        at = lambda d, h, m: mr.get_market_session(datetime(2026, 11, d, h, m, tzinfo=mr.NY_TZ))["name"]
        self.assertEqual([at(16, 3, 59), at(16, 4, 0), at(16, 9, 30), at(16, 16, 0), at(16, 20, 0), at(14, 11, 0)],
                         ["Closed", "Pre-market", "Market Open", "After-hours", "Closed", "Closed"])


class TestTableAndSnapshot(unittest.TestCase):
    def setUp(self):
        mr._cache.update(at=0, data=None)
        self.open = {"name": "Market Open", "label_ar": "السوق مفتوح", "is_live": True, "now_ny": "2026-11-16 10:00:00"}

    def snap(self, scan):
        with mock.patch.object(mr, "_scan", scan), mock.patch.object(mr, "get_market_session", return_value=self.open):
            return mr.snapshot()

    def test_filters_orders_and_limits(self):
        rows = [raw("NASDAQ:GOOD"), raw("OTC:OTCX"), raw("NYSE:LATE", change=15.0), raw("AMEX:WEAK", **{"high|15": 10.2}),
                raw("NYSE:BEST", relative_volume_10d_calc=6.0), raw("NASDAQ:NOPX", close=None)]
        d = self.snap(lambda extra: rows)
        self.assertTrue(d["ok"])
        # OTC خارج البورصات، LATE متأخر، WEAK بلا اختراق (35 نقطة)، NOPX بلا سعر
        self.assertEqual([s["name"] for s in d["stocks"]], ["BEST", "GOOD"])
        s = d["stocks"][0]
        self.assertEqual((s["symbol"], s["tv_link"]), ("NYSE:BEST", "https://www.tradingview.com/chart/?symbol=NYSE:BEST"))
        self.assertEqual((s["setup"], s["pass_breakout"], s["update_mode"]), ("اختراق مبكر محتمل", True, "delayed_streaming_900"))
        self.assertIn("value", s["momentum"])
        self.assertEqual((d["count"], d["matched"]), (2, 2))

    def test_top_results_limit(self):
        d = self.snap(lambda extra: [raw(f"NASDAQ:S{i}") for i in range(12)])
        self.assertEqual((d["count"], d["matched"]), (mr.TOP_RESULTS, 12))

    def test_rejected_momentum_fields_fall_back_to_basic_scan(self):
        calls = []

        def scan(extra):
            calls.append(len(extra))
            if extra:
                raise RuntimeError("Unknown field")
            return [{k: v for k, v in raw().items() if k in mr.BASE_FIELDS or k == "ticker"}]
        d = self.snap(scan)
        self.assertEqual(calls, [len(mr.MOMENTUM_FIELDS), 0])
        self.assertEqual(d["stocks"][0]["momentum"]["label"], "بيانات غير كافية")

    def test_failure_reports_error_then_keeps_last_good_result(self):
        def boom(extra):
            raise ConnectionError("no network")
        d = self.snap(boom)
        self.assertEqual((d["ok"], d["stocks"]), (False, []))
        self.assertIn("تعذر", d["error"])
        good = self.snap(lambda extra: [raw()])
        mr._cache["at"] = 0  # انتهت مدة التخزين
        d = self.snap(boom)
        self.assertEqual((d["stocks"], d["stale"]), (good["stocks"], True))

    def test_result_is_cached_between_refreshes(self):
        scan = mock.Mock(return_value=[raw()])
        self.snap(scan)
        self.snap(scan)
        self.assertEqual(scan.call_count, 1)


class TestRoutes(unittest.TestCase):
    def setUp(self):
        import app as platform
        self.client = platform.app.test_client()

    def test_radar_page_uses_platform_layout_and_api_route(self):
        html = self.client.get("/radar").get_data(as_text=True)
        self.assertIn("/api/momentum-radar", html)
        self.assertNotIn("<iframe", html)
        self.assertIn('href="/radar" class="active"', html)   # زر الرادار في شريط التنقل ومفعّل
        self.assertIn('href="/breakout"', html)
        self.assertLess(html.index('href="/breakout"'), html.index('href="/radar"'))  # بعد «قبل الانطلاق» (يساره)

    def test_api_returns_the_snapshot(self):
        with mock.patch.object(mr, "snapshot", return_value={"ok": True, "stocks": [], "count": 0}):
            r = self.client.get("/api/momentum-radar")
        self.assertEqual((r.status_code, r.get_json()["ok"]), (200, True))


if __name__ == "__main__":
    unittest.main()
