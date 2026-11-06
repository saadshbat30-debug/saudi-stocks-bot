"""اختبار مراحل الماسح الشامل على فاصل 5 دقائق ببيانات ياهو الحقيقية: 100 ثم 500 ثم 1,000 سهم.

python tests/live_5m_stage_check.py <مجلد_النتائج>

- العينة: الأسهم المؤهلة من قاعدة الرموز الحالية (data/symbols.json من Nasdaq Trader) مرتبة أبجديًا ثم مخلوطة
  بالبذرة 20261106 (random.Random)، والمرحلة N = أول N رمز. المراحل متداخلة (عينة 100 جزء من 500 من 1,000).
- نفس المحرك والشروط الأصلية: MACD > Signal، و50 ≤ RSI ≤ 70، وحجم آخر شمعة مغلقة > 1.5 × متوسط 20 شمعة
  مغلقة قبلها؛ AND؛ الشمعة المغلقة فقط. نفس إيقاع المحرك (دفعات 50 سهمًا، وفاصل 3 ثوانٍ).
- لا تنتقل للمرحلة التالية إذا فشلت السابقة (لم تكتمل الدورة، أو أخطاء ياهو/تحديد معدل).
- تحقق مستقل: إعادة جلب عينة بطلب مختلف (yf.Ticker.history) وحساب المؤشرات بـ pandas.
"""
import csv
import json
import os
import random
import subprocess
import sys
import time
from datetime import datetime
from zoneinfo import ZoneInfo

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from fullscan import bars as B  # noqa: E402
from fullscan import universe as U  # noqa: E402
from fullscan.engine import Engine  # noqa: E402
from fullscan.providers import RateLimited, YahooProvider  # noqa: E402
from fullscan.store import Store  # noqa: E402

NY = ZoneInfo("America/New_York")
SEED = 20261106
STAGES = (100, 500, 1000)
INTERVAL = "5m"
STAGE_TIMEOUT = 1800


def ny(t):
    return datetime.fromtimestamp(t, NY).strftime("%Y-%m-%d %H:%M:%S") if t else None


def sample(n):
    syms, info = U.load()
    pool = sorted(x["symbol"] for x in syms)
    random.Random(SEED).shuffle(pool)
    return pool[:n], info, len(pool)


class Timed(YahooProvider):
    """نفس مزود ياهو مع تسجيل زمن كل طلب وعدد مرات تحديد المعدل."""

    def __init__(self):
        self.fetch_s, self.calls, self.rate_limited, self.call_log = 0.0, 0, 0, []

    def history(self, symbols, interval):
        t = time.perf_counter()
        try:
            out = super().history(symbols, interval)
        except RateLimited as e:
            self.rate_limited += 1
            self.call_log.append({"at": time.time(), "n": len(symbols), "s": round(time.perf_counter() - t, 2), "rate_limited": str(e)[:120]})
            raise
        dt = time.perf_counter() - t
        self.fetch_s += dt
        self.calls += 1
        self.call_log.append({"at": time.time(), "n": len(symbols), "s": round(dt, 2), "errors": len(out[1])})
        return out


