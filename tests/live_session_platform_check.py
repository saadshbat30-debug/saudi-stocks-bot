"""اختبار الجلسة الرسمية على مسار المنصة نفسه: stream.py (بمصدر TradingView) ← مقاييس فلتر «قبل الانطلاق».

python tests/live_session_platform_check.py <ملف_النتائج.json>

يقيس على أسهم نشطة: التأخير (وقت الاستلام − وقت الصفقة)، وحجم اليوم والسعر مقابل ياهو مرتين بفارق 60 ثانية،
ونمو شريط الصفقات وسجل الجلسة، ومقاييس فلتر قبل الانطلاق بعد 3 دقائق.
"""
import json
import os
import sys
import time
from datetime import datetime
from zoneinfo import ZoneInfo

os.environ["DATA_SOURCE"] = "tradingview"
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import breakout  # noqa: E402
import stream  # noqa: E402

NY = ZoneInfo("America/New_York")
SYMS = ["TSLA", "NVDA", "AAPL", "AMD", "PLTR", "SOFI", "F", "BAC", "INTC", "AMZN", "META", "MSFT", "LCID", "NIO",
        "RIVN", "MARA", "RIOT", "SNAP", "PLUG", "AAL", "CCL", "GOOGL", "T", "PFE", "UBER", "HOOD", "COIN", "SMCI",
        "MU", "WBD", "NU", "GRAB", "SOUN", "RKLB", "ACHR", "JOBY", "OPEN", "CLSK", "BBAI", "IONQ"]
EXCH = {"F": "NYQ", "BAC": "NYQ", "T": "NYQ", "PFE": "NYQ", "UBER": "NYQ", "NIO": "NYQ", "CCL": "NYQ", "SNAP": "NYQ",
        "WBD": "NMS", "NU": "NYQ", "ACHR": "NYQ", "JOBY": "NYQ"}


def yahoo(symbols):
    import yfinance as yf
    out = {}
    for s in symbols:
        try:
            fi = yf.Ticker(s).fast_info
            out[s] = {"price": fi.last_price, "volume": fi.last_volume}
        except Exception as e:
            out[s] = {"error": str(e)[:80]}
    return out


def snap():
    p = stream.tv()
    out = {}
    for s in SYMS:
        q = p.quote(s)
        v = (q or {}).get("v", {})
        out[s] = {"price": v.get("lp"), "volume": v.get("volume"), "session": v.get("current_session"),
                  "update_mode": v.get("update_mode"), "tape": len(stream.tape(s, 300)), "hist": len(stream.history(s))}
    return out


def main(path):
    R = {"started": datetime.now(NY).isoformat(timespec="seconds"), "symbols": SYMS}
    p = stream.tv()
    p.client.wait_connected(20)
    stream.hint({s: (EXCH.get(s, "NMS"), False) for s in SYMS})
    stream.watch(SYMS)
    time.sleep(15)
    t1, y1 = snap(), yahoo(SYMS)
    time.sleep(60)
    t2, y2 = snap(), yahoo(SYMS)
    rows = {}
    for s in SYMS:
        a, b, ya, yb = t1[s], t2[s], y1[s], y2[s]
        rows[s] = {
            "tv_volume": [a["volume"], b["volume"]], "yahoo_volume": [ya.get("volume"), yb.get("volume")],
            "tv_volume_growth_60s": (b["volume"] or 0) - (a["volume"] or 0),
            "yahoo_volume_growth_60s": (yb.get("volume") or 0) - (ya.get("volume") or 0),
            "volume_ratio_tv_over_yahoo": round(b["volume"] / yb["volume"], 4) if b["volume"] and yb.get("volume") else None,
            "price_tv": b["price"], "price_yahoo": round(yb["price"], 4) if yb.get("price") else None,
            "price_diff_bps": round(abs(b["price"] / yb["price"] - 1) * 1e4, 1) if b["price"] and yb.get("price") else None,
            "session": b["session"], "update_mode": b["update_mode"], "tape_entries": b["tape"],
        }
    R["volume_price"] = rows
    print("حجم اليوم والسعر مقابل ياهو:", flush=True)
    for s, r in rows.items():
        print(f"  {s:5} TV/Yahoo={r['volume_ratio_tv_over_yahoo']}  نمو60ث TV={r['tv_volume_growth_60s']:,} Yahoo={r['yahoo_volume_growth_60s']:,}  فرق السعر={r['price_diff_bps']} نقطة أساس  {r['update_mode']}", flush=True)
    # مقاييس فلتر قبل الانطلاق تحتاج 3 دقائق من سجل الجلسة
    time.sleep(max(0, 200 - 75))
    now = time.time()
    met = {}
    for s in SYMS:
        m = breakout.metrics(s, None, now)
        met[s] = {k: m.get(k) for k in ("acc1m", "chg3m", "rng3m", "pos3m", "val3m", "vol1m", "age")} if m else None
    R["breakout_metrics"] = met
    R["breakout_metrics_ok"] = sum(1 for v in met.values() if v)
    st = stream.status()
    R["status"] = {k: st.get(k) for k in ("state", "watching", "symbols", "ticking_1m", "last_tick_age", "latency_median_s",
                                          "latency_p90_s", "latency_samples", "rate_limit_errors", "reconnects", "delayed_symbols")}
    print("مقاييس قبل الانطلاق متاحة لـ", R["breakout_metrics_ok"], "من", len(SYMS), flush=True)
    print("الحالة:", R["status"], flush=True)
    R["finished"] = datetime.now(NY).isoformat(timespec="seconds")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(R, f, ensure_ascii=False, indent=1, default=str)
    print("حُفظت النتائج في", path, flush=True)


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "session_platform_results.json")
    os._exit(0)
