"""شموع الشارت من Alpaca (REST، مصدر SIP) — تُستعمل بدل ياهو حين DATA_SOURCE=alpaca.

GET https://data.alpaca.markets/v2/stocks/{symbol}/bars?timeframe=…&feed=sip&adjustment=split
قراءة فقط بالمفاتيح نفسها (APCA_API_KEY_ID / APCA_API_SECRET_KEY من .env). لا أوامر.
الشموع تُرجع بشكل شموع المنصة: {"time": ثوانٍ + فرق الرياض، "open","high","low","close","volume"}.
"""
import os
import threading
import time
from datetime import datetime, timedelta, timezone

import alpaca_stream as al

DATA_URL = "https://data.alpaca.markets/v2/stocks/{}/bars"
RIYADH_OFFSET = 3 * 3600

# فاصل المنصة ← (إطار Alpaca، أيام للخلف) — المدد نفسها التي تطلبها المنصة من ياهو
TIMEFRAMES = {"1m": ("1Min", 4), "5m": ("5Min", 8), "15m": ("15Min", 32), "30m": ("30Min", 32), "1h": ("1Hour", 95),
              "1d": ("1Day", 740), "1w": ("1Week", 3660), "1mo": ("1Month", 7300)}
INTRADAY = {"1m", "5m", "15m", "30m", "1h"}
_sem = threading.Semaphore(4)  # طلبات متزامنة كحد أقصى (الماسح يطلب عدة أسهم معًا)
_session = None
_blocked = {"until": 0.0, "why": None}  # رفض المفتاح أو الاشتراك: لا يُعاد الطلب مع كل شارت


def _http():
    global _session
    if _session is None:
        import requests
        _session = requests.Session()
    return _session


def supported(symbol, interval):
    return interval in TIMEFRAMES and symbol and "." not in symbol and "^" not in symbol and "=" not in symbol


def _ts(s):
    return int(datetime.strptime(s[:19], "%Y-%m-%dT%H:%M:%S").replace(tzinfo=timezone.utc).timestamp())


def bars(symbol, interval, prepost=False, cfg=None):
    """شموع الرمز بالفاصل المطلوب. ترفع استثناء عند الفشل (المنصة تعود إلى ياهو وتذكر السبب)."""
    if time.time() < _blocked["until"]:
        raise RuntimeError(f"{_blocked['why']} (إيقاف مؤقت لطلبات Alpaca)")
    cfg = cfg or al.config()
    tf, days = TIMEFRAMES[interval]
    start = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%SZ")
    params = {"timeframe": tf, "start": start, "limit": 10000, "feed": cfg["feed"], "adjustment": "split", "sort": "asc"}
    headers = {"APCA-API-KEY-ID": cfg["key"], "APCA-API-SECRET-KEY": cfg["secret"]}
    rows = []
    with _sem:
        while True:
            r = _http().get(DATA_URL.format(symbol.replace("-", ".")), params=params, headers=headers, timeout=20)
            if r.status_code in (401, 403):  # المفتاح أو الاشتراك: يتوقف 10 دقائق بدل تكرار الرفض
                _blocked.update(until=time.time() + 600, why=f"Alpaca HTTP {r.status_code}")
            if r.status_code != 200:
                raise RuntimeError(f"Alpaca HTTP {r.status_code}: {' '.join(r.text.split())[:120]}")
            d = r.json()
            rows.extend(d.get("bars") or [])
            if not d.get("next_page_token"):
                break
            params["page_token"] = d["next_page_token"]
    out = []
    for b in rows:
        t = _ts(b["t"])
        if interval in INTRADAY and not prepost:
            ny = datetime.fromtimestamp(t, al_ny())
            if not ((9, 30) <= (ny.hour, ny.minute) < (16, 0)):
                continue
        out.append({"time": t + RIYADH_OFFSET, "open": float(b["o"]), "high": float(b["h"]), "low": float(b["l"]),
                    "close": float(b["c"]), "volume": int(b.get("v") or 0)})
    return out


def al_ny():
    from zoneinfo import ZoneInfo
    return ZoneInfo("America/New_York")


SNAP_URL = "https://data.alpaca.markets/v2/stocks/snapshots"


def snapshots(symbols, feed, cfg=None):
    """{رمز المنصة: لقطة} — آخر صفقة وآخر سعر وشمعة الدقيقة لكل رمز. feed: overnight للتداول الليلي، otc…"""
    if time.time() < _blocked["until"]:
        raise RuntimeError(f"{_blocked['why']} (إيقاف مؤقت لطلبات Alpaca)")
    cfg = cfg or al.config()
    headers = {"APCA-API-KEY-ID": cfg["key"], "APCA-API-SECRET-KEY": cfg["secret"]}
    out = {}
    for i in range(0, len(symbols), 100):
        batch = {s.replace("-", "."): s for s in symbols[i:i + 100] if "." not in s and "^" not in s}
        if not batch:
            continue
        with _sem:
            r = _http().get(SNAP_URL, params={"symbols": ",".join(batch), "feed": feed}, headers=headers, timeout=20)
        if r.status_code in (401, 403):
            _blocked.update(until=time.time() + 600, why=f"Alpaca HTTP {r.status_code}")
        if r.status_code != 200:
            raise RuntimeError(f"Alpaca HTTP {r.status_code}: {' '.join(r.text.split())[:120]}")
        d = r.json()
        for sym, snap in (d.get("snapshots") or d).items():
            if isinstance(snap, dict):
                out[batch.get(sym, sym)] = snap
    return out
