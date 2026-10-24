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
    symbol = (symbol or "").strip().upper()
    # تقويم ياهو عالمي (كوريا .KS، كندا .TO ...): نبقي الأسهم المتداولة في أمريكا فقط
    if not _US.fullmatch(symbol) or not new or not old or new == old:
        return None
    fmt = lambda x: int(x) if float(x).is_integer() else round(x, 3)
    return {"symbol": symbol, "name": (name or "").strip(), "date": when,
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


_US = re.compile(r"[A-Z][A-Z0-9]{0,5}(?:-[A-Z]{1,2})?")
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
        for it in filter(None, got):
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
        try:
            refresh()
        except Exception as e:
            print(f"⚠️ تعذر تحديث إعلانات التقسيم: {str(e)[:120]}")
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


# ===== الأسهم التي تغادر ناسداك: قائمة ناسداك الرسمية للأسهم المعلّق تداولها أو قيد الشطب =====

DELIST_URL = "https://listingcenter.nasdaq.com/IssuersPendingSuspensionDelisting.aspx"
_delist = {"items": [], "at": 0, "error": None, "fails": 0}
_UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36",
       "Accept": "text/html,application/xhtml+xml,*/*"}


def _get_html(url):
    try:
        r = requests.get(url, timeout=15, headers=_UA)
        if r.ok and "<table" in r.text.lower():
            return r.text
    except requests.RequestException:
        pass
    from curl_cffi import requests as creq  # اتصال يشبه المتصفح إذا رُفض الطلب العادي
    return creq.get(url, timeout=15, impersonate="chrome").text


def _parse_delist(html):
    """أي جدول فيه عمود Symbol: نقرأ الاسم والسبب وتاريخ التنفيذ من عناوين الأعمدة."""
    from bs4 import BeautifulSoup
    out = []
    for table in BeautifulSoup(html, "html.parser").find_all("table"):
        rows = table.find_all("tr")
        if not rows:
            continue
        head = [c.get_text(" ", strip=True).lower() for c in rows[0].find_all(["th", "td"])]
        if not any("symbol" in h for h in head):
            continue
        idx = lambda *keys: next((i for i, h in enumerate(head) if any(k in h for k in keys)), None)
        i_sym, i_name, i_reason = idx("symbol"), idx("issuer", "company", "name"), idx("reason", "deficiency")
        i_status, i_date = idx("status"), idx("effective", "suspension date", "delisting date", "date")
        for tr in rows[1:]:
            cells = [c.get_text(" ", strip=True) for c in tr.find_all(["td", "th"])]
            cell = lambda i: cells[i] if i is not None and i < len(cells) else ""
            sym = cell(i_sym).upper()
            if not _US.fullmatch(sym):
                continue
            out.append({"symbol": sym, "name": cell(i_name), "reason": cell(i_reason),
                        "status": cell(i_status), "date": cell(i_date), "source": "Nasdaq"})
    return out


def _refresh_delist():
    try:
        items = _parse_delist(_get_html(DELIST_URL))
        _delist.update(items=items, error=None, fails=0)
    except Exception as e:
        if not _delist["fails"]:
            print(f"⚠️ تعذر جلب قائمة ناسداك للأسهم قيد الشطب: {str(e)[:120]}")
        _delist.update(error=str(e)[:150], fails=_delist["fails"] + 1)
    _delist["at"] = time.time()


# ===== الأخبار المهمة: عناوين أخبار ياهو لأنشط الأسهم وصفقاتك، مصنفة حسب نوع الخبر =====

