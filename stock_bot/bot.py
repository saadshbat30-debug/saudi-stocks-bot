# -*- coding: utf-8 -*-
"""
bot.py — البوت الرئيسي لتحليل الأسهم السعودية
==============================================

ماذا يفعل؟
  1) يقرأ ملفات CSV المُصدَّرة من تكرتشارت من مجلد data/
  2) يأخذ آخر صف لكل سهم من كل ملف ويدمجها في جدول واحد
  3) يطبق الفلاتر الـ 12 (من filters.py) ويحسب النقاط لكل سهم
  4) يكتب النتائج في:
       - output/signals.xlsx   (ملف Excel ملوّن)
       - output/alerts.log     (سجل التنبيهات القوية)
       - output/latest.json    (البيانات التي تعرضها لوحة الويب)
  5) يراقب مجلد data/ عبر watchdog ويعيد التحليل فور تعديل أي ملف،
     ويعيد التحليل أيضًا كل update_interval ثانية عبر schedule.
  6) يشغّل لوحة الويب (dashboard.py) على http://localhost:8000

التشغيل:
    python bot.py                 # التشغيل الكامل (مراقبة + لوحة ويب)
    python bot.py --once          # تحليل مرة واحدة ثم الخروج
    python bot.py --no-dashboard  # بدون لوحة الويب
    python bot.py --no-browser    # بدون فتح المتصفح تلقائيًا
"""

from __future__ import annotations

import argparse
import copy
import csv
import json
import logging
import math
import os
import re
import sys
import threading
import time
import warnings
import webbrowser
from collections import deque
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

import pandas as pd
import schedule
import yaml
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter
from watchdog.events import FileSystemEventHandler
from watchdog.observers import Observer
from watchdog.observers.polling import PollingObserver

import filters as flt

# مجلد المشروع — كل المسارات في config.yaml نسبية له
BASE_DIR = Path(__file__).resolve().parent

# جعل شاشة الأوامر في Windows تعرض العربية والإيموجي بدون أخطاء
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

log = logging.getLogger("stock_bot")

# أسماء عربية لكل ملف (تظهر في التبويبات وأوراق Excel)
FILE_LABELS = {
    "sahmak": "سهمك",
    "liquidity": "السيولة اللحظية",
    "moving_avg": "المتوسطات",
    "aroon_macd": "Aroon و MACD",
    "breakout": "الاختراقات",
    "rsi_stoch": "RSI و Stochastic",
    "ichimoku": "الغيمة اليابانية",
    "ticks": "اللحظي (Ticks)",
}

# ═════════════════════════════════════════════
#  الإعدادات
# ═════════════════════════════════════════════
# ربط الأعمدة الافتراضي (نفس الموجود في config.yaml) — يُستخدم إذا حُذف قسم columns من الإعدادات
DEFAULT_COLUMNS: dict = {
    "sahmak": {
        "time": "الوقت",
        "price": "السعر",
        "change_pct": ["التغير %", "التغير%", "نسبة التغير"],
        "trades": "الصفقات",
        "buy_trades": "صفقات الشراء",
        "sell_trades": "صفقات البيع",
        "volume": "الحجم",
        "buy_volume": "حجم الشراء",
        "sell_volume": "حجم البيع",
        "value": "القيمة",
        "buy_value": "قيمة الشراء",
        "sell_value": "قيمة البيع",
    },
    "liquidity": {
        "time": "الوقت",
        "buy_volume": "حجم الشراء",
        "buy_value": "قيمة الشراء",
        "buy_trades": "صفقات الشراء",
        "sell_volume": "حجم البيع",
        "sell_value": "قيمة البيع",
        "sell_trades": "صفقات البيع",
        "net_liquidity": "صافي السيولة",
        "liquidity_ratio": ["نسبة السيولة %", "نسبة السيولة%", "نسبة السيولة"],
    },
    "moving_avg": {
        "ma_3": "المتوسط 3",
        "ma_5": "المتوسط 5",
        "ma_10": "المتوسط 10",
        "ma_20": "المتوسط 20",
        "ma_50": "المتوسط 50",
        "ma_100": "المتوسط 100",
        "ma_200": "المتوسط 200",
        "positive_signals": "إشارات إيجابية",
        "negative_signals": "إشارات سلبية",
    },
    "aroon_macd": {
        "aroon_up": "Aroon Up",
        "aroon_down": "Aroon Down",
        "aroon_osc": "Aroon Oscillator",
        "macd": "MACD",
        "macd_signal": "MACD Signal",
        "macd_hist": "MACD Histogram",
        "sar": "Parabolic SAR",
        "cmf": "CMF",
        "ppo": "PPO",
        "roc": "ROC",
        "eom": "EOM",
    },
    "breakout": {
        "breakout_ma10": "اختراق متوسط 10",
        "ma5_cross_ma20": "تقاطع متوسط 5 مع 20",
        "macd_zero": "اختراق MACD للصفر",
        "macd_cross": "تقاطع MACD",
        "stoch_cross": "تقاطع Stochastic",
        "aroon_cross": "تقاطع Aroon",
        "adx_cross": "تقاطع ADX",
        "new_cloud": "غيمة جدية",
        "cloud_breakout": "اختراق الغيمة",
        "kijun_breakout": "اختراق Kijun",
        "tenkan_kijun": "تقاطع Tenken مع Kijun",
        "sar_reversal": "انعكاس Parabolic SAR",
        "gap": "فجوة سعرية",
    },
    "rsi_stoch": {
        "rsi": "RSI",
        "stoch_d": "Stochastic D",
        "stoch_k": "Stochastic K",
        "stoch_rsi": "StochRSI",
        "cci": "CCI",
        "mfi": "MFI",
        "wr": ["W%R", "Williams %R"],
    },
    "ichimoku": {
        "cloud": "الغيمة",
        "price_vs_cloud": "السعر مع الغيمة",
        "price_vs_kijun": "السعر مع Kijun Sen",
        "tenkan_vs_kijun": "Tenken Sen مع Kijun Sen",
    },
    "ticks": {
        "time": "الوقت",
        "last_volume": "حجم آخر",
        "price": "السعر",
        "value": "القيمة",
        "direction": "الاتجاه",
        "count": "العدد",
    },
}

