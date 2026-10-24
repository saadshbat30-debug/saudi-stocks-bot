"""الاختبار التاريخي لخوارزمية الأخبار — أداة منفصلة تُشغَّل يدويًا ولا تمس المنصة.

ماذا تفعل:
1. تجلب نماذج 8-K للأيام الماضية من فهرس SEC اليومي (مجاني ورسمي) لأسهم ناسداك ونيويورك
2. من كل نموذج: وقت القبول بالثانية (توقيت نيويورك) والبنود، وعنوان البيان الصحفي المرفق (Exhibit 99.1) إن وُجد
3. تقيّم الخبر بنفس خوارزمية المنصة (news_score) — بدون أي سعر بعد لحظة النشر
4. تجلب شموع الدقيقة من ياهو (مجانًا، آخر 30 يومًا) وتقيس: حالة السهم قبل النشر (تحرك أم لا)،
   والعائد بعد 1/3/5/15/60 دقيقة من أول شمعة بعد النشر، ناقص عائد SPY في نفس الفترة
5. تحفظ النتائج في data/news_backtest.json، وتعرضها صفحة «الأخبار الذكية» في تبويب «تاريخي»

الاستخدام (في نافذة PowerShell ثانية، والمنصة تعمل أو لا تعمل):
    py backtest_news.py --days 20 --max 800

ملاحظات صادقة:
- سعر الدخول = افتتاح أول شمعة دقيقة تبدأ بعد وقت قبول النموذج (لا نستخدم الشمعة التي نُشر الخبر خلالها)
- نموذج 8-K قد يُقدَّم بعد الحدث (حتى 4 أيام عمل) أو بعد البيان الصحفي بدقائق — فهو اختبار متحفظ
- لا يشمل السبريد والعمولات والانزلاق السعري
"""
import argparse
import html
import json
import os
import re
import sys
import time
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import requests

NY = ZoneInfo("America/New_York")
HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "data", "news_backtest.json")
UA = {"User-Agent": os.environ.get("SEC_UA", "StocksPlatform personal research stocks-platform@example.com"), "Accept-Encoding": "gzip, deflate"}
HORIZONS = (1, 3, 5, 15, 60)
PRESS_ITEMS = {"1.01", "2.01", "2.02", "7.01", "8.01"}  # بنود يرافقها بيان صحفي عادةً
_IDX = re.compile(r"^(8-K)\s+(.+?)\s{2,}(\d{3,10})\s+(\d{8}|\d{4}-\d{2}-\d{2})\s+(edgar/\S+\.txt)\s*$", re.M)


class Rate:
    """حد للطلبات مشترك بين الخيوط: طلب واحد كل `gap` ثانية على الأكثر."""
    def __init__(self, gap):
        import threading
        self.gap, self.next, self.lock = gap, 0.0, threading.Lock()

    def wait(self):
        with self.lock:
            now = time.time()
            t = max(now, self.next)
            self.next = t + self.gap
        if t > now:
            time.sleep(t - now)


SEC_RATE = Rate(0.13)   # حد SEC: 10 طلبات في الثانية (نبقى تحته)
YAHOO_RATE = Rate(0.35)


def sec_get(url, tries=3):
    for k in range(tries):
        try:
            SEC_RATE.wait()
            r = requests.get(url, headers=UA, timeout=20)
            if r.status_code == 404:
                return None
            if r.ok:
                return r
        except requests.RequestException:
            pass
        time.sleep(1.5 * (k + 1))
    return None


def listed_tickers():
    """CIK ← (الرمز، البورصة) لأسهم ناسداك ونيويورك فقط."""
    r = sec_get("https://www.sec.gov/files/company_tickers_exchange.json")
    d = r.json()
    f = d["fields"]
    out = {}
    for row in d["data"]:
        x = dict(zip(f, row))
        if x.get("exchange") in ("Nasdaq", "NYSE") and x.get("ticker"):
            out.setdefault(int(x["cik"]), (x["ticker"].upper().replace(".", "-"), x["exchange"], x.get("name") or ""))
    return out


