"""فلتر المنصة الحالي (old_filter.drop_cumulative_volume، بلا تعديل) على بيانات ياهو الحقيقية المحفوظة، مقابل الحقيقة الأرضية."""
from common import *
import old_filter
from t3_truth import day_tables
import market, copy
def run(filter_fn, label):
    S=dict(days=0, clean_days=0, glitch_days=0, G=0, TP=0, FN=0, FP=0, FP_clean_days=0, FP_days=0, fp_vol=0.0, real_vol=0.0, days_fixed=0, days_still_bad=0)
    detail=[]
    bysym={}
    for rec in day_tables(): bysym.setdefault(rec[0],[]).append(rec)
    for s,recs in bysym.items():
        m=load(s,"1m"); bars=to_bars(m)
        d=load(s,"1d"); truth={ts.date():int(v) for ts,v in d.Volume.items()}
        raw=[b["volume"] for b in bars]
        filter_fn(bars, truth)
        flagged=pd.Series([bool(b.get("vol_unknown")) for b in bars], index=m.index)
        after=pd.Series([b["volume"] for b in bars], index=m.index)
        for _,day,daily,m1,f5,clean5 in recs:
            if not clean5: continue
            reg=pd.Series([session(t)=="reg" for t in m1.index], index=m1.index)
            v5=f5.Volume.reindex(m1.index.floor("5min")).values
            g=pd.Series((m1.Volume.values>1.05*v5)&(m1.Volume.values>0)&reg.values&~pd.isna(v5), index=m1.index)
            # النوع التراكمي فقط (≥ نصف حجم نافذة الـ5 دقائق × 5 على الأقل): شمعة أكبر من ضعف نافذتها
            g=g&(m1.Volume>2*pd.Series(v5,index=m1.index))
            f=flagged.reindex(m1.index)
            S["days"]+=1; n=int(g.sum()); S["G"]+=n
            tp=int((g&f).sum()); fn=int((g&~f).sum()); fp=int((~g&f).sum())
            S["TP"]+=tp; S["FN"]+=fn; S["FP"]+=fp
            real5=f5[[session(t)=="reg" for t in f5.index]].Volume.sum()
            S["fp_vol"]+=float(m1.Volume[(~g&f)].sum()); S["real_vol"]+=float(real5)
            if n: S["glitch_days"]+=1
            else: S["clean_days"]+=1; S["FP_clean_days"]+=fp
            S["FP_days"]+= fp>0
            a=after.reindex(m1.index)[reg].sum()
            if n:
                ok = a<=1.15*daily
                S["days_fixed"]+=ok; S["days_still_bad"]+=(not ok)
            detail.append((s,str(day),n,tp,fn,fp,round(m1.Volume[reg].sum()/real5,2),round(a/real5,3)))
    S["recall"]=round(S["TP"]/S["G"],4) if S["G"] else None
    S["precision"]=round(S["TP"]/(S["TP"]+S["FP"]),4) if S["TP"]+S["FP"] else None
    S["fp_vol_share_of_real"]=round(S["fp_vol"]/S["real_vol"],6)
    print("==",label); print({k:(round(v,4) if isinstance(v,float) else v) for k,v in S.items()})
    return pd.DataFrame(detail,columns=["sym","day","glitch","TP","FN","FP","1m/5m قبل","1m/5m بعد"])
if __name__=="__main__":
    df=run(old_filter.drop_cumulative_volume,"الفلتر الحالي — دقيقة واحدة — بيانات حقيقية")
    df.to_csv(RES + "/t4_filter_real.csv",index=False)
    pd.set_option("display.width",200)
    print(df[(df.FN>0)|(df.FP>0)].to_string(index=False))
    x=df[df.glitch>0]; print("أيام معطوبة: وسيط 1m/5m قبل",x["1m/5m قبل"].median(),"بعد",x["1m/5m بعد"].median(),"أدنى بعد",x["1m/5m بعد"].min(),"أقصى بعد",x["1m/5m بعد"].max())
