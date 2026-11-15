"""تقييم الفلتر الحالي والبديل المقترح (مطابقة الفواصل) على بيانات ياهو الحقيقية.
الحقيقة الأرضية (مستقلة عن الشمعة اليومية): شمعة دقيقة حجمها > 1.05× أصغر شمعة أكبر تحتويها (5 دقائق أو ساعة) مستحيلة.
النوافذ التي شمعة 5 دقائقها صفر أو أكبر من ساعتها تُقيَّم بالساعة وحدها. SOC مستبعد (مرجعه متناقض، يُعرض منفصلًا)."""
from common import *
import old_filter
import market, numpy as np
pd.set_option("display.width",220)
EX={"SOC"}
def hour_key(idx): return (idx - pd.Timedelta(minutes=30)).floor("60min")+pd.Timedelta(minutes=30)
def bound(m, f, h):
    """أصغر حجم شمعة أكبر تحتوي كل دقيقة، من مصادر ياهو السليمة."""
    v5=pd.Series(f.Volume.reindex(m.index.floor("5min")).values,index=m.index)
    v60=pd.Series(h.Volume.reindex(hour_key(m.index)).values,index=m.index)
    v5ok=v5.where((v5>0)&((v5<=1.05*v60)|v60.isna()|(v60<=0)))
    return pd.concat([v5ok, v60.where(v60>0)],axis=1).min(axis=1), v5ok, v60
def proposed(m, f, h):
    """البديل: الدقيقة المستحيلة تُعلَّم، وحجمها يُستخرج من بيانات ياهو نفسها = شمعة 5د − بقية دقائق النافذة (إن أمكن)."""
    b,v5ok,v60=bound(m,f,h)
    flag=(m.Volume>1.05*b)&(m.Volume>0)
    vol=m.Volume.astype(float).copy(); vol[flag]=0.0
    rec=pd.Series(False,index=m.index)
    others=vol.groupby(m.index.floor("5min")).transform("sum")
    nflag=flag.groupby(m.index.floor("5min")).transform("sum")
    fix=flag&(nflag==1)&v5ok.notna()&(v5ok-others>=0)
    vol[fix]=(v5ok-others)[fix]; rec[fix]=True
    return flag, vol, rec
tot={k:dict(G=0,TP=0,FN=0,FP=0,fp_vol=0.0) for k in ("current","proposed")}
real_total=0.0; days=[]; nrec=0
for s in SYMS:
    if s in EX: continue
    m,f,h,d=load(s,"1m"),load(s,"5m"),load(s,"60m"),load(s,"1d")
    first=f.index.date.min(); m=m[m.index.date>=first]
    reg=pd.Series([session(t)=="reg" for t in m.index],index=m.index)
    b,_,_=bound(m,f,h); G=(m.Volume>1.05*b)&(m.Volume>0)&reg; scor=b.notna()&reg
    truth={ts.date():int(v) for ts,v in d.Volume.items()}
    bars=to_bars(m); old_filter.drop_cumulative_volume(bars,truth)
    Fc=pd.Series([bool(x.get("vol_unknown")) for x in bars],index=m.index); Vc=pd.Series([x["volume"] for x in bars],index=m.index)
    Fp,Vp,rec=proposed(m,f,h); nrec+=int(rec.sum())
    for k,F in (("current",Fc),("proposed",Fp)):
        t=tot[k]; t["G"]+=int((G&scor).sum()); t["TP"]+=int((G&F&scor).sum()); t["FN"]+=int((G&~F&scor).sum()); t["FP"]+=int((~G&F&scor).sum()); t["fp_vol"]+=float(m.Volume[(~G&F&scor)].sum())
    h60=h[[session(t)=="reg" for t in h.index]]
    for day in sorted(set(m.index.date)):
        r=reg&(m.index.date==day); ref=h60.Volume[h60.index.date==day].sum()
        f5=f[(f.index.date==day)]; ref5=f5[[session(t)=="reg" for t in f5.index]].Volume.sum()
        real=min(ref,ref5) if ref>0 and ref5>0 else max(ref,ref5)
        if not real: continue
        real_total+=real
        days.append((s,str(day),int(G[r].sum()),round(m.Volume[r].sum()/real,3),round(Vc[r].sum()/real,3),round(Vp[r].sum()/real,3)))
for k,t in tot.items():
    t["recall"]=round(t["TP"]/t["G"],4); t["precision"]=round(t["TP"]/(t["TP"]+t["FP"]),4) if t["TP"]+t["FP"] else None
    t["real_vol_deleted_pct"]=round(100*t["fp_vol"]/real_total,3)
print(pd.DataFrame(tot).T.to_string())
print("شموع أعيد استخراج حجمها من شمعة 5د في البديل:", nrec)
D=pd.DataFrame(days,columns=["sym","day","glitch","raw","current","proposed"]); D.to_csv(RES + "/t7_days.csv",index=False)
g=D[D.glitch>0]; c=D[D.glitch==0]
q=lambda x: x.describe(percentiles=[.05,.5,.95]).round(3)[["min","5%","50%","95%","max"]].to_dict()
print(f"\nأيام معطوبة ({len(g)}): مجموع الدقيقة ÷ الحجم المرجعي (أصغر مجموعَي 5د و60د)")
for col in ("raw","current","proposed"): print(" ",col,q(g[col]))
print(f"أيام سليمة ({len(c)}):")
for col in ("raw","current","proposed"): print(" ",col,q(c[col]))
print("أيام سليمة مسّها الفلتر الحالي:", (c.current<c.raw-1e-9).sum(), " البديل:", (c.proposed<c.raw-1e-9).sum())
print("أيام معطوبة بقيت >1.15× بعد الحالي:", (g.current>1.15).sum(), " بعد البديل:", (g.proposed>1.15).sum(), " | نزلت <0.85× بعد الحالي:", (g.current<0.85).sum(), " البديل:", (g.proposed<0.85).sum())
