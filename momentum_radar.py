"""رادار الزخم (Elite Radar Pro) — منقول من التطبيق المستقل إلى خادم المنصة بلا تغيير في الشروط والأوزان.

المصدر: ماسح TradingView (مكتبة tradingview-screener، غير رسمية). البيانات هناك متأخرة 15 دقيقة للدخول المجهول
(الحقل update_mode يذكر ذلك لكل سهم ويُعرض في الواجهة). المسار /api/momentum-radar يقرأ من snapshot().
"""
import math
import os
import threading
import time
from datetime import datetime, time as dt_time
from zoneinfo import ZoneInfo

APP_NAME = "رادار الزخم"
NY_TZ = ZoneInfo("America/New_York")
CACHE_SECONDS = 15  # الواجهة تحدّث كل 20 ثانية: طلب واحد لـ TradingView مهما تعدد المتصفحون

# فلترة السوق
ALLOWED_EXCHANGES = ["NASDAQ", "NYSE", "AMEX"]
MIN_PRICE = float(os.getenv("MIN_PRICE", "0.5"))
MAX_PRICE = float(os.getenv("MAX_PRICE", "50"))
MIN_CHANGE_PCT = float(os.getenv("MIN_CHANGE_PCT", "1.0"))
MIN_VOLUME = int(os.getenv("MIN_VOLUME", "100000"))
MIN_RVOL = float(os.getenv("MIN_RVOL", "1.5"))

# النتائج
MAX_CANDIDATES = int(os.getenv("MAX_CANDIDATES", "120"))
TOP_RESULTS = int(os.getenv("TOP_RESULTS", "5"))
MIN_SCORE_TO_SHOW = float(os.getenv("MIN_SCORE_TO_SHOW", "60"))
BREAKOUT_DIST_MAX_PCT = float(os.getenv("BREAKOUT_DIST_MAX_PCT", "1.0"))

# النقاط
POINTS_RVOL = 25
POINTS_CHANGE = 10
POINTS_BREAKOUT = 30

BASE_FIELDS = ["name", "exchange", "close", "change", "volume", "relative_volume_10d_calc", "market_cap_basic",
               "sector", "update_mode", "high|15"]
RENAME = {"ticker": "symbol", "close": "price", "change": "change_pct", "relative_volume_10d_calc": "rvol",
          "market_cap_basic": "market_cap", "high|15": "high_15m"}
# حقول إضافية من TradingView يحتاجها مؤشر الزخم
MOMENTUM_RENAME = {"change|5": "chg_5m", "change|15": "chg_15m", "change|60": "chg_1h", "RSI": "rsi",
                   "MACD.macd": "macd", "MACD.signal": "macd_signal", "EMA20": "ema20", "EMA50": "ema50", "VWAP": "vwap"}
MOMENTUM_FIELDS = list(MOMENTUM_RENAME)
# وزن كل مكوّن (المجموع 100)
MOMENTUM_WEIGHTS = {"speed": 30, "rsi": 20, "macd": 15, "trend": 15, "volume": 20}
NUMERIC = ["price", "change_pct", "volume", "rvol", "market_cap", "high_15m", *MOMENTUM_RENAME.values()]
REQUIRED = ["symbol", "exchange", "price", "change_pct", "volume", "rvol"]


def _num(row, key):
    """الرقم أو None إذا كانت القيمة غير متوفرة."""
    v = row.get(key)
    try:
        v = float(v)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(v) else v


def safe_float(x, default=0.0):
    try:
        x = float(x)
    except (TypeError, ValueError):
        return default
    return default if math.isnan(x) else x


def tv_link(symbol):
    return f"https://www.tradingview.com/chart/?symbol={symbol}"


