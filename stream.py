"""بث مباشر للأسعار من ياهو (WebSocket) — كل صفقة تصل لحظة حدوثها."""
import threading
import time
from datetime import datetime
from zoneinfo import ZoneInfo

import yfinance as yf

REGULAR_MARKET = 1  # market_hours في رسائل ياهو: 0 قبل الافتتاح، 1 الجلسة، 2 بعد الإغلاق

_prices = {}  # الرمز ← آخر سعر من البث (الجلسة الرسمية)
_ext_prices = {}  # الرمز ← آخر سعر قبل الافتتاح / بعد الإغلاق
_night_prices = {}  # الرمز ← آخر سعر في التداول الليلي (Blue Ocean ATS)

NY = ZoneInfo("America/New_York")


def is_overnight(now=None):
    """التداول الليلي: 8 مساءً – 4 فجرًا بتوقيت نيويورك، من ليلة الأحد حتى فجر الجمعة."""
    t = now or datetime.now(NY)
    m = t.hour * 60 + t.minute
    return (m >= 20 * 60 and t.weekday() in (6, 0, 1, 2, 3)) or (m < 4 * 60 and t.weekday() in (0, 1, 2, 3, 4))
_wanted = set()
_state = {"ws": None, "started": False}
_lock = threading.Lock()


def _on_message(msg):
    symbol, price = msg.get("id"), msg.get("price")
    if not symbol or price is None:
        return
    if msg.get("market_hours", 0) == REGULAR_MARKET:
        target = _prices
    else:
        target = _night_prices if is_overnight() else _ext_prices
    target[symbol] = {
        "price": price,
        "change": msg.get("change"),
        "change_pct": msg.get("change_percent"),
        "volume": int(msg.get("day_volume", 0) or 0),
        "at": time.time(),
        # وقت الصفقة من ياهو (بالملي ثانية) لمقارنته بوقت بيانات الماسح
        "trade_time": int(msg.get("time", 0) or 0) / 1000 or time.time(),
    }


def _run():
    while True:
        ws = None
        try:
            ws = yf.WebSocket(verbose=False)
            with _lock:
                _state["ws"] = ws
                symbols = list(_wanted)
            if symbols:
                ws.subscribe(symbols)
            ws.listen(_on_message)
        except Exception as e:
            print(f"⚠️ انقطع البث المباشر، إعادة الاتصال: {e}")
        finally:
            with _lock:
                _state["ws"] = None
            try:
                ws and ws.close()
            except Exception:
                pass
        time.sleep(5)


def _heartbeat():
    # ياهو يتوقف عن الإرسال إذا لم يتجدد الاشتراك
    while True:
        time.sleep(15)
        with _lock:
            ws = _state["ws"]
        if ws is not None and ws._ws is not None:
            try:
                ws.subscribe([])
            except Exception:
                pass


def watch(symbols):
    """يضيف رموزًا للبث ويشغّله أول مرة."""
    with _lock:
        new = [s for s in symbols if s not in _wanted]
        _wanted.update(new)
        ws = _state["ws"]
        if not _state["started"]:
            _state["started"] = True
            threading.Thread(target=_run, daemon=True).start()
            threading.Thread(target=_heartbeat, daemon=True).start()
    if new and ws is not None and ws._ws is not None:
        try:
            ws.subscribe(new)
        except Exception:
            pass


def latest(symbol, max_age=120):
    p = _prices.get(symbol)
    return p if p and time.time() - p["at"] < max_age else None


def latest_night(symbol, max_age=600):
    p = _night_prices.get(symbol)
    return p if p and time.time() - p["at"] < max_age else None


def latest_ext(symbol, max_age=300):
    p = _ext_prices.get(symbol)
    return p if p and time.time() - p["at"] < max_age else None