# القيم الافتراضية — تُستخدم إذا نقص أي مفتاح من config.yaml
DEFAULT_CONFIG: dict = {
    "data": {
        "input_folder": "data/",
        "update_interval": 300,
        "debounce_seconds": 3,
        "use_polling": False,
        "symbol_column": "الرمز",
        "name_column": "الاسم",
        "default_symbol": "",
        "encodings": ["utf-8-sig", "cp1256", "utf-16", "utf-8"],
        "max_raw_rows": 1000,
        "files": {key: f"{key}.csv" for key in FILE_LABELS},
    },
    "filters": {
        "liquidity_threshold": 500000,
        "big_trade_threshold": 50000,
        "big_trade_field": "volume",
        "liquidity_ratio_high": 80,
        "liquidity_ratio_low": 20,
        "morning_start": "09:30",
        "morning_end": "11:30",
        "afternoon_start": "14:00",
        "timing_source": "data",
        "rsi_overbought": 70,
        "rsi_oversold": 30,
        "aroon_strong": 70,
        "cmf_positive": 0.1,
        "cmf_negative": -0.1,
        "stoch_overbought": 80,
        "stoch_oversold": 20,
    },
    "scoring": {"strong_buy": 4, "buy": 2, "sell": -2, "strong_sell": -4},
    "output": {
        "excel_file": "output/signals.xlsx",
        "alerts_file": "output/alerts.log",
        "json_file": "output/latest.json",
        "dashboard_port": 8000,
        "dashboard_host": "127.0.0.1",
        "dashboard_enabled": True,
        "open_browser": True,
        "dashboard_refresh": 300,
        "dashboard_poll": 20,
        "chart_max_stocks": 40,
        "max_alerts": 300,
    },
    "alerts": {"sound": True, "sound_file": "alert.wav", "log_big_trades": True},
    "columns": DEFAULT_COLUMNS,
}


def _deep_merge(base: dict, override: dict) -> dict:
    """دمج إعدادات المستخدم فوق الإعدادات الافتراضية (مستوى بمستوى)."""
    result = copy.deepcopy(base)
    for key, value in (override or {}).items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = value
    return result


def load_config(path: Optional[str | Path] = None) -> dict:
    """قراءة config.yaml ودمجه مع القيم الافتراضية."""
    path = Path(path) if path else BASE_DIR / "config.yaml"
    if not path.is_absolute():
        path = BASE_DIR / path
    user_cfg = {}
    if path.exists():
        with open(path, "r", encoding="utf-8") as fh:
            user_cfg = yaml.safe_load(fh) or {}
    else:
        log.warning("لم يتم العثور على %s — سيتم استخدام الإعدادات الافتراضية", path)
    return _deep_merge(DEFAULT_CONFIG, user_cfg)


def resolve_path(p: str | Path) -> Path:
    """تحويل مسار نسبي في الإعدادات إلى مسار كامل داخل مجلد المشروع."""
    p = Path(p)
    return p if p.is_absolute() else BASE_DIR / p


# ═════════════════════════════════════════════
#  قراءة ملفات CSV
# ═════════════════════════════════════════════
def norm_header(name: Any) -> str:
    """توحيد اسم العمود للمقارنة: حذف المسافات الزائدة والرموز غير المرئية وتصغير الإنجليزي."""
    return re.sub(r"\s+", " ", flt.clean_text(name)).lower()


def find_column(df: pd.DataFrame, names: Any) -> Optional[str]:
    """البحث عن عمود بالاسم (أو بأحد الأسماء البديلة) ويرجع الاسم الفعلي في الملف."""
    if df is None or names is None:
        return None
    if isinstance(names, str):
        names = [names]
    lookup = {norm_header(c): c for c in df.columns}
    for name in names:
        hit = lookup.get(norm_header(name))
        if hit is not None:
            return hit
    return None


def normalize_symbol(value: Any) -> str:
    """توحيد رمز السهم: "2222.0" ← "2222" ، "٢٢٢٢" ← "2222"."""
    text = flt.clean_text(value).translate(flt._AR_DIGITS)
    if re.fullmatch(r"\d+\.0+", text):
        text = text.split(".")[0]
    return text


def read_csv_file(path: Path, encodings: list[str]) -> Optional[pd.DataFrame]:
    """
    قراءة ملف CSV بأمان:
      - يجرب عدة ترميزات (UTF-8 / Windows-1256 العربي / UTF-16)
      - يكتشف الفاصل تلقائيًا ( , أو ; أو Tab )
      - يعيد المحاولة إذا كان تكرتشارت ما زال يكتب الملف
    """
    if not path.exists():
        return None
    last_error = None
    for attempt in range(3):
        for enc in encodings:
            try:
                df = pd.read_csv(path, encoding=enc, sep=None, engine="python",
                                 dtype=str, skipinitialspace=True)
                # تنظيف أسماء الأعمدة وحذف الصفوف/الأعمدة الفارغة تمامًا
                df.columns = [flt.clean_text(c) for c in df.columns]
                df = df.loc[:, [c for c in df.columns if c and not c.startswith("Unnamed")]]
                df = df.dropna(how="all")
                return df
            except (UnicodeDecodeError, UnicodeError):
                continue                              # جرّب الترميز التالي
            except pd.errors.EmptyDataError:
                return pd.DataFrame()
            except (PermissionError, OSError, pd.errors.ParserError, csv.Error) as exc:
                last_error = exc
                break                                 # الملف مقفل أو يُكتب الآن — انتظر وأعد
        time.sleep(1)
    log.error("تعذّرت قراءة الملف %s: %s", path.name, last_error or "ترميز غير معروف")
    return None