def daily_8k(day):
    """قائمة نماذج 8-K في يوم من فهرس SEC اليومي."""
    q = (day.month - 1) // 3 + 1
    r = sec_get(f"https://www.sec.gov/Archives/edgar/daily-index/{day.year}/QTR{q}/form.{day:%Y%m%d}.idx")
    if not r:
        return []
    return [{"form": m.group(1), "company": m.group(2).strip(), "cik": int(m.group(3)), "path": m.group(5)} for m in _IDX.finditer(r.text)]


def filing_details(path):
    """وقت القبول (نيويورك) والبنود واسم ملف Exhibit 99.1 من ترويسة النموذج."""
    folder = path.rsplit("/", 1)[0] + "/" + path.rsplit("/", 1)[1].replace(".txt", "").replace("-", "")
    acc = path.rsplit("/", 1)[1].replace(".txt", "")
    r = sec_get(f"https://www.sec.gov/Archives/{folder}/{acc}-index-headers.html")
    if not r:
        return None
    t = html.unescape(r.text)
    m = re.search(r"<ACCEPTANCE-DATETIME>(\d{14})", t)
    if not m:
        return None
    accepted = datetime.strptime(m.group(1), "%Y%m%d%H%M%S").replace(tzinfo=NY)
    items = re.findall(r"<ITEMS>(\d\.\d\d)", t)
    if not items:  # الترويسة القديمة تكتب أسماء البنود بدل أرقامها
        for name in re.findall(r"ITEM INFORMATION:\s*(.+)", t):
            num = next((n for key, n in _ITEM_NAMES if key in name.lower()), None)
            if num and num not in items:
                items.append(num)
    ex = None
    for doc in re.findall(r"<DOCUMENT>(.*?)</DOCUMENT>", t, re.S):
        typ = re.search(r"<TYPE>([^\s<]+)", doc)
        fn = re.search(r"<FILENAME>([^\s<]+)", doc)
        if typ and fn and typ.group(1).upper().startswith("EX-99"):
            ex = f"https://www.sec.gov/Archives/{folder}/{fn.group(1)}"
            break
    return {"accepted": accepted, "items": items, "ex99": ex, "url": f"https://www.sec.gov/Archives/{folder}/{acc}-index.htm"}


# أسماء بنود 8-K الرسمية ← أرقامها
_ITEM_NAMES = [("entry into a material definitive", "1.01"), ("termination of a material definitive", "1.02"), ("bankruptcy", "1.03"),
               ("completion of acquisition", "2.01"), ("results of operations", "2.02"), ("creation of a direct financial obligation", "2.03"),
               ("triggering events", "2.04"), ("exit or disposal", "2.05"), ("material impairments", "2.06"),
               ("notice of delisting", "3.01"), ("unregistered sales of equity", "3.02"), ("material modification", "3.03"),
               ("certifying accountant", "4.01"), ("non-reliance", "4.02"), ("changes in control", "5.01"),
               ("departure of directors", "5.02"), ("amendments to articles", "5.03"), ("submission of matters to a vote", "5.07"),
               ("regulation fd", "7.01"), ("other events", "8.01"), ("financial statements and exhibits", "9.01")]
_DATELINE = re.compile(r"\((GLOBE NEWSWIRE|BUSINESS WIRE|ACCESSWIRE|ACCESS Newswire|Newsfile)\)|/PRNewswire/|\b(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\.? \d{1,2}, 20\d\d\b", re.I)


