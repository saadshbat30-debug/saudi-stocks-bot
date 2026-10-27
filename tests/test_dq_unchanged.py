"""طبقة جودة البيانات لا تغيّر أي رقم أو ترتيب أو نتيجة فلتر، وتضيف حقولها الصحيحة.

يُشغَّل: py -m unittest discover tests
"""
import json
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import dq_fixture  # noqa: E402

NEW_KEYS = {"px_src", "trade_time", "trade_age", "msg_time", "msg_age", "fetched_time", "fetched_age", "ext_trade_time", "ext_src",
            "dq", "dq_rvol", "float_src", "float_at", "shares_out", "turnover_base", "float_check"}


def _norm(v):
    return json.loads(json.dumps(v, default=str, sort_keys=True))


class TestUnchanged(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with open(os.path.join(HERE, "dq_baseline.json"), encoding="utf-8") as f:
            cls.base = json.load(f)
        _, enriched, filt = dq_fixture.build()
        cls.rows = {r["symbol"]: r for r in enriched}
        cls.order = [r["symbol"] for r in enriched]
        cls.filt = _norm(filt)

    def test_same_symbols_and_order(self):
        self.assertEqual(self.order, [r["symbol"] for r in self.base["enriched"]])

    def test_every_old_field_identical(self):
        for old in self.base["enriched"]:
            new = _norm(self.rows[old["symbol"]])
            for k, v in old.items():
                self.assertEqual(new.get(k), v, f"{old['symbol']}.{k} تغيّر")

    def test_only_documented_fields_added(self):
        for old in self.base["enriched"]:
            added = set(self.rows[old["symbol"]]) - set(old)
            self.assertTrue(added <= NEW_KEYS, f"{old['symbol']}: حقول غير موثقة {added - NEW_KEYS}")

    def test_prebreakout_filter_identical(self):
        self.assertEqual(self.filt, self.base["filters"])


class TestNewFields(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        _, enriched, _ = dq_fixture.build()
        cls.r = {r["symbol"]: r for r in enriched}

    def codes(self, s):
        return {f["code"] for f in self.r[s]["dq"]}

    def test_suspicious_moves_flagged(self):
        self.assertTrue({"tiny_value", "odd_prev", "stale_trade"} <= self.codes("FREDF"))
        self.assertIn("no_volume", self.codes("AMRLL"))
        self.assertIn("subpenny", self.codes("RLBD"))

    def test_normal_stocks_not_flagged(self):
        for s in ("AAPL", "SGRY", "BRAI", "NOFL", "ASE1"):
            self.assertEqual(self.r[s]["dq"], [], s)

    def test_rvol_tiny_average(self):
        self.assertIsNotNone(self.r["INNI"]["dq_rvol"])
        self.assertIsNone(self.r["SGRY"]["dq_rvol"])

    def test_price_source_and_times(self):
        a = self.r["AAPL"]
        self.assertEqual(a["px_src"], "stream")
        self.assertEqual(a["trade_time"], int(dq_fixture.NOW - 2))   # وقت الصفقة من ياهو
        self.assertEqual(a["msg_time"], int(dq_fixture.NOW - 1))     # وقت استلام الرسالة
        f = self.r["FREDF"]
        self.assertEqual(f["px_src"], "screener")
        self.assertIsNone(f["msg_time"])
        self.assertEqual(f["trade_age"], 3 * 86400)
        self.assertEqual(self.r["PRE1"]["ext_trade_time"], int(dq_fixture.NOW - 12))

    def test_float_vs_outstanding(self):
        n = self.r["NOFL"]
        self.assertIsNone(n["float"])
        self.assertEqual(n["turnover_base"], "outstanding")
        self.assertEqual(n["shares_out"], 30_000_000)
        self.assertIsNone(n["float_src"])
        x = self.r["XXII"]
        self.assertEqual(x["turnover_base"], "float")
        self.assertEqual(x["float_src"], "Yahoo Finance (floatShares)")
        self.assertEqual(x["float_at"], int(dq_fixture.NOW - 2 * 86400))


class TestFloatAfterSplit(unittest.TestCase):
    def test_classify(self):
        import dq
        self.assertEqual(dq.classify_float(20e6, 1e6, 1 / 20)[0], "consistent")
        self.assertEqual(dq.classify_float(20e6, 20e6, 1 / 20)[0], "unchanged")
        self.assertEqual(dq.classify_float(20e6, 5e6, 1 / 20)[0], "inconsistent")
        self.assertEqual(dq.classify_float(20e6, None, 1 / 20)[0], "missing")

    def test_check_records_separately(self):
        import dq
        now = dq._split_ts("2026-10-26") + 3600
        dq._float_checks.clear()
        splits = [{"symbol": "ZZZ", "date": "2026-10-26", "new": 1, "old": 10},
                  {"symbol": "OLD", "date": "2026-09-01", "new": 1, "old": 10}]          # قديم: يُتجاهل
        meta = {"ZZZ": {"float": 10e6, "at": now - 5 * 86400}, "OLD": {"float": 1e6, "at": now - 90 * 86400}}
        dq.check_splits(splits, meta.get, lambda s: 1.05e6, now=now)
        c = dq._float_checks["ZZZ"]
        self.assertEqual((c["status"], c["used"], c["expected"]), ("consistent", "old", 1_000_000))
        self.assertNotIn("OLD", dq._float_checks)
        self.assertEqual(meta["ZZZ"]["float"], 10e6)  # الرقم المستخدم لم يتغير


if __name__ == "__main__":
    unittest.main()
