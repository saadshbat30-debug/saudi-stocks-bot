"""ماسح الزخم والماكد: يحسب المؤشرات على شموع الفاصل المختار لأسهم الزخم من قائمة السوق.

- المؤشرات نفسها بمعادلات نافذة MACD وRSI أسفل الشارت (static/indicators.js) حتى تطابق ما تراه على الشارت.
- تُحسب على الشموع **المغلقة فقط**: الشمعة الجارية تُستبعد فلا تتغير الإشارة قبل إغلاقها.
- لا يغيّر أي شرط في الفلاتر الأخرى؛ الشروط هنا يختارها المستخدم من الصفحة.
"""
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from zoneinfo import ZoneInfo

NY = ZoneInfo("America/New_York")
RIYADH_OFFSET = 3 * 3600  # أوقات الشموع في المنصة مزاحة لتوقيت الرياض
STEP = {"1m": 60, "5m": 300, "15m": 900, "30m": 1800, "1h": 3600, "1d": 86400}
INTERVALS = list(STEP)
POOL_SIZE = 30      # عدد الأسهم المفحوصة في كل مرة (لكل سهم طلب شموع)
CACHE_SECONDS = 45  # نتيجة الفحص تُعاد لنفس الفاصل والمجموعة خلال هذه المدة
MIN_VALUE = 100000  # نفس حد «نشاط حقيقي» في الصفحة الرئيسية


def ema(values, n):
    """مطابق لـ ema() في static/indicators.js: البداية متوسط بسيط لأول n قيمة."""
    out, k, prev, total, count = [None] * len(values), 2 / (n + 1), None, 0.0, 0
    for i, v in enumerate(values):
        if v is None:
            continue
        if prev is None:
            total += v
            count += 1
            if count == n:
                prev = total / n
                out[i] = prev
        else:
            prev = v * k + prev * (1 - k)
            out[i] = prev
    return out


def macd(closes, fast=12, slow=26, signal=9):
    f, s = ema(closes, fast), ema(closes, slow)
    line = [a - b if a is not None and b is not None else None for a, b in zip(f, s)]
    sig = ema(line, signal)
    hist = [a - b if a is not None and b is not None else None for a, b in zip(line, sig)]
    return line, sig, hist


def rsi_series_last(closes, n=14):
    """RSI آخر شمعة بطريقة Wilder (نفس rsi() في indicators.js وmarket.rsi)."""
    if len(closes) <= n:
        return None
    gain = loss = 0.0
    for i in range(1, len(closes)):
        d = closes[i] - closes[i - 1]
        g, l = max(d, 0), max(-d, 0)
        if i <= n:
            gain += g
            loss += l
            if i == n:
                gain /= n
                loss /= n
        else:
            gain = (gain * (n - 1) + g) / n
            loss = (loss * (n - 1) + l) / n
    return 100.0 if loss == 0 else 100 - 100 / (1 + gain / loss)


def is_forming(bar, interval, now=None):
    """هل الشمعة ما زالت جارية؟ (مصدر TradingView يرسل closed، وياهو يُحسب من الوقت)."""
    if bar.get("closed") is not None:
        return not bar["closed"]
    now = now or time.time()
    t = bar["time"] - RIYADH_OFFSET
    if interval != "1d":
        return now < t + STEP[interval]
    d, cur = datetime.fromtimestamp(t, NY), datetime.fromtimestamp(now, NY)
    return d.date() == cur.date() and cur.weekday() < 5 and cur.hour < 16