# أسطر في رأس البيان ليست عنوانه: عنوان بريدي، هاتف، بريد، اسم ملف، اسم مسؤول ومنصبه، اسم الشركة وحده، حروف متباعدة
_NOT_HEAD = re.compile(r"\(?\d{3}\)?[ .-]\d{3}[ .-]\d{4}|@|\.(docx?|html?|pdf)\b|exhibit 99|, [A-Z]{2} \d{5}|"
                       r"^\d+ .{0,30}\b(Street|St\.|Avenue|Ave\.?|Road|Blvd|Drive|Plaza|Suite)\b|^One .{0,20}Plaza|"
                       r",\s*(EVP|SVP|CFO|CEO|President|Chief|Vice President|Treasurer)\b|^([A-Z] ){3,}|inside information|"
                       r"^[\w.,&'’ -]{2,60}?,? (Inc|Corp|Corporation|Company|Co|Ltd|plc|LLC|Bancorp|Bancshares)\.?(\s*\((NYSE|NASDAQ|Nasdaq)[^)]*\))?$|"
                       r"^[\w.,&'’ -]{2,60}\((NYSE|NASDAQ|Nasdaq)[^)]*\)$", re.I)


_CUT = re.compile(r"(?i)\b(reports?|announces?|and|of|for|the|to|with|in|on|its|a|an|&)$|[-–,]$")


def press_headline(url):
    """عنوان البيان الصحفي وأول فقرة منه من ملف Exhibit 99.1."""
    r = sec_get(url)
    if not r:
        return None, ""
    text = html.unescape(re.sub(r"<[^>]+>", "\n", re.sub(r"(?is)<(script|style).*?</\1>", "", r.text)))
    lines = [re.sub(r"\s+", " ", x).strip() for x in text.splitlines()]
    lines = [x for x in lines if x]
    head, body = None, ""
    for i, x in enumerate(lines[:60]):
        if _DATELINE.search(x):
            body = " ".join(lines[i:i + 3])[:400]
            break
        if re.match(r"(?i)^(exhibit|ex-?99|press release|for immediate release|news release|contact|investor|media)", x) or _NOT_HEAD.search(x):
            continue
        if head and len(x.split()) <= 25 and _CUT.search(head) and len(head.split()) < 12:
            head = head + " " + x  # عنوان مقسوم على سطرين: «GLOBE LIFE INC. REPORTS» + «THIRD QUARTER RESULTS»
        elif 4 <= len(x.split()) <= 40 and not head:
            head = x
    return head, body


def bars(symbol, around, cache={}):
    """شموع الدقيقة (شاملة ما قبل الافتتاح وما بعد الإغلاق) لنافذة 8 أيام حول الوقت."""
    import yfinance as yf
    key = (symbol, around.date())
    if key in cache:
        return cache[key]
    keep = symbol == "SPY"  # نحفظ شموع SPY فقط (تتكرر لكل الأخبار)؛ شموع كل سهم تُستخدم مرة ثم تُترك لتوفير الذاكرة
    start = around.date() - timedelta(days=4)
    end = around.date() + timedelta(days=4)
    try:
        YAHOO_RATE.wait()
        df = yf.Ticker(symbol).history(start=start, end=end, interval="1m", prepost=True, auto_adjust=False, timeout=15)
    except Exception:
        df = None
    out = []
    if df is not None and not df.empty:
        for ts, row in df.iterrows():
            out.append((ts.tz_convert(NY).to_pydatetime(), float(row["Open"]), float(row["Close"])))
    if keep:
        cache[key] = out
    return out


def session_of(t):
    m = t.hour * 60 + t.minute
    return "open" if 570 <= m < 960 else "pre" if 240 <= m < 570 else "post" if 960 <= m < 1200 else None