def numericize(df: pd.DataFrame, skip: set[str]) -> pd.DataFrame:
    """تحويل الأعمدة الرقمية إلى أرقام حقيقية (ليصبح الفرز والرسم والتلوين صحيحًا)."""
    df = df.copy()
    for col in df.columns:
        if col in skip:
            continue
        values = df[col]
        non_empty = values[~values.map(flt.is_missing)]
        if non_empty.empty:
            continue
        converted = non_empty.map(flt.to_number)
        if converted.notna().all():                   # كل القيم أرقام ← حوّل العمود
            df[col] = values.map(flt.to_number)
    return df


def sort_by_time(df: pd.DataFrame, time_col: Optional[str]) -> pd.DataFrame:
    """ترتيب الصفوف زمنيًا إن أمكن (لأن بعض التصديرات تضع الأحدث أولًا)."""
    if not time_col or time_col not in df.columns or df.empty:
        return df
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        parsed = pd.to_datetime(df[time_col].astype(str).map(flt.clean_text), errors="coerce")
    if parsed.notna().mean() < 0.8:
        # ربما الوقت فقط بدون تاريخ — نستخدم parse_time
        times = df[time_col].map(flt.parse_time)
        if times.notna().mean() < 0.8:
            return df
        parsed = times.map(lambda t: float(t.hour * 3600 + t.minute * 60 + t.second) if t else float("nan"))
    order = parsed.reset_index(drop=True).sort_values(kind="stable").index
    return df.iloc[order].reset_index(drop=True)


class Dataset:
    """
    كل البيانات المقروءة في دورة تحليل واحدة:
      raw[key]     ← الملف كاملًا (بعد التنظيف)
      latest[key]  ← آخر صف لكل سهم (الفهرس = رمز السهم)
      cols[key]    ← أسماء الأعمدة الفعلية للحقول المعرّفة في config.yaml
    """

    def __init__(self, cfg: dict):
        self.cfg = cfg
        self.raw: dict[str, pd.DataFrame] = {}
        self.latest: dict[str, pd.DataFrame] = {}
        self.cols: dict[str, dict[str, Optional[str]]] = {}
        self.status: dict[str, dict] = {}
        self.symbols: list[str] = []
        self.names: dict[str, str] = {}

    # ─────────────────────────────────────────
    def load(self) -> "Dataset":
        data_cfg = self.cfg["data"]
        folder = resolve_path(data_cfg["input_folder"])
        sym_name, name_name = data_cfg["symbol_column"], data_cfg["name_column"]
        fallback_symbol = normalize_symbol(data_cfg.get("default_symbol") or "")

        for key, filename in data_cfg["files"].items():
            path = folder / filename
            info = {"label": FILE_LABELS.get(key, key), "file": filename,
                    "exists": path.exists(), "rows": 0, "modified": None, "note": ""}
            self.status[key] = info
            df = read_csv_file(path, data_cfg["encodings"])
            if df is None:
                info["note"] = "الملف غير موجود" if not path.exists() else "تعذرت القراءة"
                continue
            info["modified"] = datetime.fromtimestamp(path.stat().st_mtime).strftime("%Y-%m-%d %H:%M:%S")
            info["rows"] = len(df)
            if df.empty:
                info["note"] = "الملف فارغ"
                continue

            # توحيد عمود الرمز
            sym_col = find_column(df, sym_name)
            if sym_col is None:
                if fallback_symbol:
                    df.insert(0, sym_name, fallback_symbol)
                    info["note"] = f"بدون عمود رمز ← نُسب إلى {fallback_symbol}"
                else:
                    df.insert(0, sym_name, "")       # سيُعالج بعد قراءة سهمك (انظر أدناه)
                    info["note"] = "بدون عمود رمز"
            elif sym_col != sym_name:
                df = df.rename(columns={sym_col: sym_name})
            df[sym_name] = df[sym_name].map(normalize_symbol)

            name_col = find_column(df, name_name)
            if name_col and name_col != name_name:
                df = df.rename(columns={name_col: name_name})

            # ربط الحقول المعرّفة في config.yaml بالأعمدة الفعلية
            mapping = self.cfg["columns"].get(key, {}) or {}
            self.cols[key] = {field: find_column(df, names) for field, names in mapping.items()}

            # تحويل الأعمدة الرقمية (ما عدا الرمز والاسم والوقت والأعمدة النصية)
            skip = {sym_name, name_name}
            if self.cols[key].get("time"):
                skip.add(self.cols[key]["time"])
            df = numericize(df, skip)
            df = sort_by_time(df, self.cols[key].get("time"))
            self.raw[key] = df

        # ملفات بدون رمز: إذا كان في سهمك سهم واحد فقط ننسبها له تلقائيًا
        sahmak_syms = []
        if "sahmak" in self.raw:
            sahmak_syms = [s for s in self.raw["sahmak"][sym_name].unique() if s]
        for key, df in self.raw.items():
            if (df[sym_name] == "").all():
                if len(sahmak_syms) == 1:
                    df[sym_name] = sahmak_syms[0]
                    self.status[key]["note"] = f"بدون عمود رمز ← نُسب إلى {sahmak_syms[0]}"
                else:
                    log.warning("الملف %s لا يحتوي عمود '%s' — حدد default_symbol في config.yaml",
                                self.status[key]["file"], sym_name)

        # آخر صف لكل سهم
        for key, df in self.raw.items():
            valid = df[df[sym_name] != ""]
            if valid.empty:
                continue
            self.latest[key] = valid.groupby(sym_name, sort=False).tail(1).set_index(sym_name)

        # قائمة الأسهم: أسهم "سهمك" أولًا ثم أي سهم يظهر في بقية الملفات
        ordered = ["sahmak"] + [k for k in self.latest if k != "sahmak"]
        for key in ordered:
            df = self.latest.get(key)
            if df is None:
                continue
            for sym in df.index:
                if sym not in self.names:
                    self.symbols.append(sym)
                    self.names[sym] = ""
                if not self.names[sym] and name_name in df.columns and not flt.is_missing(df.at[sym, name_name]):
                    self.names[sym] = flt.clean_text(df.at[sym, name_name])
        return self

    # ─────────────────────────────────────────
    def value(self, key: str, field: str, symbol: str) -> Any:
        """قيمة حقل معيّن لسهم معيّن من ملف معيّن (أو None)."""
        df = self.latest.get(key)
        col = self.cols.get(key, {}).get(field)
        if df is None or col is None or symbol not in df.index:
            return None
        val = df.at[symbol, col]
        return None if flt.is_missing(val) else val

    def number(self, key: str, field: str, symbol: str) -> Optional[float]:
        return flt.to_number(self.value(key, field, symbol))

    def has_field(self, key: str, field: str) -> bool:
        return bool(self.cols.get(key, {}).get(field))


