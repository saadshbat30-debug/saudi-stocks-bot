"""أخبار فورية مجانية من المصدر مباشرة (أسرع من ياهو بدقائق):

1. وكالات البيانات الصحفية (GlobeNewswire، PR Newswire، Business Wire): الشركات تنشر أخبارها هنا أولًا
   (FDA، طرح أسهم، عقود، نتائج)، ونستخرج رمز السهم من صيغة "(NASDAQ: ABCD)" في نص البيان.
2. نماذج 8-K في هيئة SEC: إفصاح إلزامي عن أي حدث جوهري (إنذار شطب، بيع أسهم، إفلاس، اتفاقية...).

تُفحص كل دقيقة. يمكن تغيير قائمة الخلاصات بالمتغير WIRE_FEEDS (روابط RSS مفصولة بفواصل).
"""
import html
import os
import re
import threading
import time
from collections import deque
from email.utils import parsedate_to_datetime

import requests

POLL_SECONDS = 60
FEEDS = [
    ("GlobeNewswire", "https://www.globenewswire.com/RssFeed/orgclass/1/feedTitle/GlobeNewswire%20-%20News%20about%20Public%20Companies"),
    ("PR Newswire", "https://www.prnewswire.com/rss/news-releases-list.rss"),
    ("Business Wire", "https://feed.businesswire.com/rss/home/?rss=G1QFDERJXkJeEFpRWQ=="),
]
if os.environ.get("WIRE_FEEDS"):
    FEEDS = [(u.split("/")[2], u.strip()) for u in os.environ["WIRE_FEEDS"].split(",") if u.strip()]
SEC_8K = "https://www.sec.gov/cgi-bin/browse-edgar?action=getcurrent&type=8-K&company=&dateb=&owner=include&start=0&count=100&output=atom"
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36",
      "Accept": "application/rss+xml, application/xml, text/xml, */*"}

# رمز السهم كما يُكتب في البيانات الصحفية: (NASDAQ: ABCD) أو (Nasdaq Capital Market: ABCD) أو (NYSE American: ABC)
_TICK = re.compile(r"\b(NASDAQ|Nasdaq|NYSE American|NYSE MKT|NYSE Arca|NYSE|AMEX|Cboe|CBOE|OTCQB|OTCQX|OTC Pink|OTC)"
                   r"(?:\s+(?:Capital Market|Global Market|Global Select Market|Global Select|Markets?))?\s*:\s*\$?([A-Z][A-Z0-9]{0,5}(?:\.[A-Z])?)\b")
_ITEM = re.compile(r"<item\b[^>]*>(.*?)</item>", re.S | re.I)
_ENTRY = re.compile(r"<entry>(.*?)</entry>", re.S)

# بنود 8-K وما تعنيه (عناوين إنجليزية حتى يقرأها مقيّم الأخبار بنفس قواعده)
ITEMS_8K = {
    "1.01": "Entry into a Material Definitive Agreement", "1.02": "Termination of a Material Definitive Agreement",
    "1.03": "Bankruptcy or Receivership", "2.01": "Completion of Acquisition or Disposition of Assets",
    "2.02": "Results of Operations and Financial Condition", "2.04": "Triggering Events That Accelerate a Financial Obligation (default)",
    "2.06": "Material Impairments", "3.01": "Notice of Failure to Satisfy a Continued Listing Rule or Standard",
    "3.02": "Unregistered Sales of Equity Securities", "3.03": "Material Modification to Rights of Security Holders",
    "4.01": "Changes in Registrant's Certifying Accountant", "4.02": "Non-Reliance on Previously Issued Financial Statements (restatement)",
    "5.01": "Changes in Control of Registrant", "5.02": "Departure or Appointment of Directors or Officers",
    "5.03": "Amendments to Articles of Incorporation or Bylaws", "7.01": "Regulation FD Disclosure", "8.01": "Other Events",
}
# بنود روتينية لا تستحق التنبيه وحدها
ROUTINE_8K = {"7.01", "8.01", "9.01", "5.07", "5.02", "2.02", "3.03", "5.03"}

_items = deque(maxlen=800)
_seen = set()
_state = {"started": False, "feeds": {}, "at": 0}
_lock = threading.Lock()


def _get(url, headers=UA):
    try:
        r = requests.get(url, timeout=12, headers=headers)
        if r.ok and ("<item" in r.text or "<entry" in r.text):
            return r.text
    except requests.RequestException:
        pass
    from curl_cffi import requests as creq
    return creq.get(url, timeout=12, headers=headers, impersonate="chrome").text


def _field(block, tag):
    m = re.search(rf"<{tag}\b[^>]*>(.*?)</{tag}\s*>", block, re.S | re.I)
    if not m:
        return ""
    v = m.group(1).strip()
    if v.startswith("<![CDATA["):
        v = v[9:].rsplit("]]>", 1)[0]
    return html.unescape(re.sub(r"<[^>]+>", " ", html.unescape(v))).strip()


