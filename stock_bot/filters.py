# -*- coding: utf-8 -*-
"""
filters.py — الفلاتر الذكية لتحليل الأسهم
==========================================

هذا الملف يحتوي على:
  1) دوال مساعدة لتحويل القيم النصية القادمة من CSV إلى أرقام/إشارات.
  2) الفلاتر الـ 12، كل فلتر دالة مستقلة ترجع (النص, النقاط):
       النقاط = +1 للفلتر الإيجابي (شراء) ، -1 للسلبي (بيع) ، 0 للمحايد/المعلوماتي.
  3) قائمة FILTERS التي تجمع الفلاتر بالترتيب — لإضافة فلتر جديد:
       - اكتب دالة بنفس الشكل:  def my_filter(d, cfg): return "النص", 0
       - أضفها إلى القائمة FILTERS في أسفل الملف. انتهى!

المدخل d: قاموس فيه قيم السهم بعد التنظيف، مثل:
    {"price": 27.5, "ma_10": 27.1, "rsi": 55.2, "net_liquidity": 820000, ...}
    أي قيمة غير متوفرة تكون None.
المدخل cfg: قسم filters من config.yaml.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from datetime import datetime, time as dtime
from typing import Any, Callable, Optional

# ─────────────────────────────────────────────
# رموز التصنيف (تُستخدم في التلوين في Excel ولوحة الويب)
# ─────────────────────────────────────────────
BUY_MARKS = ("🟢", "✅", "🚀")
SELL_MARKS = ("🔴", "❌")
WARN_MARKS = ("⚠️",)
NO_DATA = "— لا توجد بيانات"

# تحويل الأرقام العربية الهندية والفواصل العربية إلى أرقام إنجليزية
_AR_DIGITS = str.maketrans("٠١٢٣٤٥٦٧٨٩٫٬−", "0123456789.,-")
# الرموز غير المرئية (اتجاه النص) التي قد تأتي مع ملفات Windows العربية
_INVISIBLE = ("‏", "‎", "‪", "‫", "‬", "﻿", "\xa0")


# ═════════════════════════════════════════════
#  دوال مساعدة للتحويل
# ═════════════════════════════════════════════
def clean_text(value: Any) -> str:
    """إزالة الرموز غير المرئية والمسافات من النص."""
    text = "" if value is None else str(value)
    for ch in _INVISIBLE:
        text = text.replace(ch, "")
    return text.strip()


def is_missing(value: Any) -> bool:
    """هل القيمة فارغة؟ (None أو NaN أو نص فارغ)"""
    if value is None:
        return True
    if isinstance(value, float) and math.isnan(value):
        return True
    return clean_text(value).lower() in {"", "nan", "none", "null", "n/a", "-", "--"}


def to_number(value: Any) -> Optional[float]:
    """
    تحويل أي قيمة إلى رقم عشري.
    يدعم: "1,234.5" ، "12.5%" ، "(500)" سالب ، "١٢٣" ، "1.5M" ، "250K".
    يرجع None إذا لم تكن القيمة رقمًا.
    """
    if isinstance(value, bool):
        return float(value)
    if isinstance(value, (int, float)):
        return None if (isinstance(value, float) and math.isnan(value)) else float(value)
    if is_missing(value):
        return None

    text = clean_text(value).translate(_AR_DIGITS)
    negative = False
    if text.startswith("(") and text.endswith(")"):        # الصيغة المحاسبية للسالب
        negative, text = True, text[1:-1]
    text = text.replace(",", "").replace("%", "").replace(" ", "")

    multiplier = 1.0
    suffix = text[-1:].upper()
    if suffix in ("K", "M", "B"):                          # اختصارات الآلاف والملايين
        multiplier = {"K": 1e3, "M": 1e6, "B": 1e9}[suffix]
        text = text[:-1]

    try:
        number = float(text) * multiplier
    except ValueError:
        return None
    return -number if negative else number


# كلمات تدل على إشارة سلبية / إيجابية في ملف الاختراقات
# (تُفحص السلبية أولًا، لأن "اختراق هابط" يجب أن يكون سلبيًا)
NEGATIVE_WORDS = ("هابط", "سلبي", "كسر", "تحت", "بيع", "نزول", "down", "bear", "sell", "↓", "▼")
POSITIVE_WORDS = ("صاعد", "إيجابي", "ايجابي", "اختراق", "فوق", "شراء", "صعود", "نعم",
                  "true", "yes", "up", "bull", "buy", "✓", "✔", "↑", "▲")
ZERO_WORDS = ("لا", "لايوجد", "لا يوجد", "false", "no", "0")


def parse_signal(value: Any) -> Optional[int]:
    """
    تحويل خلية إشارة (من ملف الاختراقات مثلًا) إلى:
        +1 إشارة إيجابية ، -1 إشارة سلبية ، 0 لا توجد إشارة ، None إذا العمود غير موجود.
    يقبل أرقامًا (1 / -1 / 0) أو نصوصًا ("صاعد" ، "هابط" ، "نعم" ، "✓" ...).
    """
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return 0
    if isinstance(value, bool):
        return 1 if value else 0
    num = to_number(value)
    if num is not None:
        return 1 if num > 0 else (-1 if num < 0 else 0)

    text = clean_text(value).lower()
    if not text or text in ZERO_WORDS:
        return 0
    if any(word in text for word in NEGATIVE_WORDS):
        return -1
    if any(word in text for word in POSITIVE_WORDS):
        return 1
    return 0


def parse_direction(value: Any) -> Optional[int]:
    """اتجاه الصفقة اللحظية: 1 شراء ، -1 بيع ، 0 محايد. يقبل أرقامًا أو كلمات شراء/بيع."""
    if is_missing(value):
        return None
    num = to_number(value)
    if num is not None:
        return 1 if num > 0 else (-1 if num < 0 else 0)
    text = clean_text(value).lower()
    if "شراء" in text or "buy" in text:
        return 1
    if "بيع" in text or "sell" in text:
        return -1
    return 0


_TIME_RE = re.compile(r"(\d{1,2}):(\d{2})(?::(\d{2}))?\s*(am|pm|ص|م)?", re.IGNORECASE)


def parse_time(value: Any) -> Optional[dtime]:
    """
    استخراج الوقت من نص مثل "10:15" أو "10:15:32" أو "2026-10-05 13:05:00" أو "1:05 م".
    يرجع datetime.time أو None.
    """
    if isinstance(value, datetime):
        return value.time()
    if isinstance(value, dtime):
        return value
    if is_missing(value):
        return None
    match = _TIME_RE.search(clean_text(value).translate(_AR_DIGITS))
    if not match:
        return None
    hour, minute = int(match.group(1)), int(match.group(2))
    second = int(match.group(3) or 0)
    ampm = (match.group(4) or "").lower()
    if ampm in ("pm", "م") and hour < 12:
        hour += 12
    if ampm in ("am", "ص") and hour == 12:
        hour = 0
    if hour > 23 or minute > 59 or second > 59:
        return None
    return dtime(hour, minute, second)


def classify_text(text: Any) -> str:
    """تصنيف نص الإشارة للتلوين: buy / sell / warn / neutral"""
    text = "" if text is None else str(text)
    if any(m in text for m in BUY_MARKS):
        return "buy"
    if any(m in text for m in SELL_MARKS):
        return "sell"
    if any(m in text for m in WARN_MARKS):
        return "warn"
    return "neutral"


# ═════════════════════════════════════════════
#  الفلاتر الـ 12
#  كل دالة: (d, cfg) ← (النص, النقاط)
# ═════════════════════════════════════════════

def filter_01_net_liquidity(d: dict, cfg: dict):
    """فلتر 1: صافي السيولة اللحظي (قيمة الشراء - قيمة البيع)."""
    v = d.get("net_liquidity")
    if v is None:
        return NO_DATA, 0
    if v > cfg["liquidity_threshold"]:
        return "🟢 شراء قوي", 1
    if v < -cfg["liquidity_threshold"]:
        return "🔴 بيع قوي", -1
    return "⚪ محايد", 0


def filter_02_big_trade(d: dict, cfg: dict):
    """فلتر 2: الصفقات الكبيرة (معلوماتي — لا يضيف نقاط، الاتجاه يحسبه فلتر 7)."""
    size = d.get("last_trade_size")
    if size is None:
        return NO_DATA, 0
    if size > cfg["big_trade_threshold"]:
        return "⚠️ صفقة كبيرة", 0
    return "عادية", 0


def filter_03_liquidity_ratio(d: dict, cfg: dict):
    """فلتر 3: نسبة السيولة اللحظية (نسبة قيمة الشراء من إجمالي التداول %)."""
    r = d.get("liquidity_ratio")
    if r is None:
        return NO_DATA, 0
    if r > cfg["liquidity_ratio_high"]:
        return "🟢 شراء قوي", 1
    if r < cfg["liquidity_ratio_low"]:
        return "🔴 بيع قوي", -1
    return "⚪ محايد", 0


def filter_04_timing(d: dict, cfg: dict):
    """فلتر 4: توقيت الجلسة (معلوماتي — لا يضيف نقاط)."""
    current = d.get("time") or datetime.now().time()
    morning_start = parse_time(cfg["morning_start"])
    morning_end = parse_time(cfg["morning_end"])
    afternoon_start = parse_time(cfg["afternoon_start"])
    if morning_start <= current <= morning_end:
        return "🌅 صباح - تجميع", 0
    if current >= afternoon_start:
        return "🌇 عصر - تصريف", 0
    return "☀️ منتصف الجلسة", 0


def filter_05_ma10(d: dict, cfg: dict):
    """فلتر 5: موقع السعر من متوسط 10."""
    price, ma10 = d.get("price"), d.get("ma_10")
    if price is None or ma10 is None:
        return NO_DATA, 0
    if price > ma10:
        return "✅ فوق متوسط 10", 1
    return "❌ تحت متوسط 10", -1


def filter_06_rsi(d: dict, cfg: dict):
    """فلتر 6: RSI — التشبع البيعي فرصة شراء (+1) والتشبع الشرائي خطر (-1)."""
    rsi = d.get("rsi")
    if rsi is None:
        return NO_DATA, 0
    if rsi > cfg["rsi_overbought"]:
        return "🔴 تشبع شرائي", -1
    if rsi < cfg["rsi_oversold"]:
        return "🟢 تشبع بيعي", 1
    return "⚪ محايد", 0


def filter_07_tick_direction(d: dict, cfg: dict):
    """فلتر 7: اتجاه آخر صفقة لحظية إذا كانت كبيرة."""
    direction, size = d.get("direction"), d.get("last_trade_size")
    if direction is None or size is None:
        return NO_DATA, 0
    if direction == 1 and size > cfg["big_trade_threshold"]:
        return "🟢 شراء كبير", 1
    if direction == -1 and size > cfg["big_trade_threshold"]:
        return "🔴 بيع كبير", -1
    return "⚪ عادي", 0


def filter_08_ma_breakout(d: dict, cfg: dict):
    """فلتر 8: اختراق متوسط 10 وتقاطع متوسط 5 مع 20 (من ملف الاختراقات)."""
    b10, cross = d.get("breakout_ma10"), d.get("ma5_cross_ma20")
    if b10 is None and cross is None:
        return NO_DATA, 0
    labels = []
    if b10 == 1:
        labels.append("✅ اختراق متوسط 10")
    elif b10 == -1:
        labels.append("❌ كسر متوسط 10")
    if cross == 1:
        labels.append("🚀 تقاطع 5 مع 20")
    elif cross == -1:
        labels.append("🔴 تقاطع 5 مع 20 هابط")
    if not labels:
        return "⚪ لا يوجد اختراق", 0
    # أي إشارة إيجابية تكفي لـ +1 ، وإن كانت كلها سلبية فـ -1
    score = 1 if 1 in (b10, cross) else -1
    return " + ".join(labels), score


def filter_09_aroon(d: dict, cfg: dict):
    """فلتر 9: Aroon لقياس قوة الاتجاه."""
    up, down = d.get("aroon_up"), d.get("aroon_down")
    if up is None and down is None:
        return NO_DATA, 0
    if up is not None and up > cfg["aroon_strong"]:
        return "🟢 اتجاه صاعد قوي", 1
    if down is not None and down > cfg["aroon_strong"]:
        return "🔴 اتجاه هابط قوي", -1
    return "⚪ لا اتجاه واضح", 0


def filter_10_macd(d: dict, cfg: dict):
    """فلتر 10: MACD مقابل خط الإشارة."""
    macd, signal = d.get("macd"), d.get("macd_signal")
    if macd is None or signal is None:
        return NO_DATA, 0
    if macd > signal:
        return "🟢 MACD إيجابي", 1
    return "🔴 MACD سلبي", -1


def filter_11_stochastic(d: dict, cfg: dict):
    """فلتر 11: Stochastic K."""
    k = d.get("stoch_k")
    if k is None:
        return NO_DATA, 0
    if k > cfg["stoch_overbought"]:
        return "🔴 تشبع شرائي", -1
    if k < cfg["stoch_oversold"]:
        return "🟢 تشبع بيعي", 1
    return "⚪ محايد", 0


def filter_12_cmf(d: dict, cfg: dict):
    """فلتر 12: CMF (تدفق الأموال لتشايكن)."""
    cmf = d.get("cmf")
    if cmf is None:
        return NO_DATA, 0
    if cmf > cfg["cmf_positive"]:
        return "🟢 تدفق سيولة إيجابي", 1
    if cmf < cfg["cmf_negative"]:
        return "🔴 تدفق سيولة سلبي", -1
    return "⚪ محايد", 0


# ═════════════════════════════════════════════
#  سجل الفلاتر — الترتيب هنا هو ترتيب الأعمدة في Excel ولوحة الويب
#  لتعطيل فلتر: احذف سطره أو ضع # قبله
# ═════════════════════════════════════════════
@dataclass(frozen=True)
class FilterDef:
    key: str                       # مفتاح داخلي
    title: str                     # عنوان العمود الظاهر للمستخدم
    func: Callable[[dict, dict], tuple]


FILTERS: list[FilterDef] = [
    FilterDef("f01", "ف1 صافي السيولة", filter_01_net_liquidity),
    FilterDef("f02", "ف2 الصفقات الكبيرة", filter_02_big_trade),
    FilterDef("f03", "ف3 نسبة السيولة", filter_03_liquidity_ratio),
    FilterDef("f04", "ف4 التوقيت", filter_04_timing),
    FilterDef("f05", "ف5 متوسط 10", filter_05_ma10),
    FilterDef("f06", "ف6 RSI", filter_06_rsi),
    FilterDef("f07", "ف7 اتجاه الصفقة", filter_07_tick_direction),
    FilterDef("f08", "ف8 اختراق المتوسطات", filter_08_ma_breakout),
    FilterDef("f09", "ف9 Aroon", filter_09_aroon),
    FilterDef("f10", "ف10 MACD", filter_10_macd),
    FilterDef("f11", "ف11 Stochastic", filter_11_stochastic),
    FilterDef("f12", "ف12 CMF", filter_12_cmf),
]

FILTER_TITLES = [f.title for f in FILTERS]


def apply_filters(d: dict, filters_cfg: dict) -> dict:
    """
    تطبيق جميع الفلاتر على سهم واحد.
    يرجع:
        {"labels": {عنوان الفلتر: النص}, "scores": {عنوان الفلتر: النقاط}, "score": المجموع}
    """
    labels, scores = {}, {}
    for f in FILTERS:
        try:
            label, score = f.func(d, filters_cfg)
        except Exception as exc:              # فلتر به خطأ لا يوقف بقية الفلاتر
            label, score = f"خطأ: {exc}", 0
        labels[f.title] = label
        scores[f.title] = int(score)
    return {"labels": labels, "scores": scores, "score": sum(scores.values())}


# مستويات الإشارة النهائية حسب مجموع النقاط
LEVEL_LABELS = {
    "strong_buy": "🟢🟢 شراء قوي",
    "buy": "🟢 شراء",
    "neutral": "⚪ محايد",
    "sell": "🔴 بيع",
    "strong_sell": "🔴🔴 بيع قوي",
}


def classify_score(score: int, scoring_cfg: dict) -> tuple[str, str]:
    """تحويل مجموع النقاط إلى (مستوى, نص الإشارة)."""
    if score >= scoring_cfg["strong_buy"]:
        level = "strong_buy"
    elif score >= scoring_cfg["buy"]:
        level = "buy"
    elif score <= scoring_cfg["strong_sell"]:
        level = "strong_sell"
    elif score <= scoring_cfg["sell"]:
        level = "sell"
    else:
        level = "neutral"
    return level, LEVEL_LABELS[level]