def indicators(bars, interval, now=None):
    """قيم المؤشرات على آخر شمعة مغلقة. None إن كانت الشموع أقل من اللازم لـ MACD."""
    closed = bars[:-1] if bars and is_forming(bars[-1], interval, now) else list(bars)
    if len(closed) < 35:  # 26 لـ EMA البطيء + 9 لخط الإشارة
        return None
    closes = [b["close"] for b in closed]
    line, sig, hist = macd(closes)
    e9, e20, e21 = ema(closes, 9), ema(closes, 20), ema(closes, 21)
    cross = None  # منذ كم شمعة عبر خط الماكد فوق الإشارة (آخر 10 شموع)
    for k in range(1, 11):
        i = len(closed) - k
        if i < 1 or hist[i] is None or hist[i - 1] is None:
            break
        if hist[i] > 0 >= hist[i - 1]:
            cross = k - 1
            break
    vols = [b.get("volume") or 0 for b in closed]
    avg20 = sum(vols[-21:-1]) / 20 if len(vols) >= 21 else None
    last = closed[-1]
    vwap = None
    if interval != "1d":  # VWAP يوم آخر شمعة (كل جلسات اليوم الظاهرة في الشارت)
        day = datetime.fromtimestamp(last["time"] - RIYADH_OFFSET, NY).date()
        pv = vv = 0.0
        for b in closed:
            if datetime.fromtimestamp(b["time"] - RIYADH_OFFSET, NY).date() == day and b.get("volume"):
                pv += (b["high"] + b["low"] + b["close"]) / 3 * b["volume"]
                vv += b["volume"]
        vwap = pv / vv if vv else None
    r = lambda v, d=4: round(v, d) if v is not None else None
    return {
        "bar_time": last["time"], "close": last["close"],
        "macd": r(line[-1]), "signal": r(sig[-1]), "hist": r(hist[-1]), "hist_prev": r(hist[-2]),
        "cross_bars_ago": cross,
        "rsi": r(rsi_series_last(closes), 1),
        "ema9": r(e9[-1]), "ema20": r(e20[-1]), "ema21": r(e21[-1]),
        "vwap": r(vwap), "vol": vols[-1], "vol_x": r(vols[-1] / avg20, 2) if avg20 else None,
        "bars": len(closed), "dropped_forming": len(closed) < len(bars),
    }


# ===== مجموعة الأسهم المفحوصة =====
POOLS = {
    "momentum": ("الزخم (سرعة 5 دقائق)", lambda r: r.get("speed")),
    "gainers": ("الأكثر ارتفاعًا", lambda r: r.get("change_pct")),
    "liquidity": ("الأعلى سيولة نسبية", lambda r: r.get("vol_ratio")),
    "value": ("الأعلى قيمة تداول", lambda r: r.get("value")),
}


def pool(rows, name, symbols=None):
    if symbols:
        by = {r["symbol"]: r for r in rows}
        return [by.get(s) or {"symbol": s} for s in symbols][:POOL_SIZE]
    _, key = POOLS.get(name, POOLS["momentum"])
    # أسهم مدرجة نشطة وصاعدة اليوم (بدون OTC): الزخم صعودًا
    live = [r for r in rows if not r.get("otc") and (r.get("value") or 0) >= MIN_VALUE
            and (r.get("price") or 0) >= 0.01 and (r.get("change_pct") or 0) > 0 and key(r) is not None]
    live.sort(key=lambda r: -key(r))
    return live[:POOL_SIZE]


_cache = {}
_lock = threading.Lock()


def scan(interval, pool_name, rows, get_chart, prepost, symbols=None):
    key = (interval, pool_name, tuple(symbols or ()))
    with _lock:
        c = _cache.get(key)
        if c and time.time() - c["at"] < CACHE_SECONDS:
            return c["data"]
    candidates = pool(rows, pool_name, symbols)
    now = time.time()

    def one(r):
        try:
            bars = get_chart(r["symbol"], interval, prepost=prepost)
        except Exception as e:
            return r, None, str(e)[:80]
        return r, indicators(bars, interval, now), None if bars else "لا شموع"

    with ThreadPoolExecutor(max_workers=6) as ex:
        results = list(ex.map(one, candidates))
    out, skipped = [], []
    for r, ind, err in results:
        if not ind:
            skipped.append({"symbol": r["symbol"], "why": err or "شموع أقل من 35 (لا يكفي لـ MACD)"})
            continue
        out.append({k: r.get(k) for k in ("symbol", "name", "price", "change_pct", "speed", "vol_ratio", "value", "live")} | {"ind": ind})
    note = None
    if not candidates and not symbols:
        note = ("سرعة 5 دقائق تحتاج دقيقة على الأقل من الأسعار بعد تشغيل المنصة — أو لا يوجد سهم صاعد نشط الآن"
                if pool_name == "momentum" else "لا يوجد سهم مدرج صاعد نشط في هذه المجموعة الآن")
    data = {"at": int(time.time()), "interval": interval, "pool": pool_name, "count": len(candidates),
            "rows": out, "skipped": skipped, "note": note}
    with _lock:
        _cache[key] = {"at": time.time(), "data": data}
    return data
