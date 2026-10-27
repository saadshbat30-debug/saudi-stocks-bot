"""بيانات ثابتة لاختبار أن طبقة جودة البيانات لا تغيّر نتائج الفلاتر ولا الترتيب.

صفوف بصيغة رد ماسح ياهو الخام، تغطي حالات حقيقية رأيناها: سهم مدرج نشط، OTC بإغلاق سابق شاذ،
سعر أجزاء السنت، سهم بلا أسهم حرة، سهم في البث وآخر من الماسح فقط، وما قبل الافتتاح.
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

NOW = 1793110000.0  # وقت ثابت للاختبار (الجلسة الرسمية، 27 أكتوبر 2026 تقريبًا)


def raw(symbol, price, prev, volume, exchange="NMS", avg=1_000_000, shares=20_000_000, hi=None, lo=None, opn=None,
        t=NOW - 30, state="REGULAR", **extra):
    return {"symbol": symbol, "shortName": symbol + " Inc", "regularMarketPrice": price, "regularMarketPreviousClose": prev,
            "regularMarketChange": price - prev, "regularMarketChangePercent": (price / prev - 1) * 100 if prev else 0,
            "regularMarketOpen": opn if opn is not None else prev, "regularMarketDayHigh": hi if hi is not None else max(price, prev),
            "regularMarketDayLow": lo if lo is not None else min(price, prev), "regularMarketVolume": volume,
            "averageDailyVolume3Month": avg, "sharesOutstanding": shares, "exchange": exchange, "marketState": state,
            "regularMarketTime": t, "fiftyTwoWeekHigh": price * 2, "fiftyTwoWeekLow": price / 2, **extra}


RAW = [
    raw("AAPL", 230.5, 228.0, 40_000_000, avg=50_000_000, shares=15_000_000_000),
    raw("XXII", 0.1426, 0.0884, 35_400_000, avg=6_450_000, shares=63_000),           # فلوت صغير جدًا
    raw("SGRY", 23.25, 15.75, 58_800_000, exchange="NYQ", avg=3_120_000, shares=127_000_000),
    raw("BRAI", 3.01, 2.12, 4_400_000, avg=131_000, shares=40_000_000),
    raw("FREDF", 0.3725, 0.0002, 1_000, exchange="PNK", avg=50_000, shares=56_100_000, t=NOW - 3 * 86400, _otc=True),  # إغلاق سابق شاذ
    raw("IVHI", 0.0165, 0.0000256, 10_000, exchange="PNK", avg=66_000, shares=104_700_000, _otc=True),
    raw("AMRLL", 0.08, 0.0002, 0, exchange="PNK", avg=0, shares=560_500_000, t=NOW - 20 * 86400, _otc=True),   # بلا حجم وصفقة قديمة
    raw("RLBD", 0.0001, 0.00001, 1_000_000, exchange="PNK", avg=4_470, shares=934_400_000, _otc=True),
    raw("INNI", 5.0, 5.0, 2_413_400, exchange="NCM", avg=1_000, shares=3_000_000),      # RVOL ضخم بمتوسط ضئيل
    raw("NOFL", 7.4, 7.0, 900_000, exchange="NGM", avg=400_000, shares=30_000_000),     # لا أسهم حرة (يُستخدم القائم)
    raw("PRE1", 12.0, 11.0, 600_000, exchange="NMS", avg=300_000, shares=9_000_000, state="PRE", preMarketPrice=12.6,
        preMarketChangePercent=5.0, preMarketTime=NOW - 12),
    raw("ASE1", 4.2, 4.0, 800_000, exchange="ASE", avg=200_000, shares=12_000_000),     # NYSE American
]

# أسهم حرة (None = غير متوفر)
FLOATS = {"AAPL": 14_800_000_000, "XXII": 501_000, "SGRY": 58_800_000, "BRAI": 40_000_000, "FREDF": 30_700_000,
          "IVHI": None, "AMRLL": 3_810_000_000, "RLBD": None, "INNI": 2_500_000, "NOFL": None, "PRE1": 8_000_000, "ASE1": 11_000_000}

# آخر رسالة بث لبعض الأسهم: (السعر، التغير %، حجم اليوم، وقت الصفقة بالثواني، وقت الاستلام)
STREAM = {"AAPL": (230.9, 1.27, 40_100_000, NOW - 2, NOW - 1), "BRAI": (3.05, 43.9, 4_450_000, NOW - 4, NOW - 3)}

# مقاييس «قبل الانطلاق» الثابتة لكل سهم (كما يحسبها breakout.metrics من البث)
METRICS = {
    "AAPL": {"acc1m": 1.6, "rvol1m": 2.7, "chg3m": 0.2, "rng3m": 0.5, "pos3m": 70.0, "val3m": 9e6, "vol1m": 300_000, "age": 2.0},
    "BRAI": {"acc1m": 3.0, "rvol1m": 8.0, "chg3m": 0.5, "rng3m": 1.0, "pos3m": 80.0, "val3m": 4e5, "vol1m": 50_000, "age": 3.0},
    "NOFL": {"acc1m": 1.8, "rvol1m": 3.0, "chg3m": 0.1, "rng3m": 0.9, "pos3m": 65.0, "val3m": 2e5, "vol1m": 20_000, "age": 4.0},
    "ASE1": {"acc1m": 2.0, "rvol1m": 4.0, "chg3m": 0.3, "rng3m": 1.1, "pos3m": 75.0, "val3m": 1.5e5, "vol1m": 30_000, "age": 1.0},
}


def build():
    """يشغّل كود المنصة الحقيقي (_row ثم _enrich ثم breakout.evaluate) على البيانات الثابتة مع بدائل للبث."""
    import time
    import market
    import breakout
    import extras
    import stream

    real_time = time.time
    time.time = lambda: NOW
    try:
        stream.latest = lambda s: ({"price": STREAM[s][0], "change": None, "change_pct": STREAM[s][1], "volume": STREAM[s][2],
                                    "trade_time": STREAM[s][3], "at": STREAM[s][4]} if s in STREAM else None)
        stream.latest_ext = lambda s: None
        stream.latest_night = lambda s: None
        market._night_quotes.clear() if hasattr(market, "_night_quotes") else None
        extras.float_for = lambda s: FLOATS.get(s)
        extras.sharia_for = lambda s: {"status": "unknown", "why": "", "source": None, "purify": None}
        market._samples.clear()
        market._market_history.clear()
        market._market_history.update({r["symbol"]: [r["regularMarketPreviousClose"]] * 30 for r in RAW})
        orig_metrics = breakout.metrics
        breakout.metrics = lambda s, avg=None, now=None: METRICS.get(s)
        try:
            rows = [market._row(dict(q)) for q in RAW]
            enriched = [market._enrich(dict(r), NOW) for r in rows if r]
            filt = {}
            for r, e in zip(rows, enriched):
                q = {**r, "price": e["price"], "volume": e["volume"], "float": FLOATS.get(r["symbol"])}
                vals, ok = breakout.evaluate(q, METRICS.get(r["symbol"]))
                filt[r["symbol"]] = {"vals": vals, "ok": ok}
        finally:
            breakout.metrics = orig_metrics
        return rows, enriched, filt
    finally:
        time.time = real_time