def measure(b, t0):
    """سعر الدخول وحالة السهم قبل النشر والعائد بعد كل مدة. b: [(وقت بداية الشمعة، افتتاح، إغلاق)]."""
    after = [x for x in b if x[0] > t0]
    if not after:
        return None
    entry_t, p0 = after[0][0], after[0][1]
    before = [x for x in b if x[0] <= t0]
    p5 = next((x[2] for x in reversed(before) if x[0] <= t0 - timedelta(minutes=5)), None)
    # إغلاق آخر جلسة رسمية قبل يوم الدخول
    prev_close = next((x[2] for x in reversed(b) if x[0].date() < entry_t.date() and session_of(x[0]) == "open"), None)
    out = {}
    for h in HORIZONS:
        tgt = entry_t + timedelta(minutes=h)
        nx = next((x for x in after if x[0] >= tgt), None)
        out[h] = nx[1] if nx and (nx[0] - tgt) <= timedelta(minutes=10) and nx[0].date() == entry_t.date() else None
    return {"entry_t": entry_t, "p0": p0, "pre5": (p0 / p5 - 1) * 100 if p5 else None,
            "day": (p0 / prev_close - 1) * 100 if prev_close else None, "h": out}


def main():
    ap = argparse.ArgumentParser(description="اختبار تاريخي لخوارزمية الأخبار على نماذج 8-K")
    ap.add_argument("--days", type=int, default=20, help="عدد الأيام التقويمية للخلف (حد شموع الدقيقة في ياهو 30 يومًا)")
    ap.add_argument("--max", type=int, default=800, help="أقصى عدد نماذج تُختبر")
    ap.add_argument("--neutral", type=int, default=250, help="أقصى عدد أخبار محايدة (المجموعة الضابطة)")
    ap.add_argument("--fresh", action="store_true", help="ابدأ من جديد بدل استكمال التشغيل السابق")
    ap.add_argument("--workers", type=int, default=4, help="عدد الطلبات المتوازية (2 لجهاز أخف)")
    ap.add_argument("--rescore", action="store_true", help="أعد حساب درجات نماذج 8-K المحفوظة بالخوارزمية الحالية بلا تحميل")
    a = ap.parse_args()
    sys.path.insert(0, HERE)
    if a.rescore:
        return rescore()
    import news_score
    from news_track import _state_at_arrival, POS, NEG
    from wires import ITEMS_8K

    print("📥 قائمة أسهم ناسداك ونيويورك من SEC…")
    tickers = listed_tickers()
    today = datetime.now(NY).date()
    days = [today - timedelta(days=k) for k in range(1, min(a.days, 29) + 1)]
    filings = []
    for d in days:
        if d.weekday() >= 5:
            continue
        got = [f for f in daily_8k(d) if f["cik"] in tickers]
        print(f"  {d}: {len(got)} نموذج 8-K لأسهم مدرجة")
        filings += got
    print(f"📄 المجموع {len(filings)} نموذج — تُفحص حتى {a.max}")

    # استكمال: النماذج المقاسة سابقًا لا تُعاد
    rows, done = [], set()
    try:
        if a.fresh:
            raise OSError("fresh")
        with open(OUT, encoding="utf-8") as fh:
            old = json.load(fh)
        rows = [r for r in old.get("rows", []) if r.get("path")]
        done = {r["path"] for r in rows} | set(old.get("meta", {}).get("skipped", []))
        if rows:
            print(f"↩️ استكمال: {len(rows)} خبر مُقاس من تشغيل سابق")
    except (OSError, ValueError):
        pass
    import threading
    from concurrent.futures import ThreadPoolExecutor
    lock, spy_cache = threading.Lock(), {}
    neutral = [sum(1 for r in rows if -15 < r["direction"] < 15)]
    skipped, checked, t_start = set(), [0], time.time()
    todo = [f for f in filings if f["path"] not in done]

    def work(f):
        if len(rows) >= a.max:
            return None
        det = filing_details(f["path"])
        if not det or not det["items"]:
            return "skip"
        items = [i for i in det["items"] if re.match(r"\d\.\d\d$", i)]
        sym, exch, name = tickers[f["cik"]]
        title = f"8-K: {name} — " + "; ".join(ITEMS_8K[i] for i in items if i in ITEMS_8K)
        summary, source = "بنود: " + ", ".join(items), "8-K"
        if det["ex99"] and set(items) & PRESS_ITEMS:
            head, body = press_headline(det["ex99"])
            if head:
                title, summary, source = head, body, "wire"  # البيان الصحفي المرفق: نقيّمه كخبر
        sc = news_score.score(title, summary, {"publisher": "SEC 8-K" if source == "8-K" else "GlobeNewswire",
                                               "time": det["accepted"].timestamp(), "market_chg": 0})
        d = sc["direction"]
        if -15 < d < 15:
            with lock:
                if neutral[0] >= a.neutral:
                    return "skip"
                neutral[0] += 1
        b = bars(sym, det["accepted"])
        m = measure(b, det["accepted"]) if b else None
        if not m:
            return "skip"
        day_key = m["entry_t"].date()
        with lock:
            sp = spy_cache.get(day_key)
        if sp is None:
            sp = bars("SPY", det["accepted"])
            with lock:
                spy_cache[day_key] = sp
        ms = measure(sp, det["accepted"]) if sp else None
        spy_day = ms["day"] if ms else 0.0
        day_rel = m["day"] - (spy_day or 0) if m["day"] is not None else None
        out = {}
        for h in HORIZONS:
            ph = m["h"][h]
            if ph is None:
                out[str(h)] = None
                continue
            ret = (ph / m["p0"] - 1) * 100
            sret = ((ms["h"][h] / ms["p0"] - 1) * 100) if ms and ms["h"][h] else 0.0
            out[str(h)] = {"ret": round(ret, 3), "excess": round(ret - sret, 3)}
        return {"id": det["url"], "path": f["path"], "symbol": sym, "title": title[:160], "source": source, "items": items,
                "direction": d, "importance": sc["importance"], "confidence": sc["confidence"], "event": sc["event"],
                "pre5m": round(m["pre5"], 2) if m["pre5"] is not None else None, "day_rel": round(day_rel, 2) if day_rel is not None else None,
                "session": session_of(m["entry_t"]),
                "state": _state_at_arrival(d, m["pre5"], day_rel) if abs(d) >= 15 else "neutral",
                "published": int(det["accepted"].timestamp()), "t0": int(m["entry_t"].timestamp()), "p0": m["p0"],
                "delay_s": int((m["entry_t"] - det["accepted"]).total_seconds()), "out": out, "url": det["url"]}

    def report():
        pos = sum(1 for r in rows if r["direction"] >= POS)
        negs = sum(1 for r in rows if r["direction"] <= NEG)
        mins = (time.time() - t_start) / 60
        print(f"  … فُحص {checked[0]}/{len(todo)} نموذج · مُقاس {len(rows)} خبر (إيجابي {pos}، سلبي {negs}، محايد {neutral[0]}) · {mins:.1f} دقيقة", flush=True)

    print(f"⏳ يبدأ الفحص ({len(todo)} نموذج متبقٍ) — يظهر التقدم كل 10 نماذج، و Ctrl+C يحفظ ويتوقف", flush=True)
    pool = ThreadPoolExecutor(max_workers=max(1, min(a.workers, 6)))
    try:
        futs = {pool.submit(work, f): f for f in todo}
        from concurrent.futures import as_completed
        for fut in as_completed(futs):
            try:
                res = fut.result()
            except Exception as e:
                res = "skip"
                print(f"  ⚠️ {futs[fut]['company'][:40]}: {str(e)[:80]}")
            with lock:
                checked[0] += 1
                if isinstance(res, dict):
                    rows.append(res)
                elif res == "skip":
                    skipped.add(futs[fut]["path"])
            if checked[0] % 10 == 0:
                report()
            if checked[0] % 50 == 0:
                save(rows, a, skipped | (done - {r["path"] for r in rows}))
            if len(rows) >= a.max:
                print(f"🏁 وصل الحد ({a.max} خبر)")
                break
    except KeyboardInterrupt:
        print("\n⏹️ أُوقف — يُحفظ ما تم…", flush=True)
        pool.shutdown(wait=False, cancel_futures=True)
        save(rows, a, skipped | (done - {r["path"] for r in rows}))
        print(f"💾 حُفظ {len(rows)} خبر. شغّل نفس الأمر لاحقًا ليكمل من حيث توقف.")
        os._exit(0)
    pool.shutdown(wait=False, cancel_futures=True)
    save(rows, a, skipped | (done - {r["path"] for r in rows}))
    report()
    pos = sum(1 for r in rows if r["direction"] >= POS)
    negs = sum(1 for r in rows if r["direction"] <= NEG)
    print(f"✅ تم: {len(rows)} خبر ({pos} إيجابي، {negs} سلبي، {neutral[0]} محايد) — النتائج في صفحة «الأخبار الذكية» ← تبويب «📚 تاريخي (8-K)»")


