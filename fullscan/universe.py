"""قاعدة رموز الأسهم الأمريكية المدرجة من الملفات الرسمية لدليل رموز Nasdaq Trader (تُنشر يوميًا).

- nasdaqlisted.txt: كل أوراق ناسداك.
- otherlisted.txt: نيويورك، ونيويورك أمريكان، وآركا، وCboe BZX، وIEX.

المؤهل للماسح: سهم عادي غير تجريبي (Test Issue = N)، ليس صندوقًا (ETF = N)، وليس وارنت أو وحدة أو حق
أو سهمًا ممتازًا أو سندًا (من اسم الورقة ورمزها). تُحفظ نسخة في data/symbols.json وتُحدَّث مرة يوميًا.
"""
import csv
import io
import json
import os
import re
import time

import requests

BASE = "https://www.nasdaqtrader.com/dynamic/SymDir/"
FILES = ("nasdaqlisted.txt", "otherlisted.txt")
CACHE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "symbols.json")
REFRESH_SECONDS = 20 * 3600
EXCHANGES = {"Q": "NASDAQ", "N": "NYSE", "A": "NYSE American", "P": "NYSE Arca", "Z": "Cboe BZX", "V": "IEX"}
NOT_COMMON = re.compile(r"\b(warrants?|units?|rights?|preferred|depositary shares? representing|notes due|debentures|subordinated|% notes)\b", re.I)


def _rows(text):
    rows = list(csv.DictReader(io.StringIO(text), delimiter="|"))
    created = None
    if rows and str(list(rows[-1].values())[0]).startswith("File Creation Time"):
        created = list(rows.pop().values())[0].split(":", 1)[1].strip()
    return rows, created


def parse(nasdaq_text, other_text):
    """يرجع (قائمة الأسهم المؤهلة، إحصاءات). الرمز بصيغة ياهو (BRK.B ← BRK-B)."""
    out, seen = [], set()
    stats = {"nasdaq_rows": 0, "other_rows": 0, "test": 0, "etf": 0, "not_common": 0, "preferred_symbol": 0, "duplicates": 0}
    nq, created_nq = _rows(nasdaq_text)
    ot, created_ot = _rows(other_text)
    stats["nasdaq_rows"], stats["other_rows"] = len(nq), len(ot)
    items = [(r["Symbol"], r, "Q") for r in nq] + [(r["ACT Symbol"], r, r.get("Exchange", "")) for r in ot]
    for sym, r, ex in items:
        if r.get("Test Issue") == "Y":
            stats["test"] += 1
            continue
        if r.get("ETF") == "Y":
            stats["etf"] += 1
            continue
        if "$" in sym:  # رمز سلسلة سهم ممتاز في ترميز ACT
            stats["preferred_symbol"] += 1
            continue
        if NOT_COMMON.search(r.get("Security Name", "")):
            stats["not_common"] += 1
            continue
        y = sym.replace(".", "-")
        if y in seen:
            stats["duplicates"] += 1
            continue
        seen.add(y)
        out.append({"symbol": y, "name": r.get("Security Name", ""), "exchange": EXCHANGES.get(ex, ex),
                    "financial_status": r.get("Financial Status") or None})
    stats["eligible"] = len(out)
    stats["file_created"] = created_nq or created_ot
    return out, stats


def load(force=False):
    """قاعدة الرموز: من الملف المحفوظ إن كان حديثًا، وإلا من Nasdaq Trader. ترجع (الأسهم، المعلومات)."""
    cached = None
    try:
        with open(CACHE, encoding="utf-8") as f:
            cached = json.load(f)
    except (OSError, ValueError):
        pass
    if cached and not force and time.time() - cached["at"] < REFRESH_SECONDS:
        return cached["symbols"], {**cached["stats"], "source": "Nasdaq Trader Symbol Directory", "at": cached["at"], "from_cache": True}
    try:
        texts = []
        for name in FILES:
            r = requests.get(BASE + name, timeout=30)
            r.raise_for_status()
            texts.append(r.text)
        symbols, stats = parse(*texts)
        if len(symbols) < 3000:  # ملف ناقص: لا نستبدل النسخة السليمة
            raise ValueError(f"الملف ناقص ({len(symbols)} سهمًا)")
        os.makedirs(os.path.dirname(CACHE), exist_ok=True)
        with open(CACHE, "w", encoding="utf-8") as f:
            json.dump({"at": time.time(), "symbols": symbols, "stats": stats}, f)
        return symbols, {**stats, "source": "Nasdaq Trader Symbol Directory", "at": time.time(), "from_cache": False}
    except Exception as e:
        if cached:
            return cached["symbols"], {**cached["stats"], "source": "Nasdaq Trader Symbol Directory (نسخة محفوظة)",
                                       "at": cached["at"], "from_cache": True, "error": str(e)[:160]}
        return [], {"source": "Nasdaq Trader Symbol Directory", "error": str(e)[:160], "eligible": 0}
