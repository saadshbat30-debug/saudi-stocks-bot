import unittest

import numpy as np
import pandas as pd

from strategy import Params, find_signals, rsi


def series(values):
    return pd.Series(values, index=pd.date_range("2025-01-01", periods=len(values)), dtype=float)


class StrategyTest(unittest.TestCase):
    def test_rsi_bounds(self):
        self.assertEqual(rsi(series(range(1, 40)), 14).iloc[-1], 100.0)
        r = rsi(series(100 + np.sin(np.arange(60))), 14).dropna()
        self.assertTrue(((r >= 0) & (r <= 100)).all())

    def test_breakout_after_squeeze(self):
        flat = 100 + 0.2 * np.sin(np.arange(40))
        signals = find_signals(series(list(flat) + [103]), Params(ma_type="sma"))
        self.assertEqual(signals[-1][1], "BUY")

    def test_rsi_filter_blocks_overbought(self):
        # Steady uptrend inside tight bands keeps RSI above 70
        trend = 100 + 0.05 * np.arange(40)
        p = Params(ma_type="sma", max_band_width_pct=50)
        self.assertEqual(find_signals(series(list(trend) + [110]), p), [])

    def test_wide_bands_no_signal(self):
        noisy = 100 + 10 * np.sin(np.arange(40))
        self.assertEqual(find_signals(series(list(noisy) + [130]), Params(ma_type="sma")), [])


if __name__ == "__main__":
    unittest.main()
