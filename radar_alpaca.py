"""مصدر رادار الزخم من Alpaca (SIP، لحظي) بدل ماسح TradingView (المتأخر 15 دقيقة) حين DATA_SOURCE=alpaca.

يرجع الصفوف بأسماء حقول TradingView نفسها، فتبقى شروط الرادار وأوزانه في momentum_radar.py كما هي:
- السعر والتغير وحجم اليوم وVWAP: من جدول المنصة (بث Alpaca: شموع اليوم لكل السوق، وصفقات للأسهم المتابَعة).
- الحجم النسبي 10 أيام: حجم اليوم ÷ متوسط آخر 10 أيام من شموع Alpaca اليومية.
- RSI 14 وMACD (12،26،9) وEMA20 وEMA50: من إغلاقات Alpaca اليومية (حتى 70 يومًا) + السعر الحالي. TradingView
  يحسب EMA وMACD من تاريخ أطول، فقد تختلف القيم قليلًا في الكسور.
- تغير 5/15/60 دقيقة وأعلى 15 دقيقة: من شموع Alpaca للدقيقة لآخر 65 دقيقة (طلب واحد لكل 100 مرشح).
"""
import time
from datetime import datetime, timedelta, timezone

import alpaca_daily
import alpaca_stream as al

BARS_URL = "https://data.alpaca.markets/v2/stocks/bars"
EXCHANGES = {"NMS": "NASDAQ", "NGM": "NASDAQ", "NCM": "NASDAQ", "NAS": "NASDAQ", "NYQ": "NYSE", "NYS": "NYSE",
             "ASE": "AMEX", "PCX": "AMEX", "BTS": "AMEX"}
_http = None


def _session():
    global _http
    if _http is None:
        import requests
        _http = requests.Session()
    return _http


def ema(values, n):
    if len(values) < n:
        return None
    k, e = 2 / (n + 1), sum(values[:n]) / n
    for v in values[n:]:
        e = v * k + e * (1 - k)
    return e


def macd(closes):
    """(خط MACD، خط الإشارة) بإعدادات 12/26/9، أو (None، None) إن قل التاريخ عن 35 يومًا."""
    if len(closes) < 35:
        return None, None
    k12, k26, k9 = 2 / 13, 2 / 27, 2 / 10
    e12, e26 = sum(closes[:12]) / 12, sum(closes[:26]) / 26
    for v in closes[12:26]:
        e12 = v * k12 + e12 * (1 - k12)
    line = []
    for v in closes[26:]:
        e12 = v * k12 + e12 * (1 - k12)
        e26 = v * k26 + e26 * (1 - k26)
        line.append(e12 - e26)
    sig = sum(line[:9]) / 9
    for v in line[9:]:
        sig = v * k9 + sig * (1 - k9)
    return line[-1], sig


def minute_bars(symbols, cfg, minutes=65):
    """{الرمز: [(وقت البداية بالثواني، الأعلى، الإغلاق)]} لآخر minutes دقيقة."""
    start = (datetime.now(timezone.utc) - timedelta(minutes=minutes)).strftime("%Y-%m-%dT%H:%M:%SZ")
    headers = {"APCA-API-KEY-ID": cfg["key"], "APCA-API-SECRET-KEY": cfg["secret"]}
    out = {}
    for i in range(0, len(symbols), 100):
        batch = {s.replace("-", "."): s for s in symbols[i:i + 100]}
        params = {"symbols": ",".join(batch), "timeframe": "1Min", "start": start, "limit": 10000, "feed": cfg["feed"], "sort": "asc"}
        while True:
            r = _session().get(BARS_URL, params=params, headers=headers, timeout=20)
            if r.status_code != 200:
                raise RuntimeError(f"Alpaca HTTP {r.status_code}: {' '.join(r.text.split())[:120]}")
            d = r.json()
            for sym, rows in (d.get("bars") or {}).items():
                out.setdefault(batch.get(sym, sym), []).extend(
                    (datetime.strptime(b["t"][:19], "%Y-%m-%dT%H:%M:%S").replace(tzinfo=timezone.utc).timestamp(), b["h"], b["c"])
                    for b in rows)
            if not d.get("next_page_token"):
                break
            params["page_token"] = d["next_page_token"]
    return out


def _change(bars, price, now, minutes):
    """تغير السعر % خلال آخر minutes دقيقة: السعر الآن مقابل إغلاق آخر شمعة انتهت قبل ذلك."""
    cut = now - minutes * 60
    before = [c for t, h, c in bars if t + 60 <= cut]
    return (price / before[-1] - 1) * 100 if before and before[-1] else None


def scan(min_price, max_price, min_change, min_volume, min_rvol, rows=None, now=None):
    """صفوف بحقول TradingView للأسهم التي تجتاز شروط الرادار الأولية، من بيانات Alpaca."""
    if rows is None:
        import market
        rows = market.get_market("us")
    now = now or time.time()
    cand = []
    for r in rows:
        if r.get("otc") or r.get("src") != "alpaca" or EXCHANGES.get(r.get("exchange")) is None:
            continue
        price, chg, vol = r.get("price"), r.get("change_pct"), r.get("volume") or 0
        hist = alpaca_daily.get(r["symbol"])
        if not price or chg is None or not hist or len(hist["vols"]) < 10:
            continue
        avg10 = sum(hist["vols"][-10:]) / 10
        rvol = vol / avg10 if avg10 else None
        if not (min_price <= price <= max_price and chg >= min_change and vol >= min_volume and rvol and rvol >= min_rvol):
            continue
        cand.append((r, hist, rvol))
    cand.sort(key=lambda x: -x[2])
    cand = cand[:400]  # مثل حد الماسح في TradingView
    bars = minute_bars([r["symbol"] for r, _, _ in cand], al.config()) if cand else {}
    c = al.client()
    out = []
    for r, hist, rvol in cand:
        sym, price = r["symbol"], r["price"]
        closes = hist["closes"] + [price]
        b = bars.get(sym, [])
        recent = [h for t, h, cl in b if t + 60 > now - 15 * 60]
        live = c.live(sym) if c else None
        line, sig = macd(closes)
        out.append({
            "ticker": f"{EXCHANGES[r['exchange']]}:{sym}", "name": sym, "exchange": EXCHANGES[r["exchange"]],
            "close": price, "change": r["change_pct"], "volume": r["volume"], "relative_volume_10d_calc": rvol,
            "market_cap_basic": r.get("market_cap"), "sector": None, "update_mode": "alpaca_sip",
            "high|15": max(recent + [price]) if recent else None,
            "change|5": _change(b, price, now, 5), "change|15": _change(b, price, now, 15), "change|60": _change(b, price, now, 60),
            "RSI": _rsi(closes), "MACD.macd": line, "MACD.signal": sig, "EMA20": ema(closes, 20), "EMA50": ema(closes, 50),
            "VWAP": (live or {}).get("vwap"),
        })
    return out


def _rsi(closes):
    import market
    return market.rsi(closes)
