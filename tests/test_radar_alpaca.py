"""DATA_SOURCE=alpaca: رادار الزخم من Alpaca، والإغلاق السابق من Alpaca، والماسح الشامل بمزود Alpaca.

python -m unittest tests.test_radar_alpaca
"""
import os
import sys
import time
import unittest
from unittest import mock

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import alpaca_daily as ad  # noqa: E402
import alpaca_stream as al  # noqa: E402
import market  # noqa: E402
import momentum_radar as mr  # noqa: E402
import radar_alpaca as ra  # noqa: E402

CLOSES = [10 + 0.3 * ((i * 7) % 11) - 0.01 * i for i in range(70)]


class TestIndicators(unittest.TestCase):
    """مقارنة بحساب pandas المرجعي (ewm بنفس البداية: متوسط بسيط لأول n قيم)."""

    def ref_ema(self, values, n):
        s = pd.Series(values[n - 1:], dtype=float)
        s.iloc[0] = sum(values[:n]) / n
        return s.ewm(span=n, adjust=False).mean().iloc[-1]

    def test_ema_and_macd_match_reference(self):
        self.assertAlmostEqual(ra.ema(CLOSES, 20), self.ref_ema(CLOSES, 20), places=9)
        self.assertAlmostEqual(ra.ema(CLOSES, 50), self.ref_ema(CLOSES, 50), places=9)
        line, sig = ra.macd(CLOSES)
        e12 = pd.Series(CLOSES[11:], dtype=float); e12.iloc[0] = sum(CLOSES[:12]) / 12
        e12 = e12.ewm(span=12, adjust=False).mean().iloc[14:].reset_index(drop=True)
        e26 = pd.Series(CLOSES[25:], dtype=float); e26.iloc[0] = sum(CLOSES[:26]) / 26
        e26 = e26.ewm(span=26, adjust=False).mean().reset_index(drop=True)
        ml = (e12 - e26)
        self.assertAlmostEqual(line, ml.iloc[-1], places=9)
        self.assertAlmostEqual(sig, self.ref_ema(list(ml[1:]), 9), places=9)
        self.assertEqual(ra.macd(CLOSES[:30]), (None, None))
        self.assertIsNone(ra.ema(CLOSES[:10], 20))


class TestScan(unittest.TestCase):
    def rows(self):
        base = {"otc": False, "src": "alpaca", "exchange": "NMS", "market_cap": 1e8}
        return [{**base, "symbol": "GOOD", "price": 10.0, "change_pct": 5.0, "volume": 3_000_000},
                {**base, "symbol": "YHOO", "price": 10.0, "change_pct": 5.0, "volume": 3_000_000, "src": "yahoo"},  # ليس من Alpaca
                {**base, "symbol": "OTCX", "price": 10.0, "change_pct": 5.0, "volume": 3_000_000, "otc": True},
                {**base, "symbol": "LOWV", "price": 10.0, "change_pct": 5.0, "volume": 900_000},                    # حجم نسبي 0.9
                {**base, "symbol": "DOWN", "price": 10.0, "change_pct": -2.0, "volume": 3_000_000}]

    def test_fields_match_tradingview_names(self):
        now = 1_800_000_000
        hist = {s: {"closes": CLOSES, "vols": [1_000_000] * 70} for s in ("GOOD", "YHOO", "OTCX", "LOWV", "DOWN")}
        bars = {"GOOD": [(now - 61 * 60, 9.0, 9.0), (now - 16 * 60, 9.5, 9.5), (now - 6 * 60, 9.8, 9.8), (now - 120, 10.2, 9.9)]}
        c = mock.Mock()
        c.live.return_value = {"vwap": 9.7}
        with mock.patch.object(ad, "_hist", hist), mock.patch.object(ra, "minute_bars", return_value=bars) as mb, \
                mock.patch.object(al, "config", return_value={}), mock.patch.object(al, "client", return_value=c):
            out = ra.scan(0.5, 50, 1.0, 100_000, 1.5, rows=self.rows(), now=now)
        self.assertEqual([r["name"] for r in out], ["GOOD"])
        self.assertEqual(mb.call_args.args[0], ["GOOD"])  # شموع الدقيقة للمرشحين فقط
        r = out[0]
        self.assertEqual((r["ticker"], r["exchange"], r["update_mode"], r["VWAP"]), ("NASDAQ:GOOD", "NASDAQ", "alpaca_sip", 9.7))
        self.assertAlmostEqual(r["relative_volume_10d_calc"], 3.0)
        self.assertAlmostEqual(r["change|5"], (10 / 9.8 - 1) * 100)    # آخر شمعة انتهت قبل 5 دقائق
        self.assertAlmostEqual(r["change|15"], (10 / 9.5 - 1) * 100)
        self.assertAlmostEqual(r["change|60"], (10 / 9.0 - 1) * 100)
        self.assertEqual(r["high|15"], 10.2)
        self.assertIsNotNone(r["RSI"]); self.assertIsNotNone(r["MACD.macd"]); self.assertIsNotNone(r["EMA50"])

    def test_radar_uses_alpaca_and_keeps_its_rules(self):
        raw = [{"ticker": "NASDAQ:GOOD", "name": "GOOD", "exchange": "NASDAQ", "close": 10.0, "change": 5.0, "volume": 2_000_000,
                "relative_volume_10d_calc": 3.0, "market_cap_basic": 1e8, "sector": None, "update_mode": "alpaca_sip",
                "high|15": 10.05, "change|5": 0.9, "change|15": 1.5, "change|60": 2.5, "RSI": 65.0, "MACD.macd": 0.2,
                "MACD.signal": 0.1, "EMA20": 9.5, "EMA50": 9.0, "VWAP": 9.8}]
        mr._cache.update(at=0, data=None)
        open_ = {"name": "Market Open", "label_ar": "السوق مفتوح", "is_live": True, "now_ny": "2026-11-18 10:00:00"}
        with mock.patch.object(al, "source_is_alpaca", return_value=True), mock.patch.object(ra, "scan", return_value=raw) as sc, \
                mock.patch.object(mr, "_scan") as tv, mock.patch.object(mr, "get_market_session", return_value=open_):
            d = mr.snapshot()
        tv.assert_not_called()
        self.assertEqual(sc.call_args.args, (mr.MIN_PRICE, mr.MAX_PRICE, mr.MIN_CHANGE_PCT, mr.MIN_VOLUME, mr.MIN_RVOL))
        self.assertEqual((d["source"], d["stocks"][0]["setup"], d["stocks"][0]["update_mode"]), ("Alpaca (SIP)", "اختراق مبكر محتمل", "alpaca_sip"))
        mr._cache.update(at=0, data=None)


