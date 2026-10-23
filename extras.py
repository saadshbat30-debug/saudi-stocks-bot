"""الأخبار، وإيقافات التداول (Nasdaq Trade Halts)، والأسهم الحرة (Float)."""
import html
import json
import os
import re
import threading
import time
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from zoneinfo import ZoneInfo

import requests
import yfinance as yf

NY = ZoneInfo("America/New_York")
DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")


# ===== الأخبار (ياهو) =====

NEWS_SECONDS = 300
_news = {}
_news_lock = threading.Lock()


def _parse_article(item):
    c = item.get("content") or item
    url = ((c.get("clickThroughUrl") or {}).get("url") or (c.get("canonicalUrl") or {}).get("url")
           or c.get("link"))
    title = c.get("title")
    if not title or not url:
        return None
    published = None
    if c.get("pubDate"):
        try:
            published = int(datetime.fromisoformat(c["pubDate"].replace("Z", "+00:00")).timestamp())
        except ValueError:
            pass
    published = published or c.get("providerPublishTime")
    return {
        "title": title,
        "url": url,
        "publisher": (c.get("provider") or {}).get("displayName") or c.get("publisher") or "",
        "time": published,
        "summary": (c.get("summary") or "")[:300],
    }


def get_news(symbol, count=12):
    now = time.time()
    with _news_lock:
        cached = _news.get(symbol)
        if cached and now - cached["at"] < NEWS_SECONDS:
            return cached["items"]
    try:
        # ياهو يقسم الأخبار: الأحدث، الكل، البيانات الصحفية — نجرب حتى نجد أخبارًا
        items = []
        for tab in ("news", "all", "press releases"):
            t = yf.Ticker(symbol)
            items = [a for a in map(_parse_article, t.get_news(count=count, tab=tab) or []) if a]
            if items:
                break
    except Exception as e:
        print(f"⚠️ فشل جلب أخبار {symbol}: {e}")
        items = cached["items"] if cached else []
    with _news_lock:
        _news[symbol] = {"items": items, "at": now}
    return items


def get_news_for(symbols, per_symbol=4):
    """أخبار عدة أسهم مدمجة ومرتبة من الأحدث."""
    with ThreadPoolExecutor(max_workers=6) as pool:
        results = list(pool.map(lambda s: (s, get_news(s)), symbols))
    seen, merged = set(), []
    for symbol, items in results:
        for a in items[:per_symbol]:
            if a["url"] in seen:
                continue
            seen.add(a["url"])
            merged.append({**a, "symbol": symbol})
    merged.sort(key=lambda a: a["time"] or 0, reverse=True)
    return merged


# ===== إيقافات التداول (Nasdaq Trader RSS — تشمل كل البورصات الأمريكية) =====

HALTS_URL = "https://www.nasdaqtrader.com/rss.aspx?feed=tradehalts"
HALTS_SECONDS = 60
HALTS_BACKOFF = 300  # ناسداك يحجب الطلبات المتكررة مؤقتًا (صفحة HTML): ننتظر 5 دقائق
_halts = {"items": [], "at": 0, "error": None, "fails": 0}
_halts_lock = threading.Lock()

REASONS = {
    "LUDP": "توقف تذبذب (LULD)",
    "LUDS": "توقف تذبذب (LULD)",
    "MWC1": "قاطع السوق - المستوى 1",
    "MWC2": "قاطع السوق - المستوى 2",
    "MWC3": "قاطع السوق - المستوى 3",
    "MWCQ": "قاطع السوق",
    "T1": "بانتظار خبر",
    "T2": "نشر خبر",
    "T3": "الخبر نُشر - بانتظار الاستئناف",
    "T5": "توقف تداول السهم",
    "T6": "نشاط غير طبيعي",
    "T7": "عدم كفاية المعلومات",
    "T8": "صناديق ETF",
    "T12": "طلب معلومات إضافية",
    "H4": "عدم الالتزام بالمتطلبات",
    "H9": "تأخر التقارير المالية",
    "H10": "إيقاف من هيئة SEC",
    "H11": "إيقاف تنظيمي",
    "O1": "مشكلة تشغيلية",
    "IPO1": "طرح أولي - لم يبدأ التداول",
    "M1": "إيقاف إدراج",
    "M2": "إيقاف إدراج",
    "D": "شطب السهم",
    "LIQ": "مشكلة سيولة",
}


