"""فحص المنصة وهي تعمل: كل قسم، زمن الرد، ومصدر البيانات، وسبب الثقل إن وُجد.

شغّل المنصة (py app.py) واتركها، ثم في نافذة ثانية:
    py platform_check.py
يقرأ من http://127.0.0.1:5000 فقط (لا يتصل بـ Alpaca ولا يقرأ المفاتيح) ويحفظ النتيجة في data/platform_check.json.
"""
import json
import os
import sys
import time
from collections import Counter
from pathlib import Path

import requests

BASE = "http://127.0.0.1:5000"
SYMBOL = sys.argv[1].upper() if len(sys.argv) > 1 else "TSLA"
SLOW = 3.0  # ثوانٍ: رد أبطأ من هذا يُعلَّم بطيئًا
report, problems = {}, []


def get(path, timeout=90):
    t = time.time()
    r = requests.get(BASE + path, timeout=timeout)
    dt = time.time() - t
    try:
        data = r.json()
    except ValueError:
        data = None
    return data, dt, r.status_code, len(r.content)


def check(name, path, summarize, timeout=90):
    try:
        data, dt, code, size = get(path, timeout)
    except requests.RequestException as e:
        report[name] = {"error": type(e).__name__}
        problems.append(f"{name}: لا رد ({type(e).__name__})")
        print(f"❌ {name}: لا رد ({type(e).__name__})")
        return None
    info = {"seconds": round(dt, 2), "http": code, "kb": round(size / 1024)}
    try:
        info.update(summarize(data) if code == 200 and data is not None else {"body": str(data)[:200]})
    except Exception as e:
        info["summary_error"] = f"{type(e).__name__}: {e}"[:160]
    report[name] = info
    flag = "❌" if code != 200 else "🐢" if dt > SLOW else "✅"
    if code != 200:
        problems.append(f"{name}: HTTP {code}")
    elif dt > SLOW:
        problems.append(f"{name}: بطيء {dt:.1f} ث")
    extra = " · ".join(f"{k}={v}" for k, v in info.items() if k not in ("seconds", "http", "kb"))
    print(f"{flag} {name} — {dt:.1f} ث، {info['kb']} KB · {extra}"[:600])
    return data


def alpaca(d):
    lat = (d.get("latency") or {}).get("trades") or {}
    h = d.get("daily_history") or {}
    out = {"state": d.get("state"), "feed": d.get("feed"), "lag_p50_ms": lat.get("p50_ms"), "lag_p95_ms": lat.get("p95_ms"),
           "msgs_s": d.get("msgs_per_s_10s"), "rx_kb_s": d.get("rx_kb_s"), "cpu_busy_pct": d.get("cpu_busy_pct"),
           "reconnects": d.get("reconnects"), "gaps": len(d.get("gaps") or []), "watched": d.get("watched"),
           "tape_watched": d.get("tape_watched"), "symbols": d.get("symbols"), "last_data_age_s": d.get("last_data_age_s"),
           "history": f"{h.get('with_history')}/{h.get('symbols')} running={h.get('running')} error={h.get('error')}",
           "last_errors": [e.get("msg", "")[:80] for e in (d.get("errors") or [])[-3:]]}
    if d.get("state") != "streaming":
        problems.append(f"بث Alpaca ليس streaming ({d.get('state')})")
    if lat.get("p50_ms") and lat["p50_ms"] > 2000:
        problems.append(f"تأخير صفقات Alpaca {lat['p50_ms'] / 1000:.1f} ث (الوسيط)")
    if d.get("cpu_busy_pct") and d["cpu_busy_pct"] > 80:
        problems.append(f"خيط بث Alpaca مشغول {d['cpu_busy_pct']}% (المعالج هو الحد)")
    return out


def status(d):
    return {"screener_age_s": d.get("screener_age"), "screener_error": (d.get("screener_error") or "")[:100] or None,
            "company_info": d.get("company_info")}


def market(rows):
    src = Counter(r.get("src") for r in rows)
    liq = Counter(r.get("liq_src") for r in rows if not r.get("otc"))
    mom = Counter(r.get("mom_src") for r in rows if not r.get("otc"))
    listed = [r for r in rows if not r.get("otc")]
    return {"rows": len(rows), "listed": len(listed), "src": dict(src), "liq_src_listed": dict(liq), "mom_src_listed": dict(mom),
            "live": sum(1 for r in rows if r.get("live")), "no_rsi_listed": sum(1 for r in listed if r.get("rsi") is None),
            "no_vol_ratio_listed": sum(1 for r in listed if r.get("vol_ratio") is None)}


def quote(d):
    q = d.get(SYMBOL) or {}
    return {k: q.get(k) for k in ("price", "change_pct", "src", "live", "volume", "sma20", "sma50", "high52", "low52", "rsi", "liq_src", "mom_src")}


