"""حقيقة أرضية على مستوى الشمعة من بيانات ياهو نفسها: دقيقة حجمها أكبر من حجم شمعة الـ5 دقائق التي تحتويها مستحيلة.
تُستخدم فقط الأيام التي شموع 5 دقائقها سليمة (مجموعها ≤1.05× اليومي ويساوي مجموع 15 دقيقة)."""
from common import *
import market, json
def day_tables():
    out=[]
    for s in SYMS:
        d=load(s,"1d"); dv={ts.date():v for ts,v in d.Volume.items()}
        m=load(s,"1m"); f=load(s,"5m"); q=load(s,"15m")
        for day in sorted(set(f.index.date)):
            if day not in dv or not dv[day]: continue
            f5=f[(f.index.date==day)]; f5r=f5[[session(t)=="reg" for t in f5.index]]
            q15=q[(q.index.date==day)]; q15r=q15[[session(t)=="reg" for t in q15.index]]
            m1=m[m.index.date==day]
            if not len(m1) or not len(f5r): continue
            clean5 = f5r.Volume.sum()<=1.05*dv[day] and abs(f5r.Volume.sum()-q15r.Volume.sum())<=0.001*max(1,f5r.Volume.sum())
            out.append((s,day,dv[day],m1,f5,clean5))
    return out
if __name__=="__main__":
    tot=dict(days=0,clean5=0,gdays=0,glitch=0,bars=0); ratios=[]; per=[]
    for s,day,daily,m1,f5,clean5 in day_tables():
        tot["days"]+=1
        if not clean5: continue
        tot["clean5"]+=1
        win=m1.index.floor("5min"); v5=f5.Volume.reindex(win).values
        reg=[session(t)=="reg" for t in m1.index]
        g=(m1.Volume.values>1.05*v5)&(m1.Volume.values>0)&reg&~pd.isna(v5)
        tot["bars"]+=sum(reg); n=int(g.sum()); tot["glitch"]+=n; tot["gdays"]+=n>0
        # نسبة حجم الشمعة المعطوبة إلى المجموع الحقيقي قبلها (من شموع 5 دقائق السليمة)
        cum5=f5[[session(t)=="reg" for t in f5.index]].Volume.cumsum()
        for ts,v in m1.Volume[g].items():
            prior=cum5[cum5.index<=ts.floor("5min")]
            c=float(prior.iloc[-1]) if len(prior) else 0
            if c: ratios.append(v/c)
        per.append((s,str(day),n,round(m1.Volume[reg].sum()/daily,2)))
    print(tot)
    r=pd.Series(ratios); print("حجم الشمعة المعطوبة ÷ المجموع الحقيقي حتى نهاية نافذتها (5د):"); print(r.describe(percentiles=[.05,.25,.5,.75,.95]).round(3).to_string())
    p=pd.DataFrame(per,columns=["sym","day","glitch_bars","1m/daily"]); print(p[p.glitch_bars>0].sort_values("glitch_bars").tail(12).to_string(index=False))
    print("توزيع عدد الشموع المعطوبة في اليوم:", p.glitch_bars.describe().round(1).to_dict())
