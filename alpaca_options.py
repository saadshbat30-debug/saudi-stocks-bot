"""عقود الخيارات من Alpaca (OPRA، لحظي مع Algo Trader Plus) بدل ياهو حين DATA_SOURCE=alpaca.

- تواريخ الانتهاء والعقود المفتوحة (open interest) وإغلاق أمس: GET paper-api…/v2/options/contracts (قراءة فقط).
- السعر (عرض/طلب/آخر صفقة) وحجم اليوم والتذبذب الضمني واليونانيات: GET data…/v1beta1/options/snapshots/{الرمز}.
ترجع الصفوف بأسماء أعمدة ياهو نفسها، فتبقى حسابات صفحة العقود (options._rows) كما هي.
"""
import re
import time
from datetime import datetime, timezone

import alpaca_stream as al

CHAIN_URL = "https://data.alpaca.markets/v1beta1/options/snapshots/{}"
OCC = re.compile(r"^([A-Z.]+?)(\d{6})([CP])(\d{8})$")
_http = None


def _session():
    global _http
    if _http is None:
        import requests
        _http = requests.Session()
    return _http


def _get(url, params, cfg):
    r = _session().get(url, params=params, timeout=20,
                       headers={"APCA-API-KEY-ID": cfg["key"], "APCA-API-SECRET-KEY": cfg["secret"]})
    if r.status_code != 200:
        raise RuntimeError(f"Alpaca HTTP {r.status_code}: {' '.join(r.text.split())[:120]}")
    return r.json()


def parse_occ(symbol):
    """AAPL261218C00190000 ← (الأصل، تاريخ الانتهاء YYYY-MM-DD، call/put، الإضراب)."""
    m = OCC.match(symbol)
    if not m:
        return None
    root, ymd, cp, strike = m.groups()
    return root, f"20{ymd[:2]}-{ymd[2:4]}-{ymd[4:]}", "call" if cp == "C" else "put", int(strike) / 1000


def contracts(symbol, cfg, date=None):
    """العقود النشطة للأصل (كل التواريخ، أو تاريخ واحد): [{symbol, expiration_date, type, strike_price, open_interest, close_price}]."""
    params = {"underlying_symbols": symbol.replace("-", "."), "status": "active", "limit": 10000}
    if date:
        params["expiration_date"] = date
    else:
        params["expiration_date_gte"] = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    out = []
    while True:
        d = _get(cfg["trading_url"] + "/v2/options/contracts", params, cfg)
        out.extend(d.get("option_contracts") or [])
        if not d.get("next_page_token"):
            return out
        params["page_token"] = d["next_page_token"]


def expirations(symbol, cfg=None):
    cfg = cfg or al.config()
    return sorted({c["expiration_date"] for c in contracts(symbol, cfg)})


def chain(symbol, date, cfg=None):
    """(صفوف الكول، صفوف البوت) بأعمدة ياهو: contractSymbol, strike, bid, ask, lastPrice, change, percentChange,
    volume, openInterest, impliedVolatility, lastTradeDate. اليونانيات تحسبها المنصة من التذبذب الضمني كما مع ياهو."""
    cfg = cfg or al.config()
    info = {c["symbol"]: c for c in contracts(symbol, cfg, date)}
    params = {"feed": "opra", "expiration_date": date, "limit": 1000}
    snaps = {}
    while True:
        d = _get(CHAIN_URL.format(symbol.replace("-", ".")), params, cfg)
        snaps.update(d.get("snapshots") or {})
        if not d.get("next_page_token"):
            break
        params["page_token"] = d["next_page_token"]
    calls, puts = [], []
    for sym in set(snaps) | set(info):
        parsed = parse_occ(sym)
        if not parsed or parsed[1] != date:
            continue
        _, _, kind, strike = parsed
        s, c = snaps.get(sym) or {}, info.get(sym) or {}
        q, t, day, prev = s.get("latestQuote") or {}, s.get("latestTrade") or {}, s.get("dailyBar") or {}, s.get("prevDailyBar") or {}
        last = t.get("p")
        prev_close = prev.get("c") or _num(c.get("close_price"))
        when = t.get("t")
        row = {
            "contractSymbol": sym, "strike": strike, "bid": q.get("bp"), "ask": q.get("ap"), "lastPrice": last,
            "change": last - prev_close if last is not None and prev_close else None,
            "percentChange": (last / prev_close - 1) * 100 if last is not None and prev_close else None,
            "volume": day.get("v") or 0, "openInterest": _num(c.get("open_interest")) or 0,
            "impliedVolatility": s.get("impliedVolatility"),
            "lastTradeDate": _Stamp(when) if when else None,
        }
        (calls if kind == "call" else puts).append(row)
    calls.sort(key=lambda r: r["strike"])
    puts.sort(key=lambda r: r["strike"])
    return calls, puts


def _num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


class _Stamp:
    """وقت آخر صفقة بواجهة .timestamp() التي تتوقعها options._rows."""

    def __init__(self, rfc3339):
        self._t = al.ts(rfc3339) if isinstance(rfc3339, str) else float(rfc3339)

    def timestamp(self):
        return self._t


class Frame:
    """بديل DataFrame الذي يقرؤه options._rows (to_dict("records"))."""

    def __init__(self, rows):
        self.rows = rows

    def to_dict(self, orient="records"):
        return self.rows
