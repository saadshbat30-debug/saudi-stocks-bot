"""بث Alpaca اللحظي (SIP) للأسهم الأمريكية: الصفقات والأسعار (أفضل عرض/طلب) وشموع الدقيقة عبر WebSocket.

طبقة مستقلة: لا تغيّر أي وظيفة في المنصة. تعمل فقط إذا ALPACA_STREAM=1، وتُقرأ بياناتها من latest_trade/latest_quote/
minute_bars وحالتها من status() (المسار /api/alpaca/status).

الأمان:
- المفاتيح من متغيرات البيئة أو ملف .env بجانب هذا الملف (لا تُكتب في الكود، و.env في .gitignore).
- لا يوجد في هذه الوحدة أي كود لإرسال أوامر. حساب التداول المسموح هو Paper فقط (paper-api.alpaca.markets)،
  وأي عنوان تداول آخر يوقف التشغيل.
- المصدر SIP وحده: أي قيمة أخرى لـ ALPACA_FEED توقف التشغيل، ولا رجوع تلقائي إلى IEX عند رفض الاشتراك.
- كل سجل يمر بمرشح يحذف المفتاح والسر (RedactFilter).

بروتوكول Alpaca (docs.alpaca.markets/docs/streaming-market-data و real-time-stock-pricing-data، قُرئت 2026-11-18):
wss://stream.data.alpaca.markets/v2/sip · {"action":"auth","key","secret"} خلال 10 ثوانٍ · {"action":"subscribe",
"trades":["*"],"quotes":["*"],"bars":["*"]} · الرسائل مصفوفات JSON، T: t صفقة، q سعر، b شمعة دقيقة، u شمعة محدثة،
c تصحيح، x إلغاء، error/success/subscription. اتصال واحد مسموح غالبًا (406).
"""
import json
import logging
import os
import random
import re
import threading
import time
from collections import deque
from datetime import datetime, timezone
from logging.handlers import RotatingFileHandler
from pathlib import Path
from urllib.parse import urlparse

HERE = Path(__file__).resolve().parent

PAPER_TRADING_URL = "https://paper-api.alpaca.markets"
STREAM_BASE = "wss://stream.data.alpaca.markets/v2/"
ALLOWED_FEED = "sip"

# أخطاء لا فائدة من إعادة المحاولة معها: المفتاح أو الاشتراك
FATAL = {402: "المفتاح أو السر غير صحيح (auth failed)",
         409: "الاشتراك لا يسمح بمصدر SIP (insufficient subscription)",
         410: "قناة غير متاحة في هذا البث"}
WAIT = {406: 60}  # اتصال آخر يستعمل الحصة (منصة أخرى أو برنامج فحص): انتظار أطول


def load_env():
    """يقرأ .env بجانب المنصة دون أن يطغى على متغيرات مضبوطة في النظام."""
    try:
        from dotenv import load_dotenv
        load_dotenv(HERE / ".env", override=False)
    except ImportError:
        pass


class ConfigError(Exception):
    pass


def config():
    """الإعدادات بعد التحقق. يرفع ConfigError برسالة عربية لا تحتوي المفاتيح."""
    load_env()
    key = os.environ.get("APCA_API_KEY_ID", "").strip()
    secret = os.environ.get("APCA_API_SECRET_KEY", "").strip()
    feed = os.environ.get("ALPACA_FEED", ALLOWED_FEED).strip().lower()
    trading_url = os.environ.get("ALPACA_TRADING_URL", PAPER_TRADING_URL).strip().rstrip("/")
    if not key or not secret:
        raise ConfigError("مفاتيح Alpaca غير مضبوطة: أضف APCA_API_KEY_ID وAPCA_API_SECRET_KEY في ملف .env")
    if feed != ALLOWED_FEED:
        raise ConfigError(f"ALPACA_FEED={feed} مرفوض: هذه المرحلة تستعمل SIP فقط (لا IEX ولا delayed_sip)")
    if urlparse(trading_url).hostname != urlparse(PAPER_TRADING_URL).hostname:
        raise ConfigError("ALPACA_TRADING_URL مرفوض: المسموح حساب Paper فقط (https://paper-api.alpaca.markets)")

    def symbols(name):
        raw = os.environ.get(name, "*").strip()
        if raw.lower() in ("", "none", "off", "0"):
            return []
        return ["*"] if raw == "*" else [s.strip().upper() for s in raw.split(",") if s.strip()]
    return {
        "key": key, "secret": secret, "feed": feed, "url": STREAM_BASE + feed, "trading_url": trading_url,
        "trades": symbols("ALPACA_TRADES"), "quotes": symbols("ALPACA_QUOTES"), "bars": symbols("ALPACA_BARS"),
        "stale_seconds": float(os.environ.get("ALPACA_STALE_SECONDS", "30")),
        "bars_keep": int(os.environ.get("ALPACA_BARS_KEEP", "30")),
    }


