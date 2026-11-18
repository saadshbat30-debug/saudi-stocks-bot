"""فحص ربط Alpaca بمفاتيحك الحقيقية — يُشغَّل على جهازك: py alpaca_check.py

ما يفحصه (قراءة فقط، لا يرسل أي أمر):
  1. الإعدادات: المفاتيح موجودة، المصدر SIP، حساب Paper.
  2. المصادقة: GET /v2/account على paper-api.alpaca.markets.
  3. صلاحية SIP: آخر صفقة لعدة أسهم بـ feed=sip من REST (يُرفض بلا اشتراك)، والبورصات التي جاءت منها.
  4. فرق ساعة الجهاز عن خادم Alpaca (يدخل في قياس التأخير).
  5. البث: اتصال wss://stream.data.alpaca.markets/v2/sip، مصادقة، اشتراك، استقبال صفقات وأسعار وشموع دقيقة،
     عدد الرسائل والرموز والبورصات، والتأخير الفعلي.
  6. إعادة الاتصال: يقطع الاتصال عمدًا في منتصف المدة ويقيس زمن العودة واستئناف البيانات.
  7. تسرب المفاتيح: يبحث عن المفتاح والسر (وأجزاء منهما) في السجل وفي كل ما طُبع وفي الحالة.

الخيارات: --seconds 90 (مدة البث) · --symbols AAPL,TSLA (بدل كل السوق) · --test-stream (بث Alpaca التجريبي
FAKEPACA، يعمل خارج أوقات السوق؛ ليس SIP) · --no-quotes (بلا أسعار كل السوق، أخف على الإنترنت).

مهم: Alpaca يسمح غالبًا باتصال بث واحد. أوقف المنصة إن كان فيها ALPACA_STREAM=1 قبل الفحص، وإلا يظهر 406.
النتيجة تُطبع وتُحفظ في data/alpaca_check_<الوقت>.txt (بلا مفاتيح).
"""
import argparse
import io
import json
import os
import sys
import time
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path

import alpaca_stream as al

DATA_URL = "https://data.alpaca.markets"
RESULTS = []


class Tee(io.TextIOBase):
    """يطبع على الشاشة ويحفظ في الملف، مع حذف المفاتيح من الاثنين."""

    def __init__(self, stream, path, redact):
        self.stream, self.file, self.redact = stream, open(path, "w", encoding="utf-8"), redact

    def write(self, s):
        s = self.redact.scrub(s)
        self.stream.write(s)
        self.file.write(s)
        return len(s)

    def flush(self):
        self.stream.flush()
        self.file.flush()


def result(name, ok, detail):
    """ok: True نجح · False فشل · None لم يُنفّذ (مع السبب)."""
    RESULTS.append((name, ok, detail))
    mark = {True: "✅ نجح", False: "❌ فشل", None: "⏸️ لم يُنفّذ"}[ok]
    print(f"{mark} — {name}: {detail}")


def http_get(url, cfg, params=None):
    import requests
    t0 = time.time()
    r = requests.get(url, params=params, timeout=20,
                     headers={"APCA-API-KEY-ID": cfg["key"], "APCA-API-SECRET-KEY": cfg["secret"]})
    return r, time.time() - t0