def _local(tag):
    return tag.rsplit("}", 1)[-1].rsplit(":", 1)[-1]


def _halt_item(f):
    symbol = f.get("IssueSymbol") or f.get("title")
    if not symbol:
        return None
    code = f.get("ReasonCode", "")
    return {
        "symbol": symbol.upper(),
        "name": f.get("IssueName", ""),
        "market": f.get("Market", ""),
        "code": code,
        "reason": REASONS.get(code, code),
        "halt_date": f.get("HaltDate", ""),
        "halt_time": f.get("HaltTime", ""),
        "resume_date": f.get("ResumptionDate", ""),
        "resume_quote": f.get("ResumptionQuoteTime", ""),
        "resume_trade": f.get("ResumptionTradeTime", ""),
        "pause_price": f.get("PauseThresholdPrice", ""),
    }


_ITEM_RE = re.compile(rb"<item\b[^>]*>(.*?)</item>", re.S | re.I)
_FIELD_RE = re.compile(rb"<(?:[\w-]+:)?(\w+)\b[^>]*>(.*?)</(?:[\w-]+:)?\1\s*>", re.S)


def _parse_halts(text):
    try:
        root = ET.fromstring(text)
        fields = [{_local(ch.tag): (ch.text or "").strip() for ch in item}
                  for item in root.iter() if _local(item.tag) == "item"]
    except ET.ParseError:
        # الرد ليس XML سليمًا (وسوم غير مغلقة أو HTML ملتصق): نستخرج العناصر نصيًا
        fields = []
        for block in _ITEM_RE.findall(text):
            f = {}
            for tag, val in _FIELD_RE.findall(block):
                val = re.sub(rb"<!\[CDATA\[(.*?)\]\]>", rb"\1", val, flags=re.S)
                f[tag.decode()] = html.unescape(re.sub(rb"<[^>]+>", b"", val).decode("utf-8", "replace")).strip()
            fields.append(f)
        if not fields:
            raise ValueError(f"الرد ليس RSS: {text[:150]!r}")
    return [h for h in map(_halt_item, fields) if h]


def _clean_xml(raw):
    """يزيل علامة BOM وأي مسافات قبل بداية XML."""
    raw = raw.lstrip(b"\xef\xbb\xbf").lstrip()
    i = raw.find(b"<")
    return raw[i:] if i > 0 else raw


def _looks_like_rss(raw):
    head = raw[:2000].lower()
    return raw.startswith(b"<") and (b"<rss" in head or b"<item" in raw.lower() or b"<channel" in head)


def _fetch_halts_xml():
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36",
        "Accept": "application/rss+xml, application/xml, text/xml, */*",
        "Accept-Encoding": "gzip, deflate",
    }
    raw = b""
    try:
        r = requests.get(HALTS_URL, timeout=10, headers=headers)
        r.raise_for_status()
        raw = _clean_xml(r.content)
        if _looks_like_rss(raw):
            return raw
    except requests.RequestException:
        pass
    # بعض الشبكات/الحمايات ترفض الطلبات البرمجية: نجرّب اتصالًا يشبه المتصفح (curl_cffi يأتي مع yfinance)
    try:
        from curl_cffi import requests as creq
        r = creq.get(HALTS_URL, timeout=10, impersonate="chrome")
        raw = _clean_xml(r.content)
        if _looks_like_rss(raw):
            return raw
    except Exception:
        pass
    raise ValueError(f"الرد ليس RSS: {raw[:150]!r}")


