"""تصحيح أحجام شموع ياهو داخل اليوم — دوال صافية بلا اتصال (قابلة للاختبار على بيانات محفوظة).

ياهو يرسل أحيانًا شمعة حجمها هو حجم اليوم التراكمي بدل حجمها. القاعدة هنا: لا يُمس حجم إلا بدليلين مستقلين،
والحجم الذي لا يمكن استخراجه من بيانات ياهو نفسها يُعلَّم «غير معروف» (volume=0, vol_unknown=True) ولا يُقدَّر.
الأوقات في الشموع مزاحة لتوقيت الرياض (+3 ساعات) كما يرسلها market.get_chart.

القياسات في docs/liquidity_audit (30 سهمًا، 5–13 نوفمبر 2026).
"""
from datetime import datetime
from zoneinfo import ZoneInfo

NY = ZoneInfo("America/New_York")
RIYADH_OFFSET = 3 * 3600


def ny_time(bar):
    return datetime.fromtimestamp(bar["time"] - RIYADH_OFFSET, NY)


def session_of(t):
    """pre قبل 9:30 · reg الجلسة الرسمية · post من 16:00 (شمعة 16:00 فيها مزاد الإغلاق)."""
    hm = (t.hour, t.minute)
    return "pre" if hm < (9, 30) else "reg" if hm < (16, 0) else "post"


def mark_unknown(bar, why):
    bar["volume"], bar["vol_unknown"], bar["vol_why"] = 0, True, why


def by_day(bars):
    days = {}
    for b in bars:
        t = ny_time(b)
        days.setdefault(t.date(), []).append((t, b))
    return days


def fix_post_close_total(bars, daily):
    """شمعة بعد الإغلاق تحمل حجم اليوم كله (احتساب مزدوج).

    قِيس: في 14 من 210 يومًا (F كل يوم، NIO، LFTO) تأتي شمعة بين 16:00 و16:04 حجمها 0.88–0.99× حجم اليوم،
    فيتضاعف مجموع اليوم وينقلب صافي الضغط (F 2026-11-10: +$334.7M معها مقابل −$23.3M بدونها).

    تُعلَّم «غير معروف» فقط بدليلين معًا:
      1. حجمها وحدها ≥ نصف حجم اليوم المرجعي (الشمعة اليومية)، و
      2. مجموع الجلسة الرسمية + حجمها > 1.15× حجم اليوم (اليوم لا يتسع لها حسابيًا).
    بقية شموع التداول الممتد تبقى كما هي. بلا مرجع يومي لا يُمس شيء، وتُعدّ الحالة «غير قابلة للتحقق».

    daily: {تاريخ نيويورك: حجم اليوم}. يرجع {"fixed": عدد المعلَّمة، "unverifiable": عدد المشتبه بها بلا مرجع}.
    """
    out = {"fixed": 0, "unverifiable": 0}
    for d, items in by_day(bars).items():
        regular = sum(b["volume"] for t, b in items if session_of(t) == "reg")
        real = (daily or {}).get(d)
        for t, b in items:
            if session_of(t) != "post" or not b["volume"]:
                continue
            v = b["volume"]
            if real:
                if v >= 0.5 * real and regular + v > 1.15 * real:
                    mark_unknown(b, "post_total")
                    out["fixed"] += 1
            elif regular and v >= 0.8 * regular:  # تشبه الحجم التراكمي لكن لا مرجع يثبته
                out["unverifiable"] += 1
    return out


