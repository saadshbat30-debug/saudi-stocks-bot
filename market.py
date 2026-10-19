import re
import threading
import time
from collections import deque

import yfinance as yf

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
        if len(df) >= 2:
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
    prev = float(df["Close"].iloc[-2])
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
    """السعر والتغير والحجم + مؤشرات RSI والمتوسطات لكل رمز."""
    stream.watch(symbols)
    with _lock:
        _refresh(_quotes, symbols, "5d", CACHE_SECONDS, _snapshot)
        _refresh(_history, symbols, "6mo", HISTORY_CACHE_SECONDS, _closes)
        result = {}
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

def _universe(market):
    return yf.EquityQuery("and", [
        yf.EquityQuery("eq", ["region", "us"]),
        yf.EquityQuery("is-in", ["exchange", *market["exchanges"]]),
        yf.EquityQuery("btwn", ["intradayprice", market["min_price"], market["max_price"]]),
    ])


_markets = {}
_market_history = {}  # الرمز ← قائمة الإغلاقات اليومية لحساب RSI
_history_state = {"at": 0, "running": False}
_market_lock = threading.Lock()


def _screen(query, limit):
    quotes = []
    while len(quotes) < limit:
        size = min(250, limit - len(quotes))
        page = yf.screen(query, offset=len(quotes), size=size, sortField="dayvolume", sortAsc=False)
        batch = page.get("quotes", [])
        quotes += batch
        if len(batch) < size:
            break
    return quotes


def _is_common_stock(symbol):
    """يستبعد الوارنتات والحقوق والوحدات والأسهم الممتازة (GME-WT, SEATW, NEE-PS ...)."""
    if "-" in symbol:
        suffix = symbol.split("-", 1)[1]
        return len(suffix) == 1 and suffix not in "WRUP"
    return not (len(symbol) == 5 and symbol[-1] in "WRU")


def _num(q, key, digits=2):
    v = q.get(key)
    return round(v, digits) if isinstance(v, (int, float)) else None


def _row(q):
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
        "day_high": q.get("regularMarketDayHigh"),
        "day_low": q.get("regularMarketDayLow"),
        "volume": q.get("regularMarketVolume") or 0,
        "avg_volume": q.get("averageDailyVolume3Month"),
        "shares": q.get("sharesOutstanding"),
        "sma50": _num(q, "fiftyDayAverage"),
        "pe": _num(q, "trailingPE"),
        "pb": _num(q, "priceToBook"),
        "market_cap": q.get("marketCap"),
        "ext_price": q.get(f"{ext}Price") if ext else None,
        "ext_change_pct": q.get(f"{ext}ChangePercent") if ext else None,
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


def get_market(market_id):
    """أسهم السوق في النطاق السعري مع مقاييس الزخم والسيولة وRSI ومتوسط 20."""
    market = MARKETS[market_id]
    now = time.time()
    with _market_lock:
        cached = _markets.get(market_id)
        if not cached or now - cached["at"] > SCREEN_SECONDS:
            try:
                rows = [r for r in map(_row, _screen(_universe(market), market["limit"])) if r]
                if rows:
                    cached = _markets[market_id] = {"rows": rows, "at": now}
                    stream.watch([r["symbol"] for r in rows])
            except Exception as e:
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

    # ===== السيولة =====
    avg_vol, shares, volume = r.pop("avg_volume"), r.pop("shares"), r["volume"]
    r["value"] = round(price * volume)
    r["vol_ratio"] = round(volume / avg_vol, 2) if avg_vol else None
    r["turnover"] = round(volume / shares * 100, 2) if shares else None

    # ===== الزخم =====
    prev = r.pop("prev_close")
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
    for k in ("day_high", "day_low", "ext_price"):
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
}
RIYADH_OFFSET = 3 * 3600  # الشارت يعرض الوقت بتوقيت الرياض

_charts = {}


def get_chart(symbol, interval):
    period, yf_interval, max_age = CHART_INTERVALS[interval]
    key = (symbol, interval)
    cached = _charts.get(key)
    if cached and time.time() - cached["at"] < max_age:
        return cached["bars"]
    try:
        df = yf.Ticker(symbol).history(period=period, interval=yf_interval, prepost=False, auto_adjust=False)
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
