"""اختبار حي لمزود TradingView مقابل المصدر الحقيقي، مع مقارنة بمصدر مستقل (ياهو).

python tests/live_tradingview_check.py [ملف_النتائج.json]

يحتاج اتصالًا بـ data.tradingview.com وquery1/query2.finance.yahoo.com. النتائج أرقام فعلية تُطبع وتُحفظ.
"""
import json
import os
import sys
import time
from datetime import datetime
from zoneinfo import ZoneInfo

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from services import tradingview_provider as tvp  # noqa: E402

NY = ZoneInfo("America/New_York")
STOCKS = [("TSLA", "NMS"), ("AAPL", "NMS"), ("AMD", "NMS"), ("SOFI", "NMS"), ("PLUG", "NCM"),
          ("F", "NYQ"), ("BAC", "NYQ"), ("GME", "NYQ"), ("SPY", "PCX"), ("BRK-B", "NYQ")]
INTERVALS = ["1m", "5m", "15m", "30m", "1h", "1d", "1w", "1mo"]
R = {"started": datetime.now(NY).isoformat(timespec="seconds")}


def log(*a):
    print(*a, flush=True)


def main(out_path):
    p = tvp.Provider()
    p.hint({s: (e, False) for s, e in STOCKS})
    p.hint({"TCEHY": ("PNK", True)})
    p.start()
    t0 = time.time()
    ok = p.client.wait_connected(20)
    R["connect_seconds"] = round(time.time() - t0, 2)
    log("1) الاتصال:", ok, R["connect_seconds"], "ث")
    if not ok:
        R["error"] = p.client.last_error
        return finish(out_path)
    symbols = [s for s, _ in STOCKS] + ["TCEHY", "^GSPC", "^DJI"]
    p.watch(symbols)

    # ===== 1 + 4 + 5: التاريخ لكل فاصل ولعدة أسهم =====
    hist = {}
    for iv in INTERVALS:
        for s in symbols:
            ext = iv in ("1m", "5m", "15m", "30m", "1h") and s not in ("^GSPC", "^DJI")
            t = time.time()
            bars, meta = p.bars(s, iv, extended=ext, wait=12)
            times = [b["time"] for b in bars]
            hist[f"{s}|{iv}"] = {"bars": len(bars), "seconds": round(time.time() - t, 2), "complete": meta.get("complete"),
                                 "error": meta.get("error"), "dupes": len(times) - len(set(times)), "sorted": times == sorted(times),
                                 "first": datetime.fromtimestamp(times[0], NY).isoformat()[:16] if times else None,
                                 "last": datetime.fromtimestamp(times[-1], NY).isoformat()[:16] if times else None,
                                 "forming": meta.get("forming"), "gaps": meta.get("gaps"), "volume_scope": meta.get("volume_scope")}
        good = [s for s in symbols if hist[f"{s}|{iv}"]["bars"] > 0 and hist[f"{s}|{iv}"]["complete"]]
        log(f"   {iv:4} نجح {len(good)}/{len(symbols)}", {s: hist[f"{s}|{iv}"]["bars"] for s in symbols})
    R["history"] = hist

    # ===== 2 + 3: تحديث الشمعة الجارية في مكانها وإغلاقها وبدء التالية =====
    watch = [s for s, _ in STOCKS[:6]]
    session = "regular" if datetime.now(NY).hour * 60 + datetime.now(NY).minute in range(570, 960) else "extended"
    ext = session == "extended"
    snaps = {s: [] for s in watch}
    end = time.time() + 130
    while time.time() < end:
        for s in watch:
            bars, meta = p.bars(s, "1m", extended=ext, wait=5)
            if bars:
                b = bars[-1]
                snaps[s].append((round(time.time()), b["time"], b["close"], b["volume"], b["closed"], len(bars)))
        time.sleep(5)
    forming = {}
    for s, sn in snaps.items():
        same_bar_changes = sum(1 for a, b in zip(sn, sn[1:]) if a[1] == b[1] and (a[2], a[3]) != (b[2], b[3]))
        rolls = sum(1 for a, b in zip(sn, sn[1:]) if b[1] > a[1])
        series = p.series.get((p.resolved(s), "1m", ext))
        closed_ok = None
        if series:
            out, _ = p._export(series, "1m", ext)
            closed_ok = all(b["closed"] for b in out[:-1])
        forming[s] = {"samples": len(sn), "in_place_updates": same_bar_changes, "new_bars_opened": rolls,
                      "bar_count_growth": (sn[-1][5] - sn[0][5]) if sn else None, "older_bars_all_closed": closed_ok}
    R["forming"] = {"session": session, "per_symbol": forming}
    log("2/3) الشمعة الجارية:", session, forming)

    # ===== 6 + 7 + 8: فصل الاتصال وإعادته، استرجاع الفجوة، عدم التكرار =====
    before = {s: {b["time"]: b for b in p.bars(s, "1m", extended=ext, wait=5)[0]} for s in watch}
    dup_before = {s: p.series[(p.resolved(s), "1m", ext)].duplicates for s in watch}
    c0 = p.client.connects
    t = time.time()
    p.client.force_reconnect()
    while p.client.connects == c0 and time.time() - t < 90:
        time.sleep(0.2)
    reconnect_s = round(time.time() - t, 2)
    time.sleep(4)
    # انقطاع أطول (≥ 70 ثانية) حتى تفوت شمعة دقيقة كاملة: نوجه إعادة الاتصال لعنوان خاطئ مؤقتًا
    # فتفشل المحاولات بتأخير المكتبة المتزايد (1، 2، 4، ...)، ثم نعيد العنوان ونتحقق من استرجاع الشموع الفائتة
    # عنوان محلي يرفض الاتصال فورًا = انقطاع حقيقي تُعاد فيه المحاولة بتأخير المكتبة المتزايد
    url = p.client.url
    p.client.url = "ws://127.0.0.1:9/refused-for-test"
    t_down = time.time()
    p.client.force_reconnect()
    time.sleep(70)
    errors_seen = p.client.last_error
    connects_during_outage = p.client.connects
    p.client.url = url
    while not (p.connected() and p.client.connects > connects_during_outage) and time.time() - t_down < 200:
        time.sleep(0.2)
    outage = (t_down, time.time())
    # انتظار اكتمال إعادة التاريخ لكل الجلسات (تُفتح بالحد المشترك)
    for s in watch:
        ser = p.series.get((p.resolved(s), "1m", ext))
        if ser:
            ser.done.wait(60)
    time.sleep(2)
    gap_test, after = {}, {}
    for s in watch:
        bars, meta = p.bars(s, "1m", extended=ext, wait=10)
        times = [b["time"] for b in bars]
        after[s] = {"bars": len(bars), "dupes": len(times) - len(set(times)), "sorted": times == sorted(times),
                    "kept_all_previous": set(before[s]) <= set(times),
                    "resyncs": meta.get("resyncs"), "duplicates_ignored": meta.get("duplicates_ignored"),
                    "corrections": meta.get("corrections"), "gaps": meta.get("gaps")}
        gap_test[s] = [x for x in times if outage[0] - 60 <= x <= outage[1]]
    R["reconnect"] = {"first_reconnect_seconds": reconnect_s, "connects_total": p.client.connects,
                      "outage_seconds": round(outage[1] - outage[0], 1), "error_during_outage": errors_seen,
                      "connected_during_outage": connects_during_outage > c0 + 1,
                      "per_symbol": after, "bars_inside_outage_recovered": {s: [datetime.fromtimestamp(x, NY).strftime("%H:%M") for x in v] for s, v in gap_test.items()},
                      "duplicates_ignored_delta": {s: after[s]["duplicates_ignored"] - dup_before[s] for s in watch}}
    log("6/7/8) إعادة الاتصال:", R["reconnect"])

    # ===== 9: المقارنة بمصدر مستقل (ياهو) =====
    R["compare_yahoo"] = compare(p)
    R["status"] = p.status()
    return finish(out_path)