# ===== المطابقة بين الفواصل =====
# شمعة الفاصل الأصغر («الابنة») تقع داخل شمعة فاصل أكبر («الأم») من ياهو نفسه. مجموع البنات لا يمكن أن يتجاوز الأم.
# لا تُعدّ الأم صحيحة لأنها الأصغر: نقص البيانات واختلاف التوقيت وتأخر التحديث تجعل أحد المصدرين أقل من الآخر
# دون عطب. لذلك لا يُغيَّر حجم إلا بدليلين:
#   (أ) حسابي: الزيادة على الأم يفسّرها حجم شمعة واحدة (الزيادة ≥ نصف حجمها) — لا فرق صغير بين مصدرين.
#   (ب) بصمة الحجم التراكمي: حجمها ≈ مجموع اليوم حتى لحظتها (0.7–1.3×، قِيس الوسيط 0.99×).
# الدليل (أ) وحده = «تعارض غير قابل للتحقق»: يُعدّ ولا يُغيَّر الحجم.
# وقبل ذلك شرط اتساق المصدرين: إن لم تتطابق البنات مع الأم (±2%) في نصف نوافذ اليوم على الأقل فالمصدران مختلفان
# (توقيت مزاح، مصدر متأخر، تغطية مختلفة)، فلا يُطابَق ذلك اليوم أصلًا ويُعدّ «غير متسق».

def _step(bars):
    """أشيع فرق زمني بين شمعتين متتاليتين = طول الفاصل."""
    diffs = {}
    for a, b in zip(bars, bars[1:]):
        d = b["time"] - a["time"]
        if d > 0:
            diffs[d] = diffs.get(d, 0) + 1
    return max(diffs, key=diffs.get) if diffs else 0


def _windows(parents):
    """[(بداية، نهاية، الشمعة الأم)]: النهاية = بداية التالية أو بداية + طول الفاصل، أيهما أقرب (شمعة 9:00–9:30 مثلًا)."""
    step = _step(parents)
    out = []
    for i, p in enumerate(parents):
        end = p["time"] + step
        if i + 1 < len(parents):
            end = min(end, parents[i + 1]["time"])
        out.append((p["time"], end, p))
    return out


def _usable(b):
    return b["volume"] > 0 and not b.get("vol_unknown") and not b.get("vol_suspect")


def _slices(children, parents):
    """لكل شمعة أم في الجلسة الرسمية: (وقتها بنيويورك، البداية، النهاية، الأم، بناتها)."""
    kids = [(ny_time(c), c) for c in children]
    j = 0
    for start, end, p in _windows(parents):
        while j < len(kids) and kids[j][1]["time"] < start:
            j += 1
        k = j
        while k < len(kids) and kids[k][1]["time"] < end:
            k += 1
        t = ny_time(p)
        if session_of(t) == "reg":
            yield t, start, end, p, [c for ct, c in kids[j:k] if session_of(ct) == "reg"]
        j = k


def inconsistent_days(children, parents, settled_before=None, candidate=None, strict=False):
    """أيام لا يتطابق فيها المصدران: أقل من نصف نوافذها القابلة للمقارنة مجموع بناتها = الأم ±2%.

    النوافذ المرشحة للعطب (candidate) لا تدخل في الحكم: العطب فرق كبير باتجاه واحد، أما المصدران المختلفان
    (توقيت مزاح، تأخر، تغطية أخرى) فيختلفان في أغلب النوافذ. اليوم الذي فيه أقل من 3 نوافذ غير مرشحة يُحكم عليه
    باتساق السلسلة كلها؛ وإن لم تكفِ السلسلة (أقل من 4) فهو غير متسق عند strict (دليل ضعيف) ومتسق بدونه.
    """
    seen, all_n, all_ok = {}, 0, 0
    for t, start, end, p, window in _slices(children, parents):
        if p["volume"] <= 0 or not window or (settled_before is not None and end > settled_before):
            continue
        total = sum(c["volume"] for c in window)
        n = seen.setdefault(t.date(), [0, 0])
        if candidate and candidate(total, p["volume"], window):
            continue
        ok = abs(total - p["volume"]) <= 0.02 * p["volume"] + 1
        n[0] += 1
        n[1] += ok
        all_n += 1
        all_ok += ok
    series_bad = all_ok < 0.5 * all_n if all_n >= 4 else strict
    return {d for d, (n, ok) in seen.items() if (ok < 0.5 * n if n >= 3 else series_bad)}


