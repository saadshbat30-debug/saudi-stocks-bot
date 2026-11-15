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
