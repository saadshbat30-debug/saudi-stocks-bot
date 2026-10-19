import threading
import time

import yfinance as yf

from config import CACHE_SECONDS

_cache = {}
_lock = threading.Lock()


def _download(symbols):
    """يسحب آخر 5 أيام لكل رمز ويرجع {الرمز: {price, prev_close}}."""
    data = yf.download(
        symbols,
        period="5d",
        interval="1d",
        group_by="ticker",
        auto_adjust=False,
        progress=False,
        threads=True,
    )
    result = {}
    for symbol in symbols:
        try:
            closes = data[symbol]["Close"].dropna()
        except KeyError:
            continue
        if len(closes) < 2:
            continue
        result[symbol] = {
            "price": float(closes.iloc[-1]),
            "prev_close": float(closes.iloc[-2]),
        }
    return result


def get_quotes(symbols):
    """أسعار الرموز مع نسبة التغير، مع تخزين مؤقت لمدة CACHE_SECONDS."""
    now = time.time()
    with _lock:
        stale = [s for s in symbols if s not in _cache or now - _cache[s]["at"] > CACHE_SECONDS]
        if stale:
            try:
                fresh = _download(stale)
            except Exception as e:
                print(f"⚠️ فشل سحب البيانات: {e}")
                fresh = {}
            for symbol, q in fresh.items():
                change = q["price"] - q["prev_close"]
                _cache[symbol] = {
                    "price": round(q["price"], 2),
                    "change": round(change, 2),
                    "change_pct": round(change / q["prev_close"] * 100, 2),
                    "at": now,
                }
        return {s: _cache[s] for s in symbols if s in _cache}
