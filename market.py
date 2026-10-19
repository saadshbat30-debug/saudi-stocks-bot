import re
import threading
import time

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
            item = {
                "price": round(q["price"], 2),
                "change": round(change, 2),
                "change_pct": round(change / q["prev_close"] * 100, 2),
                "volume": q["volume"],
            }
            h = _history.get(s)
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


def _row(q):
    price = q.get("regularMarketPrice")
    if price is None or not _is_common_stock(q["symbol"]):
        return None
    avg_vol = q.get("averageDailyVolume3Month")
    volume = q.get("regularMarketVolume") or 0
    return {
        "symbol": q["symbol"],
        "name": q.get("shortName") or q.get("longName") or q["symbol"],
        "price": round(price, 4) if price < 1 else round(price, 2),
        "change": round(q.get("regularMarketChange") or 0, 2),
        "change_pct": round(q.get("regularMarketChangePercent") or 0, 2),
        "volume": volume,
        "vol_ratio": round(volume / avg_vol, 2) if avg_vol else None,
        "avg_volume": avg_vol,
        "sma50": round(q["fiftyDayAverage"], 2) if q.get("fiftyDayAverage") else None,
        "market_cap": q.get("marketCap"),
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
    """أسهم السوق في النطاق السعري مرتبة بالأكثر تداولًا، مع RSI ومتوسط 20 عند توفرهما."""
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

    result = []
    for r in rows:
        r = dict(r)
        live = stream.latest(r["symbol"])
        if live:
            # السعر اللحظي من البث يتقدم على بيانات الماسح
            r["price"] = round(live["price"], 4) if live["price"] < 1 else round(live["price"], 2)
            if live["change"] is not None:
                r["change"] = round(live["change"], 4)
            if live["change_pct"] is not None:
                r["change_pct"] = round(live["change_pct"], 2)
            if live["volume"]:
                r["volume"] = live["volume"]
                if r["avg_volume"]:
                    r["vol_ratio"] = round(live["volume"] / r["avg_volume"], 2)
            r["live"] = True
        del r["avg_volume"]
        closes = _market_history.get(r["symbol"])
        if closes:
            closes = closes[:-1] + [r["price"]]
            value, s20 = rsi(closes), sma(closes, 20)
            r["rsi"] = round(value, 1) if value is not None else None
            r["sma20"] = round(s20, 2) if s20 else None
        result.append(r)
    return result
