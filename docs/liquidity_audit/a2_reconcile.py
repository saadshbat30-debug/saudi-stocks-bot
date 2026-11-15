"""بعد الإصلاح 2 (volfix.clean_family: المطابقة بين الفواصل) على بيانات ياهو المحفوظة، مقابل الفلتر القديم.

الحقيقة المرجعية لشموع الدقيقة (t7): دقيقة حجمها > 1.05× أصغر شمعة أكبر تحتويها (5 دقائق سليمة أو ساعة) مستحيلة.
الفلتر الجديد أشد من هذا التعريف (يشترط دليلين)، فما لا يلتقطه منها يُعدّ «غير قابل للتحقق» لا خطأ صامتًا.
مقياس مستقل عن التعريف: مجموع اليوم بعد التنظيف ÷ مجموع شموع الساعة الخام (عائلة أخرى من بيانات ياهو)."""
from common import *
import old_filter, volfix, sys
from t7_score import bound
pd.set_option("display.width", 220)
IVS = {"1m": "1m", "5m": "5m", "15m": "15m", "30m": "30m", "1h": "60m"}
def family(s, since=None):
    raw = {}
    for iv, y in IVS.items():
        df = load(s, y)
        if since: df = df[df.index.date >= since]
        raw[iv] = to_bars(df)
    return raw
def score(mutate=None, label="", syms=None, verbose=True):
    """mutate(raw) يعدّل البيانات الخام قبل التنظيف (اختبارات النقص والتوقيت والتأخر)."""
    T = dict(G=0, TP=0, FN=0, FP=0, fp_vol=0.0, derived=0, unknown=0, conflict=0, days_unverified=0, days=0)
    O = dict(TP=0, FN=0, FP=0, fp_vol=0.0)
    real_total = 0.0; days = []; err = []
    for s in syms or [x for x in SYMS if x != "SOC"]:
        m, f, h, d = load(s, "1m"), load(s, "5m"), load(s, "60m"), load(s, "1d")
        first = f.index.date.min(); m = m[m.index.date >= first]
        daily = {ts.date(): int(v) for ts, v in d.Volume.items()}
        raw = family(s, first)
        if mutate: mutate(raw)
        clean, st = volfix.clean_family(raw, daily)
        idx = m.index
        if len(clean["1m"]) != len(idx):      # اختبار حذف شموع: أعد المحاذاة بالوقت
            t = {b["time"]: b for b in clean["1m"]}
            cb = [t.get(int(ts.timestamp()) + 3 * 3600) for ts in idx]
        else: cb = clean["1m"]
        V = pd.Series([b["volume"] if b else 0 for b in cb], index=idx, dtype=float)
        changed = pd.Series([bool(b and (b.get("vol_unknown") or b.get("vol_derived"))) for b in cb], index=idx)
        present = pd.Series([b is not None for b in cb], index=idx)
        reg = pd.Series([session(t) == "reg" for t in idx], index=idx)
        b_, v5ok, _ = bound(m, f, h); G = (m.Volume > 1.05 * b_) & (m.Volume > 0) & reg; sc = b_.notna() & reg & present
        ob = to_bars(m); old_filter.drop_cumulative_volume(ob, daily)
        Fo = pd.Series([bool(x.get("vol_unknown")) for x in ob], index=idx)
        T["G"] += int((G & sc).sum()); T["TP"] += int((G & changed & sc).sum()); T["FN"] += int((G & ~changed & sc).sum())
        T["FP"] += int((~G & changed & sc).sum()); T["fp_vol"] += float((m.Volume - V)[(~G & changed & sc)].clip(lower=0).sum())
        O["TP"] += int((G & Fo & sc).sum()); O["FN"] += int((G & ~Fo & sc).sum()); O["FP"] += int((~G & Fo & sc).sum()); O["fp_vol"] += float(m.Volume[(~G & Fo & sc)].sum())
        for k in ("derived", "unknown", "conflict"): T[k] += st["1m"][k]
        # دقة الحجم المستخرج: مقابل «شمعة 5 دقائق − بقية الدقائق» لا يصلح (هو التعريف نفسه)، فيُقاس على مستوى اليوم
        hr = h[[session(t) == "reg" for t in h.index]]; fr = f[[session(t) == "reg" for t in f.index]]
        for day in sorted(set(idx.date)):
            k = reg & (idx.date == day); r60 = hr.Volume[hr.index.date == day].sum(); r5 = fr.Volume[fr.index.date == day].sum()
            ref = min(r60, r5) if r60 > 0 and r5 > 0 else max(r60, r5)
            if not ref: continue
            T["days"] += 1; real_total += ref
            days.append((s, str(day), int(G[k].sum()), round(m.Volume[k].sum() / ref, 3), round(pd.Series([x["volume"] for x in ob], index=idx)[k].sum() / ref, 3), round(V[k].sum() / ref, 3)))
        T["days_unverified"] += len(st["1m"]["days_unverified"])
    for name, t in (("القديم", O), ("الجديد", T)):
        tp, fn, fp = t["TP"], t["FN"], t["FP"]
        t["التقاط%"] = round(100 * tp / (tp + fn), 1) if tp + fn else None
        t["دقة%"] = round(100 * tp / (tp + fp), 1) if tp + fp else None
        t["حجم حقيقي مفقود%"] = round(100 * t["fp_vol"] / real_total, 3)
    D = pd.DataFrame(days, columns=["sym", "day", "glitch", "raw", "old", "new"])
    if verbose:
        print("==", label)
        print("  القديم:", {k: v for k, v in O.items() if k != "fp_vol"})
        print("  الجديد:", {k: v for k, v in T.items() if k != "fp_vol"})
        g, c = D[D.glitch > 0], D[D.glitch == 0]
        q = lambda x: {k: round(float(v), 3) for k, v in x.describe(percentiles=[.05, .5, .95])[["min", "5%", "50%", "95%", "max"]].items()}
        print(f"  أيام معطوبة ({len(g)}): مجموع الدقيقة ÷ المرجع — خام {q(g.raw)}\n     قديم {q(g.old)}\n     جديد {q(g.new)}")
        print(f"  أيام سليمة ({len(c)}): مسّها القديم {(c.old < c.raw - 1e-9).sum()} · الجديد {(c.new < c.raw - 1e-9).sum()} | معطوبة بقيت >1.15×: قديم {(g.old > 1.15).sum()} جديد {(g.new > 1.15).sum()} | نزلت <0.95×: قديم {(g.old < 0.95).sum()} جديد {(g.new < 0.95).sum()}")
    return T, O, D
if __name__ == "__main__":
    T, O, D = score(label="شموع الدقيقة — بيانات ياهو كما هي (29 سهمًا)")
    D.to_csv(RES + "/a2_days_1m.csv", index=False)
    print(D[(D.glitch > 0) & ((D.new > 1.05) | (D.new < 0.95))].to_string(index=False))
