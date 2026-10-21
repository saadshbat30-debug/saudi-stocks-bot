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
        if not items:
            print(f"ℹ️ لا توجد أخبار من ياهو لـ {symbol}")
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
HALTS_SECONDS = 30
_halts = {"items": [], "at": 0, "error": None}
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
        if now - _halts["at"] < HALTS_SECONDS:
            return _halts
    try:
        items = _parse_halts(_fetch_halts_xml())
        today = datetime.now(NY).strftime("%m/%d/%Y")
        for h in items:
            h["today"] = h["halt_date"] == today
            h["halted"] = not h["resume_trade"]
        items.sort(key=lambda h: (h["halt_date"][-4:], h["halt_date"], h["halt_time"]), reverse=True)
        result = {"items": items, "at": now, "error": None}
    except Exception as e:
        print(f"⚠️ فشل جلب إيقافات التداول: {e}")
        result = {**_halts, "at": now, "error": "تعذر جلب الإيقافات من Nasdaq"}
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


def _floats_loop(get_symbols):
    while True:
        symbols = get_symbols()
        now = time.time()
        todo = [s for s in symbols if s not in _floats or now - _floats[s].get("at", 0) > FLOAT_MAX_AGE]
        for i, s in enumerate(todo):
            try:
                info = yf.Ticker(s).info or {}
                _floats[s] = {"float": info.get("floatShares"), "at": time.time()}
            except Exception as e:
                if "rate" in str(e).lower() or "too many" in str(e).lower():
                    # ياهو يحد الطلبات: توقف 10 دقائق حتى لا يتأثر تحديث الأسعار
                    print("⏳ ياهو يحد الطلبات — إيقاف جلب الأسهم الحرة 10 دقائق")
                    _save_floats()
                    time.sleep(600)
                    continue
                _floats[s] = {"float": None, "at": time.time() - FLOAT_MAX_AGE + 86400}  # أعد المحاولة غدًا
            if i % 20 == 19:
                _save_floats()
            time.sleep(4)  # بطيء عمدًا: الأسعار أهم من الأسهم الحرة
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
