"""إعلانات الشركات للشريط الإخباري أعلى المنصة: تقسيم الأسهم (العكسي والعادي).

المصدر الأول تقويم التقسيمات في ياهو، والبديل تقويم ناسداك. يُحدَّث كل 30 دقيقة في الخلفية.
"""
import os
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


def _days_apart(a, b):
    try:
        return abs((date.fromisoformat(a) - date.fromisoformat(b)).days)
    except ValueError:
        return 99


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
            # ياهو أولًا، وناسداك يكمل الناقص؛ نفس التقسيم قد يأتي من المصدرين بفارق يوم أو يومين
            dup = any(k[0] == it["symbol"] and _days_apart(k[1], it["date"]) <= 3 for k in items)
            if not dup:
                items[(it["symbol"], it["date"])] = it
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


# التقسيمات المعروضة: أسهم ناسداك ونيويورك فقط (بدون OTC والصناديق المتداولة ETF)
LISTED = {"NMS", "NGM", "NCM", "NAS", "NYQ", "NYS", "ASE"}  # ناسداك (3 شرائح) · بورصة نيويورك · نيويورك أمريكان
_listed = {"set": set(), "at": 0}


def _listed_symbols():
    rows_fn = _state.get("get_rows")
    if rows_fn and time.time() - _listed["at"] > 120:
        try:
            got = {r["symbol"] for r in (rows_fn() or []) if r.get("exchange") in LISTED and not r.get("otc")}
            if got:
                _listed.update(set=got, at=time.time())
        except Exception:
            pass
    return _listed["set"]


def snapshot():
    start()
    with _lock:
        out = {k: _state[k] for k in ("items", "at", "source", "error")}
    listed = _listed_symbols()
    if listed:  # قبل جهوز قائمة السوق تُعرض كل التقسيمات
        out["items"] = [i for i in out["items"] if i["symbol"] in listed]
    return out


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
        # صفوف هذا الجدول فقط (بدون الجداول المتداخلة)، وصف العناوين هو أول صف فيه خانة "Symbol"
        rows = [tr for tr in table.find_all("tr") if tr.find_parent("table") is table]
        cells_of = lambda tr: [c.get_text(" ", strip=True) for c in tr.find_all(["th", "td"]) if c.find_parent("tr") is tr]
        h = next((i for i, tr in enumerate(rows) if any("symbol" in c.lower() and len(c) < 30 for c in cells_of(tr))), None)
        if h is None:
            continue
        head, rows = [c.lower() for c in cells_of(rows[h])], rows[h:]
        idx = lambda *keys: next((i for i, h in enumerate(head) if any(k in h for k in keys)), None)
        i_sym, i_name, i_reason = idx("symbol"), idx("issuer", "company", "name"), idx("reason", "deficiency")
        i_status, i_date = idx("status"), idx("effective", "suspension date", "delisting date", "date")
        for tr in rows[1:]:
            cells = cells_of(tr)
            cell = lambda i: cells[i] if i is not None and i < len(cells) else ""
            sym = cell(i_sym).upper()
            if not _US.fullmatch(sym):
                continue
            out.append({"symbol": sym, "name": cell(i_name), "reason": cell(i_reason),
                        "status": cell(i_status), "date": cell(i_date), "source": "Nasdaq"})
    return out


_HINT = re.compile(r"""["'(]([^"'()\s]{0,120}(?:\.ashx|\.asmx|\.svc|/api/|GetData|WebMethod|\.json|Handler|Service\.)[^"'()\s]{0,120})["')]""", re.I)


# ===== المصدر الرسمي الأوضح: نماذج Form 25 في هيئة الأوراق المالية الأمريكية (SEC EDGAR) =====
# Form 25-NSE تقدمه البورصة (ناسداك أو نيويورك) قبل شطب السهم بنحو 10 أيام، و Form 25 تقدمه الشركة عند الانسحاب طوعًا
SEC_FEED = "https://www.sec.gov/cgi-bin/browse-edgar?action=getcurrent&type={}&company=&dateb=&owner=include&start=0&count=100&output=atom"
SEC_TICKERS = "https://www.sec.gov/files/company_tickers.json"
SEC_UA = {"User-Agent": os.environ.get("SEC_UA", "StocksPlatform personal research stocks-platform@example.com"), "Accept-Encoding": "gzip, deflate"}
_ciks = {"map": {}, "at": 0}
_EXCHANGE = re.compile(r"stock market llc|stock exchange|\bexchange,? (inc|llc)|nyse|cboe", re.I)
_FUND = re.compile(r"\bETF\b|\bETN\b|\bFund\b|\bFunds\b|iShares|ProShares|Direxion", re.I)
_ENTRY = re.compile(r"<entry>(.*?)</entry>", re.S)
_TITLE = re.compile(r"<title>\s*([\w-]+)\s*-\s*(.*?)\s*\((\d{6,10})\)\s*\((\w[\w ]*)\)\s*</title>", re.S)