def get_halts():
    """إيقافات اليوم؛ "halted" = ما زال موقوفًا (لا يوجد وقت استئناف للتداول)."""
    now = time.time()
    with _halts_lock:
        wait = HALTS_BACKOFF if _halts["fails"] else HALTS_SECONDS
        if now - _halts["at"] < wait:
            return _halts
    try:
        items = _parse_halts(_fetch_halts_xml())
        today = datetime.now(NY).strftime("%m/%d/%Y")
        for h in items:
            h["today"] = h["halt_date"] == today
            h["halted"] = not h["resume_trade"]
        items.sort(key=lambda h: (h["halt_date"][-4:], h["halt_date"], h["halt_time"]), reverse=True)
        result = {"items": items, "at": now, "error": None, "fails": 0}
    except Exception as e:
        if not _halts["fails"]:  # رسالة واحدة لكل فترة حجب بدل رسالة كل دقيقة
            print(f"⚠️ ناسداك حجب طلب الإيقافات مؤقتًا — إعادة المحاولة كل 5 دقائق ({str(e)[:80]})")
        # نُبقي آخر قائمة ناجحة
        result = {**_halts, "at": now, "error": "ناسداك حجب الطلب مؤقتًا — تُعرض آخر قائمة ناجحة", "fails": _halts["fails"] + 1}
    with _halts_lock:
        _halts.update(result)
    return _halts


def halted_symbols():
    return {h["symbol"] for h in get_halts()["items"] if h.get("halted")}


# ===== الأسهم الحرة (Float) — تُجلب ببطء في الخلفية وتُحفظ على القرص =====

FLOATS_FILE = os.path.join(DATA_DIR, "floats.json")
FLOAT_MAX_AGE = 7 * 86400  # الأسهم الحرة نادرًا ما تتغير
_floats = {}
_floats_state = {"started": False}


def _load_floats():
    try:
        with open(FLOATS_FILE, encoding="utf-8") as f:
            _floats.update(json.load(f))
    except (OSError, ValueError):
        pass


def _save_floats():
    try:
        os.makedirs(DATA_DIR, exist_ok=True)
        tmp = FLOATS_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(_floats, f)
        os.replace(tmp, FLOATS_FILE)
    except OSError as e:
        print(f"⚠️ فشل حفظ الأسهم الحرة: {e}")


def float_for(symbol):
    entry = _floats.get(symbol)
    return entry.get("float") if entry else None


# ===== الفحص الشرعي (تقريبي، على طريقة معايير AAOIFI والمؤشرات الإسلامية) =====
# 1) النشاط: يُستبعد النشاط المحرم أصلًا، ويُعلَّم النشاط المختلط "يحتاج مراجعة"
# 2) النسب: الديون ÷ القيمة السوقية ≤ 30%، والنقد والودائع ÷ القيمة السوقية ≤ 30%
SHARIA_MAX_PCT = 30
_HARAM = {
    "bank": "بنوك تقليدية", "insurance": "تأمين تقليدي", "credit services": "إقراض بفائدة",
    "mortgage": "تمويل عقاري بفائدة", "capital markets": "خدمات مالية تقليدية", "asset management": "إدارة أصول تقليدية",
    "financial conglomerates": "مجموعات مالية تقليدية", "shell compan": "شركة استحواذ (أموالها في ودائع بفائدة)",
    "winer": "خمور", "distiller": "خمور", "brewer": "خمور", "tobacco": "تبغ", "gambling": "قمار", "casino": "قمار وكازينوهات",
}
# كلمات النشاط المحرم في اسم الشركة ووصف نشاطها (من ياهو)، لأن تصنيف القطاع وحده لا يكشف
# مثلًا منتج لحوم خنزير مصنف "أغذية" أو ملاهٍ ليلية مصنفة "مطاعم"
_HARAM_TERMS = [
    (re.compile(r"\b(pork|hogs?|swine|bacon|pig farming)\b", re.I), "لحوم الخنزير", True),
    (re.compile(r"\b(alcoholic beverages?|alcohol beverages?|spirits|whisk(e)?y|vodka|tequila|bourbon|gin|rum|liquors?|wines?|winer(y|ies)|"
                r"beers?|brewer(y|ies|s)?|brewing|distill(ery|eries|ers|ed|ing)|sake|champagne|cider|hard seltzers?)\b", re.I), "خمور ومشروبات روحية", False),
    (re.compile(r"\b(casinos?|gambling|sports? betting|sportsbooks?|lotter(y|ies)|wagering|i-?gaming|slot machines?|bingo|racetracks?|horse racing)\b", re.I), "قمار ومراهنات", True),
    (re.compile(r"\b(night ?clubs?|gentlemen'?s clubs?|adult entertainment|strip clubs?|cabarets?|adult (content|films?|videos?|websites?)|"
                r"pornograph\w*|escort services?|dance clubs?)\b", re.I), "ملاهٍ ليلية ومراقص ومحتوى إباحي", True),
    (re.compile(r"\b(cannabis|marijuana|hemp-derived thc|thc products?)\b", re.I), "حشيش وماريجوانا", True),
    (re.compile(r"\b(tobacco|cigarettes?|cigars?|vap(e|es|ing)|nicotine)\b", re.I), "تبغ ونيكوتين", False),
    (re.compile(r"\b(payday loans?|pawn ?(shops?|loans?)|consumer loans?|title loans?)\b", re.I), "إقراض بفائدة", False),
]


