"""مصادر شموع الماسح الشامل — طبقة مستقلة يمكن استبدال مزودها دون تغيير المحرك.

كل مزود يوفر history(symbols, interval) ← (الشموع لكل رمز، أسباب التعذر لكل رمز، لحظة الجلب asof).
الشمعة {"t","o","h","l","c","v"} للجلسة الرسمية فقط داخل اليوم. لا تُصنع شموع ولا أحجام عند غياب البيانات.

- YahooProvider: متاح الآن عبر yfinance (غير رسمي، للبحث والتعلم). لا بث شموع للسوق كله: يُعاد جلب الرموز
  على دفعات بإيقاع محدود، فزمن الدورة الكاملة يُقاس ويُعرض.
- AlpacaProvider: Alpaca Market Data (مرخص؛ feed=sip يحتاج اشتراك Algo Trader Plus). طلب واحد يجلب شموع
  حتى 100 رمز. مبني على الواجهة الموثقة ولم يُختبر قبل توفر مفتاح.
"""
import os
import time
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from fullscan import bars as B

NY = ZoneInfo("America/New_York")


class RateLimited(Exception):
    def __init__(self, msg, retry_after=120):
        super().__init__(msg)
        self.retry_after = retry_after


class YahooProvider:
    name = "yahoo"
    label = "Yahoo Finance عبر yfinance (غير رسمي)"
    licensed = False
    live_market_wide = False
    license_note = ("yfinance غير تابع لياهو ولا معتمد منها، ومخصص للبحث والتعلم (نص المكتبة)، وتنطبق شروط استخدام ياهو. "
                    "لا يوفر بثًا لشموع السوق كله؛ تُجلب الرموز على دفعات.")
    grace = 30  # ثوانٍ بعد نهاية الشمعة قبل اعتبارها مغلقة (شموع ياهو قد تكتمل متأخرة)
    symbols_per_s = 8.5  # المعدل المقيس في الجلسة الحية 2026-11-09 (دفعات 50 وفاصل 3 ثوانٍ)
    # الشمعة اليومية: حجم ياهو لا يكتمل لحظة الإغلاق (صفقات مزاد الإغلاق تصل بعده)، فتُنتظر 20 دقيقة
    GRACE = {"1D": 1200}

    def grace_for(self, interval):
        return self.GRACE.get(interval, self.grace)
    batch_size = int(os.environ.get("FULLSCAN_YF_BATCH", "50"))
    min_gap = float(os.environ.get("FULLSCAN_YF_GAP", "3"))  # ثوانٍ بين الدفعات
    # الفاصل ← (فاصل ياهو، المدة، تجميع بالدقائق)
    INTERVALS = {"1m": ("1m", "2d", None), "3m": ("1m", "2d", 3), "5m": ("5m", "5d", None), "15m": ("15m", "1mo", None),
                 "30m": ("30m", "1mo", None), "1h": ("60m", "3mo", None), "1D": ("1d", "1y", None)}

    def supported(self, interval):
        return interval in self.INTERVALS, None

    def history(self, symbols, interval):
        import yfinance as yf
        yf_iv, period, agg = self.INTERVALS[interval]
        asof = time.time()
        try:
            from yfinance import shared
            shared._ERRORS.clear()
        except Exception:
            shared = None
        data = yf.download(symbols, period=period, interval=yf_iv, prepost=False, group_by="ticker",
                           auto_adjust=False, threads=True, progress=False, timeout=20)
        yerr = dict(getattr(shared, "_ERRORS", {}) or {}) if shared else {}
        out, errors = {}, {}
        for s in symbols:
            try:  # group_by="ticker" يرجع أعمدة (الرمز، الحقل) حتى لرمز واحد
                df = data[s].dropna(subset=["Close"]) if s in data.columns.get_level_values(0) else None
            except (KeyError, AttributeError, IndexError):
                df = None
            if df is None or not len(df):
                errors[s] = "لا بيانات من ياهو" + (f": {str(yerr.get(s))[:80]}" if yerr.get(s) else "")
                continue
            rows = []
            for ts, r in df.iterrows():
                if ts.tzinfo is None:  # الشموع اليومية: تاريخ بلا منطقة = منتصف ليل نيويورك
                    t = int(datetime(ts.year, ts.month, ts.day, tzinfo=NY).timestamp())
                else:
                    t = int(ts.timestamp())
                v = r.get("Volume")
                rows.append({"t": t, "o": float(r["Open"]), "h": float(r["High"]), "l": float(r["Low"]),
                             "c": float(r["Close"]), "v": float(v) if v == v and v is not None else None})
            if interval != "1D":
                rows = [b for b in rows if B.in_regular(b["t"])]
            out[s] = B.aggregate(rows, agg) if agg else rows
        if not out and errors:
            text = " ".join(str(v) for v in yerr.values())
            if any(k in text for k in ("Rate", "429", "401", "Crumb", "Too Many")):
                raise RateLimited(f"ياهو رفض الطلبات: {text[:120]}")
        return out, errors, asof