def _sec_get(url):
    try:
        r = requests.get(url, timeout=15, headers=SEC_UA)
        if r.ok:
            return r
    except requests.RequestException:
        pass
    from curl_cffi import requests as creq
    return creq.get(url, timeout=15, headers=SEC_UA, impersonate="chrome")


def _cik_map():
    if time.time() - _ciks["at"] > 86400 or not _ciks["map"]:
        data = _sec_get(SEC_TICKERS).json()
        m = {}
        for row in data.values():
            m.setdefault(int(row["cik_str"]), row["ticker"].upper().replace(".", "-"))
        _ciks.update(map=m, at=time.time())
    return _ciks["map"]


def _from_sec():
    ciks, out, seen = _cik_map(), [], set()
    for form in ("25-NSE", "25"):
        text = _sec_get(SEC_FEED.format(form)).text
        for e in _ENTRY.findall(text):
            m = _TITLE.search(e)
            if not m or m.group(1).upper() not in ("25-NSE", "25"):
                continue
            # في 25-NSE نأخذ الشركة (Subject) وليس البورصة التي قدّمت النموذج
            if (m.group(1).upper() == "25-NSE" and m.group(4).lower() != "subject") or _EXCHANGE.search(m.group(2)):
                continue
            if _FUND.search(m.group(2)):
                continue  # صناديق متداولة وليست أسهم شركات
            sym = ciks.get(int(m.group(3)))
            if not sym or not _US.fullmatch(sym) or sym in seen:
                continue
            seen.add(sym)
            when = re.search(r"<updated>(\d{4}-\d{2}-\d{2})", e)
            link = re.search(r'<link[^>]*href="([^"]+)"', e)
            out.append({"symbol": sym, "name": html_unescape(m.group(2)),
                        "reason": "البورصة قدّمت طلب الشطب (Form 25-NSE)" if m.group(1).upper() == "25-NSE" else "الشركة طلبت الانسحاب من البورصة (Form 25)",
                        "status": "", "date": when.group(1) if when else "", "url": link.group(1) if link else "", "source": "SEC"})
    return out


def html_unescape(s):
    import html
    return html.unescape(s)


def _refresh_delist():
    """قائمتان: نماذج الشطب في SEC (رسمية وسهلة القراءة) + صفحة ناسداك (إن أمكن قراءتها)."""
    items, errors = [], []
    try:
        items = _parse_delist(_nasdaq_delist_html())
    except Exception as e:
        errors.append(f"Nasdaq: {str(e)[:100]}")
    try:
        have = {i["symbol"] for i in items}
        items += [i for i in _from_sec() if i["symbol"] not in have]
        _delist["sec_error"] = None
    except Exception as e:
        _delist["sec_error"] = str(e)[:150]
        errors.append(f"SEC: {str(e)[:100]}")
    if items or not _delist["items"]:
        _delist["items"] = items
    if errors and not items and not _delist["fails"]:
        print(f"⚠️ تعذر جلب قائمة الأسهم قيد الشطب: {'; '.join(errors)[:160]}")
    _delist.update(error="; ".join(errors) or None, fails=_delist["fails"] + 1 if errors and not items else 0, at=time.time())


def _nasdaq_delist_html():
    html = _get_html(DELIST_URL)
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(html, "html.parser")
    _delist["info"] = {"bytes": len(html), "title": (soup.title.get_text(strip=True) if soup.title else "")[:80],
                       "tables": [[c.get_text(" ", strip=True)[:25] for c in (t.find("tr") or t).find_all(["th", "td"])][:8] for t in soup.find_all("table")][:6],
                       "hints": sorted(set(_HINT.findall(html)))[:25]}
    return html


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


_SUFFIX = {"inc", "corp", "corporation", "co", "company", "ltd", "limited", "plc", "holdings", "holding", "group", "the",
           "technologies", "technology", "international", "sa", "nv", "ag", "se", "lp", "llc", "class", "common", "stock", "and", "&"}