_ABBR = re.compile(r"\b(Inc|Corp|Co|Ltd|Plc|Corp|Bros|No|St|Jr|Sr|U\.S|S\.A|N\.V|L\.P|L\.L\.C)\.", re.I)


def _haram_text(name, summary):
    """(السبب، قطعي؟) من اسم الشركة ووصفها.
    الخنزير والقمار والملاهي والإباحية والحشيش: أي ذكر = غير متوافق.
    الخمور والتبغ والإقراض: في الاسم أو أول جملة أو جملتين فأكثر = نشاط أساسي، ذكر عابر (متجر يبيعها) = مراجعة."""
    name = name or ""
    text = _ABBR.sub(lambda m: m.group(1).replace(".", ""), summary or "")  # "Inc." ليست نهاية جملة
    parts = re.split(r"(?<=[.!?])\s+", text)
    for rx, why, strict in _HARAM_TERMS:
        if rx.search(name):
            return why, True
        sentences = [x for x in parts if rx.search(x)]
        if sentences:
            return why, strict or len(sentences) >= 2 or bool(rx.search(parts[0]))
    return None, False


_REVIEW = {"reit": "صندوق عقاري — يعتمد على مصدر الإيجارات", "entertainment": "ترفيه — يعتمد على المحتوى",
           "aerospace & defense": "صناعات دفاعية — تختلف الفتاوى", "lodging": "فنادق — قد تشمل خمورًا"}


# ===== قائمة شرعية يستوردها المستخدم (مثل قائمة يقين) — المرجع الأول إن وُجدت =====
# تُحفظ على جهاز المستخدم فقط (data/ مستبعد من git)
SHARIA_LIST_FILE = os.path.join(DATA_DIR, "sharia_list.json")
_sharia_list = {"halal": set(), "haram": set(), "source": None, "at": None}
_TICKER = re.compile(r"^[A-Z][A-Z0-9]{0,5}(?:[.\-][A-Z0-9]{1,3})?$")
_YES = {"شرعي", "متوافق", "حلال", "halal", "compliant", "shariah compliant"}
_NO = {"غير شرعي", "غير متوافق", "حرام", "not halal", "not compliant", "non-compliant", "doubtful", "مشكوك"}


def parse_sharia_text(text):
    """يستخرج الرموز من نص صفحة منسوخة: الرمز يتبعه خلال سطرين أو ثلاثة "شرعي" أو "غير شرعي".
    وإن لم توجد هذه الكلمات يُعامل النص كقائمة رموز حلال مفصولة بمسافات أو فواصل."""
    lines = [l.strip() for l in (text or "").splitlines() if l.strip()]
    halal, haram = set(), set()
    for i, line in enumerate(lines):
        if not _TICKER.match(line):
            continue
        for nxt in lines[i + 1:i + 4]:
            low = nxt.lower()
            if low in _NO:
                haram.add(line); break
            if low in _YES:
                halal.add(line); break
    if not halal and not haram:
        halal = {t for t in re.split(r"[\s,;|]+", (text or "").upper()) if _TICKER.match(t)}
    return halal - haram, haram


