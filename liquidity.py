"""Unusual traded-value (liquidity) filter.

Port of the TickerChart scanner condition:

    VALUE >= 2 * (MONTHLASTAMOUNTSUM / 20)

VALUE              -> today's traded value (SAR)
MONTHLASTAMOUNTSUM -> sum of traded value over the last month (20 sessions)
/ 20               -> average daily traded value for that month

So the stock passes when today's traded value is at least double its
average daily traded value over the previous 20 trading sessions.
"""

import requests

LOOKBACK_DAYS = 20
MULTIPLIER = 2

YAHOO_CHART_URL = "https://query1.finance.yahoo.com/v8/finance/chart/{symbol}.SR"


def average_daily_value(values, days=LOOKBACK_DAYS):
    """MONTHLASTAMOUNTSUM / 20: average of the last `days` daily values."""
    last = list(values)[-days:]
    if len(last) < days:
        raise ValueError(f"need {days} sessions of history, got {len(last)}")
    return sum(last) / days


def is_value_spike(today_value, previous_values,
                   multiplier=MULTIPLIER, days=LOOKBACK_DAYS):
    """VALUE >= multiplier * (sum(last `days` values) / days)."""
    return today_value >= multiplier * average_daily_value(previous_values, days)


def fetch_daily_values(symbol):
    """Daily traded values (close * volume) for a Tadawul symbol, oldest first.

    Yahoo does not publish turnover, so value is approximated as
    close * volume. The last element is today's (or the latest) session.
    """
    resp = requests.get(
        YAHOO_CHART_URL.format(symbol=symbol),
        params={"range": "3mo", "interval": "1d"},
        headers={"User-Agent": "Mozilla/5.0"},
        timeout=15,
    )
    resp.raise_for_status()
    quote = resp.json()["chart"]["result"][0]["indicators"]["quote"][0]
    return [
        close * volume
        for close, volume in zip(quote["close"], quote["volume"])
        if close is not None and volume
    ]


def check_symbol(symbol):
    """Evaluate the filter for one symbol and return the numbers behind it."""
    values = fetch_daily_values(symbol)
    today, previous = values[-1], values[:-1]
    average = average_daily_value(previous)
    return {
        "symbol": symbol,
        "value": today,
        "avg_20d_value": average,
        "ratio": today / average if average else 0.0,
        "passed": today >= MULTIPLIER * average,
    }
