"""الاختبار الحاسم للأخبار: هل ترصد المنصة الخبر المؤثر **قبل** الحركة السعرية، أم تفسر حركة حدثت بالفعل؟

كل خبر حديث (نُشر قبل أقل من 30 دقيقة) يصل أثناء ما قبل الافتتاح أو الجلسة الرسمية أو ما بعد الإغلاق يُسجَّل
لحظة وصوله للمنصة (وليس وقت نشره) مع:
- سعر السهم ومؤشر S&P 500 لحظة الوصول
- حالة السهم لحظة الوصول: هل تحرك قبلها؟ (تغير آخر 5 دقائق من البث، وتغير اليوم أو ما قبل الافتتاح مقارنة بالسوق)
  ← «قبل الحركة»: لم يتحرك بعد باتجاه الخبر | «بعد الحركة»: كان قد تحرك باتجاه الخبر قبل وصوله
ثم يُقاس العائد بعد 1 و3 و5 و15 و60 دقيقة، والعائد الزائد = عائد السهم − عائد المؤشر في نفس الفترة.

المقارنة الحاسمة: الأخبار الإيجابية «قبل الحركة» مقابل المجموعة الضابطة (الأخبار المحايدة في نفس الفترات)،
مع فترة ثقة 95% لمتوسط الفرق. لا يُستخدم أي سعر بعد لحظة الوصول في حساب الدرجة نفسها.
لا تشمل النتائج السبريد ولا العمولات ولا الانزلاق السعري، وهي في الأسهم الصغيرة وقبل الافتتاح كبيرة.
"""
import json
import math
import os
import threading
import time
from datetime import datetime
from zoneinfo import ZoneInfo

NY = ZoneInfo("America/New_York")
FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "news_signals.json")
HORIZONS = (1, 3, 5, 15, 60)  # بالدقائق
KEY_H = 15                    # المدة التي يُحكم بها في سطر الحكم
MIN_N = 100                   # أقل عدد أخبار في المجموعة قبل إصدار حكم
MAX_AGE_AT_ARRIVAL = 30 * 60  # خبر نُشر قبل أكثر من 30 دقيقة من وصوله: الحركة غالبًا حصلت، لا يُختبر
MOVED_5M = 1.5                # تحرك 1.5% أو أكثر في آخر 5 دقائق باتجاه الخبر = «بعد الحركة»
MOVED_DAY = 5.0               # أو تحرك 5% أو أكثر مقارنة بالسوق اليوم / قبل الافتتاح باتجاه الخبر
MAX_ROWS = 6000
POS, NEG = 25, -25            # حد الخبر الإيجابي / السلبي في الاختبار الحاسم
BUCKETS = [("🟢 إيجابي قوي (40+)", 40, 101), ("🟢 إيجابي (15 إلى 39)", 15, 40), ("⚪ محايد", -14, 15),
           ("🔴 سلبي (−15 إلى −39)", -39, -14), ("🔴 سلبي قوي (−40 وأقل)", -101, -39)]

_rows, _ids = [], set()
_lock = threading.Lock()
_state = {"started": False}


def _session(t=None):
    """open = الجلسة الرسمية، pre = قبل الافتتاح (4–9:30)، post = بعد الإغلاق (4–8 م)، None = مغلق."""
    ny = datetime.fromtimestamp(t or time.time(), NY)
    if ny.weekday() >= 5:
        return None
    m = ny.hour * 60 + ny.minute
    return "open" if 570 <= m < 960 else "pre" if 240 <= m < 570 else "post" if 960 <= m < 1200 else None


def _price(symbol):
    """سعر الجلسة الحالية: الرسمية من البث أو الماسح، وما قبل/بعد الجلسة من سعرها."""
    import stream
    from market import row_for
    if _session() == "open":
        p = stream.latest(symbol, max_age=180)
        if p:
            return p["price"]
        return (row_for(symbol) or {}).get("price")
    p = stream.latest_ext(symbol, max_age=600)
    if p:
        return p["price"]
    r = row_for(symbol) or {}
    return r.get("ext_price") or r.get("price")


