"""عقود الخيارات (كول وبوت) من ياهو: تواريخ الانتهاء، السلسلة، الإغريقيات، Max Pain، ونسبة البوت/الكول.

بيانات ياهو للعقود متأخرة نحو 15 دقيقة.
"""
import math
import threading
import time
from datetime import datetime, timezone

import yfinance as yf

EXP_SECONDS = 600
CHAIN_SECONDS = 60
RATE = 0.04  # عائد خالٍ من المخاطر تقريبي لحساب الإغريقيات
_exp, _chain = {}, {}
_lock = threading.Lock()


def _f(v):
    """رقم صالح أو None (ياهو يرجع NaN كثيرًا)."""
    try:
        v = float(v)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(v) or math.isinf(v) else v


def expirations(symbol):
    now = time.time()
    with _lock:
        c = _exp.get(symbol)
        if c and now - c["at"] < EXP_SECONDS:
            return c["dates"]
    try:
        dates = list(yf.Ticker(symbol).options or [])
    except Exception as e:
        print(f"⚠️ فشل جلب تواريخ عقود {symbol}: {e}")
        dates = c["dates"] if c else []
    with _lock:
        _exp[symbol] = {"dates": dates, "at": now}
    return dates


# ===== بلاك-شولز: الدلتا والجاما والثيتا والفيغا من التذبذب الضمني =====

def _ncdf(x):
    return 0.5 * (1 + math.erf(x / math.sqrt(2)))


def _npdf(x):
    return math.exp(-x * x / 2) / math.sqrt(2 * math.pi)


def greeks(kind, spot, strike, years, iv):
    if not (spot and strike and iv and years) or iv <= 0 or years <= 0:
        return {}
    sq = iv * math.sqrt(years)
    d1 = (math.log(spot / strike) + (RATE + iv * iv / 2) * years) / sq
    d2 = d1 - sq
    disc = math.exp(-RATE * years)
    if kind == "call":
        delta = _ncdf(d1)
        theta = -spot * _npdf(d1) * iv / (2 * math.sqrt(years)) - RATE * strike * disc * _ncdf(d2)
    else:
        delta = _ncdf(d1) - 1
        theta = -spot * _npdf(d1) * iv / (2 * math.sqrt(years)) + RATE * strike * disc * _ncdf(-d2)
    return {
        "delta": round(delta, 3),
        "gamma": round(_npdf(d1) / (spot * sq), 4),
        "theta": round(theta / 365, 4),  # لكل يوم، للسهم الواحد
        "vega": round(spot * _npdf(d1) * math.sqrt(years) / 100, 4),  # لكل 1% تذبذب
        "itm_prob": round((_ncdf(d2) if kind == "call" else _ncdf(-d2)) * 100, 1),  # احتمال الانتهاء داخل السعر
    }


def _rows(df, kind, spot, years):
    out = []
    for r in df.to_dict("records"):
        strike = _f(r.get("strike"))
        if strike is None:
            continue
        bid, ask, last = _f(r.get("bid")), _f(r.get("ask")), _f(r.get("lastPrice"))
        mid = (bid + ask) / 2 if bid and ask else last
        iv = _f(r.get("impliedVolatility"))
        vol, oi = int(_f(r.get("volume")) or 0), int(_f(r.get("openInterest")) or 0)
        t = r.get("lastTradeDate")
        row = {
            "contract": r.get("contractSymbol"), "strike": strike, "last": last, "bid": bid, "ask": ask, "mid": mid,
            "change": _f(r.get("change")), "change_pct": _f(r.get("percentChange")),
            "volume": vol, "oi": oi, "iv": round(iv * 100, 1) if iv else None,
            "itm": bool(r.get("inTheMoney")),
            "last_trade": int(t.timestamp()) if hasattr(t, "timestamp") and not (isinstance(t, float) and math.isnan(t)) else None,
            # سعر التعادل عند الانتهاء: الكول = الإضراب + العلاوة، البوت = الإضراب − العلاوة
            "breakeven": round(strike + mid, 4) if mid and kind == "call" else round(strike - mid, 4) if mid else None,
            # نشاط غير معتاد: حجم اليوم أكبر من العقود المفتوحة
            "unusual": vol >= 500 and vol > oi,
        }
        row.update(greeks(kind, spot, strike, years, iv))
        out.append(row)
    return out


def _max_pain(calls, puts):
    """سعر الانتهاء الذي يُخسر فيه مشترو العقود أكبر قيمة (أقل مبلغ يُدفع لحاملي العقود)."""
    strikes = sorted({r["strike"] for r in calls + puts})
    best, low = None, None
    for k in strikes:
        pay = sum(r["oi"] * max(0, k - r["strike"]) for r in calls) + sum(r["oi"] * max(0, r["strike"] - k) for r in puts)
        if low is None or pay < low:
            best, low = k, pay
    return best


