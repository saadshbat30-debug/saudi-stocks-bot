"""بث مباشر للأسعار من ياهو (WebSocket) — كل صفقة تصل لحظة حدوثها."""
import threading
import time

import yfinance as yf

REGULAR_MARKET = 1  # market_hours في رسائل ياهو: 0 قبل الافتتاح، 1 الجلسة، 2 بعد الإغلاق

_prices = {}  # الرمز ← آخر سعر من البث (الجلسة الرسمية)
_ext_prices = {}  # الرمز ← آخر سعر قبل الافتتاح / بعد الإغلاق
_wanted = set()
_state = {"ws": None, "started": False}
_lock = threading.Lock()


def _on_message(msg):
    symbol, price = msg.get("id"), msg.get("price")
    if not symbol or price is None:
        return
    target = _prices if msg.get("market_hours", 0) == REGULAR_MARKET else _ext_prices
    target[symbol] = {
        "price": price,
        "change": msg.get("change"),
        "change_pct": msg.get("change_percent"),
        "volume": int(msg.get("day_volume", 0) or 0),
        "at": time.time(),
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


def latest(symbol, max_age=900):
    p = _prices.get(symbol)
    return p if p and time.time() - p["at"] < max_age else None


def latest_ext(symbol, max_age=900):
    p = _ext_prices.get(symbol)
    return p if p and time.time() - p["at"] < max_age else None