def _spx():
    try:
        from market import get_quotes
        q = get_quotes(["^GSPC"]).get("^GSPC") or {}
        return q.get("price") if _session() == "open" else (q.get("ext_price") or q.get("price"))
    except Exception:
        return None


def _pre_move(symbol):
    """تغير السعر في آخر 5 دقائق قبل الوصول (من البث، للجلسة الرسمية) وتغير اليوم/قبل الافتتاح."""
    import stream
    from market import row_for
    h = stream.history(symbol)
    m5 = round((h[-1][1] / h[0][1] - 1) * 100, 2) if len(h) >= 2 and h[0][1] else None
    r = row_for(symbol) or {}
    day = r.get("change_pct") if _session() == "open" else r.get("ext_change_pct")
    return m5, day


def _state_at_arrival(direction, m5, day_rel):
    """«قبل الحركة» إذا لم يكن السهم قد تحرك باتجاه الخبر بعد."""
    sign = 1 if direction > 0 else -1
    moved = (m5 is not None and m5 * sign >= MOVED_5M) or (day_rel is not None and day_rel * sign >= MOVED_DAY)
    return "after" if moved else "before"


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
    sess = _session(now)
    if not sess or not item.get("symbol"):
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
    direction = item.get("direction", item.get("score", 0)) or 0
    m5, day = _pre_move(item["symbol"])
    from news_score import market_change
    day_rel = round(day - market_change(), 2) if day is not None else None
    row = {"id": rid, "symbol": item["symbol"], "title": (item.get("title") or "")[:160], "source": item.get("source") or "yahoo",
           "direction": direction, "importance": item.get("importance", 0), "confidence": item.get("confidence"),
           "reaction0": item.get("reaction"), "pre5m": m5, "day_rel": day_rel, "session": sess,
           "state": _state_at_arrival(direction, m5, day_rel) if abs(direction) >= 15 else "neutral",
           "published": item.get("time"), "t0": int(now), "p0": p0, "spx0": _spx(),
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
            target = r["t0"] + h * 60
            if key in r["out"] or now < target:
                continue
            # فات وقت القياس بكثير (المنصة كانت مغلقة) أو أُغلق السوق: لا قياس لهذه المدة
            if not _session(target) or now > target + max(90, h * 30):
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
        time.sleep(20)


def start():
    with _lock:
        if _state["started"]:
            return
        _state["started"] = True
        _load()
    threading.Thread(target=_loop, daemon=True).start()


def _summary(rows, h, sign=1):
    """متوسط العائد الزائد وفترة ثقة 95% ونسبة النجاح (صعود للإيجابي، هبوط للسلبي)."""
    vals = [r["out"][str(h)] for r in rows if r["out"].get(str(h))]
    n = len(vals)
    if not n:
        return None
    ex = [v["excess"] for v in vals]
    mean = sum(ex) / n
    sd = math.sqrt(sum((x - mean) ** 2 for x in ex) / (n - 1)) if n > 1 else 0.0
    half = 1.96 * sd / math.sqrt(n) if n > 1 else None
    return {"n": n, "excess": round(mean, 2), "ci": round(half, 2) if half is not None else None,
            "avg": round(sum(v["ret"] for v in vals) / n, 2),
            "hit": round(sum(1 for v in vals if v["ret"] * sign > 0) / n * 100)}


BACKTEST_FILE = os.path.join(os.path.dirname(FILE), "news_backtest.json")


def backtest_rows():
    """نتائج الاختبار التاريخي (يُنشئها backtest_news.py في عملية منفصلة)."""
    try:
        with open(BACKTEST_FILE, encoding="utf-8") as f:
            d = json.load(f)
        return d.get("rows", []), d.get("meta", {})
    except (OSError, ValueError):
        return [], {}


def stats(session=None, mode="live"):
    meta = {}
    if mode == "backtest":
        src, meta = backtest_rows()
    else:
        with _lock:
            src = list(_rows)
    rows = [r for r in src if not session or r.get("session") == session]
    groups = [
        ("🎯 إيجابي — رُصد قبل الحركة", lambda r: r["direction"] >= POS and r.get("state") == "before", 1),
        ("↪️ إيجابي — وصل بعد الحركة", lambda r: r["direction"] >= POS and r.get("state") == "after", 1),
        ("⚪ محايد (المجموعة الضابطة)", lambda r: -15 < r["direction"] < 15, 1),
        ("🎯 سلبي — رُصد قبل الحركة", lambda r: r["direction"] <= NEG and r.get("state") == "before", -1),
        ("↪️ سلبي — وصل بعد الحركة", lambda r: r["direction"] <= NEG and r.get("state") == "after", -1),
    ]
    decisive = []
    for name, f, sign in groups:
        g = [r for r in rows if f(r)]
        decisive.append({"group": name, "count": len(g), "sign": sign, "h": {str(h): _summary(g, h, sign) for h in HORIZONS}})
    by_dir = []
    for name, lo, hi in BUCKETS:
        g = [r for r in rows if lo <= (r["direction"] or 0) < hi]
        by_dir.append({"group": name, "count": len(g), "sign": 1 if lo >= 0 else -1 if hi <= 0 else 1,
                       "h": {str(h): _summary(g, h, 1 if lo >= 0 or hi > 0 else -1) for h in HORIZONS}})

    # سطر الحكم: الإيجابي «قبل الحركة» مقابل المجموعة الضابطة بعد 15 دقيقة
    pos, ctl = decisive[0]["h"].get(str(KEY_H)), decisive[2]["h"].get(str(KEY_H))
    if not pos or pos["n"] < MIN_N:
        verdict = {"status": "wait", "text": f"⏳ بيانات غير كافية: {pos['n'] if pos else 0} من {MIN_N} خبر إيجابي رُصد قبل الحركة (بعد {KEY_H} دقيقة)"}
    else:
        diff = pos["excess"] - (ctl["excess"] if ctl else 0)
        # فترة ثقة الفرق بين متوسطين مستقلين = √(نصف فترة الأول² + نصف فترة الثاني²)
        half = math.sqrt((pos["ci"] or 0) ** 2 + ((ctl or {}).get("ci") or 0) ** 2)
        lower = diff - half
        if lower > 0:
            verdict = {"status": "yes", "text": f"✅ مثبت مبدئيًا: الأخبار الإيجابية المرصودة قبل الحركة تتفوق على المجموعة الضابطة بـ {diff:+.2f}% بعد {KEY_H} دقيقة (الحد الأدنى لفترة الثقة {lower:+.2f}%) — قبل السبريد والعمولات"}
        elif lower <= 0 < diff:
            verdict = {"status": "unclear", "text": f"🟡 غير حاسم: تفوق {diff:+.2f}% لكن فترة الثقة تشمل الصفر — يلزم بيانات أكثر"}
        else:
            verdict = {"status": "no", "text": f"❌ لم يثبت: لا تفوق على المجموعة الضابطة بعد {KEY_H} دقيقة ({diff:+.2f}%) — الدرجة تصف الخبر ولا تتنبأ بالسعر"}

    delays = sorted(r["delay_s"] for r in rows if r.get("delay_s") is not None)
    sessions = {s: sum(1 for r in rows if r.get("session") == s) for s in ("pre", "open", "post")}
    return {"decisive": decisive, "buckets": by_dir, "verdict": verdict, "total": len(rows), "horizons": HORIZONS,
            "key_h": KEY_H, "min_n": MIN_N, "sessions": sessions,
            "median_delay_s": delays[len(delays) // 2] if delays else None,
            "rules": {"moved_5m": MOVED_5M, "moved_day": MOVED_DAY, "pos": POS, "neg": NEG}, "mode": mode, "meta": meta,
            "recent": sorted(rows, key=lambda r: -r["t0"])[:30]}