class AlpacaProvider:
    """Alpaca Market Data v2 — https://data.alpaca.markets/v2/stocks/bars (عدة رموز في الطلب).

    المفاتيح من المتغيرات APCA_API_KEY_ID وAPCA_API_SECRET_KEY (لا تُكتب في الكود ولا في المستودع).
    ALPACA_FEED=sip (السوق الموحد، اشتراك مدفوع) أو iex (مجاني، بورصة IEX وحدها — حجمها جزء صغير من السوق).
    """
    name = "alpaca"
    licensed = True
    URL = "https://data.alpaca.markets/v2/stocks/bars"
    TF = {"1m": "1Min", "3m": "3Min", "5m": "5Min", "15m": "15Min", "30m": "30Min", "1h": "30Min", "1D": "1Day"}
    LOOKBACK_DAYS = {"1m": 4, "3m": 5, "5m": 8, "15m": 25, "30m": 40, "1h": 70, "1D": 550}
    grace = 5
    batch_size = 100
    symbols_per_s = 60.0  # تقدير من حد الطلبات (100 رمز/طلب)؛ لم يُقَس قبل توفر مفتاح

    def grace_for(self, interval):
        return 900 if interval == "1D" else self.grace
    min_gap = 60 / float(os.environ.get("ALPACA_RPM", "180"))  # الحد الافتراضي 200 طلب/دقيقة للخطة المجانية

    def __init__(self):
        self.key = os.environ.get("APCA_API_KEY_ID")
        self.secret = os.environ.get("APCA_API_SECRET_KEY")
        self.feed = os.environ.get("ALPACA_FEED", "sip").lower()
        self.live_market_wide = self.feed == "sip"
        self.label = f"Alpaca ({'SIP — كل البورصات' if self.feed == 'sip' else 'IEX وحدها'})"
        self.license_note = ("مصدر مرخص للاستخدام الشخصي. SIP يحتاج اشتراك Algo Trader Plus؛ "
                             "IEX المجاني لا يمثل حجم السوق كله.")

    def supported(self, interval):
        if not (self.key and self.secret):
            return False, "مفاتيح Alpaca غير مضبوطة (APCA_API_KEY_ID وAPCA_API_SECRET_KEY)"
        return interval in self.TF, None

    def history(self, symbols, interval):
        import requests
        asof = time.time()
        start = (datetime.now(timezone.utc) - timedelta(days=self.LOOKBACK_DAYS[interval])).strftime("%Y-%m-%dT%H:%M:%SZ")
        params = {"symbols": ",".join(s.replace("-", ".") for s in symbols), "timeframe": self.TF[interval],
                  "start": start, "limit": 10000, "adjustment": "raw", "feed": self.feed, "sort": "asc"}
        headers = {"APCA-API-KEY-ID": self.key, "APCA-API-SECRET-KEY": self.secret}
        raw = {}
        while True:
            r = requests.get(self.URL, params=params, headers=headers, timeout=30)
            if r.status_code == 429:
                raise RateLimited("Alpaca: تجاوز حد الطلبات", int(r.headers.get("Retry-After", 60)))
            if r.status_code in (401, 403):
                raise RateLimited(f"Alpaca رفض المفتاح أو الاشتراك ({r.status_code}): {r.text[:120]}", 600)
            r.raise_for_status()
            d = r.json()
            for sym, rows in (d.get("bars") or {}).items():
                raw.setdefault(sym.replace(".", "-"), []).extend(rows)
            if not d.get("next_page_token"):
                break
            params["page_token"] = d["next_page_token"]
        out, errors = {}, {}
        for s in symbols:
            rows = []
            for b in raw.get(s, []):
                t = int(datetime.strptime(b["t"][:19], "%Y-%m-%dT%H:%M:%S").replace(tzinfo=timezone.utc).timestamp())
                if interval == "1D":
                    d = datetime.fromtimestamp(t, NY)
                    t = int(datetime(d.year, d.month, d.day, tzinfo=NY).timestamp())
                rows.append({"t": t, "o": b["o"], "h": b["h"], "l": b["l"], "c": b["c"], "v": b.get("v")})
            if interval != "1D":
                rows = [b for b in rows if B.in_regular(b["t"])]
            if interval == "1h":
                rows = B.aggregate(rows, 60)  # ساعة تبدأ 9:30 مثل الشارت (من شمعتي 30 دقيقة)
            if rows:
                out[s] = rows
            else:
                errors[s] = "لا شموع من Alpaca لهذا الرمز"
        return out, errors, asof


def make(name=None):
    name = (name or os.environ.get("FULLSCAN_PROVIDER", "yahoo")).lower()
    return AlpacaProvider() if name == "alpaca" else YahooProvider()