def _load_sharia_list():
    try:
        with open(SHARIA_LIST_FILE, encoding="utf-8") as f:
            d = json.load(f)
        _sharia_list.update(halal=set(d.get("halal", [])), haram=set(d.get("haram", [])), source=d.get("source"), at=d.get("at"))
    except (OSError, ValueError):
        pass


def set_sharia_list(halal, haram, source):
    _sharia_list.update(halal=set(halal), haram=set(haram), source=source or "قائمة مستوردة", at=int(time.time()))
    os.makedirs(DATA_DIR, exist_ok=True)
    with open(SHARIA_LIST_FILE, "w", encoding="utf-8") as f:
        json.dump({"halal": sorted(halal), "haram": sorted(haram), "source": _sharia_list["source"], "at": _sharia_list["at"]}, f, ensure_ascii=False)


def clear_sharia_list():
    _sharia_list.update(halal=set(), haram=set(), source=None, at=None)
    try:
        os.remove(SHARIA_LIST_FILE)
    except OSError:
        pass


def sharia_list_info():
    return {"source": _sharia_list["source"], "at": _sharia_list["at"],
            "halal": len(_sharia_list["halal"]), "haram": len(_sharia_list["haram"])}


_load_sharia_list()


def sharia_for(symbol):
    """الحكم: من القائمة المستوردة إن وُجدت، وإلا من الفحص التقريبي. يرجع أيضًا نتيجة الفحص للمقارنة."""
    own = _sharia_screen(symbol)
    src = _sharia_list["source"]
    if not src:
        return own
    note = {"ok": "متوافق", "no": "غير متوافق", "review": "يحتاج مراجعة"}.get(own["status"])
    extra = f" · فحص المنصة: {note}" if note else ""
    if symbol in _sharia_list["halal"]:
        return {**own, "status": "ok", "why": f"{src}: شرعي{extra}", "source": src}
    if symbol in _sharia_list["haram"]:
        return {**own, "status": "no", "why": f"{src}: غير شرعي{extra}", "source": src}
    # قائمة الأسهم الحلال فقط: غير المدرج فيها ليس حلالًا عندها (أو لم تقيّمه)
    return {**own, "status": "no", "why": f"غير مدرج في {src} الحلال{extra}", "source": src}


def _sharia_screen(symbol):
    """{"status": ok/no/review/unknown، "why": السبب، "debt_pct"، "cash_pct"} من بيانات الشركة المحفوظة."""
    e = _floats.get(symbol) or {}
    if "sector" not in e:
        return {"status": "unknown", "why": "لم تُجلب بيانات الشركة بعد"}
    if "summary" not in e and not e.get("sector"):
        return {"status": "unknown", "why": "جارٍ تحديث بيانات الشركة"}
    if e.get("quote_type") and e["quote_type"] != "EQUITY":
        return {"status": "unknown", "why": "صندوق/أداة ليست سهم شركة — يُفحص حسب مكوناته"}
    act = f"{e.get('sector') or ''} {e.get('industry') or ''}".lower().replace("—", " ").replace(" - ", " ")
    cap, debt, cash = e.get("market_cap"), e.get("debt"), e.get("cash")
    out = {"industry": e.get("industry"), "sector": e.get("sector")}
    if cap:
        if debt is not None:
            out["debt_pct"] = round(debt / cap * 100, 1)
        if cash is not None:
            out["cash_pct"] = round(cash / cap * 100, 1)
    for k, why in _HARAM.items():
        if k in act:
            return {**out, "status": "no", "why": f"النشاط: {why}"}
    why, core = _haram_text(e.get("name"), e.get("summary"))
    if why and core:
        return {**out, "status": "no", "why": f"النشاط: {why}"}
    mention = f"وصف الشركة يذكر {why} — قد يكون جزءًا صغيرًا من الإيرادات (يُقبل عند كثير من الهيئات إن قل عن 5% مع التطهير)" if why else None
    bad = [f"الديون {out['debt_pct']}% من القيمة السوقية (الحد {SHARIA_MAX_PCT}%)"] if out.get("debt_pct", 0) > SHARIA_MAX_PCT else []
    if out.get("cash_pct", 0) > SHARIA_MAX_PCT:
        bad.append(f"النقد والودائع {out['cash_pct']}% من القيمة السوقية (الحد {SHARIA_MAX_PCT}%)")
    if bad:
        return {**out, "status": "no", "why": " · ".join(bad)}
    if mention:
        return {**out, "status": "review", "why": mention}
    for k, why in _REVIEW.items():
        if k in act:
            return {**out, "status": "review", "why": why}
    if not cap or debt is None or not e.get("industry"):
        return {**out, "status": "unknown", "why": "بيانات الديون أو النشاط غير متوفرة من ياهو"}
    return {**out, "status": "ok", "why": f"النشاط مباح · الديون {out.get('debt_pct', 0)}% · النقد {out.get('cash_pct', 0)}%"}