# ═════════════════════════════════════════════
#  تجهيز مدخلات الفلاتر لكل سهم
# ═════════════════════════════════════════════
def first_not_none(*values):
    for v in values:
        if v is not None:
            return v
    return None


def build_inputs(ds: Dataset, symbol: str, fcfg: dict) -> dict:
    """
    جمع القيم التي تحتاجها الفلاتر لسهم واحد من الملفات المختلفة.
    إذا نقصت قيمة في ملف، نحاول حسابها من ملف آخر (مثل صافي السيولة من سهمك).
    """
    n = lambda key, field: ds.number(key, field, symbol)

    # السعر: من سهمك، وإلا من آخر صفقة لحظية
    price = first_not_none(n("sahmak", "price"), n("ticks", "price"))

    # صافي السيولة: العمود الجاهز ← أو (قيمة الشراء - قيمة البيع) من السيولة ← أو من سهمك
    net = n("liquidity", "net_liquidity")
    ratio = n("liquidity", "liquidity_ratio")
    for src in ("liquidity", "sahmak"):
        buy, sell = n(src, "buy_value"), n(src, "sell_value")
        if buy is None or sell is None:
            continue
        if net is None:
            net = buy - sell
        if ratio is None and (buy + sell) > 0:
            ratio = buy / (buy + sell) * 100

    # الصفقة اللحظية الأخيرة
    last_volume, last_value = n("ticks", "last_volume"), n("ticks", "value")
    size = last_value if fcfg.get("big_trade_field") == "value" else last_volume
    direction = flt.parse_direction(ds.value("ticks", "direction", symbol))

    # الوقت: من البيانات أو من ساعة الجهاز
    t = None
    if fcfg.get("timing_source", "data") == "data":
        t = first_not_none(*(flt.parse_time(ds.value(k, "time", symbol))
                             for k in ("sahmak", "ticks", "liquidity")))

    # إشارات ملف الاختراقات (None إذا العمود غير موجود أصلًا)
    def sig(field):
        if not ds.has_field("breakout", field) or symbol not in ds.latest.get("breakout", pd.DataFrame()).index:
            return None
        return flt.parse_signal(ds.value("breakout", field, symbol))

    return {
        "price": price,
        "change_pct": n("sahmak", "change_pct"),
        "ma_10": n("moving_avg", "ma_10"),
        "net_liquidity": net,
        "liquidity_ratio": ratio,
        "last_trade_size": size,
        "last_trade_value": last_value,
        "direction": direction,
        "tick_time": ds.value("ticks", "time", symbol),
        "time": t,
        "rsi": n("rsi_stoch", "rsi"),
        "stoch_k": n("rsi_stoch", "stoch_k"),
        "breakout_ma10": sig("breakout_ma10"),
        "ma5_cross_ma20": sig("ma5_cross_ma20"),
        "aroon_up": n("aroon_macd", "aroon_up"),
        "aroon_down": n("aroon_macd", "aroon_down"),
        "macd": n("aroon_macd", "macd"),
        "macd_signal": n("aroon_macd", "macd_signal"),
        "cmf": n("aroon_macd", "cmf"),
    }


# ═════════════════════════════════════════════
#  التنبيهات
# ═════════════════════════════════════════════
def play_sound(cfg: dict) -> None:
    """إشعار صوتي على Windows (يُتجاهل بصمت على الأنظمة الأخرى)."""
    if not cfg["alerts"].get("sound"):
        return
    try:
        import winsound  # متوفر على Windows فقط
    except ImportError:
        return
    try:
        sound_file = resolve_path(cfg["alerts"].get("sound_file") or "alert.wav")
        if sound_file.exists():
            winsound.PlaySound(str(sound_file), winsound.SND_FILENAME | winsound.SND_ASYNC)
        else:
            winsound.MessageBeep(winsound.MB_ICONEXCLAMATION)
    except Exception as exc:
        log.debug("تعذر تشغيل الصوت: %s", exc)


