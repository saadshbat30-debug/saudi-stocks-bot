"""مزوّد بيانات TradingView عبر مكتبة tvscraper (غير رسمية) للأسهم الأمريكية فقط.

اتصال WebSocket واحد مشترك في الخلفية (لا يُفتح اتصال لكل طلب من المتصفح):
- جلسة أسعار واحدة لكل الأسهم المتابعة (اختُبرت بـ 1644 سهمًا في جلسة واحدة).
- جلسة شموع لكل (رمز، فاصل، جلسة) تُفتح عند الطلب وتُغلق بعد 5 دقائق بلا استخدام.

ما نستخدمه من المكتبة كما هو في كودها المصدري: TradingViewClient (الاتصال، نبضات الإبقاء،
إعادة الاتصال بتأخير متزايد 1→60 ثانية، وإعادة بناء الجلسات بعد كل اتصال عبر on_connect)،
ودوال protocol (session_id وsymbol_spec). لم نستخدم QuoteStream وBarStream لأن الأولى تضيف
الرموز واحدًا واحدًا، والثانية لا تضيف رمزًا بعد التشغيل ولا تخبر متى اكتمل التاريخ.

قيود مقيسة (2026-11-05، دخول مجهول بلا حساب):
- الأسهم المدرجة: update_mode = streaming، والمصدر المعلن «Cboe One» (original_name = BATS:…).
- OTC: delayed_streaming_900 (متأخر 15 دقيقة).
- الشموع اليومية والأسبوعية والشهرية تطابق ياهو تمامًا (الإغلاق والحجم).
- شموع ما داخل اليوم: حجمها من Cboe One وحده (نحو 5–15% من حجم السوق الموحد)، وأسعارها قريبة لا مطابقة.
"""
import json
import logging
import os
import threading
import time
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

try:
    from tvscraper import TradingViewClient, protocol
    from tvscraper.client import ANONYMOUS_TOKEN
    IMPORT_ERROR = None
except Exception as e:  # المكتبة غير مثبتة: المنصة تعمل وتعرض السبب
    TradingViewClient, protocol, ANONYMOUS_TOKEN = object, None, None
    IMPORT_ERROR = f"{type(e).__name__}: {e}"

log = logging.getLogger(__name__)
NY = ZoneInfo("America/New_York")
SOURCE = "TradingView عبر tvscraper (غير رسمي)"

# فاصل المنصة ← (دقة TradingView، طول الشمعة بالثواني أو None للشهر)
RESOLUTIONS = {
    "1m": ("1", 60), "5m": ("5", 300), "15m": ("15", 900), "30m": ("30", 1800), "1h": ("60", 3600),
    "1d": ("1D", 86400), "1w": ("1W", 7 * 86400), "1mo": ("1M", None),
}
# عدد الشموع المطلوبة: نفس المدة التي كانت تُجلب من ياهو تقريبًا (الجلسة الممتدة شموعها أكثر)
HISTORY = {
    ("1m", False): 800, ("1m", True): 2000, ("5m", False): 400, ("5m", True): 1000,
    ("15m", False): 600, ("15m", True): 1400, ("30m", False): 600, ("30m", True): 1400,
    ("1h", False): 500, ("1h", True): 1100, ("1d", False): 520, ("1w", False): 530, ("1mo", False): 1000,
}
# رموز بورصات ياهو ← بادئة TradingView (آركا تظهر في TradingView باسم AMEX)
EXCHANGES = {"NMS": "NASDAQ", "NGM": "NASDAQ", "NCM": "NASDAQ", "NYQ": "NYSE", "ASE": "AMEX", "PCX": "AMEX", "BTS": "CBOE"}
FALLBACK = ["NASDAQ", "NYSE", "AMEX", "CBOE", "OTC"]
# المؤشرات: نسخ TVC تصل بلا تأخير (DJ:DJI وSP:SPX متأخرة 10 دقائق)
INDICES = {"^DJI": "TVC:DJI", "^GSPC": "TVC:SPX", "^RUT": "TVC:RUT", "^IXIC": "NASDAQ:IXIC"}
QUOTE_FIELDS = [
    "lp", "lp_time", "ch", "chp", "volume", "open_price", "high_price", "low_price", "prev_close_price",
    "update_mode", "current_session", "rtc", "rtc_time", "rch", "rchp", "listed_exchange", "pro_name",
    "description", "type", "price_52_week_high", "price_52_week_low", "market_cap_basic", "currency_code",
]
BATCH = 100
CHART_IDLE = 300       # تُغلق جلسة الشموع بعد 5 دقائق بلا طلب
MAX_CHARTS = 40        # حد الجلسات المفتوحة معًا (اختُبر 13 جلسة + 60 فتح/إغلاق بلا خطأ)
SILENCE_RECONNECT = 90  # لا رسائل ولا نبضات لهذه المدة = اتصال معلق، يُعاد فتحه
STALE_SECONDS = 120    # سهم في الجلسة الرسمية بلا تحديث لهذه المدة = «متأخر»
# المصدر يقطع الاتصال إذا فُتحت الجلسات بسرعة ("max sessions rate reached" — رُصد عند ~10 جلسات/ثانية):
# حد مشترك لفتح جلسات الشموع، والتحميل في الخلفية لا يأخذ إلا من الفائض حتى تبقى صفحات الشارت أولًا
SESSION_RATE = float(os.environ.get("TV_SESSION_RATE", "1"))   # جلسة في الثانية
SESSION_BURST = 4
RATE_PAUSE = 90  # بعد رسالة تجاوز الحد: إيقاف فتح الجلسات في الخلفية هذه المدة
DAILY_CACHE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "tv_daily.json")
# المؤشرات (TVC:DJI ...) لا حجم لها: المصدر يرسل رقمًا وهميًا ضخمًا (1e100) فيُعامل «غير متاح»
NO_VOLUME = 1e15


