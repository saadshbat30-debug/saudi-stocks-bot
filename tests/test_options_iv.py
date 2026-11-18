import options


def test_implied_vol_recovers_input():
    for kind in ("call", "put"):
        for strike in (350, 383, 420):
            for years in (3600 / (365 * 86400), 30 / 365):
                price = options._bs_price(kind, 383.46, strike, years, 0.45)
                intrinsic = max(0, 383.46 - strike) if kind == "call" else max(0, strike - 383.46)
                if price - intrinsic < 0.01:  # بلا قيمة زمنية: التذبذب غير قابل للتحديد
                    continue
                iv = options.implied_vol(kind, 383.46, strike, years, price)
                assert abs(iv - 0.45) < 1e-4


def test_implied_vol_rejects_price_below_intrinsic():
    assert options.implied_vol("call", 383.46, 345, 1 / 365, 30.0) is None
    assert options.implied_vol("put", 383.46, 400, 1 / 365, 0) is None


def test_rows_fill_missing_iv_only_when_asked():
    class F:
        def to_dict(self, orient):
            return [{"contractSymbol": "X", "strike": 385, "bid": 2.04, "ask": 2.20, "lastPrice": 2.13, "impliedVolatility": None}]
    years = 4 * 3600 / (365 * 86400)
    solved = options._rows(F(), "put", 383.46, years, True)[0]
    assert solved["iv_calc"] and solved["iv"] > 0 and solved["delta"] < 0
    plain = options._rows(F(), "put", 383.46, years)[0]
    assert plain["iv"] is None and not plain["iv_calc"] and "delta" not in plain