def compare(p):
    try:
        import yfinance as yf
    except Exception as e:
        return {"error": str(e)}
    out = {}
    for s, _ in STOCKS:
        row = {}
        for iv, yi, per in (("1d", "1d", "1mo"), ("5m", "5m", "5d"), ("1m", "1m", "2d")):
            tvb = {b["time"]: b for b in p.bars(s, iv, extended=False, wait=10)[0] if b["closed"]}
            try:
                df = yf.Ticker(s).history(period=per, interval=yi, auto_adjust=False)
            except Exception as e:
                row[iv] = {"error": str(e)[:100]}
                continue
            yb = {int(ts.timestamp()): r for ts, r in df.iterrows()}
            common = sorted(set(tvb) & set(yb))[:-1]
            if not common:
                row[iv] = {"common": 0}
                continue
            diffs = sorted(abs(tvb[t]["close"] / yb[t]["Close"] - 1) * 1e4 for t in common if yb[t]["Close"])
            vr = sorted(tvb[t]["volume"] / yb[t]["Volume"] for t in common if yb[t]["Volume"])
            row[iv] = {"common": len(common), "close_bps_median": round(diffs[len(diffs) // 2], 2), "close_bps_max": round(diffs[-1], 2),
                       "close_within_1c": sum(1 for t in common if abs(tvb[t]["close"] - yb[t]["Close"]) <= 0.011),
                       "volume_ratio_median": round(vr[len(vr) // 2], 3) if vr else None}
        q = p.quote(s)
        try:
            fi = yf.Ticker(s).fast_info
            row["quote"] = {"tv_last": q["v"].get("lp") if q else None, "yahoo_last": round(fi.last_price, 4),
                            "tv_prev_close": q["v"].get("prev_close_price") if q else None, "yahoo_prev_close": round(fi.previous_close, 4),
                            "tv_day_volume": q["v"].get("volume") if q else None, "yahoo_day_volume": fi.last_volume}
        except Exception as e:
            row["quote"] = {"error": str(e)[:100]}
        out[s] = row
        log("9) ", s, row)
    return out


def finish(path):
    R["finished"] = datetime.now(NY).isoformat(timespec="seconds")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(R, f, ensure_ascii=False, indent=1, default=str)
    log("حُفظت النتائج في", path)
    return R


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "tradingview_live_results.json")
    os._exit(0)
