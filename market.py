import re
import threading
import time
from collections import deque

import yfinance as yf

import extras
import stream
from config import CACHE_SECONDS, HISTORY_CACHE_SECONDS, MARKETS, SCREEN_SECONDS

SYMBOL_RE = re.compile(r"^[A-Z0-9.\-^=]{1,15}$")

_quotes = {}
_history = {}
_lock = threading.Lock()


def valid_symbol(symbol):
    return bool(SYMBOL_RE.match(symbol))


def _download(symbols, period):
    data = yf.download(
        symbols,
        period=period,
        interval="1d",
        group_by="ticker",
        auto_adjust=False,
        progress=False,
        threads=True,
    )
    frames = {}
    for symbol in symbols:
        try:
            df = data[symbol].dropna(subset=["Close"])
        except KeyError:
            continue
        if len(df):
            frames[symbol] = df
    return frames


def _refresh(cache, symbols, period, max_age, build):
    now = time.time()
    stale = [s for s in symbols if s not in cache or now - cache[s]["at"] > max_age]
    if not stale:
        return
    try:
        frames = _download(stale, period)
    except Exception as e:
        print(f"⚠️ فشل سحب البيانات: {e}")
        return
    for symbol, df in frames.items():
        cache[symbol] = {**build(df), "at": now}


def _snapshot(df):
    price = float(df["Close"].iloc[-1])
    # سهم مدرج اليوم: لا يوجد إغلاق سابق، فيُقارن بسعر الافتتاح
    prev = float(df["Close"].iloc[-2]) if len(df) >= 2 else float(df["Open"].iloc[-1])
    return {"price": price, "prev_close": prev, "volume": int(df["Volume"].iloc[-1] or 0)}


def _closes(df):
    return {"closes": [float(c) for c in df["Close"]], "volumes": [int(v or 0) for v in df["Volume"]]}


def rsi(closes, period=14):
    if len(closes) <= period:
        return None
    gains = losses = 0.0
    for i in range(1, period + 1):
        d = closes[i] - closes[i - 1]
        gains += max(d, 0)
        losses += max(-d, 0)
    avg_gain, avg_loss = gains / period, losses / period
    for i in range(period + 1, len(closes)):
        d = closes[i] - closes[i - 1]
        avg_gain = (avg_gain * (period - 1) + max(d, 0)) / period
        avg_loss = (avg_loss * (period - 1) + max(-d, 0)) / period
    if avg_loss == 0:
        return 100.0
    return 100 - 100 / (1 + avg_gain / avg_loss)


def sma(values, n):
    return sum(values[-n:]) / n if len(values) >= n else None