# مقالات ملخص السوق تذكر شركات كثيرة: لا تُنسب لسهم بعينه
_ROUNDUP = re.compile(r"stocks? to (watch|buy)|stock market today|mag(nificent)? ?7|wall street|stocktwits|dow jones|s&p 500|nasdaq composite|week ahead|movers|futures|earnings (calendar|week)|\btop \d+\b", re.I)


def about_company(title, symbol, name):
    """هل الخبر عن هذه الشركة؟ يذكر رمزها أو اسمها المميز (وليس مقال ملخص للسوق)."""
    if _ROUNDUP.search(title):
        return False
    if len(symbol) >= 2 and re.search(rf"(?<![A-Za-z]){re.escape(symbol)}(?![A-Za-z])", title):
        return True
    words = [w for w in re.findall(r"[A-Za-z0-9]+", (name or "").lower()) if w not in _SUFFIX and len(w) >= 3]
    if not words:
        return True  # لا نعرف الاسم: نقبل الخبر
    t = title.lower()
    return words[0] in t or " ".join(words[:2]) in t


def _refresh_news(get_rows):
    import extras
    from concurrent.futures import ThreadPoolExecutor
    cutoff, items, seen = time.time() - NEWS_HOURS * 3600, [], set()
    rows = get_rows() or []
    names = {r["symbol"]: r.get("name") for r in rows}
    syms, st = _news_symbols(lambda: rows), {"articles": 0, "last_48h": 0, "scanned": 0}
    other = []

    def fetch(s):
        # الأخبار + البيانات الصحفية للشركة (فيها إشعارات ناسداك والطروحات عادةً)
        out = extras.get_news(s)[:10] + extras.get_news(s, tab="press releases")[:10]
        name = names.get(s) or (extras._floats.get(s) or {}).get("name")
        out = [a for a in out if about_company(a["title"], s, name)]
        time.sleep(0.3)  # لا نُثقل على ياهو
        return s, out

    with ThreadPoolExecutor(max_workers=4) as pool:
        for s, arts in pool.map(fetch, syms):
            st["scanned"] += 1
            for a in arts:
                if a["url"] in seen:
                    continue
                seen.add(a["url"]); st["articles"] += 1
                if (a.get("time") or 0) < cutoff:
                    continue
                st["last_48h"] += 1
                c = classify(a["title"])
                if not c:
                    if len(other) < 15:
                        other.append(f"{s}: {a['title'][:100]}")
                    continue
                items.append({**c, "symbol": s, "title": a["title"], "url": a["url"], "time": a.get("time"), "publisher": a.get("publisher")})
            # النتائج تظهر أولًا بأول أثناء الفحص (أول فحص بعد التشغيل يأخذ دقائق)
            if st["scanned"] % 10 == 0 or st["scanned"] == len(syms):
                ranked = sorted(items, key=lambda i: (i["prio"], -(i["time"] or 0)))[:60]
                _news_state.update(items=ranked if st["scanned"] == len(syms) or ranked else _news_state["items"], at=time.time(),
                                   stats={**st, "symbols": len(syms), "important": len(items), "other_samples": other})
    if not syms:
        _news_state["stats"] = {"symbols": 0, "note": "قائمة السوق لم تجهز بعد"}
    return len(syms)


def _events_loop(get_rows):
    last_news = last_delist = 0
    while True:
        now = time.time()
        if now - last_delist > REFRESH_SECONDS:
            _refresh_delist(); last_delist = now
        if now - last_news > NEWS_EVERY:
            try:
                if _refresh_news(get_rows):  # قائمة السوق لم تجهز بعد عند التشغيل: نعيد بعد 30 ثانية
                    last_news = now
            except Exception as e:
                print(f"⚠️ تعذر تحديث الأخبار المهمة: {str(e)[:120]}")
                last_news = now
        time.sleep(30)


def start_news(get_rows):
    with _lock:
        if _state.get("news_started"):
            return
        _state["news_started"] = True
        _state["get_rows"] = get_rows
    threading.Thread(target=_events_loop, args=(get_rows,), daemon=True).start()


def news_snapshot():
    return {"news": _news_state["items"], "news_at": _news_state["at"], "news_stats": _news_state.get("stats"),
            "delist": _delist["items"], "delist_at": _delist["at"], "delist_error": _delist["error"], "delist_info": _delist.get("info"),
            "sec_error": _delist.get("sec_error"), "news_sources": __import__("extras").NEWS_SRC}
