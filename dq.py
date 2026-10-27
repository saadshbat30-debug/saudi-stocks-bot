"""طبقة جودة البيانات (المرحلة الأولى): معلومات وتحذيرات تُضاف إلى صف السهم فقط.

لا تغيّر أي رقم محسوب، ولا ترتيب الأسهم، ولا نتائج الفلاتر، ولا مصدر السعر:
- مصدر آخر سعر ووقته: وقت الصفقة الفعلي (من ياهو) مفصولًا عن وقت استلام آخر رسالة بث وعن وقت جلب الماسح.
- «تغير غير موثوق»: تحذير معلوماتي بأسبابه، والسهم يبقى في مكانه.
- الأسهم الحرة: الحقيقية (floatShares) مفصولة عن الأسهم القائمة المستخدمة بديلًا، مع تاريخ الجلب،
  وإعادة طلبها بعد التقسيم في سجل منفصل لا يُستخدم في الفلاتر حتى المراجعة.
"""
import threading
import time
from datetime import datetime, timezone

# ===== حدود تحذير «تغير غير موثوق» (معلوماتية فقط، لا تستبعد ولا تخفي) =====
DQ_RULES = {
    "stale_trade": {"hours": 24, "text": "آخر صفقة رسمية قبل أكثر من {hours} ساعة — النسبة قد تكون من سعر قديم"},
    "tiny_value": {"pct": 300, "value": 10_000, "text": "تغير ≥ {pct}% وقيمة التداول التقديرية أقل من {value:,}$"},
    "odd_prev": {"low": 0.2, "high": 5.0, "text": "الإغلاق السابق أقل من {low:.0%} من أدنى اليوم أو أكثر من {high:.0f}× أعلاه (إغلاق سابق شاذ أو قديم محتمل)"},
    "no_volume": {"text": "تغير في السعر بلا أي حجم تداول اليوم"},
    "subpenny": {"price": 0.01, "pct": 100, "text": "سعر أقل من {price}$ وتغير ≥ {pct}%: خطوة سعرية واحدة تغيّر النسبة كثيرًا"},
}
# تحذير السيولة النسبية: المتوسط المرجعي صغير جدًا فيضخّم النسبة
RVOL_RULE = {"avg": 10_000, "ratio": 50, "text": "متوسط الحجم اليومي لآخر 3 أشهر {avg:,} سهم فقط — السيولة النسبية مضخّمة"}

FLOAT_SOURCE = "Yahoo Finance (floatShares)"
SPLIT_LOOKBACK_DAYS = 14
SPLIT_TOLERANCE = 0.25  # القيمة الجديدة «متسقة» إن كانت ضمن ±25% من (القديمة × نسبة التقسيم)


def price_info(r, live, quote_time, fetched_at, now):
    """من أين جاء السعر المعروض ومتى. live: آخر رسالة بث استُخدمت (أو None)."""
    if live:
        src = "stream"
        trade_t = live.get("trade_time") if live.get("trade_time_known", True) else None
        msg_t = live.get("at")
    else:
        src = "bars" if r.get("from_bars") else "screener"
        trade_t, msg_t = quote_time or None, None
    return {
        "px_src": src,                                     # stream | screener | bars
        "trade_time": int(trade_t) if trade_t else None,   # وقت الصفقة كما يرسله ياهو
        "trade_age": round(now - trade_t) if trade_t else None,
        "msg_time": int(msg_t) if msg_t else None,         # وقت استلام آخر رسالة بث
        "msg_age": round(now - msg_t) if msg_t else None,
        "fetched_time": int(fetched_at) if fetched_at else None,  # وقت جلب صف الماسح
        "fetched_age": round(now - fetched_at) if fetched_at else None,
    }