def get_quotes(symbols):
    """السعر والتغير والحجم + مؤشرات RSI والمتوسطات لكل رمز.

    الأسهم الموجودة في الماسح تؤخذ منه (نفس أرقام الصفحة الرئيسية، وتعمل حتى للأسهم
    المدرجة حديثًا)، والباقي من التاريخ اليومي.
    """
    stream.watch(symbols)
    result = {}
    for s in symbols:
        row = row_for(s)
        if row:
            result[s] = row
    # صفوف الماسح التي لم يُحسب لها RSI بعد (التاريخ يُحمَّل في الخلفية): احسبه لها الآن
    pending = [s for s, r in result.items() if r.get("rsi") is None]
    if pending:
        with _lock:
            _refresh(_history, pending, "6mo", HISTORY_CACHE_SECONDS, _closes)
        for s in pending:
            h = _history.get(s)
            if h:
                closes = h["closes"][:-1] + [result[s]["price"]]
                r, s20 = rsi(closes), sma(closes, 20)
                result[s]["rsi"] = round(r, 1) if r is not None else None
                result[s]["sma20"] = round(s20, 2) if s20 else None
    symbols = [s for s in symbols if s not in result]
    if not symbols:
        return result
    with _lock:
        _refresh(_quotes, symbols, "5d", CACHE_SECONDS, _snapshot)
        _refresh(_history, symbols, "6mo", HISTORY_CACHE_SECONDS, _closes)
        for s in symbols:
            q = _quotes.get(s)
            if not q:
                continue
            live = stream.latest(s)
            if live:
                q = {**q, "price": live["price"], "volume": live["volume"] or q["volume"]}
            change = q["price"] - q["prev_close"]
            change_pct = change / q["prev_close"] * 100
            if live and live["change_pct"] is not None:
                change_pct, change = live["change_pct"], live["change"] if live["change"] is not None else change
            item = {
                "price": round(q["price"], 4) if q["price"] < 1 else round(q["price"], 2),
                "change": round(change, 4),
                "change_pct": round(change_pct, 2),
                "volume": q["volume"],
            }
            h = _history.get(s)
            if live:
                item["live"] = True
            if h:
                # آخر شمعة تأخذ السعر الحالي حتى تكون المؤشرات محدثة
                closes = h["closes"][:-1] + [q["price"]]
                r, s20, s50 = rsi(closes), sma(closes, 20), sma(closes, 50)
                avg_vol = sma(h["volumes"][:-1], 20)
                item.update(
                    rsi=round(r, 1) if r is not None else None,
                    sma20=round(s20, 2) if s20 else None,
                    sma50=round(s50, 2) if s50 else None,
                    vol_ratio=round(q["volume"] / avg_vol, 2) if avg_vol else None,
                )
            result[s] = item
        return result


# ===== السوق كاملًا عبر ماسح ياهو (طلب واحد لكل 250 سهم) =====

def _universe(market, otc=False):
    parts = [
        yf.EquityQuery("eq", ["region", "us"]),
        yf.EquityQuery("is-in", ["exchange", *(market["otc_exchanges"] if otc else market["exchanges"])]),
    ]
    if market.get("max_price"):
        parts.append(yf.EquityQuery("btwn", ["intradayprice", market["min_price"], market["max_price"]]))
    elif market.get("min_price"):
        parts.append(yf.EquityQuery("gte", ["intradayprice", market["min_price"]]))
    return yf.EquityQuery("and", parts)


_markets = {}
_market_history = {}  # الرمز ← قائمة الإغلاقات اليومية لحساب RSI
_history_state = {"at": 0, "running": False}
_market_lock = threading.Lock()


def _screen_one(query, limit, sort_field, asc=False):
    quotes = []
    while len(quotes) < limit:
        size = min(250, limit - len(quotes))
        page = yf.screen(query, offset=len(quotes), size=size, sortField=sort_field, sortAsc=asc)
        batch = page.get("quotes", [])
        quotes += batch
        if len(batch) < size:
            break
    return quotes


def _screen(query, limit):
    """قائمة السوق من عدة ترتيبات مدمجة حتى لا يغيب سهم متحرك:
    الأعلى حجمًا اليوم + الأكثر ارتفاعًا + الأكثر انخفاضًا + الأعلى متوسط حجم (3 أشهر).
    متوسط الحجم يُبقي الأسهم النشطة قبل الافتتاح حين يكون حجم اليوم صفرًا للجميع."""
    sources = [("dayvolume", False, limit), ("percentchange", False, 250), ("percentchange", True, 250), ("avgdailyvol3m", False, 250)]
    seen, merged = set(), []
    for field, asc, n in sources:
        try:
            batch = _screen_one(query, n, field, asc)
        except Exception as e:
            if not merged and field == "dayvolume":
                raise  # الترتيب الأساسي فشل: أبلغ عن الخطأ
            print(f"⚠️ فشل ترتيب الماسح {field}: {e}")
            continue
        for q in batch:
            if q.get("symbol") and q["symbol"] not in seen:
                seen.add(q["symbol"]); merged.append(q)
    return merged