def chain(symbol, date=None, spot=None):
    dates = expirations(symbol)
    if not dates:
        return {"expirations": [], "error": "لا توجد عقود خيارات لهذا السهم في ياهو"}
    date = date if date in dates else dates[0]
    key, now = (symbol, date), time.time()
    with _lock:
        c = _chain.get(key)
    if not (c and now - c["at"] < CHAIN_SECONDS):
        try:
            oc = yf.Ticker(symbol).option_chain(date)
            c = {"oc": oc, "at": now}
            with _lock:
                _chain[key] = c
        except Exception as e:
            print(f"⚠️ فشل جلب عقود {symbol} {date}: {e}")
            if not c:
                return {"expirations": dates, "date": date, "error": "تعذر جلب العقود من ياهو"}
    oc = c["oc"]
    under = getattr(oc, "underlying", None) or {}
    spot = spot or _f(under.get("regularMarketPrice"))
    # الانتهاء: إغلاق الجلسة 4 م نيويورك ≈ 20:00 UTC
    exp = datetime.strptime(date, "%Y-%m-%d").replace(hour=20, tzinfo=timezone.utc)
    years = max((exp - datetime.now(timezone.utc)).total_seconds(), 3600) / (365 * 86400)
    calls, puts = _rows(oc.calls, "call", spot, years), _rows(oc.puts, "put", spot, years)
    cv, pv = sum(r["volume"] for r in calls), sum(r["volume"] for r in puts)
    co, po = sum(r["oi"] for r in calls), sum(r["oi"] for r in puts)
    return {
        "symbol": symbol, "expirations": dates, "date": date, "spot": spot,
        "days": round(years * 365, 1), "updated": int(c["at"]),
        "calls": calls, "puts": puts,
        "summary": {
            "call_volume": cv, "put_volume": pv, "call_oi": co, "put_oi": po,
            "pcr_volume": round(pv / cv, 2) if cv else None, "pcr_oi": round(po / co, 2) if co else None,
            "max_pain": _max_pain(calls, puts),
            "unusual": sum(1 for r in calls + puts if r["unusual"]),
        },
    }


# ===== فلتر العقود: كل العقود ضمن مدة الانتهاء المطلوبة لعدة أسهم =====

def _dte(date):
    exp = datetime.strptime(date, "%Y-%m-%d").replace(hour=20, tzinfo=timezone.utc)
    return (exp - datetime.now(timezone.utc)).total_seconds() / 86400


def scan(symbols, dte_min=0, dte_max=60, spots=None, max_strike_pct=40):
    """عقود كل سهم في تواريخ الانتهاء ضمن [dte_min, dte_max] يومًا، مع الفرق بين العرض والطلب.
    الفلترة الدقيقة تتم في الصفحة حتى يتغير الناتج فورًا مع تعديل الشروط."""
    from concurrent.futures import ThreadPoolExecutor
    spots = spots or {}
    jobs, nearest = [], {}
    for s in symbols:
        dates = expirations(s)
        inside = [d for d in dates if dte_min - 1 <= _dte(d) <= dte_max + 1]
        if not inside and dates:
            # لا يوجد انتهاء داخل المدة: أقرب تاريخ لها بدل لا شيء
            d = min(dates, key=lambda d: min(abs(_dte(d) - dte_min), abs(_dte(d) - dte_max)))
            inside, nearest[s] = [d], round(_dte(d))
        jobs += [(s, d) for d in inside]
    results, errors = [], {}
    with ThreadPoolExecutor(max_workers=4) as pool:
        chains = list(pool.map(lambda j: (j, chain(j[0], j[1], spots.get(j[0]))), jobs))
    for (s, d), c in chains:
        if c.get("error"):
            errors[s] = c["error"]
            continue
        spot = c.get("spot")
        for kind in ("call", "put"):
            for r in c[kind + "s"]:
                if spot and abs(r["strike"] / spot - 1) * 100 > max_strike_pct:
                    continue
                spread = round(r["ask"] - r["bid"], 4) if r["bid"] is not None and r["ask"] else None
                results.append({
                    **r, "symbol": s, "kind": kind, "date": d, "dte": round(c["days"], 1), "spot": spot,
                    "spread": spread,
                    "spread_pct": round(spread / r["mid"] * 100, 1) if spread is not None and r["mid"] else None,
                    "strike_pct": round((r["strike"] / spot - 1) * 100, 2) if spot else None,
                    "cost": round(r["ask"] * 100, 2) if r["ask"] else None,  # تكلفة العقد بسعر الطلب
                    "be_pct": round((r["breakeven"] / spot - 1) * 100, 2) if r["breakeven"] and spot else None,
                })
    for s in symbols:
        if s not in errors and not any(j[0] == s for j in jobs):
            errors[s] = "لا توجد عقود لهذا السهم"
        elif s in nearest:
            errors[s] = f"لا يوجد انتهاء ضمن المدة — عُرض أقرب تاريخ ({nearest[s]} يوم)"
    return results, errors
