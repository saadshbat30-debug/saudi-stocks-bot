import os
import shutil
import tempfile
import unittest
from datetime import datetime, timedelta
from unittest import mock

from sahmk.models import BatchQuotesResponse, Quote

import app
import lab
import market_data


class FakeClient:
    """Three stocks: A gets heavy buying (F1), B is chased (F2), C quietly sold (F3), D is the market."""

    def __init__(self):
        self.day = None
        self.prices = {"1111": 10.0, "2222": 20.0, "3333": 30.0, "4444": 40.0}

    def companies(self, market, limit, offset):
        items = [{"symbol": s, "name": "شركة " + s} for s in self.prices] + [{"symbol": "4340", "name": "الراجحي ريت"}]
        return {"results": items[offset:offset + limit], "total": len(items)}

    def _change(self, s):
        return {"1111": 0.5, "2222": 6.0, "3333": 0.2, "4444": 0.0}[s]

    def quotes(self, symbols):
        stamp = self.day.isoformat() + "T14:29:00+03:00"
        net = {"1111": 3_000_000, "2222": 500_000, "3333": -2_000_000, "4444": 0}
        return BatchQuotesResponse.from_dict({"quotes": [
            {"symbol": s, "name": "شركة " + s, "price": self.prices[s], "change_percent": self._change(s),
             "volume": 1_000_000, "net_liquidity": net[s], "updated_at": stamp} for s in symbols]})

    def quote(self, s):
        liq = {"1111": (7e6, 3e6), "2222": (7e6, 3e6), "3333": (1e6, 3e6), "4444": (1e6, 1e6)}[s]
        return Quote.from_dict({"symbol": s, "name": "شركة " + s, "price": self.prices[s],
                                "change_percent": self._change(s), "updated_at": self.day.isoformat() + "T14:30:00+03:00",
                                "liquidity": {"inflow_value": liq[0], "outflow_value": liq[1],
                                              "inflow_trades": 100, "outflow_trades": 80}})


def riyadh(day, h, m):
    return datetime(day.year, day.month, day.day, h, m, tzinfo=lab.RIYADH)


class LabTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.p = mock.patch.object(lab, "DATA_DIR", self.dir)
        self.p.start()

    def tearDown(self):
        self.p.stop()
        shutil.rmtree(self.dir)

    def run_week(self):
        c = FakeClient()
        day = datetime(2026, 10, 4).date()  # Sunday
        days = []
        while len(days) < 7:
            if lab.is_trading_day(riyadh(day, 12, 0)):
                c.day = day
                for hm in [(10, 5), (14, 30), (15, 20)]:
                    lab.tick(c, riyadh(day, *hm))
                days.append(day)
                # after the first day: A and C drift as the filters expect, market flat
                c.prices["1111"] *= 1.02
                c.prices["3333"] *= 0.98
            day += timedelta(days=1)
        return c

    def test_universe_excludes_reits(self):
        self.assertTrue(lab.is_excluded("4340", "الراجحي ريت"))
        self.assertTrue(lab.is_excluded("9300", "x"))
        self.assertFalse(lab.is_excluded("1120", "الراجحي"))

    def test_full_week_records_signals_and_measures(self):
        self.run_week()
        signals = lab._read(lab.path("signals.csv"))
        first_day = [s for s in signals if s["date"] == "2026-10-04"]
        self.assertEqual(sorted((s["filter"], s["symbol"]) for s in first_day),
                         [("F1", "1111"), ("F2", "2222"), ("F3", "3333")])
        self.assertEqual(float(first_day[0]["inflow_ratio"]), 70.0)
        # no weekend recording
        self.assertFalse(os.path.exists(lab.path("snapshots", "2026-10-09.csv")))
        res = [r for r in lab.outcomes() if r["date"] == "2026-10-04"]
        f1 = next(r for r in res if r["filter"] == "F1")
        f3 = next(r for r in res if r["filter"] == "F3")
        self.assertGreater(f1["net5"], 0)  # beat the market after costs
        self.assertGreater(f3["net5"], 0)  # underperformed, as F3 expects
        summ = lab.summary()
        self.assertIn("قيد القياس", summ["F1"]["verdict"])
        self.assertGreater(summ["F1"]["h"][1]["n"], 0)

    def test_signals_once_per_day(self):
        c = FakeClient(); c.day = datetime(2026, 10, 4).date()
        lab.tick(c, riyadh(c.day, 14, 30)); lab.tick(c, riyadh(c.day, 14, 45))
        self.assertEqual(len([s for s in lab._read(lab.path("signals.csv")) if s["filter"] == "F1"]), 1)

    def test_lab_page_renders(self):
        self.run_week()
        with mock.patch.dict(os.environ, {"SAHMK_API_KEY": "x"}):
            html = app.app.test_client().get("/lab").get_data(as_text=True)
        self.assertIn("تجميع هادئ", html)
        self.assertIn("قيد القياس", html)
        self.assertIn("1111", html)


if __name__ == "__main__":
    unittest.main()
