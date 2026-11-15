"""المعادلة الفعلية لصافي السيولة كما في الكود، على بيانات خام محفوظة، قبل الفلتر وبعده، وبكل فاصل.
اللوحة = market._flow (الكود الفعلي) × معامل تحجيم. السلّم والمؤشر السفلي: نسخ حرفي بالبايثون من JS (templates/stock.html:renderLadder وstatic/indicators.js:sessionCum)."""
from common import *
import old_filter
from t7_score import proposed
import market, copy, sys
pd.set_option("display.width",220)
def last_day(bars):  # market.get_flow
    d=(bars[-1]["time"]-market.RIYADH_OFFSET-4*3600)//86400
    return [b for b in bars if (b["time"]-market.RIYADH_OFFSET-4*3600)//86400==d]
def panel(bars, day_volume):  # market.get_flow حرفيًا
    bv=sum(b["volume"] for b in bars); vwap=sum((b["high"]+b["low"]+b["close"])/3*b["volume"] for b in bars)/bv
    value=vwap*day_volume; i,o=market._flow(bars); scale=value/(i+o)
    return dict(bars_volume=bv, vwap=round(vwap,4), raw_in=i, raw_out=o, raw_net=i-o, value=value, scale=round(scale,4), net=(i-o)*scale, inflow_pct=round(100*i/(i+o),2))
def ladder_net(bars):  # renderLadder: الشمعة بلا مدى = 0.5
    n=0
    for b in bars:
        v=(b["high"]+b["low"]+b["close"])/3*b["volume"]; s=(b["close"]-b["low"])/(b["high"]-b["low"]) if b["high"]>b["low"] else 0.5
        n+=v*(2*s-1)
    return n
def pane_net(bars):  # sessionCum: الشمعة بلا مدى حسب اتجاهها عن إغلاق السابقة (مثل market._flow)
    i,o=market._flow(bars); return i-o
def M(x): return f"{x/1e6:,.2f}M"
for s,day in [(sys.argv[1],sys.argv[2])] if len(sys.argv)>2 else [("TSLA","2026-11-13"),("CRVO","2026-11-13"),("ANVS","2026-11-13")]:
    m,f,h,d=load(s,"1m"),load(s,"5m"),load(s,"60m"),load(s,"1d")
    dayv=int(d.Volume[[str(t.date())==day for t in d.index]].iloc[0]); dclose=d[[str(t.date())==day for t in d.index]].iloc[0]
    m=m[[str(t.date())<=day for t in m.index]]
    raw=last_day(to_bars(m))
    cur_all=to_bars(m); old_filter.drop_cumulative_volume(cur_all,{ts.date():int(v) for ts,v in d.Volume.items()}); cur=last_day(cur_all)
    Fp,Vp,rec=proposed(m,f,h); mp=m.copy(); mp["Volume"]=Vp; prop=last_day(to_bars(mp))
    print(f"\n===== {s} {day} | حجم اليوم (الشمعة اليومية) {dayv:,} | شموع الدقيقة في اليوم {len(raw)} (منها بلا حجم {sum(1 for b in raw if not b['volume'])}) =====")
    for name,b in (("خام (قبل أي فلتر)",raw),("الفلتر الحالي",cur),("البديل المقترح",prop)):
        p=panel(b,dayv)
        print(f"{name:18} مجموع حجم الشموع {p['bars_volume']:>13,} | VWAP {p['vwap']:.3f} | صافي خام {M(p['raw_net']):>12} | معامل التحجيم {p['scale']:.3f} | اللوحة: قيمة {M(p['value'])} صافي {M(p['net'])} داخلة {p['inflow_pct']}% | السلّم {M(ladder_net(b))} | المؤشر السفلي {M(pane_net(b))}")
    # حساسية الفاصل: نفس المعادلة، نفس اليوم، الجلسة الرسمية، بيانات سليمة
    out=[]
    for iv in ("1m","5m","15m","30m","60m","1d"):
        if iv=="1m": x=mp
        else: x=load(s,iv)
        x=x[[str(t.date())==day and (iv=="1d" or session(t)=="reg") for t in x.index]]
        b=to_bars(x); i,o=market._flow(b); tot=i+o
        out.append((iv,len(b),sum(q["volume"] for q in b),round(tot/1e6,2),round((i-o)/1e6,2),round(100*(i-o)/tot,1) if tot else None))
    print(pd.DataFrame(out,columns=["الفاصل","شموع","الحجم","داخلة+خارجة $M","الصافي $M","الصافي ÷ القيمة %"]).to_string(index=False))
    print(f"الشمعة اليومية: O {dclose.Open:.2f} H {dclose.High:.2f} L {dclose.Low:.2f} C {dclose.Close:.2f}")