# ===== السجلات بلا مفاتيح =====
class RedactFilter(logging.Filter):
    """يحذف المفتاح والسر من كل سجل، ومن أي نص يشبه مفاتيح Alpaca (PK/AK… أو key/secret=…)."""
    PATTERNS = [re.compile(r'("?(?:key|secret|APCA-API-[A-Z-]+)"?\s*[:=]\s*"?)[^",\s}]+', re.I),
                re.compile(r"\b[PA]K[A-Z0-9]{14,}\b")]

    def __init__(self, secrets=()):
        super().__init__()
        self.secrets = [s for s in secrets if s and len(s) >= 6]

    def scrub(self, text):
        for s in self.secrets:
            text = text.replace(s, "***")
        for p in self.PATTERNS:
            text = p.sub(lambda m: (m.group(1) if m.lastindex else "") + "***", text)
        return text

    def filter(self, record):
        msg = record.getMessage()
        if record.exc_info:
            msg += "\n" + logging.Formatter().formatException(record.exc_info)
            record.exc_info = record.exc_text = None
        record.msg, record.args = self.scrub(msg), ()
        return True


log = logging.getLogger("alpaca")


def setup_logging(secrets=(), path=None, console=True):
    """سجل في data/alpaca_stream.log (يدور عند 5MB) وتحذيرات على الشاشة؛ كل المخارج تمر بمرشح المفاتيح."""
    log.setLevel(logging.INFO)
    log.propagate = False
    for h in list(log.handlers):
        log.removeHandler(h)
    flt = RedactFilter(secrets)
    path = Path(path or HERE / "data" / "alpaca_stream.log")
    path.parent.mkdir(parents=True, exist_ok=True)
    fh = RotatingFileHandler(path, maxBytes=5_000_000, backupCount=3, encoding="utf-8")
    fh.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    fh.addFilter(flt)
    log.addHandler(fh)
    if console:
        ch = logging.StreamHandler()
        ch.setLevel(logging.WARNING)
        ch.setFormatter(logging.Formatter("Alpaca: %(message)s"))
        ch.addFilter(flt)
        log.addHandler(ch)
    return path


# ===== الوقت والتأخير =====
_base_cache = {}


def ts(rfc3339):
    """RFC-3339 بدقة النانوثانية ← ثوانٍ (float). البداية حتى الثانية تُخزَّن لأنها تتكرر آلاف المرات."""
    base, _, frac = rfc3339.rstrip("Z").partition(".")
    sec = _base_cache.get(base)
    if sec is None:
        if len(_base_cache) > 4096:
            _base_cache.clear()
        sec = _base_cache[base] = datetime.strptime(base, "%Y-%m-%dT%H:%M:%S").replace(tzinfo=timezone.utc).timestamp()
    return sec + (float("0." + frac[:9]) if frac else 0.0)


class LatencyWindow:
    """تأخير الوصول = وقت الاستلام المحلي − وقت الحدث عند البورصة، لآخر 60 ثانية. يشمل فرق ساعة الجهاز."""

    def __init__(self, seconds=60, cap=20000):
        self.seconds, self.samples = seconds, deque(maxlen=cap)

    def add(self, now, delay):
        self.samples.append((now, delay))

    def stats(self, now=None):
        now = now or time.time()
        while self.samples and now - self.samples[0][0] > self.seconds:
            self.samples.popleft()
        d = sorted(x[1] for x in self.samples)
        if not d:
            return None
        q = lambda p: round(d[min(len(d) - 1, int(p * len(d)))] * 1000, 1)
        return {"n": len(d), "p50_ms": q(0.5), "p95_ms": q(0.95), "max_ms": round(d[-1] * 1000, 1), "min_ms": round(d[0] * 1000, 1)}


