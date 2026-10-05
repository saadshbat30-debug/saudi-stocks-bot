"""Bollinger Bands breakout with an RSI filter.

Port of the cTrader "Sample Breakout cBot" + RSI filter to daily bars of
Saudi (Tadawul) stocks. The cBot measures band height in pips; for stocks we
use band width as a percentage of the middle band instead.
"""
from dataclasses import dataclass

import pandas as pd


@dataclass
class Params:
    periods: int = 20
    deviations: float = 2.0
    ma_type: str = "wilder"  # "wilder", "sma" or "ema"
    max_band_width_pct: float = 8.0  # replaces BandHeightPips
    consolidation_periods: int = 1
    rsi_period: int = 14
    rsi_overbought: float = 70.0
    rsi_oversold: float = 30.0


def moving_average(series, period, ma_type):
    if ma_type == "sma":
        return series.rolling(period).mean()
    if ma_type == "ema":
        return series.ewm(span=period, adjust=False, min_periods=period).mean()
    if ma_type == "wilder":
        return series.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
    raise ValueError(f"unknown ma_type: {ma_type}")


def bollinger_bands(close, period, deviations, ma_type):
    middle = moving_average(close, period, ma_type)
    std = close.rolling(period).std(ddof=0)
    return middle + deviations * std, middle, middle - deviations * std


def rsi(close, period):
    delta = close.diff()
    gain = delta.clip(lower=0).ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
    loss = (-delta.clip(upper=0)).ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
    rs = gain / loss
    return (100 - 100 / (1 + rs)).where(loss != 0, 100.0)


def find_signals(close, params=Params()):
    """Return a list of (date, side, price, rsi) for every breakout bar.

    Like the cBot, bands and RSI come from the previous closed bar and the
    breakout is checked against the current bar's price.
    """
    top, middle, bottom = bollinger_bands(close, params.periods, params.deviations, params.ma_type)
    rsi_values = rsi(close, params.rsi_period)

    signals = []
    consolidation = 0
    for i in range(1, len(close)):
        prev_top, prev_mid, prev_bottom = top.iloc[i - 1], middle.iloc[i - 1], bottom.iloc[i - 1]
        if pd.isna(prev_top) or pd.isna(prev_mid) or prev_mid == 0:
            continue

        width_pct = (prev_top - prev_bottom) / prev_mid * 100
        if width_pct <= params.max_band_width_pct:
            consolidation += 1
        else:
            consolidation = 0

        if consolidation < params.consolidation_periods:
            continue

        price = close.iloc[i]
        if price > prev_top:
            side = "BUY"
        elif price < prev_bottom:
            side = "SELL"
        else:
            continue

        r = rsi_values.iloc[i - 1]
        if pd.notna(r) and params.rsi_oversold < r < params.rsi_overbought:
            signals.append((close.index[i], side, float(price), float(r)))
            consolidation = 0

    return signals
