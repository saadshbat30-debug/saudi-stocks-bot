import re
import threading
import time

import yfinance as yf

from config import CACHE_SECONDS, HISTORY_CACHE_SECONDS

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
    with _lock:
        _refresh(_quotes, symbols, "5d", CACHE_SECONDS, _snapshot)
        _refresh(_history, symbols, "6mo", HISTORY_CACHE_SECONDS, _closes)
        result = {}
        for s in symbols:
            q = _quotes.get(s)
            if not q:
                continue
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