def _day_regular_sum(bars):
    out = {}
    for d, items in by_day(bars).items():
        out[d] = sum(b["volume"] for t, b in items if session_of(t) == "reg")
    return out


def reconcile_children(children, parents, daily=None, settled_before=None, label="parent"):
    """يطابق شموع الفاصل الأصغر (children) بشموع الفاصل الأكبر (parents) في الجلسة الرسمية.

    الشمعة المعطوبة بالدليلين: حجمها يُستخرج من بيانات ياهو = الأم − بقية أخواتها (vol_derived=اسم المصدر) إن كانت
    الوحيدة في نافذتها، وإلا «غير معروف». settled_before: لا تُفحص نافذة تنتهي بعده (الشمعة الجارية وتأخر تحديث
    أحد المصدرين عن الآخر). daily غير مستعمل هنا (الدليلان من الشموع نفسها) ويبقى لتوحيد التوقيع.
    يرجع {"derived","unknown","conflict","inconsistent_days"}.
    """
    out = {"derived": 0, "unknown": 0, "conflict": 0, "inconsistent_days": 0}
    if not children or not parents:
        return out
    skip = inconsistent_days(children, parents, settled_before, lambda total, pv, w: total > 1.05 * pv + 1
                             and total - pv >= 0.5 * max(c["volume"] for c in w))
    out["inconsistent_days"] = len(skip)
    cum, cum_day = 0, None
    for t, start, end, p, window in _slices(children, parents):
        if t.date() != cum_day:
            cum_day, cum = t.date(), 0
        if not _usable(p):
            continue
        pv = p["volume"]
        if t.date() in skip or (settled_before is not None and end > settled_before):
            cum += pv
            continue
        live = [c for c in window if _usable(c)]
        total = sum(c["volume"] for c in live)
        flagged = []
        while live and total > 1.05 * pv + 1:
            m = max(live, key=lambda c: c["volume"])
            v = m["volume"]
            # فرق بين المصدرين لا تفسّره شمعة واحدة، أو أكبر الشموع صُحح حجمها من مصدر آخر: ليس عطبًا تراكميًا
            if total - pv < 0.5 * v or m.get("vol_derived"):
                break
            if not 0.7 <= v / (cum + pv) <= 1.3:
                out["conflict"] += 1
                m["vol_conflict"] = True
                break
            flagged.append(m)
            live.remove(m)
            total -= v
        if len(flagged) == 1 and total <= 1.05 * pv + 1:
            m = flagged[0]
            m["volume"], m["vol_derived"] = max(0, int(round(pv - total))), label
            out["derived"] += 1
        else:
            for m in flagged:
                mark_unknown(m, "cumulative")
                out["unknown"] += 1
        cum += pv
    return out


