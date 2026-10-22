"""فلتر «قبل الانطلاق» (PRE-BREAKOUT V1.0): نشاط تداول غير معتاد والسعر ما زال في نطاق ضيق.

كل الحسابات من البث اللحظي (الجلسة الرسمية فقط)، ولا تستخدم أي سعر بعد لحظة الرصد.
كل إشارة تُسجَّل ويُتابع سعرها 3 دقائق — اختبار حي يحدد هل يسبق تسارع الحجم حركة صاعدة.
تصميم بحثي: الحدود مقترحة وليست مثبتة، والنتائج لا تشمل الرسوم والانزلاق.
"""
import json
import os
import threading
import time
from bisect import bisect_right
from datetime import datetime
from zoneinfo import ZoneInfo

import extras
import stream

NY = ZoneInfo("America/New_York")
DATA_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "breakout_signals.json")

LISTED = {"NMS", "NGM", "NCM", "NYQ"}  # ناسداك (بشرائحها الثلاث) ونيويورك
WINDOW = 180  # نافذة التماسك: آخر 3 دقائق
HORIZON = 180  # مدة متابعة الإشارة
SCAN_SECONDS = 3

# الشروط: المفتاح ← (الاسم، الحد الأدنى، الحد الأعلى)
RULES = {
    "exchange": ("البورصة NASDAQ / NYSE", None, None),
    "price": ("السعر 2–20$", 2, 20),
    "float": ("الأسهم الحرة 1–50 مليون", 1e6, 50e6),
    "volume": ("حجم الجلسة ≥ 500 ألف", 500e3, None),
    "val3m": ("قيمة تداول آخر 3 دقائق ≥ 100 ألف$", 100e3, None),
    "rvol1m": ("RVOL آخر دقيقة ≥ 2.5", 2.5, None),
    "acc1m": ("تسارع الحجم ≥ 1.5", 1.5, None),
    "chg3m": ("تغير السعر آخر 3 دقائق −0.30% إلى +0.60%", -0.30, 0.60),
    "rng3m": ("نطاق أعلى/أدنى 3 دقائق ≤ 1.20%", None, 1.20),
    "pos3m": ("موضع السعر في النطاق ≥ 60%", 60, None),
    "age": ("عمر السعر ≤ 5 ثوانٍ", None, 5),
}


def _minute_factor(now):
    """توزيع الحجم داخل اليوم (شكل U): الافتتاح والإغلاق أنشط من منتصف الجلسة."""
    t = datetime.fromtimestamp(now, NY)
    m = t.hour * 60 + t.minute - (9 * 60 + 30)
    if m < 30:
        return 2.5
    if m < 60:
        return 1.5
    if m >= 360:
        return 1.8
    return 0.72  # بحيث يكون متوسط اليوم كله = 1


def in_session(now=None):
    """وقت التشغيل: الجلسة الرسمية بعد أول 5 دقائق (9:35 ص – 4:00 م بتوقيت نيويورك)."""
    t = datetime.fromtimestamp(now or time.time(), NY)
    m = t.hour * 60 + t.minute
    return t.weekday() < 5 and 9 * 60 + 35 <= m < 16 * 60


def metrics(symbol, avg_volume=None, now=None):
    """مقاييس النافذة من البث اللحظي، أو None إن لم يتجمع 3 دقائق من البيانات بعد."""
    h = stream.history(symbol)
    now = now or time.time()
    if len(h) < 2 or h[0][0] > now - WINDOW:
        return None
    times = [x[0] for x in h]

    def at(t):  # آخر رسالة عند الوقت t أو قبله
        return h[max(0, bisect_right(times, t) - 1)]

    v0, v1, v2, v3 = (at(now - 60 * k)[2] for k in range(4))
    v60, base = v0 - v1, ((v1 - v2) + (v2 - v3)) / 2
    p_now, p_start = h[-1][1], at(now - WINDOW)[1]
    window = [x[1] for x in h if x[0] >= now - WINDOW] + [p_start]
    hi, lo = max(window), min(window)
    expected = avg_volume / 390 * _minute_factor(now) if avg_volume else None
    return {
        "acc1m": round(v60 / base, 2) if base > 0 else (99.0 if v60 > 0 else None),
        "rvol1m": round(v60 / expected, 2) if expected else None,
        "chg3m": round((p_now / p_start - 1) * 100, 3) if p_start else None,
        "rng3m": round((hi - lo) / lo * 100, 3) if lo else None,
        "pos3m": round((p_now - lo) / (hi - lo) * 100, 1) if hi > lo else None,
        "val3m": round((v0 - v3) * sum(window) / len(window)),
        "vol1m": v60,
        "age": round(now - h[-1][0], 1),
    }