_pending_info = set()


def _fetch_info(s):
    info = yf.Ticker(s).info or {}
    _floats[s] = {
        "float": info.get("floatShares"), "at": time.time(),
        "sector": info.get("sector"), "industry": info.get("industry"), "quote_type": info.get("quoteType"),
        "debt": info.get("totalDebt"), "cash": info.get("totalCash"), "market_cap": info.get("marketCap"),
        "name": info.get("longName") or info.get("shortName"), "summary": (info.get("longBusinessSummary") or "")[:2500],
    }


def ensure_info(symbol):
    """صفحة سهم فُتحت: اجلب بياناته (الأسهم الحرة والفحص الشرعي) الآن بدل انتظار دوره."""
    e = _floats.get(symbol)
    if (e and "summary" in e) or symbol in _pending_info:
        return
    _pending_info.add(symbol)

    def run():
        try:
            _fetch_info(symbol)
            _save_floats()
        except Exception as ex:
            print(f"⚠️ فشل جلب بيانات {symbol}: {ex}")
        finally:
            _pending_info.discard(symbol)
    threading.Thread(target=run, daemon=True).start()


_info_state = {"total": 0}


def info_progress():
    """كم شركة اكتملت بياناتها (للفحص الشرعي) من أسهم السوق."""
    done = sum(1 for e in list(_floats.values()) if "summary" in e)
    return {"done": done, "total": max(_info_state["total"], done)}


def _floats_loop(get_symbols):
    while True:
        symbols = get_symbols()
        _info_state["total"] = len(symbols)
        now = time.time()
        # البيانات القديمة بلا قطاع تُعاد لإضافة الفحص الشرعي
        todo = [s for s in symbols if s not in _floats or now - _floats[s].get("at", 0) > FLOAT_MAX_AGE or "summary" not in _floats[s]]
        for i, s in enumerate(todo):
            try:
                _fetch_info(s)  # الأسهم الحرة + النشاط والديون والنقد للفحص الشرعي
            except Exception as e:
                if "rate" in str(e).lower() or "too many" in str(e).lower():
                    # ياهو يحد الطلبات: توقف 10 دقائق حتى لا يتأثر تحديث الأسعار
                    print("⏳ ياهو يحد الطلبات — إيقاف جلب الأسهم الحرة 10 دقائق")
                    _save_floats()
                    time.sleep(600)
                    continue
                _floats[s] = {"float": None, "sector": None, "at": time.time() - FLOAT_MAX_AGE + 86400}  # أعد المحاولة غدًا
            if i % 20 == 19:
                _save_floats()
            time.sleep(2)  # هادئ عمدًا: الأسعار أهم؛ وعند حد ياهو يتوقف 10 دقائق تلقائيًا
        if todo:
            _save_floats()
        time.sleep(600)


def start_floats(get_symbols):
    """get_symbols: دالة ترجع رموز السوق الحالية مرتبة حسب الأولوية (الأكثر حركة أولًا)."""
    if _floats_state["started"]:
        return
    _floats_state["started"] = True
    _load_floats()
    threading.Thread(target=_floats_loop, args=(get_symbols,), daemon=True).start()
