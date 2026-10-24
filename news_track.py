"""اختبار خوارزمية الأخبار على أرض الواقع: هل الأخبار ذات الدرجة العالية ترتفع بعدها فعلًا؟

كل خبر جديد يصل أثناء الجلسة الرسمية يُسجَّل لحظة وصوله للمنصة (وليس وقت نشره) مع سعر السهم ومؤشر S&P 500،
ثم يُقاس العائد بعد 5 و15 و60 دقيقة، والعائد الزائد = عائد السهم − عائد المؤشر في نفس الفترة.
لا يُستخدم أي سعر بعد لحظة الوصول في حساب الدرجة نفسها (لا تسريب من المستقبل).
لا تشمل النتائج فرق العرض والطلب (السبريد) ولا العمولات ولا الانزلاق السعري.
"""
import json
import os
import threading
import time
from datetime import datetime
from zoneinfo import ZoneInfo

NY = ZoneInfo("America/New_York")
FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "news_signals.json")
HORIZONS = (5, 15, 60)  # بالدقائق
MAX_AGE_AT_ARRIVAL = 30 * 60  # خبر نُشر قبل أكثر من 30 دقيقة من وصوله: الحركة غالبًا حصلت، لا يُختبر
MAX_ROWS = 4000
BUCKETS = [("🟢 إيجابي قوي (40+)", 40, 101), ("🟢 إيجابي (15 إلى 39)", 15, 40), ("⚪ محايد", -14, 15),
           ("🔴 سلبي (−15 إلى −39)", -39, -14), ("🔴 سلبي قوي (−40 وأقل)", -101, -39)]

_rows, _ids = [], set()
_lock = threading.Lock()
_state = {"started": False}


def _session_open(t=None):
    ny = datetime.fromtimestamp(t or time.time(), NY)
    m = ny.hour * 60 + ny.minute
    return ny.weekday() < 5 and 570 <= m < 960


def _price(symbol):
    import stream
    from market import row_for
    p = stream.latest(symbol, max_age=180)
    if p:
        return p["price"]
    r = row_for(symbol) or {}
    return r.get("price")


def _spx():
    try:
        from market import get_quotes
        return (get_quotes(["^GSPC"]).get("^GSPC") or {}).get("price")
    except Exception:
        return None


def _load():
    try:
        with open(FILE, encoding="utf-8") as f:
            rows = json.load(f)
        _rows.extend(rows[-MAX_ROWS:])
        _ids.update(r["id"] for r in _rows)
    except (OSError, ValueError):
        pass


def _save():
    try:
        os.makedirs(os.path.dirname(FILE), exist_ok=True)
        tmp = FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(_rows[-MAX_ROWS:], f, ensure_ascii=False)
        os.replace(tmp, FILE)
    except OSError:
        pass


def record(item):
    """item: خبر مقيّم فيه symbol, url, time, direction, importance, reaction, source."""
    now = time.time()
    if not _session_open(now) or not item.get("symbol"):
        return
    if item.get("time") and now - item["time"] > MAX_AGE_AT_ARRIVAL:
        return
    rid = f'{item["symbol"]}|{item.get("url")}'
    with _lock:
        if rid in _ids:
            return
    p0 = _price(item["symbol"])
    if not p0:
        return
    row = {"id": rid, "symbol": item["symbol"], "title": (item.get("title") or "")[:160], "source": item.get("source") or "yahoo",
           "direction": item.get("direction", item.get("score", 0)), "importance": item.get("importance", 0),
           "reaction0": item.get("reaction"), "published": item.get("time"), "t0": int(now), "p0": p0, "spx0": _spx(),
           "delay_s": int(now - item["time"]) if item.get("time") else None, "out": {}}
    with _lock:
        _rows.append(row); _ids.add(rid)
        del _rows[:-MAX_ROWS]


def _check():
    now, changed, spx = time.time(), False, None
    with _lock:
        pending = [r for r in _rows if len(r["out"]) < len(HORIZONS)]
    for r in pending:
        for h in HORIZONS:
            key = str(h)
            if key in r["out"] or now < r["t0"] + h * 60:
                continue
            # الجلسة أُغلقت قبل اكتمال المدة: لا قياس لهذه المدة
            if not _session_open(r["t0"] + h * 60) or now > r["t0"] + h * 60 + 600:
                r["out"][key] = None; changed = True
                continue
            p = _price(r["symbol"])
            if spx is None:
                spx = _spx()
            if not p:
                continue
            ret = (p / r["p0"] - 1) * 100
            mret = (spx / r["spx0"] - 1) * 100 if spx and r.get("spx0") else 0.0
            r["out"][key] = {"ret": round(ret, 3), "excess": round(ret - mret, 3)}
            changed = True
    if changed:
        with _lock:
            _save()


def _loop():
    while True:
        try:
            _check()
        except Exception as e:
            print(f"⚠️ اختبار الأخبار: {str(e)[:120]}")
        time.sleep(30)


def start():
    with _lock:
        if _state["started"]:
            return
        _state["started"] = True
        _load()
    threading.Thread(target=_loop, daemon=True).start()


def stats():
    """متوسط العائد والعائد الزائد ونسبة الصعود لكل فئة درجة ولكل مدة."""
    with _lock:
        rows = list(_rows)
    out = []
    for name, lo, hi in BUCKETS:
        grp = [r for r in rows if lo <= (r["direction"] or 0) < hi]
        res = {"bucket": name, "count": len(grp), "h": {}}
        for h in HORIZONS:
            vals = [r["out"][str(h)] for r in grp if r["out"].get(str(h))]
            if vals:
                n = len(vals)
                res["h"][str(h)] = {"n": n, "avg": round(sum(v["ret"] for v in vals) / n, 2),
                                    "excess": round(sum(v["excess"] for v in vals) / n, 2),
                                    "up": round(sum(1 for v in vals if v["ret"] > 0) / n * 100)}
        out.append(res)
    # أخبار مهمة إيجابية والسعر لم يتحرك بعد لحظة وصولها: هل تلحق الحركة؟
    early = [r for r in rows if (r["direction"] or 0) >= 25 and (r.get("reaction0") is None or r["reaction0"] < 15)]
    e = {"bucket": "🕐 إيجابي لم يتحرك بعد عند وصوله", "count": len(early), "h": {}}
    for h in HORIZONS:
        vals = [r["out"][str(h)] for r in early if r["out"].get(str(h))]
        if vals:
            n = len(vals)
            e["h"][str(h)] = {"n": n, "avg": round(sum(v["ret"] for v in vals) / n, 2),
                              "excess": round(sum(v["excess"] for v in vals) / n, 2), "up": round(sum(1 for v in vals if v["ret"] > 0) / n * 100)}
    out.append(e)
    delays = [r["delay_s"] for r in rows if r.get("delay_s") is not None]
    return {"buckets": out, "total": len(rows), "horizons": HORIZONS,
            "median_delay_s": sorted(delays)[len(delays) // 2] if delays else None,
            "recent": sorted(rows, key=lambda r: -r["t0"])[:30]}
