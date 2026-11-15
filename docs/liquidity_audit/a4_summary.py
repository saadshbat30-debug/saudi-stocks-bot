"""ملخص بعد كل الإصلاحات على البيانات المحفوظة: ما غيّره volfix.clean_family في كل فاصل، وصافي الضغط التقديري
قبل (الفلتر القديم + معادلة السلّم القديمة) وبعد (المعادلة الموحدة) لكل سهم في آخر يوم."""
from common import *
import old_filter, volfix, market
from a2_reconcile import family
pd.set_option("display.width", 250)
tot = {}; rows = []
def last_day(bars):
    d = (bars[-1]["time"] - 3 * 3600 - 4 * 3600) // 86400
    return [b for b in bars if (b["time"] - 3 * 3600 - 4 * 3600) // 86400 == d]
def old_ladder(bars):   # السلّم قبل التوحيد: الشمعة بلا مدى 50/50
    n = 0
    for b in bars:
        v = (b["high"] + b["low"] + b["close"]) / 3 * b["volume"]; s = (b["close"] - b["low"]) / (b["high"] - b["low"]) if b["high"] > b["low"] else 0.5
        n += v * (2 * s - 1)
    return n
def old_panel(bars, dayv):  # اللوحة قبل التوحيد: تحجيم إلى VWAP × حجم اليوم
    bv = sum(b["volume"] for b in bars)
    if not bv: return 0
    vwap = sum((b["high"] + b["low"] + b["close"]) / 3 * b["volume"] for b in bars) / bv
    i, o = market._flow(bars); return (i - o) * (vwap * dayv / (i + o)) if i + o else 0
for s in SYMS:
    f = load(s, "5m"); first = f.index.date.min()
    daily = {ts.date(): int(v) for ts, v in load(s, "1d").Volume.items()}
    raw = family(s, first); clean, st = volfix.clean_family(raw, daily)
    for iv, x in st.items():
        t = tot.setdefault(iv, dict(bars=0, derived=0, unknown=0, post_total=0, conflict=0, inconsistent_days=0, days_over_daily=0))
        t["bars"] += len(raw[iv])
        for k in ("derived", "unknown", "post_total", "conflict", "inconsistent_days"): t[k] += x[k]
        t["days_over_daily"] += len(x["days_unverified"])
    ob = [dict(b) for b in raw["1m"]]; old_filter.drop_cumulative_volume(ob, daily)
    r0, o0, n0 = last_day(raw["1m"]), last_day(ob), last_day(clean["1m"])
    day = volfix.ny_time(n0[-1]).date(); dv = daily.get(day, 0)
    i, o = market._flow(n0); new = i - o
    rows.append((s, str(day), dv, sum(b["volume"] for b in r0), sum(b["volume"] for b in o0), sum(b["volume"] for b in n0),
                 round(old_panel(o0, dv)), round(old_ladder(o0)), round(market._flow(o0)[0] - market._flow(o0)[1]), round(new),
                 st["1m"]["derived"], st["1m"]["unknown"] + st["1m"]["post_total"]))
T = pd.DataFrame(tot).T; T.to_csv(RES + "/a4_changes_by_interval.csv"); print(T.to_string())
R = pd.DataFrame(rows, columns=["sym", "آخر يوم", "حجم اليوم", "حجم الشموع خام", "بعد القديم", "بعد الجديد", "اللوحة قبل", "السلّم قبل", "المؤشر السفلي قبل", "الثلاثة بعد", "مستخرجة(كل الأيام)", "غير معروفة"])
R.to_csv(RES + "/a4_before_after.csv", index=False); print(R.to_string(index=False))
d = (R["اللوحة قبل"] - R["السلّم قبل"]).abs() / R["السلّم قبل"].abs().clip(lower=1)
print("قبل: أسهم اختلفت فيها اللوحة عن السلّم بأكثر من 5%:", int((d > .05).sum()), "من", len(R), "| السلّم عن المؤشر السفلي:", int(((R["السلّم قبل"] - R["المؤشر السفلي قبل"]).abs() > 0.05 * R["السلّم قبل"].abs().clip(lower=1)).sum()),
      "| إشارة اللوحة تعاكس السلّم:", int(((R["اللوحة قبل"] > 0) != (R["السلّم قبل"] > 0)).sum()), "| إشارة «بعد» تعاكس «السلّم قبل»:", int(((R["الثلاثة بعد"] > 0) != (R["السلّم قبل"] > 0)).sum()))