def _screen_otc(market, seen):
    """سوق OTC: الأعلى حجمًا + الأكثر ارتفاعًا (تُعلَّم otc)."""
    out = []
    for field, asc, n in (("dayvolume", False, market["otc_limit"]), ("percentchange", False, 100)):
        try:
            for q in _screen_one(_universe(market, otc=True), n, field, asc):
                if q.get("symbol") and q["symbol"] not in seen:
                    seen.add(q["symbol"]); out.append({**q, "_otc": True})
        except Exception as e:
            print(f"⚠️ فشل جلب سوق OTC ({field}): {e}")
    return out


def _is_common_stock(symbol):
    """يستبعد الوارنتات والحقوق والوحدات والأسهم الممتازة (GME-WT, SEATW, NEE-PS ...)."""
    if "-" in symbol:
        suffix = symbol.split("-", 1)[1]
        return len(suffix) == 1 and suffix not in "WRUP"
    return not (len(symbol) == 5 and symbol[-1] in "WRU")


def _num(q, key, digits=2):
    v = q.get(key)
    return round(v, digits) if isinstance(v, (int, float)) else None


_raw_quotes = {}  # آخر بيانات خام من الماسح لكل رمز (لصفحة فحص الحقول)


def _overnight_fields(q):
    """ياهو يعرض سعر التداول الليلي على موقعه؛ اسم الحقل غير موثق فنبحث عن أي حقل فيه overnight."""
    price = pct = None
    for k, v in q.items():
        lk = k.lower()
        if "overnight" in lk and isinstance(v, (int, float)):
            if lk.endswith("changepercent"):
                pct = v
            elif lk.endswith("price"):
                price = v
    return price, pct


def _row(q):
    _raw_quotes[q.get("symbol")] = q
    price = q.get("regularMarketPrice")
    if price is None or not _is_common_stock(q["symbol"]):
        return None
    state = q.get("marketState", "")
    ext = "preMarket" if state.startswith("PRE") else "postMarket" if state.startswith("POST") else None
    return {
        "symbol": q["symbol"],
        "name": q.get("shortName") or q.get("longName") or q["symbol"],
        "price": price,
        "change": q.get("regularMarketChange") or 0,
        "change_pct": q.get("regularMarketChangePercent") or 0,
        "prev_close": q.get("regularMarketPreviousClose"),
        "open": q.get("regularMarketOpen"),
        "high52": q.get("fiftyTwoWeekHigh"),
        "low52": q.get("fiftyTwoWeekLow"),
        "day_high": q.get("regularMarketDayHigh"),
        "day_low": q.get("regularMarketDayLow"),
        "volume": q.get("regularMarketVolume") or 0,
        "avg_volume": q.get("averageDailyVolume3Month"),
        "shares": q.get("sharesOutstanding"),
        "sma50": _num(q, "fiftyDayAverage"),
        "pe": _num(q, "trailingPE"),
        "pb": _num(q, "priceToBook"),
        "market_cap": q.get("marketCap"),
        "otc": bool(q.get("_otc")),
        "quote_time": q.get("regularMarketTime") or 0,
        "ext_price": q.get(f"{ext}Price") if ext else None,
        "ext_change_pct": q.get(f"{ext}ChangePercent") if ext else None,
        "night_price": _overnight_fields(q)[0],
        "night_change_pct": _overnight_fields(q)[1],
    }


def _refresh_market_history(symbols):
    try:
        for i in range(0, len(symbols), 100):
            batch = symbols[i:i + 100]
            frames = _download(batch, "3mo")
            for symbol in batch:
                # قائمة فارغة للرموز بلا بيانات حتى لا يُعاد طلبها كل مرة
                df = frames.get(symbol)
                _market_history[symbol] = [float(c) for c in df["Close"]] if df is not None else []
        _history_state["at"] = time.time()
    except Exception as e:
        print(f"⚠️ فشل تحديث المؤشرات: {e}")
    finally:
        _history_state["running"] = False


STATUS = {"screener_at": 0, "screener_error": None, "night_at": 0, "night_count": 0, "night_error": None}