def fmt_num(v: Any, digits: int = 2) -> str:
    """تنسيق رقم للعرض: 1234567.8 ← 1,234,567.80"""
    if v is None or (isinstance(v, float) and math.isnan(v)):
        return "—"
    if isinstance(v, (int, float)):
        return f"{v:,.0f}" if abs(v) >= 1000 else f"{v:,.{digits}f}"
    return str(v)


class AlertManager:
    """
    يقرر متى يُسجَّل تنبيه ويمنع التكرار:
      - تنبيه إشارة قوية: عندما يصبح السهم "شراء قوي" أو "بيع قوي" (أو يتغير بينهما)
      - تنبيه صفقة كبيرة: كل صفقة كبيرة جديدة (فلتر 7) مرة واحدة فقط
    """

    def __init__(self, cfg: dict, history: Optional[list] = None):
        self.cfg = cfg
        self.log_path = resolve_path(cfg["output"]["alerts_file"])
        self.history: deque = deque(history or [], maxlen=int(cfg["output"]["max_alerts"]))
        self.last_level: dict[str, str] = {}
        self.seen_trades: set = set()

    def _write(self, alert: dict) -> None:
        details = " ; ".join(f"{k}: {v}" for k, v in alert["details"].items())
        line = (f"[{alert['time']}] | {alert['type']} | {alert['symbol']} {alert['name']} | "
                f"السعر: {fmt_num(alert['price'])} | الإشارة: {alert['signal']} "
                f"(النقاط: {alert['score']:+d}) | التفاصيل: {details}\n")
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.log_path, "a", encoding="utf-8") as fh:
            fh.write(line)

    def process(self, rows: list[dict]) -> list[dict]:
        new_alerts = []
        now = datetime.now()
        for row in rows:
            sym, level = row["symbol"], row["level"]
            base = {
                "time": now.strftime("%Y-%m-%d %H:%M:%S"),
                "ts": int(now.timestamp() * 1000),
                "symbol": sym, "name": row["name"], "price": row["inputs"]["price"],
                "signal": row["signal"], "score": row["score"], "level": level,
            }
            details = {
                "صافي السيولة": fmt_num(row["inputs"]["net_liquidity"]),
                "نسبة السيولة %": fmt_num(row["inputs"]["liquidity_ratio"]),
                "RSI": fmt_num(row["inputs"]["rsi"]),
                "Stochastic K": fmt_num(row["inputs"]["stoch_k"]),
                "CMF": fmt_num(row["inputs"]["cmf"], 3),
                **{t: lbl for t, lbl in row["labels"].items() if lbl != flt.NO_DATA},
            }

            # 1) إشارة قوية جديدة
            previous = self.last_level.get(sym)
            if level in ("strong_buy", "strong_sell") and level != previous:
                new_alerts.append({**base, "type": "إشارة قوية", "details": details})
            self.last_level[sym] = level

            # 2) صفقة كبيرة جديدة
            big_label = row["labels"].get(flt.FILTERS[6].title, "")
            if self.cfg["alerts"].get("log_big_trades") and flt.classify_text(big_label) in ("buy", "sell"):
                key = (sym, str(row["inputs"]["tick_time"]), row["inputs"]["last_trade_size"])
                if key not in self.seen_trades:
                    self.seen_trades.add(key)
                    new_alerts.append({**base, "type": "صفقة كبيرة", "signal": big_label,
                                       "details": {"حجم الصفقة": fmt_num(row["inputs"]["last_trade_size"]),
                                                   "قيمة الصفقة": fmt_num(row["inputs"]["last_trade_value"]),
                                                   "وقت الصفقة": str(row["inputs"]["tick_time"] or "—"),
                                                   **details}})

        for i, alert in enumerate(new_alerts):
            alert["ts"] += i                          # معرّف فريد لكل تنبيه
            self._write(alert)
            self.history.appendleft(alert)
            log.info("🔔 %s | %s %s | %s", alert["type"], alert["symbol"], alert["name"], alert["signal"])
        if new_alerts:
            play_sound(self.cfg)
        return new_alerts


# ═════════════════════════════════════════════
#  كتابة ملف Excel
# ═════════════════════════════════════════════
FILL_BUY = PatternFill("solid", start_color="C6EFCE", end_color="C6EFCE")
FILL_SELL = PatternFill("solid", start_color="FFC7CE", end_color="FFC7CE")
FILL_WARN = PatternFill("solid", start_color="FFEB9C", end_color="FFEB9C")
FILL_HEAD = PatternFill("solid", start_color="1F4E78", end_color="1F4E78")
FONT_BUY = Font(color="006100", bold=True)
FONT_SELL = Font(color="9C0006", bold=True)
FONT_HEAD = Font(color="FFFFFF", bold=True)
THIN = Side(style="thin", color="D9D9D9")
BORDER = Border(left=THIN, right=THIN, top=THIN, bottom=THIN)