# ===== العميل =====
class AlpacaStream:
    """اتصال واحد بـ SIP مع إعادة اتصال تلقائية ومراقبة انقطاع البيانات.

    connect: دالة ترجع اتصالًا (للاختبار)؛ الافتراضي websocket-client. on_data(msg): يُستدعى لكل رسالة بيانات.
    """

    def __init__(self, cfg, connect=None, on_data=None, market_hours=None):
        self.cfg = cfg
        self._connect = connect or self._default_connect
        self.on_data = on_data
        self.market_hours = market_hours or _market_hours
        self.trades, self.quotes, self.bars = {}, {}, {}
        self.lat = {"trades": LatencyWindow(), "quotes": LatencyWindow(), "bars": LatencyWindow()}
        self.counts = {"trades": 0, "quotes": 0, "bars": 0, "updatedBars": 0, "corrections": 0, "cancels": 0, "other": 0}
        self.rate = deque(maxlen=120)  # (ثانية، عدد الرسائل)
        self.state, self.authenticated, self.fatal = "stopped", False, None
        self.subscription, self.errors = None, deque(maxlen=20)
        self.connects = self.reconnects = 0
        self.connected_at = self.last_data = self.last_msg = None
        self.gaps = deque(maxlen=50)  # (من، إلى، السبب) فترات الانقطاع
        self._ws, self._stop, self._thread = None, threading.Event(), None
        self._force_reason = None

    # ----- التشغيل -----
    def start(self):
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="alpaca-stream", daemon=True)
        self._thread.start()
        threading.Thread(target=self._watchdog, name="alpaca-watchdog", daemon=True).start()

    def stop(self):
        self._stop.set()
        self._close("إيقاف")

    def force_reconnect(self, reason="طلب يدوي"):
        """يغلق الاتصال الحالي فتبدأ إعادة الاتصال (تُستعمل في الاختبار وعند انقطاع البيانات)."""
        self._force_reason = reason
        self._close(reason)

    def _close(self, reason):
        ws = self._ws
        if ws is not None:
            try:
                ws.close()
            except Exception:
                pass

    def _default_connect(self):
        import websocket
        # skip_utf8_validation: تحقق websocket-client من UTF-8 مكتوب بـ Python وبطيء جدًا؛ قيس 50 ألف رسالة/ث معه
        # و1.5 مليون دونه، وفي فحص 2026-11-18 تراكمت الرسائل حتى تأخرت 12 ث. json.loads يرفض النص غير الصالح أصلًا.
        kw = {"timeout": 15, "enable_multithread": True, "skip_utf8_validation": True}
        px = os.environ.get("HTTPS_PROXY") or os.environ.get("https_proxy")
        if px:
            p = urlparse(px)
            kw.update(http_proxy_host=p.hostname, http_proxy_port=p.port, proxy_type="http")
        ca = os.environ.get("SSL_CERT_FILE") or os.environ.get("REQUESTS_CA_BUNDLE")
        if ca:
            kw["sslopt"] = {"ca_certs": ca}
        ws = websocket.create_connection(self.cfg["url"], **kw)
        ws.settimeout(None)
        return ws

    def _run(self):
        backoff = 1.0
        while not self._stop.is_set():
            started, reason = time.time(), None
            try:
                reason = self._session()
            except Exception as e:  # شبكة، TLS، بروكسي، إغلاق مفاجئ
                if self._force_reason:  # إغلاق متعمد (اختبار أو انقطاع بيانات): ليس خطأ شبكة
                    reason = self._force_reason
                else:
                    reason = f"{type(e).__name__}: {e}"
                    self._error(None, reason)
            finally:
                self._close("نهاية الجلسة")  # لا يبقى مقبس مفتوح بعد فشل المصادقة أو انقطاع
                self._ws, self.authenticated, self._force_reason, self._last_error_text = None, False, None, None
            if self._stop.is_set():
                break
            if self.fatal:
                self.state = "failed"
                log.error("توقف البث نهائيًا: %s — لن تُعاد المحاولة حتى يُصحح الإعداد", self.fatal)
                return
            self.state = "reconnecting"
            self.reconnects += 1
            self.gaps.append([time.time(), None, reason])
            if time.time() - started > 60:  # اتصال عاش دقيقة: البداية من جديد
                backoff = 1.0
            wait = max(WAIT.get(self._last_code, 0), backoff) + random.uniform(0, 0.5)
            log.warning("انقطع الاتصال (%s) — إعادة المحاولة بعد %.1f ث (المحاولة %d)", reason, wait, self.reconnects)
            self._stop.wait(wait)
            backoff = min(backoff * 2, 60)

    _last_code = None

    def _session(self):
        self.state = "connecting"
        ws = self._ws = self._connect()
        self.connects += 1
        self._handle(self._recv(ws))  # [{"T":"success","msg":"connected"}]
        ws.send(json.dumps({"action": "auth", "key": self.cfg["key"], "secret": self.cfg["secret"]}))
        self._handle(self._recv(ws))
        if not self.authenticated:
            return self._last_error_text or "لم تكتمل المصادقة"
        sub = {"action": "subscribe"}
        for ch in ("trades", "quotes", "bars"):
            if self.cfg[ch]:
                sub[ch] = self.cfg[ch]
        if self.cfg["bars"]:
            sub["updatedBars"] = self.cfg["bars"]
        ws.send(json.dumps(sub))
        self.state, self.connected_at = "streaming", time.time()
        if self.gaps and self.gaps[-1][1] is None:
            self.gaps[-1][1] = self.connected_at
        log.info("متصل بـ %s ومصادَق (اتصال رقم %d)", self.cfg["url"], self.connects)
        while not self._stop.is_set():
            raw = self._recv(ws)
            if raw is None:
                return self._force_reason or "أغلق الخادم الاتصال"
            self._handle(raw)
            if self.fatal:
                return self.fatal
        return "إيقاف"

    @staticmethod
    def _recv(ws):
        try:
            data = ws.recv()
        except Exception as e:
            if type(e).__name__ in ("WebSocketConnectionClosedException", "ConnectionClosed", "ConnectionClosedOK", "ConnectionClosedError"):
                return None
            raise
        return data or None

    _last_error_text = None
    _lag_warned = 0

    def _error(self, code, text):
        self._last_code, self._last_error_text = code, text
        self.errors.append({"at": time.time(), "code": code, "msg": text})
        log.error("خطأ%s: %s", f" {code}" if code else "", text)
        if code in FATAL:
            self.fatal = f"{code} {FATAL[code]}"

    # ----- الرسائل -----
    def _handle(self, raw):
        if raw is None:
            return
        now = time.time()
        self.last_msg = now
        msgs = json.loads(raw)
        n = 0
        for m in msgs:
            t = m.get("T")
            if t == "t":
                self.trades[m["S"]] = m
                self.counts["trades"] += 1
                self.lat["trades"].add(now, now - ts(m["t"]))
            elif t == "q":
                self.quotes[m["S"]] = m
                self.counts["quotes"] += 1
                self.lat["quotes"].add(now, now - ts(m["t"]))
            elif t in ("b", "u"):
                d = self.bars.get(m["S"])
                if d is None:
                    d = self.bars[m["S"]] = deque(maxlen=self.cfg["bars_keep"])
                bar = (m["t"], m["o"], m["h"], m["l"], m["c"], m["v"], m.get("vw"), m.get("n"))
                if t == "u" and d and d[-1][0] == m["t"]:
                    d[-1] = bar  # شمعة محدثة بصفقة متأخرة
                else:
                    d.append(bar)
                self.counts["bars" if t == "b" else "updatedBars"] += 1
                if t == "b":  # الشمعة تُرسل بعد نهاية دقيقتها: التأخير من نهاية الدقيقة
                    self.lat["bars"].add(now, now - ts(m["t"]) - 60)
            elif t == "c":
                self.counts["corrections"] += 1
            elif t == "x":
                self.counts["cancels"] += 1
            elif t == "success":
                if m.get("msg") == "authenticated":
                    self.authenticated = True
                    self.fatal = None
                continue
            elif t == "subscription":
                self.subscription = {k: v for k, v in m.items() if k != "T" and v}
                log.info("الاشتراك: %s", {k: (v if len(v) < 6 else f"{len(v)} رمز") for k, v in self.subscription.items()})
                continue
            elif t == "error":
                self._error(m.get("code"), m.get("msg"))
                continue
            else:
                self.counts["other"] += 1
            n += 1
            if self.on_data:
                self.on_data(m)
        if n:
            self.last_data = now
            sec = int(now)
            if self.rate and self.rate[-1][0] == sec:
                self.rate[-1][1] += n
            else:
                self.rate.append([sec, n])

    # ----- المراقبة -----
    def _watchdog(self):
        """أثناء ساعات التداول: لا بيانات لمدة stale_seconds ← إعادة اتصال. خارجها لا يُحكم (السوق هادئ)."""
        while not self._stop.wait(5):
            if self.state != "streaming" or not self.market_hours():
                continue
            lag = self.lat["trades"].stats() or self.lat["quotes"].stats()
            if lag and lag["p50_ms"] > 5000 and time.time() - self._lag_warned > 60:
                self._lag_warned = time.time()
                log.warning("البيانات تصل متأخرة %.1f ث (وسيط آخر دقيقة): الجهاز أو الإنترنت أبطأ من حجم البث — "
                            "قلّل الرموز (ALPACA_QUOTES/ALPACA_TRADES)", lag["p50_ms"] / 1000)
            last = self.last_data or self.connected_at
            if last and time.time() - last > self.cfg["stale_seconds"]:
                log.warning("لا بيانات منذ %.0f ث أثناء التداول — إعادة اتصال", time.time() - last)
                self.force_reconnect("انقطاع البيانات")
                self.last_data = None

    def msgs_per_second(self, window=10):
        now = int(time.time())
        return round(sum(n for s, n in self.rate if now - window <= s < now) / window, 1)

    def status(self):
        now = time.time()
        age = lambda t: round(now - t, 1) if t else None
        return {
            "state": self.state, "feed": self.cfg["feed"], "url": self.cfg["url"], "paper_only": True,
            "orders_enabled": False, "authenticated": self.authenticated, "fatal": self.fatal,
            "subscription": self.subscription, "connects": self.connects, "reconnects": self.reconnects,
            "connected_for_s": age(self.connected_at) if self.state == "streaming" else None,
            "last_data_age_s": age(self.last_data), "msgs_per_s_10s": self.msgs_per_second(),
            "counts": dict(self.counts), "symbols": {"trades": len(self.trades), "quotes": len(self.quotes), "bars": len(self.bars)},
            "latency": {k: v.stats(now) for k, v in self.lat.items()},
            "latency_note": "وقت الاستلام على هذا الجهاز − وقت الحدث عند البورصة؛ يتضمن فرق ساعة الجهاز",
            "gaps": [{"from": g[0], "to": g[1], "reason": g[2]} for g in self.gaps],
            "errors": list(self.errors),
        }