# ===== التداول الليلي من نقطة الأسعار (v7/finance/quote) — نفس ما يستخدمه موقع ياهو =====
QUOTE_URL = "https://query1.finance.yahoo.com/v7/finance/quote"
_night_quotes = {}  # الرمز ← {price, change_pct, at}
_night_extra = set()  # أسهم تُفتح صفحاتها (تُضاف للجلب الليلي)
_night_state = {"started": False}


def raw_quotes(symbols):
    """رد ياهو الخام لعدة رموز (مع طلب سعر التداول الليلي)."""
    from yfinance.data import YfData
    data = YfData().get_raw_json(QUOTE_URL, params={
        "symbols": ",".join(symbols), "formatted": "false", "overnightPrice": "true",
        "lang": "en-US", "region": "US",
    })
    return (data.get("quoteResponse") or {}).get("result") or []


def _night_loop():
    while True:
        if stream.is_overnight():
            # الأكثر قيمة تداول أولًا + الأسهم المفتوحة صفحاتها
            rows = [r for c in list(_markets.values()) for r in c["rows"]]
            rows.sort(key=lambda r: -((r.get("price") or 0) * (r.get("volume") or 0)))
            symbols = list(dict.fromkeys(list(_night_extra) + [r["symbol"] for r in rows[:300]]))
            found = 0
            try:
                for i in range(0, len(symbols), 100):
                    for q in raw_quotes(symbols[i:i + 100]):
                        price, pct = _overnight_fields(q)
                        if price:
                            _night_quotes[q["symbol"]] = {"price": price, "change_pct": pct, "at": time.time()}
                            found += 1
                STATUS.update(night_at=time.time(), night_count=found, night_error=None)
            except Exception as e:
                STATUS["night_error"] = str(e)[:200]
                print(f"⚠️ فشل جلب أسعار التداول الليلي: {e}")
            time.sleep(60)
        else:
            time.sleep(120)


def watch_night(symbol):
    _night_extra.add(symbol)
    if not _night_state["started"]:
        _night_state["started"] = True
        threading.Thread(target=_night_loop, daemon=True).start()  # آخر تحديث ناجح للماسح


def screener_age():
    return time.time() - STATUS["screener_at"] if STATUS["screener_at"] else None


def _fresher_from_bars(r):
    """إذا تأخر الماسح، خذ السعر من آخر شمعة دقيقة حديثة (إن وُجدت) لأنها أحدث."""
    age = screener_age()
    if r.get("live") or age is None or age < 120:
        return r
    for key in ((r["symbol"], "1m", True), (r["symbol"], "5m", True)):
        c = _charts.get(key)
        if c and c["bars"] and time.time() - c["at"] < 90:
            last = c["bars"][-1]["close"]
            prev = r.get("prev_close")
            r["price"] = _price_round(last)
            if prev:
                r["change"] = round(last - prev, 4)
                r["change_pct"] = round((last / prev - 1) * 100, 2)
            r["from_bars"] = True
            break
    return r


def _float_priority():
    """رموز السوق للأسهم الحرة: الأكثر حركة أولًا."""
    rows = [r for c in list(_markets.values()) for r in c["rows"]]
    return [r["symbol"] for r in sorted(rows, key=lambda r: -abs(r.get("change_pct") or 0))]