def run_stage(n, out_dir):
    syms, info, pool_n = sample(n)
    prov = Timed()
    e = Engine(prov, store=Store(":memory:"), universe_fn=lambda force=False: (
        [{"symbol": s, "name": "", "exchange": ""} for s in syms], {"eligible": len(syms), "source": info.get("source")}))
    compute = [0.0]
    orig_ingest = e.ingest

    def timed_ingest(*a, **kw):
        t = time.perf_counter()
        orig_ingest(*a, **kw)
        compute[0] += time.perf_counter() - t
    e.ingest = timed_ingest
    e.load_universe()
    e.configure({"interval": INTERVAL})  # الشروط الأصلية الافتراضية
    t0 = time.time()
    session = B.in_regular(t0)
    latest = B.latest_slot_start(INTERVAL, t0)
    next_close = B.slot_end(latest, INTERVAL) + 300 if latest is not None else None
    while time.time() - t0 < STAGE_TIMEOUT:
        e.step()
        if e.counts()["full_cycle_complete"]:
            break
    t1 = time.time()
    c, st = e.counts(), e.status()
    rows = []
    for s in syms:
        r = e.rows.get(s, {"status": "pending"})
        bar_t = r.get("bar_t")
        bar_end = B.slot_end(bar_t, INTERVAL) if bar_t else None
        rows.append({"symbol": s, "status": r["status"], "matched": r.get("matched", False),
                     "macd": r.get("macd"), "signal": r.get("signal"), "rsi": r.get("rsi"), "vol_x": r.get("vol_x"),
                     "last_closed_bar_start_ny": ny(bar_t), "last_closed_bar_end_ny": ny(bar_end),
                     "fetched_at_ny": ny(e.fetched.get(s)), "data_age_at_end_s": round(t1 - bar_end) if bar_end else None,
                     "evaluated_before_next_close": bool(next_close and e.fetched.get(s, 1e18) <= next_close and r["status"] == "ok"),
                     "why": r.get("why")})
    ages = sorted(x["data_age_at_end_s"] for x in rows if x["data_age_at_end_s"] is not None)
    errors_yahoo = sum(1 for x in rows if x["status"] == "error")
    res = {
        "stage": n, "interval": INTERVAL, "sample": {"method": f"sorted eligible symbols shuffled with random.Random({SEED}), first {n}",
                                                     "pool_size": pool_n, "universe_source": info.get("source"),
                                                     "universe_file_created": info.get("file_created")},
        "started_ny": ny(t0), "finished_ny": ny(t1), "regular_session_at_start": session,
        "latest_closed_5m_bar_at_start_ny": ny(latest), "next_5m_close_ny": ny(next_close),
        "requested": n, "fetched": sum(1 for s in syms if s in e.fetched), "with_bars": sum(1 for s in syms if s in e.closed),
        "evaluated_current": c["by_status"].get("ok", 0), "matched": c["matched"],
        "insufficient": c["by_status"].get("insufficient", 0), "no_trade": c["by_status"].get("no_trade", 0),
        "stale": c["by_status"].get("stale", 0), "pending": c["pending"], "yahoo_symbol_errors": errors_yahoo,
        "rate_limited_events": prov.rate_limited, "requests": prov.calls,
        "fetch_s": round(prov.fetch_s, 2), "compute_s": round(compute[0], 3),
        "wait_between_batches_s": round(prov.calls * prov.min_gap, 1), "cycle_s": round(t1 - t0, 1),
        "cycle_complete": c["full_cycle_complete"],
        "data_age_at_end_s": {"min": ages[0] if ages else None, "median": ages[len(ages) // 2] if ages else None,
                              "max": ages[-1] if ages else None},
        "evaluated_before_next_5m_close": sum(1 for x in rows if x["evaluated_before_next_close"]) if session else None,
        "reference_bar_ny": ny(st["reference_bar"]), "provider_last_error": st["provider"]["last_error"],
        "call_log": prov.call_log,
    }
    with open(os.path.join(out_dir, f"stage_{n}.json"), "w", encoding="utf-8") as f:
        json.dump({**res, "rows": rows}, f, ensure_ascii=False, indent=1)
    with open(os.path.join(out_dir, f"stage_{n}.csv"), "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)
    return res, rows


def verify(rows, k=10):
    """إعادة جلب مستقلة وحساب بـ pandas لعينة: المطابقة أولًا ثم غيرها."""
    import yfinance as yf
    from verify_fullscan_sample import ema_sma_seed, wilder_rsi
    ok = [r for r in rows if r["status"] == "ok"]
    pick = [r for r in ok if r["matched"]][:k // 2]
    pick += [r for r in ok if not r["matched"]][:k - len(pick)]
    out = []
    for r in pick:
        df = yf.Ticker(r["symbol"]).history(period="5d", interval="5m", prepost=False, auto_adjust=False).dropna(subset=["Close"])
        end = datetime.strptime(r["last_closed_bar_start_ny"], "%Y-%m-%d %H:%M:%S").replace(tzinfo=NY)
        df = df[[ts <= end and B.in_regular(ts.timestamp()) for ts in df.index]]
        c, v = df["Close"], df["Volume"]
        line = (ema_sma_seed(c, 12).reindex(c.index) - ema_sma_seed(c, 26).reindex(c.index)).dropna()
        sig = ema_sma_seed(line, 9)
        rsi = wilder_rsi(c)
        vx = v.iloc[-1] / v.iloc[-21:-1].mean()
        last_end = df.index[-1].timestamp() + 300
        out.append({"symbol": r["symbol"], "engine": {k2: r[k2] for k2 in ("macd", "signal", "rsi", "vol_x")},
                    "pandas": {"macd": float(line.iloc[-1]), "signal": float(sig.iloc[-1]), "rsi": float(rsi), "vol_x": float(vx)},
                    "diff": {"macd": abs(float(line.iloc[-1]) - r["macd"]), "signal": abs(float(sig.iloc[-1]) - r["signal"]),
                             "rsi": abs(float(rsi) - r["rsi"]), "vol_x": abs(float(vx) - r["vol_x"])},
                    "same_last_bar": str(df.index[-1].strftime("%Y-%m-%d %H:%M:%S")) == r["last_closed_bar_start_ny"],
                    "last_bar_closed_before_fetch": last_end <= datetime.strptime(r["fetched_at_ny"], "%Y-%m-%d %H:%M:%S").replace(tzinfo=NY).timestamp(),
                    "conditions_hold_independently": bool(line.iloc[-1] > sig.iloc[-1] and 50 <= rsi <= 70 and v.iloc[-1] > 1.5 * v.iloc[-21:-1].mean())
                    == r["matched"]})
    return out


def main(out_dir):
    os.makedirs(out_dir, exist_ok=True)
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    meta = {"git_commit": subprocess.run(["git", "rev-parse", "--short", "HEAD"], capture_output=True, text=True).stdout.strip(),
            "run_started_ny": ny(time.time()), "python": sys.version.split()[0]}
    try:
        import yfinance
        meta["yfinance"] = yfinance.__version__
    except Exception:
        pass
    summary = {"meta": meta, "stages": []}
    for n in STAGES:
        print(f"=== المرحلة {n} سهم ===", flush=True)
        res, rows = run_stage(n, out_dir)
        try:
            res["independent_check"] = verify(rows)
        except Exception as ex:
            res["independent_check"] = {"error": str(ex)[:200]}
        summary["stages"].append({k: v for k, v in res.items() if k != "call_log"})
        print(json.dumps({k: res[k] for k in ("stage", "fetched", "evaluated_current", "matched", "insufficient", "no_trade",
                                              "yahoo_symbol_errors", "rate_limited_events", "fetch_s", "compute_s", "cycle_s",
                                              "cycle_complete", "data_age_at_end_s", "evaluated_before_next_5m_close")}, ensure_ascii=False), flush=True)
        with open(os.path.join(out_dir, "summary.json"), "w", encoding="utf-8") as f:
            json.dump(summary, f, ensure_ascii=False, indent=1)
        failed = not res["cycle_complete"] or res["rate_limited_events"] or res["yahoo_symbol_errors"] > n * 0.05
        if failed:
            summary["stopped_after"] = n
            summary["stop_reason"] = "الدورة لم تكتمل أو أخطاء/تحديد معدل من ياهو"
            print("توقف: فشلت هذه المرحلة، لن تُنفذ التالية", flush=True)
            break
    meta["run_finished_ny"] = ny(time.time())
    with open(os.path.join(out_dir, "summary.json"), "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=1)


if __name__ == "__main__":
    main(sys.argv[1])
    os._exit(0)
