"""بث مباشر للأسعار من ياهو (WebSocket) — كل صفقة تصل لحظة حدوثها."""
import os
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
SHARD_SIZE = int(os.environ.get("STREAM_SHARD", "200"))  # ياهو لا يرسل إلا لجزء من الرموز إذا كثرت في اتصال واحد
_shards = []  # كل اتصال: {"symbols": set، "ws": اتصال}
_state = {"started": False}
_lock = threading.Lock()


def status():
    with _lock:
        connected = sum(1 for sh in _shards if sh["ws"] is not None and sh["ws"]._ws is not None)
        total = len(_shards)
    now = time.time()
    last = max((p["at"] for p in list(_prices.values()) + list(_ext_prices.values()) + list(_night_prices.values())), default=0)
    ticking = sum(1 for p in list(_prices.values()) + list(_ext_prices.values()) if now - p["at"] < 60)
    return {"connected": connected > 0, "connections": f"{connected}/{total}", "watching": len(_wanted),
            "ticking_1m": ticking, "last_tick_age": now - last if last else None}


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
        # هل الوقت أعلاه وقت الصفقة من ياهو فعلًا، أم وقت الاستلام بديلًا لأن الرسالة بلا وقت
        "trade_time_known": bool(msg.get("time")),
    }


def _subscribe(ws, symbols):
    for i in range(0, len(symbols), 50):  # رسائل صغيرة بدل رسالة واحدة ضخمة
        ws.subscribe(symbols[i:i + 50])


def _run(shard):
    while True:
        ws = None
        try:
            ws = yf.WebSocket(verbose=False)
            with _lock:
                shard["ws"] = ws
                symbols = list(shard["symbols"])
            if symbols:
                _subscribe(ws, symbols)
            ws.listen(_on_message)
        except Exception as e:
            print(f"⚠️ انقطع البث المباشر، إعادة الاتصال: {e}")
        finally:
            with _lock:
                shard["ws"] = None
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
            sockets = [sh["ws"] for sh in _shards]
        for ws in sockets:
            if ws is not None and ws._ws is not None:
                try:
                    ws.subscribe([])
                except Exception:
                    pass


def watch(symbols):
    """يضيف رموزًا للبث، موزعة على عدة اتصالات (كل اتصال SHARD_SIZE رمز)."""
    pending = []  # (اتصال، رموز جديدة) للاشتراك خارج القفل
    with _lock:
        new = [s for s in dict.fromkeys(symbols) if s not in _wanted]
        _wanted.update(new)
        for s in new:
            shard = next((sh for sh in _shards if len(sh["symbols"]) < SHARD_SIZE), None)
            if shard is None:
                shard = {"symbols": set(), "ws": None, "new": []}
                _shards.append(shard)
                threading.Thread(target=_run, args=(shard,), daemon=True).start()
            shard["symbols"].add(s)
            shard.setdefault("new", []).append(s)
        for sh in _shards:
            if sh.get("new"):
                pending.append((sh["ws"], sh["new"]))
                sh["new"] = []
        if not _state["started"]:
            _state["started"] = True
            threading.Thread(target=_heartbeat, daemon=True).start()
    for ws, syms in pending:
        if ws is not None and ws._ws is not None:
            try:
                _subscribe(ws, syms)
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