def get_market(market_id):
    """أسهم السوق في النطاق السعري مع مقاييس الزخم والسيولة وRSI ومتوسط 20."""
    market = MARKETS[market_id]
    now = time.time()
    with _market_lock:
        cached = _markets.get(market_id)
        if not cached or now - cached["at"] > SCREEN_SECONDS:
            try:
                quotes = _screen(_universe(market), market["limit"])
                if market.get("otc_exchanges") and market.get("otc_limit"):
                    quotes += _screen_otc(market, {q["symbol"] for q in quotes})
                rows = [r for r in map(_row, quotes) if r]
                if rows:
                    cached = _markets[market_id] = {"rows": rows, "at": now}
                    stream.watch([r["symbol"] for r in rows])
                    extras.start_floats(_float_priority)
                    if not _night_state["started"]:
                        _night_state["started"] = True
                        threading.Thread(target=_night_loop, daemon=True).start()
                    STATUS["screener_at"], STATUS["screener_error"] = now, None
            except Exception as e:
                STATUS["screener_error"] = str(e)[:200]
                print(f"⚠️ فشل سحب السوق {market_id}: {e}")
        rows = cached["rows"] if cached else []

        # تحديث التاريخ في الخلفية حتى لا تتأخر الصفحة
        missing = [r["symbol"] for r in rows if r["symbol"] not in _market_history]
        stale = now - _history_state["at"] > HISTORY_CACHE_SECONDS
        if rows and (missing or stale) and not _history_state["running"]:
            _history_state["running"] = True
            symbols = missing if missing and not stale else [r["symbol"] for r in rows]
            threading.Thread(target=_refresh_market_history, args=(symbols,), daemon=True).start()

    return [_enrich(dict(r), now) for r in rows]


_samples = {}  # الرمز ← (الوقت، السعر) لحساب سرعة التغير
_samples_lock = threading.Lock()
SPEED_WINDOW = 300  # سرعة التغير = نسبة التغير في آخر 5 دقائق


def _speed(symbol, price, now):
    """نسبة تغير السعر خلال آخر 5 دقائق (الزخم اللحظي)."""
    with _samples_lock:
        dq = _samples.setdefault(symbol, deque())
        if not dq or now - dq[-1][0] >= 10:
            dq.append((now, price))
        while len(dq) > 2 and now - dq[1][0] >= SPEED_WINDOW:
            dq.popleft()
        t0, p0 = dq[0]
    if now - t0 < 60 or not p0:
        return None
    return round((price / p0 - 1) * 100, 2)


def _price_round(p):
    return round(p, 4) if p < 1 else round(p, 2)


def _enrich(r, now):
    """يدمج البث اللحظي ويحسب مقاييس الزخم والسيولة."""
    symbol = r["symbol"]
    live = stream.latest(symbol)
    quote_time = r.pop("quote_time", 0)
    if live and live["trade_time"] < quote_time:
        live = None  # الماسح أحدث من آخر صفقة وصلت من البث
    if live:
        # السعر اللحظي من البث يتقدم على بيانات الماسح
        r["price"] = live["price"]
        if live["change"] is not None:
            r["change"] = live["change"]
        if live["change_pct"] is not None:
            r["change_pct"] = live["change_pct"]
        if live["volume"]:
            r["volume"] = live["volume"]
        r["live"] = True
    price = r["price"]
    if r["day_high"] is not None:
        r["day_high"] = max(r["day_high"], price)
    if r["day_low"] is not None:
        r["day_low"] = min(r["day_low"], price)

    ext = stream.latest_ext(symbol)
    if ext:
        r["ext_price"], r["ext_change_pct"] = ext["price"], ext["change_pct"]
        r["ext_live"] = True
    night = stream.latest_night(symbol)
    nq = _night_quotes.get(symbol)
    if not night and nq and time.time() - nq["at"] < 600:
        night = {**nq, "live": False}
        r["night_price"], r["night_change_pct"] = nq["price"], nq["change_pct"]
    elif night:
        r["night_price"], r["night_change_pct"] = night["price"], night["change_pct"]
        r["night_live"] = True
    if r.get("night_price"):
        if r.get("night_change_pct") is None and r.get("prev_close") is not None:
            # التغير مقارنة بآخر إغلاق رسمي
            r["night_change_pct"] = (r["night_price"] / price - 1) * 100 if price else None
        r["night_speed"] = _speed(symbol + "#night", r["night_price"], now)
        r["night_price"] = _price_round(r["night_price"])
        if r.get("night_change_pct") is not None:
            r["night_change_pct"] = round(r["night_change_pct"], 2)
    if r.get("ext_price"):
        # زخم الجلسة الممتدة من تحركات سعر ما قبل الافتتاح / ما بعد الإغلاق
        r["ext_speed"] = _speed(symbol + "#ext", r["ext_price"], now)

    # ===== السيولة =====
    avg_vol, shares, volume = r.pop("avg_volume"), r.pop("shares"), r["volume"]
    # الأسهم الحرة (إن جُلبت) أدق للتدوير، مثل موموو
    r["float"] = extras.float_for(symbol)
    shares = r["float"] or shares
    typical = (r["day_high"] + r["day_low"] + price) / 3 if r["day_high"] and r["day_low"] else price
    r["value"] = round(typical * volume)
    r["vol_ratio"] = round(volume / avg_vol, 2) if avg_vol else None
    r["turnover"] = round(volume / shares * 100, 2) if shares else None

    # ===== الزخم =====
    prev = r["prev_close"]
    high, low = r["day_high"], r["day_low"]
    r["amplitude"] = round((high - low) / prev * 100, 2) if prev and high is not None and low is not None else None
    r["speed"] = _speed(symbol, price, now)
    r["at_high"] = bool(high and r["change_pct"] > 0 and price >= high * 0.995)

    closes = _market_history.get(symbol)
    if closes:
        closes = closes[:-1] + [price]
        value, s20 = rsi(closes), sma(closes, 20)
        r["rsi"] = round(value, 1) if value is not None else None
        r["sma20"] = round(s20, 2) if s20 else None

    r["price"] = _price_round(price)
    r["change"] = round(r["change"], 4)
    r["change_pct"] = round(r["change_pct"], 2)
    for k in ("day_high", "day_low", "ext_price", "prev_close", "open", "high52", "low52"):
        if r.get(k) is not None:
            r[k] = _price_round(r[k])
    if r.get("ext_change_pct") is not None:
        r["ext_change_pct"] = round(r["ext_change_pct"], 2)
    return r