def check_network():
    """زمن الذهاب والإياب إلى خادم البث (اتصال TCP فقط) — يحدد مع فقد الحزم أقصى سرعة لاتصال واحد."""
    import socket
    print("\n— المسار إلى Alpaca —")
    times = []
    for _ in range(7):
        t0 = time.perf_counter()
        try:
            socket.create_connection(("stream.data.alpaca.markets", 443), timeout=10).close()
            times.append((time.perf_counter() - t0) * 1000)
        except OSError as e:
            print(f"  تعذر الاتصال: {e}")
        time.sleep(0.2)
    if not times:
        result("زمن الذهاب والإياب", False, "تعذر فتح اتصال TCP")
        return None
    times.sort()
    med = times[len(times) // 2]
    result("زمن الذهاب والإياب", True, f"وسيط {med:.0f}ms (أدنى {times[0]:.0f} · أقصى {times[-1]:.0f}) من {len(times)} محاولات"
           + (" — مرتفع: كل الصفقات لكل السوق تحتاج اتصالًا قريبًا من أمريكا" if med > 200 else ""))
    return med


def check_auth(cfg):
    print("\n— المصادقة (حساب Paper) —")
    try:
        r, dt = http_get(cfg["trading_url"] + "/v2/account", cfg)
    except Exception as e:
        result("المصادقة", False, f"تعذر الاتصال بـ {cfg['trading_url']}: {type(e).__name__}")
        return None
    if r.status_code != 200:
        result("المصادقة", False, f"HTTP {r.status_code} من {cfg['trading_url']}/v2/account: {r.text[:150]}")
        return None
    a = r.json()
    num = str(a.get("account_number") or "")
    print(f"  الحساب: {num[:2]}…{num[-3:] if len(num) > 5 else ''} · الحالة {a.get('status')} · العملة {a.get('currency')}"
          f" · التداول محجوب: {a.get('trading_blocked')} · زمن الطلب {dt * 1000:.0f}ms")
    result("المصادقة", True, f"HTTP 200 من {cfg['trading_url']} (حساب Paper) · الحالة {a.get('status')}")
    return r


def check_sip_rest(cfg, symbols):
    print("\n— صلاحية SIP من REST —")
    try:
        r, dt = http_get(DATA_URL + "/v2/stocks/trades/latest", cfg, {"symbols": ",".join(symbols), "feed": "sip"})
    except Exception as e:
        result("صلاحية SIP (REST)", False, f"تعذر الاتصال: {type(e).__name__}")
        return None
    if r.status_code != 200:
        why = ("المفتاح أو السر غير مقبول" if r.status_code == 401 else
               "الاشتراك لا يسمح ببيانات SIP الحديثة" if r.status_code == 403 else "رد غير متوقع")
        result("صلاحية SIP (REST)", False, f"HTTP {r.status_code} — {why}: {' '.join(r.text.split())[:160]}")
        return r
    trades = r.json().get("trades", {})
    now = time.time()
    xs = set()
    for s, t in trades.items():
        xs.add(t.get("x"))
        print(f"  {s}: ${t.get('p')} · بورصة {t.get('x')} · عمر آخر صفقة {now - al.ts(t['t']):.1f} ث")
    result("صلاحية SIP (REST)", True, f"HTTP 200 بـ feed=sip · بورصات آخر الصفقات: {sorted(xs)}"
           + (" (كلها V=IEX: راجع الاشتراك)" if xs == {"V"} else ""))
    return r


CLOCK_OFFSET = None


def clock_offset(resp):
    """فرق ساعة الجهاز عن ساعة خادم Alpaca (دقة ثانية واحدة من ترويسة Date)."""
    print("\n— ساعة الجهاز —")
    if resp is None or "Date" not in resp.headers:
        result("ساعة الجهاز", None, "لا رد من الخادم لقراءة ترويسة Date")
        return None
    server = parsedate_to_datetime(resp.headers["Date"]).timestamp()
    off = time.time() - server
    global CLOCK_OFFSET
    CLOCK_OFFSET = off
    ok = abs(off) < 1.5
    result("ساعة الجهاز", ok, f"الجهاز {'متقدم' if off > 0 else 'متأخر'} {abs(off):.1f} ث عن خادم Alpaca (دقة ±1 ث)"
           + ("" if ok else " — صحح ساعة Windows (الإعدادات ← الوقت ← مزامنة الآن)، فالتأخير المقاس يتضمن هذا الفرق"))
    return off


def run_stream(cfg, seconds, test_stream):
    print(f"\n— البث {'التجريبي FAKEPACA (ليس SIP)' if test_stream else 'SIP'} لمدة {seconds} ث —")
    if test_stream:
        cfg = {**cfg, "url": al.STREAM_BASE.replace("/v2/", "/v2/test"), "trades": ["FAKEPACA"], "quotes": ["FAKEPACA"], "bars": ["FAKEPACA"]}
    exchanges, symbols_seen = {}, set()

    def on_data(m):
        if m.get("T") == "t":
            exchanges[m.get("x")] = exchanges.get(m.get("x"), 0) + 1
        if m.get("S"):
            symbols_seen.add(m["S"])
    c = al.AlpacaStream(cfg, on_data=on_data, market_hours=lambda: False)  # المراقبة الآلية معطلة: نقطع يدويًا
    c.start()
    t0, peak, kicked, kick_at, back_at, data_after, loads = time.time(), 0, False, None, None, None, []
    while time.time() - t0 < seconds:
        time.sleep(1)
        peak = max(peak, c.msgs_per_second(1))
        if c.state == "failed":
            break
        sec = datetime.now(timezone.utc).second
        if (not kicked and c.state == "streaming" and time.time() - t0 > seconds / 2 and c.last_data
                and 15 <= sec <= 40 and c.counts["bars"]):  # بعد استلام شموع، وبعيدًا عن حد الدقيقة
            kicked, kick_at, before = True, time.time(), sum(c.counts.values())
            print("  … قطع الاتصال عمدًا لاختبار إعادة الاتصال")
            c.force_reconnect("اختبار إعادة الاتصال")
        if (not kicked and c.state == "streaming" and c.last_data and time.time() - t0 > seconds * 0.75
                and 15 <= sec <= 40):  # لم تصل شموع (السوق هادئ): يُختبر القطع على أي حال
            kicked, kick_at, before = True, time.time(), sum(c.counts.values())
            print("  … قطع الاتصال عمدًا لاختبار إعادة الاتصال")
            c.force_reconnect("اختبار إعادة الاتصال")
        if kicked and back_at is None and c.connects >= 2 and c.state == "streaming":
            back_at = time.time()
        if back_at and data_after is None and sum(c.counts.values()) > before:
            data_after = time.time()
        if int(time.time() - t0) % 15 == 0:
            st = c.status()
            lt = (st["latency"].get("trades") or {}).get("p50_ms")
            print(f"  {int(time.time() - t0)} ث: {st['state']} · {st['msgs_per_s_10s']} رسالة/ث · "
                  f"صفقات {c.counts['trades']} · أسعار {c.counts['quotes']} · شموع {c.counts['bars']} · "
                  f"تأخير {lt if lt is not None else '—'}ms · {st['rx_kb_s']} KB/ث · معالج الاستقبال {st['cpu_busy_pct']}%")
            loads.append((st["cpu_busy_pct"], st["rx_kb_s"]))
    st = c.status()
    c.stop()
    print(f"  الاتصال: {st['transport']} · الترميز: {st['format']} · الضغط: {st['compression'] or 'غير مفعّل (الخادم لم يقبله)'}")
    print(json.dumps({k: st[k] for k in ("state", "fatal", "subscription", "counts", "symbols", "latency", "errors")},
                     ensure_ascii=False, indent=1, default=str)[:3000])

    name = "البث التجريبي" if test_stream else "البث SIP"
    if st["fatal"] or not st["subscription"]:  # الاشتراك لا يُرسل إلا بعد مصادقة ناجحة
        msg = st["fatal"] or (st["errors"][-1]["msg"] if st["errors"] else "لم يتصل")
        if any(e.get("code") == 406 for e in st["errors"]):
            msg += " — اتصال آخر مفتوح بنفس المفاتيح: أوقف المنصة أو أي برنامج آخر يستعمل البث ثم أعد الفحص"
        result(f"{name}: المصادقة والاشتراك", False, msg)
        return st
    result(f"{name}: المصادقة والاشتراك", True, f"متصل بـ {cfg['url']} · الاشتراك {list((st['subscription'] or {}).keys())}")
    if not test_stream:
        many = len([x for x, n in exchanges.items() if n]) >= 3
        result("المصدر SIP", None if not exchanges else many,
               f"الرابط /v2/sip قُبل بعد المصادقة · بورصات الصفقات المستلمة: {dict(sorted(exchanges.items(), key=lambda x: -x[1])[:12])}"
               + ("" if exchanges else " — لا صفقات (السوق مغلق؟)"))
    n_t, n_q, n_b = st["counts"]["trades"], st["counts"]["quotes"], st["counts"]["bars"]
    result("استقبال الصفقات", True if n_t else None, f"{n_t} صفقة لـ {st['symbols']['trades']} رمز" if n_t else "لا صفقات في هذه المدة (السوق مغلق أو هادئ)")
    if cfg["quotes"]:
        result("استقبال الأسعار", True if n_q else None, f"{n_q} سعر لـ {st['symbols']['quotes']} رمز" if n_q else "لا أسعار في هذه المدة")
    if cfg["bars"]:
        result("شموع الدقيقة", True if n_b else (False if n_t > 1000 and seconds >= 130 else None),
               f"{n_b} شمعة لـ {st['symbols']['bars']} رمز (وصلت بعد حد الدقيقة)" if n_b else
               "لا شموع رغم وصول صفقات طوال أكثر من دقيقتين" if n_t > 1000 and seconds >= 130 else
               "لا شموع: المدة أقصر من دقيقتين أو لا تداول")
    lat = st["latency"]
    if lat.get("trades") or lat.get("quotes"):
        parts = [f"{k}: وسيط {v['p50_ms']}ms · 95% {v['p95_ms']}ms · أقصى {v['max_ms']}ms ({v['n']} عينة)" for k, v in lat.items() if v]
        main_lat = (lat.get("trades") or lat.get("quotes"))["p50_ms"] - (CLOCK_OFFSET or 0) * 1000
        ok = main_lat <= 2000
        result("التأخير الفعلي (آخر 60 ث)", ok, " | ".join(parts) + f" · ذروة {peak:.0f} رسالة/ث"
               + (f" · بعد طرح فرق الساعة: وسيط الصفقات ≈ {main_lat:.0f}ms" if CLOCK_OFFSET else "")
               + ("" if ok else " — أعلى من ثانيتين: الرسائل تتراكم عند الجهاز (المعالجة أو الإنترنت أبطأ من البث)."
                  " جرّب --no-quotes أو --symbols لتقليل الحجم ثم قارن"))
        cpus = [x[0] for x in loads if x[0] is not None]
        if cpus and not ok:
            busy = sorted(cpus)[len(cpus) // 2]
            print(f"  التشخيص: انشغال معالج الاستقبال (وسيط) {busy}% — "
                  + ("المعالج هو الحد: قلّل الرموز أو شغّل على جهاز أسرع" if busy >= 80 else
                     "المعالج غير مشغول، فالحد هو الإنترنت أو المسار إلى Alpaca"
                     + ("" if st["compression"] else " (والضغط غير مفعّل)")))
    else:
        result("التأخير الفعلي", None, "لا بيانات لقياسه")
    if kicked:
        ok = back_at is not None
        result("إعادة الاتصال", ok and (data_after is not None or not n_t),
               f"عاد بعد {back_at - kick_at:.1f} ث وأعاد المصادقة والاشتراك"
               + (f"، والبيانات استؤنفت بعد {data_after - kick_at:.1f} ث" if data_after else "، ولم تصل بيانات بعدها") if ok
               else f"لم يعد خلال {seconds - (kick_at - t0):.0f} ث")
    else:
        result("إعادة الاتصال", None, "لم يُختبر: البث لم يستقر قبل منتصف المدة")
    return st


def check_leaks(cfg, log_path, out_path, status):
    print("\n— تسرب المفاتيح —")
    texts = {"السجل": Path(log_path).read_text(encoding="utf-8") if Path(log_path).exists() else "",
             "المخرجات": Path(out_path).read_text(encoding="utf-8"),
             "الحالة": json.dumps(status or {}, ensure_ascii=False, default=str)}
    needles = {cfg["key"], cfg["secret"], cfg["key"][:8], cfg["secret"][:8], cfg["secret"][-8:]}
    found = [where for where, t in texts.items() for n in needles if n and n in t]
    result("عدم تسرب المفاتيح", not found, "لا أثر للمفتاح أو السر (ولا لأول/آخر 8 أحرف) في " + "، ".join(texts)
           if not found else f"وُجد جزء من مفتاح في: {sorted(set(found))}")


def main():
    ap = argparse.ArgumentParser(description="فحص ربط Alpaca (قراءة فقط)")
    ap.add_argument("--seconds", type=int, default=150)
    ap.add_argument("--symbols", default="")
    ap.add_argument("--no-quotes", action="store_true")
    ap.add_argument("--test-stream", action="store_true")
    ap.add_argument("--json", action="store_true", help="الاستقبال بـ JSON بدل msgpack (للمقارنة)")
    a = ap.parse_args()
    try:
        cfg = al.config()
    except al.ConfigError as e:
        print("❌", e)
        return 2
    if a.symbols:
        syms = [s.strip().upper() for s in a.symbols.split(",") if s.strip()]
        cfg.update(trades=syms, quotes=syms, bars=syms)
    if a.json:
        cfg["format"] = "json"
    if a.no_quotes:
        cfg["quotes"] = []
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    data = al.HERE / "data"
    data.mkdir(exist_ok=True)
    out_path, log_path = data / f"alpaca_check_{stamp}.txt", data / f"alpaca_check_{stamp}.log"
    redact = al.RedactFilter((cfg["key"], cfg["secret"]))
    tee = Tee(sys.stdout, out_path, redact)
    sys.stdout = sys.stderr = tee
    al.setup_logging((cfg["key"], cfg["secret"]), log_path, console=True)
    print(f"فحص Alpaca — {datetime.now(timezone.utc):%Y-%m-%d %H:%M:%S} UTC · المصدر {cfg['feed']} · {cfg['url']}"
          f" · التداول {cfg['trading_url']} (Paper، بلا أوامر)")
    print(f"القنوات: صفقات {cfg['trades'] or '—'} · أسعار {cfg['quotes'] or '—'} · شموع {cfg['bars'] or '—'}")
    result("الإعدادات", True, "المفاتيح من البيئة/.env · المصدر sip · حساب Paper · لا كود أوامر")
    check_network()
    acct = check_auth(cfg)
    sip = check_sip_rest(cfg, ["AAPL", "MSFT", "SPY", "TSLA", "NVDA", "AMD", "F", "SOFI"])
    clock_offset(sip if sip is not None else acct)
    status = None
    if acct is not None:
        status = run_stream(cfg, a.seconds, a.test_stream)
    else:
        result("البث", None, "تخطي: المصادقة فشلت")
    tee.flush()
    check_leaks(cfg, log_path, out_path, status)
    print("\n===== الملخص =====")
    for name, ok, _ in RESULTS:
        print(f"  {'✅' if ok else '❌' if ok is False else '⏸️'} {name}")
    print(f"\nالتقرير محفوظ: {out_path}\nالسجل: {log_path}")
    tee.flush()
    return 0 if all(ok is not False for _, ok, _ in RESULTS) else 1


if __name__ == "__main__":
    sys.exit(main())