def clean_volume(v):
    return None if v is None or not isinstance(v, (int, float)) or v >= NO_VOLUME else v


def tv_candidates(symbol, hint=None):
    """صيغ الرمز المحتملة في TradingView بالترتيب. hint = (رمز بورصة ياهو، هل هو OTC)."""
    if symbol in INDICES:
        return [INDICES[symbol]]
    if symbol.startswith("^") or symbol.endswith(".SR") or "=" in symbol or "." in symbol:
        return []  # ليست أسهمًا أمريكية (السوق السعودي يبقى على مصدره)
    ticker = symbol.replace("-", ".")  # BRK-B في ياهو = BRK.B في TradingView
    first = None
    if hint:
        exchange, otc = hint
        first = "OTC" if otc else EXCHANGES.get(exchange)
    order = [first] + [e for e in FALLBACK if e != first] if first else FALLBACK
    return [f"{e}:{ticker}" for e in order]


def delay_seconds(update_mode):
    """delayed_streaming_900 ← 900. streaming ← 0. غير معروف ← None."""
    if not update_mode:
        return None
    if "delayed" in update_mode:
        tail = update_mode.rsplit("_", 1)[-1]
        return int(tail) if tail.isdigit() else -1
    return 0


def is_forming(t, interval, now):
    """هل الشمعة التي تبدأ عند t (UTC) ما زالت جارية الآن؟ بتوقيت نيويورك ليُراعى التوقيت الصيفي."""
    step = RESOLUTIONS[interval][1]
    if interval in ("1m", "5m", "15m", "30m", "1h"):
        return now < t + step
    start, cur = datetime.fromtimestamp(t, NY), datetime.fromtimestamp(now, NY)
    close = cur.replace(hour=16, minute=0, second=0, microsecond=0)
    if interval == "1d":
        return start.date() == cur.date() and cur < close
    if interval == "1w":
        week_over = cur.weekday() >= 5 or (cur.weekday() == 4 and cur >= close)
        return start.isocalendar()[:2] == cur.isocalendar()[:2] and not week_over
    return (start.year, start.month) == (cur.year, cur.month)


def normalize_time(t, interval):
    """الشموع اليومية فما فوق: TradingView يضع وقت افتتاح الجلسة (14:30 UTC)، وياهو منتصف ليل نيويورك.
    نوحّدها على منتصف ليل نيويورك (والشهرية على أول الشهر) حتى تعمل الواجهة والمؤشرات كما كانت."""
    if interval not in ("1d", "1w", "1mo"):
        return int(t)
    d = datetime.fromtimestamp(t, NY)
    d = d.replace(day=1) if interval == "1mo" else d
    return int(datetime(d.year, d.month, d.day, tzinfo=NY).timestamp())