# ===== شموع الشارت اللحظي =====

CHART_INTERVALS = {
    # الفاصل ← (المدة، فاصل ياهو، مدة التخزين بالثواني)
    "1m": ("2d", "1m", 15),
    "5m": ("5d", "5m", 30),
    "15m": ("1mo", "15m", 60),
    "1h": ("3mo", "60m", 300),
    "1d": ("2y", "1d", 600),
    "1w": ("10y", "1wk", 3600),
    "1mo": ("max", "1mo", 3600),
}
RIYADH_OFFSET = 3 * 3600  # الشارت يعرض الوقت بتوقيت الرياض

_charts = {}


def get_chart(symbol, interval, prepost=False):
    period, yf_interval, max_age = CHART_INTERVALS[interval]
    key = (symbol, interval, prepost)
    cached = _charts.get(key)
    if cached and time.time() - cached["at"] < max_age:
        return cached["bars"]
    try:
        df = yf.Ticker(symbol).history(period=period, interval=yf_interval, prepost=prepost, auto_adjust=False)
    except Exception as e:
        print(f"⚠️ فشل سحب الشموع {symbol}: {e}")
        return cached["bars"] if cached else []
    bars = []
    for ts, row in df.dropna(subset=["Close"]).iterrows():
        bars.append({
            "time": int(ts.timestamp()) + RIYADH_OFFSET,
            "open": float(row["Open"]),
            "high": float(row["High"]),
            "low": float(row["Low"]),
            "close": float(row["Close"]),
            "volume": int(row["Volume"] or 0),
        })
    _charts[key] = {"bars": bars, "at": time.time()}
    return bars


def name_for(symbol):
    """اسم الشركة من آخر نتيجة للماسح إن وُجد."""
    for cached in _markets.values():
        for r in cached["rows"]:
            if r["symbol"] == symbol:
                return r["name"]
    return None


# ===== السيولة الداخلة والخارجة (تقديرية) =====

