"""تفاصيل كل حالة اختلف فيها الفلتر الجديد عن الحقيقة المرجعية (إيجابيات وسلبيات كاذبة) وتعارضاته."""
from common import *
import volfix
from t7_score import bound
from a2_reconcile import family
pd.set_option("display.width", 250)
rows = []
for s in [x for x in SYMS if x != "SOC"]:
    m, f, h, d = load(s, "1m"), load(s, "5m"), load(s, "60m"), load(s, "1d")
    first = f.index.date.min(); m = m[m.index.date >= first]
    daily = {ts.date(): int(v) for ts, v in d.Volume.items()}
    clean, st = volfix.clean_family(family(s, first), daily)
    cb = clean["1m"]; idx = m.index
    reg = pd.Series([session(t) == "reg" for t in idx], index=idx)
    b_, v5ok, v60 = bound(m, f, h); G = (m.Volume > 1.05 * b_) & (m.Volume > 0) & reg; sc = b_.notna() & reg
    v5 = pd.Series(f.Volume.reindex(idx.floor("5min")).values, index=idx)
    s5 = m.Volume.groupby(idx.floor("5min")).transform("sum")
    for i, ts in enumerate(idx):
        b = cb[i]; ch = bool(b.get("vol_unknown") or b.get("vol_derived"))
        kind = "FP" if (ch and not G[ts] and sc[ts]) else "FN" if (G[ts] and not ch and sc[ts]) else "تعارض" if b.get("vol_conflict") else None
        if kind:
            rows.append((kind, s, str(ts)[:16], int(m.Volume[ts]), b["volume"], b.get("vol_derived") or ("unknown" if b.get("vol_unknown") else ""),
                         int(v5[ts]) if v5[ts] == v5[ts] else -1, int(s5[ts]), int(v60[ts]) if v60[ts] == v60[ts] else -1))
R = pd.DataFrame(rows, columns=["نوع", "sym", "time", "حجم الدقيقة الخام", "بعد", "المصدر", "شمعة 5د", "مجموع دقائق النافذة", "شمعة الساعة"])
R.to_csv(RES + "/a2b_cases.csv", index=False)
print(R.to_string(index=False)); print(R["نوع"].value_counts().to_dict())