def get_market_session(now=None):
    now_ny = now or datetime.now(NY_TZ)
    t, stamp = now_ny.time(), now_ny.strftime("%Y-%m-%d %H:%M:%S")
    closed = {"name": "Closed", "label_ar": "مغلق", "is_live": False, "now_ny": stamp}
    if now_ny.weekday() >= 5:
        return closed
    if dt_time(4, 0) <= t < dt_time(9, 30):
        return {"name": "Pre-market", "label_ar": "ما قبل الافتتاح", "is_live": True, "now_ny": stamp}
    if dt_time(9, 30) <= t < dt_time(16, 0):
        return {"name": "Market Open", "label_ar": "السوق مفتوح", "is_live": True, "now_ny": stamp}
    if dt_time(16, 0) <= t < dt_time(20, 0):
        return {"name": "After-hours", "label_ar": "ما بعد الإغلاق", "is_live": True, "now_ny": stamp}
    return closed


def _scan(extra_fields):
    """طلب واحد لماسح TradingView ← قائمة صفوف خام (قواميس)."""
    from tradingview_screener import Column, Query
    q = (Query().select(*BASE_FIELDS, *extra_fields)
         .where(Column("close").between(MIN_PRICE, MAX_PRICE), Column("change") >= MIN_CHANGE_PCT,
                Column("volume") >= MIN_VOLUME, Column("relative_volume_10d_calc") >= MIN_RVOL)
         .order_by("relative_volume_10d_calc", ascending=False).limit(400))
    _, df = q.get_scanner_data()
    return [] if df is None or df.empty else df.to_dict("records")


def source_name():
    import alpaca_stream
    return "Alpaca (SIP)" if alpaca_stream.source_is_alpaca() else "TradingView Screener"


def fetch_candidates():
    import alpaca_stream
    if alpaca_stream.source_is_alpaca():
        import radar_alpaca  # الحقول نفسها من بيانات Alpaca اللحظية (DATA_SOURCE=alpaca)
        raw = radar_alpaca.scan(MIN_PRICE, MAX_PRICE, MIN_CHANGE_PCT, MIN_VOLUME, MIN_RVOL)
    else:
        # لو رفض TradingView أحد حقول الزخم نرجع للحقول الأساسية فقط، ويُحسب المؤشر من المتوفر بدل أن يتعطل الرادار
        try:
            raw = _scan(MOMENTUM_FIELDS)
        except ImportError:
            raise
        except Exception as e:
            print(f"⚠️ رادار الزخم: رُفضت حقول الزخم، رجوع للحقول الأساسية: {e}")
            raw = _scan([])
    rows = []
    for r in raw:
        row = {{**RENAME, **MOMENTUM_RENAME}.get(k, k): v for k, v in r.items()}
        if row.get("exchange") not in ALLOWED_EXCHANGES:
            continue
        for col in NUMERIC:
            if col in row:
                row[col] = _num(row, col)
        if any(row.get(k) is None for k in REQUIRED):
            continue
        # قرب السعر من قمة آخر 15 دقيقة
        high = row.get("high_15m")
        row["breakout_dist_pct"] = max(0.0, (high - row["price"]) / high * 100) if high else 999.0
        rows.append(row)
    return rows


def _ratio(value, full):
    """يحوّل القيمة إلى نسبة بين 0 و 1 (تكتمل عند full)."""
    return max(0.0, min(1.0, value / full))