def style_sheet(ws, df: pd.DataFrame, color_cols: list[str], score_cols: list[str]) -> None:
    """تنسيق ورقة Excel: اتجاه من اليمين لليسار، رأس ملوّن، تلوين الإشارات، عرض الأعمدة."""
    ws.sheet_view.rightToLeft = True
    ws.freeze_panes = "A2"
    if ws.max_row > 1:
        ws.auto_filter.ref = ws.dimensions

    for cell in ws[1]:                                # صف العناوين
        cell.fill, cell.font, cell.border = FILL_HEAD, FONT_HEAD, BORDER
        cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
    ws.row_dimensions[1].height = 32

    columns = list(df.columns)
    for idx, col in enumerate(columns, start=1):
        letter = get_column_letter(idx)
        longest = max([len(str(col))] + [len(str(v)) for v in df[col].head(300).tolist()])
        ws.column_dimensions[letter].width = min(max(longest + 3, 9), 45)

        is_color, is_score = col in color_cols, col in score_cols
        for row_idx in range(2, len(df) + 2):
            cell = ws.cell(row=row_idx, column=idx)
            cell.border = BORDER
            if isinstance(cell.value, float):
                cell.number_format = "#,##0.00" if abs(cell.value) < 1000 else "#,##0"
            if is_color:
                kind = flt.classify_text(cell.value)
                if kind == "buy":
                    cell.fill, cell.font = FILL_BUY, FONT_BUY
                elif kind == "sell":
                    cell.fill, cell.font = FILL_SELL, FONT_SELL
                elif kind == "warn":
                    cell.fill = FILL_WARN
            if is_score and isinstance(cell.value, (int, float)):
                if cell.value > 0:
                    cell.fill, cell.font = FILL_BUY, FONT_BUY
                elif cell.value < 0:
                    cell.fill, cell.font = FILL_SELL, FONT_SELL
                cell.alignment = Alignment(horizontal="center")


def write_excel(path: Path, sheets: list[tuple[str, pd.DataFrame, list[str], list[str]]]) -> bool:
    """
    كتابة ملف Excel بأمان: نكتب ملفًا مؤقتًا ثم نستبدل الأصلي.
    إذا كان الملف مفتوحًا في Excel (Windows يقفله) نطبع تحذيرًا ونحاول في الدورة القادمة.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"~tmp_{path.name}")
    with pd.ExcelWriter(tmp, engine="openpyxl") as writer:
        for name, df, color_cols, score_cols in sheets:
            sheet_name = re.sub(r"[\[\]\*\?/\\:]", "", name)[:31]
            out = df if not df.empty else pd.DataFrame({"ملاحظة": ["لا توجد بيانات"]})
            out.to_excel(writer, sheet_name=sheet_name, index=False)
            style_sheet(writer.sheets[sheet_name], out, color_cols, score_cols)
    for _ in range(3):
        try:
            os.replace(tmp, path)
            return True
        except PermissionError:
            time.sleep(1)
    log.warning("⚠️ الملف %s مفتوح في Excel — أغلقه ليتم تحديثه (النسخة الجديدة في %s)", path.name, tmp.name)
    return False


# ═════════════════════════════════════════════
#  تحويل البيانات إلى JSON للوحة الويب
# ═════════════════════════════════════════════
def json_value(v: Any) -> Any:
    """تحويل أي قيمة إلى قيمة صالحة لـ JSON (NaN ← null)."""
    if v is None:
        return None
    if isinstance(v, float):
        return None if math.isnan(v) or math.isinf(v) else round(v, 6)
    if hasattr(v, "item"):                            # أنواع numpy
        return json_value(v.item())
    if isinstance(v, (int, str, bool)):
        return v
    return str(v)


def df_to_table(df: pd.DataFrame, limit: Optional[int] = None) -> dict:
    """DataFrame ← {"columns": [...], "rows": [[...], ...]}"""
    if limit:
        df = df.tail(limit)
    return {"columns": [str(c) for c in df.columns],
            "rows": [[json_value(v) for v in row] for row in df.itertuples(index=False, name=None)]}


def atomic_write_text(path: Path, text: str) -> None:
    """كتابة ملف نصي بأمان (ملف مؤقت ثم استبدال) حتى لا تقرأ لوحة الويب ملفًا ناقصًا."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"~tmp_{path.name}")
    with open(tmp, "w", encoding="utf-8") as fh:
        fh.write(text)
    for _ in range(5):
        try:
            os.replace(tmp, path)
            return
        except PermissionError:
            time.sleep(0.3)
    log.warning("تعذر تحديث %s", path.name)