def change_flags(r, avg_vol):
    """أسباب «تغير غير موثوق» و«سيولة نسبية مضخمة» بعد اكتمال حساب الصف."""
    out = []
    chg, price, vol, prev = abs(r.get("change_pct") or 0), r.get("price") or 0, r.get("volume") or 0, r.get("prev_close")
    lo, hi, value = r.get("day_low"), r.get("day_high"), r.get("value") or 0
    age = r.get("trade_age")
    R = DQ_RULES
    if age is not None and age > R["stale_trade"]["hours"] * 3600 and chg > 0:
        out.append({"code": "stale_trade", "text": R["stale_trade"]["text"].format(**R["stale_trade"])})
    if chg >= R["tiny_value"]["pct"] and value < R["tiny_value"]["value"]:
        out.append({"code": "tiny_value", "text": R["tiny_value"]["text"].format(**R["tiny_value"])})
    if prev and lo and hi and (prev < lo * R["odd_prev"]["low"] or prev > hi * R["odd_prev"]["high"]):
        out.append({"code": "odd_prev", "text": R["odd_prev"]["text"].format(**R["odd_prev"])})
    if vol == 0 and chg > 0:
        out.append({"code": "no_volume", "text": R["no_volume"]["text"]})
    if 0 < price < R["subpenny"]["price"] and chg >= R["subpenny"]["pct"]:
        out.append({"code": "subpenny", "text": R["subpenny"]["text"].format(**R["subpenny"])})
    rvol = None
    if avg_vol is not None and avg_vol < RVOL_RULE["avg"] and (r.get("vol_ratio") or 0) >= RVOL_RULE["ratio"]:
        rvol = RVOL_RULE["text"].format(avg=int(avg_vol), ratio=RVOL_RULE["ratio"])
    return out, rvol


def float_info(symbol, real_float, outstanding, meta):
    """التمييز بين الأسهم الحرة الحقيقية والأسهم القائمة المستخدمة بديلًا في التدوير."""
    chk = _float_checks.get(symbol)
    return {
        "float_src": FLOAT_SOURCE if real_float else None,
        "float_at": int(meta["at"]) if meta and meta.get("at") and real_float else None,
        "shares_out": outstanding,                     # الأسهم القائمة (ليست حرة)
        "turnover_base": "float" if real_float else ("outstanding" if outstanding else None),
        "float_check": dict(chk) if chk else None,     # إعادة الطلب بعد التقسيم (لا تُستخدم في الفلاتر)
    }


# ===== إعادة طلب الأسهم الحرة بعد التقسيم (سجل منفصل للمراجعة) =====
_float_checks = {}  # الرمز ← {split, old, new, expected, status, checked_at}
_state = {"started": False}
_lock = threading.Lock()


def _split_ts(date_str):
    try:
        return datetime.strptime(date_str[:10], "%Y-%m-%d").replace(tzinfo=timezone.utc).timestamp()
    except (TypeError, ValueError):
        return None


def classify_float(old, new, ratio):
    """ratio = عدد الأسهم الجديدة لكل سهم قديم (1:20 عكسي = 0.05)."""
    if not new:
        return "missing", None
    if not old:
        return "no_previous", None
    expected = old * ratio
    if new == old:
        return "unchanged", expected  # ياهو لم يحدّث الرقم بعد
    if abs(new - expected) <= expected * SPLIT_TOLERANCE:
        return "consistent", expected
    return "inconsistent", expected


def check_splits(splits, float_meta, fetch, now=None):
    """splits: بنود التقسيم من corp_events. float_meta(s) ← {"float","at"}. fetch(s) ← floatShares الجديدة من ياهو."""
    now = now or time.time()
    for it in splits:
        ts = _split_ts(it.get("date") or "")
        if ts is None or ts > now or now - ts > SPLIT_LOOKBACK_DAYS * 86400:
            continue  # لم يُنفَّذ بعد، أو قديم
        s = it["symbol"]
        meta = float_meta(s)
        if not meta or not meta.get("at") or meta["at"] >= ts:
            continue  # لا رقم محفوظ، أو جُلب بعد التقسيم
        prev = _float_checks.get(s)
        if prev and prev.get("split") == it.get("date") and now - prev.get("checked_at", 0) < 6 * 3600:
            continue
        ratio = it["new"] / it["old"]
        try:
            new = fetch(s)
        except Exception:
            new = None
        status, expected = classify_float(meta.get("float"), new, ratio)
        with _lock:
            _float_checks[s] = {"split": it.get("date"), "ratio": f"{it['new']}:{it['old']}", "old": meta.get("float"),
                                "new": new, "expected": round(expected) if expected else None, "status": status,
                                "checked_at": int(now), "used": "old"}


def start(get_splits, float_meta, fetch, every=1800):
    with _lock:
        if _state["started"]:
            return
        _state["started"] = True

    def loop():
        while True:
            try:
                check_splits(get_splits(), float_meta, fetch)
            except Exception as e:
                print(f"⚠️ فحص الأسهم الحرة بعد التقسيم: {str(e)[:100]}")
            time.sleep(every)
    threading.Thread(target=loop, daemon=True).start()