def compute_momentum(row):
    """مؤشر زخم مركّب من 0 إلى 100. كل مكوّن يأخذ نسبة من 0 إلى 1، والمكوّن الذي لا تتوفر بياناته يُستبعد
    ويُعاد توزيع وزنه على الباقي."""
    price = _num(row, "price")
    parts = {}

    # 1) السرعة: +1.5% في 5 دقائق، +3% في 15 دقيقة، +5% في ساعة = كامل
    speeds = [(_num(row, "chg_5m"), 1.5), (_num(row, "chg_15m"), 3.0), (_num(row, "chg_1h"), 5.0)]
    speeds = [_ratio(v, full) for v, full in speeds if v is not None]
    if speeds:
        parts["speed"] = sum(speeds) / len(speeds)

    # 2) RSI: يبدأ من 50 ويكتمل عند 70، ويضعف بعد 80 (تشبّع شراء)
    rsi = _num(row, "rsi")
    if rsi is not None:
        parts["rsi"] = _ratio(rsi - 50, 20) if rsi <= 80 else max(0.3, 1 - (rsi - 80) / 20)

    # 3) MACD: فوق خط الإشارة + فوق الصفر
    macd, sig = _num(row, "macd"), _num(row, "macd_signal")
    if macd is not None and sig is not None:
        parts["macd"] = (0.65 if macd > sig else 0.0) + (0.35 if macd > 0 else 0.0)

    # 4) الاتجاه: السعر فوق VWAP و EMA20، و EMA20 فوق EMA50
    vwap, ema20, ema50 = _num(row, "vwap"), _num(row, "ema20"), _num(row, "ema50")
    checks = []
    if price is not None and vwap is not None:
        checks.append(price > vwap)
    if price is not None and ema20 is not None:
        checks.append(price > ema20)
    if ema20 is not None and ema50 is not None:
        checks.append(ema20 > ema50)
    if checks:
        parts["trend"] = sum(checks) / len(checks)

    # 5) الحجم النسبي: يبدأ من 1x ويكتمل عند 5x
    rvol = _num(row, "rvol")
    if rvol is not None:
        parts["volume"] = _ratio(rvol - 1, 4)

    total_weight = sum(MOMENTUM_WEIGHTS[k] for k in parts)
    value = sum(parts[k] * MOMENTUM_WEIGHTS[k] for k in parts) / total_weight * 100 if total_weight else 0.0

    if len(parts) < 3:
        label = "بيانات غير كافية"  # لا نحكم على الزخم من مكوّن أو مكوّنين فقط
    elif value >= 75:
        label = "زخم قوي جداً"
    elif value >= 55:
        label = "زخم قوي"
    elif value >= 35:
        label = "زخم متوسط"
    else:
        label = "زخم ضعيف"

    # هل الزخم يتسارع؟ وتيرة آخر 5 دقائق مقابل متوسط وتيرة آخر 15 دقيقة
    c5, c15 = _num(row, "chg_5m"), _num(row, "chg_15m")
    if c5 is None or c15 is None:
        trend = "غير معروف"
    elif c5 < 0:
        trend = "يتراجع"
    elif c5 > 0 and c5 * 3 > c15:
        trend = "يتسارع"
    else:
        trend = "ثابت"

    return {"value": round(value, 1), "label": label, "trend": trend,
            "parts": {k: round(v * 100) for k, v in parts.items()}}


def classify_row(row, session_name):
    price, chg = safe_float(row.get("price")), safe_float(row.get("change_pct"))
    vol, rvol = safe_float(row.get("volume")), safe_float(row.get("rvol"))
    breakout_dist = safe_float(row.get("breakout_dist_pct"), 999.0)

    # السهم متأخر إذا صار بعيدًا عن البداية أو الحركة صارت مبالغًا فيها
    late_move = chg >= 12 or breakout_dist > 2.5 or rvol >= 8

    score = 0
    reasons = {"rvol": False, "change": False, "breakout": False}
    if rvol > 1.5:  # 1) الحجم الذكي
        score += POINTS_RVOL
        reasons["rvol"] = True
    if chg > 2:  # 2) التغير
        score += POINTS_CHANGE
        reasons["change"] = True
    if breakout_dist <= BREAKOUT_DIST_MAX_PCT:  # 3) قريب من اختراق قمة 15 دقيقة
        score += POINTS_BREAKOUT
        reasons["breakout"] = True

    if vol < 150000:  # تخفيف لبعض الحالات الضعيفة
        score -= 5
    if price < 0.75 and chg > 20:
        score -= 8
    if late_move:  # خصم واضح للحركات المتأخرة
        score -= 18

    # تعديل حسب الجلسة
    if session_name == "Pre-market":
        if breakout_dist <= BREAKOUT_DIST_MAX_PCT and rvol >= 2:
            score += 8
    elif session_name == "Market Open":
        if breakout_dist <= BREAKOUT_DIST_MAX_PCT and chg >= 2:
            score += 10
    elif session_name == "After-hours":
        score -= 5
    elif session_name == "Closed":
        score -= 15

    if late_move:
        setup = "متأخر / ممتد"
    elif score >= 60 and reasons["breakout"] and reasons["rvol"]:
        setup = "اختراق مبكر محتمل"
    elif score >= 60 and reasons["rvol"] and reasons["change"]:
        setup = "زخم قوي"
    elif score >= 60:
        setup = "مراقبة"
    else:
        setup = "مستبعد"
    return setup, round(score, 1), reasons