def rescore():
    """يعيد تقييم الأخبار المحفوظة بعد تعديل الخوارزمية (الأسعار والعوائد المقاسة لا تتغير).
    البيانات الصحفية (wire) يُعاد جلب عنوانها وفقرتها الأولى من SEC لأن النص لم يُحفظ."""
    import news_score
    from news_track import _state_at_arrival, POS, NEG
    with open(OUT, encoding="utf-8") as fh:
        d = json.load(fh)
    changed, rows = 0, d.get("rows", [])
    wire = [r for r in rows if r.get("source") == "wire"]
    print(f"🔁 إعادة جلب عناوين {len(wire)} بيان صحفي من SEC (دقيقة إلى دقيقتين)…", flush=True)
    for k, r in enumerate(rows):
        if r.get("source") == "wire":
            det = filing_details(r["path"]) if r.get("path") else None
            head, body = press_headline(det["ex99"]) if det and det.get("ex99") else (None, "")
            if head:
                r["title"] = head[:160]
                sc = news_score.score(head, body, {"publisher": "GlobeNewswire", "time": r.get("published"), "market_chg": 0})
            else:  # لم يُستخرج عنوان صالح: يُقيَّم كنموذج 8-K عادي
                from wires import ITEMS_8K
                r["title"], r["source"] = "8-K: " + "; ".join(ITEMS_8K[i] for i in r.get("items", []) if i in ITEMS_8K), "8-K"
                sc = news_score.score(r["title"], "بنود: " + ", ".join(r.get("items", [])), {"publisher": "SEC 8-K", "time": r.get("published"), "market_chg": 0})
            if (k + 1) % 25 == 0:
                print(f"  … {k + 1}/{len(rows)}", flush=True)
        else:
            r["title"] = r["title"].replace(" (dilution)", "")
            sc = news_score.score(r["title"], "بنود: " + ", ".join(r.get("items", [])),
                                  {"publisher": "SEC 8-K", "time": r.get("published"), "market_chg": 0})
        if sc["direction"] != r["direction"]:
            changed += 1
        r.update(direction=sc["direction"], importance=sc["importance"], confidence=sc["confidence"], event=sc["event"],
                 state=_state_at_arrival(sc["direction"], r.get("pre5m"), r.get("day_rel")) if abs(sc["direction"]) >= 15 else "neutral")
    d.setdefault("meta", {})["rescored_at"] = int(time.time())
    tmp = OUT + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(d, f, ensure_ascii=False)
    os.replace(tmp, OUT)
    rows = d["rows"]
    print(f"✅ أُعيد التقييم: تغيّرت درجة {changed} خبر · إيجابي {sum(1 for r in rows if r['direction'] >= POS)}، "
          f"سلبي {sum(1 for r in rows if r['direction'] <= NEG)}، محايد {sum(1 for r in rows if -15 < r['direction'] < 15)}")


def save(rows, a, skipped=()):
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    tmp = OUT + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump({"rows": list(rows), "meta": {"at": int(time.time()), "days": a.days, "source": "SEC 8-K + Yahoo 1m",
                                                "count": len(rows), "skipped": sorted(skipped)}}, f, ensure_ascii=False)
    os.replace(tmp, OUT)


if __name__ == "__main__":
    main()
