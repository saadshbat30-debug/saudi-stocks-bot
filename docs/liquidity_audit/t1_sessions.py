from common import *
rows=[]
for s in SYMS:
    m=load(s,"1m"); d=load(s,"1d")
    dv={ts.date():v for ts,v in d.Volume.items()}
    m["sess"]=[session(t) for t in m.index]; m["day"]=m.index.date
    for day,g in m.groupby("day"):
        if day not in dv: continue
        v=g.groupby("sess").Volume.sum()
        b1600=g[g.index.time==T(16,0)].Volume.sum()
        rows.append((s,str(day),dv[day],int(v.get("pre",0)),int(v.get("reg",0)),int(v.get("post",0)),int(b1600)))
df=pd.DataFrame(rows,columns="sym day daily pre reg post bar1600".split())
df["reg/daily"]=(df.reg/df.daily).round(3); df["(reg+b1600)/daily"]=((df.reg+df.bar1600)/df.daily).round(3)
df["ext/reg"]=((df.pre+df.post)/df.reg).round(3)
df.to_csv(RES + "/t1_sessions.csv",index=False)
print(df[df.sym.isin(["TSLA","NVDA","CRVO","ANVS","F"])].tail(14).to_string(index=False))
print("\nكل الأسهم/الأيام:",len(df))
print(df[["reg/daily","(reg+b1600)/daily","ext/reg"]].describe().round(3).loc[["min","25%","50%","75%","max"]])
print("أيام مجموع جلستها الرسمية > 1.15× اليومي:", (df["reg/daily"]>1.15).sum())
print("أيام فيها حجم قبل الافتتاح >0:", (df.pre>0).sum(), " بعد الإغلاق >0:", (df.post>0).sum(), " ما عدا شمعة 16:00:", ((df.post-df.bar1600)>0).sum())