def _ts(s):
    try:
        return int(parsedate_to_datetime(s).timestamp())
    except (TypeError, ValueError):
        try:
            from datetime import datetime
            return int(datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp())
        except ValueError:
            return int(time.time())


def _tickers(text):
    """الرموز مع بورصة كل رمز: {"ABCD": True إن كان OTC}."""
    out = {}
    for ex, sym in _TICK.findall(text or ""):
        out.setdefault(sym.replace(".", "-"), ex.upper().startswith("OTC"))
    return dict(list(out.items())[:3])


def _poll_feed(name, url):
    text = _get(url)
    got = 0
    for block in _ITEM.findall(text):
        link, title = _field(block, "link"), _field(block, "title")
        if not link or not title or link in _seen:
            continue
        desc = _field(block, "description")
        syms = _tickers(f"{title} {desc}")
        _seen.add(link)
        if not syms:
            continue  # بيان لشركة غير مدرجة أو بلا رمز
        got += 1
        _add({"title": title, "url": link, "publisher": name, "time": _ts(_field(block, "pubDate")),
              "summary": desc[:300], "symbols": syms, "source": "wire"})
    return got


def _poll_8k(ciks):
    from corp_events import SEC_UA, _TITLE, _US, _EXCHANGE
    text = _get(SEC_8K, headers=SEC_UA)
    got = 0
    for e in _ENTRY.findall(text):
        link = re.search(r'<link[^>]*href="([^"]+)"', e)
        link = link.group(1) if link else ""
        m = _TITLE.search(e)
        if not m or not link or link in _seen or _EXCHANGE.search(m.group(2)):
            continue
        _seen.add(link)
        sym = ciks.get(int(m.group(3)))
        if not sym or not _US.fullmatch(sym):
            continue
        items = re.findall(r"Item\s+(\d\.\d\d)", _field(e, "summary"))
        if not items or set(items) <= ROUTINE_8K:
            continue  # إفصاح روتيني (عرض تقديمي، نتائج سبق نشرها، تعيينات...)
        names = [ITEMS_8K[i] for i in items if i in ITEMS_8K and i not in ("9.01",)]
        upd = re.search(r"<updated>([^<]+)</updated>", e)
        got += 1
        _add({"title": f"8-K: {html.unescape(m.group(2))} — " + "; ".join(names), "url": link, "publisher": "SEC 8-K",
              "time": _ts(upd.group(1)) if upd else int(time.time()), "summary": "بنود: " + ", ".join(items),
              "symbols": {sym: False}, "source": "8-K"})
    return got


def _add(a):
    import news_score
    from market import row_for
    for sym, is_otc in a["symbols"].items():
        r = row_for(sym) or {}
        sc = news_score.score(a["title"], a["summary"], {"change_pct": r.get("change_pct"), "vol_ratio": r.get("vol_ratio"),
                                                          "market_cap": r.get("market_cap"), "publisher": a["publisher"], "time": a["time"]})
        with _lock:
            _items.appendleft({**sc, "symbol": sym, "name": r.get("name") or "", "title": a["title"], "url": a["url"],
                               "time": a["time"], "publisher": a["publisher"], "summary": a["summary"], "source": a["source"],
                               "price": r.get("price"), "change_pct": r.get("change_pct"), "vol_ratio": r.get("vol_ratio"),
                               "market_cap": r.get("market_cap"), "float": r.get("float"),
                               "otc": r.get("otc") if r else is_otc, "sharia": r.get("sharia")})
        import news_track
        news_track.record(_items[0])  # تسجيل لحظة الوصول لاختبار الخوارزمية لاحقًا


def _loop():
    from corp_events import _cik_map
    while True:
        for name, url in FEEDS:
            try:
                n = _poll_feed(name, url)
                _state["feeds"][name] = {"ok": True, "new": n, "at": int(time.time())}
            except Exception as e:
                _state["feeds"][name] = {"ok": False, "error": str(e)[:120], "at": int(time.time())}
        try:
            n = _poll_8k(_cik_map())
            _state["feeds"]["SEC 8-K"] = {"ok": True, "new": n, "at": int(time.time())}
        except Exception as e:
            _state["feeds"]["SEC 8-K"] = {"ok": False, "error": str(e)[:120], "at": int(time.time())}
        _state["at"] = time.time()
        if len(_seen) > 20000:
            _seen.clear()
        time.sleep(POLL_SECONDS)


def start():
    with _lock:
        if _state["started"]:
            return
        _state["started"] = True
    threading.Thread(target=_loop, daemon=True).start()


def items(hours=48):
    cutoff = time.time() - hours * 3600
    with _lock:
        return [i for i in _items if (i["time"] or 0) >= cutoff]


def status():
    return {"feeds": _state["feeds"], "at": _state["at"], "count": len(_items)}
