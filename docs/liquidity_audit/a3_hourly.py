"""فاصل الساعة بعد الإصلاح. شموع الساعة المعطوبة تُبنى بجمع شموع الدقيقة الحقيقية المعطوبة (كما رُصد حيًّا 2026-11-11)،
والحجم الحقيقي = شمعة الساعة الفعلية من ياهو. قبل = الفلتر القديم (يحذف) · بعد = volfix.reconcile_parent (يستخرج من
شموع 5 دقائق بشروطه). تُعرض أيضًا شموع الساعة الفعلية (السليمة) للتأكد من أنها لا تُمس."""
from common import *
import old_filter, volfix
pd.set_option("display.width", 220)
def hour_key(idx): return (idx - pd.Timedelta(minutes=30)).floor("60min") + pd.Timedelta(minutes=30)
T2 = pd.read_csv(RES + "/t2_intervals.csv"); rows = []; untouched = dict(hours=0, changed=0)
for s in SYMS:
    if s == "SOC": continue
    m, f, h, d = load(s, "1m"), load(s, "5m"), load(s, "60m"), load(s, "1d")
    daily = {ts.date(): int(v) for ts, v in d.Volume.items()}
    first = f.index.date.min()
    mr = m[[session(t) == "reg" for t in m.index]]; mr = mr[mr.index.date >= first]
    sim = mr.groupby(hour_key(mr.index)).agg(Open=("Open", "first"), High=("High", "max"), Low=("Low", "min"), Close=("Close", "last"), Volume=("Volume", "sum"))
    hr = h[[session(t) == "reg" for t in h.index]]; true = hr.Volume.reindex(sim.index)
    ob = to_bars(sim); old_filter.drop_cumulative_volume(ob, daily)
    old = pd.Series([b["volume"] for b in ob], index=sim.index); oldflag = pd.Series([bool(b.get("vol_unknown")) for b in ob], index=sim.index)
    nb = to_bars(sim); st = volfix.reconcile_parent(nb, to_bars(f), daily, None, "5m")
    new = pd.Series([b["volume"] for b in nb], index=sim.index); newch = pd.Series([bool(b.get("vol_derived")) for b in nb], index=sim.index)
    sus = pd.Series([bool(b.get("vol_suspect")) for b in nb], index=sim.index)
    # شموع الساعة الفعلية: يجب ألا تتغير
    ab = to_bars(h[h.index.date >= first]); volfix.reconcile_parent(ab, to_bars(f), daily, None, "5m")
    untouched["hours"] += len(ab); untouched["changed"] += sum(1 for b in ab if b.get("vol_derived"))
    for day in sorted(set(sim.index.date)):
        t = T2[(T2.sym == s) & (T2.day == str(day))]
        if not len(t) or t["60m"].iloc[0] > 1.15: continue
        k = sim.index.date == day; tv = true[k]
        if tv.isna().any() or tv.sum() == 0: continue
        bad = sim.Volume[k] > 1.05 * tv
        rows.append(dict(sym=s, day=str(day), bad_hours=int(bad.sum()), raw=round(sim.Volume[k].sum() / tv.sum(), 2),
            old=round(old[k].sum() / tv.sum(), 3), old_real_lost_pct=round(100 * tv[oldflag[k]].sum() / tv.sum(), 1), old_clean_deleted=int((oldflag[k] & ~bad).sum()), old_left=int((bad & ~oldflag[k]).sum()),
            new=round(new[k].sum() / tv.sum(), 3), new_fixed=int((newch[k] & bad).sum()), new_wrong=int((newch[k] & ~bad).sum()), new_left=int((bad & ~newch[k]).sum()),
            new_left_marked=int((bad & ~newch[k] & sus[k]).sum()),
            new_real_lost_pct=round(100 * (tv - new[k]).clip(lower=0)[newch[k]].sum() / tv.sum(), 2)))
R = pd.DataFrame(rows); R.to_csv(RES + "/a3_hourly.csv", index=False)
g = R[R.bad_hours > 0]; c = R[R.bad_hours == 0]
print(f"أيام فيها ساعات معطوبة: {len(g)} · ساعات معطوبة: {g.bad_hours.sum()} | أيام سليمة: {len(c)}")
print(f"القديم: التقط {g.bad_hours.sum() - g.old_left.sum()} · ترك {g.old_left.sum()} · حذف ساعات سليمة {R.old_clean_deleted.sum()} · حجم حقيقي مفقود: وسيط {g.old_real_lost_pct.median()}% أقصى {g.old_real_lost_pct.max()}% · مجموع اليوم ÷ الحقيقي: {g.old.min()}–{g.old.max()}")
print(f"الجديد: صحّح {g.new_fixed.sum()} · ترك {g.new_left.sum()} (منها معلَّمة مشتبهًا بها {g.new_left_marked.sum()}) · غيّر ساعات سليمة {R.new_wrong.sum()} · حجم حقيقي مفقود: وسيط {g.new_real_lost_pct.median()}% أقصى {g.new_real_lost_pct.max()}% · مجموع اليوم ÷ الحقيقي: {g.new.min()}–{g.new.max()} (وسيط {g.new.median()})")
print(f"أيام بقيت >1.05× بعد الجديد: {(g.new > 1.05).sum()} | نزلت <0.95×: {(g.new < 0.95).sum()}")
print(g[(g.new > 1.05) | (g.new < 0.95)][["sym", "day", "bad_hours", "raw", "old", "new", "new_left", "new_left_marked"]].to_string(index=False))
print("شموع الساعة الفعلية من ياهو:", untouched)
