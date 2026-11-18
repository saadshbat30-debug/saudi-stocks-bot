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

    full = os.environ.get("DATA_SOURCE", "").strip().lower() == "alpaca"

    def symbols(name, default):
        """* = كل السوق · watch = الأسهم التي تتابعها المنصة الآن فقط · قائمة رموز (+ المتابعة) · off."""
        raw = os.environ.get(name, default).strip()
        if raw.lower() in ("", "none", "off", "0", "watch"):
            return []
        return ["*"] if raw == "*" else [s.strip().upper() for s in raw.split(",") if s.strip()]
    # مصدر المنصة (DATA_SOURCE=alpaca): الشموع اليومية لكل السوق تكفي الجدول، والصفقات لما يُتابَع فقط —
    # كل صفقات السوق لا يحملها اتصال المستخدم (فحص 2026-11-18: 338ms ذهابًا وإيابًا، والبث تأخر حتى 32 ث)
    return {
        "key": key, "secret": secret, "feed": feed, "url": STREAM_BASE + feed, "trading_url": trading_url,
        "trades": symbols("ALPACA_TRADES", "watch" if full else "*"), "quotes": symbols("ALPACA_QUOTES", "watch" if full else "*"),
        "bars": symbols("ALPACA_BARS", "watch" if full else "*"), "daily": symbols("ALPACA_DAILY", "*" if full else "off"),
        "watch_max": int(os.environ.get("ALPACA_WATCH_MAX", "60")),
        "tape_max": int(os.environ.get("ALPACA_TAPE_MAX", "200")),
        "stale_seconds": float(os.environ.get("ALPACA_STALE_SECONDS", "30")),
        "bars_keep": int(os.environ.get("ALPACA_BARS_KEEP", "30")),
        # msgpack: نصف حجم JSON تقريبًا (قيس 368 مقابل 733 KB لـ 6000 صفقة)، وAlpaca يدعمه بترويسة Content-Type
        "format": os.environ.get("ALPACA_FORMAT", "msgpack").strip().lower(),
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
logging.getLogger("websockets").setLevel(logging.CRITICAL)  # أخطاؤها تصل إلينا كاستثناءات وتُسجَّل في سجلنا بلا مفاتيح


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
    if isinstance(rfc3339, (int, float)):  # msgpack: الوقت يصل ثواني جاهزة
        return float(rfc3339)
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
        d = sorted(x[1] for x in list(self.samples))  # list(): نسخة ذرية؛ الخيط الآخر يضيف أثناء القراءة
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
        self.daily = {}  # الرمز ← (شمعة اليوم من قناة dailyBars، وقت استلامها)
        self.watched = {}  # الرمز ← آخر طلب متابعة كاملة (صفقات وأسعار وشموع دقيقة)
        self.tape_watched = {}  # الرمز ← آخر طلب متابعة الصفقات وحدها
        self.on_trade = None  # دالة (الرمز، السعر، الكمية، الوقت) لكل صفقة — المنصة تكتبها في مخزن البث المشترك
        self._send_lock = threading.Lock()
        self.lat = {"trades": LatencyWindow(), "quotes": LatencyWindow(), "bars": LatencyWindow()}
        self.counts = {"trades": 0, "quotes": 0, "bars": 0, "updatedBars": 0, "dailyBars": 0, "corrections": 0, "cancels": 0, "other": 0}
        self.rate = deque(maxlen=120)  # (ثانية، عدد الرسائل)
        self.state, self.authenticated, self.fatal = "stopped", False, None
        self.subscription, self.errors = None, deque(maxlen=20)
        self.connects = self.reconnects = 0
        self.connected_at = self.last_data = self.last_msg = None
        self.gaps = deque(maxlen=50)  # (من، إلى، السبب) فترات الانقطاع
        self._ws, self._stop, self._thread = None, threading.Event(), None
        self._force_reason = None
        self.rx_bytes, self.cpu = 0, deque(maxlen=120)  # (وقت، وقت معالج خيط الاستقبال، بايتات مستلمة)
        self.transport = self.compression = None

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

    format = "json"

    def _effective(self):
        """الرموز المتابَعة لكل قناة: المتابعة الكاملة (صفحة السهم وأنشط الأسهم) لكل القنوات، ومتابعة الصفقات
        وحدها (مرشحو «قبل الانطلاق») لقناة الصفقات فقط — الأسعار (quotes) أثقل بكثير على الاتصال."""
        full = set(self.watched)
        return {"trades": full | set(self.tape_watched), "quotes": full, "bars": full}

    def _channels(self):
        """الرموز لكل قناة عند الاشتراك: الثابتة من الإعداد + المتابَعة الآن (إلا إذا كانت القناة لكل السوق)."""
        eff, out = self._effective(), {}
        for ch in ("trades", "quotes", "bars"):
            fixed = self.cfg[ch]
            out[ch] = ["*"] if fixed == ["*"] else sorted(set(fixed) | eff[ch])
        out["dailyBars"] = self.cfg.get("daily") or []
        return out

    def watch(self, symbols, trades_only=False):
        """تطلب المنصة بيانات هذه الرموز لحظيًا. trades_only: الصفقات فقط (حد tape_max)، وإلا الصفقات والأسعار
        وشموع الدقيقة (حد watch_max). الأقدم طلبًا يخرج عند تجاوز الحد."""
        group = self.tape_watched if trades_only else self.watched
        cap = self.cfg.get("tape_max", 200) if trades_only else self.cfg.get("watch_max", 60)
        before, now = self._effective(), time.time()
        for i, sym in enumerate(symbols):
            if sym and "." not in sym and "^" not in sym:
                group[sym] = now - i * 1e-6  # القائمة مرتبة بالأهمية: الأول يبقى عند تجاوز الحد
        if len(group) > cap:
            keep = set(sorted(group, key=group.get, reverse=True)[:cap])
            for s in [s for s in group if s not in keep]:
                del group[s]
        after = self._effective()
        ws = self._ws
        if ws is None or self.state != "streaming":
            return  # تُرسل مع الاشتراك عند الاتصال
        for action, diff in (("unsubscribe", lambda ch: before[ch] - after[ch]), ("subscribe", lambda ch: after[ch] - before[ch])):
            msg = {"action": action}
            for ch in ("trades", "quotes", "bars"):
                if self.cfg[ch] == ["*"]:
                    continue
                syms = sorted(diff(ch) - set(self.cfg[ch]))
                if syms:
                    msg[ch] = syms
                    if ch == "bars":
                        msg["updatedBars"] = syms
            if len(msg) > 1:
                try:
                    with self._send_lock:
                        ws.send(self._encode(msg))
                except Exception as e:
                    log.warning("تعذر تحديث قائمة المتابعة: %s", type(e).__name__)

    def live(self, symbol):
        """أحدث ما وصل للرمز: السعر (صفقة، أو إغلاق آخر شمعة دقيقة، أو شمعة اليوم) ووقته، وحجم اليوم وأعلاه وأدناه.
        None إن لم يصل شيء. الحجم من قناة dailyBars (كل الصفقات حتى لحظة إرسالها، تتحدث كل دقيقة)."""
        trade, bars, day = self.trades.get(symbol), self.bars.get(symbol), self.daily.get(symbol)
        cands = []
        if trade:
            cands.append((ts(trade["t"]), trade["p"], "trade"))
        if bars:
            b = bars[-1]
            cands.append((ts(b[0]) + 60, b[4], "bar"))
        if day:
            cands.append((day[1], day[0]["c"], "daily"))
        if not cands:
            return None
        at, price, kind = max(cands, key=lambda x: x[0])
        out = {"price": price, "at": at, "kind": kind}
        if day:
            d = day[0]
            out.update(day_date=ts(d["t"]), volume=d["v"], day_high=d["h"], day_low=d["l"], open=d["o"], daily_at=day[1],
                       vwap=d.get("vw"))
        return out

    def _encode(self, obj):
        if self.format == "msgpack":
            import msgpack
            return msgpack.packb(obj)
        return json.dumps(obj)

    def _default_connect(self):
        """websockets (يطلب ضغط permessage-deflate الذي يدعمه Alpaca) وإلا websocket-client بلا ضغط.

        فحص 2026-11-18 على جهاز المستخدم: كل الصفقات عبر SIP دون ضغط ≈ 10 ميغابت/ث عند 10 آلاف صفقة/ث، والاستقبال
        بقي 4–5 آلاف رسالة/ث ثم قطع الخادم الاتصال. الضغط يقلل الحجم نحو 4× (قيس على إطارات صفقات بشكل Alpaca)."""
        self.transport = self.compression = None
        try:
            from websockets.sync.client import connect
        except ImportError:
            connect = None
        if connect is not None:
            headers = {}
            self.format = "json"
            if self.cfg.get("format") == "msgpack":
                try:
                    import msgpack  # noqa: F401
                    headers, self.format = {"Content-Type": "application/msgpack"}, "msgpack"
                except ImportError:
                    log.warning("مكتبة msgpack غير مثبتة: الاستقبال بـ JSON (ضعف الحجم) — py -m pip install -r requirements.txt")
            ws = connect(self.cfg["url"], compression="deflate", max_size=None, open_timeout=15, additional_headers=headers,
                         ping_interval=30, ping_timeout=120, close_timeout=2)
            self.transport = "websockets"
            self.compression = ws.response.headers.get("Sec-WebSocket-Extensions") or None
            return ws
        import websocket
        self.format = "json"
        # skip_utf8_validation: تحقق websocket-client من UTF-8 مكتوب بـ Python وبطيء جدًا (50 ألف رسالة/ث مقابل 1.5 مليون)
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
        self.transport = "websocket-client"
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
        self.state, self._last_code = "connecting", None
        ws = self._ws = self._connect()
        self.connects += 1
        self._handle(self._recv(ws))  # [{"T":"success","msg":"connected"}]
        try:
            ws.send(self._encode({"action": "auth", "key": self.cfg["key"], "secret": self.cfg["secret"]}))
        except Exception:
            pass  # الخادم أغلق بعد رسالة خطأ (406 مثلًا): الرسالة ما زالت في المخزن وتُقرأ الآن
        self._handle(self._recv(ws))
        if not self.authenticated:
            return self._last_error_text or "لم تكتمل المصادقة"
        sub = {"action": "subscribe"}
        for ch, syms in self._channels().items():
            if syms:
                sub[ch] = syms
        if sub.get("bars"):
            sub["updatedBars"] = sub["bars"]
        if len(sub) > 1:
            with self._send_lock:
                ws.send(self._encode(sub))
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
        self.rx_bytes += len(raw)
        cpu = time.thread_time()
        if not self.cpu or now - self.cpu[-1][0] >= 1:
            self.cpu.append((now, cpu, self.rx_bytes))
        if isinstance(raw, (bytes, bytearray)):
            import msgpack
            msgs = msgpack.unpackb(raw, timestamp=1)  # الأوقات ثوانٍ (float) مباشرة
        else:
            msgs = json.loads(raw)
        n = 0
        for m in msgs:
            t = m.get("T")
            if t == "t":
                self.trades[m["S"]] = m
                self.counts["trades"] += 1
                tt = ts(m["t"])
                self.lat["trades"].add(now, now - tt)
                if self.on_trade:
                    try:
                        self.on_trade(m["S"], m["p"], m.get("s") or 0, tt)
                    except Exception as e:
                        log.warning("معالجة صفقة %s: %s", m["S"], type(e).__name__)
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
            elif t == "d":  # شمعة اليوم لكل الرموز، تُرسل كل دقيقة بعد الافتتاح
                self.daily[m["S"]] = (m, now)
                self.counts["dailyBars"] += 1
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

    def load(self, window=10):
        """انشغال خيط الاستقبال (100% = المعالج هو الحد) وحجم البيانات بعد فك الضغط، لآخر window ثانية."""
        pts = [p for p in list(self.cpu) if p[0] >= time.time() - window - 1]
        if len(pts) < 2 or pts[-1][0] - pts[0][0] < 1:
            return {"rx_kb_s": None, "cpu_busy_pct": None}
        dt = pts[-1][0] - pts[0][0]
        return {"rx_kb_s": round((pts[-1][2] - pts[0][2]) / dt / 1024, 1),
                "cpu_busy_pct": round(min(100.0, (pts[-1][1] - pts[0][1]) / dt * 100), 1)}

    def msgs_per_second(self, window=10):
        now = int(time.time())
        return round(sum(n for s, n in list(self.rate) if now - window <= s < now) / window, 1)

    def status(self):
        now = time.time()
        age = lambda t: round(now - t, 1) if t else None
        return {
            "state": self.state, "feed": self.cfg["feed"], "url": self.cfg["url"], "paper_only": True,
            "orders_enabled": False, "authenticated": self.authenticated, "fatal": self.fatal,
            "subscription": self.subscription, "connects": self.connects, "reconnects": self.reconnects,
            "connected_for_s": age(self.connected_at) if self.state == "streaming" else None,
            "last_data_age_s": age(self.last_data), "msgs_per_s_10s": self.msgs_per_second(),
            "transport": self.transport, "compression": self.compression, "format": self.format, **self.load(),
            "counts": dict(self.counts), "watched": len(self.watched), "tape_watched": len(self.tape_watched),
            "symbols": {"trades": len(self.trades), "quotes": len(self.quotes), "bars": len(self.bars), "daily": len(self.daily)},
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
    return (os.environ.get("ALPACA_STREAM", "").strip().lower() in ("1", "true", "yes", "on")
            or source_is_alpaca())


def source_is_alpaca():
    """DATA_SOURCE=alpaca: أسعار الجدول وأحجامه والشارت من Alpaca SIP (قائمة الأسهم والبيانات الأساسية من ياهو)."""
    load_env()
    return os.environ.get("DATA_SOURCE", "").strip().lower() == "alpaca"


def client():
    return _client


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