def evaluate(r, m):
    """نتيجة كل شرط: True / False / None (لا توجد بيانات)."""
    flt = r.get("float") or r.get("shares")
    vals = {
        "exchange": r.get("exchange"), "price": r.get("price"), "float": flt,
        "volume": r.get("volume"), **{k: (m or {}).get(k) for k in ("val3m", "rvol1m", "acc1m", "chg3m", "rng3m", "pos3m", "age")},
    }
    out = {}
    for key, (_, lo, hi) in RULES.items():
        v = vals[key]
        if key == "exchange":
            out[key] = v in LISTED if v else None
        elif v is None:
            out[key] = None
        else:
            out[key] = (lo is None or v >= lo) and (hi is None or v <= hi)
    return vals, out


# ===== المسح المستمر وتسجيل الإشارات =====

_state = {"started": False, "candidates": [], "scan_at": 0}
_signals = []  # من الأقدم للأحدث
_lock = threading.Lock()


def _load():
    try:
        with open(DATA_FILE, encoding="utf-8") as f:
            _signals.extend(json.load(f))
    except (OSError, ValueError):
        pass
    for s in _signals:  # إشارات لم تكتمل متابعتها قبل إغلاق المنصة
        if s["status"] == "open" and time.time() - s["t"] >= HORIZON:
            s["status"] = "done"


def _save():
    try:
        os.makedirs(os.path.dirname(DATA_FILE) or ".", exist_ok=True)
        tmp = DATA_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(_signals[-3000:], f)
        os.replace(tmp, DATA_FILE)
    except OSError as e:
        print(f"⚠️ فشل حفظ إشارات قبل الانطلاق: {e}")


def _track(sig, now):
    """مسار السعر بعد الدخول: [(الثواني منذ الإشارة، التغير %)] من رسائل البث."""
    last = sig["path"][-1][0] if sig["path"] else 0
    for t, price, _ in stream.history(sig["symbol"]):
        dt = t - sig["t"]
        if dt <= last or dt > HORIZON:
            continue
        pct = round((price / sig["entry"] - 1) * 100, 3)
        if not sig["path"] or sig["path"][-1][1] != pct:
            sig["path"].append([round(dt, 1), pct])
        last = dt
    if now - sig["t"] >= HORIZON:
        sig["status"] = "done"
        return True
    return False


def _scan(get_rows):
    now = time.time()
    rows = get_rows()
    candidates, fresh = [], []
    active = in_session(now)
    recent = {s["symbol"] for s in _signals if now - s["t"] < HORIZON}
    for r in rows:
        live = stream.latest(r["symbol"], max_age=60)
        if not live:
            continue
        price = live["price"]
        if not (1.5 <= price <= 25) or r.get("otc"):  # تصفية أولية سريعة قبل الحساب
            continue
        q = {**r, "price": price, "volume": live["volume"] or r.get("volume"), "float": extras.float_for(r["symbol"])}
        m = metrics(r["symbol"], r.get("avg_volume"), now)
        vals, ok = evaluate(q, m)
        passed = sum(1 for v in ok.values() if v)
        candidates.append({"symbol": r["symbol"], "name": r.get("name"), "values": vals, "ok": ok, "passed": passed})
        if active and passed == len(RULES) and r["symbol"] not in recent:
            fresh.append({
                "id": f"{r['symbol']}-{int(now)}", "symbol": r["symbol"], "name": r.get("name"),
                "t": now, "entry": round(price, 4), "values": vals, "path": [], "status": "open",
            })
    candidates.sort(key=lambda c: (-c["passed"], -(c["values"].get("acc1m") or 0)))
    with _lock:
        _state["candidates"], _state["scan_at"] = candidates[:80], now
        for s in fresh:
            _signals.append(s)
            print(f"⚡ إشارة قبل الانطلاق: {s['symbol']} عند ${s['entry']}")
        finished = [s for s in _signals if s["status"] == "open" and _track(s, now)]
    if finished:
        _save()


def _loop(get_rows):
    while True:
        try:
            _scan(get_rows)
        except Exception as e:
            print(f"⚠️ فشل مسح قبل الانطلاق: {e}")
        time.sleep(SCAN_SECONDS)


def start(get_rows):
    """get_rows: دالة ترجع صفوف السوق النشطة (المشمولة بالبث)."""
    if _state["started"]:
        return
    _state["started"] = True
    _load()
    threading.Thread(target=_loop, args=(get_rows,), daemon=True).start()


def snapshot(limit=500):
    now = time.time()
    with _lock:
        return {
            "rules": [[k, v[0]] for k, v in RULES.items()],  # قائمة حتى يبقى الترتيب
            "in_session": in_session(now),
            "scan_age": round(now - _state["scan_at"], 1) if _state["scan_at"] else None,
            "candidates": _state["candidates"],
            "signals": [{**s, "age": round(now - s["t"], 1)} for s in _signals[-limit:]][::-1],
            "horizon": HORIZON,
        }
