"""متانة المطابقة بين الفواصل: نقص البيانات، اختلاف التوقيت، تأخر أحد المصدرين، مرجع يومي قديم أو غائب.
كل اختبار يفسد بيانات ياهو المحفوظة عمدًا ثم يقارن ما غيّره الفلتر بما غيّره على البيانات الأصلية:
  زائدة = شموع غُيّر حجمها بسبب الإفساد وحده (إيجابيات كاذبة مستحدثة) · فائتة = شموع معطوبة لم تعد تُلتقط."""
from common import *
import volfix, random, copy
from a2_reconcile import family
pd.set_option("display.width", 220)
S = [x for x in SYMS if x != "SOC"]
FAM, DAILY = {}, {}
for s in S:
    f = load(s, "5m"); FAM[s] = family(s, f.index.date.min())
    DAILY[s] = {ts.date(): int(v) for ts, v in load(s, "1d").Volume.items()}
for fam in FAM.values():      # معرّف ثابت لكل شمعة (وقتها الأصلي) لا يتأثر بالإزاحة والحذف
    for bs in fam.values():
        for b in bs: b["_id"] = b["time"]
incons = [0]
def reg(b): return volfix.session_of(volfix.ny_time(b)) == "reg"
def run(mut=None, daily_mut=None, settle=False):
    changed, vol_removed, conf, real = {}, 0, 0, 0
    incons[0] = 0
    for s in S:
        raw = copy.deepcopy(FAM[s]); daily = dict(DAILY[s])
        if mut: mut(raw, random.Random(hash(s) % 1000))
        if daily_mut: daily = daily_mut(daily)
        sb = [b for b in raw["1m"] if reg(b)][-1]["time"] - 120 if settle else None
        clean, st = volfix.clean_family(raw, daily, sb)
        for iv in ("1m", "5m", "1h"):
            for b0, b in zip(raw[iv], clean[iv]):
                if b.get("vol_unknown") or b.get("vol_derived"):
                    changed[(s, iv, b0["_id"])] = b0["volume"] - b["volume"]
            conf += st[iv]["conflict"]; incons[0] += st[iv]["inconsistent_days"]
        real += sum(b["volume"] for b in clean["5m"] if reg(b))
    return changed, conf, real
base, bconf, breal = run()
def report(name, mut=None, daily_mut=None, settle=False, ignore=lambda k: False):
    ch, conf, _ = run(mut, daily_mut, settle)
    extra = {k: v for k, v in ch.items() if k not in base and not ignore(k)}
    missed = [k for k in base if k not in ch]
    n = lambda d, iv: sum(1 for k in d if k[1] == iv)
    print(f"{name:58} زائدة 1m/5m/1h: {n(extra,'1m')}/{n(extra,'5m')}/{n(extra,'1h')}  حجم حُذف بالخطأ: {100*sum(max(0,v) for v in extra.values())/breal:.3f}%  فائتة 1m/5m/1h: {n(missed,'1m')}/{n(missed,'5m')}/{n(missed,'1h')}  تعارضات: {conf}  أيام غير متسقة: {incons[0]}")
    return extra
print(f"الأصل: شموع غُيّر حجمها 1m/5m/1h = {sum(1 for k in base if k[1]=='1m')}/{sum(1 for k in base if k[1]=='5m')}/{sum(1 for k in base if k[1]=='1h')} · تعارضات {bconf}")
def drop(iv, frac):
    def f(raw, r): raw[iv] = [b for b in raw[iv] if not (reg(b) and r.random() < frac)]
    return f
def scale(iv, frac, k):
    def f(raw, r):
        for b in raw[iv]:
            if reg(b) and r.random() < frac: b["volume"] = int(b["volume"] * k)
    return f
def shift(iv, sec):
    def f(raw, r):
        for b in raw[iv]: b["time"] += sec
    return f
def lag_last(iv, k):   # الشمعة الجارية: آخر شمعة في البيانات لم يكتمل حجمها في هذا المصدر (والدقيقة سبقتها)
    def f(raw, r):
        last = [b for b in raw[iv] if reg(b)][-1]; last["volume"] = int(last["volume"] * k)
    return f
def both(*fs):
    def f(raw, r):
        for g in fs: g(raw, r)
    return f
report("1. حذف 10% من شموع 5 دقائق (نقص في الأم)", drop("5m", .10))
report("2. حذف 10% من شموع الدقيقة (نقص في البنات)", drop("1m", .10))
report("3. حذف 20% من شموع الساعة", drop("1h", .20))
report("4. 20% من شموع 5 دقائق ناقصة الحجم 30% (مصدر متأخر)", scale("5m", .20, .7))
report("5. 20% من شموع الساعة ناقصة الحجم 30%", scale("1h", .20, .7))
report("6. 20% من شموع الدقيقة ناقصة الحجم 30%", scale("1m", .20, .7))
report("7. شموع 5 دقائق مزاحة دقيقة واحدة (اختلاف توقيت)", shift("5m", 60))
report("8. شموع الدقيقة مزاحة دقيقة واحدة", shift("1m", 60))
report("9. شموع الساعة مزاحة 5 دقائق", shift("1h", 300))
report("10. الشمعة الجارية: آخر شمعة 5د وساعة حجمها 20%", both(lag_last("5m", .2), lag_last("1h", .2)))
report("11. مثل 10 مع تجاهل النافذة غير المستقرة (settled_before)", both(lag_last("5m", .2), lag_last("1h", .2)), settle=True)
report("12. المرجع اليومي قديم (70% من الحقيقي)", daily_mut=lambda d: {k: int(v * .7) for k, v in d.items()})
report("13. المرجع اليومي غائب", daily_mut=lambda d: {})
report("14. المرجع اليومي مضخّم ×3 (لا يظهر أي يوم زائدًا)", daily_mut=lambda d: {k: v * 3 for k, v in d.items()})
