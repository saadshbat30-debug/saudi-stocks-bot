"""تشغيل حقيقي للماسح الشامل على كل قاعدة الرموز بمصدر حقيقي، وقياس التغطية والزمن والأخطاء.

python tests/live_fullscan_check.py <الفاصل> <ملف_النتائج.json> [أقصى_مدة_بالثواني]

لا بيانات مُختلقة: قاعدة الرموز من Nasdaq Trader، والشموع من المزود المختار (FULLSCAN_PROVIDER، الافتراضي ياهو).
"""
import json
import os
import sys
import time
from datetime import datetime
from zoneinfo import ZoneInfo

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from fullscan.engine import Engine  # noqa: E402
from fullscan.providers import make  # noqa: E402
from fullscan.store import Store  # noqa: E402

NY = ZoneInfo("America/New_York")


def main(interval, out, limit):
    e = Engine(make(), store=Store(os.environ.get("FULLSCAN_DB", ":memory:")))
    t0 = time.time()
    e.load_universe(force=True)
    e.configure({"interval": interval})
    R = {"interval": interval, "started_ny": datetime.now(NY).isoformat(timespec="seconds"), "universe": e.status()["universe"],
         "progress": []}
    last_write = 0
    while time.time() - t0 < limit:
        e.step()
        st = e.status()
        if time.time() - last_write > 30 or st["counts"]["full_cycle_complete"]:
            c = st["counts"]
            R["progress"].append({"t": round(time.time() - t0), "fetched": c["fetched_this_cycle"], "matched": c["matched"],
                                  "by_status": c["by_status"], "per_min": st["provider"]["symbols_per_min"],
                                  "error": st["provider"]["last_error"], "rate_limited_for": st["provider"]["rate_limited_for"]})
            R["status"] = st
            R["failures"] = e.failures(limit=40)
            R["matched"] = [{k: r.get(k) for k in ("symbol", "exchange", "close", "macd", "signal", "rsi", "vol_x", "bar_t")}
                            for r in e.changes(0)["upsert"]]
            with open(out, "w", encoding="utf-8") as f:
                json.dump(R, f, ensure_ascii=False, indent=1, default=str)
            print(json.dumps(R["progress"][-1], ensure_ascii=False), flush=True)
            last_write = time.time()
            if c["full_cycle_complete"]:
                break
    R["elapsed_s"] = round(time.time() - t0)
    with open(out, "w", encoding="utf-8") as f:
        json.dump(R, f, ensure_ascii=False, indent=1, default=str)
    print("انتهى:", R["elapsed_s"], "ث", flush=True)


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2], int(sys.argv[3]) if len(sys.argv) > 3 else 1800)
    os._exit(0)
