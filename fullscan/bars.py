"""توقيت الشموع للماسح الشامل: الجلسة الرسمية الأمريكية، نهاية كل شمعة، الشمعة المغلقة، وتجميع 3 دقائق.

الشمعة: {"t": بدايتها بثواني UTC، "o","h","l","c","v"}. الحساب بتوقيت نيويورك فيُراعى التوقيت الصيفي.
الشموع داخل اليوم للجلسة الرسمية فقط (9:30–16:00 نيويورك) حتى يكون متوسط الحجم من نفس نوع الشموع.
"""
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

NY = ZoneInfo("America/New_York")
OPEN_MIN, CLOSE_MIN = 9 * 60 + 30, 16 * 60
STEP = {"1m": 60, "3m": 180, "5m": 300, "15m": 900, "30m": 1800, "1h": 3600, "1D": 86400}
INTERVALS = list(STEP)
INTRADAY = ("1m", "3m", "5m", "15m", "30m", "1h")


def _ny(t):
    return datetime.fromtimestamp(t, NY)


def session_close(t):
    """إغلاق الجلسة الرسمية (16:00 نيويورك) ليوم الشمعة."""
    d = _ny(t)
    return d.replace(hour=16, minute=0, second=0, microsecond=0).timestamp()


def in_regular(t):
    d = _ny(t)
    m = d.hour * 60 + d.minute
    return d.weekday() < 5 and OPEN_MIN <= m < CLOSE_MIN


def slot_end(t, interval):
    """متى تُغلق الشمعة التي تبدأ عند t. الشمعة اليومية تُغلق 16:00 يومها، وآخر شمعة ساعة (15:30) تُغلق 16:00."""
    if interval == "1D":
        d = _ny(t)
        return datetime(d.year, d.month, d.day, 16, 0, tzinfo=NY).timestamp()
    return min(t + STEP[interval], session_close(t))


def is_closed(bar, interval, asof, grace):
    """مغلقة إذا انتهى وقتها قبل لحظة جلب البيانات بمهلة grace (حتى لا تُعامل شمعة ناقصة كمكتملة)."""
    return slot_end(bar["t"], interval) <= asof - grace


def split(bars, interval, asof, grace):
    """(الشموع المغلقة، الشمعة الجارية أو None) — مرتبة بلا تكرار في الوقت."""
    by_t = {}
    for b in bars:
        if b["t"] > asof:  # شمعة تبدأ بعد لحظة الجلب = بيانات مستقبلية: تُرفض
            continue
        by_t[b["t"]] = b  # آخر قيمة للوقت نفسه (تصحيح المصدر) تحل محل السابقة
    ordered = [by_t[t] for t in sorted(by_t)]
    # نهاية الشمعة تزيد مع وقت بدايتها، فالمغلقة بداية متصلة من القائمة: يكفي فحص الشموع الأخيرة
    n = len(ordered)
    while n and not is_closed(ordered[n - 1], interval, asof, grace):
        n -= 1
    closed = ordered[:n]
    forming = ordered[-1] if n < len(ordered) else None
    return closed, forming


def aggregate(minute_bars, minutes):
    """يجمع شموع الدقيقة إلى شموع N دقائق تبدأ من 9:30 نيويورك (9:30، 9:33، ...).
    الدقائق التي لا صفقات فيها لا تُصنع لها شموع؛ الشمعة المجمعة من الدقائق الموجودة فقط."""
    groups = {}
    for b in sorted(minute_bars, key=lambda x: x["t"]):
        d = _ny(b["t"])
        m = d.hour * 60 + d.minute
        if not (d.weekday() < 5 and OPEN_MIN <= m < CLOSE_MIN):
            continue
        start_min = OPEN_MIN + (m - OPEN_MIN) // minutes * minutes
        start = datetime(d.year, d.month, d.day, start_min // 60, start_min % 60, tzinfo=NY).timestamp()
        g = groups.get(start)
        if g is None:
            groups[start] = {"t": int(start), "o": b["o"], "h": b["h"], "l": b["l"], "c": b["c"],
                             "v": b["v"] if b["v"] is not None else None}
        else:
            g["h"], g["l"], g["c"] = max(g["h"], b["h"]), min(g["l"], b["l"]), b["c"]
            g["v"] = None if g["v"] is None or b["v"] is None else g["v"] + b["v"]
    return [groups[t] for t in sorted(groups)]


def latest_slot_start(interval, now):
    """بداية آخر شمعة كان يجب أن تُغلق حتى now في جلسة رسمية (أيام العمل فقط؛ العطل الرسمية لا تُعرف هنا،
    لذلك يعتمد المحرك أيضًا على أحدث شمعة مغلقة وصلت فعلًا من المصدر)."""
    day = _ny(now).replace(hour=0, minute=0, second=0, microsecond=0)
    for _ in range(10):
        if day.weekday() < 5:
            open_t = day.replace(hour=9, minute=30).timestamp()
            close_t = day.replace(hour=16).timestamp()
            if interval == "1D":
                if close_t <= now:
                    return day.timestamp()
            else:
                best, start = None, open_t
                while start < close_t:
                    if min(start + STEP[interval], close_t) <= now:
                        best = start
                    start += STEP[interval]
                if best is not None:
                    return best
        day = (day - timedelta(days=1)).replace(hour=0)
        now = day.replace(hour=23, minute=59).timestamp()
    return None
