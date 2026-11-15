"""البديل لفاصل الساعة حين تكون شمعة الساعة نفسها معطوبة: حجم الساعة = أصغر المصادر الثلاثة من ياهو
(شمعة الساعة، مجموع شموع 5 دقائق، مجموع شموع الدقيقة بعد استبعاد الدقائق المستحيلة مقابل 5 دقائق فقط).
العطب يضخّم دائمًا ولا ينقص، فالأصغر هو الأقرب للحقيقي. شمعة الساعة الفعلية من ياهو تُستعمل للحكم فقط لا للحساب."""
from common import *
import numpy as np
pd.set_option("display.width",220)
def hour_key(idx): return (idx - pd.Timedelta(minutes=30)).floor("60min")+pd.Timedelta(minutes=30)
T2=pd.read_csv(RES + "/t2_intervals.csv"); rows=[]; herr=[]
for s in SYMS:
    if s=="SOC": continue
    m,f,h=load(s,"1m"),load(s,"5m"),load(s,"60m")
    mr=m[[session(t)=="reg" for t in m.index]]; fr=f[[session(t)=="reg" for t in f.index]]; hr=h[[session(t)=="reg" for t in h.index]]
    mr=mr[mr.index.date>=f.index.date.min()]
    v5=pd.Series(fr.Volume.reindex(mr.index.floor("5min")).values,index=mr.index)
    flag=(mr.Volume>1.05*v5)&(v5>0)                      # دقيقة أكبر من شمعة 5 دقائقها
    v1=mr.Volume.where(~flag,0.0)
    others=v1.groupby(mr.index.floor("5min")).transform("sum"); n=flag.groupby(mr.index.floor("5min")).transform("sum")
    fix=flag&(n==1)&(v5-others>=0); v1[fix]=(v5-others)[fix]
    sim=mr.Volume.groupby(hour_key(mr.index)).sum()       # شمعة ساعة معطوبة (مجموع الدقيقة الخام)
    s1=v1.groupby(hour_key(mr.index)).sum(); s5=fr.Volume.groupby(hour_key(fr.index)).sum().reindex(sim.index)
    est=pd.concat([sim,s5.where(s5>0),s1.where(s1>0)],axis=1).min(axis=1)
    true=hr.Volume.reindex(sim.index)
    for day in sorted(set(sim.index.date)):
        t=T2[(T2.sym==s)&(T2.day==str(day))]
        if not len(t) or t["60m"].iloc[0]>1.15: continue
        k=sim.index.date==day; tv=true[k]
        if tv.isna().any() or tv.sum()==0: continue
        e=((est[k]-tv)/tv.where(tv>0)); herr+=list(e.dropna())
        rows.append((s,str(day),bool(t["1m"].iloc[0]>1.15),bool(t["5m"].iloc[0]>1.15),round(sim[k].sum()/tv.sum(),2),round(est[k].sum()/tv.sum(),3)))
R=pd.DataFrame(rows,columns=["sym","day","bad1m","bad5m","raw","est"]); R.to_csv(RES + "/t8b_hourly_min.csv",index=False)
for name,g in [("الدقيقة معطوبة",R[R.bad1m]),("الدقيقة و5 دقائق معطوبتان",R[R.bad1m&R.bad5m]),("سليمة",R[~R.bad1m&~R.bad5m])]:
    print(f"{name}: أيام {len(g)} | قبل: وسيط {g.raw.median():.2f} أقصى {g.raw.max():.2f} | بعد: أدنى {g.est.min():.3f} وسيط {g.est.median():.3f} أقصى {g.est.max():.3f} | أيام خارج ±5%: {((g.est-1).abs()>0.05).sum()}")
print(R[(R.est-1).abs()>0.05].to_string(index=False))
e=pd.Series(herr); print("خطأ الساعة الواحدة (كل الساعات %d): ضمن ±1%%: %.1f%% ، ضمن ±5%%: %.1f%% ، أقل من −5%%: %.1f%% ، أكثر من +5%%: %.1f%%"%(len(e),100*(e.abs()<=.01).mean(),100*(e.abs()<=.05).mean(),100*(e<-.05).mean(),100*(e>.05).mean()))
