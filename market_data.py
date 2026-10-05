"""Saudi market data from the SAHMK API (https://www.sahmk.sa/developers).

Responses are cached in memory because the free plan allows only 100
requests per day.
"""
import os
import time
from datetime import date, timedelta

import pandas as pd
from sahmk import SahmkClient, SahmkError

from strategy import Params, find_signals, indicator_state

CACHE_SECONDS = int(os.environ.get("CACHE_SECONDS", 900))
HISTORY_CACHE_SECONDS = int(os.environ.get("HISTORY_CACHE_SECONDS", 3600))

_client = None
_cache = {}


def client():
    global _client
    if _client is None:
        api_key = os.environ.get("SAHMK_API_KEY", "")
        if not api_key:
            raise SahmkError("SAHMK_API_KEY غير مضبوط")
        _client = SahmkClient(api_key)
    return _client


def cached(key, ttl, fetch):
    """Return (value, error_message), caching failures too so a plan-limit
    error doesn't burn a request on every page load."""
    hit = _cache.get(key)
    if hit and time.time() < hit[0]:
        return hit[1]
    try:
        result = (fetch(), None)
    except SahmkError as e:
        result = (None, str(e))
        if getattr(e, "status_code", None) == 403:
            ttl = 24 * 3600  # not in this plan; don't keep asking
    _cache[key] = (time.time() + ttl, result)
    return result


def market_summary():
    return cached("summary", CACHE_SECONDS, lambda: client().market_summary())


def movers(kind, limit=5):
    return cached(kind, CACHE_SECONDS, lambda: getattr(client(), kind)(limit=limit).stocks)


def quote(symbol):
    return cached(f"quote:{symbol}", CACHE_SECONDS, lambda: client().quote(symbol))


def closes(symbol):
    """Daily closing prices for the last year (Starter plan or higher)."""
    def fetch():
        result = client().historical(
            symbol,
            from_date=(date.today() - timedelta(days=365)).isoformat(),
            to_date=date.today().isoformat(),
            interval="1d",
        )
        rows = [(p.date, p.close) for p in result.data if p.date and p.close is not None]
        return pd.Series(
            [c for _, c in rows], index=pd.to_datetime([d for d, _ in rows]), dtype=float
        ).sort_index()

    return cached(f"hist:{symbol}", HISTORY_CACHE_SECONDS, fetch)


def analyze(symbol, params=Params()):
    """Quote plus Bollinger/RSI state and signals for one symbol."""
    q, quote_error = quote(symbol)
    close, history_error = closes(symbol)
    row = {"symbol": symbol, "quote": q, "error": quote_error, "history_error": history_error}
    if close is not None and len(close) > params.periods:
        signals = find_signals(close, params)
        row["state"] = indicator_state(close, params)
        row["last_signal"] = signals[-1] if signals else None
        row["signal_today"] = bool(signals) and signals[-1][0] == close.index[-1]
    return row


DIVIDEND_CACHE_SECONDS = 24 * 3600


def quotes(symbols):
    """Prices for many symbols: one batch request (Starter+), else one request each."""
    symbols = list(symbols)
    if not symbols:
        return {}, None
    batch, _ = cached(
        "quotes:" + ",".join(sorted(symbols)),
        CACHE_SECONDS,
        lambda: {q.symbol: q for q in client().quotes(symbols).quotes},
    )
    if batch is not None:
        return batch, None
    out, error = {}, None
    for s in symbols:
        q, e = quote(s)
        if q is not None:
            out[s] = q
        else:
            error = e
    return out, error


def dividends(symbol):
    return cached(f"div:{symbol}", DIVIDEND_CACHE_SECONDS, lambda: client().dividends(symbol))
