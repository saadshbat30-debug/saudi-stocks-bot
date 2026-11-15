from common import *
import old_filter
import market
pd.set_option("display.width",220)
# (أ) SOC: كل الفواصل ≈1.9× الشمعة اليومية — عطب أم مرجع يومي ناقص؟
s="SOC"; d=load(s,"1d"); truth={ts.date():int(v) for ts,v in d.Volume.items()}
for iv in ["1m","5m","60m"]:
    m=load(s,iv); bars=to_bars(m); raw=sum(b["volume"] for b in bars)
    n=old_filter.drop_cumulative_volume(bars,truth); aft=sum(b["volume"] for b in bars)
    big=m.Volume.max(); print(f"SOC {iv}: شموع {len(bars)}  معلَّمة {n}  الحجم قبل {raw:,}  بعد {aft:,}  المحذوف {100*(raw-aft)/raw:.1f}%")
m=load(s,"1m"); f=load(s,"5m")
x=m[[session(t)=="reg" for t in m.index]]; w=x.Volume.groupby(x.index.floor("5min")).sum()
j=pd.concat([w,f.Volume],axis=1,keys=["s1","v5"]).dropna(); j=j[j.v5>0]
print("SOC: نوافذ 5د يتطابق فيها Σ1m مع 5m تمامًا:", round(((j.s1-j.v5).abs()<1).mean(),3), " أكبر شمعة دقيقة ÷ حجم اليوم:", round(m.Volume.max()/d.Volume.iloc[-1],3))
print(d.Volume.tail(5).to_string())
# (ب) أسباب أخطاء الفلتر الحالي على الدقيقة
from t3_truth import day_tables
rows=[]
bysym={}
for rec in day_tables(): bysym.setdefault(rec[0],[]).append(rec)
for s,recs in bysym.items():
    m=load(s,"1m"); bars=to_bars(m); d=load(s,"1d"); truth={ts.date():int(v) for ts,v in d.Volume.items()}
    old_filter.drop_cumulative_volume(bars,truth); fl=pd.Series([bool(b.get("vol_unknown")) for b in bars],index=m.index)
    for _,day,daily,m1,f5,clean5 in recs:
        if not clean5: continue
        v5=pd.Series(f5.Volume.reindex(m1.index.floor("5min")).values,index=m1.index)
        reg=pd.Series([session(t)=="reg" for t in m1.index],index=m1.index)
        g=(m1.Volume>2*v5)&reg&(m1.Volume>0); f=fl.reindex(m1.index)
        regsum=m1.Volume[reg].sum()
        for ts in m1.index[(g&~f)]: rows.append(("FN",s,str(ts)[:16],int(m1.Volume[ts]),int(v5[ts]),round(regsum/daily,2)))
        for ts in m1.index[(~g&f)]: rows.append(("FP",s,str(ts)[:16],int(m1.Volume[ts]),int(v5[ts]) if v5[ts]==v5[ts] else -1,round(regsum/daily,2)))
e=pd.DataFrame(rows,columns=["نوع","sym","time","vol_1m","vol_5m_window","reg1m/daily"])
e.to_csv(RES + "/t6_errors.csv",index=False)
e["hhmm"]=e.time.str[11:]
print("\nFP حسب الوقت:", e[e["نوع"]=="FP"].hhmm.value_counts().head(6).to_dict())
print("FN: اليوم تحت بوابة 1.15×:", (e[e["نوع"]=="FN"]["reg1m/daily"]<=1.15).sum(), " فوقها:", (e[e["نوع"]=="FN"]["reg1m/daily"]>1.15).sum())
print(e[e["نوع"]=="FN"].head(12).to_string(index=False))
print(e[(e["نوع"]=="FP")&(e.hhmm!="09:30")].head(10).to_string(index=False))