def _market_hours(now=None):
    """جلسات التداول الممتدة 4:00–20:00 نيويورك أيام الأسبوع (بلا العطل الرسمية)."""
    from zoneinfo import ZoneInfo
    t = now or datetime.now(ZoneInfo("America/New_York"))
    return t.weekday() < 5 and 4 <= t.hour < 20


# ===== واجهة المنصة =====
_client = None


def enabled():
    load_env()
    return os.environ.get("ALPACA_STREAM", "").strip().lower() in ("1", "true", "yes", "on")


def start():
    """يُستدعى عند تشغيل المنصة. يرجع رسالة للطباعة، أو None إذا البث غير مفعّل."""
    global _client
    if not enabled():
        return None
    try:
        cfg = config()
    except ConfigError as e:
        setup_logging()
        log.error("%s", e)
        return f"⚠️ بث Alpaca لم يبدأ: {e}"
    path = setup_logging((cfg["key"], cfg["secret"]))
    _client = AlpacaStream(cfg)
    _client.start()
    return f"📡 بث Alpaca (SIP، حساب Paper، بلا أوامر) يبدأ — السجل: {path}"


def status():
    if _client is None:
        enabled_ = enabled()
        msg = "غير مفعّل: ضع ALPACA_STREAM=1 في .env" if not enabled_ else "لم يبدأ: راجع السجل data/alpaca_stream.log"
        return {"state": "disabled" if not enabled_ else "not_started", "message": msg}
    return _client.status()


def latest_trade(symbol):
    return _client.trades.get(symbol) if _client else None


def latest_quote(symbol):
    return _client.quotes.get(symbol) if _client else None


def minute_bars(symbol):
    """[(t, o, h, l, c, v, vw, n)] لآخر الدقائق من بث SIP."""
    return list(_client.bars.get(symbol, ())) if _client else []
