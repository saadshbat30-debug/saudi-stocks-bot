"""مؤشرات الماسح الشامل وشروطه.

MACD(12,26,9) وRSI(14): نفس المعادلات في نافذة المؤشرات أسفل الشارت (static/indicators.js) وفي scanner.py
(EMA تبدأ بمتوسط بسيط لأول n قيمة، وRSI بطريقة Wilder). الحجم: حجم آخر شمعة مغلقة ÷ متوسط الشموع N السابقة
لها دون شمعة التقييم نفسها.
"""
from scanner import macd, rsi_series_last

DEFAULTS = {"macd_on": True, "rsi_on": True, "rsi_min": 50.0, "rsi_max": 70.0,
            "vol_on": True, "vol_mult": 1.5, "vol_period": 20}
MIN_BARS_MACD = 35  # 26 للمتوسط البطيء + 9 لخط الإشارة


def needed_bars(cfg):
    return max(MIN_BARS_MACD, 15, int(cfg.get("vol_period", 20)) + 1)


def compute(closed, cfg):
    """قيم آخر شمعة مغلقة من قائمة شموع {"t","c","v",...}، أو (None، السبب)."""
    return compute_series([b["t"] for b in closed], [b["c"] for b in closed],
                          [b["v"] for b in closed], cfg)


def compute_series(ts, closes, vols, cfg):
    """نفس compute على مصفوفات الوقت والإغلاق والحجم (الحجم المفقود None أو NaN)."""
    n = needed_bars(cfg)
    if len(closes) < n:
        return None, f"شموع مغلقة أقل من اللازم ({len(closes)} من {n})"
    if any(c is None or c != c for c in closes):
        return None, "سعر إغلاق ناقص في الشموع"
    line, sig, _ = macd(list(closes))
    period = int(cfg.get("vol_period", 20))
    last = [None if v is None or v != v else v for v in vols[-(period + 1):]]
    vol, prev = last[-1], last[:-1]
    vol_avg = sum(prev) / period if None not in last else None
    out = {
        "macd": line[-1], "signal": sig[-1], "rsi": rsi_series_last(closes),
        "vol": vol, "vol_avg": vol_avg,
        "vol_x": (vol / vol_avg) if vol is not None and vol_avg else None,
        "close": closes[-1], "bar_t": ts[-1], "bars": len(closes),
    }
    return out, None


def evaluate(ind, cfg):
    """True = يطابق كل الشروط المفعلة (AND)، False = لا يطابق، None = بيانات شرط مفعل غير متاحة."""
    checks = []
    if cfg.get("macd_on"):
        checks.append(None if ind["macd"] is None or ind["signal"] is None else ind["macd"] > ind["signal"])
    if cfg.get("rsi_on"):
        checks.append(None if ind["rsi"] is None else cfg["rsi_min"] <= ind["rsi"] <= cfg["rsi_max"])
    if cfg.get("vol_on"):
        # أكبر تمامًا من المضاعف × المتوسط (وليس أكبر أو يساوي)
        checks.append(None if ind["vol"] is None or not ind["vol_avg"] else ind["vol"] > cfg["vol_mult"] * ind["vol_avg"])
    if not checks:
        return None
    if None in checks:
        return None
    return all(checks)


def clean_cfg(raw):
    cfg = dict(DEFAULTS)
    for k in ("macd_on", "rsi_on", "vol_on"):
        if k in raw:
            cfg[k] = bool(raw[k])
    for k, lo, hi in (("rsi_min", 0, 100), ("rsi_max", 0, 100), ("vol_mult", 0.1, 100)):
        if k in raw:
            try:
                cfg[k] = min(hi, max(lo, float(raw[k])))
            except (TypeError, ValueError):
                pass
    if "vol_period" in raw:
        try:
            cfg["vol_period"] = min(200, max(2, int(raw["vol_period"])))
        except (TypeError, ValueError):
            pass
    if cfg["rsi_min"] > cfg["rsi_max"]:
        cfg["rsi_min"], cfg["rsi_max"] = cfg["rsi_max"], cfg["rsi_min"]
    return cfg
