"""فاصل الساعة. (أ) شموع ساعة مبنية بجمع شموع الدقيقة الحقيقية المعطوبة (كما رُصد حيًّا 2026-11-11: شمعة ساعة TSLA 10:30 = 47.7M
= مجموع 4 شموع دقيقة تراكمية + حجم الساعة). الحجم الحقيقي لكل ساعة = شمعة 60 دقيقة الفعلية من ياهو في الأيام السليمة.
(ب) شموع 5 دقائق الحقيقية المعطوبة (5 أيام) ومرجعها شمعة الساعة."""
from common import *
import old_filter
import market, numpy as np
pd.set_option("display.width",220)
def hour_key(idx): return (idx - pd.Timedelta(minutes=30)).floor("60min")+pd.Timedelta(minutes=30)
T2=pd.read_csv(RES + "/t2_intervals.csv")
rows=[]
for s in SYMS:
    if s=="SOC": continue
    m,f,h,d=load(s,"1m"),load(s,"5m"),load(s,"60m"),load(s,"1d")
    truth={ts.date():int(v) for ts,v in d.Volume.items()}
    mr=m[[session(t)=="reg" for t in m.index]]; fr=f[[session(t)=="reg" for t in f.index]]; hr=h[[session(t)=="reg" for t in h.index]]
    sim=mr.groupby(hour_key(mr.index)).agg(Open=("Open","first"),High=("High","max"),Low=("Low","min"),Close=("Close","last"),Volume=("Volume","sum"))
    sim=sim[sim.index.date>=f.index.date.min()]
    bars=to_bars(sim); old_filter.drop_cumulative_volume(bars,truth)
    cur=pd.Series([b["volume"] for b in bars],index=sim.index); fl=pd.Series([bool(b.get("vol_unknown")) for b in bars],index=sim.index)
    s5=fr.Volume.groupby(hour_key(fr.index)).sum().reindex(sim.index)
    true=hr.Volume.reindex(sim.index)
    for day in sorted(set(sim.index.date)):
        t=T2[(T2.sym==s)&(T2.day==str(day))]
        if not len(t) or t["60m"].iloc[0]>1.15 or t["1m"].iloc[0]<=1.15: continue   # يوم: الدقيقة معطوبة والساعة الفعلية سليمة
        k=sim.index.date==day; tv=true[k]
        if tv.isna().any() or tv.sum()==0: continue
        bad_hours=(sim.Volume[k]>1.05*tv)
        prop=sim.Volume[k].where(~bad_hours, s5[k])   # البديل: الساعة المعطوبة تؤخذ من مجموع شموع 5 دقائق الحقيقية
        rows.append(dict(sym=s,day=str(day),hours=int(k.sum()),bad_hours=int(bad_hours.sum()),flagged=int(fl[k].sum()),
            raw=round(sim.Volume[k].sum()/tv.sum(),2), cur=round(cur[k].sum()/tv.sum(),3),
            real_deleted_pct=round(100*tv[fl[k]].sum()/tv.sum(),1), clean_hours_deleted=int((fl[k]&~bad_hours).sum()),
            bad_hours_left=int((bad_hours&~fl[k]).sum()), prop=round(prop.sum()/tv.sum(),3),
            prop_max_hour_err_pct=round(100*((prop-tv).abs()/tv.where(tv>0)).max(),1)))
R=pd.DataFrame(rows); R.to_csv(RES + "/t8_hourly.csv",index=False)
print(R.sort_values("raw").to_string(index=False))
print("\nأيام:",len(R)," ساعات معطوبة:",R.bad_hours.sum()," عُلِّمت:",R.flagged.sum()," ساعات سليمة حُذفت:",R.clean_hours_deleted.sum()," معطوبة بقيت:",R.bad_hours_left.sum())
print("الحالي: حجم حقيقي محذوف من اليوم — وسيط %.1f%% ، متوسط %.1f%% ، أقصى %.1f%%"%(R.real_deleted_pct.median(),R.real_deleted_pct.mean(),R.real_deleted_pct.max()))
print("الحالي: مجموع اليوم ÷ الحقيقي — وسيط %.2f ، أدنى %.2f ، أقصى %.2f"%(R.cur.median(),R.cur.min(),R.cur.max()))
print("البديل: مجموع اليوم ÷ الحقيقي — وسيط %.3f ، أدنى %.3f ، أقصى %.3f ؛ أقصى خطأ ساعة واحدة %.1f%%"%(R.prop.median(),R.prop.min(),R.prop.max(),R.prop_max_hour_err_pct.max()))