class TestPrevCloseAndFullscan(unittest.TestCase):
    def test_prev_close_from_alpaca_history(self):
        r = {"symbol": "AAPL", "price": 100.0, "change": 0, "change_pct": 0, "prev_close": 90.0, "volume": 1, "day_high": 1, "day_low": 1}
        c = mock.Mock()
        c.live.return_value = {"price": 105.0, "at": time.time(), "kind": "trade"}
        with mock.patch.object(al, "source_is_alpaca", return_value=True), mock.patch.object(al, "client", return_value=c), \
                mock.patch.object(ad, "_hist", {"AAPL": {"closes": [99.0, 100.0], "vols": [1, 1]}}):
            market._apply_alpaca(r, time.time(), 0)
        self.assertEqual((r["prev_close"], round(r["change_pct"], 2)), (100.0, 5.0))  # لا 90 من ياهو

    def test_fullscan_defaults_to_alpaca_provider(self):
        from fullscan import providers
        with mock.patch.dict(os.environ, {"DATA_SOURCE": "alpaca", "APCA_API_KEY_ID": "k", "APCA_API_SECRET_KEY": "s"}, clear=True), \
                mock.patch.object(al, "load_env"):
            self.assertEqual(providers.make().name, "alpaca")
        with mock.patch.dict(os.environ, {}, clear=True), mock.patch.object(al, "load_env"):
            self.assertEqual(providers.make().name, "yahoo")


if __name__ == "__main__":
    unittest.main()


class TestFunnel(unittest.TestCase):
    def test_counts_each_condition(self):
        now = 1_800_000_000
        base = {"otc": False, "src": "alpaca", "exchange": "NMS"}
        rows = [{**base, "symbol": "GOOD", "price": 10.0, "change_pct": 5.0, "volume": 3_000_000},
                {**base, "symbol": "NOHI", "price": 10.0, "change_pct": 5.0, "volume": 3_000_000},
                {**base, "symbol": "CHEAP", "price": 0.1, "change_pct": 5.0, "volume": 3_000_000},
                {**base, "symbol": "FLAT", "price": 10.0, "change_pct": 0.0, "volume": 3_000_000},
                {**base, "symbol": "YHOO", "price": 10.0, "change_pct": 5.0, "volume": 3_000_000, "src": "yahoo"}]
        hist = {s: {"closes": CLOSES, "vols": [1_000_000] * 70} for s in ("GOOD", "CHEAP", "FLAT", "YHOO")}
        with mock.patch.object(ad, "get", side_effect=hist.get), \
                mock.patch.object(ra, "minute_bars", return_value={"GOOD": [(now - 120, 10.0, 10.0)]}), \
                mock.patch.object(al, "config", return_value={}), mock.patch.object(al, "client", return_value=None):
            out = ra.scan(0.5, 50, 1.0, 100_000, 1.5, rows=rows, now=now)
        self.assertEqual([r["name"] for r in out], ["GOOD"])
        self.assertEqual(ra.FUNNEL, {"rows": 5, "alpaca_listed": 4, "with_history": 3, "price": 2, "change": 1,
                                     "volume": 1, "rvol": 1, "with_minute_bars": 1})
