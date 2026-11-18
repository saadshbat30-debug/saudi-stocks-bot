"""أخبار الأسهم من Alpaca (Benzinga، لحظية) بدل ياهو حين DATA_SOURCE=alpaca.

GET https://data.alpaca.markets/v1beta1/news?symbols=…&limit=… — قراءة فقط.
ترجع الأخبار بالشكل الذي تعرضه المنصة: {title, url, publisher, time, summary}.
"""
import html
import re
from datetime import datetime

import alpaca_stream as al

URL = "https://data.alpaca.markets/v1beta1/news"
_TAGS = re.compile(r"<[^>]+>")
_http = None


def _session():
    global _http
    if _http is None:
        import requests
        _http = requests.Session()
    return _http


def _text(s):
    return " ".join(html.unescape(_TAGS.sub(" ", s or "")).split())


def news(symbol, count=12, cfg=None):
    cfg = cfg or al.config()
    r = _session().get(URL, params={"symbols": symbol.replace("-", "."), "limit": min(count, 50), "sort": "desc"}, timeout=20,
                       headers={"APCA-API-KEY-ID": cfg["key"], "APCA-API-SECRET-KEY": cfg["secret"]})
    if r.status_code != 200:
        raise RuntimeError(f"Alpaca HTTP {r.status_code}: {' '.join(r.text.split())[:120]}")
    out = []
    for a in r.json().get("news") or []:
        if not a.get("headline"):
            continue
        t = a.get("created_at") or a.get("updated_at")
        out.append({
            "title": _text(a["headline"]),
            "url": a.get("url") or f"https://www.benzinga.com/search?q={symbol}",
            "publisher": a.get("source") or "Benzinga",
            "time": int(datetime.fromisoformat(t.replace("Z", "+00:00")).timestamp()) if t else None,
            "summary": _text(a.get("summary"))[:300],
        })
    return out