def reconcile_parent(parents, children, daily=None, settled_before=None, label="children"):
    """يفحص شموع الفاصل الأكبر بمجموع بناتها (للساعة التي لا أمّ داخل اليوم لها).

    شمعة الساعة تُستبدل بمجموع بناتها (vol_derived) فقط إذا اجتمع: المصدران متسقان في ذلك اليوم، وحجمها > 1.5×
    مجموع البنات، واليوم كله يتجاوز حجمه المرجعي بأكثر من 15%، والزيادة بحجم تراكمي (≥ ربع مجموع ما قبلها في
    اليوم)، والبنات تغطي النافذة من أولها إلى آخرها. غير ذلك لا يُمس: مجموع البنات الأصغر قد يكون نقص بيانات لا
    دليل عطب. تُعلَّم vol_suspect إن كانت أكبر من 1.5× بناتها دون بقية الأدلة حتى لا تُستعمل أمًّا لغيرها.
    يرجع {"derived","conflict","inconsistent_days"}.
    """
    out = {"derived": 0, "conflict": 0, "inconsistent_days": 0}
    if not parents or not children:
        return out
    skip = inconsistent_days(children, parents, settled_before, lambda total, pv, w: pv > 1.5 * total, strict=True)
    out["inconsistent_days"] = len(skip)
    day_sum = _day_regular_sum(parents)
    cstep = _step(children)
    cum, cum_day = 0, None
    for t, start, end, p, window in _slices(children, parents):
        if t.date() != cum_day:
            cum_day, cum = t.date(), 0
        if p["volume"] <= 0 or p.get("vol_unknown"):
            continue
        pv = p["volume"]
        if settled_before is not None and end > settled_before:
            cum += pv
            continue
        live = [c for c in window if _usable(c)]
        total = sum(c["volume"] for c in live)
        if live and pv > 1.5 * total:
            covered = (live[0]["time"] - start <= 2 * cstep and end - live[-1]["time"] <= 3 * cstep
                       and not any(c.get("vol_unknown") or c.get("vol_conflict") for c in window))
            real = (daily or {}).get(t.date())
            excess_day = bool(real) and day_sum.get(t.date(), 0) > 1.15 * real
            if t.date() not in skip and covered and excess_day and pv - total >= 0.25 * cum:
                p["volume"], p["vol_derived"] = int(total), label
                out["derived"] += 1
                pv = total
            else:
                p["vol_suspect"] = True
                out["conflict"] += 1
        cum += pv
    return out


def unverified_days(bars, daily):
    """أيام بقي مجموع جلستها الرسمية أكبر من 1.15× حجمها المرجعي بعد المطابقة: لم يوجد مصدر يثبت أي شمعة."""
    return sorted(d for d, s in _day_regular_sum(bars).items() if (daily or {}).get(d) and s > 1.15 * daily[d])


def clean_family(raw, daily=None, settled_before=None):
    """ينظف فواصل سهم واحد معًا. raw: {"1m","5m","15m","30m","1h"} ← شموع ياهو الخام (ما توفر منها)؛ لا تُعدَّل.

    الترتيب: الساعة تُفحص بمجموع شموع 5 دقائق، ثم 5/15/30 دقيقة تُطابق بالساعة، ثم الدقيقة بـ5 دقائق ثم بالساعة
    (حين تتعطل الدقيقة وشمعة 5 دقائقها في النافذة نفسها). ثم شمعة ما بعد الإغلاق الحاملة لحجم اليوم.
    يرجع ({الفاصل: شموع منظفة}, {الفاصل: إحصاءات}).
    """
    bars = {iv: [dict(b) for b in v] for iv, v in raw.items() if v}
    stats = {iv: {"derived": 0, "unknown": 0, "conflict": 0, "inconsistent_days": 0, "post_total": 0, "unverifiable": 0}
             for iv in bars}

    def add(iv, r):
        for k, v in r.items():
            stats[iv][k] = stats[iv].get(k, 0) + v

    hour, five = bars.get("1h"), bars.get("5m")
    if hour and five:
        add("1h", reconcile_parent(hour, raw["5m"], daily, settled_before, "5m"))
    for iv in ("5m", "15m", "30m"):
        if bars.get(iv) and hour:
            add(iv, reconcile_children(bars[iv], hour, daily, settled_before, "1h"))
    if bars.get("1m"):
        if five:
            add("1m", reconcile_children(bars["1m"], five, daily, settled_before, "5m"))
        if hour:
            add("1m", reconcile_children(bars["1m"], hour, daily, settled_before, "1h"))
    for iv, b in bars.items():
        r = fix_post_close_total(b, daily)
        stats[iv]["post_total"] += r["fixed"]
        stats[iv]["unverifiable"] += r["unverifiable"]
        stats[iv]["days_unverified"] = [str(d) for d in unverified_days(b, daily)]
    return bars, stats
