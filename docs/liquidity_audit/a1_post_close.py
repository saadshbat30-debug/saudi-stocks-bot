"""بعد الإصلاح 1 (volfix.fix_post_close_total): شمعة ما بعد الإغلاق التي تحمل حجم اليوم، على كل البيانات المحفوظة.
قبل = الفلتر القديم وحده · بعد = الفلتر القديم + الإصلاح. المعادلة نفسها لم تتغير."""
from common import *
import old_filter, volfix
from t9_equation import ladder_net, last_day, M
pd.set_option("display.width", 220)
tot = dict(days=0, post_bars_with_volume=0, fixed=0, expected=0, wrong=0, missed=0, post_volume_kept=0, post_volume_removed=0)
rows = []
for s in SYMS:
    m, d = load(s, "1m"), load(s, "1d")
    daily = {ts.date(): int(v) for ts, v in d.Volume.items()}
    bars = to_bars(m); old_filter.drop_cumulative_volume(bars, daily)
    before = [b["volume"] for b in bars]
    volfix.fix_post_close_total(bars, daily)
    after = pd.Series([b["volume"] for b in bars], index=m.index); before = pd.Series(before, index=m.index)
    why = pd.Series([b.get("vol_why") == "post_total" for b in bars], index=m.index)
    post = pd.Series([session(t) == "post" for t in m.index], index=m.index)
    for day, g in m.groupby(m.index.date):
        if not daily.get(day): continue
        k = m.index.date == day; tot["days"] += 1
        exp = post[k] & (before[k] > 0.5 * daily[day])           # تعريف التدقيق (t10): شمعة بعد الإغلاق > نصف حجم اليوم
        tot["post_bars_with_volume"] += int((post[k] & (before[k] > 0)).sum())
        tot["fixed"] += int(why[k].sum()); tot["expected"] += int(exp.sum())
        tot["wrong"] += int((why[k] & ~exp).sum()); tot["missed"] += int((exp & ~why[k]).sum())
        tot["post_volume_kept"] += int(after[k][post[k]].sum()); tot["post_volume_removed"] += int((before[k] - after[k])[post[k]].sum())
        if why[k].any():
            sub = lambda vol: [dict(b, volume=int(v)) for b, v in zip(to_bars(m[k]), vol[k])]
            b0, b1 = sub(before), sub(after); reg = [x for x, ts in zip(b1, m.index[k]) if session(ts) != "post"]
            rows.append((s, str(day), daily[day], int(before[k].sum()), int(after[k].sum()), M(ladder_net(b0)), M(ladder_net(b1)), M(ladder_net(reg))))
print(tot)
R = pd.DataFrame(rows, columns=["sym", "day", "daily", "مجموع الشموع قبل", "بعد", "صافي الضغط قبل", "بعد", "الجلسة الرسمية وحدها"])
R.to_csv(RES + "/a1_post_close.csv", index=False); print(R.to_string(index=False))
