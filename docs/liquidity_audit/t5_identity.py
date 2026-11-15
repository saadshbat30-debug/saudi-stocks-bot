"""هل مجموع شموع الدقيقة داخل نافذة = شمعة الفاصل الأكبر في بيانات ياهو السليمة؟ (شرط لإعادة بناء الأحجام من فاصل آخر)"""
from common import *
import numpy as np
pd.set_option("display.width",200)
def agg(fine, rule):  # مجموع الفاصل الأصغر داخل نوافذ الفاصل الأكبر (الجلسة الرسمية، نوافذ تبدأ 9:30)
    r=fine[[session(t)=="reg" for t in fine.index]]
    key=r.index.floor(rule) if rule!="60min" else (r.index - pd.Timedelta(minutes=30)).floor("60min")+pd.Timedelta(minutes=30)
    return r.Volume.groupby(key).sum()
res={}
for a,b,rule in [("1m","5m","5min"),("5m","15m","15min"),("5m","30m","30min"),("5m","60m","60min"),("1m","60m","60min")]:
    diffs=[]
    for s in SYMS:
        fa,co=load(s,a),load(s,b)
        x=agg(fa,rule); y=co[[session(t)=="reg" for t in co.index]].Volume
        j=pd.concat([x,y],axis=1,keys=["fine","coarse"]).dropna()
        j=j[j.index.date!=j.index.date.max()] if False else j
        j=j[j.coarse>0]
        diffs.append((j.fine/j.coarse).rename(s))
    r=pd.concat(diffs)
    res[f"Σ{a}/{b}"]=dict(n=len(r), exact=round((np.abs(r-1)<1e-9).mean(),4), within1pct=round((np.abs(r-1)<0.01).mean(),4), gt105=round((r>1.05).mean(),4), lt095=round((r<0.95).mean(),4), median=round(r.median(),4))
print(pd.DataFrame(res).T.to_string())
# أيام عطب كل فاصل: هل يوجد دائمًا فاصل سليم؟
t=pd.read_csv(RES + "/t2_intervals.csv"); t=t.dropna()
t["bad1"]=t["1m"]>1.15; t["bad5"]=t["5m"]>1.15; t["bad60"]=t["60m"]>1.15
print("\nأيام (150):", t[["bad1","bad5","bad60"]].sum().to_dict(), " 5m و60m معطوبان معًا:", (t.bad5&t.bad60).sum(), " الثلاثة معًا:", (t.bad1&t.bad5&t.bad60).sum())
print(t[t.bad5|t.bad60].to_string(index=False))
