"""تاريخ يومي من Alpaca (SIP) لحساب السيولة والزخم في الجدول حين DATA_SOURCE=alpaca:
متوسط حجم 3 أشهر (للسيولة النسبية)، وRSI 14 ومتوسط 20 يومًا (من الإغلاقات).

GET https://data.alpaca.markets/v2/stocks/bars?symbols=…&timeframe=1Day&feed=sip — حتى 100 رمز في الطلب.
نحو 6000 سهم × 70 يومًا: يُجلب مرة في اليوم في الخلفية (الأنشط أولًا) ويُحفظ في data/alpaca_daily.json.gz،
ويُقرأ من الملف عند إعادة التشغيل في اليوم نفسه. شمعة اليوم لا تُخزن هنا: سعر اليوم وحجمه من البث.
"""
import gzip
import json
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import alpaca_stream as al

URL = "https://data.alpaca.markets/v2/stocks/bars"
NY = ZoneInfo("America/New_York")
CACHE = Path(__file__).resolve().parent / "data" / "alpaca_daily.json.gz"
BATCH = 100
AVG_DAYS = 63  # 3 أشهر تداول، مثل averageDailyVolume3Month في ياهو
KEEP = 70

_hist = {}  # الرمز (بصيغة المنصة) ← {"closes": [...], "vols": [...]} للأيام المكتملة، الأقدم أولًا
_state = {"day": None, "running": False, "done": set(), "failed": 0, "error": None, "at": None, "loaded_from_disk": False,
          "next_try": 0.0}  # بعد فشل: لا إعادة قبل هذا الوقت (رفض المفتاح 10 دقائق، غيره دقيقة)
_lock = threading.Lock()


def _today():
    return datetime.now(NY).date().isoformat()


def _load_disk():
    if _state["loaded_from_disk"]:
        return
    _state["loaded_from_disk"] = True
    try:
        with gzip.open(CACHE, "rt", encoding="utf-8") as f:
            d = json.load(f)
        if d.get("day") == _today():
            _hist.update(d["hist"])
            _state["done"] = set(d["hist"])
            _state["day"] = d["day"]
    except (OSError, ValueError, KeyError):
        pass


def _save_disk():
    try:
        CACHE.parent.mkdir(parents=True, exist_ok=True)
        tmp = CACHE.with_suffix(".tmp")
        with gzip.open(tmp, "wt", encoding="utf-8") as f:
            json.dump({"day": _state["day"], "hist": _hist}, f)
        tmp.replace(CACHE)
    except OSError as e:
        al.log.warning("تعذر حفظ تاريخ Alpaca اليومي: %s", e)


def _fetch(symbols, cfg, http):
    """شموع يومية مكتملة (قبل اليوم) لدفعة رموز. يرجع {رمز المنصة: {"closes","vols"}}."""
    start = (datetime.now(timezone.utc) - timedelta(days=105)).strftime("%Y-%m-%dT00:00:00Z")
    to_platform = {s.replace("-", "."): s for s in symbols}
    params = {"symbols": ",".join(to_platform), "timeframe": "1Day", "start": start, "limit": 10000,
              "feed": cfg["feed"], "adjustment": "split", "sort": "asc"}
    headers = {"APCA-API-KEY-ID": cfg["key"], "APCA-API-SECRET-KEY": cfg["secret"]}
    raw, today = {}, _today()
    while True:
        r = http.get(URL, params=params, headers=headers, timeout=30)
        if r.status_code != 200:
            raise RuntimeError(f"Alpaca HTTP {r.status_code}: {' '.join(r.text.split())[:120]}")
        d = r.json()
        for sym, rows in (d.get("bars") or {}).items():
            raw.setdefault(sym, []).extend(rows)
        if not d.get("next_page_token"):
            break
        params["page_token"] = d["next_page_token"]
    out = {}
    for sym, rows in raw.items():
        rows = [b for b in rows if datetime.fromtimestamp(_ts(b["t"]), NY).date().isoformat() < today][-KEEP:]
        out[to_platform.get(sym, sym)] = {"closes": [float(b["c"]) for b in rows], "vols": [int(b.get("v") or 0) for b in rows]}
    for s in symbols:  # رمز بلا شموع: يُسجَّل فارغًا حتى لا يُطلب مرة أخرى اليوم
        out.setdefault(s, {"closes": [], "vols": []})
    return out


def _ts(s):
    return datetime.strptime(s[:19], "%Y-%m-%dT%H:%M:%S").replace(tzinfo=timezone.utc).timestamp()


def _run(symbols):
    import requests
    http = requests.Session()
    try:
        cfg = al.config()
        for i in range(0, len(symbols), BATCH):
            batch = symbols[i:i + BATCH]
            try:
                got = _fetch(batch, cfg, http)
            except Exception as e:
                _state["failed"] += len(batch)
                _state["error"] = str(e)[:160]
                al.log.warning("تاريخ Alpaca اليومي: %s", _state["error"])
                if "401" in _state["error"] or "403" in _state["error"]:
                    _state["next_try"] = time.time() + 600
                    return  # المفتاح أو الاشتراك: لا فائدة من بقية الدفعات
                _state["next_try"] = time.time() + 60
                continue
            with _lock:
                _hist.update(got)
                _state["done"].update(got)
            if i // BATCH % 10 == 9:
                _save_disk()
        _state["at"] = time.time()
        _save_disk()
    finally:
        _state["running"] = False


def ensure(symbols):
    """يبدأ في الخلفية جلب ما ينقص من الرموز (بالترتيب المعطى: الأنشط أولًا). يوم جديد ← من جديد."""
    _load_disk()
    day = _today()
    with _lock:
        if _state["day"] != day:
            _hist.clear()
            _state.update(day=day, done=set(), failed=0, error=None, next_try=0.0)
        if _state["running"] or time.time() < _state["next_try"]:
            return
        todo = [s for s in dict.fromkeys(symbols) if s not in _state["done"] and "." not in s and "^" not in s]
        if not todo:
            return
        _state["running"] = True
    threading.Thread(target=_run, args=(todo,), name="alpaca-daily", daemon=True).start()


def get(symbol):
    return _hist.get(symbol)


def avg_volume(symbol):
    """متوسط الحجم اليومي لآخر 63 يوم تداول (3 أشهر)، أو None إن كان التاريخ أقصر من 20 يومًا."""
    h = _hist.get(symbol)
    if not h or len(h["vols"]) < 20:
        return None
    v = h["vols"][-AVG_DAYS:]
    return sum(v) / len(v)


def status():
    return {"day": _state["day"], "symbols": len(_state["done"]), "with_history": sum(1 for h in _hist.values() if h["vols"]),
            "running": _state["running"], "failed": _state["failed"], "error": _state["error"],
            "finished_at": _state["at"]}
