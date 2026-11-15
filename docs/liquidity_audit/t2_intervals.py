from common import *
rows=[]
for s in SYMS:
    d=load(s,"1d"); dv={ts.date():v for ts,v in d.Volume.items()}
    rec={}
    for iv in ["1m","5m","15m","30m","60m"]:
        m=load(s,iv); m=m[[session(t)=="reg" for t in m.index]]
        for day,g in m.groupby(m.index.date):
            rec.setdefault(day,{})[iv]=g.Volume.sum()
    for day,r in rec.items():
        if day in dv and dv[day]>0: rows.append((s,str(day),dv[day],*[round(r.get(iv,float("nan"))/dv[day],2) for iv in ["1m","5m","15m","30m","60m"]]))
df=pd.DataFrame(rows,columns=["sym","day","daily","1m","5m","15m","30m","60m"])
df.to_csv(RES + "/t2_intervals.csv",index=False)
pd.set_option("display.width",200)
print(df[df.sym.isin(["TSLA","NVDA","AAPL","CRVO","ANVS","F","GME"])].to_string(index=False))
for iv in ["1m","5m","15m","30m","60m"]:
    x=df[iv].dropna(); print(iv,"أيام:",len(x),"  >1.15×:",(x>1.15).sum(),"  وسيط:",x.median(),"  أقصى:",x.max())