PRIORITY = {"اختراق مبكر محتمل": 3, "زخم قوي": 2, "مراقبة": 1, "مستبعد": 0, "متأخر / ممتد": -1}


def build_table(session_name, rows=None):
    rows = fetch_candidates() if rows is None else rows
    out = []
    for row in rows:
        setup, score, reasons = classify_row(row, session_name)
        # الحركات المتأخرة وما دون حد العرض لا تظهر
        if setup == "متأخر / ممتد" or score < MIN_SCORE_TO_SHOW:
            continue
        m = compute_momentum(row)
        out.append({**row, "setup": setup, "score": score, "pass_rvol": reasons["rvol"], "pass_change": reasons["change"],
                    "pass_breakout": reasons["breakout"], "momentum": m["value"], "momentum_info": m})
    out.sort(key=lambda r: (PRIORITY.get(r["setup"], 0), r["score"], r["momentum"], r["rvol"], r["volume"], r["change_pct"]),
             reverse=True)
    return out[:MAX_CANDIDATES]


def _stock(row):
    rsi = _num(row, "rsi")
    return {
        "symbol": str(row.get("symbol", "")),
        "name": str(row.get("name", "")),
        "exchange": str(row.get("exchange", "")),
        "price": round(safe_float(row.get("price")), 4),
        "change_pct": round(safe_float(row.get("change_pct")), 2),
        "volume": int(safe_float(row.get("volume"))),
        "rvol": round(safe_float(row.get("rvol")), 2),
        "setup": row["setup"],
        "score": row["score"],
        "breakout_dist_pct": round(safe_float(row.get("breakout_dist_pct"), 999.0), 2),
        "pass_rvol": row["pass_rvol"],
        "pass_change": row["pass_change"],
        "pass_breakout": row["pass_breakout"],
        "tv_link": tv_link(str(row.get("symbol", ""))),
        "rsi": round(rsi, 1) if rsi is not None else None,
        "momentum": row["momentum_info"],
        # delayed_streaming_900 = متأخر 15 دقيقة
        "update_mode": row.get("update_mode"),
    }


_cache = {"at": 0, "data": None}
_lock = threading.Lock()


def snapshot():
    """رد /api/momentum-radar: نفس شكل رد التطبيق المستقل، مع ok=False ورسالة عند تعذر الجلب."""
    with _lock:
        if _cache["data"] and time.time() - _cache["at"] < CACHE_SECONDS:
            return _cache["data"]
        session = get_market_session()
        src = source_name()
        base = {"app_name": APP_NAME, "session": session, "updated_at": session["now_ny"], "source": src}
        try:
            rows = build_table(session["name"])
        except ImportError:
            return {**base, "ok": False, "count": 0, "stocks": [],
                    "error": "مكتبة tradingview-screener غير مثبتة: نفّذ py -m pip install -r requirements.txt"}
        except Exception as e:
            print(f"⚠️ رادار الزخم: تعذر الجلب: {e}")
            stale = _cache["data"]
            if stale:  # آخر نتيجة ناجحة مع التنبيه، بدل شاشة فارغة
                return {**stale, "stale": True, "error": f"تعذر تحديث البيانات من {src}: {str(e)[:120]}"}
            return {**base, "ok": False, "count": 0, "stocks": [],
                    "error": f"تعذر جلب البيانات من {src}: {str(e)[:120]}"}
        stocks = [_stock(r) for r in rows[:TOP_RESULTS]]
        data = {**base, "ok": True, "count": len(stocks), "matched": len(rows), "stocks": stocks}
        _cache.update(at=time.time(), data=data)
        return data
