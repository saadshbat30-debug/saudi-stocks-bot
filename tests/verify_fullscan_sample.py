"""تحقق مستقل من عينة نتائج الماسح الشامل: يعيد جلب الشموع بطلب مختلف (yf.Ticker.history) ويحسب المؤشرات
بتنفيذ مستقل بـ pandas، ثم يقارن بقيم الملف ويتحقق من الشروط.

python tests/verify_fullscan_sample.py <ملف_النتائج.json> [حجم_العينة]
"""
import json
import sys
from datetime import datetime
from zoneinfo import ZoneInfo

import pandas as pd
import yfinance as yf

NY = ZoneInfo("America/New_York")


def ema_sma_seed(s, n):
    """EMA تبدأ بمتوسط بسيط لأول n قيمة (بـ pandas ewm بعد وضع البذرة)."""
    seed = s.iloc[:n].mean()
    x = pd.concat([pd.Series([seed], index=[s.index[n - 1]]), s.iloc[n:]])
    return x.ewm(span=n, adjust=False).mean()


def wilder_rsi(c, n=14):
    d = c.diff().dropna()
    g, l = d.clip(lower=0), (-d).clip(lower=0)
    ag = pd.concat([pd.Series([g.iloc[:n].mean()], index=[g.index[n - 1]]), g.iloc[n:]]).ewm(alpha=1 / n, adjust=False).mean()
    al = pd.concat([pd.Series([l.iloc[:n].mean()], index=[l.index[n - 1]]), l.iloc[n:]]).ewm(alpha=1 / n, adjust=False).mean()
    return 100 - 100 / (1 + ag.iloc[-1] / al.iloc[-1]) if al.iloc[-1] else 100.0


def main(path, k):
    d = json.load(open(path, encoding="utf-8"))
    cfg = d["status"]["cfg"]
    rows = d["matched"][:k]
    out, bad = [], 0
    for r in rows:
        df = yf.Ticker(r["symbol"]).history(period="1y", interval="1d", auto_adjust=False)
        df = df[[ts.date() <= datetime.fromtimestamp(r["bar_t"], NY).date() for ts in df.index]].dropna(subset=["Close"])
        c, v = df["Close"], df["Volume"]
        line = (ema_sma_seed(c, 12).reindex(c.index) - ema_sma_seed(c, 26).reindex(c.index)).dropna()  # يبدأ عند الشمعة 26
        sig = ema_sma_seed(line, 9)
        rsi = wilder_rsi(c)
        n = cfg["vol_period"]
        vx = v.iloc[-1] / v.iloc[-(n + 1):-1].mean()
        ok = line.iloc[-1] > sig.iloc[-1] and cfg["rsi_min"] <= rsi <= cfg["rsi_max"] and v.iloc[-1] > cfg["vol_mult"] * v.iloc[-(n + 1):-1].mean()
        diff = {"macd": abs(line.iloc[-1] - r["macd"]), "signal": abs(sig.iloc[-1] - r["signal"]), "rsi": abs(rsi - r["rsi"]),
                "vol_x": abs(vx - r["vol_x"])}
        close_ok = all(x < 1e-6 * max(1, abs(r["close"])) for x in (diff["macd"], diff["signal"])) and diff["rsi"] < 0.01 and diff["vol_x"] < 0.01
        bad += not (ok and close_ok)
        out.append({"symbol": r["symbol"], "bar_date": str(df.index[-1].date()), "file": {k2: r[k2] for k2 in ("macd", "signal", "rsi", "vol_x")},
                    "independent": {"macd": float(line.iloc[-1]), "signal": float(sig.iloc[-1]), "rsi": float(rsi), "vol_x": float(vx)},
                    "max_abs_diff": {k2: float(x) for k2, x in diff.items()}, "conditions_hold": bool(ok), "values_match": bool(close_ok)})
        print(f"{r['symbol']:6} {out[-1]['bar_date']}  شروط={ok}  تطابق القيم={close_ok}  فروق: MACD {diff['macd']:.2e} Signal {diff['signal']:.2e} RSI {diff['rsi']:.4f} Vol× {diff['vol_x']:.4f}", flush=True)
    print(f"العينة {len(out)}: صحيحة {len(out) - bad}، مختلفة {bad}")
    return out


if __name__ == "__main__":
    res = main(sys.argv[1], int(sys.argv[2]) if len(sys.argv) > 2 else 25)
    if len(sys.argv) > 3:
        json.dump(res, open(sys.argv[3], "w", encoding="utf-8"), ensure_ascii=False, indent=1)
