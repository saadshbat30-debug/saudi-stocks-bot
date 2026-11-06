"""اختبار أداء محرك الماسح الشامل على 1,000 و3,000 و6,000 رمز.

بيانات اختبار مُولَّدة رياضيًا (ليست أسعار سوق): نجاحه يقيس قدرة المحرك على الحساب، ولا يثبت أي شيء عن
تغطية مزود البيانات الحقيقي أو سرعته. python tests/perf_fullscan.py
"""
import os
import sys
import time
import resource

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from fullscan.engine import Engine  # noqa: E402
from fullscan.store import Store  # noqa: E402

STEP, N_BARS = 300, 300
START = 1793975400  # 2026-11-06 09:30 نيويورك


class Synthetic:
    name, label, licensed, live_market_wide, license_note = "synthetic", "بيانات اختبار", False, False, "اختبار"
    grace, batch_size, min_gap = 5, 10**9, 0

    def __init__(self):
        self.n_bars = N_BARS

    def supported(self, iv):
        return True, None

    def bars(self, s):
        k = int(s[1:])
        out, x = [], 50 + k % 200
        for i in range(self.n_bars):
            x *= 1 + ((i * 31 + k * 17) % 13 - 6) / 1000
            out.append({"t": START + i * STEP, "o": x, "h": x * 1.001, "l": x * 0.999, "c": x, "v": 1000.0 + (i * k) % 900})
        return out

    def history(self, symbols, iv):
        return {s: self.bars(s) for s in symbols}, {}, START + self.n_bars * STEP + 10


def _deep_engine_bytes(e):
    """حجم ما يحفظه المحرك في الذاكرة: الشموع المضغوطة والصفوف والمؤشرات."""
    total = 0
    for ts, cs, vs in e.closed.values():
        total += sys.getsizeof(ts) + sys.getsizeof(cs) + sys.getsizeof(vs)
    for r in e.rows.values():
        total += sys.getsizeof(r) + sum(sys.getsizeof(v) for v in r.values())
    for f in e.forming.values():
        total += sys.getsizeof(f) if f else 0
    return total


def run(n):
    p = Synthetic()
    now = [START + N_BARS * STEP + 20]
    e = Engine(p, store=Store(":memory:"), clock=lambda: now[0], sleep=lambda s: None,
               universe_fn=lambda force=False: ([{"symbol": f"S{i:05d}", "name": "", "exchange": "NYSE"} for i in range(n)], {"eligible": n}))
    e.load_universe()
    e.configure({"interval": "5m"})
    data = p.history(e.symbols, "5m")
    t = time.perf_counter()
    e.ingest(*data, e.symbols)
    first = time.perf_counter() - t
    # شمعة جديدة لكل الرموز: إعادة تقييم السوق كله
    p.n_bars += 1
    now[0] += STEP
    data = p.history(e.symbols, "5m")
    t = time.perf_counter()
    e.ingest(*data, e.symbols)
    again = time.perf_counter() - t
    t = time.perf_counter()
    d = e.changes(0)
    ch = time.perf_counter() - t
    peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024  # ذروة العملية (تشمل بيانات الاختبار نفسها)
    import gc
    del data, d
    gc.collect()
    import tracemalloc
    tracemalloc.start()
    held = _deep_engine_bytes(e) / 1e6  # ما يحتفظ به المحرك نفسه بعد تحرير بيانات الإدخال
    tracemalloc.stop()
    c = e.counts()
    print(f"{n:>6} رمز | أول تقييم {first:6.2f} ث | تقييم بعد شمعة جديدة {again:6.2f} ث | قراءة النتائج {ch * 1000:6.1f} م.ث | "
          f"قُيّم {c['evaluated_current']} | مطابق {c['matched']} | ذروة العملية {peak:6.0f} MB | يحتفظ به المحرك {held:5.0f} MB")
    return {"n": n, "first_s": round(first, 2), "new_bar_s": round(again, 2), "changes_ms": round(ch * 1000, 1),
            "evaluated": c["evaluated_current"], "matched": c["matched"], "process_peak_mb": round(peak), "engine_held_mb": round(held)}


if __name__ == "__main__":
    import json
    print("بيانات اختبار مُولَّدة (ليست أسعار سوق) — 300 شمعة لكل رمز، فاصل 5 دقائق")
    if len(sys.argv) > 2:  # حجم واحد في عملية مستقلة (حتى لا تتراكم الذاكرة بين الأحجام)
        print(json.dumps(run(int(sys.argv[2]))))
        sys.exit(0)
    import subprocess
    res = [json.loads(subprocess.run([sys.executable, "-W", "ignore", __file__, "-", str(n)], capture_output=True, text=True).stdout.strip().splitlines()[-1])
           for n in (1000, 3000, 6000)]
    for r in res:
        print(r)
    if len(sys.argv) > 1 and sys.argv[1] != "-":
        with open(sys.argv[1], "w") as f:
            json.dump(res, f, indent=1)