def chart(bars):
    now = time.time() + 3 * 3600
    return {"bars": len(bars), "last_bar_age_s": round(now - bars[-1]["time"]) if bars else None}


def chart_meta(d):
    return {"src": (d or {}).get("src"), "alpaca_error": (d or {}).get("alpaca_error")}


def radar(d):
    return {"source": d.get("source"), "count": d.get("count"), "matched": d.get("matched"), "funnel": d.get("funnel"),
            "dropped": d.get("dropped"), "error": d.get("error")}


def breakout(d):
    return {"in_session": d.get("in_session"), "scan_age_s": d.get("scan_age"), "candidates": len(d.get("candidates") or []),
            "coverage": d.get("coverage"), "signals": len(d.get("signals") or [])}


def scanner(d):
    if isinstance(d, dict):
        return {k: (len(v) if isinstance(v, list) else v) for k, v in d.items() if not isinstance(v, dict)}
    return {"items": len(d)}


def options(d):
    rows = (d.get("calls") or []) + (d.get("puts") or [])
    return {"source": d.get("source"), "date": d.get("date"), "contracts": len(rows),
            "iv_missing": sum(1 for r in rows if r.get("iv") is None), "iv_calc": sum(1 for r in rows if r.get("iv_calc")),
            "error": d.get("error")}


def news(items):
    return {"items": len(items), "newest": (items[0].get("time") if items else None), "publishers": dict(Counter(i.get("publisher") for i in items).most_common(3))}


def trades(t):
    return {"trades": len(t), "newest_age_s": round(time.time() - t[0]["time"], 1) if t and isinstance(t[0].get("time"), (int, float)) else None}


def fullscan(d):
    p = d.get("provider") or {}
    return {"provider": p.get("name"), "running": d.get("running"), "last_error": p.get("last_error"), "counts": d.get("counts")}


def liquidity(d):
    w = (d.get("windows") or {}).get("60") or {}
    return {"trades_60s": w.get("trade_count"), "buy_pct": w.get("buy_pct"), "nbbo_coverage_pct": w.get("nbbo_coverage_pct"),
            "quote": d.get("quote"), "last_trade_age_ms": (d.get("last_trade") or {}).get("age_ms"), "error": d.get("error")}


def main():
    print(f"فحص المنصة على {BASE} · السهم {SYMBOL} · {time.strftime('%Y-%m-%d %H:%M:%S')}\n")
    try:
        requests.get(BASE + "/api/status", timeout=10)
    except requests.RequestException:
        print("❌ المنصة لا ترد على 127.0.0.1:5000 — شغّلها أولًا بـ py app.py واتركها مفتوحة")
        return 1
    check("Alpaca (البث)", "/api/alpaca/status", alpaca)
    check("حالة المنصة", "/api/status", status)
    check("الجدول الرئيسي", "/api/market/us", market)
    check("صفحة السهم", f"/api/quotes?symbols={SYMBOL}", quote)
    check("الشارت 5 دقائق", f"/api/chart/{SYMBOL}?interval=5m", chart)
    check("مصدر الشارت", f"/api/chart_meta/{SYMBOL}?interval=5m", chart_meta)
    check("الشارت اليومي", f"/api/chart/{SYMBOL}?interval=1d", chart)
    check("شريط الصفقات", f"/api/trades/{SYMBOL}", trades)
    check("الأخبار", f"/api/news/{SYMBOL}", news)
    check("المؤشرات", "/api/indices", lambda d: {"items": len(d)})
    check("رادار الزخم", "/api/momentum-radar", radar)
    check("قبل الانطلاق", "/api/breakout", breakout)
    check("الزخم والماكد", "/api/scanner?interval=5m", scanner, timeout=180)
    check("العقود", f"/api/options/{SYMBOL}", options)
    check("الماسح الشامل", "/api/fullscan/status", fullscan)
    check("السيولة اللحظية (بدء)", f"/api/liquidity/{SYMBOL}", liquidity)
    print("… انتظار 60 ثانية لتمتلئ نافذة الدقيقة في السيولة اللحظية")
    time.sleep(int(os.environ.get("CHECK_WAIT", 60)))
    check("السيولة اللحظية (بعد دقيقة)", f"/api/liquidity/{SYMBOL}", liquidity)
    check("Alpaca (بعد دقيقة)", "/api/alpaca/status", alpaca)

    print("\n===== الخلاصة =====")
    print("\n".join(f"⚠️ {p}" for p in dict.fromkeys(problems)) or "لا مشكلات")
    out = Path(__file__).resolve().parent / "data" / "platform_check.json"
    out.parent.mkdir(exist_ok=True)
    out.write_text(json.dumps({"symbol": SYMBOL, "at": time.time(), "report": report, "problems": list(dict.fromkeys(problems))},
                              ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"\nالتقرير الكامل: {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
