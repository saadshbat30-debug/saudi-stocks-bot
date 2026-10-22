"""بث مباشر للأسعار من ياهو (WebSocket) — كل صفقة تصل لحظة حدوثها."""
import threading
import time
from collections import deque
from datetime import datetime
from zoneinfo import ZoneInfo

import yfinance as yf

REGULAR_MARKET = 1  # market_hours في رسائل ياهو: 0 قبل الافتتاح، 1 الجلسة، 2 بعد الإغلاق

_prices = {}  # الرمز ← آخر سعر من البث (الجلسة الرسمية)
_ext_prices = {}  # الرمز ← آخر سعر قبل الافتتاح / بعد الإغلاق
_tape = {}  # الرمز ← آخر الصفقات (من فرق الحجم اليومي بين رسائل البث)
_last_volume = {}
_night_prices = {}  # الرمز ← آخر سعر في التداول الليلي (Blue Ocean ATS)
_hist = {}  # الرمز ← آخر 5 دقائق من الجلسة الرسمية: (الوقت، السعر، حجم اليوم) — لفلتر قبل الانطلاق
HIST_SECONDS = 300

NY = ZoneInfo("America/New_York")


def is_overnight(now=None):
    """التداول الليلي: 8 مساءً – 4 فجرًا بتوقيت نيويورك، من ليلة الأحد حتى فجر الجمعة."""
    t = now or datetime.now(NY)
    m = t.hour * 60 + t.minute
    return (m >= 20 * 60 and t.weekday() in (6, 0, 1, 2, 3)) or (m < 4 * 60 and t.weekday() in (0, 1, 2, 3, 4))
_wanted = set()
_state = {"ws": None, "started": False}
_lock = threading.Lock()


def status():
    with _lock:
        connected = _state["ws"] is not None and _state["ws"]._ws is not None
    last = max((p["at"] for p in list(_prices.values()) + list(_ext_prices.values()) + list(_night_prices.values())), default=0)
    return {"connected": connected, "watching": len(_wanted), "last_tick_age": time.time() - last if last else None}


def _on_message(msg):
    symbol, price = msg.get("id"), msg.get("price")
    if not symbol or price is None:
        return
    # شريط الصفقات: الكمية = الزيادة في حجم اليوم منذ الرسالة السابقة
    vol = int(msg.get("day_volume", 0) or 0)
    prev = _last_volume.get(symbol)
    _last_volume[symbol] = vol
    if prev is not None and vol > prev:
        _tape.setdefault(symbol, deque(maxlen=300)).append({
            "time": int(msg.get("time", 0) or 0) / 1000 or time.time(),
            "price": price,
            "size": vol - prev,
            "session": "regular" if msg.get("market_hours", 0) == REGULAR_MARKET else "ext",
        })
    if msg.get("market_hours", 0) == REGULAR_MARKET:
        now = time.time()
        h = _hist.setdefault(symbol, deque())
        h.append((now, price, vol or (h[-1][2] if h else 0)))
        # نُبقي آخر رسالة قبل بداية النافذة حتى نعرف الحجم والسعر عند بدايتها
        while len(h) > 2 and h[1][0] < now - HIST_SECONDS:
            h.popleft()
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


def history(symbol):
    """رسائل الجلسة الرسمية لآخر 5 دقائق: [(الوقت، السعر، حجم اليوم)] من الأقدم للأحدث."""
    h = _hist.get(symbol)
    for _ in range(3):
        try:
            return list(h) if h else []
        except RuntimeError:  # البث أضاف رسالة أثناء النسخ
            continue
    return []


def tape(symbol, n=150):
    """آخر الصفقات من الأحدث للأقدم."""
    return list(_tape.get(symbol, ()))[-n:][::-1]


def latest_night(symbol, max_age=600):
    p = _night_prices.get(symbol)
    return p if p and time.time() - p["at"] < max_age else None


def latest_ext(symbol, max_age=300):
    p = _ext_prices.get(symbol)
    return p if p and time.time() - p["at"] < max_age else None