# ═════════════════════════════════════════════
#  البوت
# ═════════════════════════════════════════════
class StockBot:
    def __init__(self, cfg: dict):
        self.cfg = cfg
        self._lock = threading.Lock()
        self.json_path = resolve_path(cfg["output"]["json_file"])
        self.excel_path = resolve_path(cfg["output"]["excel_file"])
        self.alerts = AlertManager(cfg, self._previous_alerts())
        self.run_count = 0

    def _previous_alerts(self) -> list:
        """استرجاع التنبيهات السابقة من latest.json حتى لا تختفي من اللوحة بعد إعادة التشغيل."""
        try:
            with open(self.json_path, "r", encoding="utf-8") as fh:
                return json.load(fh).get("alerts", [])
        except Exception:
            return []

    # ─────────────────────────────────────────
    def run_safe(self) -> None:
        """تشغيل التحليل مع منع التشغيل المتزامن والتقاط أي خطأ حتى لا يتوقف البوت."""
        with self._lock:
            try:
                self.run()
            except Exception:
                log.exception("❌ خطأ أثناء التحليل")

    def run(self) -> None:
        started = time.time()
        cfg = self.cfg
        sym_col, name_col = cfg["data"]["symbol_column"], cfg["data"]["name_column"]
        ds = Dataset(cfg).load()

        if not ds.symbols:
            log.warning("لا توجد بيانات أسهم في مجلد %s", cfg["data"]["input_folder"])

        # 1) تطبيق الفلاتر على كل سهم
        rows = []
        for sym in ds.symbols:
            inputs = build_inputs(ds, sym, cfg["filters"])
            result = flt.apply_filters(inputs, cfg["filters"])
            level, signal = flt.classify_score(result["score"], cfg["scoring"])
            rows.append({"symbol": sym, "name": ds.names.get(sym, ""), "inputs": inputs,
                         "labels": result["labels"], "scores": result["scores"],
                         "score": result["score"], "level": level, "signal": signal})

        # 2) الترتيب حسب النقاط ثم صافي السيولة
        rows.sort(key=lambda r: (r["score"], r["inputs"]["net_liquidity"] or 0), reverse=True)

        # 3) التنبيهات
        self.alerts.process(rows)

        # 4) جدول الملخص
        summary = pd.DataFrame([{
            "الترتيب": i + 1,
            "الرمز": r["symbol"],
            "الاسم": r["name"],
            "السعر": r["inputs"]["price"],
            "التغير %": r["inputs"]["change_pct"],
            "صافي السيولة": r["inputs"]["net_liquidity"],
            "نسبة السيولة %": None if r["inputs"]["liquidity_ratio"] is None else round(r["inputs"]["liquidity_ratio"], 2),
            "النقاط": r["score"],
            "الإشارة": r["signal"],
            **r["labels"],
        } for i, r in enumerate(rows)])

        # 5) الجدول الكامل: الإشارات + كل الأعمدة الأصلية من كل الملفات
        full = summary[["الرمز", "الاسم", "النقاط", "الإشارة"] + flt.FILTER_TITLES].copy() \
            if not summary.empty else pd.DataFrame()
        if not full.empty:
            full = full.set_index("الرمز")
            for key, df in ds.latest.items():
                part = df.drop(columns=[c for c in (name_col,) if c in df.columns])
                label = FILE_LABELS.get(key, key)
                part = part.rename(columns={c: (f"{c} [{label}]" if c in full.columns else c)
                                            for c in part.columns})
                full = full.join(part, how="left")
            full = full.reset_index().rename(columns={"index": "الرمز"})

        signal_cols = ["الإشارة"] + flt.FILTER_TITLES
        alerts_df = pd.DataFrame([{
            "الوقت": a["time"], "النوع": a["type"], "الرمز": a["symbol"], "الاسم": a["name"],
            "السعر": a["price"], "الإشارة": a["signal"], "النقاط": a["score"],
            "التفاصيل": " ; ".join(f"{k}: {v}" for k, v in a["details"].items()),
        } for a in self.alerts.history])

        # 6) ملف Excel
        sheets = [("الإشارات", summary, signal_cols, ["النقاط", "صافي السيولة", "التغير %"]),
                  ("الجدول الكامل", full, signal_cols, ["النقاط"]),
                  ("التنبيهات", alerts_df, ["الإشارة"], ["النقاط"])]
        for key, df in ds.raw.items():
            sheets.append((FILE_LABELS.get(key, key), df, [], []))
        write_excel(self.excel_path, sheets)

        # 7) ملف JSON للوحة الويب
        self._write_json(ds, rows, summary, full, sym_col)

        self.run_count += 1
        strong_buy = sum(r["level"] == "strong_buy" for r in rows)
        strong_sell = sum(r["level"] == "strong_sell" for r in rows)
        log.info("✅ تحليل #%d: %d سهم | شراء قوي: %d | بيع قوي: %d | %.1f ث",
                 self.run_count, len(rows), strong_buy, strong_sell, time.time() - started)

    # ─────────────────────────────────────────
    def _write_json(self, ds: Dataset, rows: list, summary: pd.DataFrame,
                    full: pd.DataFrame, sym_col: str) -> None:
        cfg = self.cfg
        max_raw = int(cfg["data"]["max_raw_rows"])

        # بيانات الرسوم البيانية لكل سهم
        chart = []
        for r in rows:
            i = r["inputs"]
            chart.append({k: json_value(v) for k, v in {
                "symbol": r["symbol"], "name": r["name"], "score": r["score"], "level": r["level"],
                "price": i["price"], "ma_10": i["ma_10"], "net_liquidity": i["net_liquidity"],
                "liquidity_ratio": i["liquidity_ratio"], "rsi": i["rsi"], "stoch_k": i["stoch_k"],
                "aroon_up": i["aroon_up"], "aroon_down": i["aroon_down"], "macd": i["macd"],
                "macd_signal": i["macd_signal"], "cmf": i["cmf"],
            }.items()})

        # سلسلة الصفقات اللحظية لكل سهم (لرسم السعر والحجم)
        ticks = {}
        tdf = ds.raw.get("ticks")
        tcols = ds.cols.get("ticks", {})
        if tdf is not None and not tdf.empty:
            for sym, group in tdf.groupby(sym_col, sort=False):
                group = group.tail(max_raw)
                pick = lambda f: [json_value(v) for v in group[tcols[f]]] if tcols.get(f) else []
                ticks[sym] = {
                    "time": [flt.clean_text(v) for v in group[tcols["time"]]] if tcols.get("time") else
                            list(range(1, len(group) + 1)),
                    "price": [json_value(flt.to_number(v)) for v in pick("price")],
                    "size": [json_value(flt.to_number(v)) for v in pick("last_volume")],
                    "direction": [flt.parse_direction(v) for v in pick("direction")],
                }

        payload = {
            "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "run": self.run_count + 1,
            "filter_titles": flt.FILTER_TITLES,
            "file_labels": FILE_LABELS,
            "files_status": ds.status,
            "summary": df_to_table(summary),
            "full": df_to_table(full),
            "strong": [{
                "symbol": r["symbol"], "name": r["name"], "price": json_value(r["inputs"]["price"]),
                "change_pct": json_value(r["inputs"]["change_pct"]),
                "score": r["score"], "level": r["level"], "signal": r["signal"],
                "labels": r["labels"],
            } for r in rows if r["level"] in ("strong_buy", "strong_sell")],
            "alerts": [{k: (json_value(v) if k != "details" else v) for k, v in a.items()}
                       for a in self.alerts.history],
            "raw": {key: {"label": FILE_LABELS.get(key, key), **df_to_table(df, max_raw)}
                    for key, df in ds.raw.items()},
            "chart": chart,
            "ticks": ticks,
            "settings": {
                "filters": cfg["filters"],
                "scoring": cfg["scoring"],
                "chart_max_stocks": cfg["output"]["chart_max_stocks"],
                "update_interval": cfg["data"]["update_interval"],
            },
        }
        atomic_write_text(self.json_path, json.dumps(payload, ensure_ascii=False))


