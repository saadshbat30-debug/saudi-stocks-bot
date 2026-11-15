"""شموع ما بعد الإغلاق التي تحمل حجم اليوم كله (احتساب مزدوج): البيانات الخام وأثرها على اللوحة والسلّم، والفلتر الحالي لا يمسها."""
from common import *
import old_filter
import market
from t9_equation import panel, ladder_net, last_day, M
pd.set_option("display.width",220)
n_days=0; hits=[]
for s in SYMS:
    m,d=load(s,"1m"),load(s,"1d"); dv={ts.date():int(v) for ts,v in d.Volume.items()}
    for day,g in m.groupby(m.index.date):
        if day not in dv or not dv[day]: continue
        n_days+=1
        p=g[[session(t)=="post" for t in g.index]]
        big=p[p.Volume>0.5*dv[day]]
        for ts,r in big.iterrows(): hits.append((s,str(ts)[:16],int(r.Volume),dv[day],round(r.Volume/dv[day],3),r.Open,r.High,r.Low,r.Close))
H=pd.DataFrame(hits,columns=["sym","time","bar_volume","daily_volume","ratio","O","H","L","C"]); H.to_csv(RES + "/t10_post.csv",index=False)
print(f"أيام مفحوصة {n_days} | شموع بعد الإغلاق حجمها > نصف حجم اليوم: {len(H)} في {H[['sym']].assign(d=H.time.str[:10]).drop_duplicates().shape[0]} يوم")
print(H.head(12).to_string(index=False)); print("نسبة حجم الشمعة إلى حجم اليوم:", H.ratio.describe().round(3)[["min","50%","max"]].to_dict(), "| عند 16:00 تمامًا:", (H.time.str[11:]=="16:00").sum())
for s,day in [("F","2026-11-10"),("NIO","2026-11-13")]:
    m,d=load(s,"1m"),load(s,"1d"); m=m[[str(t.date())<=day for t in m.index]]
    dayv=int(d.Volume[[str(t.date())==day for t in d.index]].iloc[0])
    bars=to_bars(m); n=old_filter.drop_cumulative_volume(bars,{ts.date():int(v) for ts,v in d.Volume.items()})
    b=last_day(bars); reg=[x for x,ts in zip(b,m.index[-len(b):]) if session(ts)!="post"]
    for name,x in (("كما تحسبه المنصة الآن (بعد الفلتر الحالي)",b),("الجلسة الرسمية فقط (بلا شموع بعد الإغلاق)",reg)):
        p=panel(x,dayv); print(f"{s} {day} | {name}: مجموع حجم الشموع {p['bars_volume']:,} (اليومي {dayv:,}) | السلّم {M(ladder_net(x))} | اللوحة صافي {M(p['net'])} داخلة {p['inflow_pct']}% تحجيم {p['scale']}")
    print("   شموع علّمها الفلتر الحالي في هذا السهم:", n)
