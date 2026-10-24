"""إعلانات الشركات للشريط الإخباري أعلى المنصة: تقسيم الأسهم (العكسي والعادي).

المصدر الأول تقويم التقسيمات في ياهو، والبديل تقويم ناسداك. يُحدَّث كل 30 دقيقة في الخلفية.
"""
import re
import threading
import time
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import requests

REFRESH_SECONDS = 1800
DAYS_BACK, DAYS_AHEAD = 3, 45
NY = ZoneInfo("America/New_York")
NASDAQ_URL = "https://api.nasdaq.com/api/calendar/splits"
_state = {"items": [], "at": 0, "source": None, "error": None, "started": False}
_lock = threading.Lock()


def _today():
    return datetime.now(NY).date()  # تاريخ السوق الأمريكي وليس تاريخ جهازك


def _num(v):
    try:
        return float(str(v).replace(",", ""))
    except (TypeError, ValueError):
        return None


def _item(symbol, name, new, old, when, source):
    """new سهم جديد مقابل كل old سهم قديم: أقل من القديم = تقسيم عكسي."""
    if not symbol or not new or not old or new == old:
        return None
    fmt = lambda x: int(x) if float(x).is_integer() else round(x, 3)
    return {"symbol": symbol.upper(), "name": (name or "").strip(), "date": when,
            "reverse": new < old, "new": fmt(new), "old": fmt(old), "source": source}


def _from_yahoo():
    import yfinance as yf
    start, end = _today() - timedelta(days=DAYS_BACK), _today() + timedelta(days=DAYS_AHEAD)
    cal, out = yf.Calendars(start=start, end=end), []
    for offset in range(0, 500, 100):  # ياهو يرجع 100 كحد أقصى في الطلب
        df = cal.get_splits_calendar(limit=100, offset=offset, force=True)
        if df is None or df.empty:
            break
        cols = {c.lower(): c for c in df.columns}
        col = lambda *keys: next((cols[k] for k in cols if all(x in k for x in keys)), None)
        c_name, c_when = col("company"), col("payable") or col("date")
        c_old, c_new = col("old", "worth"), next((cols[k] for k in cols if "worth" in k and "old" not in k), None)
        for sym, r in df.iterrows():
            when = r.get(c_when) if c_when else None
            when = when.strftime("%Y-%m-%d") if hasattr(when, "strftime") else str(when or "")[:10]
            it = _item(str(sym), r.get(c_name) if c_name else "", _num(r.get(c_new)), _num(r.get(c_old)), when, "Yahoo")
            if it:
                out.append(it)
        if len(df) < 100:
            break
    return out


_RATIO = re.compile(r"([\d.,]+)\s*[:\-/]\s*([\d.,]+)")


def _from_nasdaq():
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36",
               "Accept": "application/json, text/plain, */*", "Origin": "https://www.nasdaq.com", "Referer": "https://www.nasdaq.com/"}
    try:
        data = requests.get(NASDAQ_URL, timeout=10, headers=headers).json()
    except Exception:
        from curl_cffi import requests as creq  # اتصال يشبه المتصفح إذا رُفض الطلب العادي
        data = creq.get(NASDAQ_URL, timeout=10, impersonate="chrome").json()
    out = []
    for r in ((data or {}).get("data") or {}).get("rows") or []:
        m = _RATIO.search(str(r.get("ratio", "")))
        if not m:
            continue
        when = r.get("executionDate") or r.get("payableDate") or ""
        try:
            when = datetime.strptime(when, "%m/%d/%Y").strftime("%Y-%m-%d")
        except ValueError:
            pass
        # ناسداك تكتب النسبة "جديد : قديم" (1 : 20 = كل 20 سهمًا تصبح سهمًا واحدًا)
        it = _item(r.get("symbol"), r.get("name"), _num(m.group(1)), _num(m.group(2)), when, "Nasdaq")
        if it:
            out.append(it)
    return out


def refresh():
    items, errors, source = {}, [], []
    for name, fn in (("Yahoo", _from_yahoo), ("Nasdaq", _from_nasdaq)):
        try:
            got = fn()
        except Exception as e:
            errors.append(f"{name}: {str(e)[:80]}")
            continue
        if got:
            source.append(name)
        for it in got:
            items.setdefault((it["symbol"], it["date"]), it)  # ياهو أولًا، وناسداك يكمل الناقص
    lo = (_today() - timedelta(days=DAYS_BACK)).isoformat()
    hi = (_today() + timedelta(days=DAYS_AHEAD)).isoformat()
    rows = sorted((i for i in items.values() if lo <= i["date"] <= hi), key=lambda i: (not i["reverse"], i["date"], i["symbol"]))
    with _lock:
        if rows or not _state["items"]:  # عند فشل المصدرين تبقى آخر قائمة ناجحة
            _state.update(items=rows, source=" + ".join(source) or None)
        _state.update(at=time.time(), error=None if rows else "; ".join(errors) or None)
    if errors and not rows:
        print(f"⚠️ تعذر جلب إعلانات تقسيم الأسهم: {'; '.join(errors)[:160]}")


def _loop():
    while True:
        refresh()
        time.sleep(REFRESH_SECONDS)


def start():
    with _lock:
        if _state["started"]:
            return
        _state["started"] = True
    threading.Thread(target=_loop, daemon=True).start()


def snapshot():
    start()
    with _lock:
        return {k: _state[k] for k in ("items", "at", "source", "error")}


def for_symbol(symbol):
    return [i for i in snapshot()["items"] if i["symbol"] == symbol.upper()]