def find_gaps(times, interval, extended):
    """فجوات الشموع داخل اليوم (للعلم فقط، لا تُملأ بشموع مصطنعة): الدقائق التي لا صفقات فيها لا تنتج شمعة."""
    step = RESOLUTIONS[interval][1]
    if interval not in ("1m", "5m", "15m", "30m") or len(times) < 2:
        return {"missing": 0, "largest_min": 0}
    lo, hi = (4 * 60, 20 * 60) if extended else (9 * 60 + 30, 16 * 60)
    missing, largest = 0, 0
    for a, b in zip(times, times[1:]):
        da, db = datetime.fromtimestamp(a, NY), datetime.fromtimestamp(b, NY)
        if da.date() != db.date():
            continue
        ma = da.hour * 60 + da.minute
        if not (lo <= ma < hi):
            continue
        n = int((b - a) // step) - 1
        if n > 0:
            missing += n
            largest = max(largest, (b - a - step) // 60)
    return {"missing": missing, "largest_min": int(largest)}


class _Limiter:
    """دلو رموز: SESSION_RATE جلسة/ثانية بحد أقصى SESSION_BURST متتالية."""

    def __init__(self, rate, burst):
        self.rate, self.burst = rate, burst
        self.tokens = burst
        self.at = time.time()
        self.paused_until = 0.0
        self.lock = threading.Lock()

    def acquire(self, background=False):
        reserve = 1 if background else 0  # الخلفية تترك رمزًا واحدًا على الأقل للطلبات التفاعلية
        while True:
            with self.lock:
                now = time.time()
                self.tokens = min(self.burst, self.tokens + (now - self.at) * self.rate)
                self.at = now
                if not (background and now < self.paused_until) and self.tokens >= 1 + reserve:
                    self.tokens -= 1
                    return
            time.sleep(0.05)


class _Client(TradingViewClient):
    """نفس اتصال المكتبة مع تسجيل وقت آخر رسالة وآخر خطأ لكشف الانقطاع والاتصال المعلق."""

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self.last_rx = 0.0
        self.connects = 0
        self.connected_at = None
        self.last_error = None
        self.disconnected_at = None
        self.critical = []  # رسائل critical_error/protocol_error من المصدر (المكتبة تسجلها فقط)

    def _connect(self):
        try:
            super()._connect()
        except Exception as e:
            self.last_error = f"{type(e).__name__}: {str(e)[:160]}"
            self.disconnected_at = self.disconnected_at or time.time()
            raise
        self.connects += 1
        self.connected_at = time.time()
        self.last_rx = time.time()
        self.disconnected_at = None

    def _read_loop(self):
        try:
            super()._read_loop()
        except Exception as e:
            self.last_error = f"{type(e).__name__}: {str(e)[:160]}"
            self.disconnected_at = time.time()
            raise

    def _dispatch(self, payload):
        self.last_rx = time.time()
        if '"critical_error"' in payload or '"protocol_error"' in payload:
            self.critical.append((time.time(), payload[:200]))
            del self.critical[:-20]
            if self.on_critical:
                self.on_critical(payload)
        super()._dispatch(payload)

    on_critical = None

    def force_reconnect(self):
        """يغلق المقبس الحالي؛ حلقة المكتبة تعيد الاتصال بتأخيرها المتزايد وتعيد بناء الجلسات."""
        ws = self._ws
        if ws is not None:
            try:
                ws.close()
            except Exception:
                pass


class _Series:
    def __init__(self, key, count, temp=False):
        self.key = key  # (رمز TradingView، فاصل المنصة، جلسة ممتدة؟)
        self.count = count
        self.temp = temp
        self.bars = {}  # وقت افتتاح الشمعة (UTC) ← (فتح، أعلى، أدنى، إغلاق، حجم)
        self.done = threading.Event()
        self.error = None
        self.cs = None
        self.accessed = time.time()
        self.last_rx = 0.0
        self.corrections = []  # شموع مغلقة أعاد المصدر إرسالها بقيم مختلفة
        self.resyncs = 0
        self.duplicates = 0  # نقاط مكررة بنفس القيم (تُتجاهل)
        self.error_at = 0.0
        self.gen = -1


class Provider:
    def __init__(self, token=None, on_quote=None):
        self.token = token or ANONYMOUS_TOKEN
        self.on_quote = on_quote  # (رمز المنصة، القيم، المفاتيح المتغيرة، وقت الاستلام، أول لقطة؟)
        self.lock = threading.RLock()
        self.client = None
        self.qs = None
        self.hints = {}     # رمز المنصة ← (بورصة ياهو، OTC؟)
        self.syms = {}      # رمز المنصة ← {"cands", "i", "status": pending|ok|unavailable, "tv"}
        self.by_tv = {}     # صيغة TradingView المرسلة ← رمز المنصة
        self.quotes = {}    # رمز المنصة ← {"v": القيم المدمجة، "rx": وقت آخر رسالة، "trade_rx": وقت آخر تغير للسعر/الحجم}
        self.series = {}    # المفتاح ← _Series
        self.by_cs = {}     # جلسة الشموع ← المفتاح
        self.latency = []   # (وقت الاستلام − وقت الصفقة) لآخر 500 صفقة في الجلسة الرسمية
        self._temp = {}     # جلسات التاريخ المؤقتة
        self.limiter = _Limiter(SESSION_RATE, SESSION_BURST)
        self.gen = 0  # رقم الاتصال الحالي: جلسات الاتصال السابق لا وجود لها عند الخادم
        self.rate_errors = 0
        self.daily_cache = {}  # الرمز ← {"at": وقت الجلب، "bars": [...]}
        self.daily_lock = threading.Lock()
        self._load_daily_cache()
        self.started = False

    # ===== التشغيل =====
    def start(self):
        if IMPORT_ERROR or self.started:
            return not IMPORT_ERROR
        self.started = True
        c = self.client = _Client(auth_token=self.token)
        c.on_critical = self._on_critical
        c.on("qsd", self._on_qsd)
        for m in ("timescale_update", "du"):
            c.on(m, self._on_bars)
        c.on("series_completed", self._on_completed)
        c.on("series_error", self._on_series_error)
        c.on("symbol_error", self._on_series_error)
        c.on_connect(self._setup)
        c.start()
        threading.Thread(target=self._janitor, name="tv-janitor", daemon=True).start()
        return True

    def _on_critical(self, payload):
        if "rate" in payload:
            self.rate_errors += 1
            self.limiter.paused_until = time.time() + RATE_PAUSE
            log.warning("TradingView: تجاوز حد فتح الجلسات، إيقاف التحميل في الخلفية %ds", RATE_PAUSE)

    def connected(self):
        return bool(self.client and self.client._connected.is_set())

    def _send(self, func, params):
        try:
            self.client.send(func, params)
            return True
        except Exception:
            return False  # غير متصل: تُرسل الجلسات من جديد عند الاتصال (_setup)

    def _setup(self):
        """بعد كل اتصال (الأول وكل إعادة اتصال): جلسة أسعار جديدة بكل الرموز، وكل جلسات الشموع المفتوحة."""
        with self.lock:
            self.gen += 1
            self.qs = protocol.session_id("qs")
            tvs = [s["cands"][s["i"]] for s in self.syms.values() if s["status"] != "unavailable"]
            series = list(self.series.values())
            self.by_cs.clear()
        self.client.send("quote_create_session", [self.qs])
        self.client.send("quote_set_fields", [self.qs, *QUOTE_FIELDS])
        for i in range(0, len(tvs), BATCH):
            self.client.send("quote_add_symbols", [self.qs, *tvs[i:i + BATCH]])
        reopen = []
        for s in series:
            if s.temp:  # طلب تاريخ مؤقت انقطع: يُعاد من الطالب
                s.error = "انقطع الاتصال أثناء جلب التاريخ"
                s.error_at = time.time()
                s.done.set()
                continue
            s.resyncs += self.client.connects > 0
            s.done.clear()
            reopen.append(s)
        if reopen:  # إعادة بناء جلسات الشموع بعد عودة الاتصال (بالحد المشترك، خارج خيط القراءة)
            threading.Thread(target=lambda: [self._open_series(x, background=True) for x in reopen], daemon=True).start()

    def _janitor(self):
        while True:
            time.sleep(15)
            c = self.client
            if c and c._connected.is_set() and time.time() - c.last_rx > SILENCE_RECONNECT:
                log.warning("TradingView: لا رسائل منذ %ds، إعادة الاتصال", SILENCE_RECONNECT)
                c.force_reconnect()
            now = time.time()
            with self.lock:
                idle = [s for s in self.series.values() if not s.temp and now - s.accessed > CHART_IDLE]
            for s in idle:
                self._close_series(s)

    # ===== الرموز والأسعار =====
    def hint(self, mapping):
        with self.lock:
            self.hints.update(mapping)

    def watch(self, symbols):
        new = []
        with self.lock:
            for s in dict.fromkeys(symbols):
                if s in self.syms:
                    continue
                cands = tv_candidates(s, self.hints.get(s))
                if not cands:
                    continue
                self.syms[s] = {"cands": cands, "i": 0, "status": "pending", "tv": None}
                self.by_tv[cands[0]] = s
                new.append(cands[0])
            qs = self.qs
        if new and qs and self.connected():
            for i in range(0, len(new), BATCH):
                self._send("quote_add_symbols", [qs, *new[i:i + BATCH]])

    def _on_qsd(self, params):
        if len(params) < 2 or not isinstance(params[1], dict):
            return
        p = params[1]
        name, status = p.get("n"), p.get("s")
        rx = time.time()
        with self.lock:
            sym = self.by_tv.get(name)
            st = self.syms.get(sym)
            if st is None or st["cands"][st["i"]] != name:
                return
            if status == "error":
                # صيغة البورصة خاطئة أو الرمز غير موجود في TradingView: جرّب البورصة التالية
                st["i"] += 1
                nxt = st["cands"][st["i"]] if st["i"] < len(st["cands"]) else None
                if nxt is None:
                    st["status"], st["i"] = "unavailable", len(st["cands"]) - 1
                    return
                self.by_tv[nxt] = sym
                qs = self.qs
            elif status == "ok":
                st["status"] = "ok"
                st["tv"] = name
                update = dict(p.get("v") or {})
                if "volume" in update:
                    update["volume"] = clean_volume(update["volume"])
                q = self.quotes.setdefault(sym, {"v": {}, "rx": 0.0, "trade_rx": 0.0})
                first = not q["v"]
                changed = {k for k, v in update.items() if q["v"].get(k) != v}
                q["v"].update(update)
                q["rx"] = rx
                v = dict(q["v"])
                if not first and changed & {"lp", "volume", "lp_time", "rtc", "rtc_time"}:
                    q["trade_rx"] = rx  # أول لقطة ليست صفقة
                if "lp_time" in changed and v.get("current_session") == "market" and v.get("lp_time") and not delay_seconds(v.get("update_mode")):
                    self.latency.append(rx - v["lp_time"])
                    del self.latency[:-500]
                qs = None
            else:
                return
        if status == "error":
            if qs:
                self._send("quote_add_symbols", [qs, nxt])
            return
        if self.on_quote and changed:
            try:
                self.on_quote(sym, v, changed, rx, first)
            except Exception:
                log.exception("on_quote")

    def quote(self, symbol):
        with self.lock:
            q = self.quotes.get(symbol)
            return {"v": dict(q["v"]), "rx": q["rx"], "trade_rx": q["trade_rx"]} if q else None

    def resolved(self, symbol, wait=0.0):
        """صيغة الرمز المقبولة في TradingView (pro_name)، أو None إن لم يُعرف بعد/غير متاح."""
        self.watch([symbol])
        end = time.time() + wait
        while True:
            with self.lock:
                st = self.syms.get(symbol)
                if not st or st["status"] == "unavailable":
                    return None
                if st["status"] == "ok":
                    q = self.quotes.get(symbol)
                    return (q and q["v"].get("pro_name")) or st["tv"]
            if time.time() >= end:
                return None
            time.sleep(0.1)

    def symbol_status(self, symbol):
        with self.lock:
            st = self.syms.get(symbol)
            return st["status"] if st else None

    def meta(self, symbol, now=None):
        """شفافية لكل سهم: المصدر، وقت آخر تحديث، العمر، حالة الاتصال، التأخير المعلن."""
        now = now or time.time()
        q = self.quote(symbol)
        st = self.symbol_status(symbol)
        if not q:
            return {"src": SOURCE, "state": "unavailable" if st == "unavailable" else "disconnected" if not self.connected() else "pending"}
        v = q["v"]
        delay = delay_seconds(v.get("update_mode"))
        state = "connected"
        if not self.connected():
            state = "disconnected"
        elif delay:
            state = "delayed"
        elif v.get("current_session") == "market" and now - q["trade_rx"] > STALE_SECONDS:
            state = "delayed"
        lp_time = v.get("lp_time")
        return {
            "src": SOURCE, "tv_symbol": v.get("pro_name"), "state": state, "update_mode": v.get("update_mode"),
            "delay_s": delay, "session": v.get("current_session"),
            "updated_at": int(q["rx"]), "age": round(now - q["rx"]),
            "trade_time": int(lp_time) if lp_time else None, "trade_age": round(now - lp_time) if lp_time else None,
            "feed": "Cboe One" if v.get("type") in ("stock", "fund", "dr") and v.get("listed_exchange") != "OTC" and not delay else None,
        }

    # ===== الشموع =====
    def _open_series(self, s, background=False):
        tv, interval, ext = s.key
        self.limiter.acquire(background=background)
        cs = protocol.session_id("cs")
        with self.lock:
            if s.cs:
                self.by_cs.pop(s.cs, None)
            s.cs = cs
            s.gen = self.gen
            s.done.clear()
            s.error = None
            self.by_cs[cs] = s.key if not s.temp else (s.key, id(s))
            if s.temp:
                self._temp[(s.key, id(s))] = s
        self._send("chart_create_session", [cs, ""])
        self._send("resolve_symbol", [cs, "sds_sym_1", protocol.symbol_spec(tv, session="extended" if ext else "regular")])
        self._send("create_series", [cs, "sds_1", "s1", "sds_sym_1", RESOLUTIONS[interval][0], s.count, ""])

    def _close_series(self, s):
        with self.lock:
            if s.temp:
                self._temp.pop((s.key, id(s)), None)
            elif self.series.get(s.key) is s:
                del self.series[s.key]
            cs = s.cs
            self.by_cs.pop(cs, None)
            current = s.gen == self.gen
        # حذف جلسة من اتصال سابق يرد عليه الخادم بخطأ unknown_session_id، فلا يُرسل
        if cs and current:
            self._send("chart_delete_session", [cs])

    def _series_for(self, cs):
        k = self.by_cs.get(cs)
        if k is None:
            return None
        return self._temp.get(k) if isinstance(k, tuple) and len(k) == 2 and isinstance(k[1], int) else self.series.get(k)

    def _on_bars(self, params):
        if len(params) < 2 or not isinstance(params[1], dict):
            return
        with self.lock:
            s = self._series_for(params[0])
            if s is None:
                return
            now = time.time()
            for block in params[1].values():
                if not isinstance(block, dict):
                    continue
                for point in block.get("s") or ():
                    v = point.get("v") or []
                    if len(v) < 5:
                        continue
                    t = int(v[0])
                    vals = (v[1], v[2], v[3], v[4], clean_volume(v[5]) if len(v) > 5 else None)
                    old = s.bars.get(t)
                    if old == vals:
                        s.duplicates += 1  # نفس الشمعة أُعيد إرسالها (مثل إعادة التاريخ بعد الاتصال)
                        continue
                    latest = max(s.bars) if s.bars else None
                    if old is not None and latest is not None and t < latest:
                        # شمعة مغلقة تغيرت: لا نغيّرها إلا لأن المصدر نفسه أرسل قيمة مختلفة، ونسجل ذلك
                        s.corrections.append({"time": t, "old": old, "new": vals, "at": int(now)})
                        del s.corrections[:-50]
                    s.bars[t] = vals  # الشمعة الجارية تُحدَّث في مكانها (نفس وقت الافتتاح)
                    s.last_rx = now
            limit = int(s.count * 1.5) + 10
            if len(s.bars) > limit:
                for t in sorted(s.bars)[:len(s.bars) - limit]:
                    del s.bars[t]

    def _on_completed(self, params):
        if params:
            with self.lock:
                s = self._series_for(params[0])
            if s:
                s.done.set()

    def _on_series_error(self, params):
        if params:
            with self.lock:
                s = self._series_for(params[0])
            if s:
                s.error = str(params[1:])[:200] if len(params) > 1 else "series_error"
                s.error_at = time.time()
                s.done.set()

    def bars(self, symbol, interval, extended=False, wait=10.0):
        """(قائمة الشموع، معلومات المصدر). الشمعة: time بالثواني UTC (اليومية: منتصف ليل نيويورك) وclosed."""
        if interval not in RESOLUTIONS:
            return [], {"error": "فاصل غير مدعوم"}
        extended = bool(extended) and interval in ("1m", "5m", "15m", "30m", "1h")
        tv = self.resolved(symbol, wait=min(wait, 8.0))
        if not tv:
            st = self.symbol_status(symbol)
            return [], {"src": SOURCE, "error": "الرمز غير متاح في TradingView" if st == "unavailable" else
                        "لم يتصل المصدر بعد" if not self.connected() else "جارٍ التعرف على الرمز"}
        key = (tv, interval, extended)
        with self.lock:
            s = self.series.get(key)
            fresh = s is None
            if fresh:
                if len(self.series) >= MAX_CHARTS:
                    victim = min(self.series.values(), key=lambda x: x.accessed)
                else:
                    victim = None
                s = self.series[key] = _Series(key, HISTORY.get((interval, extended), 500))
            s.accessed = time.time()
            # رفض المصدر الجلسة: إعادة المحاولة بعد 30 ثانية على الأقل (لا طلبات متلاحقة)
            retry = not fresh and s.error and time.time() - s.error_at > 30
        if retry and self.connected():
            self._open_series(s)
        if fresh:
            if victim:
                self._close_series(victim)
            if self.connected():
                self._open_series(s)
        s.done.wait(wait)
        return self._export(s, interval, extended)

    def _export(self, s, interval, extended, now=None):
        now = now or time.time()
        with self.lock:
            items = sorted(s.bars.items())
            meta = {"src": SOURCE, "tv_symbol": s.key[0], "complete": s.done.is_set(), "error": s.error,
                    "corrections": len(s.corrections), "resyncs": s.resyncs, "duplicates_ignored": s.duplicates,
                    "last_update_age": round(now - s.last_rx) if s.last_rx else None, "connected": self.connected()}
        out = []
        for i, (t, (o, h, l, c, v)) in enumerate(items):
            last = i == len(items) - 1
            out.append({"time": normalize_time(t, interval), "open": o, "high": h, "low": l, "close": c,
                        "volume": int(v) if v is not None else 0, "closed": not (last and is_forming(t, interval, now))})
        meta["gaps"] = find_gaps([t for t, _ in items], interval, extended)
        meta["forming"] = bool(out) and not out[-1]["closed"]
        # حجم شموع ما داخل اليوم من Cboe One وحده في الدخول المجهول (قِيس مقابل ياهو)
        if s.key[0].startswith("TVC:"):
            meta["volume_scope"] = "المؤشرات بلا حجم في المصدر"
        elif interval in ("1m", "5m", "15m", "30m", "1h") and not s.key[0].startswith("OTC:"):
            meta["volume_scope"] = "Cboe One فقط"
        return out, meta

    def _load_daily_cache(self):
        try:
            with open(DAILY_CACHE, encoding="utf-8") as f:
                self.daily_cache = json.load(f)
        except (OSError, ValueError):
            self.daily_cache = {}

    def _save_daily_cache(self):
        try:
            os.makedirs(os.path.dirname(DAILY_CACHE), exist_ok=True)
            with self.daily_lock:
                data = json.dumps(self.daily_cache, separators=(",", ":"))
            tmp = DAILY_CACHE + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                f.write(data)
            os.replace(tmp, DAILY_CACHE)
        except OSError as e:
            log.warning("TradingView: تعذر حفظ الشموع اليومية: %s", e)

    @staticmethod
    def last_close_cutoff(now=None):
        """آخر إغلاق رسمي مكتمل: 16:15 نيويورك لآخر يوم عمل ≤ الآن (بعده تكتمل شمعة اليوم بحجمها النهائي)."""
        d = datetime.fromtimestamp(now or time.time(), NY)
        cut = d.replace(hour=16, minute=15, second=0, microsecond=0)
        if d < cut:
            cut -= timedelta(days=1)
        while cut.weekday() >= 5:
            cut -= timedelta(days=1)
        return cut.timestamp()

    def daily_cached(self, symbols, count, background=True):
        """مثل daily لكن من الملف إن جُلب بعد آخر إغلاق رسمي (الشموع اليومية السابقة لا تتغير خلال اليوم)."""
        cutoff = self.last_close_cutoff()
        out, need = {}, []
        with self.daily_lock:
            for s in symbols:
                c = self.daily_cache.get(s)
                if c and c["at"] >= cutoff and c.get("count", 0) >= count:
                    out[s] = [tuple(b) for b in c["bars"][-count:]]
                else:
                    need.append(s)
        if need:
            got = self.daily(need, count, background=background)
            now = time.time()
            with self.daily_lock:
                for s, bars in got.items():
                    self.daily_cache[s] = {"at": now, "count": count, "bars": [list(b) for b in bars]}
            out.update(got)
            if got:
                self._save_daily_cache()
        return out

    def daily(self, symbols, count, concurrency=2, wait=12.0, background=True):
        """شموع يومية لعدة رموز بجلسات مؤقتة (تُغلق فور اكتمال التاريخ). الرمز ← [(t, o, h, l, c, v)]."""
        out = {}
        pending = list(dict.fromkeys(symbols))
        self.watch(pending)
        sem = threading.Semaphore(concurrency)
        lock = threading.Lock()

        def one(sym):
            try:
                tv = self.resolved(sym, wait=6.0)
                if not tv or not self.connected():
                    return
                s = _Series((tv, "1d", False), count, temp=True)
                self._open_series(s, background=background)
                s.done.wait(wait)
                with self.lock:
                    items = sorted(s.bars.items())
                self._close_series(s)
                if s.done.is_set() and not s.error and items:
                    with lock:
                        out[sym] = [(normalize_time(t, "1d"), *vals) for t, vals in items]
            finally:
                sem.release()

        threads = []
        for sym in pending:
            sem.acquire()
            th = threading.Thread(target=one, args=(sym,), daemon=True)
            th.start()
            threads.append(th)
        for th in threads:
            th.join(wait + 10)
        return out

    # ===== الحالة =====
    def status(self):
        c = self.client
        now = time.time()
        with self.lock:
            counts = {"ok": 0, "pending": 0, "unavailable": 0}
            for st in self.syms.values():
                counts[st["status"]] += 1
            delayed = sum(1 for q in self.quotes.values() if delay_seconds(q["v"].get("update_mode")))
            unavailable = [s for s, st in self.syms.items() if st["status"] == "unavailable"][:30]
            lat = sorted(self.latency)
            corrections = sum(len(s.corrections) for s in self.series.values())
            charts = len(self.series)
            ticking = sum(1 for q in self.quotes.values() if now - q["trade_rx"] < 60)
            last_trade = max((q["trade_rx"] for q in self.quotes.values()), default=0)
        connected = self.connected()
        silent = c and connected and now - c.last_rx > 30
        return {
            "source": SOURCE, "installed": IMPORT_ERROR is None, "import_error": IMPORT_ERROR,
            "state": "disconnected" if not connected else "delayed" if silent else "connected",
            "connected": connected, "connects": c.connects if c else 0,
            "reconnects": max(0, c.connects - 1) if c else 0,
            "connected_since": int(c.connected_at) if c and c.connected_at else None,
            "disconnected_for": round(now - c.disconnected_at) if c and c.disconnected_at else None,
            "last_message_age": round(now - c.last_rx, 1) if c and c.last_rx else None,
            "last_error": c.last_error if c else None,
            "watching": len(self.syms), "symbols": counts, "delayed_symbols": delayed, "unavailable_sample": unavailable,
            "ticking_1m": ticking, "last_tick_age": round(now - last_trade, 1) if last_trade else None,
            "latency_median_s": round(lat[len(lat) // 2], 2) if lat else None,
            "latency_p90_s": round(lat[len(lat) * 9 // 10], 2) if lat else None,
            "latency_samples": len(lat),
            "charts_open": charts, "bar_corrections": corrections,
            "rate_limit_errors": self.rate_errors, "session_rate_per_s": SESSION_RATE,
            "critical_errors": [x[1] for x in (c.critical[-3:] if c else [])],
            "daily_cached": len(self.daily_cache),
        }


_provider = {"p": None}
_plock = threading.Lock()


def get(on_quote=None):
    """المزود المشترك الوحيد في العملية (يُنشأ ويتصل عند أول استخدام)."""
    with _plock:
        if _provider["p"] is None:
            import os
            p = Provider(token=os.environ.get("TV_AUTH_TOKEN") or None, on_quote=on_quote)
            p.start()
            _provider["p"] = p
        elif on_quote and not _provider["p"].on_quote:
            _provider["p"].on_quote = on_quote
        return _provider["p"]