NEWS_EVERY = 600
NEWS_HOURS = 48
# (النوع، الأيقونة، الوصف، الأولوية: أقل = أهم، النمط)
_KINDS = [
    ("delist", "🚫", "شطب / مغادرة البورصة", 0, r"\bdelist|delisting|suspension of trading|form 25\b|(move|transfer|moving)s? to (the )?otc|nasdaq hearings? panel"),
    ("bankrupt", "⛔", "إفلاس", 0, r"chapter 11|chapter 7|bankrupt|insolven|receivership"),
    ("compliant", "✅", "استعاد الامتثال", 2, r"regain(s|ed)? compliance"),
    ("notice", "⚠️", "إشعار من البورصة", 1, r"(nasdaq|nyse)( american)? (notice|notification|deficiency|letter)|minimum bid|non-?compliance|listing (rule|requirement|standard)|deficiency"),
    ("rsplit", "🔻", "تقسيم عكسي", 1, r"reverse (stock |share )?split"),
    ("offering", "💸", "طرح أسهم (تخفيف)", 1, r"(public|registered direct|direct|private placement|at-the-market|atm|underwritten|secondary) offering|prices? .{0,40}offering|warrant (inducement|exercise)|shelf registration"),
    ("merger", "🤝", "استحواذ / اندماج", 1, r"\bmerger\b|to (acquire|be acquired)|acquired by|acquisition of|definitive agreement|take[- ]private|buyout|tender offer"),
    ("fda", "💊", "FDA / تجارب سريرية", 2, r"\bfda\b|phase (1|2|3|i{1,3})\b|clinical (trial|data|results)|breakthrough therapy|orphan drug|\bnda\b|\bbla\b"),
    ("earnings", "📊", "نتائج مالية", 3, r"(q[1-4]|first|second|third|fourth|quarter|fiscal|full[- ]year).{0,40}(results|earnings)|reports? .{0,30}(results|earnings)|guidance|beats? estimates|misses? estimates"),
    ("buyback", "💰", "إعادة شراء / توزيعات", 3, r"buyback|repurchase|special dividend|declares .{0,20}dividend"),
    ("contract", "📝", "عقد / شراكة", 3, r"\bcontract\b|awarded|\bpartnership\b|collaboration|strategic agreement|purchase order"),
]
_KINDS = [(k, i, l, p, re.compile(rx, re.I)) for k, i, l, p, rx in _KINDS]
_news_state = {"items": [], "at": 0, "watch": {}}


def classify(title):
    for kind, icon, label, prio, rx in _KINDS:
        if rx.search(title or ""):
            return {"kind": kind, "icon": icon, "label": label, "prio": prio}
    return None


def watch(symbols):
    """أسهم يطلبها المتصفح (صفقاتك ومفضلتك) تُضاف لمتابعة الأخبار لمدة ساعة."""
    now = time.time()
    for s in symbols[:40]:
        if _US.fullmatch(s):
            _news_state["watch"][s] = now


def _news_symbols(get_rows):
    rows = [r for r in (get_rows() or []) if (r.get("value") or 0) >= 1_000_000 and not r.get("otc")]
    by = lambda key, rev=True: [r["symbol"] for r in sorted(rows, key=lambda r: r.get(key) or 0, reverse=rev)]
    now = time.time()
    mine = [s for s, t in list(_news_state["watch"].items()) if now - t < 3600]
    return list(dict.fromkeys(mine + by("value")[:40] + by("change_pct")[:25] + by("change_pct", False)[:10]))


def _refresh_news(get_rows):
    import extras
    cutoff, items, seen = time.time() - NEWS_HOURS * 3600, [], set()
    for s in _news_symbols(get_rows):
        for a in extras.get_news(s)[:8]:
            c = classify(a["title"])
            if not c or (a.get("time") or 0) < cutoff or a["url"] in seen:
                continue
            seen.add(a["url"])
            items.append({**c, "symbol": s, "title": a["title"], "url": a["url"], "time": a.get("time"), "publisher": a.get("publisher")})
        time.sleep(0.4)  # لا نُثقل على ياهو
    items.sort(key=lambda i: (i["prio"], -(i["time"] or 0)))
    _news_state.update(items=items[:60], at=time.time())


def _events_loop(get_rows):
    last_news = last_delist = 0
    while True:
        now = time.time()
        if now - last_delist > REFRESH_SECONDS:
            _refresh_delist(); last_delist = now
        if now - last_news > NEWS_EVERY:
            try:
                _refresh_news(get_rows)
            except Exception as e:
                print(f"⚠️ تعذر تحديث الأخبار المهمة: {str(e)[:120]}")
            last_news = now
        time.sleep(30)


def start_news(get_rows):
    with _lock:
        if _state.get("news_started"):
            return
        _state["news_started"] = True
    threading.Thread(target=_events_loop, args=(get_rows,), daemon=True).start()


def news_snapshot():
    return {"news": _news_state["items"], "news_at": _news_state["at"],
            "delist": _delist["items"], "delist_at": _delist["at"], "delist_error": _delist["error"]}