# ═════════════════════════════════════════════
#  مراقبة مجلد البيانات (watchdog)
# ═════════════════════════════════════════════
class DataFolderHandler(FileSystemEventHandler):
    """
    عند تعديل/إنشاء أي ملف CSV ننتظر بضع ثوانٍ (debounce) ثم نحلل مرة واحدة،
    لأن تكرتشارت قد يكتب عدة ملفات متتالية ولا نريد 8 تحليلات متكررة.
    """

    def __init__(self, bot: StockBot, delay: float):
        super().__init__()
        self.bot, self.delay = bot, delay
        self._timer: Optional[threading.Timer] = None
        self._lock = threading.Lock()

    def on_any_event(self, event):
        if event.is_directory:
            return
        paths = [str(getattr(event, "src_path", "")), str(getattr(event, "dest_path", "") or "")]
        if not any(p.lower().endswith(".csv") for p in paths):
            return
        with self._lock:
            if self._timer:
                self._timer.cancel()
            self._timer = threading.Timer(self.delay, self._fire)
            self._timer.daemon = True
            self._timer.start()

    def _fire(self):
        log.info("📂 تم رصد تحديث في ملفات البيانات — جاري التحليل...")
        self.bot.run_safe()


# ═════════════════════════════════════════════
#  لوحة الويب في خيط منفصل
# ═════════════════════════════════════════════
def start_dashboard_thread(cfg: dict) -> str:
    from dashboard import create_app        # الاستيراد هنا لتجنب الاستيراد الدائري

    host = cfg["output"]["dashboard_host"]
    port = int(cfg["output"]["dashboard_port"])
    app = create_app(cfg)
    logging.getLogger("werkzeug").setLevel(logging.WARNING)
    thread = threading.Thread(
        target=lambda: app.run(host=host, port=port, debug=False, use_reloader=False, threaded=True),
        daemon=True, name="dashboard")
    thread.start()
    url = f"http://{'localhost' if host in ('0.0.0.0', '127.0.0.1') else host}:{port}"
    log.info("🌐 لوحة التحكم تعمل على: %s", url)
    return url


# ═════════════════════════════════════════════
#  نقطة التشغيل
# ═════════════════════════════════════════════
def main() -> None:
    parser = argparse.ArgumentParser(description="بوت تحليل الأسهم السعودية")
    parser.add_argument("--config", default="config.yaml", help="مسار ملف الإعدادات")
    parser.add_argument("--once", action="store_true", help="تحليل مرة واحدة ثم الخروج")
    parser.add_argument("--no-dashboard", action="store_true", help="عدم تشغيل لوحة الويب")
    parser.add_argument("--no-browser", action="store_true", help="عدم فتح المتصفح تلقائيًا")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(message)s", datefmt="%H:%M:%S")
    cfg = load_config(args.config)

    data_dir = resolve_path(cfg["data"]["input_folder"])
    data_dir.mkdir(parents=True, exist_ok=True)
    resolve_path(cfg["output"]["excel_file"]).parent.mkdir(parents=True, exist_ok=True)

    log.info("🚀 بدء تشغيل بوت الأسهم السعودية")
    log.info("📁 مجلد البيانات: %s", data_dir)

    bot = StockBot(cfg)
    bot.run_safe()                                    # التحليل الأول فورًا
    if args.once:
        return

    # لوحة الويب
    if cfg["output"]["dashboard_enabled"] and not args.no_dashboard:
        url = start_dashboard_thread(cfg)
        if cfg["output"]["open_browser"] and not args.no_browser:
            threading.Timer(1.5, lambda: webbrowser.open(url)).start()

    # مراقبة المجلد
    observer = PollingObserver(timeout=5) if cfg["data"]["use_polling"] else Observer()
    observer.schedule(DataFolderHandler(bot, float(cfg["data"]["debounce_seconds"])),
                      str(data_dir), recursive=False)
    observer.start()
    log.info("👀 مراقبة مجلد البيانات مفعّلة")

    # إعادة التحليل الدورية (لتحديث فلتر التوقيت وكاحتياط إذا فات watchdog تعديل)
    interval = int(cfg["data"]["update_interval"])
    schedule.every(interval).seconds.do(bot.run_safe)
    log.info("⏱️ إعادة التحليل الدوري كل %d ثانية — اضغط Ctrl+C للإيقاف", interval)

    try:
        while True:
            schedule.run_pending()
            time.sleep(1)
    except KeyboardInterrupt:
        log.info("🛑 إيقاف البوت...")
    finally:
        observer.stop()
        observer.join(timeout=5)


if __name__ == "__main__":
    main()