def row_for(symbol):
    """آخر صف محسوب للسهم من الماسح (فيه القيمة والتدوير والسرعة) إن وُجد."""
    if not _markets:
        # صفحة السهم فُتحت قبل الصفحة الرئيسية: شغّل الماسح أولًا
        for market_id in MARKETS:
            get_market(market_id)
    for cached in list(_markets.values()):
        for r in cached["rows"]:
            if r["symbol"] == symbol:
                return _fresher_from_bars(_enrich(dict(r), time.time()))
    return None


def _flow(bars):
    """يقسم قيمة كل شمعة بين داخلة وخارجة حسب موقع الإغلاق داخل مدى الشمعة
    (مثل Chaikin Money Flow): الإغلاق عند القمة = داخلة بالكامل، عند القاع = خارجة بالكامل.
    الشمعة بلا مدى تُصنَّف حسب تغيرها عن الشمعة السابقة."""
    inflow = outflow = 0.0
    for prev, b in zip([None] + bars[:-1], bars):
        value = (b["high"] + b["low"] + b["close"]) / 3 * b["volume"]
        if b["high"] > b["low"]:
            share = (b["close"] - b["low"]) / (b["high"] - b["low"])
        else:
            ref = prev["close"] if prev else b["open"]
            share = 1.0 if b["close"] > ref else 0.0 if b["close"] < ref else 0.5
        inflow += value * share
        outflow += value * (1 - share)
    return inflow, outflow


def _flow_dict(inflow, outflow):
    total = inflow + outflow
    return {
        "inflow": round(inflow),
        "outflow": round(outflow),
        "net": round(inflow - outflow),
        "inflow_pct": round(inflow / total * 100, 1) if total else None,
    }


def get_flow(symbol):
    """السيولة الداخلة/الخارجة لليوم من شموع الدقيقة.

    شموع ياهو لا تسجل كل الحجم في الأسهم الصغيرة، لذلك تُؤخذ منها النسبة فقط
    وتُطبَّق على القيمة الكلية (متوسط السعر × حجم اليوم اللحظي).
    المنصات المدفوعة تحسبها من كل صفقة (هل نُفذت عند سعر الطلب أو العرض)، فالأرقام تقريبية.
    """
    # تشمل ما قبل الافتتاح وما بعد الإغلاق، لأن جزءًا كبيرًا من تداول الأسهم الصغيرة يحدث فيها
    bars = get_chart(symbol, "1m", prepost=True)
    if bars:
        # شموع آخر يوم تداول فقط (الأوقات مزاحة لتوقيت الرياض)
        last_day = (bars[-1]["time"] - RIYADH_OFFSET - 4 * 3600) // 86400
        bars = [b for b in bars if (b["time"] - RIYADH_OFFSET - 4 * 3600) // 86400 == last_day]

    row = row_for(symbol)
    quote = row or get_quotes([symbol]).get(symbol) or {}
    price, volume = quote.get("price"), quote.get("volume") or 0

    # متوسط سعر الصفقات (VWAP) من السعر النموذجي لكل شمعة
    bars_volume = sum(b["volume"] for b in bars)
    vwap = (sum((b["high"] + b["low"] + b["close"]) / 3 * b["volume"] for b in bars) / bars_volume
            if bars_volume else price)
    value = vwap * volume if vwap else None

    inflow, outflow = _flow(bars)
    scale = value / (inflow + outflow) if value and inflow + outflow else 0

    recent = {}
    for minutes in (5, 15):
        i, o = _flow(bars[-minutes:]) if bars else (0, 0)
        recent[f"m{minutes}"] = _flow_dict(i * scale, o * scale)

    return {
        "price": price,
        "volume": volume,
        "vwap": _price_round(vwap) if vwap else None,
        "value": round(value) if value else None,
        "vol_ratio": quote.get("vol_ratio"),
        "turnover": quote.get("turnover"),
        "speed": quote.get("speed"),
        "live": bool(quote.get("live")),
        **_flow_dict(inflow * scale, outflow * scale),
        **recent,
    }


def raw_quote(symbol):
    return _raw_quotes.get(symbol)
